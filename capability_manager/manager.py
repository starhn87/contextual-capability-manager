import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .catalog import Entry, load_catalog
from .decision import CAPABILITY_REJECTION, SUPPLIED_MATERIAL, DecisionRouter, lexical_decision
from .installer import Installer
from .mcp_bridge import connect, server_configs
from .policy import Policy
from .store import Store


ROOT = Path(__file__).resolve().parent.parent


def context_key(context: str) -> str:
    return hashlib.sha256(context.encode("utf-8")).hexdigest()[:24]


class CapabilityManager:
    def __init__(self, catalog_paths: Optional[List[Path]] = None,
                 policy_path: Optional[Path] = None, data_dir: Optional[Path] = None,
                 include_codex_catalog: Optional[bool] = None):
        configured = os.environ.get("CAPMGR_CATALOGS", "")
        self.catalog_paths = catalog_paths or (
            [Path(item) for item in configured.split(os.pathsep) if item]
            if configured else [ROOT / "examples/catalog.json"]
        )
        self.policy_path = policy_path or Path(os.environ.get("CAPMGR_POLICY", str(ROOT / "examples/policy.json")))
        self.data_dir = data_dir or Path(
            os.environ.get("CAPMGR_DATA_DIR") or os.environ.get("PLUGIN_DATA")
            or str(Path.home() / ".local/share/contextual-capability-manager")
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.policy = Policy.load(self.policy_path)
        self.catalog = load_catalog(self.catalog_paths)
        include_codex_catalog = (os.environ.get("CAPMGR_INCLUDE_CODEX_CATALOG") == "1"
                                 if include_codex_catalog is None else include_codex_catalog)
        if include_codex_catalog:
            from .codex_catalog import discover
            for identifier, entry in discover().items():
                self.catalog.setdefault(identifier, entry)
        self.installer = Installer(self.data_dir / "cache", self.policy)
        self.store = Store(self.data_dir / "state.sqlite3")
        self.router = DecisionRouter(self.policy)
        self.connections = {}
        self.tool_catalogs = {}

    def _entry(self, capability_id: str) -> Entry:
        try:
            return self.catalog[capability_id]
        except KeyError:
            raise ValueError("unknown capability: " + capability_id)

    def bind_session_context(self, session_id: str, project_context: str) -> Dict[str, str]:
        if not session_id or len(session_id) > 200:
            raise ValueError("invalid session id")
        key = context_key(project_context)
        self.store.set_session_context(session_id, key)
        return {"context_key": key}

    def _session_context_key(self, session_id: Optional[str], fallback: str) -> str:
        return (self.store.session_context(session_id) if session_id else None) or context_key(fallback)

    def observe_prompt(self, prompt: str, session_id: str, turn_id: str,
                       project_context: str = "") -> Dict[str, Any]:
        """Record a local, text-free judgment even if the agent never searches."""
        if not session_id or len(session_id) > 200 or not turn_id or len(turn_id) > 200:
            raise ValueError("invalid session or turn id")
        existing = self.store.prompt_observation(turn_id)
        if existing:
            return {"decision_id": existing, "turn_id": turn_id, "already_observed": True}
        eligible = []
        for entry in self.catalog.values():
            try:
                self.policy.check_entry(entry)
                eligible.append(entry)
            except (PermissionError, ValueError):
                continue
        decision = lexical_decision(prompt[:8000], eligible)
        decision["backend"] = "prompt-observer"
        decision_id = self.store.add_decision(
            session_id, self._session_context_key(session_id, project_context),
            hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24], None,
            decision, decision["candidates"], "not_searched",
        )
        self.store.add_prompt_observation(turn_id, session_id, decision_id)
        return {"decision_id": decision_id, "turn_id": turn_id,
                "suggested_capability_id": decision["recommendation"],
                "need_reason": decision["need_reason"]}

    def search(self, task: str, context: str = "",
               session_id: Optional[str] = None,
               turn_id: Optional[str] = None) -> Dict[str, Any]:
        eligible = []
        unavailable = []
        for entry in self.catalog.values():
            try:
                self.policy.check_entry(entry)
                eligible.append(entry)
            except (PermissionError, ValueError) as exc:
                unavailable.append({"id": entry.id, "reason": str(exc)})
        decision = self.router.rank(task, eligible, context)
        indexed = {entry.id: entry for entry in eligible}
        if (session_id and turn_id and decision.get("backend") == "lexical"
                and decision.get("recommendation") is None
                and not CAPABILITY_REJECTION.search(task)
                and not SUPPLIED_MATERIAL.search(task)):
            observation_id = self.store.prompt_observation(turn_id)
            observation = self.store.get_decision(observation_id) if observation_id else None
            if observation and observation["session_id"] == session_id:
                suggested = observation["recommendation"]
                score = next((item["score"] for item in decision["candidates"]
                              if item["id"] == suggested), 0)
                if suggested in indexed and score >= 2:
                    decision = {**decision, "backend": "prompt-observer-assisted",
                                "need_probability": observation["need_probability"],
                                "need_reason": "original_prompt_recommendation",
                                "confidence": observation["confidence"],
                                "recommendation": suggested,
                                "prompt_observation_id": observation_id}
        candidates = []
        for item in decision["candidates"]:
            entry = indexed[item["id"]]
            candidates.append({**entry.summary(), "score": item["score"],
                               "cached": self.installer.package_path(entry).is_dir()})
        result = {
            "recommendation": decision["recommendation"],
            "decision": {key: value for key, value in decision.items() if key != "candidates"},
            "candidates": candidates,
            "unavailable": unavailable,
            "context_key": self._session_context_key(session_id, context),
            "context_source": "session" if session_id and self.store.session_context(session_id)
                              else "argument",
        }
        if session_id is not None:
            if not session_id or len(session_id) > 200:
                raise ValueError("invalid session id")
            self.store.mark_prompt_searched(session_id, turn_id)
            task_hash = hashlib.sha256(task.encode("utf-8")).hexdigest()[:24]
            task_text = task[:2000] if os.environ.get("CAPMGR_STORE_DECISION_TEXT") == "1" else None
            result["decision_id"] = self.store.add_decision(
                session_id, result["context_key"], task_hash, task_text,
                decision, decision["candidates"], "searched",
            )
        return result

    def resolve(self, task: str, session_id: str, context: str = "",
                turn_id: Optional[str] = None) -> Dict[str, Any]:
        search = self.search(task, context, session_id, turn_id)
        chosen = search["recommendation"]
        if not chosen:
            self.store.mark_decision(search["decision_id"], "not_selected")
            return {"status": "no_confident_match", "search": search}
        try:
            capability = self.activate(chosen, session_id)
        except Exception:
            self.store.mark_decision(search["decision_id"], "activation_failed")
            raise
        self.store.mark_decision(search["decision_id"], "activated")
        return {"status": "activated", "search": search, "capability": capability}

    def activate(self, capability_id: str, session_id: str) -> Dict[str, Any]:
        if not session_id or len(session_id) > 200:
            raise ValueError("invalid session id")
        entry = self._entry(capability_id)
        package = self.installer.install(entry)
        skills = []
        paths = [package / "SKILL.md"] + sorted((package / "skills").glob("*/SKILL.md"))
        for path in paths:
            if path.is_file():
                if path.stat().st_size > 30000:
                    raise ValueError("skill instructions exceed the per-file limit")
                skills.append({"path": str(path.relative_to(package)),
                               "instructions": path.read_text(encoding="utf-8")})
        servers = server_configs(package)
        if not skills and not servers:
            raise ValueError("installed package has no supported capability")
        self.store.activate(session_id, capability_id)
        output = {"id": capability_id, "kind": entry.kind, "skills": skills,
                  "mcp_servers": {}, "connection_errors": []}
        for name in servers:
            try:
                output["mcp_servers"][name] = self.list_tools(session_id, capability_id, name)
            except Exception as exc:
                output["connection_errors"].append({"server": name, "error": str(exc)})
        return output

    def _connection(self, session_id: str, capability_id: str, server_name: str):
        if not self.store.is_active(session_id, capability_id):
            raise PermissionError("capability is not active in this session")
        entry = self._entry(capability_id)
        package = self.installer.package_path(entry)
        configs = server_configs(package)
        if server_name not in configs:
            raise ValueError("unknown MCP server: " + server_name)
        key = (session_id, capability_id, server_name)
        if key not in self.connections:
            self.connections[key] = connect(configs[server_name], package, self.data_dir, self.policy)
        return self.connections[key]

    def list_tools(self, session_id: str, capability_id: str, server_name: str) -> List[Dict[str, Any]]:
        key = (session_id, capability_id, server_name)
        if key not in self.tool_catalogs:
            response = self._connection(*key).request("tools/list")
            tools = response.get("tools", [])
            if not isinstance(tools, list):
                raise ValueError("invalid MCP tools/list response")
            self.tool_catalogs[key] = tools
        return self.tool_catalogs[key]

    def invoke(self, session_id: str, capability_id: str, server_name: str,
               tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        tools = self.list_tools(session_id, capability_id, server_name)
        selected = next((tool for tool in tools if tool.get("name") == tool_name), None)
        if selected is None:
            raise ValueError("tool was not advertised by the MCP server")
        read_only = selected.get("annotations", {}).get("readOnlyHint") is True
        self.policy.check_tool(capability_id, tool_name, read_only)
        if arguments is not None and not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        return self._connection(session_id, capability_id, server_name).request(
            "tools/call", {"name": tool_name, "arguments": arguments or {}}
        )

    def record_outcome(self, session_id: str, capability_id: str,
                       context: str = "", success: bool = False) -> Dict[str, Any]:
        if not self.store.is_active(session_id, capability_id):
            raise PermissionError("capability is not active in this session")
        key = (self.store.session_context(session_id)
               or self.store.latest_decision_context(session_id, capability_id)
               or (context_key(context) if context else None))
        if key is None:
            raise ValueError("recording requires a session context or prior decision")
        self.store.record(key, capability_id, session_id, success)
        return {"recorded": True, "context_key": key,
                "warm_for_context": capability_id in self.store.warm_ids(key)}

    def record_decision_feedback(self, session_id: str, decision_id: str,
                                 correct_capability_id: str) -> Dict[str, Any]:
        decision = self.store.get_decision(decision_id)
        if decision is None or decision["session_id"] != session_id:
            raise ValueError("unknown decision for this session")
        if correct_capability_id not in ("none", "other") and correct_capability_id not in self.catalog:
            raise ValueError("correct capability must be a catalog id, none, or other")
        self.store.add_feedback(decision_id, correct_capability_id)
        predicted = decision["recommendation"] or "none"
        return {"decision_id": decision_id, "predicted": predicted,
                "correct": correct_capability_id,
                "matched": predicted == correct_capability_id}

    def decision_report(self, days: int = 30) -> Dict[str, Any]:
        if not 1 <= days <= 365:
            raise ValueError("days must be between 1 and 365")
        return self.store.decision_report(days)

    def list_decisions(self, days: int = 30, limit: int = 50,
                       pending_only: bool = True) -> Dict[str, Any]:
        if not 1 <= days <= 365 or not 1 <= limit <= 100:
            raise ValueError("invalid decision list range")
        return {"decisions": self.store.decisions(days, limit, pending_only)}

    def prefetch(self, context: str) -> Dict[str, Any]:
        key = context_key(context)
        installed = []
        for capability_id in self.store.warm_ids(key):
            if capability_id in self.catalog:
                self.installer.install(self.catalog[capability_id])
                installed.append(capability_id)
        return {"context_key": key, "prefetched": installed}

    def release(self, session_id: str) -> Dict[str, Any]:
        for key in list(self.connections):
            if key[0] == session_id:
                self.connections.pop(key).close()
                self.tool_catalogs.pop(key, None)
        return {"released": self.store.release(session_id)}
