from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path

from fomc_calendar import load_official_fomc_meetings
from fred_vintage_db import insert_meetings, materialize_meeting_snapshots, utc_now


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load official FOMC meetings and rebuild point-in-time snapshots."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("fred_fomc_real.sqlite"),
    )
    parser.add_argument("--start-year", type=int, default=2006)
    parser.add_argument("--through-date", type=date.fromisoformat, default=date.today())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    meetings = load_official_fomc_meetings(
        start_year=args.start_year,
        through_date=args.through_date,
    )
    if not meetings:
        raise RuntimeError("No official FOMC meetings were found")
    print(
        f"official_meetings={len(meetings)} "
        f"range={meetings[0]['meeting_start_date']}..{meetings[-1]['meeting_end_date']}",
        flush=True,
    )

    connection = sqlite3.connect(args.database, timeout=60)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            insert_meetings(connection, meetings)
            snapshot_count = materialize_meeting_snapshots(
                connection,
                progress=lambda message: print(message, flush=True),
            )
            metadata = {
                "fomc_calendar_start_year": str(args.start_year),
                "fomc_calendar_through_date": args.through_date.isoformat(),
                "fomc_calendar_source": "Federal Reserve official calendars",
                "fomc_calendar_synced_at_utc": utc_now(),
            }
            connection.executemany(
                """
                INSERT INTO database_metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                metadata.items(),
            )
        print(f"database={args.database.resolve()}")
        print(f"meeting_snapshot_values={snapshot_count}")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
