from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Callable

from decision_memory.documents import ingest_local_document
from decision_memory.fed_documents import fetch_official_document
from decision_memory.historical_document_calendar import (
    parse_historical_document_calendar,
)
from decision_memory.materialize_documents import (
    _cache_or_verify,
    _sha256_bytes,
    _sha256_file,
)
from fomc_calendar import fetch_text


HISTORICAL_PAGE_TEMPLATE = (
    "https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
)
HISTORICAL_DOCUMENT_EXCEPTIONS = {
    "FOMC-2007-06-27": {
        "required_document_types": ["minutes"],
        "reason": "The official historical page lists minutes but no statement.",
    },
    "FOMC-2020-03-02": {
        "required_document_types": ["statement"],
        "reason": (
            "The official historical page says these minutes are at the end of "
            "the March 15 minutes; no separate minutes URL exists."
        ),
    },
}
HISTORICAL_DOCUMENT_OVERRIDES = {
    ("FOMC-2007-06-27", "minutes"): {
        "publication_at": "2007-07-19T23:59:59Z",
        "source_url": "https://www.federalreserve.gov/fomc/minutes/20070628.htm",
        "calendar_source_url": (
            "https://www.federalreserve.gov/monetarypolicy/fomchistorical2007.htm"
        ),
        "reason": "Official page special markup is not enclosed by the standard panel.",
    },
    ("FOMC-2020-03-02", "statement"): {
        "publication_at": "2020-03-03T23:59:59Z",
        "source_url": (
            "https://www.federalreserve.gov/newsevents/pressreleases/"
            "monetary20200303a.htm"
        ),
        "calendar_source_url": (
            "https://www.federalreserve.gov/monetarypolicy/fomchistorical2020.htm"
        ),
        "reason": "The unscheduled meeting statement was released the following day.",
    },
}


def materialize_historical_documents(
    *,
    source_database: Path,
    app_database: Path,
    cache_directory: Path,
    manifest_path: Path,
    start_year: int,
    end_year: int,
    as_of_date: date,
    page_fetcher: Callable[[str], str] = fetch_text,
    document_fetcher: Callable[[str], bytes] = fetch_official_document,
) -> dict[str, Any]:
    if start_year > end_year:
        raise ValueError("start_year must not exceed end_year")
    source_path = source_database.resolve()
    app_path = app_database.resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Source database does not exist: {source_path}")
    if not app_path.is_file():
        raise FileNotFoundError(f"App database does not exist: {app_path}")

    source = sqlite3.connect(
        f"file:{source_path.as_posix()}?mode=ro",
        uri=True,
    )
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

    documents: list[dict[str, str]] = []
    for year in range(start_year, end_year + 1):
        page_url = HISTORICAL_PAGE_TEMPLATE.format(year=year)
        html = page_fetcher(page_url)
        documents.extend(
            parse_historical_document_calendar(
                html,
                source_url=page_url,
                source_meetings=source_meetings,
                through_date=as_of_date,
            )
        )
    selected_meeting_ids = {meeting["meeting_id"] for meeting in source_meetings}
    existing_keys = {
        (item["meeting_id"], item["document_type"]) for item in documents
    }
    applied_overrides = {}
    for key, override in HISTORICAL_DOCUMENT_OVERRIDES.items():
        meeting_id, document_type = key
        if meeting_id not in selected_meeting_ids or key in existing_keys:
            continue
        documents.append(
            {
                "meeting_id": meeting_id,
                "document_type": document_type,
                "publication_at": override["publication_at"],
                "publication_precision": "date",
                "usage_class": "label_only",
                "source_url": override["source_url"],
                "calendar_source_url": override["calendar_source_url"],
            }
        )
        applied_overrides[f"{meeting_id}/{document_type}"] = override
    document_keys = [
        (item["meeting_id"], item["document_type"]) for item in documents
    ]
    if len(document_keys) != len(set(document_keys)):
        raise RuntimeError("Historical pages produced duplicate document keys")
    types_by_meeting = {
        meeting["meeting_id"]: sorted(
            item["document_type"]
            for item in documents
            if item["meeting_id"] == meeting["meeting_id"]
        )
        for meeting in source_meetings
    }
    missing_or_incomplete = {
        meeting_id: document_types
        for meeting_id, document_types in types_by_meeting.items()
        if document_types
        != sorted(
            HISTORICAL_DOCUMENT_EXCEPTIONS.get(
                meeting_id,
                {"required_document_types": ["minutes", "statement"]},
            )["required_document_types"]
        )
    }
    if missing_or_incomplete:
        raise RuntimeError(
            "Historical statement/minutes corpus is incomplete for source meetings: "
            f"{missing_or_incomplete}"
        )

    app = sqlite3.connect(
        f"file:{app_path.as_posix()}?mode=rw",
        uri=True,
    )
    app.execute("PRAGMA foreign_keys = ON")
    manifest_documents = []
    cache_status_counts: Counter[str] = Counter()
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
            cache_status_counts[str(cached["cache_status"])] += 1
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

    registered_exceptions = {
        meeting_id: HISTORICAL_DOCUMENT_EXCEPTIONS[meeting_id]
        for meeting_id in sorted(types_by_meeting)
        if meeting_id in HISTORICAL_DOCUMENT_EXCEPTIONS
    }
    payload = {
        "schema_version": 1,
        "as_of_date": as_of_date.isoformat(),
        "start_year": start_year,
        "end_year": end_year,
        "source_database": str(source_path),
        "source_database_sha256": _sha256_file(source_path),
        "meeting_count": len(source_meetings),
        "document_count": len(manifest_documents),
        "registered_document_exceptions": registered_exceptions,
        "applied_document_overrides": applied_overrides,
        "documents": sorted(
            manifest_documents,
            key=lambda item: (item["meeting_id"], item["document_type"]),
        ),
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
        "meeting_count": len(source_meetings),
        "document_count": len(manifest_documents),
        "manifest_path": str(resolved_manifest),
        "manifest_hash": manifest_hash,
        "cache_status_counts": dict(sorted(cache_status_counts.items())),
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
        "registered_exception_count": len(registered_exceptions),
        "applied_override_count": len(applied_overrides),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize official historical FOMC statements and minutes."
    )
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--cache-directory",
        type=Path,
        default=Path("official_documents/training_2006_2020"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("document_manifests/training_2006_2020.json"),
    )
    parser.add_argument("--start-year", type=int, default=2006)
    parser.add_argument("--end-year", type=int, default=2020)
    parser.add_argument(
        "--as-of-date",
        type=date.fromisoformat,
        default=date(2026, 8, 27),
    )
    args = parser.parse_args()
    report = materialize_historical_documents(
        source_database=args.source,
        app_database=args.app,
        cache_directory=args.cache_directory,
        manifest_path=args.manifest,
        start_year=args.start_year,
        end_year=args.end_year,
        as_of_date=args.as_of_date,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
