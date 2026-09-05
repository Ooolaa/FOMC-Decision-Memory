from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


PROFILE_VERSION = "pooled_reaction_profile_cards_v2"
RECENT_VOTE_LIMIT = 8


def _meeting_date(meeting_id: str) -> str:
    if not meeting_id.startswith("FOMC-") or len(meeting_id) != 15:
        raise ValueError(f"Unsupported meeting_id: {meeting_id}")
    return meeting_id[5:]


def _published_vote_history(
    app: sqlite3.Connection,
    participant_id: str,
    meeting_id: str,
) -> list[dict[str, Any]]:
    cutoff_date = _meeting_date(meeting_id)
    return [
        {
            "meeting_id": str(source_meeting_id),
            "vote_round": int(vote_round),
            "voter_choice": str(voter_choice),
            "dissent": bool(dissent),
        }
        for source_meeting_id, vote_round, voter_choice, dissent in app.execute(
            """
            SELECT vote.meeting_id, vote.vote_round,
                   vote.voter_choice, vote.dissent
            FROM participant_vote AS vote
            WHERE vote.participant_id = ?
              AND vote.meeting_id < ?
              AND EXISTS (
                  SELECT 1
                  FROM document_source AS source
                  WHERE source.meeting_id = vote.meeting_id
                    AND substr(source.publication_at, 1, 10) <= ?
              )
            ORDER BY vote.meeting_id DESC, vote.vote_round DESC
            """,
            (participant_id, meeting_id, cutoff_date),
        ).fetchall()
    ]


def build_profile_cards(
    app: sqlite3.Connection,
    reaction_artifact: dict[str, Any],
    meeting_id: str,
) -> dict[str, Any]:
    participants = app.execute(
        """
        SELECT participant.participant_id, participant.display_name,
               meeting_participant.role, meeting_participant.is_voter,
               meeting_participant.is_chair
        FROM meeting_participant
        JOIN participant USING (participant_id)
        WHERE meeting_participant.meeting_id = ?
        ORDER BY meeting_participant.is_chair DESC,
                 meeting_participant.is_voter DESC,
                 participant.display_name
        """,
        (meeting_id,),
    ).fetchall()
    if not participants:
        raise RuntimeError(f"No meeting participants found for {meeting_id}")
    coefficients = {
        str(feature): float(value)
        for feature, value in reaction_artifact["coefficients"].items()
    }
    cards = []
    for participant_id, display_name, role, is_voter, is_chair in participants:
        vote_history = _published_vote_history(app, participant_id, meeting_id)
        prior_vote_count = len(vote_history)
        prior_dissent_count = sum(item["dissent"] for item in vote_history)
        recent_three = vote_history[:3]
        votes_since_last_dissent = next(
            (
                index
                for index, item in enumerate(vote_history)
                if item["dissent"]
            ),
            None,
        )
        cards.append(
            {
                "participant_id": str(participant_id),
                "display_name": str(display_name),
                "role": str(role),
                "is_voter": bool(is_voter),
                "is_chair": bool(is_chair),
                "prior_vote_count": prior_vote_count,
                "prior_dissent_count": prior_dissent_count,
                "prior_dissent_rate": (
                    prior_dissent_count / prior_vote_count
                    if prior_vote_count
                    else None
                ),
                "previous_vote_against": (
                    vote_history[0]["dissent"] if vote_history else None
                ),
                "recent_3_dissent_rate": (
                    sum(item["dissent"] for item in recent_three)
                    / len(recent_three)
                    if recent_three
                    else None
                ),
                "votes_since_last_dissent": votes_since_last_dissent,
                "last_dissent_meeting_id": next(
                    (
                        item["meeting_id"]
                        for item in vote_history
                        if item["dissent"]
                    ),
                    None,
                ),
                "recent_vote_history": vote_history[:RECENT_VOTE_LIMIT],
                "vote_history_as_of": _meeting_date(meeting_id),
                "vote_history_publication_checked": True,
                "macro_coefficients": coefficients,
                "coefficient_scope": "pooled_across_training_meetings",
                "individual_model_estimated": False,
            }
        )
    return {
        "schema_version": PROFILE_VERSION,
        "meeting_id": meeting_id,
        "model_id": str(reaction_artifact["model_id"]),
        "training_meeting_count": int(
            reaction_artifact["training_meeting_count"]
        ),
        "participant_count": len(cards),
        "voter_count": sum(card["is_voter"] for card in cards),
        "individual_models_estimated": False,
        "disclosure": (
            "Each card combines that participant's pre-meeting vote/dissent "
            "history published by the meeting date, including at most eight "
            "recent votes, with coefficients from one pooled macro reaction model. "
            "No participant-specific coefficient model was estimated."
        ),
        "cards": cards,
    }


def materialize_profile_cards(
    app_database: Path,
    reaction_artifact_path: Path,
    output_path: Path,
    meeting_id: str,
) -> dict[str, Any]:
    app_path = app_database.resolve()
    reaction_path = reaction_artifact_path.resolve()
    for path in (app_path, reaction_path):
        if not path.is_file():
            raise FileNotFoundError(f"Required input is missing: {path}")
    reaction_artifact = json.loads(reaction_path.read_text(encoding="utf-8"))
    app = sqlite3.connect(f"file:{app_path.as_posix()}?mode=ro", uri=True)
    try:
        artifact = build_profile_cards(app, reaction_artifact, meeting_id)
        integrity = app.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = app.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"App DB validation failed: integrity={integrity}, "
                f"foreign_keys={foreign_key_errors}"
            )
    finally:
        app.close()
    resolved_output = output_path.resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if resolved_output.exists():
        if resolved_output.read_text(encoding="utf-8") != serialized:
            raise RuntimeError(f"Existing profile-card artifact differs: {resolved_output}")
    else:
        resolved_output.write_text(serialized, encoding="utf-8")
    return {
        "artifact_path": str(resolved_output),
        "meeting_id": meeting_id,
        "participant_count": artifact["participant_count"],
        "voter_count": artifact["voter_count"],
        "individual_models_estimated": artifact["individual_models_estimated"],
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build honest pooled reaction profile cards for one meeting."
    )
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--reaction-artifact",
        type=Path,
        default=Path("artifacts/reaction/pooled_ordered_logit_v1.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "artifacts/reaction/fomc_2022_03_15_profile_cards_v1.json"
        ),
    )
    parser.add_argument("--meeting-id", default="FOMC-2022-03-15")
    args = parser.parse_args()
    report = materialize_profile_cards(
        args.app,
        args.reaction_artifact,
        args.output,
        args.meeting_id,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
