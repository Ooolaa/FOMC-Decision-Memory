import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.app_db import create_schema
from decision_memory.enterprise_trace import (
    persist_enterprise_decision_trace,
    register_synthetic_fixture_document,
)


class EnterpriseDecisionTraceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        create_schema(self.connection)
        self.connection.execute(
            """
            INSERT INTO decision_case (
                decision_id, domain, title, synthetic, composite,
                context_json, created_at
            ) VALUES (
                'enterprise-demo-financing-001', 'enterprise_demo',
                'Composite financing decision', 1, 1, '{}',
                '2026-08-28T00:00:00Z'
            )
            """
        )
        self.connection.execute(
            """
            INSERT INTO decision_assumption (
                assumption_id, decision_id, claim, monitor_series_id,
                monitor_operator, threshold_value, direction_map_version,
                monitor_rule_version, created_at
            ) VALUES (
                'enterprise-demo-financing-001-baa10y',
                'enterprise-demo-financing-001',
                'BAA10Y remains at or below 2.25 percent.',
                'BAA10Y', 'GT', 2.25, 'baa10y_upper_bound_v1',
                'baa10y_upper_bound_v1', '2026-08-28T00:00:00Z'
            )
            """
        )
        self.memo = self.root / "enterprise.html"
        self.memo.write_text(
            """
            <p>[Synthetic composite] The organization considered proceeding now or deferring the financing decision.</p>
            <p>[Synthetic composite] The illustrative decision was to defer while BAA10Y remained a monitored condition.</p>
            <p>[Synthetic composite] Three illustrative votes supported deferral and one opposed.</p>
            <p>[Synthetic composite] The decision assumes BAA10Y remains at or below 2.25 percent.</p>
            """,
            encoding="utf-8",
        )
        self.document_id = register_synthetic_fixture_document(
            self.connection,
            self.memo,
            decision_id="enterprise-demo-financing-001",
            publication_at="2021-06-01T00:00:00Z",
        )

    def tearDown(self):
        self.connection.close()
        self.temporary_directory.cleanup()

    def payload(self):
        def evidence(excerpt):
            return {
                "document_id": self.document_id,
                "locator": "synthetic composite memo",
                "excerpt": excerpt,
            }

        return {
            "schema_version": "decision_trace_v1",
            "decision_id": "enterprise-demo-financing-001",
            "meeting_id": None,
            "context": {
                "summary": "Synthetic composite financing decision.",
                "evidence_refs": [
                    evidence(
                        "[Synthetic composite] The organization considered proceeding now or deferring the financing decision."
                    )
                ],
            },
            "options": [
                {
                    "option_id": "proceed_now",
                    "description": "Proceed with financing now.",
                    "evidence_refs": [
                        evidence(
                            "[Synthetic composite] The organization considered proceeding now or deferring the financing decision."
                        )
                    ],
                },
                {
                    "option_id": "defer",
                    "description": "Defer and monitor financing conditions.",
                    "evidence_refs": [
                        evidence(
                            "[Synthetic composite] The illustrative decision was to defer while BAA10Y remained a monitored condition."
                        )
                    ],
                },
            ],
            "debate": [
                {
                    "speaker_scope": "committee",
                    "participant_id": None,
                    "position": "Proceed now versus defer.",
                    "reasoning": "Synthetic composite trade-off, not a customer quote.",
                    "evidence_refs": [
                        evidence(
                            "[Synthetic composite] The organization considered proceeding now or deferring the financing decision."
                        )
                    ],
                }
            ],
            "decision": {
                "action_class": "DEFER",
                "target_rate": None,
                "target_lower": None,
                "target_upper": None,
                "rationale": "Synthetic composite decision to wait.",
                "evidence_refs": [
                    evidence(
                        "[Synthetic composite] The illustrative decision was to defer while BAA10Y remained a monitored condition."
                    )
                ],
            },
            "vote": {
                "rounds": [
                    {
                        "vote_round": 1,
                        "for_count": 3,
                        "against_count": 1,
                        "abstain_count": 0,
                        "evidence_refs": [
                            evidence(
                                "[Synthetic composite] Three illustrative votes supported deferral and one opposed."
                            )
                        ],
                    }
                ]
            },
            "assumptions": [
                {
                    "assumption_id": "enterprise-demo-financing-001-baa10y",
                    "claim": "BAA10Y remains at or below 2.25 percent.",
                    "monitor_series_id": "BAA10Y",
                    "monitor_operator": "GT",
                    "threshold_value": 2.25,
                    "direction_map_version": "baa10y_upper_bound_v1",
                    "monitor_rule_version": "baa10y_upper_bound_v1",
                    "evidence_refs": [
                        evidence(
                            "[Synthetic composite] The decision assumes BAA10Y remains at or below 2.25 percent."
                        )
                    ],
                }
            ],
        }

    def test_synthetic_composite_trace_persists_idempotently(self):
        first = persist_enterprise_decision_trace(
            self.connection,
            self.payload(),
            extractor_version="human-authored-synthetic-composite-v1",
        )
        second = persist_enterprise_decision_trace(
            self.connection,
            self.payload(),
            extractor_version="human-authored-synthetic-composite-v1",
        )

        self.assertEqual(first, second)
        self.assertEqual(first["source_boundary"], "synthetic_composite_fixture")
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM decision_trace WHERE decision_id = ?",
                ("enterprise-demo-financing-001",),
            ).fetchone()[0],
            1,
        )
        context = json.loads(
            self.connection.execute(
                "SELECT context_json FROM decision_case WHERE decision_id = ?",
                ("enterprise-demo-financing-001",),
            ).fetchone()[0]
        )
        self.assertEqual(
            context["decision_trace_context"]["summary"],
            "Synthetic composite financing decision.",
        )

    def test_non_composite_case_is_rejected(self):
        self.connection.execute(
            "UPDATE decision_case SET composite = 0 WHERE decision_id = ?",
            ("enterprise-demo-financing-001",),
        )
        with self.assertRaisesRegex(ValueError, "synthetic/composite"):
            persist_enterprise_decision_trace(
                self.connection,
                self.payload(),
                extractor_version="human-authored-synthetic-composite-v1",
            )


if __name__ == "__main__":
    unittest.main()
