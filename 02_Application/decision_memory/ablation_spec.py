from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_PAID_VARIANTS = {
    "naked_frozen_llm",
    "named_persona_reaction",
    "anonymous_persona_reaction",
    "named_persona_no_reaction",
    "date_only_memorization_probe",
}
REQUIRED_METRICS = {
    "policy_accuracy",
    "policy_action_mae",
    "false_action_on_hold",
    "dissent_base_rate",
    "dissent_precision",
    "dissent_recall",
    "dissent_f1",
}


def load_ablation_spec(path: Path) -> dict[str, Any]:
    spec = json.loads(path.read_text(encoding="utf-8"))
    if spec.get("spec_id") != "hackathon_r5_variants_v1":
        raise ValueError("Unexpected R5 ablation spec_id")
    if len(str(spec.get("split_manifest_hash", ""))) != 64:
        raise ValueError("A frozen split manifest hash is required")
    variants = spec.get("variants")
    if not isinstance(variants, list):
        raise ValueError("variants must be a list")
    by_id = {item["variant_id"]: item for item in variants}
    if len(by_id) != len(variants):
        raise ValueError("variant_id values must be unique")
    missing = REQUIRED_PAID_VARIANTS - set(by_id)
    if missing:
        raise ValueError(f"Missing required paid variants: {sorted(missing)}")
    if set(spec.get("required_metrics", [])) != REQUIRED_METRICS:
        raise ValueError("Required deterministic policy/dissent metrics changed")
    for variant_id in REQUIRED_PAID_VARIANTS:
        if by_id[variant_id]["execution_status"] != "NOT_EXECUTED_CAP_REQUIRED":
            raise ValueError(
                f"Paid variant must remain fail-closed before cap approval: {variant_id}"
            )
    date_probe = by_id["date_only_memorization_probe"]
    if not date_probe["meeting_date"] or any(
        date_probe[field]
        for field in ("participant_names", "persona_evidence", "reaction_model", "economic_snapshot")
    ):
        raise ValueError("Date probe must expose only the meeting date")
    return spec
