"""A long session continues in a fresh provider session from a short summary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from bridge import compaction
from bridge.claude_session import ClaudeSession
from bridge.instructions import SUMMARY_REQUEST
from bridge.protocol import Done, EditorAction, Message, MessageDelta, Prompt, Usage
from bridge.provider_process import prompt_text
from bridge.turn_runner import run_turn
from joinery.session_summary import (
    KEEP,
    PINNED_HEADING,
    FileSummaryStore,
    SessionSummary,
)
from joinery.timing import TurnTiming
from joinery.transactions import FileTransaction
from joinery.turn import TurnController, TurnState


class FakeGateway:
    def __init__(self) -> None:
        self.transaction: Any = None
        self.art_gate: Any = None
        self.turn: Any = None
        self.call_lock = asyncio.Lock()
        self.progress: Any = lambda _text: None

    async def cancel(self) -> None:
        self.transaction = None

    async def submit_checks(self, _live: set[str]) -> Any:
        from joinery.submission import Validation

        return Validation()

    def start_media(self, _emit: Any) -> None:
        pass


class FakeBridge:
    """A provider whose answers are scripted, one list of events per prompt."""

    def __init__(self, *answers: list[Any]) -> None:
        self.answers = list(answers)
        self.prompts: list[Prompt] = []
        self.fresh = 0
        self.refused: list[dict[str, Any]] = []
        self.controller: TurnController | None = None

    async def send(self, prompt: Any) -> AsyncIterator[Any]:
        self.prompts.append(prompt)
        for event in self.answers.pop(0):
            if isinstance(event, BaseException):
                raise event
            if event == "mutate":
                assert self.controller is not None
                with pytest.raises(ValueError, match="summarising"):
                    self.controller.admit_mutation()
                continue
            yield event

    def editor_result(self, _id: str, result: dict[str, Any]) -> None:
        self.refused.append(result)

    async def cancel(self) -> None:
        pass

    async def new_session(self) -> None:
        self.fresh += 1


def project(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    accepted = FileTransaction(tmp_path)
    accepted.begin("Darken the door wood")
    (accepted.stage / "door.gd").write_text("extends Node\n")
    accepted.review()
    accepted.accept()
    dropped = FileTransaction(tmp_path)
    dropped.begin("Make the floor blue")
    (dropped.stage / "floor.gd").write_text("extends Node\n")
    dropped.review()
    dropped.reject()
    return tmp_path


def setup(
    tmp_path: Path, *answers: list[Any]
) -> tuple[TurnController, FakeBridge, list[dict[str, Any]], FileSummaryStore]:
    events: list[dict[str, Any]] = []
    controller = TurnController(FakeGateway(), events.append, "claude")  # type: ignore[arg-type]
    controller.requests = ["Make the door wider"]
    bridge = FakeBridge(*answers)
    bridge.controller = controller
    return controller, bridge, events, FileSummaryStore(project(tmp_path))


def test_the_summary_turn_is_quiet_and_read_only(tmp_path: Path) -> None:
    controller, bridge, events, store = setup(
        tmp_path,
        [
            "mutate",
            EditorAction(request_id="e", payload={}, context={}, targets=()),
            MessageDelta(text="- The user wants ", new_message=True),
            MessageDelta(text="a cosy harbour."),
            Done(ok=True),
        ],
    )
    summary = asyncio.run(
        compaction.compact(controller, bridge, store, tmp_path, events.append)
    )
    assert summary.text.startswith("- The user wants a cosy harbour.")
    assert "- Accepted: Darken the door wood" in summary.text
    assert "- Discarded: Make the floor blue" in summary.text
    assert "- The user asked: Make the door wider" in summary.text
    assert bridge.prompts[0].text == SUMMARY_REQUEST
    assert bridge.refused == [
        {"success": False, "message": "No changes while summarising."}
    ]
    assert bridge.fresh == 1 and controller.primer == summary.text
    assert controller.state is TurnState.IDLE
    kinds = [e["kind"] for e in events]
    assert "message_delta" not in kinds, "The summary never reaches the chat"
    assert [e["state"] for e in events if e["kind"] == "turn_state"] == [
        "compacting",
        "idle",
    ]
    notice = next(e for e in events if e["kind"] == "notice")
    assert notice == {"kind": "notice", "level": "info", "text": compaction.NOTICE}
    assert events[-2] == Usage().model_dump()
    assert store.latest() == summary


def test_a_failed_summary_turn_falls_back_to_joinerys_facts(tmp_path: Path) -> None:
    controller, bridge, events, store = setup(
        tmp_path, [Message(text="half"), RuntimeError("Provider stopped")]
    )
    summary = asyncio.run(
        compaction.compact(controller, bridge, store, tmp_path, events.append)
    )
    assert summary.text.startswith(PINNED_HEADING)
    assert bridge.fresh == 1 and controller.state is TurnState.IDLE


def test_a_review_stays_open_through_the_summary(tmp_path: Path) -> None:
    controller, bridge, events, store = setup(tmp_path, [Done(ok=True)])
    controller.begin("Rename")
    controller.record_live_edit("", True)
    asyncio.run(controller.end(True))
    assert controller.state is TurnState.IN_REVIEW
    asyncio.run(compaction.compact(controller, bridge, store, tmp_path, events.append))
    assert controller.state is TurnState.IN_REVIEW


def test_the_store_keeps_the_newest_summaries(tmp_path: Path) -> None:
    store = FileSummaryStore(tmp_path)
    assert store.latest() is None
    for index in range(KEEP + 3):
        store.save(SessionSummary(text=f"summary {index}", created=1000.0 + index))
    files = sorted((tmp_path / ".joinery/summaries").glob("*.json"))
    assert len(files) == KEEP
    latest = store.latest()
    assert latest is not None and latest.text == f"summary {KEEP + 2}"
    assert json.loads(files[0].read_text())["text"] == "summary 3"


def test_the_next_request_waits_for_the_summary_and_carries_it(tmp_path: Path) -> None:
    controller, bridge, events, store = setup(
        tmp_path,
        [Usage(context_used=850, context_window=1000), Done(ok=True)],
        [MessageDelta(text="- Harbour at dusk", new_message=True), Done(ok=True)],
        [Done(ok=True)],
    )

    async def go() -> None:
        def compact() -> Any:
            return compaction.compact(
                controller, bridge, store, tmp_path, events.append
            )

        prompt = Prompt(text="Make the door wider", project_dir=str(tmp_path))
        await run_turn(controller, bridge, prompt, events.append, TurnTiming(), compact)
        assert controller.compaction is not None
        follow = Prompt(text="And the window", project_dir=str(tmp_path))
        await run_turn(controller, bridge, follow, events.append, TurnTiming(), compact)

    asyncio.run(go())
    first, summary, follow = bridge.prompts
    assert summary.text == SUMMARY_REQUEST
    assert follow.primer.startswith("- Harbour at dusk") and not first.primer
    assert controller.primer == "" and not controller.needs_compaction()


def test_the_primer_reaches_the_prompt_once() -> None:
    plain = Prompt(text="Hi", project_dir="/p")
    primed = plain.model_copy(update={"primer": "- Harbour at dusk"})
    text = prompt_text(primed)
    assert text.count("Earlier in this chat (summary):\n- Harbour at dusk") == 1
    # After the project map, so a fresh session shares the map with the last one.
    assert text.index("Editor context:") < text.index("Earlier in this chat")
    assert text.index("Earlier in this chat") < text.index("User:")
    assert "Earlier in this chat" not in prompt_text(plain)


def test_claude_continues_under_a_new_session_id() -> None:
    session = ClaudeSession()
    session.started = True
    old = session.session_id
    asyncio.run(session.new_session())
    assert session.session_id != old and not session.started


def test_acp_opens_a_second_session_with_the_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bridge.acp_session import ACPSession

    session = ACPSession("gemini")
    session.gateway_socket = tmp_path / "gateway.sock"
    session.project = tmp_path
    requests: list[tuple[str, dict[str, Any]]] = []

    async def request(method: str, params: dict[str, Any], *_a: Any) -> dict[str, Any]:
        requests.append((method, params))
        return {"sessionId": f"s{len(requests)}"}

    session.request = request  # type: ignore[method-assign,assignment]
    asyncio.run(session.new_session())
    asyncio.run(session.new_session())
    assert [m for m, _ in requests] == ["session/new", "session/new"]
    assert requests[1][1]["mcpServers"][0]["name"] == "joinery-registry"
    assert session.session_id == "s2"


def test_codex_starts_a_second_thread(tmp_path: Path) -> None:
    from bridge.codex_session import CodexSession

    session = CodexSession()
    session.project = tmp_path
    calls: list[tuple[str, dict[str, Any]]] = []

    async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append((method, params))
        return {"thread": {"id": f"thread{len(calls)}"}}

    session.request = request  # type: ignore[method-assign]
    asyncio.run(session.new_session())
    assert calls[0][0] == "thread/start" and session.thread_id == "thread1"
    assert "submit_for_review" in calls[0][1]["developerInstructions"]


def test_the_open_chat_keeps_its_summary(tmp_path: Path) -> None:
    from bridge.chat_record import ChatRecorder
    from joinery import chats

    (tmp_path / "project.godot").write_text("config_version=5\n")
    recorder = ChatRecorder(tmp_path, "codex")
    assert recorder.latest() is None
    recorder.save(SessionSummary(text="before any chat"))
    assert recorder.latest() is not None, "Without a chat it goes to .joinery"
    recorder.begin({"text": "Make the door wider"}, ())
    recorder.save(SessionSummary(text="- Harbour at dusk", agent="codex"))
    recorder.observe({"kind": "notice", "level": "info", "text": compaction.NOTICE})
    latest = recorder.latest()
    assert latest is not None and latest.text == "- Harbour at dusk"
    meta, entries = chats.read(tmp_path, recorder.chat_id)
    assert [e["type"] for e in entries] == ["user", "summary", "notice"]
    assert "Joinery's summary of the earlier conversation:\n- Harbour at dusk" in (
        chats.summary(tmp_path, meta, entries)
    )
