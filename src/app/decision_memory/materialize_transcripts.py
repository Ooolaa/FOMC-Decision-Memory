from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Callable

from pypdf import PdfReader

from decision_memory.app_db import create_schema
from decision_memory.documents import ingest_local_document
from decision_memory.fed_documents import fetch_official_document
from decision_memory.materialize_documents import (
    _cache_or_verify,
    _sha256_bytes,
    _sha256_file,
)
from decision_memory.transcript_calendar import (
    parse_historical_transcript_calendar,
    select_transcript_sample,
)
from decision_memory.transcripts import persist_transcript_segments, split_speaker_segments
from fomc_calendar import fetch_text


HISTORICAL_PAGE_TEMPLATE = (
    "https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
)
EXTRACTION_VERSION = "pypdf_speaker_regex_v3_inline_handoff_no_period"


def extract_transcript_pdf(path: Path) -> dict[str, Any]:
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not text.strip():
        raise RuntimeError(f"Transcript PDF has no extractable text: {path}")
    return {"page_count": len(reader.pages), "text": text}


def _aggregate_segment_hash(connection: sqlite3.Connection, document_id: str) -> str:
    rows = connection.execute(
        """
        SELECT ordinal, speaker_label, participant_id, content_hash
        FROM transcript_segment
        WHERE document_id = ?
        ORDER BY ordinal
        """,
        (document_id,),
    ).fetchall()
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def materialize_transcripts(
    *,
    source_database: Path,
    app_database: Path,
    cache_directory: Path,
    manifest_path: Path,
    start_year: int,
    end_year: int,
    as_of_date: date,
    target_count: int,
    minimum_segments_per_document: int = 10,
    page_fetcher: Callable[[str], str] = fetch_text,
    document_fetcher: Callable[[str], bytes] = fetch_official_document,
    transcript_text_extractor: Callable[[Path], dict[str, Any]] = extract_transcript_pdf,
) -> dict[str, Any]:
    if start_year > end_year:
        raise ValueError("start_year must not exceed end_year")
    if minimum_segments_per_document <= 0:
        raise ValueError("minimum_segments_per_document must be positive")
    source_path = source_database.resolve()
    app_path = app_database.resolve()
    if not source_path.is_file() or not app_path.is_file():
        raise FileNotFoundError("Source and app databases must already exist")

    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    try:
        source_meetings = [
            {"meeting_id": row[0], "meeting_end_date": row[1]}
            for row in source.execute(
                """
                SELECT meeting_id, meeting_end_date
                FROM fomc_meeting
                WHERE CAST(substr(meeting_start_date, 1, 4) AS INTEGER)
                      BETWEEN ? AND ?
                ORDER BY meeting_start_date
                """,
                (start_year, end_year),
            ).fetchall()
        ]
    finally:
        source.close()
    if not source_meetings:
        raise RuntimeError("Source database has no meetings in requested years")

    available = []
    page_urls = []
    for year in range(start_year, end_year + 1):
        page_url = HISTORICAL_PAGE_TEMPLATE.format(year=year)
        page_urls.append(page_url)
        available.extend(
            parse_historical_transcript_calendar(
                page_fetcher(page_url),
                source_url=page_url,
                source_meetings=source_meetings,
                through_date=as_of_date,
            )
        )
    available_keys = [item["meeting_id"] for item in available]
    if len(available_keys) != len(set(available_keys)):
        raise RuntimeError("Historical transcript calendar produced duplicate meetings")
    selected = select_transcript_sample(available, target_count=target_count)

    app = sqlite3.connect(f"file:{app_path.as_posix()}?mode=rw", uri=True)
    app.execute("PRAGMA foreign_keys = ON")
    manifest_documents = []
    try:
        create_schema(app)
        for item in selected:
            local_path = (
                cache_directory.resolve() / item["meeting_id"] / "transcript.pdf"
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
                document_type="transcript",
                publication_at=item["publication_at"],
                usage_class=item["usage_class"],
                source_url=item["source_url"],
                expected_sha256=cached["sha256"],
            )
            extracted = transcript_text_extractor(local_path)
            segments = split_speaker_segments(str(extracted["text"]))
            if len(segments) < minimum_segments_per_document:
                raise RuntimeError(
                    f"Transcript {item['meeting_id']} produced only {len(segments)} "
                    "speaker segments"
                )
            segment_report = persist_transcript_segments(
                app,
                document_id=document_id,
                meeting_id=item["meeting_id"],
                segments=segments,
            )
            manifest_documents.append(
                {
                    **item,
                    "document_id": document_id,
                    "local_path": cached["local_path"],
                    "byte_length": cached["byte_length"],
                    "content_hash": cached["sha256"],
                    "page_count": int(extracted["page_count"]),
                    **segment_report,
                    "segment_manifest_hash": _aggregate_segment_hash(app, document_id),
                }
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

    payload = {
        "schema_version": 1,
        "extraction_version": EXTRACTION_VERSION,
        "as_of_date": as_of_date.isoformat(),
        "start_year": start_year,
        "end_year": end_year,
        "target_count": target_count,
        "available_transcript_count": len(available),
        "selection_policy": "evenly_spaced_chronological_sample_v1",
        "publication_policy": "conservative_official_page_last_update",
        "source_database": str(source_path),
        "source_database_sha256": _sha256_file(source_path),
        "calendar_source_urls": page_urls,
        "documents": manifest_documents,
    }
    payload["manifest_hash"] = _sha256_bytes(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    resolved_manifest = manifest_path.resolve()
    if resolved_manifest.exists():
        existing = json.loads(resolved_manifest.read_text(encoding="utf-8"))
        if existing != payload:
            raise RuntimeError(f"Existing transcript manifest differs: {resolved_manifest}")
    else:
        resolved_manifest.parent.mkdir(parents=True, exist_ok=True)
        with resolved_manifest.open("x", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
    return {
        "available_transcript_count": len(available),
        "selected_transcript_count": len(manifest_documents),
        "segment_count": sum(item["segment_count"] for item in manifest_documents),
        "resolved_participant_segment_count": sum(
            item["resolved_participant_segment_count"]
            for item in manifest_documents
        ),
        "manifest_path": str(resolved_manifest),
        "manifest_hash": payload["manifest_hash"],
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def resegment_transcripts_from_manifest(
    *,
    app_database: Path,
    source_manifest_path: Path,
    output_manifest_path: Path,
    minimum_segments_per_document: int = 10,
    transcript_text_extractor: Callable[[Path], dict[str, Any]] = extract_transcript_pdf,
) -> dict[str, Any]:
    if minimum_segments_per_document <= 0:
        raise ValueError("minimum_segments_per_document must be positive")
    app_path = app_database.resolve()
    source_manifest_file = source_manifest_path.resolve()
    output_manifest_file = output_manifest_path.resolve()
    if not app_path.is_file() or not source_manifest_file.is_file():
        raise FileNotFoundError("Candidate database and source manifest must exist")
    if app_path.name.casefold() == "fomc_simulation.sqlite":
        raise ValueError("Transcript resegmentation must not write to the formal database")

    source_manifest = json.loads(source_manifest_file.read_text(encoding="utf-8"))
    source_documents = source_manifest.get("documents")
    if not isinstance(source_documents, list) or not source_documents:
        raise ValueError("Source transcript manifest has no documents")

    prepared = []
    for item in source_documents:
        local_path = Path(str(item["local_path"])).resolve()
        if not local_path.is_file():
            raise FileNotFoundError(f"Cached transcript is missing: {local_path}")
        content_hash = _sha256_file(local_path)
        if content_hash != item["content_hash"]:
            raise RuntimeError(f"Cached transcript hash differs: {local_path}")
        extracted = transcript_text_extractor(local_path)
        segments = split_speaker_segments(str(extracted["text"]))
        if len(segments) < minimum_segments_per_document:
            raise RuntimeError(
                f"Transcript {item['meeting_id']} produced only {len(segments)} "
                "speaker segments"
            )
        prepared.append((item, extracted, segments))

    app = sqlite3.connect(f"file:{app_path.as_posix()}?mode=rw", uri=True)
    app.execute("PRAGMA foreign_keys = ON")
    manifest_documents = []
    try:
        app.execute("BEGIN IMMEDIATE")
        for item, extracted, segments in prepared:
            document = app.execute(
                """
                SELECT meeting_id, document_type, content_hash
                FROM document_source
                WHERE document_id = ?
                """,
                (item["document_id"],),
            ).fetchone()
            expected = (
                item["meeting_id"],
                "transcript",
                item["content_hash"],
            )
            if document != expected:
                raise RuntimeError(
                    "Candidate transcript provenance differs for "
                    f"{item['document_id']}: {document!r}"
                )
            app.execute(
                "DELETE FROM transcript_segment WHERE document_id = ?",
                (item["document_id"],),
            )
            segment_report = persist_transcript_segments(
                app,
                document_id=item["document_id"],
                meeting_id=item["meeting_id"],
                segments=segments,
            )
            manifest_documents.append(
                {
                    **item,
                    "page_count": int(extracted["page_count"]),
                    **segment_report,
                    "segment_manifest_hash": _aggregate_segment_hash(
                        app, item["document_id"]
                    ),
                }
            )
        integrity = app.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = app.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"App DB validation failed: integrity={integrity}, "
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
        for key, value in source_manifest.items()
        if key not in {"documents", "extraction_version", "manifest_hash"}
    }
    payload.update(
        {
            "extraction_version": EXTRACTION_VERSION,
            "source_manifest": str(source_manifest_file),
            "source_manifest_sha256": _sha256_file(source_manifest_file),
            "app_database": str(app_path),
            "app_database_sha256": _sha256_file(app_path),
            "documents": manifest_documents,
        }
    )
    payload["manifest_hash"] = _sha256_bytes(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output_manifest_file.parent.mkdir(parents=True, exist_ok=True)
    if output_manifest_file.exists():
        if output_manifest_file.read_text(encoding="utf-8") != serialized:
            raise RuntimeError(
                f"Existing resegmentation manifest differs: {output_manifest_file}"
            )
    else:
        output_manifest_file.write_text(serialized, encoding="utf-8")

    return {
        "document_count": len(manifest_documents),
        "segment_count": sum(item["segment_count"] for item in manifest_documents),
        "resolved_participant_segment_count": sum(
            item["resolved_participant_segment_count"]
            for item in manifest_documents
        ),
        "manifest_path": str(output_manifest_file),
        "manifest_hash": payload["manifest_hash"],
        "app_database_sha256": payload["app_database_sha256"],
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize a finite official FOMC transcript sample."
    )
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--cache-directory",
        type=Path,
        default=Path("official_documents/transcripts_2006_2020_sample50"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("document_manifests/transcripts_2006_2020_sample50.json"),
    )
    parser.add_argument("--start-year", type=int, default=2006)
    parser.add_argument("--end-year", type=int, default=2020)
    parser.add_argument("--target-count", type=int, default=50)
    parser.add_argument(
        "--as-of-date", type=date.fromisoformat, default=date(2026, 8, 28)
    )
    parser.add_argument("--resegment-from-manifest", type=Path)
    parser.add_argument("--output-manifest", type=Path)
    args = parser.parse_args()
    if args.resegment_from_manifest is not None:
        if args.output_manifest is None:
            parser.error("--output-manifest is required with --resegment-from-manifest")
        report = resegment_transcripts_from_manifest(
            app_database=args.app,
            source_manifest_path=args.resegment_from_manifest,
            output_manifest_path=args.output_manifest,
        )
    else:
        if args.output_manifest is not None:
            parser.error("--output-manifest requires --resegment-from-manifest")
        report = materialize_transcripts(
            source_database=args.source,
            app_database=args.app,
            cache_directory=args.cache_directory,
            manifest_path=args.manifest,
            start_year=args.start_year,
            end_year=args.end_year,
            as_of_date=args.as_of_date,
            target_count=args.target_count,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
