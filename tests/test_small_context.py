"""Tool results reach agents short, with the full output on request (ADR 0063).

A long result comes back as a summary plus a reference; details(ref) pages the rest.
The editor's node list is an outline (no properties) in the prompt and in
inspect_editor; inspect_editor(node=…) gives one node's properties. A Godot
write_file can carry find/replace pairs instead of the whole file.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bridge.protocol import Prompt
from bridge.provider_process import project_map, turn_text
from joinery import compact
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import COMPACTED, GatewayRPC, rpc
from joinery.mcp_runtime import PolicyStop
from joinery.roles import READ_ONLY_ROLES, offered_tools
from joinery.text_patch import apply_patch
from servers.joinery_registry.proxy import make_server


def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    return tmp_path


def scene(count: int, scene_path: str = "res://level.tscn") -> list[dict[str, object]]:
    """An editor node list as the addon sends it: transforms and up to 32 properties."""
    rows: list[dict[str, object]] = []
    for i in range(count):
        path = "." if i == 0 else ("Props/" if i > 4 else "") + f"Node{i}"
        rows.append(
            {
                "id": f"{scene_path}::{path}",
                "name": "Level" if i == 0 else f"Node{i}",
                "type": "Node3D" if i == 0 else "MeshInstance3D",
                "position": [i * 1.5, 0.0, -i * 0.25],
                "rotation_degrees": [0.0, 90.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "properties": {f"property_{k}": k * 0.5 for k in range(32)},
                **({"script": "res://game/level.gd"} if i == 0 else {}),
            }
        )
    return rows


def editor(count: int) -> dict[str, str]:
    return {
        "scene_path": "res://level.tscn",
        "nodes": json.dumps(scene(count)),
        "node_manifest_count": str(count),
    }


# --- Summaries ------------------------------------------------------------------------


def test_a_small_result_is_left_alone(tmp_path: Path) -> None:
    value = {"status": "clean", "problems": [{"rule": "r", "file": "a.gd"}]}
    assert compact.compact(tmp_path, "precheck", value) == value
    assert not (tmp_path / compact.RESULTS).exists()


def test_a_long_result_is_a_summary_with_a_reference(tmp_path: Path) -> None:
    log = "\n".join(
        f"line {i}: something happened at frame {i * 16}" for i in range(2000)
    )
    value = {
        "status": "problems",
        "attempts_left": 2,
        "problems": [{"rule": "missing", "file": f"art/{i}.png"} for i in range(300)],
        "log": log,
    }
    short = compact.compact(tmp_path, "precheck", value)
    assert compact.size(short) < compact.size(value) / 8
    # Scalars an agent decides on are never cut.
    assert short["status"] == "problems" and short["attempts_left"] == 2
    assert "more items: ask for details" in short["problems"][-1]
    assert "more lines: ask for details" in short["log"]
    assert short["log"].startswith("line 0:") and short["log"].endswith(log[-40:])
    ref = short["details"]["ref"]
    assert any("problems: " in cut for cut in short["details"]["left_out"])
    # The full result is a details call away, page by page.
    page = compact.details(tmp_path, ref, "log")
    assert page["lines"].split("\n")[0] == "line 0: something happened at frame 0"
    assert page["total_lines"] == 2000 and page["next_offset"] == compact.PAGE_LINES
    last = compact.details(tmp_path, ref, "log", offset=1990)
    assert last["lines"].endswith("line 1999: something happened at frame 31984")
    assert last["next_offset"] is None
    assert json.loads(compact.details(tmp_path, ref, "problems/250")["lines"]) == {
        "rule": "missing",
        "file": "art/250.png",
    }


def test_images_are_kept_and_not_counted(tmp_path: Path) -> None:
    picture = {"type": "image", "data": "A" * 200_000, "mimeType": "image/png"}
    value = {"content": [picture, {"type": "text", "text": "ok"}]}
    assert compact.compact(tmp_path, "call", value) == value
    long = {"content": [picture, {"type": "text", "text": "x\n" * 20_000}]}
    short = compact.compact(tmp_path, "call", long)
    assert short["content"][0] == picture
    assert "more lines" in short["content"][1]["text"]


def test_a_json_string_is_cut_as_json(tmp_path: Path) -> None:
    value = {"nodes": json.dumps([{"id": i, "name": f"n{i}"} for i in range(500)])}
    short = compact.compact(tmp_path, "call", value)
    listed = json.loads(short["nodes"])
    assert listed[0] == {"id": 0, "name": "n0"}
    assert listed[-1].startswith("…") and "more items" in listed[-1]


def test_details_refuses_unknown_and_foreign_references(tmp_path: Path) -> None:
    for ref in ("../../etc/passwd", "r123", "rzzzzzzzzzzzz"):
        with pytest.raises(ValueError, match="Unknown result"):
            compact.details(tmp_path, ref)
    with pytest.raises(ValueError, match="no longer kept"):
        compact.details(tmp_path, "r0123456789ab")


def test_old_results_are_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compact, "KEEP", 3)
    refs = [compact.store(tmp_path, "call", {"n": i}) for i in range(5)]
    kept = {p.stem for p in (tmp_path / compact.RESULTS).glob("*.json")}
    assert len(kept) == 3 and refs[-1] in kept


# --- Through the gateway --------------------------------------------------------------


def test_the_gateway_summarises_long_answers_and_serves_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        gateway.editor_context = editor(300)
        server = GatewayRPC(gateway)
        reviewer = GatewayRPC(gateway, role="reviewer")

        async def checks(kind: str, params: dict[str, object]) -> dict[str, object]:
            return {"kind": kind, "findings": [{"text": "x" * 80} for _ in range(400)]}

        server.checks = checks
        await server.start()
        await reviewer.start()
        try:
            found = await rpc(server.path, "perf_check", {})
            assert found["kind"] == "perf" and len(found["findings"]) <= 21
            ref = found["details"]["ref"]
            # Any agent of the project reads it back, a read-only one too.
            page = await rpc(
                reviewer.path, "details", {"ref": ref, "path": "findings/399"}
            )
            assert json.loads(page["lines"]) == {"text": "x" * 80}

            outline = (await rpc(server.path, "editor_context", {}))["editor"]
            assert len(outline["nodes"]) == compact.INSPECT_NODES
            assert outline["nodes"][0] == ". (Node3D) script=res://game/level.gd"
            assert "180 more nodes" in outline["nodes_note"]
            assert '"property_' not in json.dumps(outline)
            node = await rpc(server.path, "editor_context", {"node": "Props/Node7"})
            assert node["nodes"][0]["properties"]["property_3"] == 1.5
            with pytest.raises(RuntimeError, match="No node"):
                await rpc(server.path, "editor_context", {"node": "Nowhere"})
        finally:
            await reviewer.close()
            await server.close()

    asyncio.run(run())


def test_every_long_answer_kind_is_summarised() -> None:
    assert {
        "call",
        "inventory",
        "submit",
        "precheck",
        "playtest",
        "reproduce",
    } <= COMPACTED
    # Schemas and the role's own reports must reach it whole.
    assert not {"schema", "details", "report_review", "editor_context"} & COMPACTED


def test_every_role_may_read_details() -> None:
    for role in ("lead", "worker", *READ_ONLY_ROLES):
        assert "details" in offered_tools(role), role
    tools = asyncio.run(make_server(Path("g.sock"), "reviewer").list_tools())
    assert "details" in {t.name for t in tools}


# --- The prompt's editor context ------------------------------------------------------


def turn(count: int, text: str = "Make the door red") -> Prompt:
    return Prompt(
        text=text,
        project_dir="/nonexistent/game",
        editor_context={**editor(count), "scene_revision": "3"},
    )


def test_the_prompt_holds_an_outline_of_the_scene() -> None:
    head = project_map(turn(200))
    assert '"property_' not in head and "rotation_degrees" not in head
    stable = json.loads(head.removeprefix("Editor context:\n"))
    assert len(stable["nodes"]) == compact.OUTLINE_NODES
    assert stable["nodes"][1] == "Node1 (MeshInstance3D) at 1.5,0,-0.25"
    assert "120 more nodes" in stable["nodes_note"]
    # Still one spelling: two turns on the same scene share it byte for byte.
    assert project_map(turn(200, "Add a lamp")) == head


def test_editor_context_tokens_with_fake_agents() -> None:
    """The scene part of every prompt, before (ADR 0056) and now, for a 60-node scene
    with the addon's 32 properties per node: four characters a token."""
    prompt = turn(60)
    before = {k: v for k, v in prompt.editor_context.items() if k != "scene_revision"}
    old = "Editor context:\n" + json.dumps(before, sort_keys=True, ensure_ascii=False)
    new = project_map(prompt)
    old_tokens, new_tokens = len(old) // 4, len(new) // 4
    assert new_tokens < old_tokens / 8, (old_tokens, new_tokens)
    # Five turns of a Claude lead: the scene part is in every request.
    assert 5 * (old_tokens - new_tokens) > 30_000
    assert turn_text(prompt).endswith("Make the door red")


# --- Patches instead of whole files ---------------------------------------------------


def test_a_patch_changes_only_what_it_names() -> None:
    text = "extends Node\n\nvar speed := 4.0\nvar jump := 9.0\n"
    patched = apply_patch(
        text, [{"find": "var speed := 4.0", "replace": "var speed := 6.0"}]
    )
    assert patched == text.replace("4.0", "6.0")
    with pytest.raises(ValueError, match="matches 0 times"):
        apply_patch(text, [{"find": "var run", "replace": "x"}])
    with pytest.raises(ValueError, match="matches 2 times"):
        apply_patch(text, [{"find": "var ", "replace": "x"}])
    with pytest.raises(ValueError, match="exactly find"):
        apply_patch(text, [{"find": "", "replace": "x"}])
    with pytest.raises(ValueError, match="list of 1 to"):
        apply_patch(text, [])


def test_the_gateway_turns_a_patch_into_the_staged_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    (root / "player.gd").write_text("extends Node\nvar speed := 4.0\n")
    gateway = ProjectGateway(root)
    gateway.staging()
    arguments = gateway._patched(
        {
            "projectPath": str(root),
            "filePath": "res://player.gd",
            "patch": [{"find": "4.0", "replace": "6.0"}],
        }
    )
    assert arguments == {
        "projectPath": str(root),
        "filePath": "res://player.gd",
        "content": "extends Node\nvar speed := 6.0\n",
    }
    with pytest.raises(ValueError, match="already has"):
        gateway._patched(
            {"filePath": "new.gd", "patch": [{"find": "a", "replace": "b"}]}
        )
    with pytest.raises(ValueError, match="already has"):
        gateway._patched(
            {"filePath": "../outside.gd", "patch": [{"find": "a", "replace": "b"}]}
        )
    with pytest.raises(ValueError, match="not both"):
        gateway._patched(
            {
                "filePath": "player.gd",
                "content": "x",
                "patch": [{"find": "a", "replace": "b"}],
            }
        )
    # The patched file is judged like any other write.
    monkeypatch.setattr(
        "joinery.gateway.validate_arguments",
        lambda value: (
            (_ for _ in ()).throw(PolicyStop("stop")) if "bad" in str(value) else None
        ),
    )
    with pytest.raises(PolicyStop):
        gateway._patched(
            {"filePath": "player.gd", "patch": [{"find": "Node", "replace": "bad"}]}
        )
