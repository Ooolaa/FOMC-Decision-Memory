from __future__ import annotations

import re
import urllib.request
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Callable


CURRENT_CALENDAR_URL = (
    "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
)
HISTORICAL_CALENDAR_URL = (
    "https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
)

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _normalized_text(parts: list[str]) -> str:
    return " ".join("".join(parts).replace("\xa0", " ").split())


class _HistoricalHeadingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_heading = False
        self._parts: list[str] = []
        self.headings: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "h5":
            self._in_heading = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "h5" and self._in_heading:
            self.headings.append(_normalized_text(self._parts))
            self._in_heading = False


class _CurrentCalendarParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.current_year: int | None = None
        self.row_depth: int | None = None
        self.row: dict[str, str | int] | None = None
        self.capture_name: str | None = None
        self.capture_depth: int | None = None
        self.capture_parts: list[str] = []
        self.rows: list[tuple[int, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "div":
            return
        self.div_depth += 1
        classes = set(dict(attrs).get("class", "").split())
        if "fomc-meeting" in classes:
            self.row_depth = self.div_depth
            self.row = {"year": self.current_year or 0}
        if self.row is not None:
            if "fomc-meeting__month" in classes:
                self._start_capture("month")
            elif "fomc-meeting__date" in classes:
                self._start_capture("date")

    def _start_capture(self, name: str) -> None:
        self.capture_name = name
        self.capture_depth = self.div_depth
        self.capture_parts = []

    def handle_data(self, data: str) -> None:
        year_match = re.search(r"\b(20\d{2})\s+FOMC Meetings\b", data)
        if year_match:
            self.current_year = int(year_match.group(1))
        if self.capture_name is not None:
            self.capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "div":
            return
        if self.capture_depth == self.div_depth and self.row is not None:
            self.row[self.capture_name or ""] = _normalized_text(self.capture_parts)
            self.capture_name = None
            self.capture_depth = None
            self.capture_parts = []
        if self.row_depth == self.div_depth and self.row is not None:
            year = int(self.row.get("year", 0))
            month = str(self.row.get("month", ""))
            day_range = str(self.row.get("date", ""))
            if year and month and day_range:
                self.rows.append((year, month, day_range))
            self.row = None
            self.row_depth = None
        self.div_depth -= 1


def _parse_date_range(year: int, month_text: str, day_text: str) -> tuple[date, date]:
    month_parts = [part.strip().lower() for part in month_text.split("/")]
    if not month_parts or any(part not in _MONTHS for part in month_parts):
        raise ValueError(f"Unsupported FOMC month: {month_text}")
    day_match = re.match(r"\s*(\d{1,2})(?:\s*-\s*(\d{1,2}))?", day_text)
    if not day_match:
        raise ValueError(f"Unsupported FOMC day range: {day_text}")
    start_day = int(day_match.group(1))
    end_day = int(day_match.group(2) or start_day)
    start_month = _MONTHS[month_parts[0]]
    end_month = _MONTHS[month_parts[-1]]
    end_year = year + (1 if end_month < start_month else 0)
    return date(year, start_month, start_day), date(end_year, end_month, end_day)


def _meeting_record(start: date, end: date, source_url: str) -> dict[str, str]:
    return {
        "meeting_id": f"FOMC-{start.isoformat()}",
        "meeting_start_date": start.isoformat(),
        "meeting_end_date": end.isoformat(),
        "information_cutoff_date_et": (start - timedelta(days=1)).isoformat(),
        "cutoff_policy": "previous_calendar_day",
        "calendar_source_url": source_url,
    }


def parse_historical_calendar(html: str, source_url: str) -> list[dict[str, str]]:
    parser = _HistoricalHeadingParser()
    parser.feed(html)
    meetings: list[dict[str, str]] = []
    for heading in parser.headings:
        lowered = heading.lower()
        if "meeting" not in lowered:
            continue
        if any(term in lowered for term in ("conference call", "cancelled", "notation vote")):
            continue
        named_cross_month = re.match(
            r"^([A-Za-z]+)\s+(\d{1,2})\s*-\s*([A-Za-z]+)\s+"
            r"(\d{1,2}).*?-\s*(\d{4})$",
            heading,
        )
        if named_cross_month:
            start, end = _parse_date_range(
                int(named_cross_month.group(5)),
                f"{named_cross_month.group(1)}/{named_cross_month.group(3)}",
                f"{named_cross_month.group(2)}-{named_cross_month.group(4)}",
            )
        else:
            match = re.match(
                r"^([A-Za-z]+(?:/[A-Za-z]+)?)\s+"
                r"(\d{1,2}(?:\s*-\s*\d{1,2})?).*?-\s*(\d{4})$",
                heading,
            )
            if not match:
                raise ValueError(f"Unsupported historical FOMC heading: {heading}")
            start, end = _parse_date_range(
                int(match.group(3)),
                match.group(1),
                match.group(2),
            )
        meetings.append(_meeting_record(start, end, source_url))
    return sorted(meetings, key=lambda item: item["meeting_start_date"])


def parse_current_calendar(
    html: str,
    source_url: str,
    start_year: int,
    through_date: date,
) -> list[dict[str, str]]:
    parser = _CurrentCalendarParser()
    parser.feed(html)
    meetings: list[dict[str, str]] = []
    for year, month_text, day_text in parser.rows:
        if year < start_year or "notation vote" in day_text.lower():
            continue
        start, end = _parse_date_range(year, month_text, day_text)
        if start <= through_date:
            meetings.append(_meeting_record(start, end, source_url))
    return sorted(meetings, key=lambda item: item["meeting_start_date"])


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "fred-fomc-vintage-database/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8-sig")


def load_official_fomc_meetings(
    start_year: int = 2006,
    through_date: date | None = None,
    fetcher: Callable[[str], str] = fetch_text,
) -> list[dict[str, str]]:
    through_date = through_date or date.today()
    meetings: list[dict[str, str]] = []
    historical_end = min(2020, through_date.year)
    for year in range(start_year, historical_end + 1):
        url = HISTORICAL_CALENDAR_URL.format(year=year)
        meetings.extend(parse_historical_calendar(fetcher(url), url))
    if through_date.year >= 2021:
        meetings.extend(
            parse_current_calendar(
                fetcher(CURRENT_CALENDAR_URL),
                CURRENT_CALENDAR_URL,
                start_year=max(start_year, 2021),
                through_date=through_date,
            )
        )
    unique = {item["meeting_id"]: item for item in meetings}
    return sorted(unique.values(), key=lambda item: item["meeting_start_date"])
