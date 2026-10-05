"""Refresh registered HTTPS Git catalogs in independent, immutable snapshots."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .policy import within


CATALOG_PATHS = (".agents/plugins/marketplace.json", ".claude-plugin/marketplace.json")


@dataclass(frozen=True)
class Source:
    platform: str
    name: str
    url: str
    ref: str = "HEAD"

    @property
    def key(self):
        return hashlib.sha256((self.platform + "\n" + self.name + "\n" + self.url +
                               "\n" + self.ref).encode()).hexdigest()[:24]


def registered_source(platform, item):
    """Only registry metadata supplied by the host can authorize catalog sync."""
    if not isinstance(item, dict) or item.get("autoUpdate") is False:
        return None
    source = item.get("marketplaceSource" if platform == "codex" else "source") or {}
    kind = source.get("sourceType" if platform == "codex" else "source")
    if kind == "github":
        repo = source.get("repo", "")
        if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            return None
        url = "https://github.com/" + repo + ".git"
    elif kind in ("git", "url"):
        url = source.get("url") or source.get("source")
    else:
        return None
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        return None
    name = item.get("name")
    ref = source.get("ref") or "HEAD"
    if (not isinstance(name, str) or not name or not isinstance(ref, str) or not ref
            or ref.startswith("-") or any(char.isspace() for char in ref)):
        return None
    return Source(platform, name, url, ref)


def validate_catalog(root, name):
    found = False
    for relative in CATALOG_PATHS:
        path = root / relative
        if not path.is_file():
            continue
        if path.is_symlink() or not within(path.resolve(), root.resolve()):
            raise ValueError("catalog escapes snapshot")
        document = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(document, dict) or document.get("name") != name or
                not isinstance(document.get("plugins"), list)):
            raise ValueError("invalid marketplace identity or catalog")
        found = True
    if not found:
        raise ValueError("marketplace catalog missing")


def checkout(source, destination):
    """Git transfers files only; hooks, filters, submodules and prompts are disabled."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    deadline = time.monotonic() + 15
    def run(*args):
        result = subprocess.run(
            ["git", "-C", str(destination), "-c", "core.hooksPath=" + os.devnull,
             "-c", "init.templateDir=", "-c", "protocol.allow=never",
             "-c", "protocol.https.allow=always", "-c", "http.followRedirects=false",
             *args], env=environment, check=True, capture_output=True, text=True,
            timeout=max(0.1, deadline - time.monotonic()))
        return result.stdout.strip()
    run("init", "-q")
    run("fetch", "--depth", "1", "--no-tags", "--no-recurse-submodules", source.url, source.ref)
    sha = run("rev-parse", "FETCH_HEAD")
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", sha):
        raise ValueError("invalid Git revision")
    run("checkout", "--detach", "-q", sha)
    validate_catalog(destination, source.name)
    return sha


class SnapshotCache:
    def __init__(self, directory, ttl=21600, retry=300):
        self.directory = Path(directory)
        self.ttl = ttl
        self.retry = retry

    def _state(self, source):
        try:
            value = json.loads((self.directory / source.key / "state.json").read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def current(self, source):
        state = self._state(source)
        sha = state.get("sha", "")
        if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", sha):
            return None
        path = self.directory / source.key / sha
        if path.is_dir() and not path.is_symlink() and within(path.resolve(), self.directory.resolve()):
            return path
        return None

    def refresh(self, source, force=False):
        # Validate even callers that construct Source directly.
        checked = registered_source(source.platform, {"name": source.name,
            "marketplaceSource" if source.platform == "codex" else "source": {
                "sourceType" if source.platform == "codex" else "source": "git" if source.platform == "codex" else "url",
                "url": source.url, "ref": source.ref}})
        if checked != source:
            raise ValueError("unsupported marketplace source")
        state = self._state(source)
        now = time.time()
        interval = self.retry if state.get("status") == "error" else self.ttl
        if not force and now - state.get("attempted_at", 0) < interval:
            return self.current(source), {"name": source.name, **state}
        parent = self.directory / source.key
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = parent / "sync.lock"
        try:
            descriptor = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # A crashed worker must not prevent future sessions from refreshing.
            if now - lock.stat().st_mtime > 120:
                lock.unlink()
                return self.refresh(source, force)
            return self.current(source), {"name": source.name, **self._state(source), "status": "refreshing"}
        os.close(descriptor)
        try:
            return self._refresh_locked(source, parent, state, now)
        finally:
            lock.unlink(missing_ok=True)

    def _refresh_locked(self, source, parent, state, now):
        stage = Path(tempfile.mkdtemp(prefix="pending-", dir=str(parent)))
        state = {**state, "attempted_at": now}
        try:
            sha = checkout(source, stage)
            target = parent / sha
            if not target.exists():
                stage.rename(target)
            state.update({"sha": sha, "updated_at": now, "status": "fresh"})
            state.pop("error_type", None)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            state.update({"status": "error", "error_type": type(exc).__name__})
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        fd, temporary = tempfile.mkstemp(dir=str(parent), prefix="state-")
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle)
        os.replace(temporary, parent / "state.json")
        return self.current(source), {"name": source.name, **state}
