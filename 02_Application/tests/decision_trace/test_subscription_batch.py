import unittest
from pathlib import Path

from decision_memory.subscription_batch import (
    aggregate_evaluations,
    aggregate_usage,
    load_frozen_meeting_ids,
)


ROOT = Path(__file__).resolve().parents[2]


class SubscriptionBatchTests(unittest.TestCase):
    def test_frozen_manifest_has_45_unique_cases(self):
        meeting_ids = load_frozen_meeting_ids(
            ROOT / "artifacts" / "evaluation" / "frozen_45_policy_baselines_v1.json"
        )

        self.assertEqual(len(meeting_ids), 45)
        self.assertEqual(len(set(meeting_ids)), 45)
        self.assertEqual(meeting_ids[0], "FOMC-2021-01-26")

    def test_aggregate_evaluations_uses_participant_level_dissent_counts(self):
        aggregate = aggregate_evaluations(
            [
                {
                    "policy_accuracy": 1.0,
                    "policy_action_mae": 0.0,
                    "false_action_on_hold": 0.0,
                    "dissent_true_positive": 1,
                    "dissent_false_positive": 2,
                    "dissent_false_negative": 1,
                    "dissent_true_negative": 6,
                },
                {
                    "policy_accuracy": 0.0,
                    "policy_action_mae": 2.0,
                    "false_action_on_hold": 1.0,
                    "dissent_true_positive": 1,
                    "dissent_false_positive": 0,
                    "dissent_false_negative": 0,
                    "dissent_true_negative": 9,
                },
            ]
        )

        self.assertEqual(aggregate["case_count"], 2)
        self.assertEqual(aggregate["policy_accuracy"], 0.5)
        self.assertEqual(aggregate["policy_action_mae"], 1.0)
        self.assertEqual(aggregate["false_action_on_hold"], 1.0)
        self.assertEqual(aggregate["dissent_true_positive"], 2)
        self.assertAlmostEqual(aggregate["dissent_base_rate"], 3 / 20)
        self.assertAlmostEqual(aggregate["dissent_precision"], 0.5)
        self.assertAlmostEqual(aggregate["dissent_recall"], 2 / 3)
        self.assertAlmostEqual(aggregate["dissent_f1"], 4 / 7)

    def test_aggregate_usage_sums_runs_and_attempts(self):
        aggregate = aggregate_usage(
            [
                {
                    "usage": [{"attempt": 1}, {"attempt": 2}],
                    "usage_totals": {
                        "input_tokens": 100,
                        "cached_input_tokens": 25,
                        "output_tokens": 10,
                        "reasoning_output_tokens": 4,
                    },
                },
                {
                    "usage": [{"attempt": 1}],
                    "usage_totals": {
                        "input_tokens": 200,
                        "cached_input_tokens": 75,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 6,
                    },
                },
            ]
        )

        self.assertEqual(aggregate["case_count"], 2)
        self.assertEqual(aggregate["request_count"], 3)
        self.assertEqual(aggregate["repair_request_count"], 1)
        self.assertEqual(aggregate["input_tokens"], 300)
        self.assertEqual(aggregate["cached_input_tokens"], 100)
        self.assertEqual(aggregate["output_tokens"], 30)
        self.assertEqual(aggregate["reasoning_output_tokens"], 10)


if __name__ == "__main__":
    unittest.main()
