"""The checks in the chat host: fixes as tasks, the role agents, the tools, periodic runs."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from bridge.check_runner import Checks, for_agent
from bridge.protocol import Done, MessageDelta
from joinery import access, checks_core, perf, perf_static
from joinery.checks_core import Tally
from joinery.roles import role_violation, spec

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeTask:
    def __init__(self, title: str, request: str) -> None:
        self.id = title
        self.title = title
        self.request = request
        self.state = "working"
        self.message = ""


class FakeCrew:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.tasks: list[FakeTask] = []
        self.most = 0

    def working(self) -> list[FakeTask]:
        return [t for t in self.tasks if t.state == "working"]

    def _add(self, title: str, request: str) -> FakeTask:
        task = FakeTask(title, request)
        self.tasks.append(task)
        self.most = max(self.most, len(self.working()))
        asyncio.get_running_loop().call_later(
            0.05, lambda: setattr(task, "state", "in_review")
        )
        return task

    async def start_scripted(
        self, title: str, request: str, _calls: Any, **_kw: Any
    ) -> FakeTask:
        return self._add(title, request)

    async def start(self, title: str, request: str, **_kw: Any) -> FakeTask:
        return self._add(title, request)

    async def stop(self, task_id: str) -> None:
        for task in self.tasks:
            if task.id == task_id:
                task.state = "stopped"


def _perf_report(project: Path) -> checks_core.Report:
    tally = Tally(perf.AREAS)
    files = perf_static.all_files(project)
    perf_static.scenes(tally, project, files, 4)
    perf_static.scripts(tally, project, files, {"res://hot.gd": 9.0}, 16.7, 2.0)
    return tally.report("perf", project, [])


class FakeAgent:
    """A role agent that answers with fixed words."""

    def __init__(self, words: str) -> None:
        self.words = words
        self.prompts: list[str] = []

    def bind_gateway(self, path: Any) -> None:
        self.path = path

    async def connect(self, project: Any, profile_id: str = "") -> None:
        return None

    async def send(self, prompt: Any) -> AsyncIterator[Any]:
        self.prompts.append(prompt.text)
        yield MessageDelta(text=self.words, new_message=True)
        yield Done(ok=True)

    async def close(self) -> None:
        return None


class FakeEndpoint:
    path = "socket"
    checks = None

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture
def game(tmp_path: Path) -> Path:
    project = tmp_path / "perf"
    shutil.copytree(FIXTURES / "perf_game", project)
    return project


def _checks(
    game: Path, crew: FakeCrew, events: list[dict[str, Any]], words: str = ""
) -> tuple[Checks, list[FakeAgent], list[str]]:
    agents: list[FakeAgent] = []
    roles: list[str] = []
    audits: list[str] = []

    async def audit(kind: str, _params: dict[str, Any]) -> checks_core.Report:
        audits.append(kind)
        return _perf_report(game)

    def bridge(_agent: str, role: str) -> FakeAgent:
        roles.append(role)
        agents.append(FakeAgent(words))
        return agents[-1]

    checks = Checks(
        crew,  # type: ignore[arg-type]
        game,
        events.append,
        lambda: None,  # type: ignore[arg-type, return-value]
        agent="codex",
        bridge=bridge,
        endpoint=lambda _role: FakeEndpoint(),
        audit=audit,
    )
    return checks, agents, roles


def test_fix_all_runs_typed_fixes_and_agent_words_as_tasks(
    game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bridge.check_runner.POLL", 0.01)
    crew = FakeCrew(limit=3)
    events: list[dict[str, Any]] = []
    checks, agents, roles = _checks(game, crew, events, "In hot.gd, cache the sum.")

    async def go() -> None:
        assert checks.handle({"kind": "check_fix", "check": "perf", "areas": []})
        job = checks.runs["perf"].job
        assert job is not None
        await job

    asyncio.run(go())
    states = [e["state"] for e in events]
    assert states[0] == "auditing" and states[-1] == "done"
    assert all(e["kind"] == "perf" for e in events)
    titles = {t.title: t for t in crew.tasks}
    scripted = titles["Fix rendering in 1 file"]
    assert scripted.request.endswith("the app that owns the file.")
    hot = titles["Make hot.gd cheaper per frame"]
    assert hot.request.startswith("In hot.gd, cache the sum.")
    assert "performance check" in hot.request
    assert roles == ["performance", "performance"]  # the MultiMesh change asked too
    assert "Findings:" in agents[0].prompts[0]
    assert crew.most <= 2
    rows = events[-1]["tasks"]
    assert {r["kind"] for r in rows} == {"scripted", "agent"}


def test_an_agent_that_finds_nothing_leaves_the_checks_own_words(
    game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bridge.check_runner.POLL", 0.01)
    crew = FakeCrew(limit=2)
    checks, _agents, _roles = _checks(game, crew, [], "NONE.")

    async def go() -> None:
        checks.runs["perf"].start(["scripts"], [], {})
        with pytest.raises(ValueError, match="already"):
            checks.runs["perf"].start([], [], {})
        job = checks.runs["perf"].job
        assert job is not None
        await job

    asyncio.run(go())
    (task,) = crew.tasks
    assert task.request.startswith(
        "Make hot.gd cheaper per frame, from Joinery's performance check"
    )


def test_a_fresh_saved_report_is_fixed_without_measuring_again(
    game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bridge.check_runner.POLL", 0.01)
    checks_core.save(game, _perf_report(game))
    calls: list[str] = []

    async def audit(kind: str, _params: dict[str, Any]) -> checks_core.Report:
        calls.append(kind)
        return _perf_report(game)

    checks = Checks(FakeCrew(2), game, lambda _e: None, lambda: None, audit=audit)  # type: ignore[arg-type]

    async def go() -> None:
        checks.runs["perf"].start(["lighting"], [], {})
        job = checks.runs["perf"].job
        assert job is not None
        await job

    asyncio.run(go())
    assert calls == []


def test_the_tools_and_periodic_checks(game: Path) -> None:
    (game / "joinery.toml").write_text("[agents]\nperf_check_every = 2\n")
    events: list[dict[str, Any]] = []
    checks, _agents, _roles = _checks(game, FakeCrew(2), events)

    async def go() -> None:
        data = await checks.tool("perf", {"target": "web"})
        assert data["kind"] == "perf" and "runs" not in data["facts"]
        with pytest.raises(ValueError, match="target is one of"):
            await checks.tool("perf", {"target": "console"})
        checks.after_decision({"accepted": True})
        checks.after_decision({"accepted": False})
        checks.after_decision({"accepted": True})
        await asyncio.sleep(0.05)
        await checks.close()

    asyncio.run(go())
    assert [e["state"] for e in events] == ["checked"]
    assert events[0]["kind"] == "perf" and events[0]["audit"]["findings"]


def test_for_agent_trims_the_report(game: Path) -> None:
    report = _perf_report(game)
    report.facts = {
        "runs": {"headless": "x"},
        "measured": {"hot": [1], "spikes": [2], "nodes": 3},
    }
    data = for_agent(report, limit=1)
    assert data["facts"] == {"measured": {"nodes": 3}}
    assert len(data["findings"]) == 1 and "note" in data


def test_the_check_roles_only_read_and_reach_their_own_tool() -> None:
    for role, tool, other in (
        ("accessibility", "access_check", "perf_check"),
        ("performance", "perf_check", "access_check"),
    ):
        assert spec(role).read_only and spec(role).turn == "check"
        assert role_violation(role, "mcp", "joinery-registry", tool) is None
        assert role_violation(role, "mcp", "joinery-registry", other) is not None
        assert (
            role_violation(role, "mcp", "joinery-registry", "submit_for_review")
            is not None
        )
        assert role_violation(role, "bash", "", "", command="cat hot.gd") is None
        assert (
            role_violation(role, "bash", "", "", command="sed -i s/a/b/ hot.gd")
            is not None
        )
        assert role_violation(role, "other", "", "", native="Write") is not None
    # Both checks are shared reads: the lead and workers may call them, reviewers not.
    for tool in ("access_check", "perf_check"):
        assert role_violation("lead", "mcp", "joinery-registry", tool) is None
        assert role_violation("worker", "mcp", "joinery-registry", tool) is None
        assert role_violation("reviewer", "mcp", "joinery-registry", tool) is not None
    assert "playtest" in spec("performance").registry


def test_access_areas_are_labels_the_card_shows() -> None:
    assert list(access.AREAS) == [
        "contrast",
        "colour",
        "text",
        "captions",
        "controls",
        "motion",
        "pause",
        "assists",
        "menus",
        "translation",
    ]
    assert next(iter(perf.AREAS)) == "frames"


def test_the_endpoint_serves_each_check_to_the_roles_that_may_read_it() -> None:
    from types import SimpleNamespace

    from joinery.gateway_rpc import GatewayRPC, rpc

    async def close() -> None:
        return None

    gateway = SimpleNamespace(close=close, turn=None)
    asked: list[str] = []

    async def checks(kind: str, _params: dict[str, Any]) -> dict[str, Any]:
        asked.append(kind)
        return {"kind": kind}

    async def run() -> None:
        for role, method, refused in (
            ("accessibility", "access_check", ""),
            ("accessibility", "perf_check", "only reads"),
            ("performance", "perf_check", ""),
            ("worker", "access_check", ""),
            ("reviewer", "access_check", "only reads"),
        ):
            endpoint = GatewayRPC(gateway, role=role)  # type: ignore[arg-type]
            endpoint.checks = checks
            try:
                await endpoint.start()
                if refused:
                    with pytest.raises(RuntimeError, match=refused):
                        await rpc(endpoint.path, method, {})
                else:
                    assert (await rpc(endpoint.path, method, {}))["kind"] == method[:-6]
            finally:
                await endpoint.close()
        bare = GatewayRPC(gateway, role="playtester")  # type: ignore[arg-type]
        try:
            await bare.start()
            with pytest.raises(RuntimeError, match="not available"):
                await rpc(bare.path, "perf_check", {})
        finally:
            await bare.close()

    asyncio.run(run())
    assert asked == ["access", "perf", "access"]
