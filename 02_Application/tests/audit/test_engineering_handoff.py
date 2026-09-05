import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from decision_memory.engineering_handoff import (
    build_engineering_handoff,
    verify_engineering_handoff,
)


class EngineeringHandoffTest(unittest.TestCase):
    def test_manifest_driven_artifacts_are_included_and_hash_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            output = Path(temporary) / "handoff"
            artifact = root / "artifacts" / "result.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("{}\n", encoding="utf-8")
            (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
            manifest = root / "build_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "files": [
                            {
                                "path": "artifacts/result.json",
                                "byte_length": artifact.stat().st_size,
                                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            build_engineering_handoff(
                root,
                output,
                top_level_files=("app.py", "build_manifest.json"),
                include_directories=(),
                required_files=("app.py",),
                artifact_manifest="build_manifest.json",
                project_name="Demo",
                snapshot_date="2026-09-01",
            )

            self.assertTrue((output / "artifacts" / "result.json").is_file())

            artifact.write_text("[]\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest hash mismatch"):
                build_engineering_handoff(
                    root,
                    Path(temporary) / "tampered-handoff",
                    top_level_files=("app.py", "build_manifest.json"),
                    include_directories=(),
                    required_files=("app.py",),
                    artifact_manifest="build_manifest.json",
                    project_name="Demo",
                    snapshot_date="2026-09-01",
                )

    def test_builds_allowlisted_snapshot_and_verifies_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            output = Path(temporary) / "handoff"
            (root / "spec").mkdir(parents=True)
            (root / "artifacts" / "rehearsal").mkdir(parents=True)
            (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "spec" / "contract.json").write_text("{}\n", encoding="utf-8")
            (root / "spec" / "__pycache__").mkdir()
            (root / "spec" / "__pycache__" / "ignored.pyc").write_bytes(b"cache")
            (root / "artifacts" / "result.json").write_text("{}\n", encoding="utf-8")
            (root / "artifacts" / "rehearsal" / "review.sqlite").write_bytes(b"backup")
            (root / "artifacts" / "submission").mkdir()
            (root / "artifacts" / "submission" / "old-submission.zip").write_bytes(
                b"superseded"
            )
            (root / "project.pre_change.sqlite").write_bytes(b"backup")

            result = build_engineering_handoff(
                root,
                output,
                top_level_files=("app.py",),
                include_directories=("spec", "artifacts"),
                required_files=("app.py", "spec/contract.json"),
                project_name="Demo",
                snapshot_date="2026-08-29",
            )

            self.assertTrue((output / "app.py").is_file())
            self.assertTrue((output / "spec" / "contract.json").is_file())
            self.assertTrue((output / "artifacts" / "result.json").is_file())
            self.assertFalse((output / "spec" / "__pycache__").exists())
            self.assertFalse((output / "artifacts" / "rehearsal" / "review.sqlite").exists())
            self.assertFalse((output / "artifacts" / "submission").exists())
            self.assertFalse((output / "project.pre_change.sqlite").exists())

            manifest = json.loads(
                (output / "HANDOFF_MANIFEST.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema_version"], "engineering_handoff_v2")
            self.assertEqual(manifest["snapshot_kind"], "working_tree_engineering_snapshot")
            self.assertEqual(manifest["engineering_handoff_status"], "READY")
            self.assertEqual(manifest["technical_readiness_status"], "READY")
            self.assertEqual(
                manifest["hackathon_submission_status"], "PENDING_REAL_WORLD_SIGNOFF"
            )
            self.assertEqual(manifest["known_submission_blockers"], ["submission_signoff"])
            self.assertEqual(manifest["payload_file_count"], 3)
            self.assertEqual(result["secret_finding_count"], 0)

            verification = verify_engineering_handoff(output)
            self.assertTrue(verification["valid"])
            self.assertEqual(verification["hash_mismatch_count"], 0)
            self.assertEqual(verification["unexpected_file_count"], 0)

            runtime_cache = output / "decision_memory" / "__pycache__" / "generated.pyc"
            runtime_cache.parent.mkdir(parents=True)
            runtime_cache.write_bytes(b"runtime cache")
            verification_with_cache = verify_engineering_handoff(output)
            self.assertTrue(verification_with_cache["valid"])
            self.assertEqual(verification_with_cache["ignored_runtime_file_count"], 1)

    def test_rejects_high_confidence_secret_without_leaving_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            output = Path(temporary) / "handoff"
            root.mkdir()
            (root / "app.py").write_text(
                "token = '" + "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz012345'\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "secret-like content"):
                build_engineering_handoff(
                    root,
                    output,
                    top_level_files=("app.py",),
                    include_directories=(),
                    required_files=("app.py",),
                    project_name="Demo",
                    snapshot_date="2026-08-29",
                )

            self.assertFalse(output.exists())

    def test_refuses_to_overwrite_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            output = Path(temporary) / "handoff"
            root.mkdir()
            output.mkdir()
            (root / "app.py").write_text("print('ok')\n", encoding="utf-8")

            with self.assertRaisesRegex(FileExistsError, "already exists"):
                build_engineering_handoff(
                    root,
                    output,
                    top_level_files=("app.py",),
                    include_directories=(),
                    required_files=("app.py",),
                    project_name="Demo",
                    snapshot_date="2026-08-29",
                )


if __name__ == "__main__":
    unittest.main()
