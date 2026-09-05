import json
import tempfile
import unittest
from pathlib import Path

from decision_memory.artifact_manifest import DEFAULT_FILES, build_artifact_manifest


class ArtifactManifestTests(unittest.TestCase):
    def test_default_manifest_requires_current_v5_decision_trace_lineage(self):
        joined = "\n".join(DEFAULT_FILES)
        self.assertIn("decision_trace_50_v5_atomic_monitor_segmentation_v3", joined)
        self.assertIn(
            "artifacts/codex_subscription/"
            "decision_trace_50_v5_atomic_monitor_segmentation_v3/"
            "human_review_results_v1.json",
            DEFAULT_FILES,
        )
        self.assertNotIn("decision_trace_50_v4/", joined)
        self.assertIn(".gitattributes", DEFAULT_FILES)
        self.assertIn(".streamlit/config.toml", DEFAULT_FILES)

    def test_manifest_hash_changes_when_an_artifact_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact.txt"
            artifact.write_text("first", encoding="utf-8")
            first = build_artifact_manifest(root, ("artifact.txt",))
            artifact.write_text("second", encoding="utf-8")
            second = build_artifact_manifest(root, ("artifact.txt",))

        self.assertNotEqual(first["files"][0]["sha256"], second["files"][0]["sha256"])
        self.assertNotEqual(first["manifest_hash"], second["manifest_hash"])

    def test_manifest_is_portable_across_workspace_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            first_root = Path(directory) / "first"
            second_root = Path(directory) / "second"
            first_root.mkdir()
            second_root.mkdir()
            (first_root / "artifact.txt").write_text("same", encoding="utf-8")
            (second_root / "artifact.txt").write_text("same", encoding="utf-8")

            first = build_artifact_manifest(first_root, ("artifact.txt",))
            second = build_artifact_manifest(second_root, ("artifact.txt",))

        self.assertEqual(first["root"], ".")
        self.assertEqual(first, second)

    def test_manifest_protects_databases_and_is_fomc_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact.txt").write_text("content", encoding="utf-8")
            manifest = build_artifact_manifest(root, ("artifact.txt",))

        self.assertEqual(manifest["formal_app_database"], "fomc_simulation.sqlite")
        self.assertEqual(
            manifest["build_id"], "hackathon_r5_offline_build_2026-09-02_v33"
        )
        self.assertEqual(
            manifest["display_app_database"],
            "fomc_simulation.decision_trace_50_display.sqlite",
        )
        self.assertEqual(manifest["display_app_database_write_policy"], "read_only")
        self.assertEqual(manifest["formal_app_database_write_policy"], "read_only")
        self.assertEqual(manifest["application_scope"], "fomc_only")
        self.assertIsNone(manifest["mutable_runtime_file"])

    def test_manifest_includes_final_ui_reports_and_screenshots_transitively(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rehearsal_dir = root / "artifacts/rehearsal"
            mode_dir = rehearsal_dir / "normal"
            screenshot_dir = root / "artifacts/screenshots"
            mode_dir.mkdir(parents=True)
            screenshot_dir.mkdir(parents=True)
            mode_screenshot = mode_dir / "decision_replay.png"
            canonical_screenshot = screenshot_dir / "decision_replay.png"
            mode_screenshot.write_bytes(b"mode")
            canonical_screenshot.write_bytes(b"canonical")
            capture_report = mode_dir / "capture_report.json"
            capture_report.write_text(
                json.dumps(
                    [
                        {
                            "page": "Decision Replay",
                            "screenshot": mode_screenshot.name,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            final_ui = rehearsal_dir / "ui_rehearsal_r5_final_v8.json"
            final_ui.write_text(
                json.dumps(
                    {
                        "modes": [
                            {
                                "capture_report": capture_report.relative_to(root).as_posix()
                            }
                        ],
                        "canonical_screenshots": [
                            {
                                "path": canonical_screenshot.relative_to(root).as_posix()
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            manifest = build_artifact_manifest(
                root,
                ("artifacts/rehearsal/ui_rehearsal_r5_final_v8.json",),
            )

        paths = {item["path"] for item in manifest["files"]}
        self.assertEqual(
            paths,
            {
                "artifacts/rehearsal/ui_rehearsal_r5_final_v8.json",
                "artifacts/rehearsal/normal/capture_report.json",
                "artifacts/rehearsal/normal/decision_replay.png",
                "artifacts/screenshots/decision_replay.png",
            },
        )

    def test_manifest_rejects_final_ui_reference_outside_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final_ui = root / "artifacts/rehearsal/ui_rehearsal_r5_final_v8.json"
            final_ui.parent.mkdir(parents=True)
            final_ui.write_text(
                json.dumps(
                    {
                        "modes": [],
                        "canonical_screenshots": [{"path": "../../outside.png"}],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "escapes root"):
                build_artifact_manifest(
                    root,
                    ("artifacts/rehearsal/ui_rehearsal_r5_final_v8.json",),
                )


if __name__ == "__main__":
    unittest.main()
