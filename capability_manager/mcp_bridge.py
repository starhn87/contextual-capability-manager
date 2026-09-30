"""Small MCP client for session-scoped HTTP and stdio tool access."""

import json
import os
import select
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import Request

from . import __version__
from .network import open_no_redirect
from .policy import Policy, within


PROTOCOL_VERSION = "2025-03-26"


def server_configs(package: Path) -> Dict[str, Dict[str, Any]]:
    path = package / "mcp.json"
    if not path.is_file():
        path = package / ".mcp.json"
    if not path.is_file():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    servers = document.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("invalid MCP server configuration")
    return servers


class HttpConnection:
    def __init__(self, url: str, token_env: Optional[str], policy: Policy):
        policy.check_connector_url(url)
        if token_env:
            policy.check_secret_env(token_env)
        self.url = url
        self.token_env = token_env
        self.counter = 0
        self.session_id = None
        self.initialized = False

    def _send(self, payload: Dict[str, Any]):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.token_env:
            secret = os.environ.get(self.token_env, "").strip()
            if not secret:
                raise PermissionError("connector authentication required: " + self.token_env)
            headers["Authorization"] = "Bearer " + secret
        request = Request(
            self.url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        with open_no_redirect(request, timeout=20) as response:
            self.session_id = response.headers.get("Mcp-Session-Id") or self.session_id
            data = response.read(2 * 1024 * 1024)
            content_type = response.headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            for line in data.decode("utf-8").splitlines():
                if line.startswith("data:"):
                    try:
                        item = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if item.get("id") == payload.get("id"):
                        return item
            return {}
        return json.loads(data.decode("utf-8")) if data else {}

    def request(self, method: str, params: Optional[Dict[str, Any]] = None):
        self.counter += 1
        payload = {"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params or {}}
        answer = self._send(payload)
        if "error" in answer:
            raise RuntimeError(str(answer["error"]))
        return answer.get("result", {})

    def initialize(self):
        if self.initialized:
            return
        self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "contextual-capability-manager", "version": __version__},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.initialized = True

    def close(self):
        if not self.session_id:
            return
        headers = {"Mcp-Session-Id": self.session_id, "MCP-Protocol-Version": PROTOCOL_VERSION}
        if self.token_env and os.environ.get(self.token_env):
            headers["Authorization"] = "Bearer " + os.environ[self.token_env].strip()
        try:
            open_no_redirect(Request(self.url, headers=headers, method="DELETE"), timeout=3).close()
        except Exception:
            pass


class StdioConnection:
    def __init__(self, config: Dict[str, Any], package: Path, data_dir: Path, policy: Policy):
        if not policy.allow_executable:
            raise PermissionError("local executable servers need separate approval")
        command = config.get("command")
        args = config.get("args", [])
        if not isinstance(command, str) or not isinstance(args, list):
            raise ValueError("invalid stdio MCP command")
        if command.startswith("./"):
            resolved = (package / command).resolve()
            if not within(resolved, package.resolve()):
                raise PermissionError("MCP command escapes package")
            command = str(resolved)
        elif "/" in command or "\\" in command:
            raise PermissionError("MCP command must be a package path or bare executable")
        expanded_args = []
        for argument in args:
            if not isinstance(argument, str):
                raise ValueError("MCP argument is not a string")
            expanded_args.append(argument.replace("${PLUGIN_ROOT}", str(package)).replace("${PLUGIN_DATA}", str(data_dir)))
        env = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR") if key in os.environ}
        for key in config.get("env_vars", []):
            policy.check_secret_env(key)
            if key in os.environ:
                env[key] = os.environ[key]
        env["PLUGIN_ROOT"] = str(package)
        env["PLUGIN_DATA"] = str(data_dir)
        self.process = subprocess.Popen(
            [command] + expanded_args, cwd=str(package), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        self.counter = 0
        self.initialized = False

    def _send(self, payload: Dict[str, Any]):
        if self.process.poll() is not None:
            raise RuntimeError("MCP server exited")
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        if "id" not in payload:
            return {}
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.process.stdout], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("MCP server closed stdout")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == payload["id"]:
                return message
        raise TimeoutError("MCP server did not respond")

    def request(self, method: str, params: Optional[Dict[str, Any]] = None):
        self.counter += 1
        answer = self._send({"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params or {}})
        if "error" in answer:
            raise RuntimeError(str(answer["error"]))
        return answer.get("result", {})

    def initialize(self):
        if self.initialized:
            return
        self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "contextual-capability-manager", "version": __version__},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.initialized = True

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()


def connect(config: Dict[str, Any], package: Path, data_dir: Path, policy: Policy):
    kind = config.get("type")
    if kind in ("streamable-http", "http"):
        result = HttpConnection(config["url"], config.get("bearer_token_env_var"), policy)
    elif kind == "stdio" or (kind is None and "command" in config):
        result = StdioConnection(config, package, data_dir, policy)
    else:
        raise ValueError("unsupported MCP transport")
    result.initialize()
    return result
