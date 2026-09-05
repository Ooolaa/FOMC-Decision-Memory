import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.llm_sample import (
    STAGES,
    build_case_bundle,
    dry_run_cost_envelope,
    paid_confirmation_for_model,
    persist_paid_sample,
    run_paid_sample,
)
from decision_memory.app_db import create_schema
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
    profiles = [
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
    ]
    openings = [
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
    ]
    options = [
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
    ]
    return [
        envelope("profiles", profiles=profiles),
        envelope("openings", openings=openings),
        envelope("options", options=options),
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
                {"participant_id": "chair", "choice": "FOR", "rationale": "Chair proposal."},
                {"participant_id": "member", "choice": "AGAINST", "rationale": "Prefer hold."},
            ],
        ),
    ]


class FakeInputTokens:
    def __init__(self, counts):
        self.counts = list(counts)
        self.calls = []

    def count(self, **kwargs):
        self.calls.append(kwargs)
        return {"input_tokens": self.counts.pop(0)}


class FakeResponses:
    def __init__(self, outputs, input_token_counts):
        self.outputs = list(outputs)
        self.calls = []
        self.input_tokens = FakeInputTokens(input_token_counts)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.outputs.pop(0)
        if isinstance(payload, dict) and "status" in payload:
            return payload
        call_number = len(self.calls)
        return {
            "id": f"resp-{call_number}",
            "status": "completed",
            "service_tier": "default",
            "output_text": json.dumps(payload),
            "usage": {
                "input_tokens": 5_000,
                "input_tokens_details": {
                    "cached_tokens": 0 if call_number == 1 else 4_000,
                    "cache_write_tokens": 4_000 if call_number == 1 else 0,
                },
                "output_tokens": 100,
                "output_tokens_details": {"reasoning_tokens": 20},
                "total_tokens": 5_100,
            },
        }


class FakeClient:
    def __init__(self, outputs, input_token_counts=None):
        counts = input_token_counts or [5_000] * (len(outputs) + 1)
        self.responses = FakeResponses(outputs, counts)


class LLMSampleTests(unittest.TestCase):
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

    def test_five_stages_share_one_schema_and_run_sequentially(self):
        client = FakeClient(valid_outputs())

        report = run_paid_sample(
            client,
            self.bundle,
            self.spec,
            max_cost_usd=100.0,
            max_output_tokens=100,
        )

        self.assertEqual(report["stage_order"], list(STAGES))
        self.assertEqual(report["case_stage_affinity"], "SEQUENTIAL_NO_INTERLEAVING")
        self.assertEqual(report["semantic_validation"]["for_count"], 1)
        self.assertEqual(report["semantic_validation"]["against_count"], 1)
        self.assertEqual(len(client.responses.calls), 5)
        formats = [call["text"]["format"] for call in client.responses.calls]
        self.assertTrue(all(item == formats[0] for item in formats))
        for evidence_path in ("profiles", "openings", "options"):
            evidence_items = formats[0]["schema"]["properties"][evidence_path][
                "items"
            ]["properties"]["evidence_ids"]["items"]
            self.assertEqual(evidence_items["enum"], ["doc-1"])
        prefixes = [call["input"][0] for call in client.responses.calls]
        self.assertTrue(all(item == prefixes[0] for item in prefixes))
        self.assertEqual(
            [call["reasoning"]["effort"] for call in client.responses.calls],
            ["medium", "high", "high", "high", "medium"],
        )
        self.assertEqual(
            [call["reasoning"]["effort"] for call in client.responses.input_tokens.calls],
            ["medium", "high", "high", "high", "medium"],
        )
        self.assertEqual(report["cache_report"]["eligible_call_count"], 4)
        vote_prompt = client.responses.calls[4]["input"][1]["content"][0]["text"]
        self.assertIn("roster is known input", vote_prompt)
        self.assertIn("recent vote history", vote_prompt)
        self.assertIn("predict each voter's FOR or AGAINST choice", vote_prompt)

    def test_only_semantic_failure_gets_one_repair(self):
        bad_profiles = envelope(
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
        client = FakeClient([bad_profiles, *valid_outputs()])

        report = run_paid_sample(
            client,
            self.bundle,
            self.spec,
            max_cost_usd=100.0,
            max_output_tokens=100,
        )

        self.assertEqual(len(client.responses.calls), 6)
        self.assertEqual(report["usage"][0]["attempt"], 1)
        self.assertEqual(report["usage"][1]["attempt"], 2)
        repair_prompt = client.responses.calls[1]["input"][1]["content"][0]["text"]
        self.assertIn('ALLOWED_EVIDENCE_IDS=["doc-1"]', repair_prompt)

    def test_schema_layer_failure_is_not_repaired(self):
        client = FakeClient([{"status": "failed", "output_text": ""}])

        with self.assertRaisesRegex(RuntimeError, "Schema-layer response failed"):
            run_paid_sample(
                client,
                self.bundle,
                self.spec,
                max_cost_usd=100.0,
                max_output_tokens=100,
            )

        self.assertEqual(len(client.responses.calls), 1)

    def test_duplicate_document_ids_fail_before_any_request(self):
        bundle = dict(self.bundle)
        bundle["documents"] = [
            {"document_id": "doc-1", "text": "First."},
            {"document_id": "doc-1", "text": "Duplicate."},
        ]
        client = FakeClient(valid_outputs())

        with self.assertRaisesRegex(ValueError, "unique document_id"):
            run_paid_sample(
                client,
                bundle,
                self.spec,
                max_cost_usd=100.0,
                max_output_tokens=100,
            )

        self.assertEqual(len(client.responses.input_tokens.calls), 0)
        self.assertEqual(len(client.responses.calls), 0)

    def test_hard_cap_blocks_before_any_request(self):
        client = FakeClient(valid_outputs())

        with self.assertRaisesRegex(RuntimeError, "Hard cost cap"):
            run_paid_sample(
                client,
                self.bundle,
                self.spec,
                max_cost_usd=0.000001,
                max_output_tokens=100,
            )

        self.assertEqual(len(client.responses.calls), 0)

    def test_exact_token_count_applies_long_context_price_before_request(self):
        client = FakeClient(valid_outputs(), input_token_counts=[300_000])

        with self.assertRaisesRegex(RuntimeError, "Hard cost cap"):
            run_paid_sample(
                client,
                self.bundle,
                self.spec,
                max_cost_usd=1.0,
                max_output_tokens=100,
            )

        self.assertEqual(len(client.responses.input_tokens.calls), 1)
        self.assertEqual(len(client.responses.calls), 0)

    def test_stage_schema_avoids_unsupported_unique_items_keyword(self):
        schema = json.loads(
            (ROOT / "schemas" / "simulation_stage_envelope_v1.json").read_text(
                encoding="utf-8"
            )
        )

        def walk(value):
            if isinstance(value, dict):
                self.assertNotIn("uniqueItems", value)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(schema)

    def test_dry_run_is_explicitly_not_actual_usage(self):
        report = dry_run_cost_envelope(self.bundle, self.spec, max_output_tokens=100)

        self.assertEqual(report["status"], "DRY_RUN_NO_API_CALL")
        self.assertTrue(report["estimate_is_not_actual_usage"])
        self.assertEqual(report["document_count"], 1)

    def test_paid_confirmation_is_model_specific(self):
        self.assertEqual(
            paid_confirmation_for_model("gpt-5.6-terra"),
            "RUN_GPT_5_6_TERRA_PAID_SAMPLE",
        )
        self.assertEqual(
            paid_confirmation_for_model("gpt-5.6-luna"),
            "RUN_GPT_5_6_LUNA_COMPARISON_SAMPLE",
        )
        with self.assertRaisesRegex(ValueError, "approved paid sample model"):
            paid_confirmation_for_model("gpt-5.6-sol")

    def test_real_sample_bundle_keeps_policy_rates_compact_and_cutoff_safe(self):
        bundle = build_case_bundle(
            ROOT / "fred_fomc_real.sqlite",
            ROOT / "fomc_simulation.sqlite",
            meeting_id="FOMC-2022-03-15",
        )

        self.assertEqual(len(bundle["documents"]), 5)
        self.assertEqual(len(bundle["economic_snapshot"]), 6_816)
        self.assertLessEqual(len(bundle["policy_rate_context"]), 9)
        self.assertTrue(
            all(
                row["series_id"] not in {"DFEDTAR", "DFEDTARL", "DFEDTARU"}
                for row in bundle["economic_snapshot"]
            )
        )
        self.assertTrue(
            all(
                row["observation_date"] <= bundle["information_cutoff_date_et"]
                and row["realtime_start"] <= bundle["information_cutoff_date_et"]
                for row in bundle["economic_snapshot"]
            )
        )

    def test_frozen_case_uses_html_meeting_documents_not_transcript_pdfs(self):
        bundle = build_case_bundle(
            ROOT / "fred_fomc_real.sqlite",
            ROOT / "fomc_simulation.sqlite",
            meeting_id="FOMC-2021-01-26",
        )

        self.assertEqual(len(bundle["documents"]), 5)
        self.assertTrue(
            all(
                item["document_type"] in {"statement", "minutes"}
                for item in bundle["documents"]
            )
        )

    def test_completed_sample_persists_append_only_run_and_metrics(self):
        report = run_paid_sample(
            FakeClient(valid_outputs()),
            self.bundle,
            self.spec,
            max_cost_usd=100.0,
            max_output_tokens=100,
        )
        with tempfile.TemporaryDirectory() as directory:
            app_path = Path(directory) / "app.sqlite"
            app = sqlite3.connect(app_path)
            app.execute("PRAGMA foreign_keys = ON")
            create_schema(app)
            app.execute(
                "INSERT INTO document_source VALUES ('doc-1','FOMC-2022-03-15','statement','2022-03-16T23:59:59Z','label_only','{}','hash-1','now')"
            )
            app.execute(
                "INSERT INTO meeting_outcome VALUES ('FOMC-2022-03-15','HIKE',NULL,0.25,0.50,'doc-1','now')"
            )
            for participant_id, name, dissent in (
                ("chair", "Chair", 0),
                ("member", "Member", 1),
            ):
                app.execute(
                    "INSERT INTO participant VALUES (?,?,'policymaker',NULL,NULL)",
                    (participant_id, name),
                )
                app.execute(
                    "INSERT INTO meeting_participant VALUES ('FOMC-2022-03-15',?,'member',1,?)",
                    (participant_id, int(participant_id == "chair")),
                )
                app.execute(
                    "INSERT INTO participant_vote VALUES ('FOMC-2022-03-15',?,1,?,?, 'doc-1')",
                    (participant_id, "AGAINST" if dissent else "FOR", dissent),
                )
            app.commit()
            app.close()

            first = persist_paid_sample(app_path, report)
            second = persist_paid_sample(app_path, report)
            app = sqlite3.connect(app_path)
            counts = (
                app.execute("SELECT COUNT(*) FROM simulation_run").fetchone()[0],
                app.execute("SELECT COUNT(*) FROM evaluation_result").fetchone()[0],
            )
            app.close()

        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(counts, (1, 7))


if __name__ == "__main__":
    unittest.main()
