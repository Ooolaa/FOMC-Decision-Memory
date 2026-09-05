from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decision_memory.codex_subscription import (
    SUBSCRIPTION_CONFIRMATION,
    CodexSubscriptionExecutor,
    _write_new_json,
    run_subscription_sample,
)
from decision_memory.llm_sample import _sha256_file, build_case_bundle
from decision_memory.model_preflight import DEFAULT_SPEC_PATH, load_model_spec
from decision_memory.simulation_evaluation import evaluate_simulation_output


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FROZEN_MANIFEST = (
    ROOT / "artifacts" / "evaluation" / "frozen_45_policy_baselines_v1.json"
)
DEFAULT_OUTPUT_DIRECTORY = (
    ROOT / "artifacts" / "codex_subscription" / "frozen45_v1"
)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def load_frozen_meeting_ids(path: Path) -> list[str]:
    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    cases = payload.get("per_case")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Frozen manifest requires a non-empty per_case list")
    meeting_ids = [str(item["meeting_id"]) for item in cases]
    if len(meeting_ids) != len(set(meeting_ids)):
        raise ValueError("Frozen manifest contains duplicate meeting_id values")
    return meeting_ids


def aggregate_evaluations(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    if not evaluations:
        return {
            "case_count": 0,
            "policy_accuracy": None,
            "policy_action_mae": None,
            "false_action_on_hold": None,
            "dissent_base_rate": None,
            "dissent_precision": None,
            "dissent_recall": None,
            "dissent_f1": None,
        }
    true_positive = sum(int(item["dissent_true_positive"]) for item in evaluations)
    false_positive = sum(int(item["dissent_false_positive"]) for item in evaluations)
    false_negative = sum(int(item["dissent_false_negative"]) for item in evaluations)
    true_negative = sum(int(item["dissent_true_negative"]) for item in evaluations)
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else 0.0
    )
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    vote_count = true_positive + false_positive + false_negative + true_negative
    return {
        "case_count": len(evaluations),
        "policy_accuracy": sum(float(item["policy_accuracy"]) for item in evaluations)
        / len(evaluations),
        "policy_action_mae": sum(
            float(item["policy_action_mae"]) for item in evaluations
        )
        / len(evaluations),
        "false_action_on_hold": sum(
            float(item["false_action_on_hold"]) for item in evaluations
        ),
        "dissent_true_positive": true_positive,
        "dissent_false_positive": false_positive,
        "dissent_false_negative": false_negative,
        "dissent_true_negative": true_negative,
        "dissent_base_rate": (
            (true_positive + false_negative) / vote_count if vote_count else 0.0
        ),
        "dissent_precision": precision,
        "dissent_recall": recall,
        "dissent_f1": f1,
    }


def aggregate_usage(reports: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        "case_count": len(reports),
        "request_count": 0,
        "repair_request_count": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    for report in reports:
        usage = report.get("usage") or []
        totals["request_count"] += len(usage)
        totals["repair_request_count"] += sum(
            1 for record in usage if int(record.get("attempt", 1)) > 1
        )
        usage_totals = report.get("usage_totals") or {}
        for name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        ):
            totals[name] += int(usage_totals.get(name, 0) or 0)
    return totals


def _safe_output_directory(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise ValueError("Subscription batch output must stay inside the workspace")
    return resolved


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _bundle_path(output_directory: Path, meeting_id: str) -> Path:
    return output_directory / "bundles" / f"{meeting_id}.json"


def _run_path(output_directory: Path, meeting_id: str, model_id: str) -> Path:
    model_slug = model_id.replace(".", "-")
    return output_directory / "runs" / f"{meeting_id}_{model_slug}.json"


def build_frozen_bundle_preflight(
    *,
    source_database: Path,
    app_database: Path,
    frozen_manifest: Path = DEFAULT_FROZEN_MANIFEST,
    output_directory: Path = DEFAULT_OUTPUT_DIRECTORY,
) -> dict[str, Any]:
    output_root = _safe_output_directory(output_directory)
    entries = []
    for meeting_id in load_frozen_meeting_ids(frozen_manifest):
        bundle = build_case_bundle(
            source_database,
            app_database,
            meeting_id=meeting_id,
        )
        path = _bundle_path(output_root, meeting_id)
        _write_new_json(path, bundle)
        entries.append(
            {
                "meeting_id": meeting_id,
                "bundle_hash": bundle["bundle_hash"],
                "bundle_artifact": str(path.relative_to(ROOT)),
                "document_count": len(bundle["documents"]),
                "economic_snapshot_count": len(bundle["economic_snapshot"]),
                "participant_count": len(bundle["participants"]),
            }
        )
    report = {
        "schema_version": "codex_subscription_frozen_preflight_v1",
        "status": "PREFLIGHT_COMPLETED_NO_MODEL_CALL",
        "execution_provider": "local_deterministic",
        "platform_api_calls": 0,
        "platform_api_cost_usd": 0.0,
        "case_count": len(entries),
        "source_database_sha256": _sha256_file(source_database.resolve()),
        "app_database_sha256": _sha256_file(app_database.resolve()),
        "frozen_manifest_sha256": _sha256_file(frozen_manifest.resolve()),
        "cases": entries,
    }
    _write_new_json(output_root / "bundle_preflight.json", report)
    return report


def _validated_existing_report(
    path: Path,
    *,
    bundle: dict[str, Any],
    model_id: str,
) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "status": "SUBSCRIPTION_SAMPLE_COMPLETED",
        "execution_provider": "codex_subscription",
        "billing_route": "chatgpt_subscription",
        "platform_api_cost_usd": 0.0,
        "model_id": model_id,
        "meeting_id": bundle["meeting_id"],
        "bundle_hash": bundle["bundle_hash"],
    }
    mismatches = {
        key: {"expected": expected, "actual": report.get(key)}
        for key, expected in required.items()
        if report.get(key) != expected
    }
    if mismatches:
        raise ValueError(f"Existing subscription artifact mismatch: {mismatches}")
    return report


def run_frozen_subscription_batch(
    executor: CodexSubscriptionExecutor,
    *,
    source_database: Path,
    app_database: Path,
    spec: dict[str, Any],
    frozen_manifest: Path = DEFAULT_FROZEN_MANIFEST,
    output_directory: Path = DEFAULT_OUTPUT_DIRECTORY,
    max_new_cases: int | None = None,
) -> dict[str, Any]:
    if max_new_cases is not None and max_new_cases <= 0:
        raise ValueError("max_new_cases must be positive")
    output_root = _safe_output_directory(output_directory)
    meeting_ids = load_frozen_meeting_ids(frozen_manifest)
    status_path = output_root / "batch_status.json"
    completed = []
    evaluations = []
    reports = []
    new_cases = 0
    app = sqlite3.connect(
        f"file:{app_database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        for ordinal, meeting_id in enumerate(meeting_ids, start=1):
            if max_new_cases is not None and new_cases >= max_new_cases:
                break
            bundle = build_case_bundle(
                source_database,
                app_database,
                meeting_id=meeting_id,
            )
            bundle_path = _bundle_path(output_root, meeting_id)
            _write_new_json(bundle_path, bundle)
            run_path = _run_path(output_root, meeting_id, spec["model_id"])
            reused = run_path.exists()
            if reused:
                report = _validated_existing_report(
                    run_path, bundle=bundle, model_id=spec["model_id"]
                )
            else:
                report = run_subscription_sample(executor, bundle, spec)
                _write_new_json(run_path, report)
                new_cases += 1
            evaluation = evaluate_simulation_output(app, report["output"])
            reports.append(report)
            evaluations.append(evaluation)
            completed.append(
                {
                    "ordinal": ordinal,
                    "meeting_id": meeting_id,
                    "bundle_hash": bundle["bundle_hash"],
                    "run_artifact": str(run_path.relative_to(ROOT)),
                    "run_artifact_sha256": _sha256_file(run_path),
                    "reused": reused,
                    "evaluation": evaluation,
                }
            )
            _write_status(
                status_path,
                {
                    "schema_version": "codex_subscription_frozen_batch_v1",
                    "status": "RUNNING",
                    "updated_at": _utc_now(),
                    "execution_provider": "codex_subscription",
                    "billing_route": "chatgpt_subscription",
                    "platform_api_calls": 0,
                    "platform_api_cost_usd": 0.0,
                    "model_id": spec["model_id"],
                    "total_case_count": len(meeting_ids),
                    "completed_case_count": len(completed),
                    "new_case_count_this_run": new_cases,
                    "pending_case_count": len(meeting_ids) - len(completed),
                    "aggregate": aggregate_evaluations(evaluations),
                    "usage": aggregate_usage(reports),
                    "cases": completed,
                },
            )
    except Exception as error:
        _write_status(
            status_path,
            {
                "schema_version": "codex_subscription_frozen_batch_v1",
                "status": "FAILED_CLOSED",
                "updated_at": _utc_now(),
                "execution_provider": "codex_subscription",
                "billing_route": "chatgpt_subscription",
                "platform_api_calls": 0,
                "platform_api_cost_usd": 0.0,
                "model_id": spec["model_id"],
                "total_case_count": len(meeting_ids),
                "completed_case_count": len(completed),
                "new_case_count_this_run": new_cases,
                "pending_case_count": len(meeting_ids) - len(completed),
                "failure_type": type(error).__name__,
                "failure_message": str(error),
                "aggregate": aggregate_evaluations(evaluations),
                "usage": aggregate_usage(reports),
                "cases": completed,
            },
        )
        raise
    finally:
        app.close()
    status = {
        "schema_version": "codex_subscription_frozen_batch_v1",
        "status": "COMPLETED" if len(completed) == len(meeting_ids) else "PARTIAL",
        "updated_at": _utc_now(),
        "execution_provider": "codex_subscription",
        "billing_route": "chatgpt_subscription",
        "platform_api_calls": 0,
        "platform_api_cost_usd": 0.0,
        "model_id": spec["model_id"],
        "total_case_count": len(meeting_ids),
        "completed_case_count": len(completed),
        "new_case_count_this_run": new_cases,
        "pending_case_count": len(meeting_ids) - len(completed),
        "aggregate": aggregate_evaluations(evaluations),
        "usage": aggregate_usage(reports),
        "cases": completed,
    }
    _write_status(status_path, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or run the resumable Frozen-45 Codex subscription batch."
    )
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    parser.add_argument(
        "--frozen-manifest", type=Path, default=DEFAULT_FROZEN_MANIFEST
    )
    parser.add_argument(
        "--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY
    )
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--max-new-cases", type=int)
    parser.add_argument("--confirmation")
    args = parser.parse_args()
    if args.preflight_only:
        report = build_frozen_bundle_preflight(
            source_database=args.source,
            app_database=args.app,
            frozen_manifest=args.frozen_manifest,
            output_directory=args.output_directory,
        )
    else:
        if args.confirmation != SUBSCRIPTION_CONFIRMATION:
            raise ValueError(
                f"Subscription batch requires --confirmation "
                f"{SUBSCRIPTION_CONFIRMATION}"
            )
        executor = CodexSubscriptionExecutor()
        executor.verify_authentication()
        report = run_frozen_subscription_batch(
            executor,
            source_database=args.source,
            app_database=args.app,
            spec=load_model_spec(args.spec),
            frozen_manifest=args.frozen_manifest,
            output_directory=args.output_directory,
            max_new_cases=args.max_new_cases,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
