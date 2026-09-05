import unittest
from datetime import date

from decision_memory.document_calendar import parse_current_document_calendar


class DocumentCalendarTests(unittest.TestCase):
    def test_current_calendar_builds_statement_and_released_minutes_labels(self):
        html = """
        <h4><a id="year">2026 FOMC Meetings</a></h4>
        <div class="row fomc-meeting">
          <div class="fomc-meeting__month"><strong>January</strong></div>
          <div class="fomc-meeting__date">27-28</div>
          <div><strong>Statement:</strong>
            <a href="/monetarypolicy/files/statement.pdf">PDF</a>
            <a href="/newsevents/pressreleases/monetary20260128a.htm">HTML</a>
            <a href="/newsevents/pressreleases/monetary20260128a1.htm">Implementation Note</a>
          </div>
          <div class="fomc-meeting__minutes"><strong>Minutes:</strong>
            <a href="/monetarypolicy/fomcminutes20260128.htm">HTML</a>
            <br>(Released February 18, 2026)
          </div>
        </div>
        <div class="row fomc-meeting">
          <div class="fomc-meeting__month"><strong>March</strong></div>
          <div class="fomc-meeting__date">17-18*</div>
          <div><strong>Statement:</strong>
            <a href="/newsevents/pressreleases/monetary20260318a.htm">HTML</a>
          </div>
          <div class="fomc-meeting__minutes"><strong>Minutes:</strong>
            <a href="/monetarypolicy/fomcminutes20260318.htm">HTML</a>
            <br>(Released April 08, 2026)
          </div>
        </div>
        <div class="row fomc-meeting">
          <div class="fomc-meeting__month"><strong>March</strong></div>
          <div class="fomc-meeting__date">19 (notation vote)</div>
          <div><strong>Statement:</strong>
            <a href="/newsevents/pressreleases/monetary20260319a.htm">HTML</a>
          </div>
        </div>
        """

        documents = parse_current_document_calendar(
            html,
            source_url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            start_year=2021,
            through_date=date(2026, 3, 20),
        )

        self.assertEqual(len(documents), 3)
        self.assertEqual(documents[0]["meeting_id"], "FOMC-2026-01-27")
        self.assertEqual(documents[0]["document_type"], "statement")
        self.assertEqual(documents[0]["publication_at"], "2026-01-28T23:59:59Z")
        self.assertEqual(documents[1]["document_type"], "minutes")
        self.assertEqual(documents[1]["publication_at"], "2026-02-18T23:59:59Z")
        self.assertEqual(documents[2]["meeting_id"], "FOMC-2026-03-17")
        self.assertEqual(documents[2]["document_type"], "statement")


if __name__ == "__main__":
    unittest.main()
