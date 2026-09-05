import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from decision_memory.codex_subscription import (
    CodexSubscriptionError,
    CodexSubscriptionExecutor,
    cleaned_subscription_environment,
    run_subscription_sample,
)
from decision_memory.model_preflight import load_model_spec


ROOT = Path(__file__).resolve().parents[2]


def envelope(stage, *, profiles=None, openings=None, options=None, proposal=None, votes=None):
    return {
        "stage": stage,
        "profiles": profiles or [],
        "openings": openings or [],
        "options": options or [],
        "final_proposal": proposal,
        "votes": votes or [],
    }


def valid_outputs():
    return [
        envelope(
            "profiles",
            profiles=[
                {
                    "participant_id": "chair",
                    "display_name": "Chair",
                    "is_chair": True,
                    "evidence_ids": ["doc-1"],
                },
                {
                    "participant_id": "member",
                    "display_name": "Member",
                    "is_chair": False,
                    "evidence_ids": ["doc-1"],
                },
            ],
        ),
        envelope(
            "openings",
            openings=[
                {
                    "participant_id": "chair",
                    "synthetic_text": "I support a measured hike.",
                    "evidence_ids": ["doc-1"],
                },
                {
                    "participant_id": "member",
                    "synthetic_text": "I prefer to hold.",
                    "evidence_ids": ["doc-1"],
                },
            ],
        ),
        envelope(
            "options",
            options=[
                {
                    "option_id": "cut",
                    "action_class": "CUT",
                    "rationale": "Downside risk.",
                    "evidence_ids": ["doc-1"],
                },
                {
                    "option_id": "hold",
                    "action_class": "HOLD",
                    "rationale": "Wait for data.",
                    "evidence_ids": ["doc-1"],
                },
                {
                    "option_id": "hike",
                    "action_class": "HIKE",
                    "rationale": "Inflation risk.",
                    "evidence_ids": ["doc-1"],
                },
            ],
        ),
        envelope(
            "chair",
            proposal={
                "proposer_participant_id": "chair",
                "action_class": "HIKE",
                "rationale": "Inflation risk dominates.",
            },
        ),
        envelope(
            "votes",
            votes=[
                {
                    "participant_id": "chair",
                    "choice": "FOR",
                    "rationale": "Chair proposal.",
                },
                {
                    "participant_id": "member",
                    "choice": "AGAINST",
                    "rationale": "Prefer hold.",
                },
            ],
        ),
    ]


class FakeStageExecutor:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def run_stage(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "output": self.outputs.pop(0),
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 0,
                "output_tokens": 20,
                "reasoning_output_tokens": 5,
            },
            "latency_seconds": 0.1,
            "thread_id": f"thread-{len(self.calls)}",
        }


class CodexSubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {
            "schema_version": "llm_case_bundle_v1",
            "meeting_id": "FOMC-2022-03-15",
            "bundle_hash": "a" * 64,
            "participants": [
                {"participant_id": "chair", "display_name": "Chair", "is_chair": True},
                {"participant_id": "member", "display_name": "Member", "is_chair": False},
            ],
            "documents": [{"document_id": "doc-1", "text": "Inflation is elevated."}],
            "economic_snapshot": [{"series_id": "CPIAUCSL", "value_num": 280.0}],
        }

    def test_environment_removes_api_credentials(self):
        cleaned = cleaned_subscription_environment(
            {
                "OPENAI_API_KEY": "paid-key",
                "CODEX_API_KEY": "paid-codex-key",
                "OPENAI_BASE_URL": "https://example.invalid",
                "KEEP_ME": "yes",
            }
        )

        self.assertEqual(cleaned["KEEP_ME"], "yes")
        self.assertNotIn("OPENAI_API_KEY", cleaned)
        self.assertNotIn("CODEX_API_KEY", cleaned)
        self.assertNotIn("OPENAI_BASE_URL", cleaned)

    def test_auth_preflight_rejects_api_key_login(self):
        def runner(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="Logged in using API key\n", stderr=""
            )

        executor = CodexSubscriptionExecutor(command_runner=runner)

        with self.assertRaisesRegex(CodexSubscriptionError, "ChatGPT subscription"):
            executor.verify_authentication()

    def test_auth_preflight_accepts_windows_status_on_stderr(self):
        def runner(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=0,
                stdout="",
                stderr="warning\nLogged in using ChatGPT\n",
            )

        executor = CodexSubscriptionExecutor(command_runner=runner)

        self.assertEqual(
            executor.verify_authentication()["status"],
            "CHATGPT_SUBSCRIPTION_AUTHENTICATED",
        )

    def test_codex_exec_uses_schema_and_clean_environment(self):
        calls = []
        output = envelope("profiles", profiles=[{
            "participant_id": "chair",
            "display_name": "Chair",
            "is_chair": True,
            "evidence_ids": ["doc-1"],
        }])

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            if command[1:3] == ["login", "status"]:
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout="Logged in using ChatGPT\n",
                    stderr="",
                )
            output_path = Path(command[command.index("-o") + 1])
            output_path.write_text(json.dumps(output), encoding="utf-8")
            event = {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 50,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 5,
                },
            }
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=json.dumps(event) + "\n",
                stderr="",
            )

        with tempfile.TemporaryDirectory() as directory:
            executor = CodexSubscriptionExecutor(
                command_runner=runner,
                temporary_root=Path(directory),
                base_environment={"OPENAI_API_KEY": "paid-key", "KEEP_ME": "yes"},
            )
            result = executor.run_stage(
                model_id="gpt-5.6-terra",
                reasoning_effort="medium",
                prompt="Return the profile.",
                schema={"type": "object"},
            )

        command, kwargs = calls[1]
        self.assertIn("--output-schema", command)
        self.assertIn("--ephemeral", command)
        self.assertEqual(result["output"], output)
        self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
        self.assertEqual(kwargs["env"]["KEEP_ME"], "yes")

    def test_codex_exec_failure_reports_stdout_and_stderr_tails(self):
        def runner(command, **kwargs):
            if command[1:3] == ["login", "status"]:
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout="Logged in using ChatGPT\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=command,
                returncode=1,
                stdout='{"type":"error","message":"actual schema failure"}\n',
                stderr="warning from plugin loader\n",
            )

        with tempfile.TemporaryDirectory() as directory:
            executor = CodexSubscriptionExecutor(
                command_runner=runner,
                temporary_root=Path(directory),
            )
            with self.assertRaisesRegex(
                CodexSubscriptionError, "actual schema failure"
            ) as raised:
                executor.run_stage(
                    model_id="gpt-5.6-terra",
                    reasoning_effort="high",
                    prompt="Return JSON.",
                    schema={"type": "object"},
                )

        self.assertIn("warning from plugin loader", str(raised.exception))

    def test_codex_exec_rejects_tool_use_in_data_processing_stage(self):
        output = envelope(
            "profiles",
            profiles=[
                {
                    "participant_id": "chair",
                    "display_name": "Chair",
                    "is_chair": True,
                    "evidence_ids": ["doc-1"],
                }
            ],
        )

        def runner(command, **kwargs):
            if command[1:3] == ["login", "status"]:
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout="Logged in using ChatGPT\n",
                    stderr="",
                )
            output_path = Path(command[command.index("-o") + 1])
            output_path.write_text(json.dumps(output), encoding="utf-8")
            events = [
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "Get-ChildItem",
                    },
                },
                {"type": "turn.completed", "usage": {}},
            ]
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="\n".join(json.dumps(item) for item in events) + "\n",
                stderr="",
            )

        with tempfile.TemporaryDirectory() as directory:
            executor = CodexSubscriptionExecutor(
                command_runner=runner,
                temporary_root=Path(directory),
            )
            with self.assertRaisesRegex(CodexSubscriptionError, "tool use"):
                executor.run_stage(
                    model_id="gpt-5.6-terra",
                    reasoning_effort="medium",
                    prompt="Use only the supplied data.",
                    schema={"type": "object"},
                )

    def test_five_stage_subscription_result_is_not_api_billed(self):
        executor = FakeStageExecutor(valid_outputs())

        report = run_subscription_sample(
            executor,
            self.bundle,
            load_model_spec(),
        )

        self.assertEqual(report["status"], "SUBSCRIPTION_SAMPLE_COMPLETED")
        self.assertEqual(report["billing_route"], "chatgpt_subscription")
        self.assertEqual(report["platform_api_cost_usd"], 0.0)
        self.assertEqual(report["semantic_validation"]["against_count"], 1)
        self.assertEqual(
            [call["reasoning_effort"] for call in executor.calls],
            ["medium", "high", "high", "high", "medium"],
        )
        self.assertIn("reaction_profile_cards", executor.calls[0]["prompt"])
        self.assertIn("persona_evidence", executor.calls[0]["prompt"])


if __name__ == "__main__":
    unittest.main()
