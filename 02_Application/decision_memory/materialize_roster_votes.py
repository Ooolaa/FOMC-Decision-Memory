from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from decision_memory.fed_documents import (
    extract_html_paragraph_line_blocks,
    extract_html_paragraphs,
)
from decision_memory.roster import parse_policy_attendance
from decision_memory.votes import parse_vote_paragraphs, persist_vote_rounds


def _meeting_date(meeting_id: str) -> str:
    prefix = "FOMC-"
    if not meeting_id.startswith(prefix):
        raise ValueError(f"Unsupported meeting_id: {meeting_id}")
    return meeting_id[len(prefix) :]


def materialize_rosters_and_votes(
    app_database: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    minute_documents = sorted(
        (
            item
            for item in manifest.get("documents", [])
            if item.get("document_type") == "minutes"
        ),
        key=lambda item: item["meeting_id"],
    )
    if not minute_documents:
        raise RuntimeError("Document manifest contains no minutes")

    meeting_records = []
    participant_dates: defaultdict[str, list[str]] = defaultdict(list)
    participant_names: dict[str, str] = {}
    for item in minute_documents:
        content = Path(item["local_path"]).read_bytes()
        attendance = parse_policy_attendance(
            extract_html_paragraph_line_blocks(content)
        )
        rounds = parse_vote_paragraphs(
            extract_html_paragraphs(content),
            round_meeting_ids=[item["meeting_id"]],
        )
        voter_names = {
            name
            for round_data in rounds
            for name in round_data["for_names"] + round_data["against_names"]
        }
        attendance_names = {row["display_name"] for row in attendance}
        missing_voters = sorted(voter_names - attendance_names)
        if missing_voters:
            raise RuntimeError(
                f"Vote names missing from attendance for {item['meeting_id']}: "
                f"{missing_voters}"
            )
        meeting_date = _meeting_date(item["meeting_id"])
        for participant in attendance:
            participant_id = participant["participant_id"]
            existing_name = participant_names.setdefault(
                participant_id,
                participant["display_name"],
            )
            if existing_name != participant["display_name"]:
                raise RuntimeError(
                    f"participant_id collision: {participant_id}"
                )
            participant_dates[participant_id].append(meeting_date)
        meeting_records.append(
            {
                "meeting_id": item["meeting_id"],
                "document_id": item["document_id"],
                "attendance": attendance,
                "rounds": rounds,
                "voter_names": voter_names,
            }
        )

    app_path = app_database.resolve()
    if not app_path.is_file():
        raise FileNotFoundError(f"App database does not exist: {app_path}")
    connection = sqlite3.connect(
        f"file:{app_path.as_posix()}?mode=rw",
        uri=True,
    )
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        for participant_id, dates in sorted(participant_dates.items()):
            expected = (
                participant_names[participant_id],
                "policymaker",
                min(dates),
                max(dates),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO participant (
                    participant_id, display_name, role,
                    effective_start, effective_end
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (participant_id, *expected),
            )
            persisted = connection.execute(
                """
                SELECT display_name, role, effective_start, effective_end
                FROM participant WHERE participant_id = ?
                """,
                (participant_id,),
            ).fetchone()
            if persisted != expected:
                raise RuntimeError(
                    f"Existing participant conflicts with manifest: {participant_id}"
                )

        for meeting in meeting_records:
            meeting_id = meeting["meeting_id"]
            for participant in meeting["attendance"]:
                expected = (
                    participant["role"],
                    int(participant["display_name"] in meeting["voter_names"]),
                    int(participant["is_chair"]),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO meeting_participant (
                        meeting_id, participant_id, role, is_voter, is_chair
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (meeting_id, participant["participant_id"], *expected),
                )
                persisted = connection.execute(
                    """
                    SELECT role, is_voter, is_chair
                    FROM meeting_participant
                    WHERE meeting_id = ? AND participant_id = ?
                    """,
                    (meeting_id, participant["participant_id"]),
                ).fetchone()
                if persisted != expected:
                    raise RuntimeError(
                        "Existing meeting participant conflicts with manifest: "
                        f"{meeting_id}/{participant['participant_id']}"
                    )
            persist_vote_rounds(
                connection,
                meeting["rounds"],
                evidence_id=meeting["document_id"],
            )
        connection.commit()

        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"App DB validation failed: integrity={integrity}, "
                f"foreign_keys={foreign_key_errors}"
            )
        participant_count = connection.execute(
            "SELECT COUNT(*) FROM participant"
        ).fetchone()[0]
        meeting_participant_count = connection.execute(
            "SELECT COUNT(*) FROM meeting_participant"
        ).fetchone()[0]
        vote_count = connection.execute(
            "SELECT COUNT(*) FROM participant_vote"
        ).fetchone()[0]
        chair_rows = connection.execute(
            """
            SELECT participant.display_name, COUNT(*)
            FROM meeting_participant
            JOIN participant USING (participant_id)
            WHERE meeting_participant.is_chair = 1
            GROUP BY participant.display_name
            ORDER BY participant.display_name
            """
        ).fetchall()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "meeting_count": len(meeting_records),
        "participant_count": int(participant_count),
        "meeting_participant_count": int(meeting_participant_count),
        "vote_count": int(vote_count),
        "chair_meeting_counts": dict(chair_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize R5 policy attendance and vote labels."
    )
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            "document_manifests/current_45_as_of_2026-08-27_source_a7fd.json"
        ),
    )
    parser.add_argument("--expected-meetings", type=int, default=45)
    args = parser.parse_args()
    report = materialize_rosters_and_votes(args.app, args.manifest)
    if report["meeting_count"] != args.expected_meetings:
        raise RuntimeError(
            f"Expected {args.expected_meetings} meetings, got {report['meeting_count']}"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
