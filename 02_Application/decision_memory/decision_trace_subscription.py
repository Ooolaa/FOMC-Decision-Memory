from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from decision_memory.codex_subscription import (
    SUBSCRIPTION_CONFIRMATION,
    CodexSubscriptionError,
    CodexSubscriptionExecutor,
    _write_new_json,
)
from decision_memory.decision_trace import (
    SCHEMA_PATH,
    _iter_evidence_refs,
    _normalized_text,
    validate_fomc_decision_trace,
)
from decision_memory.fed_documents import extract_html_paragraphs
from decision_memory.model_preflight import DEFAULT_SPEC_PATH, load_model_spec


ROOT = Path(__file__).resolve().parents[1]
TRACE_SOURCE_BOUNDARY = "post_meeting_extraction_only_not_historical_case_input"
SPARSE_MINUTES_MEETING_ID = "FOMC-2020-03-02"
ASSUMPTION_MONITOR_CONTRACT_VERSION = "atomic_one_clause_monitor_v1"
TRACE_EXTRACTOR_VERSION = "codex-subscription-decision-trace-v5-atomic-monitor"
DEFAULT_OUTPUT_DIRECTORY = (
    ROOT
    / "artifacts"
    / "codex_subscription"
    / "decision_trace_50_v5_atomic_monitor"
)
ASSUMPTION_REPAIR_RULES = (
    "ASSUMPTION_REPAIR_RULES: Rewrite each invalid assumption as exactly one "
    "one-sided statement that the selected operator and threshold fully test. "
    "For range_or_symmetric_claim_requires_atomic_rewrite, remove range, around, "
    "symmetric, stabilize, converge, and return-to-target semantics; state only "
    "'at or above X' for GTE/GT or 'at or below X' for LTE/LT. For "
    "compound_claim_requires_atomic_rewrite, split independent series conditions "
    "into separate assumptions or retain only one condition. For "
    "temporal_path_requires_atomic_rewrite, retain one state and remove sequence "
    "language. For index_percent_threshold_requires_yoy_transform, use "
    "yoy_percent_change_v1. For nonnegative_level_threshold_is_tautological, "
    "choose a meaningful nonzero threshold or another supported atomic series."
)


class DecisionTraceExtractionError(CodexSubscriptionError):
    def __init__(self, message: str, failure_payload: dict[str, Any]) -> None:
        super().__init__(message)
        self.failure_payload = failure_payload


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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


def _read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def load_trace_meeting_ids(app_database: Path) -> list[str]:
    connection = _read_only_connection(app_database)
    try:
        rows = connection.execute(
            """
            SELECT DISTINCT meeting_id
            FROM transcript_segment
            ORDER BY meeting_id
            """
        ).fetchall()
    finally:
        connection.close()
    meeting_ids = [str(row[0]) for row in rows]
    if len(meeting_ids) != 50 or len(set(meeting_ids)) != 50:
        raise ValueError(
            "R5 DecisionTrace corpus requires exactly 50 unique transcript meetings"
        )
    return meeting_ids


def _verified_document(row: sqlite3.Row) -> tuple[dict[str, Any], bytes]:
    locator = json.loads(row["source_locator"])
    local_path = Path(locator["local_path"])
    if not local_path.is_file():
        raise FileNotFoundError(f"DecisionTrace source file is missing: {local_path}")
    content = local_path.read_bytes()
    if _sha256_bytes(content) != row["content_hash"]:
        raise ValueError(
            f"DecisionTrace source hash mismatch: {row['document_id']}"
        )
    document = {
        "document_id": row["document_id"],
        "document_type": row["document_type"],
        "publication_at": row["publication_at"],
        "usage_class": row["usage_class"],
        "source_locator": locator,
        "content_hash": row["content_hash"],
    }
    return document, content


def build_trace_bundle(
    source_database: Path,
    app_database: Path,
    *,
    meeting_id: str,
) -> dict[str, Any]:
    if meeting_id not in load_trace_meeting_ids(app_database):
        raise ValueError(f"Meeting is not in the fixed transcript corpus: {meeting_id}")
    app = _read_only_connection(app_database)
    source = _read_only_connection(source_database)
    try:
        document_rows = app.execute(
            """
            SELECT document_id, document_type, publication_at, usage_class,
                   source_locator, content_hash
            FROM document_source
            WHERE meeting_id = ?
              AND document_type IN ('statement', 'minutes', 'transcript')
            ORDER BY CASE document_type
                         WHEN 'statement' THEN 1
                         WHEN 'minutes' THEN 2
                         ELSE 3
                     END
            """,
            (meeting_id,),
        ).fetchall()
        by_type: dict[str, sqlite3.Row] = {}
        for row in document_rows:
            document_type = str(row["document_type"])
            if document_type in by_type:
                raise ValueError(
                    f"Multiple {document_type} documents for meeting: {meeting_id}"
                )
            by_type[document_type] = row
        required_types = {"statement", "transcript"}
        if not required_types.issubset(by_type):
            raise ValueError(f"Missing required DecisionTrace documents: {meeting_id}")
        missing_minutes = "minutes" not in by_type
        if missing_minutes != (meeting_id == SPARSE_MINUTES_MEETING_ID):
            raise ValueError(
                "Unexpected minutes availability outside the registered sparse exception"
            )

        documents = []
        for row in document_rows:
            document, content = _verified_document(row)
            if document["document_type"] == "transcript":
                if document["usage_class"] != "persona_evidence":
                    raise ValueError("Transcript must remain persona_evidence")
                segment_rows = app.execute(
                    """
                    SELECT ordinal, speaker_label, participant_id, text, content_hash
                    FROM transcript_segment
                    WHERE meeting_id = ? AND document_id = ?
                    ORDER BY ordinal
                    """,
                    (meeting_id, document["document_id"]),
                ).fetchall()
                if not segment_rows:
                    raise ValueError(f"Transcript has no registered segments: {meeting_id}")
                segments = []
                for segment in segment_rows:
                    text = str(segment["text"])
                    if _sha256_bytes(text.encode("utf-8")) != segment["content_hash"]:
                        raise ValueError(
                            f"Transcript segment hash mismatch: {meeting_id} "
                            f"{segment['ordinal']}"
                        )
                    segments.append(
                        {
                            "ordinal": int(segment["ordinal"]),
                            "speaker_label": segment["speaker_label"],
                            "participant_id": segment["participant_id"],
                            "text": text,
                            "content_hash": segment["content_hash"],
                        }
                    )
                document["segments"] = segments
            else:
                if document["usage_class"] != "label_only":
                    raise ValueError("Statement/minutes must remain label_only")
                paragraphs = [
                    {"ordinal": ordinal, "text": text}
                    for ordinal, text in enumerate(extract_html_paragraphs(content), start=1)
                    if text.strip()
                ]
                if not paragraphs:
                    raise ValueError(
                        f"Official HTML document has no paragraphs: {document['document_id']}"
                    )
                document["paragraphs"] = paragraphs
            documents.append(document)

        participant_rows = app.execute(
            """
            SELECT participant.participant_id, participant.display_name,
                   meeting_participant.role, meeting_participant.is_voter,
                   meeting_participant.is_chair
            FROM meeting_participant
            JOIN participant USING (participant_id)
            WHERE meeting_participant.meeting_id = ?
            ORDER BY meeting_participant.is_chair DESC,
                     meeting_participant.is_voter DESC,
                     participant.display_name
            """,
            (meeting_id,),
        ).fetchall()
        if not participant_rows:
            raise ValueError(f"Meeting roster is missing: {meeting_id}")
        participants = [dict(row) for row in participant_rows]

        outcome_row = app.execute(
            """
            SELECT action_class, target_rate, target_lower, target_upper
            FROM meeting_outcome WHERE meeting_id = ?
            """,
            (meeting_id,),
        ).fetchone()
        if outcome_row is None:
            raise ValueError(f"Meeting outcome is missing: {meeting_id}")
        outcome = dict(outcome_row)

        vote_rows = app.execute(
            """
            SELECT vote_round,
                   SUM(CASE WHEN voter_choice = 'FOR' THEN 1 ELSE 0 END) AS for_count,
                   SUM(CASE WHEN voter_choice = 'AGAINST' THEN 1 ELSE 0 END) AS against_count,
                   0 AS abstain_count
            FROM participant_vote
            WHERE meeting_id = ?
            GROUP BY vote_round
            ORDER BY vote_round
            """,
            (meeting_id,),
        ).fetchall()
        if not vote_rows:
            raise ValueError(f"Meeting vote labels are missing: {meeting_id}")
        vote_rounds = [dict(row) for row in vote_rows]

        series_rows = source.execute(
            """
            SELECT series_id, title, frequency, units, vintage_mode
            FROM economic_series
            ORDER BY series_id
            """
        ).fetchall()
        if len(series_rows) != 22:
            raise ValueError("R5 DecisionTrace monitor allowlist requires 22 series")
        monitor_series = [dict(row) for row in series_rows]
    finally:
        source.close()
        app.close()

    bundle = {
        "schema_version": "decision_trace_bundle_v1",
        "meeting_id": meeting_id,
        "decision_id": f"fomc-{meeting_id}",
        "source_boundary": TRACE_SOURCE_BOUNDARY,
        "sparse_minutes_exception": missing_minutes,
        "documents": documents,
        "participants": participants,
        "authoritative_outcome": outcome,
        "authoritative_vote_rounds": vote_rounds,
        "monitor_series": monitor_series,
    }
    bundle["bundle_hash"] = _sha256_bytes(_canonical_json(bundle).encode("utf-8"))
    return bundle


def _runtime_trace_schema(bundle: dict[str, Any]) -> dict[str, Any]:
    def typed_const(value: Any) -> dict[str, Any]:
        if value is None:
            value_type = "null"
        elif isinstance(value, bool):
            value_type = "boolean"
        elif isinstance(value, int):
            value_type = "integer"
        elif isinstance(value, float):
            value_type = "number"
        else:
            value_type = "string"
        return {"type": value_type, "const": value}

    schema = copy.deepcopy(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    schema["properties"]["schema_version"] = typed_const("decision_trace_v1")
    schema["properties"]["decision_id"] = typed_const(bundle["decision_id"])
    schema["properties"]["meeting_id"] = typed_const(bundle["meeting_id"])
    schema["$defs"]["evidence_ref"]["properties"]["document_id"] = {
        "type": "string",
        "enum": [item["document_id"] for item in bundle["documents"]],
    }
    schema["properties"]["debate"]["items"]["properties"][
        "participant_id"
    ] = {
        "type": ["string", "null"],
        "enum": [None]
        + [item["participant_id"] for item in bundle["participants"]]
    }
    schema["properties"]["assumptions"]["items"]["properties"][
        "monitor_series_id"
    ] = {
        "type": "string",
        "enum": [item["series_id"] for item in bundle["monitor_series"]],
    }

    decision_properties = schema["properties"]["decision"]["properties"]
    for name in ("action_class", "target_rate", "target_lower", "target_upper"):
        decision_properties[name] = typed_const(
            bundle["authoritative_outcome"][name]
        )

    rounds_schema = schema["properties"]["vote"]["properties"]["rounds"]
    expected_rounds = bundle["authoritative_vote_rounds"]
    rounds_schema["minItems"] = len(expected_rounds)
    rounds_schema["maxItems"] = len(expected_rounds)
    rounds_schema["items"]["properties"]["vote_round"] = {
        "type": "integer",
        "enum": [item["vote_round"] for item in expected_rounds],
    }
    return schema


def _trace_prompt(bundle: dict[str, Any]) -> str:
    return (
        "You are extracting a post-meeting DecisionTrace from official FOMC "
        "statement, minutes, and transcript evidence. This is retrospective "
        "label/evaluation work, never historical Case input. Return exactly one "
        "JSON object matching the supplied schema. Select every evidence source "
        "by its registered document_id and locator; the excerpt field must copy "
        "a short anchor phrase and will be replaced deterministically with an "
        "exact canonical substring registered at that locator. For statement or "
        "minutes use locator "
        "'paragraph <ordinal>'; for transcript use locator 'transcript segment "
        "<ordinal>'. Attribute a participant position only "
        "when that transcript segment has the same non-null participant_id; use "
        "committee scope with null participant_id for unattributed summaries. "
        "Extract at least two genuinely distinct policy options, the substantive "
        "debate, and at least one explicit monitorable assumption. Copy the "
        "authoritative outcome and vote-round constants exactly. Use only the "
        "allowed monitor series and do not invent document or participant IDs. "
        "Each assumption must encode exactly one atomic, one-sided condition. "
        "Do not encode a numeric range, symmetric target, cross-series compound "
        "condition, or peak-then-decline path with one threshold. For price-index "
        "series CPIAUCSL, CPILFESL, PCEPI, and PCEPILFE, comparisons with an "
        "inflation percentage must use monitor_rule_version "
        "'yoy_percent_change_v1'; use 'level_threshold_v1' only for a raw level. "
        "A nonnegative count or dollar level with a threshold at or below zero is "
        "not a meaningful improvement monitor. "
        "Do not call tools or inspect the filesystem.\n\nTRACE_BUNDLE="
        + json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    )


def _usage_totals(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        name: sum(int(record[name]) for record in records)
        for name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
    }


def _trace_failure_payload(
    *,
    bundle: dict[str, Any],
    spec: dict[str, Any],
    failure_layer: str,
    violations: list[str],
    candidate: Any,
    usage_records: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": "SUBSCRIPTION_TRACE_FAILED_CLOSED",
        "execution_provider": "codex_subscription",
        "billing_route": "chatgpt_subscription",
        "platform_api_cost_usd": 0.0,
        "model_id": spec["model_id"],
        "extractor_version": TRACE_EXTRACTOR_VERSION,
        "assumption_monitor_contract_version": ASSUMPTION_MONITOR_CONTRACT_VERSION,
        "meeting_id": bundle["meeting_id"],
        "bundle_hash": bundle["bundle_hash"],
        "failure_layer": failure_layer,
        "violations": violations,
        "last_candidate": candidate,
        "usage": usage_records,
        "usage_totals": _usage_totals(usage_records),
    }


def _invalid_evidence_source_texts(
    bundle: dict[str, Any], candidate: dict[str, Any]
) -> list[dict[str, Any]]:
    invalid = []
    seen = set()
    for reference in _iter_evidence_refs(candidate):
        locator = reference["locator"].strip()
        source = _locator_source_text(bundle, reference)
        if source is None or _normalized_text(reference["excerpt"]) in _normalized_text(source):
            continue
        key = (reference["document_id"], locator, reference["excerpt"])
        if key in seen:
            continue
        seen.add(key)
        invalid.append(
            {
                "document_id": reference["document_id"],
                "locator": locator,
                "invalid_excerpt": reference["excerpt"],
                "exact_source_text": source,
            }
        )
    return invalid


def _locator_source_text(
    bundle: dict[str, Any], reference: dict[str, str]
) -> str | None:
    document = next(
        (
            item
            for item in bundle["documents"]
            if item["document_id"] == reference["document_id"]
        ),
        None,
    )
    if document is None:
        return None
    locator = reference["locator"].strip()
    if document["document_type"] == "transcript":
        prefix = "transcript segment "
        items = document.get("segments") or []
    else:
        prefix = "paragraph "
        items = document.get("paragraphs") or []
    if not locator.startswith(prefix) or not locator[len(prefix) :].isdigit():
        return None
    ordinal = int(locator[len(prefix) :])
    return next(
        (item["text"] for item in items if int(item["ordinal"]) == ordinal),
        None,
    )


def _materialize_evidence_excerpts(
    bundle: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    materialized = copy.deepcopy(candidate)
    for reference in _iter_evidence_refs(materialized):
        source = _locator_source_text(bundle, reference)
        if source is None:
            continue
        normalized_source = _normalized_text(source)
        normalized_hint = _normalized_text(reference["excerpt"])
        if normalized_hint in normalized_source and len(normalized_hint) <= 1000:
            reference["excerpt"] = normalized_hint
            continue
        if len(normalized_source) <= 1000:
            reference["excerpt"] = normalized_source
            continue
        match = difflib.SequenceMatcher(
            None,
            normalized_hint.casefold(),
            normalized_source.casefold(),
            autojunk=False,
        ).find_longest_match()
        minimum_anchor = min(40, max(12, len(normalized_hint) // 3))
        if match.size < minimum_anchor:
            continue
        start = max(0, match.b - 350)
        end = min(len(normalized_source), start + 900)
        start = max(0, end - 900)
        reference["excerpt"] = normalized_source[start:end]
    return materialized


def _transcript_locators_by_participant(
    bundle: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, list[dict[str, str]]]:
    requested = {
        item["participant_id"]
        for item in candidate.get("debate", [])
        if item.get("speaker_scope") == "participant" and item.get("participant_id")
    }
    result = {participant_id: [] for participant_id in sorted(requested)}
    for document in bundle["documents"]:
        if document["document_type"] != "transcript":
            continue
        for segment in document.get("segments") or []:
            participant_id = segment.get("participant_id")
            if participant_id not in result:
                continue
            result[participant_id].append(
                {
                    "document_id": document["document_id"],
                    "locator": f"transcript segment {segment['ordinal']}",
                }
            )
    return result


def _sanitize_participant_attribution(
    bundle: dict[str, Any], candidate: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    sanitized = copy.deepcopy(candidate)
    transcript_owners = {}
    for document in bundle["documents"]:
        if document["document_type"] != "transcript":
            continue
        for segment in document.get("segments") or []:
            transcript_owners[
                (
                    document["document_id"],
                    f"transcript segment {segment['ordinal']}",
                )
            ] = segment.get("participant_id")
    demotions = []
    removed_references = []
    for item in sanitized.get("debate", []):
        if item.get("speaker_scope") != "participant":
            continue
        participant_id = item.get("participant_id")
        matching = []
        mismatching = []
        for reference in item.get("evidence_refs", []):
            key = (reference["document_id"], reference["locator"].strip())
            if key not in transcript_owners:
                continue
            if transcript_owners[key] == participant_id:
                matching.append(reference)
            else:
                mismatching.append(reference)
        if matching:
            if mismatching:
                item["evidence_refs"] = [
                    reference
                    for reference in item["evidence_refs"]
                    if reference not in mismatching
                ]
                removed_references.extend(
                    {
                        "participant_id": participant_id,
                        "document_id": reference["document_id"],
                        "locator": reference["locator"],
                        "actual_participant_id": transcript_owners[
                            (reference["document_id"], reference["locator"].strip())
                        ],
                    }
                    for reference in mismatching
                )
            continue
        demotions.append(
            {
                "original_participant_id": participant_id,
                "position": item["position"],
                "reason": "no_matching_transcript_segment",
                "cited_participant_ids": sorted(
                    {
                        transcript_owners[
                            (
                                reference["document_id"],
                                reference["locator"].strip(),
                            )
                        ]
                        for reference in mismatching
                        if transcript_owners[
                            (
                                reference["document_id"],
                                reference["locator"].strip(),
                            )
                        ]
                        is not None
                    }
                ),
            }
        )
        item["speaker_scope"] = "committee"
        item["participant_id"] = None
    return sanitized, {
        "policy": "remove_mismatched_if_supported_else_demote_to_committee_v1",
        "demoted_item_count": len(demotions),
        "removed_mismatched_reference_count": len(removed_references),
        "demotions": demotions,
        "removed_references": removed_references,
    }


def run_trace_extraction(
    executor: Any,
    bundle: dict[str, Any],
    spec: dict[str, Any],
    *,
    app_database: Path,
) -> dict[str, Any]:
    schema = _runtime_trace_schema(bundle)
    validator = Draft202012Validator(schema)
    prompt = _trace_prompt(bundle)
    usage_records = []
    trace = None
    attribution_sanitization = None
    allowed_series = {item["series_id"] for item in bundle["monitor_series"]}
    monitor_series_metadata = {
        item["series_id"]: item for item in bundle["monitor_series"]
    }
    allowed_evidence_ids = [item["document_id"] for item in bundle["documents"]]
    allowed_participant_ids = [
        item["participant_id"] for item in bundle["participants"]
    ]

    for attempt in (1, 2):
        result = executor.run_stage(
            model_id=spec["model_id"],
            reasoning_effort="high",
            prompt=prompt,
            schema=schema,
        )
        candidate = result["output"]
        usage_records.append(
            {
                "attempt": attempt,
                "thread_id": result.get("thread_id"),
                "reasoning_effort": "high",
                "latency_seconds": result["latency_seconds"],
                **result["usage"],
            }
        )
        schema_errors = sorted(
            validator.iter_errors(candidate), key=lambda item: list(item.path)
        )
        if schema_errors:
            violation = schema_errors[0].message
            raise DecisionTraceExtractionError(
                "DecisionTrace schema-layer validation failure: " + violation,
                _trace_failure_payload(
                    bundle=bundle,
                    spec=spec,
                    failure_layer="schema",
                    violations=[violation],
                    candidate=candidate,
                    usage_records=usage_records,
                ),
            )
        candidate = _materialize_evidence_excerpts(bundle, candidate)
        candidate, attribution_sanitization = _sanitize_participant_attribution(
            bundle, candidate
        )

        app = sqlite3.connect(
            f"file:{app_database.resolve().as_posix()}?mode=ro", uri=True
        )
        try:
            try:
                validate_fomc_decision_trace(
                    app,
                    candidate,
                    allowed_monitor_series_ids=allowed_series,
                    monitor_series_metadata=monitor_series_metadata,
                )
            except (FileNotFoundError, ValueError) as error:
                semantic_violation = str(error)
            else:
                semantic_violation = None
        finally:
            app.close()
        if semantic_violation is None:
            trace = candidate
            break
        if attempt == 2:
            raise DecisionTraceExtractionError(
                "DecisionTrace semantic repair failed: " + semantic_violation,
                _trace_failure_payload(
                    bundle=bundle,
                    spec=spec,
                    failure_layer="semantic",
                    violations=[semantic_violation],
                    candidate=candidate,
                    usage_records=usage_records,
                ),
            )
        prompt = (
            _trace_prompt(bundle)
            + "\n\nSEMANTIC_VIOLATIONS="
            + json.dumps([semantic_violation], ensure_ascii=False)
            + "\nEXACT_LOCATOR_SOURCE_TEXTS="
            + json.dumps(
                _invalid_evidence_source_texts(bundle, candidate),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\nALLOWED_TRANSCRIPT_LOCATORS_BY_PARTICIPANT="
            + json.dumps(
                _transcript_locators_by_participant(bundle, candidate),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\nALLOWED_EVIDENCE_IDS="
            + json.dumps(allowed_evidence_ids, ensure_ascii=False)
            + "\nALLOWED_PARTICIPANT_IDS="
            + json.dumps(allowed_participant_ids, ensure_ascii=False)
            + "\n"
            + ASSUMPTION_REPAIR_RULES
            + "\nINVALID_OUTPUT="
            + json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
            + "\nRepair only the listed violation and return the complete JSON object."
        )

    if trace is None:
        raise CodexSubscriptionError("DecisionTrace extraction produced no trace")
    evidence_refs = list(_iter_evidence_refs(trace))
    document_type_by_id = {
        item["document_id"]: item["document_type"] for item in bundle["documents"]
    }
    totals = _usage_totals(usage_records)
    return {
        "status": "SUBSCRIPTION_TRACE_COMPLETED",
        "execution_provider": "codex_subscription",
        "billing_route": "chatgpt_subscription",
        "platform_api_cost_usd": 0.0,
        "model_id": spec["model_id"],
        "extractor_version": TRACE_EXTRACTOR_VERSION,
        "assumption_monitor_contract_version": ASSUMPTION_MONITOR_CONTRACT_VERSION,
        "meeting_id": bundle["meeting_id"],
        "bundle_hash": bundle["bundle_hash"],
        "source_boundary": TRACE_SOURCE_BOUNDARY,
        "evidence_excerpt_materialization": "registered_locator_anchor_window_v2",
        "attribution_sanitization": attribution_sanitization,
        "semantic_validation": {
            "valid": True,
            "assumption_monitor_contract_version": (
                ASSUMPTION_MONITOR_CONTRACT_VERSION
            ),
            "option_count": len(trace["options"]),
            "debate_item_count": len(trace["debate"]),
            "participant_debate_count": sum(
                1 for item in trace["debate"] if item["speaker_scope"] == "participant"
            ),
            "assumption_count": len(trace["assumptions"]),
            "evidence_reference_count": len(evidence_refs),
            "transcript_evidence_reference_count": sum(
                1
                for item in evidence_refs
                if document_type_by_id[item["document_id"]] == "transcript"
            ),
        },
        "usage": usage_records,
        "usage_totals": totals,
        "trace": trace,
    }


def _safe_output_directory(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise ValueError("DecisionTrace subscription output must stay in workspace")
    return resolved


def _bundle_path(output_directory: Path, meeting_id: str) -> Path:
    return output_directory / "bundles" / f"{meeting_id}.json"


def _run_path(output_directory: Path, meeting_id: str, model_id: str) -> Path:
    return (
        output_directory
        / "runs"
        / f"{meeting_id}_{model_id.replace('.', '-')}.json"
    )


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def aggregate_trace_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "case_count": len(reports),
        "request_count": 0,
        "repair_request_count": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "option_count": 0,
        "debate_item_count": 0,
        "participant_debate_count": 0,
        "assumption_count": 0,
        "evidence_reference_count": 0,
        "transcript_evidence_reference_count": 0,
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
        validation = report.get("semantic_validation") or {}
        for name in (
            "option_count",
            "debate_item_count",
            "participant_debate_count",
            "assumption_count",
            "evidence_reference_count",
            "transcript_evidence_reference_count",
        ):
            totals[name] += int(validation.get(name, 0) or 0)
    return totals


def build_trace_bundle_preflight(
    *,
    source_database: Path,
    app_database: Path,
    output_directory: Path = DEFAULT_OUTPUT_DIRECTORY,
) -> dict[str, Any]:
    output_root = _safe_output_directory(output_directory)
    cases = []
    for meeting_id in load_trace_meeting_ids(app_database):
        bundle = build_trace_bundle(
            source_database,
            app_database,
            meeting_id=meeting_id,
        )
        path = _bundle_path(output_root, meeting_id)
        _write_new_json(path, bundle)
        transcript = next(
            item
            for item in bundle["documents"]
            if item["document_type"] == "transcript"
        )
        cases.append(
            {
                "meeting_id": meeting_id,
                "bundle_hash": bundle["bundle_hash"],
                "bundle_artifact": str(path.relative_to(ROOT)),
                "bundle_artifact_sha256": _sha256_file(path),
                "bundle_bytes": path.stat().st_size,
                "document_count": len(bundle["documents"]),
                "transcript_segment_count": len(transcript["segments"]),
                "participant_count": len(bundle["participants"]),
                "sparse_minutes_exception": bundle["sparse_minutes_exception"],
            }
        )
    report = {
        "schema_version": "decision_trace_subscription_preflight_v1",
        "status": "PREFLIGHT_COMPLETED_NO_MODEL_CALL",
        "execution_provider": "local_deterministic",
        "platform_api_calls": 0,
        "platform_api_cost_usd": 0.0,
        "extractor_version": TRACE_EXTRACTOR_VERSION,
        "assumption_monitor_contract_version": ASSUMPTION_MONITOR_CONTRACT_VERSION,
        "case_count": len(cases),
        "sparse_minutes_exception_count": sum(
            int(item["sparse_minutes_exception"]) for item in cases
        ),
        "source_database_sha256": _sha256_file(source_database.resolve()),
        "app_database_sha256": _sha256_file(app_database.resolve()),
        "cases": cases,
    }
    _write_new_json(output_root / "bundle_preflight.json", report)
    return report


def _validated_existing_report(
    path: Path,
    *,
    bundle: dict[str, Any],
    model_id: str,
    app_database: Path,
) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "status": "SUBSCRIPTION_TRACE_COMPLETED",
        "execution_provider": "codex_subscription",
        "billing_route": "chatgpt_subscription",
        "platform_api_cost_usd": 0.0,
        "model_id": model_id,
        "extractor_version": TRACE_EXTRACTOR_VERSION,
        "assumption_monitor_contract_version": ASSUMPTION_MONITOR_CONTRACT_VERSION,
        "meeting_id": bundle["meeting_id"],
        "bundle_hash": bundle["bundle_hash"],
    }
    mismatches = {
        key: {"expected": expected, "actual": report.get(key)}
        for key, expected in required.items()
        if report.get(key) != expected
    }
    if mismatches:
        raise ValueError(f"Existing DecisionTrace artifact mismatch: {mismatches}")
    app = sqlite3.connect(
        f"file:{app_database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        validate_fomc_decision_trace(
            app,
            report["trace"],
            allowed_monitor_series_ids={
                item["series_id"] for item in bundle["monitor_series"]
            },
            monitor_series_metadata={
                item["series_id"]: item for item in bundle["monitor_series"]
            },
        )
    finally:
        app.close()
    return report


def run_trace_subscription_batch(
    executor: Any,
    *,
    source_database: Path,
    app_database: Path,
    spec: dict[str, Any],
    output_directory: Path = DEFAULT_OUTPUT_DIRECTORY,
    max_new_cases: int | None = None,
) -> dict[str, Any]:
    if max_new_cases is not None and max_new_cases <= 0:
        raise ValueError("max_new_cases must be positive")
    output_root = _safe_output_directory(output_directory)
    meeting_ids = load_trace_meeting_ids(app_database)
    status_path = output_root / "batch_status.json"
    cases = []
    reports = []
    new_cases = 0
    active_failure: dict[str, Any] = {}
    try:
        for ordinal, meeting_id in enumerate(meeting_ids, start=1):
            if max_new_cases is not None and new_cases >= max_new_cases:
                break
            bundle = build_trace_bundle(
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
                    run_path,
                    bundle=bundle,
                    model_id=spec["model_id"],
                    app_database=app_database,
                )
            else:
                try:
                    report = run_trace_extraction(
                        executor,
                        bundle,
                        spec,
                        app_database=app_database,
                    )
                except DecisionTraceExtractionError as error:
                    failure_payload = error.failure_payload
                    failure_hash = _sha256_bytes(
                        _canonical_json(failure_payload).encode("utf-8")
                    )
                    failure_path = (
                        output_root
                        / "failures"
                        / f"{meeting_id}_{failure_hash[:16]}.json"
                    )
                    _write_new_json(failure_path, failure_payload)
                    active_failure = {
                        "failure_artifact": str(failure_path.relative_to(ROOT)),
                        "failure_artifact_sha256": _sha256_file(failure_path),
                        "failed_case_usage": failure_payload["usage_totals"],
                    }
                    raise
                _write_new_json(run_path, report)
                new_cases += 1
            reports.append(report)
            cases.append(
                {
                    "ordinal": ordinal,
                    "meeting_id": meeting_id,
                    "bundle_hash": bundle["bundle_hash"],
                    "run_artifact": str(run_path.relative_to(ROOT)),
                    "run_artifact_sha256": _sha256_file(run_path),
                    "reused": reused,
                    "semantic_validation": report["semantic_validation"],
                }
            )
            _write_status(
                status_path,
                {
                    "schema_version": "decision_trace_subscription_batch_v1",
                    "status": "RUNNING",
                    "updated_at": _utc_now(),
                    "execution_provider": "codex_subscription",
                    "billing_route": "chatgpt_subscription",
                    "platform_api_calls": 0,
                    "platform_api_cost_usd": 0.0,
                    "model_id": spec["model_id"],
                    "extractor_version": TRACE_EXTRACTOR_VERSION,
                    "assumption_monitor_contract_version": (
                        ASSUMPTION_MONITOR_CONTRACT_VERSION
                    ),
                    "total_case_count": len(meeting_ids),
                    "completed_case_count": len(cases),
                    "new_case_count_this_run": new_cases,
                    "pending_case_count": len(meeting_ids) - len(cases),
                    "usage": aggregate_trace_reports(reports),
                    "cases": cases,
                },
            )
    except Exception as error:
        failed = {
            "schema_version": "decision_trace_subscription_batch_v1",
            "status": "FAILED_CLOSED",
            "updated_at": _utc_now(),
            "execution_provider": "codex_subscription",
            "billing_route": "chatgpt_subscription",
            "platform_api_calls": 0,
            "platform_api_cost_usd": 0.0,
            "model_id": spec["model_id"],
            "extractor_version": TRACE_EXTRACTOR_VERSION,
            "assumption_monitor_contract_version": (
                ASSUMPTION_MONITOR_CONTRACT_VERSION
            ),
            "total_case_count": len(meeting_ids),
            "completed_case_count": len(cases),
            "new_case_count_this_run": new_cases,
            "pending_case_count": len(meeting_ids) - len(cases),
            "failure_type": type(error).__name__,
            "failure_message": str(error),
            "usage": aggregate_trace_reports(reports),
            "cases": cases,
            **active_failure,
        }
        _write_status(status_path, failed)
        raise
    status = {
        "schema_version": "decision_trace_subscription_batch_v1",
        "status": "COMPLETED" if len(cases) == len(meeting_ids) else "PARTIAL",
        "updated_at": _utc_now(),
        "execution_provider": "codex_subscription",
        "billing_route": "chatgpt_subscription",
        "platform_api_calls": 0,
        "platform_api_cost_usd": 0.0,
        "model_id": spec["model_id"],
        "extractor_version": TRACE_EXTRACTOR_VERSION,
        "assumption_monitor_contract_version": ASSUMPTION_MONITOR_CONTRACT_VERSION,
        "total_case_count": len(meeting_ids),
        "completed_case_count": len(cases),
        "new_case_count_this_run": new_cases,
        "pending_case_count": len(meeting_ids) - len(cases),
        "usage": aggregate_trace_reports(reports),
        "cases": cases,
    }
    _write_status(status_path, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or run the resumable 50-meeting DecisionTrace batch."
    )
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    parser.add_argument(
        "--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY
    )
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--max-new-cases", type=int)
    parser.add_argument("--confirmation")
    args = parser.parse_args()
    if args.preflight_only:
        report = build_trace_bundle_preflight(
            source_database=args.source,
            app_database=args.app,
            output_directory=args.output_directory,
        )
    else:
        if args.confirmation != SUBSCRIPTION_CONFIRMATION:
            raise ValueError(
                "Subscription trace batch requires --confirmation "
                + SUBSCRIPTION_CONFIRMATION
            )
        executor = CodexSubscriptionExecutor()
        executor.verify_authentication()
        report = run_trace_subscription_batch(
            executor,
            source_database=args.source,
            app_database=args.app,
            spec=load_model_spec(args.spec),
            output_directory=args.output_directory,
            max_new_cases=args.max_new_cases,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
