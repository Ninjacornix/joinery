"""submit_for_review: Joinery checks the turn's change and opens the review on success."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar
from unittest.mock import AsyncMock

import pytest

from joinery.gateway import ProjectGateway
from joinery.runner import Result
from joinery.transactions import FileTransaction
from joinery.turn import MAX_SUBMITS, TurnController, TurnState

T = TypeVar("T")

SCENE = """[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://art/models/ashtray.glb" id="1_model"]

[node name="Ashtray" type="Node3D"]

[node name="Model" parent="." instance=ExtResource("1_model")]
"""


class Session:
    """A real gateway with a controller, the offline worker faked."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (root / "project.godot").write_text("config_version=5\n")
        (root / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
        monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
        monkeypatch.setattr("joinery.gateway.approved", lambda _project: True)
        monkeypatch.setattr("joinery.submission.approved_in", lambda *_: True)
        monkeypatch.setattr("joinery.submission.check_assets", lambda *_: [])
        self.import_output = ""

        async def offline(argv: list[str], _root: Path, *_a: Any, **_k: Any) -> Result:
            if "--import" in argv and self.import_output:
                return Result(False, self.import_output, "", 0.0)
            return Result(True, "", "", 0.0)

        monkeypatch.setattr("joinery.submission.run_offline", offline)
        self.root = root
        self.state = ""
        self.events: list[dict[str, Any]] = []
        self.gateway = ProjectGateway(root)
        self.turn = TurnController(self.gateway, self.events.append, "codex")

    def stage(self, files: dict[str, str]) -> FileTransaction:
        tx = FileTransaction(self.root)
        tx.begin("Ashtray")
        self.gateway.transaction = tx
        for path, text in files.items():
            (tx.stage / path).parent.mkdir(parents=True, exist_ok=True)
            (tx.stage / path).write_text(text)
        return tx

    def run(self, step: Callable[[], Awaitable[T]]) -> T:
        """Run ``step``; ``self.state`` is the transaction's state before closing."""

        async def go() -> T:
            try:
                return await step()
            finally:
                tx = self.gateway.transaction
                self.state = tx.state if tx is not None else ""
                await self.gateway.close()

        return asyncio.run(go())

    def kinds(self) -> list[str]:
        return [str(e.get("state", e["kind"])) for e in self.events]


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    return Session(tmp_path, monkeypatch)


def test_a_passed_submit_opens_the_review(session: Session) -> None:
    session.turn.begin("Make an ashtray")
    tx = session.stage({"scenes/ashtray.tscn": SCENE, "art/models/ashtray.glb": "glTF"})
    result = session.run(lambda: session.turn.submit("An ashtray on the desk"))
    assert result.status == "in_review" and result.final
    assert result.transaction_id == tx.id and session.state == "review"
    preview = next(e for e in session.events if e["kind"] == "file_preview")
    assert preview["summary"] == "An ashtray on the desk"
    assert preview["media_pending"] is False and preview["origin"] == "agent"
    assert session.kinds() == ["working", "submitting", "file_preview", "in_review"]
    assert session.turn.state is TurnState.IN_REVIEW and session.turn.review_open


def test_a_fixable_failure_keeps_the_stage_for_another_try(session: Session) -> None:
    session.turn.begin("Make an ashtray")
    session.stage({"scenes/ashtray.tscn": SCENE})
    result = session.run(lambda: session.turn.submit("Ashtray"))
    assert result.status == "failed" and not result.final
    assert result.attempts_left == MAX_SUBMITS - 1
    (problem,) = result.problems
    assert problem.check == "references" and problem.paths == ["scenes/ashtray.tscn"]
    assert problem.message.startswith("Not submitted: scenes/ashtray.tscn uses")
    assert session.state == "staging" and session.turn.state is TurnState.WORKING
    assert "file_preview" not in session.kinds()


def test_the_last_failure_is_final_and_drops_the_change(session: Session) -> None:
    session.turn.begin("Make an ashtray")
    tx = session.stage({"scenes/ashtray.tscn": SCENE})

    async def three() -> list[Any]:
        return [await session.turn.submit("Ashtray") for _ in range(MAX_SUBMITS)]

    results = session.run(three)
    assert [r.final for r in results] == [False, False, True]
    assert results[-1].attempts_left == 0 and tx.state == "rejected"
    assert session.turn.state is TurnState.IDLE
    assert session.events[-1] == {
        "kind": "turn_state",
        "turn_id": session.turn.turn_id,
        "state": "idle",
        "reason": "failed",
    }


@pytest.mark.parametrize(
    ("files", "check"),
    [
        ({"joinery.toml": "[tools]\n"}, "protected"),
        ({"room.tscn": SCENE.replace("art/models/ashtray.glb", "a.gd")}, None),
    ],
)
def test_protected_paths_are_final_at_once(
    session: Session, files: dict[str, str], check: str | None
) -> None:
    session.turn.begin("Change things")
    session.stage({**files, "a.gd": "extends Node\n"})
    result = session.run(lambda: session.turn.submit("Things"))
    if check is None:
        assert result.status == "in_review"
        return
    assert result.final and [p.check for p in result.problems] == [check]


def test_a_scene_changed_live_and_staged_is_a_final_conflict(
    session: Session,
) -> None:
    session.turn.begin("Rename the lamp")
    session.turn.record_live_edit("res://room.tscn", True)
    session.stage(
        {"room.tscn": SCENE.replace("art/models/ashtray.glb", "a.gd"), "a.gd": ""}
    )
    result = session.run(lambda: session.turn.submit("Lamp"))
    assert result.final and [p.check for p in result.problems] == ["live_conflict"]
    assert session.events[-1]["reason"] == "failed"


def test_import_rule_lock_and_dependency_problems_come_back(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session.import_output = "SCRIPT ERROR: Parse Error: Unexpected token"
    monkeypatch.setattr(
        "joinery.submission.check_assets", lambda *_: ["art/models/a.glb: no lock"]
    )
    monkeypatch.setattr(
        "joinery.submission.check_deps", lambda *_: ["package.json: denied package x"]
    )
    session.turn.begin("Ashtray")
    session.stage({"player.gd": "extends Node\nfunc (", "art/models/a.glb": "glTF"})
    result = session.run(lambda: session.turn.submit("Ashtray"))
    checks = {p.check: p for p in result.problems}
    assert set(checks) == {"assets", "dependencies", "godot_import"}
    assert checks["assets"].paths == ["art/models/a.glb"]
    assert "Parse Error" in checks["godot_import"].message
    assert not result.final


def test_nothing_to_review(session: Session) -> None:
    session.turn.begin("What is in the scene?")
    result = session.run(lambda: session.turn.submit("Nothing"))
    assert result.status == "nothing_to_review" and result.attempt == 0
    assert session.turn.state is TurnState.WORKING


def test_the_art_gate_needs_the_user_and_drops_the_stage(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("joinery.submission.approved_in", lambda *_: False)
    session.turn.begin("Texture the crate")
    tx = session.stage({"art/props/crate.png": "png"})
    result = session.run(lambda: session.turn.submit("Crate"))
    assert result.status == "needs_user" and result.final
    assert tx.state == "rejected"
    assert any(e["kind"] == "art_gate" for e in session.events)
    assert session.events[-2]["reason"] == "needs_user"


def test_no_change_after_the_review_opens(session: Session) -> None:
    session.turn.begin("Make an ashtray")
    session.stage({"a.gd": "extends Node\n"})

    async def go() -> None:
        await session.turn.submit("Ashtray")
        with pytest.raises(ValueError, match="submitted for review"):
            await session.gateway.call("godot", "write_file", {})
        with pytest.raises(ValueError, match="submitted for review"):
            await session.turn.submit("Again")

    session.run(go)


def test_a_godot_write_to_an_unsaved_scene_is_refused(session: Session) -> None:
    session.turn.begin("Move the lamp")
    session.gateway.editor_context = {"unsaved_scenes": json.dumps(["res://room.tscn"])}
    session.gateway.connection = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="unsaved edits; change it live"):
        session.run(
            lambda: session.gateway.call(
                "godot", "write_file", {"scenePath": "res://room.tscn"}
            )
        )


def test_deciding_ends_the_turn(session: Session) -> None:
    session.turn.begin("Make an ashtray")
    tx = session.stage({"a.gd": "extends Node\n"})

    async def go() -> dict[str, Any]:
        await session.turn.submit("Ashtray")
        return await session.turn.decide(tx.id, True)

    decision = session.run(go)
    assert decision["accepted"] and (session.root / "a.gd").is_file()
    assert session.kinds()[-2:] == ["decided", "idle"]
    assert session.events[-2]["transaction_id"] == tx.id
    assert not session.turn.review_open


def test_the_agent_submits_through_the_gateway_rpc(session: Session) -> None:
    from joinery.gateway_rpc import GatewayRPC, rpc

    session.turn.begin("Make an ashtray")
    session.stage({"a.gd": "extends Node\n"})

    async def go() -> tuple[dict[str, Any], str]:
        server = GatewayRPC(session.gateway)
        await server.start()
        try:
            with pytest.raises(RuntimeError, match="one short line"):
                await rpc(server.path, "submit", {"summary": "  "})
            data = await rpc(server.path, "submit", {"summary": "An ashtray"})
            refused = ""
            try:
                await rpc(server.path, "submit", {"summary": "Again"})
            except RuntimeError as exc:
                refused = str(exc)
            return data, refused
        finally:
            await server.close()

    data, refused = asyncio.run(go())
    assert data["status"] == "in_review" and data["attempts_left"] == 2
    assert data["changes"] == [{"path": "a.gd", "change": "created"}]
    assert "submitted for review" in refused


def test_the_proxy_offers_submit_for_review_with_a_short_summary() -> None:
    from servers.joinery_registry.proxy import make_server

    tools = asyncio.run(make_server(Path("gateway.sock")).list_tools())
    (tool,) = [t for t in tools if t.name == "submit_for_review"]
    schema = tool.input_schema
    assert schema["required"] == ["summary"]
    assert schema["properties"]["summary"]["minLength"] == 1
    assert schema["properties"]["summary"]["maxLength"] == 160
