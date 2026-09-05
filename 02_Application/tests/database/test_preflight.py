import sqlite3
import unittest
from pathlib import Path

from decision_memory.preflight import (
    assert_source_ready,
    audit_source_connection,
)
from fred_vintage_db import create_schema


class SourcePreflightTests(unittest.TestCase):
    def test_empty_source_schema_fails_closed(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        create_schema(connection)
        try:
            report = audit_source_connection(connection)
        finally:
            connection.close()

        self.assertEqual(len(report["missing_series"]), 22)
        with self.assertRaisesRegex(RuntimeError, "missing_series"):
            assert_source_ready(report)

    def test_real_source_database_passes_r5_data_gates(self):
        database_path = Path("fred_fomc_real.sqlite")
        if not database_path.is_file():
            self.skipTest("real source database is not present")
        connection = sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro",
            uri=True,
        )
        try:
            report = audit_source_connection(connection)
        finally:
            connection.close()

        assert_source_ready(report)
        self.assertEqual(report["series_count"], 22)
        self.assertEqual(report["meeting_count"], 166)
        self.assertEqual(report["pre_range_coverage"], 24)
        self.assertEqual(report["constraint_episode_counts"], [55, 15])
        self.assertEqual(report["general_snapshot_max"], 6816)
        self.assertLessEqual(report["policy_context_max"], 9)


if __name__ == "__main__":
    unittest.main()
