from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from decision_memory.lag_evaluator import _statement_text, evaluate_observable_lag
from decision_memory.lag_spec import load_observable_lag_spec


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def evaluate_statement_alerts(
    app: sqlite3.Connection,
    *,
    adopted_at: str,
    contradiction_at: str,
    as_of_date: str,
    support_patterns: list[str],
    flip_patterns: list[str],
) -> dict[str, Any]:
    support_regexes = [re.compile(pattern, re.IGNORECASE) for pattern in support_patterns]
    flip_regexes = [re.compile(pattern, re.IGNORECASE) for pattern in flip_patterns]
    rows = app.execute(
        """
        SELECT document_id, meeting_id, publication_at,
               source_locator, content_hash
        FROM document_source
        WHERE document_type = 'statement'
          AND usage_class IN ('label_only', 'evaluation_only')
          AND substr(publication_at, 1, 10) <= ?
        ORDER BY publication_at, document_id
        """,
        (as_of_date,),
    ).fetchall()

    statements: list[dict[str, Any]] = []
    support_was_observed = False
    first_alert_at: str | None = None
    for document_id, meeting_id, publication_at, locator, content_hash in rows:
        statement_date = str(publication_at)[:10]
        text = _statement_text(str(locator), str(content_hash))
        has_support = any(pattern.search(text) for pattern in support_regexes)
        has_flip = any(pattern.search(text) for pattern in flip_regexes)

        if statement_date < adopted_at:
            classification = "PRE_ADOPTION"
        elif statement_date < contradiction_at:
            if has_support:
                support_was_observed = True
            classification = (
                "SUPPRESSED_PRE_CONTRADICTION"
                if has_flip and not has_support
                else "SUPPORT"
                if has_support
                else "NO_MATCH"
            )
        elif has_support and has_flip:
            classification = "SUPPRESSED_SUPPORT_FLIP_COOCCURRENCE"
        elif has_flip:
            if not support_was_observed:
                raise RuntimeError(
                    "No registered support phrase was observed before the candidate flip"
                )
            if first_alert_at is None:
                first_alert_at = statement_date
                classification = "ALERT"
            else:
                classification = "POST_ALERT_REPEAT"
        elif has_support:
            classification = "SUPPORT"
        else:
            classification = "NO_MATCH"

        statements.append(
            {
                "document_id": str(document_id),
                "meeting_id": str(meeting_id),
                "statement_date": statement_date,
                "content_hash": str(content_hash),
                "has_support_pattern": has_support,
                "has_flip_pattern": has_flip,
                "classification": classification,
            }
        )

    if not support_was_observed:
        raise RuntimeError(
            "No registered support phrase was observed on or before the contradiction"
        )

    active = [row for row in statements if row["statement_date"] >= adopted_at]
    pre_contradiction = [
        row for row in active if row["statement_date"] < contradiction_at
    ]
    cooccurrence = [
        row
        for row in active
        if row["has_support_pattern"] and row["has_flip_pattern"]
    ]
    temporal_false_alarms = [
        row
        for row in statements
        if row["classification"] == "ALERT"
        and row["statement_date"] < contradiction_at
    ]
    cooccurrence_false_alarms = [
        row
        for row in statements
        if row["classification"] == "ALERT" and row["has_support_pattern"]
    ]
    return {
        "statement_count": len(statements),
        "active_claim_statement_count": len(active),
        "pre_contradiction_statement_count": len(pre_contradiction),
        "first_alert_at": first_alert_at,
        "alert_event_count": sum(
            row["classification"] == "ALERT" for row in statements
        ),
        "post_alert_repeat_count": sum(
            row["classification"] == "POST_ALERT_REPEAT" for row in statements
        ),
        "pre_contradiction_flip_only_count": sum(
            row["classification"] == "SUPPRESSED_PRE_CONTRADICTION"
            for row in statements
        ),
        "support_flip_cooccurrence_count": len(cooccurrence),
        "temporal_false_alarm_count": len(temporal_false_alarms),
        "temporal_false_alarm_rate": _rate(
            len(temporal_false_alarms), len(pre_contradiction)
        ),
        "cooccurrence_false_alarm_count": len(cooccurrence_false_alarms),
        "cooccurrence_false_alarm_rate": _rate(
            len(cooccurrence_false_alarms), len(cooccurrence)
        ),
        "statements": statements,
    }


def build_alert_audit(
    source_database: Path,
    app_database: Path,
    spec_path: Path,
    *,
    as_of_date: str,
) -> dict[str, Any]:
    spec = load_observable_lag_spec(spec_path.resolve())
    source = sqlite3.connect(
        f"file:{source_database.resolve().as_posix()}?mode=ro", uri=True
    )
    app = sqlite3.connect(
        f"file:{app_database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        lag = evaluate_observable_lag(source, app, spec, as_of_date=as_of_date)
        phrase_set = spec["phrase_set"]
        audit = evaluate_statement_alerts(
            app,
            adopted_at=spec["assumption_adopted_at"],
            contradiction_at=lag["first_contradiction_at"],
            as_of_date=as_of_date,
            support_patterns=phrase_set["support_patterns"],
            flip_patterns=phrase_set["flip_patterns"],
        )
    finally:
        app.close()
        source.close()

    if audit["first_alert_at"] != lag["statement_flip_at"]:
        raise RuntimeError(
            "Alert audit does not reproduce the registered statement flip: "
            f"{audit['first_alert_at']} != {lag['statement_flip_at']}"
        )
    return {
        "schema_version": 1,
        "spec_id": spec["spec_id"],
        "phrase_set_version": phrase_set["version"],
        "as_of_date": as_of_date,
        "assumption_adopted_at": spec["assumption_adopted_at"],
        "first_contradiction_at": lag["first_contradiction_at"],
        "registered_statement_flip_at": lag["statement_flip_at"],
        "scope": (
            "All locally cached official FOMC statements through as_of_date; "
            "each file hash is rechecked before classification."
        ),
        "interpretation": (
            "False-alarm rates cover deterministic temporal and support/flip "
            "cooccurrence controls only. They are not human semantic labels and "
            "must not be presented as an overall real-world false-positive rate."
        ),
        **audit,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit deterministic assumption-alert false-alarm controls."
    )
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument(
        "--spec",
        type=Path,
        default=Path("metric_spec/inflation_transitory_v1.json"),
    )
    parser.add_argument("--as-of-date", default="2026-08-27")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/evaluation/statement_alert_audit_v1.json"),
    )
    args = parser.parse_args()
    report = build_alert_audit(
        args.source,
        args.app,
        args.spec,
        as_of_date=args.as_of_date,
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "statement_count": report["statement_count"],
                "first_alert_at": report["first_alert_at"],
                "temporal_false_alarm_count": report["temporal_false_alarm_count"],
                "cooccurrence_false_alarm_count": report[
                    "cooccurrence_false_alarm_count"
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
