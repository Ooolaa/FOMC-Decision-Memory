import unittest

from decision_memory.evaluation import classification_metrics


class EvaluationMetricTests(unittest.TestCase):
    def test_action_metrics_include_macro_f1_and_mae(self):
        metrics = classification_metrics(
            actual=["CUT", "HOLD", "HIKE", "HIKE"],
            predicted=["CUT", "HOLD", "HOLD", "HIKE"],
        )

        self.assertEqual(metrics["accuracy"], 0.75)
        self.assertAlmostEqual(metrics["macro_f1"], (1.0 + 2 / 3 + 2 / 3) / 3)
        self.assertEqual(metrics["mean_absolute_action_error"], 0.25)


if __name__ == "__main__":
    unittest.main()
