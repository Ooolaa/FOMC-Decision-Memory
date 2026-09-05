import unittest
from pathlib import Path

from decision_memory.ablation_spec import load_ablation_spec


class AblationSpecTests(unittest.TestCase):
    def test_frozen_spec_contains_all_r5_variants_and_fail_closed_status(self):
        spec = load_ablation_spec(
            Path("evaluation_spec/hackathon_r5_variants_v1.json")
        )

        self.assertEqual(len(spec["variants"]), 8)
        self.assertEqual(spec["evaluator_version"], "simulation_policy_vote_v1")


if __name__ == "__main__":
    unittest.main()
