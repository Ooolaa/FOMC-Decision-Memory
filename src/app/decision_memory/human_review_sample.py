from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPAIR_FLAG = "SEMANTIC_REPAIR_USED"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rank(queue_hash: str, stratum: str, meeting_id: str) -> str:
    return hashlib.sha256(
        f"{queue_hash}|{stratum}|{meeting_id}".encode("utf-8")
    ).hexdigest()


def _relative(path: Path, root: Path) -> str:
    resolved = path.resolve()
    root = root.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Review artifact must stay under root: {resolved}")
    return resolved.relative_to(root).as_posix()


def build_human_review_sample(
    qa_queue_path: Path,
    *,
    target_count: int = 12,
    root: Path = ROOT,
) -> dict[str, Any]:
    root = root.resolve()
    qa_queue_path = qa_queue_path.resolve()
    if target_count < 1:
        raise ValueError("target_count must be positive")
    if not qa_queue_path.is_file() or not qa_queue_path.is_relative_to(root):
        raise ValueError("QA queue must be a file under root")

    queue_hash = _sha256_file(qa_queue_path)
    queue = json.loads(qa_queue_path.read_text(encoding="utf-8"))
    cases = list(queue.get("cases") or [])
    if queue.get("status") != "PENDING_HUMAN_REVIEW":
        raise ValueError("QA queue must remain PENDING_HUMAN_REVIEW")
    if int(queue.get("case_count", -1)) != len(cases):
        raise ValueError("QA queue case_count does not match cases")
    if target_count > len(cases):
        raise ValueError("target_count exceeds QA queue size")

    by_meeting: dict[str, dict[str, Any]] = {}
    for item in cases:
        meeting_id = str(item.get("meeting_id") or "")
        if not meeting_id or meeting_id in by_meeting:
            raise ValueError("QA queue contains a missing or duplicate meeting_id")
        if (item.get("deterministic_revalidation") or {}).get("valid") is not True:
            raise ValueError(f"Case is not deterministically revalidated: {meeting_id}")
        run_path = (root / str(item.get("run_artifact") or "")).resolve()
        if not run_path.is_file() or not run_path.is_relative_to(root):
            raise ValueError(f"Run artifact is missing or outside root: {meeting_id}")
        if _sha256_file(run_path) != item.get("run_artifact_sha256"):
            raise ValueError(f"Run artifact hash mismatch: {meeting_id}")
        by_meeting[meeting_id] = item

    selected_reasons: dict[str, list[str]] = {}

    def select(meeting_id: str, reason: str) -> None:
        selected_reasons.setdefault(meeting_id, []).append(reason)

    repair_cases = sorted(
        (
            item
            for item in cases
            if REPAIR_FLAG in set(item.get("flags") or [])
        ),
        key=lambda item: item["meeting_id"],
    )
    if len(repair_cases) > target_count:
        raise ValueError("target_count is smaller than the semantic-repair census")
    for item in repair_cases:
        select(item["meeting_id"], "ALL_SEMANTIC_REPAIRS")

    all_flags = sorted({flag for item in cases for flag in (item.get("flags") or [])})
    for flag in all_flags:
        if any(flag in set(by_meeting[mid].get("flags") or []) for mid in selected_reasons):
            continue
        candidates = [item for item in cases if flag in set(item.get("flags") or [])]
        chosen = min(
            candidates,
            key=lambda item: _rank(queue_hash, f"FLAG:{flag}", item["meeting_id"]),
        )
        select(chosen["meeting_id"], f"FLAG_REPRESENTATIVE:{flag}")

    if len(selected_reasons) > target_count:
        raise ValueError("target_count is too small for repair census and flag coverage")

    remaining = target_count - len(selected_reasons)
    standard = sorted(
        (
            item
            for item in cases
            if item.get("audit_priority") == "STANDARD"
            and item["meeting_id"] not in selected_reasons
        ),
        key=lambda item: _rank(queue_hash, "STANDARD", item["meeting_id"]),
    )
    for item in standard[:remaining]:
        select(item["meeting_id"], "STANDARD_HASH_SAMPLE")

    remaining = target_count - len(selected_reasons)
    fallback = sorted(
        (item for item in cases if item["meeting_id"] not in selected_reasons),
        key=lambda item: _rank(queue_hash, "FALLBACK", item["meeting_id"]),
    )
    for item in fallback[:remaining]:
        select(item["meeting_id"], "HASH_FILL")

    if len(selected_reasons) != target_count:
        raise RuntimeError("Could not fill the requested review sample")

    selected_cases = []
    for meeting_id in sorted(selected_reasons):
        item = by_meeting[meeting_id]
        selected_cases.append(
            {
                "meeting_id": meeting_id,
                "audit_priority": item["audit_priority"],
                "flags": list(item.get("flags") or []),
                "selection_reasons": selected_reasons[meeting_id],
                "run_artifact": item["run_artifact"],
                "run_artifact_sha256": item["run_artifact_sha256"],
                "review_status": "PENDING",
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "decision_trace_human_review_sample_v1",
        "status": "PENDING_HUMAN_REVIEW",
        "qa_queue": _relative(qa_queue_path, root),
        "qa_queue_sha256": queue_hash,
        "source_case_count": len(cases),
        "case_count": len(selected_cases),
        "semantic_repair_case_count": sum(
            REPAIR_FLAG in set(item["flags"]) for item in selected_cases
        ),
        "standard_case_count": sum(
            item["audit_priority"] == "STANDARD" for item in selected_cases
        ),
        "covered_flags": sorted(
            {flag for item in selected_cases for flag in item["flags"]}
        ),
        "selection_algorithm": {
            "version": "risk_stratified_sha256_v1",
            "target_count": target_count,
            "steps": [
                "include every semantic-repair case",
                "add one SHA-256-ranked representative for each uncovered QA flag",
                "fill with SHA-256-ranked STANDARD cases",
                "fill any residual capacity by SHA-256 rank",
            ],
            "ranking_seed": "qa_queue_sha256",
        },
        "review_checklist": [
            "context_summary_supported",
            "options_and_debate_supported",
            "participant_attribution_supported",
            "decision_and_vote_match_labels",
            "assumption_is_falsifiable_and_monitorable",
            "no_post_cutoff_or_synthetic_source_leakage",
        ],
        "review_results_contract": {
            "separate_file_required": True,
            "must_reference_sample_manifest_sha256": True,
            "allowed_case_decisions": ["PASS", "FAIL", "NEEDS_CORRECTION"],
            "minimum_required_fields": [
                "meeting_id",
                "reviewer",
                "reviewed_at",
                "case_decision",
                "checklist_results",
                "notes",
            ],
        },
        "cases": selected_cases,
    }
    payload["sample_manifest_hash"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze a deterministic human-review sample from DecisionTrace QA."
    )
    parser.add_argument(
        "--qa-queue",
        type=Path,
        default=Path(
            "artifacts/codex_subscription/decision_trace_50_v4/qa_queue.json"
        ),
    )
    parser.add_argument("--target-count", type=int, default=12)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "artifacts/codex_subscription/decision_trace_50_v4/"
            "human_review_sample_v1.json"
        ),
    )
    args = parser.parse_args()
    payload = build_human_review_sample(
        args.qa_queue,
        target_count=args.target_count,
        root=ROOT,
    )
    output = args.output.resolve()
    if not output.is_relative_to(ROOT.resolve()):
        raise ValueError("Human-review sample output must stay in workspace")
    if output.exists():
        existing = json.loads(output.read_text(encoding="utf-8"))
        if existing != payload:
            raise RuntimeError(f"Existing human-review sample differs: {output}")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as target:
            json.dump(payload, target, ensure_ascii=False, indent=2, sort_keys=True)
            target.write("\n")
    print(
        json.dumps(
            {
                "output": _relative(output, ROOT),
                "case_count": payload["case_count"],
                "semantic_repair_case_count": payload[
                    "semantic_repair_case_count"
                ],
                "standard_case_count": payload["standard_case_count"],
                "covered_flags": payload["covered_flags"],
                "sample_manifest_hash": payload["sample_manifest_hash"],
                "status": payload["status"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
