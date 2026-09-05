from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]


_FOR_LINE = re.compile(
    r"^(?:Voting|Votes) for\s+"
    r"(?:(?:this|the) action|"
    r"(?:the )?(?:FOMC )?monetary policy action)(?:\s+were)?"
    r"\s*:?\s*(?P<names>.+?)\s*$",
    re.IGNORECASE,
)
_AGAINST_LINE = re.compile(
    r"^(?:Voting|Votes) against\s+"
    r"(?:(?:(?:this|the) action|the policy(?: action)?|"
    r"(?:the )?(?:FOMC )?monetary policy action)(?:\s+(?:was|were))?|"
    r"(?:was|were))"
    r"\s*:?\s*(?P<names>.+?)\s*$",
    re.IGNORECASE,
)
_LEGACY_HONORIFIC = re.compile(
    r"^(?:Messrs|Mses|Mr|Ms|Mrs|Dr)\.\s+",
    re.IGNORECASE,
)
_FULL_NAME = re.compile(
    r"\b[A-Z][A-Za-z'’\-]+"
    r"(?:\s+(?:[A-Z]\.|[A-Z][A-Za-z'’\-]+))*"
    r"\s+[A-Z][A-Za-z'’\-]+(?:\s+(?:Jr|Sr)\.?)?"
)
_NON_PERSON_MATCHES = {
    "vice chair",
    "vice chairman",
    "federal reserve",
    "board of governors",
    "open market",
    "market committee",
}

# Source-order mappings for the four minutes documents that contain two
# "Voting/Votes for this action" blocks. None explicitly excludes a round that
# has no corresponding meeting in the strict FOMC calendar or is not the
# meeting's policy-rate vote. The March 2020 document also records the earlier
# intermeeting vote, which has its own calendar meeting_id.
MULTI_VOTE_MEETING_MAPPINGS: dict[str, list[str | None]] = {
    "FOMC-2008-01-29": ["FOMC-2008-01-29", None],
    "FOMC-2008-10-28": ["FOMC-2008-10-28", None],
    "FOMC-2011-12-13": ["FOMC-2011-12-13", None],
    "FOMC-2020-03-15": ["FOMC-2020-03-15", "FOMC-2020-03-02"],
}


def _clean_paragraph(value: str) -> str:
    return " ".join(value.replace("**", "").split())


def _parse_names(value: str) -> list[str]:
    cleaned = value.strip().rstrip(".").strip()
    if re.match(r"^none\b", cleaned, re.IGNORECASE):
        return []
    if not _LEGACY_HONORIFIC.match(cleaned):
        name_region = re.split(
            r"\.\s+(?=(?:Mr|Ms|Mrs|Governor|President|Statement|The|Consistent)\b)",
            cleaned,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        name_region = re.sub(
            r",\s+(?i:who|which)\b.*?"
            r"(?=(?:;\s+(?:and\s+)?|,\s+and\s+|\s+and\s+)"
            r"[A-Z][A-Za-z'-]+(?:\s+[A-Z]\.)?"
            r"(?:\s+[A-Z][A-Za-z'-]+)+)",
            "",
            name_region,
        )
        name_region = re.split(
            r",\s+(?i:who|which)\b",
            name_region,
            maxsplit=1,
        )[0]
        names = [
            name
            for name in _FULL_NAME.findall(name_region)
            if name.casefold() not in _NON_PERSON_MATCHES
        ]
        if names:
            if len(set(names)) != len(names):
                raise ValueError("Duplicate voter name in one vote list")
            return names
    cleaned = re.sub(
        r",\s+(?:who|which)\b.*?;\s+and\s+",
        ", ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.split(
        r",\s+(?:who|which)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.split(
        r"\.\s+(?=(?:Mr|Ms|Mrs|Governor|President|Statement|The)\b)",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    if _LEGACY_HONORIFIC.match(cleaned):
        cleaned = cleaned.replace(";", ",")
        cleaned = re.sub(r",\s+and\s+", ", ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+and\s+", ", ", cleaned, flags=re.IGNORECASE)
        names = [
            _LEGACY_HONORIFIC.sub("", item.strip())
            for item in cleaned.split(",")
            if item.strip()
        ]
    else:
        names = [
            name
            for name in _FULL_NAME.findall(cleaned)
            if name.casefold() not in _NON_PERSON_MATCHES
        ]
    if not names:
        raise ValueError(f"Could not parse voter names: {value}")
    if len(set(names)) != len(names):
        raise ValueError("Duplicate voter name in one vote list")
    return names


def _vote_clauses(paragraph: str) -> list[str]:
    starts = [
        match.start()
        for match in re.finditer(
            r"\b(?:Voting|Votes)\s+(?:for|against)\b",
            paragraph,
            flags=re.IGNORECASE,
        )
    ]
    if not starts:
        return [paragraph]
    starts.append(len(paragraph))
    return [
        paragraph[starts[index] : starts[index + 1]].strip()
        for index in range(len(starts) - 1)
    ]


def parse_vote_paragraphs(
    paragraphs: Iterable[str],
    *,
    round_meeting_ids: Sequence[str | None] | None = None,
) -> list[dict[str, Any]]:
    parsed_pairs: list[tuple[list[str], list[str], bool]] = []
    pending_for: list[str] | None = None
    for paragraph in paragraphs:
        cleaned = _clean_paragraph(paragraph)
        for clause in _vote_clauses(cleaned):
            for_match = _FOR_LINE.match(clause)
            if for_match:
                if pending_for is not None:
                    parsed_pairs.append((pending_for, [], False))
                pending_for = _parse_names(for_match.group("names"))
                if not pending_for:
                    raise ValueError("A voting-for paragraph cannot be empty")
                continue
            against_match = _AGAINST_LINE.match(clause)
            if against_match:
                if pending_for is None:
                    raise ValueError("Voting-against paragraph appears before voting-for")
                against = _parse_names(against_match.group("names"))
                overlap = sorted(set(pending_for) & set(against))
                if overlap:
                    raise ValueError(f"Voters appear on both sides: {overlap}")
                parsed_pairs.append((pending_for, against, True))
                pending_for = None
                continue
            if re.match(
                r"^Votes\s+(?:for|against)(?:\s+these actions)?\s*:",
                clause,
                re.IGNORECASE,
            ):
                # Early minutes can append a separate Board vote without the
                # explicit FOMC policy-action object. It is not this meeting's
                # monetary-policy vote and must not be materialized as one.
                continue
            if re.match(r"^(?:Voting|Votes)\s+for\b", clause, re.IGNORECASE):
                raise ValueError(f"Unrecognized voting-for clause: {clause}")
            if re.match(
                r"^(?:Voting|Votes)\s+against\b", clause, re.IGNORECASE
            ):
                raise ValueError(f"Unrecognized voting-against clause: {clause}")
    if pending_for is not None:
        parsed_pairs.append((pending_for, [], False))
    if not parsed_pairs:
        raise ValueError("No complete FOMC vote block was found")
    if round_meeting_ids is None:
        if len(parsed_pairs) > 1:
            raise ValueError("Multiple vote blocks require an explicit meeting mapping")
        meeting_ids: list[str | None] = [None]
    else:
        if len(round_meeting_ids) != len(parsed_pairs):
            raise ValueError("Vote-block count does not match the explicit meeting mapping")
        if any(
            meeting_id is not None and not meeting_id.strip()
            for meeting_id in round_meeting_ids
        ):
            raise ValueError("Explicit meeting mapping contains an empty meeting_id")
        meeting_ids = list(round_meeting_ids)

    per_meeting_round: defaultdict[str | None, int] = defaultdict(int)
    result = []
    for source_round, ((for_names, against_names, against_explicit), meeting_id) in enumerate(
        zip(parsed_pairs, meeting_ids),
        start=1,
    ):
        if meeting_id is None and round_meeting_ids is not None:
            continue
        per_meeting_round[meeting_id] += 1
        result.append(
            {
                "source_round": source_round,
                "vote_round": per_meeting_round[meeting_id],
                "meeting_id": meeting_id,
                "for_names": for_names,
                "against_names": against_names,
                "against_explicit": against_explicit,
                "total_votes": len(for_names) + len(against_names),
            }
        )
    return result


def persist_vote_rounds(
    connection: sqlite3.Connection,
    rounds: Iterable[dict[str, Any]],
    *,
    evidence_id: str,
) -> int:
    evidence = connection.execute(
        "SELECT 1 FROM document_source WHERE document_id = ?",
        (evidence_id,),
    ).fetchone()
    if evidence is None:
        raise ValueError(f"Unknown evidence_id: {evidence_id}")

    pending_rows = []
    for round_data in rounds:
        meeting_id = round_data.get("meeting_id")
        if not isinstance(meeting_id, str) or not meeting_id:
            raise ValueError("Every persisted vote round requires a meeting_id")
        vote_round = int(round_data["vote_round"])
        for voter_choice, dissent, names in (
            ("FOR", 0, round_data["for_names"]),
            ("AGAINST", 1, round_data["against_names"]),
        ):
            for name in names:
                voter_rows = connection.execute(
                    """
                    SELECT participant.participant_id, participant.display_name
                    FROM participant
                    JOIN meeting_participant
                      ON meeting_participant.participant_id = participant.participant_id
                    WHERE meeting_participant.meeting_id = ?
                      AND meeting_participant.is_voter = 1
                    """,
                    (meeting_id,),
                ).fetchall()
                exact = [row for row in voter_rows if row[1] == name]
                surname = [
                    row
                    for row in voter_rows
                    if " " not in name
                    and row[1].rstrip(".").split()[-1].casefold()
                    == name.rstrip(".").casefold()
                ]
                matches = exact or surname
                if len(matches) != 1:
                    raise ValueError(
                        f"No rostered voter named {name!r} for {meeting_id}"
                    )
                pending_rows.append(
                    (
                        meeting_id,
                        matches[0][0],
                        vote_round,
                        voter_choice,
                        dissent,
                        evidence_id,
                    )
                )
    connection.executemany(
        """
        INSERT OR IGNORE INTO participant_vote (
            meeting_id, participant_id, vote_round, voter_choice,
            dissent, evidence_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        pending_rows,
    )
    for row in pending_rows:
        persisted = connection.execute(
            """
            SELECT voter_choice, dissent, evidence_id
            FROM participant_vote
            WHERE meeting_id = ? AND participant_id = ? AND vote_round = ?
            """,
            row[:3],
        ).fetchone()
        if persisted != row[3:]:
            raise ValueError(
                "Existing participant vote conflicts with parsed official evidence"
            )
    return len(pending_rows)


def audit_vote_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    minute_documents = [
        item
        for item in manifest.get("documents", [])
        if item.get("document_type") == "minutes"
    ]
    parsed = []
    errors = []
    multi_vote_document_count = 0
    multi_vote_round_count = 0
    mapped_vote_meeting_ids: set[str] = set()
    for item in minute_documents:
        paragraphs: list[str] = []
        try:
            from decision_memory.fed_documents import extract_html_paragraphs

            paragraphs = extract_html_paragraphs(
                Path(item["local_path"]).read_bytes()
            )
            round_mapping = MULTI_VOTE_MEETING_MAPPINGS.get(
                item["meeting_id"], [item["meeting_id"]]
            )
            rounds = parse_vote_paragraphs(
                paragraphs,
                round_meeting_ids=round_mapping,
            )
            if item["meeting_id"] in MULTI_VOTE_MEETING_MAPPINGS:
                multi_vote_document_count += 1
                multi_vote_round_count += len(round_mapping)
                mapped_vote_meeting_ids.update(
                    meeting_id for meeting_id in round_mapping if meeting_id is not None
                )
            parsed.extend(rounds)
        except Exception as error:
            errors.append(
                {
                    "meeting_id": item.get("meeting_id"),
                    "error": f"{type(error).__name__}: {error}",
                    "vote_paragraphs": [
                        paragraph
                        for paragraph in paragraphs
                        if paragraph.casefold().startswith(
                            ("voting for", "votes for", "voting against", "votes against")
                        )
                    ]
                }
            )
    return {
        "minute_document_count": len(minute_documents),
        "parsed_meeting_count": len({item["meeting_id"] for item in parsed}),
        "vote_round_count": len(parsed),
        "vote_count": sum(item["total_votes"] for item in parsed),
        "dissent_meeting_count": sum(bool(item["against_names"]) for item in parsed),
        "implicit_against_count": sum(
            not item["against_explicit"] for item in parsed
        ),
        "multi_vote_document_count": multi_vote_document_count,
        "multi_vote_round_count": multi_vote_round_count,
        "mapped_vote_meeting_count": len(mapped_vote_meeting_ids),
        "multi_vote_meeting_mappings": MULTI_VOTE_MEETING_MAPPINGS,
        "errors": errors,
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise ValueError("Vote audit output must stay inside workspace")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if resolved.exists():
        if resolved.read_text(encoding="utf-8") != serialized:
            raise FileExistsError(f"Refusing to overwrite vote audit: {resolved}")
        return
    resolved.write_text(serialized, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit official FOMC minutes vote blocks and explicit mappings."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("document_manifests/training_2006_2020.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/evaluation/vote_parser_audit_v1.json"),
    )
    args = parser.parse_args()
    manifest = args.manifest.resolve()
    report = audit_vote_manifest(manifest)
    if report["errors"]:
        raise RuntimeError(f"Vote audit has {len(report['errors'])} parse errors")
    if (
        report["multi_vote_document_count"] != 4
        or report["multi_vote_round_count"] != 8
        or report["mapped_vote_meeting_count"] != 5
    ):
        raise RuntimeError("Multi-vote audit does not match the preregistered 4/8/5 gate")
    artifact = {
        "schema_version": "vote_parser_audit_v1",
        "status": "COMPLETED",
        "source_manifest": str(manifest.relative_to(ROOT.resolve())).replace("\\", "/"),
        "source_manifest_sha256": _sha256_file(manifest),
        **report,
    }
    _write_new_json(args.output, artifact)
    print(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
