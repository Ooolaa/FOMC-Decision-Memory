import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.model_preflight import load_model_spec
from decision_memory.votes_comparison import (
    build_votes_only_case,
    dry_run_votes_only,
    run_paid_votes_only,
    votes_only_confirmation_for_model,
)


def votes_envelope(votes):
    return {
        "stage": "votes",
        "profiles": [],
        "openings": [],
        "options": [],
        "final_proposal": None,
        "votes": votes,
    }


class FakeResponses:
    def __init__(self, outputs, input_tokens=300_000):
        self.outputs = list(outputs)
        self.calls = []
        self.count_calls = []
        self.input_tokens = self
        self.input_token_count = input_tokens

    def count(self, **kwargs):
        self.count_calls.append(kwargs)
        return {"input_tokens": self.input_token_count}

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        return {
            "id": "resp-votes-only",
            "status": "completed",
            "service_tier": "default",
            "output_text": json.dumps(output),
            "usage": {
                "input_tokens": self.input_token_count,
                "input_tokens_details": {
                    "cached_tokens": 0,
                    "cache_write_tokens": self.input_token_count - 10,
                },
                "output_tokens": 100,
                "output_tokens_details": {"reasoning_tokens": 20},
                "total_tokens": self.input_token_count + 100,
            },
        }


class FakeClient:
    def __init__(self, outputs, input_tokens=300_000):
        self.responses = FakeResponses(outputs, input_tokens)


class VotesComparisonTests(unittest.TestCase):
    def setUp(self):
        self.spec = load_model_spec()
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
        self.anchor = {
            "status": "PAID_SAMPLE_COMPLETED",
            "model_id": "gpt-5.6-terra",
            "meeting_id": "FOMC-2022-03-15",
            "bundle_hash": "a" * 64,
            "usage": [{"stage": "votes", "preflight_input_tokens": 300_000}],
            "output": {
                "schema_version": "simulation_output_v1",
                "meeting_id": "FOMC-2022-03-15",
                "synthetic": True,
                "profiles": [
                    {"participant_id": "chair", "display_name": "Chair", "is_chair": True},
                    {"participant_id": "member", "display_name": "Member", "is_chair": False},
                ],
                "discussion": [
                    {"participant_id": "chair", "synthetic_text": "Support the proposal."},
                    {"participant_id": "member", "synthetic_text": "Prefer caution."},
                ],
                "final_proposal": {
                    "proposer_participant_id": "chair",
                    "action_class": "HIKE",
                    "rationale": "Inflation remains elevated.",
                },
                "votes": [
                    {"participant_id": "chair", "choice": "FOR"},
                    {"participant_id": "member", "choice": "AGAINST"},
                ],
            },
        }

    def _app_database(self, directory):
        path = Path(directory) / "app.sqlite"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE meeting_outcome (meeting_id TEXT PRIMARY KEY, action_class TEXT);
            CREATE TABLE meeting_participant (
                meeting_id TEXT,
                participant_id TEXT,
                is_voter INTEGER
            );
            CREATE TABLE participant_vote (meeting_id TEXT, participant_id TEXT, dissent INTEGER);
            INSERT INTO meeting_outcome VALUES ('FOMC-2022-03-15', 'HIKE');
            INSERT INTO meeting_participant VALUES ('FOMC-2022-03-15', 'chair', 1);
            INSERT INTO meeting_participant VALUES ('FOMC-2022-03-15', 'member', 1);
            INSERT INTO participant_vote VALUES ('FOMC-2022-03-15', 'chair', 0);
            INSERT INTO participant_vote VALUES ('FOMC-2022-03-15', 'member', 1);
            """
        )
        connection.commit()
        connection.close()
        return path

    def test_runs_only_votes_with_locked_context_and_read_only_evaluation(self):
        votes_case = build_votes_only_case(self.bundle, self.anchor)
        client = FakeClient(
            [
                votes_envelope(
                    [
                        {"participant_id": "chair", "choice": "FOR", "rationale": "Support."},
                        {"participant_id": "member", "choice": "AGAINST", "rationale": "Too tight."},
                    ]
                )
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            report = run_paid_votes_only(
                client,
                self.bundle,
                votes_case,
                self.spec,
                app_database=self._app_database(directory),
                max_cost_usd=10.0,
                max_output_tokens=100,
            )

        self.assertEqual(report["stage_order"], ["votes"])
        self.assertEqual(report["evaluation"]["dissent_f1"], 1.0)
        self.assertEqual(report["output"]["profiles"], self.anchor["output"]["profiles"])
        self.assertEqual(report["output"]["discussion"], self.anchor["output"]["discussion"])
        self.assertEqual(report["output"]["final_proposal"], self.anchor["output"]["final_proposal"])
        self.assertEqual(len(client.responses.calls), 1)
        self.assertEqual(client.responses.calls[0]["reasoning"]["effort"], "medium")
        self.assertIn(votes_case["locked_context_hash"], client.responses.calls[0]["prompt_cache_key"])
        self.assertTrue(report["cache_report"]["not_applicable"])

    def test_semantic_roster_failure_gets_one_repair(self):
        votes_case = build_votes_only_case(self.bundle, self.anchor)
        client = FakeClient(
            [
                votes_envelope(
                    [{"participant_id": "chair", "choice": "FOR", "rationale": "Support."}]
                ),
                votes_envelope(
                    [
                        {"participant_id": "chair", "choice": "FOR", "rationale": "Support."},
                        {"participant_id": "member", "choice": "AGAINST", "rationale": "Too tight."},
                    ]
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            report = run_paid_votes_only(
                client,
                self.bundle,
                votes_case,
                self.spec,
                app_database=self._app_database(directory),
                max_cost_usd=10.0,
                max_output_tokens=100,
            )

        self.assertEqual(len(client.responses.calls), 2)
        self.assertEqual(report["repair_count"], 1)
        repair_prompt = client.responses.calls[1]["input"][1]["content"][0]["text"]
        self.assertIn("REPAIR_THESE_SEMANTIC_VIOLATIONS", repair_prompt)

    def test_hard_cap_blocks_before_paid_request(self):
        votes_case = build_votes_only_case(self.bundle, self.anchor)
        client = FakeClient([])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "Hard cost cap"):
                run_paid_votes_only(
                    client,
                    self.bundle,
                    votes_case,
                    self.spec,
                    app_database=self._app_database(directory),
                    max_cost_usd=0.000001,
                    max_output_tokens=100,
                )
        self.assertEqual(len(client.responses.calls), 0)

    def test_label_roster_mismatch_fails_before_token_count_or_request(self):
        votes_case = build_votes_only_case(self.bundle, self.anchor)
        client = FakeClient([])
        with tempfile.TemporaryDirectory() as directory:
            app_database = self._app_database(directory)
            connection = sqlite3.connect(app_database)
            connection.execute(
                "DELETE FROM participant_vote WHERE participant_id = 'member'"
            )
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(ValueError, "label roster"):
                run_paid_votes_only(
                    client,
                    self.bundle,
                    votes_case,
                    self.spec,
                    app_database=app_database,
                    max_cost_usd=10.0,
                    max_output_tokens=100,
                )
        self.assertEqual(len(client.responses.count_calls), 0)
        self.assertEqual(len(client.responses.calls), 0)

    def test_anchor_and_bundle_mismatch_fails_closed(self):
        anchor = dict(self.anchor)
        anchor["bundle_hash"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "bundle hash"):
            build_votes_only_case(self.bundle, anchor)

    def test_dry_run_and_confirmation_are_explicit(self):
        votes_case = build_votes_only_case(self.bundle, self.anchor)
        report = dry_run_votes_only(self.bundle, votes_case, self.spec, max_output_tokens=100)
        self.assertEqual(report["status"], "VOTES_ONLY_DRY_RUN_NO_API_CALL")
        self.assertTrue(report["estimate_is_not_actual_usage"])
        self.assertEqual(
            votes_only_confirmation_for_model("gpt-5.6-terra"),
            "RUN_GPT_5_6_TERRA_VOTES_ONLY",
        )
        self.assertEqual(
            votes_only_confirmation_for_model("gpt-5.6-luna"),
            "RUN_GPT_5_6_LUNA_VOTES_ONLY",
        )


if __name__ == "__main__":
    unittest.main()
