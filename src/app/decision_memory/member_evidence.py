from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from decision_memory.public_communications import policy_relevance_score


CONCERN_TAXONOMY = {
    "inflation": {
        "label": "通膨與物價穩定",
        "terms": (
            "inflation",
            "price stability",
            "prices",
            "disinflation",
            "inflation expectations",
        ),
    },
    "labor": {
        "label": "勞動市場與就業",
        "terms": (
            "labor market",
            "employment",
            "unemployment",
            "jobs",
            "wage",
            "payroll",
        ),
    },
    "growth": {
        "label": "經濟成長與需求",
        "terms": (
            "economic growth",
            "economic activity",
            "output",
            "gdp",
            "consumption",
            "demand",
            "soft landing",
        ),
    },
    "financial_conditions": {
        "label": "金融情勢、信用與房市",
        "terms": (
            "financial conditions",
            "credit",
            "banking",
            "liquidity",
            "balance sheet",
            "housing",
            "mortgage",
        ),
    },
    "policy_path": {
        "label": "利率路徑與貨幣政策傳導",
        "terms": (
            "monetary policy",
            "federal funds rate",
            "interest rate",
            "rate cuts",
            "rate hikes",
            "restrictive",
            "neutral rate",
            "policy transmission",
        ),
    },
}


def _term_count(text: str, term: str) -> int:
    return len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", text.casefold()))


def _topic_document_score(document: dict[str, Any], terms: tuple[str, ...]) -> int:
    title = str(document.get("title", ""))
    text = str(document.get("text", ""))
    return sum(
        4 * _term_count(title, term) + min(8, _term_count(text, term))
        for term in terms
    )


def infer_concerns(
    communications: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Infer issue salience from deterministic term counts, not from an LLM."""
    scored = []
    for topic_id, specification in CONCERN_TAXONOMY.items():
        document_scores = [
            (
                _topic_document_score(document, specification["terms"]),
                str(document["document_id"]),
            )
            for document in communications
        ]
        score = sum(item[0] for item in document_scores)
        if score <= 0:
            continue
        evidence_ids = [
            document_id
            for document_score, document_id in sorted(
                document_scores,
                key=lambda item: (-item[0], item[1]),
            )
            if document_score > 0
        ][:3]
        scored.append(
            {
                "topic_id": topic_id,
                "label": specification["label"],
                "score": score,
                "evidence_ids": evidence_ids,
                "method": "deterministic_term_score",
            }
        )
    scored.sort(key=lambda item: (-item["score"], item["topic_id"]))
    selected = scored[:limit]
    total = sum(item["score"] for item in selected)
    for item in selected:
        item["share_within_top_topics"] = item["score"] / total if total else 0.0
    return selected


def _source_url(source_locator: str) -> str | None:
    try:
        locator = json.loads(source_locator)
    except (json.JSONDecodeError, TypeError):
        return None
    for key in ("source_url", "source_pdf_url", "source_page_url"):
        value = locator.get(key)
        if isinstance(value, str) and value.startswith("https://"):
            return value
    return None


def _short_excerpt(text: str, *, word_limit: int = 20) -> str:
    normalized = " ".join(text.split())
    words = re.findall(r"\S+", normalized)
    if not words:
        return ""
    terms = tuple(
        term
        for specification in CONCERN_TAXONOMY.values()
        for term in specification["terms"]
    )
    positions = [
        normalized.casefold().find(term)
        for term in terms
        if normalized.casefold().find(term) >= 0
    ]
    start = 0
    if positions:
        anchor_word = len(normalized[: min(positions)].split())
        start = max(0, anchor_word - 5)
    excerpt_words = words[start : start + word_limit]
    prefix = "… " if start > 0 else ""
    suffix = " …" if start + word_limit < len(words) else ""
    return prefix + " ".join(excerpt_words) + suffix


def load_member_vote_history(
    app: sqlite3.Connection,
    participant_id: str,
    source_meeting_id: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    rows = app.execute(
        """
        SELECT vote.meeting_id, vote.voter_choice, vote.dissent,
               outcome.action_class
        FROM participant_vote AS vote
        LEFT JOIN meeting_outcome AS outcome
          ON outcome.meeting_id = vote.meeting_id
        WHERE vote.participant_id = ?
          AND vote.meeting_id <= ?
        ORDER BY vote.meeting_id DESC, vote.vote_round DESC
        LIMIT ?
        """,
        (participant_id, source_meeting_id, limit),
    ).fetchall()
    return [
        {
            "meeting_id": str(meeting_id),
            "actual_policy_action": str(action_class) if action_class else None,
            "voter_choice": str(voter_choice),
            "dissent": bool(dissent),
        }
        for meeting_id, voter_choice, dissent, action_class in rows
    ]


def load_member_communications(
    communications: sqlite3.Connection,
    participant_id: str,
    as_of_date: str,
    *,
    query_limit: int = 30,
    display_limit: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = communications.execute(
        """
        SELECT communication.document_id, source.publication_at,
               communication.title, communication.text,
               source.source_locator
        FROM public_communication AS communication
        JOIN document_source AS source USING (document_id)
        WHERE communication.participant_id = ?
          AND date(source.publication_at) <= date(?)
          AND source.usage_class = 'persona_evidence'
        ORDER BY source.publication_at DESC, communication.document_id
        LIMIT ?
        """,
        (participant_id, as_of_date, query_limit),
    ).fetchall()
    documents = []
    for document_id, publication_at, title, text, source_locator in rows:
        document = {
            "document_id": str(document_id),
            "publication_date": str(publication_at)[:10],
            "title": str(title),
            "text": str(text),
            "source_url": _source_url(str(source_locator)),
        }
        document["importance_score"] = policy_relevance_score(
            document["title"], document["text"]
        )
        documents.append(document)
    concerns = infer_concerns(documents)
    important = sorted(
        (document for document in documents if document["importance_score"] > 0),
        key=lambda item: (
            -int(item["importance_score"]),
            str(item["publication_date"]),
            str(item["document_id"]),
        ),
        reverse=False,
    )[:display_limit]
    return [
        {
            "document_id": document["document_id"],
            "publication_date": document["publication_date"],
            "title": document["title"],
            "source_url": document["source_url"],
            "importance_score": document["importance_score"],
            "excerpt": _short_excerpt(document["text"]),
        }
        for document in important
    ], concerns


def enrich_voter_rows(
    app: sqlite3.Connection,
    voter_forecast: dict[str, Any],
    *,
    communications_database: Path | None,
    forecast_as_of: str,
) -> None:
    communications = None
    if communications_database is not None and communications_database.is_file():
        communications = sqlite3.connect(
            f"file:{communications_database.resolve().as_posix()}?mode=ro",
            uri=True,
        )
    try:
        for row in voter_forecast["rows"]:
            row["vote_history"] = load_member_vote_history(
                app,
                row["participant_id"],
                voter_forecast["source_meeting_id"],
            )
            row["important_communications"] = []
            row["inferred_concerns"] = []
            if communications is not None:
                important, concerns = load_member_communications(
                    communications,
                    row["participant_id"],
                    forecast_as_of,
                )
                row["important_communications"] = important
                row["inferred_concerns"] = concerns
            row["communication_document_count"] = len(
                row["important_communications"]
            )
            row["communication_evidence_status"] = (
                "AVAILABLE"
                if row["important_communications"]
                else "NO_LOCAL_CUTOFF_SAFE_EVIDENCE"
            )
    finally:
        if communications is not None:
            communications.close()
