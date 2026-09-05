from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_voter_vote_comparison(
    report: dict[str, Any],
    actual_votes: list[dict[str, Any]],
    *,
    reveal_identity: bool,
) -> dict[str, Any]:
    predicted_votes = list((report.get("model_output") or {}).get("votes") or [])
    if not predicted_votes:
        raise ValueError("Model output has no per-voter predictions")
    predicted_ids = [str(item.get("participant_id") or "") for item in predicted_votes]
    if not all(predicted_ids) or len(set(predicted_ids)) != len(predicted_ids):
        raise ValueError("Predicted voter roster contains missing or duplicate IDs")

    actual_by_id: dict[str, dict[str, Any]] = {}
    for item in actual_votes:
        participant_id = str(item.get("participant_id") or "")
        if not participant_id or participant_id in actual_by_id:
            raise ValueError("Actual voter roster contains missing or duplicate IDs")
        actual_by_id[participant_id] = item

    declared_mapping = dict(report.get("model_to_actual_participant_id") or {})
    if declared_mapping:
        if set(declared_mapping) != set(predicted_ids):
            raise ValueError("Anonymous voter roster mapping is incomplete")
        mapping = {str(key): str(value) for key, value in declared_mapping.items()}
    else:
        mapping = {participant_id: participant_id for participant_id in predicted_ids}
    mapped_actual_ids = list(mapping.values())
    if (
        len(set(mapped_actual_ids)) != len(mapped_actual_ids)
        or set(mapped_actual_ids) != set(actual_by_id)
    ):
        raise ValueError("Predicted voter roster does not match known actual roster")

    rows: list[dict[str, Any]] = []
    predicted_dissenters: list[str] = []
    actual_dissenters: list[str] = []
    missed_dissenters: list[str] = []
    false_alarm_dissenters: list[str] = []
    for predicted in predicted_votes:
        model_id = str(predicted["participant_id"])
        actual = actual_by_id[mapping[model_id]]
        predicted_choice = str(predicted.get("choice") or "")
        actual_choice = str(actual.get("voter_choice") or "")
        if predicted_choice not in {"FOR", "AGAINST"} or actual_choice not in {
            "FOR",
            "AGAINST",
        }:
            raise ValueError("Vote choice must be FOR or AGAINST")
        voter = (
            str(actual.get("display_name") or mapping[model_id])
            if reveal_identity
            else model_id
        )
        predicted_against = predicted_choice == "AGAINST"
        actual_against = actual_choice == "AGAINST"
        if predicted_against and actual_against:
            dissent_result = "TRUE_POSITIVE"
        elif predicted_against:
            dissent_result = "FALSE_POSITIVE"
            false_alarm_dissenters.append(voter)
        elif actual_against:
            dissent_result = "FALSE_NEGATIVE"
            missed_dissenters.append(voter)
        else:
            dissent_result = "TRUE_NEGATIVE"
        if predicted_against:
            predicted_dissenters.append(voter)
        if actual_against:
            actual_dissenters.append(voter)
        rows.append(
            {
                "voter": voter,
                "predicted_choice": predicted_choice,
                "actual_choice": actual_choice,
                "correct": predicted_choice == actual_choice,
                "dissent_result": dissent_result,
            }
        )

    return {
        "known_roster_is_input": True,
        "prediction_target": "per_voter_FOR_AGAINST",
        "identity_revealed": reveal_identity,
        "voter_count": len(rows),
        "correct_count": sum(bool(item["correct"]) for item in rows),
        "predicted_dissenters": predicted_dissenters,
        "actual_dissenters": actual_dissenters,
        "missed_dissenters": missed_dissenters,
        "false_alarm_dissenters": false_alarm_dissenters,
        "rows": rows,
    }


def load_completed_variant_case(
    variant_root: Path,
    *,
    variant_id: str,
    meeting_id: str,
) -> dict[str, Any] | None:
    root = variant_root.resolve()
    status_path = root / variant_id / "batch_status.json"
    if not status_path.is_file():
        return None
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("status") != "COMPLETED":
        return None
    if (
        status.get("variant_id") != variant_id
        or int(status.get("platform_api_calls", -1)) != 0
        or float(status.get("platform_api_cost_usd", -1)) != 0.0
    ):
        raise ValueError(f"Completed variant status is invalid: {variant_id}")
    case = next(
        (item for item in status.get("cases", []) if item["meeting_id"] == meeting_id),
        None,
    )
    if case is None:
        raise ValueError(f"Completed variant is missing case: {variant_id} {meeting_id}")
    run_path = (root.parents[2] / case["run_artifact"]).resolve()
    workspace_root = root.parents[2]
    if not run_path.is_relative_to(workspace_root) or not run_path.is_file():
        raise ValueError(f"Completed variant run artifact is missing: {variant_id}")
    if _sha256_file(run_path) != case["run_artifact_sha256"]:
        raise ValueError(f"Completed variant run artifact hash mismatch: {variant_id}")
    report = json.loads(run_path.read_text(encoding="utf-8"))
    if (
        report.get("variant_id") != variant_id
        or report.get("meeting_id") != meeting_id
        or float(report.get("platform_api_cost_usd", -1)) != 0.0
        or report.get("evaluation") != case.get("evaluation")
    ):
        raise ValueError(f"Completed variant run conflicts with status: {variant_id}")
    return {
        "status_path": status_path,
        "status": status,
        "case": case,
        "report": report,
    }


def load_completed_variant_matrix(
    matrix_path: Path,
    *,
    workspace_root: Path,
) -> dict[str, Any] | None:
    root = workspace_root.resolve()
    resolved_matrix = matrix_path.resolve()
    if not resolved_matrix.is_relative_to(root):
        raise ValueError("Variant matrix must stay inside workspace")
    if not resolved_matrix.is_file():
        return None
    matrix = json.loads(resolved_matrix.read_text(encoding="utf-8"))
    if matrix.get("status") != "EVALUATION_MATRIX_COMPLETED":
        return None
    if (
        int(matrix.get("platform_api_calls", -1)) != 0
        or float(matrix.get("platform_api_cost_usd", -1)) != 0.0
        or not matrix.get("rows")
    ):
        raise ValueError("Completed variant matrix is invalid")
    for source in matrix.get("sources", {}).values():
        source_path = (root / source["path"]).resolve()
        if not source_path.is_relative_to(root) or not source_path.is_file():
            raise ValueError("Completed variant matrix source is missing")
        if _sha256_file(source_path) != source["sha256"]:
            raise ValueError("Completed variant matrix source hash mismatch")
        source_payload = json.loads(source_path.read_text(encoding="utf-8"))
        for case in source_payload.get("cases", []):
            if "run_artifact" not in case:
                continue
            run_path = (root / case["run_artifact"]).resolve()
            if not run_path.is_relative_to(root) or not run_path.is_file():
                raise ValueError("Completed variant matrix run is missing")
            if _sha256_file(run_path) != case["run_artifact_sha256"]:
                raise ValueError("Completed variant matrix run hash mismatch")
    return matrix
