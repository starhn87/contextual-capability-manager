import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from capability_manager.manager import default_data_dir
from capability_manager.runtime import platform, storage_id, record_hook_failure
from capability_manager.session_summary import receipt, save_receipt
from capability_manager.store import Store
from capability_manager import __version__


def main():
    event = json.load(sys.stdin)
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not session_id or len(session_id) > 200:
        return
    try:
        directory = default_data_dir()
        store = Store(directory / "state.sqlite3", platform(), __version__)
        released = store.release(session_id)
        for identifier in released:
            store.add_event(session_id, "capability_released", "revoked", capability_id=identifier)
        store.add_event(session_id, "session_released", "completed")
        summary = receipt(store, session_id, directory)
        summary["storage_id"] = storage_id(directory)
        save_receipt(summary, directory)
    except Exception as exc:
        record_hook_failure("SessionEnd", session_id, exc, Path(__file__).resolve().parent.parent)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
