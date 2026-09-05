from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from decision_memory.decision_trace import persist_fomc_decision_trace
from decision_memory.human_review_results import validate_human_review_results


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BATCH_TRACE_COUNT = 50


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_reviewed_runs(corpus_directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    corpus = corpus_directory.resolve()
    review_audit = validate_human_review_results(
        corpus / "human_review_sample_v1.json",
        corpus / "human_review_results_v1.json",
        root=ROOT,
    )
    if review_audit["formal_import_gate"] != "PASS":
        raise ValueError("DecisionTrace human review gate is not PASS")

    status = _read_json(corpus / "batch_status.json")
    case_items = list(status.get("cases") or [])
    meeting_ids = [str(item.get("meeting_id") or "") for item in case_items]
    if (
        status.get("status") != "COMPLETED"
        or status.get("execution_provider") != "codex_subscription"
        or int(status.get("platform_api_calls", -1)) != 0
        or int(status.get("total_case_count", -1)) != EXPECTED_BATCH_TRACE_COUNT
        or int(status.get("completed_case_count", -1)) != EXPECTED_BATCH_TRACE_COUNT
        or int(status.get("pending_case_count", -1)) != 0
        or len(meeting_ids) != EXPECTED_BATCH_TRACE_COUNT
        or "" in meeting_ids
        or len(set(meeting_ids)) != EXPECTED_BATCH_TRACE_COUNT
    ):
        raise ValueError("DecisionTrace corpus is not a completed unique 50-case batch")

    reports = []
    for item in case_items:
        run_path = (ROOT / item["run_artifact"]).resolve()
        if not run_path.is_file() or not run_path.is_relative_to(ROOT):
            raise ValueError(f"DecisionTrace run artifact is missing: {item['meeting_id']}")
        if _sha256_file(run_path) != item["run_artifact_sha256"]:
            raise ValueError(
                f"DecisionTrace run artifact hash mismatch: {item['meeting_id']}"
            )
        report = _read_json(run_path)
        if (
            report.get("status") != "SUBSCRIPTION_TRACE_COMPLETED"
            or report.get("meeting_id") != item["meeting_id"]
            or report.get("extractor_version") != status.get("extractor_version")
            or not (report.get("semantic_validation") or {}).get("valid")
        ):
            raise ValueError(f"DecisionTrace run metadata mismatch: {item['meeting_id']}")
        reports.append(report)
    return review_audit, reports


def materialize_decision_trace_corpus(
    source_database: Path,
    candidate_database: Path,
    corpus_directory: Path,
    output_database: Path,
) -> dict[str, Any]:
    source = source_database.resolve()
    candidate = candidate_database.resolve()
    output = output_database.resolve()
    if not source.is_file() or not candidate.is_file():
        raise FileNotFoundError("Source and candidate databases are required")
    if output in {source, candidate} or output.exists():
        raise ValueError("output database must be new and distinct from both inputs")

    review_audit, reports = _load_reviewed_runs(corpus_directory)
    source_hash_before = _sha256_file(source)
    candidate_hash_before = _sha256_file(candidate)

    source_connection = sqlite3.connect(
        f"file:{source.as_posix()}?mode=ro", uri=True
    )
    source_connection.row_factory = sqlite3.Row
    try:
        series_rows = source_connection.execute(
            """
            SELECT series_id, title, frequency, units, vintage_mode
            FROM economic_series
            """
        ).fetchall()
    finally:
        source_connection.close()
    monitor_series_metadata = {
        str(row["series_id"]): dict(row) for row in series_rows
    }
    if not monitor_series_metadata:
        raise ValueError("Economic-series monitor allowlist is empty")

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate, output)
    connection = sqlite3.connect(output)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        for report in reports:
            trace = copy.deepcopy(report["trace"])
            for assumption in trace["assumptions"]:
                assumption["assumption_id"] = (
                    f"{trace['meeting_id']}::{assumption['assumption_id']}"
                )
            persist_fomc_decision_trace(
                connection,
                trace,
                extractor_version=report["extractor_version"],
                allowed_monitor_series_ids=set(monitor_series_metadata),
                monitor_series_metadata=monitor_series_metadata,
            )
        connection.commit()

        integrity_check = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        counts = {
            "fomc_trace_count": connection.execute(
                """
                SELECT COUNT(*)
                FROM decision_trace
                JOIN decision_case USING (decision_id)
                WHERE domain = 'fomc'
                """
            ).fetchone()[0],
            "meeting_outcome_count": connection.execute(
                "SELECT COUNT(*) FROM meeting_outcome"
            ).fetchone()[0],
            "participant_vote_count": connection.execute(
                "SELECT COUNT(*) FROM participant_vote"
            ).fetchone()[0],
        }
        if integrity_check != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"Derived database validation failed: integrity={integrity_check}, "
                f"foreign_keys={foreign_key_errors}"
            )
        if counts != {
            "fomc_trace_count": 51,
            "meeting_outcome_count": 166,
            "participant_vote_count": 1736,
        }:
            raise RuntimeError(f"Derived database counts are unexpected: {counts}")
    except Exception:
        connection.rollback()
        connection.close()
        output.unlink(missing_ok=True)
        raise
    else:
        connection.close()

    source_hash_after = _sha256_file(source)
    candidate_hash_after = _sha256_file(candidate)
    if (
        source_hash_after != source_hash_before
        or candidate_hash_after != candidate_hash_before
    ):
        output.unlink(missing_ok=True)
        raise RuntimeError("An input database changed during materialization")

    return {
        "schema_version": "decision_trace_display_materialization_v1",
        "source_database": str(source),
        "source_database_sha256": source_hash_after,
        "candidate_database": str(candidate),
        "candidate_database_sha256": candidate_hash_after,
        "output_database": str(output),
        "output_database_sha256": _sha256_file(output),
        "imported_batch_trace_count": len(reports),
        **counts,
        "integrity_check": integrity_check,
        "foreign_key_error_count": len(foreign_key_errors),
        "assumption_id_namespace": "meeting_id::artifact_assumption_id",
        "human_review": review_audit,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize the reviewed 50-case DecisionTrace display database."
    )
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("fomc_simulation.transcript_segmentation_v3_candidate.sqlite"),
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(
            "artifacts/codex_subscription/"
            "decision_trace_50_v5_atomic_monitor_segmentation_v3"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("fomc_simulation.decision_trace_50_display.sqlite"),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            materialize_decision_trace_corpus(
                args.source,
                args.candidate,
                args.corpus,
                args.output,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
