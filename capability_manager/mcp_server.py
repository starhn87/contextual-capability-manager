"""MCP stdio facade for the capability manager."""

import json
import sys
from typing import Any, Dict

from . import __version__
from .manager import CapabilityManager


TOOLS = [
    {
        "name": "resolve_static_skill",
        "description": "Default choice for a missing skill: search registered but uninstalled capabilities and return only bounded skill instructions. Never starts a plugin hook or MCP server. A pinned Git skill may be downloaded to the private cache. Use with the current session ID.",
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string", "description": "Short capability gap description; omit source text, private data, and secrets."},
            "session_id": {"type": "string"},
            "turn_id": {"type": "string", "description": "Current turn ID from the prompt observer, when available."},
            "context": {"type": "string", "description": "Short task type for ranking; omit private data and secrets."}},
            "required": ["task", "session_id"], "additionalProperties": False},
    },
    {
        "name": "resolve_capability",
        "description": "For a task requiring an approved MCP server or executable plugin, search and activate a matching capability. This can start external tools, so use only when static skill guidance is insufficient and platform permission allows it.",
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string", "description": "Short capability gap description; omit source text, private data, and secrets."}, "session_id": {"type": "string"},
            "turn_id": {"type": "string", "description": "Current turn ID from the prompt observer, when available."},
            "context": {"type": "string", "description": "Short task type for ranking; omit source text, private data, and secrets. The session hook supplies the stable project key."}},
            "required": ["task", "session_id"], "additionalProperties": False},
    },
    {
        "name": "search_capabilities",
        "description": "Inspect candidate capabilities, including ones not installed yet, without installing them. Provide session_id to log this decision for later evaluation.",
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string", "description": "Short capability gap description; omit source text, private data, and secrets."},
            "context": {"type": "string", "description": "Short task type; omit source text, private data, and secrets."},
            "session_id": {"type": "string"}, "turn_id": {"type": "string"}},
            "required": ["task"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "activate_capability",
        "description": "Install and activate a specific capability allowed by the preapproved policy in this session.",
        "inputSchema": {"type": "object", "properties": {
            "capability_id": {"type": "string"}, "session_id": {"type": "string"}},
            "required": ["capability_id", "session_id"], "additionalProperties": False},
    },
    {
        "name": "invoke_capability_tool",
        "description": "Call a tool of a capability already activated in this session; tool permissions are checked on every call.",
        "inputSchema": {"type": "object", "properties": {
            "session_id": {"type": "string"}, "capability_id": {"type": "string"},
            "server_name": {"type": "string"}, "tool_name": {"type": "string"},
            "arguments": {"type": "object"}},
            "required": ["session_id", "capability_id", "server_name", "tool_name"],
            "additionalProperties": False},
    },
    {
        "name": "record_capability_result",
        "description": "Record whether an activated capability helped, using the context key already stored for this session. Do not send task text. This is usage feedback, not a label that its selection was correct.",
        "inputSchema": {"type": "object", "properties": {
            "session_id": {"type": "string"}, "capability_id": {"type": "string"},
            "success": {"type": "boolean"}},
            "required": ["session_id", "capability_id", "success"],
            "additionalProperties": False},
    },
    {
        "name": "record_decision_feedback",
        "description": "Record the correct choice for a logged decision only when the user explicitly confirms or corrects it. Use 'none' when no new capability was needed, 'other' when the right one was absent from the catalog. Never infer this label from successful activation.",
        "inputSchema": {"type": "object", "properties": {
            "session_id": {"type": "string"}, "decision_id": {"type": "string"},
            "correct_capability_id": {"type": "string"}},
            "required": ["session_id", "decision_id", "correct_capability_id"],
            "additionalProperties": False},
    },
    {
        "name": "list_capability_decisions",
        "description": "List logged decisions and their labels for review. Raw task text is absent unless explicitly enabled in configuration.",
        "inputSchema": {"type": "object", "properties": {
            "days": {"type": "integer", "minimum": 1, "maximum": 365},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "pending_only": {"type": "boolean"}}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "capability_decision_report",
        "description": "Show selection coverage and accuracy using explicitly labeled decisions, grouped by decision backend.",
        "inputSchema": {"type": "object", "properties": {
            "days": {"type": "integer", "minimum": 1, "maximum": 365}},
            "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "capability_event_report",
        "description": "Count manager-mediated prompt, selection, delivery, tool, outcome, and release events by platform and version. This contains no prompt text or tool arguments.",
        "inputSchema": {"type": "object", "properties": {
            "days": {"type": "integer", "minimum": 1, "maximum": 365}},
            "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "release_capability_session",
        "description": "Revoke temporary capabilities for a completed session while retaining cached packages.",
        "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}},
                        "required": ["session_id"], "additionalProperties": False},
    },
]


def _dispatch(manager: CapabilityManager, name: str, args: Dict[str, Any]) -> Any:
    if name == "resolve_static_skill":
        return manager.resolve_static(args["task"], args["session_id"],
                                      args.get("context", ""), args.get("turn_id"))
    if name == "resolve_capability":
        return manager.resolve(args["task"], args["session_id"], args.get("context", ""),
                               args.get("turn_id"))
    if name == "search_capabilities":
        return manager.search(args["task"], args.get("context", ""), args.get("session_id"),
                              args.get("turn_id"))
    if name == "activate_capability":
        return manager.activate(args["capability_id"], args["session_id"])
    if name == "invoke_capability_tool":
        return manager.invoke(args["session_id"], args["capability_id"],
                              args["server_name"], args["tool_name"], args.get("arguments"))
    if name == "record_capability_result":
        return manager.record_outcome(args["session_id"], args["capability_id"],
                                      success=args["success"])
    if name == "record_decision_feedback":
        return manager.record_decision_feedback(args["session_id"], args["decision_id"],
                                                args["correct_capability_id"])
    if name == "list_capability_decisions":
        return manager.list_decisions(args.get("days", 30), args.get("limit", 50),
                                      args.get("pending_only", True))
    if name == "capability_decision_report":
        return manager.decision_report(args.get("days", 30))
    if name == "capability_event_report":
        return manager.event_report(args.get("days", 30))
    if name == "release_capability_session":
        return manager.release(args["session_id"])
    raise ValueError("unknown tool: " + name)


def handle(manager: CapabilityManager, message: Dict[str, Any]) -> Dict[str, Any]:
    method = message.get("method")
    if method == "initialize":
        result = {"protocolVersion": message.get("params", {}).get("protocolVersion", "2025-03-26"),
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "contextual-capability-manager", "version": __version__}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params", {})
        try:
            value = _dispatch(manager, params["name"], params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}
        except Exception as exc:
            result = {"isError": True, "content": [
                {"type": "text", "text": json.dumps({"error": type(exc).__name__, "message": str(exc)})}
            ]}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": message.get("id"),
                "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": message.get("id"), "result": result}


def main() -> None:
    manager = CapabilityManager()
    try:
        for line in sys.stdin:
            try:
                message = json.loads(line)
                if "id" not in message:
                    continue
                response = handle(manager, message)
            except Exception as exc:
                response = {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32700, "message": type(exc).__name__ + ": " + str(exc)}}
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    finally:
        for key in list(manager.connections):
            manager.connections.pop(key).close()


if __name__ == "__main__":
    main()
