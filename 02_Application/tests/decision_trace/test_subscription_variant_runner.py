import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from decision_memory.llm_sample import build_case_bundle
from decision_memory.model_preflight import load_model_spec
from decision_memory.simulation_variants import prepare_variant_bundle
from decision_memory.subscription_variant_runner import (
    build_variant_bundle_preflight,
    run_variant_case,
    run_variant_subscription_batch,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DATABASE = ROOT / "fred_fomc_real.sqlite"
APP_DATABASE = ROOT / "fomc_simulation.sqlite"
REACTION_ARTIFACT = ROOT / "artifacts" / "reaction" / "pooled_ordered_logit_v1.json"


class FakeDateExecutor:
    def __init__(self):
        self.calls = []

    def run_stage(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "output": {"action_class": "HIKE", "rationale": "Date-only probe."},
            "usage": {
                "input_tokens": 20,
                "cached_input_tokens": 0,
                "output_tokens": 5,
                "reasoning_output_tokens": 1,
            },
            "latency_seconds": 0.1,
            "thread_id": "date-thread",
        }


class SubscriptionVariantRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = build_case_bundle(
            SOURCE_DATABASE,
            APP_DATABASE,
            meeting_id="FOMC-2022-03-15",
        )
        cls.reaction = json.loads(REACTION_ARTIFACT.read_text(encoding="utf-8"))

    def test_anonymous_output_is_restored_before_deterministic_evaluation(self):
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
        participants = prepared["model_bundle"]["participants"]
        chair = next(item for item in participants if item["is_chair"])
        model_output = {
            "schema_version": "simulation_output_v1",
            "meeting_id": prepared["model_bundle"]["meeting_id"],
            "synthetic": True,
            "profiles": [
                {
                    "participant_id": item["participant_id"],
                    "display_name": item["display_name"],
                    "is_chair": item["is_chair"],
                    "evidence_ids": [prepared["model_bundle"]["documents"][0]["document_id"]],
                }
                for item in participants
            ],
            "discussion": [
                {
                    "participant_id": item["participant_id"],
                    "synthetic_text": "Synthetic position.",
                }
                for item in participants
            ],
            "final_proposal": {
                "proposer_participant_id": chair["participant_id"],
                "action_class": "HIKE",
                "rationale": "Inflation risk.",
            },
            "votes": [
                {"participant_id": item["participant_id"], "choice": "FOR"}
                for item in participants
            ],
        }

        def fake_sample_runner(executor, bundle, spec):
            return {
                "status": "SUBSCRIPTION_SAMPLE_COMPLETED",
                "execution_provider": "codex_subscription",
                "billing_route": "chatgpt_subscription",
                "platform_api_cost_usd": 0.0,
                "model_id": spec["model_id"],
                "meeting_id": bundle["meeting_id"],
                "bundle_hash": bundle["bundle_hash"],
                "usage": [],
                "usage_totals": {
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                },
                "output": model_output,
            }

        report = run_variant_case(
            object(),
            prepared,
            load_model_spec(),
            app_database=APP_DATABASE,
            sample_runner=fake_sample_runner,
        )

        self.assertEqual(report["meeting_id"], "FOMC-2022-03-15")
        self.assertEqual(report["evaluation"]["policy_accuracy"], 1.0)
        self.assertEqual(
            {item["participant_id"] for item in report["evaluation_output"]["votes"]},
            {item["participant_id"] for item in self.base["participants"]},
        )
        self.assertEqual(report["platform_api_cost_usd"], 0.0)

    def test_date_probe_uses_one_request_and_reports_policy_only_metrics(self):
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
        executor = FakeDateExecutor()

        report = run_variant_case(
            executor,
            prepared,
            load_model_spec(),
            app_database=APP_DATABASE,
        )

        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(report["evaluation"]["metric_coverage"], "policy_only")
        self.assertEqual(report["evaluation"]["policy_accuracy"], 1.0)
        self.assertNotIn("economic_snapshot", executor.calls[0]["prompt"])
        self.assertEqual(report["platform_api_cost_usd"], 0.0)

    def test_variant_batch_resumes_from_hash_verified_case_artifacts(self):
        def fake_case_runner(executor, prepared, spec, *, app_database):
            return {
                "status": "SUBSCRIPTION_VARIANT_COMPLETED",
                "execution_provider": "codex_subscription",
                "billing_route": "chatgpt_subscription",
                "platform_api_cost_usd": 0.0,
                "model_id": spec["model_id"],
                "variant_id": prepared["variant_id"],
                "meeting_id": prepared["actual_meeting_id"],
                "model_bundle_hash": prepared["model_bundle"]["bundle_hash"],
                "usage": [],
                "usage_totals": {
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                },
                "evaluation": {
                    "policy_accuracy": 1.0,
                    "policy_action_mae": 0.0,
                    "false_action_on_hold": 0,
                    "dissent_true_positive": 0,
                    "dissent_false_positive": 0,
                    "dissent_false_negative": 0,
                    "dissent_true_negative": 1,
                },
            }

        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            first = run_variant_subscription_batch(
                object(),
                source_database=SOURCE_DATABASE,
                app_database=APP_DATABASE,
                model_spec=load_model_spec(),
                ablation_spec_path=ROOT
                / "evaluation_spec"
                / "hackathon_r5_variants_v1.json",
                reaction_artifact_path=REACTION_ARTIFACT,
                variant_id="naked_frozen_llm",
                output_directory=Path(directory),
                max_new_cases=1,
                case_runner=fake_case_runner,
            )
            second = run_variant_subscription_batch(
                object(),
                source_database=SOURCE_DATABASE,
                app_database=APP_DATABASE,
                model_spec=load_model_spec(),
                ablation_spec_path=ROOT
                / "evaluation_spec"
                / "hackathon_r5_variants_v1.json",
                reaction_artifact_path=REACTION_ARTIFACT,
                variant_id="naked_frozen_llm",
                output_directory=Path(directory),
                max_new_cases=1,
                case_runner=fake_case_runner,
            )

        self.assertEqual(first["completed_case_count"], 1)
        self.assertEqual(second["completed_case_count"], 2)
        self.assertTrue(second["cases"][0]["reused"])
        self.assertFalse(second["cases"][1]["reused"])
        self.assertEqual(second["aggregate"]["policy_accuracy"], 1.0)
        self.assertEqual(second["platform_api_calls"], 0)

    def test_variant_preflight_materializes_inputs_without_model_calls(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            report = build_variant_bundle_preflight(
                source_database=SOURCE_DATABASE,
                app_database=APP_DATABASE,
                ablation_spec_path=ROOT
                / "evaluation_spec"
                / "hackathon_r5_variants_v1.json",
                reaction_artifact_path=REACTION_ARTIFACT,
                variant_id="anonymous_persona_reaction",
                output_directory=Path(directory),
                meeting_ids=["FOMC-2022-03-15", "FOMC-2023-09-19"],
            )

        self.assertEqual(report["status"], "PREFLIGHT_COMPLETED_NO_MODEL_CALL")
        self.assertEqual(report["case_count"], 2)
        self.assertEqual(report["platform_api_calls"], 0)
        self.assertEqual(
            report["source_database_sha256"],
            hashlib.sha256(SOURCE_DATABASE.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            report["app_database_sha256"],
            hashlib.sha256(APP_DATABASE.read_bytes()).hexdigest(),
        )
        self.assertTrue(all(item["participant_names_exposed"] is False for item in report["cases"]))


if __name__ == "__main__":
    unittest.main()
