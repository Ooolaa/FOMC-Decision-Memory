from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_FILES = (
    ".gitattributes",
    ".streamlit/config.toml",
    "fred_fomc_real.sqlite",
    "fomc_simulation.sqlite",
    "fomc_simulation.vote_core_candidate.sqlite",
    "fomc_simulation.transcript_segmentation_v3_candidate.sqlite",
    "fomc_simulation.decision_trace_50_display.sqlite",
    "document_manifests/current_45_as_of_2026-08-27_source_a7fd.json",
    "document_manifests/training_2006_2020.json",
    "document_manifests/transcripts_2006_2020_sample50_v3_inline_handoff_no_period.json",
    "outcome_manifests/current_45_rate_delta_v1.json",
    "outcome_manifests/training_2006_2020_rate_delta_v1.json",
    "metric_spec/inflation_transitory_v1.json",
    "metric_spec/rate_only_response_v1.json",
    "model_spec/gpt-5.6-terra-standard-2026-08-27.json",
    "model_spec/gpt-5.6-luna-standard-2026-08-28.json",
    "model_spec/reaction_feature_contract_hackathon_r5_v1.json",
    "evaluation_spec/hackathon_r5_variants_v1.json",
    "evaluation_spec/terra_luna_controlled_3case_v1.json",
    "evaluation_spec/terra_luna_controlled_3case_v1_cap_amendment_1.json",
    "evaluation_spec/terra_luna_votes_only_3case_v1.json",
    "evaluation_spec/terra_luna_votes_only_3case_v1_authorization_1.json",
    "schemas/decision_trace_v1.json",
    "schemas/simulation_output_v1.json",
    "schemas/simulation_stage_envelope_v1.json",
    "fixtures/decision_trace_fomc_2022_03_15.json",
    "fixtures/next_meeting_official_context_2026-09-01.json",
    "decision_memory/profile_cards.py",
    "decision_memory/decision_trace.py",
    "decision_memory/decision_trace_subscription.py",
    "decision_memory/decision_trace_qa.py",
    "decision_memory/materialize_decision_trace_corpus.py",
    "decision_memory/assumption_monitor_audit.py",
    "decision_memory/transcripts.py",
    "decision_memory/human_review_sample.py",
    "decision_memory/human_review_results.py",
    "decision_memory/next_meeting_forecast.py",
    "decision_memory/forecast_ensemble.py",
    "decision_memory/simulation_variants.py",
    "decision_memory/member_evidence.py",
    "decision_memory/official_forecast_context.py",
    "decision_memory/ai_member_explanation.py",
    "decision_memory/submission_gate.py",
    "decision_memory/lag_spec.py",
    "decision_memory/model_preflight.py",
    "decision_memory/llm_sample.py",
    "decision_memory/subscription_variant_runner.py",
    "decision_memory/ui_variant_artifacts.py",
    "decision_memory/variant_matrix.py",
    "decision_memory/votes.py",
    "decision_memory/votes_comparison.py",
    "artifacts/reaction/pooled_ordered_logit_v1.json",
    "artifacts/reaction/fomc_2022_03_15_profile_cards_v1.json",
    "artifacts/evaluation/frozen_45_policy_baselines_v1.json",
    "artifacts/evaluation/frozen_45_vote_baselines_candidate_v1.json",
    "artifacts/evaluation/rate_only_censoring_audit_v1.json",
    "artifacts/evaluation/statement_alert_audit_v1.json",
    "artifacts/evaluation/vote_parser_audit_v1.json",
    "artifacts/evaluation/terra_luna_fomc_2022_03_15_comparison_v1.json",
    "artifacts/evaluation/terra_luna_controlled_3case_v1.json",
    "artifacts/evaluation/terra_luna_votes_only_3case_v1.json",
    "artifacts/evaluation/r5_subscription_variant_matrix_v1.json",
    "artifacts/evaluation/decision_trace_assumption_monitor_audit_v1.json",
    "artifacts/evaluation/source_refresh_2026-09-01_audit.json",
    "artifacts/evaluation/decision_trace_display_materialization_v1.json",
    "artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/bundle_preflight.json",
    "artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/batch_status.json",
    "artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/qa_queue.json",
    "artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/human_review_sample_v1.json",
    "artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/human_review_results_v1.json",
    "artifacts/codex_subscription/r5_variants_v2/date_only_memorization_probe/batch_status.json",
    "artifacts/codex_subscription/r5_variants_v2/naked_frozen_llm/batch_status.json",
    "artifacts/codex_subscription/r5_variants_v2/named_persona_no_reaction/batch_status.json",
    "artifacts/codex_subscription/r5_variants_v2/named_persona_reaction/batch_status.json",
    "artifacts/codex_subscription/r5_variants_v2/anonymous_persona_reaction/batch_status.json",
    "artifacts/cache/fomc_2022_03_15_offline_baseline.json",
    "artifacts/forecast/fomc_2026_09_15_ensemble_v1/ensemble_forecast.json",
    "artifacts/forecast/fomc_2026_09_15_ensemble_v1/bundles/naked_frozen_llm.json",
    "artifacts/forecast/fomc_2026_09_15_ensemble_v1/bundles/named_persona_reaction.json",
    "artifacts/forecast/fomc_2026_09_15_ensemble_v1/bundles/anonymous_persona_reaction.json",
    "artifacts/forecast/fomc_2026_09_15_ensemble_v1/bundles/named_persona_no_reaction.json",
    "artifacts/forecast/fomc_2026_09_15_ensemble_v1/runs/naked_frozen_llm.json",
    "artifacts/forecast/fomc_2026_09_15_ensemble_v1/runs/named_persona_reaction.json",
    "artifacts/forecast/fomc_2026_09_15_ensemble_v1/runs/anonymous_persona_reaction.json",
    "artifacts/forecast/fomc_2026_09_15_ensemble_v1/runs/named_persona_no_reaction.json",
    "artifacts/llm_preflight/fomc_2022_03_15_case_bundle.json",
    "artifacts/llm_preflight/fomc_2022_03_15_case_bundle_paid_sample_20260828_v1.json",
    "artifacts/llm_preflight/fomc_2022_03_15_paid_sample_20260828_v1.json",
    "artifacts/llm_preflight/fomc_2022_03_15_luna_comparison_failure_20260828_v1.json",
    "artifacts/llm_preflight/fomc_2022_03_15_luna_remediation_retest_20260828_v1.json",
    "artifacts/llm_preflight/controlled_3case_20260828_v1/fomc_2022_03_15_bundle.json",
    "artifacts/llm_preflight/controlled_3case_20260828_v1/fomc_2022_03_15_terra.json",
    "artifacts/llm_preflight/controlled_3case_20260828_v1/fomc_2023_09_19_bundle.json",
    "artifacts/llm_preflight/controlled_3case_20260828_v1/fomc_2023_09_19_terra.json",
    "artifacts/llm_preflight/controlled_3case_20260828_v1/fomc_2023_09_19_luna.json",
    "artifacts/llm_preflight/controlled_3case_20260828_v1/fomc_2024_09_17_bundle.json",
    "artifacts/llm_preflight/controlled_3case_20260828_v1/fomc_2024_09_17_terra.json",
    "artifacts/llm_preflight/controlled_3case_20260828_v1/fomc_2024_09_17_luna.json",
    "artifacts/llm_preflight/votes_only_3case_20260828_v1/fomc_2022_03_15_votes_case.json",
    "artifacts/llm_preflight/votes_only_3case_20260828_v1/fomc_2022_03_15_terra.json",
    "artifacts/llm_preflight/votes_only_3case_20260828_v1/fomc_2022_03_15_luna.json",
    "artifacts/llm_preflight/votes_only_3case_20260828_v1/fomc_2023_09_19_votes_case.json",
    "artifacts/llm_preflight/votes_only_3case_20260828_v1/fomc_2023_09_19_terra.json",
    "artifacts/llm_preflight/votes_only_3case_20260828_v1/fomc_2023_09_19_luna.json",
    "artifacts/llm_preflight/votes_only_3case_20260828_v1/fomc_2024_09_17_votes_case.json",
    "artifacts/llm_preflight/votes_only_3case_20260828_v1/fomc_2024_09_17_terra.json",
    "artifacts/llm_preflight/votes_only_3case_20260828_v1/fomc_2024_09_17_luna.json",
    "artifacts/rehearsal/ui_rehearsal_r5_final_v8.json",
    "artifacts/rehearsal/ui_decision_trace_catalog_2026-09-01.json",
    "artifacts/screenshots/next_meeting_forecast.png",
    "artifacts/screenshots/next_meeting_forecast_ai_connected.png",
    "artifacts/screenshots/decision_replay.png",
    "artifacts/screenshots/historical_results.png",
    "app.py",
    "run_app.ps1",
    "scripts/capture_streamlit_ui.py",
    "scripts/run_next_meeting_ensemble.py",
    "docs/plans/2026-08-28-terra-luna-controlled-3case-comparison.md",
    "docs/plans/2026-08-28-terra-luna-votes-only-comparison.md",
    "docs/plans/2026-09-01-decision-trace-display-corpus.md",
    "docs/plans/2026-09-01-fomc-only-interface.md",
    "tests/votes/test_votes_comparison.py",
    "tests/votes/test_votes.py",
    "tests/review/test_human_review_sample.py",
    "tests/review/test_human_review_results.py",
    "tests/audit/test_assumption_monitor_semantics.py",
    "tests/audit/test_assumption_monitor_audit.py",
    "tests/ui/test_capture_streamlit_ui.py",
    "tests/ui/test_app.py",
    "tests/decision_trace/test_materialize_decision_trace_corpus.py",
    "tests/forecast/test_next_meeting_forecast.py",
    "tests/forecast/test_forecast_ensemble.py",
    "tests/forecast/test_official_forecast_context.py",
    "tests/ui/test_ai_member_explanation.py",
    "tests/audit/test_transcript_segmentation_v3_audit.py",
    "tests/forecast/test_reaction_feature_contract.py",
    "tests/audit/test_submission_gate.py",
    "tests/decision_trace/test_subscription_variant_runner.py",
    "tests/evaluation/test_lag_spec.py",
    "tests/ui/test_ui_variant_artifacts.py",
    "tests/evaluation/test_variant_matrix.py",
    "RUNBOOK.md",
    "DEMO_SCRIPT.md",
    "DECISION_TRACE_HUMAN_REVIEW.md",
    "submission_templates/hackathon_r5_submission_signoff_v1.json",
    "submission_templates/hackathon_r5_final_ui_rehearsal_v8.json",
    "submission_templates/decision_trace_human_review_results_v5_atomic_monitor.json",
    "R5_CORRECTION_AUDIT_2026-08-31.md",
    "HACKATHON_SUBMISSION.md",
    "SUBMISSION_CHECKLIST.md",
    "requirements.txt",
)

FINAL_UI_REHEARSAL = "artifacts/rehearsal/ui_rehearsal_r5_final_v8.json"


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _append_reference(
    entries: list[str],
    seen: set[str],
    root: Path,
    value: Any,
    *,
    base: Path | None = None,
) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Final UI rehearsal contains an empty artifact reference")
    resolved_root = root.resolve()
    resolved = ((base or resolved_root) / value).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"Final UI rehearsal reference escapes root: {value}")
    relative = resolved.relative_to(resolved_root).as_posix()
    if relative not in seen:
        entries.append(relative)
        seen.add(relative)
    return resolved


def _expand_final_ui_dependencies(root: Path, files: tuple[str, ...]) -> tuple[str, ...]:
    entries = list(dict.fromkeys(item.replace("\\", "/") for item in files))
    seen = set(entries)
    if FINAL_UI_REHEARSAL not in seen:
        return tuple(entries)
    final_ui_path = root.resolve() / FINAL_UI_REHEARSAL
    if not final_ui_path.is_file():
        return tuple(entries)

    payload = json.loads(final_ui_path.read_text(encoding="utf-8"))
    for mode in payload.get("modes") or []:
        report_path = _append_reference(
            entries,
            seen,
            root,
            mode.get("capture_report"),
        )
        if not report_path.is_file():
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, list):
            raise ValueError(f"Final UI capture report must be a list: {report_path}")
        for item in report:
            if not isinstance(item, dict):
                raise ValueError(f"Final UI capture report item must be an object: {report_path}")
            screenshot_values = []
            if item.get("screenshot") is not None:
                screenshot_values.append(item["screenshot"])
            screenshot_values.extend(item.get("screenshots") or [])
            for screenshot in screenshot_values:
                _append_reference(
                    entries,
                    seen,
                    root,
                    screenshot,
                    base=report_path.parent,
                )

    for item in payload.get("canonical_screenshots") or []:
        if not isinstance(item, dict):
            raise ValueError("Final UI canonical screenshot item must be an object")
        _append_reference(entries, seen, root, item.get("path"))
    return tuple(entries)


def build_artifact_manifest(root: Path, files: tuple[str, ...] = DEFAULT_FILES) -> dict[str, Any]:
    resolved_root = root.resolve()
    files = _expand_final_ui_dependencies(resolved_root, files)
    entries = []
    for relative in files:
        path = resolved_root / relative
        if not path.is_file():
            raise FileNotFoundError(f"Required offline artifact is missing: {path}")
        entries.append(
            {
                "path": relative.replace("\\", "/"),
                "byte_length": path.stat().st_size,
                "sha256": _hash(path),
            }
        )
    payload = {
        "schema_version": 1,
        "build_id": "hackathon_r5_offline_build_2026-09-02_v33",
        "root": ".",
        "root_policy": "workspace_relative_portable",
        "formal_app_database": "fomc_simulation.sqlite",
        "formal_app_database_write_policy": "read_only",
        "display_app_database": "fomc_simulation.decision_trace_50_display.sqlite",
        "display_app_database_write_policy": "read_only",
        "application_scope": "fomc_only",
        "mutable_runtime_file": None,
        "mutable_runtime_disclosure": (
            "The formal and display app databases are immutable during the demo. "
            "The FOMC-only interface does not expose database write actions."
        ),
        "files": entries,
    }
    payload["manifest_hash"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze the R5 offline build hashes.")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/manifests/hackathon_r5_offline_build_2026-09-02_v33.json"),
    )
    args = parser.parse_args()
    payload = build_artifact_manifest(args.root)
    output = args.output.resolve()
    if output.exists():
        existing = json.loads(output.read_text(encoding="utf-8"))
        if existing != payload:
            raise RuntimeError(f"Existing build manifest differs: {output}")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as target:
            json.dump(payload, target, ensure_ascii=False, indent=2, sort_keys=True)
            target.write("\n")
    print(
        json.dumps(
            {
                "output": str(output),
                "file_count": len(payload["files"]),
                "manifest_hash": payload["manifest_hash"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
