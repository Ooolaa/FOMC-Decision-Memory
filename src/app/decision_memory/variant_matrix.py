from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from decision_memory.subscription_variant_runner import _aggregate_variant_reports


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SUBSCRIPTION_VARIANTS = [
    "naked_frozen_llm",
    "named_persona_reaction",
    "anonymous_persona_reaction",
    "named_persona_no_reaction",
    "date_only_memorization_probe",
]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise ValueError("Variant matrix output must stay inside workspace")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if resolved.exists():
        if resolved.read_text(encoding="utf-8") != serialized:
            raise FileExistsError(f"Refusing to overwrite variant matrix: {resolved}")
        return
    resolved.write_text(serialized, encoding="utf-8")


def build_variant_matrix(
    deterministic_baseline_path: Path,
    subscription_status_paths: dict[str, Path],
    *,
    output_path: Path,
    required_variant_ids: list[str] = REQUIRED_SUBSCRIPTION_VARIANTS,
) -> dict[str, Any]:
    if set(subscription_status_paths) != set(required_variant_ids):
        raise ValueError("Subscription variant status paths do not match requirements")
    baseline_path = deterministic_baseline_path.resolve()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    meeting_ids = list(baseline["split_manifest"]["meeting_ids"])
    expected_cases = set(meeting_ids)
    if len(expected_cases) != len(meeting_ids):
        raise ValueError("Deterministic baseline split contains duplicate cases")

    rows = []
    deterministic_ids = {
        "majority": "majority_deterministic",
        "persistence": "persistence_deterministic",
        "pooled_reaction": "pooled_reaction_deterministic",
    }
    for source_id, variant_id in deterministic_ids.items():
        metrics = baseline["metrics"][source_id]
        if int(metrics["n"]) != len(meeting_ids):
            raise ValueError(f"Deterministic baseline case count mismatch: {source_id}")
        rows.append(
            {
                "variant_id": variant_id,
                "kind": "deterministic_baseline",
                "metric_coverage": "policy_only",
                "n": int(metrics["n"]),
                "policy_accuracy": float(metrics["accuracy"]),
                "policy_action_mae": float(metrics["mean_absolute_action_error"]),
                "false_action_on_hold": float(
                    metrics["false_action_count_on_hold"]
                ),
                "dissent_base_rate": None,
                "dissent_precision": None,
                "dissent_recall": None,
                "dissent_f1": None,
            }
        )

    sources = {
        "deterministic_baseline": {
            "path": str(baseline_path.relative_to(ROOT.resolve())),
            "sha256": _sha256_file(baseline_path),
        }
    }
    for variant_id in required_variant_ids:
        status_path = subscription_status_paths[variant_id].resolve()
        if not status_path.is_relative_to(ROOT.resolve()):
            raise ValueError("Subscription variant status must stay inside workspace")
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("status") != "COMPLETED":
            raise ValueError(f"Subscription variant is not complete: {variant_id}")
        if status.get("variant_id") != variant_id:
            raise ValueError(f"Subscription variant id mismatch: {variant_id}")
        if int(status.get("platform_api_calls", -1)) != 0 or float(
            status.get("platform_api_cost_usd", -1)
        ) != 0.0:
            raise ValueError(f"Subscription variant reports Platform API use: {variant_id}")
        actual_cases = [item["meeting_id"] for item in status["cases"]]
        if (
            len(actual_cases) != len(meeting_ids)
            or set(actual_cases) != expected_cases
            or int(status["completed_case_count"]) != len(meeting_ids)
        ):
            raise ValueError(f"Subscription variant split mismatch: {variant_id}")
        reports = []
        for item in status["cases"]:
            run_path = (ROOT / item["run_artifact"]).resolve()
            if not run_path.is_relative_to(ROOT.resolve()) or not run_path.is_file():
                raise ValueError(f"Subscription run artifact is missing: {variant_id}")
            if _sha256_file(run_path) != item["run_artifact_sha256"]:
                raise ValueError(f"Subscription run artifact hash mismatch: {variant_id}")
            report = json.loads(run_path.read_text(encoding="utf-8"))
            if (
                report.get("variant_id") != variant_id
                or report.get("meeting_id") != item["meeting_id"]
                or int(report.get("platform_api_calls", 0)) != 0
                or float(report.get("platform_api_cost_usd", -1)) != 0.0
                or report.get("evaluation") != item.get("evaluation")
            ):
                raise ValueError(
                    f"Subscription run artifact conflicts with status: {variant_id}"
                )
            reports.append(report)
        recomputed_aggregate = _aggregate_variant_reports(reports)
        if recomputed_aggregate != status.get("aggregate"):
            raise ValueError(f"Subscription aggregate mismatch: {variant_id}")
        aggregate = status["aggregate"]
        rows.append(
            {
                "variant_id": variant_id,
                "kind": "subscription_llm_probe"
                if variant_id == "date_only_memorization_probe"
                else "subscription_llm",
                "metric_coverage": aggregate["metric_coverage"],
                "n": int(aggregate["case_count"]),
                "policy_accuracy": aggregate.get("policy_accuracy"),
                "policy_action_mae": aggregate.get("policy_action_mae"),
                "false_action_on_hold": aggregate.get("false_action_on_hold"),
                "dissent_base_rate": aggregate.get("dissent_base_rate"),
                "dissent_precision": aggregate.get("dissent_precision"),
                "dissent_recall": aggregate.get("dissent_recall"),
                "dissent_f1": aggregate.get("dissent_f1"),
            }
        )
        sources[variant_id] = {
            "path": str(status_path.relative_to(ROOT.resolve())),
            "sha256": _sha256_file(status_path),
        }

    report = {
        "schema_version": "r5_subscription_variant_matrix_v1",
        "status": "EVALUATION_MATRIX_COMPLETED",
        "case_count": len(meeting_ids),
        "split_manifest_hash": baseline["split_manifest"]["manifest_hash"],
        "platform_api_calls": 0,
        "platform_api_cost_usd": 0.0,
        "rows": rows,
        "sources": sources,
        "disclosures": [
            "Deterministic baselines do not emit individual dissent predictions.",
            "Date-only is a policy-only memorization probe; dissent metrics are not applicable.",
            "All LLM development variants were executed through ChatGPT subscription auth, not Platform API billing.",
        ],
    }
    _write_json(output_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the frozen R5 variant matrix.")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("artifacts/evaluation/frozen_45_policy_baselines_v1.json"),
    )
    parser.add_argument(
        "--variant-root",
        type=Path,
        default=Path("artifacts/codex_subscription/r5_variants_v2"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/evaluation/r5_subscription_variant_matrix_v1.json"),
    )
    args = parser.parse_args()
    statuses = {
        variant_id: args.variant_root / variant_id / "batch_status.json"
        for variant_id in REQUIRED_SUBSCRIPTION_VARIANTS
    }
    report = build_variant_matrix(
        args.baseline,
        statuses,
        output_path=args.output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
