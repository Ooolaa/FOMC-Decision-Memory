from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from decision_memory.profile_cards import build_profile_cards
from decision_memory.public_communications import policy_relevance_score


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _persona_evidence(
    app_database: Path,
    *,
    meeting_id: str,
    cutoff_date: str,
    participant_ids: list[str],
    per_participant_limit: int = 2,
) -> list[dict[str, Any]]:
    app = sqlite3.connect(
        f"file:{app_database.resolve().as_posix()}?mode=ro", uri=True
    )
    app.row_factory = sqlite3.Row
    evidence = []
    try:
        has_public_communications = app.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'public_communication'
            """
        ).fetchone() is not None
        for participant_id in participant_ids:
            candidates = []
            rows = app.execute(
                """
                SELECT segment.document_id,
                       segment.meeting_id AS source_meeting_id,
                       segment.ordinal,
                       segment.text,
                       segment.content_hash,
                       source.publication_at
                FROM transcript_segment AS segment
                JOIN document_source AS source USING (document_id)
                WHERE segment.participant_id = ?
                  AND segment.meeting_id <> ?
                  AND substr(source.publication_at, 1, 10) <= ?
                ORDER BY source.publication_at DESC,
                         segment.meeting_id DESC,
                         segment.ordinal DESC
                LIMIT ?
                """,
                (
                    participant_id,
                    meeting_id,
                    cutoff_date,
                    per_participant_limit * 2,
                ),
            ).fetchall()
            for row in rows:
                text = str(row["text"])
                if hashlib.sha256(text.encode("utf-8")).hexdigest() != row["content_hash"]:
                    raise ValueError(
                        "Persona transcript segment hash mismatch: "
                        f"{row['document_id']} transcript segment {row['ordinal']}"
                    )
                candidates.append(
                    {
                        "evidence_kind": "transcript",
                        "participant_id": participant_id,
                        "document_id": str(row["document_id"]),
                        "source_meeting_id": str(row["source_meeting_id"]),
                        "publication_at": str(row["publication_at"]),
                        "locator": f"transcript segment {row['ordinal']}",
                        "text": text,
                        "content_hash": str(row["content_hash"]),
                    }
                )
            if has_public_communications:
                public_rows = app.execute(
                    """
                    SELECT communication.document_id,
                           communication.title,
                           communication.text,
                           communication.content_hash,
                           source.publication_at
                    FROM public_communication AS communication
                    JOIN document_source AS source USING (document_id)
                    WHERE communication.participant_id = ?
                      AND substr(source.publication_at, 1, 10) <= ?
                    ORDER BY source.publication_at DESC,
                             communication.document_id DESC
                    """,
                    (participant_id, cutoff_date),
                ).fetchall()
                for row in public_rows:
                    text = str(row["text"])
                    if (
                        hashlib.sha256(text.encode("utf-8")).hexdigest()
                        != row["content_hash"]
                    ):
                        raise ValueError(
                            "Public communication hash mismatch: "
                            f"{row['document_id']}"
                        )
                    relevance_score = policy_relevance_score(
                        str(row["title"]), text
                    )
                    if relevance_score <= 0:
                        continue
                    candidates.append(
                        {
                            "evidence_kind": "public_communication",
                            "participant_id": participant_id,
                            "document_id": str(row["document_id"]),
                            "source_meeting_id": None,
                            "publication_at": str(row["publication_at"]),
                            "locator": "public communication",
                            "title": str(row["title"]),
                            "text": text,
                            "content_hash": str(row["content_hash"]),
                            "policy_relevance_score": relevance_score,
                        }
                    )
            candidates.sort(
                key=lambda item: (
                    item["publication_at"],
                    item["document_id"],
                    item["locator"],
                ),
                reverse=True,
            )
            evidence.extend(candidates[:per_participant_limit])
    finally:
        app.close()
    return evidence


def _voter_reaction_cards(
    app_database: Path,
    reaction_artifact: dict[str, Any],
    meeting_id: str,
    participant_ids: set[str],
) -> list[dict[str, Any]]:
    app = sqlite3.connect(
        f"file:{app_database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        artifact = build_profile_cards(app, reaction_artifact, meeting_id)
    finally:
        app.close()
    cards = [
        card for card in artifact["cards"] if card["participant_id"] in participant_ids
    ]
    if {card["participant_id"] for card in cards} != participant_ids:
        raise ValueError("Reaction profile cards do not cover all simulated voters")
    return cards


def _redact_participant_names(text: str, participants: list[dict[str, Any]]) -> str:
    redacted = text
    terms = []
    for participant in participants:
        display_name = str(participant["display_name"]).strip()
        if display_name:
            terms.append(display_name)
            surname = display_name.split()[-1]
            if len(surname) >= 3:
                terms.append(surname)
    for term in sorted(set(terms), key=len, reverse=True):
        redacted = re.sub(
            rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])",
            "[REDACTED_PARTICIPANT]",
            redacted,
            flags=re.IGNORECASE,
        )
    return redacted


def _anonymize(
    model_bundle: dict[str, Any],
    actual_participants: list[dict[str, Any]],
) -> dict[str, str]:
    actual_to_model = {}
    model_to_actual = {}
    for ordinal, participant in enumerate(model_bundle["participants"], start=1):
        prefix = "chair" if participant["is_chair"] else "participant"
        model_id = f"{prefix}-{ordinal:02d}"
        actual_id = participant["participant_id"]
        actual_to_model[actual_id] = model_id
        model_to_actual[model_id] = actual_id
        participant["participant_id"] = model_id
        participant["display_name"] = "Chair" if participant["is_chair"] else f"Participant {ordinal:02d}"

    for item in model_bundle["persona_evidence"]:
        item["participant_id"] = actual_to_model[item["participant_id"]]
        item["text"] = _redact_participant_names(item["text"], actual_participants)
        if "title" in item:
            item["title"] = _redact_participant_names(
                item["title"], actual_participants
            )
    for card in model_bundle["reaction_profile_cards"]:
        card["participant_id"] = actual_to_model[card["participant_id"]]
        card["display_name"] = next(
            participant["display_name"]
            for participant in model_bundle["participants"]
            if participant["participant_id"] == card["participant_id"]
        )
    for document in model_bundle["documents"]:
        document["text"] = _redact_participant_names(
            document["text"], actual_participants
        )
    return model_to_actual


def prepare_variant_bundle(
    base_bundle: dict[str, Any],
    variant: dict[str, Any],
    *,
    app_database: Path,
    reaction_artifact: dict[str, Any],
) -> dict[str, Any]:
    actual_meeting_id = str(base_bundle["meeting_id"])
    variant_id = str(variant["variant_id"])
    opaque_case_id = "case-" + _canonical_hash(
        {"bundle_hash": base_bundle["bundle_hash"], "variant_id": variant_id}
    )[:20]
    if variant.get("meeting_date"):
        return {
            "variant_id": variant_id,
            "actual_meeting_id": actual_meeting_id,
            "model_to_actual_participant_id": {},
            "anonymity_verified": True,
            "model_bundle": {
                "schema_version": "date_only_probe_bundle_v1",
                "case_id": opaque_case_id,
                "meeting_date": base_bundle["meeting_start_date"],
            },
        }

    model = copy.deepcopy(base_bundle)
    actual_participants = copy.deepcopy(model["participants"])
    participant_ids = [item["participant_id"] for item in actual_participants]
    model["meeting_id"] = opaque_case_id
    model.pop("meeting_start_date", None)
    model.pop("meeting_end_date", None)
    model.pop("information_cutoff_date_et", None)
    if not variant.get("economic_snapshot"):
        model["economic_snapshot"] = []
        model["policy_rate_context"] = []
    model["persona_evidence"] = (
        _persona_evidence(
            app_database,
            meeting_id=actual_meeting_id,
            cutoff_date=str(base_bundle["information_cutoff_date_et"]),
            participant_ids=participant_ids,
        )
        if variant.get("persona_evidence")
        else []
    )
    model["reaction_profile_cards"] = (
        _voter_reaction_cards(
            app_database,
            reaction_artifact,
            actual_meeting_id,
            set(participant_ids),
        )
        if variant.get("reaction_model")
        else []
    )
    model_to_actual = {}
    anonymity_verified = False
    if not variant.get("participant_names"):
        model_to_actual = _anonymize(model, actual_participants)
        serialized_model = json.dumps(model, ensure_ascii=False).casefold()
        leaked = []
        for participant in actual_participants:
            for term in (
                str(participant["participant_id"]),
                str(participant["display_name"]),
            ):
                if term and term.casefold() in serialized_model:
                    leaked.append(term)
        if leaked:
            raise ValueError(
                "Anonymous model bundle still contains participant identity: "
                f"{sorted(set(leaked))}"
            )
        anonymity_verified = True
    model.pop("bundle_hash", None)
    model["bundle_hash"] = _canonical_hash(model)
    return {
        "variant_id": variant_id,
        "actual_meeting_id": actual_meeting_id,
        "model_to_actual_participant_id": model_to_actual,
        "anonymity_verified": anonymity_verified,
        "model_bundle": model,
    }


def prepare_prebuilt_variant_bundle(
    base_bundle: dict[str, Any],
    variant: dict[str, Any],
) -> dict[str, Any]:
    """Apply one frozen variant to a future bundle with prebuilt persona inputs."""
    actual_meeting_id = str(base_bundle["meeting_id"])
    variant_id = str(variant["variant_id"])
    model = copy.deepcopy(base_bundle)
    actual_participants = copy.deepcopy(model["participants"])
    opaque_case_id = "case-" + _canonical_hash(
        {"bundle_hash": base_bundle["bundle_hash"], "variant_id": variant_id}
    )[:20]
    model["meeting_id"] = opaque_case_id
    model.pop("meeting_start_date", None)
    model.pop("meeting_end_date", None)
    model.pop("information_cutoff_date_et", None)
    if not variant.get("economic_snapshot"):
        model["economic_snapshot"] = []
        model["policy_rate_context"] = []
        model.pop("derived_features", None)
    if not variant.get("persona_evidence"):
        model["persona_evidence"] = []
    if not variant.get("reaction_model"):
        model["reaction_profile_cards"] = []

    model_to_actual = {}
    anonymity_verified = False
    if not variant.get("participant_names"):
        model_to_actual = _anonymize(model, actual_participants)
        serialized_model = json.dumps(model, ensure_ascii=False).casefold()
        leaked = []
        for participant in actual_participants:
            for term in (
                str(participant["participant_id"]),
                str(participant["display_name"]),
            ):
                if term and term.casefold() in serialized_model:
                    leaked.append(term)
        if leaked:
            raise ValueError(
                "Anonymous prebuilt bundle still contains participant identity: "
                f"{sorted(set(leaked))}"
            )
        anonymity_verified = True

    model.pop("bundle_hash", None)
    model["bundle_hash"] = _canonical_hash(model)
    return {
        "variant_id": variant_id,
        "actual_meeting_id": actual_meeting_id,
        "model_to_actual_participant_id": model_to_actual,
        "anonymity_verified": anonymity_verified,
        "model_bundle": model,
    }


def restore_simulation_output(
    output: dict[str, Any],
    *,
    actual_meeting_id: str,
    model_to_actual_participant_id: dict[str, str],
) -> dict[str, Any]:
    restored = copy.deepcopy(output)
    restored["meeting_id"] = actual_meeting_id
    if not model_to_actual_participant_id:
        return restored
    for item in restored["profiles"]:
        item["participant_id"] = model_to_actual_participant_id[item["participant_id"]]
    for item in restored["discussion"]:
        item["participant_id"] = model_to_actual_participant_id[item["participant_id"]]
    restored["final_proposal"]["proposer_participant_id"] = (
        model_to_actual_participant_id[
            restored["final_proposal"]["proposer_participant_id"]
        ]
    )
    for item in restored["votes"]:
        item["participant_id"] = model_to_actual_participant_id[item["participant_id"]]
    return restored
