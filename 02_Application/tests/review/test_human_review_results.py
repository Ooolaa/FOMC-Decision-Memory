import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from decision_memory.human_review_results import validate_human_review_results


CHECKS = [
    "context_summary_supported",
    "options_and_debate_supported",
    "participant_attribution_supported",
]
ROOT = Path(__file__).resolve().parents[2]


class HumanReviewResultsTests(unittest.TestCase):
    def _fixture(self, root: Path, *, decisions=("PASS", "PASS")):
        sample = root / "sample.json"
        sample.write_text(
            json.dumps(
                {
                    "schema_version": "decision_trace_human_review_sample_v1",
                    "status": "PENDING_HUMAN_REVIEW",
                    "review_checklist": CHECKS,
                    "cases": [
                        {"meeting_id": "FOMC-A", "review_status": "PENDING"},
                        {"meeting_id": "FOMC-B", "review_status": "PENDING"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        reviews = []
        for meeting_id, decision in zip(("FOMC-A", "FOMC-B"), decisions):
            reviews.append(
                {
                    "meeting_id": meeting_id,
                    "reviewer": "Human Reviewer",
                    "reviewed_at": "2026-08-29T13:00:00+08:00",
                    "case_decision": decision,
                    "checklist_results": {
                        check: decision == "PASS" for check in CHECKS
                    },
                    "notes": "Checked against the cited source evidence.",
                }
            )
        expected_status = (
            "APPROVED_SAMPLE"
            if all(decision == "PASS" for decision in decisions)
            else "COMPLETE_WITH_FINDINGS"
        )
        results = root / "results.json"
        results.write_text(
            json.dumps(
                {
                    "schema_version": "decision_trace_human_review_results_v1",
                    "sample_manifest": "sample.json",
                    "sample_manifest_sha256": hashlib.sha256(
                        sample.read_bytes()
                    ).hexdigest(),
                    "human_reviewer_attestation": "I_AM_A_HUMAN_REVIEWER",
                    "review_status": expected_status,
                    "reviews": reviews,
                }
            ),
            encoding="utf-8",
        )
        return sample, results

    def test_complete_all_pass_results_open_the_sample_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sample, results = self._fixture(root)
            audit = validate_human_review_results(sample, results, root=root)

        self.assertEqual(audit["review_status"], "APPROVED_SAMPLE")
        self.assertEqual(audit["reviewed_case_count"], 2)
        self.assertEqual(audit["formal_import_gate"], "PASS")

    def test_complete_findings_keep_formal_import_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sample, results = self._fixture(root, decisions=("PASS", "FAIL"))
            audit = validate_human_review_results(sample, results, root=root)

        self.assertEqual(audit["review_status"], "COMPLETE_WITH_FINDINGS")
        self.assertEqual(audit["formal_import_gate"], "BLOCKED")
        self.assertEqual(audit["decision_counts"], {"FAIL": 1, "PASS": 1})

    def test_missing_case_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sample, results = self._fixture(root)
            payload = json.loads(results.read_text(encoding="utf-8"))
            payload["reviews"].pop()
            payload["review_status"] = "IN_PROGRESS"
            results.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "review set"):
                validate_human_review_results(sample, results, root=root)

    def test_wrong_sample_hash_or_missing_human_attestation_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sample, results = self._fixture(root)
            payload = json.loads(results.read_text(encoding="utf-8"))
            payload["sample_manifest_sha256"] = "0" * 64
            results.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "sample manifest hash"):
                validate_human_review_results(sample, results, root=root)

            payload["sample_manifest_sha256"] = hashlib.sha256(
                sample.read_bytes()
            ).hexdigest()
            payload.pop("human_reviewer_attestation")
            results.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "human reviewer attestation"):
                validate_human_review_results(sample, results, root=root)

    def test_workspace_template_matches_the_frozen_review_sample(self):
        sample_path = (
            ROOT
            / "artifacts/codex_subscription/decision_trace_50_v4/"
            "human_review_sample_v1.json"
        )
        template_path = (
            ROOT / "submission_templates/decision_trace_human_review_results_v1.json"
        )
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        template = json.loads(template_path.read_text(encoding="utf-8"))

        self.assertEqual(
            template["sample_manifest_sha256"],
            hashlib.sha256(sample_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            [item["meeting_id"] for item in template["reviews"]],
            [item["meeting_id"] for item in sample["cases"]],
        )
        self.assertEqual(
            set(template["reviews"][0]["checklist_results"]),
            set(sample["review_checklist"]),
        )
        self.assertEqual(
            template["human_reviewer_attestation"],
            "__FILL_AFTER_REAL_HUMAN_REVIEW__",
        )


if __name__ == "__main__":
    unittest.main()
