from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from decision_memory.reaction_model import (
    ACTION_VALUE,
    VALUE_ACTION,
    build_meeting_feature_row,
    predict_ordered_logit,
)


CLASSES = ("CUT", "HOLD", "HIKE")


def classification_metrics(
    *,
    actual: Sequence[str],
    predicted: Sequence[str],
) -> dict[str, Any]:
    if not actual or len(actual) != len(predicted):
        raise ValueError("actual and predicted must have equal non-zero length")
    if any(value not in CLASSES for value in list(actual) + list(predicted)):
        raise ValueError("Unsupported action class")
    per_class = {}
    for action_class in CLASSES:
        true_positive = sum(
            actual_value == action_class and predicted_value == action_class
            for actual_value, predicted_value in zip(actual, predicted)
        )
        false_positive = sum(
            actual_value != action_class and predicted_value == action_class
            for actual_value, predicted_value in zip(actual, predicted)
        )
        false_negative = sum(
            actual_value == action_class and predicted_value != action_class
            for actual_value, predicted_value in zip(actual, predicted)
        )
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0.0
        )
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        per_class[action_class] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": sum(value == action_class for value in actual),
        }
    return {
        "n": len(actual),
        "accuracy": sum(a == p for a, p in zip(actual, predicted)) / len(actual),
        "macro_f1": sum(item["f1"] for item in per_class.values()) / len(CLASSES),
        "mean_absolute_action_error": sum(
            abs(ACTION_VALUE[a] - ACTION_VALUE[p])
            for a, p in zip(actual, predicted)
        )
        / len(actual),
        "false_action_count_on_hold": sum(
            a == "HOLD" and p != "HOLD" for a, p in zip(actual, predicted)
        ),
        "predicted_class_counts": dict(sorted(Counter(predicted).items())),
        "per_class": per_class,
    }


def evaluate_frozen_cases(
    source: sqlite3.Connection,
    app: sqlite3.Connection,
    reaction_artifact: dict[str, Any],
    *,
    test_start: str = "2021-01-01",
) -> dict[str, Any]:
    all_meetings = source.execute(
        """
        SELECT meeting_id, meeting_start_date
        FROM fomc_meeting ORDER BY meeting_start_date
        """
    ).fetchall()
    outcomes = {
        str(meeting_id): str(action_class)
        for meeting_id, action_class in app.execute(
            "SELECT meeting_id, action_class FROM meeting_outcome"
        ).fetchall()
    }
    test_meetings = [
        (str(meeting_id), str(meeting_date))
        for meeting_id, meeting_date in all_meetings
        if str(meeting_date) >= test_start
    ]
    if not test_meetings:
        raise RuntimeError("Frozen split contains no meetings")

    training_majority_value = int(
        max(
            reaction_artifact["class_counts"].items(),
            key=lambda item: int(item[1]),
        )[0]
    )
    majority_action = VALUE_ACTION[training_majority_value]
    previous_outcome = {}
    prior = None
    for meeting_id, _ in all_meetings:
        meeting_id = str(meeting_id)
        if prior is not None:
            previous_outcome[meeting_id] = prior
        if meeting_id in outcomes:
            prior = outcomes[meeting_id]

    per_case = []
    for meeting_id, meeting_date in test_meetings:
        actual = outcomes.get(meeting_id)
        if actual is None:
            raise RuntimeError(f"Frozen case has no outcome: {meeting_id}")
        feature_row = build_meeting_feature_row(source, app, meeting_id)
        reaction = predict_ordered_logit(feature_row, reaction_artifact)
        persistence = previous_outcome.get(meeting_id)
        if persistence is None:
            raise RuntimeError(f"No prior outcome for persistence baseline: {meeting_id}")
        dissent_count = int(
            app.execute(
                """
                SELECT COUNT(*) FROM participant_vote
                WHERE meeting_id = ? AND dissent = 1
                """,
                (meeting_id,),
            ).fetchone()[0]
        )
        per_case.append(
            {
                "meeting_id": meeting_id,
                "meeting_start_date": meeting_date,
                "actual": actual,
                "majority": majority_action,
                "persistence": persistence,
                "pooled_reaction": reaction["action_class"],
                "pooled_probabilities": reaction["probabilities"],
                "dissent_count": dissent_count,
            }
        )
    actual = [item["actual"] for item in per_case]
    metrics = {
        name: classification_metrics(
            actual=actual,
            predicted=[item[name] for item in per_case],
        )
        for name in ("majority", "persistence", "pooled_reaction")
    }
    split_payload = {
        "test_start": test_start,
        "meeting_ids": [item["meeting_id"] for item in per_case],
    }
    split_hash = hashlib.sha256(
        json.dumps(split_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "evaluation_version": "frozen_45_policy_baselines_v1",
        "reaction_model_id": reaction_artifact["model_id"],
        "split_manifest": {**split_payload, "manifest_hash": split_hash},
        "actual_class_counts": dict(sorted(Counter(actual).items())),
        "metrics": metrics,
        "per_case": per_case,
        "dissent_disclosure": (
            "Policy baselines do not predict individual dissent; dissent precision/recall "
            "is deferred to the structured simulator output."
        ),
    }


def materialize_frozen_evaluation(
    source_database: Path,
    app_database: Path,
    reaction_artifact_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    artifact = json.loads(reaction_artifact_path.read_text(encoding="utf-8"))
    source_path = source_database.resolve()
    app_path = app_database.resolve()
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    app = sqlite3.connect(f"file:{app_path.as_posix()}?mode=ro", uri=True)
    try:
        result = evaluate_frozen_cases(source, app, artifact)
    finally:
        app.close()
        source.close()
    serialized = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    resolved = output_path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if resolved.exists():
        if resolved.read_text(encoding="utf-8") != serialized:
            raise RuntimeError(f"Existing frozen evaluation differs: {resolved}")
    else:
        resolved.write_text(serialized, encoding="utf-8")
    return {
        "evaluation_version": result["evaluation_version"],
        "case_count": len(result["per_case"]),
        "actual_class_counts": result["actual_class_counts"],
        "metrics": result["metrics"],
        "output_path": str(resolved),
        "split_manifest_hash": result["split_manifest"]["manifest_hash"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate R5 frozen policy baselines.")
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--reaction-artifact",
        type=Path,
        default=Path("artifacts/reaction/pooled_ordered_logit_v1.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/evaluation/frozen_45_policy_baselines_v1.json"),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            materialize_frozen_evaluation(
                args.source,
                args.app,
                args.reaction_artifact,
                args.output,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
