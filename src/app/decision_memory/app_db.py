from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


EVENT_TYPES = {
    "CONTRADICTION",
    "REVIEW_REQUESTED",
    "REVIEWED",
    "STATEMENT_FLIP",
    "POLICY_RESPONSE",
    "CENSORED",
}


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS document_source (
    document_id   TEXT PRIMARY KEY,
    meeting_id    TEXT,
    document_type TEXT NOT NULL,
    publication_at TEXT NOT NULL,
    usage_class   TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (content_hash)
);

CREATE TABLE IF NOT EXISTS transcript_segment (
    segment_id     TEXT PRIMARY KEY,
    document_id    TEXT NOT NULL,
    meeting_id     TEXT NOT NULL,
    ordinal        INTEGER NOT NULL CHECK (ordinal >= 0),
    speaker_label  TEXT NOT NULL,
    participant_id TEXT,
    text            TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (document_id, ordinal),
    FOREIGN KEY (document_id) REFERENCES document_source(document_id),
    FOREIGN KEY (participant_id) REFERENCES participant(participant_id)
);

CREATE INDEX IF NOT EXISTS ix_transcript_segment_meeting_speaker
    ON transcript_segment(meeting_id, speaker_label, ordinal);

CREATE TABLE IF NOT EXISTS public_communication (
    document_id    TEXT PRIMARY KEY,
    participant_id TEXT NOT NULL,
    title           TEXT NOT NULL,
    text            TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES document_source(document_id),
    FOREIGN KEY (participant_id) REFERENCES participant(participant_id)
);

CREATE INDEX IF NOT EXISTS ix_public_communication_participant
    ON public_communication(participant_id, document_id);

CREATE TABLE IF NOT EXISTS participant (
    participant_id TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    role           TEXT NOT NULL,
    effective_start TEXT,
    effective_end   TEXT
);

CREATE TABLE IF NOT EXISTS meeting_participant (
    meeting_id    TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    role          TEXT NOT NULL,
    is_voter      INTEGER NOT NULL CHECK (is_voter IN (0, 1)),
    is_chair      INTEGER NOT NULL CHECK (is_chair IN (0, 1)),
    PRIMARY KEY (meeting_id, participant_id),
    FOREIGN KEY (participant_id) REFERENCES participant(participant_id)
);

CREATE TABLE IF NOT EXISTS meeting_outcome (
    meeting_id        TEXT PRIMARY KEY,
    action_class      TEXT NOT NULL,
    target_rate       REAL,
    target_lower      REAL,
    target_upper      REAL,
    source_document_id TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (source_document_id) REFERENCES document_source(document_id)
);

CREATE TABLE IF NOT EXISTS participant_vote (
    meeting_id     TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    vote_round     INTEGER NOT NULL DEFAULT 1 CHECK (vote_round > 0),
    voter_choice   TEXT NOT NULL,
    dissent        INTEGER NOT NULL CHECK (dissent IN (0, 1)),
    evidence_id    TEXT NOT NULL,
    PRIMARY KEY (meeting_id, participant_id, vote_round),
    FOREIGN KEY (participant_id) REFERENCES participant(participant_id),
    FOREIGN KEY (evidence_id) REFERENCES document_source(document_id)
);

CREATE TABLE IF NOT EXISTS decision_case (
    decision_id  TEXT PRIMARY KEY,
    domain       TEXT NOT NULL,
    title        TEXT NOT NULL,
    synthetic    INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
    composite    INTEGER NOT NULL CHECK (composite IN (0, 1)),
    context_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_trace (
    trace_id          TEXT PRIMARY KEY,
    decision_id       TEXT NOT NULL UNIQUE,
    options_json      TEXT NOT NULL,
    debate_json       TEXT NOT NULL,
    decision_json     TEXT NOT NULL,
    vote_json         TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES decision_case(decision_id)
);

CREATE TABLE IF NOT EXISTS decision_assumption (
    assumption_id         TEXT PRIMARY KEY,
    decision_id           TEXT NOT NULL,
    claim                 TEXT NOT NULL,
    monitor_series_id     TEXT NOT NULL,
    monitor_operator      TEXT NOT NULL,
    threshold_value       REAL,
    direction_map_version TEXT NOT NULL,
    monitor_rule_version  TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES decision_case(decision_id)
);

CREATE TABLE IF NOT EXISTS assumption_event (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    assumption_id TEXT NOT NULL,
    event_type    TEXT NOT NULL
        CHECK (event_type IN (
            'CONTRADICTION', 'REVIEW_REQUESTED', 'REVIEWED',
            'STATEMENT_FLIP', 'POLICY_RESPONSE', 'CENSORED'
        )),
    occurred_at   TEXT NOT NULL,
    actor         TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (assumption_id) REFERENCES decision_assumption(assumption_id),
    UNIQUE (assumption_id, event_type, occurred_at)
);

CREATE INDEX IF NOT EXISTS ix_assumption_event_timeline
    ON assumption_event(assumption_id, occurred_at, event_id);

CREATE TABLE IF NOT EXISTS policy_rate_context (
    meeting_id           TEXT NOT NULL,
    ordinal              INTEGER NOT NULL CHECK (ordinal BETWEEN 0 AND 8),
    record_kind          TEXT NOT NULL CHECK (record_kind IN ('CURRENT', 'CHANGE')),
    cutoff_date          TEXT NOT NULL,
    effective_date       TEXT NOT NULL,
    regime               TEXT NOT NULL,
    direction            TEXT NOT NULL,
    target_rate          REAL,
    lower_rate           REAL,
    upper_rate           REAL,
    regime_started_at    TEXT,
    regime_duration_days INTEGER,
    source_series_ids_json TEXT NOT NULL,
    rule_version         TEXT NOT NULL,
    source_hash          TEXT NOT NULL,
    PRIMARY KEY (meeting_id, ordinal)
);

CREATE TABLE IF NOT EXISTS reaction_model (
    model_id       TEXT PRIMARY KEY,
    model_version  TEXT NOT NULL,
    train_start    TEXT NOT NULL,
    train_end      TEXT NOT NULL,
    metrics_json   TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reaction_coefficient (
    model_id       TEXT NOT NULL,
    participant_id TEXT,
    feature        TEXT NOT NULL,
    coefficient    REAL NOT NULL,
    PRIMARY KEY (model_id, participant_id, feature),
    FOREIGN KEY (model_id) REFERENCES reaction_model(model_id),
    FOREIGN KEY (participant_id) REFERENCES participant(participant_id)
);

CREATE TABLE IF NOT EXISTS simulation_case (
    case_id        TEXT PRIMARY KEY,
    meeting_id     TEXT,
    decision_id    TEXT,
    manifest_hash  TEXT NOT NULL,
    synthetic      INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
    created_at     TEXT NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES decision_case(decision_id)
);

CREATE TABLE IF NOT EXISTS simulation_run (
    run_id          TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    prompt_hash     TEXT NOT NULL,
    schema_hash     TEXT NOT NULL,
    output_json     TEXT NOT NULL,
    input_tokens    INTEGER,
    cached_tokens   INTEGER,
    output_tokens   INTEGER,
    cost_usd        REAL,
    latency_ms      INTEGER,
    synthetic       INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
    status          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES simulation_case(case_id)
);

CREATE TABLE IF NOT EXISTS evaluation_result (
    evaluation_id    TEXT PRIMARY KEY,
    case_id           TEXT NOT NULL,
    experiment        TEXT NOT NULL,
    metric            TEXT NOT NULL,
    baseline_score    REAL,
    candidate_score   REAL,
    evaluator_version TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES simulation_case(case_id)
);
"""


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid ISO timestamp: {value}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp must include a timezone: {value}")
    return parsed.astimezone(timezone.utc)


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)


def migrate_assumption_event_schema(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'assumption_event'
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("assumption_event table does not exist")
    if "STATEMENT_FLIP" in str(row[0]):
        return False

    connection.execute("SAVEPOINT migrate_assumption_event_v2")
    try:
        connection.execute("DROP INDEX IF EXISTS ix_assumption_event_timeline")
        connection.execute(
            """
            CREATE TABLE assumption_event_v2 (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                assumption_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK (event_type IN (
                    'CONTRADICTION', 'REVIEW_REQUESTED', 'REVIEWED',
                    'STATEMENT_FLIP', 'POLICY_RESPONSE', 'CENSORED'
                )),
                occurred_at TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (assumption_id)
                    REFERENCES decision_assumption(assumption_id),
                UNIQUE (assumption_id, event_type, occurred_at)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO assumption_event_v2 (
                event_id, assumption_id, event_type, occurred_at,
                actor, payload_json, created_at
            )
            SELECT event_id, assumption_id, event_type, occurred_at,
                   actor, payload_json, created_at
            FROM assumption_event
            """
        )
        connection.execute("DROP TABLE assumption_event")
        connection.execute(
            "ALTER TABLE assumption_event_v2 RENAME TO assumption_event"
        )
        connection.execute(
            """
            CREATE INDEX ix_assumption_event_timeline
            ON assumption_event(assumption_id, occurred_at, event_id)
            """
        )
        connection.execute("RELEASE SAVEPOINT migrate_assumption_event_v2")
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT migrate_assumption_event_v2")
        connection.execute("RELEASE SAVEPOINT migrate_assumption_event_v2")
        raise
    return True


def seed_enterprise_demo(
    connection: sqlite3.Connection,
    contradiction_at: str,
    decision_at: str = "2021-06-01T00:00:00Z",
    threshold_value: float = 2.25,
    contradiction_value: float | None = None,
    contradiction_source_hash: str | None = None,
) -> dict[str, str]:
    _parse_utc(contradiction_at)
    _parse_utc(decision_at)
    decision_id = "enterprise-demo-financing-001"
    assumption_id = "enterprise-demo-financing-001-baa10y"
    created_at = _utc_now()

    connection.execute(
        """
        INSERT INTO decision_case (
            decision_id, domain, title, synthetic, composite,
            context_json, created_at
        ) VALUES (?, 'enterprise_demo', ?, 1, 1, ?, ?)
        """,
        (
            decision_id,
            "Composite financing decision",
            json.dumps(
                {
                    "source": "synthetic_composite",
                    "disclosure": "Demonstration case; not a real customer outcome.",
                    "decision_at": decision_at,
                    "monitor_series_id": "BAA10Y",
                    "threshold_value": threshold_value,
                },
                sort_keys=True,
            ),
            created_at,
        ),
    )
    connection.execute(
        """
        INSERT INTO decision_assumption (
            assumption_id, decision_id, claim, monitor_series_id,
            monitor_operator, threshold_value, direction_map_version,
            monitor_rule_version, created_at
        ) VALUES (?, ?, ?, 'BAA10Y', 'GT', ?, 'baa10y_upper_bound_v1',
                  'baa10y_upper_bound_v1', ?)
        """,
        (
            assumption_id,
            decision_id,
            f"BAA10Y remains at or below {threshold_value:.2f} percent.",
            threshold_value,
            created_at,
        ),
    )
    record_assumption_event(
        connection,
        assumption_id,
        "CONTRADICTION",
        contradiction_at,
        actor="deterministic-monitor",
        payload={
            "series_id": "BAA10Y",
            "operator": "GT",
            "threshold_value": threshold_value,
            "value": contradiction_value,
            "source_hash": contradiction_source_hash,
            "time_precision": "date",
            "monitor_rule_version": "baa10y_upper_bound_v1",
        },
    )
    return {
        "decision_id": decision_id,
        "assumption_id": assumption_id,
    }


def record_assumption_event(
    connection: sqlite3.Connection,
    assumption_id: str,
    event_type: str,
    occurred_at: str,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> int:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unsupported assumption event type: {event_type}")
    if not actor.strip():
        raise ValueError("actor is required")
    event_time = _parse_utc(occurred_at)

    assumption = connection.execute(
        "SELECT 1 FROM decision_assumption WHERE assumption_id = ?",
        (assumption_id,),
    ).fetchone()
    if assumption is None:
        raise ValueError(f"Unknown assumption_id: {assumption_id}")

    if event_type in {
        "REVIEW_REQUESTED",
        "REVIEWED",
        "STATEMENT_FLIP",
        "POLICY_RESPONSE",
        "CENSORED",
    }:
        contradiction_row = connection.execute(
            """
            SELECT occurred_at
            FROM assumption_event
            WHERE assumption_id = ? AND event_type = 'CONTRADICTION'
            ORDER BY occurred_at, event_id
            LIMIT 1
            """,
            (assumption_id,),
        ).fetchone()
        if contradiction_row is None:
            raise ValueError("A CONTRADICTION event is required before review")
        if event_time < _parse_utc(contradiction_row[0]):
            raise ValueError("Derived/review event cannot precede CONTRADICTION")

    if event_type == "REVIEWED":
        request_row = connection.execute(
            """
            SELECT occurred_at
            FROM assumption_event
            WHERE assumption_id = ? AND event_type = 'REVIEW_REQUESTED'
            ORDER BY occurred_at, event_id
            LIMIT 1
            """,
            (assumption_id,),
        ).fetchone()
        if request_row is None:
            raise ValueError("A REVIEW_REQUESTED event is required before REVIEWED")
        if event_time < _parse_utc(request_row[0]):
            raise ValueError("REVIEWED cannot precede REVIEW_REQUESTED")

    if event_type in {"POLICY_RESPONSE", "CENSORED"}:
        flip_row = connection.execute(
            """
            SELECT occurred_at
            FROM assumption_event
            WHERE assumption_id = ? AND event_type = 'STATEMENT_FLIP'
            ORDER BY occurred_at, event_id
            LIMIT 1
            """,
            (assumption_id,),
        ).fetchone()
        if flip_row is None:
            raise ValueError(
                f"A STATEMENT_FLIP event is required before {event_type}"
            )
        if event_time < _parse_utc(flip_row[0]):
            raise ValueError(f"{event_type} cannot precede STATEMENT_FLIP")

    cursor = connection.execute(
        """
        INSERT INTO assumption_event (
            assumption_id, event_type, occurred_at, actor,
            payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            assumption_id,
            event_type,
            occurred_at,
            actor,
            json.dumps(payload or {}, sort_keys=True),
            _utc_now(),
        ),
    )
    return int(cursor.lastrowid)


def workflow_recognition_lag_seconds(
    connection: sqlite3.Connection,
    assumption_id: str,
) -> int | None:
    events = dict(
        connection.execute(
            """
            SELECT event_type, MIN(occurred_at)
            FROM assumption_event
            WHERE assumption_id = ?
              AND event_type IN ('CONTRADICTION', 'REVIEWED')
            GROUP BY event_type
            """,
            (assumption_id,),
        ).fetchall()
    )
    if "CONTRADICTION" not in events or "REVIEWED" not in events:
        return None
    lag = _parse_utc(events["REVIEWED"]) - _parse_utc(events["CONTRADICTION"])
    if lag.total_seconds() < 0:
        raise ValueError("REVIEWED cannot precede CONTRADICTION")
    return int(lag.total_seconds())
