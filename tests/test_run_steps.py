"""Steps, then run (ADR 0072): a to-do's independent steps as parallel tasks.

Fake agents only, no inference. With ``[agents] run_steps = "together"`` the steps the
agent's to-do says need nothing first are made by parallel tasks of the crew; the lead
agent takes no turn meanwhile and hears back once, at the end or when a step fails.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from bridge.crew import Crew
from bridge.protocol import Done, Message, Prompt
from bridge.steps import StepRun, title
from bridge.turn_runner import run_turn
from joinery import done_when, metrics
from joinery.gateway import ProjectGateway
from joinery.timing import TurnTiming
from joinery.turn import STEPS_RUNNING, TurnController
from servers.joinery_registry.proxy import make_server
from tests.test_crew import Endpoint, project, wire, write

__all__ = ["project"]

Step = Callable[[ProjectGateway], AsyncIterator[Any]]

#: Three steps: two separate props that need nothing first, then the shelf that holds both.
TODO = {
    "steps": ["Make a chair", "Make a lamp", "Put both on the shelf"],
    "after": [[], [], [1, 2]],
    "done_when": [{"text": "The shelf looks tidy", "kind": "look"}],
}
TWO = {
    "steps": ["Make a chair", "Make a lamp"],
    "after": [[], []],
    "done_when": [{"text": "Both look like oak", "kind": "look"}],
}


def run(step: Callable[[], Any]) -> Any:
    return asyncio.run(step())


def makes(path: str) -> Step:
    async def step(lane: ProjectGateway) -> AsyncIterator[Any]:
        await write(lane, path, path + "\n")
        assert lane.turn is not None
        result = await lane.turn.submit(path)
        yield Message(text=f"{path}: {result.status}")
        yield Done(ok=True)

    return step


def breaks(_lane: ProjectGateway) -> AsyncIterator[Any]:
    async def step() -> AsyncIterator[Any]:
        raise RuntimeError("The provider went away.")
        yield Done(ok=False)  # pragma: no cover

    return step()


class StepAgent:
    """A task's fake provider: what it does depends on which step its prompt names."""

    def __init__(self, test: Steps) -> None:
        self.test = test
        self.lane: ProjectGateway | None = None

    def bind_gateway(self, path: Any) -> None:
        self.lane = path

    async def connect(self, _project: Path, _profile: str = "") -> None:
        return None

    async def send(self, prompt: Prompt) -> AsyncIterator[Any]:
        assert self.lane is not None
        self.test.task_prompts.append(prompt)
        found = re.search(r"Your step \((\d+) of", prompt.text)
        assert found is not None, prompt.text
        async for event in self.test.behaviours[int(found.group(1))](wire(self.lane)):
            yield event

    def editor_result(self, _id: str, _result: dict[str, Any]) -> None:
        return None

    async def cancel(self) -> None:
        return None

    async def close(self) -> None:
        return None


class Lead:
    """The lead agent: each prompt runs the next scripted step on the chat's gateway."""

    def __init__(self, gateway: ProjectGateway, steps: list[Step]) -> None:
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


class Steps:
    """A chat host in miniature: the lead's controller, the crew and the step runner."""

    def __init__(self, project: Path, limit: int = 3) -> None:
        self.project = project
        self.events: list[dict[str, Any]] = []
        self.task_prompts: list[Prompt] = []
        self.behaviours: dict[int, Step] = {}
        self.gateway = wire(ProjectGateway(project))
        self.gateway.progress = lambda text: self.events.append(
            {"kind": "progress", "text": text}
        )
        self.controller = TurnController(self.gateway, self.events.append, "codex")
        self.controller.runs_steps = True

        async def on_stop(_reason: str) -> None:
            return None

        self.crew = Crew(
            self.gateway,
            self.events.append,
            agent="codex",
            profile="",
            limit=limit,
            bridge=lambda _agent: StepAgent(self),
            endpoint=Endpoint,
            on_stop=on_stop,
            lead_busy=lambda: not self.controller.stepping,
        )
        self.runner = StepRun(
            self.crew,
            lambda: Prompt(text="", project_dir=str(project)),
            self.events.append,
            self.gateway.progress,
            poll=0.01,
        )

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("kind") == kind]

    async def close(self) -> None:
        await self.crew.close()
        await self.gateway.close()


@pytest.fixture
def together(project: Path) -> Path:
    (project / "joinery.toml").write_text(
        '[tools]\nenabled=["godot"]\n[agents]\nrun_steps = "together"\n'
    )
    return project


# --- The to-do's order ---------------------------------------------------------------


def test_each_step_needs_the_one_before_unless_the_agent_says_otherwise() -> None:
    plain = done_when.proposal({"steps": ["a", "b", "c"]})
    assert [plain.needs(i) for i in range(3)] == [[], [1], [2]]
    assert done_when.independent(plain) == [0]
    told = done_when.proposal(TODO)
    assert told.as_dict()["after"] == [[], [], [1, 2]]
    assert done_when.independent(told) == [0, 1]
    for bad in ([[], [2]], [[]], [[], [0]], [[], ["1"]], [[], [True]]):
        with pytest.raises(ValueError, match="after"):
            done_when.proposal({"steps": ["a", "b"], "after": bad})


def test_steps_run_together_only_when_asked_and_two_or_more_are_free(
    project: Path,
) -> None:
    told = done_when.proposal(TODO)
    assert done_when.run_steps_mode(project) == "one_by_one"
    assert done_when.runs_together(told, "one_by_one") == []
    assert done_when.runs_together(told, "together") == [0, 1]
    assert (
        done_when.runs_together(done_when.proposal({"steps": ["a", "b"]}), "together")
        == []
    )
    (project / "joinery.toml").write_text('[agents]\nrun_steps = "together"\n')
    assert done_when.run_steps_mode(project) == "together"
    (project / "joinery.toml").write_text('[agents]\nrun_steps = "sometimes"\n')
    assert done_when.run_steps_mode(project) == "one_by_one"


def test_removing_a_step_on_the_card_puts_the_steps_back_in_order() -> None:
    told = done_when.proposal(TODO)
    reworded = done_when.edited(told, {"steps": ["A chair", "A lamp", "Shelve them"]})
    assert reworded.after == told.after
    shorter = done_when.edited(told, {"steps": ["A chair", "Shelve it"]})
    assert [shorter.needs(i) for i in range(2)] == [[], [1]]


def test_the_lead_hears_what_each_step_became_and_what_is_left() -> None:
    told = done_when.proposal(TODO, "Furnish the study")
    outcomes = [
        done_when.StepOutcome(1, "Make a chair", "accepted"),
        done_when.StepOutcome(2, "Make a lamp", "failed", "Blender stopped."),
    ]
    assert done_when.remaining(told, outcomes) == [1, 2]
    text = done_when.steps_prompt(told, outcomes)
    assert "Joinery ran steps 1 and 2 of your to-do as parallel tasks" in text
    assert "- Step 1 (Make a chair): made and accepted" in text
    assert "- Step 2 (Make a lamp): failed: Blender stopped." in text
    assert "Steps left:\n2. Make a lamp\n3. Put both on the shelf" in text
    assert "The request was: Furnish the study" in text
    assert done_when.step_words([1, 2, 4]) == "steps 1, 2 and 4"
    assert title(3, "x" * 80).startswith("Step 3 · ") and len(title(3, "x" * 80)) == 48


# --- Running -------------------------------------------------------------------------


def test_go_runs_independent_steps_as_tasks_and_skips_the_lead_when_none_are_left(
    together: Path,
) -> None:
    async def go() -> None:
        test = Steps(together)
        test.behaviours = {1: makes("notes/chair.txt"), 2: makes("notes/lamp.txt")}
        # The lead is never asked: popping from an empty script would fail the test.
        lead = Lead(test.gateway, [])
        todo = done_when.proposal(TWO, "Furnish the study")
        timing = TurnTiming()
        try:
            outcome = await run_turn(
                test.controller,
                lead,
                Prompt(text=done_when.go_prompt(todo), project_dir=str(together)),
                test.events.append,
                timing,
                request=todo.request,
                todo=todo,
                steps=test.runner.run,
            )
            assert outcome == "done" and lead.prompts == []
            tasks = {t.title: t for t in test.crew.tasks.values()}
            assert sorted(tasks) == ["Step 1 · Make a chair", "Step 2 · Make a lamp"]
            assert {t.state for t in tasks.values()} == {"in_review"}
            assert {t.origin for t in tasks.values()} == {"lead"}
            # Each step's agent is told its step alone, with the request for context.
            texts = sorted(p.text for p in test.task_prompts)
            assert texts[0].startswith("Your step (1 of 2): Make a chair")
            assert "Don't call start_task, start_variants or propose_todo" in (
                test.task_prompts[0].rules
            )
            states = [e["state"] for e in test.of("todo")]
            assert states == ["steps", "checked"]
            assert test.of("todo")[0]["together"] == [1, 2]
            (checked,) = [e for e in test.of("todo") if e["state"] == "checked"]
            assert checked["results"][0]["status"] == "inconclusive"
            assert "separate changes" in checked["results"][0]["evidence"]
            (note,) = [m for m in test.of("message") if "at the same time" in m["text"]]
            assert note["text"].startswith("Made steps 1 and 2 at the same time")
            assert "Making steps 1 and 2 at once…" in [
                e["text"] for e in test.of("progress")
            ]
            # Measured: how the steps ran, one span per step.
            attrs = timing.trace.attributes
            assert attrs[metrics.STEPS_MODE] == "together"
            assert attrs[metrics.STEPS_TOGETHER] == 2
            assert attrs[metrics.STEPS_FAILED] == 0
            assert attrs[metrics.STEPS_BACK] is False
            assert timing.trace.path is not None
            spans = metrics.read(timing.trace.path)
            steps = [s for s in spans if s["attributes"][metrics.KIND] == metrics.STEP]
            assert sorted(s["attributes"][metrics.STEP_NUMBER] for s in steps) == [1, 2]
            summary = metrics.summarise("x", spans)
            assert summary["steps"] == {
                "mode": "together",
                "together": 2,
                "failed": 0,
                "back_to_lead": False,
            }
            assert "steps:  together, 2 as parallel tasks, no agent turn after" in (
                metrics.render([summary])
            )
        finally:
            await test.close()

    run(go)


def test_steps_that_others_need_are_decided_first_then_the_lead_does_the_rest(
    together: Path,
) -> None:
    async def go() -> None:
        test = Steps(together)
        test.behaviours = {1: makes("notes/chair.txt"), 2: makes("notes/lamp.txt")}

        async def shelves(lane: ProjectGateway) -> AsyncIterator[Any]:
            # Both props were accepted before the lead heard back: they are in the project.
            assert (together / "notes/chair.txt").is_file()
            assert (together / "notes/lamp.txt").is_file()
            await write(lane, "notes/shelf.txt", "chair, lamp\n")
            assert lane.turn is not None
            await lane.turn.submit("The shelf")
            yield Message(text="The shelf holds both.")
            yield Done(ok=True)

        lead = Lead(test.gateway, [shelves])
        todo = done_when.proposal(TODO, "Furnish the study")

        async def user_reviews() -> None:
            while (
                len([t for t in test.crew.tasks.values() if t.state == "in_review"]) < 2
            ):
                await asyncio.sleep(0.01)
            # Nobody is asked anything while the steps wait for the user.
            assert lead.prompts == []
            for task in list(test.crew.tasks.values()):
                await test.crew.decide(task.transaction_id, True)

        reviewer = asyncio.create_task(user_reviews())
        try:
            outcome = await run_turn(
                test.controller,
                lead,
                Prompt(text=done_when.go_prompt(todo), project_dir=str(together)),
                test.events.append,
                TurnTiming(),
                request=todo.request,
                todo=todo,
                steps=test.runner.run,
            )
            await reviewer
            assert outcome == "done"
            (asked,) = lead.prompts
            assert "- Step 1 (Make a chair): made and accepted" in asked.text
            assert "Steps left:\n3. Put both on the shelf" in asked.text
            assert "Review steps 1 and 2 to go on with the rest…" in [
                e["text"] for e in test.of("progress")
            ]
            assert test.controller.review_open
        finally:
            reviewer.cancel()
            await test.close()

    run(go)


def test_a_failed_step_comes_back_to_the_lead_at_once(together: Path) -> None:
    async def go() -> None:
        test = Steps(together)
        test.behaviours = {1: makes("notes/chair.txt"), 2: breaks}

        async def redoes(lane: ProjectGateway) -> AsyncIterator[Any]:
            await write(lane, "notes/lamp.txt", "lamp\n")
            assert lane.turn is not None
            await lane.turn.submit("The lamp")
            yield Done(ok=True)

        lead = Lead(test.gateway, [redoes])
        todo = done_when.proposal(TWO, "Furnish the study")
        timing = TurnTiming()
        try:
            await run_turn(
                test.controller,
                lead,
                Prompt(text=done_when.go_prompt(todo), project_dir=str(together)),
                test.events.append,
                timing,
                request=todo.request,
                todo=todo,
                steps=test.runner.run,
            )
            (asked,) = lead.prompts
            assert (
                "- Step 2 (Make a lamp): failed: The provider went away." in asked.text
            )
            assert "Steps left:\n2. Make a lamp" in asked.text
            assert timing.trace.attributes[metrics.STEPS_FAILED] == 1
            assert timing.trace.attributes[metrics.STEPS_BACK] is True
        finally:
            await test.close()

    run(go)


def test_a_todo_that_runs_on_without_go_hands_its_steps_over_when_the_lead_ends(
    together: Path,
) -> None:
    (together / "joinery.toml").write_text(
        '[tools]\nenabled=["godot"]\n[agents]\nrun_steps = "together"\ntodo_go = "never"\n'
    )

    async def go() -> None:
        test = Steps(together)
        test.behaviours = {1: makes("notes/chair.txt"), 2: makes("notes/lamp.txt")}
        answers: list[dict[str, Any]] = []

        async def proposes(lane: ProjectGateway) -> AsyncIterator[Any]:
            assert lane.turn is not None
            answers.append(lane.turn.propose_todo(TWO))
            with pytest.raises(ValueError, match="parallel tasks"):
                await write(lane, "notes/chair.txt", "mine\n")
            yield Done(ok=True)

        lead = Lead(test.gateway, [proposes])
        try:
            outcome = await run_turn(
                test.controller,
                lead,
                Prompt(text="Furnish the study", project_dir=str(together)),
                test.events.append,
                TurnTiming(),
                request="Furnish the study",
                steps=test.runner.run,
            )
            assert outcome == "done"
            (answer,) = answers
            assert answer["status"] == "running_steps" and answer["steps"] == [1, 2]
            assert answer["next"] == STEPS_RUNNING
            assert len(lead.prompts) == 1
            assert {t.state for t in test.crew.tasks.values()} == {"in_review"}
        finally:
            await test.close()

    run(go)


def test_with_tasks_off_the_lead_makes_every_step_itself(together: Path) -> None:
    async def go() -> None:
        test = Steps(together, limit=1)

        async def does_it(lane: ProjectGateway) -> AsyncIterator[Any]:
            await write(lane, "notes/chair.txt", "chair\n")
            yield Done(ok=True)

        lead = Lead(test.gateway, [does_it])
        todo = done_when.proposal(TWO, "Furnish the study")
        timing = TurnTiming()
        try:
            await run_turn(
                test.controller,
                lead,
                Prompt(text="Go", project_dir=str(together)),
                test.events.append,
                timing,
                request=todo.request,
                todo=todo,
                steps=test.runner.run,
            )
            (asked,) = lead.prompts
            assert asked.text == done_when.go_prompt(todo)
            assert any("parallel = 1" in n["text"] for n in test.of("notice"))
            assert test.crew.tasks == {}
            assert timing.trace.attributes[metrics.STEPS_MODE] == "one_by_one"
        finally:
            await test.close()

    run(go)


def test_one_by_one_is_measured_too(project: Path) -> None:
    async def go() -> None:
        test = Steps(project)

        async def does_it(lane: ProjectGateway) -> AsyncIterator[Any]:
            yield Done(ok=True)

        lead = Lead(test.gateway, [does_it])
        todo = done_when.proposal(TWO)
        timing = TurnTiming()
        try:
            await run_turn(
                test.controller,
                lead,
                Prompt(text="Go", project_dir=str(project)),
                test.events.append,
                timing,
                todo=todo,
                steps=test.runner.run,
            )
            assert test.crew.tasks == {}
            assert timing.trace.attributes[metrics.STEPS_MODE] == "one_by_one"
        finally:
            await test.close()

    run(go)


def test_stopping_the_request_stops_every_step(together: Path) -> None:
    async def go() -> None:
        test = Steps(together)
        gate = asyncio.Event()

        def waits(_lane: ProjectGateway) -> AsyncIterator[Any]:
            async def step() -> AsyncIterator[Any]:
                await gate.wait()
                yield Done(ok=True)

            return step()

        test.behaviours = {1: waits, 2: waits}
        todo = done_when.proposal(TWO)
        job = asyncio.create_task(test.runner.run(todo, [0, 1]))
        while len(test.crew.working()) < 2:
            await asyncio.sleep(0.01)
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        try:
            assert {t.state for t in test.crew.tasks.values()} == {"stopped"}
        finally:
            await test.close()

    run(go)


def test_the_tool_passes_the_steps_order_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[dict[str, Any]] = []

    async def fake(_socket: Path, method: str, params: dict[str, Any]) -> Any:
        asked.append(params)
        return {"status": "running_steps", "todo_id": "t1", "next": STEPS_RUNNING}

    monkeypatch.setattr("servers.joinery_registry.proxy.rpc", fake)

    async def go() -> None:
        async with Client(make_server(tmp_path / "gateway.sock")) as client:
            result = await client.call_tool(
                "propose_todo", {"steps": ["a", "b"], "after": [[], []]}
            )
        (text,) = result.content
        assert isinstance(text, TextContent)
        assert json.loads(text.text)["status"] == "running_steps"
        assert asked[0]["after"] == [[], []]

    run(go)
