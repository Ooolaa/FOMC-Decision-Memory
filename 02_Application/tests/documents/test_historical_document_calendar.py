import unittest
from datetime import date

from decision_memory.historical_document_calendar import (
    parse_historical_document_calendar,
)


class HistoricalDocumentCalendarTests(unittest.TestCase):
    def test_old_page_maps_end_date_to_source_meeting_and_release_date(self):
        html = """
        <div class="panel panel-default">
          <div class="panel-heading"><h5>January 31 Meeting - 2006</h5></div>
          <div class="panel-body">
            <p><a href="/newsevents/press/monetary/20060131a.htm">Statement</a></p>
            <p><a href="/fomc/minutes/20060131.htm">Minutes</a>
               (Released Feb 21, 2006)</p>
          </div>
        </div>
        <div class="panel panel-default">
          <div class="panel-heading"><h5>Conference Call - 2006</h5></div>
          <div class="panel-body">
            <p><a href="/newsevents/press/monetary/20060201a.htm">Statement</a></p>
          </div>
        </div>
        """
        documents = parse_historical_document_calendar(
            html,
            source_url="https://www.federalreserve.gov/monetarypolicy/fomchistorical2006.htm",
            source_meetings=[
                {
                    "meeting_id": "FOMC-2006-01-31",
                    "meeting_end_date": "2006-01-31",
                }
            ],
            through_date=date(2026, 8, 27),
        )

        self.assertEqual(len(documents), 2)
        self.assertEqual(
            [item["document_type"] for item in documents],
            ["statement", "minutes"],
        )
        self.assertEqual(documents[0]["publication_at"], "2006-01-31T23:59:59Z")
        self.assertEqual(documents[1]["publication_at"], "2006-02-21T23:59:59Z")
        self.assertEqual(documents[0]["meeting_id"], "FOMC-2006-01-31")
        self.assertEqual(
            documents[0]["source_url"],
            "https://www.federalreserve.gov/newsevents/press/monetary/20060131a.htm",
        )

    def test_modern_history_page_identifies_html_minutes_by_href(self):
        html = """
        <div class="panel panel-default">
          <div class="panel-heading"><h5>January 27-28 Meeting - 2015</h5></div>
          <div class="panel-body">
            <p><a href="/newsevents/pressreleases/monetary20150128a.htm">Statement</a></p>
            Minutes (Released February 18, 2015):<br>
            <a href="/monetarypolicy/fomcminutes20150128.htm">HTML</a> |
            <a href="/monetarypolicy/files/fomcminutes20150128.pdf">PDF</a>
          </div>
        </div>
        """
        documents = parse_historical_document_calendar(
            html,
            source_url="https://www.federalreserve.gov/monetarypolicy/fomchistorical2015.htm",
            source_meetings=[
                {
                    "meeting_id": "FOMC-2015-01-27",
                    "meeting_end_date": "2015-01-28",
                }
            ],
            through_date=date(2026, 8, 27),
        )

        self.assertEqual(len(documents), 2)
        self.assertEqual(documents[1]["document_type"], "minutes")
        self.assertEqual(documents[1]["publication_at"], "2015-02-18T23:59:59Z")
        self.assertTrue(documents[1]["source_url"].endswith("fomcminutes20150128.htm"))


if __name__ == "__main__":
    unittest.main()
