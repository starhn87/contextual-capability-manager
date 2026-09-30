"""Explicitly register approved capability sources for future sessions."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .catalog import Entry
from .mcp_bridge import server_configs


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=".capmgr-", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def register_source(data_dir: Path, *, identifier: str, name: str, description: str,
                    kind: str, publisher: str, version: str, tags: List[str],
                    source_dir: Optional[Path] = None, source_url: Optional[str] = None,
                    sha256: Optional[str] = None, allow_executable: bool = False,
                    connector_hosts: Optional[List[str]] = None,
                    allowed_read_tools: Optional[List[str]] = None,
                    dry_run: bool = False) -> Dict[str, Any]:
    """Add one reviewed source; never grant write permissions or fetch on registration."""
    if (source_dir is None) == (source_url is None):
        raise ValueError("select exactly one source directory or HTTPS ZIP URL")
    if not name.strip() or not description.strip() or not publisher.strip():
        raise ValueError("name, description and publisher are required")
    data_dir = data_dir.expanduser().resolve()
    catalog_path = data_dir / "catalog.json"
    policy_path = data_dir / "policy.json"
    if catalog_path.exists() != policy_path.exists():
        raise ValueError("catalog.json and policy.json must be configured together")
    hosts = connector_hosts or []
    for host in hosts:
        if not host or "/" in host or ":" in host:
            raise ValueError("connector hosts must be hostnames without scheme or port")
    if source_dir is not None:
        directory = source_dir.expanduser().resolve()
        if not directory.is_dir():
            raise ValueError("source directory does not exist")
        skill_files = ([directory / "SKILL.md"] +
                       list((directory / "skills").glob("*/SKILL.md")))
        servers = server_configs(directory)
        if not any(path.is_file() for path in skill_files) and not servers:
            raise ValueError("source has no skill or MCP server")
        if not any((directory / marker).is_file() for marker in
                   ("SKILL.md", "plugin.json", ".codex-plugin/plugin.json",
                    ".claude-plugin/plugin.json")):
            raise ValueError("source has no skill or plugin manifest")
        needs_execute = any(config.get("type") == "stdio" or "command" in config
                            for config in servers.values())
        if needs_execute and not allow_executable:
            raise PermissionError("stdio MCP source needs --allow-executable for this ID")
        source = {"type": "directory", "path": str(directory)}
        download_host = None
    else:
        parsed = urlparse(source_url or "")
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("remote source must use HTTPS")
        if not sha256 or len(sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha256):
            raise ValueError("remote source needs a 64-digit SHA-256 digest")
        source = {"type": "https_zip", "url": source_url, "sha256": sha256.lower()}
        download_host = parsed.hostname
    raw = {"id": identifier, "name": name, "description": description,
           "kind": kind, "publisher": publisher, "version": version,
           "tags": tags, "source": source,
           "permissions": {"execute": allow_executable}}
    Entry.parse(raw, catalog_path)
    catalog = _read_json(catalog_path, {"capabilities": []})
    policy = _read_json(policy_path, {
        "publishers": [], "kinds": [], "local_roots": [], "download_hosts": [],
        "connector_hosts": [], "decision_hosts": [], "allowed_secret_env": [],
        "allowed_read_tools": [], "allow_executable": False,
        "executable_ids": [], "allow_external_write": False,
        "allowed_write_tools": [],
    })
    entries = catalog.get("capabilities")
    if not isinstance(entries, list) or any(item.get("id") == identifier for item in entries):
        raise ValueError("invalid catalog or duplicate capability id")
    entries.append(raw)
    for key, value in (("publishers", publisher), ("kinds", kind)):
        if value not in policy[key]:
            policy[key].append(value)
    if source_dir is not None:
        if str(directory) not in policy["local_roots"]:
            policy["local_roots"].append(str(directory))
    elif download_host not in policy["download_hosts"]:
        policy["download_hosts"].append(download_host)
    if allow_executable and identifier not in policy["executable_ids"]:
        policy["executable_ids"].append(identifier)
    for host in hosts:
        if host not in policy["connector_hosts"]:
            policy["connector_hosts"].append(host)
    for tool in allowed_read_tools or []:
        if not tool.startswith(identifier + ":"):
            raise ValueError("read tool must be scoped as ID:tool-name")
        if tool not in policy["allowed_read_tools"]:
            policy["allowed_read_tools"].append(tool)
    result = {"catalog": str(catalog_path), "policy": str(policy_path),
              "entry": raw, "policy_changes": {
                  key: policy[key] for key in ("publishers", "kinds", "local_roots",
                                          "download_hosts", "connector_hosts",
                                          "executable_ids", "allowed_read_tools")},
              "dry_run": dry_run}
    if not dry_run:
        _write_json(policy_path, policy)
        _write_json(catalog_path, catalog)
    return result
