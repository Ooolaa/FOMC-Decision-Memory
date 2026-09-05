from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from typing import Any


POLICY_RATE_CONTEXT_VERSION = "policy_rate_context_v1"
TARGET_RANGE_START = date(2008, 12, 16)
MAX_CHANGE_EVENTS = 8


def _visible_values(
    connection: sqlite3.Connection,
    series_id: str,
    cutoff_date: str,
) -> list[tuple[str, float]]:
    rows = connection.execute(
        """
        WITH ranked AS (
            SELECT
                observation_date,
                value_num,
                ROW_NUMBER() OVER (
                    PARTITION BY observation_date
                    ORDER BY realtime_start DESC
                ) AS version_rank
            FROM observation_vintage
            WHERE series_id = ?
              AND observation_date <= ?
              AND realtime_start <= ?
              AND value_num IS NOT NULL
        )
        SELECT observation_date, value_num
        FROM ranked
        WHERE version_rank = 1
        ORDER BY observation_date
        """,
        (series_id, cutoff_date, cutoff_date),
    ).fetchall()
    return [(row[0], float(row[1])) for row in rows]


def _single_target_states(
    connection: sqlite3.Connection,
    cutoff_date: str,
) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    previous: float | None = None
    for observation_date, target in _visible_values(
        connection,
        "DFEDTAR",
        cutoff_date,
    ):
        if previous is None or target != previous:
            states.append(
                {
                    "effective_date": observation_date,
                    "target_rate": target,
                    "lower_rate": None,
                    "upper_rate": None,
                    "source_series_ids": ["DFEDTAR"],
                }
            )
            previous = target
    return states


def _target_range_states(
    connection: sqlite3.Connection,
    cutoff_date: str,
) -> list[dict[str, Any]]:
    lower_by_date = dict(_visible_values(connection, "DFEDTARL", cutoff_date))
    upper_by_date = dict(_visible_values(connection, "DFEDTARU", cutoff_date))
    dates = sorted(set(lower_by_date) | set(upper_by_date))
    states: list[dict[str, Any]] = []
    lower: float | None = None
    upper: float | None = None
    previous: tuple[float, float] | None = None
    for observation_date in dates:
        if observation_date in lower_by_date:
            lower = lower_by_date[observation_date]
        if observation_date in upper_by_date:
            upper = upper_by_date[observation_date]
        if lower is None or upper is None:
            continue
        current = (lower, upper)
        if current != previous:
            states.append(
                {
                    "effective_date": observation_date,
                    "target_rate": None,
                    "lower_rate": lower,
                    "upper_rate": upper,
                    "source_series_ids": ["DFEDTARL", "DFEDTARU"],
                }
            )
            previous = current
    return states


def _midpoint(state: dict[str, Any]) -> float:
    target = state["target_rate"]
    if target is not None:
        return float(target)
    return (float(state["lower_rate"]) + float(state["upper_rate"])) / 2


def _directions(states: list[dict[str, Any]]) -> list[str]:
    directions = ["INITIAL"]
    for previous, current in zip(states, states[1:]):
        delta = _midpoint(current) - _midpoint(previous)
        directions.append("UP" if delta > 0 else "DOWN" if delta < 0 else "UNCHANGED")
    return directions


def _is_lower_bound(state: dict[str, Any]) -> bool:
    return (
        state["target_rate"] is None
        and float(state["lower_rate"]) <= 0.0
        and float(state["upper_rate"]) <= 0.25
    )


def _regime_start_index(
    states: list[dict[str, Any]],
    directions: list[str],
) -> int:
    current_index = len(states) - 1
    if _is_lower_bound(states[current_index]):
        start = current_index
        while start > 0 and _is_lower_bound(states[start - 1]):
            start -= 1
        return start

    current_direction = directions[current_index]
    if current_direction in {"UP", "DOWN"}:
        start = current_index
        while start > 0 and directions[start - 1] == current_direction:
            start -= 1
        return start
    return current_index


def _with_hash(record: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {**record, "source_hash": hashlib.sha256(encoded).hexdigest()}


def build_policy_rate_context(
    connection: sqlite3.Connection,
    meeting_id: str,
) -> list[dict[str, Any]]:
    meeting = connection.execute(
        """
        SELECT information_cutoff_date_et
        FROM fomc_meeting
        WHERE meeting_id = ?
        """,
        (meeting_id,),
    ).fetchone()
    if meeting is None:
        raise ValueError(f"Unknown meeting_id: {meeting_id}")
    cutoff_date = str(meeting[0])
    cutoff = date.fromisoformat(cutoff_date)

    if cutoff < TARGET_RANGE_START:
        states = _single_target_states(connection, cutoff_date)
        base_regime = "SINGLE_TARGET"
    else:
        states = _target_range_states(connection, cutoff_date)
        base_regime = "TARGET_RANGE"
    if not states:
        raise ValueError(f"No policy-rate input is visible for {meeting_id}")

    directions = _directions(states)
    current_index = len(states) - 1
    regime_start_index = _regime_start_index(states, directions)
    current_state = states[current_index]
    regime = "LOWER_BOUND" if _is_lower_bound(current_state) else base_regime
    regime_started_at = states[regime_start_index]["effective_date"]

    current_record = _with_hash(
        {
            "meeting_id": meeting_id,
            "record_kind": "CURRENT",
            "cutoff_date": cutoff_date,
            "effective_date": current_state["effective_date"],
            "regime": regime,
            "direction": directions[current_index],
            "target_rate": current_state["target_rate"],
            "lower_rate": current_state["lower_rate"],
            "upper_rate": current_state["upper_rate"],
            "regime_started_at": regime_started_at,
            "regime_duration_days": (
                cutoff - date.fromisoformat(regime_started_at)
            ).days,
            "source_series_ids": current_state["source_series_ids"],
            "rule_version": POLICY_RATE_CONTEXT_VERSION,
        }
    )

    change_records = []
    first_change = max(0, len(states) - MAX_CHANGE_EVENTS)
    for index in range(len(states) - 1, first_change - 1, -1):
        state = states[index]
        change_records.append(
            _with_hash(
                {
                    "meeting_id": meeting_id,
                    "record_kind": "CHANGE",
                    "cutoff_date": cutoff_date,
                    "effective_date": state["effective_date"],
                    "regime": (
                        "LOWER_BOUND" if _is_lower_bound(state) else base_regime
                    ),
                    "direction": directions[index],
                    "target_rate": state["target_rate"],
                    "lower_rate": state["lower_rate"],
                    "upper_rate": state["upper_rate"],
                    "regime_started_at": None,
                    "regime_duration_days": None,
                    "source_series_ids": state["source_series_ids"],
                    "rule_version": POLICY_RATE_CONTEXT_VERSION,
                }
            )
        )
    return [current_record, *change_records]


def replace_policy_rate_context(
    connection: sqlite3.Connection,
    records: list[dict[str, Any]],
) -> int:
    if not records or len(records) > 9:
        raise ValueError("policy_rate_context must contain between 1 and 9 records")
    if records[0]["record_kind"] != "CURRENT":
        raise ValueError("The first policy-rate record must be CURRENT")
    meeting_id = records[0]["meeting_id"]
    if any(record["meeting_id"] != meeting_id for record in records):
        raise ValueError("All policy-rate records must belong to one meeting")
    if any(record["effective_date"] > record["cutoff_date"] for record in records):
        raise ValueError("Policy-rate context contains a post-cutoff record")

    connection.execute(
        "DELETE FROM policy_rate_context WHERE meeting_id = ?",
        (meeting_id,),
    )
    connection.executemany(
        """
        INSERT INTO policy_rate_context (
            meeting_id, ordinal, record_kind, cutoff_date, effective_date,
            regime, direction, target_rate, lower_rate, upper_rate,
            regime_started_at, regime_duration_days, source_series_ids_json,
            rule_version, source_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                meeting_id,
                ordinal,
                record["record_kind"],
                record["cutoff_date"],
                record["effective_date"],
                record["regime"],
                record["direction"],
                record["target_rate"],
                record["lower_rate"],
                record["upper_rate"],
                record["regime_started_at"],
                record["regime_duration_days"],
                json.dumps(record["source_series_ids"], sort_keys=True),
                record["rule_version"],
                record["source_hash"],
            )
            for ordinal, record in enumerate(records)
        ],
    )
    return len(records)
