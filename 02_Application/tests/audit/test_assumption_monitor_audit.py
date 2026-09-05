import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.assumption_monitor_audit import audit_assumption_monitors


ROOT = Path(__file__).resolve().parents[2]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def monitor(
    assumption_id: str,
    series_id: str,
    claim: str,
    operator: str,
    threshold: float,
    rule: str,
) -> dict:
    return {
        "assumption_id": assumption_id,
        "claim": claim,
        "monitor_series_id": series_id,
        "monitor_operator": operator,
        "threshold_value": threshold,
        "direction_map_version": "direction-v1",
        "monitor_rule_version": rule,
        "evidence_refs": [],
    }


class AssumptionMonitorAuditTests(unittest.TestCase):
    def test_audits_existing_runs_without_mutating_them(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            root = Path(directory)
            source_path = root / "source.sqlite"
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
            source.executemany(
                "INSERT INTO economic_series VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        "CPIAUCSL",
                        "Consumer Price Index",
                        "Monthly",
                        "Index 1982-1984=100",
                        "ALFRED_VINTAGE",
                    ),
                    (
                        "PAYEMS",
                        "All Employees, Total Nonfarm",
                        "Monthly",
                        "Thousands of Persons",
                        "ALFRED_VINTAGE",
                    ),
                    (
                        "UNRATE",
                        "Unemployment Rate",
                        "Monthly",
                        "Percent",
                        "ALFRED_VINTAGE",
                    ),
                ],
            )
            source.commit()
            source.close()

            run_1 = root / "case-1.json"
            run_1.write_text(
                json.dumps(
                    {
                        "trace": {
                            "assumptions": [
                                monitor(
                                    "bad-index",
                                    "CPIAUCSL",
                                    "Inflation remains above 2 percent.",
                                    "GT",
                                    2.0,
                                    "v1",
                                ),
                                monitor(
                                    "valid-level",
                                    "UNRATE",
                                    "Unemployment remains above 6.5 percent.",
                                    "GT",
                                    6.5,
                                    "level_threshold_v1",
                                ),
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            run_2 = root / "case-2.json"
            run_2.write_text(
                json.dumps(
                    {
                        "trace": {
                            "assumptions": [
                                monitor(
                                    "bad-payroll",
                                    "PAYEMS",
                                    "Payroll employment will continue to improve.",
                                    "GTE",
                                    0.0,
                                    "level_threshold_v1",
                                )
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            original_hashes = {run_1: file_hash(run_1), run_2: file_hash(run_2)}
            status_path = root / "batch_status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "execution_provider": "codex_subscription",
                        "platform_api_calls": 0,
                        "cases": [
                            {
                                "meeting_id": "FOMC-TEST-1",
                                "run_artifact": str(run_1.relative_to(ROOT)),
                                "run_artifact_sha256": original_hashes[run_1],
                            },
                            {
                                "meeting_id": "FOMC-TEST-2",
                                "run_artifact": str(run_2.relative_to(ROOT)),
                                "run_artifact_sha256": original_hashes[run_2],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output_path = root / "audit.json"

            report = audit_assumption_monitors(
                status_path,
                source_path,
                output_path=output_path,
            )

            self.assertEqual(report["status"], "COMPLETE_WITH_BLOCKING_DEFECTS")
            self.assertEqual(report["case_count"], 2)
            self.assertEqual(report["assumption_count"], 3)
            self.assertEqual(report["invalid_case_count"], 2)
            self.assertEqual(report["invalid_assumption_count"], 2)
            self.assertEqual(
                report["violation_counts"],
                {
                    "index_percent_threshold_requires_yoy_transform": 1,
                    "nonnegative_level_threshold_is_tautological": 1,
                    "temporal_path_requires_atomic_rewrite": 1,
                },
            )
            self.assertEqual(report["platform_api_calls"], 0)
            self.assertTrue(output_path.is_file())
            self.assertEqual(
                {path: file_hash(path) for path in original_hashes},
                original_hashes,
            )

    def test_rejects_tampered_run(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            root = Path(directory)
            source_path = root / "source.sqlite"
            source = sqlite3.connect(source_path)
            source.execute(
                """
                CREATE TABLE economic_series (
                    series_id TEXT PRIMARY KEY, title TEXT, frequency TEXT,
                    units TEXT, vintage_mode TEXT
                )
                """
            )
            source.execute(
                "INSERT INTO economic_series VALUES "
                "('UNRATE', 'Unemployment Rate', 'Monthly', 'Percent', 'ALFRED')"
            )
            source.commit()
            source.close()
            run_path = root / "run.json"
            run_path.write_text('{"trace":{"assumptions":[]}}', encoding="utf-8")
            status_path = root / "batch_status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "execution_provider": "codex_subscription",
                        "platform_api_calls": 0,
                        "cases": [
                            {
                                "meeting_id": "FOMC-TEST",
                                "run_artifact": str(run_path.relative_to(ROOT)),
                                "run_artifact_sha256": "0" * 64,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                audit_assumption_monitors(status_path, source_path)


if __name__ == "__main__":
    unittest.main()
