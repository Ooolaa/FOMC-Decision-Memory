from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from statsmodels.miscmodels.ordinal_model import OrderedModel


FEATURE_NAMES = (
    "cpi_yoy",
    "unemployment_level",
    "unemployment_12m_change",
    "payroll_yoy",
    "credit_spread_baa10y",
    "yield_curve_10y_2y",
    "policy_midpoint",
)
ACTION_VALUE = {"CUT": 0, "HOLD": 1, "HIKE": 2}
VALUE_ACTION = {value: action for action, value in ACTION_VALUE.items()}


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _snapshot_values(
    source: sqlite3.Connection,
    meeting_id: str,
    series_id: str,
) -> list[tuple[str, float]]:
    rows = source.execute(
        """
        SELECT snapshot.observation_date, vintage.value_num
        FROM meeting_snapshot_value AS snapshot
        JOIN observation_vintage AS vintage
          ON vintage.series_id = snapshot.series_id
         AND vintage.observation_date = snapshot.observation_date
         AND vintage.realtime_start = snapshot.realtime_start
        WHERE snapshot.meeting_id = ?
          AND snapshot.series_id = ?
          AND vintage.value_num IS NOT NULL
        ORDER BY snapshot.observation_date
        """,
        (meeting_id, series_id),
    ).fetchall()
    if not rows:
        raise RuntimeError(f"No snapshot values for {meeting_id}/{series_id}")
    return [(str(row[0]), float(row[1])) for row in rows]


def _latest(values: list[tuple[str, float]]) -> float:
    return values[-1][1]


def _year_ago_pair(values: list[tuple[str, float]]) -> tuple[float, float]:
    latest_date, latest_value = values[-1]
    year_ago_date = f"{int(latest_date[:4]) - 1:04d}{latest_date[4:]}"
    historical = {observation_date: value for observation_date, value in values}
    if year_ago_date not in historical:
        raise RuntimeError(
            f"Snapshot lacks 12-month lag for latest observation {latest_date}"
        )
    return latest_value, historical[year_ago_date]


def _yoy_percent(values: list[tuple[str, float]]) -> float:
    latest_value, year_ago_value = _year_ago_pair(values)
    if year_ago_value == 0:
        raise RuntimeError("Year-ago value is zero")
    return (latest_value / year_ago_value - 1.0) * 100.0


def build_meeting_feature_row(
    source: sqlite3.Connection,
    app: sqlite3.Connection,
    meeting_id: str,
) -> dict[str, float]:
    cpi = _snapshot_values(source, meeting_id, "CPIAUCSL")
    unemployment = _snapshot_values(source, meeting_id, "UNRATE")
    payroll = _snapshot_values(source, meeting_id, "PAYEMS")
    unemployment_latest, unemployment_year_ago = _year_ago_pair(unemployment)
    policy = app.execute(
        """
        SELECT target_rate, lower_rate, upper_rate
        FROM policy_rate_context
        WHERE meeting_id = ? AND ordinal = 0 AND record_kind = 'CURRENT'
        """,
        (meeting_id,),
    ).fetchone()
    if policy is None:
        raise RuntimeError(f"No compact policy context for {meeting_id}")
    if policy[0] is not None:
        policy_midpoint = float(policy[0])
    elif policy[1] is not None and policy[2] is not None:
        policy_midpoint = (float(policy[1]) + float(policy[2])) / 2.0
    else:
        raise RuntimeError(f"Incomplete current policy range for {meeting_id}")
    return {
        "cpi_yoy": _yoy_percent(cpi),
        "unemployment_level": unemployment_latest,
        "unemployment_12m_change": unemployment_latest - unemployment_year_ago,
        "payroll_yoy": _yoy_percent(payroll),
        "credit_spread_baa10y": _latest(
            _snapshot_values(source, meeting_id, "BAA10Y")
        ),
        "yield_curve_10y_2y": (
            _latest(_snapshot_values(source, meeting_id, "DGS10"))
            - _latest(_snapshot_values(source, meeting_id, "DGS2"))
        ),
        "policy_midpoint": policy_midpoint,
    }


def predict_ordered_logit(
    feature_row: dict[str, float],
    artifact: dict[str, Any],
) -> dict[str, Any]:
    eta = 0.0
    for feature in artifact["features"]:
        standardized = (
            float(feature_row[feature]) - float(artifact["means"][feature])
        ) / float(artifact["scales"][feature])
        eta += standardized * float(artifact["coefficients"][feature])
    cutpoints = [float(value) for value in artifact["cutpoints"]]
    if len(cutpoints) != 2 or cutpoints[0] >= cutpoints[1]:
        raise ValueError("Three-class ordered logit requires two increasing cutpoints")
    cumulative = [1.0 / (1.0 + np.exp(-(cutpoint - eta))) for cutpoint in cutpoints]
    probabilities = [
        cumulative[0],
        cumulative[1] - cumulative[0],
        1.0 - cumulative[1],
    ]
    predicted_value = int(np.argmax(probabilities))
    return {
        "action_class": VALUE_ACTION[predicted_value],
        "action_value": predicted_value,
        "probabilities": {
            VALUE_ACTION[index]: float(probability)
            for index, probability in enumerate(probabilities)
        },
        "linear_predictor": float(eta),
    }


def train_pooled_ordered_logit(
    source: sqlite3.Connection,
    app: sqlite3.Connection,
    *,
    train_end: str = "2020-12-31",
) -> dict[str, Any]:
    meetings = source.execute(
        """
        SELECT meeting_id, meeting_start_date
        FROM fomc_meeting
        WHERE meeting_start_date <= ?
        ORDER BY meeting_start_date
        """,
        (train_end,),
    ).fetchall()
    rows = []
    actions = []
    meeting_ids = []
    for meeting_id, _ in meetings:
        outcome = app.execute(
            "SELECT action_class FROM meeting_outcome WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()
        if outcome is None:
            raise RuntimeError(f"Missing training outcome: {meeting_id}")
        feature_row = build_meeting_feature_row(source, app, str(meeting_id))
        rows.append([feature_row[name] for name in FEATURE_NAMES])
        actions.append(ACTION_VALUE[str(outcome[0])])
        meeting_ids.append(str(meeting_id))
    if len(rows) < 30 or len(set(actions)) != 3:
        raise RuntimeError("Ordered logit requires at least 30 rows and all three actions")

    matrix = np.asarray(rows, dtype=float)
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0, ddof=0)
    if np.any(scales <= 0):
        raise RuntimeError("A reaction feature has zero training variance")
    standardized = (matrix - means) / scales
    endog = np.asarray(actions, dtype=int)
    model = OrderedModel(endog, standardized, distr="logit")
    fitted = model.fit(method="bfgs", disp=False, maxiter=1000)
    probabilities = np.asarray(fitted.model.predict(fitted.params, exog=standardized))
    predicted = probabilities.argmax(axis=1)
    accuracy = float(np.mean(predicted == endog))
    mean_absolute_action_error = float(np.mean(np.abs(predicted - endog)))
    coefficients = {
        name: float(fitted.params[index])
        for index, name in enumerate(FEATURE_NAMES)
    }
    thresholds = {
        str(name): float(fitted.params[index])
        for index, name in enumerate(
            fitted.model.data.param_names[len(FEATURE_NAMES):],
            start=len(FEATURE_NAMES),
        )
    }
    cutpoints = [
        float(value)
        for value in fitted.model.transform_threshold_params(fitted.params)[1:-1]
    ]
    training_payload = {
        "model_version": "pooled_ordered_logit_v1",
        "meeting_ids": meeting_ids,
        "features": list(FEATURE_NAMES),
        "means": dict(zip(FEATURE_NAMES, means.tolist())),
        "scales": dict(zip(FEATURE_NAMES, scales.tolist())),
        "actions": actions,
        "coefficients": coefficients,
        "thresholds": thresholds,
        "cutpoints": cutpoints,
    }
    model_hash = hashlib.sha256(
        json.dumps(training_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        **training_payload,
        "model_id": f"reaction-{model_hash[:24]}",
        "training_start": str(meetings[0][1]),
        "training_end": str(meetings[-1][1]),
        "training_meeting_count": len(meeting_ids),
        "class_counts": dict(sorted(Counter(actions).items())),
        "accuracy": accuracy,
        "mean_absolute_action_error": mean_absolute_action_error,
        "log_likelihood": float(fitted.llf),
        "converged": bool(fitted.mle_retvals.get("converged", False)),
    }


def persist_reaction_model(
    app: sqlite3.Connection,
    artifact: dict[str, Any],
) -> None:
    metrics = {
        key: artifact[key]
        for key in (
            "training_meeting_count",
            "class_counts",
            "accuracy",
            "mean_absolute_action_error",
            "log_likelihood",
            "converged",
            "means",
            "scales",
            "thresholds",
            "cutpoints",
        )
    }
    expected_model = (
        artifact["model_version"],
        artifact["training_start"],
        artifact["training_end"],
        json.dumps(metrics, sort_keys=True),
    )
    app.execute(
        """
        INSERT OR IGNORE INTO reaction_model (
            model_id, model_version, train_start, train_end,
            metrics_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (artifact["model_id"], *expected_model, _utc_now()),
    )
    persisted = app.execute(
        """
        SELECT model_version, train_start, train_end, metrics_json
        FROM reaction_model WHERE model_id = ?
        """,
        (artifact["model_id"],),
    ).fetchone()
    if persisted != expected_model:
        raise RuntimeError("Existing reaction model conflicts with artifact")
    for feature, coefficient in artifact["coefficients"].items():
        app.execute(
            """
            INSERT OR IGNORE INTO reaction_coefficient (
                model_id, participant_id, feature, coefficient
            ) VALUES (?, NULL, ?, ?)
            """,
            (artifact["model_id"], feature, coefficient),
        )


def train_and_materialize_reaction_model(
    source_database: Path,
    app_database: Path,
    artifact_path: Path,
) -> dict[str, Any]:
    source_path = source_database.resolve()
    app_path = app_database.resolve()
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    app = sqlite3.connect(f"file:{app_path.as_posix()}?mode=rw", uri=True)
    app.execute("PRAGMA foreign_keys = ON")
    try:
        artifact = train_pooled_ordered_logit(source, app)
        persist_reaction_model(app, artifact)
        app.commit()
        integrity = app.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = app.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                f"App DB validation failed: integrity={integrity}, "
                f"foreign_keys={foreign_key_errors}"
            )
    except Exception:
        app.rollback()
        raise
    finally:
        app.close()
        source.close()
    resolved_artifact = artifact_path.resolve()
    resolved_artifact.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if resolved_artifact.exists():
        if resolved_artifact.read_text(encoding="utf-8") != serialized:
            raise RuntimeError(f"Existing reaction artifact differs: {resolved_artifact}")
    else:
        resolved_artifact.write_text(serialized, encoding="utf-8")
    return {
        "model_id": artifact["model_id"],
        "training_meeting_count": artifact["training_meeting_count"],
        "class_counts": artifact["class_counts"],
        "accuracy": artifact["accuracy"],
        "mean_absolute_action_error": artifact["mean_absolute_action_error"],
        "converged": artifact["converged"],
        "artifact_path": str(resolved_artifact),
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the R5 pooled ordered logit.")
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--artifact",
        type=Path,
        default=Path("artifacts/reaction/pooled_ordered_logit_v1.json"),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            train_and_materialize_reaction_model(args.source, args.app, args.artifact),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
