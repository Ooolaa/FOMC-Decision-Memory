from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from decision_memory.app_db import record_assumption_event
from decision_memory.fed_documents import extract_html_paragraphs


def _year_ago(value: str) -> str:
    parsed = date.fromisoformat(value)
    try:
        return parsed.replace(year=parsed.year - 1).isoformat()
    except ValueError:
        return parsed.replace(year=parsed.year - 1, day=28).isoformat()


def _first_yoy_breach(
    source: sqlite3.Connection,
    *,
    series_id: str,
    after_date: str,
    threshold: float,
) -> dict[str, Any]:
    first_releases = source.execute(
        """
        SELECT observation_date, MIN(realtime_start) AS first_release_at
        FROM observation_vintage
        WHERE series_id = ?
          AND realtime_start > ?
          AND value_num IS NOT NULL
        GROUP BY observation_date
        ORDER BY first_release_at, observation_date
        """,
        (series_id, after_date),
    ).fetchall()
    for observation_date, first_release_at in first_releases:
        current_row = source.execute(
            """
            SELECT value_num
            FROM observation_vintage
            WHERE series_id = ?
              AND observation_date = ?
              AND realtime_start = ?
              AND value_num IS NOT NULL
            ORDER BY realtime_end DESC
            LIMIT 1
            """,
            (series_id, observation_date, first_release_at),
        ).fetchone()
        lag_date = _year_ago(str(observation_date))
        lag_row = source.execute(
            """
            SELECT value_num
            FROM observation_vintage
            WHERE series_id = ?
              AND observation_date = ?
              AND realtime_start <= ?
              AND realtime_end >= ?
              AND value_num IS NOT NULL
            ORDER BY realtime_start DESC
            LIMIT 1
            """,
            (series_id, lag_date, first_release_at, first_release_at),
        ).fetchone()
        if current_row is None or lag_row is None or float(lag_row[0]) == 0:
            continue
        metric_value = (float(current_row[0]) / float(lag_row[0]) - 1.0) * 100.0
        if metric_value > threshold:
            return {
                "first_contradiction_at": str(first_release_at),
                "contradicting_observation_date": str(observation_date),
                "contradiction_metric_value": metric_value,
                "current_value": float(current_row[0]),
                "lag_observation_date": lag_date,
                "lag_value_visible_at_release": float(lag_row[0]),
            }
    raise RuntimeError(
        f"No point-in-time {series_id} year-over-year breach after {after_date}"
    )


def _statement_text(
    source_locator_json: str,
    expected_hash: str,
) -> str:
    locator = json.loads(source_locator_json)
    local_path = Path(locator["local_path"])
    if not local_path.is_file():
        raise FileNotFoundError(f"Statement cache file is missing: {local_path}")
    content = local_path.read_bytes()
    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(f"Statement content hash mismatch: {local_path}")
    return " ".join(extract_html_paragraphs(content))


def _first_statement_flip(
    app: sqlite3.Connection,
    *,
    adopted_at: str,
    contradiction_at: str,
    support_patterns: list[str],
    flip_patterns: list[str],
) -> dict[str, str] | None:
    support_regexes = [re.compile(pattern, re.IGNORECASE) for pattern in support_patterns]
    flip_regexes = [re.compile(pattern, re.IGNORECASE) for pattern in flip_patterns]
    rows = app.execute(
        """
        SELECT document_id, meeting_id, publication_at,
               source_locator, content_hash
        FROM document_source
        WHERE document_type = 'statement'
          AND usage_class IN ('label_only', 'evaluation_only')
        ORDER BY publication_at, document_id
        """
    ).fetchall()
    support_was_observed = False
    for document_id, meeting_id, publication_at, locator, content_hash in rows:
        statement_date = str(publication_at)[:10]
        if statement_date < adopted_at:
            continue
        text = _statement_text(locator, content_hash)
        has_support = any(pattern.search(text) for pattern in support_regexes)
        if statement_date <= contradiction_at and has_support:
            support_was_observed = True
        if statement_date < contradiction_at:
            continue
        has_flip = any(pattern.search(text) for pattern in flip_regexes)
        if has_flip and not has_support:
            if not support_was_observed:
                raise RuntimeError(
                    "No registered support phrase was observed before the candidate flip"
                )
            return {
                "statement_flip_at": statement_date,
                "statement_flip_meeting_id": str(meeting_id),
                "statement_flip_document_id": str(document_id),
            }
    return None


def _first_policy_response(
    source: sqlite3.Connection,
    app: sqlite3.Connection,
    *,
    statement_flip_at: str,
    policy_direction: str,
) -> dict[str, str] | None:
    closing_action = "HIKE" if policy_direction == "HAWKISH" else "CUT"
    rows = app.execute(
        """
        SELECT meeting_id, action_class, source_document_id
        FROM meeting_outcome
        WHERE action_class = ?
        ORDER BY meeting_id
        """,
        (closing_action,),
    ).fetchall()
    candidates = []
    for meeting_id, action_class, document_id in rows:
        meeting = source.execute(
            """
            SELECT meeting_end_date
            FROM fomc_meeting WHERE meeting_id = ?
            """,
            (meeting_id,),
        ).fetchone()
        if meeting is None:
            raise RuntimeError(f"Outcome meeting is absent from source DB: {meeting_id}")
        meeting_end_date = str(meeting[0])
        if meeting_end_date >= statement_flip_at:
            candidates.append(
                (
                    meeting_end_date,
                    str(meeting_id),
                    str(action_class),
                    str(document_id),
                )
            )
    if not candidates:
        return None
    meeting_end_date, meeting_id, action_class, document_id = min(candidates)
    return {
        "policy_response_at": meeting_end_date,
        "policy_response_meeting_id": meeting_id,
        "policy_response_action_class": action_class,
        "policy_response_document_id": document_id,
    }


def _days_between(start: str, end: str) -> int:
    difference = date.fromisoformat(end) - date.fromisoformat(start)
    if difference.days < 0:
        raise ValueError(f"Lag end precedes start: {start} -> {end}")
    return difference.days


def evaluate_observable_lag(
    source: sqlite3.Connection,
    app: sqlite3.Connection,
    spec: dict[str, Any],
    *,
    as_of_date: str,
) -> dict[str, Any]:
    as_of = date.fromisoformat(as_of_date)
    contradiction_rule = spec["contradiction_rule"]
    contradiction = _first_yoy_breach(
        source,
        series_id=contradiction_rule["series_id"],
        after_date=spec["assumption_adopted_at"],
        threshold=float(contradiction_rule["threshold_value"]),
    )
    phrase_set = spec["phrase_set"]
    flip = _first_statement_flip(
        app,
        adopted_at=spec["assumption_adopted_at"],
        contradiction_at=contradiction["first_contradiction_at"],
        support_patterns=phrase_set["support_patterns"],
        flip_patterns=phrase_set["flip_patterns"],
    )
    result: dict[str, Any] = {
        "spec_id": spec["spec_id"],
        "phrase_set_version": phrase_set["version"],
        "policy_response_rule_version": spec["policy_response_rule_version"],
        "as_of_date": as_of_date,
        **contradiction,
        "statement_flip_at": None,
        "policy_response_at": None,
        "recognition_lag_days": None,
        "action_lag_days": None,
        "response_lag_days": None,
        "censoring_status": "NO_STATEMENT_FLIP_AS_OF",
    }
    if flip is None:
        return result
    if date.fromisoformat(flip["statement_flip_at"]) > as_of:
        raise ValueError("statement_flip_at exceeds as_of_date")
    result.update(flip)
    result["recognition_lag_days"] = _days_between(
        contradiction["first_contradiction_at"],
        flip["statement_flip_at"],
    )
    response = _first_policy_response(
        source,
        app,
        statement_flip_at=flip["statement_flip_at"],
        policy_direction=spec["policy_direction"],
    )
    if response is None or date.fromisoformat(response["policy_response_at"]) > as_of:
        result["censoring_status"] = "RIGHT_CENSORED_RATE_ONLY"
        result["censored_at"] = as_of_date
        return result
    result.update(response)
    result["action_lag_days"] = _days_between(
        flip["statement_flip_at"],
        response["policy_response_at"],
    )
    result["response_lag_days"] = _days_between(
        contradiction["first_contradiction_at"],
        response["policy_response_at"],
    )
    result["censoring_status"] = "OBSERVED"
    return result


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


def _date_timestamp(value: str) -> str:
    date.fromisoformat(value)
    return f"{value}T00:00:00Z"


def _persist_event_once(
    app: sqlite3.Connection,
    *,
    assumption_id: str,
    event_type: str,
    occurred_at: str,
    payload: dict[str, Any],
) -> None:
    actor = "deterministic-evaluator"
    expected = (occurred_at, actor, json.dumps(payload, sort_keys=True))
    existing = app.execute(
        """
        SELECT occurred_at, actor, payload_json
        FROM assumption_event
        WHERE assumption_id = ? AND event_type = ?
        ORDER BY occurred_at, event_id
        LIMIT 1
        """,
        (assumption_id, event_type),
    ).fetchone()
    if existing is None:
        record_assumption_event(
            app,
            assumption_id,
            event_type,
            occurred_at,
            actor=actor,
            payload=payload,
        )
        return
    if existing != expected:
        raise RuntimeError(
            f"Existing {event_type} event conflicts with deterministic result"
        )


def persist_observable_lag_result(
    app: sqlite3.Connection,
    result: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    if result["spec_id"] != spec["spec_id"]:
        raise ValueError("Lag result and spec_id do not match")
    decision_id = f"metric-{spec['spec_id']}"
    assumption_id = f"assumption-{spec['spec_id']}"
    context = {
        "assumption_adopted_at": spec["assumption_adopted_at"],
        "disclosures": spec["disclosures"],
        "source_boundary": "post_meeting_metric_evaluation_not_case_input",
    }
    expected_case = (
        "fomc_metric",
        "Observable recognition lag: inflation transitory claim",
        0,
        0,
        _canonical_json(context),
    )
    app.execute(
        """
        INSERT OR IGNORE INTO decision_case (
            decision_id, domain, title, synthetic, composite,
            context_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (decision_id, *expected_case, _utc_now()),
    )
    persisted_case = app.execute(
        """
        SELECT domain, title, synthetic, composite, context_json
        FROM decision_case WHERE decision_id = ?
        """,
        (decision_id,),
    ).fetchone()
    if persisted_case != expected_case:
        raise RuntimeError(f"Existing metric decision_case conflicts: {decision_id}")

    contradiction_rule = spec["contradiction_rule"]
    expected_assumption = (
        decision_id,
        spec["claim"],
        contradiction_rule["series_id"],
        contradiction_rule["operator"],
        float(contradiction_rule["threshold_value"]),
        f"{spec['policy_direction'].lower()}_rate_v1",
        spec["spec_id"],
    )
    app.execute(
        """
        INSERT OR IGNORE INTO decision_assumption (
            assumption_id, decision_id, claim, monitor_series_id,
            monitor_operator, threshold_value, direction_map_version,
            monitor_rule_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (assumption_id, *expected_assumption, _utc_now()),
    )
    persisted_assumption = app.execute(
        """
        SELECT decision_id, claim, monitor_series_id, monitor_operator,
               threshold_value, direction_map_version, monitor_rule_version
        FROM decision_assumption WHERE assumption_id = ?
        """,
        (assumption_id,),
    ).fetchone()
    if persisted_assumption != expected_assumption:
        raise RuntimeError(
            f"Existing metric assumption conflicts: {assumption_id}"
        )

    _persist_event_once(
        app,
        assumption_id=assumption_id,
        event_type="CONTRADICTION",
        occurred_at=_date_timestamp(result["first_contradiction_at"]),
        payload={
            "series_id": contradiction_rule["series_id"],
            "transformation": contradiction_rule["transformation"],
            "threshold_value": contradiction_rule["threshold_value"],
            "observation_date": result["contradicting_observation_date"],
            "metric_value": result["contradiction_metric_value"],
            "vintage_policy": contradiction_rule["vintage_policy"],
            "rule_version": spec["spec_id"],
        },
    )
    if result["statement_flip_at"] is not None:
        _persist_event_once(
            app,
            assumption_id=assumption_id,
            event_type="STATEMENT_FLIP",
            occurred_at=_date_timestamp(result["statement_flip_at"]),
            payload={
                "meeting_id": result["statement_flip_meeting_id"],
                "document_id": result["statement_flip_document_id"],
                "phrase_set_version": result["phrase_set_version"],
                "recognition_lag_days": result["recognition_lag_days"],
                "proxy_disclosure": spec["disclosures"][0],
            },
        )
        if result["censoring_status"] == "OBSERVED":
            _persist_event_once(
                app,
                assumption_id=assumption_id,
                event_type="POLICY_RESPONSE",
                occurred_at=_date_timestamp(result["policy_response_at"]),
                payload={
                    "meeting_id": result["policy_response_meeting_id"],
                    "document_id": result["policy_response_document_id"],
                    "action_class": result["policy_response_action_class"],
                    "action_lag_days": result["action_lag_days"],
                    "response_lag_days": result["response_lag_days"],
                    "rule_version": result["policy_response_rule_version"],
                },
            )
        elif result["censoring_status"] == "RIGHT_CENSORED_RATE_ONLY":
            _persist_event_once(
                app,
                assumption_id=assumption_id,
                event_type="CENSORED",
                occurred_at=_date_timestamp(result["censored_at"]),
                payload={
                    "status": result["censoring_status"],
                    "rule_version": result["policy_response_rule_version"],
                    "informative_censoring": True,
                },
            )
    event_count = int(
        app.execute(
            "SELECT COUNT(*) FROM assumption_event WHERE assumption_id = ?",
            (assumption_id,),
        ).fetchone()[0]
    )
    return {
        "decision_id": decision_id,
        "assumption_id": assumption_id,
        "event_count": event_count,
        "censoring_status": result["censoring_status"],
    }
