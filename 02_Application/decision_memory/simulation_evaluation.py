from __future__ import annotations

import sqlite3
from typing import Any

from decision_memory.offline_simulator import validate_simulation_output


ACTION_ORDINAL = {"CUT": -1, "HOLD": 0, "HIKE": 1}
EVALUATOR_VERSION = "simulation_policy_vote_v2"


def _safe_divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate_simulation_output(
    app: sqlite3.Connection,
    output: dict[str, Any],
) -> dict[str, Any]:
    semantic = validate_simulation_output(output)
    meeting_id = output["meeting_id"]
    outcome = app.execute(
        "SELECT action_class FROM meeting_outcome WHERE meeting_id = ?",
        (meeting_id,),
    ).fetchone()
    if outcome is None:
        raise ValueError(f"No outcome label for {meeting_id}")
    actual_action = str(outcome[0])
    predicted_action = output["final_proposal"]["action_class"]

    actual_votes = {
        row[0]: bool(row[1])
        for row in app.execute(
            """
            SELECT participant_id, MAX(dissent)
            FROM participant_vote WHERE meeting_id = ?
            GROUP BY participant_id
            """,
            (meeting_id,),
        ).fetchall()
    }
    known_voters = {
        str(row[0])
        for row in app.execute(
            """
            SELECT participant_id FROM meeting_participant
            WHERE meeting_id = ? AND is_voter = 1
            """,
            (meeting_id,),
        ).fetchall()
    }
    if set(actual_votes) != known_voters:
        missing_labels = sorted(known_voters - set(actual_votes))
        unexpected_labels = sorted(set(actual_votes) - known_voters)
        raise ValueError(
            "Historical vote labels differ from known voter roster: "
            f"missing_labels={missing_labels}, unexpected_labels={unexpected_labels}"
        )
    predicted_votes = {
        item["participant_id"]: item["choice"] == "AGAINST"
        for item in output["votes"]
    }
    if set(predicted_votes) != known_voters:
        missing = sorted(known_voters - set(predicted_votes))
        extra = sorted(set(predicted_votes) - known_voters)
        raise ValueError(
            f"Predicted voter roster differs from known roster: missing={missing}, extra={extra}"
        )

    true_positive = sum(
        actual_votes[participant_id] and predicted
        for participant_id, predicted in predicted_votes.items()
    )
    false_positive = sum(
        not actual_votes[participant_id] and predicted
        for participant_id, predicted in predicted_votes.items()
    )
    false_negative = sum(
        actual_votes[participant_id] and not predicted
        for participant_id, predicted in predicted_votes.items()
    )
    true_negative = len(predicted_votes) - true_positive - false_positive - false_negative
    precision = _safe_divide(true_positive, true_positive + false_positive)
    recall = _safe_divide(true_positive, true_positive + false_negative)
    f1 = _safe_divide(2 * precision * recall, precision + recall)
    action_error = abs(ACTION_ORDINAL[predicted_action] - ACTION_ORDINAL[actual_action])
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "meeting_id": meeting_id,
        "synthetic": True,
        "actual_action": actual_action,
        "predicted_action": predicted_action,
        "policy_accuracy": float(predicted_action == actual_action),
        "policy_action_mae": float(action_error),
        "false_action_on_hold": float(
            actual_action == "HOLD" and predicted_action != "HOLD"
        ),
        "proposal_action_matches_label": predicted_action == actual_action,
        "vote_count": len(predicted_votes),
        "known_voter_count": len(known_voters),
        "label_roster_complete": True,
        "actual_dissent_count": sum(actual_votes.values()),
        "predicted_dissent_count": sum(predicted_votes.values()),
        "dissent_base_rate": _safe_divide(sum(actual_votes.values()), len(actual_votes)),
        "dissent_true_positive": true_positive,
        "dissent_false_positive": false_positive,
        "dissent_false_negative": false_negative,
        "dissent_true_negative": true_negative,
        "dissent_precision": precision,
        "dissent_recall": recall,
        "dissent_f1": f1,
        "semantic_vote_balance": semantic,
        "vote_comparison_disclosure": (
            "FOR/AGAINST is compared to historical dissent labels even when the "
            "synthetic proposal action differs; proposal_action_matches_label must "
            "therefore be reported beside dissent metrics."
        ),
    }
