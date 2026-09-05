from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from decision_memory.member_evidence import infer_concerns
from decision_memory.public_communications import policy_relevance_score


_OFFICIAL_HOST_SUFFIXES = (
    "federalreserve.gov",
    "clevelandfed.org",
    "minneapolisfed.org",
    "newyorkfed.org",
    "philadelphiafed.org",
)


def _is_official_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    return parsed.scheme == "https" and any(
        host == suffix or host.endswith("." + suffix)
        for suffix in _OFFICIAL_HOST_SUFFIXES
    )


def load_official_forecast_context(path: Path) -> dict[str, Any]:
    context = json.loads(path.read_text(encoding="utf-8"))
    if context.get("schema_version") != "next_meeting_official_context_v1":
        raise ValueError("Unsupported official forecast context schema")
    if context.get("membership_semantics") != (
        "CURRENT_VOTING_MEMBERS_NOT_ATTENDANCE_CONFIRMATION"
    ):
        raise ValueError("Official membership semantics are missing")
    for key in ("meeting_calendar_source_url", "membership_source_url"):
        if not _is_official_url(str(context.get(key, ""))):
            raise ValueError(f"{key} must use an official Federal Reserve source")
    members = context.get("voting_members")
    if not isinstance(members, list) or len(members) != 12:
        raise ValueError("Official context must contain exactly 12 voting members")
    participant_ids = [str(member.get("participant_id", "")) for member in members]
    if not all(participant_ids) or len(set(participant_ids)) != 12:
        raise ValueError("Official voting member participant IDs must be unique")
    valid_roles = {"chair", "vice_chair", "member"}
    if any(member.get("role") not in valid_roles for member in members):
        raise ValueError("Official voting member role is invalid")
    if sum(member.get("role") == "chair" for member in members) != 1:
        raise ValueError("Official context must contain exactly one chair")
    if sum(member.get("role") == "vice_chair" for member in members) != 1:
        raise ValueError("Official context must contain exactly one vice chair")
    for communication in context.get("supplemental_communications", []):
        if communication.get("participant_id") not in participant_ids:
            raise ValueError("Supplemental communication has an unknown participant")
        if communication.get("publication_date", "") > context.get("as_of_date", ""):
            raise ValueError("Supplemental communication is after the context cutoff")
        if communication.get("text_kind") != "source_summary":
            raise ValueError("Supplemental communication must disclose summary semantics")
        if not _is_official_url(str(communication.get("source_url", ""))):
            raise ValueError("Supplemental communication must use an official source")
    return context


def apply_supplemental_communications(
    voter_forecast: dict[str, Any],
    context: dict[str, Any],
) -> None:
    by_participant: dict[str, list[dict[str, Any]]] = {}
    for source in context.get("supplemental_communications", []):
        item = dict(source)
        item["importance_score"] = policy_relevance_score(
            str(item["title"]), str(item["text"])
        )
        by_participant.setdefault(str(item["participant_id"]), []).append(item)

    for row in voter_forecast["rows"]:
        supplemental = by_participant.get(str(row["participant_id"]), [])
        existing = list(row.get("important_communications", []))
        existing_ids = {item["document_id"] for item in existing}
        display_supplemental = [
            {
                "document_id": item["document_id"],
                "publication_date": item["publication_date"],
                "title": item["title"],
                "source_url": item["source_url"],
                "importance_score": item["importance_score"],
                "excerpt": item["display_summary_zh"],
                "text_kind": item["text_kind"],
            }
            for item in supplemental
            if item["document_id"] not in existing_ids
        ]
        merged = existing + display_supplemental
        merged.sort(
            key=lambda item: (
                -int(item.get("importance_score", 0)),
                str(item.get("publication_date", "")),
                str(item.get("document_id", "")),
            )
        )
        row["important_communications"] = merged[:5]
        concern_documents = [
            {
                "document_id": item["document_id"],
                "title": item["title"],
                "text": item["text"],
            }
            for item in supplemental
        ] + [
            {
                "document_id": item["document_id"],
                "title": item["title"],
                "text": item.get("excerpt", ""),
            }
            for item in existing
        ]
        if concern_documents:
            row["inferred_concerns"] = infer_concerns(concern_documents)
        row["communication_document_count"] = len(row["important_communications"])
        row["communication_evidence_status"] = (
            "AVAILABLE"
            if row["important_communications"]
            else "NO_LOCAL_CUTOFF_SAFE_EVIDENCE"
        )
