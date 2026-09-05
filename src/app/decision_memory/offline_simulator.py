from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from decision_memory.reaction_model import (
    build_meeting_feature_row,
    predict_ordered_logit,
)


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "simulation_output_v1.json"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def validate_simulation_output(
    output: dict[str, Any],
    *,
    expected_meeting_id: str | None = None,
) -> dict[str, int]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if expected_meeting_id is not None:
        schema["properties"]["meeting_id"] = {
            "type": "string",
            "const": expected_meeting_id,
        }
    errors = sorted(
        Draft202012Validator(schema).iter_errors(output),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        raise ValueError(f"Simulation schema violation: {errors[0].message}")
    participant_ids = [item["participant_id"] for item in output["profiles"]]
    if len(participant_ids) != len(set(participant_ids)):
        raise ValueError("Simulation profiles contain duplicate participants")
    chairs = [
        item["participant_id"] for item in output["profiles"] if item["is_chair"]
    ]
    if len(chairs) != 1:
        raise ValueError("Simulation must contain exactly one Chair")
    if output["final_proposal"]["proposer_participant_id"] != chairs[0]:
        raise ValueError("Final proposal must be made by the Chair")
    discussion_ids = [item["participant_id"] for item in output["discussion"]]
    vote_ids = [item["participant_id"] for item in output["votes"]]
    if set(discussion_ids) != set(participant_ids):
        raise ValueError("Every profile must have exactly one discussion contribution")
    if len(discussion_ids) != len(set(discussion_ids)):
        raise ValueError("Discussion contains duplicate participants")
    if set(vote_ids) != set(participant_ids) or len(vote_ids) != len(set(vote_ids)):
        raise ValueError("Votes must balance one-for-one with profiles")
    return {
        "participant_count": len(participant_ids),
        "for_count": sum(item["choice"] == "FOR" for item in output["votes"]),
        "against_count": sum(
            item["choice"] == "AGAINST" for item in output["votes"]
        ),
    }


def simulate_offline_case(
    source: sqlite3.Connection,
    app: sqlite3.Connection,
    reaction_artifact: dict[str, Any],
    meeting_id: str,
) -> dict[str, Any]:
    feature_row = build_meeting_feature_row(source, app, meeting_id)
    prediction = predict_ordered_logit(feature_row, reaction_artifact)
    participants = app.execute(
        """
        SELECT participant.participant_id, participant.display_name,
               meeting_participant.is_chair
        FROM meeting_participant
        JOIN participant USING (participant_id)
        WHERE meeting_participant.meeting_id = ?
          AND meeting_participant.is_voter = 1
        ORDER BY meeting_participant.is_chair DESC, participant.display_name
        """,
        (meeting_id,),
    ).fetchall()
    if not participants:
        raise RuntimeError(f"No voter profiles for {meeting_id}")
    profiles = []
    discussion = []
    for participant_id, display_name, is_chair in participants:
        history = app.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(dissent), 0)
            FROM participant_vote
            WHERE participant_id = ? AND meeting_id < ?
            """,
            (participant_id, meeting_id),
        ).fetchone()
        prior_votes, prior_dissents = map(int, history)
        profiles.append(
            {
                "participant_id": str(participant_id),
                "display_name": str(display_name),
                "is_chair": bool(is_chair),
                "profile_source": "pre-cutoff vote history plus pooled reaction model",
                "prior_vote_count": prior_votes,
                "prior_dissent_rate": (
                    prior_dissents / prior_votes if prior_votes else None
                ),
                "pooled_action_probabilities": prediction["probabilities"],
            }
        )
        discussion.append(
            {
                "participant_id": str(participant_id),
                "synthetic_text": (
                    f"[Synthetic baseline] The pooled reaction model favors "
                    f"{prediction['action_class']}. This fallback does not claim an "
                    f"individualized historical stance for {display_name}."
                ),
            }
        )
    chair_id = next(item["participant_id"] for item in profiles if item["is_chair"])
    output = {
        "schema_version": "simulation_output_v1",
        "meeting_id": meeting_id,
        "synthetic": True,
        "profiles": profiles,
        "discussion": discussion,
        "final_proposal": {
            "proposer_participant_id": chair_id,
            "action_class": prediction["action_class"],
            "rationale": (
                "Offline pooled ordered-logit baseline; no LLM or actual outcome was "
                "used to choose the proposal."
            ),
        },
        "votes": [
            {"participant_id": item["participant_id"], "choice": "FOR"}
            for item in profiles
        ],
    }
    validation = validate_simulation_output(output)
    return {
        "output": output,
        "validation": validation,
        "feature_row": feature_row,
        "prediction": prediction,
    }


def materialize_offline_case(
    source_database: Path,
    app_database: Path,
    reaction_artifact_path: Path,
    output_path: Path,
    *,
    meeting_id: str,
) -> dict[str, Any]:
    reaction_artifact = json.loads(
        reaction_artifact_path.read_text(encoding="utf-8")
    )
    source_path = source_database.resolve()
    app_path = app_database.resolve()
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    app = sqlite3.connect(f"file:{app_path.as_posix()}?mode=rw", uri=True)
    app.execute("PRAGMA foreign_keys = ON")
    try:
        result = simulate_offline_case(source, app, reaction_artifact, meeting_id)
        manifest_hash = _sha256_json(
            {
                "meeting_id": meeting_id,
                "model_id": reaction_artifact["model_id"],
                "feature_row": result["feature_row"],
                "participant_ids": [
                    item["participant_id"] for item in result["output"]["profiles"]
                ],
            }
        )
        case_id = f"offline-{meeting_id}"
        app.execute(
            """
            INSERT OR IGNORE INTO simulation_case (
                case_id, meeting_id, decision_id, manifest_hash,
                synthetic, created_at
            ) VALUES (?, ?, NULL, ?, 1, ?)
            """,
            (case_id, meeting_id, manifest_hash, _utc_now()),
        )
        persisted_case = app.execute(
            "SELECT meeting_id, manifest_hash, synthetic FROM simulation_case WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        if persisted_case != (meeting_id, manifest_hash, 1):
            raise RuntimeError("Existing offline simulation case conflicts")
        output_json = _canonical_json(result["output"])
        schema_hash = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()
        run_id = f"run-{_sha256_json({'case': case_id, 'output': result['output']})[:24]}"
        app.execute(
            """
            INSERT OR IGNORE INTO simulation_run (
                run_id, case_id, model_id, prompt_hash, schema_hash,
                output_json, input_tokens, cached_tokens, output_tokens,
                cost_usd, latency_ms, synthetic, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 1,
                      'CACHED_OFFLINE_BASELINE', ?)
            """,
            (
                run_id,
                case_id,
                reaction_artifact["model_id"],
                "offline-template-v1",
                schema_hash,
                output_json,
                _utc_now(),
            ),
        )
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
    artifact = {
        **result,
        "case_id": case_id,
        "run_id": run_id,
        "manifest_hash": manifest_hash,
        "model_id": reaction_artifact["model_id"],
        "status": "CACHED_OFFLINE_BASELINE",
    }
    serialized = json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    resolved = output_path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if resolved.exists():
        if resolved.read_text(encoding="utf-8") != serialized:
            raise RuntimeError(f"Existing offline simulation differs: {resolved}")
    else:
        resolved.write_text(serialized, encoding="utf-8")
    return {
        "case_id": case_id,
        "run_id": run_id,
        "meeting_id": meeting_id,
        "proposal": result["output"]["final_proposal"]["action_class"],
        **result["validation"],
        "status": "CACHED_OFFLINE_BASELINE",
        "cost_usd": 0,
        "output_path": str(resolved),
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the offline cached demo case.")
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
        default=Path("artifacts/cache/fomc_2022_03_15_offline_baseline.json"),
    )
    parser.add_argument("--meeting-id", default="FOMC-2022-03-15")
    args = parser.parse_args()
    print(
        json.dumps(
            materialize_offline_case(
                args.source,
                args.app,
                args.reaction_artifact,
                args.output,
                meeting_id=args.meeting_id,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
