import sqlite3
import unittest

from decision_memory.app_db import create_schema
from decision_memory.profile_cards import build_profile_cards


class ProfileCardTests(unittest.TestCase):
    def test_cards_cover_all_meeting_participants_and_disclose_pooling(self):
        app = sqlite3.connect(":memory:")
        app.execute("PRAGMA foreign_keys = ON")
        create_schema(app)
        for participant_id, display_name in (
            ("chair", "Chair Example"),
            ("member", "Member Example"),
        ):
            app.execute(
                """
                INSERT INTO participant (
                    participant_id, display_name, role,
                    effective_start, effective_end
                ) VALUES (?, ?, 'policymaker', '2020-01-01', NULL)
                """,
                (participant_id, display_name),
            )
        app.executemany(
            """
            INSERT INTO meeting_participant (
                meeting_id, participant_id, role, is_voter, is_chair
            ) VALUES ('FOMC-2022-03-15', ?, ?, ?, ?)
            """,
            [
                ("chair", "chair", 1, 1),
                ("member", "participant", 0, 0),
            ],
        )
        app.execute(
            """
            INSERT INTO document_source VALUES (
                'doc', 'FOMC-2021-12-14', 'statement',
                '2021-12-14T00:00:00Z', 'label_only', '{}',
                'hash', '2026-08-28T00:00:00Z'
            )
            """
        )
        app.execute(
            """
            INSERT INTO document_source VALUES (
                'doc-2', 'FOMC-2022-01-25', 'statement',
                '2022-01-26T00:00:00Z', 'label_only', '{}',
                'hash-2', '2026-08-28T00:00:00Z'
            )
            """
        )
        app.execute(
            """
            INSERT INTO document_source VALUES (
                'doc-late', 'FOMC-2022-02-01', 'minutes',
                '2022-04-01T00:00:00Z', 'label_only', '{}',
                'hash-late', '2026-08-28T00:00:00Z'
            )
            """
        )
        app.execute(
            """
            INSERT INTO participant_vote VALUES (
                'FOMC-2021-12-14', 'chair', 1, 'AGAINST', 1, 'doc'
            )
            """
        )
        app.execute(
            """
            INSERT INTO participant_vote VALUES (
                'FOMC-2022-01-25', 'chair', 1, 'FOR', 0, 'doc-2'
            )
            """
        )
        app.execute(
            """
            INSERT INTO participant_vote VALUES (
                'FOMC-2022-02-01', 'chair', 1, 'AGAINST', 1, 'doc-late'
            )
            """
        )
        artifact = build_profile_cards(
            app,
            {
                "model_id": "pooled-v1",
                "training_meeting_count": 121,
                "coefficients": {"cpi_yoy": -0.1, "unemployment_level": 0.2},
            },
            "FOMC-2022-03-15",
        )
        app.close()

        self.assertEqual(artifact["participant_count"], 2)
        self.assertEqual(artifact["voter_count"], 1)
        self.assertFalse(artifact["individual_models_estimated"])
        chair = next(card for card in artifact["cards"] if card["participant_id"] == "chair")
        self.assertEqual(chair["prior_vote_count"], 2)
        self.assertEqual(chair["prior_dissent_rate"], 0.5)
        self.assertFalse(chair["previous_vote_against"])
        self.assertEqual(chair["recent_3_dissent_rate"], 0.5)
        self.assertEqual(chair["votes_since_last_dissent"], 1)
        self.assertEqual(
            chair["recent_vote_history"],
            [
                {
                    "meeting_id": "FOMC-2022-01-25",
                    "vote_round": 1,
                    "voter_choice": "FOR",
                    "dissent": False,
                },
                {
                    "meeting_id": "FOMC-2021-12-14",
                    "vote_round": 1,
                    "voter_choice": "AGAINST",
                    "dissent": True,
                },
            ],
        )
        self.assertEqual(chair["macro_coefficients"]["cpi_yoy"], -0.1)


if __name__ == "__main__":
    unittest.main()
