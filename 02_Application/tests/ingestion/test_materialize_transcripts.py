import json
import hashlib
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from decision_memory.app_db import create_schema
from decision_memory.materialize_transcripts import (
    materialize_transcripts,
    resegment_transcripts_from_manifest,
)


class MaterializeTranscriptTests(unittest.TestCase):
    def test_official_sample_is_cached_segmented_and_manifested_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.sqlite"
            source = sqlite3.connect(source_path)
            source.execute(
                """
                CREATE TABLE fomc_meeting (
                    meeting_id TEXT PRIMARY KEY,
                    meeting_start_date TEXT NOT NULL,
                    meeting_end_date TEXT NOT NULL
                )
                """
            )
            source.execute(
                "INSERT INTO fomc_meeting VALUES (?, ?, ?)",
                ("FOMC-2006-01-31", "2006-01-31", "2006-01-31"),
            )
            source.commit()
            source.close()

            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            app.commit()
            app.close()
            manifest_path = root / "manifest.json"
            html = """
            <div class="panel panel-default">
              <a href="/monetarypolicy/files/FOMC20060131meeting.pdf">
                Transcript (626 KB PDF)
              </a>
            </div>
            <div>Last Update: May 28, 2020</div>
            """

            kwargs = dict(
                source_database=source_path,
                app_database=app_path,
                cache_directory=root / "cache",
                manifest_path=manifest_path,
                start_year=2006,
                end_year=2006,
                as_of_date=date(2026, 8, 28),
                target_count=1,
                minimum_segments_per_document=1,
                page_fetcher=lambda _: html,
                document_fetcher=lambda _: b"fake-pdf",
                transcript_text_extractor=lambda _: {
                    "page_count": 2,
                    "text": (
                        "CHAIRMAN GREENSPAN. Thank you.\n"
                        "MR. FERGUSON. Let us begin."
                    ),
                },
            )
            first = materialize_transcripts(**kwargs)
            second = materialize_transcripts(**kwargs)

            app = sqlite3.connect(app_path)
            document_count = app.execute(
                "SELECT COUNT(*) FROM document_source WHERE document_type='transcript'"
            ).fetchone()[0]
            segment_count = app.execute(
                "SELECT COUNT(*) FROM transcript_segment"
            ).fetchone()[0]
            app.close()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(first, second)
        self.assertEqual(document_count, 1)
        self.assertEqual(segment_count, 2)
        self.assertEqual(manifest["target_count"], 1)
        self.assertEqual(manifest["documents"][0]["segment_count"], 2)

    def test_resegment_manifest_replaces_old_segments_and_writes_new_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "transcript.pdf"
            pdf_path.write_bytes(b"frozen official transcript")
            pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
            app_path = root / "candidate.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            app.executemany(
                "INSERT INTO participant VALUES (?, ?, ?, ?, ?)",
                [
                    ("ben-s-bernanke", "Ben S. Bernanke", "chair", None, None),
                    ("jeffrey-m-lacker", "Jeffrey M. Lacker", "member", None, None),
                ],
            )
            app.executemany(
                "INSERT INTO meeting_participant VALUES (?, ?, ?, ?, ?)",
                [
                    ("FOMC-2009-09-22", "ben-s-bernanke", "chair", 1, 1),
                    ("FOMC-2009-09-22", "jeffrey-m-lacker", "member", 0, 0),
                ],
            )
            app.execute(
                "INSERT INTO document_source VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "doc-transcript",
                    "FOMC-2009-09-22",
                    "transcript",
                    "2015-01-01T23:59:59Z",
                    "persona_evidence",
                    "https://www.federalreserve.gov/example.pdf",
                    pdf_hash,
                    "2026-08-31T00:00:00Z",
                ),
            )
            app.execute(
                "INSERT INTO transcript_segment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "old-segment",
                    "doc-transcript",
                    "FOMC-2009-09-22",
                    0,
                    "CHAIRMAN BERNANKE",
                    "ben-s-bernanke",
                    "Thank you. MR. LACKER. I prefer no further action.",
                    "a" * 64,
                    "2026-08-31T00:00:00Z",
                ),
            )
            app.commit()
            app.close()
            source_manifest = root / "manifest_v1.json"
            source_manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "extraction_version": "pypdf_speaker_regex_v1",
                        "documents": [
                            {
                                "meeting_id": "FOMC-2009-09-22",
                                "document_id": "doc-transcript",
                                "local_path": str(pdf_path),
                                "content_hash": pdf_hash,
                                "page_count": 1,
                                "segment_count": 1,
                                "resolved_participant_segment_count": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            source_manifest_hash = hashlib.sha256(
                source_manifest.read_bytes()
            ).hexdigest()
            output_manifest = root / "manifest_v2.json"

            report = resegment_transcripts_from_manifest(
                app_database=app_path,
                source_manifest_path=source_manifest,
                output_manifest_path=output_manifest,
                minimum_segments_per_document=1,
                transcript_text_extractor=lambda _: {
                    "page_count": 1,
                    "text": (
                        "CHAIRMAN BERNANKE. Thank you. President Lacker. "
                        "MR. LACKER. I prefer no further action."
                    ),
                },
            )

            app = sqlite3.connect(app_path)
            rows = app.execute(
                "SELECT speaker_label, participant_id FROM transcript_segment "
                "ORDER BY ordinal"
            ).fetchall()
            app.close()
            manifest = json.loads(output_manifest.read_text(encoding="utf-8"))

        self.assertEqual(
            rows,
            [
                ("CHAIRMAN BERNANKE", "ben-s-bernanke"),
                ("MR. LACKER", "jeffrey-m-lacker"),
            ],
        )
        self.assertEqual(report["segment_count"], 2)
        self.assertEqual(report["resolved_participant_segment_count"], 2)
        self.assertEqual(
            manifest["extraction_version"],
            "pypdf_speaker_regex_v3_inline_handoff_no_period",
        )
        self.assertEqual(
            manifest["source_manifest_sha256"],
            source_manifest_hash,
        )


if __name__ == "__main__":
    unittest.main()
