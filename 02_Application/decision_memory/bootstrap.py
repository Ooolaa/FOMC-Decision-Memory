from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from decision_memory.app_db import create_schema
from decision_memory.enterprise import seed_enterprise_fixture_from_source
from decision_memory.policy_rate import (
    build_policy_rate_context,
    replace_policy_rate_context,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bootstrap_app_database(
    source_path: Path,
    output_path: Path,
    enterprise_decision_date: str | None = None,
    enterprise_threshold: float | None = None,
) -> dict[str, Any]:
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Source database does not exist: {source_path}")
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite app database: {output_path}")
    if source_path == output_path:
        raise ValueError("Source and app database paths must differ")
    if (enterprise_decision_date is None) != (enterprise_threshold is None):
        raise ValueError(
            "enterprise_decision_date and enterprise_threshold must be provided together"
        )

    source = sqlite3.connect(
        f"file:{source_path.as_posix()}?mode=ro",
        uri=True,
    )
    app = sqlite3.connect(output_path)
    app.execute("PRAGMA foreign_keys = ON")
    meeting_count = 0
    policy_record_count = 0
    try:
        create_schema(app)
        meeting_ids = [
            row[0]
            for row in source.execute(
                "SELECT meeting_id FROM fomc_meeting ORDER BY meeting_start_date"
            ).fetchall()
        ]
        for meeting_id in meeting_ids:
            records = build_policy_rate_context(source, meeting_id)
            policy_record_count += replace_policy_rate_context(app, records)
        meeting_count = len(meeting_ids)
        enterprise_fixture = None
        if enterprise_decision_date is not None and enterprise_threshold is not None:
            enterprise_fixture = seed_enterprise_fixture_from_source(
                source,
                app,
                decision_date=enterprise_decision_date,
                threshold_value=enterprise_threshold,
            )
        app.commit()

        integrity = app.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"App database integrity_check failed: {integrity}")
        foreign_key_errors = app.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(
                f"App database foreign_key_check failed: {foreign_key_errors}"
            )
    except Exception:
        app.rollback()
        app.close()
        source.close()
        output_path.unlink(missing_ok=True)
        raise
    else:
        app.close()
        source.close()

    return {
        "source_database": str(source_path),
        "source_sha256": _sha256(source_path),
        "app_database": str(output_path),
        "app_sha256": _sha256(output_path),
        "meetings": meeting_count,
        "policy_rate_context_records": policy_record_count,
        "enterprise_fixture": enterprise_fixture,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the derived FOMC decision-memory app database."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("fred_fomc_real.sqlite"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("fomc_simulation.sqlite"),
    )
    parser.add_argument(
        "--enterprise-decision-date",
        help="Optional YYYY-MM-DD decision date for the synthetic enterprise fixture.",
    )
    parser.add_argument(
        "--enterprise-threshold",
        type=float,
        help="Optional frozen BAA10Y upper-bound threshold for the fixture.",
    )
    args = parser.parse_args()
    report = bootstrap_app_database(
        args.source,
        args.output,
        enterprise_decision_date=args.enterprise_decision_date,
        enterprise_threshold=args.enterprise_threshold,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
