import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from decision_memory.materialize_decision_trace_corpus import (
    materialize_decision_trace_corpus,
)


ROOT = Path(__file__).resolve().parents[2]
CORPUS = (
    ROOT
    / "artifacts"
    / "codex_subscription"
    / "decision_trace_50_v5_atomic_monitor_segmentation_v3"
)


class DecisionTraceCorpusMaterializationTests(unittest.TestCase):
    def test_materializes_reviewed_corpus_into_new_derived_database(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "display.sqlite"
            report = materialize_decision_trace_corpus(
                ROOT / "fred_fomc_real.sqlite",
                ROOT / "fomc_simulation.transcript_segmentation_v3_candidate.sqlite",
                CORPUS,
                output,
            )

            self.assertEqual(report["imported_batch_trace_count"], 50)
            self.assertEqual(report["fomc_trace_count"], 51)
            self.assertEqual(report["meeting_outcome_count"], 166)
            self.assertEqual(report["participant_vote_count"], 1736)
            self.assertEqual(report["integrity_check"], "ok")
            self.assertEqual(report["foreign_key_error_count"], 0)
            self.assertTrue(output.is_file())

            connection = sqlite3.connect(output)
            try:
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM decision_trace
                        JOIN decision_case USING (decision_id)
                        WHERE domain = 'fomc'
                        """
                    ).fetchone()[0],
                    51,
                )
            finally:
                connection.close()

    def test_refuses_to_mutate_an_input_database(self):
        candidate = (
            ROOT / "fomc_simulation.transcript_segmentation_v3_candidate.sqlite"
        )
        with self.assertRaisesRegex(ValueError, "output database must be new"):
            materialize_decision_trace_corpus(
                ROOT / "fred_fomc_real.sqlite",
                candidate,
                CORPUS,
                candidate,
            )

    def test_review_gate_blocks_before_copying_database(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "blocked.sqlite"
            with mock.patch(
                "decision_memory.materialize_decision_trace_corpus."
                "validate_human_review_results",
                return_value={"formal_import_gate": "BLOCKED"},
            ):
                with self.assertRaisesRegex(ValueError, "human review gate"):
                    materialize_decision_trace_corpus(
                        ROOT / "fred_fomc_real.sqlite",
                        ROOT
                        / "fomc_simulation.transcript_segmentation_v3_candidate.sqlite",
                        CORPUS,
                        output,
                    )

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
