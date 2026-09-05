from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from decision_memory.codex_subscription import (
    CodexSubscriptionExecutor,
    run_subscription_sample,
)
from decision_memory.fed_documents import extract_html_paragraphs
from decision_memory.model_preflight import load_model_spec
from decision_memory.simulation_variants import (
    prepare_prebuilt_variant_bundle,
    restore_simulation_output,
)


ACTION_CLASSES = {"CUT", "HOLD", "HIKE"}
VOTE_CLASSES = {"FOR", "AGAINST"}
FORWARD_VARIANTS = (
    "naked_frozen_llm",
    "named_persona_reaction",
    "anonymous_persona_reaction",
    "named_persona_no_reaction",
)
MODEL_LABELS_ZH = {
    "naked_frozen_llm": "匿名總體資料模擬",
    "named_persona_reaction": "具名委員與歷史反應",
    "anonymous_persona_reaction": "匿名委員與歷史反應",
    "named_persona_no_reaction": "具名委員但不使用歷史反應",
}
FORWARD_CONFIRMATION = "RUN_NEXT_MEETING_SUBSCRIPTION_ENSEMBLE"


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    serialized = json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != serialized:
            raise FileExistsError(f"Refusing to overwrite different artifact: {path}")
        return
    path.write_text(serialized, encoding="utf-8")


def combine_forward_predictions(
    predictions: list[dict[str, Any]],
    *,
    roster_participant_ids: list[str],
    fallback_action: str,
    minimum_policy_support: int = 3,
    minimum_against_support: int = 2,
) -> dict[str, Any]:
    if not predictions:
        raise ValueError("At least one forward prediction is required")
    if len(set(roster_participant_ids)) != len(roster_participant_ids):
        raise ValueError("Roster participant identifiers must be unique")
    if fallback_action not in ACTION_CLASSES:
        raise ValueError(f"Unsupported fallback action: {fallback_action}")
    if minimum_policy_support <= 0 or minimum_policy_support > len(predictions):
        raise ValueError("Invalid minimum policy support")
    if minimum_against_support <= 0 or minimum_against_support > len(predictions):
        raise ValueError("Invalid minimum against support")

    roster = set(roster_participant_ids)
    model_keys = []
    for prediction in predictions:
        model_key = str(prediction["model_key"])
        if model_key in model_keys:
            raise ValueError(f"Duplicate model key: {model_key}")
        model_keys.append(model_key)
        if prediction["policy_action"] not in ACTION_CLASSES:
            raise ValueError(
                f"Unsupported policy action for {model_key}: "
                f"{prediction['policy_action']}"
            )
        votes = prediction.get("votes")
        if not isinstance(votes, dict) or set(votes) != roster:
            raise ValueError(f"Model participant coverage mismatch: {model_key}")
        invalid_votes = set(votes.values()) - VOTE_CLASSES
        if invalid_votes:
            raise ValueError(
                f"Unsupported vote values for {model_key}: {sorted(invalid_votes)}"
            )

    policy_counts = Counter(
        str(prediction["policy_action"]) for prediction in predictions
    )
    leading_action, support_count = sorted(
        policy_counts.items(), key=lambda item: (-item[1], item[0])
    )[0]
    consensus_reached = support_count >= minimum_policy_support
    action_class = leading_action if consensus_reached else fallback_action

    vote_rows = []
    for participant_id in roster_participant_ids:
        against_support = sum(
            prediction["votes"][participant_id] == "AGAINST"
            for prediction in predictions
        )
        vote_rows.append(
            {
                "participant_id": participant_id,
                "predicted_vote": (
                    "AGAINST"
                    if against_support >= minimum_against_support
                    else "FOR"
                ),
                "against_support_count": against_support,
                "model_count": len(predictions),
            }
        )

    return {
        "policy": {
            "action_class": action_class,
            "leading_action": leading_action,
            "support_count": support_count,
            "model_count": len(predictions),
            "minimum_support": minimum_policy_support,
            "consensus_reached": consensus_reached,
            "fallback_used": not consensus_reached,
            "fallback_action": fallback_action,
            "counts": {
                action: policy_counts.get(action, 0)
                for action in ("CUT", "HOLD", "HIKE")
            },
        },
        "votes": vote_rows,
        "minimum_against_support": minimum_against_support,
        "model_keys": model_keys,
    }


def _recent_documents(
    app_database: Path,
    *,
    as_of_date: str,
    workspace_root: Path,
    count: int = 5,
) -> list[dict[str, Any]]:
    app = sqlite3.connect(
        f"file:{app_database.resolve().as_posix()}?mode=ro", uri=True
    )
    app.row_factory = sqlite3.Row
    try:
        rows = app.execute(
            """
            SELECT document_id, meeting_id, document_type, publication_at,
                   source_locator, content_hash
            FROM document_source
            WHERE substr(publication_at, 1, 10) <= ?
              AND document_type IN ('statement', 'minutes')
            ORDER BY publication_at DESC, document_type
            LIMIT ?
            """,
            (as_of_date, count),
        ).fetchall()
    finally:
        app.close()
    if len(rows) != count:
        raise RuntimeError(
            f"Expected {count} pre-cutoff documents, got {len(rows)}"
        )

    documents = []
    for row in rows:
        locator = json.loads(row["source_locator"])
        local_path = Path(locator["local_path"])
        if not local_path.is_absolute():
            local_path = workspace_root / local_path
        if _sha256_file(local_path) != row["content_hash"]:
            raise RuntimeError(f"Document hash mismatch: {local_path}")
        text = "\n".join(extract_html_paragraphs(local_path.read_bytes()))
        documents.append(
            {
                "document_id": str(row["document_id"]),
                "meeting_id": str(row["meeting_id"]),
                "document_type": str(row["document_type"]),
                "publication_at": str(row["publication_at"]),
                "content_hash": str(row["content_hash"]),
                "source_url": str(locator["source_url"]),
                "text": text,
            }
        )
    return documents


def build_forward_case_bundle(
    forecast: dict[str, Any],
    *,
    source_database: Path,
    app_database: Path,
    reaction_artifact: dict[str, Any],
    workspace_root: Path,
) -> dict[str, Any]:
    voter_rows = forecast["voter_forecast"]["rows"]
    participants = [
        {
            "participant_id": str(row["participant_id"]),
            "display_name": str(row["display_name"]),
            "is_chair": row["role"] == "chair",
        }
        for row in voter_rows
    ]
    if sum(item["is_chair"] for item in participants) != 1:
        raise ValueError("Forward bundle requires exactly one Chair")

    persona_evidence = []
    reaction_cards = []
    coefficients = {
        str(name): float(value)
        for name, value in reaction_artifact["coefficients"].items()
    }
    for row in voter_rows:
        participant_id = str(row["participant_id"])
        for item in row.get("important_communications") or []:
            text = str(item.get("excerpt") or "").strip()
            if not text:
                continue
            persona_evidence.append(
                {
                    "evidence_kind": "public_communication",
                    "participant_id": participant_id,
                    "document_id": str(item["document_id"]),
                    "source_meeting_id": None,
                    "publication_at": str(item["publication_date"]),
                    "locator": "public communication",
                    "title": str(item["title"]),
                    "text": text,
                    "content_hash": hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                    "policy_relevance_score": int(item["importance_score"]),
                }
            )
        vote_history = list(row.get("vote_history") or [])
        recent_three = vote_history[:3]
        reaction_cards.append(
            {
                "participant_id": participant_id,
                "display_name": str(row["display_name"]),
                "role": str(row["role"]),
                "is_voter": True,
                "is_chair": row["role"] == "chair",
                "prior_vote_count": int(row["prior_vote_count"]),
                "prior_dissent_count": int(row["prior_dissent_count"]),
                "prior_dissent_rate": float(row["prior_dissent_rate"]),
                "previous_vote_against": (
                    bool(vote_history[0]["dissent"]) if vote_history else None
                ),
                "recent_3_dissent_rate": (
                    sum(bool(item["dissent"]) for item in recent_three)
                    / len(recent_three)
                    if recent_three
                    else None
                ),
                "recent_vote_history": vote_history[:8],
                "vote_history_as_of": forecast["forecast_as_of"],
                "vote_history_publication_checked": True,
                "macro_coefficients": coefficients,
                "coefficient_scope": "pooled_across_training_meetings",
                "individual_model_estimated": False,
            }
        )

    policy_context = forecast["policy_context"]
    bundle = {
        "schema_version": "forward_llm_case_bundle_v1",
        "meeting_id": str(forecast["meeting_id"]),
        "meeting_start_date": str(forecast["meeting_start_date"]),
        "meeting_end_date": str(forecast["meeting_end_date"]),
        "information_cutoff_date_et": str(forecast["forecast_as_of"]),
        "synthetic_output_required": True,
        "label_exclusion": (
            "The future meeting outcome and votes do not exist and are not model input. "
            "Use only information available by the supplied cutoff."
        ),
        "source_database_sha256": _sha256_file(source_database),
        "participants": participants,
        "policy_rate_context": [
            {
                "record_kind": "CURRENT",
                "cutoff_date": forecast["forecast_as_of"],
                "effective_date": policy_context["effective_date"],
                "lower_rate": policy_context["lower_rate"],
                "upper_rate": policy_context["upper_rate"],
                "midpoint": policy_context["midpoint"],
            }
        ],
        "economic_snapshot": [
            {
                "series_id": item["series_id"],
                "observation_date": item["observation_date"],
                "realtime_start": item["visible_version_date"],
                "value_num": item["value"],
            }
            for item in forecast["feature_evidence"]
        ],
        "derived_features": forecast["features"],
        "documents": _recent_documents(
            app_database,
            as_of_date=forecast["forecast_as_of"],
            workspace_root=workspace_root,
        ),
        "persona_evidence": persona_evidence,
        "reaction_profile_cards": reaction_cards,
    }
    bundle["bundle_hash"] = _canonical_hash(bundle)
    return bundle


def load_forward_ensemble_artifact(
    artifact_path: Path,
    *,
    workspace_root: Path,
    expected_meeting_id: str,
    meeting_start_date: str,
    roster_participant_ids: list[str],
    fallback_action: str,
) -> dict[str, Any] | None:
    if not artifact_path.is_file():
        return None
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != "forward_ensemble_forecast_v1":
        raise ValueError("Unexpected forward ensemble schema")
    if artifact.get("meeting_id") != expected_meeting_id:
        raise ValueError("Forward ensemble meeting mismatch")
    if str(artifact.get("forecast_as_of")) >= meeting_start_date:
        raise ValueError("Forward ensemble cutoff is not before the meeting")
    locked_at = str(artifact.get("locked_at", ""))
    if not locked_at or locked_at[:10] >= meeting_start_date:
        raise ValueError("Forward ensemble was not locked before the meeting")

    predictions = []
    model_rows = []
    for item in artifact.get("models") or []:
        variant_id = str(item["variant_id"])
        if variant_id not in FORWARD_VARIANTS:
            raise ValueError(f"Unsupported forward variant: {variant_id}")
        run_path = workspace_root / str(item["run_artifact"])
        if _sha256_file(run_path) != item["run_artifact_sha256"]:
            raise ValueError(f"Forward run hash mismatch: {variant_id}")
        run = json.loads(run_path.read_text(encoding="utf-8"))
        if run.get("variant_id") != variant_id:
            raise ValueError(f"Forward run variant mismatch: {variant_id}")
        output = run["output"]
        votes = {
            str(vote["participant_id"]): str(vote["choice"])
            for vote in output["votes"]
        }
        predictions.append(
            {
                "model_key": variant_id,
                "policy_action": str(output["final_proposal"]["action_class"]),
                "votes": votes,
            }
        )
        model_rows.append(
            {
                "model_key": variant_id,
                "model_label_zh": MODEL_LABELS_ZH[variant_id],
                "policy_action": str(output["final_proposal"]["action_class"]),
                "historical_policy_accuracy": float(
                    item["historical_policy_accuracy"]
                ),
                "historical_dissent_f1": float(item["historical_dissent_f1"]),
                "run_artifact": str(item["run_artifact"]),
            }
        )
    if {row["model_key"] for row in model_rows} != set(FORWARD_VARIANTS):
        raise ValueError("Forward ensemble must contain all four registered variants")

    combined = combine_forward_predictions(
        predictions,
        roster_participant_ids=roster_participant_ids,
        fallback_action=fallback_action,
    )
    return {**artifact, "model_rows": model_rows, "combined": combined}


def run_forward_subscription_ensemble(
    *,
    source_database: Path,
    app_database: Path,
    reaction_artifact_path: Path,
    policy_evaluation_path: Path,
    vote_evaluation_path: Path,
    variant_matrix_path: Path,
    ablation_spec_path: Path,
    model_spec_path: Path,
    official_context_path: Path,
    communications_database: Path,
    output_directory: Path,
    confirmation: str,
    executor: CodexSubscriptionExecutor | None = None,
) -> dict[str, Any]:
    if confirmation != FORWARD_CONFIRMATION:
        raise ValueError("Forward subscription ensemble requires explicit confirmation")
    workspace_root = Path(__file__).resolve().parents[1]
    from decision_memory.next_meeting_forecast import build_next_meeting_forecast

    forecast = build_next_meeting_forecast(
        source_database,
        app_database,
        reaction_artifact_path,
        policy_evaluation_path,
        vote_evaluation_path,
        communications_database=communications_database,
        official_context_path=official_context_path,
    )
    reaction_artifact = json.loads(
        reaction_artifact_path.read_text(encoding="utf-8")
    )
    base_bundle = build_forward_case_bundle(
        forecast,
        source_database=source_database,
        app_database=app_database,
        reaction_artifact=reaction_artifact,
        workspace_root=workspace_root,
    )
    ablation = json.loads(ablation_spec_path.read_text(encoding="utf-8"))
    variants = {item["variant_id"]: item for item in ablation["variants"]}
    matrix = json.loads(variant_matrix_path.read_text(encoding="utf-8"))
    historical = {item["variant_id"]: item for item in matrix["rows"]}
    model_spec = load_model_spec(model_spec_path)
    active_executor = executor or CodexSubscriptionExecutor()
    active_executor.verify_authentication()

    output_directory = output_directory.resolve()
    if not output_directory.is_relative_to(workspace_root):
        raise ValueError("Forward ensemble output must stay inside workspace")
    model_artifacts = []
    for variant_id in FORWARD_VARIANTS:
        prepared = prepare_prebuilt_variant_bundle(
            base_bundle,
            variants[variant_id],
        )
        bundle_path = output_directory / "bundles" / f"{variant_id}.json"
        _write_new_json(bundle_path, prepared)
        run_path = output_directory / "runs" / f"{variant_id}.json"
        if run_path.exists():
            run = json.loads(run_path.read_text(encoding="utf-8"))
            if run.get("model_bundle_hash") != prepared["model_bundle"]["bundle_hash"]:
                raise ValueError(f"Existing forward run bundle mismatch: {variant_id}")
        else:
            sample = run_subscription_sample(
                active_executor,
                prepared["model_bundle"],
                model_spec,
            )
            restored = restore_simulation_output(
                sample["output"],
                actual_meeting_id=forecast["meeting_id"],
                model_to_actual_participant_id=prepared[
                    "model_to_actual_participant_id"
                ],
            )
            run = {
                "schema_version": "forward_variant_run_v1",
                "status": "SUBSCRIPTION_FORWARD_COMPLETED",
                "execution_provider": "codex_subscription",
                "billing_route": "chatgpt_subscription",
                "platform_api_cost_usd": 0.0,
                "variant_id": variant_id,
                "model_id": model_spec["model_id"],
                "meeting_id": forecast["meeting_id"],
                "forecast_as_of": forecast["forecast_as_of"],
                "model_bundle_hash": prepared["model_bundle"]["bundle_hash"],
                "usage": sample["usage"],
                "usage_totals": sample["usage_totals"],
                "output": restored,
            }
            _write_new_json(run_path, run)
        model_artifacts.append(
            {
                "variant_id": variant_id,
                "model_label_zh": MODEL_LABELS_ZH[variant_id],
                "historical_policy_accuracy": historical[variant_id][
                    "policy_accuracy"
                ],
                "historical_dissent_f1": historical[variant_id]["dissent_f1"],
                "run_artifact": str(run_path.relative_to(workspace_root)),
                "run_artifact_sha256": _sha256_file(run_path),
            }
        )

    artifact = {
        "schema_version": "forward_ensemble_forecast_v1",
        "meeting_id": forecast["meeting_id"],
        "meeting_start_date": forecast["meeting_start_date"],
        "meeting_end_date": forecast["meeting_end_date"],
        "forecast_as_of": forecast["forecast_as_of"],
        "locked_at": _utc_now(),
        "policy_rule": "THREE_OF_FOUR_ELSE_PERSISTENCE",
        "vote_rule": "TWO_OF_FOUR_AGAINST",
        "historical_metric_disclosure_zh": (
            "歷史正確率僅供研究比較；日期記憶測試顯示可能存在答案記憶，"
            "本次會前鎖定結果才是前瞻驗證起點。"
        ),
        "models": model_artifacts,
    }
    artifact_path = output_directory / "ensemble_forecast.json"
    _write_new_json(artifact_path, artifact)
    loaded = load_forward_ensemble_artifact(
        artifact_path,
        workspace_root=workspace_root,
        expected_meeting_id=forecast["meeting_id"],
        meeting_start_date=forecast["meeting_start_date"],
        roster_participant_ids=[
            row["participant_id"] for row in forecast["voter_forecast"]["rows"]
        ],
        fallback_action=forecast["policy_prediction"]["action_class"],
    )
    if loaded is None:
        raise RuntimeError("Forward ensemble artifact was not created")
    return loaded
