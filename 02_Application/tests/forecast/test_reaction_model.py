import sqlite3
import unittest

from decision_memory.app_db import create_schema as create_app_schema
from decision_memory.reaction_model import (
    build_meeting_feature_row,
    predict_ordered_logit,
)
from fred_vintage_db import (
    create_schema as create_source_schema,
    insert_meetings,
    insert_observations,
    insert_series,
    materialize_meeting_snapshots,
)


class ReactionModelFeatureTests(unittest.TestCase):
    def test_prediction_probabilities_are_ordered_and_sum_to_one(self):
        artifact = {
            "features": ["x"],
            "means": {"x": 0.0},
            "scales": {"x": 1.0},
            "coefficients": {"x": 1.0},
            "cutpoints": [-1.0, 1.0],
        }

        prediction = predict_ordered_logit({"x": 2.0}, artifact)

        self.assertEqual(prediction["action_class"], "HIKE")
        self.assertAlmostEqual(sum(prediction["probabilities"].values()), 1.0)

    def test_features_use_only_snapshot_vintages_and_compact_policy_context(self):
        source = sqlite3.connect(":memory:")
        create_source_schema(source)
        frequencies = {
            "CPIAUCSL": ("Monthly", "M"),
            "UNRATE": ("Monthly", "M"),
            "PAYEMS": ("Monthly", "M"),
            "BAA10Y": ("Daily", "D"),
            "DGS10": ("Daily", "D"),
            "DGS2": ("Daily", "D"),
        }
        for series_id, (frequency, short) in frequencies.items():
            insert_series(
                source,
                {
                    "id": series_id,
                    "title": series_id,
                    "frequency": frequency,
                    "frequency_short": short,
                    "units": "Index",
                },
                release_id=None,
                vintage_mode="ALFRED",
            )
        insert_meetings(
            source,
            [{
                "meeting_id": "FOMC-2020-12-15",
                "meeting_start_date": "2020-12-15",
                "meeting_end_date": "2020-12-16",
                "information_cutoff_date_et": "2020-12-14",
                "cutoff_policy": "previous_calendar_day",
                "calendar_source_url": "https://www.federalreserve.gov/",
            }],
        )
        observations = {
            "CPIAUCSL": [("2019-11-01", 100.0), ("2020-11-01", 103.0)],
            "UNRATE": [("2019-11-01", 4.0), ("2020-11-01", 6.0)],
            "PAYEMS": [("2019-11-01", 150.0), ("2020-11-01", 147.0)],
            "BAA10Y": [("2020-12-11", 2.1)],
            "DGS10": [("2020-12-11", 1.0)],
            "DGS2": [("2020-12-11", 0.2)],
        }
        for series_id, values in observations.items():
            insert_observations(
                source,
                series_id,
                [
                    {
                        "date": observation_date,
                        "value": str(value),
                        "realtime_start": observation_date,
                        "realtime_end": "9999-12-31",
                    }
                    for observation_date, value in values
                ],
            )
        materialize_meeting_snapshots(source)

        app = sqlite3.connect(":memory:")
        create_app_schema(app)
        app.execute(
            """
            INSERT INTO policy_rate_context (
                meeting_id, ordinal, record_kind, cutoff_date, effective_date,
                regime, direction, target_rate, lower_rate, upper_rate,
                regime_started_at, regime_duration_days, source_series_ids_json,
                rule_version, source_hash
            ) VALUES (
                'FOMC-2020-12-15', 0, 'CURRENT', '2020-12-14', '2020-12-14',
                'TARGET_RANGE', 'HOLD', NULL, 0, 0.25,
                '2020-03-16', 273, '["DFEDTARL","DFEDTARU"]',
                'policy_rate_context_v1', 'hash'
            )
            """
        )
        try:
            row = build_meeting_feature_row(source, app, "FOMC-2020-12-15")
        finally:
            source.close()
            app.close()

        self.assertAlmostEqual(row["cpi_yoy"], 3.0)
        self.assertAlmostEqual(row["unemployment_level"], 6.0)
        self.assertAlmostEqual(row["unemployment_12m_change"], 2.0)
        self.assertAlmostEqual(row["payroll_yoy"], -2.0)
        self.assertAlmostEqual(row["credit_spread_baa10y"], 2.1)
        self.assertAlmostEqual(row["yield_curve_10y_2y"], 0.8)
        self.assertAlmostEqual(row["policy_midpoint"], 0.125)


if __name__ == "__main__":
    unittest.main()
