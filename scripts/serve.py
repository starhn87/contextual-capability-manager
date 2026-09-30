"""Start the MCP server from an installed plugin without relying on cwd."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capability_manager.mcp_server import main


if __name__ == "__main__":
    main()
