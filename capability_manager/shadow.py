"""Compare labeled routing cases without changing live capability decisions."""

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from .evaluation import DEFAULT_CASES, ROOT, load_cases, run


def compare(path: Path = DEFAULT_CASES,
            policy_path: Path = ROOT / "examples/policy.json",
            models: List[str] = None, allow_remote: bool = False) -> Dict[str, Any]:
    models = models or ["jev-latest", "kev-latest"]
    if len(set(models)) != len(models) or any(not value or len(value) > 80 for value in models):
        raise ValueError("models must be distinct nonempty names of at most 80 characters")
    dataset = load_cases(path)
    baseline = run(path, "lexical", policy_path)
    report = {"dataset": baseline["dataset"], "cases": len(dataset["cases"]),
              "mode": "shadow_only", "baseline": {
                  "metrics": baseline["metrics"], "cases": baseline["cases"]},
              "models": []}
    if not allow_remote:
        report["models"] = [{"model": model, "status": "not_run_remote_disabled"}
                            for model in models]
        return report
    if not os.environ.get("CAPMGR_DECIDER_URL"):
        raise ValueError("CAPMGR_DECIDER_URL is required for remote shadow evaluation")
    for model in models:
        started = time.monotonic()
        result = run(path, "configured", policy_path, model=model)
        valid = result["decision_backends"] == ["system-one"]
        arm = {"model": model, "status": "evaluated" if valid else "backend_unavailable",
               "decision_backends": result["decision_backends"],
               "elapsed_ms": int((time.monotonic()-started)*1000)}
        if valid:
            arm["metrics"] = result["metrics"]
            arm["cases"] = result["cases"]
        report["models"].append(arm)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Jev/Kev on reviewed cases without activation")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--policy", type=Path, default=ROOT / "examples/policy.json")
    parser.add_argument("--models", nargs="+", default=["jev-latest", "kev-latest"])
    parser.add_argument("--allow-remote", action="store_true",
                        help="Send the case prompts and short metadata to the configured endpoint")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = compare(args.cases, args.policy, args.models, args.allow_remote)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
