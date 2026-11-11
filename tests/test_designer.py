"""The design check in the chat host: the designer agent's pass with a fake provider,
its endpoint's evidence check, and the check after every N accepted changes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridge import designer as designer_mod
from bridge.designer import BUSY, Designer, merged, told
from bridge.instructions import DESIGN_PROMPT
from bridge.protocol import Done, MessageDelta, Prompt
from joinery import design
from joinery.gateway_rpc import GatewayRPC, rpc
from tests.test_design import BRIEF, LEVEL, PROJECT_GODOT, write

GOAL_HIDDEN = {
    "severity": "medium",
    "title": "The flag is never pointed out",
    "player": "The player doesn't know the flag ends the level.",
    "why": "The brief's win is touching the flag; nothing on screen says so.",
    "basis": "brief",
    "evidence": [
        {"path": "design/brief.md", "line": 22, "quote": "Win: touch the flag"}
    ],
    "fix": "Show 'Reach the flag' for a few seconds when the level starts.",
}
MADE_UP = {
    **GOAL_HIDDEN,
    "title": "Made up",
    "evidence": [{"path": "nope.gd", "line": 1}],
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return write(
        tmp_path / "game",
        {
            "project.godot": PROJECT_GODOT,
            "design/brief.md": BRIEF,
            "game/level.gd": LEVEL,
        },
    )


class FakeEndpoint:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = Path("gateway.sock")
        self.reports: list[dict[str, Any]] = []
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    def report(self, findings: list[dict[str, Any]]) -> None:
        kept, dropped = design.agent_findings(self.root, {"findings": findings})
        self.reports.append(
            {"findings": [f.model_dump() for f in kept], "dropped": dropped}
        )


class FakeAgent:
    """A provider that reads, then reports through its endpoint, then ends."""

    def __init__(
        self, endpoint: FakeEndpoint, findings: list[dict[str, Any]], hang: bool = False
    ) -> None:
        self.endpoint = endpoint
        self.findings = findings
        self.hang = hang
        self.prompts: list[Prompt] = []
        self.closed = False

    def bind_gateway(self, path: Any) -> None:
        assert path == self.endpoint.path

    async def connect(self, _project: Any, _profile: str = "") -> None:
        return None

    async def send(self, prompt: Prompt) -> AsyncIterator[Any]:
        self.prompts.append(prompt)
        yield MessageDelta(text="Reading the brief.", new_message=True)
        if self.hang:
            await asyncio.Event().wait()
        self.endpoint.report(self.findings)
        yield Done(ok=True)

    async def cancel(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


def make(
    project: Path, findings: list[dict[str, Any]], hang: bool = False
) -> tuple[Designer, list[dict[str, Any]], FakeEndpoint, list[FakeAgent]]:
    events: list[dict[str, Any]] = []
    endpoint = FakeEndpoint(project)
    agents: list[FakeAgent] = []

    def bridge(agent: str) -> FakeAgent:
        assert agent == "claude"
        child = FakeAgent(endpoint, findings, hang)
        agents.append(child)
        return child

    designer = Designer(
        project,
        lambda event: events.append(dict(event)),
        agent="claude",
        profile="",
        bridge=bridge,
        endpoint=lambda: endpoint,
    )
    return designer, events, endpoint, agents


def test_the_agent_pass_adds_findings_that_hold_and_drops_the_rest(
    project: Path,
) -> None:
    designer, events, endpoint, agents = make(project, [GOAL_HIDDEN, MADE_UP])

    async def run() -> None:
        run = designer.start(deep=True)
        assert run.job is not None
        await run.job

    asyncio.run(run())
    states = [e["state"] for e in events]
    assert states == ["checking", "reading", "done"]
    final = events[-1]
    assert final["kind"] == "design" and final["origin"] == "user" and final["deep"]
    findings = final["report"]["findings"]
    agent = [f for f in findings if f["source"] == "agent"]
    assert [f["title"] for f in agent] == ["The flag is never pointed out"]
    assert final["dropped"] == 1
    assert any(f["source"] == "check" for f in findings), "Joinery's findings stay"
    assert final["report"]["summary"] == f"{len(findings)} design problems"
    # The agent was told what Joinery already found, and its own role closed.
    prompt = agents[0].prompts[0].text
    assert prompt.startswith(DESIGN_PROMPT.split("{found}")[0])
    assert "isn't in the input map" in prompt
    assert endpoint.started and endpoint.closed and agents[0].closed


def test_a_check_only_run_never_starts_an_agent(project: Path) -> None:
    designer, events, endpoint, agents = make(project, [GOAL_HIDDEN])

    async def run() -> None:
        run = designer.start(deep=False)
        with pytest.raises(ValueError, match=BUSY):
            designer.start()
        assert run.job is not None
        await run.job

    asyncio.run(run())
    assert [e["state"] for e in events] == ["checking", "done"]
    assert not agents and not endpoint.started
    assert all(f["source"] == "check" for f in events[-1]["report"]["findings"])


def test_an_agent_out_of_time_still_shows_the_checks(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        designer_mod, "spec", lambda _role: SimpleNamespace(seconds=0.05)
    )
    designer, events, _endpoint, agents = make(project, [], hang=True)

    async def run() -> None:
        run = designer.start()
        assert run.job is not None
        await run.job

    asyncio.run(run())
    assert events[-1]["state"] == "done"
    assert "ran out of time" in events[-1]["message"]
    assert events[-1]["report"]["findings"], "the check's own findings are still shown"
    assert agents[0].closed


def test_stop_stops_the_agent(project: Path) -> None:
    designer, events, _endpoint, agents = make(project, [], hang=True)

    async def run() -> None:
        designer.start()
        while not agents:
            await asyncio.sleep(0.01)
        await designer.stop()

    asyncio.run(run())
    assert events[-1]["state"] == "stopped"
    assert not designer.busy()


def test_check_every_n_accepted_changes(project: Path) -> None:
    designer, events, _endpoint, agents = make(project, [])
    accepted = {"kind": "file_decision", "accepted": True}

    async def run() -> None:
        designer.after_decision(accepted)
        assert not events, "off by default"
        design.set_check_every(project, 2)
        designer.after_decision(accepted)
        designer.after_decision({"accepted": False})
        assert not events, "a discarded change doesn't count"
        designer.after_decision(accepted)
        assert (
            designer.current is not None and designer.current.origin == "after_change"
        )
        assert designer.current.job is not None
        await designer.current.job
        designer.after_decision(accepted)
        assert designer.accepted == 1

    asyncio.run(run())
    assert [e["state"] for e in events] == ["checking", "done"]
    assert not events[-1]["deep"] and not agents


def test_merged_skips_what_joinery_already_found(project: Path) -> None:
    report = design.check(project)
    first = report.findings[0]
    same = {
        **GOAL_HIDDEN,
        "title": first.title,
        "evidence": [{"path": first.evidence[0].path, "line": first.evidence[0].line}],
    }
    endpoint = FakeEndpoint(project)
    endpoint.report([same, GOAL_HIDDEN, GOAL_HIDDEN])
    out, dropped = merged(report, endpoint.reports)
    assert dropped == 0
    # The repeat of Joinery's finding and the agent's own repeat are left out; a new
    # problem on the same brief line as one of Joinery's is kept.
    assert len(out.findings) == len(report.findings) + 1
    assert told(report).startswith("- ")
    assert told(design.Report(project="x", summary="")) == "- nothing"


def test_the_endpoint_records_only_findings_with_evidence(project: Path) -> None:
    gateway = SimpleNamespace(project=project.resolve())

    async def run() -> tuple[GatewayRPC, dict[str, Any]]:
        endpoint = GatewayRPC(gateway, role="designer")  # type: ignore[arg-type]
        try:
            await endpoint.start()
            answer = await rpc(
                endpoint.path, "report_design", {"findings": [GOAL_HIDDEN, MADE_UP]}
            )
            with pytest.raises(RuntimeError, match="findings is a list"):
                await rpc(endpoint.path, "report_design", {"findings": "x"})
        finally:
            await endpoint.close()
        return endpoint, answer

    endpoint, answer = asyncio.run(run())
    assert answer["recorded"] == 1
    assert "nope.gd is not a file" in answer["dropped"][0]
    assert "report them again" in answer["next"]
    assert endpoint.reports[0]["findings"][0]["source"] == "agent"
