import unittest

from decision_memory.decision_trace import assumption_monitor_violations


SERIES_METADATA = {
    "CPIAUCSL": {
        "series_id": "CPIAUCSL",
        "title": "Consumer Price Index for All Urban Consumers: All Items",
        "frequency": "Monthly",
        "units": "Index 1982-1984=100",
        "vintage_mode": "ALFRED_VINTAGE",
    },
    "PAYEMS": {
        "series_id": "PAYEMS",
        "title": "All Employees, Total Nonfarm",
        "frequency": "Monthly",
        "units": "Thousands of Persons",
        "vintage_mode": "ALFRED_VINTAGE",
    },
    "PCEPILFE": {
        "series_id": "PCEPILFE",
        "title": "Personal Consumption Expenditures Excluding Food and Energy",
        "frequency": "Monthly",
        "units": "Index 2017=100",
        "vintage_mode": "ALFRED_VINTAGE",
    },
    "T5YIFR": {
        "series_id": "T5YIFR",
        "title": "5-Year, 5-Year Forward Inflation Expectation Rate",
        "frequency": "Daily",
        "units": "Percent",
        "vintage_mode": "FRED_ONLY_OBSERVATION_DATE",
    },
    "UNRATE": {
        "series_id": "UNRATE",
        "title": "Unemployment Rate",
        "frequency": "Monthly",
        "units": "Percent",
        "vintage_mode": "ALFRED_VINTAGE",
    },
}


def assumption(
    series_id: str,
    claim: str,
    operator: str,
    threshold: float,
    rule: str = "level_threshold_v1",
) -> dict:
    return {
        "assumption_id": "test-assumption",
        "claim": claim,
        "monitor_series_id": series_id,
        "monitor_operator": operator,
        "threshold_value": threshold,
        "direction_map_version": "test-direction-v1",
        "monitor_rule_version": rule,
        "evidence_refs": [],
    }


class AssumptionMonitorSemanticTests(unittest.TestCase):
    def test_price_index_compared_with_percent_requires_yoy_transform(self):
        item = assumption(
            "CPIAUCSL",
            "Inflation remains above 2 percent.",
            "GT",
            2.0,
            rule="v1",
        )

        self.assertEqual(
            assumption_monitor_violations(item, SERIES_METADATA),
            ["index_percent_threshold_requires_yoy_transform"],
        )

    def test_price_index_yoy_rule_is_valid(self):
        item = assumption(
            "CPIAUCSL",
            "Twelve-month inflation remains above 2 percent.",
            "GT",
            2.0,
            rule="yoy_percent_change_v1",
        )

        self.assertEqual(assumption_monitor_violations(item, SERIES_METADATA), [])

    def test_nonnegative_level_gte_zero_is_tautological(self):
        item = assumption(
            "PAYEMS",
            "Payroll employment will continue to improve.",
            "GTE",
            0.0,
        )

        self.assertEqual(
            assumption_monitor_violations(item, SERIES_METADATA),
            [
                "nonnegative_level_threshold_is_tautological",
                "temporal_path_requires_atomic_rewrite",
            ],
        )

    def test_numeric_range_cannot_be_encoded_by_one_sided_threshold(self):
        item = assumption(
            "UNRATE",
            "Unemployment remains in a 4.5 to 5 percent range.",
            "LTE",
            5.0,
        )

        self.assertEqual(
            assumption_monitor_violations(item, SERIES_METADATA),
            ["range_or_symmetric_claim_requires_atomic_rewrite"],
        )

    def test_symmetric_target_cannot_be_encoded_by_one_sided_threshold(self):
        item = assumption(
            "T5YIFR",
            "Inflation expectations remain symmetric around 2 percent.",
            "GTE",
            2.0,
        )

        self.assertEqual(
            assumption_monitor_violations(item, SERIES_METADATA),
            ["range_or_symmetric_claim_requires_atomic_rewrite"],
        )

    def test_compound_cross_series_claim_requires_atomic_rewrite(self):
        item = assumption(
            "UNRATE",
            "Unemployment remains above 7.5 percent with subdued inflation.",
            "GT",
            7.5,
        )

        self.assertEqual(
            assumption_monitor_violations(item, SERIES_METADATA),
            ["compound_claim_requires_atomic_rewrite"],
        )

    def test_peak_then_decline_path_requires_atomic_rewrite(self):
        item = assumption(
            "UNRATE",
            "Unemployment peaks near 18 percent and then declines.",
            "LTE",
            18.0,
        )

        self.assertEqual(
            assumption_monitor_violations(item, SERIES_METADATA),
            ["temporal_path_requires_atomic_rewrite"],
        )

    def test_atomic_level_threshold_is_valid(self):
        item = assumption(
            "UNRATE",
            "Unemployment remains above 6.5 percent.",
            "GT",
            6.5,
        )

        self.assertEqual(assumption_monitor_violations(item, SERIES_METADATA), [])


if __name__ == "__main__":
    unittest.main()
