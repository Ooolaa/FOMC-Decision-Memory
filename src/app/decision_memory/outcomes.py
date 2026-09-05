from __future__ import annotations

import sqlite3
from typing import Any


RULE_VERSION = "rate_delta_v1"


def _latest_value(
    connection: sqlite3.Connection,
    series_id: str,
    on_or_before: str,
) -> tuple[str, float]:
    row = connection.execute(
        """
        SELECT observation_date, value_num
        FROM observation_vintage
        WHERE series_id = ?
          AND observation_date <= ?
          AND realtime_start <= ?
          AND value_num IS NOT NULL
        ORDER BY observation_date DESC, realtime_start DESC
        LIMIT 1
        """,
        (series_id, on_or_before, on_or_before),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"No {series_id} value visible on or before {on_or_before}"
        )
    return str(row[0]), float(row[1])


def _first_value_after(
    connection: sqlite3.Connection,
    series_id: str,
    after_date: str,
) -> tuple[str, float]:
    row = connection.execute(
        """
        SELECT observation_date, value_num
        FROM observation_vintage
        WHERE series_id = ?
          AND observation_date > ?
          AND value_num IS NOT NULL
        ORDER BY observation_date, realtime_start DESC
        LIMIT 1
        """,
        (series_id, after_date),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"No {series_id} value after {after_date}")
    return str(row[0]), float(row[1])


def _first_range_after(
    connection: sqlite3.Connection,
    after_date: str,
) -> tuple[str, float, float]:
    row = connection.execute(
        """
        SELECT lower.observation_date, lower.value_num, upper.value_num
        FROM observation_vintage AS lower
        JOIN observation_vintage AS upper
          ON upper.observation_date = lower.observation_date
         AND upper.series_id = 'DFEDTARU'
        WHERE lower.series_id = 'DFEDTARL'
          AND lower.observation_date > ?
          AND lower.value_num IS NOT NULL
          AND upper.value_num IS NOT NULL
        ORDER BY lower.observation_date,
                 lower.realtime_start DESC,
                 upper.realtime_start DESC
        LIMIT 1
        """,
        (after_date,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"No complete target range after {after_date}")
    return str(row[0]), float(row[1]), float(row[2])


def _first_range_on_or_after(
    connection: sqlite3.Connection,
    start_date: str,
) -> tuple[str, float, float]:
    row = connection.execute(
        """
        SELECT lower.observation_date, lower.value_num, upper.value_num
        FROM observation_vintage AS lower
        JOIN observation_vintage AS upper
          ON upper.observation_date = lower.observation_date
         AND upper.series_id = 'DFEDTARU'
        WHERE lower.series_id = 'DFEDTARL'
          AND lower.observation_date >= ?
          AND lower.value_num IS NOT NULL
          AND upper.value_num IS NOT NULL
        ORDER BY lower.observation_date,
                 lower.realtime_start DESC,
                 upper.realtime_start DESC
        LIMIT 1
        """,
        (start_date,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"No complete target range on or after {start_date}")
    return str(row[0]), float(row[1]), float(row[2])


def _action_class(before: float, after: float) -> str:
    difference = after - before
    if difference > 1e-9:
        return "HIKE"
    if difference < -1e-9:
        return "CUT"
    return "HOLD"


def derive_rate_outcome(
    connection: sqlite3.Connection,
    meeting_id: str,
) -> dict[str, Any]:
    meeting = connection.execute(
        """
        SELECT meeting_end_date, information_cutoff_date_et
        FROM fomc_meeting WHERE meeting_id = ?
        """,
        (meeting_id,),
    ).fetchone()
    if meeting is None:
        raise ValueError(f"Unknown meeting_id: {meeting_id}")
    meeting_end_date, cutoff_date = map(str, meeting)
    if cutoff_date < "2008-12-16":
        _, pre_target = _latest_value(connection, "DFEDTAR", cutoff_date)
        if meeting_end_date >= "2008-12-16":
            effective_date, target_lower, target_upper = _first_range_on_or_after(
                connection,
                meeting_end_date,
            )
            return {
                "meeting_id": meeting_id,
                "action_class": _action_class(
                    pre_target,
                    (target_lower + target_upper) / 2,
                ),
                "target_rate": None,
                "target_lower": target_lower,
                "target_upper": target_upper,
                "pre_target_rate": pre_target,
                "pre_target_lower": None,
                "pre_target_upper": None,
                "outcome_effective_date": effective_date,
                "rule_version": RULE_VERSION,
            }
        effective_date, target = _first_value_after(
            connection,
            "DFEDTAR",
            meeting_end_date,
        )
        return {
            "meeting_id": meeting_id,
            "action_class": _action_class(pre_target, target),
            "target_rate": target,
            "target_lower": None,
            "target_upper": None,
            "pre_target_rate": pre_target,
            "pre_target_lower": None,
            "pre_target_upper": None,
            "outcome_effective_date": effective_date,
            "rule_version": RULE_VERSION,
        }

    _, pre_lower = _latest_value(connection, "DFEDTARL", cutoff_date)
    _, pre_upper = _latest_value(connection, "DFEDTARU", cutoff_date)
    effective_date, target_lower, target_upper = _first_range_after(
        connection,
        meeting_end_date,
    )
    return {
        "meeting_id": meeting_id,
        "action_class": _action_class(
            (pre_lower + pre_upper) / 2,
            (target_lower + target_upper) / 2,
        ),
        "target_rate": None,
        "target_lower": target_lower,
        "target_upper": target_upper,
        "pre_target_rate": None,
        "pre_target_lower": pre_lower,
        "pre_target_upper": pre_upper,
        "outcome_effective_date": effective_date,
        "rule_version": RULE_VERSION,
    }
