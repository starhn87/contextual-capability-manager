"""Reload host catalogs and policy without installing native plugins."""

import os
import threading
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from .catalog import load_catalog
from .installer import check_static_skill
from .marketplace_sync import SnapshotCache, registered_source
from .policy import Policy


class CatalogIndex:
    def __init__(self, configuration, directory, codex=False, claude=False):
        self.configuration = configuration
        self.codex = codex
        self.claude = claude
        self.lock = threading.RLock()
        self._codex_listing = {"available": []}
        self._codex_roots = []
        self._listed_at = 0
        self._last_discovery_error = None
        self._sources = []
        self._remote_status = []
        self.snapshots = SnapshotCache(Path(directory) / "marketplace-snapshots")
        self.catalog = {}
        self.policy = None
        self._default_policy = False
        self.updated_at = None
        self._stop = threading.Event()
        self._worker = None

    def refresh(self, local_only=True, sync_remote=False, force=False):
        if sync_remote:
            self.refresh(local_only=local_only, force=force)
            with self.lock:
                sources = list(self._sources)
            statuses = []
            if os.environ.get("CAPMGR_SYNC_MARKETPLACES") != "0":
                deadline = time.monotonic() + 30
                for source in sources:
                    if self._stop.is_set():
                        break
                    if not self.sync_permitted(source):
                        statuses.append({"platform": source.platform, "name": source.name,
                                         "status": "blocked_by_policy"})
                        continue
                    if time.monotonic() >= deadline:
                        statuses.append({"name": source.name, "status": "deferred"})
                        continue
                    try:
                        _, status = self.snapshots.refresh(source, force)
                    except (OSError, ValueError) as exc:
                        status = {"name": source.name, "status": "error", "error_type": type(exc).__name__}
                    statuses.append({"platform": source.platform, **status})
            with self.lock:
                self._remote_status = statuses
            return self.refresh(local_only=True)
        with self.lock:
            if self.codex and not local_only and (force or time.time() - self._listed_at >= 60):
                from .codex_catalog import listing, registered_marketplaces
                try:
                    # Replace both only after a complete listing succeeded.
                    document, roots = listing(), registered_marketplaces()
                    self._codex_listing, self._codex_roots = document, roots
                    self._last_discovery_error = None
                except Exception as exc:
                    self._last_discovery_error = type(exc).__name__
                self._listed_at = time.time()
            registrations = [("codex", item) for item in self._codex_roots]
            if self.claude:
                from .claude_catalog import registered_marketplaces
                registrations += [("claude", item) for item in registered_marketplaces()]
            self._sources = [source for platform, item in registrations
                             for source in [registered_source(platform, item)] if source]
            snapshots = {platform: {} for platform in ("codex", "claude")}
            for source in self._sources:
                current = self.snapshots.current(source)
                if current:
                    snapshots[source.platform][source.name] = current
            paths, policy_path, default_policy = self.configuration()
            # Invalid/missing policy fails closed, rather than keeping old grants.
            policy = Policy.load(policy_path)
            self._default_policy = default_policy
            catalog = load_catalog(paths)
            discovered = {}
            if self.claude:
                from .claude_catalog import discover
                discovered.update(discover(snapshots=snapshots["claude"]))
            if self.codex:
                from .codex_catalog import parse_listing, snapshot_listing
                document = dict(self._codex_listing)
                available = list(document.get("available", []))
                installed = {item.get("pluginId") for item in document.get("installed", [])
                             if isinstance(item, dict) and item.get("installed") is True}
                host_policies = {item.get("pluginId") or (str(item.get("name")) + "@" +
                                 str(item.get("marketplaceName"))): item.get("installPolicy")
                                 for item in document.get("available", []) if isinstance(item, dict)}
                for item in self._codex_roots:
                    name = item.get("name")
                    root = snapshots["codex"].get(name) or (Path(item["root"]) if item.get("root") else None)
                    if not root:
                        continue
                    try:
                        fresh = snapshot_listing(root, name, installed)
                    except (OSError, ValueError, TypeError):
                        continue
                    for entry in fresh["available"]:
                        restriction = host_policies.get(entry["pluginId"])
                        if restriction not in (None, "AVAILABLE"):
                            entry["installPolicy"] = restriction
                    available = [entry for entry in available if entry.get("marketplaceName") != name]
                    available += fresh["available"]
                document["available"] = available
                discovered.update(parse_listing(document))
            if default_policy:
                publishers, roots, hosts = set(policy.publishers), set(policy.local_roots), set(policy.download_hosts)
                for entry in discovered.values():
                    if entry.source["type"] == "directory":
                        path = Path(entry.source["path"])
                        try:
                            check_static_skill(path)
                        except (OSError, ValueError, PermissionError):
                            continue
                        roots.add(path.resolve())
                    elif entry.source["type"] == "git":
                        hosts.add(urlparse(entry.source["url"]).hostname)
                    else:
                        continue
                    publishers.add(entry.publisher)
                policy = replace(policy, publishers=sorted(publishers), local_roots=sorted(roots),
                                 download_hosts=sorted(hosts))
            for identifier, entry in discovered.items():
                catalog.setdefault(identifier, entry)
            self.catalog, self.policy = catalog, policy
            self.updated_at = time.time()
            return catalog, policy

    def status(self):
        with self.lock:
            eligible = 0
            for entry in self.catalog.values():
                try:
                    self.policy.check_entry(entry)
                    eligible += 1
                except (PermissionError, ValueError):
                    pass
            remote = []
            attempts = {(item.get("platform"), item["name"]): item for item in self._remote_status}
            for source in self._sources:
                state = self.snapshots._state(source)
                value = {"platform": source.platform, "name": source.name, "status": "pending", **state,
                         "has_snapshot": self.snapshots.current(source) is not None}
                if value["status"] == "fresh" and time.time() - value.get("updated_at", 0) >= self.snapshots.ttl:
                    value["status"] = "stale"
                if os.environ.get("CAPMGR_SYNC_MARKETPLACES") == "0":
                    value["status"] = "disabled"
                elif not self.sync_permitted(source):
                    value["status"] = "blocked_by_policy"
                elif attempts.get((source.platform, source.name), {}).get("status") == "deferred":
                    value["status"] = "deferred"
                elif attempts.get((source.platform, source.name), {}).get("status") == "refreshing":
                    value["status"] = "refreshing"
                elif attempts.get((source.platform, source.name), {}).get("status") == "error":
                    value.update({key: attempts[(source.platform, source.name)][key]
                                  for key in ("status", "error_type")})
                remote.append(value)
            return {"updated_at": self.updated_at, "known_entries": len(self.catalog),
                    "policy_eligible_entries": eligible, "codex_auto_discovery": self.codex,
                    "claude_auto_discovery": self.claude, "local_poll_seconds": 60,
                    "remote_ttl_seconds": self.snapshots.ttl,
                    "remote_sync_enabled": os.environ.get("CAPMGR_SYNC_MARKETPLACES") != "0",
                    "discovery_error": self._last_discovery_error,
                    "discovery_pending": self.codex and not self._listed_at,
                    "remote_sources": [source.name for source in self._sources],
                    "remote_results": remote,
                    "background_running": bool(self._worker and self._worker.is_alive())}

    def sync_permitted(self, source):
        with self.lock:
            return self._default_policy or urlparse(source.url).hostname in self.policy.download_hosts

    def start(self, refresh):
        if not (self.codex or self.claude) or self._worker:
            return
        def work():
            while not self._stop.is_set():
                try:
                    refresh(local_only=False, sync_remote=True)
                except Exception as exc:
                    with self.lock:
                        self._last_discovery_error = type(exc).__name__
                if self._stop.wait(60):
                    return
        self._worker = threading.Thread(target=work, name="capability-catalog-refresh", daemon=True)
        self._worker.start()

    def stop(self):
        self._stop.set()
        if self._worker:
            # Finish the current bounded CLI/Git operation before the MCP process exits.
            self._worker.join(timeout=32)
