import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.app_db import create_schema, seed_enterprise_demo
from decision_memory.review_actions import (
    complete_review,
    get_assumption_status,
    request_review,
)


class ReviewActionTests(unittest.TestCase):
    def test_file_backed_review_actions_complete_the_enterprise_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "app.sqlite"
            connection = sqlite3.connect(database_path)
            connection.execute("PRAGMA foreign_keys = ON")
            create_schema(connection)
            ids = seed_enterprise_demo(
                connection,
                contradiction_at="2026-08-01T00:00:00Z",
            )
            connection.commit()
            connection.close()

            requested = request_review(
                database_path,
                ids["assumption_id"],
                actor="demo-user",
                occurred_at="2026-08-03T00:00:00Z",
            )
            completed = complete_review(
                database_path,
                ids["assumption_id"],
                actor="demo-user",
                occurred_at="2026-08-05T12:00:00Z",
            )
            status = get_assumption_status(database_path, ids["assumption_id"])

        self.assertEqual(requested["state"], "REVIEW_REQUESTED")
        self.assertEqual(completed["state"], "REVIEWED")
        self.assertEqual(status["workflow_recognition_lag_seconds"], 388800)
        self.assertEqual(status["event_count"], 3)
        self.assertTrue(status["synthetic"])
        self.assertTrue(status["composite"])

    def test_unknown_assumption_fails_without_creating_an_event(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "app.sqlite"
            connection = sqlite3.connect(database_path)
            create_schema(connection)
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(ValueError, "Unknown assumption_id"):
                request_review(
                    database_path,
                    "missing",
                    actor="demo-user",
                    occurred_at="2026-08-03T00:00:00Z",
                )

            connection = sqlite3.connect(database_path)
            try:
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM assumption_event"
                ).fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(event_count, 0)


if __name__ == "__main__":
    unittest.main()
