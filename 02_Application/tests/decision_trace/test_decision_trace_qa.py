import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from decision_memory.decision_trace_qa import build_trace_qa_queue


ROOT = Path(__file__).resolve().parents[2]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DecisionTraceQaTests(unittest.TestCase):
    def test_builds_pending_human_review_queue_with_deterministic_flags(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            output = Path(directory)
            run_path = output / "case.json"
            run_path.write_text(
                json.dumps(
                    {
                        "meeting_id": "FOMC-2006-09-20",
                        "usage": [{"attempt": 1}, {"attempt": 2}],
                        "semantic_validation": {
                            "valid": True,
                            "participant_debate_count": 0,
                            "transcript_evidence_reference_count": 0,
                        },
                        "attribution_sanitization": {
                            "demoted_item_count": 1,
                            "removed_mismatched_reference_count": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            status_path = output / "batch_status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "status": "PARTIAL",
                        "execution_provider": "codex_subscription",
                        "platform_api_calls": 0,
                        "cases": [
                            {
                                "meeting_id": "FOMC-2006-09-20",
                                "run_artifact": str(run_path.relative_to(ROOT)),
                                "run_artifact_sha256": sha256_file(run_path),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            queue = build_trace_qa_queue(status_path)

            self.assertEqual(queue["status"], "PENDING_HUMAN_REVIEW")
            self.assertEqual(queue["case_count"], 1)
            self.assertEqual(queue["priority_counts"], {"HIGH": 1})
            self.assertEqual(queue["cases"][0]["review_status"], "PENDING")
            self.assertEqual(
                queue["cases"][0]["flags"],
                [
                    "NO_PARTICIPANT_LEVEL_DEBATE",
                    "NO_TRANSCRIPT_EVIDENCE",
                    "SEMANTIC_REPAIR_USED",
                    "ATTRIBUTION_DEMOTED_TO_COMMITTEE",
                    "MISMATCHED_ATTRIBUTION_REFERENCE_REMOVED",
                ],
            )
            self.assertTrue((output / "qa_queue.json").is_file())

    def test_rejects_tampered_run_artifact(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            output = Path(directory)
            run_path = output / "case.json"
            run_path.write_text("{}", encoding="utf-8")
            status_path = output / "batch_status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "status": "PARTIAL",
                        "execution_provider": "codex_subscription",
                        "platform_api_calls": 0,
                        "cases": [
                            {
                                "meeting_id": "FOMC-2006-01-31",
                                "run_artifact": str(run_path.relative_to(ROOT)),
                                "run_artifact_sha256": "0" * 64,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                build_trace_qa_queue(status_path)

    def test_revalidates_trace_and_records_database_health(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            output = Path(directory)
            source_path = output / "source.sqlite"
            source = sqlite3.connect(source_path)
            source.execute(
                """
                CREATE TABLE economic_series (
                    series_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    units TEXT NOT NULL,
                    vintage_mode TEXT NOT NULL
                )
                """
            )
            source.execute(
                """
                INSERT INTO economic_series VALUES (
                    'CPIAUCSL', 'Consumer Price Index', 'Monthly',
                    'Index 1982-1984=100', 'ALFRED_VINTAGE'
                )
                """
            )
            source.commit()
            source.close()
            app_path = output / "app.sqlite"
            sqlite3.connect(app_path).close()
            run_path = output / "case.json"
            run_path.write_text(
                json.dumps(
                    {
                        "trace": {"meeting_id": "FOMC-2006-09-20"},
                        "usage": [{"attempt": 1}],
                        "semantic_validation": {
                            "valid": True,
                            "participant_debate_count": 1,
                            "transcript_evidence_reference_count": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            status_path = output / "batch_status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "status": "COMPLETE",
                        "execution_provider": "codex_subscription",
                        "platform_api_calls": 0,
                        "cases": [
                            {
                                "meeting_id": "FOMC-2006-09-20",
                                "run_artifact": str(run_path.relative_to(ROOT)),
                                "run_artifact_sha256": sha256_file(run_path),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch(
                "decision_memory.decision_trace_qa.validate_fomc_decision_trace"
            ) as validator:
                queue = build_trace_qa_queue(
                    status_path,
                    source_database=source_path,
                    app_database=app_path,
                )

            validator.assert_called_once()
            self.assertEqual(queue["deterministically_revalidated_case_count"], 1)
            self.assertEqual(queue["database_health"]["source"]["integrity_check"], "ok")
            self.assertEqual(
                queue["database_health"]["application"]["foreign_key_violation_count"],
                0,
            )

    def test_complete_corpus_gate_rejects_partial_batch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            status_path = Path(directory) / "batch_status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "execution_provider": "codex_subscription",
                        "platform_api_calls": 0,
                        "total_case_count": 50,
                        "completed_case_count": 49,
                        "pending_case_count": 1,
                        "cases": [],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "completed 50-case corpus"):
                build_trace_qa_queue(status_path, require_complete_corpus=True)


if __name__ == "__main__":
    unittest.main()
