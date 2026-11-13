"""The benchmark suite and its runner, with fake agents and a fake chat host."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from bridge.protocol import Done, Message, Prompt, Usage
from bridge.turn_runner import run_turn
from joinery import benchmark
from joinery.gateway import ProjectGateway
from joinery.runner import Result
from joinery.timing import TurnTiming
from joinery.turn import TurnController
from tests.test_crew import FakeConnection, wire, write
from tests.test_review_agents import Author

KINDS = {
    "model edit",
    "new prop",
    "texture recolour",
    "sound",
    "animation tweak",
    "script fix",
    "blockout",
    "kit batch",
    "scene layout",
    "UI text",
    "make and place",
}


def test_the_suite_covers_the_typical_requests_on_the_testbed() -> None:
    suite = benchmark.load()
    assert {case.kind for case in suite.cases} == KINDS
    assert suite.project == (benchmark.ROOT / "testbed").resolve()
    assert (suite.project / "project.godot").is_file()
    assert suite.repeats >= 1
    for case in suite.cases:
        assert case.request and case.expect
    seeded = {case.id: case.seed for case in suite.cases if case.seed}
    assert set(seeded) == {
        "animation-tweak",
        "script-fix",
        "ui-text",
        "make-and-place",
    }
    script = seeded["script-fix"]["game/door_toggle.gd"]
    assert script.startswith("extends Node3D") and "\tis_open = true" in script
    kitchen = seeded["make-and-place"]["game/kitchen.tscn"]
    assert '[node name="Table" type="Node3D" parent="."]' in kitchen
    assert 'type="MeshInstance3D" parent="Table"' in kitchen, "a top to rest on"
    for scene in ("game/door_swing.tscn", "ui/hud.tscn", "game/kitchen.tscn"):
        text = next(s[scene] for s in seeded.values() if scene in s)
        assert text.startswith("[gd_scene")
    # What the unseeded cases change is in the testbed.
    for path in ("art/props/door.blend", "recipes/sfx_door.scd", "main.tscn"):
        assert (suite.project / path).is_file()


def test_a_seed_path_stays_inside_the_project(tmp_path: Path) -> None:
    suite = tmp_path / "suite.toml"
    suite.write_text(
        '[[case]]\nid = "a"\nkind = "k"\nrequest = "r"\n'
        '[case.seed]\n"../outside.gd" = "x"\n'
    )
    with pytest.raises(ValueError, match="relative"):
        benchmark.load(suite)


def test_each_run_starts_from_a_fresh_copy_with_its_seeds(tmp_path: Path) -> None:
    suite = benchmark.load()
    case = next(c for c in suite.cases if c.id == "ui-text")
    first = benchmark.prepare(suite, case, tmp_path)
    (first / ".joinery/metrics").mkdir(parents=True)
    (first / ".joinery/metrics/old.jsonl").write_text("{}\n")
    again = benchmark.prepare(suite, case, tmp_path)
    assert again == first and not (again / ".joinery").exists()
    assert 'text = "Press E to open"' in (again / "ui/hud.tscn").read_text()
    assert (again / "art/props/door.blend").is_file()


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The testbed's apps and checks, faked: no worker, Docker or Godot runs."""
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    monkeypatch.setattr("joinery.gateway.approved", lambda _project: True)
    monkeypatch.setattr("joinery.gateway.MCPConnection", FakeConnection)
    monkeypatch.setattr("joinery.submission.approved_in", lambda *_: True)
    monkeypatch.setattr("joinery.submission.check_assets", lambda *_: [])

    async def run_offline(*_a: Any, **_k: Any) -> Result:
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", run_offline)


async def fake_agent(project: Path, case: benchmark.Case, pin: benchmark.Pin) -> None:
    """The fake agents the turn tests use: change the first expected file, submit,
    report usage; the change is accepted."""
    assert pin.agent == "codex" and pin.model == "m1"

    async def makes(lane: ProjectGateway) -> AsyncIterator[Any]:
        target = next(p for p in case.expect if not p.endswith("/"))
        await write(lane, "notes/" + Path(target).name + ".txt", case.request)
        assert lane.turn is not None
        await lane.turn.submit(case.kind)
        yield Usage(
            input_tokens=1000 + len(case.id), output_tokens=100, cached_tokens=400
        )
        yield Message(text="Done.")
        yield Done(ok=True)

    gateway = wire(ProjectGateway(project))
    controller = TurnController(gateway, lambda _e: None, pin.agent)
    try:
        await run_turn(
            controller,
            Author(gateway, [makes]),
            Prompt(text=case.request, project_dir=str(project)),
            lambda _e: None,
            TurnTiming(),
        )
        await controller.decide(controller.review_id, True)
    finally:
        await gateway.close()


def test_the_runner_reports_medians_from_each_runs_recorded_cost(
    tmp_path: Path, offline: None
) -> None:
    suite = benchmark.load()
    report = asyncio.run(
        benchmark.run(
            suite,
            fake_agent,
            tmp_path,
            pin=benchmark.Pin("codex", "m1"),
            repeats=2,
            only=("ui-text", "sound"),
        )
    )
    assert [c["id"] for c in report["cases"]] == ["sound", "ui-text"]
    sound = report["cases"][0]
    assert len(sound["runs"]) == 2
    run = sound["runs"][0]
    assert run["changes"] == 1 and run["outcome"] == "accepted"
    assert run["tokens"] == {"input": 1005, "output": 100, "cached": 400}
    assert run["rounds"] == {"agent_turns": 1, "send_backs": 0}
    assert sound["median"]["input_tokens"] == 1005
    assert report["median"]["runs"] == 4
    assert report["median"]["input_tokens"] == 1006
    assert report["agent"] == "codex" and report["model"] == "m1"
    text = benchmark.render(report)
    assert "| sound | sound | 2 |" in text and "| **all** |" in text
    assert "codex / m1" in text


def test_a_failed_run_is_reported_not_hidden(tmp_path: Path) -> None:
    async def breaks(_p: Path, _c: benchmark.Case, _pin: benchmark.Pin) -> None:
        raise RuntimeError("The agent didn't connect.")

    suite = benchmark.load()
    report = asyncio.run(
        benchmark.run(suite, breaks, tmp_path, repeats=1, only=("sound",))
    )
    assert report["cases"][0]["median"] == {"runs": 0}
    assert "- sound: The agent didn't connect." in benchmark.render(report)


FAKE_HOST = """
import json, sys
def say(event):
    print(json.dumps(event), flush=True)
say({"kind": "connection", "state": "connected"})
prompt = json.loads(sys.stdin.readline())
assert prompt["kind"] == "prompt", prompt
say({"kind": "editor_action", "request_id": "r1", "payload": {}})
answer = json.loads(sys.stdin.readline())
say({"kind": "file_preview", "transaction_id": "tx1"})
say({"kind": "timing", "outcome": "done"})
decision = json.loads(sys.stdin.readline())
with open(sys.argv[1], "w") as out:
    json.dump({"prompt": prompt, "answer": answer, "decision": decision}, out)
say({"kind": "file_decision", "transaction_id": "tx1", "accepted": True})
sys.stdin.readline()
"""


def test_the_live_driver_speaks_the_chat_hosts_protocol(tmp_path: Path) -> None:
    """With a stand-in host: the request goes with the pinned model, a live edit is
    refused (no editor), and the change is accepted when the turn ends."""
    import json

    host = tmp_path / "host.py"
    host.write_text(FAKE_HOST)
    seen = tmp_path / "seen.json"
    driver = benchmark.LiveDriver(
        timeout=30, command=lambda _p, _pin: [sys.executable, str(host), str(seen)]
    )
    case = benchmark.Case("ui-text", "UI text", "Change the prompt.")
    asyncio.run(driver(tmp_path, case, benchmark.Pin("claude", "m-pinned", "high")))
    got = json.loads(seen.read_text())
    assert got["prompt"]["text"] == "Change the prompt."
    assert got["prompt"]["selection"] == {
        "mode": "auto",
        "model": "m-pinned",
        "effort": "high",
    }
    assert got["answer"]["kind"] == "editor_result"
    assert got["answer"]["result"]["success"] is False
    assert got["decision"] == {
        "kind": "decision",
        "transaction_id": "tx1",
        "accept": True,
    }
    with pytest.raises(ValueError, match="agent"):
        asyncio.run(driver(tmp_path, case, benchmark.Pin("")))


def test_the_eval_runner_runs_nothing_without_live(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sys.path.insert(0, str(benchmark.ROOT / "evals"))
    try:
        import run as evals_run  # type: ignore[import-not-found]
    finally:
        sys.path.remove(str(benchmark.ROOT / "evals"))
    assert evals_run.main(["--suite", "benchmark"]) == 0
    out = capsys.readouterr().out
    assert "script-fix" in out and "Nothing was run" in out
    assert evals_run.main(["--suite", "benchmark", "--live"]) == 2
