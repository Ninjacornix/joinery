"""Read-only diagnostics run against a disposable copy, never the open project."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from joinery.authoring import run_offline
from joinery.catalog import load_catalog
from joinery.deps import check_deps
from joinery.lockfile import check_assets
from joinery.policy import load_denylist
from joinery.transactions import EXCLUDED


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    project = parser.parse_args().project.resolve()
    server: MCPServer[Any] = MCPServer("joinery-verify")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def validate_project() -> dict[str, Any]:
        """Run provenance/dependency checks and a real offline Godot import on a disposable copy. Reports actual errors without saving the editor's unsaved scene."""
        problems = check_assets(project, load_catalog()) + check_deps(
            project, load_denylist()
        )
        with tempfile.TemporaryDirectory(prefix="joinery-verify-") as directory:
            scratch = Path(directory)
            shutil.copytree(
                project,
                scratch,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*EXCLUDED),
            )
            got = await run_offline(
                ["godot", "--headless", "--path", "/work", "--editor", "--import"],
                scratch,
                180,
            )
            if (
                not got.ok
                or "SCRIPT ERROR:" in got.output
                or "Parse Error:" in got.output
            ):
                problems.append(got.output[-4000:])
        return {"ok": not problems, "problems": problems}

    server.run()


if __name__ == "__main__":
    main()
