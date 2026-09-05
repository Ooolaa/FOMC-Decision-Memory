from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from decision_memory.ablation_spec import load_ablation_spec
from decision_memory.codex_subscription import (
    SUBSCRIPTION_CONFIRMATION,
    CodexSubscriptionExecutor,
    _write_new_json,
    run_subscription_sample,
)
from decision_memory.llm_sample import build_case_bundle
from decision_memory.model_preflight import DEFAULT_SPEC_PATH, load_model_spec
from decision_memory.simulation_evaluation import evaluate_simulation_output
from decision_memory.simulation_variants import restore_simulation_output
from decision_memory.subscription_batch import (
    DEFAULT_FROZEN_MANIFEST,
    aggregate_evaluations,
    aggregate_usage,
    load_frozen_meeting_ids,
)
from decision_memory.simulation_variants import prepare_variant_bundle


DATE_PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "action_class": {"type": "string", "enum": ["CUT", "HOLD", "HIKE"]},
        "rationale": {"type": "string", "minLength": 1},
    },
    "required": ["action_class", "rationale"],
    "additionalProperties": False,
}
ACTION_ORDINAL = {"CUT": -1, "HOLD": 0, "HIKE": 1}
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ABLATION_SPEC = ROOT / "evaluation_spec" / "hackathon_r5_variants_v1.json"
DEFAULT_REACTION_ARTIFACT = (
    ROOT / "artifacts" / "reaction" / "pooled_ordered_logit_v1.json"
)


def _read_only_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _date_probe_evaluation(
    app_database: Path, meeting_id: str, predicted_action: str
) -> dict[str, Any]:
    app = _read_only_connection(app_database)
    try:
        row = app.execute(
            "SELECT action_class FROM meeting_outcome WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()
    finally:
        app.close()
    if row is None:
        raise ValueError(f"Date probe outcome label is missing: {meeting_id}")
    actual = str(row[0])
    return {
        "evaluator_version": "date_probe_policy_only_v1",
        "metric_coverage": "policy_only",
        "actual_action_class": actual,
        "predicted_action_class": predicted_action,
        "policy_accuracy": float(predicted_action == actual),
        "policy_action_mae": abs(
            ACTION_ORDINAL[predicted_action] - ACTION_ORDINAL[actual]
        ),
        "false_action_on_hold": int(actual == "HOLD" and predicted_action != "HOLD"),
        "dissent_metrics": None,
    }


def run_variant_case(
    executor: Any,
    prepared: dict[str, Any],
    spec: dict[str, Any],
    *,
    app_database: Path,
    sample_runner: Callable[..., dict[str, Any]] = run_subscription_sample,
) -> dict[str, Any]:
    variant_id = prepared["variant_id"]
    meeting_id = prepared["actual_meeting_id"]
    model_bundle = prepared["model_bundle"]
    if variant_id == "date_only_memorization_probe":
        prompt = (
            "This is a memorization probe. Based only on the supplied meeting date, "
            "predict CUT, HOLD, or HIKE. Do not call tools or inspect the filesystem. "
            "Return exactly the required JSON object. INPUT="
            + json.dumps(model_bundle, ensure_ascii=False, separators=(",", ":"))
        )
        result = executor.run_stage(
            model_id=spec["model_id"],
            reasoning_effort="medium",
            prompt=prompt,
            schema=DATE_PROBE_SCHEMA,
        )
        errors = sorted(
            Draft202012Validator(DATE_PROBE_SCHEMA).iter_errors(result["output"]),
            key=lambda item: list(item.path),
        )
        if errors:
            raise ValueError(f"Date probe schema failure: {errors[0].message}")
        usage = {
            "stage": "date_probe",
            "attempt": 1,
            "thread_id": result.get("thread_id"),
            "reasoning_effort": "medium",
            "latency_seconds": result["latency_seconds"],
            **result["usage"],
        }
        return {
            "status": "SUBSCRIPTION_VARIANT_COMPLETED",
            "execution_provider": "codex_subscription",
            "billing_route": "chatgpt_subscription",
            "platform_api_cost_usd": 0.0,
            "model_id": spec["model_id"],
            "variant_id": variant_id,
            "meeting_id": meeting_id,
            "model_bundle_hash": model_bundle["case_id"],
            "usage": [usage],
            "usage_totals": {
                name: int(result["usage"].get(name, 0) or 0)
                for name in (
                    "input_tokens",
                    "cached_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                )
            },
            "model_output": result["output"],
            "evaluation": _date_probe_evaluation(
                app_database, meeting_id, result["output"]["action_class"]
            ),
        }

    sample = sample_runner(executor, model_bundle, spec)
    if sample.get("platform_api_cost_usd") != 0.0:
        raise ValueError("Subscription variant sample reported Platform API cost")
    model_output = sample["output"]
    evaluation_output = restore_simulation_output(
        model_output,
        actual_meeting_id=meeting_id,
        model_to_actual_participant_id=prepared[
            "model_to_actual_participant_id"
        ],
    )
    app = _read_only_connection(app_database)
    try:
        evaluation = evaluate_simulation_output(app, evaluation_output)
    finally:
        app.close()
    return {
        "status": "SUBSCRIPTION_VARIANT_COMPLETED",
        "execution_provider": "codex_subscription",
        "billing_route": "chatgpt_subscription",
        "platform_api_cost_usd": 0.0,
        "model_id": spec["model_id"],
        "variant_id": variant_id,
        "meeting_id": meeting_id,
        "model_bundle_hash": model_bundle["bundle_hash"],
        "participant_identity_exposed_to_model": not bool(
            prepared["model_to_actual_participant_id"]
        ),
        "model_to_actual_participant_id": prepared[
            "model_to_actual_participant_id"
        ],
        "usage": sample.get("usage") or [],
        "usage_totals": sample.get("usage_totals") or {},
        "model_output": model_output,
        "evaluation_output": evaluation_output,
        "evaluation": evaluation,
    }


def _aggregate_variant_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    if not reports:
        return {"case_count": 0, "policy_accuracy": None}
    evaluations = [report["evaluation"] for report in reports]
    if all(item.get("metric_coverage") == "policy_only" for item in evaluations):
        return {
            "case_count": len(evaluations),
            "metric_coverage": "policy_only",
            "policy_accuracy": sum(item["policy_accuracy"] for item in evaluations)
            / len(evaluations),
            "policy_action_mae": sum(item["policy_action_mae"] for item in evaluations)
            / len(evaluations),
            "false_action_on_hold": sum(
                item["false_action_on_hold"] for item in evaluations
            ),
            "dissent_metrics": None,
        }
    aggregate = aggregate_evaluations(evaluations)
    aggregate["metric_coverage"] = "policy_and_dissent"
    return aggregate


def _validated_existing_variant_report(
    path: Path,
    *,
    variant_id: str,
    meeting_id: str,
    model_id: str,
    model_bundle_hash: str,
) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "status": "SUBSCRIPTION_VARIANT_COMPLETED",
        "execution_provider": "codex_subscription",
        "billing_route": "chatgpt_subscription",
        "platform_api_cost_usd": 0.0,
        "variant_id": variant_id,
        "meeting_id": meeting_id,
        "model_id": model_id,
        "model_bundle_hash": model_bundle_hash,
    }
    mismatch = {
        key: {"expected": expected, "actual": report.get(key)}
        for key, expected in required.items()
        if report.get(key) != expected
    }
    if mismatch:
        raise ValueError(f"Existing subscription variant artifact mismatch: {mismatch}")
    return report


def build_variant_bundle_preflight(
    *,
    source_database: Path,
    app_database: Path,
    ablation_spec_path: Path,
    reaction_artifact_path: Path,
    variant_id: str,
    output_directory: Path,
    frozen_manifest: Path = DEFAULT_FROZEN_MANIFEST,
    meeting_ids: list[str] | None = None,
) -> dict[str, Any]:
    output_root = output_directory.resolve()
    if not output_root.is_relative_to(ROOT.resolve()):
        raise ValueError("Subscription variant output must stay inside workspace")
    ablation_spec = load_ablation_spec(ablation_spec_path.resolve())
    variant = next(
        (item for item in ablation_spec["variants"] if item["variant_id"] == variant_id),
        None,
    )
    if variant is None or not str(variant.get("kind", "")).startswith("paid_llm"):
        raise ValueError(f"Unknown subscription LLM variant: {variant_id}")
    reaction_artifact = json.loads(
        reaction_artifact_path.resolve().read_text(encoding="utf-8")
    )
    selected = (
        list(meeting_ids)
        if meeting_ids is not None
        else load_frozen_meeting_ids(frozen_manifest.resolve())
    )
    cases = []
    for meeting_id in selected:
        base_bundle = build_case_bundle(
            source_database,
            app_database,
            meeting_id=meeting_id,
        )
        prepared = prepare_variant_bundle(
            base_bundle,
            variant,
            app_database=app_database,
            reaction_artifact=reaction_artifact,
        )
        bundle_path = output_root / "bundles" / f"{meeting_id}.json"
        _write_new_json(bundle_path, prepared)
        model_bundle = prepared["model_bundle"]
        cases.append(
            {
                "meeting_id": meeting_id,
                "model_bundle_hash": model_bundle.get(
                    "bundle_hash", model_bundle.get("case_id")
                ),
                "bundle_artifact": str(bundle_path.relative_to(ROOT)),
                "bundle_artifact_sha256": _sha256_file(bundle_path),
                "model_bundle_utf8_bytes": len(
                    json.dumps(
                        model_bundle, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                ),
                "participant_count": len(model_bundle.get("participants") or []),
                "persona_evidence_count": len(
                    model_bundle.get("persona_evidence") or []
                ),
                "reaction_profile_count": len(
                    model_bundle.get("reaction_profile_cards") or []
                ),
                "economic_snapshot_count": len(
                    model_bundle.get("economic_snapshot") or []
                ),
                "participant_names_exposed": bool(variant["participant_names"]),
                "anonymity_verified": prepared["anonymity_verified"],
                "explicit_meeting_date_exposed": bool(variant["meeting_date"]),
            }
        )
    report = {
        "schema_version": "subscription_variant_preflight_v2",
        "status": "PREFLIGHT_COMPLETED_NO_MODEL_CALL",
        "execution_provider": "local_deterministic",
        "platform_api_calls": 0,
        "platform_api_cost_usd": 0.0,
        "variant_id": variant_id,
        "case_count": len(cases),
        "source_database_sha256": _sha256_file(source_database.resolve()),
        "app_database_sha256": _sha256_file(app_database.resolve()),
        "ablation_spec_sha256": _sha256_file(ablation_spec_path.resolve()),
        "reaction_artifact_sha256": _sha256_file(reaction_artifact_path.resolve()),
        "frozen_manifest_sha256": _sha256_file(frozen_manifest.resolve()),
        "cases": cases,
    }
    _write_new_json(output_root / "bundle_preflight.json", report)
    return report


def run_variant_subscription_batch(
    executor: Any,
    *,
    source_database: Path,
    app_database: Path,
    model_spec: dict[str, Any],
    ablation_spec_path: Path,
    reaction_artifact_path: Path,
    variant_id: str,
    output_directory: Path,
    frozen_manifest: Path = DEFAULT_FROZEN_MANIFEST,
    max_new_cases: int | None = None,
    case_runner: Callable[..., dict[str, Any]] = run_variant_case,
) -> dict[str, Any]:
    if max_new_cases is not None and max_new_cases <= 0:
        raise ValueError("max_new_cases must be positive")
    output_root = output_directory.resolve()
    if not output_root.is_relative_to(ROOT.resolve()):
        raise ValueError("Subscription variant output must stay inside workspace")
    ablation_spec = load_ablation_spec(ablation_spec_path.resolve())
    variant = next(
        (item for item in ablation_spec["variants"] if item["variant_id"] == variant_id),
        None,
    )
    if variant is None or not str(variant.get("kind", "")).startswith("paid_llm"):
        raise ValueError(f"Unknown subscription LLM variant: {variant_id}")
    reaction_artifact = json.loads(
        reaction_artifact_path.resolve().read_text(encoding="utf-8")
    )
    meeting_ids = load_frozen_meeting_ids(frozen_manifest.resolve())
    status_path = output_root / "batch_status.json"
    reports = []
    cases = []
    new_cases = 0
    source_database_sha256 = _sha256_file(source_database.resolve())
    app_database_sha256 = _sha256_file(app_database.resolve())
    try:
        for ordinal, meeting_id in enumerate(meeting_ids, start=1):
            if max_new_cases is not None and new_cases >= max_new_cases:
                break
            base_bundle = build_case_bundle(
                source_database,
                app_database,
                meeting_id=meeting_id,
            )
            prepared = prepare_variant_bundle(
                base_bundle,
                variant,
                app_database=app_database,
                reaction_artifact=reaction_artifact,
            )
            model_bundle_hash = prepared["model_bundle"].get(
                "bundle_hash", prepared["model_bundle"].get("case_id")
            )
            bundle_path = output_root / "bundles" / f"{meeting_id}.json"
            _write_new_json(bundle_path, prepared)
            run_path = (
                output_root
                / "runs"
                / f"{meeting_id}_{model_spec['model_id'].replace('.', '-')}.json"
            )
            reused = run_path.exists()
            if reused:
                report = _validated_existing_variant_report(
                    run_path,
                    variant_id=variant_id,
                    meeting_id=meeting_id,
                    model_id=model_spec["model_id"],
                    model_bundle_hash=model_bundle_hash,
                )
            else:
                report = case_runner(
                    executor,
                    prepared,
                    model_spec,
                    app_database=app_database,
                )
                _write_new_json(run_path, report)
                new_cases += 1
            reports.append(report)
            cases.append(
                {
                    "ordinal": ordinal,
                    "meeting_id": meeting_id,
                    "model_bundle_hash": model_bundle_hash,
                    "bundle_artifact": str(bundle_path.relative_to(ROOT)),
                    "bundle_artifact_sha256": _sha256_file(bundle_path),
                    "run_artifact": str(run_path.relative_to(ROOT)),
                    "run_artifact_sha256": _sha256_file(run_path),
                    "reused": reused,
                    "evaluation": report["evaluation"],
                }
            )
            _write_status(
                status_path,
                {
                    "schema_version": "subscription_variant_batch_v1",
                    "status": "RUNNING",
                    "updated_at": _utc_now(),
                    "execution_provider": "codex_subscription",
                    "billing_route": "chatgpt_subscription",
                    "platform_api_calls": 0,
                    "platform_api_cost_usd": 0.0,
                    "model_id": model_spec["model_id"],
                    "variant_id": variant_id,
                    "source_database_sha256": source_database_sha256,
                    "app_database_sha256": app_database_sha256,
                    "total_case_count": len(meeting_ids),
                    "completed_case_count": len(cases),
                    "new_case_count_this_run": new_cases,
                    "pending_case_count": len(meeting_ids) - len(cases),
                    "aggregate": _aggregate_variant_reports(reports),
                    "usage": aggregate_usage(reports),
                    "cases": cases,
                },
            )
    except Exception as error:
        failed = {
            "schema_version": "subscription_variant_batch_v1",
            "status": "FAILED_CLOSED",
            "updated_at": _utc_now(),
            "execution_provider": "codex_subscription",
            "billing_route": "chatgpt_subscription",
            "platform_api_calls": 0,
            "platform_api_cost_usd": 0.0,
            "model_id": model_spec["model_id"],
            "variant_id": variant_id,
            "source_database_sha256": source_database_sha256,
            "app_database_sha256": app_database_sha256,
            "total_case_count": len(meeting_ids),
            "completed_case_count": len(cases),
            "new_case_count_this_run": new_cases,
            "pending_case_count": len(meeting_ids) - len(cases),
            "failure_type": type(error).__name__,
            "failure_message": str(error),
            "aggregate": _aggregate_variant_reports(reports),
            "usage": aggregate_usage(reports),
            "cases": cases,
        }
        _write_status(status_path, failed)
        raise
    status = {
        "schema_version": "subscription_variant_batch_v1",
        "status": "COMPLETED" if len(cases) == len(meeting_ids) else "PARTIAL",
        "updated_at": _utc_now(),
        "execution_provider": "codex_subscription",
        "billing_route": "chatgpt_subscription",
        "platform_api_calls": 0,
        "platform_api_cost_usd": 0.0,
        "model_id": model_spec["model_id"],
        "variant_id": variant_id,
        "source_database_sha256": source_database_sha256,
        "app_database_sha256": app_database_sha256,
        "ablation_spec_sha256": _sha256_file(ablation_spec_path.resolve()),
        "reaction_artifact_sha256": _sha256_file(reaction_artifact_path.resolve()),
        "frozen_manifest_sha256": _sha256_file(frozen_manifest.resolve()),
        "total_case_count": len(meeting_ids),
        "completed_case_count": len(cases),
        "new_case_count_this_run": new_cases,
        "pending_case_count": len(meeting_ids) - len(cases),
        "aggregate": _aggregate_variant_reports(reports),
        "usage": aggregate_usage(reports),
        "cases": cases,
    }
    _write_status(status_path, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one frozen R5 LLM variant through ChatGPT subscription auth."
    )
    parser.add_argument("--variant-id", required=True)
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument("--model-spec", type=Path, default=DEFAULT_SPEC_PATH)
    parser.add_argument("--ablation-spec", type=Path, default=DEFAULT_ABLATION_SPEC)
    parser.add_argument(
        "--reaction-artifact", type=Path, default=DEFAULT_REACTION_ARTIFACT
    )
    parser.add_argument("--frozen-manifest", type=Path, default=DEFAULT_FROZEN_MANIFEST)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--max-new-cases", type=int)
    parser.add_argument("--confirmation")
    args = parser.parse_args()
    output_directory = args.output_directory or (
        ROOT / "artifacts" / "codex_subscription" / "r5_variants_v2" / args.variant_id
    )
    if args.preflight_only:
        report = build_variant_bundle_preflight(
            source_database=args.source,
            app_database=args.app,
            ablation_spec_path=args.ablation_spec,
            reaction_artifact_path=args.reaction_artifact,
            variant_id=args.variant_id,
            output_directory=output_directory,
            frozen_manifest=args.frozen_manifest,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.confirmation != SUBSCRIPTION_CONFIRMATION:
        raise ValueError(
            "Subscription variant batch requires --confirmation "
            + SUBSCRIPTION_CONFIRMATION
        )
    executor = CodexSubscriptionExecutor()
    executor.verify_authentication()
    report = run_variant_subscription_batch(
        executor,
        source_database=args.source,
        app_database=args.app,
        model_spec=load_model_spec(args.model_spec),
        ablation_spec_path=args.ablation_spec,
        reaction_artifact_path=args.reaction_artifact,
        variant_id=args.variant_id,
        output_directory=output_directory,
        frozen_manifest=args.frozen_manifest,
        max_new_cases=args.max_new_cases,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
