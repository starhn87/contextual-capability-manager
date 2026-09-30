"""Read discoverable local Codex marketplace packages without installing them."""

import json
import re
import subprocess
from pathlib import Path
from typing import Dict

from .catalog import Entry
from .mcp_bridge import server_configs


def _identifier(name: str, marketplace: str) -> str:
    raw = "codex-" + marketplace + "-" + name
    return re.sub(r"[^a-z0-9._-]+", "-", raw.lower())[:80]


def _manifest(package: Path):
    for path in (package / "plugin.json", package / ".codex-plugin/plugin.json"):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def parse_listing(document: Dict[str, object]) -> Dict[str, Entry]:
    entries = {}
    for item in document.get("available", []) + document.get("installed", []):
        if item.get("installed") is not False:
            continue
        source = item.get("source") or {}
        if source.get("source") != "local":
            continue
        package = Path(source.get("path", "")).expanduser().resolve()
        if not package.is_dir():
            continue
        manifest = _manifest(package)
        if not manifest:
            continue
        skill_paths = list((package / "skills").glob("*/SKILL.md"))
        try:
            servers = server_configs(package)
        except (ValueError, json.JSONDecodeError):
            continue
        if not skill_paths and not servers:
            continue
        name = str(item.get("name") or manifest.get("name") or "")
        market = str(item.get("marketplaceName") or "unknown")
        identifier = _identifier(name, market)
        if not identifier:
            continue
        description = str(manifest.get("description") or "")
        interface = manifest.get("extensions", {}).get("com.openai", {}).get("interface", {})
        if not interface:
            interface = manifest.get("interface", {})
        description = str(interface.get("longDescription") or interface.get("shortDescription") or description)
        tags = [name, market]
        for skill in skill_paths:
            tags.append(skill.parent.name)
        has_executable = any("command" in config for config in servers.values())
        raw = {
            "id": identifier, "name": name, "description": description,
            "kind": "plugin", "publisher": market,
            "version": str(item.get("version") or manifest.get("version") or "0"),
            "tags": tags,
            "source": {"type": "directory", "path": str(package)},
            "permissions": {"execute": has_executable},
        }
        entries[identifier] = Entry.parse(raw, package.parent / "codex-marketplace.json")
    return entries


def discover() -> Dict[str, Entry]:
    try:
        process = subprocess.run(["codex", "plugin", "list", "--available", "--json"],
                                 capture_output=True, text=True, timeout=15, check=True)
        return parse_listing(json.loads(process.stdout))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return {}
