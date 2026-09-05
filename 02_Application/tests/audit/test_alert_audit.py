import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.alert_audit import evaluate_statement_alerts


class StatementAlertAuditTests(unittest.TestCase):
    def test_temporal_and_cooccurrence_false_alarms_are_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = sqlite3.connect(":memory:")
            app.execute(
                """
                CREATE TABLE document_source (
                    document_id TEXT PRIMARY KEY,
                    meeting_id TEXT,
                    document_type TEXT NOT NULL,
                    publication_at TEXT NOT NULL,
                    usage_class TEXT NOT NULL,
                    source_locator TEXT NOT NULL,
                    content_hash TEXT NOT NULL
                )
                """
            )
            fixtures = [
                ("s1", "2021-04-28", "support phrase"),
                ("s2", "2021-05-05", "flip phrase"),
                ("s3", "2021-06-01", "support phrase and flip phrase"),
                ("s4", "2021-07-01", "flip phrase"),
                ("s5", "2021-08-01", "flip phrase"),
            ]
            for document_id, publication_at, text in fixtures:
                path = root / f"{document_id}.html"
                content = f"<html><body><p>{text}</p></body></html>".encode()
                path.write_bytes(content)
                app.execute(
                    """
                    INSERT INTO document_source VALUES (?, ?, 'statement', ?,
                        'evaluation_only', ?, ?)
                    """,
                    (
                        document_id,
                        f"FOMC-{publication_at}",
                        publication_at,
                        json.dumps({"local_path": str(path)}),
                        hashlib.sha256(content).hexdigest(),
                    ),
                )

            report = evaluate_statement_alerts(
                app,
                adopted_at="2021-04-28",
                contradiction_at="2021-05-12",
                as_of_date="2021-12-31",
                support_patterns=[r"support phrase"],
                flip_patterns=[r"flip phrase"],
            )

        self.assertEqual(report["statement_count"], 5)
        self.assertEqual(report["first_alert_at"], "2021-07-01")
        self.assertEqual(report["alert_event_count"], 1)
        self.assertEqual(report["pre_contradiction_flip_only_count"], 1)
        self.assertEqual(report["support_flip_cooccurrence_count"], 1)
        self.assertEqual(report["temporal_false_alarm_count"], 0)
        self.assertEqual(report["cooccurrence_false_alarm_count"], 0)
        self.assertEqual(report["post_alert_repeat_count"], 1)
        self.assertEqual(
            [row["classification"] for row in report["statements"]],
            [
                "SUPPORT",
                "SUPPRESSED_PRE_CONTRADICTION",
                "SUPPRESSED_SUPPORT_FLIP_COOCCURRENCE",
                "ALERT",
                "POST_ALERT_REPEAT",
            ],
        )

    def test_missing_prior_support_fails_closed(self):
        app = sqlite3.connect(":memory:")
        app.execute(
            """
            CREATE TABLE document_source (
                document_id TEXT PRIMARY KEY,
                meeting_id TEXT,
                document_type TEXT NOT NULL,
                publication_at TEXT NOT NULL,
                usage_class TEXT NOT NULL,
                source_locator TEXT NOT NULL,
                content_hash TEXT NOT NULL
            )
            """
        )
        with self.assertRaisesRegex(RuntimeError, "No registered support phrase"):
            evaluate_statement_alerts(
                app,
                adopted_at="2021-04-28",
                contradiction_at="2021-05-12",
                as_of_date="2021-12-31",
                support_patterns=[r"support phrase"],
                flip_patterns=[r"flip phrase"],
            )


if __name__ == "__main__":
    unittest.main()
