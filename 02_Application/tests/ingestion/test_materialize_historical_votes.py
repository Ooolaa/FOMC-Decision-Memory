import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.app_db import create_schema
from decision_memory.documents import ingest_local_document
from decision_memory.materialize_historical_votes import (
    materialize_historical_votes,
)


class MaterializeHistoricalVoteTests(unittest.TestCase):
    def test_statement_votes_create_full_name_voter_roster_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            statement_path = root / "statement.html"
            statement_path.write_text(
                """
                <p>Voting for the FOMC monetary policy action were: Alan Greenspan,
                Chairman; Timothy F. Geithner, Vice Chairman; and Janet L. Yellen.</p>
                <p>Voting against this action was Jeffrey M. Lacker.</p>
                """,
                encoding="utf-8",
            )
            document_id = ingest_local_document(
                app,
                statement_path,
                meeting_id="FOMC-2006-01-31",
                document_type="statement",
                publication_at="2006-01-31T23:59:59Z",
                usage_class="label_only",
                source_url="https://www.federalreserve.gov/statement.htm",
            )
            app.execute(
                """
                INSERT INTO participant (
                    participant_id, display_name, role,
                    effective_start, effective_end
                ) VALUES ('archived-speaker', 'Archived Speaker', 'participant',
                          '2006-01-31', '2006-01-31')
                """
            )
            app.executemany(
                """
                INSERT INTO participant (
                    participant_id, display_name, role,
                    effective_start, effective_end
                ) VALUES (?, ?, ?, '2006-01-31', '2006-01-31')
                """,
                [
                    ("timothy-f-geithner", "Timothy F. Geithner", "policymaker"),
                    ("known-attendee", "Known Attendee", "participant"),
                ],
            )
            app.executemany(
                """
                INSERT INTO meeting_participant (
                    meeting_id, participant_id, role, is_voter, is_chair
                ) VALUES ('FOMC-2006-01-31', ?, ?, 0, 0)
                """,
                [
                    ("timothy-f-geithner", "vice_chair"),
                    ("known-attendee", "participant"),
                ],
            )
            app.execute(
                """
                INSERT INTO transcript_segment (
                    segment_id, document_id, meeting_id, ordinal,
                    speaker_label, participant_id, text, content_hash, created_at
                ) VALUES ('segment-1', ?, 'FOMC-2006-01-31', 0,
                          'ARCHIVED SPEAKER', 'archived-speaker', 'Prior remarks.',
                          'segment-hash', '2026-08-31T00:00:00Z')
                """,
                (document_id,),
            )
            app.commit()
            app.close()
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "meeting_id": "FOMC-2006-01-31",
                                "document_type": "statement",
                                "document_id": document_id,
                                "local_path": str(statement_path.resolve()),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            first = materialize_historical_votes(app_path, manifest_path)
            second = materialize_historical_votes(app_path, manifest_path)
            app = sqlite3.connect(app_path)
            try:
                votes = app.execute(
                    """
                    SELECT participant.display_name, participant_vote.voter_choice
                    FROM participant_vote
                    JOIN participant USING (participant_id)
                    ORDER BY participant.display_name
                    """
                ).fetchall()
                chair = app.execute(
                    """
                    SELECT participant.display_name
                    FROM meeting_participant
                    JOIN participant USING (participant_id)
                    WHERE is_chair = 1
                    """
                ).fetchone()[0]
                archived_speaker = app.execute(
                    """
                    SELECT display_name FROM participant
                    WHERE participant_id = 'archived-speaker'
                    """
                ).fetchone()
                preserved_roster = app.execute(
                    """
                    SELECT participant_id, role, is_voter, is_chair
                    FROM meeting_participant
                    WHERE meeting_id = 'FOMC-2006-01-31'
                      AND participant_id IN ('timothy-f-geithner', 'known-attendee')
                    ORDER BY participant_id
                    """
                ).fetchall()
            finally:
                app.close()

        self.assertEqual(first, second)
        self.assertEqual(first["meeting_count"], 1)
        self.assertEqual(first["vote_count"], 4)
        self.assertEqual(chair, "Alan Greenspan")
        self.assertEqual(archived_speaker, ("Archived Speaker",))
        self.assertEqual(
            preserved_roster,
            [
                ("known-attendee", "participant", 0, 0),
                ("timothy-f-geithner", "vice_chair", 1, 0),
            ],
        )
        self.assertEqual(
            votes,
            [
                ("Alan Greenspan", "FOR"),
                ("Janet L. Yellen", "FOR"),
                ("Jeffrey M. Lacker", "AGAINST"),
                ("Timothy F. Geithner", "FOR"),
            ],
        )

    def test_statement_and_minutes_vote_disagreement_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            statement_path = root / "statement.html"
            minutes_path = root / "minutes.html"
            statement_path.write_text(
                "<p>Voting for this action: Jerome H. Powell and John C. Williams.</p>",
                encoding="utf-8",
            )
            minutes_path.write_text(
                """
                <p>Votes for this action: Messrs. Powell and Williams.</p>
                <p>Votes against this action: Ms. Bowman.</p>
                """,
                encoding="utf-8",
            )
            documents = []
            for document_type, path in (
                ("statement", statement_path),
                ("minutes", minutes_path),
            ):
                document_id = ingest_local_document(
                    app,
                    path,
                    meeting_id="FOMC-2024-09-17",
                    document_type=document_type,
                    publication_at="2024-10-09T18:00:00Z",
                    usage_class="label_only",
                    source_url=f"https://www.federalreserve.gov/{document_type}.htm",
                )
                documents.append(
                    {
                        "meeting_id": "FOMC-2024-09-17",
                        "document_type": document_type,
                        "document_id": document_id,
                        "local_path": str(path.resolve()),
                    }
                )
            app.commit()
            app.close()
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps({"documents": documents}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "vote evidence disagrees"):
                materialize_historical_votes(app_path, manifest_path)

            app = sqlite3.connect(app_path)
            try:
                vote_count = app.execute(
                    "SELECT COUNT(*) FROM participant_vote"
                ).fetchone()[0]
            finally:
                app.close()

        self.assertEqual(vote_count, 0)

    def test_statement_and_minutes_share_the_most_complete_voter_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            statement_path = root / "statement.html"
            minutes_path = root / "minutes.html"
            statement_path.write_text(
                """
                <p>Voting for this action: Kevin Warsh and Jerome H. Powell.</p>
                <p>Voting against this action: None.</p>
                <p>Statement release.</p>
                """,
                encoding="utf-8",
            )
            minutes_path.write_text(
                """
                <p>Voting for this action: Kevin Warsh and Jerome H. Powell.</p>
                <p>Voting against this action: None.</p>
                <p>Minutes release.</p>
                """,
                encoding="utf-8",
            )
            documents = []
            for document_type, path in (
                ("statement", statement_path),
                ("minutes", minutes_path),
            ):
                document_id = ingest_local_document(
                    app,
                    path,
                    meeting_id="FOMC-2026-06-16",
                    document_type=document_type,
                    publication_at="2026-07-08T18:00:00Z",
                    usage_class="label_only",
                    source_url=f"https://www.federalreserve.gov/{document_type}.htm",
                )
                documents.append(
                    {
                        "meeting_id": "FOMC-2026-06-16",
                        "document_type": document_type,
                        "document_id": document_id,
                        "local_path": str(path.resolve()),
                    }
                )
            app.execute(
                """
                INSERT INTO participant (
                    participant_id, display_name, role,
                    effective_start, effective_end
                ) VALUES ('kevin-m-warsh', 'Kevin M. Warsh', 'policymaker',
                          '2026-06-16', '2026-06-16')
                """
            )
            app.execute(
                """
                INSERT INTO meeting_participant (
                    meeting_id, participant_id, role, is_voter, is_chair
                ) VALUES ('FOMC-2026-06-16', 'kevin-m-warsh',
                          'chair', 1, 1)
                """
            )
            app.commit()
            app.close()
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps({"documents": documents}),
                encoding="utf-8",
            )

            materialize_historical_votes(app_path, manifest_path)
            minutes_document_id = next(
                item["document_id"]
                for item in documents
                if item["document_type"] == "minutes"
            )
            app = sqlite3.connect(app_path)
            app.execute(
                "UPDATE participant_vote SET evidence_id = ?",
                (minutes_document_id,),
            )
            app.commit()
            app.close()

            materialize_historical_votes(app_path, manifest_path)
            app = sqlite3.connect(app_path)
            try:
                warsh_rows = app.execute(
                    """
                    SELECT participant_id, display_name
                    FROM participant
                    WHERE display_name LIKE 'Kevin%Warsh'
                    ORDER BY participant_id
                    """
                ).fetchall()
                vote_participant_ids = app.execute(
                    """
                    SELECT participant_id FROM participant_vote
                    WHERE meeting_id = 'FOMC-2026-06-16'
                    ORDER BY participant_id
                    """
                ).fetchall()
                evidence_ids = app.execute(
                    "SELECT DISTINCT evidence_id FROM participant_vote"
                ).fetchall()
            finally:
                app.close()

        self.assertEqual(warsh_rows, [("kevin-m-warsh", "Kevin M. Warsh")])
        self.assertIn(("kevin-m-warsh",), vote_participant_ids)
        self.assertNotIn(("kevin-warsh",), vote_participant_ids)
        self.assertEqual(evidence_ids, [(minutes_document_id,)])


if __name__ == "__main__":
    unittest.main()
