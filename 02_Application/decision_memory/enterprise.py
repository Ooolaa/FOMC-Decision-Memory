from __future__ import annotations

import sqlite3
from datetime import date

from decision_memory.app_db import seed_enterprise_demo


def seed_enterprise_fixture_from_source(
    source_connection: sqlite3.Connection,
    app_connection: sqlite3.Connection,
    decision_date: str,
    threshold_value: float,
) -> dict[str, str]:
    date.fromisoformat(decision_date)
    if threshold_value <= 0:
        raise ValueError("threshold_value must be positive")
    vintage_mode_row = source_connection.execute(
        """
        SELECT vintage_mode
        FROM economic_series
        WHERE series_id = 'BAA10Y'
        """
    ).fetchone()
    if vintage_mode_row != ("FRED_ONLY_OBSERVATION_DATE",):
        raise ValueError(
            "BAA10Y must use FRED_ONLY_OBSERVATION_DATE for the demo monitor"
        )

    contradiction = source_connection.execute(
        """
        SELECT observation_date, value_num, source_hash
        FROM observation_vintage
        WHERE series_id = 'BAA10Y'
          AND observation_date > ?
          AND realtime_start <= observation_date
          AND value_num > ?
        ORDER BY observation_date, realtime_start
        LIMIT 1
        """,
        (decision_date, threshold_value),
    ).fetchone()
    if contradiction is None:
        raise ValueError(
            "No BAA10Y contradiction exists after the decision date at this threshold"
        )

    contradiction_date, contradiction_value, source_hash = contradiction
    ids = seed_enterprise_demo(
        app_connection,
        contradiction_at=f"{contradiction_date}T00:00:00Z",
        decision_at=f"{decision_date}T00:00:00Z",
        threshold_value=float(threshold_value),
        contradiction_value=float(contradiction_value),
        contradiction_source_hash=str(source_hash),
    )
    return {
        **ids,
        "decision_date": decision_date,
        "contradiction_date": str(contradiction_date),
    }
