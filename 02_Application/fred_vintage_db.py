from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


FRED_API_BASE = "https://api.stlouisfed.org"
SNAPSHOT_WINDOW_LIMITS = {"D": 1008, "W": 208, "M": 120, "Q": 40}
SNAPSHOT_SELECTION_POLICY = "latest_visible_window_D1008_W208_M120_Q40"
FRED_ONLY_SERIES_IDS = frozenset({"DFEDTAR", "DFEDTARU", "DFEDTARL"})


class FredApiError(RuntimeError):
    def __init__(self, endpoint: str, status_code: int, api_message: str) -> None:
        super().__init__(f"FRED API {status_code} for {endpoint}: {api_message}")
        self.endpoint = endpoint
        self.status_code = status_code
        self.api_message = api_message


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE database_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE economic_release (
    release_id   INTEGER PRIMARY KEY,
    release_name TEXT NOT NULL,
    source_url   TEXT
);

CREATE TABLE release_event (
    release_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id       INTEGER NOT NULL,
    release_date     TEXT NOT NULL,
    scheduled_at_utc TEXT,
    time_precision   TEXT NOT NULL
        CHECK (time_precision IN ('date', 'minute')),
    fetched_at_utc   TEXT NOT NULL,
    FOREIGN KEY (release_id) REFERENCES economic_release(release_id),
    UNIQUE (release_id, release_date)
);

CREATE TABLE economic_series (
    series_id                   TEXT PRIMARY KEY,
    title                       TEXT NOT NULL,
    release_id                  INTEGER,
    frequency                   TEXT NOT NULL,
    frequency_short             TEXT,
    units                       TEXT NOT NULL,
    units_short                 TEXT,
    seasonal_adjustment         TEXT,
    seasonal_adjustment_short   TEXT,
    observation_start           TEXT,
    observation_end             TEXT,
    upstream_last_updated       TEXT,
    notes                       TEXT,
    vintage_mode                TEXT NOT NULL DEFAULT 'ALFRED'
        CHECK (vintage_mode IN ('ALFRED', 'FRED_ONLY_OBSERVATION_DATE')),
    FOREIGN KEY (release_id) REFERENCES economic_release(release_id)
);

CREATE TABLE observation_vintage (
    series_id          TEXT NOT NULL,
    observation_date   TEXT NOT NULL,
    realtime_start     TEXT NOT NULL,
    realtime_end       TEXT NOT NULL,
    value_raw          TEXT NOT NULL,
    value_num          REAL,
    is_initial_release INTEGER NOT NULL
        CHECK (is_initial_release IN (0, 1)),
    release_event_id   INTEGER,
    fetched_at_utc     TEXT NOT NULL,
    source_hash        TEXT NOT NULL,
    PRIMARY KEY (series_id, observation_date, realtime_start),
    FOREIGN KEY (series_id) REFERENCES economic_series(series_id),
    FOREIGN KEY (release_event_id) REFERENCES release_event(release_event_id),
    CHECK (realtime_end >= realtime_start)
);

CREATE TABLE fomc_meeting (
    meeting_id                 TEXT PRIMARY KEY,
    meeting_start_date         TEXT NOT NULL,
    meeting_end_date           TEXT NOT NULL,
    information_cutoff_date_et TEXT NOT NULL,
    cutoff_policy              TEXT NOT NULL,
    calendar_source_url        TEXT NOT NULL
);

CREATE TABLE meeting_snapshot_value (
    meeting_id        TEXT NOT NULL,
    series_id         TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    realtime_start   TEXT NOT NULL,
    selection_policy TEXT NOT NULL,
    created_at_utc   TEXT NOT NULL,
    PRIMARY KEY (meeting_id, series_id, observation_date),
    FOREIGN KEY (meeting_id) REFERENCES fomc_meeting(meeting_id),
    FOREIGN KEY (series_id, observation_date, realtime_start)
        REFERENCES observation_vintage(series_id, observation_date, realtime_start)
);

CREATE INDEX ix_vintage_asof
    ON observation_vintage(series_id, observation_date, realtime_start);

CREATE INDEX ix_release_event_date
    ON release_event(release_date);

CREATE INDEX ix_snapshot_meeting_series
    ON meeting_snapshot_value(meeting_id, series_id, observation_date);

CREATE VIEW v_meeting_information_set AS
SELECT
    m.meeting_id,
    m.meeting_start_date,
    m.information_cutoff_date_et,
    s.series_id,
    s.title,
    s.frequency,
    s.units,
    snap.observation_date,
    o.value_num,
    o.value_raw,
    o.realtime_start AS visible_version_date,
    o.realtime_end AS version_realtime_end,
    o.is_initial_release,
    snap.selection_policy
FROM meeting_snapshot_value AS snap
JOIN fomc_meeting AS m
  ON m.meeting_id = snap.meeting_id
JOIN observation_vintage AS o
  ON o.series_id = snap.series_id
 AND o.observation_date = snap.observation_date
 AND o.realtime_start = snap.realtime_start
JOIN economic_series AS s
  ON s.series_id = snap.series_id;
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)


def ensure_schema_migrations(connection: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(economic_series)")
    }
    if "vintage_mode" not in columns:
        connection.execute(
            """
            ALTER TABLE economic_series
            ADD COLUMN vintage_mode TEXT NOT NULL DEFAULT 'ALFRED'
                CHECK (vintage_mode IN ('ALFRED', 'FRED_ONLY_OBSERVATION_DATE'))
            """
        )


def insert_release(connection: sqlite3.Connection, release: dict[str, Any]) -> int:
    release_id = int(release["id"])
    connection.execute(
        """
        INSERT INTO economic_release (release_id, release_name, source_url)
        VALUES (?, ?, ?)
        ON CONFLICT(release_id) DO UPDATE SET
            release_name = excluded.release_name,
            source_url = excluded.source_url
        """,
        (release_id, release["name"], release.get("link")),
    )
    return release_id


def insert_release_events(
    connection: sqlite3.Connection,
    release_id: int,
    release_dates: Iterable[dict[str, Any]],
    fetched_at_utc: str | None = None,
) -> None:
    fetched_at = fetched_at_utc or utc_now()
    connection.executemany(
        """
        INSERT INTO release_event (
            release_id, release_date, scheduled_at_utc,
            time_precision, fetched_at_utc
        ) VALUES (?, ?, NULL, 'date', ?)
        ON CONFLICT(release_id, release_date) DO UPDATE SET
            fetched_at_utc = excluded.fetched_at_utc
        """,
        [(release_id, item["date"], fetched_at) for item in release_dates],
    )


def insert_series(
    connection: sqlite3.Connection,
    metadata: dict[str, Any],
    release_id: int | None,
    vintage_mode: str = "ALFRED",
) -> None:
    connection.execute(
        """
        INSERT INTO economic_series (
            series_id, title, release_id, frequency, frequency_short,
            units, units_short, seasonal_adjustment,
            seasonal_adjustment_short, observation_start, observation_end,
            upstream_last_updated, notes, vintage_mode
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(series_id) DO UPDATE SET
            title = excluded.title,
            release_id = excluded.release_id,
            frequency = excluded.frequency,
            frequency_short = excluded.frequency_short,
            units = excluded.units,
            units_short = excluded.units_short,
            seasonal_adjustment = excluded.seasonal_adjustment,
            seasonal_adjustment_short = excluded.seasonal_adjustment_short,
            observation_start = excluded.observation_start,
            observation_end = excluded.observation_end,
            upstream_last_updated = excluded.upstream_last_updated,
            notes = excluded.notes,
            vintage_mode = excluded.vintage_mode
        """,
        (
            metadata["id"],
            metadata["title"],
            release_id,
            metadata["frequency"],
            metadata.get("frequency_short"),
            metadata["units"],
            metadata.get("units_short"),
            metadata.get("seasonal_adjustment"),
            metadata.get("seasonal_adjustment_short"),
            metadata.get("observation_start"),
            metadata.get("observation_end"),
            metadata.get("last_updated"),
            metadata.get("notes"),
            vintage_mode,
        ),
    )


def _source_hash(series_id: str, observation: dict[str, Any]) -> str:
    payload = "|".join(
        [
            series_id,
            observation["date"],
            observation["realtime_start"],
            observation["realtime_end"],
            observation["value"],
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def insert_observations(
    connection: sqlite3.Connection,
    series_id: str,
    observations: Iterable[dict[str, Any]],
    fetched_at_utc: str | None = None,
) -> int:
    fetched_at = fetched_at_utc or utc_now()
    rows = list(observations)
    first_visible: dict[str, str] = {}
    for item in rows:
        if item["value"] == ".":
            continue
        observation_date = item["date"]
        realtime_start = item["realtime_start"]
        current = first_visible.get(observation_date)
        if current is None or realtime_start < current:
            first_visible[observation_date] = realtime_start

    payload = []
    for item in rows:
        value_raw = item["value"]
        value_num = None if value_raw == "." else float(value_raw)
        payload.append(
            (
                series_id,
                item["date"],
                item["realtime_start"],
                item["realtime_end"],
                value_raw,
                value_num,
                int(first_visible.get(item["date"]) == item["realtime_start"]),
                fetched_at,
                _source_hash(series_id, item),
            )
        )

    connection.executemany(
        """
        INSERT INTO observation_vintage (
            series_id, observation_date, realtime_start, realtime_end,
            value_raw, value_num, is_initial_release, fetched_at_utc,
            source_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(series_id, observation_date, realtime_start) DO UPDATE SET
            realtime_end = excluded.realtime_end,
            value_raw = excluded.value_raw,
            value_num = excluded.value_num,
            is_initial_release = excluded.is_initial_release,
            fetched_at_utc = excluded.fetched_at_utc,
            source_hash = excluded.source_hash
        """,
        payload,
    )
    return len(payload)


def finalize_series_observations(
    connection: sqlite3.Connection,
    series_id: str,
) -> None:
    connection.execute(
        """
        UPDATE observation_vintage AS observation
        SET is_initial_release = CASE
            WHEN observation.value_num IS NOT NULL
             AND observation.realtime_start = (
                SELECT MIN(candidate.realtime_start)
                FROM observation_vintage AS candidate
                WHERE candidate.series_id = observation.series_id
                  AND candidate.observation_date = observation.observation_date
                  AND candidate.value_num IS NOT NULL
             )
            THEN 1
            ELSE 0
        END
        WHERE observation.series_id = ?
        """,
        (series_id,),
    )
    connection.execute(
        """
        UPDATE observation_vintage
        SET release_event_id = (
            SELECT event.release_event_id
            FROM economic_series AS series
            JOIN release_event AS event
              ON event.release_id = series.release_id
             AND event.release_date = observation_vintage.realtime_start
            WHERE series.series_id = observation_vintage.series_id
        )
        WHERE series_id = ?
        """,
        (series_id,),
    )


def insert_meetings(
    connection: sqlite3.Connection,
    meetings: Iterable[dict[str, str]],
) -> None:
    connection.executemany(
        """
        INSERT INTO fomc_meeting (
            meeting_id, meeting_start_date, meeting_end_date,
            information_cutoff_date_et, cutoff_policy, calendar_source_url
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(meeting_id) DO UPDATE SET
            meeting_start_date = excluded.meeting_start_date,
            meeting_end_date = excluded.meeting_end_date,
            information_cutoff_date_et = excluded.information_cutoff_date_et,
            cutoff_policy = excluded.cutoff_policy,
            calendar_source_url = excluded.calendar_source_url
        """,
        [
            (
                item["meeting_id"],
                item["meeting_start_date"],
                item["meeting_end_date"],
                item["information_cutoff_date_et"],
                item.get("cutoff_policy", "previous_calendar_day"),
                item.get(
                    "calendar_source_url",
                    "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                ),
            )
            for item in meetings
        ],
    )


def materialize_meeting_snapshots(
    connection: sqlite3.Connection,
    progress: Callable[[str], None] | None = None,
) -> int:
    created_at = utc_now()
    connection.execute("DELETE FROM meeting_snapshot_value")
    meetings = connection.execute(
        """
        SELECT meeting_id, information_cutoff_date_et
        FROM fomc_meeting
        ORDER BY meeting_start_date
        """
    ).fetchall()
    series = connection.execute(
        "SELECT series_id, frequency_short FROM economic_series ORDER BY series_id"
    ).fetchall()
    for meeting_number, (meeting_id, cutoff_date) in enumerate(meetings, start=1):
        for series_id, frequency_short in series:
            limit = SNAPSHOT_WINDOW_LIMITS.get(
                frequency_short,
                SNAPSHOT_WINDOW_LIMITS["M"],
            )
            connection.execute(
                """
                WITH recent_dates AS (
                    SELECT DISTINCT observation_date
                    FROM observation_vintage
                    WHERE series_id = ?
                      AND observation_date <= ?
                      AND realtime_start <= ?
                      AND value_num IS NOT NULL
                    ORDER BY observation_date DESC
                    LIMIT ?
                ),
                visible_versions AS (
                    SELECT
                        recent_dates.observation_date,
                        (
                            SELECT MAX(observation.realtime_start)
                            FROM observation_vintage AS observation
                            WHERE observation.series_id = ?
                              AND observation.observation_date = recent_dates.observation_date
                              AND observation.realtime_start <= ?
                              AND observation.value_num IS NOT NULL
                        ) AS realtime_start
                    FROM recent_dates
                )
                INSERT INTO meeting_snapshot_value (
                    meeting_id, series_id, observation_date, realtime_start,
                    selection_policy, created_at_utc
                )
                SELECT ?, ?, observation_date, realtime_start, ?, ?
                FROM visible_versions
                """,
                (
                    series_id,
                    cutoff_date,
                    cutoff_date,
                    limit,
                    series_id,
                    cutoff_date,
                    meeting_id,
                    series_id,
                    SNAPSHOT_SELECTION_POLICY,
                    created_at,
                ),
            )
        if progress and (meeting_number == 1 or meeting_number % 10 == 0):
            progress(f"snapshots={meeting_number}/{len(meetings)} meetings")
    return int(
        connection.execute("SELECT COUNT(*) FROM meeting_snapshot_value").fetchone()[0]
    )


class FredClient:
    def __init__(
        self,
        api_key: str,
        fetch_json: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("FRED_API_KEY(User) is missing or blank")
        self._api_key = api_key
        self._fetch_json = fetch_json or self._request_json
        self._release_date_cache: dict[tuple[int, str], list[dict[str, Any]]] = {}

    def _request_json(self, endpoint: str, parameters: dict[str, Any]) -> dict[str, Any]:
        query = dict(parameters)
        query["api_key"] = self._api_key
        query["file_type"] = "json"
        url = f"{FRED_API_BASE}{endpoint}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, headers={"User-Agent": "fred-fomc-vintage-db/1.0"})
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                last_error = error
                if 400 <= error.code < 500 and error.code != 429:
                    try:
                        error_body = json.loads(error.read().decode("utf-8"))
                        api_message = str(error_body.get("error_message", error.reason))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        api_message = str(error.reason)
                    raise FredApiError(endpoint, error.code, api_message) from error
                if attempt < 2:
                    time.sleep(attempt + 1)
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(attempt + 1)
        raise RuntimeError(f"FRED API request failed for {endpoint}") from last_error

    def series_metadata(self, series_id: str) -> dict[str, Any]:
        result = self._fetch_json("/fred/series", {"series_id": series_id})
        series = result.get("seriess", [])
        if len(series) != 1:
            raise RuntimeError(f"Expected one metadata row for {series_id}")
        return series[0]

    def series_release(self, series_id: str) -> dict[str, Any]:
        result = self._fetch_json("/fred/series/release", {"series_id": series_id})
        releases = result.get("releases", [])
        if len(releases) != 1:
            raise RuntimeError(f"Expected one release row for {series_id}")
        return releases[0]

    def release_dates(self, release_id: int, start_date: str) -> list[dict[str, Any]]:
        cache_key = (release_id, start_date)
        if cache_key in self._release_date_cache:
            return self._release_date_cache[cache_key]

        release_dates: list[dict[str, Any]] = []
        offset = 0
        limit = 10000
        while True:
            result = self._fetch_json(
                "/fred/release/dates",
                {
                    "release_id": release_id,
                    "limit": limit,
                    "offset": offset,
                    "sort_order": "asc",
                },
            )
            page = result.get("release_dates", [])
            release_dates.extend(page)
            total = int(result.get("count", len(release_dates)))
            offset += len(page)
            if not page or offset >= total:
                break

        filtered = [item for item in release_dates if item["date"] >= start_date]
        self._release_date_cache[cache_key] = filtered
        return filtered

    def _observations_for_realtime_period(
        self,
        series_id: str,
        observation_start: str,
        realtime_start: str,
        realtime_end: str,
    ) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        offset = 0
        limit = 100000
        while True:
            result = self._fetch_json(
                "/fred/series/observations",
                {
                    "series_id": series_id,
                    "observation_start": observation_start,
                    "realtime_start": realtime_start,
                    "realtime_end": realtime_end,
                    "output_type": 1,
                    "limit": limit,
                    "offset": offset,
                    "sort_order": "asc",
                },
            )
            page = result.get("observations", [])
            observations.extend(page)
            total = int(result.get("count", len(observations)))
            offset += len(page)
            if not page or offset >= total:
                break
        return observations

    def _current_observations(
        self,
        series_id: str,
        observation_start: str,
    ) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        offset = 0
        limit = 100000
        while True:
            result = self._fetch_json(
                "/fred/series/observations",
                {
                    "series_id": series_id,
                    "observation_start": observation_start,
                    "limit": limit,
                    "offset": offset,
                    "sort_order": "asc",
                },
            )
            page = result.get("observations", [])
            observations.extend(page)
            total = int(result.get("count", len(observations)))
            offset += len(page)
            if not page or offset >= total:
                break
        return observations

    def observations(self, series_id: str, observation_start: str) -> list[dict[str, Any]]:
        try:
            return self._observations_for_realtime_period(
                series_id,
                observation_start,
                "1776-07-04",
                "9999-12-31",
            )
        except FredApiError as error:
            if "does not exist in ALFRED" in error.api_message:
                raise
            if error.status_code != 400:
                raise
        except RuntimeError as error:
            cause = error.__cause__
            if not isinstance(cause, urllib.error.HTTPError) or cause.code != 400:
                raise

        observations_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        chunk_start = date.fromisoformat(observation_start)
        final_date = date.today()
        while chunk_start <= final_date:
            chunk_end = min(chunk_start + timedelta(days=1499), final_date)
            chunk = self._observations_for_realtime_period(
                series_id,
                observation_start,
                chunk_start.isoformat(),
                chunk_end.isoformat(),
            )
            for item in chunk:
                observations_by_key[(item["date"], item["realtime_start"])] = item
            chunk_start = chunk_end + timedelta(days=1)

        return sorted(
            observations_by_key.values(),
            key=lambda item: (item["date"], item["realtime_start"]),
        )

    def observations_with_mode(
        self,
        series_id: str,
        observation_start: str,
        allow_fred_only: bool,
    ) -> tuple[list[dict[str, Any]], str]:
        try:
            return self.observations(series_id, observation_start), "ALFRED"
        except FredApiError as error:
            if (
                not allow_fred_only
                or error.status_code != 400
                or "does not exist in ALFRED" not in error.api_message
            ):
                raise

        current = self._current_observations(series_id, observation_start)
        normalized = [
            {
                "date": item["date"],
                "value": item["value"],
                "realtime_start": item["date"],
                "realtime_end": "9999-12-31",
            }
            for item in current
        ]
        return normalized, "FRED_ONLY_OBSERVATION_DATE"

    def _paged_observation_batches(
        self,
        parameters: dict[str, Any],
        first_result: dict[str, Any] | None = None,
    ) -> Iterable[list[dict[str, Any]]]:
        limit = 10000
        offset = 0
        total: int | None = None
        if first_result is not None:
            first_page = first_result.get("observations", [])
            total = int(first_result.get("count", len(first_page)))
            if first_page:
                yield first_page
                offset = len(first_page)

        while total is None or offset < total:
            page_parameters = dict(parameters)
            page_parameters["limit"] = limit
            page_parameters["offset"] = offset
            result = self._fetch_json("/fred/series/observations", page_parameters)
            page = result.get("observations", [])
            total = int(result.get("count", offset + len(page)))
            if not page:
                break
            yield page
            offset += len(page)

    def _chunked_realtime_batches(
        self,
        series_id: str,
        observation_start: str,
    ) -> Iterable[list[dict[str, Any]]]:
        chunk_start = date.fromisoformat(observation_start)
        final_date = date.today()
        while chunk_start <= final_date:
            chunk_end = min(chunk_start + timedelta(days=1499), final_date)
            parameters = {
                "series_id": series_id,
                "observation_start": observation_start,
                "realtime_start": chunk_start.isoformat(),
                "realtime_end": chunk_end.isoformat(),
                "output_type": 1,
                "sort_order": "asc",
            }
            yield from self._paged_observation_batches(parameters)
            chunk_start = chunk_end + timedelta(days=1)

    def _fred_only_current_batches(
        self,
        series_id: str,
        observation_start: str,
    ) -> Iterable[list[dict[str, Any]]]:
        parameters = {
            "series_id": series_id,
            "observation_start": observation_start,
            "sort_order": "asc",
        }
        for page in self._paged_observation_batches(parameters):
            yield [
                {
                    "date": item["date"],
                    "value": item["value"],
                    "realtime_start": item["date"],
                    "realtime_end": "9999-12-31",
                }
                for item in page
            ]

    def observation_batches_with_mode(
        self,
        series_id: str,
        observation_start: str,
        allow_fred_only: bool,
        force_fred_only: bool = False,
    ) -> tuple[Iterable[list[dict[str, Any]]], str]:
        if force_fred_only:
            if not allow_fred_only:
                raise ValueError("force_fred_only requires allow_fred_only")
            return (
                self._fred_only_current_batches(series_id, observation_start),
                "FRED_ONLY_OBSERVATION_DATE",
            )

        full_period_parameters = {
            "series_id": series_id,
            "observation_start": observation_start,
            "realtime_start": "1776-07-04",
            "realtime_end": "9999-12-31",
            "output_type": 1,
            "sort_order": "asc",
        }
        probe_parameters = dict(full_period_parameters)
        probe_parameters["limit"] = 1
        probe_parameters["offset"] = 0
        try:
            first_result = self._fetch_json(
                "/fred/series/observations",
                probe_parameters,
            )
        except FredApiError as error:
            if "does not exist in ALFRED" in error.api_message:
                if not allow_fred_only:
                    raise
                return (
                    self._fred_only_current_batches(series_id, observation_start),
                    "FRED_ONLY_OBSERVATION_DATE",
                )
            if error.status_code != 400:
                raise
            return self._chunked_realtime_batches(series_id, observation_start), "ALFRED"
        except RuntimeError as error:
            cause = error.__cause__
            if not isinstance(cause, urllib.error.HTTPError) or cause.code != 400:
                raise
            return self._chunked_realtime_batches(series_id, observation_start), "ALFRED"

        return (
            self._paged_observation_batches(full_period_parameters, first_result),
            "ALFRED",
        )


def _ingest_series(
    connection: sqlite3.Connection,
    client: FredClient,
    series_id: str,
    observation_start: str,
    fetched_at_utc: str,
    force_fred_only: bool = False,
) -> int:
    metadata = client.series_metadata(series_id)
    release = client.series_release(series_id)
    observation_batches, vintage_mode = client.observation_batches_with_mode(
        series_id,
        observation_start,
        allow_fred_only=(
            metadata.get("frequency_short") == "D"
            or metadata.get("frequency") == "Daily"
        ),
        force_fred_only=(force_fred_only or series_id in FRED_ONLY_SERIES_IDS),
    )
    release_id = insert_release(connection, release)
    insert_release_events(
        connection,
        release_id,
        client.release_dates(release_id, observation_start),
        fetched_at_utc=fetched_at_utc,
    )
    insert_series(connection, metadata, release_id, vintage_mode=vintage_mode)
    for batch in observation_batches:
        insert_observations(
            connection,
            series_id,
            batch,
            fetched_at_utc=fetched_at_utc,
        )
    finalize_series_observations(connection, series_id)
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM observation_vintage WHERE series_id = ?",
            (series_id,),
        ).fetchone()[0]
    )


def build_database(
    output_path: Path,
    client: FredClient,
    series_ids: Iterable[str],
    meetings: Iterable[dict[str, str]],
    observation_start: str,
) -> dict[str, int]:
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing database: {output_path}")

    connection = sqlite3.connect(output_path)
    connection.execute("PRAGMA foreign_keys = ON")
    counts: dict[str, int] = {}
    try:
        create_schema(connection)
        built_at = utc_now()
        connection.executemany(
            "INSERT INTO database_metadata (key, value) VALUES (?, ?)",
            [
                ("dataset_status", "REAL_FRED_ALFRED"),
                ("api_base", FRED_API_BASE),
                ("observation_start", observation_start),
                ("built_at_utc", built_at),
                ("cutoff_policy", "previous_calendar_day"),
                ("snapshot_window_policy", SNAPSHOT_SELECTION_POLICY),
            ],
        )

        for series_id in series_ids:
            counts[series_id] = _ingest_series(
                connection,
                client,
                series_id,
                observation_start,
                built_at,
            )

        insert_meetings(connection, meetings)
        counts["meeting_snapshot_value"] = materialize_meeting_snapshots(connection)
        connection.commit()

        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(f"SQLite foreign_key_check failed: {foreign_key_errors}")
        return counts
    except Exception:
        connection.rollback()
        connection.close()
        output_path.unlink(missing_ok=True)
        raise
    finally:
        try:
            connection.close()
        except sqlite3.ProgrammingError:
            pass


def update_database(
    database_path: Path,
    client: FredClient,
    series_ids: Iterable[str],
    meetings: Iterable[dict[str, str]],
    observation_start: str,
    progress: Callable[[str], None] | None = None,
    strict_point_in_time: bool = False,
    calendar_through_date: str | None = None,
) -> dict[str, int]:
    if not database_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {database_path}")

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    counts: dict[str, int] = {}
    try:
        ensure_schema_migrations(connection)
        status_row = connection.execute(
            "SELECT value FROM database_metadata WHERE key = 'dataset_status'"
        ).fetchone()
        if status_row != ("REAL_FRED_ALFRED",):
            raise RuntimeError("Refusing to update a database without REAL_FRED_ALFRED status")

        existing_series = dict(
            connection.execute(
                "SELECT series_id, vintage_mode FROM economic_series"
            ).fetchall()
        )
        updated_at = utc_now()
        for series_id in series_ids:
            if series_id in existing_series and not strict_point_in_time:
                counts[series_id] = 0
                if progress:
                    progress(f"skip series={series_id} already_present=true")
                continue
            if progress:
                action = "refresh" if series_id in existing_series else "start"
                progress(f"{action} series={series_id}")
            counts[series_id] = _ingest_series(
                connection,
                client,
                series_id,
                observation_start,
                updated_at,
                force_fred_only=(
                    existing_series.get(series_id)
                    == "FRED_ONLY_OBSERVATION_DATE"
                ),
            )
            if progress:
                progress(f"done series={series_id} vintage_rows={counts[series_id]}")

        insert_meetings(connection, meetings)
        counts["meeting_snapshot_value"] = materialize_meeting_snapshots(
            connection,
            progress=progress,
        )
        metadata_updates = [
            ("last_updated_at_utc", updated_at),
            ("snapshot_window_policy", SNAPSHOT_SELECTION_POLICY),
        ]
        if calendar_through_date is not None:
            metadata_updates.append(
                ("fomc_calendar_through_date", calendar_through_date)
            )
        if strict_point_in_time:
            existing_start_row = connection.execute(
                "SELECT value FROM database_metadata WHERE key = 'observation_start'"
            ).fetchone()
            effective_start = min(
                observation_start,
                existing_start_row[0] if existing_start_row else observation_start,
            )
            metadata_updates.extend(
                [
                    ("observation_start", effective_start),
                    ("point_in_time_mode", "STRICT_AS_OF"),
                    ("missing_vintage_policy", "PRESERVE_MISSING"),
                    (
                        "fred_only_policy",
                        "OBSERVATION_DATE_PROXY_WITH_EXPLICIT_SERIES_MARKER",
                    ),
                ]
            )
        connection.executemany(
            """
            INSERT INTO database_metadata (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            metadata_updates,
        )
        connection.commit()

        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(f"SQLite foreign_key_check failed: {foreign_key_errors}")
        return counts
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
