"""Start the MCP server with the same storage resolution used by session hooks."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capability_manager.mcp_server import main
from capability_manager.runtime import configure_data_dir


if __name__ == "__main__":
    configure_data_dir(Path(__file__).resolve().parent.parent)
    main()
