"""Read discoverable local Codex marketplace packages without installing them."""

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Dict

from .catalog import Entry
from .mcp_bridge import server_configs
from .claude_catalog import _local_version
from .policy import within


def _identifier(name: str, marketplace: str) -> str:
    raw = "codex-" + marketplace + "-" + name
    normalized = re.sub(r"[^a-z0-9._-]+", "-", raw.lower())
    return (normalized if len(normalized) <= 80 else normalized[:65] + "-" +
            hashlib.sha256(normalized.encode()).hexdigest()[:14])


def _manifest(package: Path):
    for path in (package / "plugin.json", package / ".codex-plugin/plugin.json",
                 package / ".claude-plugin/plugin.json"):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def parse_listing(document: Dict[str, object]) -> Dict[str, Entry]:
    entries = {}
    for item in document.get("available", []) + document.get("installed", []):
        if not isinstance(item, dict) or item.get("installed") is not False:
            continue
        name = str(item.get("name") or "")
        if not name or name == "contextual-capability-manager":
            continue
        market = str(item.get("marketplaceName") or "unknown")
        identifier = _identifier(name, market)
        source = item.get("source") or {}
        if not isinstance(source, dict):
            source = {}
        package = None
        package_source = {"type": "native", "plugin_id": item.get("pluginId", name + "@" + market)}
        if source.get("source") == "local" and isinstance(source.get("path"), str):
            package = Path(source["path"]).expanduser().resolve()
        if item.get("installPolicy") not in (None, "AVAILABLE"):
            package = None
        manifest = {}
        skill_paths = []
        servers = {}
        try:
            if package and package.is_dir():
                manifest = _manifest(package) or {}
                if not isinstance(manifest, dict):
                    raise ValueError("invalid plugin manifest")
                skill_paths = list((package / "skills").glob("*/SKILL.md"))
                if (package / "SKILL.md").is_file():
                    skill_paths.append(package / "SKILL.md")
                servers = server_configs(package)
                if skill_paths or servers:
                    package_source = {"type": "directory", "path": str(package)}
        except (OSError, ValueError, TypeError, AttributeError):
            package = None
            manifest = {}
        description = str(item.get("description") or manifest.get("description") or name)
        extensions = manifest.get("extensions") or {}
        extensions = extensions if isinstance(extensions, dict) else {}
        openai = extensions.get("com.openai") or {}
        openai = openai if isinstance(openai, dict) else {}
        interface = openai.get("interface", {})
        if not interface:
            interface = manifest.get("interface", {})
        if not isinstance(interface, dict):
            interface = {}
        description = str(interface.get("longDescription") or interface.get("shortDescription") or description)
        tags = [name, market]
        for skill in skill_paths:
            tags.append(skill.parent.name)
        has_executable = (any("command" in config for config in servers.values()) or
                          bool(manifest.get("hooks") or manifest.get("lspServers")) or
                          bool(package and (package / "hooks").exists()))
        raw = {
            "id": identifier, "name": name, "description": description,
            "kind": "plugin", "publisher": market,
            "version": (_local_version(package, item) if package_source["type"] == "directory"
                        else str(item.get("version") or "0")),
            "tags": tags,
            "source": package_source,
            "permissions": {"execute": has_executable},
        }
        try:
            entries[identifier] = Entry.parse(raw, (package.parent if package else Path.home()) /
                                             "codex-marketplace.json")
        except ValueError:
            continue
    return entries


def listing():
    process = subprocess.run(["codex", "plugin", "list", "--available", "--json"],
                             capture_output=True, text=True, timeout=15, check=True)
    document = json.loads(process.stdout)
    if not isinstance(document, dict) or not isinstance(document.get("available"), list):
        raise ValueError("invalid Codex plugin listing")
    return document


def registered_marketplaces():
    process = subprocess.run(["codex", "plugin", "marketplace", "list", "--json"],
                             capture_output=True, text=True, timeout=15, check=True)
    return json.loads(process.stdout)["marketplaces"]


def snapshot_listing(root: Path, marketplace: str, installed: set):
    """Read an independent snapshot; native installations remain host-owned."""
    paths = [root / ".agents/plugins/marketplace.json", root / ".claude-plugin/marketplace.json"]
    path = next((path for path in paths if path.is_file()), None)
    if path is None:
        raise ValueError("marketplace catalog missing")
    document = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(document, dict) or document.get("name") != marketplace or
            not isinstance(document.get("plugins"), list)):
        raise ValueError("marketplace identity changed")
    available = []
    for item in document["plugins"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        identity = item["name"] + "@" + marketplace
        if identity in installed:
            continue
        source = item.get("source")
        policy = item.get("policy") or {}
        if not isinstance(policy, dict):
            policy = {"installation": "UNAVAILABLE"}
        if isinstance(source, str):
            source = {"source": "local", "path": source}
        if isinstance(source, dict) and source.get("source") == "local":
            package = (root / str(source.get("path", ""))).resolve()
            source = {"source": "local", "path": str(package)} if within(package, root.resolve()) else {}
        available.append({**item, "source": source or {}, "installed": False,
                          "pluginId": identity, "marketplaceName": marketplace,
                          "installPolicy": policy.get("installation", "AVAILABLE")})
    return {"available": available}


def discover() -> Dict[str, Entry]:
    return parse_listing(listing())
