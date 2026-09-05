from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Callable

from decision_memory.document_calendar import parse_current_document_calendar
from decision_memory.documents import ingest_local_document
from decision_memory.fed_documents import (
    cache_official_document,
    fetch_official_document,
)
from fomc_calendar import CURRENT_CALENDAR_URL, fetch_text


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_or_verify(
    source_url: str,
    local_path: Path,
    fetcher: Callable[[str], bytes],
) -> dict[str, Any]:
    if not local_path.exists():
        report = cache_official_document(
            source_url,
            local_path,
            fetcher=fetcher,
        )
        report["cache_status"] = "CREATED"
        return report
    if not local_path.is_file():
        raise RuntimeError(f"Cached document path is not a file: {local_path}")
    local_content = local_path.read_bytes()
    official_content = fetcher(source_url)
    if local_content != official_content:
        raise RuntimeError(
            f"Cached document differs from the current official source: {source_url}"
        )
    return {
        "source_url": source_url,
        "local_path": str(local_path.resolve()),
        "byte_length": len(local_content),
        "sha256": _sha256_bytes(local_content),
        "cache_status": "VERIFIED_REUSED",
    }


def materialize_current_documents(
    *,
    source_database: Path,
    app_database: Path,
    cache_directory: Path,
    manifest_path: Path,
    calendar_html: str,
    calendar_source_url: str,
    start_year: int,
    as_of_date: date,
    document_fetcher: Callable[[str], bytes] = fetch_official_document,
) -> dict[str, Any]:
    source_path = source_database.resolve()
    app_path = app_database.resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Source database does not exist: {source_path}")
    if not app_path.is_file():
        raise FileNotFoundError(f"App database does not exist: {app_path}")

    documents = parse_current_document_calendar(
        calendar_html,
        source_url=calendar_source_url,
        start_year=start_year,
        through_date=as_of_date,
    )
    meeting_ids = sorted({item["meeting_id"] for item in documents})
    source = sqlite3.connect(
        f"file:{source_path.as_posix()}?mode=ro",
        uri=True,
    )
    try:
        known_meetings = {
            row[0]
            for row in source.execute(
                "SELECT meeting_id FROM fomc_meeting"
            ).fetchall()
        }
    finally:
        source.close()
    unknown_meetings = sorted(set(meeting_ids) - known_meetings)
    if unknown_meetings:
        raise RuntimeError(
            f"Document calendar contains meetings absent from source DB: {unknown_meetings}"
        )

    app = sqlite3.connect(
        f"file:{app_path.as_posix()}?mode=rw",
        uri=True,
    )
    app.execute("PRAGMA foreign_keys = ON")
    manifest_documents = []
    cache_status_counts: dict[str, int] = {}
    try:
        for item in documents:
            local_path = (
                cache_directory.resolve()
                / item["meeting_id"]
                / f"{item['document_type']}.html"
            )
            cached = _cache_or_verify(
                item["source_url"],
                local_path,
                document_fetcher,
            )
            document_id = ingest_local_document(
                app,
                local_path,
                meeting_id=item["meeting_id"],
                document_type=item["document_type"],
                publication_at=item["publication_at"],
                usage_class=item["usage_class"],
                source_url=item["source_url"],
                expected_sha256=cached["sha256"],
            )
            cache_status = str(cached["cache_status"])
            cache_status_counts[cache_status] = (
                cache_status_counts.get(cache_status, 0) + 1
            )
            manifest_documents.append(
                {
                    **item,
                    "document_id": document_id,
                    "local_path": cached["local_path"],
                    "byte_length": cached["byte_length"],
                    "content_hash": cached["sha256"],
                }
            )
        app.commit()
    except Exception:
        app.rollback()
        raise
    finally:
        app.close()

    payload = {
        "schema_version": 1,
        "as_of_date": as_of_date.isoformat(),
        "calendar_source_url": calendar_source_url,
        "source_database": str(source_path),
        "source_database_sha256": _sha256_file(source_path),
        "meeting_count": len(meeting_ids),
        "document_count": len(manifest_documents),
        "documents": manifest_documents,
    }
    manifest_hash = _sha256_bytes(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    payload["manifest_hash"] = manifest_hash
    resolved_manifest = manifest_path.resolve()
    if resolved_manifest.exists():
        existing = json.loads(resolved_manifest.read_text(encoding="utf-8"))
        if existing != payload:
            raise RuntimeError(
                f"Existing document manifest differs: {resolved_manifest}"
            )
    else:
        resolved_manifest.parent.mkdir(parents=True, exist_ok=True)
        with resolved_manifest.open("x", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
    return {
        "meeting_count": len(meeting_ids),
        "document_count": len(manifest_documents),
        "manifest_path": str(resolved_manifest),
        "manifest_hash": manifest_hash,
        "cache_status_counts": cache_status_counts,
    }


def rebind_verified_manifest(
    *,
    source_database: Path,
    app_database: Path,
    prior_manifest_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Bind immutable cached evidence to a new source DB hash without refetching it."""
    source_path = source_database.resolve()
    app_path = app_database.resolve()
    prior_path = prior_manifest_path.resolve()
    output_path = manifest_path.resolve()
    if not source_path.is_file() or not app_path.is_file() or not prior_path.is_file():
        raise FileNotFoundError("Source DB, app DB and prior manifest must all exist")

    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    documents = prior.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("Prior manifest has no documents")

    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    try:
        known_meetings = {
            row[0] for row in source.execute("SELECT meeting_id FROM fomc_meeting")
        }
    finally:
        source.close()

    verified = 0
    for item in documents:
        meeting_id = item["meeting_id"]
        if meeting_id not in known_meetings:
            raise RuntimeError(f"Manifest meeting is absent from source DB: {meeting_id}")
        local_path = Path(item["local_path"])
        if not local_path.is_file():
            raise FileNotFoundError(f"Cached evidence is missing: {local_path}")
        actual_hash = _sha256_file(local_path)
        if actual_hash != item["content_hash"]:
            raise RuntimeError(f"Cached evidence hash mismatch: {local_path}")
        verified += 1

    app = sqlite3.connect(f"file:{app_path.as_posix()}?mode=rw", uri=True)
    app.execute("PRAGMA foreign_keys = ON")
    try:
        for item in documents:
            document_id = ingest_local_document(
                app,
                Path(item["local_path"]),
                meeting_id=item["meeting_id"],
                document_type=item["document_type"],
                publication_at=item["publication_at"],
                usage_class=item["usage_class"],
                source_url=item["source_url"],
                expected_sha256=item["content_hash"],
            )
            if document_id != item["document_id"]:
                raise RuntimeError(
                    f"Cached evidence document_id changed: {item['document_id']}"
                )
        integrity = app.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = app.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"App DB evidence ingestion failed: integrity={integrity}, "
                f"foreign_keys={foreign_key_errors}"
            )
        app.commit()
    except Exception:
        app.rollback()
        raise
    finally:
        app.close()

    payload = {
        key: value
        for key, value in prior.items()
        if key not in {"manifest_hash", "source_database", "source_database_sha256"}
    }
    payload["source_database"] = str(source_path)
    payload["source_database_sha256"] = _sha256_file(source_path)
    payload["rebound_from_manifest"] = str(prior_path)
    payload["rebind_mode"] = "OFFLINE_VERIFIED_CACHED_EVIDENCE"
    payload["manifest_hash"] = _sha256_bytes(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    if output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if existing != payload:
            raise RuntimeError(f"Existing rebound manifest differs: {output_path}")
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("x", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
    return {
        "meeting_count": payload["meeting_count"],
        "document_count": payload["document_count"],
        "verified_cached_documents": verified,
        "manifest_path": str(output_path),
        "manifest_hash": payload["manifest_hash"],
        "source_database_sha256": payload["source_database_sha256"],
        "rebind_mode": payload["rebind_mode"],
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize the R5 45-case official statement/minutes corpus."
    )
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--cache-directory",
        type=Path,
        default=Path("official_documents/current_45"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("document_manifests/current_45_as_of_2026-08-27.json"),
    )
    parser.add_argument("--start-year", type=int, default=2021)
    parser.add_argument(
        "--as-of-date",
        type=date.fromisoformat,
        default=date(2026, 8, 27),
    )
    parser.add_argument("--expected-meetings", type=int, default=45)
    parser.add_argument(
        "--reuse-manifest",
        type=Path,
        help="Offline rebind of already hashed cached evidence; performs no web fetch.",
    )
    args = parser.parse_args()
    if args.reuse_manifest:
        report = rebind_verified_manifest(
            source_database=args.source,
            app_database=args.app,
            prior_manifest_path=args.reuse_manifest,
            manifest_path=args.manifest,
        )
    else:
        report = materialize_current_documents(
            source_database=args.source,
            app_database=args.app,
            cache_directory=args.cache_directory,
            manifest_path=args.manifest,
            calendar_html=fetch_text(CURRENT_CALENDAR_URL),
            calendar_source_url=CURRENT_CALENDAR_URL,
            start_year=args.start_year,
            as_of_date=args.as_of_date,
        )
    if report["meeting_count"] != args.expected_meetings:
        raise RuntimeError(
            f"Expected {args.expected_meetings} meetings, got {report['meeting_count']}"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
