import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List
from urllib.parse import urlparse

from .catalog import Entry


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class Policy:
    publishers: List[str]
    kinds: List[str]
    local_roots: List[Path]
    download_hosts: List[str]
    connector_hosts: List[str]
    decision_hosts: List[str]
    allowed_secret_env: List[str]
    allowed_read_tools: List[str]
    allow_executable: bool
    executable_ids: List[str]
    allow_external_write: bool
    allowed_write_tools: List[str]
    native_marketplaces: List[str] = field(default_factory=list)
    native_ids: List[str] = field(default_factory=list)
    oauth_hosts: List[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Policy":
        path = path.expanduser().resolve()
        raw = json.loads(path.read_text(encoding="utf-8"))
        roots = [(path.parent / item).resolve() for item in raw.get("local_roots", [])]
        return cls(
            publishers=raw.get("publishers", []),
            kinds=raw.get("kinds", ["skill"]),
            local_roots=roots,
            download_hosts=raw.get("download_hosts", []),
            connector_hosts=raw.get("connector_hosts", []),
            decision_hosts=raw.get("decision_hosts", []),
            allowed_secret_env=raw.get("allowed_secret_env", []),
            allowed_read_tools=raw.get("allowed_read_tools", []),
            allow_executable=bool(raw.get("allow_executable", False)),
            executable_ids=raw.get("executable_ids", []),
            allow_external_write=bool(raw.get("allow_external_write", False)),
            allowed_write_tools=raw.get("allowed_write_tools", []),
            native_marketplaces=raw.get("native_marketplaces", []),
            native_ids=raw.get("native_ids", []),
            oauth_hosts=raw.get("oauth_hosts", []),
        )

    def check_entry(self, entry: Entry) -> None:
        if entry.source["type"] == "native":
            self.check_native(entry)
            return
        if entry.publisher not in self.publishers or entry.kind not in self.kinds:
            raise PermissionError("publisher or capability kind is outside the approved policy")
        if entry.permissions.get("execute") and not self.can_execute(entry.id):
            raise PermissionError("executable capability needs separate approval")
        if entry.permissions.get("external_write") and not self.allow_external_write:
            raise PermissionError("external write capability needs separate approval")
        source = entry.source
        if source["type"] == "directory":
            value = source.get("path")
            if not isinstance(value, str):
                raise ValueError("directory source has no path")
            candidate = (entry.catalog_path.parent / value).resolve()
            if not any(within(candidate, root) for root in self.local_roots):
                raise PermissionError("local source is outside approved roots")
        elif source["type"] == "https_zip":
            url = urlparse(str(source.get("url", "")))
            if url.scheme != "https" or url.hostname not in self.download_hosts:
                raise PermissionError("download host is outside approved policy")
            digest = source.get("sha256", "")
            if not isinstance(digest, str) or len(digest) != 64:
                raise PermissionError("HTTPS packages require a pinned SHA-256 digest")
        elif source["type"] == "git":
            url = urlparse(str(source.get("url", "")))
            if (url.scheme != "https" or url.hostname not in self.download_hosts or
                    url.username or url.password or url.query or url.fragment):
                raise PermissionError("git source host is outside approved policy")
            sha = source.get("sha", "")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", sha):
                raise PermissionError("git source requires a pinned commit SHA")
            subdir = source.get("subdir", "")
            if (not isinstance(subdir, str) or Path(subdir).is_absolute() or
                    ".." in Path(subdir).parts or "\\" in subdir):
                raise PermissionError("git source contains an unsafe subdirectory")

    def check_connector_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("https", "http"):
            raise PermissionError("unsupported connector URL")
        if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost"):
            raise PermissionError("plain HTTP is limited to loopback")
        if parsed.hostname not in self.connector_hosts:
            raise PermissionError("connector host is outside approved policy")

    def check_secret_env(self, name: str) -> None:
        if name not in self.allowed_secret_env:
            raise PermissionError("credential environment variable is outside approved policy")

    def check_native(self, entry: Entry) -> None:
        source = entry.source
        if entry.kind not in self.kinds:
            raise PermissionError("capability kind is outside the approved policy")
        if source.get("install_policy") not in (None, "AVAILABLE"):
            raise PermissionError("native plugin installation is restricted by the host")
        if (source.get("marketplace") not in self.native_marketplaces
                and entry.id not in self.native_ids):
            raise PermissionError("native plugin needs platform installation approval in policy")
        if entry.permissions.get("execute") and not self.can_execute(entry.id):
            raise PermissionError("executable capability needs separate approval")
        if entry.permissions.get("external_write") and not self.allow_external_write:
            raise PermissionError("external write capability needs separate approval")

    def check_oauth_url(self, url: str) -> None:
        parsed = urlparse(url)
        if (parsed.hostname not in self.oauth_hosts or parsed.username or parsed.password
                or parsed.fragment or parsed.scheme not in ("https", "http")
                or (parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost"))):
            raise PermissionError("OAuth endpoint is outside approved policy")

    def can_execute(self, capability_id: str) -> bool:
        return self.allow_executable or capability_id in self.executable_ids

    def check_tool(self, capability_id: str, tool_name: str, read_only: bool) -> None:
        identity = capability_id + ":" + tool_name
        if read_only and identity in self.allowed_read_tools:
            return
        if self.allow_external_write and identity in self.allowed_write_tools:
            return
        if identity in self.allowed_read_tools:
            raise PermissionError("read-approved tool is not advertised as read-only")
        raise PermissionError("tool is outside the approved action list")
