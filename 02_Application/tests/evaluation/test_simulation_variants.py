import json
import unittest
from pathlib import Path

from decision_memory.llm_sample import build_case_bundle
from decision_memory.simulation_variants import prepare_variant_bundle


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DATABASE = ROOT / "fred_fomc_real.sqlite"
APP_DATABASE = ROOT / "fomc_simulation.sqlite"
REACTION_ARTIFACT = ROOT / "artifacts" / "reaction" / "pooled_ordered_logit_v1.json"


class SimulationVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = build_case_bundle(
            SOURCE_DATABASE,
            APP_DATABASE,
            meeting_id="FOMC-2022-03-15",
        )
        cls.reaction = json.loads(REACTION_ARTIFACT.read_text(encoding="utf-8"))

    def test_named_persona_reaction_includes_bounded_persona_and_reaction_inputs(self):
        prepared = prepare_variant_bundle(
            self.base,
            {
                "variant_id": "named_persona_reaction",
                "participant_names": True,
                "persona_evidence": True,
                "reaction_model": True,
                "economic_snapshot": True,
                "meeting_date": False,
            },
            app_database=APP_DATABASE,
            reaction_artifact=self.reaction,
        )

        model = prepared["model_bundle"]
        self.assertNotEqual(model["meeting_id"], self.base["meeting_id"])
        self.assertNotIn("meeting_start_date", model)
        self.assertTrue(model["economic_snapshot"])
        self.assertTrue(model["persona_evidence"])
        self.assertEqual(
            len(model["reaction_profile_cards"]), len(model["participants"])
        )
        self.assertEqual(prepared["model_to_actual_participant_id"], {})

    def test_anonymous_variant_removes_real_names_and_restores_id_mapping(self):
        prepared = prepare_variant_bundle(
            self.base,
            {
                "variant_id": "anonymous_persona_reaction",
                "participant_names": False,
                "persona_evidence": True,
                "reaction_model": True,
                "economic_snapshot": True,
                "meeting_date": False,
            },
            app_database=APP_DATABASE,
            reaction_artifact=self.reaction,
        )

        serialized = json.dumps(prepared["model_bundle"], ensure_ascii=False).casefold()
        for participant in self.base["participants"]:
            self.assertNotIn(participant["display_name"].casefold(), serialized)
            self.assertNotIn(participant["participant_id"].casefold(), serialized)
        self.assertEqual(
            set(prepared["model_to_actual_participant_id"].values()),
            {item["participant_id"] for item in self.base["participants"]},
        )
        self.assertTrue(prepared["anonymity_verified"])

    def test_naked_variant_excludes_persona_and_reaction_but_keeps_economy(self):
        prepared = prepare_variant_bundle(
            self.base,
            {
                "variant_id": "naked_frozen_llm",
                "participant_names": False,
                "persona_evidence": False,
                "reaction_model": False,
                "economic_snapshot": True,
                "meeting_date": False,
            },
            app_database=APP_DATABASE,
            reaction_artifact=self.reaction,
        )

        model = prepared["model_bundle"]
        self.assertEqual(model["persona_evidence"], [])
        self.assertEqual(model["reaction_profile_cards"], [])
        self.assertTrue(model["economic_snapshot"])

    def test_date_probe_exposes_only_date(self):
        prepared = prepare_variant_bundle(
            self.base,
            {
                "variant_id": "date_only_memorization_probe",
                "participant_names": False,
                "persona_evidence": False,
                "reaction_model": False,
                "economic_snapshot": False,
                "meeting_date": True,
            },
            app_database=APP_DATABASE,
            reaction_artifact=self.reaction,
        )

        self.assertEqual(
            set(prepared["model_bundle"]),
            {"schema_version", "case_id", "meeting_date"},
        )
        self.assertEqual(
            prepared["model_bundle"]["meeting_date"], "2022-03-15"
        )


if __name__ == "__main__":
    unittest.main()
