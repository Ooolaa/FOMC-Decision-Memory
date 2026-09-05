import unittest

from build_real_fred_db import DEFAULT_SERIES


class DefaultSeriesTests(unittest.TestCase):
    def test_includes_all_three_policy_rate_series_without_duplicates(self):
        self.assertEqual(len(DEFAULT_SERIES), 22)
        self.assertEqual(len(DEFAULT_SERIES), len(set(DEFAULT_SERIES)))
        self.assertTrue(
            {"DFEDTAR", "DFEDTARU", "DFEDTARL"}.issubset(DEFAULT_SERIES)
        )


if __name__ == "__main__":
    unittest.main()
