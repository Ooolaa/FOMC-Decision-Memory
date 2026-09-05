import sqlite3
import unittest
from pathlib import Path

from decision_memory.lag_spec import (
    constraint_episode_counts,
    frozen_constraint_audit,
    is_rate_constrained,
    load_rate_only_spec,
)


SPEC_PATH = Path("metric_spec/rate_only_response_v1.json")


class RateOnlyLagSpecTests(unittest.TestCase):
    def test_boundaries_and_checksum_are_frozen(self):
        spec = load_rate_only_spec(SPEC_PATH)

        self.assertEqual(spec["spec_id"], "rate_only_response_v1")
        self.assertEqual(
            [
                (episode["start"], episode["end"], episode["expected_meetings"])
                for episode in spec["constraint_episodes"]
            ],
            [
                ("2009-01-27", "2015-10-27", 55),
                ("2020-04-28", "2022-01-25", 15),
            ],
        )
        self.assertEqual(spec["expected_constrained_meetings"], 70)
        self.assertFalse(spec["non_rate_tools_close_event"])

    def test_episode_membership_is_inclusive_only_at_registered_boundaries(self):
        spec = load_rate_only_spec(SPEC_PATH)

        self.assertFalse(is_rate_constrained("2008-12-16", spec))
        self.assertTrue(is_rate_constrained("2009-01-27", spec))
        self.assertTrue(is_rate_constrained("2015-10-27", spec))
        self.assertFalse(is_rate_constrained("2015-12-15", spec))
        self.assertFalse(is_rate_constrained("2020-03-15", spec))
        self.assertTrue(is_rate_constrained("2020-04-28", spec))
        self.assertTrue(is_rate_constrained("2022-01-25", spec))
        self.assertFalse(is_rate_constrained("2022-03-15", spec))

    def test_real_source_database_reproduces_55_plus_15(self):
        database_path = Path("fred_fomc_real.sqlite")
        if not database_path.is_file():
            self.skipTest("real source database is not present")
        connection = sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro",
            uri=True,
        )
        try:
            counts = constraint_episode_counts(
                connection,
                load_rate_only_spec(SPEC_PATH),
            )
        finally:
            connection.close()

        self.assertEqual(counts, [55, 15])
        self.assertEqual(sum(counts), 70)

    def test_frozen_split_reproduces_nine_of_forty_five_constrained_cases(self):
        database_path = Path("fred_fomc_real.sqlite")
        if not database_path.is_file():
            self.skipTest("real source database is not present")
        connection = sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro",
            uri=True,
        )
        try:
            report = frozen_constraint_audit(
                connection,
                load_rate_only_spec(SPEC_PATH),
                test_start="2021-01-01",
            )
        finally:
            connection.close()

        self.assertEqual(report["case_count"], 45)
        self.assertEqual(report["constrained_case_count"], 9)
        self.assertEqual(report["observed_rate_capacity_case_count"], 36)


if __name__ == "__main__":
    unittest.main()
