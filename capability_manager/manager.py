import hashlib
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .catalog import Entry
from .catalog_refresh import CatalogIndex
from .decision import (CAPABILITY_REJECTION, SUPPLIED_MATERIAL, DecisionRouter,
                       lexical_decision, lexical_rank)
from .installer import Installer, check_static_skill
from .instructions import read_skills
from .mcp_bridge import connect, server_configs
from .policy import Policy
from .store import Store
from .runtime import (data_dir as runtime_data_dir, platform as current_platform,
                      storage_id, check_storage, status as runtime_status)
from .session_summary import receipt, save_receipt, markdown as summary_markdown
from . import __version__


ROOT = Path(__file__).resolve().parent.parent


def context_key(context: str) -> str:
    return hashlib.sha256(context.encode("utf-8")).hexdigest()[:24]


def default_data_dir() -> Path:
    return runtime_data_dir(ROOT)


def default_config_dir() -> Path:
    return Path(os.environ.get("CAPMGR_CONFIG_DIR")
                or str(Path.home() / ".config/contextual-capability-manager"))


def runtime_platform() -> str:
    return current_platform()


class ActiveVersionChanged(PermissionError):
    pass


class CapabilityManager:
    def __init__(self, catalog_paths: Optional[List[Path]] = None,
                 policy_path: Optional[Path] = None, data_dir: Optional[Path] = None,
                 include_codex_catalog: Optional[bool] = None,
                 include_claude_catalog: Optional[bool] = None,
                 defer_discovery: bool = False):
        self.data_dir = data_dir or default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._explicit_catalog_paths = catalog_paths
        self._explicit_policy_path = policy_path
        configured = os.environ.get("CAPMGR_CATALOGS", "")
        self._configured_catalogs = configured
        self._configured_root = default_config_dir()
        self._forced_config_root = bool(os.environ.get("CAPMGR_CONFIG_DIR"))
        self._configured_policy = os.environ.get("CAPMGR_POLICY")
        self._include_claude = (
            catalog_paths is None and not configured and
            (os.environ.get("CAPMGR_INCLUDE_CLAUDE_CATALOG") == "1" or
             (os.environ.get("CAPMGR_INCLUDE_CLAUDE_CATALOG") != "0" and runtime_platform() == "claude"))
            if include_claude_catalog is None else include_claude_catalog)
        self._include_codex = (
            catalog_paths is None and not configured and
            (os.environ.get("CAPMGR_INCLUDE_CODEX_CATALOG") == "1" or
             (os.environ.get("CAPMGR_INCLUDE_CODEX_CATALOG") != "0" and runtime_platform() == "codex"))
            if include_codex_catalog is None else include_codex_catalog)
        self.index = CatalogIndex(self._catalog_configuration, self.data_dir,
                                  codex=self._include_codex, claude=self._include_claude)
        self.catalog, self.policy = self.index.refresh(local_only=defer_discovery)
        self.installer = Installer(self.data_dir / "cache", self.policy)
        self.store = Store(self.data_dir / "state.sqlite3", runtime_platform(), __version__)
        self.router = DecisionRouter(self.policy)
        self.connections = {}
        self.tool_catalogs = {}
        self.session_entries = {}

    def _catalog_configuration(self):
        configured = self._configured_catalogs
        configured_root = self._configured_root
        data_catalog = self.data_dir / "catalog.json"
        data_policy = self.data_dir / "policy.json"
        config_catalog = configured_root / "catalog.json"
        config_policy = configured_root / "policy.json"
        if data_catalog.exists() != data_policy.exists() or config_catalog.exists() != config_policy.exists():
            raise ValueError("catalog.json and policy.json must be configured together")
        if self._forced_config_root or config_catalog.exists():
            local_catalog, local_policy = config_catalog, config_policy
        else:
            local_catalog, local_policy = data_catalog, data_policy
        self.catalog_paths = self._explicit_catalog_paths if self._explicit_catalog_paths is not None else (
            [Path(item) for item in configured.split(os.pathsep) if item]
            if configured else [local_catalog if local_catalog.exists()
                             else ROOT / "examples/catalog.json"]
        )
        if ((self._include_claude or self._include_codex) and not configured and
                not local_catalog.exists() and self._explicit_catalog_paths is None):
            self.catalog_paths = []
        self.policy_path = self._explicit_policy_path or Path(self._configured_policy or
            str(local_policy if local_policy.exists() else ROOT / "examples/policy.json"))
        default_policy = (self._explicit_policy_path is None and not self._configured_policy
                          and not local_policy.exists())
        return self.catalog_paths, self.policy_path, default_policy

    def refresh_catalog(self, local_only=True, sync_remote=False, force=False):
        self.index.refresh(local_only, sync_remote, force)
        with self.index.lock:
            self.catalog, self.policy = self.index.catalog, self.index.policy
            self.installer.policy = self.policy
            self.router.policy = self.policy
        return self.index.status()

    def read_static(self, task, context=""):
        from .readonly_skill import read_static_skill
        with self.index.lock:
            self.refresh_catalog()
            return read_static_skill(self.catalog, self.policy, self.installer.cache_dir, task, context)

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
        self.store.add_event(session_id, "session_started", "bound")
        return {"context_key": key, "storage_id": storage_id(self.data_dir)}

    def check_storage(self, expected_storage_id: Optional[str] = None) -> None:
        check_storage(self.data_dir, expected_storage_id)

    def runtime_status(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        with self.index.lock:
            return {**runtime_status(self.data_dir, self.store, session_id),
                    "catalog_entries": len(self.index.catalog), "catalog_status": self.index.status()}

    def _prepare(self, entry: Entry, session_id: str) -> Path:
        previous = self.session_entries.get((session_id, entry.id))
        if previous and previous != entry and self.store.is_active(session_id, entry.id):
            raise ActiveVersionChanged("capability changed; release its current session access before reactivation")
        self.store.record_preparation(session_id, entry.summary())
        cached = self.installer.package_path(entry).is_dir()
        package = self.installer.install(entry)
        state = "cache_reused" if cached else "installed"
        self.store.record_preparation(session_id, entry.summary(), state)
        self.store.add_event(session_id, "capability_prepared", state,
                             decision_id=self.store.latest_decision_id(session_id, entry.id),
                             capability_id=entry.id)
        return package

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
        self.refresh_catalog()
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
        self.store.add_event(session_id, "prompt_observed", "recorded",
                             turn_id=turn_id, decision_id=decision_id,
                             capability_id=decision["recommendation"])
        return {"decision_id": decision_id, "turn_id": turn_id,
                "suggested_capability_id": decision["recommendation"],
                "need_reason": decision["need_reason"]}

    def search(self, task: str, context: str = "",
               session_id: Optional[str] = None,
               turn_id: Optional[str] = None,
               static_only: bool = False,
               expected_storage_id: Optional[str] = None) -> Dict[str, Any]:
        self.check_storage(expected_storage_id)
        self.refresh_catalog(local_only=False)
        started = time.monotonic()
        eligible = []
        unavailable = []
        for entry in self.catalog.values():
            try:
                self.policy.check_entry(entry)
                if static_only:
                    if entry.source["type"] == "https_zip":
                        continue
                    if entry.source["type"] == "directory":
                        source = (entry.catalog_path.parent / entry.source["path"]).resolve()
                        try:
                            check_static_skill(source)
                        except (OSError, ValueError, PermissionError):
                            continue
                eligible.append(entry)
            except (PermissionError, ValueError) as exc:
                unavailable.append({"id": entry.id, "reason": str(exc)})
        unavailable_count = len(unavailable)
        if len(unavailable) > 8:
            ranked = lexical_rank(task + " " + context, [self.catalog[item["id"]] for item in unavailable])
            selected = {entry.id for entry, _ in ranked[:8]}
            unavailable = [item for item in unavailable if item["id"] in selected]
        try:
            decision = self.router.rank(task, eligible, context)
        except Exception as exc:
            if session_id:
                self.store.add_event(session_id, "capability_searched", "error",
                                     turn_id=turn_id, error_type=type(exc).__name__)
            raise
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
            "unavailable_count": unavailable_count,
            "catalog_status": self.index.status(),
            "storage_id": storage_id(self.data_dir),
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
            self.store.add_event(session_id, "capability_searched",
                                 "recommended" if decision["recommendation"] else "abstained",
                                 turn_id=turn_id, decision_id=result["decision_id"],
                                 capability_id=decision["recommendation"],
                                 duration_ms=int((time.monotonic()-started)*1000))
        return result

    def resolve(self, task: str, session_id: str, context: str = "",
                turn_id: Optional[str] = None,
                expected_storage_id: Optional[str] = None) -> Dict[str, Any]:
        search = self.search(task, context, session_id, turn_id, expected_storage_id=expected_storage_id)
        chosen = search["recommendation"]
        if not chosen:
            self.store.mark_decision(search["decision_id"], "not_selected")
            return {"status": "no_confident_match", "search": search}
        try:
            capability = self.activate(chosen, session_id, task + " " + context)
        except PermissionError as exc:
            self.store.mark_decision(search["decision_id"], "requires_review")
            return {"status": "requires_review", "search": search,
                    "capability_id": chosen, "reason": str(exc)}
        except Exception:
            self.store.mark_decision(search["decision_id"], "activation_failed")
            raise
        status = {"ready": "activated", "partial": "partially_activated",
                  "unavailable": "unavailable"}[capability["availability"]]
        self.store.mark_decision(search["decision_id"], status)
        return {"status": status, "search": search, "capability": capability}

    def resolve_static(self, task: str, session_id: str, context: str = "",
                       turn_id: Optional[str] = None,
                       expected_storage_id: Optional[str] = None) -> Dict[str, Any]:
        """Apply only skill text, without launching a bundled server or hook."""
        search = self.search(task, context, session_id, turn_id, static_only=True,
                             expected_storage_id=expected_storage_id)
        chosen = search["recommendation"]
        if not chosen:
            self.store.mark_decision(search["decision_id"], "not_selected")
            return {"status": "no_confident_match", "search": search}
        try:
            entry = self._entry(chosen)
            if entry.source["type"] == "https_zip":
                raise PermissionError("archive source needs separate review")
            package = self._prepare(entry, session_id)
            check_static_skill(package)
            instructions = read_skills(package, task + " " + context)
            if not instructions["skills"]:
                raise ValueError("package has no skill instructions")
            self.store.activate(session_id, chosen)
            self.session_entries[(session_id, chosen)] = entry
            self.store.mark_delivery(session_id, chosen, "ready", "static_skill")
        except PermissionError as exc:
            if not isinstance(exc, ActiveVersionChanged):
                self.store.mark_delivery(session_id, chosen, "requires_review")
            self.store.mark_decision(search["decision_id"], "requires_review")
            self.store.add_event(session_id, "activation_failed", "requires_review",
                                 turn_id=turn_id, decision_id=search["decision_id"],
                                 capability_id=chosen, error_type=type(exc).__name__)
            return {"status": "requires_review", "search": search,
                    "capability_id": chosen, "reason": str(exc)}
        except Exception as exc:
            self.store.mark_delivery(session_id, chosen, "failed")
            self.store.mark_decision(search["decision_id"], "activation_failed")
            self.store.add_event(session_id, "activation_failed", "error",
                                 turn_id=turn_id, decision_id=search["decision_id"],
                                 capability_id=chosen, error_type=type(exc).__name__)
            raise
        self.store.mark_decision(search["decision_id"], "activated")
        self.store.add_event(session_id, "capability_delivered", "static_skill",
                             turn_id=turn_id, decision_id=search["decision_id"],
                             capability_id=chosen)
        return {"status": "activated", "search": search,
                "capability": {"id": chosen, "kind": entry.kind, **instructions,
                               "availability": "ready",
                               "mcp_servers": {}, "connection_errors": []}}

    def activate(self, capability_id: str, session_id: str, task: str = "") -> Dict[str, Any]:
        if not session_id or len(session_id) > 200:
            raise ValueError("invalid session id")
        self.refresh_catalog()
        started = time.monotonic()
        decision_id = self.store.latest_decision_id(session_id, capability_id)
        verified_id = None
        lease_created = False
        try:
            entry = self._entry(capability_id)
            verified_id = entry.id
            package = self._prepare(entry, session_id)
            servers = server_configs(package)
            instructions = read_skills(package, task, allow_unmatched=bool(servers))
            if not instructions["skills"] and not servers:
                raise ValueError("installed package has no supported capability")
            self.store.activate(session_id, capability_id)
            self.session_entries[(session_id, capability_id)] = entry
            lease_created = True
            output = {"id": capability_id, "kind": entry.kind, **instructions,
                      "mcp_servers": {}, "connection_errors": []}
            approved_tools = 0
            for name in servers:
                try:
                    tools = self.list_tools(session_id, capability_id, name)
                    allowed = 0
                    for tool in tools:
                        try:
                            self.policy.check_tool(capability_id, tool.get("name", ""),
                                tool.get("annotations", {}).get("readOnlyHint") is True)
                            allowed += 1
                        except PermissionError:
                            pass
                    if not allowed:
                        raise PermissionError("MCP server has no policy-approved tools")
                    output["mcp_servers"][name] = tools
                    approved_tools += allowed
                except Exception as exc:
                    output["connection_errors"].append({"server": name, "error": str(exc)})
        except Exception as exc:
            if lease_created:
                self.store.deactivate(session_id, capability_id)
                for key in list(self.connections):
                    if key[:2] == (session_id, capability_id):
                        self.tool_catalogs.pop(key, None)
                        try:
                            self.connections.pop(key).close()
                        except Exception:
                            pass
            if verified_id and not isinstance(exc, ActiveVersionChanged):
                self.store.mark_delivery(session_id, capability_id,
                                         "requires_review" if isinstance(exc, PermissionError) else "failed")
            self.store.add_event(session_id, "activation_failed", "error",
                                 decision_id=decision_id, capability_id=verified_id,
                                 error_type=type(exc).__name__,
                                 duration_ms=int((time.monotonic()-started)*1000))
            raise
        if not instructions["skills"] and not approved_tools:
            self.store.deactivate(session_id, capability_id)
            for key in list(self.connections):
                if key[:2] == (session_id, capability_id):
                    self.tool_catalogs.pop(key, None)
                    try:
                        self.connections.pop(key).close()
                    except Exception as exc:
                        output["connection_errors"].append({"server": key[2], "error": type(exc).__name__})
            output["availability"] = "unavailable"
            self.store.mark_delivery(session_id, capability_id, "unavailable", "gateway")
            self.store.add_event(session_id, "activation_failed", "unavailable",
                                 decision_id=decision_id, capability_id=capability_id,
                                 error_type="NoUsableCapability",
                                 duration_ms=int((time.monotonic()-started)*1000))
            return output
        output["availability"] = "partial" if output["connection_errors"] else "ready"
        self.store.mark_delivery(session_id, capability_id, output["availability"],
                                 "gateway" if approved_tools else "static_skill")
        self.store.add_event(session_id, "capability_delivered",
                             "gateway_partial" if output["connection_errors"] else "gateway",
                             decision_id=decision_id, capability_id=capability_id,
                             duration_ms=int((time.monotonic()-started)*1000))
        return output

    def _connection(self, session_id: str, capability_id: str, server_name: str):
        if not self.store.is_active(session_id, capability_id):
            raise PermissionError("capability is not active in this session")
        entry = self.session_entries.get((session_id, capability_id)) or self._entry(capability_id)
        self.policy.check_entry(entry)
        package = self.installer.package_path(entry)
        configs = server_configs(package)
        if server_name not in configs:
            raise ValueError("unknown MCP server: " + server_name)
        key = (session_id, capability_id, server_name)
        if key not in self.connections:
            self.connections[key] = connect(configs[server_name], package, self.data_dir,
                                            self.policy, capability_id)
        return self.connections[key]

    def list_tools(self, session_id: str, capability_id: str, server_name: str) -> List[Dict[str, Any]]:
        if not self.store.is_active(session_id, capability_id):
            raise PermissionError("capability is not active in this session")
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
        self.refresh_catalog()
        started = time.monotonic()
        decision_id = self.store.latest_decision_id(session_id, capability_id)
        verified_capability = None
        verified_tool = None
        try:
            tools = self.list_tools(session_id, capability_id, server_name)
            verified_capability = capability_id
            selected = next((tool for tool in tools if tool.get("name") == tool_name), None)
            if selected is None:
                raise ValueError("tool was not advertised by the MCP server")
            verified_tool = tool_name
            read_only = selected.get("annotations", {}).get("readOnlyHint") is True
            self.policy.check_tool(capability_id, tool_name, read_only)
            if arguments is not None and not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            result = self._connection(session_id, capability_id, server_name).request(
                "tools/call", {"name": tool_name, "arguments": arguments or {}}
            )
        except Exception as exc:
            self.store.add_event(session_id, "tool_call", "error",
                                 decision_id=decision_id, capability_id=verified_capability,
                                 tool_name=verified_tool,
                                 error_type=type(exc).__name__,
                                 duration_ms=int((time.monotonic()-started)*1000))
            raise
        self.store.add_event(session_id, "tool_call",
                             "tool_error" if result.get("isError") else "completed",
                             decision_id=decision_id, capability_id=capability_id,
                             tool_name=tool_name,
                             duration_ms=int((time.monotonic()-started)*1000))
        return result

    def record_outcome(self, session_id: str, capability_id: str,
                       context: str = "", success: bool = False) -> Dict[str, Any]:
        try:
            if not self.store.is_active(session_id, capability_id):
                raise PermissionError("capability is not active in this session")
            key = (self.store.session_context(session_id)
                   or self.store.latest_decision_context(session_id, capability_id)
                   or (context_key(context) if context else None))
            if key is None:
                raise ValueError("recording requires a session context or prior decision")
            self.store.record(key, capability_id, session_id, success)
        except Exception as exc:
            self.store.add_event(session_id, "outcome_reported", "rejected",
                                 capability_id=capability_id if self.store.is_active(session_id, capability_id)
                                 else None, error_type=type(exc).__name__)
            raise
        self.store.add_event(session_id, "outcome_reported",
                             "reported_success" if success else "reported_failure",
                             decision_id=self.store.latest_decision_id(session_id, capability_id),
                             capability_id=capability_id)
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
        self.store.add_event(session_id, "feedback_recorded", "labeled",
                             decision_id=decision_id,
                             capability_id=correct_capability_id)
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
        self.refresh_catalog()
        key = context_key(context)
        installed = []
        for capability_id in self.store.warm_ids(key):
            if capability_id in self.catalog:
                self.installer.install(self.catalog[capability_id])
                installed.append(capability_id)
        return {"context_key": key, "prefetched": installed}

    def session_summary(self, session_id: str) -> Dict[str, Any]:
        summary = receipt(self.store, session_id, self.data_dir)
        summary["storage_id"] = storage_id(self.data_dir)
        return summary

    def release(self, session_id: str, expected_storage_id: Optional[str] = None) -> Dict[str, Any]:
        self.check_storage(expected_storage_id)
        if not isinstance(session_id, str) or not session_id or len(session_id) > 200:
            raise ValueError("invalid session id")
        released = self.store.release(session_id)
        for key in list(self.session_entries):
            if key[0] == session_id:
                self.session_entries.pop(key)
        cleanup_errors = []
        for key in list(self.connections):
            if key[0] == session_id:
                self.tool_catalogs.pop(key, None)
                try:
                    self.connections.pop(key).close()
                except Exception as exc:
                    cleanup_errors.append({"capability_id": key[1], "error_type": type(exc).__name__})
                    self.store.add_event(session_id, "connection_cleanup", "error",
                                         capability_id=key[1], error_type=type(exc).__name__)
        for identifier in released:
            self.store.add_event(session_id, "capability_released", "revoked", capability_id=identifier)
        self.store.add_event(session_id, "session_released",
                             "completed_with_errors" if cleanup_errors else "completed")
        summary = self.session_summary(session_id)
        summary["summary_markdown"] = summary_markdown(summary)
        result = {"released": released, "session_summary": summary,
                  "summary_markdown": summary["summary_markdown"]}
        try:
            result["reports"] = save_receipt(summary, self.data_dir)
        except OSError as exc:
            result["report_save_error"] = type(exc).__name__
        return result

    def event_report(self, days: int = 30) -> Dict[str, Any]:
        if not 1 <= days <= 365:
            raise ValueError("days must be between 1 and 365")
        return self.store.event_report(days)
