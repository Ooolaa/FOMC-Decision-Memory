import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.bootstrap import bootstrap_app_database
from fred_vintage_db import (
    create_schema,
    insert_meetings,
    insert_observations,
    insert_series,
)


class BootstrapTests(unittest.TestCase):
    def test_builds_derived_database_without_mutating_source(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "source.sqlite"
            output_path = root / "app.sqlite"
            source = sqlite3.connect(source_path)
            source.execute("PRAGMA foreign_keys = ON")
            create_schema(source)
            insert_series(
                source,
                {
                    "id": "DFEDTAR",
                    "title": "Federal Funds Target Rate",
                    "frequency": "Daily",
                    "frequency_short": "D",
                    "units": "Percent",
                },
                release_id=None,
                vintage_mode="FRED_ONLY_OBSERVATION_DATE",
            )
            insert_observations(
                source,
                "DFEDTAR",
                [
                    {
                        "date": "2007-10-31",
                        "value": "4.5",
                        "realtime_start": "2007-10-31",
                        "realtime_end": "9999-12-31",
                    }
                ],
            )
            insert_meetings(
                source,
                [
                    {
                        "meeting_id": "FOMC-2007-12-11",
                        "meeting_start_date": "2007-12-11",
                        "meeting_end_date": "2007-12-11",
                        "information_cutoff_date_et": "2007-12-10",
                    }
                ],
            )
            source.commit()
            source_table_count_before = source.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()[0]
            source.close()

            report = bootstrap_app_database(source_path, output_path)

            source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
            app = sqlite3.connect(f"file:{output_path.as_posix()}?mode=ro", uri=True)
            try:
                source_tables = source.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
                ).fetchone()[0]
                policy_rows = app.execute(
                    "SELECT COUNT(*) FROM policy_rate_context"
                ).fetchone()[0]
            finally:
                source.close()
                app.close()

        self.assertEqual(report["meetings"], 1)
        self.assertGreaterEqual(policy_rows, 1)
        self.assertEqual(source_tables, source_table_count_before)


if __name__ == "__main__":
    unittest.main()
