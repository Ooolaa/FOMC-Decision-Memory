import sqlite3
import unittest

from decision_memory.app_db import (
    create_schema,
    migrate_assumption_event_schema,
    record_assumption_event,
    seed_enterprise_demo,
    workflow_recognition_lag_seconds,
)


class EnterpriseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        create_schema(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_schema_contains_r5_minimum_tables(self):
        tables = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            ).fetchall()
        }

        self.assertEqual(
            tables,
            {
                "document_source",
                "transcript_segment",
                "public_communication",
                "participant",
                "meeting_participant",
                "meeting_outcome",
                "participant_vote",
                "decision_case",
                "decision_trace",
                "decision_assumption",
                "assumption_event",
                "policy_rate_context",
                "reaction_model",
                "reaction_coefficient",
                "simulation_case",
                "simulation_run",
                "evaluation_result",
            },
        )

    def test_review_events_produce_recomputable_workflow_recognition_lag(self):
        ids = seed_enterprise_demo(
            self.connection,
            contradiction_at="2026-08-01T00:00:00Z",
        )

        record_assumption_event(
            self.connection,
            ids["assumption_id"],
            "REVIEW_REQUESTED",
            "2026-08-03T00:00:00Z",
            actor="demo-user",
        )
        record_assumption_event(
            self.connection,
            ids["assumption_id"],
            "REVIEWED",
            "2026-08-05T12:00:00Z",
            actor="demo-user",
        )

        self.assertEqual(
            workflow_recognition_lag_seconds(
                self.connection,
                ids["assumption_id"],
            ),
            388800,
        )
        case = self.connection.execute(
            """
            SELECT domain, synthetic, composite
            FROM decision_case
            WHERE decision_id = ?
            """,
            (ids["decision_id"],),
        ).fetchone()
        monitor = self.connection.execute(
            """
            SELECT monitor_series_id
            FROM decision_assumption
            WHERE assumption_id = ?
            """,
            (ids["assumption_id"],),
        ).fetchone()
        self.assertEqual(case, ("enterprise_demo", 1, 1))
        self.assertEqual(monitor, ("BAA10Y",))

    def test_reviewed_event_requires_an_earlier_review_request(self):
        ids = seed_enterprise_demo(
            self.connection,
            contradiction_at="2026-08-01T00:00:00Z",
        )

        with self.assertRaisesRegex(ValueError, "REVIEW_REQUESTED"):
            record_assumption_event(
                self.connection,
                ids["assumption_id"],
                "REVIEWED",
                "2026-08-05T12:00:00Z",
                actor="demo-user",
            )

    def test_lag_is_none_until_review_is_completed(self):
        ids = seed_enterprise_demo(
            self.connection,
            contradiction_at="2026-08-01T00:00:00Z",
        )
        record_assumption_event(
            self.connection,
            ids["assumption_id"],
            "REVIEW_REQUESTED",
            "2026-08-03T00:00:00Z",
            actor="demo-user",
        )

        self.assertIsNone(
            workflow_recognition_lag_seconds(
                self.connection,
                ids["assumption_id"],
            )
        )

    def test_fomc_metric_events_require_deterministic_sequence(self):
        self.connection.execute(
            """
            INSERT INTO decision_case (
                decision_id, domain, title, synthetic, composite,
                context_json, created_at
            ) VALUES ('fomc-demo', 'fomc', 'FOMC demo', 0, 0, '{}', 'now')
            """
        )
        self.connection.execute(
            """
            INSERT INTO decision_assumption (
                assumption_id, decision_id, claim, monitor_series_id,
                monitor_operator, threshold_value, direction_map_version,
                monitor_rule_version, created_at
            ) VALUES (
                'fomc-demo-cpi', 'fomc-demo', 'Inflation is transitory',
                'CPIAUCSL', 'GT', 3.0, 'inflation_hawkish_v1',
                'inflation_transitory_v1', 'now'
            )
            """
        )
        record_assumption_event(
            self.connection,
            'fomc-demo-cpi',
            'CONTRADICTION',
            '2021-05-12T00:00:00Z',
            actor='deterministic-evaluator',
        )
        record_assumption_event(
            self.connection,
            'fomc-demo-cpi',
            'STATEMENT_FLIP',
            '2021-12-15T00:00:00Z',
            actor='deterministic-evaluator',
        )
        record_assumption_event(
            self.connection,
            'fomc-demo-cpi',
            'POLICY_RESPONSE',
            '2022-03-16T00:00:00Z',
            actor='deterministic-evaluator',
        )

        events = self.connection.execute(
            """
            SELECT event_type FROM assumption_event
            WHERE assumption_id = 'fomc-demo-cpi'
            ORDER BY occurred_at
            """
        ).fetchall()
        self.assertEqual(
            events,
            [('CONTRADICTION',), ('STATEMENT_FLIP',), ('POLICY_RESPONSE',)],
        )

    def test_legacy_event_table_migration_preserves_events(self):
        ids = seed_enterprise_demo(
            self.connection,
            contradiction_at="2026-08-01T00:00:00Z",
        )
        self.connection.execute("DROP INDEX ix_assumption_event_timeline")
        self.connection.execute("ALTER TABLE assumption_event RENAME TO event_v2")
        self.connection.execute(
            """
            CREATE TABLE assumption_event (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                assumption_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK (
                    event_type IN ('CONTRADICTION', 'REVIEW_REQUESTED', 'REVIEWED')
                ),
                occurred_at TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (assumption_id)
                    REFERENCES decision_assumption(assumption_id),
                UNIQUE (assumption_id, event_type, occurred_at)
            )
            """
        )
        self.connection.execute(
            """
            INSERT INTO assumption_event
            SELECT * FROM event_v2
            """
        )
        self.connection.execute("DROP TABLE event_v2")

        changed = migrate_assumption_event_schema(self.connection)

        self.assertTrue(changed)
        self.assertEqual(
            self.connection.execute(
                "SELECT event_type FROM assumption_event"
            ).fetchall(),
            [("CONTRADICTION",)],
        )
        record_assumption_event(
            self.connection,
            ids["assumption_id"],
            "STATEMENT_FLIP",
            "2026-08-02T00:00:00Z",
            actor="deterministic-evaluator",
        )


if __name__ == "__main__":
    unittest.main()
