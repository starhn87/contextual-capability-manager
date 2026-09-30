import argparse
import json
from pathlib import Path

from .manager import CapabilityManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover and activate capabilities in the current session")
    parser.add_argument("--catalog", action="append", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--data-dir", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search")
    search.add_argument("task")
    search.add_argument("--context", default="")
    search.add_argument("--session")
    search.add_argument("--turn")
    resolve = sub.add_parser("resolve")
    resolve.add_argument("task")
    resolve.add_argument("--session", required=True)
    resolve.add_argument("--context", default="")
    resolve.add_argument("--turn")
    activate = sub.add_parser("activate")
    activate.add_argument("capability_id")
    activate.add_argument("--session", required=True)
    call = sub.add_parser("call")
    call.add_argument("capability_id")
    call.add_argument("server_name")
    call.add_argument("tool_name")
    call.add_argument("--session", required=True)
    call.add_argument("--arguments", default="{}")
    record = sub.add_parser("record")
    record.add_argument("capability_id")
    record.add_argument("--session", required=True)
    record.add_argument("--context", default="")
    record.add_argument("--success", action="store_true")
    feedback = sub.add_parser("feedback")
    feedback.add_argument("decision_id")
    feedback.add_argument("correct_capability_id")
    feedback.add_argument("--session", required=True)
    decisions = sub.add_parser("decisions")
    decisions.add_argument("--days", type=int, default=30)
    decisions.add_argument("--limit", type=int, default=50)
    decisions.add_argument("--all", action="store_true")
    report = sub.add_parser("report")
    report.add_argument("--days", type=int, default=30)
    prefetch = sub.add_parser("prefetch")
    prefetch.add_argument("context")
    release = sub.add_parser("release")
    release.add_argument("--session", required=True)
    args = parser.parse_args()
    manager = CapabilityManager(args.catalog, args.policy, args.data_dir)
    if args.command == "search":
        result = manager.search(args.task, args.context, args.session, args.turn)
    elif args.command == "resolve":
        result = manager.resolve(args.task, args.session, args.context, args.turn)
    elif args.command == "activate":
        result = manager.activate(args.capability_id, args.session)
    elif args.command == "call":
        result = manager.invoke(args.session, args.capability_id, args.server_name,
                                args.tool_name, json.loads(args.arguments))
    elif args.command == "record":
        result = manager.record_outcome(args.session, args.capability_id, args.context, args.success)
    elif args.command == "feedback":
        result = manager.record_decision_feedback(args.session, args.decision_id,
                                                   args.correct_capability_id)
    elif args.command == "decisions":
        result = manager.list_decisions(args.days, args.limit, not args.all)
    elif args.command == "report":
        result = manager.decision_report(args.days)
    elif args.command == "prefetch":
        result = manager.prefetch(args.context)
    else:
        result = manager.release(args.session)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
