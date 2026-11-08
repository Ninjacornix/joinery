"""Actual schema discovery and read-only calls for every integrated MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from joinery.mcp_runtime import MCPConnection
from joinery.server_install import configured
from tests.upstream.plan import CORE_SERVERS


async def run() -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="joinery-all-mcp-probes-") as directory:
        project = Path(directory)
        (project / "project.godot").write_text("config_version=5\n")
        (project / "joinery.toml").write_text(
            '[tools]\nenabled=["godot","blender","inkscape","supercollider","krita","material-maker"]\n'
        )
        (project / "joinery.lock").write_text("version=1\n")
        specs, missing = configured(project)
        if missing:
            raise RuntimeError(
                "Install pinned servers before native probes: "
                + ", ".join(item.name for item in missing)
            )
        if {spec.name for spec in specs} != CORE_SERVERS:
            raise RuntimeError(
                "Integrated MCP coverage differs from the explicit test plan."
            )
        reports = []
        probes = {
            "godot": ("get_project_info", {"projectPath": str(project)}),
            "blender": ("get_blendfile_summary_datablocks", {}),
            "inkscape": ("inkscape_system", {"operation": "diagnostics"}),
            "claude-collider": ("cc_status", {}),
            "joinery-registry": ("list_assets", {}),
            "joinery-verify": ("validate_project", {}),
        }
        for spec in specs:
            connection = MCPConnection(spec, project)
            try:
                await connection.connect(timeout=30)
                if not connection.tools:
                    raise RuntimeError("A connected server exposed no tools.")
                tool, arguments = probes.get(spec.name, ("document_capabilities", {}))
                result = await connection.call(tool, arguments, timeout=30)
                report = {
                    "server": spec.name,
                    "revision": spec.revision,
                    "schema_count": len(connection.tools),
                    "probe": tool,
                    "probe_succeeded": not result.is_error,
                    "content": [
                        part.model_dump(mode="json", by_alias=True, exclude_none=True)
                        for part in result.content
                    ],
                }
            except Exception as error:  # noqa: BLE001 - Record each failed probe and continue the inventory.
                report = {
                    "server": spec.name,
                    "revision": spec.revision,
                    "schema_count": len(connection.tools),
                    "probe_succeeded": False,
                    "error": str(error),
                }
            finally:
                await connection.close()
            reports.append(report)
            print(json.dumps(report), flush=True)
        return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = asyncio.run(run())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(reports, indent=2) + "\n")
    raise SystemExit(int(any(not report["probe_succeeded"] for report in reports)))
