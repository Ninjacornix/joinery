"""Rewind a chat to an earlier message, or branch it there (ADR 0080)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery import chats
from joinery.cli import main as cli_main
from joinery.transactions import FileTransaction
from joinery.undo_changes import stage_undo
from tests.test_chats import FakeBridge, chat_events, host

AGENTS = ["codex", "claude", "gemini", "opencode"]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    FakeBridge.instances.clear()
    FakeBridge.resumable = True
    return tmp_path


def accept(project: Path, summary: str, files: dict[str, str | None]) -> str:
    """An accepted change: each file written, or removed when None."""
    tx = FileTransaction(project)
    tx.begin(summary)
    for rel, text in files.items():
        path = tx.stage / rel
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    tx.review()
    tx.accept()
    return tx.id


def saved_chat(project: Path, changes: list[str] | None = None) -> str:
    """A chat of three turns; the second and third made ``changes``."""
    chat_id = chats.create(project, "Door", "codex")
    chats.update(project, chat_id, handle="thread-1")
    ids = changes or []
    for number, text in enumerate(["Door", "Darker", "Lamps"]):
        chats.append(
            project,
            chat_id,
            {"type": "user", "text": text, "targets": [{"id": "res://door.tscn"}]},
        )
        chats.append(project, chat_id, {"type": "agent", "text": f"Did {text}."})
        if number and len(ids) >= number:
            chats.append(
                project,
                chat_id,
                {
                    "type": "change",
                    "transaction_id": ids[number - 1],
                    "summary": text,
                    "state": "accepted",
                },
            )
        chats.append(project, chat_id, {"type": "turn", "outcome": "done"})
    return chat_id


def test_rewind_drops_the_later_turns_and_says_what_they_changed(
    project: Path,
) -> None:
    first = accept(project, "Darker door", {"door.gd": "dark"})
    second = accept(project, "Two lamps", {"lamp.gd": "lamp"})
    chat_id = saved_chat(project, [first, second])
    result = chats.rewind(project, chat_id, 1)
    assert result["request"] == "Darker"
    assert result["targets"] == [{"id": "res://door.tscn"}]
    assert result["dropped"] == 2
    assert [(c["transaction_id"], c["state"]) for c in result["changes"]] == [
        (first, "accepted"),
        (second, "accepted"),
    ]
    meta, entries = chats.read(project, chat_id)
    assert meta["epoch"] == 1 and meta["handle"] == ""
    assert [e["type"] for e in entries] == ["user", "agent", "turn", "rewind"]
    # The project is untouched: the changes stay accepted.
    assert (project / "door.gd").read_text() == "dark"
    told = chats.summary(project, meta, entries)
    assert "The user rewound this chat to an earlier message: 2 later turns" in told
    assert '"Darker"' in told
    assert f"{first[:8]} (Darker): accepted, so its files are still in the project" in (
        told
    )
    with pytest.raises(ValueError, match="no longer in the chat"):
        chats.rewind(project, chat_id, 1)


def test_rewinding_to_the_first_message_empties_the_chat(project: Path) -> None:
    chat_id = saved_chat(project)
    assert chats.rewind(project, chat_id, 0)["dropped"] == 3
    _, entries = chats.read(project, chat_id)
    assert [e["type"] for e in entries] == ["rewind"]
    assert chats.listing(project)[0]["turns"] == 0


def test_branch_copies_the_history_up_to_that_turn(project: Path) -> None:
    chat_id = saved_chat(project)
    meta = chats.branch(project, chat_id, 1)
    assert meta["id"] != chat_id and meta["branched_from"] == chat_id
    assert meta["title"] == "Door (branch)" and meta["handle"] == ""
    _, entries = chats.read(project, meta["id"])
    assert [e["text"] for e in entries if e["type"] == "user"] == ["Door", "Darker"]
    assert entries[-1]["type"] == "turn"
    # A branch of a branch keeps one mark; the original is unchanged.
    again = chats.branch(project, meta["id"], 0)
    assert again["title"] == "Door (branch)"
    assert len(chats.read(project, chat_id)[1]) == 9


@pytest.mark.parametrize("agent", AGENTS)
def test_the_host_starts_fresh_after_a_rewind(
    project: Path, monkeypatch: pytest.MonkeyPatch, agent: str
) -> None:
    chat: dict[str, str] = {}
    darker: dict[str, str] = {"text": "Darker"}
    lighter: dict[str, str] = {"text": "Lighter"}

    def remember() -> None:
        # The sidebar names the open chat with every prompt.
        chat["id"] = darker["chat_id"] = lighter["chat_id"] = chats.listing(project)[0][
            "id"
        ]

    def rewind() -> None:
        chats.rewind(project, chat["id"], 1)

    events = host(
        project,
        monkeypatch,
        [{"text": "Door", "chat_id": ""}, remember, darker, rewind, lighter],
        agent,
    )
    opened = chat_events(events)
    assert opened[0]["mode"] == "new" and len(opened) == 2
    assert opened[1]["mode"] == "summary"
    assert opened[1]["message"] == chats.REWOUND_NOTE
    bridge = FakeBridge.instances[-1]
    assert bridge.fresh == 1 and bridge.resumed == []
    assert bridge.prompts[0] == "Door" and bridge.prompts[1] == "Darker"
    assert bridge.prompts[2].startswith("This continues an earlier Journeyman chat")
    assert "The user asked: Door" in bridge.prompts[2]
    assert "The user asked: Darker" not in bridge.prompts[2]
    assert "The user rewound this chat" in bridge.prompts[2]
    assert bridge.prompts[2].endswith("\n\nLighter")
    _, entries = chats.read(project, chat["id"])
    assert [e["text"] for e in entries if e["type"] == "user"] == ["Door", "Lighter"]


@pytest.mark.parametrize("agent", AGENTS)
def test_a_branch_continues_from_a_summary(
    project: Path, monkeypatch: pytest.MonkeyPatch, agent: str
) -> None:
    source = saved_chat(project)
    branched = chats.branch(project, source, 0)["id"]
    events = host(project, monkeypatch, [{"text": "Oak", "chat_id": branched}], agent)
    (opened,) = chat_events(events)
    assert opened["mode"] == "summary" and opened["chat_id"] == branched
    bridge = FakeBridge.instances[-1]
    assert bridge.resumed == [], "a branch never takes the original's session"
    assert "The user asked: Door" in bridge.prompts[0]
    assert "Darker" not in bridge.prompts[0]
    assert len(chats.read(project, source)[1]) == 9


def test_undo_stages_one_reviewed_change_back_to_before(project: Path) -> None:
    (project / "door.gd").write_text("light")
    (project / "keep.gd").write_text("keep")
    first = accept(project, "Darker door", {"door.gd": "dark", "art/lamp.gd": "1"})
    second = accept(project, "Brighter lamp", {"art/lamp.gd": "2", "keep.gd": None})
    result = stage_undo(project, [second, first])
    assert result["undone"] == 2 and result["state"] == "review"
    assert result["summary"] == "Undo the 2 changes made after an earlier message"
    # Nothing changed yet: the user accepts it in Review like any change.
    assert (project / "door.gd").read_text() == "dark"
    tx = FileTransaction.load(project, result["transaction_id"])
    tx.accept()
    assert (project / "door.gd").read_text() == "light"
    assert (project / "keep.gd").read_text() == "keep"
    assert not (project / "art/lamp.gd").exists()
    assert not (project / "art").exists()
    # The earlier changes stay accepted: history is never rewritten.
    assert FileTransaction.load(project, first).state == "accepted"


def test_undo_refuses_a_file_changed_since(project: Path) -> None:
    (project / "door.gd").write_text("light")
    change = accept(project, "Darker door", {"door.gd": "dark"})
    (project / "door.gd").write_text("edited by hand")
    with pytest.raises(ValueError, match="door.gd changed after those changes"):
        stage_undo(project, [change])
    with pytest.raises(ValueError, match="nothing to undo"):
        stage_undo(project, ["0" * 32])


def test_the_cli_rewinds_branches_and_undoes(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "door.gd").write_text("light")
    change = accept(project, "Darker door", {"door.gd": "dark"})
    chat_id = saved_chat(project, [change])
    base = ["chats", "--project", str(project), "--id", chat_id]
    assert cli_main(["chats", "branch", *base[1:], "--turn", "1"]) == 0
    branched = json.loads(capsys.readouterr().out)["chat"]
    assert branched["branched_from"] == chat_id
    assert cli_main(["chats", "rewind", *base[1:], "--turn", "1"]) == 0
    rewound = json.loads(capsys.readouterr().out)
    assert rewound["request"] == "Darker"
    assert rewound["changes"][0]["state"] == "accepted"
    assert cli_main(["chats", "undo", *base[1:], "--changes", change]) == 0
    undone = json.loads(capsys.readouterr().out)
    assert undone["state"] == "review" and undone["undone"] == 1
    assert cli_main(["chats", "rewind", *base[1:], "--turn", "9"]) == 1
    assert "no longer in the chat" in capsys.readouterr().out
