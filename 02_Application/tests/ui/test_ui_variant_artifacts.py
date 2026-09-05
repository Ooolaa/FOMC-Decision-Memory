import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from decision_memory.ui_variant_artifacts import (
    build_voter_vote_comparison,
    load_completed_variant_case,
    load_completed_variant_matrix,
)


ROOT = Path(__file__).resolve().parents[2]


class UiVariantArtifactTests(unittest.TestCase):
    def test_builds_per_known_voter_prediction_truth_table(self):
        report = {
            "model_output": {
                "votes": [
                    {"participant_id": "chair", "choice": "FOR"},
                    {"participant_id": "dissenter", "choice": "FOR"},
                ]
            },
            "model_to_actual_participant_id": {},
        }
        actual_votes = [
            {
                "participant_id": "chair",
                "display_name": "Known Chair",
                "voter_choice": "FOR",
            },
            {
                "participant_id": "dissenter",
                "display_name": "Known Dissenter",
                "voter_choice": "AGAINST",
            },
        ]

        comparison = build_voter_vote_comparison(
            report, actual_votes, reveal_identity=True
        )

        self.assertEqual(comparison["voter_count"], 2)
        self.assertEqual(comparison["correct_count"], 1)
        self.assertEqual(comparison["predicted_dissenters"], [])
        self.assertEqual(comparison["actual_dissenters"], ["Known Dissenter"])
        self.assertEqual(comparison["missed_dissenters"], ["Known Dissenter"])
        self.assertEqual(comparison["rows"][1]["dissent_result"], "FALSE_NEGATIVE")

    def test_anonymous_vote_comparison_preserves_ablation_identity_boundary(self):
        report = {
            "model_output": {
                "votes": [
                    {"participant_id": "chair-01", "choice": "FOR"},
                    {"participant_id": "participant-02", "choice": "AGAINST"},
                ]
            },
            "model_to_actual_participant_id": {
                "chair-01": "real-chair",
                "participant-02": "real-member",
            },
        }
        actual_votes = [
            {
                "participant_id": "real-chair",
                "display_name": "Real Chair Name",
                "voter_choice": "FOR",
            },
            {
                "participant_id": "real-member",
                "display_name": "Real Member Name",
                "voter_choice": "AGAINST",
            },
        ]

        comparison = build_voter_vote_comparison(
            report, actual_votes, reveal_identity=False
        )

        self.assertEqual(
            [item["voter"] for item in comparison["rows"]],
            ["chair-01", "participant-02"],
        )
        self.assertNotIn("Real Member Name", json.dumps(comparison))

    def test_vote_comparison_fails_closed_on_roster_mismatch(self):
        report = {
            "model_output": {
                "votes": [{"participant_id": "known", "choice": "FOR"}]
            },
            "model_to_actual_participant_id": {},
        }
        actual_votes = [
            {
                "participant_id": "different",
                "display_name": "Different Voter",
                "voter_choice": "FOR",
            }
        ]

        with self.assertRaisesRegex(ValueError, "roster"):
            build_voter_vote_comparison(report, actual_votes, reveal_identity=True)

    def test_loads_only_completed_hash_verified_case(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            artifact_root = Path(directory)
            variant_root = artifact_root / "r5_variants_v2"
            variant_directory = variant_root / "named_persona_reaction"
            variant_directory.mkdir(parents=True)
            run_path = variant_directory / "run.json"
            evaluation = {"policy_accuracy": 1.0}
            run_path.write_text(
                json.dumps(
                    {
                        "variant_id": "named_persona_reaction",
                        "meeting_id": "FOMC-2022-03-15",
                        "platform_api_cost_usd": 0.0,
                        "evaluation": evaluation,
                    }
                ),
                encoding="utf-8",
            )
            status_path = variant_directory / "batch_status.json"
            status = {
                "status": "RUNNING",
                "variant_id": "named_persona_reaction",
                "platform_api_calls": 0,
                "platform_api_cost_usd": 0.0,
                "cases": [
                    {
                        "meeting_id": "FOMC-2022-03-15",
                        "run_artifact": str(run_path.relative_to(ROOT)),
                        "run_artifact_sha256": hashlib.sha256(
                            run_path.read_bytes()
                        ).hexdigest(),
                        "evaluation": evaluation,
                    }
                ],
            }
            status_path.write_text(json.dumps(status), encoding="utf-8")
            self.assertIsNone(
                load_completed_variant_case(
                    variant_root,
                    variant_id="named_persona_reaction",
                    meeting_id="FOMC-2022-03-15",
                )
            )

            status["status"] = "COMPLETED"
            status_path.write_text(json.dumps(status), encoding="utf-8")
            loaded = load_completed_variant_case(
                variant_root,
                variant_id="named_persona_reaction",
                meeting_id="FOMC-2022-03-15",
            )
            self.assertEqual(loaded["report"]["evaluation"], evaluation)

            run_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_completed_variant_case(
                    variant_root,
                    variant_id="named_persona_reaction",
                    meeting_id="FOMC-2022-03-15",
                )

    def test_variant_matrix_requires_completed_hash_verified_sources(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            artifact_root = Path(directory)
            run_path = artifact_root / "run.json"
            run_path.write_text('{"result":"verified"}', encoding="utf-8")
            source_path = artifact_root / "source.json"
            source_path.write_text(
                json.dumps(
                    {
                        "status": "COMPLETED",
                        "cases": [
                            {
                                "run_artifact": str(run_path.relative_to(ROOT)),
                                "run_artifact_sha256": hashlib.sha256(
                                    run_path.read_bytes()
                                ).hexdigest(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            matrix_path = artifact_root / "matrix.json"
            matrix = {
                "status": "EVALUATION_MATRIX_COMPLETED",
                "platform_api_calls": 0,
                "platform_api_cost_usd": 0.0,
                "rows": [{"variant_id": "persistence_deterministic", "n": 45}],
                "sources": {
                    "persistence_deterministic": {
                        "path": str(source_path.relative_to(ROOT)),
                        "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                    }
                },
            }
            matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

            loaded = load_completed_variant_matrix(matrix_path, workspace_root=ROOT)
            self.assertEqual(loaded["rows"][0]["n"], 45)

            run_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "run hash mismatch"):
                load_completed_variant_matrix(matrix_path, workspace_root=ROOT)
            run_path.write_text('{"result":"verified"}', encoding="utf-8")

            source_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                load_completed_variant_matrix(matrix_path, workspace_root=ROOT)


if __name__ == "__main__":
    unittest.main()
