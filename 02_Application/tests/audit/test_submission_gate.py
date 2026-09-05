import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from decision_memory.submission_gate import (
    evaluate_submission_gate,
    evaluate_technical_readiness,
    validate_final_ui_rehearsal,
    validate_submission_signoff,
)


ROOT = Path(__file__).resolve().parents[2]


class SubmissionGateTests(unittest.TestCase):
    def _final_ui_fixture(self, root: Path):
        app = root / "app.py"
        app.write_text("app", encoding="utf-8")
        launcher = root / "run_app.ps1"
        launcher.write_text("launcher", encoding="utf-8")
        matrix = root / "matrix.json"
        matrix.write_text("{}", encoding="utf-8")
        views = [
            ("next_meeting_forecast", "下次會議預測", None),
            ("decision_replay", "決策重播", None),
            ("historical_results", "歷史測試結果", None),
        ]
        modes = []
        for mode_name in (
            "normal_browser_path",
            "process_without_openai_api_key",
            "stop_and_restart",
        ):
            mode_dir = root / mode_name
            mode_dir.mkdir()
            report = []
            for view_id, page, domain in views:
                screenshot = mode_dir / f"{view_id}.png"
                screenshot.write_bytes(f"image-{view_id}".encode("utf-8"))
                item = {
                    "page": page,
                    "screenshot": screenshot.name,
                    "body_text_chars": 100,
                    "body_text_sha256": hashlib.sha256(
                        f"body-{view_id}".encode("utf-8")
                    ).hexdigest(),
                }
                if domain:
                    item["domain"] = domain
                report.append(item)
            report_path = mode_dir / "capture_report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            mode = {
                "name": mode_name,
                "result": "PASS",
                "capture_report": report_path.relative_to(root).as_posix(),
                "capture_report_sha256": hashlib.sha256(
                    report_path.read_bytes()
                ).hexdigest(),
                "health_probe": "status=200 body=ok",
            }
            if mode_name == "process_without_openai_api_key":
                mode["api_key_removed_from_child_process"] = True
            if mode_name == "stop_and_restart":
                mode["stopped_probe_failed"] = True
            modes.append(mode)

        canonical = []
        normal_dir = root / "normal_browser_path"
        for view_id, _, _ in views:
            destination = root / f"canonical-{view_id}.png"
            destination.write_bytes((normal_dir / f"{view_id}.png").read_bytes())
            canonical.append(
                {
                    "view_id": view_id,
                    "path": destination.relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                }
            )
        payload = {
            "schema_version": "hackathon_r5_final_ui_rehearsal_v8",
            "status": "PASS",
            "executed_at": "2026-08-29T15:00:00+08:00",
            "app_sha256": hashlib.sha256(app.read_bytes()).hexdigest(),
            "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
            "matrix_path": "matrix.json",
            "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
            "browser": {
                "family": "Microsoft Edge",
                "version": "test",
                "viewport": "1440x1100",
            },
            "network_binding": {
                "address": "127.0.0.1",
                "wildcard_listener_absent": True,
                "health_probe": "status=200 body=ok",
            },
            "modes": modes,
            "canonical_screenshots": canonical,
            "equivalence": {
                "body_text_sha256_equal_for_all_required_views": True,
                "screenshot_sha256_equal_for_all_required_views": True,
            },
        }
        artifact = root / "final-ui.json"
        artifact.write_text(json.dumps(payload), encoding="utf-8")
        return artifact, payload

    def test_current_workspace_reports_exact_remaining_blockers(self):
        report = evaluate_submission_gate(ROOT)
        checks = {item["check_id"]: item for item in report["checks"]}

        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(checks["source_database"]["status"], "PASS")
        self.assertEqual(checks["formal_app_database"]["status"], "PASS")
        self.assertEqual(checks["display_app_database"]["status"], "PASS")
        self.assertEqual(checks["candidate_vote_labels"]["status"], "PASS")
        self.assertIn('"labeled_meeting_count": 166', checks["candidate_vote_labels"]["details"])
        self.assertIn('"vote_row_count": 1736', checks["candidate_vote_labels"]["details"])
        self.assertIn('"dissent_row_count": 103', checks["candidate_vote_labels"]["details"])
        self.assertIn('"roster_mismatch_meeting_count": 0', checks["candidate_vote_labels"]["details"])
        self.assertEqual(checks["reaction_feature_contract"]["status"], "PASS")
        self.assertEqual(checks["decision_trace_corpus"]["status"], "PASS")
        self.assertIn(
            "decision_trace_50_v5_atomic_monitor_segmentation_v3",
            checks["decision_trace_corpus"]["details"],
        )
        self.assertEqual(checks["human_review_sample"]["status"], "PASS")
        self.assertIn(
            "028c259ed6ad2383e3ce67d38ad8672e3916fda357c834e249c6cbd15eb555ea",
            checks["human_review_sample"]["details"],
        )
        self.assertEqual(checks["human_review_results"]["status"], "PASS")
        self.assertIn('"reviewed_case_count": 12', checks["human_review_results"]["details"])
        self.assertEqual(checks["subscription_variants"]["status"], "PASS")
        self.assertIn("anonymous_persona_reaction=45/45", checks["subscription_variants"]["details"])
        self.assertEqual(checks["variant_matrix"]["status"], "PASS")
        self.assertEqual(checks["final_ui_rehearsal"]["status"], "PASS")
        self.assertEqual(checks["v33_manifest"]["status"], "PASS")
        self.assertEqual(checks["submission_signoff"]["status"], "BLOCKED")
        self.assertEqual(report["blockers"], ["submission_signoff"])

    def test_current_workspace_is_technically_ready_without_submission_signoff(self):
        report = evaluate_technical_readiness(ROOT)

        self.assertEqual(report["schema_version"], "hackathon_r5_technical_gate_v1")
        self.assertEqual(report["status"], "READY")
        self.assertEqual(report["check_count"], 12)
        self.assertEqual(report["pass_count"], 12)
        self.assertEqual(report["blockers"], [])
        self.assertNotIn(
            "submission_signoff",
            {item["check_id"] for item in report["checks"]},
        )

    def test_valid_submission_signoff_requires_video_rehearsals_and_second_person(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "demo.mp4"
            video.write_bytes(b"video")
            archive = root / "submission.zip"
            archive.write_bytes(b"archive")
            payload = {
                "schema_version": "hackathon_r5_submission_signoff_v1",
                "presenter": "Primary Human Presenter",
                "video": {
                    "path": "demo.mp4",
                    "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
                    "watched_end_to_end_with_audio": True,
                },
                "rehearsals": [
                    {
                        "run": index,
                        "duration_seconds": 235,
                        "occurred_at": f"2026-08-29T1{index}:00:00+08:00",
                    }
                    for index in (1, 2, 3)
                ],
                "submission": {
                    "archive_path": "submission.zip",
                    "confirmation_id": "hackathon-confirmation-001",
                },
                "second_person_review": {
                    "reviewer": "Second Human Reviewer",
                    "signed_at": "2026-08-29T15:00:00+08:00",
                    "synthetic_labels_visible": True,
                    "no_secrets_or_private_data_visible": True,
                    "download_access_tested": True,
                    "contact_information_checked": True,
                },
            }
            signoff = root / "signoff.json"
            signoff.write_text(json.dumps(payload), encoding="utf-8")

            audit = validate_submission_signoff(signoff, root=root)

        self.assertEqual(audit["status"], "PASS")
        self.assertEqual(audit["rehearsal_count"], 3)
        self.assertEqual(audit["maximum_rehearsal_seconds"], 235)

    def test_final_ui_rehearsal_verifies_three_mode_equivalence_and_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, _ = self._final_ui_fixture(root)
            audit = validate_final_ui_rehearsal(artifact, root=root)

            self.assertEqual(audit["status"], "PASS")
            self.assertEqual(audit["mode_count"], 3)
            self.assertEqual(audit["view_count"], 3)

            payload = json.loads(artifact.read_text(encoding="utf-8"))
            payload["network_binding"]["address"] = "0.0.0.0"
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "network-binding contract"):
                validate_final_ui_rehearsal(artifact, root=root)
            payload["network_binding"]["address"] = "127.0.0.1"
            artifact.write_text(json.dumps(payload), encoding="utf-8")

            (root / "stop_and_restart/decision_replay.png").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "screenshot equivalence"):
                validate_final_ui_rehearsal(artifact, root=root)

    def test_signoff_fails_on_overlong_rehearsal_or_self_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "demo.mp4"
            video.write_bytes(b"video")
            archive = root / "submission.zip"
            archive.write_bytes(b"archive")
            payload = {
                "schema_version": "hackathon_r5_submission_signoff_v1",
                "presenter": "Same Person",
                "video": {
                    "path": "demo.mp4",
                    "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
                    "watched_end_to_end_with_audio": True,
                },
                "rehearsals": [
                    {"run": 1, "duration_seconds": 241, "occurred_at": "2026-08-29T11:00:00+08:00"},
                    {"run": 2, "duration_seconds": 239, "occurred_at": "2026-08-29T12:00:00+08:00"},
                    {"run": 3, "duration_seconds": 238, "occurred_at": "2026-08-29T13:00:00+08:00"},
                ],
                "submission": {
                    "archive_path": "submission.zip",
                    "confirmation_id": "id",
                },
                "second_person_review": {
                    "reviewer": "Same Person",
                    "signed_at": "2026-08-29T15:00:00+08:00",
                    "synthetic_labels_visible": True,
                    "no_secrets_or_private_data_visible": True,
                    "download_access_tested": True,
                    "contact_information_checked": True,
                },
            }
            signoff = root / "signoff.json"
            signoff.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "240 seconds"):
                validate_submission_signoff(signoff, root=root)


if __name__ == "__main__":
    unittest.main()
