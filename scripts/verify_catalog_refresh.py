"""Verify same-process catalog changes with isolated data and optional live reads."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from capability_manager.manager import CapabilityManager
from capability_manager.marketplace_sync import SnapshotCache, registered_source


def write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def fixture_plugin(market, name, marker):
    package = market / "plugins" / name
    write(package / ".codex-plugin/plugin.json", {"name": name, "version": "1.0.0",
                                                "description": name + " team release template"})
    skill = package / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(marker, encoding="utf-8")


def fixture_catalog(market, names):
    write(market / ".agents/plugins/marketplace.json", {"name": "verification-team", "plugins": [
        {"name": name, "source": {"source": "local", "path": "./plugins/" + name}}
        for name in names]})


def environment(root, platform="codex"):
    env = {key: value for key, value in os.environ.items() if not key.startswith("CAPMGR_")}
    env.update(CAPMGR_PLATFORM=platform, CAPMGR_DATA_DIR=str(root / "data"),
               CAPMGR_CONFIG_DIR=str(root / "config"), CAPMGR_SYNC_MARKETPLACES="0")
    return env


def same_process(root):
    market = root / "market"
    fixture_plugin(market, "zebra", "ZEBRA-MARKER-ONLY-IN-FILE")
    fixture_catalog(market, ["zebra"])
    binary = root / "bin" / "codex"
    binary.parent.mkdir()
    listing = {"available": [], "installed": []}
    registrations = {"marketplaces": [{"name": "verification-team", "root": str(market)}]}
    initialized_marker = root / "initialize-returned"
    binary.write_text("#!" + sys.executable + "\nimport json,sys,time\nfrom pathlib import Path\n" +
                      "deadline=time.monotonic()+5\n" +
                      "while not Path(" + repr(str(initialized_marker)) + ").exists():\n" +
                      "    if time.monotonic()>deadline: raise RuntimeError('initialization blocked by discovery')\n" +
                      "    time.sleep(0.01)\nprint(json.dumps(" +
                      repr(listing) + " if '--available' in sys.argv else " + repr(registrations) + "))\n")
    binary.chmod(0o700)
    env = environment(root)
    env["PATH"] = str(binary.parent) + os.pathsep + env.get("PATH", os.defpath)
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts/serve.py")], cwd=str(ROOT),
                               env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    sequence = 0
    def request(method, params=None):
        nonlocal sequence
        sequence += 1
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": sequence, "method": method,
                                       "params": params or {}}) + "\n")
        process.stdin.flush()
        message = json.loads(process.stdout.readline())
        if "error" in message:
            raise RuntimeError(message["error"])
        return message["result"]
    def call(name, arguments):
        response = request("tools/call", {"name": name, "arguments": arguments})
        if response.get("isError"):
            raise RuntimeError(response["content"])
        return json.loads(response["content"][0]["text"])
    try:
        initialized = request("initialize")
        initialized_marker.touch()
        first = call("read_static_skill", {"task": "Create our Zebra release template"})
        fixture_plugin(market, "otter", "OTTER-MARKER-ONLY-IN-FILE")
        fixture_catalog(market, ["zebra", "otter"])
        second = call("read_static_skill", {"task": "Create our Otter release template"})
        fixture_catalog(market, ["otter"])
        removed = call("read_static_skill", {"task": "Create our Zebra"})
        status = call("capability_runtime_status", {"session_id": "isolated-verification"})
        receipt = call("capability_session_summary", {"session_id": "isolated-verification",
                                                     "expected_storage_id": status["storage_id"]})
        checks = {"initial_marker_delivered": "ZEBRA-MARKER-ONLY-IN-FILE" in json.dumps(first),
                  "initialize_before_discovery_completion": initialized_marker.is_file(),
                  "new_marker_delivered_same_process": "OTTER-MARKER-ONLY-IN-FILE" in json.dumps(second),
                  "removed_candidate_absent": removed["status"] == "no_local_static_match",
                  "no_preparation_or_access": all(value == 0 for value in receipt["counts"].values())}
        assert all(checks.values()), checks
        return {"source_version": initialized["serverInfo"]["version"], "checks": checks,
                "catalog_status": status["catalog_status"], "receipt": receipt["counts"]}
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        errors = process.stderr.read()
        if process.returncode or errors:
            raise RuntimeError("isolated MCP process failed: " + errors[:500])


def live_codex(root):
    with patch.dict(os.environ, environment(root), clear=True):
        manager = CapabilityManager()
        return manager.runtime_status("isolated-native-listing-verification")


def live_git(root):
    # Exercise one public source already registered by the user; no new registration.
    registry = json.loads((Path.home() / ".claude/plugins/known_marketplaces.json").read_text())
    item = {**registry["contextual-capabilities"], "name": "contextual-capabilities"}
    source = registered_source("claude", item)
    assert source is not None and "starhn87/contextual-capability-manager" in source.url
    cache = SnapshotCache(root / "git-snapshots")
    snapshot, result = cache.refresh(source)
    assert snapshot is not None and result["status"] == "fresh", result
    return {**result, "catalog_exists": (snapshot / ".claude-plugin/marketplace.json").is_file()}


def user_hashes():
    paths = [Path.home() / relative for relative in (".codex/config.toml", ".codex/hooks.json",
             ".claude/plugins/installed_plugins.json", ".claude/plugins/known_marketplaces.json")]
    cache = Path.home() / ".codex/plugins/cache/local-capabilities/contextual-capability-manager"
    if cache.is_dir():
        paths += [path for path in cache.rglob("*") if path.is_file()]
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-codex", action="store_true", help="Read the native configured plugin catalog")
    parser.add_argument("--live-git", action="store_true", help="Fetch the public, already registered manager catalog")
    args = parser.parse_args()
    before = user_hashes()
    report = {"scope": "source-MCP and isolated fixtures; not a deployed-plugin or model-behavior test"}
    with tempfile.TemporaryDirectory(prefix="capmgr-catalog-verification-") as directory:
        root = Path(directory)
        report["same_process"] = same_process(root)
        if args.live_codex:
            report["native_codex"] = live_codex(root / "native")
        if args.live_git:
            report["registered_public_git"] = live_git(root)
    report["user_configuration_and_manager_cache_unchanged"] = before == user_hashes()
    report["temporary_data_removed"] = not root.exists()
    assert report["user_configuration_and_manager_cache_unchanged"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
