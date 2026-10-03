"""Repeat real Codex CLI sessions and count capability-manager lifecycle events."""

import argparse
import json
import os
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
QUERIES = {
    "observed": "SELECT count(*) FROM prompt_observations",
    "suggested": "SELECT count(*) FROM decisions WHERE backend='prompt-observer' AND recommendation IS NOT NULL",
    "searched": "SELECT count(*) FROM decisions WHERE backend='prompt-observer' AND activation_status='searched'",
    "activated": "SELECT count(*) FROM decisions WHERE activation_status='activated'",
    "active_leases": "SELECT count(*) FROM active",
    "bound_sessions": "SELECT count(*) FROM session_contexts",
}


def inspect_state(database: Path):
    if not database.is_file():
        return {key: 0 for key in QUERIES}
    with sqlite3.connect(database) as connection:
        return {name: connection.execute(query).fetchone()[0]
                for name, query in QUERIES.items()}


def inspect_events(output: str):
    attempts = []
    denied = 0
    searches = 0
    activations = 0
    unbound_searches = 0
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if item.get("type") != "mcp_tool_call" or item.get("server") != "capability_manager":
            continue
        if event.get("type") == "item.started":
            attempts.append(item.get("tool"))
        elif event.get("type") == "item.completed":
            if "approval" in str(item.get("error", "")).lower():
                denied += 1
            content = (item.get("result") or {}).get("content") or []
            try:
                value = json.loads(content[0]["text"])
            except (IndexError, KeyError, TypeError, json.JSONDecodeError):
                continue
            if not isinstance(value, dict):
                continue
            resolvers = ("resolve_capability", "resolve_static_skill")
            search = value.get("search") if item.get("tool") in resolvers else value
            if item.get("tool") in (*resolvers, "search_capabilities") and isinstance(search, dict):
                searches += 1
                unbound_searches += search.get("context_source") != "session"
            activations += value.get("status") == "activated"
    return {"attempted_tools": attempts, "approval_denials": denied,
            "searches": searches, "activations": activations,
            "unbound_searches": unbound_searches}


def summarize(runs):
    completed = [run for run in runs if run["exit_code"] == 0]
    observed = [run for run in completed if run["state"]["observed"] > 0]
    positives = [run for run in observed if run["expected_activation"]]
    negatives = [run for run in observed if not run["expected_activation"]]
    return {
        "runs": len(runs), "cli_completed": len(completed),
        "hook_observed": len(observed),
        "manager_tool_attempts": sum(len(run["events_summary"]["attempted_tools"]) for run in completed),
        "manager_tool_approval_denials": sum(run["events_summary"]["approval_denials"] for run in completed),
        "manager_searches": sum(run["events_summary"]["searches"] for run in completed),
        "unbound_manager_searches": sum(run["events_summary"]["unbound_searches"] for run in completed),
        "positive_activated": sum(run["events_summary"]["activations"] > 0 for run in positives),
        "positive_total": len(positives),
        "negative_activated": sum(run["events_summary"]["activations"] > 0 for run in negatives),
        "negative_total": len(negatives),
        "unreleased_hook_contexts": sum(run["state"]["bound_sessions"] > 0
                                        for run in completed),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "evals/live-cases.json")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--case", action="append", help="Only run selected case ID; repeatable")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evals/runs")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 20 or not 30 <= args.timeout <= 1200:
        parser.error("repeat must be 1-20 and timeout must be 30-1200 seconds")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    if args.case:
        cases = [case for case in cases if case.get("id") in args.case]
        if len(cases) != len(set(args.case)):
            parser.error("unknown or duplicate case ID")
    for case in cases:
        if not isinstance(case.get("expected_activation"), bool) or not case.get("prompt"):
            parser.error("every case needs prompt and expected_activation")
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    runs = []
    for case in cases:
        for repeat in range(args.repeat):
            run_id = case["id"] + "-" + str(repeat + 1)
            run_dir = output / run_id
            if run_dir.exists():
                run_dir = output / (run_id + "-" + uuid.uuid4().hex[:8])
            work_dir = run_dir / "workspace"
            work_dir.mkdir(parents=True)
            state_dir = run_dir / "state"
            env = os.environ.copy()
            env.update({
                "CAPMGR_DATA_DIR": str(state_dir),
                "CAPMGR_CATALOGS": str(ROOT / "examples/catalog.json"),
                "CAPMGR_POLICY": str(ROOT / "examples/policy.json"),
                "CAPMGR_INCLUDE_CODEX_CATALOG": "0",
            })
            for key in ("CAPMGR_DECIDER_URL", "CAPMGR_STORE_DECISION_TEXT"):
                env.pop(key, None)
            command = ["codex", "exec", "--ephemeral", "--json",
                       "--skip-git-repo-check", "--sandbox", "read-only",
                       "-C", str(work_dir), case["prompt"]]
            started = time.monotonic()
            try:
                process = subprocess.run(command, capture_output=True, text=True,
                                         env=env, timeout=args.timeout)
                exit_code = process.returncode
                stdout, stderr = process.stdout, process.stderr
            except subprocess.TimeoutExpired as exc:
                exit_code = 124
                stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            (run_dir / "events.jsonl").write_text(stdout, encoding="utf-8")
            (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
            run = {"id": run_id, "expected_activation": case["expected_activation"],
                   "exit_code": exit_code, "seconds": round(time.monotonic() - started, 2),
                   "state": inspect_state(state_dir / "state.sqlite3"),
                   "events_summary": inspect_events(stdout),
                   "events": str(run_dir / "events.jsonl")}
            runs.append(run)
            print(json.dumps(run, ensure_ascii=False), flush=True)
    report = {"summary": summarize(runs), "runs": runs}
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
