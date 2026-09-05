import sqlite3
import tempfile
import unittest
import urllib.error
from datetime import date, timedelta
from pathlib import Path

from fred_vintage_db import (
    FredApiError,
    FredClient,
    build_database,
    create_schema,
    insert_meetings,
    insert_observations,
    insert_series,
    materialize_meeting_snapshots,
    update_database,
)


class PointInTimeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        create_schema(self.connection)
        insert_series(
            self.connection,
            {
                "id": "TEST_GDP",
                "title": "Test GDP",
                "frequency": "Quarterly",
                "units": "Percent",
                "seasonal_adjustment": "Seasonally Adjusted",
                "observation_start": "2023-10-01",
                "observation_end": "2023-10-01",
            },
            release_id=None,
        )
        insert_observations(
            self.connection,
            "TEST_GDP",
            [
                {
                    "date": "2023-10-01",
                    "value": "3.3",
                    "realtime_start": "2024-01-25",
                    "realtime_end": "2024-02-27",
                },
                {
                    "date": "2023-10-01",
                    "value": "3.2",
                    "realtime_start": "2024-02-28",
                    "realtime_end": "2024-03-27",
                },
                {
                    "date": "2023-10-01",
                    "value": "3.4",
                    "realtime_start": "2024-03-28",
                    "realtime_end": "9999-12-31",
                },
            ],
        )
        insert_meetings(
            self.connection,
            [
                {
                    "meeting_id": "JAN",
                    "meeting_start_date": "2024-01-30",
                    "meeting_end_date": "2024-01-31",
                    "information_cutoff_date_et": "2024-01-29",
                },
                {
                    "meeting_id": "MAR",
                    "meeting_start_date": "2024-03-19",
                    "meeting_end_date": "2024-03-20",
                    "information_cutoff_date_et": "2024-03-18",
                },
            ],
        )

    def tearDown(self):
        self.connection.close()

    def test_each_meeting_uses_the_latest_version_visible_before_cutoff(self):
        materialize_meeting_snapshots(self.connection)

        rows = self.connection.execute(
            """
            SELECT meeting_id, value_raw, visible_version_date
            FROM v_meeting_information_set
            ORDER BY meeting_id
            """
        ).fetchall()

        self.assertEqual(
            rows,
            [
                ("JAN", "3.3", "2024-01-25"),
                ("MAR", "3.2", "2024-02-28"),
            ],
        )


class SnapshotWindowTests(unittest.TestCase):
    def test_snapshot_uses_requested_window_per_frequency(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        create_schema(connection)
        limits = {"D": 1008, "W": 208, "M": 120, "Q": 40}
        first_date = date(2020, 1, 1)
        try:
            for frequency_short, limit in limits.items():
                series_id = f"TEST_{frequency_short}"
                insert_series(
                    connection,
                    {
                        "id": series_id,
                        "title": series_id,
                        "frequency": frequency_short,
                        "frequency_short": frequency_short,
                        "units": "Index",
                    },
                    release_id=None,
                )
                insert_observations(
                    connection,
                    series_id,
                    [
                        {
                            "date": (first_date + timedelta(days=index)).isoformat(),
                            "value": str(index),
                            "realtime_start": (
                                first_date + timedelta(days=index)
                            ).isoformat(),
                            "realtime_end": "9999-12-31",
                        }
                        for index in range(limit + 1)
                    ],
                )

            insert_meetings(
                connection,
                [
                    {
                        "meeting_id": "WINDOW-TEST",
                        "meeting_start_date": "2024-01-30",
                        "meeting_end_date": "2024-01-31",
                        "information_cutoff_date_et": "2024-01-29",
                    }
                ],
            )
            materialize_meeting_snapshots(connection)
            actual = dict(
                connection.execute(
                    """
                    SELECT SUBSTR(series_id, 6), COUNT(*)
                    FROM meeting_snapshot_value
                    GROUP BY series_id
                    """
                ).fetchall()
            )
        finally:
            connection.close()

        self.assertEqual(actual, limits)


class IncrementalUpdateTests(unittest.TestCase):
    class FakeFredClient:
        def series_metadata(self, series_id):
            return {
                "id": series_id,
                "title": f"Test {series_id}",
                "frequency": "Monthly",
                "units": "Index",
                "seasonal_adjustment": "Seasonally Adjusted",
                "observation_start": "2024-01-01",
                "observation_end": "2024-01-01",
            }

        def series_release(self, series_id):
            return {
                "id": 1 if series_id == "SERIES_A" else 2,
                "name": f"Release {series_id}",
            }

        def release_dates(self, release_id, start_date):
            return []

        def observations(self, series_id, observation_start):
            return [
                {
                    "date": "2024-01-01",
                    "value": "1.0" if series_id == "SERIES_A" else "2.0",
                    "realtime_start": "2024-01-15",
                    "realtime_end": "9999-12-31",
                }
            ]

        def observations_with_mode(
            self,
            series_id,
            observation_start,
            allow_fred_only,
        ):
            return self.observations(series_id, observation_start), "ALFRED"

        def observation_batches_with_mode(
            self,
            series_id,
            observation_start,
            allow_fred_only,
            force_fred_only=False,
        ):
            observations, mode = self.observations_with_mode(
                series_id,
                observation_start,
                allow_fred_only,
            )
            return iter([observations]), mode

    def test_update_adds_missing_series_without_replacing_existing_database(self):
        meetings = [
            {
                "meeting_id": "TEST-MEETING",
                "meeting_start_date": "2024-01-30",
                "meeting_end_date": "2024-01-31",
                "information_cutoff_date_et": "2024-01-29",
            }
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "test.sqlite"
            client = self.FakeFredClient()
            build_database(
                database_path,
                client,
                ["SERIES_A"],
                meetings,
                "2000-01-01",
            )

            update_database(
                database_path,
                client,
                ["SERIES_A", "SERIES_B"],
                meetings,
                "2000-01-01",
            )

            connection = sqlite3.connect(database_path)
            try:
                series = connection.execute(
                    "SELECT series_id FROM economic_series ORDER BY series_id"
                ).fetchall()
                snapshot_count = connection.execute(
                    "SELECT COUNT(*) FROM meeting_snapshot_value"
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(series, [("SERIES_A",), ("SERIES_B",)])
            self.assertEqual(snapshot_count, 2)

    def test_update_records_calendar_through_date_when_provided(self):
        meetings = [
            {
                "meeting_id": "TEST-MEETING",
                "meeting_start_date": "2024-01-30",
                "meeting_end_date": "2024-01-31",
                "information_cutoff_date_et": "2024-01-29",
            }
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "test.sqlite"
            client = self.FakeFredClient()
            build_database(
                database_path,
                client,
                ["SERIES_A"],
                meetings,
                "2000-01-01",
            )

            update_database(
                database_path,
                client,
                ["SERIES_A"],
                meetings,
                "2000-01-01",
                calendar_through_date="2026-08-27",
            )

            connection = sqlite3.connect(database_path)
            try:
                through_date = connection.execute(
                    """
                    SELECT value
                    FROM database_metadata
                    WHERE key = 'fomc_calendar_through_date'
                    """
                ).fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(through_date, "2026-08-27")

    def test_strict_point_in_time_refreshes_existing_series_and_records_policy(self):
        meetings = [
            {
                "meeting_id": "TEST-MEETING",
                "meeting_start_date": "2024-01-30",
                "meeting_end_date": "2024-01-31",
                "information_cutoff_date_et": "2024-01-29",
            }
        ]

        class BackfillClient(self.FakeFredClient):
            def observations(self, series_id, observation_start):
                rows = super().observations(series_id, observation_start)
                if observation_start == "1996-01-01":
                    rows.append(
                        {
                            "date": "1996-01-01",
                            "value": "0.5",
                            "realtime_start": "1996-01-15",
                            "realtime_end": "9999-12-31",
                        }
                    )
                return rows

        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "test.sqlite"
            client = BackfillClient()
            build_database(
                database_path,
                client,
                ["SERIES_A"],
                meetings,
                "2000-01-01",
            )

            update_database(
                database_path,
                client,
                ["SERIES_A"],
                meetings,
                "1996-01-01",
                strict_point_in_time=True,
            )

            connection = sqlite3.connect(database_path)
            try:
                vintage_dates = connection.execute(
                    """
                    SELECT observation_date
                    FROM observation_vintage
                    WHERE series_id = 'SERIES_A'
                    ORDER BY observation_date
                    """
                ).fetchall()
                metadata = dict(
                    connection.execute(
                        """
                        SELECT key, value
                        FROM database_metadata
                        WHERE key IN (
                            'observation_start', 'point_in_time_mode',
                            'missing_vintage_policy'
                        )
                        """
                    ).fetchall()
                )
            finally:
                connection.close()

            self.assertEqual(vintage_dates, [("1996-01-01",), ("2024-01-01",)])
            self.assertEqual(metadata["observation_start"], "1996-01-01")
            self.assertEqual(metadata["point_in_time_mode"], "STRICT_AS_OF")
            self.assertEqual(metadata["missing_vintage_policy"], "PRESERVE_MISSING")

    def test_strict_refresh_backfills_existing_fred_only_series(self):
        meetings = [
            {
                "meeting_id": "TEST-MEETING",
                "meeting_start_date": "2024-01-30",
                "meeting_end_date": "2024-01-31",
                "information_cutoff_date_et": "2024-01-29",
            }
        ]

        class FredOnlyBackfillClient(self.FakeFredClient):
            def __init__(self):
                self.requests = []

            def series_metadata(self, series_id):
                metadata = super().series_metadata(series_id)
                metadata["frequency"] = "Daily"
                metadata["frequency_short"] = "D"
                return metadata

            def observation_batches_with_mode(
                self,
                series_id,
                observation_start,
                allow_fred_only,
                force_fred_only=False,
            ):
                self.requests.append(
                    (
                        series_id,
                        observation_start,
                        allow_fred_only,
                        force_fred_only,
                    )
                )
                observations = [
                    {
                        "date": "2024-01-01",
                        "value": "1.0",
                        "realtime_start": "2024-01-01",
                        "realtime_end": "9999-12-31",
                    }
                ]
                if observation_start <= "1996-01-02":
                    observations.insert(
                        0,
                        {
                            "date": "1996-01-02",
                            "value": "0.5",
                            "realtime_start": "1996-01-02",
                            "realtime_end": "9999-12-31",
                        },
                    )
                return iter([observations]), "FRED_ONLY_OBSERVATION_DATE"

        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "test.sqlite"
            client = FredOnlyBackfillClient()
            build_database(
                database_path,
                client,
                ["SERIES_A"],
                meetings,
                "2000-01-01",
            )
            client.requests.clear()
            counts = update_database(
                database_path,
                client,
                ["SERIES_A"],
                meetings,
                "1996-01-01",
                strict_point_in_time=True,
            )

            connection = sqlite3.connect(database_path)
            try:
                vintage_dates = connection.execute(
                    """
                    SELECT observation_date
                    FROM observation_vintage
                    WHERE series_id = 'SERIES_A'
                    ORDER BY observation_date
                    """
                ).fetchall()
                vintage_mode = connection.execute(
                    """
                    SELECT vintage_mode
                    FROM economic_series
                    WHERE series_id = 'SERIES_A'
                    """
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(counts["SERIES_A"], 2)
            self.assertEqual(vintage_dates, [("1996-01-02",), ("2024-01-01",)])
            self.assertEqual(vintage_mode, "FRED_ONLY_OBSERVATION_DATE")
            self.assertEqual(
                client.requests,
                [("SERIES_A", "1996-01-01", True, True)],
            )


class FredClientTests(unittest.TestCase):
    def test_daily_series_falls_back_to_chunked_realtime_periods(self):
        requests = []

        def fake_fetch(endpoint, parameters):
            requests.append(parameters.copy())
            if parameters["realtime_end"] == "9999-12-31":
                http_error = urllib.error.HTTPError(
                    "https://example.invalid",
                    400,
                    "Bad Request",
                    None,
                    None,
                )
                raise RuntimeError("FRED API request failed") from http_error
            return {
                "count": 1,
                "observations": [
                    {
                        "date": "2024-01-02",
                        "value": "4.25",
                        "realtime_start": "2024-01-02",
                        "realtime_end": "9999-12-31",
                    }
                ],
            }

        observations = FredClient("fake-key", fetch_json=fake_fetch).observations(
            "TEST_DAILY",
            "2024-01-01",
        )

        self.assertEqual(len(observations), 1)
        self.assertGreater(len(requests), 1)
        self.assertTrue(
            all(
                request["realtime_end"] != "9999-12-31"
                for request in requests[1:]
            )
        )

    def test_fred_only_daily_series_is_marked_and_uses_observation_date(self):
        def fake_fetch(endpoint, parameters):
            if "realtime_start" in parameters:
                raise FredApiError(
                    endpoint,
                    400,
                    "The series does not exist in ALFRED but may exist in FRED.",
                )
            return {
                "count": 1,
                "observations": [
                    {
                        "date": "2024-01-02",
                        "value": "4.25",
                        "realtime_start": "2026-08-21",
                        "realtime_end": "2026-08-21",
                    }
                ],
            }

        observations, vintage_mode = FredClient(
            "fake-key",
            fetch_json=fake_fetch,
        ).observations_with_mode(
            "TEST_DAILY",
            "2024-01-01",
            allow_fred_only=True,
        )

        self.assertEqual(vintage_mode, "FRED_ONLY_OBSERVATION_DATE")
        self.assertEqual(observations[0]["realtime_start"], "2024-01-02")
        self.assertEqual(observations[0]["realtime_end"], "9999-12-31")


class PolicyRateIngestionTests(unittest.TestCase):
    class PolicyRateClient:
        def __init__(self):
            self.requests = []

        def series_metadata(self, series_id):
            return {
                "id": series_id,
                "title": f"Test {series_id}",
                "frequency": "Daily",
                "frequency_short": "D",
                "units": "Percent",
            }

        def series_release(self, series_id):
            return {"id": 1, "name": "Federal Funds Target Rate"}

        def release_dates(self, release_id, start_date):
            return []

        def observation_batches_with_mode(
            self,
            series_id,
            observation_start,
            allow_fred_only,
            force_fred_only=False,
        ):
            self.requests.append((series_id, allow_fred_only, force_fred_only))
            if not force_fred_only:
                raise FredApiError(
                    "/fred/series/observations",
                    400,
                    "The series does not exist in ALFRED but may exist in FRED.",
                )
            return (
                iter(
                    [
                        [
                            {
                                "date": "2008-12-16",
                                "value": "0.25",
                                "realtime_start": "2008-12-16",
                                "realtime_end": "9999-12-31",
                            }
                        ]
                    ]
                ),
                "FRED_ONLY_OBSERVATION_DATE",
            )

    def test_policy_rate_series_force_fred_only_ingestion(self):
        meetings = [
            {
                "meeting_id": "TEST-MEETING",
                "meeting_start_date": "2008-12-17",
                "meeting_end_date": "2008-12-17",
                "information_cutoff_date_et": "2008-12-16",
            }
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "test.sqlite"
            client = self.PolicyRateClient()
            build_database(
                database_path,
                client,
                ["DFEDTARU"],
                meetings,
                "1996-01-01",
            )

            connection = sqlite3.connect(database_path)
            try:
                vintage_mode = connection.execute(
                    """
                    SELECT vintage_mode
                    FROM economic_series
                    WHERE series_id = 'DFEDTARU'
                    """
                ).fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(client.requests, [("DFEDTARU", True, True)])
        self.assertEqual(vintage_mode, "FRED_ONLY_OBSERVATION_DATE")


if __name__ == "__main__":
    unittest.main()
