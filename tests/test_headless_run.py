"""`joinery run`: one request through the chat host without the editor (ADR 0077)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bridge import stdio
from joinery import headless
from joinery.cli import main as cli_main
from joinery.gateway import ProjectGateway
from joinery.transactions import FileTransaction
from tests.test_chats import FakeBridge, FakeGateway, FakeRPC

#: A stand-in chat host: one turn that stages a change and starts a task, then waits
#: to be asked when it is quiet. It writes what it was sent to argv[1].
FAKE_HOST = """
import json, sys
def say(event):
    print(json.dumps(event), flush=True)
def hear():
    return json.loads(sys.stdin.readline())
got = []
say({"kind": "connection", "state": "connected"})
got.append(hear())
say({"kind": "editor_action", "request_id": "r1", "payload": {}})
got.append(hear())
say({"kind": "editor_view", "request_id": "v1"})
got.append(hear())
say({"kind": "turn_state", "turn_id": "t", "state": "working"})
say({"kind": "tool_activity", "server": "joinery-registry", "tool": "call_tool"})
say({"kind": "check_problems", "attempt": 1, "final": False, "problems": []})
say({"kind": "usage", "input_tokens": 10, "output_tokens": 2})
say({"kind": "message_delta", "text": "Made it ", "new_message": True})
say({"kind": "message_delta", "text": "red."})
say({"kind": "file_preview", "transaction_id": "tx1", "summary": "Red door"})
say({"kind": "task", "task_id": "k1", "title": "Lamp", "state": "working",
     "transaction_id": ""})
say({"kind": "timing", "outcome": "done"})
got.append(hear())
say({"kind": "task", "task_id": "k1", "title": "Lamp", "state": "in_review",
     "transaction_id": "tx2"})
say({"kind": "quiet"})
with open(sys.argv[1], "w") as out:
    json.dump(got, out)
rest = sys.stdin.readline()
with open(sys.argv[1] + ".rest", "w") as out:
    out.write(rest)
"""

IDLE_HOST = """
import json, sys
print(json.dumps({"kind": "connection", "state": "connected"}), flush=True)
got = [json.loads(sys.stdin.readline()), json.loads(sys.stdin.readline())]
print(json.dumps({"kind": "quiet"}), flush=True)
with open(sys.argv[1], "w") as out:
    json.dump(got, out)
sys.stdin.readline()
"""


def fake(tmp_path: Path, source: str) -> tuple[Any, Path]:
    script = tmp_path / "host.py"
    script.write_text(source)
    seen = tmp_path / "seen.json"
    return (lambda _p, _pin: [sys.executable, str(script), str(seen)]), seen


def test_a_run_leaves_its_change_for_review_and_never_accepts(tmp_path: Path) -> None:
    command, seen = fake(tmp_path, FAKE_HOST)
    events: list[dict[str, Any]] = []
    outcome = asyncio.run(
        headless.run_request(
            tmp_path,
            "Make the door red",
            headless.Pin("claude", "m1", "high"),
            on_event=events.append,
            timeout=30,
            command=command,
        )
    )
    got = json.loads(seen.read_text())
    assert got[0] == {
        "kind": "prompt",
        "text": "Make the door red",
        "selection": {"mode": "auto", "model": "m1", "effort": "high"},
    }
    # What only an editor can do is refused, so the agent stages saved files.
    assert got[1]["kind"] == "editor_result" and not got[1]["result"]["success"]
    assert got[2]["kind"] == "editor_view_result" and not got[2]["result"]["success"]
    # The turn ended: ask when nothing is at work, never decide.
    assert got[3] == {"kind": "when_quiet"}
    assert all(m["kind"] != "decision" for m in got)
    assert outcome.state == "review" and outcome.transaction_id == "tx1"
    assert outcome.summary == "Red door" and outcome.reply == "Made it red."
    assert outcome.tasks == [
        {"task_id": "k1", "title": "Lamp", "transaction_id": "tx2"}
    ]
    kinds = [e["kind"] for e in events]
    for kind in ("turn_state", "tool_activity", "check_problems", "usage"):
        assert kind in kinds
    assert "message_delta" not in kinds and kinds[-1] == "quiet"
    # The host's input closes, so it closes and keeps what waits.
    assert (tmp_path / "seen.json.rest").read_text() == ""


def test_idle_starts_what_waits_and_waits_for_it(tmp_path: Path) -> None:
    command, seen = fake(tmp_path, IDLE_HOST)
    outcome = asyncio.run(
        headless.run_idle(tmp_path, headless.Pin("codex"), timeout=30, command=command)
    )
    assert json.loads(seen.read_text()) == [
        {"kind": "idle", "action": "now"},
        {"kind": "when_quiet"},
    ]
    assert outcome.state == "none"


def test_the_cli_streams_json_and_ends_with_the_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    command, _seen = fake(tmp_path, FAKE_HOST)
    monkeypatch.setattr(headless, "chat_command", command)
    args = ["run", "--project", str(tmp_path), "--request", "Red", "--json"]
    assert cli_main([*args, "--agent", "claude"]) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert lines[0]["kind"] == "connection"
    result = lines[-1]
    assert result["kind"] == "run_result" and result["state"] == "review"
    assert result["transaction_id"] == "tx1" and result["cost"] is None
    # No agent pinned and none used before: say how to choose one.
    assert cli_main(args) == 1
    assert "--agent" in json.loads(capsys.readouterr().out)["error"]


def test_the_chat_host_command_keeps_reviews_unless_it_accepts() -> None:
    pin = headless.Pin("claude")
    assert headless.chat_command(Path("p"), pin)[-1] == "--keep-reviews"
    from joinery import benchmark

    assert "--keep-reviews" not in benchmark.chat_command(Path("p"), pin)


def test_a_kept_change_outlives_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))

    def staged() -> FileTransaction:
        tx = FileTransaction(tmp_path)
        tx.begin("Note")
        (tx.stage / "note.txt").write_text("staged")
        tx.review()
        return tx

    async def go() -> None:
        gateway = ProjectGateway(tmp_path)
        kept = staged()
        gateway.transaction = kept
        gateway.keep_reviews = True
        lane = gateway.lane("task-1", "Lamp")
        in_lane = staged()
        lane.transaction = in_lane
        await gateway.close()
        assert FileTransaction.load(tmp_path, kept.id).state == "review"
        assert FileTransaction.load(tmp_path, in_lane.id).held
        # The editor's next chat host keeps them too, and drops an ordinary one.
        plain = staged()
        again = ProjectGateway(tmp_path)
        assert FileTransaction.load(tmp_path, kept.id).state == "review"
        assert FileTransaction.load(tmp_path, plain.id).state == "rejected"
        again.transaction = FileTransaction.load(tmp_path, kept.id)
        await again.close()  # Not a headless host: what it holds is discarded.
        assert FileTransaction.load(tmp_path, kept.id).state == "rejected"

    asyncio.run(go())


def test_the_host_says_when_it_is_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    events: list[dict[str, Any]] = []
    gateways: list[FakeGateway] = []

    def gateway(project: Path) -> FakeGateway:
        made = FakeGateway(project)
        gateways.append(made)
        return made

    monkeypatch.setattr(stdio, "emit", events.append)
    monkeypatch.setattr(stdio, "Bridge", FakeBridge)
    monkeypatch.setattr(stdio, "ProjectGateway", gateway)
    monkeypatch.setattr(stdio, "GatewayRPC", FakeRPC)
    monkeypatch.setattr(stdio, "Specialists", lambda *_a: AsyncMock())
    monkeypatch.setattr(stdio, "game_suggestions", lambda _p: {"kind": "suggestions"})

    async def reader(_stream: Any) -> tuple[asyncio.StreamReader, Any]:
        stream = asyncio.StreamReader()

        async def feed() -> None:
            stream.feed_data(b'{"kind": "prompt", "text": "Door", "chat_id": ""}\n')
            while not any(e.get("kind") == "timing" for e in events):
                await asyncio.sleep(0.01)
            stream.feed_data(b'{"kind": "when_quiet"}\n')
            while not any(e.get("kind") == "quiet" for e in events):
                await asyncio.sleep(0.01)
            stream.feed_eof()

        asyncio.get_running_loop().create_task(feed())
        return stream, type("T", (), {"close": lambda self: None})()

    monkeypatch.setattr(stdio, "input_reader", reader)
    assert asyncio.run(stdio.serve(tmp_path, "codex", keep_reviews=True)) == 0
    assert gateways[0].keep_reviews is True  # type: ignore[attr-defined]
    kinds = [e.get("kind") for e in events]
    assert kinds.index("timing") < kinds.index("quiet")
