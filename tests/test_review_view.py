"""Read-only agents read the change under review as staged, never another task's.

The Godot MCP here is a stand-in for the offline worker: it serves the folder its
docker spec mounts at /work, joins file arguments onto ``projectPath`` the way the
pinned server does (so a ``res://`` it is given names a folder called "res:"), and
refuses to write through a read-only mount.
"""

from __future__ import annotations

import asyncio
import posixpath
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from joinery.gateway import VIEW, ProjectGateway, result_text
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.mcp_runtime import (
    CHANGE_FOLDERS,
    ServerSpec,
    godot_relative,
    names_changes,
    worker_root,
)
from joinery.workers import godot_worker

OLD_SCRIPT = "extends Node\n# before\n"
NEW_SCRIPT = "extends Node\n# after\n"
NEW_SCENE = '[gd_scene format=3]\n[node name="Keep" type="Node3D"]\n'


def _text(text: str, error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=error)


class FakeWorker:
    """The pinned Godot MCP in the worker, reading the folder it mounts."""

    def __init__(self, spec: ServerSpec, _project: Path) -> None:
        self.spec = spec
        self.tools = {"read_file": object(), "write_file": object()}
        self.root = worker_root(spec)
        assert self.root is not None
        self.readonly = any(arg.endswith(":/work:ro") for arg in spec.args)

    async def connect(self, timeout: float = 0) -> None:
        return None

    async def call(
        self, tool: str, arguments: dict[str, Any], timeout: float = 0
    ) -> CallToolResult:
        assert self.root is not None
        project = Path(str(arguments["projectPath"]))
        if not project.is_relative_to(self.root):
            return _text(f"Path outside the allowed folder: {project}", True)
        if not (project / "project.godot").is_file():
            return _text(f"Not a valid Godot project: {project}", True)
        target = Path(posixpath.join(str(project), str(arguments["filePath"])))
        if tool == "write_file":
            if self.readonly:
                return _text("Failed to write file: EROFS: read-only file system", True)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(arguments["content"]))
            return _text(f"File written: {arguments['filePath']}")
        if not target.is_file():
            return _text(f"File does not exist: {arguments['filePath']}", True)
        return _text(f"{target}\n{target.read_text()}")

    async def close(self) -> None:
        return None


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/door.gd").write_text(OLD_SCRIPT)
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    monkeypatch.setattr("joinery.gateway.MCPConnection", FakeWorker)

    class Installed:
        revision = "test"

    monkeypatch.setattr(
        "joinery.server_install.load_installed", lambda _tool: Installed()
    )
    return tmp_path


def wire(gateway: ProjectGateway) -> ProjectGateway:
    """The project's own connection: the read-only worker on the project."""

    async def connection(_server: str) -> FakeWorker:
        return FakeWorker(godot_worker(gateway.project, readonly=True), gateway.project)

    gateway.connection = connection  # type: ignore[method-assign]
    return gateway


async def stage(gateway: ProjectGateway) -> Path:
    """The author's change: a new scene and a changed script, written by Godot."""
    for path, text in (("game/keep.tscn", NEW_SCENE), ("scripts/door.gd", NEW_SCRIPT)):
        result = await gateway.call(
            "godot",
            "write_file",
            {"projectPath": str(gateway.project), "filePath": path, "content": text},
        )
        assert not result.is_error, result_text(result)
    assert gateway.transaction is not None
    return gateway.transaction.stage


def run(step: Callable[[], Awaitable[Any]]) -> Any:
    return asyncio.run(step())


async def as_reviewer(
    gateway: ProjectGateway, arguments: dict[str, Any], tool: str = "read_file"
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
    return "\n".join(
        str(part.get("text", "")) for part in data.get("content", [])
    )


def test_the_reviewer_reads_new_and_changed_files_as_staged(project: Path) -> None:
    async def go() -> None:
        gateway = wire(ProjectGateway(project))
        try:
            staged = await stage(gateway)
            assert not (project / "game/keep.tscn").exists()
            for path, text in (
                ("res://game/keep.tscn", NEW_SCENE),
                ("scripts/door.gd", NEW_SCRIPT),
                ("res://scripts/door.gd", NEW_SCRIPT),
            ):
                data = await as_reviewer(
                    gateway, {"projectPath": str(project), "filePath": path}
                )
                answer = text_of(data)
                assert not data.get("isError"), answer
                assert text in answer
                # Answered in project paths, never the stage's.
                assert str(project / path.removeprefix("res://")) in answer
                assert str(staged) not in answer
            # The stage folder the reviewer's prompt names reads the same change.
            data = await as_reviewer(
                gateway, {"projectPath": str(staged), "filePath": "scripts/door.gd"}
            )
            assert NEW_SCRIPT in text_of(data)
            # Its reads use a read-only view of the stage, not the author's worker.
            view = gateway.staged[VIEW + "godot"]
            assert isinstance(view, FakeWorker) and view.readonly
            assert view.root == staged.resolve()
            assert not gateway.staged["godot"].readonly  # type: ignore[attr-defined]
        finally:
            await gateway.close()

    run(go)


def test_the_reviewer_cannot_write(project: Path) -> None:
    async def go() -> None:
        gateway = wire(ProjectGateway(project))
        try:
            staged = await stage(gateway)
            with pytest.raises(RuntimeError, match="only inspect"):
                await as_reviewer(
                    gateway,
                    {
                        "projectPath": str(project),
                        "filePath": "scripts/door.gd",
                        "content": "broken",
                    },
                    tool="write_file",
                )
            with pytest.raises(ValueError, match="only reads"):
                await gateway.call(
                    "godot",
                    "write_file",
                    {"projectPath": str(project), "filePath": "x.gd", "content": ""},
                    view=True,
                )
            # Even the view's own mount refuses a write.
            await gateway.call(
                "godot",
                "read_file",
                {"projectPath": str(project), "filePath": "scripts/door.gd"},
                view=True,
            )
            refused = await gateway.staged[VIEW + "godot"].call(
                "write_file",
                {"projectPath": str(staged), "filePath": "x.gd", "content": ""},
            )
            assert refused.is_error and "read-only" in result_text(refused)
            assert (staged / "scripts/door.gd").read_text() == NEW_SCRIPT
            assert not (staged / "x.gd").exists()
        finally:
            await gateway.close()

    run(go)


def test_another_tasks_stage_is_never_visible(project: Path) -> None:
    async def go() -> None:
        root = wire(ProjectGateway(project))
        other = wire(root.lane("b" * 32, "Other task"))
        try:
            mine = await stage(root)
            result = await other.call(
                "godot",
                "write_file",
                {
                    "projectPath": str(project),
                    "filePath": "game/secret.tscn",
                    "content": "other",
                },
            )
            assert not result.is_error
            assert other.transaction is not None
            theirs = other.transaction.stage
            # By project path: the reviewer of my change reads my stage only.
            data = await as_reviewer(
                root, {"projectPath": str(project), "filePath": "game/secret.tscn"}
            )
            assert data.get("isError") and "does not exist" in text_of(data)
            # By the other stage's folder, or a path into it: refused.
            for arguments in (
                {"projectPath": str(theirs), "filePath": "game/secret.tscn"},
                {
                    "projectPath": str(project),
                    "filePath": str(theirs.relative_to(project) / "game/secret.tscn"),
                },
            ):
                with pytest.raises(RuntimeError, match="no agent reads another"):
                    await as_reviewer(root, arguments)
            assert (theirs / "game/secret.tscn").read_text() == "other"
            assert not (mine / "game/secret.tscn").exists()
        finally:
            await other.close()
            await root.close()

    run(go)


def test_a_reviewer_with_no_staged_change_reads_the_project_but_no_stage(
    project: Path,
) -> None:
    async def go() -> None:
        root = wire(ProjectGateway(project))
        other = wire(root.lane("c" * 32, "Other task"))
        try:
            await stage(other)
            assert other.transaction is not None
            relative = other.transaction.stage.relative_to(project)
            data = await as_reviewer(
                root, {"projectPath": str(project), "filePath": "scripts/door.gd"}
            )
            assert OLD_SCRIPT in text_of(data)
            with pytest.raises(RuntimeError, match="no agent reads another"):
                await as_reviewer(
                    root,
                    {
                        "projectPath": str(project),
                        "filePath": f"{relative}/scripts/door.gd",
                    },
                )
        finally:
            await other.close()
            await root.close()

    run(go)


def test_the_author_reads_its_own_staged_edits(project: Path) -> None:
    async def go() -> None:
        gateway = wire(ProjectGateway(project))
        try:
            await stage(gateway)
            for path, text in (
                ("res://game/keep.tscn", NEW_SCENE),
                ("res://scripts/door.gd", NEW_SCRIPT),
            ):
                result = await gateway.call(
                    "godot", "read_file", {"projectPath": str(project), "filePath": path}
                )
                assert not result.is_error, result_text(result)
                assert text in result_text(result)
            assert VIEW + "godot" not in gateway.staged
        finally:
            await gateway.close()

    run(go)


def test_godot_file_arguments_lose_res_prefix_and_change_folders_are_named() -> None:
    assert godot_relative(
        {
            "projectPath": "/p",
            "filePath": "res://a/b.gd",
            "scenePath": "res://c.tscn",
            "texturePath": "t.png",
            "properties": {"texture": "res://keep.png"},
        }
    ) == {
        "projectPath": "/p",
        "filePath": "a/b.gd",
        "scenePath": "c.tscn",
        "texturePath": "t.png",
        "properties": {"texture": "res://keep.png"},
    }
    assert names_changes({"a": ["/p/.joinery/transactions/x/stage/a.gd"]})
    assert names_changes(".joinery\\transactions\\x")
    assert not names_changes({"a": "/p/.joinery/previews/x.png", "b": 3})
    assert "another" in CHANGE_FOLDERS
