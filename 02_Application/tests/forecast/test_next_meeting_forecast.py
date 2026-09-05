import unittest
from pathlib import Path

from decision_memory.member_evidence import infer_concerns
from decision_memory.next_meeting_forecast import build_next_meeting_forecast


ROOT = Path(__file__).resolve().parents[2]


class NextMeetingForecastTests(unittest.TestCase):
    def test_frozen_workspace_builds_a_reproducible_next_meeting_forecast(self):
        forecast = build_next_meeting_forecast(
            ROOT / "fred_fomc_real.sqlite",
            ROOT / "fomc_simulation.sqlite",
            ROOT / "artifacts/reaction/pooled_ordered_logit_v1.json",
            ROOT / "artifacts/evaluation/frozen_45_policy_baselines_v1.json",
            ROOT / "artifacts/evaluation/frozen_45_vote_baselines_candidate_v1.json",
            communications_database=(
                ROOT / "fomc_simulation.transcript_segmentation_v3_candidate.sqlite"
            ),
            official_context_path=(
                ROOT / "fixtures/next_meeting_official_context_2026-09-01.json"
            ),
            ensemble_artifact_path=(
                ROOT
                / "artifacts/forecast/fomc_2026_09_15_ensemble_v1/ensemble_forecast.json"
            ),
        )

        self.assertEqual(forecast["meeting_id"], "FOMC-2026-09-15")
        self.assertEqual(forecast["meeting_end_date"], "2026-09-16")
        self.assertEqual(forecast["forecast_as_of"], "2026-09-01")
        self.assertEqual(
            forecast["policy_prediction"]["primary_model"], "forward_ensemble"
        )
        self.assertEqual(forecast["status"], "LOCKED_FORWARD_ENSEMBLE")
        self.assertEqual(
            forecast["policy_prediction"]["ensemble"]["combined"]["policy"][
                "support_count"
            ],
            4,
        )
        self.assertIn(
            forecast["policy_prediction"]["action_class"],
            {"CUT", "HOLD", "HIKE"},
        )
        self.assertAlmostEqual(
            sum(forecast["policy_prediction"]["probabilities"].values()),
            1.0,
        )
        self.assertEqual(
            forecast["voter_forecast"]["roster_status"],
            "OFFICIAL_CURRENT_MEMBERSHIP",
        )
        self.assertEqual(
            forecast["voter_forecast"]["membership_source_updated_at"],
            "2026-08-19",
        )
        self.assertEqual(forecast["voter_forecast"]["source_meeting_id"], "FOMC-2026-07-28")
        self.assertEqual(len(forecast["voter_forecast"]["rows"]), 12)
        self.assertEqual(
            sum(
                row["predicted_vote"] == "AGAINST"
                for row in forecast["voter_forecast"]["rows"]
            ),
            3,
        )
        self.assertEqual(
            {row["predicted_vote"] for row in forecast["voter_forecast"]["rows"]}
            <= {"FOR", "AGAINST"},
            True,
        )
        self.assertTrue(
            all(
                evidence["observation_date"] <= forecast["forecast_as_of"]
                for evidence in forecast["feature_evidence"]
            )
        )
        self.assertTrue(
            all(
                row["important_communications"]
                for row in forecast["voter_forecast"]["rows"]
            )
        )
        self.assertEqual(
            next(
                row["role"]
                for row in forecast["voter_forecast"]["rows"]
                if row["display_name"] == "Kevin M. Warsh"
            ),
            "chair",
        )
        waller = next(
            row
            for row in forecast["voter_forecast"]["rows"]
            if row["display_name"] == "Christopher J. Waller"
        )
        self.assertTrue(waller["vote_history"])
        self.assertTrue(waller["important_communications"])
        self.assertTrue(waller["inferred_concerns"])
        self.assertTrue(
            all(
                item["publication_date"] <= forecast["forecast_as_of"]
                for item in waller["important_communications"]
            )
        )
        self.assertTrue(
            all(
                concern["evidence_ids"]
                for concern in waller["inferred_concerns"]
            )
        )
        self.assertTrue(
            any(
                "inflation" in item["excerpt"].casefold()
                or "labor" in item["excerpt"].casefold()
                or "policy" in item["excerpt"].casefold()
                for item in waller["important_communications"]
            )
        )

    def test_concern_inference_is_deterministic_and_evidence_bounded(self):
        communications = [
            {
                "document_id": "speech-1",
                "title": "Inflation and the Labor Market",
                "text": "Inflation remains high. Employment and wage growth are slowing.",
            },
            {
                "document_id": "speech-2",
                "title": "Price Stability",
                "text": "Price stability is necessary for sustainable growth.",
            },
        ]

        concerns = infer_concerns(communications)

        self.assertEqual(concerns[0]["topic_id"], "inflation")
        self.assertIn("speech-1", concerns[0]["evidence_ids"])
        self.assertTrue(all(item["method"] == "deterministic_term_score" for item in concerns))


if __name__ == "__main__":
    unittest.main()
