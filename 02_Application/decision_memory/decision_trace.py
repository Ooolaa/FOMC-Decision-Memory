from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from decision_memory.fed_documents import extract_html_paragraphs


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "decision_trace_v1.json"
SOURCE_BOUNDARY = "post_meeting_label_only_not_case_input"

PRICE_INDEX_SERIES_IDS = frozenset(
    {"CPIAUCSL", "CPILFESL", "PCEPI", "PCEPILFE"}
)
NONNEGATIVE_LEVEL_UNIT_PREFIXES = (
    "billions",
    "dollars",
    "millions",
    "number",
    "thousands",
)


def assumption_monitor_violations(
    assumption: dict[str, Any],
    series_metadata: dict[str, dict[str, Any]],
) -> list[str]:
    """Return deterministic semantic defects in a one-clause monitor rule."""
    series_id = str(assumption["monitor_series_id"])
    metadata = series_metadata.get(series_id)
    if metadata is None:
        return ["monitor_series_metadata_is_missing"]

    claim = _normalized_text(str(assumption["claim"])).casefold()
    operator = str(assumption["monitor_operator"]).upper()
    threshold = float(assumption["threshold_value"])
    rule = str(assumption["monitor_rule_version"]).casefold()
    units = str(metadata.get("units") or "").strip().casefold()
    violations: list[str] = []

    if series_id in PRICE_INDEX_SERIES_IDS and "yoy" not in rule:
        violations.append("index_percent_threshold_requires_yoy_transform")

    if (
        units.startswith(NONNEGATIVE_LEVEL_UNIT_PREFIXES)
        and operator in {"GT", "GTE"}
        and threshold <= 0
    ):
        violations.append("nonnegative_level_threshold_is_tautological")

    numeric_interval = re.search(
        r"\b\d+(?:\.\d+)?\s*(?:%|percent)?\s*"
        r"(?:-|\N{EN DASH}|\N{EM DASH}|to)\s*"
        r"\d+(?:\.\d+)?\s*(?:%|percent)?\b",
        claim,
    )
    between_interval = re.search(
        r"\bbetween\s+\d+(?:\.\d+)?\s*(?:%|percent)?\s+and\s+"
        r"\d+(?:\.\d+)?",
        claim,
    )
    target_band = re.search(
        r"\b(?:around|symmetric(?:ally)?)\b.{0,30}\b\d+(?:\.\d+)?",
        claim,
    )
    if numeric_interval or between_interval or target_band:
        violations.append("range_or_symmetric_claim_requires_atomic_rewrite")

    conditional = re.search(
        r"\b(?:conditional on|provided that|subject to)\b", claim
    )
    inflation_concept = re.search(
        r"\b(?:inflation|price(?:s)?|pce|cpi|expectations?)\b", claim
    )
    labor_concept = re.search(
        r"\b(?:unemployment|employment|labor|payrolls?|jobs?)\b", claim
    )
    cross_series_claim = bool(
        (series_id in {"UNRATE", "PAYEMS", "ICSA"} and inflation_concept)
        or (series_id in PRICE_INDEX_SERIES_IDS | {"T10YIE", "T5YIFR"} and labor_concept)
    )
    if conditional or cross_series_claim:
        violations.append("compound_claim_requires_atomic_rewrite")

    temporal_path = re.search(
        r"\b(?:peak\w*|rise\w*|increase\w*)\b.{0,80}"
        r"\b(?:then|followed by)\b"
        r"|\bthen\b.{0,40}\b(?:declin\w*|fall\w*|improv\w*|decreas\w*)\b"
        r"|\bfollowed by\b"
        r"|\bcontinue(?:s|d)? to\s+(?:improve|deteriorate|decline|fall|rise|increase|decrease)\b",
        claim,
    )
    if temporal_path:
        violations.append("temporal_path_requires_atomic_rewrite")

    return violations


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _iter_evidence_refs(payload: dict[str, Any]) -> Iterable[dict[str, str]]:
    yield from payload["context"]["evidence_refs"]
    for option in payload["options"]:
        yield from option["evidence_refs"]
    for debate_item in payload["debate"]:
        yield from debate_item["evidence_refs"]
    yield from payload["decision"]["evidence_refs"]
    for round_data in payload["vote"]["rounds"]:
        yield from round_data["evidence_refs"]
    for assumption in payload["assumptions"]:
        yield from assumption["evidence_refs"]


def _validate_schema(payload: dict[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(_load_schema()).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        raise ValueError(f"DecisionTrace schema violation at {path}: {error.message}")


def _validated_document_text(
    connection: sqlite3.Connection,
    document_id: str,
    meeting_id: str,
    locator_text: str,
    cache: dict[tuple[str, str], str],
) -> str:
    cache_key = (document_id, locator_text)
    if cache_key in cache:
        return cache[cache_key]
    row = connection.execute(
        """
        SELECT meeting_id, document_type, usage_class, source_locator, content_hash
        FROM document_source WHERE document_id = ?
        """,
        (document_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown evidence document_id: {document_id}")
    document_meeting_id, document_type, usage_class, source_locator, content_hash = row
    if document_meeting_id != meeting_id:
        raise ValueError(
            f"Evidence document belongs to another meeting: {document_id}"
        )
    transcript_evidence = (
        document_type == "transcript" and usage_class == "persona_evidence"
    )
    if usage_class not in {"label_only", "evaluation_only"} and not transcript_evidence:
        raise ValueError(
            "DecisionTrace extraction evidence must be post-meeting label/evaluation "
            f"material: {document_id}"
        )
    locator = json.loads(source_locator)
    local_path = Path(locator["local_path"])
    if not local_path.is_file():
        raise FileNotFoundError(f"Evidence cache file is missing: {local_path}")
    content = local_path.read_bytes()
    if hashlib.sha256(content).hexdigest() != content_hash:
        raise ValueError(f"Evidence content hash mismatch: {document_id}")

    if transcript_evidence:
        match = re.fullmatch(r"transcript segment ([0-9]+)", locator_text.strip())
        if match is None:
            raise ValueError(
                "Transcript evidence locator must be 'transcript segment <ordinal>'"
            )
        segment = connection.execute(
            """
            SELECT text, content_hash
            FROM transcript_segment
            WHERE document_id = ? AND meeting_id = ? AND ordinal = ?
            """,
            (document_id, meeting_id, int(match.group(1))),
        ).fetchone()
        if segment is None:
            raise ValueError(
                f"Unknown transcript evidence locator for document: {locator_text}"
            )
        if hashlib.sha256(segment[0].encode("utf-8")).hexdigest() != segment[1]:
            raise ValueError(
                f"Transcript segment content hash mismatch: {document_id} {locator_text}"
            )
        text = _normalized_text(segment[0])
    else:
        match = re.fullmatch(r"paragraph ([0-9]+)", locator_text.strip())
        if match is None:
            raise ValueError(
                "Document evidence locator must be 'paragraph <ordinal>'"
            )
        paragraphs = extract_html_paragraphs(content)
        ordinal = int(match.group(1))
        if ordinal < 1 or ordinal > len(paragraphs):
            raise ValueError(
                f"Unknown document evidence locator: {document_id} {locator_text}"
            )
        text = _normalized_text(paragraphs[ordinal - 1])
    cache[cache_key] = text
    return text


def validate_fomc_decision_trace(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    allowed_monitor_series_ids: set[str],
    monitor_series_metadata: dict[str, dict[str, Any]],
) -> None:
    _validate_schema(payload)
    meeting_id = payload["meeting_id"]
    if payload["decision_id"] != f"fomc-{meeting_id}":
        raise ValueError("decision_id must be fomc-<meeting_id>")
    if not allowed_monitor_series_ids:
        raise ValueError("allowed_monitor_series_ids must not be empty")
    missing_metadata = sorted(
        allowed_monitor_series_ids - set(monitor_series_metadata)
    )
    if missing_metadata:
        raise ValueError(
            "Monitor series metadata is missing for allowlisted series: "
            + ", ".join(missing_metadata)
        )

    option_ids = [item["option_id"] for item in payload["options"]]
    if len(option_ids) != len(set(option_ids)):
        raise ValueError("DecisionTrace option_id values must be unique")
    assumption_ids = [item["assumption_id"] for item in payload["assumptions"]]
    if len(assumption_ids) != len(set(assumption_ids)):
        raise ValueError("DecisionTrace assumption_id values must be unique")

    document_text_cache: dict[tuple[str, str], str] = {}
    for reference in _iter_evidence_refs(payload):
        document_text = _validated_document_text(
            connection,
            reference["document_id"],
            meeting_id,
            reference["locator"],
            document_text_cache,
        )
        excerpt = _normalized_text(reference["excerpt"])
        if excerpt not in document_text:
            raise ValueError(
                "Evidence excerpt not found in official document at "
                f"{reference['document_id']} {reference['locator']}: "
                + json.dumps(reference["excerpt"], ensure_ascii=False)
            )

    for item in payload["debate"]:
        participant_id = item["participant_id"]
        if item["speaker_scope"] == "committee":
            if participant_id is not None:
                raise ValueError(
                    "Committee-level minutes text cannot be attributed to a participant"
                )
            continue
        if not participant_id:
            raise ValueError("participant speaker_scope requires participant_id")
        participant = connection.execute(
            """
            SELECT 1 FROM meeting_participant
            WHERE meeting_id = ? AND participant_id = ?
            """,
            (meeting_id, participant_id),
        ).fetchone()
        if participant is None:
            raise ValueError(
                f"Debate participant is not rostered for meeting: {participant_id}"
            )
        matching_transcript = False
        for reference in item["evidence_refs"]:
            document = connection.execute(
                """
                SELECT document_type
                FROM document_source
                WHERE document_id = ? AND meeting_id = ?
                """,
                (reference["document_id"], meeting_id),
            ).fetchone()
            if document is None or document[0] != "transcript":
                continue
            match = re.fullmatch(
                r"transcript segment ([0-9]+)", reference["locator"].strip()
            )
            if match is None:
                continue
            segment = connection.execute(
                """
                SELECT participant_id
                FROM transcript_segment
                WHERE document_id = ? AND meeting_id = ? AND ordinal = ?
                """,
                (
                    reference["document_id"],
                    meeting_id,
                    int(match.group(1)),
                ),
            ).fetchone()
            if segment is None:
                continue
            if segment[0] != participant_id:
                raise ValueError(
                    "Participant debate transcript evidence belongs to another "
                    f"participant: expected={participant_id}, actual={segment[0]}, "
                    f"locator={reference['locator']}"
                )
            matching_transcript = True
        if not matching_transcript:
            raise ValueError(
                "Participant debate requires at least one matching transcript "
                f"segment: {participant_id}"
            )

    actual_outcome = connection.execute(
        """
        SELECT action_class, target_rate, target_lower, target_upper
        FROM meeting_outcome WHERE meeting_id = ?
        """,
        (meeting_id,),
    ).fetchone()
    if actual_outcome is None:
        raise ValueError(f"Meeting outcome is required before DecisionTrace: {meeting_id}")
    reported_outcome = payload["decision"]
    if actual_outcome != (
        reported_outcome["action_class"],
        reported_outcome["target_rate"],
        reported_outcome["target_lower"],
        reported_outcome["target_upper"],
    ):
        raise ValueError("DecisionTrace decision conflicts with meeting_outcome label")

    actual_vote_rows = connection.execute(
        """
        SELECT vote_round,
               SUM(CASE WHEN voter_choice = 'FOR' THEN 1 ELSE 0 END),
               SUM(CASE WHEN voter_choice = 'AGAINST' THEN 1 ELSE 0 END)
        FROM participant_vote
        WHERE meeting_id = ?
        GROUP BY vote_round
        ORDER BY vote_round
        """,
        (meeting_id,),
    ).fetchall()
    reported_vote_rows = [
        (
            round_data["vote_round"],
            round_data["for_count"],
            round_data["against_count"],
        )
        for round_data in payload["vote"]["rounds"]
    ]
    if actual_vote_rows != reported_vote_rows:
        raise ValueError("DecisionTrace vote counts conflict with participant_vote labels")
    if any(round_data["abstain_count"] != 0 for round_data in payload["vote"]["rounds"]):
        raise ValueError("participant_vote has no abstention label; abstain_count must be 0")

    for assumption in payload["assumptions"]:
        if assumption["monitor_series_id"] not in allowed_monitor_series_ids:
            raise ValueError(
                "Assumption monitor series is not in the pre-registered allowlist: "
                f"{assumption['monitor_series_id']}"
            )
        violations = assumption_monitor_violations(
            assumption,
            monitor_series_metadata,
        )
        if violations:
            raise ValueError(
                "Assumption monitor semantic violation: "
                f"assumption_id={assumption['assumption_id']}; "
                + ", ".join(violations)
            )


def persist_fomc_decision_trace(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    extractor_version: str,
    allowed_monitor_series_ids: set[str],
    monitor_series_metadata: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not extractor_version.strip():
        raise ValueError("extractor_version is required")
    validate_fomc_decision_trace(
        connection,
        payload,
        allowed_monitor_series_ids=allowed_monitor_series_ids,
        monitor_series_metadata=monitor_series_metadata,
    )
    decision_id = payload["decision_id"]
    meeting_id = payload["meeting_id"]
    context = {
        "meeting_id": meeting_id,
        "summary": payload["context"]["summary"],
        "evidence_refs": payload["context"]["evidence_refs"],
        "source_boundary": SOURCE_BOUNDARY,
    }
    expected_case = (
        "fomc",
        f"FOMC decision replay: {meeting_id}",
        0,
        0,
        _canonical_json(context),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO decision_case (
            decision_id, domain, title, synthetic, composite,
            context_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (decision_id, *expected_case, _utc_now()),
    )
    persisted_case = connection.execute(
        """
        SELECT domain, title, synthetic, composite, context_json
        FROM decision_case WHERE decision_id = ?
        """,
        (decision_id,),
    ).fetchone()
    if persisted_case != expected_case:
        raise RuntimeError(f"Existing decision_case conflicts with trace: {decision_id}")

    hash_input = _canonical_json(
        {"payload": payload, "extractor_version": extractor_version}
    ).encode("utf-8")
    trace_id = f"trace-{hashlib.sha256(hash_input).hexdigest()[:24]}"
    expected_trace = (
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
        (trace_id, *expected_trace, _utc_now()),
    )
    persisted_trace = connection.execute(
        """
        SELECT decision_id, options_json, debate_json, decision_json,
               vote_json, extractor_version
        FROM decision_trace WHERE decision_id = ?
        """,
        (decision_id,),
    ).fetchone()
    if persisted_trace != expected_trace:
        raise RuntimeError(f"Existing DecisionTrace conflicts: {decision_id}")

    for assumption in payload["assumptions"]:
        expected_assumption = (
            decision_id,
            assumption["claim"],
            assumption["monitor_series_id"],
            assumption["monitor_operator"],
            assumption["threshold_value"],
            assumption["direction_map_version"],
            assumption["monitor_rule_version"],
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO decision_assumption (
                assumption_id, decision_id, claim, monitor_series_id,
                monitor_operator, threshold_value, direction_map_version,
                monitor_rule_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assumption["assumption_id"],
                *expected_assumption,
                _utc_now(),
            ),
        )
        persisted_assumption = connection.execute(
            """
            SELECT decision_id, claim, monitor_series_id, monitor_operator,
                   threshold_value, direction_map_version, monitor_rule_version
            FROM decision_assumption WHERE assumption_id = ?
            """,
            (assumption["assumption_id"],),
        ).fetchone()
        if persisted_assumption != expected_assumption:
            raise RuntimeError(
                "Existing decision_assumption conflicts with trace: "
                f"{assumption['assumption_id']}"
            )

    return {
        "decision_id": decision_id,
        "trace_id": trace_id,
        "assumption_count": len(payload["assumptions"]),
        "evidence_reference_count": sum(1 for _ in _iter_evidence_refs(payload)),
        "source_boundary": SOURCE_BOUNDARY,
    }
