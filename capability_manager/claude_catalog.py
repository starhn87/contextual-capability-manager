"""Discover uninstalled plugins from Claude Code's registered marketplaces."""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

from .catalog import Entry
from .policy import within


SHA = re.compile(r"^[0-9a-fA-F]{40,64}$")


def plugins_dir() -> Path:
    return Path(os.environ.get("CAPMGR_CLAUDE_PLUGINS_DIR") or
                Path.home() / ".claude/plugins").expanduser()


def _id(marketplace: str, name: str) -> str:
    raw = "claude-" + marketplace + "-" + name
    return re.sub(r"[^a-z0-9._-]+", "-", raw.casefold())[:80]


def _installed(root: Path) -> set:
    path = root / "installed_plugins.json"
    try:
        return set(json.loads(path.read_text(encoding="utf-8")).get("plugins", {}))
    except (OSError, ValueError, TypeError):
        return set()


def _local_version(package: Path, listed: dict) -> str:
    manifest = package / ".claude-plugin/plugin.json"
    try:
        declared = json.loads(manifest.read_text(encoding="utf-8")).get("version", "0")
    except (OSError, ValueError, TypeError):
        declared = listed.get("version") or "0"
    files = [manifest, package / "SKILL.md"] + list((package / "skills").glob("*/SKILL.md"))
    fingerprint = hashlib.sha256()
    for path in sorted(files):
        if path.is_file():
            stat = path.stat()
            fingerprint.update(str(path.relative_to(package)).encode())
            fingerprint.update(f"{stat.st_mtime_ns}:{stat.st_size}".encode())
    version = re.sub(r"[^A-Za-z0-9._+-]+", "-", str(declared))[:50].strip(".-") or "0"
    return version + "-" + fingerprint.hexdigest()[:10]


def _entry(item: dict, marketplace: str, catalog_path: Path) -> Optional[Entry]:
    name = item.get("name")
    if not isinstance(name, str) or not name or name == "contextual-capability-manager":
        return None
    source = item.get("source")
    tags = [name, marketplace]
    if isinstance(item.get("category"), str):
        tags.append(item["category"])
    if isinstance(source, str):
        if not source.startswith("./"):
            return None
        root = catalog_path.parent.parent.resolve()
        package = (root / source).resolve()
        if not within(package, root) or not package.is_dir():
            return None
        skill_paths = ([package / "SKILL.md"] +
                       list((package / "skills").glob("*/SKILL.md")))
        if not any(path.is_file() for path in skill_paths):
            return None
        # No executable component is needed for on-demand skill instructions.
        if any((package / marker).exists() for marker in
               ("hooks", ".mcp.json", "mcp.json")):
            return None
        for manifest in (package / "plugin.json", package / ".claude-plugin/plugin.json"):
            if manifest.is_file():
                try:
                    document = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    return None
                if any(document.get(key) for key in ("hooks", "mcpServers", "lspServers")):
                    return None
        tags += [path.parent.name for path in skill_paths if path.is_file()]
        package_source = {"type": "directory", "path": str(package)}
        version = _local_version(package, item)
    elif isinstance(source, dict) and source.get("source") in ("url", "git-subdir"):
        url = source.get("url", "")
        sha = source.get("sha", "")
        parsed = urlparse(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                parsed.password or not SHA.fullmatch(str(sha))):
            return None
        subdir = source.get("path", "") if source.get("source") == "git-subdir" else ""
        subpath = Path(subdir)
        if subpath.is_absolute() or ".." in subpath.parts or "\\" in subdir:
            return None
        package_source = {"type": "git", "url": url, "sha": sha.lower(),
                          "subdir": subdir}
        version = sha.lower()[:12]
    else:
        return None
    raw = {"id": _id(marketplace, name), "name": name,
           "description": str(item.get("description") or ""),
           "kind": "plugin", "publisher": "claude:" + marketplace,
           "version": version, "tags": tags, "source": package_source,
           "permissions": {}}
    try:
        return Entry.parse(raw, catalog_path)
    except ValueError:
        return None


def discover(root: Optional[Path] = None) -> Dict[str, Entry]:
    root = (root or plugins_dir()).expanduser().resolve()
    installed = _installed(root)
    entries = {}
    for catalog_path in sorted((root / "marketplaces").glob("*/.claude-plugin/marketplace.json")):
        try:
            document = json.loads(catalog_path.read_text(encoding="utf-8"))
            marketplace = document["name"]
            items = document["plugins"]
            if not isinstance(marketplace, str) or not isinstance(items, list):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("name"), str) and item["name"] + "@" + marketplace in installed:
                continue
            entry = _entry(item, marketplace, catalog_path)
            if entry:
                entries.setdefault(entry.id, entry)
    return entries
