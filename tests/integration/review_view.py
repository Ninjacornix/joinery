"""Explicit real-app test: a reviewer reads the staged change through the real worker.

    uv run python tests/integration/review_view.py

The author stages a new scene and a changed script with the pinned Godot MCP in
joinery-mcp-worker. The reviewer's reads (its own gateway endpoint, role
``reviewer``) then go to a second worker that mounts the stage read-only: the new
scene and the changed script are read by their res:// paths, the answers name
project paths, a write is refused both by the gateway and by the read-only mount,
and another task's stage can't be read.
"""

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any

from joinery.gateway import VIEW, ProjectGateway, result_text
from joinery.gateway_rpc import GatewayRPC, rpc

SCENE = '[gd_scene format=3]\n\n[node name="Keep" type="Node3D"]\n'
SCRIPT = "extends Node\n\nfunc _ready() -> void:\n\tprint(\"after\")\n"


async def write(gateway: ProjectGateway, path: str, text: str) -> None:
    result = await gateway.call(
        "godot",
        "write_file",
        {"projectPath": str(gateway.project), "filePath": path, "content": text},
    )
    assert not result.is_error, result_text(result)


async def as_reviewer(
    gateway: ProjectGateway, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    serving = GatewayRPC(gateway, role="reviewer")
    await serving.start()
    try:
        return await rpc(
            serving.path,
            "call",
            {"server": "godot", "tool": tool, "arguments": arguments},
        )
    finally:
        await serving.close()


def text_of(data: dict[str, Any]) -> str:
    return "\n".join(str(part.get("text", "")) for part in data.get("content", []))


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-review-view-") as directory:
        project = Path(directory).resolve()
        (project / "project.godot").write_text(
            'config_version=5\n[application]\nconfig/name="Review view"\n'
        )
        (project / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
        (project / "scripts").mkdir()
        (project / "scripts/door.gd").write_text("extends Node\n")
        gateway = ProjectGateway(project)
        other = gateway.lane("d" * 32, "Other task")
        try:
            await gateway.discover()
            await write(gateway, "game/keep_blockout.tscn", SCENE)
            await write(gateway, "scripts/door.gd", SCRIPT)
            assert gateway.transaction is not None
            stage = gateway.transaction.stage
            assert not (project / "game/keep_blockout.tscn").exists()

            started = time.monotonic()
            data = await as_reviewer(
                gateway,
                "read_file",
                {"projectPath": str(project), "filePath": "res://game/keep_blockout.tscn"},
            )
            first = time.monotonic() - started
            assert not data.get("isError"), text_of(data)
            assert 'name="Keep"' in text_of(data)
            data = await as_reviewer(
                gateway,
                "read_file",
                {"projectPath": str(project), "filePath": "res://scripts/door.gd"},
            )
            assert 'print("after")' in text_of(data), text_of(data)
            data = await as_reviewer(
                gateway,
                "read_scene",
                {"projectPath": str(project), "scenePath": "res://game/keep_blockout.tscn"},
            )
            assert not data.get("isError"), text_of(data)
            assert "Keep" in text_of(data)
            data = await as_reviewer(
                gateway, "get_project_info", {"projectPath": str(project)}
            )
            answer = text_of(data)
            assert str(project) in answer and "/work" not in answer, answer
            assert str(stage) not in answer, answer

            view = gateway.staged[VIEW + "godot"]
            assert any(arg.endswith(":/work:ro") for arg in view.spec.args)
            try:
                await as_reviewer(
                    gateway,
                    "write_file",
                    {"projectPath": str(project), "filePath": "x.gd", "content": ""},
                )
            except RuntimeError as exc:
                assert "only inspect" in str(exc)
            else:
                raise AssertionError("The reviewer wrote a file.")
            refused = await view.call(
                "write_file",
                {"projectPath": str(stage), "filePath": "x.gd", "content": "x"},
            )
            assert refused.is_error, result_text(refused)
            assert not (stage / "x.gd").exists()

            await write(other, "game/other.tscn", SCENE)
            assert other.transaction is not None
            data = await as_reviewer(
                gateway,
                "read_file",
                {"projectPath": str(project), "filePath": "res://game/other.tscn"},
            )
            assert data.get("isError"), text_of(data)
            try:
                await as_reviewer(
                    gateway,
                    "read_file",
                    {
                        "projectPath": str(other.transaction.stage),
                        "filePath": "game/other.tscn",
                    },
                )
            except RuntimeError as exc:
                assert "another" in str(exc)
            else:
                raise AssertionError("The reviewer read another task's stage.")

            result = await gateway.call(
                "godot",
                "read_file",
                {"projectPath": str(project), "filePath": "res://game/keep_blockout.tscn"},
            )
            assert not result.is_error, result_text(result)
            print(
                "Real MCP read-only stage view (new + changed file, project paths, "
                f"writes refused, other stage hidden; first view read {first:.1f}s): PASS"
            )
        finally:
            await other.close()
            await gateway.close()


if __name__ == "__main__":
    asyncio.run(main())
