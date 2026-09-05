import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.app_db import create_schema
from decision_memory.documents import (
    build_case_document_manifest,
    ingest_local_document,
)


class DocumentIngestionTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        create_schema(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_manifest_excludes_late_and_label_only_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            statement = root / "statement.txt"
            statement.write_text("Policy statement visible at cutoff.", encoding="utf-8")
            minutes = root / "minutes.txt"
            minutes.write_text("Minutes published after the meeting.", encoding="utf-8")
            late_input = root / "late-input.txt"
            late_input.write_text("Late document marked input allowed.", encoding="utf-8")

            visible = ingest_local_document(
                self.connection,
                statement,
                meeting_id="FOMC-2021-01-26",
                document_type="prior_statement",
                publication_at="2020-12-16T19:00:00Z",
                usage_class="input_allowed",
                source_url="https://www.federalreserve.gov/monetarypolicy/fomcstatement20201216a.htm",
            )
            ingest_local_document(
                self.connection,
                minutes,
                meeting_id="FOMC-2021-01-26",
                document_type="minutes",
                publication_at="2021-02-17T19:00:00Z",
                usage_class="label_only",
                source_url="https://www.federalreserve.gov/monetarypolicy/fomcminutes20210127.htm",
            )
            ingest_local_document(
                self.connection,
                late_input,
                meeting_id="FOMC-2021-01-26",
                document_type="late_input_probe",
                publication_at="2021-01-27T18:00:00Z",
                usage_class="input_allowed",
                source_url="https://www.federalreserve.gov/monetarypolicy/example.htm",
            )

        manifest = build_case_document_manifest(
            self.connection,
            meeting_id="FOMC-2021-01-26",
            cutoff_at="2021-01-25T23:59:59Z",
        )

        self.assertEqual([item["document_id"] for item in manifest["documents"]], [visible])
        self.assertEqual(manifest["excluded_late_count"], 1)
        self.assertEqual(manifest["excluded_usage_count"], 1)
        self.assertEqual(len(manifest["manifest_hash"]), 64)
        locator = json.loads(manifest["documents"][0]["source_locator"])
        self.assertEqual(locator["kind"], "local_cache_with_official_source")

    def test_hash_mismatch_fails_before_database_insert(self):
        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "statement.txt"
            document.write_text("Official content", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "SHA-256"):
                ingest_local_document(
                    self.connection,
                    document,
                    meeting_id="FOMC-2021-01-26",
                    document_type="statement",
                    publication_at="2021-01-27T19:00:00Z",
                    usage_class="label_only",
                    source_url="https://www.federalreserve.gov/monetarypolicy/example.htm",
                    expected_sha256="0" * 64,
                )

        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM document_source").fetchone()[0],
            0,
        )

    def test_non_official_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "statement.txt"
            document.write_text("Content", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Federal Reserve"):
                ingest_local_document(
                    self.connection,
                    document,
                    meeting_id="FOMC-2021-01-26",
                    document_type="statement",
                    publication_at="2021-01-27T19:00:00Z",
                    usage_class="label_only",
                    source_url="https://example.com/not-official",
                )

    def test_identical_document_ingestion_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "statement.txt"
            document.write_text("Official content", encoding="utf-8")
            arguments = {
                "meeting_id": "FOMC-2021-01-26",
                "document_type": "statement",
                "publication_at": "2021-01-27T23:59:59Z",
                "usage_class": "label_only",
                "source_url": "https://www.federalreserve.gov/monetarypolicy/example.htm",
            }

            first = ingest_local_document(self.connection, document, **arguments)
            second = ingest_local_document(self.connection, document, **arguments)

        self.assertEqual(first, second)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM document_source").fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()
