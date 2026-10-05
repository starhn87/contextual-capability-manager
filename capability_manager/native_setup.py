"""Host installation and resumable, session-scoped OAuth for native connectors.

The CLI owns native installation. Credentials stay in memory, never in receipts,
SQLite, native configuration, or model-visible tool results.
"""

import base64
import hashlib
import json
import re
import secrets
import subprocess
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request

from .codex_catalog import listing
from .mcp_bridge import HttpConnection
from .network import open_no_redirect


def native_identity(entry):
    name, market = entry.source.get("plugin_name"), entry.source.get("marketplace")
    if not all(isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9._-]+", value)
               and not value.startswith("-") for value in (name, market)):
        raise ValueError("invalid native plugin identity")
    return name, market


class CodexInstaller:
    def verify(self, entry, policy):
        policy.check_native(entry)
        name, market = native_identity(entry)
        document = listing()
        current = next((item for item in document.get("installed", []) if item.get("name") == name
                        and item.get("marketplaceName") == market and item.get("installed") is True), None)
        if not current or current.get("enabled") is False:
            raise PermissionError("native plugin is no longer enabled on the host")
        if current.get("installPolicy") not in (None, "AVAILABLE"):
            raise PermissionError("native plugin is restricted by the host")
        return current

    def install(self, entry, policy):
        policy.check_native(entry)
        name, market = native_identity(entry)

        def installed():
            document = listing()
            return next((item for item in document.get("installed", [])
                         if item.get("name") == name and item.get("marketplaceName") == market
                         and item.get("installed") is True), None)

        current = installed()
        if current:
            if current.get("enabled") is False:
                raise PermissionError("native plugin is disabled by the host")
            return {"package_state": "native_reused", "version": str(current.get("version") or entry.version)}
        # Recheck live host restrictions instead of trusting a cached catalog.
        live = next((item for item in listing().get("available", [])
                     if item.get("name") == name and item.get("marketplaceName") == market), None)
        if not live or live.get("installPolicy") != "AVAILABLE":
            raise PermissionError("native plugin installation is restricted by the host")
        try:
            subprocess.run(["codex", "plugin", "add", name + "@" + market, "--json"],
                           capture_output=True, text=True, check=True, timeout=60)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # An interrupted CLI can have committed installation already.
            current = installed()
            if not current:
                raise RuntimeError("native installation failed; host did not confirm installation") from None
        current = installed()
        if not current or current.get("enabled") is False:
            raise RuntimeError("native installation was not confirmed by the host")
        return {"package_state": "native_installed", "version": str(current.get("version") or entry.version)}


def connection_policy(entry, policy):
    """Only the built-in official adapter receives bounded default host grants."""
    config = entry.source.get("connection")
    if not isinstance(config, dict):
        return None, policy
    name, market = native_identity(entry)
    if (name == "linear" and market in ("openai-curated", "openai-curated-remote")
            and config.get("url") == "https://mcp.linear.app/mcp/readonly"
            and config.get("scopes") == ["read"]):
        policy = replace(policy, connector_hosts=sorted(set(policy.connector_hosts + ["mcp.linear.app"])),
                         oauth_hosts=sorted(set(policy.oauth_hosts + ["mcp.linear.app"])))
    policy.check_connector_url(config["url"])
    if not isinstance(config.get("scopes"), list) or not all(
            isinstance(scope, str) and scope for scope in config["scopes"]):
        raise ValueError("invalid OAuth scopes")
    return config, policy


def json_request(url, policy, payload=None, form=False):
    policy.check_oauth_url(url)
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = (urlencode(payload).encode() if form else json.dumps(payload).encode())
        headers["Content-Type"] = ("application/x-www-form-urlencoded" if form else "application/json")
    with open_no_redirect(Request(url, data=data, headers=headers), timeout=12) as response:
        result = json.loads(response.read(256 * 1024))
    if not isinstance(result, dict):
        raise ValueError("invalid OAuth response")
    return result


class OAuthFlow:
    ttl = 300

    def __init__(self, config, policy, owner_valid=lambda: True):
        self.config, self.policy = config, policy
        self.state = secrets.token_urlsafe(32)
        self.verifier = secrets.token_urlsafe(64)
        self.created_at = time.monotonic()
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.token = None
        self.expires_at = 0
        self.error = None
        self.claimed = False
        self.closed = False
        self.server = None
        self.timer = None
        self.authorization_url = None
        self.owner_valid = owner_valid
        try:
            self._start()
        except Exception:
            self.close()
            raise

    def _start(self):
        parsed = urlparse(self.config["url"])
        metadata_url = urlunparse((parsed.scheme, parsed.netloc,
            "/.well-known/oauth-protected-resource" + parsed.path, "", "", ""))
        resource = json_request(metadata_url, self.policy)
        if resource.get("resource") != self.config["url"]:
            raise PermissionError("OAuth resource identity mismatch")
        servers = resource.get("authorization_servers", [])
        if not servers or not isinstance(servers[0], str):
            raise ValueError("OAuth authorization server is missing")
        issuer = servers[0].rstrip("/")
        self.policy.check_oauth_url(issuer)
        parsed = urlparse(issuer)
        discovery = urlunparse((parsed.scheme, parsed.netloc,
            "/.well-known/oauth-authorization-server" + parsed.path, "", "", ""))
        metadata = json_request(discovery, self.policy)
        if metadata.get("issuer", "").rstrip("/") != issuer:
            raise PermissionError("OAuth issuer identity mismatch")
        if "S256" not in metadata.get("code_challenge_methods_supported", []):
            raise PermissionError("OAuth server does not support PKCE S256")
        for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
            self.policy.check_oauth_url(metadata[key])
        self.issuer = issuer
        self.issuer_parameter = metadata.get("authorization_response_iss_parameter_supported") is True
        self.token_endpoint = metadata["token_endpoint"]
        flow = self

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                host = urlparse(flow.redirect_uri).netloc
                valid = (parsed.path == "/callback" and self.headers.get("Host") == host
                         and len(params.get("state", [])) == 1
                         and secrets.compare_digest(params["state"][0], flow.state))
                if not valid:
                    self.send_error(400, "Invalid authorization callback")
                    return
                with flow.lock:
                    if flow.claimed or flow.closed or not flow.owner_valid() or time.monotonic() - flow.created_at >= flow.ttl:
                        self.send_error(410, "Authorization has expired")
                        return
                    flow.claimed = True
                try:
                    if params.get("error"):
                        raise PermissionError("authorization_denied")
                    if (flow.issuer_parameter and params.get("iss") != [flow.issuer]):
                        raise PermissionError("issuer_mismatch")
                    if len(params.get("code", [])) != 1:
                        raise ValueError("authorization_code_missing")
                    result = json_request(flow.token_endpoint, flow.policy, {
                        "grant_type": "authorization_code", "client_id": flow.client_id,
                        "code": params["code"][0], "redirect_uri": flow.redirect_uri,
                        "code_verifier": flow.verifier, "resource": flow.config["url"]}, form=True)
                    token = result.get("access_token")
                    if not isinstance(token, str) or not token or result.get("token_type", "").lower() != "bearer":
                        raise ValueError("invalid_access_token")
                    if result.get("scope") and not set(result["scope"].split()) <= set(flow.config["scopes"]):
                        raise PermissionError("unexpected_oauth_scope")
                    expires = max(1, min(float(result.get("expires_in", 3600)), 86400))
                    with flow.lock:
                        if flow.closed or not flow.owner_valid():
                            raise PermissionError("authorization_cancelled")
                        flow.token, flow.expires_at = token, time.monotonic() + expires
                except Exception as exc:
                    # Never echo server bodies, codes, tokens, or query strings.
                    flow.error = type(exc).__name__
                finally:
                    flow.verifier = ""
                    flow.done.set()
                body = ("Authorization complete. You can return to Codex." if not flow.error else
                        "Authorization failed. Return to Codex to retry.").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
        self.server.daemon_threads = True
        self.redirect_uri = "http://127.0.0.1:" + str(self.server.server_port) + "/callback"
        registration = json_request(metadata["registration_endpoint"], self.policy, {
            "client_name": "Contextual Capability Manager", "redirect_uris": [self.redirect_uri],
            "grant_types": ["authorization_code"], "response_types": ["code"],
            "token_endpoint_auth_method": "none"})
        self.client_id = registration["client_id"]
        if not isinstance(self.client_id, str) or not self.client_id:
            raise ValueError("invalid OAuth client registration")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()
        query = {"response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
                 "scope": " ".join(self.config["scopes"]), "state": self.state,
                 "code_challenge": challenge, "code_challenge_method": "S256",
                 "resource": self.config["url"]}
        endpoint = metadata["authorization_endpoint"]
        self.authorization_url = endpoint + ("&" if "?" in endpoint else "?") + urlencode(query)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self._watch()

    def status(self, wait_seconds=0):
        self.done.wait(min(max(wait_seconds, 0), 50))
        if not self.owner_valid() and not self.closed:
            self.close()
        if self.closed or (not self.token and time.monotonic() - self.created_at >= self.ttl):
            return "expired"
        if self.error:
            return "authentication_failed"
        if self.token and self.expires_at > time.monotonic():
            return "authenticated"
        return "awaiting_auth"

    def _watch(self):
        if self.closed:
            return
        if (not self.owner_valid() or (not self.token and time.monotonic() - self.created_at >= self.ttl)
                or (self.token and self.expires_at <= time.monotonic())):
            self.close()
            return
        self.timer = threading.Timer(2, self._watch)
        self.timer.daemon = True
        self.timer.start()

    def stop_callback(self):
        with self.lock:
            server, self.server = self.server, None
        if server:
            if hasattr(self, "worker"):
                server.shutdown()
            server.server_close()

    def close(self):
        with self.lock:
            self.closed = True
            self.token = None
            self.verifier = ""
            self.done.set()
        if self.timer:
            self.timer.cancel()
        self.stop_callback()


class OAuthConnection(HttpConnection):
    def __init__(self, url, policy, flow):
        super().__init__(url, None, policy)
        self.flow = flow

    def auth_headers(self):
        if self.flow.status() != "authenticated":
            raise AuthenticationRequired("connector authentication expired")
        return {"Authorization": "Bearer " + self.flow.token}

    def request(self, method, params=None):
        try:
            result = super().request(method, params)
        except RuntimeError:
            raise RuntimeError("authenticated connector request failed") from None
        token = self.flow.token
        def redact(value):
            if isinstance(value, str):
                return value.replace(token, "[credential redacted]") if token else value
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, dict):
                return {redact(key): redact(item) for key, item in value.items()}
            return value
        return redact(result)


def authentication_required(exc):
    return isinstance(exc, AuthenticationRequired) or (isinstance(exc, HTTPError) and exc.code in (401, 403))


class AuthenticationRequired(PermissionError):
    pass
