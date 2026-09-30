"""Start the MCP server with the same data directory used by Claude hooks."""

import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capability_manager.mcp_server import main


def configure_data_dir(plugin_root: Path) -> Optional[Path]:
    """Recover from absent or unexpanded CLAUDE_PLUGIN_DATA in MCP env."""
    configured = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    if configured and Path(configured).is_absolute() and "${" not in configured:
        return Path(configured)
    # Installed Claude plugins live under plugins/cache/<marketplace>/<plugin>/<version>.
    cache = plugin_root.parent.parent.parent
    if cache.name == "cache" and cache.parent.name == "plugins":
        data = cache.parent / "data" / (plugin_root.parent.name + "-" + plugin_root.parent.parent.name)
        os.environ["CLAUDE_PLUGIN_DATA"] = str(data)
        return data
    os.environ.pop("CLAUDE_PLUGIN_DATA", None)
    return None


if __name__ == "__main__":
    configure_data_dir(Path(__file__).resolve().parent.parent)
    main()
