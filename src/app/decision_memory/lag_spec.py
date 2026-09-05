from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load_observable_lag_spec(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as source:
        spec = json.load(source)

    if spec.get("spec_id") != "inflation_transitory_v1":
        raise ValueError("Unexpected observable-lag spec_id")
    if spec.get("status") != "pre_registered":
        raise ValueError("Observable-lag spec must be pre_registered before freeze")
    date.fromisoformat(spec["freeze_on"])
    date.fromisoformat(spec["assumption_adopted_at"])
    contradiction = spec.get("contradiction_rule", {})
    if contradiction.get("transformation") != "YEAR_OVER_YEAR_PERCENT":
        raise ValueError("Only point-in-time year-over-year transformation is supported")
    if contradiction.get("operator") != "GT":
        raise ValueError("inflation_transitory_v1 requires a GT contradiction rule")
    if float(contradiction.get("threshold_value", 0)) <= 0:
        raise ValueError("Contradiction threshold must be positive")
    if contradiction.get("vintage_policy") != (
        "FIRST_RELEASE_WITH_LAG_VALUE_VISIBLE_THAT_DAY"
    ):
        raise ValueError("Observable lag must use the registered first-release policy")
    phrase_set = spec.get("phrase_set", {})
    if not phrase_set.get("support_patterns") or not phrase_set.get("flip_patterns"):
        raise ValueError("Both support and flip phrase patterns are required")
    if spec.get("policy_direction") not in {"HAWKISH", "DOVISH"}:
        raise ValueError("policy_direction must be HAWKISH or DOVISH")
    if spec.get("policy_response_rule_version") != "rate_only_response_v1":
        raise ValueError("Observable lag must use rate_only_response_v1")
    return spec


def load_rate_only_spec(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as source:
        spec = json.load(source)

    if spec.get("spec_id") != "rate_only_response_v1":
        raise ValueError("Unexpected rate-only spec_id")
    episodes = spec.get("constraint_episodes")
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("constraint_episodes must be a non-empty list")

    previous_end: date | None = None
    expected_total = 0
    for episode in episodes:
        start = date.fromisoformat(episode["start"])
        end = date.fromisoformat(episode["end"])
        if start > end:
            raise ValueError("Constraint episode start must not exceed end")
        if previous_end is not None and start <= previous_end:
            raise ValueError("Constraint episodes must be ordered and non-overlapping")
        expected_meetings = int(episode["expected_meetings"])
        if expected_meetings <= 0:
            raise ValueError("expected_meetings must be positive")
        expected_total += expected_meetings
        previous_end = end

    if expected_total != int(spec.get("expected_constrained_meetings", -1)):
        raise ValueError("Constraint episode checksum does not match expected total")
    if spec.get("non_rate_tools_close_event") is not False:
        raise ValueError("rate_only_response_v1 cannot be closed by non-rate tools")
    return spec


def is_rate_constrained(meeting_start_date: str, spec: dict[str, Any]) -> bool:
    meeting_date = date.fromisoformat(meeting_start_date)
    return any(
        date.fromisoformat(episode["start"])
        <= meeting_date
        <= date.fromisoformat(episode["end"])
        for episode in spec["constraint_episodes"]
    )


def constraint_episode_counts(
    connection: sqlite3.Connection,
    spec: dict[str, Any],
) -> list[int]:
    return [
        int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM fomc_meeting
                WHERE meeting_start_date BETWEEN ? AND ?
                """,
                (episode["start"], episode["end"]),
            ).fetchone()[0]
        )
        for episode in spec["constraint_episodes"]
    ]


def frozen_constraint_audit(
    connection: sqlite3.Connection,
    spec: dict[str, Any],
    *,
    test_start: str,
) -> dict[str, Any]:
    date.fromisoformat(test_start)
    rows = connection.execute(
        """
        SELECT meeting_id, meeting_start_date
        FROM fomc_meeting
        WHERE meeting_start_date >= ?
        ORDER BY meeting_start_date, meeting_id
        """,
        (test_start,),
    ).fetchall()
    cases = [
        {
            "meeting_id": str(meeting_id),
            "meeting_start_date": str(meeting_start_date),
            "rate_constrained": is_rate_constrained(str(meeting_start_date), spec),
        }
        for meeting_id, meeting_start_date in rows
    ]
    constrained = [item for item in cases if item["rate_constrained"]]
    split_payload = {
        "test_start": test_start,
        "meeting_ids": [item["meeting_id"] for item in cases],
    }
    split_hash = hashlib.sha256(
        json.dumps(split_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "test_start": test_start,
        "case_count": len(cases),
        "constrained_case_count": len(constrained),
        "observed_rate_capacity_case_count": len(cases) - len(constrained),
        "constrained_meeting_ids": [item["meeting_id"] for item in constrained],
        "split_manifest_hash": split_hash,
        "cases": cases,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise ValueError("Lag audit output must stay inside workspace")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if resolved.exists():
        if resolved.read_text(encoding="utf-8") != serialized:
            raise FileExistsError(f"Refusing to overwrite lag audit: {resolved}")
        return
    resolved.write_text(serialized, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit the preregistered rate-constraint episodes and Frozen split."
    )
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument(
        "--spec", type=Path, default=Path("metric_spec/rate_only_response_v1.json")
    )
    parser.add_argument("--test-start", default="2021-01-01")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/evaluation/rate_only_censoring_audit_v1.json"),
    )
    args = parser.parse_args()
    source_path = args.source.resolve()
    spec_path = args.spec.resolve()
    spec = load_rate_only_spec(spec_path)
    connection = sqlite3.connect(
        f"file:{source_path.as_posix()}?mode=ro",
        uri=True,
    )
    try:
        episode_counts = constraint_episode_counts(connection, spec)
        frozen = frozen_constraint_audit(
            connection,
            spec,
            test_start=args.test_start,
        )
    finally:
        connection.close()
    if episode_counts != [55, 15]:
        raise RuntimeError(f"Constraint episode counts changed: {episode_counts}")
    if frozen["case_count"] != 45 or frozen["constrained_case_count"] != 9:
        raise RuntimeError(
            "Frozen constraint gate changed: "
            f"{frozen['constrained_case_count']}/{frozen['case_count']}"
        )
    artifact = {
        "schema_version": "rate_only_censoring_audit_v1",
        "status": "COMPLETED",
        "spec_id": spec["spec_id"],
        "spec_sha256": _sha256_file(spec_path),
        "source_database_sha256": _sha256_file(source_path),
        "constraint_episode_counts": episode_counts,
        "expected_constrained_meetings": spec["expected_constrained_meetings"],
        "censoring_disclosure": spec["censoring_disclosure"],
        "recognition_censored_by_rate_constraint": spec[
            "recognition_censored_by_rate_constraint"
        ],
        **frozen,
    }
    _write_new_json(args.output, artifact)
    print(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
