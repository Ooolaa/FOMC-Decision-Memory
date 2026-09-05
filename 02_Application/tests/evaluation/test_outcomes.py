import sqlite3
import unittest

from decision_memory.outcomes import derive_rate_outcome
from fred_vintage_db import (
    create_schema,
    insert_meetings,
    insert_observations,
    insert_series,
)


class MeetingOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        create_schema(self.connection)
        for series_id in ("DFEDTARL", "DFEDTARU"):
            insert_series(
                self.connection,
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
        insert_meetings(
            self.connection,
            [
                {
                    "meeting_id": "FOMC-2022-03-15",
                    "meeting_start_date": "2022-03-15",
                    "meeting_end_date": "2022-03-16",
                    "information_cutoff_date_et": "2022-03-14",
                    "cutoff_policy": "previous_calendar_day",
                    "calendar_source_url": "https://www.federalreserve.gov/",
                }
            ],
        )
        for series_id, before, after in [
            ("DFEDTARL", 0.0, 0.25),
            ("DFEDTARU", 0.25, 0.5),
        ]:
            insert_observations(
                self.connection,
                series_id,
                [
                    {
                        "date": "2022-03-14",
                        "value": str(before),
                        "realtime_start": "2022-03-14",
                        "realtime_end": "9999-12-31",
                    },
                    {
                        "date": "2022-03-17",
                        "value": str(after),
                        "realtime_start": "2022-03-17",
                        "realtime_end": "9999-12-31",
                    },
                ],
            )

    def tearDown(self):
        self.connection.close()

    def test_range_change_uses_cutoff_and_first_post_meeting_effective_value(self):
        outcome = derive_rate_outcome(self.connection, "FOMC-2022-03-15")

        self.assertEqual(outcome["action_class"], "HIKE")
        self.assertEqual(outcome["target_lower"], 0.25)
        self.assertEqual(outcome["target_upper"], 0.5)
        self.assertEqual(outcome["pre_target_lower"], 0.0)
        self.assertEqual(outcome["pre_target_upper"], 0.25)
        self.assertEqual(outcome["outcome_effective_date"], "2022-03-17")
        self.assertEqual(outcome["rule_version"], "rate_delta_v1")

    def test_2008_transition_compares_single_target_to_same_day_target_range(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        create_schema(connection)
        for series_id in ("DFEDTAR", "DFEDTARL", "DFEDTARU"):
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
        insert_meetings(
            connection,
            [{
                "meeting_id": "FOMC-2008-12-15",
                "meeting_start_date": "2008-12-15",
                "meeting_end_date": "2008-12-16",
                "information_cutoff_date_et": "2008-12-14",
                "cutoff_policy": "previous_calendar_day",
                "calendar_source_url": "https://www.federalreserve.gov/",
            }],
        )
        insert_observations(
            connection,
            "DFEDTAR",
            [{
                "date": "2008-12-14",
                "value": "1.0",
                "realtime_start": "2008-12-14",
                "realtime_end": "9999-12-31",
            }],
        )
        for series_id, value in (("DFEDTARL", 0.0), ("DFEDTARU", 0.25)):
            insert_observations(
                connection,
                series_id,
                [{
                    "date": "2008-12-16",
                    "value": str(value),
                    "realtime_start": "2008-12-16",
                    "realtime_end": "9999-12-31",
                }],
            )
        try:
            outcome = derive_rate_outcome(connection, "FOMC-2008-12-15")
        finally:
            connection.close()

        self.assertEqual(outcome["action_class"], "CUT")
        self.assertEqual(outcome["pre_target_rate"], 1.0)
        self.assertEqual(outcome["target_lower"], 0.0)
        self.assertEqual(outcome["target_upper"], 0.25)
        self.assertEqual(outcome["outcome_effective_date"], "2008-12-16")


if __name__ == "__main__":
    unittest.main()
