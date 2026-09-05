from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from decision_memory.decision_trace import _iter_evidence_refs
from decision_memory.enterprise_trace import (
    persist_enterprise_decision_trace,
    register_synthetic_fixture_document,
)


PLACEHOLDER_DOCUMENT_ID = "ENTERPRISE_FIXTURE_DOCUMENT_ID"


def materialize_enterprise_trace(
    app_database: Path,
    fixture_path: Path,
    memo_path: Path,
    *,
    extractor_version: str,
) -> dict[str, Any]:
    app_path = app_database.resolve()
    fixture = fixture_path.resolve()
    memo = memo_path.resolve()
    for path in (app_path, fixture, memo):
        if not path.is_file():
            raise FileNotFoundError(f"Required enterprise fixture is missing: {path}")
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    app = sqlite3.connect(f"file:{app_path.as_posix()}?mode=rw", uri=True)
    app.execute("PRAGMA foreign_keys = ON")
    try:
        document_id = register_synthetic_fixture_document(
            app,
            memo,
            decision_id=payload["decision_id"],
            publication_at="2021-06-01T00:00:00Z",
        )
        for reference in _iter_evidence_refs(payload):
            if reference["document_id"] != PLACEHOLDER_DOCUMENT_ID:
                raise ValueError(
                    "Enterprise fixture contains an unexpected document_id: "
                    f"{reference['document_id']}"
                )
            reference["document_id"] = document_id
        report = persist_enterprise_decision_trace(
            app,
            payload,
            extractor_version=extractor_version,
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
    return {
        **report,
        "document_id": document_id,
        "fixture": str(fixture),
        "memo": str(memo),
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize the synthetic/composite enterprise demo trace."
    )
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("fixtures/decision_trace_enterprise_demo_financing.json"),
    )
    parser.add_argument(
        "--memo",
        type=Path,
        default=Path("fixtures/enterprise_demo_financing_decision.html"),
    )
    parser.add_argument(
        "--extractor-version",
        default="human-authored-synthetic-composite-v1",
    )
    args = parser.parse_args()
    report = materialize_enterprise_trace(
        args.app,
        args.fixture,
        args.memo,
        extractor_version=args.extractor_version,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
