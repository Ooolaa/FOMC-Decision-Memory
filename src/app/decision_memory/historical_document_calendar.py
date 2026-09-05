from __future__ import annotations

import re
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, urlunparse


_DATE_TOKEN = re.compile(r"(?<!\d)(20\d{6})(?:[a-z])?\.htm", re.IGNORECASE)
_RELEASED = re.compile(
    r"Released\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)
_MINUTES_HREF = re.compile(
    r"(?:fomcminutes\d{8}|/fomc/minutes/\d{8}|/monetarypolicy/fomc\d{8})\.htm$",
    re.IGNORECASE,
)


def _normalized(parts: Iterable[str]) -> str:
    return " ".join("".join(parts).replace("\xa0", " ").split())


class _HistoricalPanelParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.panel_depth: int | None = None
        self.panel_text: list[str] = []
        self.panel_anchors: list[dict[str, str]] = []
        self.anchor_href: str | None = None
        self.anchor_parts: list[str] = []
        self.panels: list[dict[str, Any]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered = tag.lower()
        attrs_dict = dict(attrs)
        if lowered == "div":
            self.div_depth += 1
            classes = set((attrs_dict.get("class") or "").split())
            if self.panel_depth is None and {"panel", "panel-default"}.issubset(classes):
                self.panel_depth = self.div_depth
                self.panel_text = []
                self.panel_anchors = []
        elif lowered == "a" and self.panel_depth is not None:
            self.anchor_href = attrs_dict.get("href") or ""
            self.anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self.panel_depth is not None:
            self.panel_text.append(data)
        if self.anchor_href is not None:
            self.anchor_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "a" and self.anchor_href is not None:
            self.panel_anchors.append(
                {
                    "href": self.anchor_href,
                    "label": _normalized(self.anchor_parts),
                }
            )
            self.anchor_href = None
            self.anchor_parts = []
        if lowered != "div":
            return
        if self.panel_depth == self.div_depth:
            self.panels.append(
                {
                    "text": _normalized(self.panel_text),
                    "anchors": self.panel_anchors,
                }
            )
            self.panel_depth = None
            self.panel_text = []
            self.panel_anchors = []
        self.div_depth -= 1


def _official_https_url(base_url: str, href: str) -> str:
    resolved = urljoin(base_url, href)
    parsed = urlparse(resolved)
    hostname = (parsed.hostname or "").lower()
    if hostname != "federalreserve.gov" and not hostname.endswith(
        ".federalreserve.gov"
    ):
        raise ValueError(f"Historical document URL is not Federal Reserve: {resolved}")
    return urlunparse(parsed._replace(scheme="https"))


def _url_end_date(url: str) -> str | None:
    match = _DATE_TOKEN.search(url)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d").date().isoformat()


def _end_of_date(value: str) -> str:
    date.fromisoformat(value)
    return f"{value}T23:59:59Z"


def parse_historical_document_calendar(
    html: str,
    *,
    source_url: str,
    source_meetings: list[dict[str, str]],
    through_date: date,
) -> list[dict[str, str]]:
    meetings_by_end: dict[str, dict[str, str]] = {}
    for meeting in source_meetings:
        end_date = meeting["meeting_end_date"]
        if end_date in meetings_by_end:
            raise ValueError(f"Multiple source meetings share end date: {end_date}")
        meetings_by_end[end_date] = meeting

    parser = _HistoricalPanelParser()
    parser.feed(html)
    documents: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for panel in parser.panels:
        anchors = panel["anchors"]
        statement_anchor = next(
            (
                item
                for item in anchors
                if str(item["label"]).casefold().startswith("statement")
            ),
            None,
        )
        minutes_anchor = next(
            (
                item
                for item in anchors
                if str(item["label"]).casefold().startswith("minutes")
                or _MINUTES_HREF.search(str(item["href"]))
            ),
            None,
        )
        anchor_for_date = statement_anchor or minutes_anchor
        if anchor_for_date is None:
            continue
        end_date = _url_end_date(str(anchor_for_date["href"]))
        if end_date is None or end_date not in meetings_by_end:
            continue
        meeting = meetings_by_end[end_date]
        if date.fromisoformat(end_date) > through_date:
            continue

        if statement_anchor is not None:
            key = (meeting["meeting_id"], "statement")
            if key in seen:
                raise RuntimeError(f"Duplicate historical document: {key}")
            seen.add(key)
            documents.append(
                {
                    "meeting_id": meeting["meeting_id"],
                    "document_type": "statement",
                    "publication_at": _end_of_date(end_date),
                    "publication_precision": "date",
                    "usage_class": "label_only",
                    "source_url": _official_https_url(
                        source_url,
                        str(statement_anchor["href"]),
                    ),
                    "calendar_source_url": source_url,
                }
            )

        if minutes_anchor is not None:
            release_match = _RELEASED.search(str(panel["text"]))
            if release_match is None:
                raise RuntimeError(
                    "Historical minutes link has no deterministic release date: "
                    f"{meeting['meeting_id']}"
                )
            release_text = release_match.group(1)
            try:
                release_date = datetime.strptime(
                    release_text,
                    "%B %d, %Y",
                ).date()
            except ValueError:
                release_date = datetime.strptime(
                    release_text,
                    "%b %d, %Y",
                ).date()
            if release_date <= through_date:
                key = (meeting["meeting_id"], "minutes")
                if key in seen:
                    raise RuntimeError(f"Duplicate historical document: {key}")
                seen.add(key)
                documents.append(
                    {
                        "meeting_id": meeting["meeting_id"],
                        "document_type": "minutes",
                        "publication_at": _end_of_date(release_date.isoformat()),
                        "publication_precision": "date",
                        "usage_class": "label_only",
                        "source_url": _official_https_url(
                            source_url,
                            str(minutes_anchor["href"]),
                        ),
                        "calendar_source_url": source_url,
                    }
                )
    order = {"statement": 0, "minutes": 1}
    return sorted(
        documents,
        key=lambda item: (item["meeting_id"], order[item["document_type"]]),
    )
