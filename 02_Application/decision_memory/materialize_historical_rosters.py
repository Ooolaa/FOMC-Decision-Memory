from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from decision_memory.roster import (
    LEGACY_SURNAME_OVERRIDES,
    parse_policy_attendance_html,
    participant_id_for_name,
)


PARTICIPANT_ALIASES = {
    "eric-rosengren": "eric-s-rosengren",
    "kevin-warsh": "kevin-m-warsh",
    "president-fisher": "richard-w-fisher",
    "president-plosser": "charles-i-plosser",
}


def _preferred_name(names: list[str]) -> str:
    return max(
        names,
        key=lambda name: (
            not name.casefold().startswith("president "),
            len(name.split()),
            len(name),
        ),
    )


def _merge_alias(
    connection: sqlite3.Connection,
    alias_id: str,
    canonical_id: str,
) -> bool:
    alias = connection.execute(
        "SELECT display_name, effective_start, effective_end FROM participant WHERE participant_id = ?",
        (alias_id,),
    ).fetchone()
    if alias is None:
        return False
    canonical = connection.execute(
        "SELECT display_name, effective_start, effective_end FROM participant WHERE participant_id = ?",
        (canonical_id,),
    ).fetchone()
    if canonical is None:
        raise RuntimeError(f"Canonical participant is missing: {canonical_id}")

    meeting_rows = connection.execute(
        """
        SELECT meeting_id, role, is_voter, is_chair
        FROM meeting_participant WHERE participant_id = ?
        """,
        (alias_id,),
    ).fetchall()
    for meeting_id, role, is_voter, is_chair in meeting_rows:
        existing = connection.execute(
            """
            SELECT role, is_voter, is_chair FROM meeting_participant
            WHERE meeting_id = ? AND participant_id = ?
            """,
            (meeting_id, canonical_id),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO meeting_participant (
                    meeting_id, participant_id, role, is_voter, is_chair
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (meeting_id, canonical_id, role, is_voter, is_chair),
            )
        else:
            connection.execute(
                """
                UPDATE meeting_participant
                SET is_voter = ?, is_chair = ?
                WHERE meeting_id = ? AND participant_id = ?
                """,
                (
                    max(existing[1], is_voter),
                    max(existing[2], is_chair),
                    meeting_id,
                    canonical_id,
                ),
            )

    vote_rows = connection.execute(
        """
        SELECT meeting_id, vote_round, voter_choice, dissent, evidence_id
        FROM participant_vote WHERE participant_id = ?
        """,
        (alias_id,),
    ).fetchall()
    for meeting_id, vote_round, choice, dissent, evidence_id in vote_rows:
        existing = connection.execute(
            """
            SELECT voter_choice, dissent, evidence_id FROM participant_vote
            WHERE meeting_id = ? AND participant_id = ? AND vote_round = ?
            """,
            (meeting_id, canonical_id, vote_round),
        ).fetchone()
        expected = (choice, dissent, evidence_id)
        if existing is not None and existing != expected:
            raise RuntimeError(
                f"Alias vote conflicts for {meeting_id}/{alias_id}/{canonical_id}"
            )
        if existing is None:
            connection.execute(
                """
                INSERT INTO participant_vote (
                    meeting_id, participant_id, vote_round,
                    voter_choice, dissent, evidence_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (meeting_id, canonical_id, vote_round, choice, dissent, evidence_id),
            )

    connection.execute("DELETE FROM participant_vote WHERE participant_id = ?", (alias_id,))
    connection.execute("DELETE FROM meeting_participant WHERE participant_id = ?", (alias_id,))
    starts = [value for value in (alias[1], canonical[1]) if value]
    ends = [value for value in (alias[2], canonical[2]) if value]
    connection.execute(
        """
        UPDATE participant SET effective_start = ?, effective_end = ?
        WHERE participant_id = ?
        """,
        (min(starts) if starts else None, max(ends) if ends else None, canonical_id),
    )
    connection.execute("DELETE FROM participant WHERE participant_id = ?", (alias_id,))
    return True


def materialize_historical_rosters(
    app_database: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    minutes = sorted(
        (
            item
            for item in manifest.get("documents", [])
            if item.get("document_type") == "minutes"
        ),
        key=lambda item: item["meeting_id"],
    )
    if not minutes:
        raise RuntimeError("Historical manifest contains no minutes")

    app_path = app_database.resolve()
    connection = sqlite3.connect(f"file:{app_path.as_posix()}?mode=rw", uri=True)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        merged_aliases = [
            alias_id
            for alias_id, canonical_id in PARTICIPANT_ALIASES.items()
            if _merge_alias(connection, alias_id, canonical_id)
        ]
        name_groups: defaultdict[str, list[str]] = defaultdict(list)
        for (display_name,) in connection.execute(
            "SELECT display_name FROM participant"
        ).fetchall():
            name_groups[display_name.split()[-1].rstrip(".").casefold()].append(
                display_name
            )
        resolver = {
            surname: _preferred_name(names)
            for surname, names in name_groups.items()
        }
        resolver.update(LEGACY_SURNAME_OVERRIDES)

        parsed = []
        for item in minutes:
            participants = parse_policy_attendance_html(
                Path(item["local_path"]).read_bytes(),
                surname_resolver=resolver,
            )
            parsed.append((item["meeting_id"], participants))

        for meeting_id, participants in parsed:
            meeting_date = meeting_id.removeprefix("FOMC-")
            voter_ids = {
                row[0]
                for row in connection.execute(
                    "SELECT participant_id FROM participant_vote WHERE meeting_id = ?",
                    (meeting_id,),
                ).fetchall()
            }
            for participant in participants:
                participant_id = participant["participant_id"]
                connection.execute(
                    """
                    INSERT OR IGNORE INTO participant (
                        participant_id, display_name, role,
                        effective_start, effective_end
                    ) VALUES (?, ?, 'policymaker', ?, ?)
                    """,
                    (participant_id, participant["display_name"], meeting_date, meeting_date),
                )
                persisted_name = connection.execute(
                    "SELECT display_name FROM participant WHERE participant_id = ?",
                    (participant_id,),
                ).fetchone()[0]
                if persisted_name != participant["display_name"]:
                    raise RuntimeError(f"Participant identity conflict: {participant_id}")
                connection.execute(
                    """
                    UPDATE participant
                    SET effective_start = min(effective_start, ?),
                        effective_end = max(effective_end, ?)
                    WHERE participant_id = ?
                    """,
                    (meeting_date, meeting_date, participant_id),
                )
                expected = (
                    participant["role"],
                    int(participant_id in voter_ids),
                    int(participant["is_chair"]),
                )
                connection.execute(
                    """
                    INSERT INTO meeting_participant (
                        meeting_id, participant_id, role, is_voter, is_chair
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(meeting_id, participant_id) DO UPDATE SET
                        role = excluded.role,
                        is_voter = excluded.is_voter,
                        is_chair = excluded.is_chair
                    """,
                    (meeting_id, participant_id, *expected),
                )

        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        chair_errors = connection.execute(
            """
            SELECT meeting_id, SUM(is_chair) AS chair_count
            FROM meeting_participant GROUP BY meeting_id
            HAVING chair_count <> 1
            """
        ).fetchall()
        roster_range = connection.execute(
            """
            SELECT MIN(participant_count), MAX(participant_count)
            FROM (
                SELECT meeting_id, COUNT(*) AS participant_count
                FROM meeting_participant
                WHERE meeting_id IN (
                    SELECT DISTINCT meeting_id FROM document_source
                    WHERE document_type = 'minutes'
                )
                GROUP BY meeting_id
            )
            """
        ).fetchone()
        if integrity != "ok" or foreign_key_errors or chair_errors:
            raise RuntimeError(
                f"Historical roster validation failed: integrity={integrity}, "
                f"foreign_keys={foreign_key_errors}, chairs={chair_errors}"
            )
        connection.commit()
        totals = {
            "participant_count": connection.execute(
                "SELECT COUNT(*) FROM participant"
            ).fetchone()[0],
            "meeting_participant_count": connection.execute(
                "SELECT COUNT(*) FROM meeting_participant"
            ).fetchone()[0],
            "vote_count": connection.execute(
                "SELECT COUNT(*) FROM participant_vote"
            ).fetchone()[0],
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "manifest": str(manifest_path.resolve()),
        "parsed_minutes": len(parsed),
        "parsed_meeting_participants": sum(len(items) for _, items in parsed),
        "merged_aliases": merged_aliases,
        "roster_size_min": roster_range[0],
        "roster_size_max": roster_range[1],
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
        **totals,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize 2006-2020 FOMC policy attendance from official minutes."
    )
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("document_manifests/training_2006_2020.json"),
    )
    parser.add_argument("--expected-minutes", type=int, default=120)
    args = parser.parse_args()
    report = materialize_historical_rosters(args.app, args.manifest)
    if report["parsed_minutes"] != args.expected_minutes:
        raise RuntimeError(
            f"Expected {args.expected_minutes} minutes, got {report['parsed_minutes']}"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
