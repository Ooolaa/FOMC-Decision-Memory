import hashlib
import json
import sqlite3
import unittest
from pathlib import Path

from decision_memory.saved_run_reevaluation import reevaluate_saved_batch


ROOT = Path(__file__).resolve().parents[2]


class VoteLabelRepairAuditTests(unittest.TestCase):
    def test_candidate_audit_matches_frozen_databases_and_vote_gate(self):
        audit = json.loads(
            (ROOT / "artifacts/evaluation/vote_label_repair_audit_v1.json").read_text(
                encoding="utf-8"
            )
        )
        formal = ROOT / audit["formal_database"]["path"]
        candidate = ROOT / audit["candidate_database"]["path"]
        self.assertEqual(
            hashlib.sha256(formal.read_bytes()).hexdigest(),
            audit["formal_database"]["sha256"],
        )
        self.assertEqual(
            hashlib.sha256(candidate.read_bytes()).hexdigest(),
            audit["candidate_database"]["sha256"],
        )

        app = sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro", uri=True)
        app.execute("ATTACH DATABASE ? AS formal", (str(formal),))
        try:
            self.assertEqual(app.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(app.execute("PRAGMA foreign_key_check").fetchall(), [])
            scope = audit["validated_scope"]
            self.assertEqual(
                app.execute("SELECT COUNT(*) FROM participant_vote").fetchone()[0],
                scope["candidate_vote_count"],
            )
            self.assertEqual(
                app.execute("SELECT COUNT(*) FROM meeting_participant").fetchone()[0],
                scope["meeting_participant_count"],
            )
            differences = audit["candidate_difference_from_formal"]
            self.assertEqual(
                app.execute(
                    """
                    SELECT COUNT(*)
                    FROM main.participant_vote candidate
                    LEFT JOIN formal.participant_vote old
                      USING (meeting_id, participant_id, vote_round)
                    WHERE old.participant_id IS NULL
                    """
                ).fetchone()[0],
                differences["vote_keys_added"],
            )
            self.assertEqual(
                app.execute(
                    """
                    SELECT COUNT(*)
                    FROM formal.participant_vote old
                    LEFT JOIN main.participant_vote candidate
                      USING (meeting_id, participant_id, vote_round)
                    WHERE candidate.participant_id IS NULL
                    """
                ).fetchone()[0],
                differences["formal_vote_keys_missing_from_candidate"],
            )
        finally:
            app.close()

        gate = json.loads(
            (ROOT / audit["known_roster_vote_gate"]["artifact"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            gate["app_database_sha256"],
            audit["candidate_database"]["sha256"],
        )
        self.assertEqual(gate["label_coverage"], 1.0)
        self.assertEqual(gate["gate"]["required_roster_coverage"], 1.0)
        self.assertEqual(
            gate["gate"]["reference_f1"],
            audit["known_roster_vote_gate"]["reference_f1"],
        )
        diagnostic = json.loads(
            (ROOT / audit["saved_output_diagnostic"]["artifact"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(diagnostic["case_count"], 45)
        self.assertEqual(diagnostic["gate"]["roster_coverage"], 1.0)
        self.assertEqual(diagnostic["gate"]["label_coverage"], 1.0)
        self.assertFalse(diagnostic["gate"]["metric_only_pass"])
        self.assertTrue(diagnostic["input_lineage_stale"])
        self.assertFalse(diagnostic["gate"]["promotion_eligible"])

    def test_saved_frozen_outputs_are_re_evaluated_without_api_calls(self):
        result = reevaluate_saved_batch(
            ROOT / "fomc_simulation.vote_labels_fixed_candidate.sqlite",
            ROOT / "artifacts/codex_subscription/frozen45_v1/batch_status.json",
            ROOT / "artifacts/evaluation/frozen_45_vote_baselines_candidate_v1.json",
        )

        self.assertEqual(result["case_count"], 45)
        self.assertEqual(result["platform_api_calls_for_reevaluation"], 0)
        self.assertEqual(result["aggregate"]["dissent_f1"], 0.31818181818181823)
        self.assertTrue(result["input_lineage_stale"])
        self.assertFalse(result["gate"]["metric_only_pass"])
        self.assertFalse(result["gate"]["promotion_eligible"])


if __name__ == "__main__":
    unittest.main()
