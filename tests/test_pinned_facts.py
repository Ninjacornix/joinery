"""A summary keeps a short block of pinned facts word for word (ADR 0080)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from bridge import compaction
from bridge.chat_record import ChatRecorder
from bridge.protocol import Done, MessageDelta
from joinery import chats, done_when, session_summary
from joinery.console_errors import Book, Entry
from joinery.session_summary import PINNED_HEADING, FileSummaryStore, SessionSummary
from joinery.turn import TurnController
from tests.test_compaction import FakeBridge, FakeGateway, project


def todo() -> done_when.Todo:
    return done_when.Todo(
        id="t1",
        request="Add a harbour",
        steps=["Block out the pier", "Add the boats"],
        done_when=[done_when.Criterion(id="c1", text="Boats float at the pier")],
    )


def error_book(path: Path) -> None:
    book = Book(path)
    book.add(
        Entry(
            source="output",
            kind="error",
            message="Invalid call. Nonexistent function 'sail'",
            where="res://boat.gd:12",
        )
    )
    book.save()


def test_pinned_lists_the_todo_open_errors_and_answers(tmp_path: Path) -> None:
    project(tmp_path)
    error_book(tmp_path)
    earlier = SessionSummary(
        text="x",
        pinned=["The user answered: Blue sails", "Open error: gone", "To-do: old"],
    )
    lines = session_summary.pinned(tmp_path, todo(), ["Two boats"], earlier)
    assert lines[0] == (
        'To-do for "Add a harbour": 1) Block out the pier; 2) Add the boats. '
        "Done when: Boats float at the pier."
    )
    assert lines[1] == (
        "Open error (res://boat.gd:12): Invalid call. Nonexistent function 'sail'"
    )
    # Only answers and decisions carry over; the to-do and errors are read again.
    assert lines[2:] == [
        "The user answered: Blue sails",
        "The user answered: Two boats",
    ]


def test_compaction_pins_facts_after_a_long_agent_summary(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    controller = TurnController(FakeGateway(), events.append, "claude")  # type: ignore[arg-type]
    controller.requests = ["Make the door wider"]
    controller.pending_todo = todo()
    controller.answers = ["Two boats"]  # type: ignore[attr-defined]
    long_text = "word " * 3000
    bridge = FakeBridge([MessageDelta(text=long_text, new_message=True), Done(ok=True)])
    store = FileSummaryStore(project(tmp_path))
    error_book(tmp_path)
    summary = asyncio.run(
        compaction.compact(controller, bridge, store, tmp_path, events.append)
    )
    pinned = summary.text.split(PINNED_HEADING, 1)[1]
    for line in (
        "- Accepted: Darken the door wood",
        "- Discarded: Make the floor blue",
        '- To-do for "Add a harbour"',
        "- Open error (res://boat.gd:12)",
        "- The user answered: Two boats",
    ):
        assert line in pinned
    assert "- The user asked: Make the door wider" in pinned
    assert summary.pinned[-1] == "The user answered: Two boats"


def test_a_saved_chat_keeps_pinned_facts_whole(tmp_path: Path) -> None:
    """The summary a fresh session gets from a saved chat cuts the agent's words,
    never the pinned block."""
    (tmp_path / "project.godot").write_text("config_version=5\n")
    recorder = ChatRecorder(tmp_path, "codex")
    recorder.begin({"text": "Make the door wider"}, ())
    text = session_summary.build(
        "long " * 2000, ["Accepted: Darken the door"], ["The user answered: Oak"]
    )
    recorder.save(SessionSummary(text=text, pinned=["The user answered: Oak"]))
    latest = recorder.latest()
    assert latest is not None and latest.pinned == ["The user answered: Oak"]
    meta, entries = chats.read(tmp_path, recorder.chat_id)
    told = chats.summary(tmp_path, meta, entries)
    assert (
        PINNED_HEADING + "\n- Accepted: Darken the door\n- The user answered: Oak"
    ) in told
    assert told.count("long") < 1000
