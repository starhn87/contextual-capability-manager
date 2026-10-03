import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capability_manager.evaluation import DEFAULT_CASES, ROOT, load_cases, run, score_cases
from capability_manager.shadow import compare
from scripts.run_live_codex_eval import inspect_events


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
        for path in (DEFAULT_CASES, ROOT / "evals/challenge.json", ROOT / "evals/boundaries.json"):
            with self.subTest(dataset=path.name):
                report = run(path)
                metrics = report["metrics"]
                self.assertEqual(metrics["false_activations"], 0)
                self.assertEqual(metrics["wrong_capabilities"], 0)
                self.assertEqual(metrics["unknown_autoselections"], 0)
                self.assertGreaterEqual(metrics["need_recall"], 0.8)

    def test_review_boundaries_choose_the_expected_capability(self):
        report = run(ROOT / "evals/boundaries.json")
        self.assertEqual(report["metrics"]["correct_selection"], report["metrics"]["cases"])

    def test_live_parser_counts_static_and_gateway_searches_equally(self):
        for tool in ("resolve_capability", "resolve_static_skill"):
            event = {"type": "item.completed", "item": {
                "type": "mcp_tool_call", "server": "capability_manager", "tool": tool,
                "result": {"content": [{"type": "text", "text": json.dumps({
                    "status": "activated", "search": {"context_source": "argument"}})}]}}}
            with self.subTest(tool=tool):
                result = inspect_events(json.dumps(event))
                self.assertEqual(result["searches"], 1)
                self.assertEqual(result["unbound_searches"], 1)
                self.assertEqual(result["activations"], 1)

    def test_shadow_comparison_never_calls_remote_without_explicit_flag(self):
        with patch.dict(os.environ, {"CAPMGR_DECIDER_URL": "http://127.0.0.1:8000/v1/systemone"}):
            with patch("capability_manager.decision.open_no_redirect",
                       side_effect=AssertionError("remote request is forbidden")):
                result = compare(models=["jev-latest", "kev-latest"])
        self.assertEqual(result["mode"], "shadow_only")
        self.assertEqual([item["status"] for item in result["models"]],
                         ["not_run_remote_disabled", "not_run_remote_disabled"])

    def test_shadow_comparison_scores_both_models_and_rejects_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            policy = Path(temp) / "policy.json"
            policy.write_text(json.dumps({"decision_hosts": ["127.0.0.1"]}))
            seen = []

            def fake_open(request, timeout):
                model = json.loads(request.data)["model"]
                seen.append(model)
                if model == "kev-latest":
                    raise TimeoutError("test timeout")
                answer = {"answers": {"needed": {"noul": 0.9},
                                       "capability": {"choice": "none", "confidence": 0.9}}}
                return io.BytesIO(json.dumps(answer).encode())

            with patch.dict(os.environ, {"CAPMGR_DECIDER_URL": "http://127.0.0.1:8000/v1/systemone"}):
                with patch("capability_manager.decision.open_no_redirect", side_effect=fake_open):
                    result = compare(DEFAULT_CASES, policy, ["jev-latest", "kev-latest"], True)
        self.assertEqual([item["status"] for item in result["models"]],
                         ["evaluated", "backend_unavailable"])
        self.assertIn("metrics", result["models"][0])
        self.assertNotIn("metrics", result["models"][1])
        self.assertEqual(len(seen), 2 * result["cases"])
        self.assertEqual(set(seen), {"jev-latest", "kev-latest"})


if __name__ == "__main__":
    unittest.main()
