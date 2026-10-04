import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from capability_manager.manager import CapabilityManager
from capability_manager.mcp_server import TOOLS, handle
from capability_manager.mcp_bridge import HttpConnection, StdioConnection
from capability_manager.runtime import configure_data_dir, platform, storage_id
from capability_manager.session_summary import receipt, save_receipt
from capability_manager.store import Store
from test_manager import Fixture, MOCK_MCP, ROOT, write_json


class SessionSummaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = Fixture(temporary.name, allow_exec=True)

    def skill(self, identifier="notes-skill"):
        package = self.fixture.add(identifier)
        (package / "SKILL.md").write_text("Summarize meeting notes with exact owners.", encoding="utf-8")
        return package

    def environment(self):
        return dict(os.environ, CAPMGR_CATALOGS=str(self.fixture.catalog),
                    CAPMGR_POLICY=str(self.fixture.policy), CAPMGR_DATA_DIR=str(self.fixture.data),
                    CAPMGR_INCLUDE_CLAUDE_CATALOG="0", CAPMGR_INCLUDE_CODEX_CATALOG="0",
                    CAPMGR_PLATFORM="codex")

    def hook(self, name, event, env=None):
        return subprocess.run([sys.executable, str(ROOT / "hooks" / (name + ".py"))],
                              input=json.dumps(event), capture_output=True, text=True,
                              env=env or self.environment(), cwd=str(ROOT), timeout=5)

    def test_prompt_provides_verified_zero_receipt_without_preparing_a_package(self):
        self.skill()
        start = self.hook('session_start', {'session_id': 'empty', 'cwd': '/private/project'})
        self.assertEqual(start.returncode, 0, start.stderr)
        submitted = self.hook('user_prompt_submit', {
            'session_id': 'empty', 'turn_id': 'routine', 'prompt': '12와 18의 최대공약수는?',
            'cwd': '/private/project'})
        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        manager = self.fixture.manager()
        summary = manager.session_summary('empty')
        self.assertEqual(summary['counts'], {
            'capabilities': 0, 'new_packages': 0, 'cache_reused': 0, 'active': 0, 'released': 0})
        guidance = json.loads(submitted.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn(summary['summary_markdown'], guidance)
        self.assertIn('추가 설치 0', summary['summary_markdown'])
        self.assertIn('권한 해제 0', summary['summary_markdown'])
        self.assertFalse(summary['release_completed'])
        self.assertEqual(list((manager.data_dir / 'cache').glob('*')), [])
        ended = self.hook('session_end', {'session_id': 'empty'})
        self.assertEqual(ended.returncode, 0, ended.stderr)
        saved = json.loads(next((manager.data_dir / 'session-summaries').glob('*/summary.json')).read_text())
        self.assertTrue(saved['release_completed'])
        self.assertEqual(saved['counts'], summary['counts'])
        self.assertEqual(saved['summary_markdown'], summary['summary_markdown'])

    def test_prompt_never_reuses_zero_receipt_after_preparation(self):
        self.skill()
        manager = self.fixture.manager()
        empty_receipt = manager.session_summary('chat')['summary_markdown']
        manager.activate('notes-skill', 'chat')
        submitted = self.hook('user_prompt_submit', {
            'session_id': 'chat', 'turn_id': 'later', 'prompt': '12와 18의 최대공약수는?'})
        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        guidance = json.loads(submitted.stdout)['hookSpecificOutput']['additionalContext']
        self.assertNotIn(empty_receipt, guidance)
        summary = manager.session_summary('chat')
        self.assertEqual(summary['counts']['active'], 1)
        self.assertEqual(summary['counts']['new_packages'], 1)

    def test_installation_cache_reuse_and_idempotent_release_are_distinct(self):
        self.skill()
        manager = self.fixture.manager()
        manager.activate("notes-skill", "first")
        manager.activate("notes-skill", "first")
        before = manager.session_summary("first")
        self.assertEqual(before["counts"]["new_packages"], 1)
        self.assertEqual(before["counts"]["active"], 1)
        self.assertIn("결과 미확인", before["summary_markdown"])
        ended = manager.release("first")
        self.assertEqual(ended["released"], ["notes-skill"])
        self.assertTrue(ended["session_summary"]["release_completed"])
        self.assertIn("해제 완료", ended["summary_markdown"])
        self.assertTrue(ended["session_summary"]["capabilities"][0]["cache_retained"])
        self.assertEqual(manager.release("first")["released"], [])
        manager.activate("notes-skill", "second")
        reused = manager.session_summary("second")
        self.assertEqual(reused["counts"]["cache_reused"], 1)
        self.assertEqual(reused["counts"]["new_packages"], 0)
        # A follow-up in the same chat can reactivate without duplicating its receipt row.
        manager.activate("notes-skill", "first")
        resumed = manager.session_summary("first")
        self.assertEqual(resumed["counts"]["capabilities"], 1)
        self.assertFalse(resumed["release_completed"])
        self.assertEqual(resumed["capabilities"][0]["access_state"], "active")

    def test_real_gateway_calls_and_reported_outcomes_are_separate(self):
        package = self.fixture.add("mock-plugin", kind="plugin", execute=True)
        write_json(package / "plugin.json", {"name": "mock-plugin", "version": "1.0.0"})
        (package / "server.py").write_text(MOCK_MCP, encoding="utf-8")
        write_json(package / ".mcp.json", {"mcpServers": {"notes": {
            "command": "python3", "args": ["${PLUGIN_ROOT}/server.py"]}}})
        manager = self.fixture.manager()
        self.addCleanup(manager.release, "chat")
        manager.activate("mock-plugin", "chat")
        manager.invoke("chat", "mock-plugin", "notes", "lookup", {"key": "private-note"})
        first = manager.session_summary("chat")["capabilities"][0]
        self.assertEqual((first["tool_successes"], first["reported_result"]), (1, "not_reported"))
        with self.assertRaises(PermissionError):
            manager.invoke("chat", "mock-plugin", "notes", "delete", {})
        manager.record_outcome("chat", "mock-plugin", "repo", False)
        summary = manager.release("chat")["session_summary"]
        item = summary["capabilities"][0]
        self.assertEqual((item["tool_successes"], item["tool_errors"], item["reported_result"]), (1, 1, "failure"))
        self.assertIn("실패 보고", summary["summary_markdown"])
        self.assertNotIn("private-note", json.dumps(summary))

    def test_unavailable_connector_is_installed_but_never_claimed_used(self):
        package = self.fixture.add("demo-connector", kind="connector")
        write_json(package / "plugin.json", {"name": "demo-connector", "version": "1.0.0"})
        write_json(package / ".mcp.json", {"mcpServers": {"notes": {
            "type": "http", "url": "http://127.0.0.1:9999/mcp"}}})
        manager = self.fixture.manager()
        with patch("capability_manager.manager.connect", side_effect=OSError("secret authentication text")):
            result = manager.activate("demo-connector", "chat")
        self.assertEqual(result["availability"], "unavailable")
        summary = manager.release("chat")["session_summary"]
        item = summary["capabilities"][0]
        self.assertEqual((item["package_state"], item["access_state"]), ("installed", "inactive"))
        self.assertIn("사용 불가", summary["summary_markdown"])
        self.assertNotIn("secret authentication text", json.dumps(summary))

    def test_unapproved_executable_attempt_is_visible_without_false_installation(self):
        self.fixture.allow_exec = False
        self.fixture.write()
        self.fixture.add("mock-plugin", kind="plugin", execute=True)
        manager = self.fixture.manager()
        with self.assertRaises(PermissionError):
            manager.activate("mock-plugin", "chat")
        summary = manager.session_summary("chat")
        item = summary["capabilities"][0]
        self.assertEqual((item["package_state"], item["cache_retained"]), ("not_prepared", False))
        self.assertEqual(item["availability"], "requires_review")
        self.assertIn("설치 안 됨", summary["summary_markdown"])

    def test_failed_connection_close_still_revokes_and_survives_later_reports(self):
        self.skill()
        manager = self.fixture.manager()
        manager.activate("notes-skill", "chat")
        manager.activate("notes-skill", "another-chat")
        broken = Mock()
        broken.close.side_effect = OSError("private connection detail")
        manager.connections[("chat", "notes-skill", "broken")] = broken
        released = manager.release("chat")
        self.assertFalse(manager.store.is_active("chat", "notes-skill"))
        self.assertTrue(manager.store.is_active("another-chat", "notes-skill"))
        self.assertEqual(released["session_summary"]["cleanup_errors"][0]["error_type"], "OSError")
        later = manager.release("chat")["session_summary"]
        self.assertIn("일부 연결 종료", later["summary_markdown"])
        self.assertNotIn("private connection detail", json.dumps(later))

    def test_http_cleanup_failure_is_visible_after_local_access_is_revoked(self):
        self.skill()
        manager = self.fixture.manager()
        manager.activate("notes-skill", "chat")
        connection = HttpConnection("http://127.0.0.1:9999/mcp", None, manager.policy)
        connection.session_id = "remote-session"
        manager.connections[("chat", "notes-skill", "http")] = connection
        with patch("capability_manager.mcp_bridge.open_no_redirect", side_effect=OSError("private URL detail")):
            result = manager.release("chat")
        self.assertTrue(result["session_summary"]["release_completed"])
        self.assertEqual(result["session_summary"]["cleanup_errors"][0]["error_type"], "OSError")
        self.assertNotIn("private URL detail", json.dumps(result))

    def test_failed_stdio_initialization_does_not_leave_a_process_running(self):
        package = self.fixture.add("mock-plugin", kind="plugin", execute=True)
        write_json(package / "plugin.json", {"name": "mock-plugin", "version": "1.0.0"})
        write_json(package / "mcp.json", {"mcpServers": {"broken": {
            "command": "python3", "args": ["server.py"]}}})
        (package / "server.py").write_text('''import json,sys
for line in sys.stdin:
    msg=json.loads(line)
    if "id" not in msg: continue
    print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"error":{"code":-32603,"message":"init failed"}}), flush=True)
''', encoding="utf-8")
        connections = []
        def capture(*args):
            connection = StdioConnection(*args)
            connections.append(connection)
            self.addCleanup(connection.close)
            return connection
        manager = self.fixture.manager()
        with patch("capability_manager.mcp_bridge.StdioConnection", side_effect=capture):
            result = manager.activate("mock-plugin", "chat")
        self.assertEqual(result["availability"], "unavailable")
        self.assertIsNotNone(connections[0].process.poll())
        self.assertFalse(manager.store.is_active("chat", "mock-plugin"))

    def test_old_leases_have_unknown_origin_and_catalog_independent_receipts(self):
        manager = self.fixture.manager()
        manager.store.activate("old-chat", "removed-from-catalog")
        before = manager.session_summary("old-chat")
        self.assertEqual(before["counts"]["active"], 1)
        self.assertEqual(before["capabilities"][0]["package_state"], "unknown")
        self.assertIsNone(before["capabilities"][0]["cache_retained"])
        after = manager.release("old-chat")["session_summary"]
        self.assertEqual(after["capabilities"][0]["access_state"], "released")
        self.assertIsNone(after["capabilities"][0]["first_prepared_at"])
        self.assertIn("이전 기록 없음", after["summary_markdown"])
        self.skill("removed-from-catalog")
        manager = self.fixture.manager()
        with patch("capability_manager.store.time.time", return_value=2000000000):
            manager.activate("removed-from-catalog", "old-chat")
            known = manager.session_summary("old-chat")["capabilities"][0]
        self.assertEqual(known["first_prepared_at"], 2000000000)
        self.assertEqual(known["package_state"], "installed")

    def test_receipt_escapes_metadata_and_uses_private_hashed_file_names(self):
        self.skill()
        manager = self.fixture.manager()
        name = "[click](https://example.com)|\n<script>`*_"
        manager.store.record_preparation("../../outside", {
            "id": "notes-skill", "name": name, "kind": "skill", "version": "1.0.0"})
        summary = receipt(manager.store, "../../outside", manager.data_dir)
        self.assertNotIn("<script>", summary["summary_markdown"])
        self.assertIn("\\[click\\]", summary["summary_markdown"])
        reports = save_receipt(summary, manager.data_dir)
        for filename in reports.values():
            saved = Path(filename)
            self.assertEqual(saved.parent.parent, manager.data_dir / "session-summaries")
            self.assertEqual(len(saved.parent.name), 24)
            self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
        self.assertEqual(json.loads(Path(reports["json"]).read_text())["session_id"], "../../outside")

    def test_mcp_storage_mismatch_is_rejected_before_selection_or_installation(self):
        self.skill()
        manager = self.fixture.manager()
        def call(name, arguments):
            response = handle(manager, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                       "params": {"name": name, "arguments": arguments}})["result"]
            return response, json.loads(response["content"][0]["text"])
        response, _ = call("resolve_static_skill", {
            "task": "Use a skill to summarize meeting notes", "session_id": "chat", "expected_storage_id": "0" * 24})
        self.assertTrue(response["isError"])
        self.assertEqual(manager.store.decisions(pending_only=False), [])
        self.assertEqual(manager.session_summary("chat")["capabilities"], [])
        self.assertFalse(manager.installer.package_path(manager.catalog["notes-skill"]).exists())
        _, status = call("capability_runtime_status", {"session_id": "chat"})
        self.assertEqual(status["storage_id"], storage_id(manager.data_dir))
        _, summary = call("capability_session_summary", {
            "session_id": "chat", "expected_storage_id": status["storage_id"]})
        self.assertIn("없음", summary["summary_markdown"])
        schema = next(tool for tool in TOOLS if tool["name"] == "capability_session_summary")
        self.assertTrue(schema["annotations"]["readOnlyHint"])

    def test_hooks_mcp_and_cli_share_storage_and_end_without_catalog_loading(self):
        self.skill()
        env = self.environment()
        start = self.hook("session_start", {"session_id": "chat", "cwd": "/private/project"}, env)
        self.assertEqual(start.returncode, 0, start.stderr)
        self.assertIn(storage_id(self.fixture.data), start.stdout)
        self.assertNotIn("/private/project", start.stdout)
        submitted = self.hook("user_prompt_submit", {"session_id": "chat", "turn_id": "turn", "prompt":
                              "Use a skill for secret meeting notes", "cwd": "/private/project"}, env)
        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        self.assertNotIn("secret meeting notes", submitted.stdout)
        with patch.dict(os.environ, env):
            manager = CapabilityManager()
            self.assertTrue(manager.runtime_status("chat")["session_context_bound"])
            manager.resolve_static("Use a skill to summarize meeting notes", "chat", turn_id="turn",
                                   expected_storage_id=storage_id(self.fixture.data))
            manager.record_outcome("chat", "notes-skill", success=True)
        invalid_catalog = dict(env, CAPMGR_CATALOGS=str(self.fixture.root / "missing.json"))
        ended = self.hook("session_end", {"session_id": "chat"}, invalid_catalog)
        self.assertEqual(ended.returncode, 0, ended.stderr)
        self.assertEqual(ended.stdout, "")
        self.assertFalse(manager.store.is_active("chat", "notes-skill"))
        saved = list((self.fixture.data / "session-summaries").glob("*/summary.json"))
        self.assertEqual(len(saved), 1)
        self.assertTrue(json.loads(saved[0].read_text())["release_completed"])
        report = subprocess.run([sys.executable, "-m", "capability_manager.cli", "--data-dir", str(self.fixture.data),
                                 "session-report", "--session", "chat"], env=invalid_catalog, cwd=str(ROOT),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("해제 완료", report.stdout)
        self.assertIn("성공 보고", report.stdout)
        status = subprocess.run([sys.executable, "-m", "capability_manager.cli", "status", "--session", "chat"],
                                env=invalid_catalog, cwd=str(ROOT), capture_output=True, text=True, timeout=5)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["storage_id"], storage_id(self.fixture.data))

    def test_hook_failure_metadata_does_not_expose_prompt_or_exception_paths(self):
        env = dict(self.environment(), CAPMGR_CATALOGS=str(self.fixture.root / "private-missing.json"))
        submitted = self.hook("user_prompt_submit", {"session_id": "chat", "prompt": "private secret prompt"}, env)
        self.assertIn("systemMessage", submitted.stdout)
        self.assertIn("FileNotFoundError", submitted.stderr)
        self.assertNotIn("private secret prompt", submitted.stdout + submitted.stderr)
        self.assertNotIn("private-missing.json", submitted.stdout + submitted.stderr)
        metadata = (self.fixture.data / "hook-errors.jsonl").read_text()
        self.assertNotIn("private-missing.json", metadata)
        self.assertNotIn("private secret prompt", metadata)
        status = self.fixture.manager().runtime_status("chat")
        self.assertEqual(status["recent_hook_errors"][0]["phase"], "UserPromptSubmit")

    def test_runtime_storage_priority_preserves_codex_platform_with_claude_aliases(self):
        root = self.fixture.root
        env = {"PLUGIN_ROOT": str(root), "PLUGIN_DATA": str(root / "codex"),
               "CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_PLUGIN_DATA": str(root / "claude")}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(platform(), "codex")
            self.assertEqual(configure_data_dir(root), root / "codex")
            os.environ["CAPMGR_DATA_DIR"] = str(root / "explicit")
            self.assertEqual(configure_data_dir(root), root / "explicit")
            os.environ["CAPMGR_DATA_DIR"] = "relative/path"
            with self.assertRaises(ValueError):
                configure_data_dir(root)

    def test_event_metrics_count_unique_sessions_separately_from_repeated_calls(self):
        self.skill()
        manager = self.fixture.manager()
        for _ in range(3):
            manager.search("Use a skill to summarize meeting notes", session_id="chat")
        manager.search("Use a skill to summarize meeting notes", session_id="second")
        report = manager.event_report()
        self.assertEqual(report["events"], 4)
        self.assertEqual(report["unique_sessions"], 2)
        self.assertEqual(report["unique_sessions_by_event"]["capability_searched"], 2)
        self.assertEqual(report["latency_ms_by_event"]["capability_searched"]["samples"], 4)


if __name__ == "__main__":
    unittest.main()
