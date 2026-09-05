import sqlite3
import unittest

from decision_memory.app_db import create_schema
from decision_memory.vote_baselines import evaluate_vote_baselines


class VoteBaselineTests(unittest.TestCase):
    def _build_app(self) -> sqlite3.Connection:
        app = sqlite3.connect(":memory:")
        app.execute("PRAGMA foreign_keys = ON")
        create_schema(app)
        for participant_id, display_name in (
            ("chair", "Chair"),
            ("hawk", "Hawk"),
        ):
            app.execute(
                "INSERT INTO participant VALUES (?, ?, 'policymaker', NULL, NULL)",
                (participant_id, display_name),
            )
        for meeting_id, hawk_dissent in (
            ("FOMC-2020-01-28", 1),
            ("FOMC-2020-06-09", 1),
            ("FOMC-2021-01-26", 1),
        ):
            document_id = f"doc-{meeting_id}"
            app.execute(
                """
                INSERT INTO document_source VALUES (
                    ?, ?, 'minutes', '2021-01-01T00:00:00Z',
                    'label_only', '{}', ?, 'now'
                )
                """,
                (document_id, meeting_id, f"hash-{meeting_id}"),
            )
            for participant_id, dissent in (
                ("chair", 0),
                ("hawk", hawk_dissent),
            ):
                app.execute(
                    """
                    INSERT INTO meeting_participant VALUES (
                        ?, ?, 'member', 1, ?
                    )
                    """,
                    (meeting_id, participant_id, int(participant_id == "chair")),
                )
                app.execute(
                    """
                    INSERT INTO participant_vote VALUES (?, ?, 1, ?, ?, ?)
                    """,
                    (
                        meeting_id,
                        participant_id,
                        "AGAINST" if dissent else "FOR",
                        dissent,
                        document_id,
                    ),
                )
        return app

    def test_known_roster_vote_baselines_are_point_in_time_and_explicit(self):
        app = self._build_app()

        report = evaluate_vote_baselines(app, test_start="2021-01-01")
        app.close()

        self.assertEqual(report["known_input"], "meeting_participant.is_voter=1")
        self.assertEqual(report["prediction_target"], "participant_vote.FOR_AGAINST")
        self.assertEqual(report["test_meeting_count"], 1)
        self.assertEqual(report["test_voter_case_count"], 2)
        self.assertEqual(report["label_coverage"], 1.0)
        self.assertEqual(report["baselines"]["all_for"]["f1"], 0.0)
        self.assertEqual(report["baselines"]["prior_dissent_rate"]["f1"], 1.0)
        self.assertEqual(report["gate"]["required_roster_coverage"], 1.0)

    def test_missing_vote_label_for_known_voter_fails_closed(self):
        app = self._build_app()
        app.execute(
            """
            DELETE FROM participant_vote
            WHERE meeting_id = 'FOMC-2021-01-26' AND participant_id = 'hawk'
            """
        )

        with self.assertRaisesRegex(ValueError, "known voter labels are incomplete"):
            evaluate_vote_baselines(app, test_start="2021-01-01")
        app.close()


if __name__ == "__main__":
    unittest.main()
