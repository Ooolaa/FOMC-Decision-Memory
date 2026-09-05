import unittest

from decision_memory.offline_simulator import validate_simulation_output


class OfflineSimulatorTests(unittest.TestCase):
    def test_chair_proposes_and_vote_totals_must_balance(self):
        output = {
            "schema_version": "simulation_output_v1",
            "meeting_id": "FOMC-2022-03-15",
            "synthetic": True,
            "profiles": [
                {"participant_id": "chair", "display_name": "Chair", "is_chair": True},
                {"participant_id": "member", "display_name": "Member", "is_chair": False},
            ],
            "discussion": [
                {"participant_id": "chair", "synthetic_text": "I propose a hike."},
                {"participant_id": "member", "synthetic_text": "I support the proposal."},
            ],
            "final_proposal": {
                "proposer_participant_id": "chair",
                "action_class": "HIKE",
                "rationale": "Pooled baseline signal.",
            },
            "votes": [
                {"participant_id": "chair", "choice": "FOR"},
                {"participant_id": "member", "choice": "FOR"},
            ],
        }

        report = validate_simulation_output(output)

        self.assertEqual(report["for_count"], 2)
        self.assertEqual(report["against_count"], 0)

        output["final_proposal"]["proposer_participant_id"] = "member"
        with self.assertRaisesRegex(ValueError, "Chair"):
            validate_simulation_output(output)

    def test_opaque_case_id_requires_an_explicit_expected_id(self):
        output = {
            "schema_version": "simulation_output_v1",
            "meeting_id": "case-1779e5250cf73d69bcfe",
            "synthetic": True,
            "profiles": [
                {"participant_id": "chair", "display_name": "Chair", "is_chair": True},
                {"participant_id": "member", "display_name": "Member", "is_chair": False},
            ],
            "discussion": [
                {"participant_id": "chair", "synthetic_text": "I propose a hold."},
                {"participant_id": "member", "synthetic_text": "I support it."},
            ],
            "final_proposal": {
                "proposer_participant_id": "chair",
                "action_class": "HOLD",
                "rationale": "Balanced risks.",
            },
            "votes": [
                {"participant_id": "chair", "choice": "FOR"},
                {"participant_id": "member", "choice": "FOR"},
            ],
        }

        with self.assertRaisesRegex(ValueError, "Simulation schema violation"):
            validate_simulation_output(output)
        report = validate_simulation_output(
            output,
            expected_meeting_id="case-1779e5250cf73d69bcfe",
        )
        self.assertEqual(report["participant_count"], 2)


if __name__ == "__main__":
    unittest.main()
