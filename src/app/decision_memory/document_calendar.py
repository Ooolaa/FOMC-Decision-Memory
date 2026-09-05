from __future__ import annotations

import re
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

from fomc_calendar import _parse_date_range


_STATEMENT_HTML = re.compile(r"/monetary\d{8}a\.htm$", re.IGNORECASE)
_MINUTES_HTML = re.compile(r"/fomcminutes\d{8}\.htm$", re.IGNORECASE)
_RELEASED = re.compile(
    r"Released\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)


def _normalized(parts: list[str]) -> str:
    return " ".join("".join(parts).replace("\xa0", " ").split())


class _DocumentCalendarParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.current_year: int | None = None
        self.row_depth: int | None = None
        self.row: dict[str, object] | None = None
        self.capture_name: str | None = None
        self.capture_depth: int | None = None
        self.capture_parts: list[str] = []
        self.rows: list[dict[str, object]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attrs_dict = dict(attrs)
        if tag.lower() == "div":
            self.div_depth += 1
            classes = set((attrs_dict.get("class") or "").split())
            if "fomc-meeting" in classes:
                self.row_depth = self.div_depth
                self.row = {
                    "year": self.current_year,
                    "text_parts": [],
                    "statement_url": None,
                    "minutes_url": None,
                }
            if self.row is not None:
                if "fomc-meeting__month" in classes:
                    self._start_capture("month")
                elif "fomc-meeting__date" in classes:
                    self._start_capture("date")
        elif tag.lower() == "a" and self.row is not None:
            href = attrs_dict.get("href") or ""
            if _STATEMENT_HTML.search(href):
                self.row["statement_url"] = href
            elif _MINUTES_HTML.search(href):
                self.row["minutes_url"] = href

    def _start_capture(self, name: str) -> None:
        self.capture_name = name
        self.capture_depth = self.div_depth
        self.capture_parts = []

    def handle_data(self, data: str) -> None:
        year_match = re.search(r"\b(20\d{2})\s+FOMC Meetings\b", data)
        if year_match:
            self.current_year = int(year_match.group(1))
        if self.row is not None:
            text_parts = self.row["text_parts"]
            assert isinstance(text_parts, list)
            text_parts.append(data)
        if self.capture_name is not None:
            self.capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "div":
            return
        if self.capture_depth == self.div_depth and self.row is not None:
            self.row[self.capture_name or ""] = _normalized(self.capture_parts)
            self.capture_name = None
            self.capture_depth = None
            self.capture_parts = []
        if self.row_depth == self.div_depth and self.row is not None:
            self.row["text"] = _normalized(self.row["text_parts"])  # type: ignore[arg-type]
            self.row.pop("text_parts", None)
            self.rows.append(self.row)
            self.row = None
            self.row_depth = None
        self.div_depth -= 1


def _end_of_date(value: date) -> str:
    return f"{value.isoformat()}T23:59:59Z"


def parse_current_document_calendar(
    html: str,
    *,
    source_url: str,
    start_year: int,
    through_date: date,
) -> list[dict[str, str]]:
    parser = _DocumentCalendarParser()
    parser.feed(html)
    documents = []
    for row in parser.rows:
        year = row.get("year")
        month = row.get("month")
        day_range = row.get("date")
        if not isinstance(year, int) or year < start_year:
            continue
        if not isinstance(month, str) or not isinstance(day_range, str):
            continue
        row_text = str(row.get("text", "")).casefold()
        if "notation vote" in row_text or "cancelled" in row_text:
            continue
        start, end = _parse_date_range(year, month, day_range)
        if start > through_date:
            continue
        meeting_id = f"FOMC-{start.isoformat()}"
        statement_url = row.get("statement_url")
        if isinstance(statement_url, str) and end <= through_date:
            documents.append(
                {
                    "meeting_id": meeting_id,
                    "document_type": "statement",
                    "publication_at": _end_of_date(end),
                    "publication_precision": "date",
                    "usage_class": "label_only",
                    "source_url": urljoin(source_url, statement_url),
                    "calendar_source_url": source_url,
                }
            )
        minutes_url = row.get("minutes_url")
        release_match = _RELEASED.search(str(row.get("text", "")))
        if isinstance(minutes_url, str) and release_match:
            release_date = datetime.strptime(
                release_match.group(1),
                "%B %d, %Y",
            ).date()
            if release_date <= through_date:
                documents.append(
                    {
                        "meeting_id": meeting_id,
                        "document_type": "minutes",
                        "publication_at": _end_of_date(release_date),
                        "publication_precision": "date",
                        "usage_class": "label_only",
                        "source_url": urljoin(source_url, minutes_url),
                        "calendar_source_url": source_url,
                    }
                )
    document_order = {"statement": 0, "minutes": 1}
    return sorted(
        documents,
        key=lambda item: (
            item["meeting_id"],
            document_order[item["document_type"]],
        ),
    )
