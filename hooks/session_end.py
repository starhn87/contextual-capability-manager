import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from capability_manager.manager import CapabilityManager


def main():
    event = json.load(sys.stdin)
    session_id = event.get("session_id")
    if session_id:
        CapabilityManager().release(session_id)


if __name__ == "__main__":
    main()
