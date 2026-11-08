"""Run the pinned official Blender addon on its background main thread."""

import sys

sys.path.insert(0, "/opt/blender-mcp/addon")
from blender_mcp_addon.cli import cli_execute

raise SystemExit(cli_execute(["--host", "127.0.0.1", "--port", "9876"]))
