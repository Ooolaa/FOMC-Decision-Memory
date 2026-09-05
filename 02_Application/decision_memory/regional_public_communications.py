from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from pypdf import PdfReader

from decision_memory.app_db import create_schema
from decision_memory.documents import ingest_local_document
from decision_memory.fed_documents import (
    cache_official_document,
    fetch_official_document,
    is_official_federal_reserve_url,
)
from decision_memory.public_communications import (
    _normalize,
    persist_public_communication,
    policy_relevance_score,
    resolve_participant_id,
)


_DALLAS_INDEX_URL = "https://www.dallasfed.org/news/speeches/logan"
_CHICAGO_INDEX_URL = "https://www.chicagofed.org/people/g/austan-goolsbee"
_BOSTON_INDEX_URL = "https://www.bostonfed.org/news-and-events/speeches.aspx"
_SAN_FRANCISCO_INDEX_URL = (
    "https://www.frbsf.org/news-and-media/speeches/mary-c-daly/"
)
_RICHMOND_INDEX_ROOT = (
    "https://www.richmondfed.org/press_room/speeches/thomas_i_barkin"
)
_DALLAS_PATH = re.compile(
    r"^/news/speeches/logan/(?P<year>\d{4})/[a-z0-9-]+/?$",
    re.IGNORECASE,
)
_DALLAS_DATED_SLUG = re.compile(r"^lkl(?P<date>\d{6})$", re.IGNORECASE)
_CHICAGO_PATH = re.compile(
    r"^/publications/speeches/(?P<year>\d{4})/[a-z0-9-]+/?$",
    re.IGNORECASE,
)
_BOSTON_PATH = re.compile(
    r"^/news-and-events/speeches/(?P<year>\d{4})/[a-z0-9-]+\.aspx$",
    re.IGNORECASE,
)
_SAN_FRANCISCO_PATH = re.compile(
    r"^/news-and-media/speeches/mary-c-daly/"
    r"(?P<year>\d{4})/(?P<month>\d{2})/(?P<slug>[a-z0-9-]+)/?$",
    re.IGNORECASE,
)
_RICHMOND_PATH = re.compile(
    r"^/press_room/speeches/thomas_i_barkin/"
    r"(?P<year>\d{4})/(?P<slug>barkin(?:_speech)?_\d{8}(?:_en)?)/?$",
    re.IGNORECASE,
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
_EXCLUDED_CLASS_MARKERS = (
    "footnote",
    "related",
    "subscribe",
    "profile",
    "share",
)


class _LinkParser(HTMLParser):
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


def parse_dallas_speech_links(
    content: bytes,
    index_url: str,
    *,
    years: set[int],
) -> list[str]:
    if not years:
        raise ValueError("At least one Dallas archive year is required")
    parsed_index = urlparse(index_url)
    if (
        not is_official_federal_reserve_url(index_url)
        or parsed_index.hostname not in {"dallasfed.org", "www.dallasfed.org"}
    ):
        raise ValueError("Dallas speech index must use the official Dallas Fed host")
    parser = _LinkParser()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    links = set()
    for href in parser.hrefs:
        absolute = urljoin(index_url, href)
        parsed = urlparse(absolute)
        match = _DALLAS_PATH.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"dallasfed.org", "www.dallasfed.org"}
            or match is None
            or int(match.group("year")) not in years
        ):
            continue
        links.add(parsed._replace(query="", fragment="").geturl())
    return sorted(links)


def parse_chicago_speech_links(
    content: bytes,
    index_url: str,
    *,
    years: set[int],
) -> list[str]:
    if not years:
        raise ValueError("At least one Chicago archive year is required")
    parsed_index = urlparse(index_url)
    if (
        not is_official_federal_reserve_url(index_url)
        or parsed_index.hostname not in {"chicagofed.org", "www.chicagofed.org"}
    ):
        raise ValueError("Chicago speech index must use the official Chicago Fed host")
    parser = _LinkParser()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    links = set()
    for href in parser.hrefs:
        absolute = urljoin(index_url, href)
        parsed = urlparse(absolute)
        match = _CHICAGO_PATH.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"chicagofed.org", "www.chicagofed.org"}
            or match is None
            or int(match.group("year")) not in years
        ):
            continue
        links.add(parsed._replace(query="", fragment="").geturl())
    return sorted(links)


def parse_boston_speech_links(
    content: bytes,
    index_url: str,
    *,
    years: set[int],
) -> list[str]:
    if not years:
        raise ValueError("At least one Boston archive year is required")
    parsed_index = urlparse(index_url)
    if (
        not is_official_federal_reserve_url(index_url)
        or parsed_index.hostname not in {"bostonfed.org", "www.bostonfed.org"}
    ):
        raise ValueError("Boston speech index must use the official Boston Fed host")
    parser = _LinkParser()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    links = set()
    for href in parser.hrefs:
        absolute = urljoin(index_url, href)
        parsed = urlparse(absolute)
        match = _BOSTON_PATH.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"bostonfed.org", "www.bostonfed.org"}
            or match is None
            or int(match.group("year")) not in years
        ):
            continue
        links.add(parsed._replace(query="", fragment="").geturl())
    return sorted(links)


def parse_san_francisco_speech_links(
    content: bytes,
    index_url: str,
    *,
    years: set[int],
) -> list[str]:
    if not years:
        raise ValueError("At least one San Francisco archive year is required")
    parsed_index = urlparse(index_url)
    if (
        not is_official_federal_reserve_url(index_url)
        or parsed_index.hostname not in {"frbsf.org", "www.frbsf.org"}
    ):
        raise ValueError(
            "San Francisco speech index must use the official San Francisco Fed host"
        )
    parser = _LinkParser()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    links = set()
    for href in parser.hrefs:
        absolute = urljoin(index_url, href)
        parsed = urlparse(absolute)
        match = _SAN_FRANCISCO_PATH.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"frbsf.org", "www.frbsf.org"}
            or match is None
            or int(match.group("year")) not in years
        ):
            continue
        normalized_path = parsed.path.rstrip("/") + "/"
        links.add(parsed._replace(path=normalized_path, query="", fragment="").geturl())
    return sorted(links)


def parse_richmond_barkin_speech_links(
    content: bytes,
    index_url: str,
    *,
    years: set[int],
) -> list[str]:
    if not years:
        raise ValueError("At least one Richmond archive year is required")
    parsed_index = urlparse(index_url)
    if (
        not is_official_federal_reserve_url(index_url)
        or parsed_index.hostname not in {"richmondfed.org", "www.richmondfed.org"}
    ):
        raise ValueError("Richmond speech index must use the official Richmond Fed host")
    decoded = content.decode("utf-8-sig", errors="replace")
    parser = _LinkParser()
    parser.feed(decoded)
    candidates = list(parser.hrefs)
    stripped = decoded.lstrip()
    if stripped.startswith("<?xml") or stripped.startswith("<rss"):
        try:
            root = ET.fromstring(decoded)
        except ET.ParseError as error:
            raise ValueError("Invalid Richmond RSS archive") from error
        candidates.extend(
            element.text.strip()
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1].casefold() == "link"
            and element.text
            and element.text.strip()
        )
    links = set()
    for href in candidates:
        absolute = urljoin(index_url, href)
        parsed = urlparse(absolute)
        match = _RICHMOND_PATH.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"richmondfed.org", "www.richmondfed.org"}
            or match is None
            or int(match.group("year")) not in years
        ):
            continue
        links.add(parsed._replace(query="", fragment="").geturl().rstrip("/"))
    return sorted(links)


class _DallasCommunicationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._content_depth: int | None = None
        self._excluded_depth: int | None = None
        self._capture_depth: int | None = None
        self._capture_kind: str | None = None
        self._parts: list[str] = []
        self.publication_date_text: str | None = None
        self.speaker_label: str | None = None
        self.title: str | None = None
        self.paragraphs: list[str] = []

    def _begin_capture(self, kind: str) -> None:
        self._capture_depth = self._depth
        self._capture_kind = kind
        self._parts = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered = tag.casefold()
        if lowered == "br" and self._capture_depth is not None:
            self._parts.append(" ")
        if lowered in _VOID_TAGS:
            return
        self._depth += 1
        attributes = {key.casefold(): value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").casefold().split())
        if self._content_depth is None and "dal-main-content" in classes:
            self._content_depth = self._depth
        if self._content_depth is None:
            return
        class_text = " ".join(classes)
        if self._excluded_depth is None and any(
            marker in class_text for marker in _EXCLUDED_CLASS_MARKERS
        ):
            self._excluded_depth = self._depth
        if (
            lowered == "span"
            and "date" in classes
            and self._capture_kind == "body"
        ):
            self._capture_depth = None
            self._capture_kind = None
            self._parts = []
            self._begin_capture("publication_date")
            return
        if self._excluded_depth is not None or self._capture_depth is not None:
            return
        if lowered == "div" and "dal-crouton" in classes:
            self._begin_capture("speaker")
        elif lowered == "h1" and "dal-headline" in classes:
            self._begin_capture("title")
        elif lowered == "p" and "dal-content-date" in classes:
            self._begin_capture("publication_date")
        elif lowered == "span" and "date" in classes:
            self._begin_capture("publication_date")
        elif (
            lowered == "p"
            and self.title is not None
            and self.publication_date_text is not None
        ):
            self._begin_capture("body")

    def handle_data(self, data: str) -> None:
        if self._capture_depth is not None:
            self._parts.append(data)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.casefold() == "br" and self._capture_depth is not None:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._capture_depth == self._depth:
            value = _normalize("".join(self._parts))
            if value:
                if self._capture_kind == "speaker":
                    self.speaker_label = value.removeprefix("Speech by ").strip()
                elif self._capture_kind == "title":
                    self.title = value
                elif self._capture_kind == "publication_date":
                    self.publication_date_text = value
                elif self._capture_kind == "body":
                    self.paragraphs.append(value)
            self._capture_depth = None
            self._capture_kind = None
            self._parts = []
        if self._excluded_depth == self._depth:
            self._excluded_depth = None
        if self._content_depth == self._depth:
            self._content_depth = None
        self._depth = max(0, self._depth - 1)


def _dallas_slug_publication_date(source_url: str) -> str:
    parsed = urlparse(source_url)
    path_match = _DALLAS_PATH.fullmatch(parsed.path)
    slug_match = _DALLAS_DATED_SLUG.fullmatch(Path(parsed.path).name)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"dallasfed.org", "www.dallasfed.org"}
        or path_match is None
        or slug_match is None
    ):
        raise ValueError("Dallas publication fallback requires an official dated slug")
    try:
        publication_date = datetime.strptime(
            slug_match.group("date"), "%y%m%d"
        ).date()
    except ValueError as error:
        raise ValueError("Dallas speech slug contains an invalid date") from error
    if publication_date.year != int(path_match.group("year")):
        raise ValueError("Dallas speech slug date conflicts with archive year")
    return publication_date.isoformat()


def parse_dallas_communication(
    content: bytes,
    *,
    source_url: str | None = None,
) -> dict[str, Any]:
    parser = _DallasCommunicationParser()
    fallback_publication_date = None
    if source_url is not None:
        fallback_publication_date = _dallas_slug_publication_date(source_url)
        parser.publication_date_text = datetime.fromisoformat(
            fallback_publication_date
        ).strftime("%B %d, %Y")
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
        raise ValueError("Dallas communication is missing " + ", ".join(missing))
    try:
        publication_date = datetime.strptime(
            str(parser.publication_date_text), "%B %d, %Y"
        ).date().isoformat()
    except ValueError as error:
        raise ValueError(
            f"Unsupported Dallas publication date: {parser.publication_date_text}"
        ) from error
    return {
        "publication_date": publication_date,
        "speaker_label": str(parser.speaker_label),
        "title": str(parser.title),
        "paragraphs": parser.paragraphs,
    }


class _ChicagoCommunicationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._title_container_depth: int | None = None
        self._text_depth: int | None = None
        self._capture_depth: int | None = None
        self._capture_kind: str | None = None
        self._parts: list[str] = []
        self.publication_date_text: str | None = None
        self.title: str | None = None
        self.paragraphs: list[str] = []

    def _begin_capture(self, kind: str) -> None:
        self._capture_depth = self._depth
        self._capture_kind = kind
        self._parts = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered = tag.casefold()
        if lowered == "br" and self._capture_depth is not None:
            self._parts.append(" ")
        if lowered in _VOID_TAGS:
            return
        self._depth += 1
        attributes = {key.casefold(): value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").casefold().split())
        if "cfeddetail__title" in classes:
            self._title_container_depth = self._depth
        if "cfedcontent__text" in classes:
            self._text_depth = self._depth
        if self._capture_depth is not None:
            return
        if "cfeddetail__lastupdated" in classes:
            self._begin_capture("publication_date")
        elif lowered == "h1" and self._title_container_depth is not None:
            self._begin_capture("title")
        elif self._text_depth is not None and lowered in {"p", "li", "h2", "h3"}:
            self._begin_capture("body")

    def handle_data(self, data: str) -> None:
        if self._capture_depth is not None:
            self._parts.append(data)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.casefold() == "br" and self._capture_depth is not None:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._capture_depth == self._depth:
            value = _normalize("".join(self._parts))
            if value:
                if self._capture_kind == "publication_date":
                    self.publication_date_text = value
                elif self._capture_kind == "title":
                    self.title = value
                elif self._capture_kind == "body":
                    self.paragraphs.append(value)
            self._capture_depth = None
            self._capture_kind = None
            self._parts = []
        if self._title_container_depth == self._depth:
            self._title_container_depth = None
        if self._text_depth == self._depth:
            self._text_depth = None
        self._depth = max(0, self._depth - 1)


def parse_chicago_communication(content: bytes) -> dict[str, Any]:
    decoded = content.decode("utf-8-sig", errors="replace")
    normalized_page = _normalize(decoded).casefold()
    if "austan" not in normalized_page or "goolsbee" not in normalized_page:
        raise ValueError("Chicago communication does not identify Austan Goolsbee")
    parser = _ChicagoCommunicationParser()
    parser.feed(decoded)
    missing = [
        name
        for name, value in (
            ("last updated date", parser.publication_date_text),
            ("title", parser.title),
            ("body paragraphs", parser.paragraphs),
        )
        if not value
    ]
    if missing:
        raise ValueError("Chicago communication is missing " + ", ".join(missing))
    date_match = re.search(
        r"Last Updated:\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        str(parser.publication_date_text),
        flags=re.IGNORECASE,
    )
    if date_match is None:
        raise ValueError(
            f"Unsupported Chicago publication date: {parser.publication_date_text}"
        )
    date_text = date_match.group(1)
    date_format = "%m/%d/%Y" if len(date_text.rsplit("/", 1)[-1]) == 4 else "%m/%d/%y"
    try:
        publication_date = datetime.strptime(date_text, date_format).date().isoformat()
    except ValueError as error:
        raise ValueError(f"Invalid Chicago publication date: {date_text}") from error
    return {
        "publication_date": publication_date,
        "speaker_label": "Austan D. Goolsbee",
        "title": str(parser.title),
        "paragraphs": parser.paragraphs,
    }


class _BostonCommunicationParser(HTMLParser):
    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self._depth = 0
        self._capture_depth: int | None = None
        self._capture_kind: str | None = None
        self._parts: list[str] = []
        self._pending_pdf_href: str | None = None
        self.publication_at: str | None = None
        self.speaker_label: str | None = None
        self.title: str | None = None
        self.pdf_url: str | None = None

    def _metadata(self, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if (
            attributes.get("property", "").casefold()
            == "article:published_time"
            and attributes.get("content")
        ):
            self.publication_at = attributes["content"].strip()
        if (
            attributes.get("itemprop", "").casefold() == "name"
            and attributes.get("content")
        ):
            self.title = _normalize(attributes["content"])

    def _begin_capture(self, kind: str) -> None:
        self._capture_depth = self._depth
        self._capture_kind = kind
        self._parts = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if lowered == "meta":
            self._metadata(attrs)
            return
        if lowered == "br" and self._capture_depth is not None:
            self._parts.append(" ")
        if lowered in _VOID_TAGS:
            return
        self._depth += 1
        classes = set(attributes.get("class", "").casefold().split())
        if self._capture_depth is not None:
            return
        if lowered == "a" and "byline-link" in classes:
            self._begin_capture("speaker")
        elif lowered == "a" and attributes.get("href"):
            absolute = urljoin(self.source_url, attributes["href"])
            parsed = urlparse(absolute)
            if (
                parsed.scheme == "https"
                and parsed.hostname in {"bostonfed.org", "www.bostonfed.org"}
                and "/documents/speeches/pdf/collins/" in parsed.path.casefold()
                and parsed.path.casefold().endswith(".pdf")
            ):
                self._pending_pdf_href = parsed._replace(
                    query="", fragment=""
                ).geturl()
                self._begin_capture("pdf_link")

    def handle_data(self, data: str) -> None:
        if self._capture_depth is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture_depth == self._depth:
            value = _normalize("".join(self._parts))
            if self._capture_kind == "speaker" and value:
                self.speaker_label = value
            elif (
                self._capture_kind == "pdf_link"
                and self._pending_pdf_href is not None
                and "full-text" in value.casefold()
            ):
                self.pdf_url = self._pending_pdf_href
            self._capture_depth = None
            self._capture_kind = None
            self._parts = []
            self._pending_pdf_href = None
        self._depth = max(0, self._depth - 1)


def parse_boston_communication(
    content: bytes,
    *,
    source_url: str,
) -> dict[str, Any]:
    parsed_source = urlparse(source_url)
    if (
        not is_official_federal_reserve_url(source_url)
        or parsed_source.hostname not in {"bostonfed.org", "www.bostonfed.org"}
        or _BOSTON_PATH.fullmatch(parsed_source.path) is None
    ):
        raise ValueError("Boston communication requires an official speech page")
    parser = _BostonCommunicationParser(source_url)
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    missing = [
        name
        for name, value in (
            ("published time", parser.publication_at),
            ("speaker byline", parser.speaker_label),
            ("title", parser.title),
            ("full-text PDF", parser.pdf_url),
        )
        if not value
    ]
    if missing:
        raise ValueError("Boston communication is missing " + ", ".join(missing))
    try:
        publication_date = datetime.fromisoformat(
            str(parser.publication_at).replace("Z", "+00:00")
        ).date().isoformat()
    except ValueError as error:
        raise ValueError(
            f"Invalid Boston published time: {parser.publication_at}"
        ) from error
    return {
        "publication_date": publication_date,
        "speaker_label": str(parser.speaker_label),
        "title": str(parser.title),
        "pdf_url": str(parser.pdf_url),
    }


class _SanFranciscoCommunicationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._main_depth: int | None = None
        self._body_depth: int | None = None
        self._body_done = False
        self._capture_depth: int | None = None
        self._capture_kind: str | None = None
        self._parts: list[str] = []
        self.publication_at: str | None = None
        self.speaker_label: str | None = None
        self.speech_info_text: str | None = None
        self.title: str | None = None
        self.paragraphs: list[str] = []

    def _begin_capture(self, kind: str) -> None:
        self._capture_depth = self._depth
        self._capture_kind = kind
        self._parts = []

    def _capture_metadata(self, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if (
            attributes.get("property", "").casefold()
            == "article:published_time"
            and attributes.get("content")
        ):
            self.publication_at = attributes["content"].strip()
        if (
            attributes.get("name", "").casefold() == "parsely-author"
            and attributes.get("content")
        ):
            self.speaker_label = _normalize(attributes["content"])

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if lowered == "meta":
            self._capture_metadata(attrs)
            return
        if lowered == "br" and self._capture_depth is not None:
            self._parts.append(" ")
        if lowered in _VOID_TAGS:
            return
        self._depth += 1
        classes = set(attributes.get("class", "").casefold().split())
        if self._main_depth is None and "sffed-main-content" in classes:
            self._main_depth = self._depth
        elif (
            self._main_depth is not None
            and self._body_depth is None
            and "entry-content" in classes
            and "wp-block-post-content" in classes
        ):
            self._body_depth = self._depth
        if "sffed-associated-people__heading--in-content-flow" in classes:
            self._body_done = True
            self._body_depth = None
        if self._capture_depth is not None:
            return
        if lowered == "h1" and "wp-block-post-title" in classes:
            self._begin_capture("title")
        elif "speech-info" in classes:
            self._begin_capture("speech_info")
        elif (
            self._body_depth is not None
            and not self._body_done
            and lowered in {"p", "li", "h2", "h3"}
        ):
            self._begin_capture("body")

    def handle_data(self, data: str) -> None:
        if self._capture_depth is not None:
            self._parts.append(data)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.casefold() == "meta":
            self._capture_metadata(attrs)
            return
        if tag.casefold() == "br" and self._capture_depth is not None:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._capture_depth == self._depth:
            value = _normalize("".join(self._parts))
            if value:
                if self._capture_kind == "title":
                    self.title = value
                elif self._capture_kind == "speech_info":
                    self.speech_info_text = value
                elif self._capture_kind == "body":
                    self.paragraphs.append(value)
            self._capture_depth = None
            self._capture_kind = None
            self._parts = []
        if self._body_depth == self._depth:
            self._body_depth = None
        if self._main_depth == self._depth:
            self._main_depth = None
        self._depth = max(0, self._depth - 1)


def parse_san_francisco_communication(
    content: bytes,
    *,
    source_url: str | None = None,
) -> dict[str, Any]:
    parser = _SanFranciscoCommunicationParser()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    if parser.speaker_label is None and parser.speech_info_text:
        speaker_match = re.search(
            r"\bBy\s+(Mary\s+C\.\s+Daly)\b",
            parser.speech_info_text,
            flags=re.IGNORECASE,
        )
        if speaker_match is not None:
            parser.speaker_label = "Mary C. Daly"
    if parser.speaker_label is None and source_url is not None:
        parsed_source = urlparse(source_url)
        if (
            is_official_federal_reserve_url(source_url)
            and parsed_source.hostname in {"frbsf.org", "www.frbsf.org"}
            and _SAN_FRANCISCO_PATH.fullmatch(parsed_source.path) is not None
        ):
            parser.speaker_label = "Mary C. Daly"
    missing = [
        name
        for name, value in (
            ("published time", parser.publication_at),
            ("speaker", parser.speaker_label),
            ("title", parser.title),
            ("body paragraphs", parser.paragraphs),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "San Francisco communication is missing " + ", ".join(missing)
        )
    if str(parser.speaker_label).casefold() != "mary c. daly":
        raise ValueError(
            "San Francisco communication does not identify Mary C. Daly"
        )
    try:
        publication_date = datetime.fromisoformat(
            str(parser.publication_at).replace("Z", "+00:00")
        ).date().isoformat()
    except ValueError as error:
        raise ValueError(
            f"Invalid San Francisco published time: {parser.publication_at}"
        ) from error
    return {
        "publication_date": publication_date,
        "speaker_label": str(parser.speaker_label),
        "title": str(parser.title),
        "paragraphs": parser.paragraphs,
    }


class _RichmondBarkinCommunicationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._body_depth: int | None = None
        self._capture_depth: int | None = None
        self._parts: list[str] = []
        self.publication_date_text: str | None = None
        self.speaker_label: str | None = None
        self.title: str | None = None
        self.paragraphs: list[str] = []

    def _capture_metadata(self, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        name = attributes.get("name", "").casefold()
        content = _normalize(attributes.get("content", ""))
        if not content:
            return
        if name == "citation_title":
            self.title = content
        elif name == "citation_author":
            if content.casefold() in {"barkin, tom", "tom barkin"}:
                self.speaker_label = "Tom Barkin"
            else:
                self.speaker_label = content
        elif name == "citation_publication_date":
            self.publication_date_text = content

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered = tag.casefold()
        if lowered == "meta":
            self._capture_metadata(attrs)
            return
        if lowered == "br" and self._capture_depth is not None:
            self._parts.append(" ")
        if lowered in _VOID_TAGS:
            return
        self._depth += 1
        attributes = {key.casefold(): value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").casefold().split())
        if self._body_depth is None and "tmplt__content" in classes:
            self._body_depth = self._depth
            return
        if (
            self._body_depth is not None
            and self._capture_depth is None
            and lowered in {"p", "li", "h2", "h3"}
        ):
            self._capture_depth = self._depth
            self._parts = []

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.casefold() == "meta":
            self._capture_metadata(attrs)
        elif tag.casefold() == "br" and self._capture_depth is not None:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._capture_depth is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture_depth == self._depth:
            value = _normalize("".join(self._parts))
            if value:
                self.paragraphs.append(value)
            self._capture_depth = None
            self._parts = []
        if self._body_depth == self._depth:
            self._body_depth = None
        self._depth = max(0, self._depth - 1)


def parse_richmond_barkin_communication(content: bytes) -> dict[str, Any]:
    parser = _RichmondBarkinCommunicationParser()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    missing = [
        name
        for name, value in (
            ("citation publication date", parser.publication_date_text),
            ("citation author", parser.speaker_label),
            ("citation title", parser.title),
            ("body paragraphs", parser.paragraphs),
        )
        if not value
    ]
    if missing:
        raise ValueError("Richmond communication is missing " + ", ".join(missing))
    if str(parser.speaker_label).casefold() != "tom barkin":
        raise ValueError("Richmond communication does not identify Tom Barkin")
    try:
        publication_date = datetime.strptime(
            str(parser.publication_date_text), "%Y/%m/%d"
        ).date().isoformat()
    except ValueError as error:
        raise ValueError(
            f"Invalid Richmond citation date: {parser.publication_date_text}"
        ) from error
    return {
        "publication_date": publication_date,
        "speaker_label": "Tom Barkin",
        "title": str(parser.title),
        "paragraphs": parser.paragraphs,
    }


def materialize_richmond_barkin_speeches(
    app_database: Path,
    *,
    years: list[int],
    cache_root: Path,
    fetcher: Callable[[str], bytes] = fetch_official_document,
    max_documents: int | None = None,
) -> dict[str, Any]:
    selected_years = set(years)
    if not selected_years:
        raise ValueError("At least one Richmond archive year is required")
    if min(selected_years) < 2018 or max(selected_years) > 2100:
        raise ValueError("Richmond Barkin years must be between 2018 and 2100")
    if max_documents is not None and max_documents <= 0:
        raise ValueError("max_documents must be positive")
    app_path = app_database.resolve()
    if not app_path.is_file():
        raise FileNotFoundError(f"App database does not exist: {app_path}")
    cache_path = cache_root.resolve()
    cache_path.mkdir(parents=True, exist_ok=True)
    links: set[str] = set()
    for year in sorted(selected_years):
        index_url = f"{_RICHMOND_INDEX_ROOT}/{year}?cc_view=rss"
        links.update(
            parse_richmond_barkin_speech_links(
                fetcher(index_url),
                index_url,
                years=selected_years,
            )
        )
    selected_links = sorted(links)
    if max_documents is not None:
        selected_links = selected_links[:max_documents]
    app = sqlite3.connect(app_path)
    app.execute("PRAGMA foreign_keys = ON")
    ingested = []
    new_cache_count = 0
    try:
        create_schema(app)
        for source_url in selected_links:
            parsed_url = urlparse(source_url)
            match = _RICHMOND_PATH.fullmatch(parsed_url.path)
            if match is None:
                raise ValueError(f"Unexpected Richmond speech URL: {source_url}")
            page_path = (
                cache_path
                / "pages"
                / match.group("year")
                / f"{match.group('slug')}.html"
            )
            if page_path.is_file():
                page_content = page_path.read_bytes()
            else:
                cache_official_document(source_url, page_path, fetcher=fetcher)
                page_content = page_path.read_bytes()
                new_cache_count += 1
            try:
                parsed = parse_richmond_barkin_communication(page_content)
            except ValueError as error:
                raise ValueError(
                    f"Failed to parse Richmond speech {source_url}: {error}"
                ) from error
            participant_id = resolve_participant_id(app, parsed["speaker_label"])
            document_id = ingest_local_document(
                app,
                page_path,
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
                    "local_path": str(page_path),
                    "sha256": hashlib.sha256(page_content).hexdigest(),
                    "policy_relevance_score": policy_relevance_score(
                        parsed["title"], text
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
    finally:
        app.close()
    return {
        "schema_version": "richmond_barkin_public_communication_ingest_v1",
        "status": "COMPLETED",
        "app_database": str(app_path),
        "years": sorted(selected_years),
        "archive_link_count": len(selected_links),
        "ingested_document_count": len(ingested),
        "policy_relevant_document_count": sum(
            item["policy_relevance_score"] > 0 for item in ingested
        ),
        "new_cache_file_count": new_cache_count,
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
        "documents": ingested,
    }


def materialize_san_francisco_daly_speeches(
    app_database: Path,
    *,
    years: list[int],
    cache_root: Path,
    fetcher: Callable[[str], bytes] = fetch_official_document,
    max_documents: int | None = None,
) -> dict[str, Any]:
    selected_years = set(years)
    if not selected_years:
        raise ValueError("At least one San Francisco archive year is required")
    if min(selected_years) < 2018 or max(selected_years) > 2100:
        raise ValueError("San Francisco Daly years must be between 2018 and 2100")
    if max_documents is not None and max_documents <= 0:
        raise ValueError("max_documents must be positive")
    app_path = app_database.resolve()
    if not app_path.is_file():
        raise FileNotFoundError(f"App database does not exist: {app_path}")
    cache_path = cache_root.resolve()
    cache_path.mkdir(parents=True, exist_ok=True)
    links: set[str] = set()
    for page_number in range(1, 7):
        index_url = (
            _SAN_FRANCISCO_INDEX_URL
            if page_number == 1
            else f"{_SAN_FRANCISCO_INDEX_URL}page/{page_number}/"
        )
        links.update(
            parse_san_francisco_speech_links(
                fetcher(index_url),
                index_url,
                years=selected_years,
            )
        )
    selected_links = sorted(links)
    if max_documents is not None:
        selected_links = selected_links[:max_documents]
    app = sqlite3.connect(app_path)
    app.execute("PRAGMA foreign_keys = ON")
    ingested = []
    skipped = []
    new_cache_count = 0
    try:
        create_schema(app)
        for source_url in selected_links:
            parsed_url = urlparse(source_url)
            match = _SAN_FRANCISCO_PATH.fullmatch(parsed_url.path)
            if match is None:
                raise ValueError(
                    f"Unexpected San Francisco speech URL: {source_url}"
                )
            page_path = (
                cache_path
                / "pages"
                / match.group("year")
                / match.group("month")
                / f"{match.group('slug')}.html"
            )
            if page_path.is_file():
                page_content = page_path.read_bytes()
            else:
                cache_official_document(source_url, page_path, fetcher=fetcher)
                page_content = page_path.read_bytes()
                new_cache_count += 1
            try:
                parsed = parse_san_francisco_communication(
                    page_content, source_url=source_url
                )
            except ValueError as error:
                if (
                    str(error)
                    == "San Francisco communication is missing body paragraphs"
                ):
                    skipped.append(
                        {
                            "source_url": source_url,
                            "local_path": str(page_path),
                            "sha256": hashlib.sha256(page_content).hexdigest(),
                            "reason": "missing_speech_body",
                        }
                    )
                    continue
                raise ValueError(
                    f"Failed to parse San Francisco speech {source_url}: {error}"
                ) from error
            participant_id = resolve_participant_id(app, parsed["speaker_label"])
            document_id = ingest_local_document(
                app,
                page_path,
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
                    "local_path": str(page_path),
                    "sha256": hashlib.sha256(page_content).hexdigest(),
                    "policy_relevance_score": policy_relevance_score(
                        parsed["title"], text
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
    finally:
        app.close()
    return {
        "schema_version": "san_francisco_daly_public_communication_ingest_v1",
        "status": "COMPLETED",
        "app_database": str(app_path),
        "years": sorted(selected_years),
        "archive_link_count": len(selected_links),
        "ingested_document_count": len(ingested),
        "skipped_document_count": len(skipped),
        "skipped_documents": skipped,
        "policy_relevant_document_count": sum(
            item["policy_relevance_score"] > 0 for item in ingested
        ),
        "new_cache_file_count": new_cache_count,
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
        "documents": ingested,
    }


def _extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not _normalize(text):
        raise ValueError(f"PDF text extraction produced no content: {path}")
    return text


def materialize_boston_collins_speeches(
    app_database: Path,
    *,
    years: list[int],
    cache_root: Path,
    fetcher: Callable[[str], bytes] = fetch_official_document,
    text_extractor: Callable[[Path], str] = _extract_pdf_text,
    max_documents: int | None = None,
) -> dict[str, Any]:
    selected_years = set(years)
    if not selected_years:
        raise ValueError("At least one Boston archive year is required")
    if min(selected_years) < 2022 or max(selected_years) > 2100:
        raise ValueError("Boston Collins years must be between 2022 and 2100")
    if max_documents is not None and max_documents <= 0:
        raise ValueError("max_documents must be positive")
    app_path = app_database.resolve()
    if not app_path.is_file():
        raise FileNotFoundError(f"App database does not exist: {app_path}")
    cache_path = cache_root.resolve()
    cache_path.mkdir(parents=True, exist_ok=True)
    links = parse_boston_speech_links(
        fetcher(_BOSTON_INDEX_URL),
        _BOSTON_INDEX_URL,
        years=selected_years,
    )
    if max_documents is not None:
        links = links[:max_documents]
    app = sqlite3.connect(app_path)
    app.execute("PRAGMA foreign_keys = ON")
    ingested = []
    skipped = []
    new_cache_count = 0
    try:
        create_schema(app)
        for source_url in links:
            parsed_url = urlparse(source_url)
            match = _BOSTON_PATH.fullmatch(parsed_url.path)
            if match is None:
                raise ValueError(f"Unexpected Boston speech URL: {source_url}")
            year = int(match.group("year"))
            page_path = cache_path / "pages" / str(year) / Path(parsed_url.path).name
            if page_path.is_file():
                page_content = page_path.read_bytes()
            else:
                cache_official_document(source_url, page_path, fetcher=fetcher)
                page_content = page_path.read_bytes()
                new_cache_count += 1
            try:
                parsed = parse_boston_communication(
                    page_content, source_url=source_url
                )
            except ValueError as error:
                if str(error) == "Boston communication is missing full-text PDF":
                    skipped.append(
                        {
                            "source_page_url": source_url,
                            "local_page_path": str(page_path),
                            "page_sha256": hashlib.sha256(page_content).hexdigest(),
                            "reason": "missing_full_text_pdf",
                        }
                    )
                    continue
                raise ValueError(
                    f"Failed to parse Boston speech {source_url}: {error}"
                ) from error
            pdf_url = str(parsed["pdf_url"])
            pdf_name = Path(urlparse(pdf_url).path).name
            pdf_path = cache_path / "pdf" / str(year) / pdf_name
            if pdf_path.is_file():
                pdf_content = pdf_path.read_bytes()
            else:
                cache_official_document(pdf_url, pdf_path, fetcher=fetcher)
                pdf_content = pdf_path.read_bytes()
                new_cache_count += 1
            text = text_extractor(pdf_path)
            participant_id = resolve_participant_id(app, parsed["speaker_label"])
            document_id = ingest_local_document(
                app,
                pdf_path,
                meeting_id=None,
                document_type="speech",
                publication_at=f"{parsed['publication_date']}T23:59:59Z",
                usage_class="persona_evidence",
                source_url=pdf_url,
            )
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
                    "source_page_url": source_url,
                    "source_pdf_url": pdf_url,
                    "local_page_path": str(page_path),
                    "local_pdf_path": str(pdf_path),
                    "page_sha256": hashlib.sha256(page_content).hexdigest(),
                    "pdf_sha256": hashlib.sha256(pdf_content).hexdigest(),
                    "policy_relevance_score": policy_relevance_score(
                        parsed["title"], text
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
    finally:
        app.close()
    return {
        "schema_version": "boston_collins_public_communication_ingest_v1",
        "status": "COMPLETED",
        "app_database": str(app_path),
        "years": sorted(selected_years),
        "archive_link_count": len(links),
        "ingested_document_count": len(ingested),
        "skipped_document_count": len(skipped),
        "skipped_documents": skipped,
        "policy_relevant_document_count": sum(
            item["policy_relevance_score"] > 0 for item in ingested
        ),
        "new_cache_file_count": new_cache_count,
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
        "documents": ingested,
    }


def materialize_chicago_goolsbee_speeches(
    app_database: Path,
    *,
    years: list[int],
    cache_root: Path,
    fetcher: Callable[[str], bytes] = fetch_official_document,
    max_documents: int | None = None,
) -> dict[str, Any]:
    selected_years = set(years)
    if not selected_years:
        raise ValueError("At least one Chicago archive year is required")
    if min(selected_years) < 2023 or max(selected_years) > 2100:
        raise ValueError("Chicago Goolsbee years must be between 2023 and 2100")
    if max_documents is not None and max_documents <= 0:
        raise ValueError("max_documents must be positive")
    app_path = app_database.resolve()
    if not app_path.is_file():
        raise FileNotFoundError(f"App database does not exist: {app_path}")
    cache_path = cache_root.resolve()
    cache_path.mkdir(parents=True, exist_ok=True)
    links = parse_chicago_speech_links(
        fetcher(_CHICAGO_INDEX_URL),
        _CHICAGO_INDEX_URL,
        years=selected_years,
    )
    if max_documents is not None:
        links = links[:max_documents]
    app = sqlite3.connect(app_path)
    app.execute("PRAGMA foreign_keys = ON")
    ingested = []
    new_cache_count = 0
    try:
        create_schema(app)
        for source_url in links:
            parsed_url = urlparse(source_url)
            match = _CHICAGO_PATH.fullmatch(parsed_url.path)
            if match is None:
                raise ValueError(f"Unexpected Chicago speech URL: {source_url}")
            year = int(match.group("year"))
            local_path = cache_path / str(year) / f"{Path(parsed_url.path).name}.html"
            if local_path.is_file():
                content = local_path.read_bytes()
            else:
                cache_official_document(source_url, local_path, fetcher=fetcher)
                content = local_path.read_bytes()
                new_cache_count += 1
            try:
                parsed = parse_chicago_communication(content)
            except ValueError as error:
                raise ValueError(
                    f"Failed to parse Chicago speech {source_url}: {error}"
                ) from error
            participant_id = resolve_participant_id(app, parsed["speaker_label"])
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
        "schema_version": "chicago_goolsbee_public_communication_ingest_v1",
        "status": "COMPLETED",
        "app_database": str(app_path),
        "years": sorted(selected_years),
        "archive_link_count": len(links),
        "ingested_document_count": len(ingested),
        "policy_relevant_document_count": sum(
            item["policy_relevance_score"] > 0 for item in ingested
        ),
        "new_cache_file_count": new_cache_count,
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
        "documents": ingested,
    }


def materialize_dallas_logan_speeches(
    app_database: Path,
    *,
    years: list[int],
    cache_root: Path,
    fetcher: Callable[[str], bytes] = fetch_official_document,
    max_documents: int | None = None,
) -> dict[str, Any]:
    selected_years = set(years)
    if not selected_years:
        raise ValueError("At least one Dallas archive year is required")
    if min(selected_years) < 2022 or max(selected_years) > 2100:
        raise ValueError("Dallas Logan archive years must be between 2022 and 2100")
    if max_documents is not None and max_documents <= 0:
        raise ValueError("max_documents must be positive")
    app_path = app_database.resolve()
    if not app_path.is_file():
        raise FileNotFoundError(f"App database does not exist: {app_path}")
    cache_path = cache_root.resolve()
    cache_path.mkdir(parents=True, exist_ok=True)
    links = parse_dallas_speech_links(
        fetcher(_DALLAS_INDEX_URL),
        _DALLAS_INDEX_URL,
        years=selected_years,
    )
    if max_documents is not None:
        links = links[:max_documents]
    app = sqlite3.connect(app_path)
    app.execute("PRAGMA foreign_keys = ON")
    ingested = []
    new_cache_count = 0
    try:
        create_schema(app)
        for source_url in links:
            parsed_url = urlparse(source_url)
            match = _DALLAS_PATH.fullmatch(parsed_url.path)
            if match is None:
                raise ValueError(f"Unexpected Dallas speech URL: {source_url}")
            year = int(match.group("year"))
            filename = f"{Path(parsed_url.path).name}.html"
            local_path = cache_path / str(year) / filename
            if local_path.is_file():
                content = local_path.read_bytes()
            else:
                cache_official_document(source_url, local_path, fetcher=fetcher)
                content = local_path.read_bytes()
                new_cache_count += 1
            try:
                parsed = parse_dallas_communication(
                    content, source_url=source_url
                )
            except ValueError as error:
                raise ValueError(
                    f"Failed to parse Dallas speech {source_url}: {error}"
                ) from error
            participant_id = resolve_participant_id(app, parsed["speaker_label"])
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
        "schema_version": "dallas_logan_public_communication_ingest_v1",
        "status": "COMPLETED",
        "app_database": str(app_path),
        "years": sorted(selected_years),
        "archive_link_count": len(links),
        "ingested_document_count": len(ingested),
        "policy_relevant_document_count": sum(
            item["policy_relevance_score"] > 0 for item in ingested
        ),
        "new_cache_file_count": new_cache_count,
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
        "documents": ingested,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest cutoff-safe Reserve Bank speeches as persona evidence."
    )
    parser.add_argument(
        "--source",
        choices=(
            "dallas-logan",
            "chicago-goolsbee",
            "boston-collins",
            "san-francisco-daly",
            "richmond-barkin",
        ),
        default="dallas-logan",
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
    )
    parser.add_argument("--max-documents", type=int)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.end_year < args.start_year:
        raise ValueError("end-year must not precede start-year")
    years = list(range(args.start_year, args.end_year + 1))
    if args.source == "richmond-barkin":
        report = materialize_richmond_barkin_speeches(
            args.app,
            years=years,
            cache_root=args.cache_root
            or Path("official_documents/public_communications/richmond"),
            max_documents=args.max_documents,
        )
    elif args.source == "san-francisco-daly":
        report = materialize_san_francisco_daly_speeches(
            args.app,
            years=years,
            cache_root=args.cache_root
            or Path("official_documents/public_communications/san_francisco"),
            max_documents=args.max_documents,
        )
    elif args.source == "boston-collins":
        report = materialize_boston_collins_speeches(
            args.app,
            years=years,
            cache_root=args.cache_root
            or Path("official_documents/public_communications/boston"),
            max_documents=args.max_documents,
        )
    elif args.source == "chicago-goolsbee":
        report = materialize_chicago_goolsbee_speeches(
            args.app,
            years=years,
            cache_root=args.cache_root
            or Path("official_documents/public_communications/chicago"),
            max_documents=args.max_documents,
        )
    else:
        report = materialize_dallas_logan_speeches(
            args.app,
            years=years,
            cache_root=args.cache_root
            or Path("official_documents/public_communications/dallas"),
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
