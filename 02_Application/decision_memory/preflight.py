from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from build_real_fred_db import DEFAULT_SERIES
from decision_memory.lag_spec import (
    constraint_episode_counts,
    load_rate_only_spec,
)
from decision_memory.policy_rate import build_policy_rate_context


POLICY_SERIES = ("DFEDTAR", "DFEDTARU", "DFEDTARL")
EXPECTED_MEETINGS = 166
EXPECTED_PRE_RANGE_MEETINGS = 24
GENERAL_SNAPSHOT_LIMIT = 6816
SPEC_PATH = Path(__file__).resolve().parents[1] / "metric_spec" / "rate_only_response_v1.json"


def audit_source_connection(connection: sqlite3.Connection) -> dict[str, Any]:
    connection.execute("PRAGMA foreign_keys = ON")
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    metadata = dict(
        connection.execute(
            "SELECT key, value FROM database_metadata ORDER BY key"
        ).fetchall()
    )
    present_series = {
        row[0]
        for row in connection.execute(
            "SELECT series_id FROM economic_series"
        ).fetchall()
    }
    required_series = set(DEFAULT_SERIES)
    missing_series = sorted(required_series - present_series)
    meeting_count = int(
        connection.execute("SELECT COUNT(*) FROM fomc_meeting").fetchone()[0]
    )
    snapshot_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM meeting_snapshot_value"
        ).fetchone()[0]
    )
    cutoff_violations = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM meeting_snapshot_value AS snapshot
            JOIN fomc_meeting AS meeting USING (meeting_id)
            WHERE snapshot.observation_date > meeting.information_cutoff_date_et
               OR snapshot.realtime_start > meeting.information_cutoff_date_et
            """
        ).fetchone()[0]
    )
    general_snapshot_max = int(
        connection.execute(
            """
            SELECT COALESCE(MAX(row_count), 0)
            FROM (
                SELECT COUNT(*) AS row_count
                FROM meeting_snapshot_value
                WHERE series_id NOT IN (?, ?, ?)
                GROUP BY meeting_id
            )
            """,
            POLICY_SERIES,
        ).fetchone()[0]
    )
    pre_range_coverage = int(
        connection.execute(
            """
            SELECT COUNT(DISTINCT meeting.meeting_id)
            FROM fomc_meeting AS meeting
            JOIN meeting_snapshot_value AS snapshot
              ON snapshot.meeting_id = meeting.meeting_id
             AND snapshot.series_id = 'DFEDTAR'
            WHERE meeting.information_cutoff_date_et < '2008-12-16'
            """
        ).fetchone()[0]
    )
    range_coverage = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM fomc_meeting AS meeting
            WHERE meeting.information_cutoff_date_et >= '2008-12-16'
              AND EXISTS (
                SELECT 1 FROM meeting_snapshot_value AS lower_snapshot
                WHERE lower_snapshot.meeting_id = meeting.meeting_id
                  AND lower_snapshot.series_id = 'DFEDTARL'
              )
              AND EXISTS (
                SELECT 1 FROM meeting_snapshot_value AS upper_snapshot
                WHERE upper_snapshot.meeting_id = meeting.meeting_id
                  AND upper_snapshot.series_id = 'DFEDTARU'
              )
            """
        ).fetchone()[0]
    )
    policy_modes = dict(
        connection.execute(
            """
            SELECT series_id, vintage_mode
            FROM economic_series
            WHERE series_id IN (?, ?, ?)
            """,
            POLICY_SERIES,
        ).fetchall()
    )

    spec = load_rate_only_spec(SPEC_PATH)
    episode_counts = constraint_episode_counts(connection, spec)
    policy_context_max = 0
    policy_context_cutoff_violations = 0
    if not (set(POLICY_SERIES) - present_series):
        for (meeting_id,) in connection.execute(
            "SELECT meeting_id FROM fomc_meeting ORDER BY meeting_start_date"
        ):
            records = build_policy_rate_context(connection, meeting_id)
            policy_context_max = max(policy_context_max, len(records))
            policy_context_cutoff_violations += sum(
                record["effective_date"] > record["cutoff_date"]
                for record in records
            )

    return {
        "integrity": integrity,
        "foreign_key_errors": [list(row) for row in foreign_key_errors],
        "metadata": metadata,
        "series_count": len(present_series),
        "missing_series": missing_series,
        "meeting_count": meeting_count,
        "snapshot_count": snapshot_count,
        "cutoff_violations": cutoff_violations,
        "general_snapshot_max": general_snapshot_max,
        "pre_range_coverage": pre_range_coverage,
        "range_coverage": range_coverage,
        "policy_vintage_modes": policy_modes,
        "policy_context_max": policy_context_max,
        "policy_context_cutoff_violations": policy_context_cutoff_violations,
        "constraint_episode_counts": episode_counts,
    }


def assert_source_ready(report: dict[str, Any]) -> None:
    failures = []
    if report["integrity"] != "ok":
        failures.append("integrity")
    if report["foreign_key_errors"]:
        failures.append("foreign_key_errors")
    if report["missing_series"]:
        failures.append("missing_series")
    if report["metadata"].get("dataset_status") != "REAL_FRED_ALFRED":
        failures.append("dataset_status")
    if report["metadata"].get("point_in_time_mode") != "STRICT_AS_OF":
        failures.append("point_in_time_mode")
    if report["metadata"].get("missing_vintage_policy") != "PRESERVE_MISSING":
        failures.append("missing_vintage_policy")
    if report["meeting_count"] != EXPECTED_MEETINGS:
        failures.append("meeting_count")
    if report["snapshot_count"] <= 0:
        failures.append("snapshot_count")
    if report["cutoff_violations"] != 0:
        failures.append("cutoff_violations")
    if not 0 < report["general_snapshot_max"] <= GENERAL_SNAPSHOT_LIMIT:
        failures.append("general_snapshot_max")
    if report["pre_range_coverage"] != EXPECTED_PRE_RANGE_MEETINGS:
        failures.append("pre_range_coverage")
    if report["range_coverage"] != EXPECTED_MEETINGS - EXPECTED_PRE_RANGE_MEETINGS:
        failures.append("range_coverage")
    if any(
        report["policy_vintage_modes"].get(series_id)
        != "FRED_ONLY_OBSERVATION_DATE"
        for series_id in POLICY_SERIES
    ):
        failures.append("policy_vintage_modes")
    if not 0 < report["policy_context_max"] <= 9:
        failures.append("policy_context_max")
    if report["policy_context_cutoff_violations"] != 0:
        failures.append("policy_context_cutoff_violations")
    if report["constraint_episode_counts"] != [55, 15]:
        failures.append("constraint_episode_counts")
    if failures:
        raise RuntimeError("Source preflight failed: " + ", ".join(failures))


def audit_source_database(database_path: Path) -> dict[str, Any]:
    if not database_path.is_file():
        raise FileNotFoundError(f"Source database does not exist: {database_path}")
    connection = sqlite3.connect(
        f"file:{database_path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    try:
        report = audit_source_connection(connection)
    finally:
        connection.close()
    with database_path.open("rb") as source:
        report["database_sha256"] = hashlib.sha256(source.read()).hexdigest()
    report["database_path"] = str(database_path.resolve())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the R5 FOMC source database.")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("fred_fomc_real.sqlite"),
    )
    args = parser.parse_args()
    report = audit_source_database(args.database)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    assert_source_ready(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
