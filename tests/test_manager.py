import io
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from capability_manager.manager import CapabilityManager, context_key
from capability_manager.onboarding import register_source
from capability_manager.store import Store


ROOT = Path(__file__).resolve().parent.parent


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


class Fixture:
    def __init__(self, root, allow_exec=False):
        self.root = Path(root)
        self.packages = self.root / "packages"
        self.packages.mkdir()
        self.data = self.root / "data"
        self.catalog = self.root / "catalog.json"
        self.policy = self.root / "policy.json"
        self.entries = []
        self.allow_exec = allow_exec
        self.write()

    def add(self, identifier, kind="skill", tags=None, execute=False):
        package = self.packages / identifier
        package.mkdir()
        entry = {
            "id": identifier, "name": identifier.replace("-", " "),
            "description": "Summarize meeting notes and action items" if kind == "skill" else "Look up local demo notes",
            "kind": kind, "publisher": "test", "version": "1.0.0",
            "tags": tags or (["notes", "meeting", "summary"] if kind == "skill" else ["lookup", "notes"]),
            "source": {"type": "directory", "path": "packages/" + identifier},
            "permissions": {"execute": execute},
        }
        self.entries.append(entry)
        self.write()
        return package

    def write(self):
        write_json(self.catalog, {"capabilities": self.entries})
        write_json(self.policy, {
            "publishers": ["test"], "kinds": ["skill", "plugin", "connector"],
            "local_roots": ["packages"], "download_hosts": [],
            "connector_hosts": ["127.0.0.1"], "decision_hosts": ["127.0.0.1"],
            "allowed_secret_env": [], "allowed_read_tools": ["mock-plugin:lookup"],
            "allow_executable": self.allow_exec, "allow_external_write": False,
            "allowed_write_tools": [],
        })

    def manager(self):
        return CapabilityManager([self.catalog], self.policy, self.data)


MOCK_MCP = '''import json,sys
for line in sys.stdin:
    msg=json.loads(line)
    if "id" not in msg: continue
    method=msg.get("method")
    if method=="initialize": result={"protocolVersion":"2025-03-26","capabilities":{"tools":{}},"serverInfo":{"name":"mock","version":"1"}}
    elif method=="tools/list": result={"tools":[{"name":"lookup","description":"Read a note","inputSchema":{"type":"object"},"annotations":{"readOnlyHint":True}},{"name":"delete","description":"Delete a note","inputSchema":{"type":"object"}}]}
    elif method=="tools/call": result={"content":[{"type":"text","text":"found:"+str(msg["params"]["arguments"].get("key",""))}]}
    else: result={}
    sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":result})+"\\n")
    sys.stdout.flush()
'''


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_uninstalled_skill_is_applied_and_released_in_same_session(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("---\nname: notes-skill\ndescription: Summarize notes.\n---\nUse exact owners.")
        manager = fixture.manager()
        found = manager.search("summarize meeting notes", "repo-a")
        self.assertFalse(found["candidates"][0]["cached"])
        result = manager.resolve("need a skill to summarize meeting notes", "session-a", "repo-a")
        self.assertEqual(result["status"], "activated")
        self.assertIn("Use exact owners", result["capability"]["skills"][0]["instructions"])
        self.assertTrue(manager.store.is_active("session-a", "notes-skill"))
        for _ in range(3):
            manager.record_outcome("session-a", "notes-skill", "repo-a", True)
        self.assertEqual(manager.prefetch("repo-a")["prefetched"], [])
        for session_id in ("session-b", "session-c"):
            manager.activate("notes-skill", session_id)
            manager.record_outcome(session_id, "notes-skill", "repo-a", True)
        self.assertEqual(manager.prefetch("repo-a")["prefetched"], ["notes-skill"])
        self.assertEqual(manager.release("session-a")["released"], ["notes-skill"])
        self.assertFalse(manager.store.is_active("session-a", "notes-skill"))
        self.assertTrue(manager.installer.package_path(manager.catalog["notes-skill"]).is_dir())

    def test_manager_events_trace_delivery_without_prompt_or_tool_arguments(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("Summarize notes.")
        with patch.dict(os.environ, {"CAPMGR_PLATFORM": "claude"}):
            manager = fixture.manager()
        manager.bind_session_context("session-a", "/private/project")
        manager.observe_prompt("Use a skill for secret meeting notes", "session-a", "turn-a")
        result = manager.resolve_static("Use a skill to summarize meeting notes",
                                        "session-a", turn_id="turn-a")
        self.assertEqual(result["status"], "activated")
        manager.record_outcome("session-a", "notes-skill", success=True)
        manager.release("session-a")
        events = list(reversed(manager.store.events(session_id="session-a")))
        self.assertEqual([event["event_type"] for event in events], [
            "prompt_observed", "capability_searched", "capability_delivered",
            "outcome_reported", "session_released"])
        self.assertEqual(events[2]["status"], "static_skill")
        self.assertTrue(all(event["platform"] == "claude" for event in events))
        self.assertTrue(all(event["plugin_version"] for event in events))
        self.assertNotIn("secret meeting notes", json.dumps(events))
        self.assertEqual(manager.event_report()["events"], 5)

    def test_older_store_gains_event_table_without_losing_decisions(self):
        path = Path(self.temp.name) / "old.sqlite3"
        with sqlite3.connect(str(path)) as db:
            db.execute("CREATE TABLE decisions(id TEXT PRIMARY KEY, session_id TEXT, "
                       "context_key TEXT, task_hash TEXT, task_text TEXT, backend TEXT, "
                       "model TEXT, recommendation TEXT, candidates_json TEXT, "
                       "need_probability REAL, confidence REAL, activation_status TEXT, "
                       "created_at INTEGER)")
            db.execute("INSERT INTO decisions VALUES('old','s','c','h',NULL,'lexical',NULL,"
                       "NULL,'[]',0,0,'searched',1)")
        store = Store(path, "codex", "0.1.7")
        store.add_event("s", "capability_searched", "abstained", decision_id="old")
        self.assertEqual(store.get_decision("old")["id"], "old")
        self.assertEqual(store.event_report()["groups"][0]["platform"], "codex")

    def test_releasing_one_session_preserves_another(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("---\nname: notes-skill\ndescription: Notes.\n---\nSummarize.")
        manager = fixture.manager()
        manager.activate("notes-skill", "session-a")
        manager.activate("notes-skill", "session-b")
        manager.release("session-a")
        self.assertFalse(manager.store.is_active("session-a", "notes-skill"))
        self.assertTrue(manager.store.is_active("session-b", "notes-skill"))

    def test_rejected_outcome_is_visible_without_counting_as_success(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("Summarize notes.")
        manager = fixture.manager()
        manager.activate("notes-skill", "session-a")
        with self.assertRaises(ValueError):
            manager.record_outcome("session-a", "notes-skill", success=True)
        self.assertEqual(manager.store.events(session_id="session-a")[0]["status"], "rejected")
        self.assertEqual(manager.store.event_report()["events"], 2)
        self.assertEqual(manager.store.warm_ids(context_key("")), [])

    def test_project_context_binding_keeps_prefetch_key_stable(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("---\nname: notes-skill\ndescription: Notes.\n---\nSummarize.")
        manager = fixture.manager()
        for session_id in ("a", "b", "c"):
            manager.bind_session_context(session_id, "/project/example")
            result = manager.resolve("need a skill to summarize meeting notes", session_id,
                                     "different task context " + session_id)
            self.assertEqual(result["search"]["context_key"], context_key("/project/example"))
            self.assertEqual(result["search"]["context_source"], "session")
            recorded = manager.record_outcome(session_id, "notes-skill", success=True)
            self.assertEqual(recorded["context_key"], context_key("/project/example"))
        self.assertEqual(manager.prefetch("/project/example")["prefetched"], ["notes-skill"])
        manager.release("a")
        self.assertIsNone(manager.store.session_context("a"))

    def test_prompt_hook_records_skipped_search_without_storing_prompt(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("Summarize notes.")
        manager = fixture.manager()
        manager.bind_session_context("session-a", "/project/example")
        env = dict(os.environ, CAPMGR_CATALOGS=str(fixture.catalog),
                   CAPMGR_POLICY=str(fixture.policy), CAPMGR_DATA_DIR=str(fixture.data))

        def submit(turn_id, prompt):
            event = {"session_id": "session-a", "turn_id": turn_id,
                     "cwd": "/project/example", "prompt": prompt}
            return subprocess.run(
                [sys.executable, str(ROOT / "hooks/user_prompt_submit.py")],
                input=json.dumps(event), text=True, capture_output=True, env=env, check=True,
            ).stdout

        routine_output = submit("turn-1", "Summarize these private meeting notes.")
        self.assertIn("turn_id: turn-1", routine_output)
        self.assertNotIn("suggests notes-skill", routine_output)
        skipped_id = manager.store.prompt_observation("turn-1")
        skipped = manager.store.get_decision(skipped_id)
        self.assertEqual(skipped["activation_status"], "not_searched")
        self.assertIsNone(skipped["task_text"])
        output = submit("turn-2", "Use a skill to summarize meeting notes.")
        self.assertIn("notes-skill", output)
        self.assertNotIn("meeting notes", output)
        implicit_output = submit("turn-3", "Put notes into our required meeting format.")
        self.assertIn("project-specific format", implicit_output)
        self.assertNotIn("Put notes", implicit_output)
        selected_id = manager.store.prompt_observation("turn-2")
        manager.search("need a skill to summarize meeting notes", session_id="session-a",
                       turn_id="turn-2")
        self.assertEqual(manager.store.get_decision(selected_id)["activation_status"], "searched")
        manager.record_decision_feedback("session-a", skipped_id, "notes-skill")
        observed = manager.decision_report()["prompt_observations"]
        self.assertEqual(observed, {"total": 3, "searched": 1, "not_searched": 2,
                                    "labeled_not_searched": 1, "missed_capability": 1})

    def test_claude_prompt_without_turn_id_gets_observable_unique_turns(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("Summarize notes.")
        env = dict(os.environ, CAPMGR_CATALOGS=str(fixture.catalog),
                   CAPMGR_POLICY=str(fixture.policy), CLAUDE_PLUGIN_DATA=str(fixture.data))
        env.pop("CAPMGR_DATA_DIR", None)
        env.pop("PLUGIN_DATA", None)
        event = {"session_id": "claude-session", "cwd": "/project/example",
                 "prompt": "Use a skill to summarize meeting notes."}
        ids = []
        for _ in range(2):
            process = subprocess.run(
                [sys.executable, str(ROOT / "hooks/user_prompt_submit.py")],
                input=json.dumps(event), text=True, capture_output=True, env=env, check=True,
            )
            guidance = json.loads(process.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("suggests notes-skill", guidance)
            ids.append(guidance.split("turn_id: ", 1)[1].split(".", 1)[0])
        self.assertNotEqual(ids[0], ids[1])
        manager = CapabilityManager([fixture.catalog], fixture.policy, fixture.data)
        self.assertTrue(all(manager.store.prompt_observation(turn) for turn in ids))
        manager.search("need a skill to summarize meeting notes",
                       session_id="claude-session", turn_id=ids[0])
        self.assertEqual(manager.store.get_decision(manager.store.prompt_observation(ids[0]))
                         ["activation_status"], "searched")

    def test_registered_local_capability_loads_without_environment_configuration(self):
        source = Path(self.temp.name) / "team-skill"
        source.mkdir()
        (source / "SKILL.md").write_text("---\nname: team-skill\n---\nUse the team format.")
        data = Path(self.temp.name) / "configured"
        result = register_source(
            data, identifier="team-skill", name="Team meeting notes",
            description="Prepare team meeting notes", kind="skill", publisher="team",
            version="1.0.0", tags=["meeting", "notes"], source_dir=source,
            dry_run=True,
        )
        self.assertTrue(result["dry_run"])
        self.assertFalse((data / "catalog.json").exists())
        register_source(
            data, identifier="team-skill", name="Team meeting notes",
            description="Prepare team meeting notes", kind="skill", publisher="team",
            version="1.0.0", tags=["meeting", "notes"], source_dir=source,
        )
        with patch.dict(os.environ, {"CAPMGR_CONFIG_DIR": str(data)}):
            manager = CapabilityManager(data_dir=Path(self.temp.name) / "runtime")
        self.assertEqual(manager.resolve("Use a skill for team meeting notes", "session-a")
                         ["status"], "activated")
        self.assertEqual(manager.release("session-a")["released"], ["team-skill"])

    def test_registered_executable_is_scoped_to_one_capability(self):
        source = Path(self.temp.name) / "server-plugin"
        source.mkdir()
        write_json(source / "plugin.json", {"name": "server-plugin", "version": "1.0.0"})
        write_json(source / "mcp.json", {"mcpServers": {"mock": {
            "type": "stdio", "command": "python3", "args": ["server.py"]}}})
        (source / "server.py").write_text(MOCK_MCP)
        data = Path(self.temp.name) / "configured"
        params = dict(identifier="server-plugin", name="Server plugin",
                      description="Look up local demo notes", kind="plugin", publisher="team",
                      version="1.0.0", tags=["lookup", "notes"], source_dir=source)
        with self.assertRaises(PermissionError):
            register_source(data, **params)
        register_source(data, **params, allow_executable=True,
                        allowed_read_tools=["server-plugin:lookup"])
        manager = CapabilityManager(data_dir=data)
        self.assertTrue(manager.policy.can_execute("server-plugin"))
        self.assertFalse(manager.policy.can_execute("another-plugin"))
        result = manager.activate("server-plugin", "session-a")
        self.assertEqual(result["mcp_servers"]["mock"][0]["name"], "lookup")
        manager.release("session-a")

    def test_static_resolver_never_starts_an_executable_plugin(self):
        fixture = Fixture(self.temp.name, allow_exec=True)
        package = fixture.add("notes-server", kind="plugin", execute=True,
                              tags=["notes", "meeting"])
        (package / "SKILL.md").write_text("Summarize notes.")
        write_json(package / "mcp.json", {"mcpServers": {"mock": {
            "type": "stdio", "command": "python3", "args": ["server.py"]}}})
        (package / "server.py").write_text(MOCK_MCP)
        manager = fixture.manager()
        with patch.object(manager, "list_tools", side_effect=AssertionError("MCP must not start")):
            result = manager.resolve_static("Use a plugin for meeting notes", "session-a")
        self.assertEqual(result["status"], "no_confident_match")
        self.assertFalse(manager.store.is_active("session-a", "notes-server"))

    def test_pinned_remote_source_registration_does_not_download(self):
        data = Path(self.temp.name) / "configured"
        result = register_source(
            data, identifier="remote-notes", name="Remote notes",
            description="Prepare approved meeting notes", kind="skill",
            publisher="team", version="1.0.0", tags=["meeting"],
            source_url="https://packages.example.test/notes.zip", sha256="a" * 64,
        )
        self.assertEqual(result["entry"]["source"]["sha256"], "a" * 64)
        self.assertEqual(CapabilityManager(data_dir=data).search(
            "Use a skill for meeting notes")["recommendation"], "remote-notes")
        self.assertFalse(any((data / "cache").glob("remote-notes/*")))

    def test_claude_mcp_entrypoint_exposes_manager_tools(self):
        data = Path(self.temp.name) / "claude-data"
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=str(data))
        env.pop("PLUGIN_DATA", None)
        env.pop("CAPMGR_DATA_DIR", None)
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        process = subprocess.run(
            [sys.executable, str(ROOT / "scripts/serve.py")],
            input="\n".join(json.dumps(item) for item in messages) + "\n",
            text=True, capture_output=True, env=env, check=True,
        )
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"],
                         "contextual-capability-manager")
        self.assertIn("resolve_capability",
                      {tool["name"] for tool in replies[1]["result"]["tools"]})

    def test_prompt_observer_can_avoid_dynamic_catalog_discovery(self):
        fixture = Fixture(self.temp.name)
        with patch.dict(os.environ, {"CAPMGR_INCLUDE_CODEX_CATALOG": "1"}):
            with patch("capability_manager.codex_catalog.discover",
                       side_effect=AssertionError("dynamic discovery must not run")):
                manager = CapabilityManager([fixture.catalog], fixture.policy, fixture.data,
                                            include_codex_catalog=False)
        self.assertEqual(manager.catalog, {})

    def test_local_need_gate_abstains_for_routine_or_ambiguous_tasks(self):
        fixture = Fixture(self.temp.name)
        first = fixture.add("notes-skill")
        (first / "SKILL.md").write_text("Summarize notes.")
        manager = fixture.manager()
        routine = manager.search("summarize meeting notes")
        self.assertIsNone(routine["recommendation"])
        self.assertEqual(routine["decision"]["need_probability"], 0.0)
        rejected = manager.search("Do not use a skill to summarize meeting notes")
        self.assertIsNone(rejected["recommendation"])
        self.assertEqual(rejected["decision"]["need_probability"], 0.0)
        explicit = manager.search("need a skill to summarize meeting notes")
        self.assertEqual(explicit["recommendation"], "notes-skill")
        self.assertEqual(explicit["decision"]["confidence"], 1.0)
        second = fixture.add("similar-skill")
        (second / "SKILL.md").write_text("Summarize notes.")
        ambiguous = fixture.manager().search("need a skill to summarize meeting notes")
        self.assertIsNone(ambiguous["recommendation"])
        self.assertEqual(ambiguous["decision"]["confidence"], 0.0)

    def test_project_specific_task_can_recommend_without_capability_noun(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("Summarize notes in the team's format.")
        manager = fixture.manager()
        inferred = manager.search("Put these meeting notes into our required format.")
        self.assertEqual(inferred["recommendation"], "notes-skill")
        self.assertEqual(inferred["decision"]["need_reason"], "project_specific_task")
        self.assertIsNone(manager.search("Explain what a skill is for meeting notes")
                          ["recommendation"])
        self.assertIsNone(manager.search("Use our required meeting notes format pasted below")
                          ["recommendation"])

    def test_original_prompt_recommendation_survives_agent_paraphrase_in_same_turn(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("Use exact owners.")
        manager = fixture.manager()
        observed = manager.observe_prompt(
            "Put these meeting notes into our required format.", "session-a", "turn-a")
        self.assertEqual(observed["suggested_capability_id"], "notes-skill")
        result = manager.resolve(
            "Format meeting notes into decisions and owners; no template in workspace.",
            "session-a", turn_id="turn-a")
        self.assertEqual(result["status"], "activated")
        self.assertEqual(result["search"]["decision"]["backend"], "prompt-observer-assisted")
        self.assertEqual(result["search"]["decision"]["prompt_observation_id"],
                         observed["decision_id"])
        self.assertEqual(manager.store.get_decision(observed["decision_id"])
                         ["activation_status"], "searched")
        self.assertIsNone(manager.search(
            "Do not use a skill to summarize meeting notes", session_id="session-a",
            turn_id="turn-a")["recommendation"])
        self.assertIsNone(manager.search(
            "Format meeting notes into decisions and owners", session_id="session-b",
            turn_id="turn-a")["recommendation"])

    def test_stdio_plugin_tool_can_run_in_same_session_and_write_is_denied(self):
        fixture = Fixture(self.temp.name, allow_exec=True)
        package = fixture.add("mock-plugin", kind="plugin", tags=["lookup", "notes"], execute=True)
        write_json(package / "plugin.json", {"name": "mock-plugin", "version": "1.0.0"})
        write_json(package / "mcp.json", {"mcpServers": {
            "mock": {"type": "stdio", "command": "python3", "args": ["server.py"]}}})
        (package / "server.py").write_text(MOCK_MCP)
        manager = fixture.manager()
        result = manager.activate("mock-plugin", "session-b")
        self.assertEqual(result["mcp_servers"]["mock"][0]["name"], "lookup")
        value = manager.invoke("session-b", "mock-plugin", "mock", "lookup",
                               {"key": "private-input"})
        self.assertEqual(value["content"][0]["text"], "found:private-input")
        manager.tool_catalogs[("session-b", "mock-plugin", "mock")][0]["annotations"]["readOnlyHint"] = False
        with self.assertRaises(PermissionError):
            manager.invoke("session-b", "mock-plugin", "mock", "lookup", {"key": "abc"})
        with self.assertRaises(PermissionError):
            manager.invoke("session-b", "mock-plugin", "mock", "delete", {"key": "abc"})
        events = manager.store.events(session_id="session-b")
        calls = [event for event in events if event["event_type"] == "tool_call"]
        self.assertEqual([event["status"] for event in calls], ["error", "error", "completed"])
        self.assertNotIn("private-input", json.dumps(events))
        self.assertEqual(manager.release("session-b")["released"], ["mock-plugin"])

    def test_unapproved_executable_is_not_installed(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("blocked-plugin", kind="plugin", execute=True)
        write_json(package / "plugin.json", {"name": "blocked-plugin"})
        manager = fixture.manager()
        with self.assertRaises(PermissionError):
            manager.activate("blocked-plugin", "session-c")
        self.assertFalse(manager.installer.package_path(manager.catalog["blocked-plugin"]).exists())

    def test_catalog_rejects_version_that_escapes_cache(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("---\nname: notes-skill\ndescription: Notes.\n---\nSummarize.")
        fixture.entries[0]["version"] = "../../outside"
        fixture.write()
        with self.assertRaises(ValueError):
            fixture.manager()
        self.assertFalse((fixture.root / "outside").exists())

    def test_existing_outcome_database_migrates_without_false_prefetch(self):
        database = Path(self.temp.name) / "old.sqlite3"
        with sqlite3.connect(str(database)) as connection:
            connection.execute("CREATE TABLE outcomes(context_key TEXT, capability_id TEXT,"
                               " success INTEGER, created_at INTEGER)")
            connection.execute("INSERT INTO outcomes VALUES('context', 'skill', 1, 9999999999)")
        store = Store(database)
        self.assertEqual(store.warm_ids("context"), [])
        for session_id in ("a", "b", "c"):
            store.record("context", "skill", session_id, True)
        self.assertEqual(store.warm_ids("context"), ["skill"])

    def test_system_one_choice_and_need_gate(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("---\nname: notes-skill\ndescription: Notes.\n---\nSummarize.")

        seen = {}

        def fake_urlopen(request, timeout):
            seen["body"] = json.loads(request.data)
            answer = {"answers": {
                "needed": {"type": "noul", "noul": 0.94},
                "capability": {"type": "choice", "choice": "notes-skill", "confidence": 0.91,
                               "probabilities": {"notes-skill": 0.91, "none": 0.09}},
            }}
            return io.BytesIO(json.dumps(answer).encode())

        with patch.dict(os.environ, {"CAPMGR_DECIDER_URL": "http://127.0.0.1:8000/v1/systemone",
                                     "CAPMGR_DECIDER_MODEL": "kev-latest"}):
            with patch("capability_manager.decision.open_no_redirect", side_effect=fake_urlopen):
                manager = fixture.manager()
                result = manager.search("organize this conversation", "repo-z")
        self.assertEqual(result["recommendation"], "notes-skill")
        self.assertEqual(result["decision"]["backend"], "system-one")
        self.assertEqual(seen["body"]["questions"]["capability"]["type"], "choice")

    def test_decisions_need_explicit_labels_for_accuracy(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("---\nname: notes-skill\ndescription: Notes.\n---\nSummarize.")
        manager = fixture.manager()
        selected = manager.resolve("need a skill to summarize meeting notes", "session-a", "repo-a")
        rejected = manager.resolve("unrelated task", "session-b", "repo-a")
        selected_id = selected["search"]["decision_id"]
        rejected_id = rejected["search"]["decision_id"]
        self.assertEqual(rejected["status"], "no_confident_match")
        self.assertIsNone(manager.store.get_decision(selected_id)["task_text"])
        outcome = manager.record_outcome("session-a", "notes-skill", success=True)
        self.assertEqual(outcome["context_key"], selected["search"]["context_key"])
        unlabeled = manager.decision_report()["groups"]["lexical"]
        self.assertEqual(unlabeled["labeled"], 0)
        self.assertIsNone(unlabeled["accuracy"])
        pending = manager.list_decisions()["decisions"]
        self.assertEqual(len(pending), 2)
        self.assertEqual({item["session_id"] for item in pending}, {"session-a", "session-b"})
        with self.assertRaises(ValueError):
            manager.record_decision_feedback("session-b", selected_id, "none")
        manager.record_decision_feedback("session-a", selected_id, "none")
        manager.record_decision_feedback("session-b", rejected_id, "notes-skill")
        report = manager.decision_report()["groups"]["lexical"]
        self.assertEqual(report["labeled"], 2)
        self.assertEqual(report["accuracy"], 0.0)
        self.assertEqual(report["false_positives"], 1)
        self.assertEqual(report["false_negatives"], 1)
        self.assertEqual(report["coverage"], 0.5)
        self.assertEqual(manager.list_decisions()["decisions"], [])

    def test_decision_text_is_opt_in(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("---\nname: notes-skill\ndescription: Notes.\n---\nSummarize.")
        with patch.dict(os.environ, {"CAPMGR_STORE_DECISION_TEXT": "1"}):
            manager = fixture.manager()
            searched = manager.search("summarize meeting notes", "repo-a", "session-a")
        self.assertEqual(manager.store.get_decision(searched["decision_id"])["task_text"],
                         "summarize meeting notes")

    def test_unknown_labels_are_abstentions_not_known_capability_misses(self):
        store = Store(Path(self.temp.name) / "state.sqlite3")
        for index, probability in enumerate((1.0, 0.0)):
            decision = {"backend": "lexical", "recommendation": None,
                        "need_probability": probability, "confidence": 0.0}
            identifier = store.add_decision("s", "c", str(index), None, decision, [], "not_selected")
            store.add_feedback(identifier, "other")
        report = store.decision_report()["groups"]["lexical"]
        self.assertEqual(report["accuracy"], 1.0)
        self.assertEqual(report["false_negatives"], 0)
        self.assertEqual(report["unknown_cases"], 2)
        self.assertEqual(report["unknown_need_detected"], 1)
        self.assertEqual(report["unknown_autoselections"], 0)
        self.assertEqual(report["need_recall"], 0.5)
        self.assertIsNone(report["known_accuracy"])

        identifier = store.add_decision("s", "c", "wrong", None,
            {"backend": "lexical", "recommendation": "notes-skill", "need_probability": 1.0},
            [], "activated")
        store.add_feedback(identifier, "other")
        report = store.decision_report()["groups"]["lexical"]
        self.assertEqual(report["unknown_autoselections"], 1)
        self.assertEqual(report["wrong_capability"], 0)

    def test_configured_decider_cannot_override_explicit_rejection(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("Summarize notes.")
        answer = {"answers": {"needed": {"noul": 1.0},
                              "capability": {"choice": "notes-skill", "confidence": 1.0}}}
        with patch.dict(os.environ, {"CAPMGR_DECIDER_URL": "http://127.0.0.1:8000/v1/systemone"}):
            with patch("capability_manager.decision.open_no_redirect",
                       return_value=io.BytesIO(json.dumps(answer).encode())):
                result = fixture.manager().resolve(
                    "회의록 정리 스킬을 설치하지 말고 설명해줘.", "s")
        self.assertEqual(result["status"], "no_confident_match")
        self.assertIsNone(result["search"]["recommendation"])
        self.assertEqual(result["search"]["decision"]["need_reason"], "explicit_rejection")
        self.assertFalse((fixture.data / "cache/notes-skill/1.0.0").exists())

    def test_decision_backend_failure_does_not_auto_install(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("---\nname: notes-skill\ndescription: Notes.\n---\nSummarize.")
        with patch.dict(os.environ, {"CAPMGR_DECIDER_URL": "http://127.0.0.1:8000/v1/systemone"}):
            with patch("capability_manager.decision.open_no_redirect", side_effect=TimeoutError):
                manager = fixture.manager()
                result = manager.resolve("summarize meeting notes", "session-x")
        self.assertEqual(result["status"], "no_confident_match")
        self.assertEqual(result["search"]["decision"]["backend"], "lexical-fallback")
        self.assertFalse(manager.installer.package_path(manager.catalog["notes-skill"]).exists())

    def test_http_connector_is_usable_after_activation(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("mock-connector", kind="connector", tags=["lookup", "records"])
        write_json(package / "plugin.json", {"name": "mock-connector", "version": "1.0.0"})
        write_json(package / "mcp.json", {"mcpServers": {"records": {
            "type": "streamable-http", "url": "http://127.0.0.1:9999/mcp"}}})
        policy = json.loads(fixture.policy.read_text())
        policy["allowed_read_tools"].append("mock-connector:lookup")
        write_json(fixture.policy, policy)

        class Response(io.BytesIO):
            headers = {"Content-Type": "application/json"}

        def fake_urlopen(request, timeout):
            message = json.loads(request.data)
            if "id" not in message:
                return Response(b"")
            method = message["method"]
            if method == "initialize":
                result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "records", "version": "1"}}
            elif method == "tools/list":
                result = {"tools": [{"name": "lookup", "inputSchema": {"type": "object"},
                                      "annotations": {"readOnlyHint": True}}]}
            else:
                result = {"content": [{"type": "text", "text": "record:42"}]}
            return Response(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode())

        with patch("capability_manager.mcp_bridge.open_no_redirect", side_effect=fake_urlopen):
            manager = fixture.manager()
            activated = manager.activate("mock-connector", "session-h")
            self.assertEqual(activated["mcp_servers"]["records"][0]["name"], "lookup")
            result = manager.invoke("session-h", "mock-connector", "records", "lookup", {"id": 42})
            self.assertEqual(result["content"][0]["text"], "record:42")
            manager.release("session-h")

    def test_zip_path_traversal_is_rejected(self):
        fixture = Fixture(self.temp.name)
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("../escape.txt", "bad")
            archive.writestr("SKILL.md", "---\nname: zip-skill\ndescription: Zip.\n---")
        raw = data.getvalue()
        fixture.entries.append({
            "id": "zip-skill", "name": "Zip skill", "description": "Zip test", "kind": "skill",
            "publisher": "test", "version": "1", "tags": ["zip"],
            "source": {"type": "https_zip", "url": "https://example.org/skill.zip",
                       "sha256": hashlib.sha256(raw).hexdigest()}, "permissions": {},
        })
        fixture.write()
        policy = json.loads(fixture.policy.read_text())
        policy["download_hosts"] = ["example.org"]
        write_json(fixture.policy, policy)

        class Response(io.BytesIO):
            def geturl(self):
                return "https://example.org/skill.zip"

        with patch("capability_manager.installer.open_no_redirect", return_value=Response(raw)):
            manager = fixture.manager()
            with self.assertRaises(ValueError):
                manager.activate("zip-skill", "session-z")
        self.assertFalse((fixture.root / "escape.txt").exists())

    def test_mcp_facade_handles_discovery_and_activation(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("---\nname: notes-skill\ndescription: Notes.\n---\nUse exact owners.")
        from capability_manager.mcp_server import handle
        manager = fixture.manager()
        start = handle(manager, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(start["result"]["serverInfo"]["name"], "contextual-capability-manager")
        listed = handle(manager, {"jsonrpc": "2.0", "id": 11, "method": "tools/list"})
        names = {item["name"] for item in listed["result"]["tools"]}
        self.assertIn("record_decision_feedback", names)
        self.assertIn("capability_decision_report", names)
        self.assertIn("capability_event_report", names)
        called = handle(manager, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": "resolve_capability", "arguments": {
                                      "task": "need a skill to summarize meeting notes", "session_id": "thread-1"}}})
        result = json.loads(called["result"]["content"][0]["text"])
        self.assertEqual(result["status"], "activated")
        self.assertIn("Use exact owners", result["capability"]["skills"][0]["instructions"])
        self.assertIn("decision_id", result["search"])
        report = handle(manager, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                  "params": {"name": "capability_decision_report", "arguments": {}}})
        metrics = json.loads(report["result"]["content"][0]["text"])
        self.assertEqual(metrics["groups"]["lexical"]["labeled"], 0)
        feedback = handle(manager, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                    "params": {"name": "record_decision_feedback", "arguments": {
                                        "session_id": "thread-1", "decision_id": result["search"]["decision_id"],
                                        "correct_capability_id": "notes-skill"}}})
        self.assertTrue(json.loads(feedback["result"]["content"][0]["text"])["matched"])
        recorded = handle(manager, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                    "params": {"name": "record_capability_result", "arguments": {
                                        "session_id": "thread-1", "capability_id": "notes-skill",
                                        "success": True}}})
        self.assertTrue(json.loads(recorded["result"]["content"][0]["text"])["recorded"])
        trace = handle(manager, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                 "params": {"name": "capability_event_report", "arguments": {}}})
        self.assertGreaterEqual(json.loads(trace["result"]["content"][0]["text"])["events"], 3)

    def test_mcp_stdio_process_resolves_during_same_conversation(self):
        fixture = Fixture(self.temp.name)
        package = fixture.add("notes-skill")
        (package / "SKILL.md").write_text("---\nname: notes-skill\ndescription: Notes.\n---\nUse exact owners.")
        env = dict(os.environ)
        env.update({"CAPMGR_CATALOGS": str(fixture.catalog), "CAPMGR_POLICY": str(fixture.policy),
                    "CAPMGR_DATA_DIR": str(fixture.data)})
        process = subprocess.Popen([sys.executable, "-m", "capability_manager.mcp_server"],
                                   cwd=str(ROOT), env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            def request(identifier, method, params=None):
                process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier,
                                                "method": method, "params": params or {}}) + "\n")
                process.stdin.flush()
                return json.loads(process.stdout.readline())

            self.assertIn("tools", request(1, "tools/list")["result"])
            answer = request(2, "tools/call", {"name": "resolve_capability", "arguments": {
                "task": "need a skill to summarize meeting notes", "session_id": "same-chat"}})
            resolved = json.loads(answer["result"]["content"][0]["text"])
            self.assertEqual(resolved["status"], "activated")
            self.assertIn("Use exact owners", resolved["capability"]["skills"][0]["instructions"])
        finally:
            process.stdin.close()
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
