from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable


_SPEAKER_NAME_PART = r"[A-Z][A-Z'\u2019\uFFFD-]*"
_SPEAKER = re.compile(
    r"(?m)(?:^[ \t]*|(?<=\s))(?P<speaker>"
    r"(?:(?:VICE\s+)?CHAIR(?:MAN|WOMAN)?|MR\.|MS\.|MRS\.|GOVERNOR|PRESIDENT)"
    r"\s+"
    + _SPEAKER_NAME_PART
    + r"(?:\s+"
    + _SPEAKER_NAME_PART
    + r"){0,3}"
    r"|PARTICIPANT|SEVERAL)\.(?:\d+)?\s+"
)
_GENERIC_SPEAKERS = {"PARTICIPANT", "SEVERAL"}


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _normalize_segment_text(value: str) -> str:
    return " ".join(value.replace("\u00ad", "").split())


def split_speaker_segments(text: str) -> list[dict[str, str]]:
    matches = list(_SPEAKER.finditer(text))
    segments = []
    for ordinal, match in enumerate(matches):
        end = matches[ordinal + 1].start() if ordinal + 1 < len(matches) else len(text)
        body = _normalize_segment_text(text[match.end() : end])
        if not body:
            continue
        segments.append(
            {
                "speaker_label": match.group("speaker").strip(),
                "text": body,
            }
        )
    return segments


def _speaker_surname(speaker_label: str) -> str | None:
    if speaker_label in _GENERIC_SPEAKERS:
        return None
    tokens = speaker_label.rstrip(".").split()
    if not tokens:
        return None
    return tokens[-1].rstrip(".").casefold()


def _participant_for_speaker(
    connection: sqlite3.Connection,
    *,
    meeting_id: str,
    speaker_label: str,
) -> str | None:
    surname = _speaker_surname(speaker_label)
    if surname is None:
        return None
    rows = connection.execute(
        """
        SELECT participant.participant_id, participant.display_name
        FROM participant
        JOIN meeting_participant
          ON meeting_participant.participant_id = participant.participant_id
        WHERE meeting_participant.meeting_id = ?
        """,
        (meeting_id,),
    ).fetchall()
    matches = [
        row[0]
        for row in rows
        if str(row[1]).rstrip(".").split()[-1].casefold() == surname
    ]
    return str(matches[0]) if len(matches) == 1 else None


def persist_transcript_segments(
    connection: sqlite3.Connection,
    *,
    document_id: str,
    meeting_id: str,
    segments: Iterable[dict[str, str]],
) -> dict[str, int]:
    document = connection.execute(
        """
        SELECT meeting_id, document_type
        FROM document_source
        WHERE document_id = ?
        """,
        (document_id,),
    ).fetchone()
    if document != (meeting_id, "transcript"):
        raise ValueError(
            f"Transcript document provenance mismatch: {document_id}/{meeting_id}"
        )
    pending = []
    resolved = 0
    for ordinal, segment in enumerate(segments):
        speaker_label = str(segment["speaker_label"]).strip()
        text = _normalize_segment_text(str(segment["text"]))
        if not speaker_label or not text:
            raise ValueError("Transcript segments require speaker_label and text")
        participant_id = _participant_for_speaker(
            connection,
            meeting_id=meeting_id,
            speaker_label=speaker_label,
        )
        resolved += int(participant_id is not None)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        segment_id = "segment-" + hashlib.sha256(
            f"{document_id}|{ordinal}|{speaker_label}|{content_hash}".encode("utf-8")
        ).hexdigest()[:24]
        pending.append(
            (
                segment_id,
                document_id,
                meeting_id,
                ordinal,
                speaker_label,
                participant_id,
                text,
                content_hash,
                _utc_now(),
            )
        )
    if not pending:
        raise ValueError("Transcript produced no speaker segments")
    connection.executemany(
        """
        INSERT OR IGNORE INTO transcript_segment (
            segment_id, document_id, meeting_id, ordinal, speaker_label,
            participant_id, text, content_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        pending,
    )
    persisted = connection.execute(
        "SELECT COUNT(*) FROM transcript_segment WHERE document_id = ?",
        (document_id,),
    ).fetchone()[0]
    if persisted != len(pending):
        raise RuntimeError(
            f"Transcript segment count mismatch for {document_id}: "
            f"expected {len(pending)}, got {persisted}"
        )
    return {
        "segment_count": len(pending),
        "resolved_participant_segment_count": resolved,
    }
