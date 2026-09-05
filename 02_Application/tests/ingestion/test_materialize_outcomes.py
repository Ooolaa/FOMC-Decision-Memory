import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.app_db import create_schema as create_app_schema
from decision_memory.documents import ingest_local_document
from decision_memory.materialize_outcomes import materialize_meeting_outcomes
from fred_vintage_db import (
    create_schema as create_source_schema,
    insert_meetings,
    insert_observations,
    insert_series,
)


class MaterializeOutcomeTests(unittest.TestCase):
    def test_outcome_is_idempotent_and_manifest_preserves_derivation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.sqlite"
            source = sqlite3.connect(source_path)
            create_source_schema(source)
            for series_id in ("DFEDTARL", "DFEDTARU"):
                insert_series(
                    source,
                    {
                        "id": series_id,
                        "title": series_id,
                        "frequency": "Daily",
                        "frequency_short": "D",
                        "units": "Percent",
                    },
                    release_id=None,
                    vintage_mode="FRED_ONLY_OBSERVATION_DATE",
                )
            insert_meetings(
                source,
                [
                    {
                        "meeting_id": "FOMC-2022-03-15",
                        "meeting_start_date": "2022-03-15",
                        "meeting_end_date": "2022-03-16",
                        "information_cutoff_date_et": "2022-03-14",
                        "cutoff_policy": "previous_calendar_day",
                        "calendar_source_url": "https://www.federalreserve.gov/",
                    }
                ],
            )
            for series_id, before, after in (
                ("DFEDTARL", 0.0, 0.25),
                ("DFEDTARU", 0.25, 0.5),
            ):
                insert_observations(
                    source,
                    series_id,
                    [
                        {
                            "date": "2022-03-14",
                            "value": str(before),
                            "realtime_start": "2022-03-14",
                            "realtime_end": "9999-12-31",
                        },
                        {
                            "date": "2022-03-17",
                            "value": str(after),
                            "realtime_start": "2022-03-17",
                            "realtime_end": "9999-12-31",
                        },
                    ],
                )
            source.commit()
            source.close()

            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_app_schema(app)
            statement_path = root / "statement.html"
            statement_path.write_text("<p>Federal funds target range.</p>")
            document_id = ingest_local_document(
                app,
                statement_path,
                meeting_id="FOMC-2022-03-15",
                document_type="statement",
                publication_at="2022-03-16T23:59:59Z",
                usage_class="label_only",
                source_url="https://www.federalreserve.gov/statement.htm",
            )
            app.commit()
            app.close()

            document_manifest = root / "documents.json"
            document_manifest.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "meeting_id": "FOMC-2022-03-15",
                                "document_type": "statement",
                                "document_id": document_id,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            outcome_manifest = root / "outcomes.json"

            first = materialize_meeting_outcomes(
                source_path,
                app_path,
                document_manifest,
                outcome_manifest,
            )
            second = materialize_meeting_outcomes(
                source_path,
                app_path,
                document_manifest,
                outcome_manifest,
            )
            app = sqlite3.connect(app_path)
            try:
                persisted = app.execute(
                    """
                    SELECT action_class, target_lower, target_upper,
                           source_document_id
                    FROM meeting_outcome
                    """
                ).fetchone()
            finally:
                app.close()
            manifest = json.loads(outcome_manifest.read_text(encoding="utf-8"))

        self.assertEqual(first, second)
        self.assertEqual(first["meeting_count"], 1)
        self.assertEqual(first["action_class_counts"], {"HIKE": 1})
        self.assertEqual(persisted, ("HIKE", 0.25, 0.5, document_id))
        self.assertEqual(manifest["rule_version"], "rate_delta_v1")
        self.assertEqual(manifest["outcomes"][0]["pre_target_upper"], 0.25)
        self.assertEqual(
            manifest["outcomes"][0]["outcome_effective_date"],
            "2022-03-17",
        )
        self.assertEqual(first["manifest_hash"], manifest["manifest_hash"])


if __name__ == "__main__":
    unittest.main()
