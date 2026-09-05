from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_DECISIONS = {"PASS", "FAIL", "NEEDS_CORRECTION"}
HUMAN_ATTESTATION = "I_AM_A_HUMAN_REVIEWER"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_file(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"{label} must be a file under root")
    return resolved


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _require_timezone_timestamp(value: Any, meeting_id: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"reviewed_at is missing: {meeting_id}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"reviewed_at is not ISO 8601: {meeting_id}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"reviewed_at must include a timezone: {meeting_id}")


def validate_human_review_results(
    sample_manifest_path: Path,
    results_path: Path,
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    root = root.resolve()
    sample_manifest_path = _workspace_file(
        sample_manifest_path, root, "Sample manifest"
    )
    results_path = _workspace_file(results_path, root, "Review results")
    sample_sha256 = _sha256_file(sample_manifest_path)
    results_sha256 = _sha256_file(results_path)
    sample = json.loads(sample_manifest_path.read_text(encoding="utf-8"))
    results = json.loads(results_path.read_text(encoding="utf-8"))

    if sample.get("schema_version") != "decision_trace_human_review_sample_v1":
        raise ValueError("Unsupported sample manifest schema")
    if sample.get("status") != "PENDING_HUMAN_REVIEW":
        raise ValueError("Sample manifest must remain PENDING_HUMAN_REVIEW")
    if results.get("schema_version") != "decision_trace_human_review_results_v1":
        raise ValueError("Unsupported human-review results schema")
    if results.get("sample_manifest") != _relative(sample_manifest_path, root):
        raise ValueError("Review results reference the wrong sample manifest")
    if results.get("sample_manifest_sha256") != sample_sha256:
        raise ValueError("Review results sample manifest hash does not match")
    if results.get("human_reviewer_attestation") != HUMAN_ATTESTATION:
        raise ValueError("Exact human reviewer attestation is required")

    sample_cases = list(sample.get("cases") or [])
    sample_ids = [str(item.get("meeting_id") or "") for item in sample_cases]
    if not sample_ids or "" in sample_ids or len(set(sample_ids)) != len(sample_ids):
        raise ValueError("Sample manifest has missing or duplicate meeting IDs")
    checklist = list(sample.get("review_checklist") or [])
    if not checklist or len(set(checklist)) != len(checklist):
        raise ValueError("Sample manifest review checklist is invalid")

    reviews = list(results.get("reviews") or [])
    review_ids = [str(item.get("meeting_id") or "") for item in reviews]
    if len(review_ids) != len(set(review_ids)) or set(review_ids) != set(sample_ids):
        raise ValueError("review set must contain each sampled meeting exactly once")

    decisions = Counter()
    checklist_failures = 0
    for review in reviews:
        meeting_id = str(review["meeting_id"])
        reviewer = review.get("reviewer")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError(f"Reviewer is missing: {meeting_id}")
        _require_timezone_timestamp(review.get("reviewed_at"), meeting_id)
        decision = review.get("case_decision")
        if decision not in ALLOWED_DECISIONS:
            raise ValueError(f"Invalid case decision: {meeting_id}")
        decisions[decision] += 1
        checklist_results = review.get("checklist_results")
        if not isinstance(checklist_results, dict) or set(checklist_results) != set(
            checklist
        ):
            raise ValueError(f"Checklist keys do not match the sample: {meeting_id}")
        if not all(isinstance(value, bool) for value in checklist_results.values()):
            raise ValueError(f"Checklist values must be booleans: {meeting_id}")
        checklist_failures += sum(not value for value in checklist_results.values())
        notes = review.get("notes")
        if not isinstance(notes, str) or not notes.strip():
            raise ValueError(f"Review notes are required: {meeting_id}")

    approved = decisions == {"PASS": len(sample_ids)} and checklist_failures == 0
    computed_status = "APPROVED_SAMPLE" if approved else "COMPLETE_WITH_FINDINGS"
    if results.get("review_status") != computed_status:
        raise ValueError(
            f"review_status must be {computed_status} for the submitted reviews"
        )

    return {
        "schema_version": "decision_trace_human_review_audit_v1",
        "sample_manifest": _relative(sample_manifest_path, root),
        "sample_manifest_sha256": sample_sha256,
        "review_results": _relative(results_path, root),
        "review_results_sha256": results_sha256,
        "review_status": computed_status,
        "sample_case_count": len(sample_ids),
        "reviewed_case_count": len(reviews),
        "decision_counts": dict(sorted(decisions.items())),
        "checklist_failure_count": checklist_failures,
        "formal_import_gate": "PASS" if approved else "BLOCKED",
        "formal_import_disclosure": (
            "PASS applies only to the sampled cases and does not mean all 50 traces "
            "were individually reviewed."
            if approved
            else "At least one sampled case has a finding; formal import remains blocked."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate completed human DecisionTrace review results fail closed."
    )
    parser.add_argument(
        "--sample",
        type=Path,
        default=Path(
            "artifacts/codex_subscription/decision_trace_50_v4/"
            "human_review_sample_v1.json"
        ),
    )
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    audit = validate_human_review_results(args.sample, args.results, root=ROOT)
    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if audit["formal_import_gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
