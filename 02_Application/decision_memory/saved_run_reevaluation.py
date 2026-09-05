from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from decision_memory.simulation_evaluation import evaluate_simulation_output
from decision_memory.subscription_batch import aggregate_evaluations


ROOT = Path(__file__).resolve().parents[1]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reevaluate_saved_batch(
    app_database: Path,
    batch_status_path: Path,
    baseline_path: Path,
) -> dict[str, Any]:
    app_path = app_database.resolve()
    status_path = batch_status_path.resolve()
    reference_path = baseline_path.resolve()
    for path in (app_path, status_path, reference_path):
        if not path.is_file():
            raise FileNotFoundError(f"Required input is missing: {path}")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    preflight_path = status_path.with_name("bundle_preflight.json")
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    app_hash = _sha256_file(app_path)
    app = sqlite3.connect(f"file:{app_path.as_posix()}?mode=ro", uri=True)
    cases = []
    evaluations = []
    try:
        for item in status["cases"]:
            run_path = Path(item["run_artifact"])
            if not run_path.is_absolute():
                run_path = ROOT / run_path
            run_path = run_path.resolve()
            run_hash = _sha256_file(run_path)
            if run_hash != item["run_artifact_sha256"]:
                raise RuntimeError(f"Saved run hash differs: {run_path}")
            run = json.loads(run_path.read_text(encoding="utf-8"))
            evaluation = evaluate_simulation_output(app, run["output"])
            evaluations.append(evaluation)
            cases.append(
                {
                    "meeting_id": item["meeting_id"],
                    "run_artifact": str(run_path.relative_to(ROOT)),
                    "run_artifact_sha256": run_hash,
                    "evaluation": evaluation,
                }
            )
        integrity = app.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = app.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"App DB validation failed: integrity={integrity}, "
                f"foreign_keys={foreign_key_errors}"
            )
    finally:
        app.close()
    aggregate = aggregate_evaluations(evaluations)
    reference_f1 = float(reference["gate"]["reference_f1"])
    source_bundle_hash = str(preflight["app_database_sha256"])
    stale_inputs = source_bundle_hash != app_hash
    metric_only_pass = float(aggregate["dissent_f1"]) > reference_f1
    return {
        "schema_version": "saved_run_reevaluation_v1",
        "status": (
            "DIAGNOSTIC_ONLY_STALE_INPUT_BUNDLES"
            if stale_inputs
            else "CURRENT_INPUT_LINEAGE"
        ),
        "app_database": str(app_path),
        "app_database_sha256": app_hash,
        "source_batch_status": str(status_path),
        "source_batch_status_sha256": _sha256_file(status_path),
        "source_bundle_app_database_sha256": source_bundle_hash,
        "input_lineage_stale": stale_inputs,
        "model_id": status["model_id"],
        "billing_route": status["billing_route"],
        "platform_api_calls_for_reevaluation": 0,
        "case_count": len(cases),
        "aggregate": aggregate,
        "gate": {
            "reference_artifact": str(reference_path),
            "reference_baseline": reference["gate"]["reference_baseline"],
            "reference_f1": reference_f1,
            "model_f1": aggregate["dissent_f1"],
            "metric_only_pass": metric_only_pass,
            "roster_coverage": 1.0,
            "label_coverage": 1.0,
            "promotion_eligible": bool(metric_only_pass and not stale_inputs),
            "promotion_blockers": [
                blocker
                for blocker, active in (
                    ("input_lineage_stale", stale_inputs),
                    ("model_dissent_f1_not_above_reference", not metric_only_pass),
                )
                if active
            ],
        },
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
        "cases": cases,
    }


def materialize_saved_batch_reevaluation(
    app_database: Path,
    batch_status_path: Path,
    baseline_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    result = reevaluate_saved_batch(
        app_database,
        batch_status_path,
        baseline_path,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    resolved_output = output_path.resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    if resolved_output.exists():
        if resolved_output.read_text(encoding="utf-8") != serialized:
            raise RuntimeError(f"Existing reevaluation artifact differs: {resolved_output}")
    else:
        resolved_output.write_text(serialized, encoding="utf-8")
    return {
        "output_path": str(resolved_output),
        "status": result["status"],
        "case_count": result["case_count"],
        "aggregate": result["aggregate"],
        "gate": result["gate"],
        "platform_api_calls_for_reevaluation": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-evaluate immutable saved simulation outputs without API calls."
    )
    parser.add_argument(
        "--app",
        type=Path,
        default=Path("fomc_simulation.vote_labels_fixed_candidate.sqlite"),
    )
    parser.add_argument(
        "--batch-status",
        type=Path,
        default=Path("artifacts/codex_subscription/frozen45_v1/batch_status.json"),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path(
            "artifacts/evaluation/frozen_45_vote_baselines_candidate_v1.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "artifacts/evaluation/frozen45_saved_outputs_candidate_reevaluation_v1.json"
        ),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            materialize_saved_batch_reevaluation(
                args.app,
                args.batch_status,
                args.baseline,
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
