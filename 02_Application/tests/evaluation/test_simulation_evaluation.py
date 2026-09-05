import sqlite3
import unittest

from decision_memory.app_db import create_schema
from decision_memory.simulation_evaluation import evaluate_simulation_output


class SimulationEvaluationTests(unittest.TestCase):
    def test_policy_and_dissent_metrics_use_deterministic_labels(self):
        app = sqlite3.connect(":memory:")
        app.execute("PRAGMA foreign_keys = ON")
        create_schema(app)
        app.execute(
            """
            INSERT INTO document_source VALUES (
                'doc-1', 'FOMC-2022-03-15', 'statement',
                '2022-03-16T23:59:59Z', 'label_only', '{}', 'hash-1', 'now'
            )
            """
        )
        app.execute(
            """
            INSERT INTO meeting_outcome VALUES (
                'FOMC-2022-03-15', 'HIKE', NULL, 0.25, 0.50, 'doc-1', 'now'
            )
            """
        )
        for participant_id, name, dissent in (
            ("chair", "Chair", 0),
            ("member", "Member", 1),
        ):
            app.execute(
                "INSERT INTO participant VALUES (?, ?, 'policymaker', NULL, NULL)",
                (participant_id, name),
            )
            app.execute(
                "INSERT INTO meeting_participant VALUES ('FOMC-2022-03-15', ?, 'member', 1, ?)",
                (participant_id, int(participant_id == "chair")),
            )
            app.execute(
                "INSERT INTO participant_vote VALUES ('FOMC-2022-03-15', ?, 1, ?, ?, 'doc-1')",
                (participant_id, "AGAINST" if dissent else "FOR", dissent),
            )
        output = {
            "schema_version": "simulation_output_v1",
            "meeting_id": "FOMC-2022-03-15",
            "synthetic": True,
            "profiles": [
                {"participant_id": "chair", "display_name": "Chair", "is_chair": True},
                {"participant_id": "member", "display_name": "Member", "is_chair": False},
            ],
            "discussion": [
                {"participant_id": "chair", "synthetic_text": "I propose a hike."},
                {"participant_id": "member", "synthetic_text": "I dissent."},
            ],
            "final_proposal": {
                "proposer_participant_id": "chair",
                "action_class": "HIKE",
                "rationale": "Inflation risk.",
            },
            "votes": [
                {"participant_id": "chair", "choice": "FOR"},
                {"participant_id": "member", "choice": "AGAINST"},
            ],
        }

        report = evaluate_simulation_output(app, output)
        app.close()

        self.assertEqual(report["policy_accuracy"], 1.0)
        self.assertEqual(report["dissent_precision"], 1.0)
        self.assertEqual(report["dissent_recall"], 1.0)
        self.assertEqual(report["dissent_f1"], 1.0)
        self.assertTrue(report["proposal_action_matches_label"])

        app = sqlite3.connect(":memory:")
        app.execute("PRAGMA foreign_keys = ON")
        create_schema(app)
        app.execute(
            """
            INSERT INTO document_source VALUES (
                'doc-1', 'FOMC-2022-03-15', 'statement',
                '2022-03-16T23:59:59Z', 'label_only', '{}', 'hash-1', 'now'
            )
            """
        )
        app.execute(
            """
            INSERT INTO meeting_outcome VALUES (
                'FOMC-2022-03-15', 'HIKE', NULL, 0.25, 0.50, 'doc-1', 'now'
            )
            """
        )
        for participant_id, name in (("chair", "Chair"), ("member", "Member")):
            app.execute(
                "INSERT INTO participant VALUES (?, ?, 'policymaker', NULL, NULL)",
                (participant_id, name),
            )
            app.execute(
                "INSERT INTO meeting_participant VALUES ('FOMC-2022-03-15', ?, 'member', 1, ?)",
                (participant_id, int(participant_id == "chair")),
            )
        app.execute(
            "INSERT INTO participant_vote VALUES ('FOMC-2022-03-15', 'chair', 1, 'FOR', 0, 'doc-1')"
        )

        with self.assertRaisesRegex(ValueError, "vote labels.*known voter roster"):
            evaluate_simulation_output(app, output)
        app.close()


if __name__ == "__main__":
    unittest.main()
