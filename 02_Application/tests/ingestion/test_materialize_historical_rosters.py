import sqlite3
import unittest

from decision_memory.app_db import create_schema
from decision_memory.materialize_historical_rosters import _merge_alias


class HistoricalRosterMaterializerTests(unittest.TestCase):
    def test_alias_merge_moves_roster_and_vote_without_duplication(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        create_schema(connection)
        connection.execute(
            """
            INSERT INTO document_source VALUES (
                'doc-1', 'FOMC-2014-09-16', 'statement',
                '2014-09-17T23:59:59Z', 'label_only', '{}', 'hash-1', 'now'
            )
            """
        )
        connection.execute(
            "INSERT INTO participant VALUES ('president-fisher', 'President Fisher', 'policymaker', '2014-09-16', '2014-09-16')"
        )
        connection.execute(
            "INSERT INTO participant VALUES ('richard-w-fisher', 'Richard W. Fisher', 'policymaker', '2006-01-31', '2014-12-16')"
        )
        connection.execute(
            "INSERT INTO meeting_participant VALUES ('FOMC-2014-09-16', 'president-fisher', 'member', 1, 0)"
        )
        connection.execute(
            "INSERT INTO participant_vote VALUES ('FOMC-2014-09-16', 'president-fisher', 1, 'AGAINST', 1, 'doc-1')"
        )

        merged = _merge_alias(connection, "president-fisher", "richard-w-fisher")
        alias_count = connection.execute(
            "SELECT COUNT(*) FROM participant WHERE participant_id = 'president-fisher'"
        ).fetchone()[0]
        vote = connection.execute(
            """
            SELECT voter_choice, dissent FROM participant_vote
            WHERE meeting_id = 'FOMC-2014-09-16'
              AND participant_id = 'richard-w-fisher'
            """
        ).fetchone()
        roster = connection.execute(
            """
            SELECT is_voter, is_chair FROM meeting_participant
            WHERE meeting_id = 'FOMC-2014-09-16'
              AND participant_id = 'richard-w-fisher'
            """
        ).fetchone()
        connection.close()

        self.assertTrue(merged)
        self.assertEqual(alias_count, 0)
        self.assertEqual(vote, ("AGAINST", 1))
        self.assertEqual(roster, (1, 0))


if __name__ == "__main__":
    unittest.main()
