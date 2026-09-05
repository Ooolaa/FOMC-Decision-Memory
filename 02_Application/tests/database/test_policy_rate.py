import sqlite3
import unittest

from decision_memory.app_db import create_schema as create_app_schema
from decision_memory.policy_rate import (
    build_policy_rate_context,
    replace_policy_rate_context,
)
from fred_vintage_db import (
    create_schema,
    insert_meetings,
    insert_observations,
    insert_series,
)


def add_policy_series(connection, series_id, observations):
    insert_series(
        connection,
        {
            "id": series_id,
            "title": series_id,
            "frequency": "Daily",
            "frequency_short": "D",
            "units": "Percent",
        },
        release_id=None,
        vintage_mode="FRED_ONLY_OBSERVATION_DATE",
    )
    insert_observations(
        connection,
        series_id,
        [
            {
                "date": observation_date,
                "value": str(value),
                "realtime_start": observation_date,
                "realtime_end": "9999-12-31",
            }
            for observation_date, value in observations
        ],
    )


class PolicyRateContextTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        create_schema(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_single_target_uses_latest_value_visible_before_cutoff(self):
        add_policy_series(
            self.connection,
            "DFEDTAR",
            [
                ("2008-10-08", 1.5),
                ("2008-10-29", 1.0),
                ("2008-12-15", 0.5),
            ],
        )
        insert_meetings(
            self.connection,
            [
                {
                    "meeting_id": "FOMC-2008-12-15",
                    "meeting_start_date": "2008-12-15",
                    "meeting_end_date": "2008-12-16",
                    "information_cutoff_date_et": "2008-12-14",
                }
            ],
        )

        records = build_policy_rate_context(
            self.connection,
            "FOMC-2008-12-15",
        )

        self.assertLessEqual(len(records), 9)
        self.assertEqual(records[0]["record_kind"], "CURRENT")
        self.assertEqual(records[0]["target_rate"], 1.0)
        self.assertIsNone(records[0]["lower_rate"])
        self.assertTrue(
            all(record["effective_date"] <= "2008-12-14" for record in records)
        )

    def test_range_context_uses_cycle_start_and_excludes_post_cutoff_change(self):
        lower = [
            ("2020-03-16", 0.0),
            ("2022-03-17", 0.25),
            ("2022-05-05", 0.75),
            ("2022-06-16", 1.5),
            ("2022-07-28", 2.25),
            ("2022-09-21", 3.0),
        ]
        upper = [
            ("2020-03-16", 0.25),
            ("2022-03-17", 0.5),
            ("2022-05-05", 1.0),
            ("2022-06-16", 1.75),
            ("2022-07-28", 2.5),
            ("2022-09-21", 3.25),
        ]
        add_policy_series(self.connection, "DFEDTARL", lower)
        add_policy_series(self.connection, "DFEDTARU", upper)
        insert_meetings(
            self.connection,
            [
                {
                    "meeting_id": "FOMC-2022-09-20",
                    "meeting_start_date": "2022-09-20",
                    "meeting_end_date": "2022-09-21",
                    "information_cutoff_date_et": "2022-09-19",
                }
            ],
        )

        records = build_policy_rate_context(
            self.connection,
            "FOMC-2022-09-20",
        )

        current = records[0]
        self.assertEqual((current["lower_rate"], current["upper_rate"]), (2.25, 2.5))
        self.assertEqual(current["direction"], "UP")
        self.assertEqual(current["regime_started_at"], "2022-03-17")
        self.assertEqual(current["regime_duration_days"], 186)
        self.assertTrue(
            all(record["effective_date"] <= "2022-09-19" for record in records)
        )

    def test_lower_bound_duration_starts_when_range_first_touches_bound(self):
        add_policy_series(
            self.connection,
            "DFEDTARL",
            [("2020-03-02", 1.0), ("2020-03-16", 0.0)],
        )
        add_policy_series(
            self.connection,
            "DFEDTARU",
            [("2020-03-02", 1.25), ("2020-03-16", 0.25)],
        )
        insert_meetings(
            self.connection,
            [
                {
                    "meeting_id": "FOMC-2021-01-26",
                    "meeting_start_date": "2021-01-26",
                    "meeting_end_date": "2021-01-27",
                    "information_cutoff_date_et": "2021-01-25",
                }
            ],
        )

        current = build_policy_rate_context(
            self.connection,
            "FOMC-2021-01-26",
        )[0]

        self.assertEqual(current["regime"], "LOWER_BOUND")
        self.assertEqual(current["regime_started_at"], "2020-03-16")
        self.assertEqual(current["regime_duration_days"], 315)

    def test_compact_context_can_be_persisted_in_the_derived_app_database(self):
        add_policy_series(
            self.connection,
            "DFEDTAR",
            [("2007-09-18", 4.75), ("2007-10-31", 4.5)],
        )
        insert_meetings(
            self.connection,
            [
                {
                    "meeting_id": "FOMC-2007-12-11",
                    "meeting_start_date": "2007-12-11",
                    "meeting_end_date": "2007-12-11",
                    "information_cutoff_date_et": "2007-12-10",
                }
            ],
        )
        app_connection = sqlite3.connect(":memory:")
        app_connection.execute("PRAGMA foreign_keys = ON")
        create_app_schema(app_connection)
        try:
            records = build_policy_rate_context(
                self.connection,
                "FOMC-2007-12-11",
            )
            replace_policy_rate_context(app_connection, records)
            stored = app_connection.execute(
                """
                SELECT ordinal, record_kind, target_rate, rule_version
                FROM policy_rate_context
                WHERE meeting_id = 'FOMC-2007-12-11'
                ORDER BY ordinal
                """
            ).fetchall()
        finally:
            app_connection.close()

        self.assertLessEqual(len(stored), 9)
        self.assertEqual(stored[0][0:3], (0, "CURRENT", 4.5))
        self.assertTrue(all(row[3] == "policy_rate_context_v1" for row in stored))


if __name__ == "__main__":
    unittest.main()
