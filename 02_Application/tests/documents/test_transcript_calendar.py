import unittest
from datetime import date

from decision_memory.transcript_calendar import (
    parse_historical_transcript_calendar,
    select_transcript_sample,
)


class TranscriptCalendarTests(unittest.TestCase):
    def test_official_transcript_uses_conservative_page_update_date(self):
        html = """
        <div class="panel panel-default">
          <a href="/monetarypolicy/files/FOMC20060131meeting.pdf">
            Transcript (626 KB PDF)
          </a>
        </div>
        <div>Last Update: May 28, 2020</div>
        """
        documents = parse_historical_transcript_calendar(
            html,
            source_url=(
                "https://www.federalreserve.gov/monetarypolicy/"
                "fomchistorical2006.htm"
            ),
            source_meetings=[
                {
                    "meeting_id": "FOMC-2006-01-31",
                    "meeting_end_date": "2006-01-31",
                }
            ],
            through_date=date(2026, 8, 28),
        )

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["document_type"], "transcript")
        self.assertEqual(documents[0]["usage_class"], "persona_evidence")
        self.assertEqual(documents[0]["publication_at"], "2020-05-28T23:59:59Z")

    def test_sample_is_deterministic_and_spans_the_full_calendar(self):
        documents = [
            {
                "meeting_id": f"FOMC-2006-01-{day:02d}",
                "meeting_end_date": f"2006-01-{day:02d}",
            }
            for day in range(1, 11)
        ]

        sample = select_transcript_sample(documents, target_count=4)

        self.assertEqual(len(sample), 4)
        self.assertEqual(sample[0]["meeting_id"], "FOMC-2006-01-01")
        self.assertEqual(sample[-1]["meeting_id"], "FOMC-2006-01-10")
        self.assertEqual(sample, select_transcript_sample(documents, target_count=4))


if __name__ == "__main__":
    unittest.main()
