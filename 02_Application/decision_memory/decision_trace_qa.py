from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decision_memory.decision_trace import validate_fomc_decision_trace


ROOT = Path(__file__).resolve().parents[1]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_only_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def _database_health(path: Path) -> dict[str, Any]:
    connection = _read_only_connection(path)
    try:
        integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        foreign_key_rows = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
    finally:
        connection.close()
    if integrity_rows != ["ok"]:
        raise ValueError(f"SQLite integrity check failed: {path}")
    if foreign_key_rows:
        raise ValueError(f"SQLite foreign-key check failed: {path}")
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())),
        "sha256": _sha256_file(path),
        "integrity_check": "ok",
        "foreign_key_violation_count": 0,
    }


def build_trace_qa_queue(
    batch_status_path: Path,
    *,
    output_path: Path | None = None,
    source_database: Path | None = None,
    app_database: Path | None = None,
    require_complete_corpus: bool = False,
) -> dict[str, Any]:
    status_path = batch_status_path.resolve()
    status_bytes = status_path.read_bytes()
    status = json.loads(status_bytes.decode("utf-8"))
    status_sha256 = hashlib.sha256(status_bytes).hexdigest()
    if status.get("execution_provider") != "codex_subscription":
        raise ValueError("DecisionTrace QA requires a subscription batch")
    if int(status.get("platform_api_calls", -1)) != 0:
        raise ValueError("DecisionTrace QA batch must report zero Platform API calls")
    if require_complete_corpus:
        case_items = status.get("cases") or []
        meeting_ids = [str(item["meeting_id"]) for item in case_items]
        if (
            status.get("status") != "COMPLETED"
            or int(status.get("total_case_count", -1)) != 50
            or int(status.get("completed_case_count", -1)) != 50
            or int(status.get("pending_case_count", -1)) != 0
            or len(meeting_ids) != 50
            or len(set(meeting_ids)) != 50
        ):
            raise ValueError("R5 DecisionTrace QA requires a completed 50-case corpus")

    if (source_database is None) != (app_database is None):
        raise ValueError("source_database and app_database must be supplied together")
    database_health = None
    allowed_monitor_series_ids: set[str] | None = None
    monitor_series_metadata: dict[str, dict[str, Any]] | None = None
    app_connection: sqlite3.Connection | None = None
    if source_database is not None and app_database is not None:
        source_path = source_database.resolve()
        app_path = app_database.resolve()
        if not source_path.is_relative_to(ROOT.resolve()) or not app_path.is_relative_to(
            ROOT.resolve()
        ):
            raise ValueError("DecisionTrace QA databases must stay in workspace")
        database_health = {
            "source": _database_health(source_path),
            "application": _database_health(app_path),
        }
        source_connection = _read_only_connection(source_path)
        try:
            source_connection.row_factory = sqlite3.Row
            series_rows = source_connection.execute(
                """
                SELECT series_id, title, frequency, units, vintage_mode
                FROM economic_series ORDER BY series_id
                """
            ).fetchall()
            monitor_series_metadata = {
                str(row["series_id"]): dict(row) for row in series_rows
            }
            allowed_monitor_series_ids = set(monitor_series_metadata)
        finally:
            source_connection.close()
        if not allowed_monitor_series_ids:
            raise ValueError("Economic-series allowlist is empty")
        app_connection = _read_only_connection(app_path)

    cases = []
    try:
        for item in status.get("cases") or []:
            run_path = (ROOT / item["run_artifact"]).resolve()
            if not run_path.is_relative_to(ROOT.resolve()) or not run_path.is_file():
                raise ValueError(f"DecisionTrace run artifact is missing: {run_path}")
            if _sha256_file(run_path) != item["run_artifact_sha256"]:
                raise ValueError(
                    f"DecisionTrace run artifact hash mismatch: {item['meeting_id']}"
                )
            report = json.loads(run_path.read_text(encoding="utf-8"))
            validation = report.get("semantic_validation") or {}
            deterministic_validation = {"executed": False, "valid": None}
            if (
                app_connection is not None
                and allowed_monitor_series_ids is not None
                and monitor_series_metadata is not None
            ):
                validate_fomc_decision_trace(
                    app_connection,
                    report["trace"],
                    allowed_monitor_series_ids=allowed_monitor_series_ids,
                    monitor_series_metadata=monitor_series_metadata,
                )
                deterministic_validation = {"executed": True, "valid": True}
            flags = []
            if int(validation.get("participant_debate_count", 0) or 0) == 0:
                flags.append("NO_PARTICIPANT_LEVEL_DEBATE")
            if int(validation.get("transcript_evidence_reference_count", 0) or 0) == 0:
                flags.append("NO_TRANSCRIPT_EVIDENCE")
            if len(report.get("usage") or []) > 1:
                flags.append("SEMANTIC_REPAIR_USED")
            attribution = report.get("attribution_sanitization") or {}
            if int(attribution.get("demoted_item_count", 0) or 0) > 0:
                flags.append("ATTRIBUTION_DEMOTED_TO_COMMITTEE")
            if int(
                attribution.get("removed_mismatched_reference_count", 0) or 0
            ) > 0:
                flags.append("MISMATCHED_ATTRIBUTION_REFERENCE_REMOVED")
            cases.append(
                {
                    "meeting_id": item["meeting_id"],
                    "run_artifact": item["run_artifact"],
                    "run_artifact_sha256": item["run_artifact_sha256"],
                    "audit_priority": "HIGH" if flags else "STANDARD",
                    "review_status": "PENDING",
                    "flags": flags,
                    "semantic_validation": validation,
                    "deterministic_revalidation": deterministic_validation,
                }
            )
    finally:
        if app_connection is not None:
            app_connection.close()

    priority_counts = {
        priority: sum(1 for item in cases if item["audit_priority"] == priority)
        for priority in ("HIGH", "STANDARD")
        if any(item["audit_priority"] == priority for item in cases)
    }
    queue = {
        "schema_version": "decision_trace_qa_queue_v1",
        "status": "PENDING_HUMAN_REVIEW",
        "created_at": _utc_now(),
        "source_batch_status": str(status_path.relative_to(ROOT.resolve())),
        "source_batch_status_sha256": status_sha256,
        "batch_status": status["status"],
        "case_count": len(cases),
        "deterministically_revalidated_case_count": sum(
            1 for item in cases if item["deterministic_revalidation"]["valid"] is True
        ),
        "database_health": database_health,
        "priority_counts": priority_counts,
        "cases": cases,
    }
    target = output_path.resolve() if output_path else status_path.parent / "qa_queue.json"
    if not target.is_relative_to(ROOT.resolve()):
        raise ValueError("DecisionTrace QA output must stay in workspace")
    _write_json(target, queue)
    return queue


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic human-review queue for DecisionTrace runs."
    )
    parser.add_argument("batch_status", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    args = parser.parse_args()
    queue = build_trace_qa_queue(
        args.batch_status,
        output_path=args.output,
        source_database=args.source,
        app_database=args.app,
        require_complete_corpus=True,
    )
    print(json.dumps(queue, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
