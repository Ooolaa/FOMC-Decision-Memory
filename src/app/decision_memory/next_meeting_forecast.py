from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from decision_memory.forecast_ensemble import load_forward_ensemble_artifact
from decision_memory.member_evidence import enrich_voter_rows
from decision_memory.official_forecast_context import (
    apply_supplemental_communications,
    load_official_forecast_context,
)
from decision_memory.reaction_model import predict_ordered_logit


NEXT_MEETING = {
    "meeting_id": "FOMC-2026-09-15",
    "meeting_start_date": "2026-09-15",
    "meeting_end_date": "2026-09-16",
    "has_sep": True,
    "calendar_source_url": (
        "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    ),
}


def _visible_values(
    connection: sqlite3.Connection,
    series_id: str,
    as_of_date: str,
) -> list[tuple[str, str, float]]:
    rows = connection.execute(
        """
        WITH ranked AS (
            SELECT
                observation_date,
                realtime_start,
                value_num,
                ROW_NUMBER() OVER (
                    PARTITION BY observation_date
                    ORDER BY realtime_start DESC
                ) AS version_rank
            FROM observation_vintage
            WHERE series_id = ?
              AND observation_date <= ?
              AND realtime_start <= ?
              AND value_num IS NOT NULL
        )
        SELECT observation_date, realtime_start, value_num
        FROM ranked
        WHERE version_rank = 1
        ORDER BY observation_date
        """,
        (series_id, as_of_date, as_of_date),
    ).fetchall()
    if not rows:
        raise RuntimeError(f"No visible values for {series_id} as of {as_of_date}")
    return [(str(row[0]), str(row[1]), float(row[2])) for row in rows]


def _year_ago_pair(
    values: list[tuple[str, str, float]],
) -> tuple[tuple[str, str, float], tuple[str, str, float]]:
    latest = values[-1]
    year_ago_date = f"{int(latest[0][:4]) - 1:04d}{latest[0][4:]}"
    history = {row[0]: row for row in values}
    if year_ago_date not in history:
        raise RuntimeError(f"No 12-month lag for latest observation {latest[0]}")
    return latest, history[year_ago_date]


def _yoy_percent(values: list[tuple[str, str, float]]) -> float:
    latest, year_ago = _year_ago_pair(values)
    if year_ago[2] == 0:
        raise RuntimeError("Year-ago value is zero")
    return (latest[2] / year_ago[2] - 1.0) * 100.0


def _latest_evidence(
    series_id: str,
    values: list[tuple[str, str, float]],
) -> dict[str, Any]:
    observation_date, realtime_start, value = values[-1]
    return {
        "series_id": series_id,
        "observation_date": observation_date,
        "visible_version_date": realtime_start,
        "value": value,
    }


def build_as_of_feature_row(
    source: sqlite3.Connection,
    as_of_date: str,
) -> tuple[dict[str, float], list[dict[str, Any]], dict[str, Any]]:
    series = {
        series_id: _visible_values(source, series_id, as_of_date)
        for series_id in (
            "CPIAUCSL",
            "UNRATE",
            "PAYEMS",
            "BAA10Y",
            "DGS10",
            "DGS2",
            "DFEDTARL",
            "DFEDTARU",
        )
    }
    unemployment_latest, unemployment_year_ago = _year_ago_pair(series["UNRATE"])
    lower = series["DFEDTARL"][-1]
    upper = series["DFEDTARU"][-1]
    policy_context = {
        "lower_rate": lower[2],
        "upper_rate": upper[2],
        "midpoint": (lower[2] + upper[2]) / 2.0,
        "effective_date": max(lower[0], upper[0]),
    }
    feature_row = {
        "cpi_yoy": _yoy_percent(series["CPIAUCSL"]),
        "unemployment_level": unemployment_latest[2],
        "unemployment_12m_change": (
            unemployment_latest[2] - unemployment_year_ago[2]
        ),
        "payroll_yoy": _yoy_percent(series["PAYEMS"]),
        "credit_spread_baa10y": series["BAA10Y"][-1][2],
        "yield_curve_10y_2y": (
            series["DGS10"][-1][2] - series["DGS2"][-1][2]
        ),
        "policy_midpoint": policy_context["midpoint"],
    }
    evidence = [
        _latest_evidence(series_id, values)
        for series_id, values in series.items()
    ]
    return feature_row, evidence, policy_context


def _database_as_of(source: sqlite3.Connection) -> str:
    row = source.execute(
        "SELECT value FROM database_metadata WHERE key = 'last_updated_at_utc'"
    ).fetchone()
    if row is None:
        raise RuntimeError("Source database has no last_updated_at_utc metadata")
    available_date = date.fromisoformat(str(row[0])[:10])
    meeting_cutoff = (
        date.fromisoformat(NEXT_MEETING["meeting_start_date"]) - timedelta(days=1)
    )
    return min(available_date, meeting_cutoff).isoformat()


def _latest_completed_meeting(app: sqlite3.Connection) -> tuple[str, str]:
    row = app.execute(
        """
        SELECT meeting_id, action_class
        FROM meeting_outcome
        WHERE meeting_id < ?
        ORDER BY meeting_id DESC
        LIMIT 1
        """,
        (NEXT_MEETING["meeting_id"],),
    ).fetchone()
    if row is None:
        raise RuntimeError("No completed meeting is available before the forecast")
    return str(row[0]), str(row[1])


def _build_voter_forecast(
    app: sqlite3.Connection,
    source_meeting_id: str,
    threshold: float,
    *,
    official_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if official_context is None:
        roster = app.execute(
            """
            SELECT participant.participant_id, participant.display_name,
                   meeting_participant.role
            FROM meeting_participant
            JOIN participant
              ON participant.participant_id = meeting_participant.participant_id
            WHERE meeting_participant.meeting_id = ?
              AND meeting_participant.is_voter = 1
            ORDER BY participant.display_name
            """,
            (source_meeting_id,),
        ).fetchall()
    else:
        roster = []
        for member in official_context["voting_members"]:
            database_member = app.execute(
                """
                SELECT participant_id, display_name
                FROM participant
                WHERE participant_id = ? AND display_name = ?
                """,
                (member["participant_id"], member["display_name"]),
            ).fetchone()
            if database_member is None:
                raise RuntimeError(
                    "Official voting member is not present in the app database: "
                    f"{member['display_name']}"
                )
            roster.append((database_member[0], database_member[1], member["role"]))
        roster.sort(key=lambda item: str(item[1]))
    if not roster:
        raise RuntimeError(f"No voter roster for {source_meeting_id}")
    rows = []
    for participant_id, display_name, role in roster:
        history = app.execute(
            """
            SELECT meeting_id, MAX(dissent)
            FROM participant_vote
            WHERE participant_id = ?
              AND meeting_id <= ?
            GROUP BY meeting_id
            ORDER BY meeting_id
            """,
            (participant_id, source_meeting_id),
        ).fetchall()
        vote_count = len(history)
        dissent_count = sum(bool(item[1]) for item in history)
        dissent_rate = dissent_count / vote_count if vote_count else 0.0
        predicted_against = bool(vote_count and dissent_rate >= threshold)
        rows.append(
            {
                "participant_id": str(participant_id),
                "display_name": str(display_name),
                "role": str(role),
                "prior_vote_count": vote_count,
                "prior_dissent_count": dissent_count,
                "prior_dissent_rate": dissent_rate,
                "predicted_vote": "AGAINST" if predicted_against else "FOR",
            }
        )
    result = {
        "roster_status": (
            "OFFICIAL_CURRENT_MEMBERSHIP"
            if official_context is not None
            else "PROVISIONAL"
        ),
        "source_meeting_id": source_meeting_id,
        "prediction_target": "FOR_AGAINST_ON_PRIMARY_POLICY_PROPOSAL",
        "baseline": "prior_dissent_rate",
        "threshold": threshold,
        "rows": rows,
    }
    if official_context is not None:
        result.update(
            {
                "membership_source_url": official_context["membership_source_url"],
                "membership_source_updated_at": official_context[
                    "membership_source_updated_at"
                ],
                "membership_semantics": official_context["membership_semantics"],
            }
        )
    return result


def build_next_meeting_forecast(
    source_database: Path,
    app_database: Path,
    reaction_artifact_path: Path,
    policy_evaluation_path: Path,
    vote_evaluation_path: Path,
    *,
    communications_database: Path | None = None,
    official_context_path: Path | None = None,
    ensemble_artifact_path: Path | None = None,
) -> dict[str, Any]:
    reaction_artifact = json.loads(
        reaction_artifact_path.read_text(encoding="utf-8")
    )
    policy_evaluation = json.loads(
        policy_evaluation_path.read_text(encoding="utf-8")
    )
    vote_evaluation = json.loads(
        vote_evaluation_path.read_text(encoding="utf-8")
    )
    official_context = (
        load_official_forecast_context(official_context_path)
        if official_context_path is not None
        else None
    )
    source = sqlite3.connect(
        f"file:{source_database.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    app = sqlite3.connect(
        f"file:{app_database.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    try:
        forecast_as_of = _database_as_of(source)
        if official_context is not None:
            forecast_as_of = min(forecast_as_of, official_context["as_of_date"])
        feature_row, feature_evidence, policy_context = build_as_of_feature_row(
            source,
            forecast_as_of,
        )
        reaction_prediction = predict_ordered_logit(
            feature_row,
            reaction_artifact,
        )
        source_meeting_id, persistence_action = _latest_completed_meeting(app)
        policy_metrics = policy_evaluation["metrics"]
        primary_model = max(
            ("persistence", "pooled_reaction"),
            key=lambda model: float(policy_metrics[model]["accuracy"]),
        )
        action_class = (
            persistence_action
            if primary_model == "persistence"
            else reaction_prediction["action_class"]
        )
        voter_forecast = _build_voter_forecast(
            app,
            source_meeting_id,
            float(vote_evaluation["selected_prior_dissent_rate_threshold"]),
            official_context=official_context,
        )
        enrich_voter_rows(
            app,
            voter_forecast,
            communications_database=communications_database,
            forecast_as_of=forecast_as_of,
        )
        if official_context is not None:
            apply_supplemental_communications(voter_forecast, official_context)
        ensemble = (
            load_forward_ensemble_artifact(
                ensemble_artifact_path,
                workspace_root=Path(__file__).resolve().parents[1],
                expected_meeting_id=NEXT_MEETING["meeting_id"],
                meeting_start_date=NEXT_MEETING["meeting_start_date"],
                roster_participant_ids=[
                    row["participant_id"] for row in voter_forecast["rows"]
                ],
                fallback_action=action_class,
            )
            if ensemble_artifact_path is not None
            else None
        )
        if ensemble is not None:
            action_class = ensemble["combined"]["policy"]["action_class"]
            ensemble_votes = {
                row["participant_id"]: row
                for row in ensemble["combined"]["votes"]
            }
            for row in voter_forecast["rows"]:
                row["baseline_predicted_vote"] = row["predicted_vote"]
                combined_vote = ensemble_votes[row["participant_id"]]
                row["predicted_vote"] = combined_vote["predicted_vote"]
                row["against_support_count"] = combined_vote[
                    "against_support_count"
                ]
                row["ensemble_model_count"] = combined_vote["model_count"]
    finally:
        app.close()
        source.close()

    return {
        **NEXT_MEETING,
        "forecast_as_of": forecast_as_of,
        "policy_prediction": {
            "action_class": action_class,
            "primary_model": (
                "forward_ensemble" if ensemble is not None else primary_model
            ),
            "source_meeting_id": source_meeting_id,
            "persistence_action_class": persistence_action,
            "ordered_logit_action_class": reaction_prediction["action_class"],
            "probabilities": reaction_prediction["probabilities"],
            "models_agree": persistence_action == reaction_prediction["action_class"],
            "frozen_accuracy": {
                model: float(policy_metrics[model]["accuracy"])
                for model in ("persistence", "pooled_reaction")
            },
            "ensemble": ensemble,
        },
        "policy_context": policy_context,
        "features": feature_row,
        "feature_evidence": feature_evidence,
        "voter_forecast": voter_forecast,
        "status": (
            "LOCKED_FORWARD_ENSEMBLE"
            if ensemble is not None
            else "PROVISIONAL_OFFLINE_BASELINE"
        ),
    }
