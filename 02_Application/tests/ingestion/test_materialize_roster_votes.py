import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.app_db import create_schema
from decision_memory.documents import ingest_local_document
from decision_memory.materialize_roster_votes import materialize_rosters_and_votes


class MaterializeRosterVoteTests(unittest.TestCase):
    def test_one_official_minutes_document_materializes_roster_and_votes(self):
        html = b"""
        <p><strong>Attendance</strong><br>
        Jerome H. Powell, Chair<br>
        John C. Williams, Vice Chair</p>
        <p>Mary C. Daly, Alternate Members of the Committee</p>
        <p>Neel Kashkari, Presidents of the Federal Reserve Banks of Minneapolis</p>
        <p>James A. Clouse, Secretary</p>
        <p><strong>Voting for this action:</strong> Jerome H. Powell and John C. Williams.</p>
        <p><strong>Voting against this action:</strong> None.</p>
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            minutes_path = root / "minutes.html"
            minutes_path.write_bytes(html)
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            app.execute("PRAGMA foreign_keys = ON")
            create_schema(app)
            document_id = ingest_local_document(
                app,
                minutes_path,
                meeting_id="FOMC-2026-01-27",
                document_type="minutes",
                publication_at="2026-02-18T23:59:59Z",
                usage_class="label_only",
                source_url="https://www.federalreserve.gov/monetarypolicy/fomcminutes20260128.htm",
            )
            app.commit()
            app.close()
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "meeting_id": "FOMC-2026-01-27",
                                "document_type": "minutes",
                                "document_id": document_id,
                                "local_path": str(minutes_path.resolve()),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            first = materialize_rosters_and_votes(app_path, manifest_path)
            second = materialize_rosters_and_votes(app_path, manifest_path)
            app = sqlite3.connect(app_path)
            try:
                chair = app.execute(
                    """
                    SELECT participant.display_name
                    FROM participant
                    JOIN meeting_participant USING (participant_id)
                    WHERE meeting_participant.is_chair = 1
                    """
                ).fetchone()[0]
            finally:
                app.close()

        self.assertEqual(first["meeting_count"], 1)
        self.assertEqual(first["participant_count"], 4)
        self.assertEqual(first["meeting_participant_count"], 4)
        self.assertEqual(first["vote_count"], 2)
        self.assertEqual(second, first)
        self.assertEqual(chair, "Jerome H. Powell")


if __name__ == "__main__":
    unittest.main()
