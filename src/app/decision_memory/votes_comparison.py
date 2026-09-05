from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from decision_memory.llm_sample import (
    STAGE_REASONING_EFFORT,
    _read,
    _request_cost_upper_bound,
    _runtime_stage_schema,
    _usage_record,
    _write_new_json,
    render_stable_prefix,
    semantic_violations,
    usage_cost_usd,
)
from decision_memory.model_preflight import (
    DEFAULT_SPEC_PATH,
    load_model_spec,
    load_user_scope_openai_key,
    run_model_preflight,
)
from decision_memory.offline_simulator import validate_simulation_output
from decision_memory.simulation_evaluation import evaluate_simulation_output


DEFAULT_MAX_OUTPUT_TOKENS = 2_048
APPROVED_MODELS = {"gpt-5.6-terra", "gpt-5.6-luna"}


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def votes_only_confirmation_for_model(model_id: str) -> str:
    if model_id not in APPROVED_MODELS:
        raise ValueError(f"No approved votes-only model: {model_id}")
    return "RUN_" + model_id.upper().replace(".", "_").replace("-", "_") + "_VOTES_ONLY"


def _assert_votes_case(votes_case: dict[str, Any], bundle: dict[str, Any]) -> None:
    if votes_case.get("schema_version") != "votes_only_case_v1":
        raise ValueError("Unsupported votes-only case schema")
    if votes_case.get("meeting_id") != bundle.get("meeting_id"):
        raise ValueError("Votes-only case meeting differs from bundle")
    if votes_case.get("bundle_hash") != bundle.get("bundle_hash"):
        raise ValueError("Votes-only case bundle hash differs from bundle")
    expected_locked_hash = _canonical_hash(votes_case.get("locked_context"))
    if votes_case.get("locked_context_hash") != expected_locked_hash:
        raise ValueError("Votes-only locked context hash mismatch")
    unsealed = {key: value for key, value in votes_case.items() if key != "votes_case_hash"}
    if votes_case.get("votes_case_hash") != _canonical_hash(unsealed):
        raise ValueError("Votes-only case hash mismatch")


def build_votes_only_case(
    bundle: dict[str, Any],
    anchor: dict[str, Any],
    *,
    bundle_sha256: str | None = None,
    anchor_sha256: str | None = None,
) -> dict[str, Any]:
    if anchor.get("status") != "PAID_SAMPLE_COMPLETED":
        raise ValueError("Anchor must be a completed paid sample")
    if anchor.get("meeting_id") != bundle.get("meeting_id"):
        raise ValueError("Anchor meeting differs from bundle")
    if anchor.get("bundle_hash") != bundle.get("bundle_hash"):
        raise ValueError("Anchor bundle hash differs from bundle hash")

    anchor_output = anchor.get("output")
    if not isinstance(anchor_output, dict):
        raise ValueError("Anchor output is missing")
    validate_simulation_output(anchor_output)
    bundle_ids = [item["participant_id"] for item in bundle.get("participants", [])]
    anchor_ids = [item["participant_id"] for item in anchor_output["profiles"]]
    if len(bundle_ids) != len(set(bundle_ids)) or set(bundle_ids) != set(anchor_ids):
        raise ValueError("Anchor voter roster differs from bundle")

    vote_usage = [
        item
        for item in anchor.get("usage", [])
        if item.get("stage") == "votes" and item.get("attempt", 1) == 1
    ]
    if len(vote_usage) != 1 or int(vote_usage[0].get("preflight_input_tokens", 0)) <= 0:
        raise ValueError("Anchor must contain one exact votes input-token count")

    locked_context = {
        "profiles": anchor_output["profiles"],
        "discussion": anchor_output["discussion"],
        "final_proposal": anchor_output["final_proposal"],
    }
    payload = {
        "schema_version": "votes_only_case_v1",
        "meeting_id": bundle["meeting_id"],
        "bundle_hash": bundle["bundle_hash"],
        "bundle_sha256": bundle_sha256 or _canonical_hash(bundle),
        "anchor_model_id": anchor["model_id"],
        "anchor_sha256": anchor_sha256 or _canonical_hash(anchor),
        "anchor_vote_preflight_input_tokens": int(
            vote_usage[0]["preflight_input_tokens"]
        ),
        "instrument_disclosure": (
            "Conditional votes-only instrument using locked profiles, discussion and "
            "Chair proposal. It does not replay the original five-stage votes prompt."
        ),
        "locked_context": locked_context,
        "locked_context_hash": _canonical_hash(locked_context),
    }
    payload["votes_case_hash"] = _canonical_hash(payload)
    return payload


def render_votes_only_prefix(
    bundle: dict[str, Any], votes_case: dict[str, Any]
) -> str:
    _assert_votes_case(votes_case, bundle)
    return (
        render_stable_prefix(bundle)
        + "\n\nLOCKED_PRE_VOTE_CONTEXT_JSON:\n"
        + json.dumps(
            votes_case["locked_context"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\nThe locked context is immutable. Do not revise the profiles, discussion "
        "or Chair proposal."
    )


def _votes_instruction() -> str:
    return (
        "STAGE=votes. Return exactly one FOR or AGAINST vote per supplied "
        "participant on the locked Chair proposal. Populate only votes; profiles, "
        "openings and options must be empty and final_proposal must be null."
    )


def dry_run_votes_only(
    bundle: dict[str, Any],
    votes_case: dict[str, Any],
    spec: dict[str, Any],
    *,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    rendered = render_votes_only_prefix(bundle, votes_case) + _votes_instruction()
    character_estimate = math.ceil(len(rendered) / 4)
    empirical_upper_bound = math.ceil(
        votes_case["anchor_vote_preflight_input_tokens"] * 1.10
    )
    conservative_input_tokens = max(character_estimate, empirical_upper_bound)
    one_attempt = _request_cost_upper_bound(
        conservative_input_tokens,
        spec,
        max_output_tokens,
    )
    return {
        "status": "VOTES_ONLY_DRY_RUN_NO_API_CALL",
        "model_id": spec["model_id"],
        "meeting_id": bundle["meeting_id"],
        "votes_case_hash": votes_case["votes_case_hash"],
        "locked_context_hash": votes_case["locked_context_hash"],
        "anchor_vote_preflight_input_tokens": votes_case[
            "anchor_vote_preflight_input_tokens"
        ],
        "conservative_input_tokens": conservative_input_tokens,
        "max_output_tokens": max_output_tokens,
        "one_attempt_upper_bound_usd": round(one_attempt, 8),
        "two_attempt_upper_bound_usd": round(one_attempt * 2, 8),
        "estimate_basis": (
            "max(chars/4, 110% of the anchor five-stage votes exact input count); "
            "the long-context price card is applied when the resulting count exceeds "
            "the model threshold"
        ),
        "estimate_is_not_actual_usage": True,
    }


def _evaluate_read_only(app_database: Path, output: dict[str, Any]) -> dict[str, Any]:
    app_path = app_database.resolve()
    if not app_path.is_file():
        raise FileNotFoundError(f"App database is missing: {app_path}")
    connection = sqlite3.connect(f"file:{app_path.as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        return evaluate_simulation_output(connection, output)
    finally:
        connection.close()


def _validate_app_labels(
    app_database: Path,
    meeting_id: str,
    participant_ids: set[str],
) -> None:
    app_path = app_database.resolve()
    if not app_path.is_file():
        raise FileNotFoundError(f"App database is missing: {app_path}")
    connection = sqlite3.connect(f"file:{app_path.as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        outcome = connection.execute(
            "SELECT action_class FROM meeting_outcome WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()
        if outcome is None:
            raise ValueError(f"No outcome label for {meeting_id}")
        label_ids = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT participant_id FROM participant_vote WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchall()
        }
        if label_ids != participant_ids:
            missing = sorted(participant_ids - label_ids)
            extra = sorted(label_ids - participant_ids)
            raise ValueError(
                "Votes-only label roster differs from locked profiles: "
                f"missing={missing}, extra={extra}"
            )
    finally:
        connection.close()


def run_paid_votes_only(
    client: Any,
    bundle: dict[str, Any],
    votes_case: dict[str, Any],
    spec: dict[str, Any],
    *,
    app_database: Path,
    max_cost_usd: float,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    if max_cost_usd <= 0:
        raise ValueError("max_cost_usd must be positive")
    _assert_votes_case(votes_case, bundle)
    _validate_app_labels(
        app_database,
        votes_case["meeting_id"],
        {
            item["participant_id"]
            for item in votes_case["locked_context"]["profiles"]
        },
    )
    schema, allowed_evidence_ids = _runtime_stage_schema(bundle)
    validator = Draft202012Validator(schema)
    stable = render_votes_only_prefix(bundle, votes_case)
    dynamic = _votes_instruction()
    usage_records = []
    spent = 0.0
    reserved_upper_bound = 0.0

    for attempt in (1, 2):
        request_reasoning = {
            "effort": STAGE_REASONING_EFFORT["votes"],
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
            raise RuntimeError(f"Input-token count failed for votes attempt {attempt}")
        worst_next = _request_cost_upper_bound(
            exact_input_tokens,
            spec,
            max_output_tokens,
        )
        if reserved_upper_bound + worst_next > max_cost_usd:
            raise RuntimeError(
                "Hard cost cap blocks votes attempt "
                f"{attempt}: spent={spent:.4f}, reserved={reserved_upper_bound:.4f}, "
                f"conservative_next={worst_next:.4f}, cap={max_cost_usd:.4f}"
            )

        started = time.perf_counter()
        response = client.responses.create(
            model=spec["model_id"],
            service_tier="default",
            store=False,
            max_output_tokens=max_output_tokens,
            reasoning=request_reasoning,
            prompt_cache_key=votes_case["locked_context_hash"],
            extra_body={"prompt_cache_options": {"mode": "implicit", "ttl": "30m"}},
            input=request_input,
            text=request_text,
            truncation="disabled",
        )
        latency = time.perf_counter() - started
        if _read(response, "status") != "completed":
            raise RuntimeError(
                "Schema-layer response failed for votes: "
                f"status={_read(response, 'status')}"
            )
        output_text = _read(response, "output_text")
        if not output_text:
            raise RuntimeError("Schema-layer response has no output_text: votes")
        try:
            envelope = json.loads(output_text)
        except json.JSONDecodeError as error:
            raise RuntimeError("Schema-layer JSON failure for votes") from error
        schema_errors = sorted(
            validator.iter_errors(envelope), key=lambda item: list(item.path)
        )
        if schema_errors:
            raise RuntimeError(
                "Schema-layer validation failure for votes: "
                + schema_errors[0].message
            )

        record = _usage_record(
            response,
            stage="votes",
            attempt=attempt,
            latency=latency,
        )
        record["preflight_input_tokens"] = exact_input_tokens
        record["reserved_cost_upper_bound_usd"] = round(worst_next, 8)
        record["cost_usd"] = usage_cost_usd(record, spec)
        spent += record["cost_usd"]
        reserved_upper_bound += worst_next
        usage_records.append(record)

        violations = semantic_violations("votes", envelope, bundle, {})
        if not violations:
            break
        if attempt == 2:
            raise RuntimeError(
                "Semantic repair failed for votes: " + "; ".join(violations)
            )
        dynamic = (
            _votes_instruction()
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

    locked = votes_case["locked_context"]
    final = {
        "schema_version": "simulation_output_v1",
        "meeting_id": votes_case["meeting_id"],
        "synthetic": True,
        "profiles": locked["profiles"],
        "discussion": locked["discussion"],
        "final_proposal": locked["final_proposal"],
        "votes": [
            {"participant_id": item["participant_id"], "choice": item["choice"]}
            for item in envelope["votes"]
        ],
    }
    semantic_report = validate_simulation_output(final)
    evaluation = _evaluate_read_only(app_database, final)
    return {
        "status": "PAID_VOTES_ONLY_COMPLETED",
        "model_id": spec["model_id"],
        "meeting_id": votes_case["meeting_id"],
        "bundle_hash": votes_case["bundle_hash"],
        "votes_case_hash": votes_case["votes_case_hash"],
        "locked_context_hash": votes_case["locked_context_hash"],
        "anchor_model_id": votes_case["anchor_model_id"],
        "stage_order": ["votes"],
        "instrument_disclosure": votes_case["instrument_disclosure"],
        "semantic_validation": semantic_report,
        "evaluation": evaluation,
        "cache_report": {
            "not_applicable": True,
            "eligible_call_count": 0,
            "reason": "Standalone votes-only request has no prior same-model cache-write stage.",
        },
        "usage": usage_records,
        "repair_count": len(usage_records) - 1,
        "total_cost_usd": round(spent, 8),
        "reserved_cost_upper_bound_usd": round(reserved_upper_bound, 8),
        "hard_cap_usd": max_cost_usd,
        "output": final,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or execute one fail-closed votes-only model comparison run."
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    parser.add_argument("--case-output", type=Path)
    parser.add_argument("--run-output", type=Path)
    parser.add_argument("--execute-paid-votes-only", action="store_true")
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--confirmation")
    args = parser.parse_args()

    spec = load_model_spec(args.spec)
    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    anchor = json.loads(args.anchor.read_text(encoding="utf-8"))
    votes_case = build_votes_only_case(
        bundle,
        anchor,
        bundle_sha256=_file_hash(args.bundle),
        anchor_sha256=_file_hash(args.anchor),
    )
    if args.case_output is not None:
        _write_new_json(args.case_output, votes_case)

    if not args.execute_paid_votes_only:
        print(
            json.dumps(
                dry_run_votes_only(bundle, votes_case, spec),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    required_confirmation = votes_only_confirmation_for_model(spec["model_id"])
    if args.confirmation != required_confirmation:
        raise RuntimeError(
            f"Paid votes-only run requires --confirmation {required_confirmation}"
        )
    if args.max_cost_usd is None:
        raise RuntimeError("Paid votes-only run requires --max-cost-usd")
    if args.run_output is None:
        raise RuntimeError("Paid votes-only run requires --run-output")
    if args.run_output.resolve().exists():
        raise FileExistsError(
            f"Paid votes-only output already exists: {args.run_output.resolve()}"
        )
    run_model_preflight(args.spec)
    from openai import OpenAI

    client = OpenAI(api_key=load_user_scope_openai_key(), timeout=180.0)
    report = run_paid_votes_only(
        client,
        bundle,
        votes_case,
        spec,
        app_database=args.app,
        max_cost_usd=args.max_cost_usd,
    )
    report["bundle_sha256"] = votes_case["bundle_sha256"]
    report["anchor_sha256"] = votes_case["anchor_sha256"]
    _write_new_json(args.run_output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
