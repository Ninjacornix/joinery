"""Real Codex native-source/export/scene workflow in a technical fixture only.

Only this disposable greybox test mocks the art-direction response and gate.
No real project's approval is created or altered. All operations still use the
real MCP gateway, offline native applications, provenance and review checks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from mcp.types import CallToolResult, TextContent

from bridge.codex_session import CodexSession
from bridge.protocol import Done, Message, MessageDelta, Prompt
from joinery.authoring import run_offline
from joinery.catalog import load_catalog
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC
from joinery.lockfile import check_assets
from joinery.recipe import rebuild_all
from joinery.runner import SandboxRunner
from joinery.transactions import inventory
from joinery.wiring import write_agent_wiring

SOURCE = "art/sources/technical_prop.blend"
EXPORT = "art/models/technical_prop.glb"
SCENE = "technical_prop.tscn"
ART = "Technical greybox fixture only: two rectangular meshes Head and Grip. No finished game artwork."


class TechnicalGateway(ProjectGateway):
    async def call(
        self, server: str, tool: str, arguments: dict[str, Any]
    ) -> CallToolResult:
        if server == "joinery-registry" and tool == "art_direction":
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {"approved": True, "text": ART, "technical_fixture": True}
                        ),
                    )
                ]
            )
        return await super().call(server, tool, arguments)


class MeteredRPC(GatewayRPC):
    def __init__(self, gateway: ProjectGateway) -> None:
        super().__init__(gateway)
        self.turn = "setup"
        self.calls: list[dict[str, Any]] = []

    async def _client(self, reader: Any, writer: Any) -> None:
        entry: dict[str, Any] = {"turn": self.turn}
        started = time.monotonic()

        class Reader:
            async def readline(self) -> bytes:
                line = cast(bytes, await reader.readline())
                request = json.loads(line)
                params = request.get("params", {})
                entry.update(
                    method=request.get("method"),
                    server=params.get("server", ""),
                    tool=params.get("tool", ""),
                    query=params.get("query", ""),
                    input_bytes=len(line),
                )
                return line

        class Writer:
            def write(self, data: bytes) -> None:
                response = json.loads(data)
                entry.update(
                    output_bytes=len(data),
                    ok=response.get("ok"),
                    tool_error=response.get("result", {}).get("isError", False),
                )
                writer.write(data)

            def __getattr__(self, name: str) -> Any:
                return getattr(writer, name)

        try:
            await super()._client(
                cast(asyncio.StreamReader, Reader()),
                cast(asyncio.StreamWriter, Writer()),
            )
        finally:
            entry["seconds"] = round(time.monotonic() - started, 3)
            self.calls.append(entry)
            print("RPC " + json.dumps(entry), flush=True)


async def geometry(project: Path, width: float) -> dict[str, Any]:
    code = (
        "import bpy,json; print('GEOMETRY:'+json.dumps({o.name:"
        "{'dimensions':list(o.dimensions),'vertices':len(o.data.vertices),"
        "'position':list(o.location)} for o in bpy.data.objects if o.type=='MESH'}))"
    )
    got = await run_offline(
        ["blender", "--background", SOURCE, "--python-expr", code],
        project,
        readonly=True,
    )
    assert got.ok, got.output
    line = next(
        line for line in got.output.splitlines() if line.startswith("GEOMETRY:")
    )
    data: dict[str, Any] = json.loads(line.removeprefix("GEOMETRY:"))
    assert set(data) == {"Head", "Grip"}, data
    for name, expected in (("Head", [width, 0.4, 0.3]), ("Grip", [0.2, 0.2, 1.0])):
        assert data[name]["vertices"] == 8, data
        assert all(
            abs(a - b) < 0.0001 for a, b in zip(data[name]["dimensions"], expected)
        ), data
    for name, expected in (("Head", [0, 0, 1.0]), ("Grip", [0, 0, 0.35])):
        assert all(
            abs(a - b) < 0.0001 for a, b in zip(data[name]["position"], expected)
        ), data
    assert not check_assets(project, load_catalog())
    return data


async def reopen(project: Path, width: float) -> None:
    # Verification helper exists only in a separate disposable copy, never in
    # the accepted project or the model's transaction.
    assert f"res://{EXPORT}" in (project / SCENE).read_text()
    with tempfile.TemporaryDirectory(prefix="joinery-prop-reopen-") as directory:
        copy = Path(directory) / "project"
        shutil.copytree(
            project, copy, ignore=shutil.ignore_patterns(".joinery", ".git")
        )
        (copy / "verify_prop.gd").write_text(
            "extends SceneTree\nfunc _initialize():\n"
            f'    var packed = load("res://{SCENE}")\n'
            '    assert(packed is PackedScene, "Saved scene must reopen")\n'
            "    var scene = packed.instantiate()\n"
            '    assert(scene.name == "TechnicalPropLevel" and scene.get_child_count() == 1)\n'
            '    assert(scene.get_child(0).name == "TechnicalProp")\n'
            "    root.add_child(scene)\n"
            '    var head = scene.find_child("Head", true, false)\n'
            '    var grip = scene.find_child("Grip", true, false)\n'
            "    assert(head is MeshInstance3D and grip is MeshInstance3D)\n"
            f"    assert(abs(head.mesh.get_aabb().size.x * head.scale.x - {width}) < 0.0001)\n"
            '    print("GODOT_PROP_REOPEN:PASS")\n    quit()\n'
        )
        imported = await run_offline(
            ["godot", "--headless", "--path", "/work", "--editor", "--import"],
            copy,
            180,
        )
        assert imported.ok and "SCRIPT ERROR:" not in imported.output, imported.output
        smoke = await run_offline(
            ["godot", "--headless", "--path", "/work", "--script", "verify_prop.gd"],
            copy,
            60,
        )
        assert smoke.ok and "GODOT_PROP_REOPEN:PASS" in smoke.output, smoke.output
        assert "SCRIPT ERROR:" not in smoke.output, smoke.output


async def main(metrics_path: Path) -> None:
    metrics: dict[str, Any] = {"stages": [], "result": "running"}
    rpc: MeteredRPC | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="joinery-agent-prop-") as directory:
            project = Path(directory)
            (project / "project.godot").write_text(
                'config_version=5\n[application]\nconfig/name="Technical prop fixture"\n'
            )
            (project / "joinery.toml").write_text(
                '[tools]\nenabled=["godot","blender"]\n'
            )
            (project / "joinery.lock").write_text("version=1\n")
            (project / "art").mkdir()
            (project / "art/bible.md").write_text(ART)
            write_agent_wiring(project)
            gateway = TechnicalGateway(project)
            rpc = MeteredRPC(gateway)
            await rpc.start()
            session = CodexSession()
            session.gateway_socket = rpc.path

            async def stage(name: str, operation: Any) -> Any:
                start = time.monotonic()
                try:
                    return await operation
                finally:
                    row = {"name": name, "seconds": round(time.monotonic() - start, 3)}
                    metrics["stages"].append(row)
                    print("STAGE " + json.dumps(row), flush=True)

            async def turn(name: str, text: str) -> None:
                rpc.turn = name
                gateway.request_summary = text[:160]
                done = False
                async for event in session.send(
                    Prompt(project_dir=str(project), text=text)
                ):
                    if isinstance(event, (Message, MessageDelta)):
                        print(event.text, end="", flush=True)
                    if isinstance(event, Done):
                        done = event.ok
                assert done, "Real provider did not complete successfully"

            try:
                with patch("joinery.gateway.approved", return_value=True), patch("joinery.submission.approved", return_value=True):
                    await stage("connect", session.connect(project))
                    await stage(
                        "create_model_turn",
                        turn(
                            "create",
                            f"Create a disposable technical greybox assembly, not finished artwork. Read art_direction. "
                            f"Use the connected gateway's logged Blender document operator to create {SOURCE} and export {EXPORT}. "
                            "Create exactly two cube meshes named Head and Grip, no default camera/light. "
                            "Head dimensions must be (1.0,0.4,0.3) metres and location (0,0,1.0); "
                            "Grip dimensions (0.2,0.2,1.0) and location (0,0,0.35). Apply mesh scale. "
                            f"Then create {SCENE} with a Node3D root named TechnicalPropLevel and one instance of that GLB "
                            "named TechnicalProp at the origin. No live editor is attached: use saved-file tools. "
                            "Discover actual schemas, operate the gateway, leave one staged preview for review. Do not create a manual substitute scene mesh.",
                        ),
                    )
                    assert not (project / SOURCE).exists()
                    assert gateway.transaction
                    first = await stage(
                        "create_native_verify", geometry(gateway.transaction.stage, 1.0)
                    )
                    preview = await stage("create_review", gateway.finish())
                    assert preview and preview["media"]
                    await stage(
                        "create_accept", gateway.decide(preview["transaction_id"], True)
                    )
                    assert (project / SCENE).is_file() and (project / EXPORT).is_file()
                    baseline = inventory(project)
                    update = (
                        f"The initial preview was accepted. Change the SAME native source {SOURCE}: "
                        "make only Head 1.5 metres wide along X, retain its other dimensions and position, "
                        "and preserve Grip exactly. Refresh every existing export and keep the saved scene's "
                        "same TechnicalProp instance referencing that export. Reuse previously read schemas; "
                        "resolve the registered native source. Leave changes staged for review."
                    )
                    await stage(
                        "update_reject_model_turn", turn("update_reject", update)
                    )
                    assert inventory(project) == baseline
                    assert gateway.transaction
                    changed = await stage(
                        "update_reject_native_verify",
                        geometry(gateway.transaction.stage, 1.5),
                    )
                    assert changed["Grip"] == first["Grip"]
                    assert changed["Head"]["position"] == first["Head"]["position"]
                    preview = await stage("update_reject_review", gateway.finish())
                    assert preview
                    await stage(
                        "reject", gateway.decide(preview["transaction_id"], False)
                    )
                    assert inventory(project) == baseline
                    await stage(
                        "update_accept_model_turn",
                        turn(
                            "update_accept",
                            "The previous update was rejected and all files restored. Please perform that same Head-width update again on the existing source and refresh its exports. Keep Grip unchanged. Leave the new preview staged.",
                        ),
                    )
                    assert gateway.transaction
                    changed = await stage(
                        "update_accept_native_verify",
                        geometry(gateway.transaction.stage, 1.5),
                    )
                    assert changed["Grip"] == first["Grip"]
                    preview = await stage("update_accept_review", gateway.finish())
                    assert preview
                    await stage(
                        "update_accept", gateway.decide(preview["transaction_id"], True)
                    )
                    accepted = inventory(project)
                    rebuilt = await stage(
                        "clean_recipe_replay",
                        asyncio.to_thread(rebuild_all, project, SandboxRunner()),
                    )
                    assert rebuilt and all(item.ok for item in rebuilt), rebuilt
                    assert inventory(project) == accepted
                    await stage("godot_reopen_smoke", reopen(project, 1.5))
                    assert inventory(project) == accepted
                    metrics["result"] = "PASS"
                    metrics["geometry"] = changed
                    print(
                        "\nReal Codex source/export/Godot instance/create/accept/update/reject/update/accept/clean replay/reopen: PASS",
                        flush=True,
                    )
            finally:
                await session.close()
                await rpc.close()
    except BaseException as exc:
        metrics["result"] = "FAIL"
        metrics["error"] = str(exc)
        raise
    finally:
        metrics["rpc"] = rpc.calls if rpc else []
        metrics_path.write_text(json.dumps(metrics, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--metrics", type=Path, default=Path("/tmp/joinery-agent-prop-metrics.json")
    )
    asyncio.run(asyncio.wait_for(main(parser.parse_args().metrics), 1200))
