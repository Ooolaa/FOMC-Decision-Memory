from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from decision_memory.decision_trace import persist_fomc_decision_trace


def materialize_trace_fixture(
    source_database: Path,
    app_database: Path,
    fixture_path: Path,
    *,
    extractor_version: str,
) -> dict[str, object]:
    source_path = source_database.resolve()
    app_path = app_database.resolve()
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    app = sqlite3.connect(f"file:{app_path.as_posix()}?mode=rw", uri=True)
    app.execute("PRAGMA foreign_keys = ON")
    try:
        source.row_factory = sqlite3.Row
        series_rows = source.execute(
            """
            SELECT series_id, title, frequency, units, vintage_mode
            FROM economic_series
            """
        ).fetchall()
        monitor_series_metadata = {
            str(row["series_id"]): dict(row) for row in series_rows
        }
        allowed_series = set(monitor_series_metadata)
        report = persist_fomc_decision_trace(
            app,
            payload,
            extractor_version=extractor_version,
            allowed_monitor_series_ids=allowed_series,
            monitor_series_metadata=monitor_series_metadata,
        )
        app.commit()
        integrity = app.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = app.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"App DB validation failed: integrity={integrity}, "
                f"foreign_keys={foreign_key_errors}"
            )
    except Exception:
        app.rollback()
        raise
    finally:
        app.close()
        source.close()
    return {
        **report,
        "fixture": str(fixture_path.resolve()),
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize the audited demo trace.")
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("fixtures/decision_trace_fomc_2022_03_15.json"),
    )
    parser.add_argument(
        "--extractor-version",
        default="human-audited-official-docs-v1",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            materialize_trace_fixture(
                args.source,
                args.app,
                args.fixture,
                extractor_version=args.extractor_version,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
