import unittest
import json
from copy import deepcopy
from pathlib import Path
import tempfile

from decision_memory.decision_trace import _normalized_text
from decision_memory.decision_trace_subscription import (
    ASSUMPTION_MONITOR_CONTRACT_VERSION,
    DecisionTraceExtractionError,
    TRACE_EXTRACTOR_VERSION,
    _runtime_trace_schema,
    build_trace_bundle_preflight,
    build_trace_bundle,
    load_trace_meeting_ids,
    run_trace_subscription_batch,
    run_trace_extraction,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DATABASE = ROOT / "fred_fomc_real.sqlite"
APP_DATABASE = ROOT / "fomc_simulation.sqlite"


class FakeTraceExecutor:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def run_stage(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "output": self.outputs.pop(0),
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 25,
                "output_tokens": 10,
                "reasoning_output_tokens": 5,
            },
            "latency_seconds": 1.25,
            "thread_id": f"thread-{len(self.calls)}",
        }


def valid_trace_payload(bundle):
    statement = next(
        item for item in bundle["documents"] if item["document_type"] == "statement"
    )
    paragraph = statement["paragraphs"][0]
    transcript = next(
        item for item in bundle["documents"] if item["document_type"] == "transcript"
    )
    segment = next(item for item in transcript["segments"] if item["participant_id"])

    def paragraph_evidence():
        return {
            "document_id": statement["document_id"],
            "locator": f"paragraph {paragraph['ordinal']}",
            "excerpt": paragraph["text"][:1000],
        }

    outcome = bundle["authoritative_outcome"]
    return {
        "schema_version": "decision_trace_v1",
        "decision_id": bundle["decision_id"],
        "meeting_id": bundle["meeting_id"],
        "context": {
            "summary": "Machine-extracted historical context.",
            "evidence_refs": [paragraph_evidence()],
        },
        "options": [
            {
                "option_id": "option-a",
                "description": "One option discussed by the Committee.",
                "evidence_refs": [paragraph_evidence()],
            },
            {
                "option_id": "option-b",
                "description": "A second option discussed by the Committee.",
                "evidence_refs": [paragraph_evidence()],
            },
        ],
        "debate": [
            {
                "speaker_scope": "participant",
                "participant_id": segment["participant_id"],
                "position": "A participant position.",
                "reasoning": "A participant rationale.",
                "evidence_refs": [
                    {
                        "document_id": transcript["document_id"],
                        "locator": f"transcript segment {segment['ordinal']}",
                        "excerpt": segment["text"][:1000],
                    }
                ],
            }
        ],
        "decision": {
            "action_class": outcome["action_class"],
            "target_rate": outcome["target_rate"],
            "target_lower": outcome["target_lower"],
            "target_upper": outcome["target_upper"],
            "rationale": "The authoritative outcome was selected.",
            "evidence_refs": [paragraph_evidence()],
        },
        "vote": {
            "rounds": [
                {
                    **round_data,
                    "evidence_refs": [paragraph_evidence()],
                }
                for round_data in bundle["authoritative_vote_rounds"]
            ]
        },
        "assumptions": [
            {
                "assumption_id": f"{bundle['meeting_id'].lower()}-assumption-1",
                "claim": "A monitorable macroeconomic assumption.",
                "monitor_series_id": bundle["monitor_series"][0]["series_id"],
                "monitor_operator": "GT",
                "threshold_value": 1.0,
                "direction_map_version": "machine_extracted_direction_v1",
                "monitor_rule_version": "machine_extracted_threshold_v1",
                "evidence_refs": [paragraph_evidence()],
            }
        ],
    }


class DecisionTraceSubscriptionTests(unittest.TestCase):
    def test_fixed_transcript_corpus_contains_50_unique_meetings(self):
        meeting_ids = load_trace_meeting_ids(APP_DATABASE)

        self.assertEqual(len(meeting_ids), 50)
        self.assertEqual(len(set(meeting_ids)), 50)
        self.assertEqual(meeting_ids[0], "FOMC-2006-01-31")
        self.assertEqual(meeting_ids[-1], "FOMC-2020-12-15")

    def test_trace_bundle_contains_hash_verified_documents_and_labels(self):
        bundle = build_trace_bundle(
            SOURCE_DATABASE,
            APP_DATABASE,
            meeting_id="FOMC-2006-01-31",
        )

        self.assertEqual(bundle["schema_version"], "decision_trace_bundle_v1")
        self.assertEqual(bundle["meeting_id"], "FOMC-2006-01-31")
        self.assertEqual(
            {item["document_type"] for item in bundle["documents"]},
            {"statement", "minutes", "transcript"},
        )
        transcript = next(
            item for item in bundle["documents"] if item["document_type"] == "transcript"
        )
        self.assertGreater(len(transcript["segments"]), 0)
        self.assertTrue(all(item["text"] for item in transcript["segments"]))
        self.assertEqual(len(bundle["monitor_series"]), 22)
        self.assertTrue(bundle["participants"])
        self.assertTrue(bundle["authoritative_vote_rounds"])
        self.assertEqual(len(bundle["bundle_hash"]), 64)
        self.assertFalse(bundle["sparse_minutes_exception"])

    def test_2020_emergency_meeting_is_only_sparse_minutes_exception(self):
        bundle = build_trace_bundle(
            SOURCE_DATABASE,
            APP_DATABASE,
            meeting_id="FOMC-2020-03-02",
        )

        self.assertEqual(
            {item["document_type"] for item in bundle["documents"]},
            {"statement", "transcript"},
        )
        self.assertTrue(bundle["sparse_minutes_exception"])

    def test_runtime_schema_freezes_evidence_roster_outcome_vote_and_series(self):
        bundle = build_trace_bundle(
            SOURCE_DATABASE,
            APP_DATABASE,
            meeting_id="FOMC-2006-01-31",
        )
        schema = _runtime_trace_schema(bundle)

        evidence_enum = schema["$defs"]["evidence_ref"]["properties"][
            "document_id"
        ]["enum"]
        participant_enum = schema["properties"]["debate"]["items"][
            "properties"
        ]["participant_id"]["enum"]
        series_enum = schema["properties"]["assumptions"]["items"][
            "properties"
        ]["monitor_series_id"]["enum"]
        decision_properties = schema["properties"]["decision"]["properties"]

        self.assertEqual(
            evidence_enum,
            [item["document_id"] for item in bundle["documents"]],
        )
        self.assertEqual(
            set(participant_enum),
            {None, *(item["participant_id"] for item in bundle["participants"])},
        )
        self.assertEqual(
            series_enum,
            [item["series_id"] for item in bundle["monitor_series"]],
        )
        self.assertEqual(
            decision_properties["action_class"]["const"],
            bundle["authoritative_outcome"]["action_class"],
        )
        self.assertEqual(schema["properties"]["schema_version"]["type"], "string")
        self.assertEqual(schema["properties"]["decision_id"]["type"], "string")
        self.assertEqual(decision_properties["action_class"]["type"], "string")
        self.assertIn(
            decision_properties["target_rate"]["type"],
            ("number", "null"),
        )
        rounds_schema = schema["properties"]["vote"]["properties"]["rounds"]
        self.assertIsInstance(rounds_schema["items"], dict)
        self.assertNotIn("prefixItems", rounds_schema)
        self.assertEqual(
            rounds_schema["items"]["properties"]["vote_round"]["enum"],
            [item["vote_round"] for item in bundle["authoritative_vote_rounds"]],
        )
        self.assertEqual(
            rounds_schema["minItems"], len(bundle["authoritative_vote_rounds"])
        )
        self.assertEqual(
            rounds_schema["maxItems"], len(bundle["authoritative_vote_rounds"])
        )

    def test_subscription_extraction_validates_and_records_usage(self):
        bundle = build_trace_bundle(
            SOURCE_DATABASE,
            APP_DATABASE,
            meeting_id="FOMC-2006-01-31",
        )
        payload = valid_trace_payload(bundle)
        executor = FakeTraceExecutor([payload])

        report = run_trace_extraction(
            executor,
            bundle,
            {"model_id": "gpt-5.6-terra"},
            app_database=APP_DATABASE,
        )

        self.assertEqual(report["status"], "SUBSCRIPTION_TRACE_COMPLETED")
        self.assertEqual(report["billing_route"], "chatgpt_subscription")
        self.assertEqual(report["platform_api_cost_usd"], 0.0)
        self.assertEqual(report["usage_totals"]["input_tokens"], 100)
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(executor.calls[0]["reasoning_effort"], "high")
        self.assertEqual(
            report["extractor_version"],
            "codex-subscription-decision-trace-v5-atomic-monitor",
        )
        self.assertEqual(
            report["semantic_validation"]["assumption_monitor_contract_version"],
            "atomic_one_clause_monitor_v1",
        )
        self.assertEqual(
            ASSUMPTION_MONITOR_CONTRACT_VERSION,
            "atomic_one_clause_monitor_v1",
        )
        self.assertEqual(
            TRACE_EXTRACTOR_VERSION,
            "codex-subscription-decision-trace-v5-atomic-monitor",
        )

    def test_semantic_failure_repairs_once_with_specific_violation(self):
        bundle = build_trace_bundle(
            SOURCE_DATABASE,
            APP_DATABASE,
            meeting_id="FOMC-2006-01-31",
        )
        valid = valid_trace_payload(bundle)
        invalid = deepcopy(valid)
        invalid["context"]["evidence_refs"][0]["locator"] = "paragraph 9999"
        executor = FakeTraceExecutor([invalid, valid])

        report = run_trace_extraction(
            executor,
            bundle,
            {"model_id": "gpt-5.6-terra"},
            app_database=APP_DATABASE,
        )

        self.assertEqual(len(executor.calls), 2)
        self.assertIn("SEMANTIC_VIOLATIONS", executor.calls[1]["prompt"])
        self.assertIn("ALLOWED_EVIDENCE_IDS", executor.calls[1]["prompt"])
        self.assertIn("EXACT_LOCATOR_SOURCE_TEXTS", executor.calls[1]["prompt"])
        self.assertIn(
            "ALLOWED_TRANSCRIPT_LOCATORS_BY_PARTICIPANT",
            executor.calls[1]["prompt"],
        )
        self.assertIn(
            invalid["context"]["evidence_refs"][0]["locator"],
            executor.calls[1]["prompt"],
        )
        self.assertEqual(report["usage"][1]["attempt"], 2)

    def test_assumption_semantic_failure_repairs_with_monitor_contract(self):
        bundle = build_trace_bundle(
            SOURCE_DATABASE,
            APP_DATABASE,
            meeting_id="FOMC-2006-01-31",
        )
        valid = valid_trace_payload(bundle)
        valid_assumption = valid["assumptions"][0]
        valid_assumption.update(
            {
                "claim": "Twelve-month inflation remains above 2 percent.",
                "monitor_series_id": "CPIAUCSL",
                "monitor_operator": "GT",
                "threshold_value": 2.0,
                "monitor_rule_version": "yoy_percent_change_v1",
            }
        )
        invalid = deepcopy(valid)
        invalid["assumptions"][0]["monitor_rule_version"] = "v1"
        executor = FakeTraceExecutor([invalid, valid])

        report = run_trace_extraction(
            executor,
            bundle,
            {"model_id": "gpt-5.6-terra"},
            app_database=APP_DATABASE,
        )

        self.assertEqual(len(executor.calls), 2)
        self.assertIn(
            "index_percent_threshold_requires_yoy_transform",
            executor.calls[1]["prompt"],
        )
        self.assertIn("ASSUMPTION_REPAIR_RULES", executor.calls[1]["prompt"])
        self.assertIn("exactly one one-sided statement", executor.calls[1]["prompt"])
        self.assertIn("yoy_percent_change_v1", executor.calls[0]["prompt"])
        self.assertEqual(
            report["trace"]["assumptions"][0]["monitor_rule_version"],
            "yoy_percent_change_v1",
        )

    def test_subscription_materializes_exact_excerpt_from_registered_locator(self):
        bundle = build_trace_bundle(
            SOURCE_DATABASE,
            APP_DATABASE,
            meeting_id="FOMC-2006-01-31",
        )
        candidate = valid_trace_payload(bundle)
        candidate["context"]["evidence_refs"][0]["excerpt"] = (
            "A provisional model excerpt that will not be trusted."
        )
        executor = FakeTraceExecutor([candidate])

        report = run_trace_extraction(
            executor,
            bundle,
            {"model_id": "gpt-5.6-terra"},
            app_database=APP_DATABASE,
        )

        expected = bundle["documents"][0]["paragraphs"][0]["text"]
        self.assertEqual(
            report["trace"]["context"]["evidence_refs"][0]["excerpt"],
            expected,
        )
        self.assertEqual(len(executor.calls), 1)

    def test_long_ocr_locator_materializes_a_bounded_exact_anchor_window(self):
        bundle = build_trace_bundle(
            SOURCE_DATABASE,
            APP_DATABASE,
            meeting_id="FOMC-2006-01-31",
        )
        candidate = valid_trace_payload(bundle)
        minutes = next(
            item for item in bundle["documents"] if item["document_type"] == "minutes"
        )
        paragraph = max(minutes["paragraphs"], key=lambda item: len(item["text"]))
        normalized_source = _normalized_text(paragraph["text"])
        self.assertGreater(len(normalized_source), 1000)
        hint = normalized_source[300:500]
        hint = hint[:80] + " X " + hint[81:]
        candidate["context"]["evidence_refs"][0] = {
            "document_id": minutes["document_id"],
            "locator": f"paragraph {paragraph['ordinal']}",
            "excerpt": hint,
        }

        report = run_trace_extraction(
            FakeTraceExecutor([candidate]),
            bundle,
            {"model_id": "gpt-5.6-terra"},
            app_database=APP_DATABASE,
        )

        excerpt = report["trace"]["context"]["evidence_refs"][0]["excerpt"]
        self.assertLessEqual(len(excerpt), 1000)
        self.assertIn(excerpt, normalized_source)

    def test_unsupported_participant_attribution_is_demoted_without_repair(self):
        bundle = build_trace_bundle(
            SOURCE_DATABASE,
            APP_DATABASE,
            meeting_id="FOMC-2006-01-31",
        )
        candidate = valid_trace_payload(bundle)
        debate = candidate["debate"][0]
        actual_segment_participant = debate["participant_id"]
        debate["participant_id"] = next(
            item["participant_id"]
            for item in bundle["participants"]
            if item["participant_id"] != actual_segment_participant
        )
        executor = FakeTraceExecutor([candidate])

        report = run_trace_extraction(
            executor,
            bundle,
            {"model_id": "gpt-5.6-terra"},
            app_database=APP_DATABASE,
        )

        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(report["trace"]["debate"][0]["speaker_scope"], "committee")
        self.assertIsNone(report["trace"]["debate"][0]["participant_id"])
        self.assertEqual(
            report["attribution_sanitization"]["demoted_item_count"], 1
        )

    def test_participant_attribution_without_transcript_is_demoted_without_repair(self):
        bundle = build_trace_bundle(
            SOURCE_DATABASE,
            APP_DATABASE,
            meeting_id="FOMC-2006-01-31",
        )
        candidate = valid_trace_payload(bundle)
        statement = next(
            item for item in bundle["documents"] if item["document_type"] == "statement"
        )
        paragraph = statement["paragraphs"][0]
        candidate["debate"][0]["evidence_refs"] = [
            {
                "document_id": statement["document_id"],
                "locator": f"paragraph {paragraph['ordinal']}",
                "excerpt": paragraph["text"][:1000],
            }
        ]
        executor = FakeTraceExecutor([candidate])

        report = run_trace_extraction(
            executor,
            bundle,
            {"model_id": "gpt-5.6-terra"},
            app_database=APP_DATABASE,
        )

        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(report["trace"]["debate"][0]["speaker_scope"], "committee")
        self.assertIsNone(report["trace"]["debate"][0]["participant_id"])
        self.assertEqual(
            report["attribution_sanitization"]["demoted_item_count"], 1
        )

    def test_preflight_builds_all_50_without_model_calls(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            report = build_trace_bundle_preflight(
                source_database=SOURCE_DATABASE,
                app_database=APP_DATABASE,
                output_directory=Path(directory),
            )

            self.assertEqual(report["status"], "PREFLIGHT_COMPLETED_NO_MODEL_CALL")
            self.assertEqual(report["case_count"], 50)
            self.assertEqual(report["sparse_minutes_exception_count"], 1)
            self.assertEqual(report["platform_api_calls"], 0)
            self.assertEqual(
                report["extractor_version"],
                "codex-subscription-decision-trace-v5-atomic-monitor",
            )
            self.assertEqual(
                report["assumption_monitor_contract_version"],
                "atomic_one_clause_monitor_v1",
            )
            self.assertTrue((Path(directory) / "bundle_preflight.json").is_file())

    def test_batch_resumes_from_hash_verified_case_artifacts(self):
        meeting_ids = load_trace_meeting_ids(APP_DATABASE)
        first_bundle = build_trace_bundle(
            SOURCE_DATABASE, APP_DATABASE, meeting_id=meeting_ids[0]
        )
        second_bundle = build_trace_bundle(
            SOURCE_DATABASE, APP_DATABASE, meeting_id=meeting_ids[1]
        )
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            output_directory = Path(directory)
            first = run_trace_subscription_batch(
                FakeTraceExecutor([valid_trace_payload(first_bundle)]),
                source_database=SOURCE_DATABASE,
                app_database=APP_DATABASE,
                spec={"model_id": "gpt-5.6-terra"},
                output_directory=output_directory,
                max_new_cases=1,
            )
            second = run_trace_subscription_batch(
                FakeTraceExecutor([valid_trace_payload(second_bundle)]),
                source_database=SOURCE_DATABASE,
                app_database=APP_DATABASE,
                spec={"model_id": "gpt-5.6-terra"},
                output_directory=output_directory,
                max_new_cases=1,
            )

            self.assertEqual(first["completed_case_count"], 1)
            self.assertEqual(second["completed_case_count"], 2)
            self.assertTrue(second["cases"][0]["reused"])
            self.assertFalse(second["cases"][1]["reused"])
            self.assertEqual(second["platform_api_calls"], 0)
            self.assertEqual(second["usage"]["case_count"], 2)
            self.assertEqual(
                second["assumption_monitor_contract_version"],
                "atomic_one_clause_monitor_v1",
            )

    def test_second_semantic_failure_is_preserved_as_batch_artifact(self):
        meeting_id = load_trace_meeting_ids(APP_DATABASE)[0]
        bundle = build_trace_bundle(
            SOURCE_DATABASE, APP_DATABASE, meeting_id=meeting_id
        )
        invalid = valid_trace_payload(bundle)
        invalid["context"]["evidence_refs"][0]["locator"] = "paragraph 9999"
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            output_directory = Path(directory)
            with self.assertRaises(DecisionTraceExtractionError):
                run_trace_subscription_batch(
                    FakeTraceExecutor([invalid, invalid]),
                    source_database=SOURCE_DATABASE,
                    app_database=APP_DATABASE,
                    spec={"model_id": "gpt-5.6-terra"},
                    output_directory=output_directory,
                    max_new_cases=1,
                )

            status = json.loads(
                (output_directory / "batch_status.json").read_text(encoding="utf-8")
            )
            failure_path = ROOT / status["failure_artifact"]
            failure = json.loads(
                failure_path.read_text(encoding="utf-8")
            )

            self.assertEqual(status["status"], "FAILED_CLOSED")
            self.assertEqual(status["completed_case_count"], 0)
            self.assertEqual(failure["meeting_id"], meeting_id)
            self.assertEqual(len(failure["usage"]), 2)
            self.assertEqual(
                failure["last_candidate"]["context"]["evidence_refs"][0][
                    "locator"
                ],
                "paragraph 9999",
            )


if __name__ == "__main__":
    unittest.main()
