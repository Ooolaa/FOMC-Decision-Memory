from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from decision_memory.app_db import migrate_assumption_event_schema


def migrate_app_database(database_path: Path) -> dict[str, object]:
    resolved = database_path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"App database does not exist: {resolved}")
    connection = sqlite3.connect(
        f"file:{resolved.as_posix()}?mode=rw",
        uri=True,
    )
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        event_schema_changed = migrate_assumption_event_schema(connection)
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"App DB validation failed: integrity={integrity}, "
                f"foreign_keys={foreign_key_errors}"
            )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "database": str(resolved),
        "assumption_event_schema_changed": event_schema_changed,
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate the R5 app database.")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("fomc_simulation.sqlite"),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            migrate_app_database(args.database),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
