import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "model_spec/reaction_feature_contract_hackathon_r5_v1.json"
REACTION_MODEL = ROOT / "artifacts/reaction/pooled_ordered_logit_v1.json"


class ReactionFeatureContractTests(unittest.TestCase):
    def test_hackathon_contract_explicitly_approves_baa10y_proxy(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        reaction = json.loads(REACTION_MODEL.read_text(encoding="utf-8"))

        self.assertEqual(contract["decision_status"], "APPROVED")
        self.assertEqual(contract["scope"], "hackathon_r5")
        self.assertEqual(contract["planned_feature"], "NFCI")
        self.assertEqual(contract["approved_proxy_feature"], "credit_spread_baa10y")
        self.assertEqual(contract["approved_proxy_series_id"], "BAA10Y")
        self.assertTrue(contract["disclosure_required"])
        self.assertFalse(contract["existing_bundle_rerun_required"])
        self.assertEqual(contract["approved_feature_set"], reaction["features"])
        self.assertNotIn("NFCI", reaction["features"])
        self.assertEqual(
            contract["reaction_model"]["artifact_sha256"],
            hashlib.sha256(REACTION_MODEL.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            contract["reaction_model"]["model_id"], reaction["model_id"]
        )


if __name__ == "__main__":
    unittest.main()
