"""Create a local Codex marketplace without modifying user configuration."""

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "marketplace"
PLUGIN = OUT / "plugins" / "contextual-capability-manager"


def main():
    if OUT.exists():
        shutil.rmtree(str(OUT))
    PLUGIN.mkdir(parents=True)
    for folder in ("capability_manager", "skills", "examples", "hooks", "scripts",
                   ".codex-plugin", ".claude-plugin"):
        shutil.copytree(str(ROOT / folder), str(PLUGIN / folder),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for filename in ("plugin.json", "mcp.json", ".mcp.json"):
        shutil.copy2(str(ROOT / filename), str(PLUGIN / filename))
    index = OUT / ".agents" / "plugins"
    index.mkdir(parents=True)
    (index / "marketplace.json").write_text(json.dumps({
        "name": "local-capabilities",
        "interface": {"displayName": "Local Capabilities"},
        "plugins": [{
            "name": "contextual-capability-manager",
            "source": {"source": "local", "path": "./plugins/contextual-capability-manager"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        }],
    }, indent=2) + "\n", encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
