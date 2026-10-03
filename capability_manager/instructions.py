"""Select relevant skill instructions and bound their combined context size."""

import re
from pathlib import Path
from typing import Any, Dict

from .catalog import Entry
from .decision import lexical_rank


MAX_SKILL_BYTES = 30000
MAX_INSTRUCTION_BYTES = 60000


def read_skills(package: Path, task: str = "", allow_unmatched: bool = False) -> Dict[str, Any]:
    root = package / "SKILL.md"
    paths = sorted(path for path in (package / "skills").glob("*/SKILL.md") if path.is_file())
    original_count = len(paths) + int(root.is_file())
    if task and len(paths) > 1:
        entries = []
        by_id = {}
        for index, path in enumerate(paths):
            with path.open(encoding="utf-8") as handle:
                header = handle.read(2000)
            metadata = {}
            if header.startswith("---\n"):
                frontmatter = header.split("---", 2)[1]
                for key in ("name", "description"):
                    match = re.search(r"^" + key + r":\s*(.+)$", frontmatter, re.MULTILINE)
                    if match:
                        metadata[key] = match[1].strip("\"'")
            identifier = "skill-" + str(index)
            entries.append(Entry(identifier, metadata.get("name", path.parent.name),
                                 metadata.get("description", header[:500]), "skill", "package",
                                 "0", [path.parent.name], {}, {}, package))
            by_id[identifier] = path
        ranked = lexical_rank(task, entries)
        unmatched = not ranked or ranked[0][1] < 2
        ambiguous = len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 2
        if unmatched or ambiguous:
            if not allow_unmatched:
                raise ValueError("skill instructions have no clear match; describe the specific skill gap")
            paths = []
        else:
            paths = [by_id[ranked[0][0].id]]
    if root.is_file():
        paths.insert(0, root)
    skills = []
    total = 0
    for path in paths:
        with path.open("rb") as handle:
            data = handle.read(MAX_SKILL_BYTES + 1)
        if len(data) > MAX_SKILL_BYTES:
            raise ValueError("skill instructions exceed the per-file limit")
        total += len(data)
        if total > MAX_INSTRUCTION_BYTES:
            raise ValueError("skill instructions exceed the combined context limit")
        skills.append({"path": str(path.relative_to(package)), "instructions": data.decode("utf-8")})
    return {"skills": skills, "instruction_bytes": total,
            "omitted_skill_count": original_count - len(skills)}
