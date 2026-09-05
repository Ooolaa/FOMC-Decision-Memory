import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from decision_memory.variant_matrix import build_variant_matrix


ROOT = Path(__file__).resolve().parents[2]


class VariantMatrixTests(unittest.TestCase):
    def test_combines_deterministic_and_subscription_metrics(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            root = Path(directory)
            baseline_path = root / "baseline.json"
            baseline_path.write_text(
                json.dumps(
                    {
                        "split_manifest": {
                            "manifest_hash": "a" * 64,
                            "meeting_ids": ["case-1"],
                        },
                        "metrics": {
                            "majority": {
                                "n": 1,
                                "accuracy": 1.0,
                                "mean_absolute_action_error": 0.0,
                                "false_action_count_on_hold": 0,
                            },
                            "persistence": {
                                "n": 1,
                                "accuracy": 1.0,
                                "mean_absolute_action_error": 0.0,
                                "false_action_count_on_hold": 0,
                            },
                            "pooled_reaction": {
                                "n": 1,
                                "accuracy": 0.0,
                                "mean_absolute_action_error": 1.0,
                                "false_action_count_on_hold": 1,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            status_path = root / "variant.json"
            evaluation = {
                "policy_accuracy": 1.0,
                "policy_action_mae": 0.0,
                "false_action_on_hold": 0.0,
                "dissent_true_positive": 1,
                "dissent_false_positive": 1,
                "dissent_false_negative": 0,
                "dissent_true_negative": 0,
            }
            run_path = root / "run.json"
            run_path.write_text(
                json.dumps(
                    {
                        "variant_id": "naked_frozen_llm",
                        "meeting_id": "case-1",
                        "platform_api_calls": 0,
                        "platform_api_cost_usd": 0.0,
                        "evaluation": evaluation,
                    }
                ),
                encoding="utf-8",
            )
            status_path.write_text(
                json.dumps(
                    {
                        "status": "COMPLETED",
                        "variant_id": "naked_frozen_llm",
                        "completed_case_count": 1,
                        "platform_api_calls": 0,
                        "platform_api_cost_usd": 0.0,
                        "cases": [
                            {
                                "meeting_id": "case-1",
                                "run_artifact": str(run_path.relative_to(ROOT)),
                                "run_artifact_sha256": hashlib.sha256(
                                    run_path.read_bytes()
                                ).hexdigest(),
                                "evaluation": evaluation,
                            }
                        ],
                        "aggregate": {
                            "case_count": 1,
                            "metric_coverage": "policy_and_dissent",
                            "policy_accuracy": 1.0,
                            "policy_action_mae": 0.0,
                            "false_action_on_hold": 0.0,
                            "dissent_true_positive": 1,
                            "dissent_false_positive": 1,
                            "dissent_false_negative": 0,
                            "dissent_true_negative": 0,
                            "dissent_base_rate": 0.5,
                            "dissent_precision": 0.5,
                            "dissent_recall": 1.0,
                            "dissent_f1": 2 / 3,
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = build_variant_matrix(
                baseline_path,
                {"naked_frozen_llm": status_path},
                output_path=root / "matrix.json",
                required_variant_ids=["naked_frozen_llm"],
            )
            run_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "run artifact hash mismatch"):
                build_variant_matrix(
                    baseline_path,
                    {"naked_frozen_llm": status_path},
                    output_path=root / "tampered-matrix.json",
                    required_variant_ids=["naked_frozen_llm"],
                )

        self.assertEqual(report["status"], "EVALUATION_MATRIX_COMPLETED")
        self.assertEqual(report["case_count"], 1)
        self.assertEqual(len(report["rows"]), 4)
        naked = next(
            row for row in report["rows"] if row["variant_id"] == "naked_frozen_llm"
        )
        self.assertEqual(naked["dissent_f1"], 2 / 3)
        self.assertEqual(report["platform_api_calls"], 0)


if __name__ == "__main__":
    unittest.main()
