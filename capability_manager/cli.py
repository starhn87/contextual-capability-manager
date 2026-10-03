import argparse
import json
from pathlib import Path

from . import __version__
from .manager import CapabilityManager, default_config_dir, default_data_dir
from .onboarding import register_source
from .runtime import check_storage, platform, status as runtime_status, storage_id
from .session_summary import receipt
from .store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover and activate capabilities in the current session")
    parser.add_argument("--catalog", action="append", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--expected-storage-id")
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("catalog-add", help="Register one reviewed source for future sessions")
    register.add_argument("--id", required=True)
    register.add_argument("--name", required=True)
    register.add_argument("--description", required=True)
    register.add_argument("--kind", choices=("skill", "plugin", "connector"), required=True)
    register.add_argument("--publisher", required=True)
    register.add_argument("--version", default="1.0.0")
    register.add_argument("--tag", action="append", default=[])
    source = register.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-dir", type=Path)
    source.add_argument("--source-url")
    register.add_argument("--sha256")
    register.add_argument("--allow-executable", action="store_true")
    register.add_argument("--connector-host", action="append", default=[])
    register.add_argument("--allow-read-tool", action="append", default=[])
    register.add_argument("--dry-run", action="store_true")
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
    events = sub.add_parser("events")
    events.add_argument("--days", type=int, default=30)
    events.add_argument("--limit", type=int, default=100)
    events.add_argument("--session")
    event_report = sub.add_parser("event-report")
    event_report.add_argument("--days", type=int, default=30)
    prefetch = sub.add_parser("prefetch")
    prefetch.add_argument("context")
    release = sub.add_parser("release")
    release.add_argument("--session", required=True)
    summary = sub.add_parser("session-report", help="Show installation, usage and cleanup for one session")
    summary.add_argument("--session", required=True)
    summary.add_argument("--format", choices=("markdown", "json"), default="markdown")
    status = sub.add_parser("status", help="Diagnose storage alignment and hook failures")
    status.add_argument("--session")
    args = parser.parse_args()
    if args.command == "catalog-add":
        config_dir = args.config_dir or default_config_dir()
        result = register_source(
            config_dir, identifier=args.id, name=args.name,
            description=args.description, kind=args.kind,
            publisher=args.publisher, version=args.version, tags=args.tag,
            source_dir=args.source_dir, source_url=args.source_url,
            sha256=args.sha256, allow_executable=args.allow_executable,
            connector_hosts=args.connector_host,
            allowed_read_tools=args.allow_read_tool, dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    # Reporting and diagnostics must work even if the current catalog is broken or missing.
    if args.command in ("session-report", "status"):
        directory = args.data_dir or default_data_dir()
        if args.command != "status":
            check_storage(directory, args.expected_storage_id)
        store = Store(directory / "state.sqlite3", platform(), __version__)
        if args.command == "status":
            result = runtime_status(directory, store, args.session)
        else:
            result = receipt(store, args.session, directory)
            result["storage_id"] = storage_id(directory)
            if args.format == "markdown":
                print(result["summary_markdown"])
                return
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    manager = CapabilityManager(args.catalog, args.policy, args.data_dir)
    manager.check_storage(args.expected_storage_id)
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
    elif args.command == "events":
        if not 1 <= args.days <= 365 or not 1 <= args.limit <= 500:
            raise ValueError("invalid event list range")
        result = {"events": manager.store.events(args.days, args.limit, args.session)}
    elif args.command == "event-report":
        result = manager.event_report(args.days)
    elif args.command == "prefetch":
        result = manager.prefetch(args.context)
    else:
        result = manager.release(args.session)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
