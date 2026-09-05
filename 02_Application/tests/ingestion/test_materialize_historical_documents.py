import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from decision_memory.app_db import create_schema as create_app_schema
from decision_memory.materialize_historical_documents import (
    materialize_historical_documents,
)
from fred_vintage_db import create_schema as create_source_schema, insert_meetings


class MaterializeHistoricalDocumentTests(unittest.TestCase):
    def test_historical_corpus_is_cached_and_manifested_idempotently(self):
        page_html = """
        <div class="panel panel-default">
          <div class="panel-heading"><h5>January 31 Meeting - 2006</h5></div>
          <div class="panel-body">
            <p><a href="/newsevents/press/monetary/20060131a.htm">Statement</a></p>
            <p><a href="/fomc/minutes/20060131.htm">Minutes</a>
               (Released Feb 21, 2006)</p>
          </div>
        </div>
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.sqlite"
            source = sqlite3.connect(source_path)
            create_source_schema(source)
            insert_meetings(
                source,
                [
                    {
                        "meeting_id": "FOMC-2006-01-31",
                        "meeting_start_date": "2006-01-31",
                        "meeting_end_date": "2006-01-31",
                        "information_cutoff_date_et": "2006-01-30",
                        "cutoff_policy": "previous_calendar_day",
                        "calendar_source_url": "https://www.federalreserve.gov/",
                    }
                ],
            )
            source.commit()
            source.close()
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_app_schema(app)
            app.commit()
            app.close()
            manifest_path = root / "manifest.json"

            def page_fetcher(url):
                self.assertTrue(url.endswith("fomchistorical2006.htm"))
                return page_html

            def document_fetcher(url):
                return (
                    b"<p>Statement evidence.</p>"
                    if url.endswith("a.htm")
                    else b"<p>Minutes evidence.</p>"
                )

            first = materialize_historical_documents(
                source_database=source_path,
                app_database=app_path,
                cache_directory=root / "cache",
                manifest_path=manifest_path,
                start_year=2006,
                end_year=2006,
                as_of_date=date(2026, 8, 27),
                page_fetcher=page_fetcher,
                document_fetcher=document_fetcher,
            )
            second = materialize_historical_documents(
                source_database=source_path,
                app_database=app_path,
                cache_directory=root / "cache",
                manifest_path=manifest_path,
                start_year=2006,
                end_year=2006,
                as_of_date=date(2026, 8, 27),
                page_fetcher=page_fetcher,
                document_fetcher=document_fetcher,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(first["meeting_count"], 1)
        self.assertEqual(first["document_count"], 2)
        self.assertEqual(first["cache_status_counts"], {"CREATED": 2})
        self.assertEqual(second["cache_status_counts"], {"VERIFIED_REUSED": 2})
        self.assertEqual(first["manifest_hash"], second["manifest_hash"])
        self.assertEqual(manifest["document_count"], 2)

    def test_registered_2007_missing_statement_exception_is_explicit(self):
        page_html = "<p>Official page special markup omitted from parser fixture.</p>"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.sqlite"
            source = sqlite3.connect(source_path)
            create_source_schema(source)
            insert_meetings(
                source,
                [{
                    "meeting_id": "FOMC-2007-06-27",
                    "meeting_start_date": "2007-06-27",
                    "meeting_end_date": "2007-06-28",
                    "information_cutoff_date_et": "2007-06-26",
                    "cutoff_policy": "previous_calendar_day",
                    "calendar_source_url": "https://www.federalreserve.gov/",
                }],
            )
            source.commit()
            source.close()
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_app_schema(app)
            app.commit()
            app.close()

            report = materialize_historical_documents(
                source_database=source_path,
                app_database=app_path,
                cache_directory=root / "cache",
                manifest_path=root / "manifest.json",
                start_year=2007,
                end_year=2007,
                as_of_date=date(2026, 8, 27),
                page_fetcher=lambda _: page_html,
                document_fetcher=lambda _: b"<p>Minutes evidence.</p>",
            )

        self.assertEqual(report["document_count"], 1)
        self.assertEqual(report["registered_exception_count"], 1)
        self.assertEqual(report["applied_override_count"], 1)


if __name__ == "__main__":
    unittest.main()
