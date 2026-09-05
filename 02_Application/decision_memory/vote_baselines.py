from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence


EVALUATION_VERSION = "known_roster_vote_baselines_v1"


def _safe_divide(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def binary_metrics(
    actual: Sequence[bool],
    predicted: Sequence[bool],
) -> dict[str, Any]:
    if not actual or len(actual) != len(predicted):
        raise ValueError("actual and predicted must have equal non-zero length")
    true_positive = sum(a and p for a, p in zip(actual, predicted))
    false_positive = sum(not a and p for a, p in zip(actual, predicted))
    false_negative = sum(a and not p for a, p in zip(actual, predicted))
    true_negative = len(actual) - true_positive - false_positive - false_negative
    precision = _safe_divide(true_positive, true_positive + false_positive)
    recall = _safe_divide(true_positive, true_positive + false_negative)
    specificity = _safe_divide(true_negative, true_negative + false_positive)
    f1 = _safe_divide(2 * precision * recall, precision + recall)
    mcc_denominator = math.sqrt(
        (true_positive + false_positive)
        * (true_positive + false_negative)
        * (true_negative + false_positive)
        * (true_negative + false_negative)
    )
    return {
        "n": len(actual),
        "positive_count": sum(actual),
        "base_rate": _safe_divide(sum(actual), len(actual)),
        "predicted_positive_count": sum(predicted),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "accuracy": _safe_divide(true_positive + true_negative, len(actual)),
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "balanced_accuracy": (recall + specificity) / 2,
        "f1": f1,
        "matthews_correlation": _safe_divide(
            true_positive * true_negative - false_positive * false_negative,
            mcc_denominator,
        ),
    }


def _meeting_date(meeting_id: str) -> str:
    if not meeting_id.startswith("FOMC-") or len(meeting_id) != 15:
        raise ValueError(f"Unsupported meeting_id: {meeting_id}")
    return meeting_id[5:]


def _build_point_in_time_cases(app: sqlite3.Connection) -> list[dict[str, Any]]:
    meeting_ids = [
        str(row[0])
        for row in app.execute(
            """
            SELECT DISTINCT meeting_id FROM meeting_participant
            WHERE is_voter = 1
            ORDER BY meeting_id
            """
        ).fetchall()
    ]
    if not meeting_ids:
        raise RuntimeError("No known voter rosters are available")
    history: defaultdict[str, list[bool]] = defaultdict(list)
    cases: list[dict[str, Any]] = []
    for meeting_id in meeting_ids:
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
        labels = {
            str(participant_id): bool(dissent)
            for participant_id, dissent in app.execute(
                """
                SELECT participant_id, MAX(dissent)
                FROM participant_vote
                WHERE meeting_id = ?
                GROUP BY participant_id
                """,
                (meeting_id,),
            ).fetchall()
        }
        if set(labels) != known_voters:
            missing = sorted(known_voters - set(labels))
            extra = sorted(set(labels) - known_voters)
            raise ValueError(
                f"{meeting_id} known voter labels are incomplete: "
                f"missing={missing}, extra={extra}"
            )
        for participant_id in sorted(known_voters):
            prior = history[participant_id]
            prior_dissent_count = sum(prior)
            cases.append(
                {
                    "meeting_id": meeting_id,
                    "meeting_date": _meeting_date(meeting_id),
                    "participant_id": participant_id,
                    "actual_against": labels[participant_id],
                    "prior_vote_count": len(prior),
                    "prior_dissent_count": prior_dissent_count,
                    "prior_dissent_rate": (
                        prior_dissent_count / len(prior) if prior else None
                    ),
                    "previous_vote_against": prior[-1] if prior else False,
                }
            )
        for participant_id in known_voters:
            history[participant_id].append(labels[participant_id])
    return cases


def _rate_predictions(
    cases: Sequence[dict[str, Any]],
    threshold: float,
) -> list[bool]:
    return [
        bool(
            case["prior_vote_count"]
            and float(case["prior_dissent_rate"]) >= threshold
        )
        for case in cases
    ]


def evaluate_vote_baselines(
    app: sqlite3.Connection,
    *,
    test_start: str = "2021-01-01",
) -> dict[str, Any]:
    cases = _build_point_in_time_cases(app)
    training = [case for case in cases if case["meeting_date"] < test_start]
    test = [case for case in cases if case["meeting_date"] >= test_start]
    if not training or not test:
        raise RuntimeError("Vote baseline split requires non-empty training and test cases")
    threshold_grid = [value / 20 for value in range(1, 21)]
    threshold_trace = []
    training_actual = [bool(case["actual_against"]) for case in training]
    for threshold in threshold_grid:
        metrics = binary_metrics(
            training_actual,
            _rate_predictions(training, threshold),
        )
        threshold_trace.append({"threshold": threshold, "metrics": metrics})
    selected = max(
        threshold_trace,
        key=lambda item: (
            item["metrics"]["f1"],
            item["metrics"]["balanced_accuracy"],
            item["metrics"]["precision"],
            item["threshold"],
        ),
    )
    selected_threshold = float(selected["threshold"])
    actual = [bool(case["actual_against"]) for case in test]
    predictions = {
        "all_for": [False] * len(test),
        "previous_vote": [bool(case["previous_vote_against"]) for case in test],
        "prior_dissent_rate": _rate_predictions(test, selected_threshold),
    }
    baselines = {
        name: binary_metrics(actual, values)
        for name, values in predictions.items()
    }
    reference_name, reference_metrics = max(
        baselines.items(),
        key=lambda item: (
            item[1]["f1"],
            item[1]["balanced_accuracy"],
            item[1]["precision"],
        ),
    )
    per_case = []
    for index, case in enumerate(test):
        per_case.append(
            {
                **case,
                "all_for_prediction": predictions["all_for"][index],
                "previous_vote_prediction": predictions["previous_vote"][index],
                "prior_dissent_rate_prediction": predictions["prior_dissent_rate"][index],
            }
        )
    return {
        "evaluation_version": EVALUATION_VERSION,
        "known_input": "meeting_participant.is_voter=1",
        "prediction_target": "participant_vote.FOR_AGAINST",
        "test_start": test_start,
        "training_meeting_count": len({case["meeting_id"] for case in training}),
        "training_voter_case_count": len(training),
        "test_meeting_count": len({case["meeting_id"] for case in test}),
        "test_voter_case_count": len(test),
        "label_coverage": 1.0,
        "selected_prior_dissent_rate_threshold": selected_threshold,
        "threshold_selection": {
            "training_only": True,
            "selection_order": [
                "f1",
                "balanced_accuracy",
                "precision",
                "higher_threshold",
            ],
            "trace": threshold_trace,
        },
        "baselines": baselines,
        "gate": {
            "required_roster_coverage": 1.0,
            "required_label_coverage": 1.0,
            "primary_metric": "dissent_f1",
            "reference_baseline": reference_name,
            "reference_f1": reference_metrics["f1"],
            "promotion_condition": "model_dissent_f1 > reference_f1",
            "accuracy_is_guardrail_only": True,
        },
        "per_case": per_case,
    }


def materialize_vote_baselines(
    app_database: Path,
    output_path: Path,
    *,
    test_start: str = "2021-01-01",
) -> dict[str, Any]:
    app_path = app_database.resolve()
    if not app_path.is_file():
        raise FileNotFoundError(f"App database does not exist: {app_path}")
    app = sqlite3.connect(f"file:{app_path.as_posix()}?mode=ro", uri=True)
    try:
        result = evaluate_vote_baselines(app, test_start=test_start)
        integrity = app.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = app.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"App DB validation failed: integrity={integrity}, "
                f"foreign_keys={foreign_key_errors}"
            )
    finally:
        app.close()
    result["app_database"] = str(app_path)
    result["app_database_sha256"] = hashlib.sha256(app_path.read_bytes()).hexdigest()
    serialized = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    resolved_output = output_path.resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    if resolved_output.exists():
        if resolved_output.read_text(encoding="utf-8") != serialized:
            raise RuntimeError(f"Existing vote baseline artifact differs: {resolved_output}")
    else:
        resolved_output.write_text(serialized, encoding="utf-8")
    return {
        "evaluation_version": result["evaluation_version"],
        "output_path": str(resolved_output),
        "app_database_sha256": result["app_database_sha256"],
        "test_meeting_count": result["test_meeting_count"],
        "test_voter_case_count": result["test_voter_case_count"],
        "baselines": result["baselines"],
        "gate": result["gate"],
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate deterministic per-known-voter dissent baselines."
    )
    parser.add_argument(
        "--app",
        type=Path,
        default=Path("fomc_simulation.vote_labels_fixed_candidate.sqlite"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "artifacts/evaluation/frozen_45_vote_baselines_candidate_v1.json"
        ),
    )
    parser.add_argument("--test-start", default="2021-01-01")
    args = parser.parse_args()
    print(
        json.dumps(
            materialize_vote_baselines(
                args.app,
                args.output,
                test_start=args.test_start,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
