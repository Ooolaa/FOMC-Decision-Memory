import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from decision_memory.app_db import create_schema
from decision_memory.materialize_documents import (
    materialize_current_documents,
    rebind_verified_manifest,
)


CALENDAR_HTML = """
<h4><a>2026 FOMC Meetings</a></h4>
<div class="row fomc-meeting">
  <div class="fomc-meeting__month">January</div>
  <div class="fomc-meeting__date">27-28</div>
  <div><strong>Statement:</strong>
    <a href="/newsevents/pressreleases/monetary20260128a.htm">HTML</a>
  </div>
  <div class="fomc-meeting__minutes"><strong>Minutes:</strong>
    <a href="/monetarypolicy/fomcminutes20260128.htm">HTML</a>
    (Released February 18, 2026)
  </div>
</div>
"""


class MaterializeDocumentTests(unittest.TestCase):
    def test_official_documents_are_cached_registered_and_manifested_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.sqlite"
            source = sqlite3.connect(source_path)
            source.execute(
                "CREATE TABLE fomc_meeting (meeting_id TEXT PRIMARY KEY)"
            )
            source.execute(
                "INSERT INTO fomc_meeting VALUES ('FOMC-2026-01-27')"
            )
            source.commit()
            source.close()

            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            app.commit()
            app.close()

            def fetcher(url):
                return f"<html><body><p>{url}</p></body></html>".encode("utf-8")

            arguments = {
                "source_database": source_path,
                "app_database": app_path,
                "cache_directory": root / "cache",
                "manifest_path": root / "manifest.json",
                "calendar_html": CALENDAR_HTML,
                "calendar_source_url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                "start_year": 2021,
                "as_of_date": date(2026, 2, 20),
                "document_fetcher": fetcher,
            }
            first = materialize_current_documents(**arguments)
            second = materialize_current_documents(**arguments)
            app = sqlite3.connect(app_path)
            try:
                document_count = app.execute(
                    "SELECT COUNT(*) FROM document_source"
                ).fetchone()[0]
            finally:
                app.close()
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(first["document_count"], 2)
        self.assertEqual(second["manifest_hash"], first["manifest_hash"])
        self.assertEqual(document_count, 2)
        self.assertEqual(manifest["meeting_count"], 1)
        self.assertEqual(manifest["document_count"], 2)

    def test_rebind_reuses_only_hash_verified_cached_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.sqlite"
            source = sqlite3.connect(source_path)
            source.execute("CREATE TABLE fomc_meeting (meeting_id TEXT PRIMARY KEY)")
            source.execute("INSERT INTO fomc_meeting VALUES ('FOMC-2026-01-27')")
            source.commit()
            source.close()

            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            app.commit()
            app.close()

            def fetcher(url):
                return f"<html><body><p>{url}</p></body></html>".encode("utf-8")

            prior_path = root / "prior.json"
            materialize_current_documents(
                source_database=source_path,
                app_database=app_path,
                cache_directory=root / "cache",
                manifest_path=prior_path,
                calendar_html=CALENDAR_HTML,
                calendar_source_url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                start_year=2021,
                as_of_date=date(2026, 2, 20),
                document_fetcher=fetcher,
            )
            prior = json.loads(prior_path.read_text(encoding="utf-8"))

            source = sqlite3.connect(source_path)
            source.execute("CREATE TABLE source_revision (revision INTEGER)")
            source.commit()
            source.close()
            rebound_app_path = root / "rebound-app.sqlite"
            rebound_app = sqlite3.connect(rebound_app_path)
            create_schema(rebound_app)
            rebound_app.commit()
            rebound_app.close()
            rebound_path = root / "rebound.json"
            report = rebind_verified_manifest(
                source_database=source_path,
                app_database=rebound_app_path,
                prior_manifest_path=prior_path,
                manifest_path=rebound_path,
            )
            repeated = rebind_verified_manifest(
                source_database=source_path,
                app_database=rebound_app_path,
                prior_manifest_path=prior_path,
                manifest_path=rebound_path,
            )
            rebound = json.loads(rebound_path.read_text(encoding="utf-8"))
            rebound_app = sqlite3.connect(rebound_app_path)
            rebound_document_count = rebound_app.execute(
                "SELECT COUNT(*) FROM document_source"
            ).fetchone()[0]
            rebound_app.close()

        self.assertEqual(report["verified_cached_documents"], 2)
        self.assertEqual(repeated["manifest_hash"], report["manifest_hash"])
        self.assertEqual(rebound_document_count, 2)
        self.assertEqual(rebound["rebind_mode"], "OFFLINE_VERIFIED_CACHED_EVIDENCE")
        self.assertNotEqual(
            rebound["source_database_sha256"],
            prior["source_database_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
