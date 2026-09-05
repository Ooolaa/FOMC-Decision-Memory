from __future__ import annotations

import hashlib
import json
import mimetypes
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from decision_memory.fed_documents import is_official_federal_reserve_url


USAGE_CLASSES = {
    "input_allowed",
    "persona_evidence",
    "label_only",
    "evaluation_only",
}
DEFAULT_INPUT_USAGE_CLASSES = frozenset({"input_allowed", "persona_evidence"})


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid ISO timestamp: {value}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp must include a timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def ingest_local_document(
    connection: sqlite3.Connection,
    local_path: Path,
    *,
    meeting_id: str | None,
    document_type: str,
    publication_at: str,
    usage_class: str,
    source_url: str,
    expected_sha256: str | None = None,
) -> str:
    resolved = local_path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Document does not exist: {resolved}")
    if meeting_id is not None and not meeting_id.strip():
        raise ValueError("meeting_id must be non-empty when supplied")
    if not document_type.strip():
        raise ValueError("document_type is required")
    _parse_utc(publication_at)
    if usage_class not in USAGE_CLASSES:
        raise ValueError(f"Unsupported usage_class: {usage_class}")
    if not is_official_federal_reserve_url(source_url):
        raise ValueError("source_url must be an official Federal Reserve HTTPS URL")

    content = resolved.read_bytes()
    content_hash = _sha256_bytes(content)
    if expected_sha256 is not None and content_hash.lower() != expected_sha256.lower():
        raise ValueError(
            f"Document SHA-256 mismatch: expected {expected_sha256.lower()}, "
            f"got {content_hash}"
        )
    document_id = f"doc-{content_hash[:24]}"
    source_locator = json.dumps(
        {
            "kind": "local_cache_with_official_source",
            "local_path": str(resolved),
            "mime_type": mimetypes.guess_type(resolved.name)[0]
            or "application/octet-stream",
            "byte_length": len(content),
            "source_url": source_url,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    existing = connection.execute(
        """
        SELECT document_id, meeting_id, document_type, publication_at,
               usage_class, source_locator
        FROM document_source
        WHERE content_hash = ?
        """,
        (content_hash,),
    ).fetchone()
    if existing is not None:
        if existing[1:] != (
            meeting_id,
            document_type,
            publication_at,
            usage_class,
            source_locator,
        ):
            raise ValueError(
                "Document content hash already exists with different provenance"
            )
        return str(existing[0])
    connection.execute(
        """
        INSERT INTO document_source (
            document_id, meeting_id, document_type, publication_at,
            usage_class, source_locator, content_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            meeting_id,
            document_type,
            publication_at,
            usage_class,
            source_locator,
            content_hash,
            _utc_now(),
        ),
    )
    return document_id


def build_case_document_manifest(
    connection: sqlite3.Connection,
    *,
    meeting_id: str,
    cutoff_at: str,
    allowed_usage_classes: Iterable[str] = DEFAULT_INPUT_USAGE_CLASSES,
) -> dict[str, Any]:
    cutoff = _parse_utc(cutoff_at)
    allowed = frozenset(allowed_usage_classes)
    if not allowed or not allowed.issubset(USAGE_CLASSES):
        raise ValueError("allowed_usage_classes contains no valid input class")
    rows = connection.execute(
        """
        SELECT document_id, document_type, publication_at, usage_class,
               source_locator, content_hash
        FROM document_source
        WHERE meeting_id = ?
        ORDER BY publication_at, document_id
        """,
        (meeting_id,),
    ).fetchall()
    visible = []
    excluded_usage_count = 0
    excluded_late_count = 0
    for row in rows:
        if row[3] not in allowed:
            excluded_usage_count += 1
            continue
        if _parse_utc(row[2]) > cutoff:
            excluded_late_count += 1
            continue
        visible.append(
            {
                "document_id": row[0],
                "document_type": row[1],
                "publication_at": row[2],
                "usage_class": row[3],
                "source_locator": row[4],
                "content_hash": row[5],
            }
        )
    hash_input = json.dumps(
        {
            "meeting_id": meeting_id,
            "cutoff_at": cutoff_at,
            "allowed_usage_classes": sorted(allowed),
            "documents": [
                {
                    "document_id": item["document_id"],
                    "content_hash": item["content_hash"],
                    "publication_at": item["publication_at"],
                }
                for item in visible
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "meeting_id": meeting_id,
        "cutoff_at": cutoff_at,
        "allowed_usage_classes": sorted(allowed),
        "documents": visible,
        "excluded_late_count": excluded_late_count,
        "excluded_usage_count": excluded_usage_count,
        "manifest_hash": _sha256_bytes(hash_input),
    }
