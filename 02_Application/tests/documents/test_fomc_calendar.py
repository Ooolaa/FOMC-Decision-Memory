import unittest
from datetime import date

from fomc_calendar import (
    parse_current_calendar,
    parse_historical_calendar,
)


class HistoricalCalendarTests(unittest.TestCase):
    def test_keeps_official_meetings_and_excludes_other_actions(self):
        html = """
        <h5>January 31 Meeting - 2006</h5>
        <h5>March 27-28 Meeting - 2006</h5>
        <h5>March 2 (unscheduled) Meeting - 2006</h5>
        <h5>October 4 (unscheduled) - 2006</h5>
        <h5>May 9 Conference Call - 2006</h5>
        <h5>March 17-18 (cancelled) Meeting - 2006</h5>
        <h5>March 19 (notation vote) - 2006</h5>
        """

        meetings = parse_historical_calendar(
            html,
            source_url="https://example.test/2006",
        )

        self.assertEqual(
            [
                (
                    item["meeting_start_date"],
                    item["meeting_end_date"],
                    item["information_cutoff_date_et"],
                )
                for item in meetings
            ],
            [
                ("2006-01-31", "2006-01-31", "2006-01-30"),
                ("2006-03-02", "2006-03-02", "2006-03-01"),
                ("2006-03-27", "2006-03-28", "2006-03-26"),
            ],
        )

    def test_parses_cross_month_meeting(self):
        meetings = parse_historical_calendar(
            "<h5>April/May 30-1 Meeting - 2019</h5>",
            source_url="https://example.test/2019",
        )

        self.assertEqual(meetings[0]["meeting_id"], "FOMC-2019-04-30")
        self.assertEqual(meetings[0]["meeting_end_date"], "2019-05-01")

    def test_parses_cross_month_heading_with_both_month_names(self):
        meetings = parse_historical_calendar(
            "<h5>July 31-August 1 Meeting - 2012</h5>",
            source_url="https://example.test/2012",
        )

        self.assertEqual(meetings[0]["meeting_id"], "FOMC-2012-07-31")
        self.assertEqual(meetings[0]["meeting_end_date"], "2012-08-01")


class CurrentCalendarTests(unittest.TestCase):
    def test_extracts_rows_and_filters_future_and_notation_votes(self):
        html = """
        <h4><a>2026 FOMC Meetings</a></h4>
        <div class="row fomc-meeting">
          <div class="fomc-meeting__month"><strong>January</strong></div>
          <div class="fomc-meeting__date">27-28</div>
        </div>
        <div class="row fomc-meeting">
          <div class="fomc-meeting__month"><strong>September</strong></div>
          <div class="fomc-meeting__date">15-16*</div>
        </div>
        <h4><a>2025 FOMC Meetings</a></h4>
        <div class="row fomc-meeting">
          <div class="fomc-meeting__month"><strong>August</strong></div>
          <div class="fomc-meeting__date">22 (notation vote)</div>
        </div>
        <div class="row fomc-meeting">
          <div class="fomc-meeting__month"><strong>Jan/Feb</strong></div>
          <div class="fomc-meeting__date">31-1</div>
        </div>
        """

        meetings = parse_current_calendar(
            html,
            source_url="https://example.test/current",
            start_year=2025,
            through_date=date(2026, 8, 21),
        )

        self.assertEqual(
            [item["meeting_id"] for item in meetings],
            ["FOMC-2025-01-31", "FOMC-2026-01-27"],
        )
        self.assertEqual(meetings[0]["meeting_end_date"], "2025-02-01")


if __name__ == "__main__":
    unittest.main()
