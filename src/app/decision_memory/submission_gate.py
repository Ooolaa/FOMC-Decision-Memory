from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from decision_memory.artifact_manifest import build_artifact_manifest
from decision_memory.human_review_results import validate_human_review_results
from decision_memory.human_review_sample import build_human_review_sample
from decision_memory.ui_variant_artifacts import load_completed_variant_matrix
from decision_memory.variant_matrix import REQUIRED_SUBSCRIPTION_VARIANTS


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATABASE_SHA256 = (
    "02f96292422ece4556e952902a4660c663652d9eaff8b470e75eec3dc7c91187"
)
FORMAL_APP_DATABASE_SHA256 = (
    "83ef409125bea85f9463f2c1bf2c7a9accb46414d6e7268262b53c93a1c9732c"
)
TRANSCRIPT_V3_CANDIDATE_DATABASE_SHA256 = (
    "9be4bcf672b2f1dcf53f31a8fc985fb1acc02e9ed55a0de505bf1d82c7ebbcb3"
)
DISPLAY_APP_DATABASE_SHA256 = (
    "d60364029cafb6e79dc8b3e6a902b06016970620b827752f09f1d3df54139186"
)
EXPECTED_CASE_COUNT = 45
DECISION_TRACE_LINEAGE = (
    "artifacts/codex_subscription/"
    "decision_trace_50_v5_atomic_monitor_segmentation_v3"
)
DECISION_TRACE_EXTRACTOR_VERSION = (
    "codex-subscription-decision-trace-v5-atomic-monitor"
)
ASSUMPTION_MONITOR_CONTRACT_VERSION = "atomic_one_clause_monitor_v1"
REQUIRED_UI_MODES = {
    "normal_browser_path",
    "process_without_openai_api_key",
    "stop_and_restart",
}
REQUIRED_VIEW_IDS = {
    "next_meeting_forecast",
    "decision_replay",
    "historical_results",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_file(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"{label} must be a file under root")
    return resolved


def _timezone_timestamp(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")


def _database_audit(path: Path, expected_sha256: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"Database is missing: {path}")
    actual_sha256 = _sha256_file(path)
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
    return {
        "sha256": actual_sha256,
        "expected_sha256": expected_sha256,
        "integrity_check": integrity,
        "foreign_key_violation_count": len(foreign_keys),
        "valid": actual_sha256 == expected_sha256
        and integrity == "ok"
        and not foreign_keys,
    }


def _candidate_vote_label_audit(path: Path) -> dict[str, Any]:
    audit = _database_audit(path, TRANSCRIPT_V3_CANDIDATE_DATABASE_SHA256)
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        labeled_meeting_count, vote_row_count, dissent_row_count = connection.execute(
            """
            SELECT COUNT(DISTINCT meeting_id), COUNT(*), COALESCE(SUM(dissent), 0)
            FROM participant_vote
            """
        ).fetchone()
        roster_mismatch_meeting_count = connection.execute(
            """
            WITH labeled_meetings AS (
                SELECT DISTINCT meeting_id FROM participant_vote
            ), mismatches AS (
                SELECT mp.meeting_id, mp.participant_id
                FROM meeting_participant AS mp
                JOIN labeled_meetings AS lm ON lm.meeting_id = mp.meeting_id
                WHERE mp.is_voter = 1
                EXCEPT
                SELECT meeting_id, participant_id FROM participant_vote
                UNION
                SELECT meeting_id, participant_id FROM participant_vote
                EXCEPT
                SELECT mp.meeting_id, mp.participant_id
                FROM meeting_participant AS mp
                JOIN labeled_meetings AS lm ON lm.meeting_id = mp.meeting_id
                WHERE mp.is_voter = 1
            )
            SELECT COUNT(DISTINCT meeting_id) FROM mismatches
            """
        ).fetchone()[0]
    finally:
        connection.close()
    audit.update(
        {
            "labeled_meeting_count": int(labeled_meeting_count),
            "vote_row_count": int(vote_row_count),
            "dissent_row_count": int(dissent_row_count),
            "roster_mismatch_meeting_count": int(roster_mismatch_meeting_count),
        }
    )
    audit["valid"] = bool(
        audit["valid"]
        and audit["labeled_meeting_count"] == 166
        and audit["vote_row_count"] == 1736
        and audit["dissent_row_count"] == 103
        and audit["roster_mismatch_meeting_count"] == 0
    )
    return audit


def _view_id(item: dict[str, Any]) -> str:
    return {
        "下次會議預測": "next_meeting_forecast",
        "決策重播": "decision_replay",
        "歷史測試結果": "historical_results",
        "Decision Replay": "decision_replay",
        "Historical Test Results": "historical_results",
    }.get(str(item.get("page")), "")


def validate_final_ui_rehearsal(
    rehearsal_path: Path,
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    root = root.resolve()
    rehearsal_path = _workspace_file(rehearsal_path, root, "Final UI rehearsal")
    payload = json.loads(rehearsal_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "hackathon_r5_final_ui_rehearsal_v8":
        raise ValueError("Unsupported final UI rehearsal schema")
    if payload.get("status") != "PASS":
        raise ValueError("Final UI rehearsal status must be PASS")
    _timezone_timestamp(payload.get("executed_at"), "Final UI rehearsal executed_at")

    app_path = _workspace_file(root / "app.py", root, "App")
    launcher_path = _workspace_file(root / "run_app.ps1", root, "Launcher")
    matrix_path = _workspace_file(
        root / str(payload.get("matrix_path") or ""), root, "Variant matrix"
    )
    for label, expected, path in (
        ("app", payload.get("app_sha256"), app_path),
        ("launcher", payload.get("launcher_sha256"), launcher_path),
        ("matrix", payload.get("matrix_sha256"), matrix_path),
    ):
        if expected != _sha256_file(path):
            raise ValueError(f"Final UI rehearsal {label} SHA-256 mismatch")

    browser = payload.get("browser") or {}
    if (
        browser.get("family") != "Microsoft Edge"
        or not browser.get("version")
        or browser.get("viewport") != "1440x1100"
    ):
        raise ValueError("Final UI rehearsal browser contract is invalid")

    network_binding = payload.get("network_binding") or {}
    if (
        network_binding.get("address") != "127.0.0.1"
        or network_binding.get("wildcard_listener_absent") is not True
        or network_binding.get("health_probe") != "status=200 body=ok"
    ):
        raise ValueError("Final UI rehearsal network-binding contract is invalid")

    modes = list(payload.get("modes") or [])
    mode_names = [item.get("name") for item in modes]
    if len(modes) != 3 or set(mode_names) != REQUIRED_UI_MODES:
        raise ValueError("Final UI rehearsal must contain the three required modes")
    signatures: dict[str, dict[str, tuple[str, str]]] = {}
    for mode in modes:
        mode_name = str(mode["name"])
        if mode.get("result") != "PASS" or mode.get("health_probe") != "status=200 body=ok":
            raise ValueError(f"Final UI rehearsal mode did not pass: {mode_name}")
        if (
            mode_name == "process_without_openai_api_key"
            and mode.get("api_key_removed_from_child_process") is not True
        ):
            raise ValueError("No-key rehearsal did not remove the child-process API key")
        if (
            mode_name == "stop_and_restart"
            and mode.get("stopped_probe_failed") is not True
        ):
            raise ValueError("Restart rehearsal did not prove the stopped probe failed")
        report_path = _workspace_file(
            root / str(mode.get("capture_report") or ""),
            root,
            f"Capture report for {mode_name}",
        )
        if mode.get("capture_report_sha256") != _sha256_file(report_path):
            raise ValueError(f"Capture report SHA-256 mismatch: {mode_name}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, list) or len(report) != len(REQUIRED_VIEW_IDS):
            raise ValueError(
                f"Capture report must contain {len(REQUIRED_VIEW_IDS)} views: {mode_name}"
            )
        mode_signature: dict[str, tuple[str, str]] = {}
        for item in report:
            view_id = _view_id(item)
            if view_id not in REQUIRED_VIEW_IDS or view_id in mode_signature:
                raise ValueError(f"Capture report view set is invalid: {mode_name}")
            body_hash = item.get("body_text_sha256")
            if not isinstance(body_hash, str) or len(body_hash) != 64:
                raise ValueError(f"Body-text SHA-256 is invalid: {mode_name} {view_id}")
            screenshot_path = _workspace_file(
                report_path.parent / str(item.get("screenshot") or ""),
                root,
                f"Screenshot for {mode_name} {view_id}",
            )
            mode_signature[view_id] = (body_hash, _sha256_file(screenshot_path))
        if set(mode_signature) != REQUIRED_VIEW_IDS:
            raise ValueError(f"Capture report is missing a required view: {mode_name}")
        signatures[mode_name] = mode_signature

    normal = signatures["normal_browser_path"]
    for mode_name, signature in signatures.items():
        if {key: value[0] for key, value in signature.items()} != {
            key: value[0] for key, value in normal.items()
        }:
            raise ValueError(f"Final UI body-text equivalence failed: {mode_name}")
        if {key: value[1] for key, value in signature.items()} != {
            key: value[1] for key, value in normal.items()
        }:
            raise ValueError(f"Final UI screenshot equivalence failed: {mode_name}")

    canonical = list(payload.get("canonical_screenshots") or [])
    if (
        len(canonical) != len(REQUIRED_VIEW_IDS)
        or {item.get("view_id") for item in canonical} != REQUIRED_VIEW_IDS
    ):
        raise ValueError("Canonical screenshot set is invalid")
    for item in canonical:
        view_id = str(item["view_id"])
        screenshot_path = _workspace_file(
            root / str(item.get("path") or ""), root, f"Canonical screenshot {view_id}"
        )
        actual_hash = _sha256_file(screenshot_path)
        if item.get("sha256") != actual_hash or actual_hash != normal[view_id][1]:
            raise ValueError(f"Canonical screenshot does not match normal mode: {view_id}")

    equivalence = payload.get("equivalence") or {}
    if (
        equivalence.get("body_text_sha256_equal_for_all_required_views") is not True
        or equivalence.get("screenshot_sha256_equal_for_all_required_views") is not True
    ):
        raise ValueError("Final UI equivalence disclosure is incomplete")

    return {
        "status": "PASS",
        "rehearsal_sha256": _sha256_file(rehearsal_path),
        "mode_count": len(modes),
        "view_count": len(REQUIRED_VIEW_IDS),
        "matrix_sha256": _sha256_file(matrix_path),
        "app_sha256": _sha256_file(app_path),
        "launcher_sha256": _sha256_file(launcher_path),
    }


def validate_submission_signoff(
    signoff_path: Path,
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    root = root.resolve()
    signoff_path = _workspace_file(signoff_path, root, "Submission signoff")
    payload = json.loads(signoff_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "hackathon_r5_submission_signoff_v1":
        raise ValueError("Unsupported submission signoff schema")
    presenter = payload.get("presenter")
    if not isinstance(presenter, str) or not presenter.strip():
        raise ValueError("Presenter is required")

    video = payload.get("video") or {}
    video_path = _workspace_file(root / str(video.get("path") or ""), root, "Video")
    if video.get("sha256") != _sha256_file(video_path):
        raise ValueError("Video SHA-256 mismatch")
    if video.get("watched_end_to_end_with_audio") is not True:
        raise ValueError("Video must be watched end to end with audio")

    rehearsals = list(payload.get("rehearsals") or [])
    if len(rehearsals) != 3 or {item.get("run") for item in rehearsals} != {1, 2, 3}:
        raise ValueError("Exactly three numbered rehearsals are required")
    durations = []
    for item in rehearsals:
        duration = item.get("duration_seconds")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            raise ValueError("Rehearsal duration must be numeric")
        if duration <= 0 or duration > 240:
            raise ValueError("Every rehearsal must finish within 240 seconds")
        durations.append(float(duration))
        _timezone_timestamp(item.get("occurred_at"), "Rehearsal occurred_at")

    submission = payload.get("submission") or {}
    _workspace_file(
        root / str(submission.get("archive_path") or ""),
        root,
        "Submission archive",
    )
    confirmation = submission.get("confirmation_id")
    if not isinstance(confirmation, str) or not confirmation.strip():
        raise ValueError("Submission confirmation ID is required")

    review = payload.get("second_person_review") or {}
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("Second-person reviewer is required")
    if reviewer.strip().casefold() == presenter.strip().casefold():
        raise ValueError("Second-person reviewer must differ from the presenter")
    _timezone_timestamp(review.get("signed_at"), "Second-person signed_at")
    required_flags = (
        "synthetic_labels_visible",
        "no_secrets_or_private_data_visible",
        "download_access_tested",
        "contact_information_checked",
    )
    missing = [flag for flag in required_flags if review.get(flag) is not True]
    if missing:
        raise ValueError(f"Second-person checks are incomplete: {missing}")

    return {
        "status": "PASS",
        "signoff_sha256": _sha256_file(signoff_path),
        "video_sha256": _sha256_file(video_path),
        "rehearsal_count": len(rehearsals),
        "maximum_rehearsal_seconds": max(durations),
        "second_person_reviewer": reviewer.strip(),
        "submission_confirmation_id": confirmation.strip(),
    }


def evaluate_submission_gate(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    checks: list[dict[str, Any]] = []

    def add(check_id: str, passed: bool, details: str) -> None:
        checks.append(
            {
                "check_id": check_id,
                "status": "PASS" if passed else "BLOCKED",
                "details": details,
            }
        )

    for check_id, relative, expected in (
        ("source_database", "fred_fomc_real.sqlite", SOURCE_DATABASE_SHA256),
        (
            "formal_app_database",
            "fomc_simulation.sqlite",
            FORMAL_APP_DATABASE_SHA256,
        ),
        (
            "display_app_database",
            "fomc_simulation.decision_trace_50_display.sqlite",
            DISPLAY_APP_DATABASE_SHA256,
        ),
    ):
        try:
            audit = _database_audit(root / relative, expected)
            add(check_id, audit["valid"], json.dumps(audit, sort_keys=True))
        except Exception as error:
            add(check_id, False, str(error))

    try:
        candidate_vote_audit = _candidate_vote_label_audit(
            root / "fomc_simulation.transcript_segmentation_v3_candidate.sqlite"
        )
        add(
            "candidate_vote_labels",
            candidate_vote_audit["valid"],
            json.dumps(candidate_vote_audit, sort_keys=True),
        )
    except Exception as error:
        add("candidate_vote_labels", False, str(error))

    try:
        contract_path = root / "model_spec/reaction_feature_contract_hackathon_r5_v1.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        model_path = root / contract["reaction_model"]["artifact"]
        valid_contract = (
            contract.get("decision_status") == "APPROVED"
            and contract.get("scope") == "hackathon_r5"
            and contract.get("approved_proxy_series_id") == "BAA10Y"
            and contract.get("disclosure_required") is True
            and _sha256_file(model_path)
            == contract["reaction_model"]["artifact_sha256"]
        )
        add(
            "reaction_feature_contract",
            valid_contract,
            f"contract={contract.get('contract_id')} proxy={contract.get('approved_proxy_series_id')}",
        )
    except Exception as error:
        add("reaction_feature_contract", False, str(error))

    try:
        trace_path = root / DECISION_TRACE_LINEAGE / "batch_status.json"
        qa_path = trace_path.parent / "qa_queue.json"
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        case_contract_versions = {
            (item.get("semantic_validation") or {}).get(
                "assumption_monitor_contract_version"
            )
            for item in trace.get("cases") or []
        }
        valid_trace = (
            trace.get("status") == "COMPLETED"
            and int(trace.get("completed_case_count", -1)) == 50
            and trace.get("extractor_version") == DECISION_TRACE_EXTRACTOR_VERSION
            and case_contract_versions == {ASSUMPTION_MONITOR_CONTRACT_VERSION}
            and qa.get("status") == "PENDING_HUMAN_REVIEW"
            and int(qa.get("case_count", -1)) == 50
            and int(qa.get("deterministically_revalidated_case_count", -1)) == 50
            and qa.get("source_batch_status_sha256") == _sha256_file(trace_path)
        )
        add(
            "decision_trace_corpus",
            valid_trace,
            (
                f"lineage={DECISION_TRACE_LINEAGE} "
                f"batch={trace.get('completed_case_count')}/50 "
                f"qa={qa.get('deterministically_revalidated_case_count')}/50 "
                f"extractor={trace.get('extractor_version')} "
                f"monitor_contracts={sorted(case_contract_versions, key=str)}"
            ),
        )
    except Exception as error:
        add("decision_trace_corpus", False, str(error))

    sample_path = root / DECISION_TRACE_LINEAGE / "human_review_sample_v1.json"
    try:
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        recomputed = build_human_review_sample(
            trace_path.parent / "qa_queue.json", target_count=12, root=root
        )
        add(
            "human_review_sample",
            sample == recomputed,
            f"sample={sample.get('case_count')}/12 hash={sample.get('sample_manifest_hash')}",
        )
    except Exception as error:
        add("human_review_sample", False, str(error))

    review_results = root / DECISION_TRACE_LINEAGE / "human_review_results_v1.json"
    if not review_results.is_file():
        add("human_review_results", False, "v5 human review results file is missing")
    else:
        try:
            audit = validate_human_review_results(
                sample_path, review_results, root=root
            )
            add(
                "human_review_results",
                audit["formal_import_gate"] == "PASS",
                json.dumps(audit, sort_keys=True),
            )
        except Exception as error:
            add("human_review_results", False, str(error))

    variant_details = []
    variants_valid = True
    variant_root = root / "artifacts/codex_subscription/r5_variants_v2"
    for variant_id in REQUIRED_SUBSCRIPTION_VARIANTS:
        try:
            status = json.loads(
                (variant_root / variant_id / "batch_status.json").read_text(
                    encoding="utf-8"
                )
            )
            count = int(status.get("completed_case_count", -1))
            valid = (
                status.get("status") == "COMPLETED"
                and count == EXPECTED_CASE_COUNT
                and len(status.get("cases") or []) == EXPECTED_CASE_COUNT
                and int(status.get("platform_api_calls", -1)) == 0
                and float(status.get("platform_api_cost_usd", -1)) == 0.0
            )
            variants_valid = variants_valid and valid
            variant_details.append(f"{variant_id}={count}/{EXPECTED_CASE_COUNT}")
        except Exception as error:
            variants_valid = False
            variant_details.append(f"{variant_id}=ERROR:{error}")
    add("subscription_variants", variants_valid, "; ".join(variant_details))

    matrix_path = root / "artifacts/evaluation/r5_subscription_variant_matrix_v1.json"
    try:
        matrix = load_completed_variant_matrix(matrix_path, workspace_root=root)
        add(
            "variant_matrix",
            matrix is not None and len(matrix.get("rows") or []) == 8,
            "matrix missing" if matrix is None else f"rows={len(matrix['rows'])}",
        )
    except Exception as error:
        add("variant_matrix", False, str(error))

    final_rehearsal = root / "artifacts/rehearsal/ui_rehearsal_r5_final_v8.json"
    if not final_rehearsal.is_file():
        add("final_ui_rehearsal", False, "final current-code UI rehearsal is missing")
    else:
        try:
            audit = validate_final_ui_rehearsal(final_rehearsal, root=root)
            add("final_ui_rehearsal", True, json.dumps(audit, sort_keys=True))
        except Exception as error:
            add("final_ui_rehearsal", False, str(error))

    manifest_path = root / (
        "artifacts/manifests/hackathon_r5_offline_build_2026-09-02_v33.json"
    )
    if not manifest_path.is_file():
        add("v33_manifest", False, "v33 manifest is missing")
    else:
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected = build_artifact_manifest(root)
            add(
                "v33_manifest",
                existing == expected,
                f"manifest_hash={existing.get('manifest_hash')}",
            )
        except Exception as error:
            add("v33_manifest", False, str(error))

    signoff_path = root / "artifacts/submission/submission_signoff_v1.json"
    if not signoff_path.is_file():
        add("submission_signoff", False, "submission signoff is missing")
    else:
        try:
            audit = validate_submission_signoff(signoff_path, root=root)
            add("submission_signoff", True, json.dumps(audit, sort_keys=True))
        except Exception as error:
            add("submission_signoff", False, str(error))

    blockers = [item["check_id"] for item in checks if item["status"] != "PASS"]
    return {
        "schema_version": "hackathon_r5_submission_gate_v1",
        "status": "READY" if not blockers else "BLOCKED",
        "check_count": len(checks),
        "pass_count": len(checks) - len(blockers),
        "blocker_count": len(blockers),
        "blockers": blockers,
        "checks": checks,
    }


def evaluate_technical_readiness(root: Path = ROOT) -> dict[str, Any]:
    """Evaluate the R5 build without real-world submission administration."""
    submission_report = evaluate_submission_gate(root)
    checks = [
        item
        for item in submission_report["checks"]
        if item["check_id"] != "submission_signoff"
    ]
    blockers = [item["check_id"] for item in checks if item["status"] != "PASS"]
    return {
        "schema_version": "hackathon_r5_technical_gate_v1",
        "status": "READY" if not blockers else "BLOCKED",
        "check_count": len(checks),
        "pass_count": len(checks) - len(blockers),
        "blocker_count": len(blockers),
        "blockers": blockers,
        "excluded_submission_checks": ["submission_signoff"],
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the Hackathon R5 technical or submission gate."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--scope",
        choices=("submission", "technical"),
        default="submission",
        help="technical excludes only the real-world submission sign-off check",
    )
    args = parser.parse_args()
    report = (
        evaluate_technical_readiness(args.root)
        if args.scope == "technical"
        else evaluate_submission_gate(args.root)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
