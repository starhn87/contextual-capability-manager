"""Shared storage resolution and content-free hook failure diagnostics."""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

from . import __version__


def _absolute(value: str) -> Optional[Path]:
    if not value or "${" in value:
        return None
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else None


def platform() -> str:
    explicit = os.environ.get("CAPMGR_PLATFORM", "")
    if explicit in ("codex", "claude", "cli"):
        return explicit
    if _absolute(os.environ.get("PLUGIN_DATA", "")) or os.environ.get("PLUGIN_ROOT"):
        return "codex"
    if os.environ.get("CLAUDE_PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return "claude"
    return "cli"


def configure_data_dir(plugin_root: Path) -> Optional[Path]:
    for key in ("CAPMGR_DATA_DIR", "PLUGIN_DATA", "CLAUDE_PLUGIN_DATA"):
        candidate = _absolute(os.environ.get(key, ""))
        if candidate:
            return candidate
        if key == "CAPMGR_DATA_DIR" and os.environ.get(key) and "${" not in os.environ[key]:
            raise ValueError("CAPMGR_DATA_DIR must be an absolute path")
    # Preserve the established Claude cache convention when its variable was not expanded.
    root = plugin_root.resolve()
    cache = root.parent.parent.parent
    if platform() == "claude" and cache.name == "cache" and cache.parent.name == "plugins":
        data = cache.parent / "data" / (root.parent.name + "-" + root.parent.parent.name)
        os.environ["CLAUDE_PLUGIN_DATA"] = str(data)
        return data
    return None


def data_dir(plugin_root: Path) -> Path:
    return configure_data_dir(plugin_root) or Path.home() / ".local/share/contextual-capability-manager"


def storage_id(directory: Path) -> str:
    return hashlib.sha256(str(directory.resolve()).encode("utf-8")).hexdigest()[:24]


def check_storage(directory: Path, expected_storage_id: Optional[str] = None) -> None:
    if expected_storage_id is None:
        return
    if not isinstance(expected_storage_id, str) or not re.fullmatch(r"[a-f0-9]{24}", expected_storage_id):
        raise ValueError("invalid expected_storage_id")
    if expected_storage_id != storage_id(directory):
        raise ValueError("capability manager hooks and MCP use different state stores; check runtime status")


def status(directory: Path, store, session_id: Optional[str] = None):
    if session_id is not None and (not isinstance(session_id, str) or not session_id or len(session_id) > 200):
        raise ValueError("invalid session id")
    return {"platform": store.platform, "plugin_version": store.plugin_version,
            "storage_id": storage_id(directory),
            "session_context_bound": bool(session_id and store.session_context(session_id)),
            "recent_hook_errors": hook_failures(directory)}


def record_hook_failure(phase: str, session_id: str, error: Exception, plugin_root: Path) -> None:
    record = {"phase": phase, "session_id": session_id[:200], "error_type": type(error).__name__,
              "platform": platform(), "plugin_version": __version__, "created_at": int(time.time())}
    # Neither exception messages, filesystem paths, nor source prompt text are logged.
    sys.stderr.write("capability-manager hook failure: " + json.dumps(record) + "\n")
    try:
        directory = data_dir(plugin_root)
        directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(directory / "hook-errors.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except (OSError, ValueError):
        pass


def hook_failures(directory: Path, limit: int = 5):
    try:
        with (directory / "hook-errors.jsonl").open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 8192))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
        records = []
        for line in lines:
            try:
                record = json.loads(line)
                if isinstance(record, dict):
                    allowed = ("phase", "session_id", "error_type", "platform", "plugin_version", "created_at")
                    records.append({key: record[key] for key in allowed if key in record})
            except ValueError:
                continue
        return records[-limit:]
    except OSError:
        return []
