import unittest
from pathlib import Path

from decision_memory.official_forecast_context import load_official_forecast_context


ROOT = Path(__file__).resolve().parents[2]


class OfficialForecastContextTests(unittest.TestCase):
    def test_snapshot_has_current_voting_members_and_missing_regional_evidence(self):
        context = load_official_forecast_context(
            ROOT / "fixtures/next_meeting_official_context_2026-09-01.json"
        )

        self.assertEqual(context["meeting_id"], "FOMC-2026-09-15")
        self.assertEqual(context["as_of_date"], "2026-09-01")
        self.assertEqual(context["membership_source_updated_at"], "2026-08-19")
        self.assertEqual(len(context["voting_members"]), 12)
        self.assertEqual(
            len({member["participant_id"] for member in context["voting_members"]}),
            12,
        )
        self.assertEqual(
            {member["role"] for member in context["voting_members"]},
            {"chair", "vice_chair", "member"},
        )
        supplemental_names = {
            item["participant_id"] for item in context["supplemental_communications"]
        }
        self.assertEqual(
            supplemental_names,
            {"anna-paulson", "beth-m-hammack", "john-c-williams", "neel-kashkari"},
        )
        self.assertTrue(
            all(
                item["source_url"].startswith("https://")
                and item["publication_date"] <= context["as_of_date"]
                and item["text_kind"] == "source_summary"
                for item in context["supplemental_communications"]
            )
        )


if __name__ == "__main__":
    unittest.main()
