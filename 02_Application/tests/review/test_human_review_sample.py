import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from decision_memory.human_review_sample import build_human_review_sample


ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HumanReviewSampleTests(unittest.TestCase):
    def test_selection_includes_all_repairs_flag_coverage_and_standard_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = [
                ("A", "HIGH", ["SEMANTIC_REPAIR_USED", "ATTRIBUTION_DEMOTED_TO_COMMITTEE"]),
                ("B", "HIGH", ["SEMANTIC_REPAIR_USED"]),
                ("C", "HIGH", ["NO_PARTICIPANT_LEVEL_DEBATE"]),
                ("D", "HIGH", ["MISMATCHED_ATTRIBUTION_REFERENCE_REMOVED"]),
                ("E", "STANDARD", []),
                ("F", "STANDARD", []),
                ("G", "STANDARD", []),
            ]
            queue_cases = []
            for meeting_id, priority, flags in cases:
                run = root / f"{meeting_id}.json"
                run.write_text(json.dumps({"meeting_id": meeting_id}), encoding="utf-8")
                queue_cases.append(
                    {
                        "meeting_id": meeting_id,
                        "audit_priority": priority,
                        "flags": flags,
                        "review_status": "PENDING",
                        "run_artifact": run.name,
                        "run_artifact_sha256": _sha256(run),
                        "deterministic_revalidation": {"executed": True, "valid": True},
                    }
                )
            queue = root / "qa_queue.json"
            queue.write_text(
                json.dumps(
                    {
                        "schema_version": "decision_trace_qa_queue_v1",
                        "status": "PENDING_HUMAN_REVIEW",
                        "case_count": len(queue_cases),
                        "cases": queue_cases,
                    }
                ),
                encoding="utf-8",
            )

            first = build_human_review_sample(queue, target_count=6, root=root)
            second = build_human_review_sample(queue, target_count=6, root=root)

        self.assertEqual(first, second)
        selected = {item["meeting_id"]: item for item in first["cases"]}
        self.assertEqual(len(selected), 6)
        self.assertTrue({"A", "B", "C", "D"}.issubset(selected))
        self.assertEqual(
            sum(item["audit_priority"] == "STANDARD" for item in selected.values()),
            2,
        )
        self.assertEqual(first["status"], "PENDING_HUMAN_REVIEW")

    def test_real_queue_freezes_twelve_case_risk_stratified_sample(self):
        sample = build_human_review_sample(
            ROOT
            / "artifacts/codex_subscription/decision_trace_50_v4/qa_queue.json",
            target_count=12,
            root=ROOT,
        )

        self.assertEqual(sample["case_count"], 12)
        self.assertEqual(sample["semantic_repair_case_count"], 8)
        self.assertGreaterEqual(sample["standard_case_count"], 2)
        self.assertEqual(
            set(sample["covered_flags"]),
            {
                "ATTRIBUTION_DEMOTED_TO_COMMITTEE",
                "MISMATCHED_ATTRIBUTION_REFERENCE_REMOVED",
                "NO_PARTICIPANT_LEVEL_DEBATE",
                "SEMANTIC_REPAIR_USED",
            },
        )
        self.assertTrue(all(item["review_status"] == "PENDING" for item in sample["cases"]))


if __name__ == "__main__":
    unittest.main()
