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
from bridge.instructions import SUMMARY_REQUEST
from bridge.protocol import Done, EditorAction, Message, MessageDelta, Prompt, Usage
from bridge.turn_runner import failure_notice, run_turn, settle_compaction
from joinery import chats
from joinery.gateway import ProjectGateway
from joinery.runner import Result
from joinery.timing import TurnTiming
from joinery.transactions import FileTransaction
from joinery.turn import Problem, SubmitResult

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
        self.policy_stop: Any = None
        self.prompts: list[Prompt] = []
        self.editor_results: list[dict[str, Any]] = []
        #: Each provider session opened, by agent id.
        self.agents: list[str] = []
        self.scripts: list[Script] = []
        self.user_prompts = 1
        #: The prompt lines the sidebar sends, by default "Make an ashtray".
        self.lines: list[dict[str, Any]] = []
        #: What the provider was asked to do, in order.
        self.log: list[str] = []
        #: Lines the sidebar sends once ``when`` holds for the events so far.
        self.replies: list[
            tuple[Callable[[list[dict[str, Any]]], bool], dict[str, Any]]
        ] = []
        host = self

        class FakeBridge:
            handle = ""

            def __init__(self, agent: str) -> None:
                self.model_choices: list[Any] = []
                self.host = host
                self.agent = agent
                host.agents.append(agent)

            async def resume(self, _handle: str) -> bool:
                return False

            async def new_session(self) -> None:
                host.log.append("new_session")

            def bind_gateway(self, _path: Path) -> None:
                pass

            async def connect(self, _project: Path, _profile: str) -> None:
                pass

            async def send(self, prompt: Any) -> AsyncIterator[Any]:
                host.prompts.append(prompt)
                host.log.append("send")
                async for event in host.scripts.pop(0)(self):
                    yield event
                host.log.append("sent")

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
            def __init__(self, gateway: Any, stop: Any) -> None:
                host.policy_stop = stop
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
            for index in range(prompts):
                done = sum(e.get("kind") == "done" for e in self.events)
                send(
                    self.lines[index]
                    if index < len(self.lines)
                    else {"kind": "prompt", "text": "Make an ashtray"}
                )
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
    assert "ashtray.tscn uses a file the change doesn't include" in notice["text"]
    assert "Not submitted" not in notice["text"], "The agent's wording stays with it"
    assert notice["text"].endswith("Nothing was changed.")
    assert host.prompts[1].primer == ""
    timing = next(e for e in host.events if e.get("kind") == "timing")
    assert timing["outcome"] == "failed", "A dropped change is no success"
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


def test_a_new_chat_waits_for_the_summary_and_starts_without_it(host: Host) -> None:
    async def long_turn(bridge: Any) -> AsyncIterator[Any]:
        yield Usage(context_used=850, context_window=1000)
        yield Message(text="Done.")
        yield Done(ok=True)

    async def summary(bridge: Any) -> AsyncIterator[Any]:
        await asyncio.sleep(0.3)
        yield MessageDelta(text="- Harbour at dusk", new_message=True)
        yield Done(ok=True)

    async def new_chat(bridge: Any) -> AsyncIterator[Any]:
        yield Done(ok=True)

    host.lines = [
        {"kind": "prompt", "text": "Make the harbour"},
        {"kind": "prompt", "text": "Something else", "chat_id": ""},
    ]
    host.run(long_turn, summary, new_chat, prompts=2)
    _first, asked, fresh = host.prompts
    assert asked.text == SUMMARY_REQUEST
    assert fresh.primer == "" and "Harbour at dusk" not in fresh.text
    # The summary ends before the sidebar's new chat replaces the session.
    assert host.log == [
        "send",
        "sent",
        "send",
        "sent",
        "new_session",
        "new_session",
        "send",
        "sent",
    ]
    chat_a = next(e["chat_id"] for e in host.events if e.get("mode") == "new")
    meta, entries = chats.read(host.root, chat_a)
    assert next(e["text"] for e in entries if e["type"] == "summary").startswith(
        "- Harbour at dusk"
    )
    assert meta["handle"] == "", "Reopened later, chat A continues from its summary"


def test_a_stop_while_waiting_keeps_the_summary_running() -> None:
    from joinery.turn import TurnController

    class Gateway:
        turn: Any = None
        progress: Any = staticmethod(lambda _text: None)

    controller = TurnController(Gateway(), lambda _e: None)  # type: ignore[arg-type]

    async def go() -> None:
        release = asyncio.Event()

        async def summarise() -> str:
            await release.wait()
            return "saved"

        controller.compaction = asyncio.create_task(summarise())
        waiting = asyncio.create_task(settle_compaction(controller))
        await asyncio.sleep(0.01)
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        assert controller.compaction is not None
        assert not controller.compaction.cancelled()
        release.set()
        assert await controller.compaction == "saved"

    asyncio.run(go())


def test_the_summary_primes_only_its_own_chat() -> None:
    from joinery.turn import TurnController

    class Gateway:
        turn: Any = None
        transaction: Any = None
        art_gate: Any = None
        progress: Any = staticmethod(lambda _text: None)

    sent: list[Prompt] = []

    class Agent:
        async def send(self, prompt: Prompt) -> AsyncIterator[Any]:
            sent.append(prompt)
            yield Done(ok=True)

        def editor_result(self, _id: str, _result: dict[str, Any]) -> None:
            pass

    controller = TurnController(Gateway(), lambda _e: None)  # type: ignore[arg-type]
    prompt = Prompt(text="Hi", project_dir="/p")

    async def go() -> None:
        controller.primer, controller.primer_chat = "- Chat A", "a"
        await run_turn(
            controller, Agent(), prompt, lambda _e: None, TurnTiming(), chat_id="b"
        )
        controller.primer, controller.primer_chat = "- Chat A", "a"
        await run_turn(
            controller, Agent(), prompt, lambda _e: None, TurnTiming(), chat_id="a"
        )

    asyncio.run(go())
    assert [p.primer for p in sent] == ["", "- Chat A"]
    assert controller.primer == ""


@pytest.mark.parametrize(
    ("problems", "text"),
    [
        (
            [Problem("godot_import", "Godot import/script validation failed: ERROR x")],
            (
                "Journeyman's change didn't pass Joinery's checks: Godot couldn't import "
                "it. Nothing was changed."
            ),
        ),
        (
            [Problem("checks", "Joinery could not run its checks: [Errno 2] docker")],
            (
                "Joinery couldn't check Journeyman's change, so nothing was changed. "
                "Make sure Docker is running, then ask again."
            ),
        ),
        (
            [Problem("tests", "The project's tests failed: ...", ["test/a_test.gd"])],
            (
                "Journeyman's change didn't pass Joinery's checks: the project's tests "
                "failed (a_test.gd). Nothing was changed."
            ),
        ),
    ],
)
def test_the_failure_line_is_plain(problems: list[Problem], text: str) -> None:
    result = SubmitResult("failed", 3, 0, True, problems=problems)
    assert failure_notice(result)["text"] == text


def test_a_rule_zero_stop_ends_the_turn_without_a_submit(host: Host) -> None:
    async def agent(bridge: Any) -> AsyncIterator[Any]:
        host.tx = host.stage({"scenes/ashtray.tscn": SCENE})  # type: ignore[attr-defined]
        # The agent's policy hook reports the stop over the gateway, like the RPC.
        asyncio.ensure_future(host.policy_stop("STOP, Rule Zero by Joinery policy"))
        await asyncio.sleep(1)
        yield Done(ok=True)

    host.run(agent)
    assert len(host.prompts) == 1, "Never continued after a stop"
    assert host.tx.state == "rejected"  # type: ignore[attr-defined]
    kinds = host.kinds()
    assert "file_preview" not in kinds and "submitting" not in kinds
    stop = next(e for e in host.events if e.get("kind") == "message")
    assert stop["text"].startswith("Stopped by project policy: STOP, Rule Zero")


QUOTA_WORDS = "You've hit your usage limit. Try again at 4:12 PM."


def signed_in(monkeypatch: pytest.MonkeyPatch, *agents: str) -> None:
    names = {"claude": "Claude Code", "gemini": "Gemini CLI"}
    monkeypatch.setattr(
        "bridge.provider_switch.candidates",
        lambda current, _ready=None: [
            {"id": a, "name": names[a]} for a in agents if a != current
        ],
    )


def out_of_quota(host: Host) -> Script:
    async def agent(bridge: Any) -> AsyncIterator[Any]:
        host.stage({"a.gd": "extends Node\n"})
        yield Message(text=QUOTA_WORDS, level="warn")
        yield Done(ok=False)

    return agent


def carries_on(host: Host) -> Script:
    async def agent(bridge: Any) -> AsyncIterator[Any]:
        assert bridge.agent == "claude"
        assert host.gateway is not None and host.gateway.turn is not None
        result = await host.gateway.turn.submit("An ashtray", "agent")
        assert result.status == "in_review"
        yield Message(text="Finished the ashtray.")
        yield Done(ok=True)

    return agent


def test_the_project_fallback_carries_a_quota_stop_on(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    (host.root / "joinery.toml").write_text(
        '[tools]\nenabled=["godot"]\n[agents]\nfallback_provider="claude"\n'
    )
    signed_in(monkeypatch, "claude", "gemini")
    host.run(out_of_quota(host), carries_on(host))
    assert host.agents == ["codex", "claude"]
    carried = host.prompts[1]
    assert "ran out of quota" in carried.text and "- a.gd (new)" in carried.text
    assert "Make an ashtray" in carried.text
    switched = next(e for e in host.events if e.get("kind") == "provider_switch")
    assert switched["state"] == "switched" and switched["to"] == "claude"
    assert "fallback agent" in switched["message"]
    kinds = host.kinds()
    assert "in_review" in kinds
    assert host.events[[k for k in kinds].index("done")]["ok"] is True


def test_the_user_is_asked_before_another_agent_carries_on(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    signed_in(monkeypatch, "claude")
    host.replies.append(
        (
            lambda events: any(e.get("state") == "offer" for e in events),
            {"kind": "provider_switch", "agent": "claude"},
        )
    )
    host.run(out_of_quota(host), carries_on(host))
    states = [e["state"] for e in host.events if e.get("kind") == "provider_switch"]
    assert states == ["offer", "switched"]
    assert host.agents == ["codex", "claude"]
    assert "in_review" in host.kinds()


def test_no_ends_the_request_and_nothing_else_starts(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    signed_in(monkeypatch, "claude")
    host.replies.append(
        (
            lambda events: any(e.get("state") == "offer" for e in events),
            {"kind": "provider_switch", "agent": ""},
        )
    )
    host.run(out_of_quota(host))
    states = [e["state"] for e in host.events if e.get("kind") == "provider_switch"]
    assert states == ["offer", "declined"]
    assert host.agents == ["codex"] and len(host.prompts) == 1


def test_without_another_signed_in_agent_the_user_is_told(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    signed_in(monkeypatch)
    host.run(out_of_quota(host))
    assert host.agents == ["codex"]
    told = [e for e in host.events if e.get("kind") == "notice"]
    assert any("no other agent is signed in" in e["text"] for e in told)
    assert "provider_switch" not in host.kinds()
