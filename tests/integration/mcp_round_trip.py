"""Explicit real-app test: pinned MCP -> offline worker -> reviewed native files.

The pinned Godot MCP and the Godot it drives both run in joinery-mcp-worker
with no network; the agent's project paths go in and come back as host paths.
"""

import asyncio
import json
import tempfile
from pathlib import Path

from joinery.gateway import ProjectGateway, result_text
from joinery.submission import check_and_open


async def project_path(gateway: ProjectGateway, project: Path) -> str:
    """Where the server says the project is, as the agent reads it."""
    result = await gateway.call(
        "godot", "get_project_info", {"projectPath": str(project)}
    )
    assert not result.is_error, result_text(result)
    text = result_text(result)
    assert "/work" not in text, text
    return str(json.loads(text)["path"])


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-real-mcp-") as directory:
        project = Path(directory).resolve()
        (project / "project.godot").write_text(
            'config_version=5\n[application]\nconfig/name="MCP smoke"\n'
        )
        (project / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
        gateway = ProjectGateway(project)
        try:
            discovered = await gateway.discover()
            assert len(discovered["tools"]) > 100
            result = await gateway.call(
                "godot",
                "create_scene",
                {
                    "projectPath": str(project),
                    "scenePath": "level.tscn",
                    "rootNodeType": "Node3D",
                },
            )
            assert not result.is_error, result_text(result)
            assert not (project / "level.tscn").exists()
            result = await gateway.call(
                "godot",
                "add_node",
                {
                    "projectPath": str(project),
                    "scenePath": "level.tscn",
                    "parentNodePath": ".",
                    "nodeType": "Node3D",
                    "nodeName": "Box",
                },
            )
            assert not result.is_error, result_text(result)
            # Asked inside the staged change, answered in project paths.
            assert await project_path(gateway, project) == str(project)
            preview = await check_and_open(gateway)
            assert preview
            await gateway.decide(preview["transaction_id"], False)
            assert not (project / "level.tscn").exists()
            result = await gateway.call(
                "godot",
                "create_scene",
                {
                    "projectPath": str(project),
                    "scenePath": "level.tscn",
                    "rootNodeType": "Node3D",
                },
            )
            assert not result.is_error, result_text(result)
            preview = await check_and_open(gateway)
            assert preview
            await gateway.decide(preview["transaction_id"], True)
            assert (project / "level.tscn").is_file()
            result = await gateway.call(
                "godot",
                "read_scene",
                {"projectPath": str(project), "scenePath": "level.tscn"},
            )
            assert not result.is_error, result_text(result)
            # Asked of the read-only worker on the project itself.
            assert await project_path(gateway, project) == str(project)
            print("Real MCP offline create/add/reject/accept/read: PASS")
        finally:
            await gateway.close()


if __name__ == "__main__":
    asyncio.run(main())
