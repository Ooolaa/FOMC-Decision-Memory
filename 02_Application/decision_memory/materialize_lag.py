from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from decision_memory.lag_evaluator import (
    evaluate_observable_lag,
    persist_observable_lag_result,
)
from decision_memory.lag_spec import load_observable_lag_spec


def materialize_observable_lag(
    source_database: Path,
    app_database: Path,
    spec_path: Path,
    *,
    as_of_date: str,
) -> dict[str, object]:
    source_path = source_database.resolve()
    app_path = app_database.resolve()
    for required_path in (source_path, app_path, spec_path.resolve()):
        if not required_path.is_file():
            raise FileNotFoundError(f"Required file does not exist: {required_path}")
    spec = load_observable_lag_spec(spec_path.resolve())
    source = sqlite3.connect(
        f"file:{source_path.as_posix()}?mode=ro",
        uri=True,
    )
    app = sqlite3.connect(
        f"file:{app_path.as_posix()}?mode=rw",
        uri=True,
    )
    app.execute("PRAGMA foreign_keys = ON")
    try:
        result = evaluate_observable_lag(
            source,
            app,
            spec,
            as_of_date=as_of_date,
        )
        golden = spec["golden_case"]
        for field, expected in golden.items():
            if result.get(field) != expected:
                raise RuntimeError(
                    f"Golden lag mismatch for {field}: "
                    f"expected {expected}, got {result.get(field)}"
                )
        persistence = persist_observable_lag_result(app, result, spec)
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
        "result": result,
        "persistence": persistence,
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize the pre-registered observable lag golden case."
    )
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--spec",
        type=Path,
        default=Path("metric_spec/inflation_transitory_v1.json"),
    )
    parser.add_argument("--as-of-date", default="2026-08-27")
    args = parser.parse_args()
    report = materialize_observable_lag(
        args.source,
        args.app,
        args.spec,
        as_of_date=args.as_of_date,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
