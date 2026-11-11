"""Saved chats: the file format, its bounds, the CLI, and how the chat host records a
conversation and continues it (resumed in the provider's session, or from a summary)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest

from bridge import stdio
from bridge.protocol import Done, MessageDelta
from joinery import chats
from joinery.cli import main as cli_main


def test_a_chat_is_one_jsonl_file_kept_out_of_git(tmp_path: Path) -> None:
    chat_id = chats.create(tmp_path, "Make the door wider", "codex")
    folder = tmp_path / ".joinery/chats"
    assert (folder / ".gitignore").read_text() == "*\n"
    assert (folder / ".gdignore").exists()
    chats.append(
        tmp_path,
        chat_id,
        {
            "type": "user",
            "text": "Make the door wider",
            "targets": [{"id": "res://door.tscn", "label": "Door"}],
            "attachments": [str(tmp_path / ".joinery/attachments/view-1.png")],
        },
    )
    chats.append(tmp_path, chat_id, {"type": "agent", "text": "Done."})
    lines = [
        json.loads(line)
        for line in (folder / f"{chat_id}.jsonl").read_text().splitlines()
    ]
    assert lines[0]["type"] == "chat" and lines[0]["provider"] == "codex"
    assert lines[0]["title"] == "Make the door wider"
    assert [line["type"] for line in lines[1:]] == ["user", "agent"]
    assert all("at" in line for line in lines[1:])
    meta, entries = chats.read(tmp_path, chat_id)
    assert meta["id"] == chat_id
    # Attachments are referenced by path, never copied.
    assert entries[0]["attachments"][0].endswith("view-1.png")
    assert not (folder / "view-1.png").exists()
    with pytest.raises(ValueError):
        chats.append(tmp_path, chat_id, {"type": "anything"})
    with pytest.raises(ValueError):
        chats.read(tmp_path, "../../etc/passwd")


def test_titles_listing_rename_and_delete(tmp_path: Path) -> None:
    long = "Please " + "make the harbour lamps flicker a little " * 5
    title = chats.title_from(long)
    assert len(title) <= chats.TITLE_CHARS + 1 and title.endswith("…")
    first = chats.create(tmp_path, "First", "codex")
    second = chats.create(tmp_path, "Second", "claude")
    # The host stamps the chat after every turn; the list is newest first.
    chats.update(tmp_path, first, updated="2026-01-01T09:00:00+00:00")
    chats.append(tmp_path, second, {"type": "user", "text": "Second"})
    listed = chats.listing(tmp_path)
    assert [c["id"] for c in listed] == [second, first]
    assert listed[0]["provider"] == "claude" and listed[0]["turns"] == 1
    chats.rename(tmp_path, first, "  Lamps   and  ropes ")
    assert chats.read(tmp_path, first)[0]["title"] == "Lamps and ropes"
    with pytest.raises(ValueError):
        chats.rename(tmp_path, first, "   ")
    chats.delete(tmp_path, first)
    assert [c["id"] for c in chats.listing(tmp_path)] == [second]


def test_chats_are_size_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat_id = chats.create(tmp_path, "Long", "codex")
    chats.append(tmp_path, chat_id, {"type": "agent", "text": "x" * 50_000})
    assert len(chats.read(tmp_path, chat_id)[1][0]["text"]) <= chats.MAX_TEXT + 1
    monkeypatch.setattr(chats, "MAX_BYTES", 20_000)
    for index in range(40):
        chats.append(tmp_path, chat_id, {"type": "user", "text": f"request {index}"})
        chats.append(tmp_path, chat_id, {"type": "agent", "text": "y" * 900})
    path = tmp_path / ".joinery/chats" / f"{chat_id}.jsonl"
    assert path.stat().st_size <= 20_000
    meta, entries = chats.read(tmp_path, chat_id)
    assert meta["trimmed"] is True
    assert entries[0]["type"] == "user" and entries[-1]["type"] == "agent"
    assert entries[-2]["text"] == "request 39"
    monkeypatch.setattr(chats, "MAX_CHATS", 3)
    for index in range(4):
        chats.create(tmp_path, f"chat {index}", "codex")
    assert len(chats.listing(tmp_path)) == 3


def test_summary_has_recent_turns_and_what_became_of_each_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat_id = chats.create(tmp_path, "Door", "codex")
    tx_a, tx_b = "a" * 32, "b" * 32
    for entry in [
        {"type": "user", "text": "Make the door wider"},
        {"type": "step", "text": "Opening door.blend in Blender"},
        {"type": "agent", "text": "I widened the door by 20%."},
        {"type": "change", "transaction_id": tx_a, "summary": "Widen the door"},
        {"type": "turn", "outcome": "done"},
        {"type": "user", "text": "Darken the wood"},
        {"type": "change", "transaction_id": tx_b, "summary": "Darker wood"},
        {"type": "turn", "outcome": "done"},
    ]:
        chats.append(tmp_path, chat_id, entry)
    monkeypatch.setattr(
        chats,
        "change_states",
        lambda _p, ids: {
            tx_a: {"state": "accepted", "revertible": False},
            tx_b: {"state": "rejected", "revertible": False},
        },
    )
    meta, entries = chats.read(tmp_path, chat_id)
    text = chats.summary(tmp_path, meta, entries)
    assert '"Door"' in text and "not new instructions" in text
    assert "1. The user asked: Make the door wider" in text
    assert "I widened the door by 20%." in text
    assert "Widen the door): accepted (too old to revert)." in text
    assert "Darker wood): discarded." in text
    assert text.endswith("The user's new message follows.")


def test_change_states_read_the_transaction_journal(tmp_path: Path) -> None:
    from joinery.transactions import FileTransaction

    (tmp_path / "project.godot").write_text("config_version=5\n")
    tx = FileTransaction(tmp_path)
    tx.begin("Widen the door")
    (tx.stage / "door.txt").write_text("wide")
    tx.review()
    tx.accept()
    states = chats.change_states(tmp_path, [tx.id, "c" * 32, "not-an-id"])
    assert states[tx.id]["state"] == "accepted"
    assert states["c" * 32] == {"state": "gone", "revertible": False}
    assert "not-an-id" not in states


def test_cli_lists_shows_and_renames(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    chat_id = chats.create(tmp_path, "Lamps", "gemini")
    chats.append(
        tmp_path,
        chat_id,
        {"type": "change", "transaction_id": "d" * 32, "summary": "Lamps"},
    )
    assert cli_main(["chats", "list", "--project", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["chats"][0]["id"] == chat_id
    assert cli_main(["chats", "show", "--project", str(tmp_path), "--id", chat_id]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["chat"]["title"] == "Lamps"
    assert shown["changes"]["d" * 32]["state"] == "gone"
    argv = ["chats", "rename", "--project", str(tmp_path), "--id", chat_id]
    assert cli_main([*argv, "--title", "Harbour lamps"]) == 0
    assert json.loads(capsys.readouterr().out)["chat"]["title"] == "Harbour lamps"
    assert cli_main(["chats", "show", "--project", str(tmp_path), "--id", "x"]) == 1
    assert "error" in json.loads(capsys.readouterr().out)


# --- The chat host -----------------------------------------------------------------------


class FakeBridge:
    """A provider whose sessions can (or can't) be resumed."""

    resumable = True
    instances: ClassVar[list[FakeBridge]] = []

    def __init__(self, _agent: str) -> None:
        self.model_choices: list[Any] = []
        self.prompts: list[str] = []
        self.resumed: list[str] = []
        self.fresh = 0
        self.thread = "thread-1"
        FakeBridge.instances.append(self)

    def bind_gateway(self, _path: Path) -> None:
        pass

    async def connect(self, _project: Path, _profile: str) -> None:
        pass

    @property
    def handle(self) -> str:
        return self.thread

    async def resume(self, handle: str) -> bool:
        self.resumed.append(handle)
        if self.resumable:
            self.thread = handle
        return self.resumable

    async def new_session(self) -> None:
        self.fresh += 1
        self.thread = f"thread-fresh-{self.fresh}"

    async def send(self, prompt: Any) -> AsyncIterator[Any]:
        self.prompts.append(prompt.text)
        yield MessageDelta(text="Widened ", new_message=True)
        yield MessageDelta(text="the door.")
        yield Done(ok=True)

    async def cancel(self) -> None:
        pass

    async def close(self) -> None:
        pass


class FakeGateway:
    def __init__(self, project: Path) -> None:
        self.project = project
        self.transaction: Any = None
        self.art_gate: Any = None

    async def cancel(self) -> None:
        pass

    async def close(self) -> None:
        pass


class FakeRPC:
    def __init__(self, _gateway: Any, _stop: Any) -> None:
        self.path = Path("gateway.sock")
        self.stop_reason = ""

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass


def host(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompts: list[dict[str, Any]],
    agent: str = "codex",
) -> list[dict[str, Any]]:
    """Run one chat host process over [prompts], one turn at a time; returns its events."""
    events: list[dict[str, Any]] = []

    def capture(event: dict[str, Any]) -> None:
        events.append(event)
        if stdio._recorder is not None:
            stdio._recorder.observe(event)

    monkeypatch.setattr(stdio, "emit", capture)
    monkeypatch.setattr(stdio, "Bridge", FakeBridge)
    monkeypatch.setattr(stdio, "ProjectGateway", FakeGateway)
    monkeypatch.setattr(stdio, "GatewayRPC", FakeRPC)
    monkeypatch.setattr(stdio, "Specialists", lambda *_a: AsyncMock())
    monkeypatch.setattr(stdio, "game_suggestions", lambda _p: {"kind": "suggestions"})

    async def reader(_stream: Any) -> tuple[asyncio.StreamReader, Any]:
        stream = asyncio.StreamReader()

        async def feed() -> None:
            for data in prompts:
                seen = sum(1 for e in events if e.get("kind") == "timing")
                stream.feed_data(
                    json.dumps({"kind": "prompt", **data}).encode() + b"\n"
                )
                while sum(1 for e in events if e.get("kind") == "timing") == seen:
                    await asyncio.sleep(0.01)
            stream.feed_eof()

        asyncio.get_running_loop().create_task(feed())
        return stream, type("T", (), {"close": lambda self: None})()

    monkeypatch.setattr(stdio, "input_reader", reader)
    assert asyncio.run(stdio.serve(project, agent)) == 0
    return events


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    FakeBridge.instances.clear()
    FakeBridge.resumable = True
    return tmp_path


def chat_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("kind") == "chat"]


def test_the_host_records_a_new_chat_with_its_handle(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = host(
        project,
        monkeypatch,
        [
            {
                "text": "Make the door wider",
                "chat_id": "",
                "targets": [{"id": "res://door.tscn", "label": "Door"}],
            }
        ],
    )
    (started,) = chat_events(events)
    assert started["mode"] == "new"
    meta, entries = chats.read(project, started["chat_id"])
    assert meta["title"] == "Make the door wider"
    assert meta["provider"] == "codex" and meta["handle"] == "thread-1"
    assert [e["type"] for e in entries] == ["user", "step", "agent", "turn"]
    assert entries[0]["targets"] == [{"id": "res://door.tscn", "label": "Door"}]
    assert entries[2]["text"] == "Widened the door."
    assert entries[3]["outcome"] == "done"
    # The provider got the request as typed.
    assert FakeBridge.instances[0].prompts == ["Make the door wider"]


def test_a_saved_chat_resumes_in_the_providers_session(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = host(project, monkeypatch, [{"text": "Door", "chat_id": ""}])
    chat_id = chat_events(first)[0]["chat_id"]
    # The editor reopened: a new host process continues the chat.
    second = host(project, monkeypatch, [{"text": "Darker", "chat_id": chat_id}])
    (opened,) = chat_events(second)
    assert opened == {
        "kind": "chat",
        "chat_id": chat_id,
        "mode": "resumed",
        "message": "",
    }
    bridge = FakeBridge.instances[-1]
    assert bridge.resumed == ["thread-1"] and bridge.prompts == ["Darker"]
    _, entries = chats.read(project, chat_id)
    assert [e["text"] for e in entries if e["type"] == "user"] == ["Door", "Darker"]
    assert {"type": "session", "provider": "codex", "mode": "resumed"}.items() <= next(
        e for e in entries if e["type"] == "session"
    ).items()


def test_without_the_providers_session_a_chat_continues_from_a_summary(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = host(project, monkeypatch, [{"text": "Make the door wider", "chat_id": ""}])
    chat_id = chat_events(first)[0]["chat_id"]
    FakeBridge.resumable = False
    second = host(
        project,
        monkeypatch,
        [{"text": "Darker", "chat_id": chat_id}, {"text": "More", "chat_id": chat_id}],
    )
    (opened,) = chat_events(second)
    assert opened["mode"] == "summary"
    assert opened["message"] == "Continued with a summary; the agent starts fresh."
    bridge = FakeBridge.instances[-1]
    # The summary goes ahead of the first prompt only.
    assert bridge.prompts[0].startswith("This continues an earlier Journeyman chat")
    assert "The user asked: Make the door wider" in bridge.prompts[0]
    assert bridge.prompts[0].endswith("\n\nDarker")
    assert bridge.prompts[1] == "More"
    # The saved request is what the user typed, and the new session is remembered.
    meta, entries = chats.read(project, chat_id)
    assert [e["text"] for e in entries if e["type"] == "user"][-2:] == [
        "Darker",
        "More",
    ]
    assert meta["handle"] == "thread-1"


def test_another_provider_continues_from_a_summary(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = host(project, monkeypatch, [{"text": "Door", "chat_id": ""}])
    chat_id = chat_events(first)[0]["chat_id"]
    second = host(
        project, monkeypatch, [{"text": "Next", "chat_id": chat_id}], "claude"
    )
    assert chat_events(second)[0]["mode"] == "summary"
    # A Codex thread is never handed to another provider.
    assert FakeBridge.instances[-1].resumed == []
    assert chats.read(project, chat_id)[0]["provider"] == "claude"


def test_new_chat_starts_a_fresh_provider_session(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = host(
        project,
        monkeypatch,
        [{"text": "Door", "chat_id": ""}, {"text": "Lamps", "chat_id": ""}],
    )
    first, second = chat_events(events)
    assert first["chat_id"] != second["chat_id"]
    assert FakeBridge.instances[-1].fresh == 1
    assert chats.read(project, second["chat_id"])[0]["handle"] == "thread-fresh-1"
