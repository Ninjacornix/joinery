"""A request's to-do and its done-when checks (ADR 0060), with fake agents: no inference.

The agent proposes steps and done-when items with ``propose_todo``; the sidebar shows them
and, for a request with several steps, the agent waits for the user's Go. After the change
passes Joinery's checks every item is checked (a seeded playtest of the stage, the tests,
the checks) and the review shows Pass, Fail or Inconclusive with the evidence. A Fail goes
back to the agent once; it never drops the change.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from bridge.instructions import REVIEW_GUIDE
from bridge.protocol import Done, Message, Prompt
from bridge.turn_runner import run_turn
from joinery import done_when, metrics, playtest
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.roles import role_violation
from joinery.timing import TurnTiming
from joinery.turn import WAITING_FOR_GO, TurnController
from servers.joinery_registry.proxy import make_server
from tests.test_crew import project, wire, write

__all__ = ["project"]

TODO = {
    "steps": [
        "Put a lever beside the gate",
        "Open the gate when the lever is pulled",
        "Put the key in the vault",
    ],
    "done_when": [
        {"text": "The player can reach the vault", "kind": "reach", "target": "vault"},
        {
            "text": "The gate opens when the lever is pulled",
            "kind": "test",
            "target": "res://tests/test_gate.gd",
        },
        {"text": "No errors in 60 s of play", "kind": "no_errors", "seconds": 60},
        {"text": "The key looks like brass", "kind": "look"},
    ],
}


def run(step: Callable[[], Any]) -> Any:
    return asyncio.run(step())


def todo_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("kind") == "todo"]


class Author:
    """The author agent: each prompt runs the next scripted step on the chat's gateway."""

    def __init__(self, gateway: ProjectGateway, steps: list[Any]) -> None:
        self.gateway = gateway
        self.steps = steps
        self.prompts: list[Prompt] = []

    async def send(self, prompt: Prompt) -> AsyncIterator[Any]:
        self.prompts.append(prompt)
        step = self.steps.pop(0)
        async for event in step(self.gateway):
            yield event

    def editor_result(self, _id: str, _result: dict[str, Any]) -> None:
        return None


def report(reached: bool, *, errors: bool = False) -> dict[str, Any]:
    """A playtest report as joinery.playtest.run returns it."""
    items = (
        [
            {
                "kind": "script_error",
                "title": "Script error",
                "detail": "Invalid call. Nonexistent function 'open'.",
                "where": "res://game/gate.gd:12",
                "t": 4.5,
            }
        ]
        if errors
        else []
    )
    return {
        "seed": done_when.SEED,
        "played_seconds": 60.0,
        "items": items,
        "reach": [
            {
                "target": "vault",
                "found": True,
                "reached": reached,
                "t": 42.0 if reached else None,
                "closest": 0.0 if reached else 3.4,
                "node": "Vault",
            }
        ],
    }


class Plays(list[playtest.Options]):
    """Every check run's options, and the reports the runs return, in order."""

    def __init__(self) -> None:
        super().__init__()
        self.reports: list[dict[str, Any]] = []


@pytest.fixture
def plays(monkeypatch: pytest.MonkeyPatch) -> Plays:
    """Every check run's options; the reports come from ``plays.reports``."""
    asked = Plays()
    reports = asked.reports

    async def fake(options: playtest.Options) -> dict[str, Any]:
        asked.append(options)
        return reports.pop(0) if len(reports) > 1 else reports[0]

    monkeypatch.setattr("joinery.done_when._play", fake)
    return asked


# --- The proposal ------------------------------------------------------------------------


def test_a_proposal_is_checked_and_numbered() -> None:
    todo = done_when.proposal(TODO, "Lock the vault behind a lever gate")
    assert todo.steps == TODO["steps"]
    assert [c.id for c in todo.done_when] == ["d1", "d2", "d3", "d4"]
    assert [c.kind for c in todo.done_when] == ["reach", "test", "no_errors", "look"]
    assert todo.request == "Lock the vault behind a lever gate"
    for bad in (
        {"steps": []},
        {"steps": ["x"] * 9},
        {"steps": ["a"], "done_when": [{"text": "x", "kind": "fly"}]},
        {"steps": ["a"], "done_when": [{"text": "x", "kind": "reach"}]},
        {
            "steps": ["a"],
            "done_when": [{"text": "x", "kind": "test", "target": "tests/a.gd"}],
        },
        {
            "steps": ["a"],
            "done_when": [{"text": "x", "kind": "check", "target": "vibe"}],
        },
        {
            "steps": ["a"],
            "done_when": [{"text": "x", "kind": "no_errors", "seconds": 5}],
        },
        {"steps": ["a"], "done_when": [{"text": " ", "kind": "look"}]},
    ):
        with pytest.raises((ValueError, TypeError)):
            done_when.proposal(bad)


def test_the_user_rewords_and_removes_items_but_kinds_stay_the_agents() -> None:
    todo = done_when.proposal(TODO)
    edited = done_when.edited(
        todo,
        {
            "steps": ["Put a lever by the gate", "", "Put the key in the vault"],
            "done_when": [
                {"id": "d1", "text": "I can walk into the vault", "kind": "look"},
                {"id": "d3", "text": "No errors"},
                {"id": "d9", "text": "Invented by the card"},
            ],
        },
    )
    assert edited.steps == ["Put a lever by the gate", "Put the key in the vault"]
    assert [(c.id, c.kind, c.text) for c in edited.done_when] == [
        ("d1", "reach", "I can walk into the vault"),
        ("d3", "no_errors", "No errors"),
    ]
    assert edited.id == todo.id
    with pytest.raises(ValueError, match="at least one step"):
        done_when.edited(todo, {"steps": [" "]})


def test_go_waits_for_several_steps_by_default(project: Path) -> None:
    one = done_when.proposal({"steps": ["Make the door red"]})
    many = done_when.proposal(TODO)
    assert done_when.go_mode(project) == "multi"
    assert done_when.waits_for_go(many, "multi") and not done_when.waits_for_go(
        one, "multi"
    )
    assert done_when.waits_for_go(one, "always")
    assert not done_when.waits_for_go(many, "never")
    (project / "joinery.toml").write_text('[agents]\ntodo_go = "never"\n')
    assert done_when.go_mode(project) == "never"
    (project / "joinery.toml").write_text('[agents]\ntodo_go = "sometimes"\n')
    assert done_when.go_mode(project) == "multi"


def test_go_tells_the_agent_the_todo_as_the_user_left_it() -> None:
    todo = done_when.proposal(TODO, "Lock the vault")
    text = done_when.go_prompt(todo)
    assert "1. Put a lever beside the gate" in text
    assert "- The player can reach the vault" in text
    assert "submit_for_review" in text and "The request was: Lock the vault" in text


def test_agents_are_told_when_to_propose_a_todo() -> None:
    assert "call propose_todo before changing anything" in REVIEW_GUIDE
    assert "waiting_for_go, end your turn" in REVIEW_GUIDE
    assert "Skip it for small, one-step requests" in REVIEW_GUIDE


# --- Checking each item ----------------------------------------------------------------


def test_each_kind_is_checked_with_evidence(tmp_path: Path, plays: Plays) -> None:
    stage = tmp_path / "stage"
    (stage / "tests").mkdir(parents=True)
    (stage / "project.godot").write_text("config_version=5\n")
    (stage / "tests/test_gate.gd").write_text("extends GutTest\n")
    (stage / "addons/gut").mkdir(parents=True)
    (stage / "addons/gut/gut_cmdln.gd").write_text("extends SceneTree\n")
    plays.reports.append(report(True))
    todo = done_when.proposal(
        {
            **TODO,
            "done_when": [
                *TODO["done_when"],
                {"text": "It imports", "kind": "check", "target": "godot_import"},
            ],
        }
    )
    results = run(
        lambda: done_when.verify(
            todo,
            project=tmp_path,
            stage=stage,
            ran=["references", "Godot import", "tests"],
            skipped=[],
        )
    )
    by_id = {r.id: r for r in results}
    assert by_id["d1"].status == "pass"
    assert by_id["d1"].evidence == "The bot reached Vault at 0:42, seed 4242."
    assert by_id["d2"].status == "pass"
    assert "ran with the project's tests and passed" in by_id["d2"].evidence
    assert by_id["d3"].status == "pass"
    assert by_id["d3"].evidence == "No errors in 1:00 of play, seed 4242."
    assert by_id["d4"].status == "inconclusive"
    assert by_id["d5"].status == "pass"
    # One seeded run of the stage, as long as the longest asks, aiming for every place.
    (options,) = plays
    assert options.project == stage and options.seed == done_when.SEED
    assert options.seconds == 60 and options.reach == ["vault"]
    assert options.out is not None and options.out.is_relative_to(
        tmp_path / ".joinery" / "playtests"
    )


def test_a_place_not_reached_or_an_error_fails_and_a_missing_name_is_inconclusive(
    tmp_path: Path, plays: Plays
) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "project.godot").write_text("config_version=5\n")
    missing = report(False, errors=True)
    missing["reach"].append(
        {"target": "kitchen", "found": False, "reached": False, "t": None}
    )
    plays.reports.append(missing)
    todo = done_when.proposal(
        {
            "steps": ["a"],
            "done_when": [
                {"text": "Reach the vault", "kind": "reach", "target": "the vault"},
                {"text": "Reach the kitchen", "kind": "reach", "target": "kitchen"},
                {"text": "No errors", "kind": "no_errors"},
                {
                    "text": "Gate test",
                    "kind": "test",
                    "target": "res://tests/test_gate.gd",
                },
            ],
        }
    )
    results = run(
        lambda: done_when.verify(
            todo, project=tmp_path, stage=stage, ran=[], skipped=["tests"]
        )
    )
    vault, kitchen, errors, test = results
    assert vault.status == "fail"
    assert (
        "didn't get to Vault in 1:00 of play (closest 3.4), seed 4242" in vault.evidence
    )
    assert kitchen.status == "inconclusive" and "named like" in kitchen.evidence
    assert errors.status == "fail"
    assert errors.evidence.startswith(
        "Script error at 0:04: Invalid call. Nonexistent function 'open'. "
        "(res://game/gate.gd:12)"
    )
    assert test.status == "fail" and "isn't in the change" in test.evidence
    assert done_when.counts_line(results) == "0 of 4 done"


def test_a_live_edit_alone_cannot_be_played_yet(tmp_path: Path) -> None:
    todo = done_when.proposal(
        {"steps": ["a"], "done_when": [{"text": "No errors", "kind": "no_errors"}]}
    )
    (result,) = run(
        lambda: done_when.verify(todo, project=tmp_path, stage=None, ran=[], skipped=[])
    )
    assert result.status == "inconclusive" and "live preview" in result.evidence


# --- The turn ----------------------------------------------------------------------------


def test_a_multi_step_request_waits_for_go_and_changes_nothing_until_then(
    project: Path,
) -> None:
    async def go() -> None:
        events: list[dict[str, Any]] = []
        gateway = wire(ProjectGateway(project))
        controller = TurnController(gateway, events.append, "codex")
        seen: list[dict[str, Any]] = []

        async def proposes(lane: ProjectGateway) -> AsyncIterator[Any]:
            assert lane.turn is not None
            seen.append(lane.turn.propose_todo(TODO))
            with pytest.raises(ValueError, match="pressed Go"):
                await write(lane, "notes/gate.md", "lever\n")
            with pytest.raises(ValueError, match="pressed Go"):
                lane.turn.propose_todo(TODO)
            yield Message(text="Here is my to-do.")
            yield Done(ok=True)

        author = Author(gateway, [proposes])
        try:
            outcome = await run_turn(
                controller,
                author,
                Prompt(text="Lock the vault", project_dir=str(project)),
                events.append,
                TurnTiming(),
                request="Lock the vault",
            )
            assert outcome == "done"
            (answer,) = seen
            assert answer["status"] == "waiting_for_go"
            assert WAITING_FOR_GO in answer["next"]
            (shown,) = todo_events(events)
            assert shown["state"] == "waiting" and shown["steps"] == TODO["steps"]
            assert [d["kind"] for d in shown["done_when"]] == [
                "reach",
                "test",
                "no_errors",
                "look",
            ]
            assert controller.pending_todo is not None
            assert gateway.transaction is None or not gateway.transaction.diff()
            # Another request instead of Go drops the to-do.
            controller.begin("Something else")
            assert todo_events(events)[-1] == {
                "kind": "todo",
                "todo_id": shown["todo_id"],
                "state": "dropped",
            }
            assert controller.pending_todo is None
        finally:
            await gateway.close()

    run(go)


def test_go_starts_the_work_and_a_fail_goes_back_to_the_agent_once(
    project: Path, plays: Plays
) -> None:
    """The done-when checks run after Joinery's checks; the vault the bot couldn't reach
    reaches the author as a fixable problem, the fixed change opens with every result."""
    plays.reports.extend([report(False), report(True)])

    async def go() -> None:
        events: list[dict[str, Any]] = []
        gateway = wire(ProjectGateway(project))
        gateway.progress = lambda text: events.append(
            {"kind": "progress", "text": text}
        )
        controller = TurnController(gateway, events.append, "codex")
        results: list[dict[str, Any]] = []

        async def proposes(lane: ProjectGateway) -> AsyncIterator[Any]:
            assert lane.turn is not None
            lane.turn.propose_todo(TODO)
            yield Done(ok=True)

        async def works(lane: ProjectGateway) -> AsyncIterator[Any]:
            await write(lane, "notes/gate.md", "lever, gate shut\n")
            assert lane.turn is not None
            results.append((await lane.turn.submit("The vault gate")).as_tool_result())
            yield Message(text="Submitted.")
            yield Done(ok=True)

        async def fixes(lane: ProjectGateway) -> AsyncIterator[Any]:
            await write(lane, "notes/gate.md", "lever, gate opens\n")
            assert lane.turn is not None
            results.append((await lane.turn.submit("The vault gate")).as_tool_result())
            yield Message(text="The vault gate is open for review.")
            yield Done(ok=True)

        author = Author(gateway, [proposes, works, fixes])
        timing = TurnTiming()
        try:
            await run_turn(
                controller,
                author,
                Prompt(text="Lock the vault", project_dir=str(project)),
                events.append,
                TurnTiming(),
                request="Lock the vault",
            )
            held = controller.pending_todo
            assert held is not None
            # The user removes the test item and the look item, then presses Go.
            todo = done_when.edited(
                held,
                {
                    "steps": TODO["steps"],
                    "done_when": [
                        {"id": "d1", "text": "The player can reach the vault"},
                        {"id": "d3", "text": "No errors in 60 s of play"},
                    ],
                },
            )
            outcome = await run_turn(
                controller,
                author,
                Prompt(text=done_when.go_prompt(todo), project_dir=str(project)),
                events.append,
                timing,
                request=todo.request,
                todo=todo,
            )
            assert outcome == "done"
            sent_back, opened = results
            assert sent_back["status"] == "failed" and not sent_back["final"]
            (problem,) = sent_back["problems"]
            assert problem["check"] == "done_when"
            assert problem["message"].startswith(
                "The player can reach the vault: The bot didn't get to Vault"
            )
            # A send-back costs no submit attempt.
            assert sent_back["attempts_left"] == 3
            assert "done-when" in sent_back["next"]
            assert opened["status"] == "in_review"
            (preview,) = [e for e in events if e.get("kind") == "file_preview"]
            assert [(r["id"], r["status"]) for r in preview["done_when"]] == [
                ("d1", "pass"),
                ("d3", "pass"),
            ]
            assert preview["done_when"][0]["evidence"].startswith(
                "The bot reached Vault"
            )
            assert preview["done_when_summary"] == "2 of 2 done"
            states = [e["state"] for e in todo_events(events)]
            assert states == [
                "waiting",
                "checking",
                "checked",
                "fixing",
                "checking",
                "checked",
            ]
            checked = [e for e in todo_events(events) if e["state"] == "checked"]
            assert checked[0]["summary"] == "1 of 2 done"
            assert "Checking the done-when items…" in [
                e["text"] for e in events if e.get("kind") == "progress"
            ]
            # Two runs of the same seed: the first change, then the fixed one.
            assert [o.seed for o in plays] == [done_when.SEED, done_when.SEED]
            # The send-back counts like the reviewer's.
            assert timing.trace.attributes[metrics.SEND_BACKS] == 1
        finally:
            await gateway.close()

    run(go)


def test_a_fail_that_stays_opens_with_its_result_and_never_drops_the_change(
    project: Path, plays: Plays
) -> None:
    plays.reports.append(report(False))
    (project / "joinery.toml").write_text(
        '[tools]\nenabled=["godot"]\n[agents]\ntodo_go = "never"\n'
    )

    async def go() -> None:
        events: list[dict[str, Any]] = []
        gateway = wire(ProjectGateway(project))
        controller = TurnController(gateway, events.append, "codex")
        answers: list[dict[str, Any]] = []

        async def all_at_once(lane: ProjectGateway) -> AsyncIterator[Any]:
            assert lane.turn is not None
            answers.append(lane.turn.propose_todo(TODO))
            await write(lane, "notes/gate.md", "lever\n")
            answers.append((await lane.turn.submit("The gate")).as_tool_result())
            # The agent can't make the bot reach it: it says why and submits again.
            answers.append((await lane.turn.submit("The gate")).as_tool_result())
            yield Message(text="The bot can't make the jump; a person can.")
            yield Done(ok=True)

        author = Author(gateway, [all_at_once])
        try:
            outcome = await run_turn(
                controller,
                author,
                Prompt(text="Lock the vault", project_dir=str(project)),
                events.append,
                TurnTiming(),
                request="Lock the vault",
            )
            assert outcome == "done"
            go_answer, sent_back, opened = answers
            # The project never waits for Go: the agent carries on at once.
            assert go_answer["status"] == "go"
            assert todo_events(events)[0]["state"] == "going"
            assert sent_back["problems"][0]["check"] == "done_when"
            assert opened["status"] == "in_review"
            (preview,) = [e for e in events if e.get("kind") == "file_preview"]
            statuses = {r["id"]: r["status"] for r in preview["done_when"]}
            assert statuses == {
                "d1": "fail",
                "d2": "fail",
                "d3": "pass",
                "d4": "inconclusive",
            }
            # The stage didn't change between the two submits: one playtest only.
            assert len(plays) == 1
        finally:
            await gateway.close()

    run(go)


# --- Every agent, the same gate ------------------------------------------------------------


def test_only_the_lead_proposes_a_todo(project: Path) -> None:
    verdict = role_violation("worker", "mcp", "joinery-registry", "propose_todo")
    assert verdict is not None and "to-do" in verdict.reason
    assert role_violation("lead", "mcp", "joinery-registry", "propose_todo") is None
    assert role_violation("reviewer", "mcp", "joinery-registry", "propose_todo")

    async def go() -> None:
        gateway = ProjectGateway(project)
        controller = TurnController(gateway, lambda _e: None, "codex")
        controller.begin("Lock the vault")
        worker = GatewayRPC(gateway, None, role="worker")
        lead = GatewayRPC(gateway, None)
        await worker.start()
        await lead.start()
        try:
            with pytest.raises(RuntimeError, match="Only Journeyman's own request"):
                await rpc(worker.path, "propose_todo", TODO)
            answer = await rpc(lead.path, "propose_todo", TODO)
            assert answer["status"] == "waiting_for_go"
        finally:
            await worker.close()
            await lead.close()

    run(go)


def test_the_tool_passes_the_todo_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[dict[str, Any]] = []

    async def fake(_socket: Path, method: str, params: dict[str, Any]) -> Any:
        assert method == "propose_todo"
        asked.append(params)
        return {"status": "go", "todo_id": "t1", "next": "Carry on."}

    monkeypatch.setattr("servers.joinery_registry.proxy.rpc", fake)

    async def go() -> None:
        async with Client(make_server(tmp_path / "gateway.sock")) as client:
            result = await client.call_tool(
                "propose_todo",
                {
                    "steps": ["Put a lever by the gate"],
                    "done_when": [
                        {"text": "Reach the vault", "kind": "reach", "target": "vault"}
                    ],
                },
            )
        (text,) = result.content
        assert isinstance(text, TextContent)
        assert json.loads(text.text)["status"] == "go"
        assert asked == [
            {
                "steps": ["Put a lever by the gate"],
                "done_when": [
                    {"text": "Reach the vault", "kind": "reach", "target": "vault"}
                ],
            }
        ]

    run(go)
