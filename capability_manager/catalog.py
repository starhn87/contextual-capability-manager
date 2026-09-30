import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


CAPABILITY_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    description: str
    kind: str
    publisher: str
    version: str
    tags: List[str]
    source: Dict[str, Any]
    permissions: Dict[str, bool]
    catalog_path: Path

    @classmethod
    def parse(cls, raw: Dict[str, Any], catalog_path: Path) -> "Entry":
        identifier = raw.get("id", "")
        if not isinstance(identifier, str) or not CAPABILITY_ID.fullmatch(identifier):
            raise ValueError("invalid capability id")
        kind = raw.get("kind")
        if kind not in ("skill", "plugin", "connector"):
            raise ValueError("invalid capability kind for " + identifier)
        source = raw.get("source")
        if not isinstance(source, dict) or source.get("type") not in ("directory", "https_zip", "git"):
            raise ValueError("invalid source for " + identifier)
        version = str(raw.get("version") or "0")
        if not VERSION.fullmatch(version) or version in (".", ".."):
            raise ValueError("invalid version for " + identifier)
        tags = raw.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError("invalid tags for " + identifier)
        return cls(
            id=identifier,
            name=str(raw.get("name") or identifier),
            description=str(raw.get("description") or ""),
            kind=kind,
            publisher=str(raw.get("publisher") or "unknown"),
            version=version,
            tags=tags,
            source=source,
            permissions={key: bool(value) for key, value in raw.get("permissions", {}).items()},
            catalog_path=catalog_path,
        )

    def summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "publisher": self.publisher,
            "version": self.version,
            "permissions": self.permissions,
        }


def load_catalog(paths: List[Path]) -> Dict[str, Entry]:
    entries = {}
    for path in paths:
        path = path.expanduser().resolve()
        document = json.loads(path.read_text(encoding="utf-8"))
        items = document.get("capabilities")
        if not isinstance(items, list):
            raise ValueError("catalog needs a capabilities array: " + str(path))
        for raw in items:
            entry = Entry.parse(raw, path)
            if entry.id in entries:
                raise ValueError("duplicate capability id: " + entry.id)
            entries[entry.id] = entry
    return entries
