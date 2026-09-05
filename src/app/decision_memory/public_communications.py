from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from decision_memory.app_db import create_schema
from decision_memory.documents import ingest_local_document
from decision_memory.fed_documents import (
    cache_official_document,
    fetch_official_document,
    is_official_federal_reserve_url,
)


_EXCLUDED_CLASS_MARKERS = (
    "footnote",
    "share",
    "related",
    "lastupdate",
)
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_POLICY_TERMS = (
    "monetary policy",
    "economic outlook",
    "inflation",
    "price stability",
    "maximum employment",
    "employment",
    "labor market",
    "federal funds rate",
    "dual mandate",
)


def _normalize(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class _BoardCommunicationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._article_depth: int | None = None
        self._excluded_depth: int | None = None
        self._capture_depth: int | None = None
        self._capture_kind: str | None = None
        self._parts: list[str] = []
        self._body_ended = False
        self.publication_date_text: str | None = None
        self.speaker_label: str | None = None
        self.title: str | None = None
        self.paragraphs: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered = tag.casefold()
        if lowered == "br" and self._capture_depth is not None:
            self._parts.append(" ")
        if lowered == "hr" and self._article_depth is not None:
            self._body_ended = True
        if lowered in _VOID_TAGS:
            return
        self._depth += 1
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if attributes.get("id", "").casefold() == "article":
            self._article_depth = self._depth
            self._body_ended = False
        if self._article_depth is None:
            return
        classes = attributes.get("class", "").casefold()
        if self._excluded_depth is None and any(
            marker in classes for marker in _EXCLUDED_CLASS_MARKERS
        ):
            self._excluded_depth = self._depth
        if self._excluded_depth is not None or self._capture_depth is not None:
            return
        if lowered == "h3":
            self._begin_capture("title")
        elif lowered == "p":
            if "article__time" in classes:
                self._begin_capture("publication_date")
            elif "speaker" in classes:
                self._begin_capture("speaker")
            elif "location" not in classes and not self._body_ended:
                self._begin_capture("body")

    def _begin_capture(self, kind: str) -> None:
        self._capture_depth = self._depth
        self._capture_kind = kind
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture_depth is not None:
            self._parts.append(data)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered = tag.casefold()
        if lowered == "br" and self._capture_depth is not None:
            self._parts.append(" ")
        elif lowered == "hr" and self._article_depth is not None:
            self._body_ended = True

    def handle_endtag(self, tag: str) -> None:
        if self._capture_depth == self._depth:
            value = _normalize("".join(self._parts))
            if value:
                if self._capture_kind == "publication_date":
                    self.publication_date_text = value
                elif self._capture_kind == "speaker":
                    self.speaker_label = value
                elif self._capture_kind == "title":
                    self.title = value
                elif self._capture_kind == "body" and self.title is not None:
                    self.paragraphs.append(value)
            self._capture_depth = None
            self._capture_kind = None
            self._parts = []
        if self._excluded_depth == self._depth:
            self._excluded_depth = None
        if self._article_depth == self._depth:
            self._article_depth = None
        self._depth = max(0, self._depth - 1)


class _BoardSpeechLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.casefold() != "a":
            return
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if attributes.get("href"):
            self.hrefs.append(attributes["href"])


def parse_board_speech_links(content: bytes, index_url: str) -> list[str]:
    if not is_official_federal_reserve_url(index_url):
        raise ValueError("Board speech index must use an official Federal Reserve URL")
    parser = _BoardSpeechLinkParser()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    links = set()
    for href in parser.hrefs:
        absolute = urljoin(index_url, href)
        parsed = urlparse(absolute)
        if not is_official_federal_reserve_url(absolute):
            continue
        if not parsed.path.casefold().startswith("/newsevents/speech/"):
            continue
        if not parsed.path.casefold().endswith((".htm", ".html")):
            continue
        links.add(absolute)
    return sorted(links)


def policy_relevance_score(title: str, text: str) -> int:
    normalized_title = _normalize(title).casefold()
    normalized_text = _normalize(text).casefold()
    return sum(
        4 * normalized_title.count(term) + min(5, normalized_text.count(term))
        for term in _POLICY_TERMS
    )


def resolve_participant_id(
    connection: sqlite3.Connection,
    speaker_label: str,
) -> str:
    normalized = _normalize(speaker_label)
    if not normalized:
        raise ValueError("speaker_label is required")
    surname = normalized.rstrip(".").split()[-1].casefold()
    rows = connection.execute(
        "SELECT participant_id, display_name FROM participant"
    ).fetchall()
    surname_matches = [
        (str(participant_id), str(display_name))
        for participant_id, display_name in rows
        if str(display_name).rstrip(".").split()[-1].casefold() == surname
    ]
    exact_suffix = [
        participant_id
        for participant_id, display_name in surname_matches
        if normalized.casefold().endswith(display_name.casefold())
    ]
    matches = exact_suffix or [item[0] for item in surname_matches]
    if len(matches) != 1:
        raise ValueError(
            f"Speaker does not resolve to exactly one participant: {speaker_label!r}"
        )
    return matches[0]


def parse_board_communication(content: bytes) -> dict[str, Any]:
    parser = _BoardCommunicationParser()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    missing = [
        name
        for name, value in (
            ("publication date", parser.publication_date_text),
            ("speaker", parser.speaker_label),
            ("title", parser.title),
            ("body paragraphs", parser.paragraphs),
        )
        if not value
    ]
    if missing:
        raise ValueError("Board communication is missing " + ", ".join(missing))
    try:
        publication_date = datetime.strptime(
            str(parser.publication_date_text), "%B %d, %Y"
        ).date().isoformat()
    except ValueError as error:
        raise ValueError(
            f"Unsupported Board publication date: {parser.publication_date_text}"
        ) from error
    return {
        "publication_date": publication_date,
        "speaker_label": str(parser.speaker_label),
        "title": str(parser.title),
        "paragraphs": parser.paragraphs,
    }


def persist_public_communication(
    connection: sqlite3.Connection,
    *,
    document_id: str,
    participant_id: str,
    title: str,
    text: str,
) -> str:
    source = connection.execute(
        """
        SELECT document_type, usage_class
        FROM document_source WHERE document_id = ?
        """,
        (document_id,),
    ).fetchone()
    if source is None:
        raise ValueError(f"Unknown document_id: {document_id}")
    if source[0] not in {"speech", "testimony", "interview"}:
        raise ValueError("Public communication requires a supported document_type")
    if source[1] != "persona_evidence":
        raise ValueError("Public communication must use persona_evidence")
    if connection.execute(
        "SELECT 1 FROM participant WHERE participant_id = ?",
        (participant_id,),
    ).fetchone() is None:
        raise ValueError(f"Unknown participant_id: {participant_id}")
    normalized_title = _normalize(title)
    normalized_text = _normalize(text)
    if not normalized_title or not normalized_text:
        raise ValueError("Public communication requires title and text")
    content_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    connection.execute(
        """
        INSERT OR IGNORE INTO public_communication (
            document_id, participant_id, title, text, content_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            participant_id,
            normalized_title,
            normalized_text,
            content_hash,
            _utc_now(),
        ),
    )
    persisted = connection.execute(
        """
        SELECT participant_id, title, text, content_hash
        FROM public_communication WHERE document_id = ?
        """,
        (document_id,),
    ).fetchone()
    expected = (
        participant_id,
        normalized_title,
        normalized_text,
        content_hash,
    )
    if persisted != expected:
        raise ValueError("Existing public communication conflicts with supplied data")
    return document_id


def board_speech_index_url(year: int) -> str:
    if year < 2006 or year > 2100:
        raise ValueError(f"Unsupported Board speech archive year: {year}")
    filename = f"{year}-speeches.htm" if year >= 2011 else f"{year}speech.htm"
    return f"https://www.federalreserve.gov/newsevents/{filename}"


def materialize_board_speeches(
    app_database: Path,
    *,
    years: list[int],
    cache_root: Path,
    fetcher: Callable[[str], bytes] = fetch_official_document,
    max_documents: int | None = None,
) -> dict[str, Any]:
    if not years:
        raise ValueError("At least one archive year is required")
    if max_documents is not None and max_documents <= 0:
        raise ValueError("max_documents must be positive")
    app_path = app_database.resolve()
    if not app_path.is_file():
        raise FileNotFoundError(f"App database does not exist: {app_path}")
    cache_path = cache_root.resolve()
    cache_path.mkdir(parents=True, exist_ok=True)
    app = sqlite3.connect(app_path)
    app.execute("PRAGMA foreign_keys = ON")
    ingested = []
    unmatched = []
    link_count = 0
    new_cache_count = 0
    try:
        create_schema(app)
        stop = False
        for year in sorted(set(years)):
            index_url = board_speech_index_url(year)
            links = parse_board_speech_links(fetcher(index_url), index_url)
            link_count += len(links)
            for source_url in links:
                if max_documents is not None and len(ingested) >= max_documents:
                    stop = True
                    break
                filename = Path(urlparse(source_url).path).name
                local_path = cache_path / str(year) / filename
                if local_path.is_file():
                    content = local_path.read_bytes()
                else:
                    cache_official_document(
                        source_url,
                        local_path,
                        fetcher=fetcher,
                    )
                    content = local_path.read_bytes()
                    new_cache_count += 1
                parsed = parse_board_communication(content)
                try:
                    participant_id = resolve_participant_id(
                        app, parsed["speaker_label"]
                    )
                except ValueError as error:
                    unmatched.append(
                        {
                            "source_url": source_url,
                            "speaker_label": parsed["speaker_label"],
                            "reason": str(error),
                        }
                    )
                    continue
                document_id = ingest_local_document(
                    app,
                    local_path,
                    meeting_id=None,
                    document_type="speech",
                    publication_at=f"{parsed['publication_date']}T23:59:59Z",
                    usage_class="persona_evidence",
                    source_url=source_url,
                )
                text = "\n".join(parsed["paragraphs"])
                persist_public_communication(
                    app,
                    document_id=document_id,
                    participant_id=participant_id,
                    title=parsed["title"],
                    text=text,
                )
                app.commit()
                ingested.append(
                    {
                        "document_id": document_id,
                        "participant_id": participant_id,
                        "publication_date": parsed["publication_date"],
                        "speaker_label": parsed["speaker_label"],
                        "title": parsed["title"],
                        "source_url": source_url,
                        "local_path": str(local_path),
                        "content_sha256": hashlib.sha256(content).hexdigest(),
                        "policy_relevance_score": policy_relevance_score(
                            parsed["title"], text
                        ),
                    }
                )
            if stop:
                break
        integrity = app.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = app.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"App DB validation failed: integrity={integrity}, "
                f"foreign_keys={foreign_key_errors}"
            )
    finally:
        app.close()
    return {
        "schema_version": "board_public_communication_ingest_v1",
        "status": "COMPLETED",
        "app_database": str(app_path),
        "years": sorted(set(years)),
        "archive_link_count": link_count,
        "ingested_document_count": len(ingested),
        "policy_relevant_document_count": sum(
            item["policy_relevance_score"] > 0 for item in ingested
        ),
        "new_cache_file_count": new_cache_count,
        "unmatched_speaker_count": len(unmatched),
        "unmatched_speakers": unmatched,
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
        "documents": ingested,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest cutoff-safe Board speeches as named persona evidence."
    )
    parser.add_argument(
        "--app",
        type=Path,
        default=Path("fomc_simulation.vote_core_candidate.sqlite"),
    )
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path("official_documents/public_communications/board"),
    )
    parser.add_argument("--max-documents", type=int)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.end_year < args.start_year:
        raise ValueError("end-year must not precede start-year")
    report = materialize_board_speeches(
        args.app,
        years=list(range(args.start_year, args.end_year + 1)),
        cache_root=args.cache_root,
        max_documents=args.max_documents,
    )
    if args.report is not None:
        resolved = args.report.resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            report, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        if resolved.exists() and resolved.read_text(encoding="utf-8") != serialized:
            raise FileExistsError(f"Refusing to overwrite report: {resolved}")
        if not resolved.exists():
            resolved.write_text(serialized, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
