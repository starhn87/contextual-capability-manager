import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from capability_manager.codex_catalog import parse_listing
from capability_manager.manager import CapabilityManager
from capability_manager.marketplace_sync import Source, SnapshotCache, registered_source
from capability_manager.mcp_server import _dispatch, handle
from test_manager import Fixture


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def add_plugin(root, name, text):
    package = root / "plugins" / name
    write(package / ".codex-plugin/plugin.json", {"name": name, "version": "1.0.0",
                                                "description": name + " team release template"})
    skill = package / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(text, encoding="utf-8")
    return package


def catalog(root, names):
    write(root / ".agents/plugins/marketplace.json", {"name": "team", "plugins": [
        {"name": name, "description": name + " release template", "source": {
            "source": "local", "path": "./plugins/" + name}} for name in names]})


class CatalogRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.market = self.root / "market"
        self.package = add_plugin(self.market, "zebra", "ZEBRA-ONLY-IN-FILE")
        catalog(self.market, ["zebra"])
        self.env = {"CAPMGR_PLATFORM": "codex", "CAPMGR_CONFIG_DIR": str(self.root / "config"),
                    "CAPMGR_DATA_DIR": str(self.root / "data"), "CAPMGR_SYNC_MARKETPLACES": "0"}
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.listing = patch("capability_manager.codex_catalog.listing", return_value={
            "available": [], "installed": []}).start()
        self.registrations = patch("capability_manager.codex_catalog.registered_marketplaces", return_value=[
            {"name": "team", "root": str(self.market)}]).start()
        self.addCleanup(patch.stopall)

    def test_codex_discovers_and_uses_an_implicit_task_without_configuration(self):
        manager = CapabilityManager()
        self.assertTrue(manager.index.status()["codex_auto_discovery"])
        self.assertEqual(manager.catalog_paths, [])
        result = manager.read_static("Create our Zebra release template")
        self.assertEqual(result["status"], "instructions_ready")
        self.assertIn("ZEBRA-ONLY-IN-FILE", result["capability"]["skills"][0]["instructions"])
        self.assertEqual(manager.session_summary("chat")["counts"]["active"], 0)

    def test_same_manager_reads_additions_changes_and_removals_without_processes(self):
        manager = CapabilityManager()
        old_version = manager.catalog["codex-team-zebra"].version
        (self.package / "skills/zebra/SKILL.md").write_text("ZEBRA-UPDATED-CONTENT")
        add_plugin(self.market, "otter", "OTTER-ONLY-IN-FILE")
        catalog(self.market, ["zebra", "otter"])
        with patch("subprocess.run", side_effect=AssertionError("lookup started a process")):
            updated = manager.read_static("Create our Zebra release template")
            added = manager.read_static("Create our Otter release template")
            catalog(self.market, ["otter"])
            removed = manager.read_static("Create our Zebra")
        self.assertIn("ZEBRA-UPDATED-CONTENT", updated["capability"]["skills"][0]["instructions"])
        self.assertNotEqual(old_version, updated["capability"]["version"])
        self.assertIn("OTTER-ONLY-IN-FILE", added["capability"]["skills"][0]["instructions"])
        self.assertEqual(removed["status"], "no_local_static_match")
        self.assertNotIn("codex-team-zebra", manager.catalog)

    def test_new_policy_revokes_default_discovery_and_malformed_policy_fails_closed(self):
        manager = CapabilityManager()
        self.assertEqual(manager.read_static("Create our Zebra release template")["status"], "instructions_ready")
        config = self.root / "config"
        write(config / "catalog.json", {"capabilities": []})
        write(config / "policy.json", {"publishers": [], "kinds": ["skill", "plugin"]})
        self.assertEqual(manager.read_static("Create our Zebra release template")["status"], "no_local_static_match")
        (config / "policy.json").write_text("broken")
        with self.assertRaises(ValueError):
            manager.read_static("Create our Zebra release template")

    def test_explicit_disable_skips_native_cli(self):
        with patch.dict(os.environ, {"CAPMGR_INCLUDE_CODEX_CATALOG": "0"}):
            manager = CapabilityManager()
        self.assertFalse(manager.index.status()["codex_auto_discovery"])
        self.listing.assert_not_called()

    def test_discovery_failure_retains_last_listing_and_exposes_error(self):
        manager = CapabilityManager()
        self.listing.side_effect = OSError("PRIVATE-ERROR-DETAILS")
        status = manager.refresh_catalog(local_only=False, force=True)
        self.assertEqual(status["discovery_error"], "OSError")
        self.assertNotIn("PRIVATE-ERROR-DETAILS", json.dumps(status))
        self.assertIn("codex-team-zebra", manager.catalog)

    def test_removed_registry_is_not_kept_as_a_stale_candidate(self):
        manager = CapabilityManager()
        self.registrations.return_value = []
        manager.refresh_catalog(local_only=False, force=True)
        self.assertNotIn("codex-team-zebra", manager.catalog)

    def test_native_references_are_visible_without_installation_or_authentication(self):
        records = {"available": [{"name": "mail-service", "marketplaceName": "remote",
            "version": "1", "installed": False, "source": {"source": "remote"},
            "description": "mail service messages account connector"}], "installed": []}
        self.listing.return_value = records
        manager = CapabilityManager()
        self.assertIn("codex-remote-mail-service", manager.catalog)
        with patch.object(manager.installer, "install", side_effect=AssertionError("native installed")):
            result = manager.search("Need a connector for mail service messages")
        self.assertIsNone(result["recommendation"])
        blocked = next(item for item in result["unavailable"] if item["id"] == "codex-remote-mail-service")
        self.assertIn("platform installation", blocked["reason"])

    def test_local_reread_cannot_remove_a_host_installation_restriction(self):
        self.listing.return_value = {"available": [{"name": "zebra", "marketplaceName": "team",
            "pluginId": "zebra@team", "installed": False, "installPolicy": "NOT_AVAILABLE",
            "source": {"source": "local", "path": str(self.package)}}]}
        manager = CapabilityManager()
        result = manager.read_static("Create our Zebra release template")
        self.assertEqual(result["status"], "no_local_static_match")
        self.assertEqual(manager.catalog["codex-team-zebra"].source["type"], "native")

    def test_many_native_candidates_do_not_flood_a_lookup_result(self):
        self.listing.return_value = {"available": [{"name": "native-" + str(i), "installed": False,
            "marketplaceName": "remote", "source": {"source": "remote"}} for i in range(100)]}
        manager = CapabilityManager()
        result = manager.read_static("Create our Zebra release template")
        self.assertEqual(result["status"], "instructions_ready")
        self.assertLessEqual(len(result["unavailable"]), 8)
        self.assertEqual(manager.index.status()["known_entries"], 101)

    def test_catalog_change_does_not_replace_a_prepared_session_version(self):
        manager = CapabilityManager()
        result = manager.resolve_static("Create our Zebra release template", "chat")
        self.assertEqual(result["status"], "activated")
        old_entry = manager.session_entries[("chat", "codex-team-zebra")]
        (self.package / "skills/zebra/SKILL.md").write_text("CHANGED-ZEBRA-VERSION")
        manager.refresh_catalog()
        self.assertNotEqual(old_entry.version, manager.catalog[old_entry.id].version)
        result = manager.resolve_static("Create our Zebra release template", "chat")
        self.assertEqual(result["status"], "requires_review")
        self.assertEqual(manager.session_entries[("chat", old_entry.id)], old_entry)
        manager.release("chat")
        self.assertEqual(manager.session_entries, {})
        self.assertEqual(manager.resolve_static("Create our Zebra release template", "chat")["status"], "activated")
        manager.release("chat")

    def test_refresh_tool_checks_storage_before_any_discovery(self):
        manager = CapabilityManager()
        before = self.listing.call_count
        with self.assertRaises(ValueError):
            _dispatch(manager, "refresh_capability_catalog", {"expected_storage_id": "0" * 24})
        self.assertEqual(self.listing.call_count, before)
        response = handle(manager, {"id": 1, "method": "tools/list"})
        tool = next(tool for tool in response["result"]["tools"] if tool["name"] == "refresh_capability_catalog")
        self.assertFalse(tool["annotations"]["readOnlyHint"])

    def test_background_refresh_runs_and_stops_without_a_user_command(self):
        manager = CapabilityManager()
        done = threading.Event()
        def refresh(**kwargs):
            add_plugin(self.market, "otter", "BACKGROUND-OTTER")
            catalog(self.market, ["zebra", "otter"])
            manager.refresh_catalog(**kwargs)
            done.set()
        manager.index.start(refresh)
        try:
            self.assertTrue(done.wait(3))
            self.assertIn("codex-team-otter", manager.catalog)
        finally:
            manager.index.stop()
        self.assertFalse(manager.index.status()["background_running"])

    def test_custom_policy_blocks_remote_sync_without_network_access(self):
        config = self.root / "config"
        write(config / "catalog.json", {"capabilities": []})
        write(config / "policy.json", {"publishers": [], "download_hosts": []})
        self.registrations.return_value[0]["marketplaceSource"] = {
            "sourceType": "git", "source": "https://example.test/team.git"}
        with patch.dict(os.environ, {"CAPMGR_SYNC_MARKETPLACES": "1"}):
            manager = CapabilityManager()
            with patch("capability_manager.marketplace_sync.checkout", side_effect=AssertionError("download")):
                status = manager.refresh_catalog(sync_remote=True)
        self.assertEqual(status["remote_results"][0]["status"], "blocked_by_policy")

    def test_claude_reads_a_new_registered_local_marketplace_in_the_same_session(self):
        plugins = self.root / "claude"
        with patch.dict(os.environ, {"CAPMGR_PLATFORM": "claude", "CAPMGR_CLAUDE_PLUGINS_DIR": str(plugins)}):
            manager = CapabilityManager()
            self.assertEqual(manager.catalog, {})
            # The local registry may point outside ~/.claude/plugins/marketplaces.
            write(self.market / ".claude-plugin/marketplace.json", {"name": "team", "plugins": [
                {"name": "zebra", "description": "Zebra release template", "source": "./plugins/zebra"}]})
            write(plugins / "known_marketplaces.json", {"team": {
                "source": {"source": "directory", "path": str(self.market)},
                "installLocation": str(self.market)}})
            result = manager.resolve_static("Create our Zebra release template", "claude-chat")
            self.assertEqual(result["status"], "activated")
            self.assertIn("ZEBRA-ONLY-IN-FILE", result["capability"]["skills"][0]["instructions"])
            manager.release("claude-chat")
            write(plugins / "known_marketplaces.json", {})
            manager.refresh_catalog()
            self.assertEqual(manager.catalog, {})

    def test_claude_unregister_excludes_a_leftover_marketplace_directory(self):
        plugins = self.root / "claude"
        market = plugins / "marketplaces/team"
        add_plugin(market, "zebra", "REGISTERED-ZEBRA")
        write(market / ".claude-plugin/marketplace.json", {"name": "team", "plugins": [
            {"name": "zebra", "description": "Zebra release template", "source": "./plugins/zebra"}]})
        write(plugins / "known_marketplaces.json", {"team": {"installLocation": str(market)}})
        with patch.dict(os.environ, {"CAPMGR_PLATFORM": "claude", "CAPMGR_CLAUDE_PLUGINS_DIR": str(plugins)}):
            manager = CapabilityManager()
            self.assertIn("claude-team-zebra", manager.catalog)
            write(plugins / "known_marketplaces.json", {})
            manager.refresh_catalog()
            self.assertTrue(market.is_dir())
            self.assertEqual(manager.catalog, {})

    def test_claude_changed_local_marketplace_identity_is_not_accepted(self):
        plugins = self.root / "claude"
        write(plugins / "known_marketplaces.json", {"team": {"installLocation": str(self.market)}})
        path = self.market / ".claude-plugin/marketplace.json"
        listing = {"name": "team", "plugins": [{"name": "zebra", "description": "Zebra release template",
                                                 "source": "./plugins/zebra"}]}
        write(path, listing)
        with patch.dict(os.environ, {"CAPMGR_PLATFORM": "claude", "CAPMGR_CLAUDE_PLUGINS_DIR": str(plugins)}):
            manager = CapabilityManager()
            self.assertIn("claude-team-zebra", manager.catalog)
            write(path, {**listing, "name": "different-publisher"})
            manager.refresh_catalog()
            self.assertEqual(manager.catalog, {})


class MarketplaceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = self.root / "origin"
        self.origin.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        add_plugin(self.origin, "zebra", "PINNED-ZEBRA")
        catalog(self.origin, ["zebra"])
        self.commit()
        self.cache = SnapshotCache(self.root / "snapshots")
        self.source = Source("codex", "team", "https://example.test/team.git")
        self.original_run = subprocess.run

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.origin), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "Update fixture")
        return self.git("rev-parse", "HEAD")

    def transport(self, args, **kwargs):
        # Exercise actual Git; redirect only the test HTTPS remote to an isolated fixture.
        args = list(args)
        if "fetch" in args:
            args[args.index(self.source.url)] = str(self.origin)
            args[1:1] = ["-c", "protocol.file.allow=always"]
        return self.original_run(args, **kwargs)

    def test_remote_updates_are_isolated_immutable_and_rate_limited(self):
        before = hashlib.sha256((self.origin / ".agents/plugins/marketplace.json").read_bytes()).hexdigest()
        with patch("capability_manager.marketplace_sync.subprocess.run", side_effect=self.transport) as process:
            first, status = self.cache.refresh(self.source)
            self.assertEqual(status["status"], "fresh")
            calls = process.call_count
            self.assertEqual(self.cache.refresh(self.source)[0], first)
            self.assertEqual(process.call_count, calls)
        self.assertEqual(hashlib.sha256((self.origin / ".agents/plugins/marketplace.json").read_bytes()).hexdigest(), before)
        add_plugin(self.origin, "otter", "PINNED-OTTER")
        catalog(self.origin, ["zebra", "otter"])
        self.commit()
        with patch("capability_manager.marketplace_sync.subprocess.run", side_effect=self.transport):
            second, status = self.cache.refresh(self.source, force=True)
        self.assertNotEqual(first, second)
        self.assertFalse((first / "plugins/otter").exists())
        self.assertTrue((second / "plugins/otter").exists())

    def test_network_failure_keeps_last_valid_snapshot_with_visible_error(self):
        with patch("capability_manager.marketplace_sync.subprocess.run", side_effect=self.transport):
            good, _ = self.cache.refresh(self.source)
        with patch("capability_manager.marketplace_sync.subprocess.run", side_effect=OSError("PRIVATE-TOKEN")) as process:
            current, status = self.cache.refresh(self.source, force=True)
            self.assertEqual(self.cache.refresh(self.source)[0], good)
            self.assertEqual(process.call_count, 1)
        self.assertEqual(current, good)
        self.assertEqual(status["status"], "error")
        self.assertNotIn("PRIVATE-TOKEN", json.dumps(status))
        self.assertFalse(any(self.cache.directory.glob("*/pending-*")))

    def test_invalid_remote_catalog_cannot_replace_last_good_snapshot(self):
        with patch("capability_manager.marketplace_sync.subprocess.run", side_effect=self.transport):
            good, _ = self.cache.refresh(self.source)
        write(self.origin / ".agents/plugins/marketplace.json", {"name": "another-owner", "plugins": []})
        self.commit()
        with patch("capability_manager.marketplace_sync.subprocess.run", side_effect=self.transport):
            current, status = self.cache.refresh(self.source, force=True)
        self.assertEqual(current, good)
        self.assertEqual(status["status"], "error")

    def test_other_session_refresh_does_not_download_or_overwrite_its_state(self):
        parent = self.cache.directory / self.source.key
        parent.mkdir(parents=True)
        lock = parent / "sync.lock"
        lock.touch()
        with patch("subprocess.run", side_effect=AssertionError("duplicate download")):
            current, status = self.cache.refresh(self.source)
        self.assertIsNone(current)
        self.assertEqual(status["status"], "refreshing")
        self.assertTrue(lock.exists())
        self.assertFalse((parent / "state.json").exists())

    def test_crashed_worker_lock_expires_and_is_cleaned_up(self):
        parent = self.cache.directory / self.source.key
        parent.mkdir(parents=True)
        lock = parent / "sync.lock"
        lock.touch()
        os.utime(lock, (1, 1))
        with patch("capability_manager.marketplace_sync.subprocess.run", side_effect=self.transport):
            current, status = self.cache.refresh(self.source)
        self.assertIsNotNone(current)
        self.assertEqual(status["status"], "fresh")
        self.assertFalse(lock.exists())

    def test_only_registered_https_sources_are_supported(self):
        for url in ("http://example.test/repo", "ssh://example.test/repo", "file:///tmp/repo",
                    "https://name:password@example.test/repo", "https://example.test/repo?token=secret"):
            with self.subTest(url=url), patch("subprocess.run", side_effect=AssertionError("network called")):
                with self.assertRaises(ValueError):
                    self.cache.refresh(Source("codex", "team", url))
        self.assertIsNone(registered_source("claude", {"name": "team", "autoUpdate": False,
            "source": {"source": "github", "repo": "team/catalog"}}))

    def test_new_remote_catalog_reaches_the_existing_manager_without_changing_host_files(self):
        host = self.root / "host-checkout"
        shutil.copytree(self.origin, host)
        host_catalog = (host / ".agents/plugins/marketplace.json").read_bytes()
        registrations = [{"name": "team", "root": str(host), "marketplaceSource": {
            "sourceType": "git", "source": self.source.url}}]
        env = {"CAPMGR_PLATFORM": "codex", "CAPMGR_CONFIG_DIR": str(self.root / "empty-config"),
               "CAPMGR_DATA_DIR": str(self.root / "runtime")}
        with patch.dict(os.environ, env, clear=True), \
             patch("capability_manager.codex_catalog.listing", return_value={"available": []}), \
             patch("capability_manager.codex_catalog.registered_marketplaces", return_value=registrations):
            manager = CapabilityManager()
            manager.index.snapshots = self.cache
            add_plugin(self.origin, "otter", "NEW-REMOTE-OTTER")
            catalog(self.origin, ["zebra", "otter"])
            self.commit()
            with patch("capability_manager.marketplace_sync.subprocess.run", side_effect=self.transport):
                status = manager.refresh_catalog(sync_remote=True, force=True)
            self.assertEqual(status["remote_results"][0]["status"], "fresh")
            with patch("subprocess.run", side_effect=AssertionError("read started a process")):
                result = manager.read_static("Create our Otter release template")
            self.assertEqual(result["status"], "instructions_ready")
            self.assertIn("NEW-REMOTE-OTTER", result["capability"]["skills"][0]["instructions"])
            self.assertEqual(manager.session_summary("chat")["counts"]["new_packages"], 0)
        self.assertEqual((host / ".agents/plugins/marketplace.json").read_bytes(), host_catalog)
        self.assertFalse((host / "plugins/otter").exists())


if __name__ == "__main__":
    unittest.main()
