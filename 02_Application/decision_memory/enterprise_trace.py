from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decision_memory.decision_trace import (
    _canonical_json,
    _iter_evidence_refs,
    _normalized_text,
    _validate_schema,
)
from decision_memory.fed_documents import extract_html_paragraphs


SOURCE_BOUNDARY = "synthetic_composite_fixture"
DOCUMENT_TYPE = "synthetic_composite_decision_memo"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_utc(value: str) -> None:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("publication_at must include a timezone")


def register_synthetic_fixture_document(
    connection: sqlite3.Connection,
    local_path: Path,
    *,
    decision_id: str,
    publication_at: str,
) -> str:
    resolved = local_path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Synthetic fixture document is missing: {resolved}")
    if not decision_id.strip():
        raise ValueError("decision_id is required")
    _parse_utc(publication_at)
    content = resolved.read_bytes()
    content_hash = hashlib.sha256(content).hexdigest()
    document_id = f"synthetic-doc-{content_hash[:24]}"
    source_locator = json.dumps(
        {
            "kind": "synthetic_composite_fixture",
            "local_path": str(resolved),
            "byte_length": len(content),
            "disclosure": "Demonstration fixture; not a real customer document.",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    expected = (
        decision_id,
        DOCUMENT_TYPE,
        publication_at,
        "evaluation_only",
        source_locator,
        content_hash,
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO document_source (
            document_id, meeting_id, document_type, publication_at,
            usage_class, source_locator, content_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, *expected, _utc_now()),
    )
    persisted = connection.execute(
        """
        SELECT meeting_id, document_type, publication_at, usage_class,
               source_locator, content_hash
        FROM document_source WHERE document_id = ?
        """,
        (document_id,),
    ).fetchone()
    if persisted != expected:
        raise RuntimeError(f"Synthetic fixture document conflicts: {document_id}")
    return document_id


def _validated_fixture_text(
    connection: sqlite3.Connection,
    document_id: str,
    decision_id: str,
    cache: dict[str, str],
) -> str:
    if document_id in cache:
        return cache[document_id]
    row = connection.execute(
        """
        SELECT meeting_id, document_type, usage_class, source_locator, content_hash
        FROM document_source WHERE document_id = ?
        """,
        (document_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown enterprise evidence document_id: {document_id}")
    if row[0] != decision_id or row[1] != DOCUMENT_TYPE or row[2] != "evaluation_only":
        raise ValueError("Enterprise trace evidence is not a registered synthetic fixture")
    locator = json.loads(row[3])
    if locator.get("kind") != "synthetic_composite_fixture" or "not a real customer" not in locator.get(
        "disclosure", ""
    ).lower():
        raise ValueError("Synthetic fixture disclosure is missing")
    local_path = Path(locator["local_path"])
    content = local_path.read_bytes()
    if hashlib.sha256(content).hexdigest() != row[4]:
        raise ValueError(f"Synthetic fixture content hash mismatch: {document_id}")
    text = _normalized_text(" ".join(extract_html_paragraphs(content)))
    cache[document_id] = text
    return text


def validate_enterprise_decision_trace(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
) -> None:
    _validate_schema(payload)
    if payload["meeting_id"] is not None:
        raise ValueError("Enterprise DecisionTrace meeting_id must be null")
    decision_id = payload["decision_id"]
    case = connection.execute(
        """
        SELECT domain, synthetic, composite
        FROM decision_case WHERE decision_id = ?
        """,
        (decision_id,),
    ).fetchone()
    if case != ("enterprise_demo", 1, 1):
        raise ValueError(
            "Enterprise DecisionTrace requires an enterprise_demo synthetic/composite case"
        )

    option_ids = [item["option_id"] for item in payload["options"]]
    if len(option_ids) != len(set(option_ids)):
        raise ValueError("DecisionTrace option_id values must be unique")
    assumption_ids = [item["assumption_id"] for item in payload["assumptions"]]
    if len(assumption_ids) != len(set(assumption_ids)):
        raise ValueError("DecisionTrace assumption_id values must be unique")

    evidence_cache: dict[str, str] = {}
    for reference in _iter_evidence_refs(payload):
        text = _validated_fixture_text(
            connection,
            reference["document_id"],
            decision_id,
            evidence_cache,
        )
        if _normalized_text(reference["excerpt"]) not in text:
            raise ValueError(
                "Enterprise evidence excerpt not found in synthetic fixture: "
                f"{reference['document_id']}"
            )

    for item in payload["debate"]:
        if item["speaker_scope"] != "committee" or item["participant_id"] is not None:
            raise ValueError(
                "Enterprise composite debate cannot be attributed to a real participant"
            )
    decision = payload["decision"]
    if decision["action_class"] not in {"PROCEED", "DEFER", "REJECT"}:
        raise ValueError("Enterprise action_class must be PROCEED, DEFER, or REJECT")
    if any(
        decision[field] is not None
        for field in ("target_rate", "target_lower", "target_upper")
    ):
        raise ValueError("Enterprise DecisionTrace cannot contain policy-rate targets")

    rounds = payload["vote"]["rounds"]
    round_ids = [item["vote_round"] for item in rounds]
    if len(round_ids) != len(set(round_ids)):
        raise ValueError("Enterprise vote_round values must be unique")
    if any(
        item["for_count"] + item["against_count"] + item["abstain_count"] <= 0
        for item in rounds
    ):
        raise ValueError("Enterprise synthetic vote rounds must contain at least one vote")

    for assumption in payload["assumptions"]:
        persisted = connection.execute(
            """
            SELECT claim, monitor_series_id, monitor_operator, threshold_value,
                   direction_map_version, monitor_rule_version
            FROM decision_assumption
            WHERE assumption_id = ? AND decision_id = ?
            """,
            (assumption["assumption_id"], decision_id),
        ).fetchone()
        expected = (
            assumption["claim"],
            assumption["monitor_series_id"],
            assumption["monitor_operator"],
            assumption["threshold_value"],
            assumption["direction_map_version"],
            assumption["monitor_rule_version"],
        )
        if persisted != expected:
            raise ValueError(
                "Enterprise DecisionTrace assumption conflicts with registered monitor: "
                f"{assumption['assumption_id']}"
            )


def persist_enterprise_decision_trace(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    extractor_version: str,
) -> dict[str, Any]:
    if not extractor_version.strip():
        raise ValueError("extractor_version is required")
    validate_enterprise_decision_trace(connection, payload)
    decision_id = payload["decision_id"]
    case_context_row = connection.execute(
        "SELECT context_json FROM decision_case WHERE decision_id = ?",
        (decision_id,),
    ).fetchone()
    if case_context_row is None:
        raise ValueError(f"Unknown enterprise decision case: {decision_id}")
    case_context = json.loads(case_context_row[0])
    if not isinstance(case_context, dict):
        raise ValueError("Enterprise decision_case context_json must be an object")
    existing_trace_context = case_context.get("decision_trace_context")
    if existing_trace_context is not None and existing_trace_context != payload["context"]:
        raise RuntimeError(
            f"Existing enterprise DecisionTrace context conflicts: {decision_id}"
        )
    case_context["decision_trace_context"] = payload["context"]
    connection.execute(
        "UPDATE decision_case SET context_json = ? WHERE decision_id = ?",
        (_canonical_json(case_context), decision_id),
    )
    trace_id = "trace-" + hashlib.sha256(
        _canonical_json(
            {"payload": payload, "extractor_version": extractor_version}
        ).encode("utf-8")
    ).hexdigest()[:24]
    expected = (
        decision_id,
        _canonical_json(payload["options"]),
        _canonical_json(payload["debate"]),
        _canonical_json(payload["decision"]),
        _canonical_json(payload["vote"]),
        extractor_version,
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO decision_trace (
            trace_id, decision_id, options_json, debate_json,
            decision_json, vote_json, extractor_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (trace_id, *expected, _utc_now()),
    )
    persisted = connection.execute(
        """
        SELECT decision_id, options_json, debate_json, decision_json,
               vote_json, extractor_version
        FROM decision_trace WHERE decision_id = ?
        """,
        (decision_id,),
    ).fetchone()
    if persisted != expected:
        raise RuntimeError(f"Existing enterprise DecisionTrace conflicts: {decision_id}")
    return {
        "decision_id": decision_id,
        "trace_id": trace_id,
        "assumption_count": len(payload["assumptions"]),
        "evidence_reference_count": sum(1 for _ in _iter_evidence_refs(payload)),
        "source_boundary": SOURCE_BOUNDARY,
    }
