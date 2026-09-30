import json
import tempfile
import unittest
from pathlib import Path

from capability_manager.evaluation import DEFAULT_CASES, ROOT, load_cases, run, score_cases


class EvaluationTests(unittest.TestCase):
    def test_dataset_is_valid_and_has_direct_indirect_negative_unknown_cases(self):
        data = load_cases(DEFAULT_CASES)
        self.assertEqual({case["category"] for case in data["cases"]},
                         {"direct", "indirect", "negative", "unknown"})
        self.assertEqual({entry["kind"] for entry in data["capabilities"]},
                         {"skill", "plugin", "connector"})
        challenge = load_cases(ROOT / "evals/challenge.json")
        self.assertEqual(len(challenge["capabilities"]), len(data["capabilities"]))
        self.assertEqual({case["category"] for case in challenge["cases"]},
                         {"indirect", "negative"})

    def test_counts_keep_abstention_and_need_detection_distinct(self):
        cases = [{"id": "a", "expected": "none"},
                 {"id": "b", "expected": "other"},
                 {"id": "c", "expected": "meeting-brief"}]
        decisions = [{"recommendation": None, "need_probability": 0},
                     {"recommendation": None, "need_probability": 1},
                     {"recommendation": None, "need_probability": 0}]
        metrics = score_cases(cases, decisions)["metrics"]
        self.assertEqual(metrics["correct_selection"], 2)
        self.assertEqual(metrics["missed_capabilities"], 1)
        self.assertEqual(metrics["need_true_positives"], 1)
        self.assertEqual(metrics["need_false_negatives"], 1)
        self.assertEqual(metrics["unknown_need_detected"], 1)
        self.assertEqual(metrics["known_capability_accuracy"], 0.0)

    def test_evaluation_does_not_write_to_the_dataset_or_install_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "cases.json"
            source.write_text(DEFAULT_CASES.read_text(encoding="utf-8"), encoding="utf-8")
            before = source.read_bytes()
            report = run(source)
            self.assertEqual(report["metrics"]["cases"], len(json.loads(before)["cases"]))
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(sorted(path.name for path in Path(temp).iterdir()), ["cases.json"])

    def test_labeled_routing_regression_gate(self):
        for path in (DEFAULT_CASES, ROOT / "evals/challenge.json"):
            with self.subTest(dataset=path.name):
                report = run(path)
                metrics = report["metrics"]
                self.assertEqual(metrics["false_activations"], 0)
                self.assertEqual(metrics["wrong_capabilities"], 0)
                self.assertEqual(metrics["unknown_autoselections"], 0)
                self.assertGreaterEqual(metrics["need_recall"], 0.8)


if __name__ == "__main__":
    unittest.main()
