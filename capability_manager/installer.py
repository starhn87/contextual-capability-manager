import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request

from .catalog import Entry
from .network import open_no_redirect
from .policy import Policy, within


MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_BYTES = 80 * 1024 * 1024
MAX_FILES = 1000


def _check_directory(source: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(str(source))
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("capability package contains a symlink")


def check_static_skill(source: Path) -> None:
    """Accept bounded skill text without executable plugin components."""
    if not source.is_dir():
        raise ValueError("git source subdirectory does not exist")
    if any((source / marker).exists() for marker in ("hooks", ".mcp.json", "mcp.json")):
        raise PermissionError("remote plugin has executable components; review it before enabling")
    for manifest in (source / "plugin.json", source / ".claude-plugin/plugin.json",
                     source / ".codex-plugin/plugin.json"):
        if manifest.is_file():
            document = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError("plugin manifest must be an object")
            if any(document.get(key) for key in ("hooks", "mcpServers", "lspServers")):
                raise PermissionError("remote plugin declares executable components")
            extensions = document.get("extensions") or {}
            if not isinstance(extensions, dict):
                raise ValueError("plugin extensions must be an object")
            openai = extensions.get("com.openai") or {}
            if not isinstance(openai, dict):
                raise ValueError("OpenAI plugin extension must be an object")
            if any(openai.get(key) for key in ("hooks", "mcpServers", "lspServers")):
                raise PermissionError("plugin extension declares executable components")
    if not (source / "SKILL.md").is_file() and not any(
            (source / "skills").glob("*/SKILL.md")):
        raise ValueError("remote plugin has no skill instructions")
    count = total = 0
    for current, directories, files in os.walk(source):
        directories[:] = [name for name in directories if name != ".git"]
        for name in directories + files:
            path = Path(current) / name
            if path.is_symlink():
                raise ValueError("remote plugin contains a symlink")
            if path.is_file():
                count += 1
                total += path.stat().st_size
                if count > MAX_FILES or total > MAX_EXTRACTED_BYTES:
                    raise ValueError("remote plugin exceeds size limits")


def _fetch_git(source: dict, staging: Path) -> Path:
    repository = staging / "repository"
    repository.mkdir()
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    def run(*arguments: str) -> str:
        process = subprocess.run(
            ["git", "-C", str(repository), "-c", "core.hooksPath=" + os.devnull,
             "-c", "submodule.recurse=false", *arguments],
            capture_output=True, text=True, timeout=60, env=environment,
        )
        if process.returncode:
            raise RuntimeError("git source could not be fetched or verified: " +
                               process.stderr.strip()[:300])
        return process.stdout.strip()
    run("init", "--quiet")
    run("fetch", "--depth", "1", "--no-tags", source["url"], source["sha"])
    actual = run("rev-parse", "FETCH_HEAD").lower()
    if actual != source["sha"].lower():
        raise ValueError("git source commit does not match pinned SHA")
    run("checkout", "--quiet", "--detach", actual)
    package = (repository / source.get("subdir", "")).resolve()
    if not within(package, repository.resolve()):
        raise ValueError("git source subdirectory escapes checkout")
    check_static_skill(package)
    return package


def _extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(str(archive)) as handle:
        members = handle.infolist()
        if len(members) > MAX_FILES or sum(item.file_size for item in members) > MAX_EXTRACTED_BYTES:
            raise ValueError("capability archive exceeds size limits")
        for item in members:
            name = Path(item.filename)
            if name.is_absolute() or ".." in name.parts or item.filename.startswith("\\"):
                raise ValueError("capability archive contains an unsafe path")
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("capability archive contains a symlink")
            target = destination / name
            if not within(target.resolve(), destination.resolve()):
                raise ValueError("capability archive escapes destination")
        handle.extractall(str(destination))


class Installer:
    def __init__(self, cache_dir: Path, policy: Policy):
        self.cache_dir = cache_dir
        self.policy = policy
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def package_path(self, entry: Entry) -> Path:
        return self.cache_dir / entry.id / entry.version

    def install(self, entry: Entry) -> Path:
        self.policy.check_entry(entry)
        final = self.package_path(entry)
        if final.is_dir():
            return final
        final.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="staging-", dir=str(final.parent)))
        try:
            if entry.source["type"] == "directory":
                source = (entry.catalog_path.parent / entry.source["path"]).resolve()
                _check_directory(source)
                shutil.copytree(str(source), str(staging / "package"))
            elif entry.source["type"] == "git":
                source = _fetch_git(entry.source, staging)
                shutil.copytree(str(source), str(staging / "package"),
                                ignore=shutil.ignore_patterns(".git"))
            else:
                request = Request(entry.source["url"], headers={"User-Agent": "capability-manager/0.1"})
                with open_no_redirect(request, timeout=15) as response:
                    if response.geturl() != entry.source["url"]:
                        raise ValueError("package redirects are not allowed")
                    data = response.read(MAX_ARCHIVE_BYTES + 1)
                if len(data) > MAX_ARCHIVE_BYTES:
                    raise ValueError("capability archive exceeds download limit")
                if hashlib.sha256(data).hexdigest() != entry.source["sha256"].lower():
                    raise ValueError("package digest mismatch")
                archive = staging / "package.zip"
                archive.write_bytes(data)
                (staging / "package").mkdir()
                _extract_zip(archive, staging / "package")
            package = staging / "package"
            if not any((package / marker).is_file() for marker in
                       ("SKILL.md", "plugin.json", ".codex-plugin/plugin.json",
                        ".claude-plugin/plugin.json")) and not any(
                            (package / "skills").glob("*/SKILL.md")):
                raise ValueError("package has no skill or plugin manifest")
            os.replace(str(package), str(final))
            return final
        finally:
            shutil.rmtree(str(staging), ignore_errors=True)
