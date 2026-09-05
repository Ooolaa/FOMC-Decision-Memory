import sqlite3
import unittest

from decision_memory.app_db import create_schema
from decision_memory.transcripts import persist_transcript_segments, split_speaker_segments


class TranscriptSegmentationTests(unittest.TestCase):
    def test_speaker_labels_split_without_treating_page_headers_as_speakers(self):
        text = """
        Transcript of the Federal Open Market Committee Meeting
        CHAIRMAN GREENSPAN. Thank you all very much.
        MR. FERGUSON. Let me open the floor now.
        PARTICIPANT. Second.
        """

        segments = split_speaker_segments(text)

        self.assertEqual(
            [segment["speaker_label"] for segment in segments],
            ["CHAIRMAN GREENSPAN", "MR. FERGUSON", "PARTICIPANT"],
        )
        self.assertEqual(segments[0]["text"], "Thank you all very much.")

    def test_inline_speaker_handoff_after_pdf_page_text_starts_a_new_segment(self):
        text = (
            "CHAIRMAN BERNANKE. Thank you. President Lacker. "
            "September 22-23, 2009 145 of 212 "
            "MR. LACKER. Thank you, Mr. Chairman. I prefer no further action."
        )

        segments = split_speaker_segments(text)

        self.assertEqual(
            [segment["speaker_label"] for segment in segments],
            ["CHAIRMAN BERNANKE", "MR. LACKER"],
        )
        self.assertEqual(
            segments[1]["text"],
            "Thank you, Mr. Chairman. I prefer no further action.",
        )

    def test_speaker_name_does_not_swallow_acronym_and_following_speaker(self):
        text = (
            "MR. FISCHER. MPC. "
            "MR. KOCHERLAKOTA. MPC, yes."
        )

        segments = split_speaker_segments(text)

        self.assertEqual(
            [segment["speaker_label"] for segment in segments],
            ["MR. FISCHER", "MR. KOCHERLAKOTA"],
        )
        self.assertEqual(segments[0]["text"], "MPC.")
        self.assertEqual(segments[1]["text"], "MPC, yes.")

    def test_segments_resolve_rostered_surnames_and_persist_idempotently(self):
        connection = sqlite3.connect(":memory:")
        create_schema(connection)
        connection.execute(
            "INSERT INTO participant VALUES (?, ?, ?, ?, ?)",
            ("alan-greenspan", "Alan Greenspan", "chair", None, None),
        )
        connection.execute(
            "INSERT INTO meeting_participant VALUES (?, ?, ?, ?, ?)",
            ("FOMC-2006-01-31", "alan-greenspan", "chair", 1, 1),
        )
        connection.execute(
            "INSERT INTO document_source VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "doc-transcript",
                "FOMC-2006-01-31",
                "transcript",
                "2020-05-28T23:59:59Z",
                "persona_evidence",
                "{}",
                "a" * 64,
                "2026-08-28T00:00:00Z",
            ),
        )
        segments = [
            {"speaker_label": "CHAIRMAN GREENSPAN", "text": "Thank you."},
            {"speaker_label": "PARTICIPANT", "text": "Second."},
        ]

        first = persist_transcript_segments(
            connection,
            document_id="doc-transcript",
            meeting_id="FOMC-2006-01-31",
            segments=segments,
        )
        second = persist_transcript_segments(
            connection,
            document_id="doc-transcript",
            meeting_id="FOMC-2006-01-31",
            segments=segments,
        )

        rows = connection.execute(
            "SELECT speaker_label, participant_id FROM transcript_segment ORDER BY ordinal"
        ).fetchall()
        self.assertEqual(first, second)
        self.assertEqual(rows[0], ("CHAIRMAN GREENSPAN", "alan-greenspan"))
        self.assertEqual(rows[1], ("PARTICIPANT", None))


if __name__ == "__main__":
    unittest.main()
