import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from capability_manager.manager import CapabilityManager


def main():
    event = json.load(sys.stdin)
    session_id = event.get("session_id", "")
    context = event.get("cwd", "")
    try:
        manager = CapabilityManager()
        if session_id:
            manager.bind_session_context(session_id, context)
        manager.prefetch(context)
    except Exception:
        pass
    if session_id:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
            "additionalContext": "Capability manager session_id: " + session_id +
            ". Use this ID for capability manager calls in this chat."}}))


if __name__ == "__main__":
    main()
