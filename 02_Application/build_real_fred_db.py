from __future__ import annotations

import argparse
import sqlite3
import winreg
from datetime import date
from pathlib import Path

from fomc_calendar import load_official_fomc_meetings
from fred_vintage_db import FredClient, build_database, update_database


DEFAULT_SERIES = [
    "UNRATE",
    "PAYEMS",
    "PCEPILFE",
    "GDPC1",
    "CPIAUCSL",
    "CPILFESL",
    "PCEPI",
    "CES0500000003",
    "ICSA",
    "INDPRO",
    "RSAFS",
    "HOUST",
    "PERMIT",
    "DGS2",
    "DGS10",
    "BAA10Y",
    "NFCI",
    "T10YIE",
    "T5YIFR",
    "DFEDTAR",
    "DFEDTARU",
    "DFEDTARL",
]


def load_user_fred_api_key() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "FRED_API_KEY")
    except FileNotFoundError as error:
        raise RuntimeError("FRED_API_KEY(User) is missing") from error
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("FRED_API_KEY(User) is missing or blank")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a point-in-time FOMC SQLite database from FRED/ALFRED."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("fred_fomc_real.sqlite"),
    )
    parser.add_argument(
        "--observation-start",
        default="1996-01-01",
    )
    parser.add_argument(
        "--series",
        nargs="+",
        default=DEFAULT_SERIES,
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Add missing series to an existing REAL_FRED_ALFRED database.",
    )
    parser.add_argument(
        "--calendar-through-date",
        type=date.fromisoformat,
        default=date.today(),
    )
    parser.add_argument(
        "--strict-point-in-time",
        action="store_true",
        help=(
            "Refresh existing series from observation-start and preserve missing "
            "historical vintages instead of synthesizing backfill values."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = load_user_fred_api_key()
    database_path = args.output.resolve()
    client = FredClient(api_key)
    meetings = load_official_fomc_meetings(
        start_year=2006,
        through_date=args.calendar_through_date,
    )
    if args.update_existing:
        counts = update_database(
            database_path=database_path,
            client=client,
            series_ids=args.series,
            meetings=meetings,
            observation_start=args.observation_start,
            progress=lambda message: print(message, flush=True),
            strict_point_in_time=args.strict_point_in_time,
            calendar_through_date=args.calendar_through_date.isoformat(),
        )
    else:
        counts = build_database(
            output_path=database_path,
            client=client,
            series_ids=args.series,
            meetings=meetings,
            observation_start=args.observation_start,
        )

    connection = sqlite3.connect(args.output)
    try:
        series_count = connection.execute(
            "SELECT COUNT(*) FROM economic_series"
        ).fetchone()[0]
        vintage_count = connection.execute(
            "SELECT COUNT(*) FROM observation_vintage"
        ).fetchone()[0]
        meeting_count = connection.execute(
            "SELECT COUNT(*) FROM fomc_meeting"
        ).fetchone()[0]
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM meeting_snapshot_value"
        ).fetchone()[0]
    finally:
        connection.close()

    print(f"database={args.output.resolve()}")
    print(f"series={series_count}")
    print(f"observation_vintages={vintage_count}")
    print(f"meetings={meeting_count}")
    print(f"meeting_snapshot_values={snapshot_count}")
    print("downloaded_by_series=" + ",".join(f"{key}:{value}" for key, value in counts.items() if key in args.series))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
