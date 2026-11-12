"""What each change cost, recorded with fake agents: no inference runs here."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from bridge.protocol import Done, Execution, Message, Prompt, Usage
from bridge.turn_runner import run_turn
from joinery import metrics
from joinery.bootstrap import transactions
from joinery.cli import main
from joinery.gateway import ProjectGateway
from joinery.peer_review import PeerReview, ReviewNote, ReviewRequest
from joinery.timing import TurnTiming
from joinery.turn import TurnController
from tests.test_crew import FakeConnection, project, wire, write
from tests.test_review_agents import Author, agent_for

__all__ = ["project"]

BLOCK = ReviewNote(
    "block", "The lamp is 3 m tall; the bible says 1.5 m.", "notes/lamp.md"
)


def kinds(spans: list[dict[str, Any]]) -> list[str]:
    return [s["attributes"][metrics.KIND] for s in spans]


def only(spans: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [s for s in spans if s["attributes"][metrics.KIND] == kind]


# --- Spans --------------------------------------------------------------------------


def test_a_span_times_its_block_and_marks_a_failure(tmp_path: Path) -> None:
    trace = metrics.Trace()
    with trace.span(metrics.CHECKS, "checks", {"a": 1}) as attrs:
        attrs["b"] = "two"
    with pytest.raises(RuntimeError), trace.span(metrics.TOOL, "execute_tool x"):
        raise RuntimeError("boom")
    ok, failed = trace.spans
    assert ok["attributes"] == {metrics.KIND: metrics.CHECKS, "a": 1, "b": "two"}
    assert ok["status"] == {"code": "OK"} and ok["parent_span_id"] == trace.root_id
    assert ok["end_time_unix_nano"] >= ok["start_time_unix_nano"]
    assert failed["status"] == {"code": "ERROR"}
    assert failed["attributes"][metrics.ERROR_TYPE] == "RuntimeError"
    # A span that ends after the save is added to the same file.
    path = trace.save(tmp_path)
    assert path == tmp_path / ".joinery/metrics" / (trace.trace_id + ".jsonl")
    trace.add(metrics.REVIEW_RENDER, "review_media", 1, 2)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert kinds(lines) == [
        metrics.CHANGE,
        metrics.CHECKS,
        metrics.TOOL,
        metrics.REVIEW_RENDER,
    ]
    assert {line["trace_id"] for line in lines} == {trace.trace_id}


def test_only_plain_values_are_kept_and_nothing_leaves_the_project(
    tmp_path: Path,
) -> None:
    trace = metrics.Trace()
    trace.add("x", "x", 0, 1, {"keep": 3, "drop": {"nested": 1}, "empty": ""})
    assert trace.spans[0]["attributes"] == {metrics.KIND: "x", "keep": 3}
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    (project / ".joinery").mkdir(parents=True)
    (project / ".joinery/metrics").symlink_to(outside)
    with pytest.raises(ValueError, match="inside this project"):
        trace.save(project)
    assert not list(outside.iterdir())
    trace.attributes[metrics.TRANSACTION] = "../escape"
    with pytest.raises(ValueError):
        trace.save(tmp_path)


def test_old_changes_are_pruned(tmp_path: Path) -> None:
    for _ in range(5):
        metrics.Trace().save(tmp_path)
    metrics.prune(tmp_path, keep=2)
    assert len(list((tmp_path / ".joinery/metrics").glob("*.jsonl"))) == 2


def test_turn_tokens_come_from_session_totals() -> None:
    assert metrics.usage_delta((100, 10, 50), (180, 30, 90)) == (80, 20, 40)
    # A new provider session starts its totals again: all of them are this turn's.
    assert metrics.usage_delta((100, 10, 50), (40, 5, 0)) == (40, 5, 0)
    assert metrics.token_attributes(None) == {metrics.USAGE_REPORTED: False}
    assert metrics.token_attributes((1, 2, 3)) == {
        metrics.USAGE_REPORTED: True,
        "gen_ai.usage.input_tokens": 1,
        "gen_ai.usage.output_tokens": 2,
        "gen_ai.usage.cache_read.input_tokens": 3,
    }


def test_a_worker_may_split_its_start_from_its_work() -> None:
    assert metrics.worker_split(None) == {}
    assert metrics.worker_split(
        {"joinery/worker_start_ms": 2400.4, "joinery/work_ms": 3800, "other": 1}
    ) == {metrics.WORKER_START_MS: 2400, metrics.WORKER_WORK_MS: 3800}
    assert metrics.worker_split({"joinery/worker_start_ms": -1}) == {}
    assert metrics.worker_split({"joinery/work_ms": True}) == {}


# --- A change, end to end -----------------------------------------------------------


class TimedConnection(FakeConnection):
    """A worker that reports how long it took to start and to work."""

    async def call(
        self, tool: str, arguments: dict[str, Any], timeout: float = 0
    ) -> CallToolResult:
        await super().call(tool, arguments, timeout)
        return CallToolResult(
            content=[TextContent(type="text", text="ok")],
            _meta={"joinery/worker_start_ms": 2400, "joinery/work_ms": 3800},
        )


def test_a_change_records_its_turns_tools_checks_reviewer_and_send_back(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two agent turns (the second fixes what the reviewer sent back), each with the
    tokens its provider reported; the file is the transaction's, and the user's
    decision joins it."""
    monkeypatch.setattr("joinery.gateway.MCPConnection", TimedConnection)
    seen: list[ReviewRequest] = []
    reviews = [PeerReview("reviewed", [BLOCK]), PeerReview("reviewed", [])]

    async def reviewer(request: ReviewRequest) -> PeerReview:
        seen.append(request)
        return reviews.pop(0)

    async def first(lane: ProjectGateway) -> AsyncIterator[Any]:
        yield Execution(model="m1", name="Model One", effort="", mode="auto")
        await write(lane, "notes/lamp.md", "height 3m\n")
        assert lane.turn is not None
        assert (await lane.turn.submit("A lamp")).status == "failed"
        yield Usage(model="m1", input_tokens=1000, output_tokens=100, cached_tokens=600)
        yield Message(text="Sent back.")
        yield Done(ok=True)

    async def fixes(lane: ProjectGateway) -> AsyncIterator[Any]:
        await write(lane, "notes/lamp.md", "height 1.5m\n")
        assert lane.turn is not None
        assert (await lane.turn.submit("A lamp")).status == "in_review"
        # Session totals: this prompt used the difference.
        yield Usage(
            model="m1", input_tokens=1800, output_tokens=250, cached_tokens=1200
        )
        yield Done(ok=True)

    async def go() -> str:
        events: list[dict[str, Any]] = []
        gateway = wire(ProjectGateway(project))
        controller = TurnController(gateway, events.append, "claude")
        controller.reviewer = reviewer
        try:
            outcome = await run_turn(
                controller,
                Author(gateway, [first, fixes]),
                Prompt(text="Make a lamp", project_dir=str(project)),
                events.append,
                TurnTiming(),
                request="Make a lamp",
            )
            assert outcome == "done"
            # The chat's timing goes back to the host when the turn ends.
            assert gateway.timing is None
            identifier = controller.review_id
            await controller.decide(identifier, True)
            return identifier
        finally:
            await gateway.close()

    identifier = asyncio.run(go())
    path = project / ".joinery/metrics" / (identifier + ".jsonl")
    spans = metrics.read(path)
    (root,) = only(spans, metrics.CHANGE)
    assert root["attributes"][metrics.TRANSACTION] == identifier
    assert root["attributes"][metrics.OUTCOME] == "done"
    assert root["attributes"][metrics.AGENT] == "claude"
    assert root["attributes"][metrics.SUBMITS] == 2
    assert root["attributes"][metrics.SEND_BACKS] == 1
    assert root["attributes"][metrics.CONTINUATIONS] == 1

    turns = only(spans, metrics.AGENT_TURN)
    assert [t["attributes"][metrics.ROUND] for t in turns] == [0, 1]
    assert [t["attributes"]["gen_ai.usage.input_tokens"] for t in turns] == [1000, 800]
    assert [t["attributes"]["gen_ai.usage.output_tokens"] for t in turns] == [100, 150]
    assert [t["attributes"]["gen_ai.usage.cache_read.input_tokens"] for t in turns] == [
        600,
        600,
    ]
    lead = turns[0]["attributes"]
    assert lead["gen_ai.operation.name"] == "invoke_agent"
    assert lead["gen_ai.provider.name"] == "anthropic"
    assert lead["gen_ai.agent.name"] == "lead"
    assert lead["gen_ai.request.model"] == "m1"

    tools = only(spans, metrics.TOOL)
    assert len(tools) == 2
    assert tools[0]["attributes"]["gen_ai.tool.name"] == "joinery-sound.write"
    assert tools[0]["attributes"][metrics.WORKER_START_MS] == 2400
    assert tools[0]["attributes"][metrics.WORKER_WORK_MS] == 3800
    assert len(only(spans, metrics.CHECKS)) == 2
    reviewed = only(spans, metrics.REVIEWER)
    assert [r["attributes"][metrics.ROUND] for r in reviewed] == [1, 2]
    assert [r["attributes"]["joinery.review.status"] for r in reviewed] == [
        "reviewed",
        "reviewed",
    ]
    (back,) = only(spans, metrics.SEND_BACK)
    assert back["attributes"][metrics.SENT_BACK_BY] == "reviewer"
    (decided,) = only(spans, metrics.DECIDED)
    assert decided["attributes"][metrics.DECISION] == "accepted"
    # No prompt or reply text is kept.
    text = path.read_text()
    assert "lamp" not in text.replace("notes/lamp", "") and "Sent back" not in text

    (summary,) = metrics.changes(project)
    assert summary["id"] == identifier and summary["decision"] == "accepted"
    assert summary["tokens"] == {"input": 1800, "output": 250, "cached": 1200}
    assert summary["rounds"] == {
        "agent_turns": 2,
        "submits": 2,
        "send_backs": 1,
        "continuations": 1,
    }
    assert {"agent_turn", "tool", "checks", "reviewer", "send_back"} <= set(
        summary["by_kind_ms"]
    )
    # The change's row in `joinery transactions list` carries its cost.
    (entry,) = [e for e in transactions(project, "list") if e["id"] == identifier]
    assert entry["cost"]["tokens"] == 2050 and entry["cost"]["rounds"] == 2


def test_an_agent_that_reports_no_usage_has_time_but_no_tokens(project: Path) -> None:
    async def asks(_lane: ProjectGateway) -> AsyncIterator[Any]:
        yield Message(text="The door is 2 m tall.")
        yield Done(ok=True)

    async def go() -> None:
        gateway = wire(ProjectGateway(project))
        controller = TurnController(gateway, lambda _e: None, "opencode")
        try:
            await run_turn(
                controller,
                Author(gateway, [asks]),
                Prompt(text="How tall is the door?", project_dir=str(project)),
                lambda _e: None,
                TurnTiming(),
            )
        finally:
            await gateway.close()

    asyncio.run(go())
    (summary,) = metrics.changes(project)
    assert summary["tokens"] is None and summary["transaction_id"] == ""
    assert summary["rounds"]["agent_turns"] == 1
    (turn,) = only(
        metrics.read(next((project / ".joinery/metrics").iterdir())), "agent_turn"
    )
    assert "gen_ai.provider.name" not in turn["attributes"]
    assert "not reported" in metrics.render([summary])


def test_a_stopped_turn_is_still_recorded(project: Path) -> None:
    started = asyncio.Event()

    async def hangs(_lane: ProjectGateway) -> AsyncIterator[Any]:
        started.set()
        await asyncio.Event().wait()
        yield Done(ok=True)

    async def go() -> None:
        gateway = wire(ProjectGateway(project))
        controller = TurnController(gateway, lambda _e: None, "codex")
        try:
            job = asyncio.create_task(
                run_turn(
                    controller,
                    Author(gateway, [hangs]),
                    Prompt(text="Make a lamp", project_dir=str(project)),
                    lambda _e: None,
                    TurnTiming(),
                )
            )
            await started.wait()
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        finally:
            await gateway.close()

    asyncio.run(go())
    (summary,) = metrics.changes(project)
    assert summary["outcome"] == "stopped"


def test_the_reviewer_agents_own_tokens_are_counted(project: Path) -> None:
    """The reviewer runs in its own provider session: what it reports is its turn's."""

    class Counting:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        async def send(self, prompt: Prompt) -> AsyncIterator[Any]:
            async for event in self.inner.send(prompt):
                if isinstance(event, Done):
                    yield Usage(input_tokens=500, output_tokens=40, cached_tokens=100)
                yield event

    async def act(_path: Path, _prompt: Prompt) -> str:
        return "No problems found."

    async def go() -> None:
        gateway = wire(ProjectGateway(project))
        controller = TurnController(gateway, lambda _e: None, "codex")
        reviewer = agent_for(gateway, act, [])
        made = reviewer._bridge
        reviewer._bridge = lambda agent, role: Counting(made(agent, role))  # type: ignore[assignment]
        reviewer.equip(controller)

        async def makes(lane: ProjectGateway) -> AsyncIterator[Any]:
            await write(lane, "notes/lamp.md", "lamp")
            assert lane.turn is not None
            await lane.turn.submit("A lamp")
            yield Done(ok=True)

        try:
            await run_turn(
                controller,
                Author(gateway, [makes]),
                Prompt(text="Make a lamp", project_dir=str(project)),
                lambda _e: None,
                TurnTiming(),
            )
        finally:
            await gateway.close()

    asyncio.run(go())
    (path,) = (project / ".joinery/metrics").iterdir()
    spans = metrics.read(path)
    (review,) = [
        s
        for s in only(spans, metrics.AGENT_TURN)
        if s["attributes"]["gen_ai.agent.name"] == "reviewer"
    ]
    assert review["attributes"]["gen_ai.usage.input_tokens"] == 500
    assert review["attributes"]["gen_ai.provider.name"] == "openai"
    assert review["attributes"]["gen_ai.request.model"] == "Model One"


# --- The command --------------------------------------------------------------------


def _recorded(
    project: Path, seconds: float, tokens: tuple[int, int, int] | None
) -> None:
    trace = metrics.Trace()
    trace.clock.wall = 1_000_000_000 * len(list(project.glob(".joinery/metrics/*")))
    start = trace.clock.wall
    trace.add(
        metrics.AGENT_TURN,
        "invoke_agent lead",
        start,
        start + int(seconds * 1e9),
        metrics.token_attributes(tokens),
    )
    trace.attributes[metrics.OUTCOME] = "done"
    trace.save(project)


def test_the_command_prints_each_change_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _recorded(tmp_path, 3.0, (1200, 300, 800))
    _recorded(tmp_path, 5.0, None)
    assert main(["metrics", "--project", str(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [c["tokens"] for c in data["changes"]] == [
        {"input": 1200, "output": 300, "cached": 800},
        None,
    ]
    assert main(["metrics", "--project", str(tmp_path), "--last", "1"]) == 0
    text = capsys.readouterr().out
    assert "agent_turn 5.0s" in text and "not reported by this agent" in text
    assert "1.2k" not in text  # only the newest change
    assert main(["metrics", "--project", str(tmp_path)]) == 0
    assert "in 1.2k (cached 800), out 300" in capsys.readouterr().out
    assert main(["metrics", "--project", str(tmp_path / "none")]) == 1


def test_an_empty_project_says_nothing_was_measured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["metrics", "--project", str(tmp_path)]) == 0
    assert "No change has been measured" in capsys.readouterr().out
