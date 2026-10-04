import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from capability_manager.manager import context_key, default_data_dir
from capability_manager.runtime import platform, storage_id, record_hook_failure
from capability_manager.store import Store
from capability_manager import __version__


def main():
    event = json.load(sys.stdin)
    session_id = event.get("session_id", "")
    context = event.get("cwd", "")
    if not isinstance(session_id, str) or not session_id or len(session_id) > 200:
        return
    try:
        directory = default_data_dir()
        store = Store(directory / "state.sqlite3", platform(), __version__)
        store.set_session_context(session_id, context_key(context))
        store.add_event(session_id, "session_started", "bound")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
            "additionalContext": "Capability manager session_id: " + session_id +
            ". Capability manager storage_id: " + storage_id(directory) +
            ". Pass session_id and expected_storage_id to manager calls. Always show a compact manager "
            "receipt in the final answer for a completed request, including when no capability was used. "
            "Use a verified empty receipt from UserPromptSubmit without extra tool calls when nothing "
            "was prepared afterward. After finishing capability use, call release_capability_session "
            "and show its summary_markdown. If no verified receipt is available, report the status as "
            "unverified instead of assuming zero installations or successful cleanup."}}))
    except Exception as exc:
        record_hook_failure("SessionStart", session_id, exc, Path(__file__).resolve().parent.parent)
        print(json.dumps({"systemMessage": "Capability manager SessionStart failed (" +
                          type(exc).__name__ + "). Its session context was not bound."}))


if __name__ == "__main__":
    main()
