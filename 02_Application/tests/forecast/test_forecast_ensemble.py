import unittest

from decision_memory.forecast_ensemble import combine_forward_predictions


class ForecastEnsembleTests(unittest.TestCase):
    def setUp(self):
        self.roster = ["member-a", "member-b"]

    def test_three_of_four_policy_support_and_two_vote_support_form_consensus(self):
        predictions = [
            {
                "model_key": "model-1",
                "policy_action": "HOLD",
                "votes": {"member-a": "FOR", "member-b": "AGAINST"},
            },
            {
                "model_key": "model-2",
                "policy_action": "HOLD",
                "votes": {"member-a": "FOR", "member-b": "AGAINST"},
            },
            {
                "model_key": "model-3",
                "policy_action": "HOLD",
                "votes": {"member-a": "FOR", "member-b": "FOR"},
            },
            {
                "model_key": "model-4",
                "policy_action": "HIKE",
                "votes": {"member-a": "AGAINST", "member-b": "FOR"},
            },
        ]

        result = combine_forward_predictions(
            predictions,
            roster_participant_ids=self.roster,
            fallback_action="HOLD",
        )

        self.assertEqual(result["policy"]["action_class"], "HOLD")
        self.assertTrue(result["policy"]["consensus_reached"])
        self.assertEqual(result["policy"]["support_count"], 3)
        self.assertFalse(result["policy"]["fallback_used"])
        self.assertEqual(
            {row["participant_id"]: row["predicted_vote"] for row in result["votes"]},
            {"member-a": "FOR", "member-b": "AGAINST"},
        )
        self.assertEqual(
            next(
                row["against_support_count"]
                for row in result["votes"]
                if row["participant_id"] == "member-b"
            ),
            2,
        )

    def test_two_two_policy_split_uses_declared_fallback(self):
        predictions = [
            {
                "model_key": f"model-{index}",
                "policy_action": "CUT" if index < 2 else "HOLD",
                "votes": {participant_id: "FOR" for participant_id in self.roster},
            }
            for index in range(4)
        ]

        result = combine_forward_predictions(
            predictions,
            roster_participant_ids=self.roster,
            fallback_action="HOLD",
        )

        self.assertEqual(result["policy"]["action_class"], "HOLD")
        self.assertFalse(result["policy"]["consensus_reached"])
        self.assertTrue(result["policy"]["fallback_used"])

    def test_missing_member_vote_fails_closed(self):
        predictions = [
            {
                "model_key": "model-1",
                "policy_action": "HOLD",
                "votes": {"member-a": "FOR"},
            }
        ]

        with self.assertRaisesRegex(ValueError, "participant coverage"):
            combine_forward_predictions(
                predictions,
                roster_participant_ids=self.roster,
                fallback_action="HOLD",
                minimum_policy_support=1,
                minimum_against_support=1,
            )


if __name__ == "__main__":
    unittest.main()
