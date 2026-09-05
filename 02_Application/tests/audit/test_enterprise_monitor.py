import json
import sqlite3
import unittest

from decision_memory.app_db import create_schema as create_app_schema
from decision_memory.enterprise import seed_enterprise_fixture_from_source
from fred_vintage_db import (
    create_schema as create_source_schema,
    insert_observations,
    insert_series,
)


class EnterpriseMonitorTests(unittest.TestCase):
    def test_first_baa10y_crossing_seeds_a_deterministic_contradiction(self):
        source = sqlite3.connect(":memory:")
        source.execute("PRAGMA foreign_keys = ON")
        create_source_schema(source)
        insert_series(
            source,
            {
                "id": "BAA10Y",
                "title": "Moody's Seasoned Baa Corporate Bond Yield Relative to 10-Year Treasury",
                "frequency": "Daily",
                "frequency_short": "D",
                "units": "Percent",
            },
            release_id=None,
            vintage_mode="FRED_ONLY_OBSERVATION_DATE",
        )
        insert_observations(
            source,
            "BAA10Y",
            [
                {
                    "date": observation_date,
                    "value": str(value),
                    "realtime_start": observation_date,
                    "realtime_end": "9999-12-31",
                }
                for observation_date, value in [
                    ("2021-06-01", 2.2),
                    ("2022-02-25", 2.24),
                    ("2022-02-28", 2.34),
                    ("2022-03-01", 2.4),
                ]
            ],
        )

        app = sqlite3.connect(":memory:")
        app.execute("PRAGMA foreign_keys = ON")
        create_app_schema(app)
        try:
            ids = seed_enterprise_fixture_from_source(
                source,
                app,
                decision_date="2021-06-01",
                threshold_value=2.25,
            )
            assumption = app.execute(
                """
                SELECT monitor_series_id, monitor_operator, threshold_value,
                       monitor_rule_version
                FROM decision_assumption
                WHERE assumption_id = ?
                """,
                (ids["assumption_id"],),
            ).fetchone()
            contradiction = app.execute(
                """
                SELECT occurred_at, payload_json
                FROM assumption_event
                WHERE assumption_id = ? AND event_type = 'CONTRADICTION'
                """,
                (ids["assumption_id"],),
            ).fetchone()
        finally:
            app.close()
            source.close()

        self.assertEqual(
            assumption,
            ("BAA10Y", "GT", 2.25, "baa10y_upper_bound_v1"),
        )
        self.assertEqual(contradiction[0], "2022-02-28T00:00:00Z")
        self.assertEqual(json.loads(contradiction[1])["value"], 2.34)


if __name__ == "__main__":
    unittest.main()
