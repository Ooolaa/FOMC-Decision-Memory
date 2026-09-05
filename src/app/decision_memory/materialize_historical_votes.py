from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from decision_memory.fed_documents import extract_html_paragraphs
from decision_memory.roster import participant_id_for_name
from decision_memory.votes import parse_vote_paragraphs, persist_vote_rounds


def _meeting_date(meeting_id: str) -> str:
    if not meeting_id.startswith("FOMC-"):
        raise ValueError(f"Unsupported meeting_id: {meeting_id}")
    return meeting_id[5:]


def _surname(name: str) -> str:
    parts = re.findall(r"[A-Za-z'-]+", name)
    if not parts:
        raise ValueError(f"Cannot derive surname from voter name: {name}")
    if parts[-1].casefold() in {"jr", "sr", "ii", "iii", "iv"} and len(parts) > 1:
        return parts[-2].casefold()
    return parts[-1].casefold()


def _full_name_registry(rounds: list[dict[str, Any]]) -> dict[str, str]:
    variants: defaultdict[str, set[str]] = defaultdict(set)
    for round_data in rounds:
        for name in round_data["for_names"] + round_data["against_names"]:
            if " " in name:
                variants[_surname(name)].add(name)
    registry = {}
    for surname, names in variants.items():
        first_names = {
            re.findall(r"[A-Za-z'-]+", name)[0].casefold() for name in names
        }
        if len(first_names) != 1:
            continue
        registry[surname] = max(names, key=lambda value: (len(value), value))
    return registry


def _canonicalize_round(
    round_data: dict[str, Any],
    registry: dict[str, str],
) -> dict[str, Any]:
    canonical = dict(round_data)

    def canonical_name(name: str) -> str:
        match = registry.get(_surname(name))
        if match is None:
            if " " in name:
                return name
            raise ValueError(f"No unique full-name voter mapping for {name!r}")
        return match

    canonical["for_names"] = [canonical_name(name) for name in round_data["for_names"]]
    canonical["against_names"] = [
        canonical_name(name) for name in round_data["against_names"]
    ]
    if set(canonical["for_names"]) & set(canonical["against_names"]):
        raise ValueError("Canonical voter appears on both sides of a vote")
    canonical["total_votes"] = len(canonical["for_names"]) + len(
        canonical["against_names"]
    )
    return canonical


def _round_signature(round_data: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return (
        tuple(sorted(_surname(name) for name in round_data["for_names"])),
        tuple(sorted(_surname(name) for name in round_data["against_names"])),
    )


def materialize_historical_votes(
    app_database: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents_by_meeting: defaultdict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for item in manifest.get("documents", []):
        documents_by_meeting[item["meeting_id"]][item["document_type"]] = item
    if not documents_by_meeting:
        raise RuntimeError("Document manifest contains no meetings")

    parsed_records = []
    all_rounds = []
    for meeting_id in sorted(documents_by_meeting):
        documents = documents_by_meeting[meeting_id]
        candidates = [
            document
            for document in (documents.get("statement"), documents.get("minutes"))
            if document is not None
        ]
        if not candidates:
            raise RuntimeError(f"Meeting has no vote evidence document: {meeting_id}")
        parsed_candidates = []
        errors = []
        for candidate in candidates:
            paragraphs = extract_html_paragraphs(
                Path(candidate["local_path"]).read_bytes()
            )
            try:
                candidate_rounds = parse_vote_paragraphs(
                    paragraphs,
                    round_meeting_ids=[meeting_id],
                )
            except Exception as error:
                errors.append(f"{candidate['document_type']}: {error}")
                continue
            if len(candidate_rounds) != 1:
                errors.append(
                    f"{candidate['document_type']}: expected one policy vote"
                )
                continue
            parsed_candidates.append((candidate, candidate_rounds))
        if not parsed_candidates:
            raise RuntimeError(
                f"Could not parse policy vote for {meeting_id}: {errors}"
            )
        signatures = {
            _round_signature(candidate_rounds[0])
            for _, candidate_rounds in parsed_candidates
        }
        if len(signatures) != 1:
            details = {
                candidate["document_type"]: _round_signature(candidate_rounds[0])
                for candidate, candidate_rounds in parsed_candidates
            }
            raise RuntimeError(
                f"Official vote evidence disagrees for {meeting_id}: {details}"
            )
        for _, candidate_rounds in parsed_candidates:
            all_rounds.extend(candidate_rounds)
        parsed_records.append(
            {
                "meeting_id": meeting_id,
                "candidates": [
                    {
                        "document_id": candidate["document_id"],
                        "document_type": candidate["document_type"],
                        "round": candidate_rounds[0],
                    }
                    for candidate, candidate_rounds in parsed_candidates
                ],
            }
        )

    registry = _full_name_registry(all_rounds)
    for record in parsed_records:
        for candidate in record["candidates"]:
            candidate["round"] = _canonicalize_round(candidate["round"], registry)

    app_path = app_database.resolve()
    if not app_path.is_file():
        raise FileNotFoundError(f"App database does not exist: {app_path}")
    app = sqlite3.connect(f"file:{app_path.as_posix()}?mode=rw", uri=True)
    app.execute("PRAGMA foreign_keys = ON")
    try:
        for record in parsed_records:
            existing_evidence_ids = {
                row[0]
                for row in app.execute(
                    """
                    SELECT DISTINCT evidence_id FROM participant_vote
                    WHERE meeting_id = ?
                    """,
                    (record["meeting_id"],),
                ).fetchall()
            }
            matching_candidates = [
                candidate
                for candidate in record["candidates"]
                if candidate["document_id"] in existing_evidence_ids
            ]
            if len(matching_candidates) > 1:
                raise RuntimeError(
                    "Existing vote labels use multiple official evidence documents: "
                    f"{record['meeting_id']}"
                )
            selected = matching_candidates[0] if matching_candidates else record["candidates"][0]
            record.update(selected)
            roster_variants: defaultdict[str, set[str]] = defaultdict(set)
            for (display_name,) in app.execute(
                """
                SELECT participant.display_name
                FROM meeting_participant
                JOIN participant USING (participant_id)
                WHERE meeting_participant.meeting_id = ?
                """,
                (record["meeting_id"],),
            ).fetchall():
                roster_variants[_surname(display_name)].add(display_name)
            roster_registry = {
                surname: next(iter(names))
                for surname, names in roster_variants.items()
                if len(names) == 1
            }
            record["round"] = _canonicalize_round(record["round"], roster_registry)

        participant_dates: defaultdict[str, list[str]] = defaultdict(list)
        participant_names: dict[str, str] = {}
        for record in parsed_records:
            meeting_date = _meeting_date(record["meeting_id"])
            round_data = record["round"]
            for name in round_data["for_names"] + round_data["against_names"]:
                participant_id = participant_id_for_name(name)
                existing = participant_names.setdefault(participant_id, name)
                if existing != name:
                    raise RuntimeError(f"participant_id collision: {participant_id}")
                participant_dates[participant_id].append(meeting_date)

        meeting_ids = [record["meeting_id"] for record in parsed_records]
        app.executemany(
            "DELETE FROM participant_vote WHERE meeting_id = ?",
            ((meeting_id,) for meeting_id in meeting_ids),
        )
        app.executemany(
            """
            UPDATE meeting_participant
            SET is_voter = 0, is_chair = 0
            WHERE meeting_id = ?
            """,
            ((meeting_id,) for meeting_id in meeting_ids),
        )
        for participant_id, dates in sorted(participant_dates.items()):
            display_name = participant_names[participant_id]
            existing = app.execute(
                """
                SELECT display_name, role, effective_start, effective_end
                FROM participant WHERE participant_id = ?
                """,
                (participant_id,),
            ).fetchone()
            if existing is None:
                app.execute(
                    """
                    INSERT INTO participant (
                        participant_id, display_name, role,
                        effective_start, effective_end
                    ) VALUES (?, ?, 'policymaker', ?, ?)
                    """,
                    (participant_id, display_name, min(dates), max(dates)),
                )
            else:
                if existing[0] != display_name or existing[1] != "policymaker":
                    raise RuntimeError(
                        f"Existing participant conflicts with vote corpus: {participant_id}"
                    )
                app.execute(
                    """
                    UPDATE participant
                    SET effective_start = MIN(effective_start, ?),
                        effective_end = MAX(effective_end, ?)
                    WHERE participant_id = ?
                    """,
                    (min(dates), max(dates), participant_id),
                )

        for record in parsed_records:
            meeting_id = record["meeting_id"]
            round_data = record["round"]
            chair_name = round_data["for_names"][0]
            for name in round_data["for_names"] + round_data["against_names"]:
                participant_id = participant_id_for_name(name)
                is_chair = int(name == chair_name)
                existing_roster = app.execute(
                    """
                    SELECT role FROM meeting_participant
                    WHERE meeting_id = ? AND participant_id = ?
                    """,
                    (meeting_id, participant_id),
                ).fetchone()
                if existing_roster is None:
                    app.execute(
                        """
                        INSERT INTO meeting_participant (
                            meeting_id, participant_id, role, is_voter, is_chair
                        ) VALUES (?, ?, ?, 1, ?)
                        """,
                        (
                            meeting_id,
                            participant_id,
                            "chair" if is_chair else "member",
                            is_chair,
                        ),
                    )
                else:
                    app.execute(
                        """
                        UPDATE meeting_participant
                        SET is_voter = 1, is_chair = ?
                        WHERE meeting_id = ? AND participant_id = ?
                        """,
                        (is_chair, meeting_id, participant_id),
                    )
                persisted = app.execute(
                    """
                    SELECT is_voter, is_chair
                    FROM meeting_participant
                    WHERE meeting_id = ? AND participant_id = ?
                    """,
                    (meeting_id, participant_id),
                ).fetchone()
                if persisted != (1, is_chair):
                    raise RuntimeError(
                        f"Existing meeting voter conflicts: {meeting_id}/{participant_id}"
                    )
            persist_vote_rounds(
                app,
                [round_data],
                evidence_id=record["document_id"],
            )
        integrity = app.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = app.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"App DB validation failed: integrity={integrity}, "
                f"foreign_keys={foreign_key_errors}"
            )
        app.commit()
        participant_count = int(app.execute("SELECT COUNT(*) FROM participant").fetchone()[0])
        vote_count = int(app.execute("SELECT COUNT(*) FROM participant_vote").fetchone()[0])
    except Exception:
        app.rollback()
        raise
    finally:
        app.close()
    return {
        "meeting_count": len(parsed_records),
        "participant_count": participant_count,
        "vote_count": vote_count,
        "statement_evidence_meetings": sum(
            record["document_type"] == "statement" for record in parsed_records
        ),
        "minutes_fallback_meetings": sum(
            record["document_type"] == "minutes" for record in parsed_records
        ),
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize historical FOMC policy votes from official statements."
    )
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("document_manifests/training_2006_2020.json"),
    )
    parser.add_argument("--expected-meetings", type=int, default=121)
    args = parser.parse_args()
    report = materialize_historical_votes(args.app, args.manifest)
    if report["meeting_count"] != args.expected_meetings:
        raise RuntimeError(
            f"Expected {args.expected_meetings} meetings, got {report['meeting_count']}"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
