import hashlib
import os
import shutil
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
            if not any((package / marker).is_file() for marker in ("SKILL.md", "plugin.json", ".codex-plugin/plugin.json")):
                raise ValueError("package has no skill or plugin manifest")
            os.replace(str(package), str(final))
            return final
        finally:
            shutil.rmtree(str(staging), ignore_errors=True)
