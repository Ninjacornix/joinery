"""Explicit real-app test: pinned MCP -> offline worker -> reviewed native files."""

import asyncio
import tempfile
from pathlib import Path

from joinery.gateway import ProjectGateway, result_text


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-real-mcp-") as directory:
        project = Path(directory)
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
            preview = await gateway.finish()
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
            preview = await gateway.finish()
            assert preview
            await gateway.decide(preview["transaction_id"], True)
            assert (project / "level.tscn").is_file()
            result = await gateway.call(
                "godot",
                "read_scene",
                {"projectPath": str(project), "scenePath": "level.tscn"},
            )
            assert not result.is_error, result_text(result)
            print("Real MCP offline create/add/reject/accept/read: PASS")
        finally:
            await gateway.close()


if __name__ == "__main__":
    asyncio.run(main())
