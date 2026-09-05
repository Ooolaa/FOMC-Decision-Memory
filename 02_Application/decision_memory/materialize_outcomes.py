from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decision_memory.outcomes import RULE_VERSION, derive_rate_outcome


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def materialize_meeting_outcomes(
    source_database: Path,
    app_database: Path,
    document_manifest_path: Path,
    outcome_manifest_path: Path,
) -> dict[str, Any]:
    source_path = source_database.resolve()
    app_path = app_database.resolve()
    document_path = document_manifest_path.resolve()
    outcome_path = outcome_manifest_path.resolve()
    for required_path in (source_path, app_path, document_path):
        if not required_path.is_file():
            raise FileNotFoundError(f"Required file does not exist: {required_path}")

    document_manifest = json.loads(document_path.read_text(encoding="utf-8"))
    documents_by_meeting: dict[str, dict[str, dict[str, Any]]] = {}
    for item in document_manifest.get("documents", []):
        documents_by_meeting.setdefault(item["meeting_id"], {})[
            item["document_type"]
        ] = item
    evidence_documents = []
    for meeting_id, documents in sorted(documents_by_meeting.items()):
        evidence = documents.get("statement") or documents.get("minutes")
        if evidence is None:
            raise RuntimeError(f"Meeting has no outcome evidence: {meeting_id}")
        evidence_documents.append(evidence)
    if not evidence_documents:
        raise RuntimeError("Document manifest contains no outcome evidence")
    meeting_ids = [item["meeting_id"] for item in evidence_documents]
    if len(meeting_ids) != len(set(meeting_ids)):
        raise RuntimeError("Document manifest contains duplicate meeting statements")

    source = sqlite3.connect(
        f"file:{source_path.as_posix()}?mode=ro",
        uri=True,
    )
    try:
        outcomes = [derive_rate_outcome(source, meeting_id) for meeting_id in meeting_ids]
    finally:
        source.close()

    evidence_by_meeting = {
        item["meeting_id"]: item["document_id"] for item in evidence_documents
    }
    app = sqlite3.connect(
        f"file:{app_path.as_posix()}?mode=rw",
        uri=True,
    )
    app.execute("PRAGMA foreign_keys = ON")
    try:
        for outcome in outcomes:
            meeting_id = outcome["meeting_id"]
            source_document_id = evidence_by_meeting[meeting_id]
            document_row = app.execute(
                """
                SELECT meeting_id, document_type
                FROM document_source WHERE document_id = ?
                """,
                (source_document_id,),
            ).fetchone()
            if (
                document_row is None
                or document_row[0] != meeting_id
                or document_row[1] not in {"statement", "minutes"}
            ):
                raise RuntimeError(
                    "Outcome evidence does not match meeting: "
                    f"{meeting_id}/{source_document_id}"
                )
            expected = (
                outcome["action_class"],
                outcome["target_rate"],
                outcome["target_lower"],
                outcome["target_upper"],
                source_document_id,
            )
            app.execute(
                """
                INSERT OR IGNORE INTO meeting_outcome (
                    meeting_id, action_class, target_rate, target_lower,
                    target_upper, source_document_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (meeting_id, *expected, _utc_now()),
            )
            persisted = app.execute(
                """
                SELECT action_class, target_rate, target_lower, target_upper,
                       source_document_id
                FROM meeting_outcome WHERE meeting_id = ?
                """,
                (meeting_id,),
            ).fetchone()
            if persisted != expected:
                raise RuntimeError(
                    f"Existing meeting outcome conflicts with source: {meeting_id}"
                )
        app.commit()
        integrity = app.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = app.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"App DB validation failed: integrity={integrity}, "
                f"foreign_keys={foreign_key_errors}"
            )
    except Exception:
        app.rollback()
        raise
    finally:
        app.close()

    manifest_without_hash = {
        "rule_version": RULE_VERSION,
        "source_database_sha256": _sha256_file(source_path),
        "document_manifest_sha256": _sha256_file(document_path),
        "meeting_count": len(outcomes),
        "outcomes": outcomes,
    }
    manifest_hash = _manifest_hash(manifest_without_hash)
    manifest = {**manifest_without_hash, "manifest_hash": manifest_hash}
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = outcome_path.with_suffix(outcome_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(outcome_path)

    action_counts = dict(sorted(Counter(
        outcome["action_class"] for outcome in outcomes
    ).items()))
    return {
        "meeting_count": len(outcomes),
        "action_class_counts": action_counts,
        "manifest_hash": manifest_hash,
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize deterministic rate-only FOMC outcome labels."
    )
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--documents",
        type=Path,
        default=Path(
            "document_manifests/current_45_as_of_2026-08-27_source_a7fd.json"
        ),
    )
    parser.add_argument(
        "--outcomes",
        type=Path,
        default=Path("outcome_manifests/current_45_rate_delta_v1.json"),
    )
    parser.add_argument("--expected-meetings", type=int, default=45)
    args = parser.parse_args()
    report = materialize_meeting_outcomes(
        args.source,
        args.app,
        args.documents,
        args.outcomes,
    )
    if report["meeting_count"] != args.expected_meetings:
        raise RuntimeError(
            f"Expected {args.expected_meetings} meetings, "
            f"got {report['meeting_count']}"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
