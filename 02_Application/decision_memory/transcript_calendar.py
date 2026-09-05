from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from decision_memory.historical_document_calendar import (
    _HistoricalPanelParser,
    _end_of_date,
    _official_https_url,
)


_TRANSCRIPT_DATE = re.compile(
    r"/FOMC(20\d{6})(?:meeting|confcall)\.pdf$",
    re.IGNORECASE,
)
_LAST_UPDATE = re.compile(
    r"Last\s+Update:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)


def _page_update_date(html: str) -> date:
    match = _LAST_UPDATE.search(" ".join(html.replace("&nbsp;", " ").split()))
    if match is None:
        raise RuntimeError("Historical transcript page has no Last Update date")
    for format_string in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(match.group(1), format_string).date()
        except ValueError:
            continue
    raise RuntimeError(f"Unsupported Last Update date: {match.group(1)!r}")


def _transcript_end_date(href: str) -> str | None:
    match = _TRANSCRIPT_DATE.search(href)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d").date().isoformat()


def parse_historical_transcript_calendar(
    html: str,
    *,
    source_url: str,
    source_meetings: list[dict[str, str]],
    through_date: date,
) -> list[dict[str, str]]:
    publication_date = _page_update_date(html)
    if publication_date > through_date:
        return []
    meetings_by_end = {
        meeting["meeting_end_date"]: meeting for meeting in source_meetings
    }
    if len(meetings_by_end) != len(source_meetings):
        raise ValueError("Multiple source meetings share an end date")

    parser = _HistoricalPanelParser()
    parser.feed(html)
    documents: list[dict[str, str]] = []
    seen: set[str] = set()
    for panel in parser.panels:
        transcript_anchor = next(
            (
                anchor
                for anchor in panel["anchors"]
                if str(anchor["label"]).casefold().startswith("transcript")
                and str(anchor["href"]).casefold().endswith(".pdf")
            ),
            None,
        )
        if transcript_anchor is None:
            continue
        meeting_end_date = _transcript_end_date(str(transcript_anchor["href"]))
        if meeting_end_date is None or meeting_end_date not in meetings_by_end:
            continue
        meeting = meetings_by_end[meeting_end_date]
        meeting_id = meeting["meeting_id"]
        if meeting_id in seen:
            raise RuntimeError(f"Duplicate historical transcript: {meeting_id}")
        seen.add(meeting_id)
        documents.append(
            {
                "meeting_id": meeting_id,
                "meeting_end_date": meeting_end_date,
                "document_type": "transcript",
                "publication_at": _end_of_date(publication_date.isoformat()),
                "publication_precision": "conservative_page_last_update",
                "usage_class": "persona_evidence",
                "source_url": _official_https_url(
                    source_url,
                    str(transcript_anchor["href"]),
                ),
                "calendar_source_url": source_url,
            }
        )
    return sorted(documents, key=lambda item: (item["meeting_end_date"], item["meeting_id"]))


def select_transcript_sample(
    documents: list[dict[str, Any]],
    *,
    target_count: int,
) -> list[dict[str, Any]]:
    if target_count <= 0:
        raise ValueError("target_count must be positive")
    ordered = sorted(
        documents,
        key=lambda item: (item["meeting_end_date"], item["meeting_id"]),
    )
    if len(ordered) < target_count:
        raise RuntimeError(
            f"Only {len(ordered)} transcripts are available; need {target_count}"
        )
    if target_count == 1:
        return [ordered[0]]
    indices = [
        round(index * (len(ordered) - 1) / (target_count - 1))
        for index in range(target_count)
    ]
    if len(set(indices)) != target_count:
        raise RuntimeError("Transcript sample index calculation produced duplicates")
    return [ordered[index] for index in indices]
