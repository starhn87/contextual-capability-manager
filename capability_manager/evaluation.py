"""Reproducible, metadata-only evaluation of capability routing."""

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .catalog import Entry
from .decision import DecisionRouter, lexical_decision
from .policy import Policy


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "evals/cases.json"


def load_cases(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError("unsupported evaluation version")
    specs = data.get("capabilities")
    if specs is None and isinstance(data.get("capabilities_from"), str):
        source = (path.parent / data["capabilities_from"]).resolve()
        specs = json.loads(source.read_text(encoding="utf-8")).get("capabilities")
        data["capabilities"] = specs
    cases = data.get("cases")
    if not isinstance(specs, list) or not specs or not isinstance(cases, list) or not cases:
        raise ValueError("evaluation needs capabilities and cases")
    ids = [item["id"] for item in specs]
    if len(set(ids)) != len(ids) or any(identifier in ("none", "other") for identifier in ids):
        raise ValueError("duplicate or reserved capability id")
    case_ids = [case["id"] for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("duplicate case id")
    for case in cases:
        expected = case.get("expected")
        if expected not in ids and expected not in ("none", "other"):
            raise ValueError("unknown expected capability for " + case["id"])
        available = case.get("available_ids", ids)
        if not isinstance(available, list) or any(identifier not in ids for identifier in available):
            raise ValueError("unknown available capability for " + case["id"])
        if expected in ids and expected not in available:
            raise ValueError("expected capability is unavailable for " + case["id"])
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            raise ValueError("empty evaluation prompt for " + case["id"])
    return data


def _entries(specs: List[Dict[str, Any]], path: Path) -> Dict[str, Entry]:
    entries = {}
    for spec in specs:
        metadata = {**spec, "publisher": "evaluation", "version": "1",
                    "source": {"type": "directory", "path": "metadata-only"},
                    "permissions": {}}
        entry = Entry.parse(metadata, path)
        entries[entry.id] = entry
    return entries


def score_cases(cases: List[Dict[str, Any]], decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(cases) != len(decisions):
        raise ValueError("one decision is required per case")
    counts = {"cases": len(cases), "need_true_positives": 0, "need_false_positives": 0,
              "need_false_negatives": 0, "need_true_negatives": 0,
              "correct_selection": 0, "false_activations": 0,
              "missed_capabilities": 0, "wrong_capabilities": 0,
              "unknown_autoselections": 0, "unknown_need_detected": 0}
    rows = []
    by_category: Dict[str, Dict[str, int]] = {}
    for case, decision in zip(cases, decisions):
        expected = case["expected"]
        recommendation = decision.get("recommendation")
        predicted_need = float(decision.get("need_probability") or 0) >= 0.85
        actual_need = expected != "none"
        if predicted_need and actual_need:
            counts["need_true_positives"] += 1
        elif predicted_need:
            counts["need_false_positives"] += 1
        elif actual_need:
            counts["need_false_negatives"] += 1
        else:
            counts["need_true_negatives"] += 1
        correct = (recommendation is None if expected in ("none", "other")
                   else recommendation == expected)
        counts["correct_selection"] += int(correct)
        if expected == "none" and recommendation is not None:
            counts["false_activations"] += 1
        elif expected == "other":
            counts["unknown_autoselections"] += int(recommendation is not None)
            counts["unknown_need_detected"] += int(predicted_need)
        elif expected not in ("none", "other") and recommendation is None:
            counts["missed_capabilities"] += 1
        elif expected not in ("none", "other") and recommendation != expected:
            counts["wrong_capabilities"] += 1
        category = case.get("category", "uncategorized")
        bucket = by_category.setdefault(category, {"cases": 0, "correct_selection": 0,
                                                   "need_detected": 0})
        bucket["cases"] += 1
        bucket["correct_selection"] += int(correct)
        bucket["need_detected"] += int(predicted_need)
        rows.append({"id": case["id"], "category": category, "expected": expected,
                     "recommendation": recommendation, "predicted_need": predicted_need,
                     "need_probability": decision.get("need_probability"),
                     "confidence": decision.get("confidence"), "correct_selection": correct,
                     "candidates": decision.get("candidates", [])})
    precision_denominator = counts["need_true_positives"] + counts["need_false_positives"]
    recall_denominator = counts["need_true_positives"] + counts["need_false_negatives"]
    metrics = {
        **counts,
        "need_precision": counts["need_true_positives"] / precision_denominator
        if precision_denominator else None,
        "need_recall": counts["need_true_positives"] / recall_denominator
        if recall_denominator else None,
        "selection_accuracy": counts["correct_selection"] / len(cases) if cases else None,
        "known_capability_accuracy": (
            (sum(row["correct_selection"] for row in rows
                 if row["expected"] not in ("none", "other")) /
             sum(case["expected"] not in ("none", "other") for case in cases))
            if any(case["expected"] not in ("none", "other") for case in cases) else None
        ),
    }
    return {"metrics": metrics, "by_category": by_category, "cases": rows}


def run(path: Path = DEFAULT_CASES, backend: str = "lexical",
        policy_path: Path = ROOT / "examples/policy.json",
        model: Optional[str] = None) -> Dict[str, Any]:
    dataset = load_cases(path)
    entries = _entries(dataset["capabilities"], path)
    if backend not in ("lexical", "configured"):
        raise ValueError("backend must be lexical or configured")
    if backend == "configured" and not os.environ.get("CAPMGR_DECIDER_URL"):
        raise ValueError("configured backend requires CAPMGR_DECIDER_URL")
    router = DecisionRouter(Policy.load(policy_path), model=model) if backend == "configured" else None
    decisions = []
    for case in dataset["cases"]:
        available = case.get("available_ids", list(entries))
        candidates = [entries[identifier] for identifier in available]
        decision = (router.rank(case["prompt"], candidates)
                    if router else lexical_decision(case["prompt"], candidates))
        decisions.append(decision)
    result = score_cases(dataset["cases"], decisions)
    resolved = path.resolve()
    try:
        result["dataset"] = str(resolved.relative_to(ROOT))
    except ValueError:
        result["dataset"] = str(resolved)
    result["backend"] = backend
    if router:
        result["model"] = router.model
    result["version"] = __version__
    result["decision_backends"] = sorted({item.get("backend", "unknown") for item in decisions})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate capability routing without installing packages")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--backend", choices=["lexical", "configured"], default="lexical")
    parser.add_argument("--policy", type=Path, default=ROOT / "examples/policy.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.cases, args.backend, args.policy)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
