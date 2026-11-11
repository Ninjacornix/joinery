"""A turn that builds a chair from boxes is sent back and passes as one Blender model."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from bridge import stdio
from bridge.protocol import Done, EditorAction, Message
from tests.test_complex_models import HEAD, chair
from tests.test_turn_runner import Host

PLACED = (
    HEAD.replace("load_steps=4", "load_steps=5").replace(
        '[sub_resource type="BoxMesh" id="seat"]',
        '[ext_resource type="PackedScene" path="res://art/models/chair.glb" '
        'id="1_chair"]\n\n[sub_resource type="BoxMesh" id="seat"]',
    )
    + """
[node name="Chair" parent="." instance=ExtResource("1_chair")]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 2, 0, 1)
"""
)


@pytest.mark.parametrize("agent", ["codex", "claude", "gemini", "opencode"])
def test_the_author_is_sent_back_and_resubmits_a_blender_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: str
) -> None:
    host = Host(tmp_path, monkeypatch)
    results: list[Any] = []

    async def author(_bridge: Any) -> AsyncIterator[Any]:
        assert host.gateway is not None and host.gateway.turn is not None
        assert host.prompts[0].editor_context["complex_models"] == "blender"
        tx = host.stage({"scenes/main.tscn": HEAD + chair()})
        first = await host.gateway.turn.submit("A wooden chair", "agent")
        results.append(first)
        # The fix: a model made in Blender (stubbed here) placed as one instance.
        (tx.stage / "art/models").mkdir(parents=True)
        (tx.stage / "art/models/chair.glb").write_text("glTF")
        (tx.stage / "scenes/main.tscn").write_text(PLACED)
        second = await host.gateway.turn.submit("A wooden chair (Blender)", "agent")
        results.append(second)
        yield Message(text="The chair is open for review.")
        yield Done(ok=True)

    host.scripts = [author]
    assert asyncio.run(stdio.serve(host.root, agent)) == 0
    first, second = results
    assert first.status == "failed" and not first.final
    assert [p.check for p in first.problems] == ["complex_models"]
    assert first.problems[0].message.startswith(
        "Chair is built from 6 placeholder shapes. Make it in Blender"
    )
    assert second.status == "in_review" and second.attempt == 2
    assert "file_preview" in host.kinds()


def test_placeholders_on_lets_the_boxes_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = Host(tmp_path, monkeypatch)
    with (tmp_path / "joinery.toml").open("a") as toml:
        toml.write('\n[agents]\ncomplex_models = "placeholders"\n')
    results: list[Any] = []

    async def author(_bridge: Any) -> AsyncIterator[Any]:
        assert host.gateway is not None and host.gateway.turn is not None
        assert host.prompts[0].editor_context["complex_models"] == "placeholders"
        host.stage({"scenes/main.tscn": HEAD + chair()})
        results.append(await host.gateway.turn.submit("A box chair", "agent"))
        yield Done(ok=True)

    host.run(author)
    assert results[0].status == "in_review"


def test_a_live_chair_of_boxes_is_sent_back_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = Host(tmp_path, monkeypatch)
    results: list[Any] = []
    boxes = [
        {
            "operation": "create_box",
            "name": f"Chair{part}",
            "size": [0.5, 0.06, 0.5] if part == "Seat" else [0.05, 0.45, 0.05],
            "position": [x, y, 0],
        }
        for part, x, y in (("Seat", 0, 0.45), ("Leg1", 0.2, 0.2), ("Leg2", -0.2, 0.2))
    ]

    async def author(_bridge: Any) -> AsyncIterator[Any]:
        assert host.gateway is not None and host.gateway.turn is not None
        yield EditorAction(
            request_id="edit-1",
            payload={"summary": "Chair", "operations": boxes},
            context={"scene_path": "res://main.tscn", "scene_instance": "1"},
            targets=(),
        )
        while not host.editor_results:
            await asyncio.sleep(0.01)
        results.append(await host.gateway.turn.submit("A chair", "agent"))
        results.append(await host.gateway.turn.submit("A chair", "agent"))
        yield Done(ok=True)

    host.replies.append(
        (
            lambda events: any(e.get("kind") == "editor_action" for e in events),
            {
                "kind": "editor_result",
                "request_id": "edit-1",
                "result": {"success": True},
            },
        )
    )
    host.run(author)
    first, second = results
    assert first.status == "failed" and first.problems[0].check == "complex_models"
    assert "the scene on screen: ChairLeg1" in first.problems[0].message
    assert second.status == "in_review"
