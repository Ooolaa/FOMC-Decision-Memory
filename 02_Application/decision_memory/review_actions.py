from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decision_memory.app_db import (
    record_assumption_event,
    workflow_recognition_lag_seconds,
)


def _now_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _connect_existing(database_path: Path, *, read_only: bool) -> sqlite3.Connection:
    resolved = database_path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"App database does not exist: {resolved}")
    mode = "ro" if read_only else "rw"
    connection = sqlite3.connect(
        f"file:{resolved.as_posix()}?mode={mode}",
        uri=True,
    )
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _status_from_connection(
    connection: sqlite3.Connection,
    assumption_id: str,
) -> dict[str, Any]:
    assumption = connection.execute(
        """
        SELECT assumption.assumption_id, assumption.claim,
               assumption.monitor_series_id, assumption.monitor_operator,
               assumption.threshold_value, decision.decision_id,
               decision.domain, decision.title, decision.synthetic,
               decision.composite
        FROM decision_assumption AS assumption
        JOIN decision_case AS decision USING (decision_id)
        WHERE assumption.assumption_id = ?
        """,
        (assumption_id,),
    ).fetchone()
    if assumption is None:
        raise ValueError(f"Unknown assumption_id: {assumption_id}")
    event_rows = connection.execute(
        """
        SELECT event_id, event_type, occurred_at, actor, payload_json
        FROM assumption_event
        WHERE assumption_id = ?
        ORDER BY occurred_at, event_id
        """,
        (assumption_id,),
    ).fetchall()
    event_types = {row[1] for row in event_rows}
    state = "REVIEWED" if "REVIEWED" in event_types else (
        "REVIEW_REQUESTED" if "REVIEW_REQUESTED" in event_types else (
            "CONTRADICTED" if "CONTRADICTION" in event_types else "MONITORED"
        )
    )
    return {
        "assumption_id": assumption[0],
        "claim": assumption[1],
        "monitor_series_id": assumption[2],
        "monitor_operator": assumption[3],
        "threshold_value": assumption[4],
        "decision_id": assumption[5],
        "domain": assumption[6],
        "title": assumption[7],
        "synthetic": bool(assumption[8]),
        "composite": bool(assumption[9]),
        "state": state,
        "event_count": len(event_rows),
        "workflow_recognition_lag_seconds": workflow_recognition_lag_seconds(
            connection,
            assumption_id,
        ),
        "events": [
            {
                "event_id": row[0],
                "event_type": row[1],
                "occurred_at": row[2],
                "actor": row[3],
                "payload": json.loads(row[4]),
            }
            for row in event_rows
        ],
    }


def get_assumption_status(
    database_path: Path,
    assumption_id: str,
) -> dict[str, Any]:
    connection = _connect_existing(database_path, read_only=True)
    try:
        return _status_from_connection(connection, assumption_id)
    finally:
        connection.close()


def _record_review_action(
    database_path: Path,
    assumption_id: str,
    event_type: str,
    *,
    actor: str,
    occurred_at: str | None,
) -> dict[str, Any]:
    connection = _connect_existing(database_path, read_only=False)
    try:
        record_assumption_event(
            connection,
            assumption_id,
            event_type,
            occurred_at or _now_utc(),
            actor=actor,
            payload={"action_source": "user"},
        )
        connection.commit()
        return _status_from_connection(connection, assumption_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def request_review(
    database_path: Path,
    assumption_id: str,
    *,
    actor: str,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    return _record_review_action(
        database_path,
        assumption_id,
        "REVIEW_REQUESTED",
        actor=actor,
        occurred_at=occurred_at,
    )


def complete_review(
    database_path: Path,
    assumption_id: str,
    *,
    actor: str,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    return _record_review_action(
        database_path,
        assumption_id,
        "REVIEWED",
        actor=actor,
        occurred_at=occurred_at,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read or update append-only assumption review events."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("fomc_simulation.sqlite"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "request", "complete"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("assumption_id")
        if command != "status":
            subparser.add_argument("--actor", required=True)
            subparser.add_argument("--occurred-at")
    args = parser.parse_args()

    if args.command == "status":
        report = get_assumption_status(args.database, args.assumption_id)
    elif args.command == "request":
        report = request_review(
            args.database,
            args.assumption_id,
            actor=args.actor,
            occurred_at=args.occurred_at,
        )
    else:
        report = complete_review(
            args.database,
            args.assumption_id,
            actor=args.actor,
            occurred_at=args.occurred_at,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
