"""Review authority, Rule Zero propagation and safe project targeting."""

import asyncio
import json
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from joinery.art_approval import approved, fingerprint
from joinery.authoring import edit_document
from joinery.catalog import ROOT
from joinery.gateway import ProjectGateway, _map_paths
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.mcp_runtime import MCPConnection, PolicyStop, ServerSpec, validate_schema
from joinery.transactions import FileTransaction


def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    return tmp_path


def test_model_rpc_has_no_approval_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        tx = FileTransaction(tmp_path)
        tx.begin("Review")
        (tx.stage / "new.txt").write_text("staged")
        tx.review()
        gateway.transaction = tx
        server = GatewayRPC(gateway)
        await server.start()
        try:
            with pytest.raises(RuntimeError, match="no approval"):
                await rpc(
                    server.path, "decision", {"accept": True, "transaction_id": tx.id}
                )
            assert tx.state == "review"
            assert not (tmp_path / "new.txt").exists()
            await gateway.decide(tx.id, False)
        finally:
            await server.close()

    asyncio.run(run())


def test_editor_can_restore_files_when_combined_live_accept_goes_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        try:
            tx = FileTransaction(tmp_path)
            tx.begin("Combined preview")
            (tx.stage / "new.txt").write_text("reviewed")
            tx.review()
            gateway.transaction = tx
            await gateway.decide(tx.id, True)
            assert (tmp_path / "new.txt").is_file()
            result = gateway.rollback_decision(tx.id)
            assert result["accepted"] is False
            assert not (tmp_path / "new.txt").exists()
            assert tx.state == "reverted"
            with pytest.raises(ValueError, match="cannot be rolled back"):
                gateway.rollback_decision(tx.id)
        finally:
            await gateway.close()

    asyncio.run(run())


def test_editor_rollback_keeps_newer_manual_file_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        try:
            tx = FileTransaction(tmp_path)
            tx.begin("Combined preview")
            (tx.stage / "new.txt").write_text("reviewed")
            tx.review()
            gateway.transaction = tx
            await gateway.decide(tx.id, True)
            (tmp_path / "new.txt").write_text("manual")
            with pytest.raises(ValueError, match="newer manual"):
                gateway.rollback_decision(tx.id)
            assert (tmp_path / "new.txt").read_text() == "manual"
        finally:
            await gateway.close()

    asyncio.run(run())


def test_accepting_art_draft_opens_review_without_silently_approving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        try:
            tx = FileTransaction(tmp_path)
            tx.begin("Draft art direction")
            (tx.stage / "art").mkdir()
            (tx.stage / "art/bible.md").write_text("Technical fixture direction.")
            tx.review()
            gateway.transaction = tx
            decision = await gateway.decide(tx.id, True)
            assert decision["accepted"] is True
            assert "click Approve these rules" in decision["message"]
            assert decision["art_review"]["text"] == "Technical fixture direction."
            assert decision["art_review"]["sha256"] == fingerprint(tmp_path)
            assert not approved(tmp_path)
        finally:
            await gateway.close()

    asyncio.run(run())


def test_rule_zero_stop_is_latched_and_reaches_turn_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        callback = AsyncMock()
        gateway.call = AsyncMock(side_effect=PolicyStop("Test policy stop"))  # type: ignore[method-assign]
        server = GatewayRPC(gateway, callback)
        await server.start()
        try:
            with pytest.raises(RuntimeError, match="Test policy stop"):
                await rpc(server.path, "call", {"server": "godot", "tool": "test"})
            await asyncio.sleep(0)
            callback.assert_awaited_once_with("Test policy stop")
            with pytest.raises(RuntimeError, match="Test policy stop"):
                await rpc(server.path, "call", {"server": "godot", "tool": "retry"})
            assert gateway.call.await_count == 1
        finally:
            await server.close()

    asyncio.run(run())


def test_live_editor_rpc_uses_host_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        gateway.editor_context = {
            "scene_instance": "123",
            "scene_path": "res://level.tscn",
        }
        gateway.edit_scene = AsyncMock(
            return_value={"success": True, "status": "preview"}
        )
        server = GatewayRPC(gateway)
        await server.start()
        try:
            assert (await rpc(server.path, "editor_context", {}))["editor"][
                "scene_instance"
            ] == "123"
            result = await rpc(
                server.path, "editor_edit", {"summary": "Rename", "operations": []}
            )
            assert result["status"] == "preview"
            gateway.edit_scene.assert_awaited_once()
        finally:
            await server.close()

    asyncio.run(run())


def test_schema_references_never_fetch_external_content() -> None:
    validate_schema({"$defs": {"x": {"type": "string"}}, "$ref": "#/$defs/x"})
    with pytest.raises(ValueError, match="local definitions"):
        validate_schema({"properties": {"x": {"$ref": "https://example.com/schema"}}})


def test_path_mapping_keeps_siblings_and_embedded_code_intact(tmp_path: Path) -> None:
    original = tmp_path.resolve()
    stage = original / "stage"
    data = {
        "projectPath": str(original),
        "file": str(original / "scene.tscn"),
        "other": str(original) + "-other/a",
        "code": "print('res://level.tscn')",
    }
    mapped = _map_paths(data, original, stage)
    assert mapped["file"] == str(stage / "scene.tscn")
    assert mapped["other"] == data["other"]
    assert mapped["code"] == data["code"]
    assert json.loads(json.dumps(mapped))["projectPath"] == str(stage)


def test_windows_paths_map_case_slashes_and_drive_to_native_stage_and_posix_worker() -> (
    None
):
    original = PureWindowsPath("C:/Game Project")
    stage = PureWindowsPath("D:/Private Stage")
    values = {
        "root": "c:/GAME PROJECT",
        "file": "c:\\game project\\art\\model.glb",
        "sibling": "C:/Game Project-other/model.glb",
        "other_drive": "E:/Game Project/model.glb",
        "code": "print('C:/Game Project/model.glb')",
    }
    mapped = _map_paths(values, original, stage)
    assert mapped["root"] == str(stage)
    assert mapped["file"] == str(stage / "art/model.glb")
    for key in ("sibling", "other_drive", "code"):
        assert mapped[key] == values[key]
    worker = _map_paths(mapped, stage, PurePosixPath("/work"))
    assert worker["root"] == "/work"
    assert worker["file"] == "/work/art/model.glb"


def test_sentence_tool_search_finds_native_operations_without_dumping_every_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        gateway.discover = AsyncMock(
            return_value={
                "servers": [{"name": "blender", "tools": ["unrelated"] * 1000}],
                "tools": [
                    {
                        "server": "joinery-blender-ops",
                        "name": "operate_document",
                        "description": "Create a native Blender document and export model assets",
                    },
                    {
                        "server": "joinery-registry",
                        "name": "art_direction",
                        "description": "Read project art direction",
                    },
                ],
            }
        )  # type: ignore[method-assign]
        server = GatewayRPC(gateway)
        await server.start()
        try:
            result = await rpc(
                server.path,
                "inventory",
                {
                    "query": "art_direction project Blender native document create low poly textured mesh assets"
                },
            )
            assert {t["name"] for t in result["tools"]} == {
                "operate_document",
                "art_direction",
            }
            assert "tools" not in result["servers"][0]
            assert result["servers"][0]["tool_count"] == 1000
            assert len(json.dumps(result)) < 2000
        finally:
            await server.close()

    asyncio.run(run())


def test_real_proxy_preserves_tool_error_flag_and_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        gateway.call = AsyncMock(
            return_value=CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="Refresh every output of the existing source recipe: art/models/fixture.png",
                    )
                ],
                is_error=True,
            )
        )  # type: ignore[method-assign]
        server = GatewayRPC(gateway)
        await server.start()
        connection = MCPConnection(
            ServerSpec(
                name="joinery-registry",
                command=sys.executable,
                args=[
                    "-m",
                    "servers.joinery_registry.proxy",
                    "--socket",
                    str(server.path),
                ],
                cwd=str(ROOT),
            ),
            tmp_path,
        )
        try:
            await connection.connect()
            result = await connection.call(
                "call_tool", {"server": "godot", "tool": "fixture", "arguments": {}}
            )
            assert result.is_error
            assert "Refresh every output" in result.content[0].text
            assert "Error executing tool" not in result.content[0].text
        finally:
            await connection.close()
            await server.close()

    asyncio.run(run())


def test_real_native_server_returns_missing_source_reason(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["blender"]\n')

    async def run() -> None:
        connection = MCPConnection(
            ServerSpec(
                name="joinery-blender-ops",
                command=sys.executable,
                args=["-m", "servers.blender_ops", "--project", str(tmp_path)],
                cwd=str(ROOT),
            ),
            tmp_path,
        )
        try:
            await connection.connect()
            result = await connection.call(
                "inspect_document",
                {"tool": "blender", "source": "art/sources/missing.blend"},
            )
            assert result.is_error
            assert "source document is missing" in result.content[0].text
        finally:
            await connection.close()

    asyncio.run(run())


def test_partial_native_recipe_update_fails_before_touching_source(
    tmp_path: Path,
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["blender"]\n')
    (tmp_path / "art/sources").mkdir(parents=True)
    (tmp_path / "art/sources/fixture.blend").write_bytes(b"existing source")
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes/original.toml").write_text(
        'version=1\ntool="blender"\nscript="recipes/original.py"\noutputs=["art/sources/fixture.blend","art/models/fixture.glb","art/models/fixture.png"]\n'
    )
    tx = FileTransaction(tmp_path)
    tx.begin("Update source")
    with pytest.raises(ValueError, match="Refresh every output"):
        asyncio.run(
            edit_document(
                tx.stage,
                "blender",
                "art/sources/fixture.blend",
                "raise RuntimeError('must not execute')",
                ["art/models/fixture.glb"],
            )
        )
    assert (tx.stage / "art/sources/fixture.blend").read_bytes() == b"existing source"
    assert not (tx.stage / "recipes/inputs").exists()
    assert tx.diff() == []


class _FakeConnection:
    def __init__(self, seen: list[object]) -> None:
        self.spec = ServerSpec(
            name="joinery-blender-ops", command="true", source="first-party"
        )
        self.tools: dict[str, object] = {"adjust_map": object()}
        self.seen = seen

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def call(
        self, tool: str, arguments: dict, timeout: float = 60
    ) -> CallToolResult:
        self.seen.append((tool, arguments))
        return CallToolResult(
            content=[TextContent(type="text", text="refused")], is_error=True
        )


def test_unapproved_bible_gives_the_structured_art_gate_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[object] = []

    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        gateway.request_summary = "Make the steel rougher"
        connection = _FakeConnection(seen)
        monkeypatch.setattr(gateway, "connection", AsyncMock(return_value=connection))
        try:
            result = await gateway.call(
                "joinery-blender-ops",
                "adjust_map",
                {"source": "art/sources/hammer.blend", "material": "Steel"},
            )
            assert result.is_error
            assert result.structured_content == {
                "needs_art_approval": True,
                "resume": "Make the steel rougher",
                "message": result.structured_content["message"],
            }
            assert "how your game should look" in result.content[0].text
            assert gateway.transaction is None, "Nothing is staged behind the gate"
            gate = await gateway.finish()
            assert gate is not None and gate["kind"] == "art_gate"
            assert (
                gate["needs_art_approval"]
                and gate["resume"] == "Make the steel rougher"
            )
            assert await gateway.finish() is None, "The gate is reported once"
        finally:
            await gateway.close()

    asyncio.run(run())
    assert seen == [], "The tool never ran"


def test_part_targets_reach_the_stage_before_a_typed_part_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[object] = []
    root = project(tmp_path, monkeypatch)
    monkeypatch.setattr("joinery.gateway.approved", lambda _project: True)
    target = {
        "id": "res://art/models/hammer.glb#material=Steel&image=steel_rough",
        "label": "Roughness · Steel",
        "part_kind": "texture",
        "owner_source": "art/sources/hammer.blend",
        "material": "Steel",
        "image": "steel_rough",
    }

    async def run() -> None:
        gateway = ProjectGateway(root)
        gateway.target_context = (
            target,
            {"id": "res://main.tscn::Door", "label": "Door"},
        )
        connection = _FakeConnection(seen)

        async def staged_call(
            tool: str, arguments: dict, timeout: float = 60
        ) -> CallToolResult:
            stage = gateway.transaction.stage
            written = json.loads((stage / ".joinery/part_targets.json").read_text())
            seen.append(written)
            return CallToolResult(
                content=[TextContent(type="text", text="refused")], is_error=True
            )

        connection.call = staged_call  # type: ignore[method-assign]
        monkeypatch.setattr(gateway, "connection", AsyncMock(return_value=connection))
        monkeypatch.setattr(
            "joinery.gateway.MCPConnection", lambda *_a, **_k: connection
        )
        try:
            await gateway.call(
                "joinery-blender-ops",
                "adjust_map",
                {"source": "art/sources/hammer.blend"},
            )
            assert gateway.transaction is not None
            # The Review tab names the app that made the change.
            seen.append(gateway.transaction.tools)
        finally:
            await gateway.close()

    asyncio.run(run())
    assert seen == [[target], ["blender-ops"]], "Only part targets, and only this prompt's"
