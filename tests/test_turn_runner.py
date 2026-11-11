"""The chat host runs each request through the turn controller, for every provider."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bridge import stdio
from bridge.protocol import Done, EditorAction, Message, Prompt
from joinery.gateway import ProjectGateway
from joinery.runner import Result
from joinery.transactions import FileTransaction

SCENE = """[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://art/models/ashtray.glb" id="1_model"]

[node name="Ashtray" type="Node3D"]
"""

Script = Callable[[Any], AsyncIterator[Any]]


class Host:
    """The chat host with a scripted provider and a real gateway."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (root / "project.godot").write_text("config_version=5\n")
        (root / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
        monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
        monkeypatch.setattr("joinery.submission.approved_in", lambda *_: True)
        monkeypatch.setattr("joinery.submission.check_assets", lambda *_: [])

        async def offline(_argv: list[str], _root: Path, *_a: Any, **_k: Any) -> Result:
            return Result(True, "", "", 0.0)

        monkeypatch.setattr("joinery.submission.run_offline", offline)
        self.root = root
        self.events: list[dict[str, Any]] = []
        self.gateway: ProjectGateway | None = None
        self.prompts: list[Prompt] = []
        self.editor_results: list[dict[str, Any]] = []
        self.scripts: list[Script] = []
        self.user_prompts = 1
        #: Lines the sidebar sends once ``when`` holds for the events so far.
        self.replies: list[
            tuple[Callable[[list[dict[str, Any]]], bool], dict[str, Any]]
        ] = []
        host = self

        class FakeBridge:
            handle = ""

            def __init__(self, _agent: str) -> None:
                self.model_choices: list[Any] = []
                self.host = host

            async def resume(self, _handle: str) -> bool:
                return False

            async def new_session(self) -> None:
                pass

            def bind_gateway(self, _path: Path) -> None:
                pass

            async def connect(self, _project: Path, _profile: str) -> None:
                pass

            async def send(self, prompt: Any) -> AsyncIterator[Any]:
                host.prompts.append(prompt)
                async for event in host.scripts.pop(0)(self):
                    yield event

            def editor_result(self, _id: str, result: dict[str, Any]) -> None:
                host.editor_results.append(result)

            async def cancel(self) -> None:
                pass

            async def close(self) -> None:
                pass

        def gateway(project: Path) -> ProjectGateway:
            self.gateway = ProjectGateway(project)
            return self.gateway

        class FakeRPC:
            def __init__(self, gateway: Any, _stop: Any) -> None:
                self.path = Path("gateway.sock")
                self.stop_reason = ""
                self.gateway = gateway

            async def start(self) -> None:
                pass

            async def close(self) -> None:
                await self.gateway.close()

        monkeypatch.setattr(stdio, "emit", self.events.append)
        monkeypatch.setattr(stdio, "Bridge", FakeBridge)
        monkeypatch.setattr(stdio, "ProjectGateway", gateway)
        monkeypatch.setattr(stdio, "GatewayRPC", FakeRPC)
        monkeypatch.setattr(stdio, "Specialists", lambda *_a: AsyncMock())
        monkeypatch.setattr(
            stdio, "game_suggestions", lambda _p: {"kind": "suggestions"}
        )
        monkeypatch.setattr(stdio, "input_reader", self._reader)

    async def _reader(self, _stream: Any) -> tuple[asyncio.StreamReader, Any]:
        stream = asyncio.StreamReader()
        prompts = self.user_prompts

        def send(line: dict[str, Any]) -> None:
            stream.feed_data(json.dumps(line).encode() + b"\n")

        async def drive() -> None:
            for _ in range(prompts):
                done = sum(e.get("kind") == "done" for e in self.events)
                send({"kind": "prompt", "text": "Make an ashtray"})
                while sum(e.get("kind") == "done" for e in self.events) == done:
                    for when, line in list(self.replies):
                        if when(self.events):
                            self.replies.remove((when, line))
                            send(line)
                    await asyncio.sleep(0.01)
            for when, line in list(self.replies):
                if when(self.events):
                    send(line)
            await asyncio.sleep(0.05)
            stream.feed_eof()

        asyncio.get_running_loop().create_task(drive())
        return stream, type("T", (), {"close": lambda self: None})()

    def run(self, *scripts: Script, prompts: int = 1) -> None:
        self.scripts = list(scripts)
        self.user_prompts = prompts
        assert asyncio.run(stdio.serve(self.root, "codex")) == 0

    def stage(self, files: dict[str, str]) -> FileTransaction:
        gateway = self.gateway
        assert gateway is not None
        tx = FileTransaction(self.root)
        tx.begin("Ashtray")
        gateway.transaction = tx
        for path, text in files.items():
            (tx.stage / path).parent.mkdir(parents=True, exist_ok=True)
            (tx.stage / path).write_text(text)
        return tx

    def kinds(self) -> list[str]:
        return [
            str(e["state"]) if e.get("kind") == "turn_state" else str(e.get("kind"))
            for e in self.events
        ]


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Host:
    return Host(tmp_path, monkeypatch)


def order(kinds: list[str], *names: str) -> list[int]:
    return [kinds.index(name) for name in names]


def test_the_agent_submits_and_the_review_opens_before_done(host: Host) -> None:
    async def agent(bridge: Any) -> AsyncIterator[Any]:
        host.stage({"a.gd": "extends Node\n"})
        assert host.gateway is not None and host.gateway.turn is not None
        result = await host.gateway.turn.submit("An ashtray", "agent")
        assert result.status == "in_review"
        yield Message(text="The ashtray is open for review.")
        yield Done(ok=True)

    host.run(agent)
    kinds = host.kinds()
    positions = order(
        kinds, "working", "submitting", "file_preview", "in_review", "message", "done"
    )
    assert positions == sorted(positions)
    preview = host.events[kinds.index("file_preview")]
    assert preview["summary"] == "An ashtray" and preview["origin"] == "agent"
    assert "review_preparing" not in kinds and "notice" not in kinds


def test_joinery_submits_a_change_the_agent_left(host: Host) -> None:
    async def agent(bridge: Any) -> AsyncIterator[Any]:
        host.stage({"a.gd": "extends Node\n"})
        yield Message(text="Made the ashtray.")
        yield Done(ok=True)

    host.run(agent)
    kinds = host.kinds()
    positions = order(kinds, "message", "submitting", "file_preview", "done")
    assert positions == sorted(positions)
    preview = host.events[kinds.index("file_preview")]
    assert preview["origin"] == "joinery" and preview["summary"] == "Make an ashtray"


def test_a_failed_auto_submit_asks_once_then_says_so(host: Host) -> None:
    async def agent(bridge: Any) -> AsyncIterator[Any]:
        tx = host.stage({"scenes/ashtray.tscn": SCENE})
        host.tx = tx  # type: ignore[attr-defined]
        yield Message(text="The ashtray will open for review.")
        yield Done(ok=True)

    async def still_broken(bridge: Any) -> AsyncIterator[Any]:
        yield Message(text="I couldn't make the model.")
        yield Done(ok=True)

    host.run(agent, still_broken)
    assert len(host.prompts) == 2, "Exactly one continuation"
    follow = host.prompts[1].text
    assert "did not pass" in follow and "submit_for_review" in follow
    assert "references: Not submitted: scenes/ashtray.tscn uses" in follow
    kinds = host.kinds()
    assert "file_preview" not in kinds and kinds.count("notice") == 1
    notice = host.events[kinds.index("notice")]
    assert notice["level"] == "warn"
    assert notice["text"].startswith("Journeyman's change didn't pass Joinery's checks")
    assert "scenes/ashtray.tscn uses art/models/ashtray.glb" in notice["text"]
    assert notice["text"].endswith("Nothing was changed.")
    assert kinds.index("notice") < kinds.index("done") and kinds.count("done") == 1
    assert host.tx.state == "rejected"  # type: ignore[attr-defined]


def test_the_continuation_can_fix_the_change(host: Host) -> None:
    async def agent(bridge: Any) -> AsyncIterator[Any]:
        host.stage({"scenes/ashtray.tscn": SCENE})
        yield Done(ok=True)

    async def fixed(bridge: Any) -> AsyncIterator[Any]:
        assert host.gateway is not None and host.gateway.turn is not None
        tx = host.gateway.transaction
        assert tx is not None and tx.state == "staging"
        (tx.stage / "art/models").mkdir(parents=True)
        (tx.stage / "art/models/ashtray.glb").write_text("glTF")
        result = await host.gateway.turn.submit("An ashtray with its model", "agent")
        assert result.status == "in_review" and result.attempt == 2
        yield Message(text="Fixed; it's on screen.")
        yield Done(ok=True)

    host.run(agent, fixed)
    kinds = host.kinds()
    assert "notice" not in kinds and kinds.count("file_preview") == 1
    assert host.events[kinds.index("file_preview")]["summary"] == (
        "An ashtray with its model"
    )


def test_a_failed_turn_rejects_its_change(host: Host) -> None:
    async def agent(bridge: Any) -> AsyncIterator[Any]:
        host.tx = host.stage({"a.gd": "extends Node\n"})  # type: ignore[attr-defined]
        yield Done(ok=False)

    host.run(agent)
    assert host.tx.state == "rejected"  # type: ignore[attr-defined]
    idle = [e for e in host.events if e.get("state") == "idle"]
    assert idle[-1]["reason"] == "failed"
    assert "file_preview" not in host.kinds()


def test_the_user_can_accept_while_the_agent_is_still_writing(host: Host) -> None:
    decided = asyncio.Event()

    async def agent(bridge: Any) -> AsyncIterator[Any]:
        tx = host.stage({"a.gd": "extends Node\n"})
        assert host.gateway is not None and host.gateway.turn is not None
        await host.gateway.turn.submit("An ashtray", "agent")
        host.replies.append(
            (
                lambda _events: True,
                {"kind": "decision", "transaction_id": tx.id, "accept": True},
            )
        )
        while not any(e.get("kind") == "file_decision" for e in host.events):
            await asyncio.sleep(0.01)
        decided.set()
        yield Message(text="It's on screen now.")
        yield Done(ok=True)

    host.run(agent)
    assert decided.is_set() and (host.root / "a.gd").is_file()
    kinds = host.kinds()
    positions = order(kinds, "in_review", "decided", "file_decision", "message")
    assert positions == sorted(positions)


def test_a_codex_live_edit_is_refused_once_the_change_is_submitted(
    host: Host,
) -> None:
    async def agent(bridge: Any) -> AsyncIterator[Any]:
        host.stage({"a.gd": "extends Node\n"})
        assert host.gateway is not None and host.gateway.turn is not None
        await host.gateway.turn.submit("A script", "agent")
        yield EditorAction(
            request_id="edit-1",
            payload={"summary": "Rename", "operations": []},
            context={"scene_path": "res://room.tscn", "scene_instance": "1"},
            targets=(),
        )
        yield Done(ok=True)

    host.run(agent)
    assert host.editor_results[0]["success"] is False
    assert "submitted for review" in host.editor_results[0]["message"]
    assert "editor_action" not in host.kinds()


def test_a_codex_live_edit_of_an_unsaved_scene_goes_to_review(host: Host) -> None:
    async def agent(bridge: Any) -> AsyncIterator[Any]:
        yield EditorAction(
            request_id="edit-1",
            payload={"summary": "Rename", "operations": []},
            context={"scene_path": "", "scene_instance": "empty"},
            targets=(),
        )
        while not host.editor_results:
            await asyncio.sleep(0.01)
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
    host.run(agent)
    kinds = host.kinds()
    assert "editor_action" in kinds and "file_preview" not in kinds
    assert kinds.index("in_review") < kinds.index("done")
