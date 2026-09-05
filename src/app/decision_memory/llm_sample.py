from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from decision_memory.fed_documents import extract_html_paragraphs
from decision_memory.model_preflight import (
    DEFAULT_SPEC_PATH,
    load_model_spec,
    load_user_scope_openai_key,
    run_model_preflight,
)
from decision_memory.offline_simulator import validate_simulation_output
from decision_memory.simulation_evaluation import evaluate_simulation_output


ROOT = Path(__file__).resolve().parents[1]
STAGE_SCHEMA_PATH = ROOT / "schemas" / "simulation_stage_envelope_v1.json"
STAGES = ("profiles", "openings", "options", "chair", "votes")
STAGE_REASONING_EFFORT = {
    "profiles": "medium",
    "openings": "high",
    "options": "high",
    "chair": "high",
    "votes": "medium",
}
PAID_CONFIRMATION = "RUN_GPT_5_6_TERRA_PAID_SAMPLE"
PAID_CONFIRMATIONS = {
    "gpt-5.6-terra": PAID_CONFIRMATION,
    "gpt-5.6-luna": "RUN_GPT_5_6_LUNA_COMPARISON_SAMPLE",
}
DEFAULT_MAX_OUTPUT_TOKENS = 4_000


def paid_confirmation_for_model(model_id: str) -> str:
    try:
        return PAID_CONFIRMATIONS[model_id]
    except KeyError as error:
        raise ValueError(f"No approved paid sample model: {model_id}") from error


def _runtime_stage_schema(bundle: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    document_ids = [str(item["document_id"]) for item in bundle["documents"]]
    if not document_ids or len(document_ids) != len(set(document_ids)):
        raise ValueError("Case documents must have unique document_id values")
    schema = json.loads(STAGE_SCHEMA_PATH.read_text(encoding="utf-8"))
    for field in ("profiles", "openings", "options"):
        schema["properties"][field]["items"]["properties"]["evidence_ids"][
            "items"
        ]["enum"] = document_ids
    return schema, document_ids


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _read(value: Any, key: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def build_case_bundle(
    source_database: Path,
    app_database: Path,
    *,
    meeting_id: str,
    document_count: int = 5,
) -> dict[str, Any]:
    source_path = source_database.resolve()
    app_path = app_database.resolve()
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    app = sqlite3.connect(f"file:{app_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    app.row_factory = sqlite3.Row
    try:
        meeting = source.execute(
            """
            SELECT meeting_start_date, meeting_end_date, information_cutoff_date_et
            FROM fomc_meeting WHERE meeting_id = ?
            """,
            (meeting_id,),
        ).fetchone()
        if meeting is None:
            raise ValueError(f"Unknown meeting: {meeting_id}")
        cutoff = meeting["information_cutoff_date_et"]
        snapshots = [
            dict(row)
            for row in source.execute(
                """
                SELECT snapshot.series_id, snapshot.observation_date,
                       snapshot.realtime_start, vintage.value_num
                FROM meeting_snapshot_value AS snapshot
                JOIN observation_vintage AS vintage
                  ON vintage.series_id = snapshot.series_id
                 AND vintage.observation_date = snapshot.observation_date
                 AND vintage.realtime_start = snapshot.realtime_start
                WHERE snapshot.meeting_id = ?
                  AND snapshot.series_id NOT IN ('DFEDTAR', 'DFEDTARL', 'DFEDTARU')
                ORDER BY snapshot.series_id, snapshot.observation_date
                """,
                (meeting_id,),
            )
        ]
        if not snapshots:
            raise RuntimeError(f"No economic snapshot rows for {meeting_id}")
        leaked = [
            row
            for row in snapshots
            if row["observation_date"] > cutoff or row["realtime_start"] > cutoff
        ]
        if leaked:
            raise RuntimeError(f"Cutoff leakage in case snapshot: {leaked[:3]}")

        participants = [
            dict(row)
            for row in app.execute(
                """
                SELECT participant.participant_id, participant.display_name,
                       meeting_participant.is_chair
                FROM meeting_participant
                JOIN participant USING (participant_id)
                WHERE meeting_participant.meeting_id = ?
                  AND meeting_participant.is_voter = 1
                ORDER BY meeting_participant.is_chair DESC, participant.display_name
                """,
                (meeting_id,),
            )
        ]
        if not participants or sum(row["is_chair"] for row in participants) != 1:
            raise RuntimeError("Sample requires voters and exactly one Chair")
        for participant in participants:
            participant["is_chair"] = bool(participant["is_chair"])

        policy_context = [
            dict(row)
            for row in app.execute(
                """
                SELECT ordinal, record_kind, cutoff_date, effective_date, regime,
                       direction, target_rate, lower_rate, upper_rate,
                       regime_started_at, regime_duration_days,
                       source_series_ids_json, rule_version, source_hash
                FROM policy_rate_context WHERE meeting_id = ?
                ORDER BY ordinal
                """,
                (meeting_id,),
            )
        ]
        document_rows = app.execute(
            """
            SELECT document_id, meeting_id, document_type, publication_at,
                   source_locator, content_hash
            FROM document_source
            WHERE substr(publication_at, 1, 10) <= ?
              AND meeting_id <> ?
              AND document_type IN ('statement', 'minutes')
            ORDER BY publication_at DESC, document_type
            LIMIT ?
            """,
            (cutoff, meeting_id, document_count),
        ).fetchall()
    finally:
        app.close()
        source.close()

    if len(document_rows) != document_count:
        raise RuntimeError(
            f"Expected {document_count} pre-cutoff documents, got {len(document_rows)}"
        )
    documents = []
    for row in document_rows:
        locator = json.loads(row["source_locator"])
        local_path = Path(locator["local_path"])
        actual_hash = _sha256_file(local_path)
        if actual_hash != row["content_hash"]:
            raise RuntimeError(f"Document hash mismatch: {local_path}")
        text = "\n".join(extract_html_paragraphs(local_path.read_bytes()))
        documents.append(
            {
                "document_id": row["document_id"],
                "meeting_id": row["meeting_id"],
                "document_type": row["document_type"],
                "publication_at": row["publication_at"],
                "content_hash": row["content_hash"],
                "source_url": locator["source_url"],
                "text": text,
            }
        )

    bundle = {
        "schema_version": "llm_case_bundle_v1",
        "meeting_id": meeting_id,
        "meeting_start_date": meeting["meeting_start_date"],
        "meeting_end_date": meeting["meeting_end_date"],
        "information_cutoff_date_et": cutoff,
        "synthetic_output_required": True,
        "label_exclusion": (
            "Current-meeting statement, minutes, outcome and votes are not model input. "
            "The participant roster is evaluation setup metadata."
        ),
        "source_database_sha256": _sha256_file(source_path),
        "participants": participants,
        "policy_rate_context": policy_context,
        "economic_snapshot": snapshots,
        "documents": documents,
    }
    bundle["bundle_hash"] = _canonical_hash(bundle)
    return bundle


def render_stable_prefix(bundle: dict[str, Any]) -> str:
    return (
        "You are running a synthetic FOMC decision simulation. Every generated "
        "utterance is synthetic, never a historical quote. Use only the supplied "
        "point-in-time economic snapshot and documents published by the cutoff. "
        "Cite only supplied document_id values. The Chair alone selects the final "
        "proposal. Do not infer or reveal the held-out outcome or vote labels.\n\n"
        "CASE_BUNDLE_JSON:\n"
        + json.dumps(bundle, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )


def _empty_envelope(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "profiles": [],
        "openings": [],
        "options": [],
        "final_proposal": None,
        "votes": [],
    }


def _stage_instruction(stage: str, prior: dict[str, Any]) -> str:
    rules = {
        "profiles": (
            "Return one cautious synthetic profile for every supplied participant. "
            "Use persona_evidence and reaction_profile_cards when supplied; pooled "
            "reaction coefficients are not participant-specific estimates."
        ),
        "openings": (
            "Return exactly one synthetic opening position per profile, using any "
            "supplied persona_evidence and reaction_profile_cards only as bounded inputs."
        ),
        "options": "Return exactly three options: one CUT, one HOLD and one HIKE.",
        "chair": "The supplied Chair must select one option and state the rationale.",
        "votes": (
            "The participant roster is known input, not a prediction target. "
            "Use the Chair proposal plus any cutoff-safe recent vote history and "
            "reaction profile cards to predict each voter's FOR or AGAINST choice "
            "exactly once. Do not infer or score attendance."
        ),
    }
    return (
        f"STAGE={stage}. {rules[stage]} Populate only the field for this stage; "
        "all other arrays must be empty and final_proposal must be null except "
        "during chair. PRIOR_STAGE_OUTPUTS="
        + json.dumps(prior, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )


def semantic_violations(
    stage: str,
    envelope: dict[str, Any],
    bundle: dict[str, Any],
    prior: dict[str, Any],
) -> list[str]:
    violations = []
    if envelope.get("stage") != stage:
        violations.append(f"stage must equal {stage}")
    active_field = {
        "profiles": "profiles",
        "openings": "openings",
        "options": "options",
        "chair": "final_proposal",
        "votes": "votes",
    }[stage]
    for field in ("profiles", "openings", "options", "votes"):
        values = envelope.get(field, [])
        if field == active_field and not values:
            violations.append(f"{field} must be non-empty")
        if field != active_field and values:
            violations.append(f"{field} must be empty during {stage}")
    proposal = envelope.get("final_proposal")
    if stage == "chair" and proposal is None:
        violations.append("final_proposal is required during chair")
    if stage != "chair" and proposal is not None:
        violations.append(f"final_proposal must be null during {stage}")

    expected_ids = {item["participant_id"] for item in bundle["participants"]}
    chair_id = next(
        item["participant_id"] for item in bundle["participants"] if item["is_chair"]
    )
    document_ids = {item["document_id"] for item in bundle["documents"]}
    if stage == "profiles":
        actual_ids = {item["participant_id"] for item in envelope["profiles"]}
        if actual_ids != expected_ids:
            violations.append("profiles must cover the supplied participants exactly")
        chairs = [item for item in envelope["profiles"] if item["is_chair"]]
        if len(chairs) != 1 or chairs[0]["participant_id"] != chair_id:
            violations.append("profiles must preserve exactly the supplied Chair")
        for item in envelope["profiles"]:
            if len(item["evidence_ids"]) != len(set(item["evidence_ids"])):
                violations.append("profile evidence_ids must be unique")
            if not set(item["evidence_ids"]).issubset(document_ids):
                violations.append("profile evidence_ids must resolve to supplied documents")
    elif stage == "openings":
        actual_ids = {item["participant_id"] for item in envelope["openings"]}
        if actual_ids != expected_ids:
            violations.append("openings must cover the supplied participants exactly")
        for item in envelope["openings"]:
            if len(item["evidence_ids"]) != len(set(item["evidence_ids"])):
                violations.append("opening evidence_ids must be unique")
            if not set(item["evidence_ids"]).issubset(document_ids):
                violations.append("opening evidence_ids must resolve to supplied documents")
    elif stage == "options":
        actions = [item["action_class"] for item in envelope["options"]]
        if len(actions) != 3 or set(actions) != {"CUT", "HOLD", "HIKE"}:
            violations.append("options must contain exactly CUT, HOLD and HIKE")
        for item in envelope["options"]:
            if len(item["evidence_ids"]) != len(set(item["evidence_ids"])):
                violations.append("option evidence_ids must be unique")
            if not set(item["evidence_ids"]).issubset(document_ids):
                violations.append("option evidence_ids must resolve to supplied documents")
    elif stage == "chair" and proposal is not None:
        if proposal["proposer_participant_id"] != chair_id:
            violations.append("only the supplied Chair may propose")
        available = {item["action_class"] for item in prior["options"]["options"]}
        if proposal["action_class"] not in available:
            violations.append("Chair proposal must select a supplied option")
    elif stage == "votes":
        actual_ids = {item["participant_id"] for item in envelope["votes"]}
        if actual_ids != expected_ids or len(envelope["votes"]) != len(expected_ids):
            violations.append("votes must cover each supplied participant exactly once")
    return sorted(set(violations))


def _usage_record(response: Any, *, stage: str, attempt: int, latency: float) -> dict:
    usage = _read(response, "usage", {})
    input_details = _read(usage, "input_tokens_details", {})
    output_details = _read(usage, "output_tokens_details", {})
    return {
        "stage": stage,
        "attempt": attempt,
        "response_id": _read(response, "id"),
        "service_tier": _read(response, "service_tier"),
        "input_tokens": int(_read(usage, "input_tokens", 0) or 0),
        "cached_input_tokens": int(_read(input_details, "cached_tokens", 0) or 0),
        "cache_write_tokens": int(_read(input_details, "cache_write_tokens", 0) or 0),
        "output_tokens": int(_read(usage, "output_tokens", 0) or 0),
        "reasoning_tokens": int(_read(output_details, "reasoning_tokens", 0) or 0),
        "total_tokens": int(_read(usage, "total_tokens", 0) or 0),
        "latency_seconds": round(latency, 6),
    }


def usage_cost_usd(record: dict[str, Any], spec: dict[str, Any]) -> float:
    prices = (
        spec["long_context_pricing_usd_per_million"]
        if record["input_tokens"] > spec["context"]["long_context_threshold_tokens"]
        else spec["pricing_usd_per_million"]
    )
    cached = record["cached_input_tokens"]
    cache_write = record["cache_write_tokens"]
    uncached = max(0, record["input_tokens"] - cached - cache_write)
    return round(
        (
            uncached * prices["input"]
            + cached * prices["cached_input"]
            + cache_write * prices["cache_write"]
            + record["output_tokens"] * prices["output"]
        )
        / 1_000_000,
        8,
    )


def _request_max_cost(
    input_text: str,
    dynamic_text: str,
    spec: dict[str, Any],
    max_output_tokens: int,
) -> float:
    tokens = _estimate_tokens(input_text + dynamic_text)
    return _request_cost_upper_bound(tokens, spec, max_output_tokens)


def _request_cost_upper_bound(
    input_tokens: int,
    spec: dict[str, Any],
    max_output_tokens: int,
) -> float:
    prices = (
        spec["long_context_pricing_usd_per_million"]
        if input_tokens > spec["context"]["long_context_threshold_tokens"]
        else spec["pricing_usd_per_million"]
    )
    input_rate = max(prices["input"], prices["cache_write"])
    return (
        input_tokens * input_rate + max_output_tokens * prices["output"]
    ) / 1_000_000


def dry_run_cost_envelope(
    bundle: dict[str, Any],
    spec: dict[str, Any],
    *,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    stable = render_stable_prefix(bundle)
    prior = {stage: _empty_envelope(stage) for stage in STAGES}
    dynamic = [_stage_instruction(stage, prior) for stage in STAGES]
    conservative = sum(
        _request_max_cost(stable, text, spec, max_output_tokens) for text in dynamic
    )
    stable_tokens = _estimate_tokens(stable)
    first_prices = (
        spec["long_context_pricing_usd_per_million"]
        if stable_tokens > spec["context"]["long_context_threshold_tokens"]
        else spec["pricing_usd_per_million"]
    )
    theoretical = stable_tokens * first_prices["cache_write"]
    theoretical += stable_tokens * first_prices["cached_input"] * (len(STAGES) - 1)
    theoretical += sum(
        _estimate_tokens(text) * first_prices["input"] for text in dynamic
    )
    theoretical += len(STAGES) * max_output_tokens * first_prices["output"]
    return {
        "status": "DRY_RUN_NO_API_CALL",
        "meeting_id": bundle["meeting_id"],
        "document_count": len(bundle["documents"]),
        "snapshot_row_count": len(bundle["economic_snapshot"]),
        "stable_prefix_utf8_bytes": len(stable.encode("utf-8")),
        "estimated_stable_prefix_tokens_chars_over_4": stable_tokens,
        "max_output_tokens_per_stage": max_output_tokens,
        "theoretical_cached_cost_usd": round(theoretical / 1_000_000, 4),
        "conservative_all_cache_write_cost_usd": round(conservative, 4),
        "estimate_is_not_actual_usage": True,
    }


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def run_paid_sample(
    client: Any,
    bundle: dict[str, Any],
    spec: dict[str, Any],
    *,
    max_cost_usd: float,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    if max_cost_usd <= 0:
        raise ValueError("max_cost_usd must be positive")
    schema, allowed_evidence_ids = _runtime_stage_schema(bundle)
    validator = Draft202012Validator(schema)
    stable = render_stable_prefix(bundle)
    stable_tokens_estimate = _estimate_tokens(stable)
    prior: dict[str, Any] = {}
    usage_records = []
    spent = 0.0
    reserved_upper_bound = 0.0

    for stage in STAGES:
        dynamic = _stage_instruction(stage, prior)
        for attempt in (1, 2):
            request_reasoning = {
                "effort": STAGE_REASONING_EFFORT[stage],
                "context": "current_turn",
            }
            request_input = [
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": stable}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": dynamic}],
                },
            ]
            request_text = {
                "format": {
                    "type": "json_schema",
                    "name": "simulation_stage_envelope_v1",
                    "strict": True,
                    "schema": schema,
                }
            }
            token_count = client.responses.input_tokens.count(
                model=spec["model_id"],
                reasoning=request_reasoning,
                input=request_input,
                text=request_text,
                truncation="disabled",
            )
            exact_input_tokens = int(_read(token_count, "input_tokens", 0) or 0)
            if exact_input_tokens <= 0:
                raise RuntimeError(f"Input-token count failed for {stage} attempt {attempt}")
            worst_next = _request_cost_upper_bound(
                exact_input_tokens,
                spec,
                max_output_tokens,
            )
            if reserved_upper_bound + worst_next > max_cost_usd:
                raise RuntimeError(
                    f"Hard cost cap blocks {stage} attempt {attempt}: "
                    f"spent={spent:.4f}, reserved={reserved_upper_bound:.4f}, "
                    f"conservative_next={worst_next:.4f}, "
                    f"cap={max_cost_usd:.4f}"
                )
            started = time.perf_counter()
            response = client.responses.create(
                model=spec["model_id"],
                service_tier="default",
                store=False,
                max_output_tokens=max_output_tokens,
                reasoning=request_reasoning,
                prompt_cache_key=bundle["bundle_hash"][:64],
                extra_body={
                    "prompt_cache_options": {"mode": "implicit", "ttl": "30m"}
                },
                input=request_input,
                text=request_text,
                truncation="disabled",
            )
            latency = time.perf_counter() - started
            if _read(response, "status") != "completed":
                raise RuntimeError(
                    f"Schema-layer response failed for {stage}: "
                    f"status={_read(response, 'status')}"
                )
            output_text = _read(response, "output_text")
            if not output_text:
                raise RuntimeError(f"Schema-layer response has no output_text: {stage}")
            try:
                envelope = json.loads(output_text)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"Schema-layer JSON failure for {stage}") from error
            schema_errors = sorted(validator.iter_errors(envelope), key=lambda item: list(item.path))
            if schema_errors:
                raise RuntimeError(
                    f"Schema-layer validation failure for {stage}: {schema_errors[0].message}"
                )
            record = _usage_record(response, stage=stage, attempt=attempt, latency=latency)
            record["preflight_input_tokens"] = exact_input_tokens
            record["reserved_cost_upper_bound_usd"] = round(worst_next, 8)
            record["cost_usd"] = usage_cost_usd(record, spec)
            spent += record["cost_usd"]
            reserved_upper_bound += worst_next
            if stage != "profiles" and attempt == 1:
                record["eligible_shared_prefix_tokens_estimate"] = stable_tokens_estimate
                record["shared_prefix_cache_rate"] = min(
                    1.0,
                    record["cached_input_tokens"] / stable_tokens_estimate,
                )
            usage_records.append(record)

            violations = semantic_violations(stage, envelope, bundle, prior)
            if not violations:
                prior[stage] = envelope
                break
            if attempt == 2:
                raise RuntimeError(
                    f"Semantic repair failed for {stage}: {'; '.join(violations)}"
                )
            dynamic = (
                _stage_instruction(stage, prior)
                + " REPAIR_THESE_SEMANTIC_VIOLATIONS="
                + json.dumps(violations, ensure_ascii=False)
                + " ALLOWED_EVIDENCE_IDS="
                + json.dumps(
                    allowed_evidence_ids,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + " INVALID_OUTPUT="
                + json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
            )

    final = {
        "schema_version": "simulation_output_v1",
        "meeting_id": bundle["meeting_id"],
        "synthetic": True,
        "profiles": prior["profiles"]["profiles"],
        "discussion": [
            {
                "participant_id": item["participant_id"],
                "synthetic_text": item["synthetic_text"],
            }
            for item in prior["openings"]["openings"]
        ],
        "final_proposal": prior["chair"]["final_proposal"],
        "votes": [
            {"participant_id": item["participant_id"], "choice": item["choice"]}
            for item in prior["votes"]["votes"]
        ],
    }
    semantic_report = validate_simulation_output(final)
    cache_rates = [
        record["shared_prefix_cache_rate"]
        for record in usage_records
        if "shared_prefix_cache_rate" in record
    ]
    cache_report = {
        "denominator": "eligible post-write calls only; profiles cache-write excluded",
        "eligible_call_count": len(cache_rates),
        "median": statistics.median(cache_rates) if cache_rates else None,
        "p10": _quantile(cache_rates, 0.10),
    }
    cache_report["gate_pass"] = bool(
        cache_report["median"] is not None
        and cache_report["median"] >= 0.95
        and cache_report["p10"] >= 0.80
    )
    return {
        "status": "PAID_SAMPLE_COMPLETED",
        "model_id": spec["model_id"],
        "meeting_id": bundle["meeting_id"],
        "bundle_hash": bundle["bundle_hash"],
        "stage_order": list(STAGES),
        "case_stage_affinity": "SEQUENTIAL_NO_INTERLEAVING",
        "semantic_validation": semantic_report,
        "cache_report": cache_report,
        "usage": usage_records,
        "total_cost_usd": round(spent, 8),
        "reserved_cost_upper_bound_usd": round(reserved_upper_bound, 8),
        "hard_cap_usd": max_cost_usd,
        "output": final,
    }


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def persist_paid_sample(
    app_database: Path,
    report: dict[str, Any],
) -> dict[str, Any]:
    if report.get("status") != "PAID_SAMPLE_COMPLETED":
        raise ValueError("Only a completed paid sample can be persisted")
    app_path = app_database.resolve()
    connection = sqlite3.connect(f"file:{app_path.as_posix()}?mode=rw", uri=True)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        evaluation = evaluate_simulation_output(connection, report["output"])
        case_id = f"paid-sample-{report['meeting_id']}"
        case_expected = (
            report["meeting_id"],
            None,
            report["bundle_hash"],
            1,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO simulation_case (
                case_id, meeting_id, decision_id, manifest_hash, synthetic, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (case_id, *case_expected, _utc_now()),
        )
        persisted_case = connection.execute(
            """
            SELECT meeting_id, decision_id, manifest_hash, synthetic
            FROM simulation_case WHERE case_id = ?
            """,
            (case_id,),
        ).fetchone()
        if persisted_case != case_expected:
            raise RuntimeError(f"Existing paid sample case conflicts: {case_id}")

        prompt_hash = _canonical_hash(
            {
                "runner": "five_stage_responses_v1",
                "bundle_hash": report["bundle_hash"],
                "stage_order": report["stage_order"],
            }
        )
        schema_hash = _sha256_file(STAGE_SCHEMA_PATH)
        run_identity = {
            "model_id": report["model_id"],
            "bundle_hash": report["bundle_hash"],
            "usage": report["usage"],
            "output": report["output"],
        }
        run_id = f"run-{_canonical_hash(run_identity)[:24]}"
        totals = {
            key: sum(int(item[key]) for item in report["usage"])
            for key in ("input_tokens", "cached_input_tokens", "output_tokens")
        }
        run_expected = (
            case_id,
            report["model_id"],
            prompt_hash,
            schema_hash,
            json.dumps(report["output"], ensure_ascii=False, sort_keys=True),
            totals["input_tokens"],
            totals["cached_input_tokens"],
            totals["output_tokens"],
            float(report["total_cost_usd"]),
            round(sum(item["latency_seconds"] for item in report["usage"]) * 1_000),
            1,
            "PAID_SAMPLE_COMPLETED",
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO simulation_run (
                run_id, case_id, model_id, prompt_hash, schema_hash, output_json,
                input_tokens, cached_tokens, output_tokens, cost_usd,
                latency_ms, synthetic, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, *run_expected, _utc_now()),
        )
        persisted_run = connection.execute(
            """
            SELECT case_id, model_id, prompt_hash, schema_hash, output_json,
                   input_tokens, cached_tokens, output_tokens, cost_usd,
                   latency_ms, synthetic, status
            FROM simulation_run WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if persisted_run != run_expected:
            raise RuntimeError(f"Existing paid sample run conflicts: {run_id}")

        metric_names = (
            "policy_accuracy",
            "policy_action_mae",
            "false_action_on_hold",
            "dissent_base_rate",
            "dissent_precision",
            "dissent_recall",
            "dissent_f1",
        )
        evaluation_ids = []
        for metric in metric_names:
            evaluation_id = (
                "eval-"
                + _canonical_hash(
                    {"run_id": run_id, "metric": metric, "version": evaluation["evaluator_version"]}
                )[:24]
            )
            expected = (
                case_id,
                "paid_five_document_sample",
                metric,
                None,
                float(evaluation[metric]),
                evaluation["evaluator_version"],
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO evaluation_result (
                    evaluation_id, case_id, experiment, metric,
                    baseline_score, candidate_score, evaluator_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (evaluation_id, *expected, _utc_now()),
            )
            persisted = connection.execute(
                """
                SELECT case_id, experiment, metric, baseline_score,
                       candidate_score, evaluator_version
                FROM evaluation_result WHERE evaluation_id = ?
                """,
                (evaluation_id,),
            ).fetchone()
            if persisted != expected:
                raise RuntimeError(f"Existing evaluation conflicts: {evaluation_id}")
            evaluation_ids.append(evaluation_id)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"Paid sample persistence failed: integrity={integrity}, "
                f"foreign_keys={foreign_key_errors}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "case_id": case_id,
        "run_id": run_id,
        "evaluation_ids": evaluation_ids,
        "evaluation": evaluation,
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.resolve()
    if resolved.exists():
        existing = json.loads(resolved.read_text(encoding="utf-8"))
        if existing == payload:
            return
        raise FileExistsError(f"Refusing to overwrite different artifact: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("x", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or execute the fail-closed five-document paid usage sample."
    )
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument("--meeting-id", default="FOMC-2022-03-15")
    parser.add_argument("--document-count", type=int, default=5)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    parser.add_argument(
        "--bundle-output",
        type=Path,
        default=Path("artifacts/llm_preflight/fomc_2022_03_15_case_bundle.json"),
    )
    parser.add_argument(
        "--run-output",
        type=Path,
        default=Path("artifacts/llm_preflight/fomc_2022_03_15_paid_sample.json"),
    )
    parser.add_argument("--execute-paid-sample", action="store_true")
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--confirmation")
    args = parser.parse_args()

    spec = load_model_spec(args.spec)
    bundle = build_case_bundle(
        args.source,
        args.app,
        meeting_id=args.meeting_id,
        document_count=args.document_count,
    )
    _write_new_json(args.bundle_output, bundle)
    if not args.execute_paid_sample:
        print(
            json.dumps(
                dry_run_cost_envelope(bundle, spec),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    required_confirmation = paid_confirmation_for_model(spec["model_id"])
    if args.confirmation != required_confirmation:
        raise RuntimeError(
            f"Paid sample requires --confirmation {required_confirmation}"
        )
    if args.max_cost_usd is None:
        raise RuntimeError("Paid sample requires --max-cost-usd")
    run_model_preflight(args.spec)
    from openai import OpenAI

    client = OpenAI(api_key=load_user_scope_openai_key(), timeout=180.0)
    report = run_paid_sample(
        client,
        bundle,
        spec,
        max_cost_usd=args.max_cost_usd,
    )
    report["persistence"] = persist_paid_sample(args.app, report)
    _write_new_json(args.run_output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
