from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from decision_memory.decision_trace import assumption_monitor_violations


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "artifacts"
    / "evaluation"
    / "decision_trace_assumption_monitor_audit_v1.json"
)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _workspace_file(path: Path, description: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()) or not resolved.is_file():
        raise ValueError(f"{description} must be an existing workspace file")
    return resolved


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    target = path.resolve()
    if not target.is_relative_to(ROOT.resolve()):
        raise ValueError("Assumption audit output must stay in workspace")
    if target.exists():
        raise FileExistsError(f"Assumption audit output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _load_series_metadata(source_database: Path) -> dict[str, dict[str, Any]]:
    connection = sqlite3.connect(
        f"file:{source_database.resolve().as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT series_id, title, frequency, units, vintage_mode
            FROM economic_series ORDER BY series_id
            """
        ).fetchall()
    finally:
        connection.close()
    metadata = {str(row["series_id"]): dict(row) for row in rows}
    if not metadata:
        raise ValueError("Economic-series metadata is empty")
    return metadata


def audit_assumption_monitors(
    batch_status_path: Path,
    source_database: Path,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    status_path = _workspace_file(batch_status_path, "Batch status")
    source_path = _workspace_file(source_database, "Source database")
    status_bytes = status_path.read_bytes()
    status = json.loads(status_bytes.decode("utf-8"))
    if status.get("execution_provider") != "codex_subscription":
        raise ValueError("Assumption audit requires a Codex subscription batch")
    if int(status.get("platform_api_calls", -1)) != 0:
        raise ValueError("Assumption audit requires zero Platform API calls")

    series_metadata = _load_series_metadata(source_path)
    violation_counts: Counter[str] = Counter()
    cases = []
    assumption_count = 0
    invalid_assumption_count = 0
    for case in status.get("cases") or []:
        run_path = _workspace_file(ROOT / case["run_artifact"], "Run artifact")
        actual_hash = _file_hash(run_path)
        if actual_hash != case["run_artifact_sha256"]:
            raise ValueError(
                f"DecisionTrace run artifact hash mismatch: {case['meeting_id']}"
            )
        run = json.loads(run_path.read_text(encoding="utf-8"))
        assumptions = (run.get("trace") or {}).get("assumptions")
        if not isinstance(assumptions, list):
            raise ValueError(
                f"DecisionTrace assumptions are missing: {case['meeting_id']}"
            )
        assumption_results = []
        for assumption in assumptions:
            violations = assumption_monitor_violations(
                assumption,
                series_metadata,
            )
            assumption_count += 1
            if violations:
                invalid_assumption_count += 1
                violation_counts.update(violations)
            assumption_results.append(
                {
                    "assumption_id": assumption["assumption_id"],
                    "claim": assumption["claim"],
                    "monitor_series_id": assumption["monitor_series_id"],
                    "monitor_operator": assumption["monitor_operator"],
                    "threshold_value": assumption["threshold_value"],
                    "monitor_rule_version": assumption["monitor_rule_version"],
                    "valid": not violations,
                    "violations": violations,
                }
            )
        case_invalid_count = sum(
            1 for item in assumption_results if item["valid"] is False
        )
        cases.append(
            {
                "meeting_id": case["meeting_id"],
                "run_artifact": case["run_artifact"],
                "run_artifact_sha256": actual_hash,
                "assumption_count": len(assumption_results),
                "invalid_assumption_count": case_invalid_count,
                "valid": case_invalid_count == 0,
                "assumptions": assumption_results,
            }
        )

    invalid_case_count = sum(1 for item in cases if item["valid"] is False)
    report = {
        "schema_version": "decision_trace_assumption_monitor_audit_v1",
        "status": (
            "COMPLETE_WITH_BLOCKING_DEFECTS"
            if invalid_assumption_count
            else "COMPLETE_VALIDATED"
        ),
        "execution_provider": "local_deterministic",
        "platform_api_calls": 0,
        "platform_api_cost_usd": 0.0,
        "semantic_contract_version": "atomic_one_clause_monitor_v1",
        "source_batch_status": str(status_path.relative_to(ROOT.resolve())),
        "source_batch_status_sha256": hashlib.sha256(status_bytes).hexdigest(),
        "source_database": str(source_path.relative_to(ROOT.resolve())),
        "source_database_sha256": _file_hash(source_path),
        "series_metadata_sha256": hashlib.sha256(
            _canonical_json(series_metadata).encode("utf-8")
        ).hexdigest(),
        "case_count": len(cases),
        "valid_case_count": len(cases) - invalid_case_count,
        "invalid_case_count": invalid_case_count,
        "assumption_count": assumption_count,
        "valid_assumption_count": assumption_count - invalid_assumption_count,
        "invalid_assumption_count": invalid_assumption_count,
        "violation_counts": dict(sorted(violation_counts.items())),
        "cases": cases,
    }
    if output_path is not None:
        _write_new_json(output_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit DecisionTrace assumption monitor semantics offline."
    )
    parser.add_argument("batch_status", type=Path)
    parser.add_argument(
        "--source", type=Path, default=Path("fred_fomc_real.sqlite")
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = audit_assumption_monitors(
        args.batch_status,
        args.source,
        output_path=args.output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
