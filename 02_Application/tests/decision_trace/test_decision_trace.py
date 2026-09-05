import sqlite3
import hashlib
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from decision_memory.app_db import create_schema
from decision_memory.decision_trace import persist_fomc_decision_trace
from decision_memory.documents import ingest_local_document


class DecisionTraceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        create_schema(self.connection)
        self.monitor_series_metadata = {
            "CPIAUCSL": {
                "series_id": "CPIAUCSL",
                "title": "Consumer Price Index for All Urban Consumers: All Items",
                "frequency": "Monthly",
                "units": "Index 1982-1984=100",
                "vintage_mode": "ALFRED_VINTAGE",
            }
        }

        statement_path = self.root / "statement.html"
        statement_path.write_text(
            "<p>The Committee decided to raise the target range.</p>",
            encoding="utf-8",
        )
        minutes_path = self.root / "minutes.html"
        minutes_path.write_text(
            """
            <p>Participants considered maintaining the target range or raising it.</p>
            <p>Inflation remained elevated and labor demand was strong.</p>
            <p>Voting for this action: Alice Example. Voting against: Bob Example.</p>
            """,
            encoding="utf-8",
        )
        self.statement_id = ingest_local_document(
            self.connection,
            statement_path,
            meeting_id="FOMC-2022-03-15",
            document_type="statement",
            publication_at="2022-03-16T23:59:59Z",
            usage_class="label_only",
            source_url="https://www.federalreserve.gov/statement.htm",
        )
        self.minutes_id = ingest_local_document(
            self.connection,
            minutes_path,
            meeting_id="FOMC-2022-03-15",
            document_type="minutes",
            publication_at="2022-04-06T23:59:59Z",
            usage_class="label_only",
            source_url="https://www.federalreserve.gov/minutes.htm",
        )
        transcript_path = self.root / "transcript.pdf"
        transcript_path.write_bytes(b"fixture transcript pdf")
        self.transcript_id = ingest_local_document(
            self.connection,
            transcript_path,
            meeting_id="FOMC-2022-03-15",
            document_type="transcript",
            publication_at="2027-01-01T23:59:59Z",
            usage_class="persona_evidence",
            source_url="https://www.federalreserve.gov/transcript.pdf",
        )
        for participant_id, display_name in (
            ("alice-example", "Alice Example"),
            ("bob-example", "Bob Example"),
        ):
            self.connection.execute(
                """
                INSERT INTO participant (
                    participant_id, display_name, role,
                    effective_start, effective_end
                ) VALUES (?, ?, 'policymaker', '2022-03-15', '2022-03-16')
                """,
                (participant_id, display_name),
            )
            self.connection.execute(
                """
                INSERT INTO meeting_participant (
                    meeting_id, participant_id, role, is_voter, is_chair
                ) VALUES ('FOMC-2022-03-15', ?, 'member', 1, ?)
                """,
                (participant_id, int(participant_id == "alice-example")),
            )
        self.connection.execute(
            """
            INSERT INTO meeting_outcome (
                meeting_id, action_class, target_rate, target_lower,
                target_upper, source_document_id, created_at
            ) VALUES (
                'FOMC-2022-03-15', 'HIKE', NULL, 0.25, 0.50, ?,
                '2026-08-27T00:00:00Z'
            )
            """,
            (self.statement_id,),
        )
        for participant_id, choice, dissent in (
            ("alice-example", "FOR", 0),
            ("bob-example", "AGAINST", 1),
        ):
            self.connection.execute(
                """
                INSERT INTO participant_vote (
                    meeting_id, participant_id, vote_round, voter_choice,
                    dissent, evidence_id
                ) VALUES ('FOMC-2022-03-15', ?, 1, ?, ?, ?)
                """,
                (participant_id, choice, dissent, self.minutes_id),
            )
        transcript_text = (
            "I support a measured increase because inflation is elevated, "
            "while acknowledging uncertainty around the outlook."
        )
        self.connection.execute(
            """
            INSERT INTO transcript_segment (
                segment_id, document_id, meeting_id, ordinal, speaker_label,
                participant_id, text, content_hash, created_at
            ) VALUES (?, ?, 'FOMC-2022-03-15', 17, 'ALICE EXAMPLE',
                      'alice-example', ?, ?, '2027-01-01T00:00:00Z')
            """,
            (
                "segment-fixture-17",
                self.transcript_id,
                transcript_text,
                hashlib.sha256(transcript_text.encode("utf-8")).hexdigest(),
            ),
        )

        def evidence(document_id, excerpt):
            if document_id == self.statement_id:
                ordinal = 1
            elif excerpt.startswith("Participants considered"):
                ordinal = 1
            elif excerpt.startswith("Inflation remained"):
                ordinal = 2
            else:
                ordinal = 3
            return {
                "document_id": document_id,
                "locator": f"paragraph {ordinal}",
                "excerpt": excerpt,
            }

        self.payload = {
            "schema_version": "decision_trace_v1",
            "decision_id": "fomc-FOMC-2022-03-15",
            "meeting_id": "FOMC-2022-03-15",
            "context": {
                "summary": "Inflation was elevated while labor demand remained strong.",
                "evidence_refs": [
                    evidence(
                        self.minutes_id,
                        "Inflation remained elevated and labor demand was strong.",
                    )
                ],
            },
            "options": [
                {
                    "option_id": "hold",
                    "description": "Maintain the target range.",
                    "evidence_refs": [
                        evidence(
                            self.minutes_id,
                            "Participants considered maintaining the target range or raising it.",
                        )
                    ],
                },
                {
                    "option_id": "hike",
                    "description": "Raise the target range.",
                    "evidence_refs": [
                        evidence(
                            self.statement_id,
                            "The Committee decided to raise the target range.",
                        )
                    ],
                },
            ],
            "debate": [
                {
                    "speaker_scope": "committee",
                    "participant_id": None,
                    "position": "Some preferred waiting while others supported tightening.",
                    "reasoning": "Inflation and labor demand informed the trade-off.",
                    "evidence_refs": [
                        evidence(
                            self.minutes_id,
                            "Participants considered maintaining the target range or raising it.",
                        )
                    ],
                }
            ],
            "decision": {
                "action_class": "HIKE",
                "target_rate": None,
                "target_lower": 0.25,
                "target_upper": 0.50,
                "rationale": "The Committee raised the range.",
                "evidence_refs": [
                    evidence(
                        self.statement_id,
                        "The Committee decided to raise the target range.",
                    )
                ],
            },
            "vote": {
                "rounds": [
                    {
                        "vote_round": 1,
                        "for_count": 1,
                        "against_count": 1,
                        "abstain_count": 0,
                        "evidence_refs": [
                            evidence(
                                self.minutes_id,
                                "Voting for this action: Alice Example. Voting against: Bob Example.",
                            )
                        ],
                    }
                ]
            },
            "assumptions": [
                {
                    "assumption_id": "fomc-2022-03-inflation",
                    "claim": "Elevated inflation warrants a higher target range.",
                    "monitor_series_id": "CPIAUCSL",
                    "monitor_operator": "GT",
                    "threshold_value": 2.0,
                    "direction_map_version": "inflation_hawkish_v1",
                    "monitor_rule_version": "cpi_yoy_upper_v1",
                    "evidence_refs": [
                        evidence(
                            self.minutes_id,
                            "Inflation remained elevated and labor demand was strong.",
                        )
                    ],
                }
            ],
        }

    def tearDown(self):
        self.connection.close()
        self.temporary_directory.cleanup()

    def test_valid_trace_persists_idempotently_with_source_boundaries(self):
        first = persist_fomc_decision_trace(
            self.connection,
            self.payload,
            extractor_version="gpt-5.6-terra-trace-v1",
            allowed_monitor_series_ids={"CPIAUCSL"},
            monitor_series_metadata=self.monitor_series_metadata,
        )
        second = persist_fomc_decision_trace(
            self.connection,
            self.payload,
            extractor_version="gpt-5.6-terra-trace-v1",
            allowed_monitor_series_ids={"CPIAUCSL"},
            monitor_series_metadata=self.monitor_series_metadata,
        )
        decision = self.connection.execute(
            "SELECT domain, synthetic, composite, context_json FROM decision_case"
        ).fetchone()

        self.assertEqual(first, second)
        self.assertEqual(first["assumption_count"], 1)
        self.assertEqual(decision[:3], ("fomc", 0, 0))
        self.assertIn("post_meeting_label_only", decision[3])

    def test_semantically_invalid_assumption_is_rejected_before_persist(self):
        payload = deepcopy(self.payload)
        payload["assumptions"][0]["monitor_rule_version"] = "v1"

        with self.assertRaisesRegex(
            ValueError, "index_percent_threshold_requires_yoy_transform"
        ):
            persist_fomc_decision_trace(
                self.connection,
                payload,
                extractor_version="gpt-5.6-terra-trace-v1",
                allowed_monitor_series_ids={"CPIAUCSL"},
                monitor_series_metadata=self.monitor_series_metadata,
            )

        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM decision_case").fetchone()[0],
            0,
        )

    def test_fabricated_excerpt_is_rejected(self):
        payload = deepcopy(self.payload)
        payload["context"]["evidence_refs"][0]["excerpt"] = (
            "This sentence does not exist in the official document."
        )

        with self.assertRaisesRegex(ValueError, "not found"):
            persist_fomc_decision_trace(
                self.connection,
                payload,
                extractor_version="gpt-5.6-terra-trace-v1",
                allowed_monitor_series_ids={"CPIAUCSL"},
                monitor_series_metadata=self.monitor_series_metadata,
            )

    def test_excerpt_must_resolve_inside_the_registered_paragraph(self):
        payload = deepcopy(self.payload)
        payload["context"]["evidence_refs"][0] = {
            "document_id": self.minutes_id,
            "locator": "paragraph 1",
            "excerpt": "Inflation remained elevated and labor demand was strong.",
        }

        with self.assertRaisesRegex(ValueError, "not found"):
            persist_fomc_decision_trace(
                self.connection,
                payload,
                extractor_version="gpt-5.6-terra-trace-v1",
                allowed_monitor_series_ids={"CPIAUCSL"},
                monitor_series_metadata=self.monitor_series_metadata,
            )

    def test_same_meeting_transcript_segment_is_valid_extraction_evidence(self):
        payload = deepcopy(self.payload)
        payload["debate"] = [
            {
                "speaker_scope": "participant",
                "participant_id": "alice-example",
                "position": "Support a measured increase.",
                "reasoning": "Inflation was elevated but uncertainty remained.",
                "evidence_refs": [
                    {
                        "document_id": self.transcript_id,
                        "locator": "transcript segment 17",
                        "excerpt": (
                            "I support a measured increase because inflation is elevated, "
                            "while acknowledging uncertainty around the outlook."
                        ),
                    }
                ],
            }
        ]

        report = persist_fomc_decision_trace(
            self.connection,
            payload,
            extractor_version="codex-subscription-transcript-v1",
            allowed_monitor_series_ids={"CPIAUCSL"},
            monitor_series_metadata=self.monitor_series_metadata,
        )

        self.assertEqual(report["decision_id"], "fomc-FOMC-2022-03-15")
        self.assertEqual(report["source_boundary"], "post_meeting_label_only_not_case_input")

    def test_participant_debate_rejects_another_participants_transcript_segment(self):
        payload = deepcopy(self.payload)
        payload["debate"] = [
            {
                "speaker_scope": "participant",
                "participant_id": "bob-example",
                "position": "Oppose the increase.",
                "reasoning": "Preferred waiting for more data.",
                "evidence_refs": [
                    {
                        "document_id": self.transcript_id,
                        "locator": "transcript segment 17",
                        "excerpt": (
                            "I support a measured increase because inflation is elevated, "
                            "while acknowledging uncertainty around the outlook."
                        ),
                    }
                ],
            }
        ]

        with self.assertRaisesRegex(ValueError, "belongs to another participant"):
            persist_fomc_decision_trace(
                self.connection,
                payload,
                extractor_version="codex-subscription-transcript-v1",
                allowed_monitor_series_ids={"CPIAUCSL"},
                monitor_series_metadata=self.monitor_series_metadata,
            )

    def test_transcript_excerpt_must_exist_in_registered_segments(self):
        payload = deepcopy(self.payload)
        payload["context"]["evidence_refs"] = [
            {
                "document_id": self.transcript_id,
                "locator": "transcript segment 17",
                "excerpt": "A fabricated transcript sentence.",
            }
        ]

        with self.assertRaises(ValueError) as raised:
            persist_fomc_decision_trace(
                self.connection,
                payload,
                extractor_version="codex-subscription-transcript-v1",
                allowed_monitor_series_ids={"CPIAUCSL"},
                monitor_series_metadata=self.monitor_series_metadata,
            )

        self.assertIn("not found", str(raised.exception))
        self.assertIn("transcript segment 17", str(raised.exception))
        self.assertIn("A fabricated transcript sentence.", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
