"""The sound pass in the chat host: the sound designer's role, its tool, and the tasks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridge.host import Bridge
from bridge.protocol import Done, MessageDelta, Prompt
from bridge.sound_pass import SoundPassRun, focus_categories
from joinery import blueprints, sound_pass
from joinery.art_approval import approve
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.hook import violation
from joinery.policy import Severity
from joinery.roles import READ_ONLY_ROLES, ROLE_ENV, SOUND_AUDIT, spec

PROVIDERS = ("claude", "codex", "gemini", "opencode")


def _game(tmp_path: Path, *, runtime: bool = True, approved: bool = True) -> Path:
    project = tmp_path / "game"
    project.mkdir(parents=True)
    (project / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="S"\n'
    )
    (project / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    blueprints.apply(project, "third-person-3d", [])
    for path in sound_pass.RUNTIME:
        target = project / sound_pass.rel(path)
        if not runtime:
            # A game made before the core had its sound nodes.
            target.unlink(missing_ok=True)
        elif not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("extends Node\n")
    (project / "art").mkdir(exist_ok=True)
    (project / "art/bible.md").write_text("# Art bible (test fixture)\n")
    if approved:
        approve(project, "test", allow_open=True)
    return project


class FakeTask:
    def __init__(self, title: str) -> None:
        self.id = title
        self.title = title
        self.state = "working"
        self.message = ""


class FakeCrew:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.tasks: list[FakeTask] = []
        self.calls: dict[str, list[Any]] = {}
        self.most = 0

    def working(self) -> list[FakeTask]:
        return [t for t in self.tasks if t.state == "working"]

    async def start_scripted(
        self, title: str, _request: str, calls: Any, **kw: Any
    ) -> FakeTask:
        task = FakeTask(title)
        self.tasks.append(task)
        self.calls[title] = calls
        assert kw["facts"] and kw["targets"]
        self.most = max(self.most, len(self.working()))
        asyncio.get_running_loop().call_later(
            0.03, lambda: setattr(task, "state", "in_review")
        )
        return task

    async def stop(self, task_id: str) -> None:
        for task in self.tasks:
            if task.id == task_id:
                task.state = "stopped"


class FakeEndpoint:
    def __init__(self) -> None:
        self.path = Path("gateway.sock")
        self.sound_audit: Any = None
        self.closed = False

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class FakeAgent:
    """A provider that calls sound_audit through its endpoint, then writes ``text``."""

    def __init__(self, endpoint: FakeEndpoint, text: str, fail: bool = False) -> None:
        self.endpoint = endpoint
        self.text = text
        self.fail = fail
        self.prompts: list[Prompt] = []
        self.audit: dict[str, Any] = {}

    def bind_gateway(self, path: Any) -> None:
        assert path == self.endpoint.path

    async def connect(self, _project: Any, _profile: str = "") -> None:
        if self.fail:
            raise RuntimeError("not signed in")

    async def send(self, prompt: Prompt) -> AsyncIterator[Any]:
        self.prompts.append(prompt)
        yield MessageDelta(text="Listening to the level.", new_message=True)
        self.audit = await self.endpoint.sound_audit({})
        yield MessageDelta(text=self.text, new_message=True)
        yield Done(ok=True)

    async def cancel(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _run(
    project: Path, crew: FakeCrew, agent: FakeAgent | None, events: list[dict[str, Any]]
) -> SoundPassRun:
    endpoint = agent.endpoint if agent else None
    return SoundPassRun(
        crew,  # type: ignore[arg-type]
        project,
        events.append,
        lambda: Prompt(text="", project_dir=str(project)),
        agent="codex",
        bridge=(lambda _agent: agent) if agent else None,  # type: ignore[arg-type,return-value]
        endpoint=(lambda: endpoint) if endpoint else None,  # type: ignore[arg-type,return-value]
    )


def test_the_pass_makes_its_sounds_as_parallel_tasks_and_the_designer_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bridge.sound_pass.POLL", 0.01)
    project = _game(tmp_path)
    crew = FakeCrew(limit=3)
    endpoint = FakeEndpoint()
    agent = FakeAgent(
        endpoint, "The level was silent. Footsteps on grass and stone are coming."
    )
    events: list[dict[str, Any]] = []

    async def go() -> None:
        run = _run(project, crew, agent, events)
        run.start("", "")
        with pytest.raises(ValueError, match="already running"):
            run.start()
        assert run.job is not None
        await run.job

    asyncio.run(go())
    assert crew.most == 2  # the chat's own request keeps one of the three
    assert [t.title for t in crew.tasks] == [
        "Add footsteps, jump and land to the player",
        "Add wind ambience and floor surfaces to level.tscn",
        "Add pickup sound to coin",
        "Add lever and plate sounds to 2 props",
        "Add UI click and hover sounds",
    ]
    calls = crew.calls["Add pickup sound to coin"]
    assert [(c.server, c.tool) for c in calls] == [
        ("joinery-sound", "make_sound"),
        ("godot", "write_file"),
    ]
    states = [e["state"] for e in events]
    assert states[0] == "auditing" and states[-1] == "done"
    last = events[-1]
    assert last["kind"] == "sound_pass"
    assert last["audit"]["headline"] == "11 sounds missing in level.tscn"
    assert all(row["state"] == "in_review" for row in last["tasks"])
    assert last["tasks"][0]["categories"] == ["footsteps", "jump_land"]
    assert last["summary"].startswith("The level was silent.")
    # The designer read the audit through its own read-only tool.
    assert agent.audit["headline"] == "11 sounds missing in level.tscn"
    assert "note_for_you" in agent.audit
    assert "Add pickup sound to coin: pickup_coin" in agent.prompts[0].text


def test_the_sounds_are_made_even_when_the_designer_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bridge.sound_pass.POLL", 0.01)
    project = _game(tmp_path)
    crew = FakeCrew(limit=4)
    agent = FakeAgent(FakeEndpoint(), "", fail=True)
    events: list[dict[str, Any]] = []

    async def go() -> None:
        run = _run(project, crew, agent, events)
        run.start("footsteps only")
        assert run.job is not None
        await run.job

    asyncio.run(go())
    assert events[-1]["state"] == "done"
    assert [t.title for t in crew.tasks] == [
        "Add footsteps to the player",
        "Add floor surfaces to level.tscn",
    ]
    assert "couldn't write its report" in events[-1]["message"]


def test_the_pass_waits_for_approval_and_for_the_core(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []

    async def go(project: Path) -> None:
        run = _run(project, FakeCrew(2), None, events)
        run.start()
        assert run.job is not None
        await run.job

    asyncio.run(go(_game(tmp_path / "a", approved=False)))
    assert events[-1]["state"] == "failed" and events[-1]["art_gate"]
    assert events[-1]["audit"]["headline"].endswith("missing in level.tscn")
    events.clear()
    asyncio.run(go(_game(tmp_path / "b", runtime=False)))
    assert events[-1]["state"] == "failed"
    assert "no sound nodes yet" in events[-1]["message"]


def test_focus_words_pick_categories() -> None:
    assert focus_categories("") == []
    assert focus_categories("footsteps only") == ["footsteps"]
    assert focus_categories("the ambience and the coins") == ["pickups", "ambience"]
    assert focus_categories("doors, levers") == ["switches"]


# --- The role, the same for every provider ------------------------------------------


def _payload(provider: str, tool: str, cwd: Path) -> dict[str, Any]:
    if provider == "claude":
        return {
            "tool_name": f"mcp__joinery-registry__{tool}",
            "tool_input": {},
            "cwd": str(cwd),
        }
    if provider == "codex":
        return {
            "tool_name": f"mcp__joinery_registry__{tool}",
            "tool_input": {},
            "cwd": str(cwd),
        }
    if provider == "gemini":
        return {
            "tool_name": f"mcp_joinery-registry_{tool}",
            "tool_input": {},
            "cwd": str(cwd),
        }
    return {
        "tool": tool,
        "args": {},
        "server": "joinery-registry",
        "directory": str(cwd),
    }


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    "role,tool,allowed",
    [
        ("sound-designer", SOUND_AUDIT, True),
        ("sound-designer", "view_editor", True),
        ("sound-designer", "inspect_editor", True),
        ("sound-designer", "submit_for_review", False),
        ("sound-designer", "edit_scene", False),
        ("sound-designer", "start_task", False),
        ("sound-designer", "playtest", False),
        ("sound-designer", "report_review", False),
        ("lead", SOUND_AUDIT, True),
        ("worker", SOUND_AUDIT, True),
        ("reviewer", SOUND_AUDIT, False),
    ],
)
def test_the_sound_designer_only_listens_whichever_provider_runs_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    role: str,
    tool: str,
    allowed: bool,
) -> None:
    for name in ("CLAUDE_PROJECT_DIR", "CODEX_PROJECT_DIR", "JOINERY_INSPECTION_ONLY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setenv(ROLE_ENV, role)
    verdict = violation(_payload(provider, tool, tmp_path))
    assert (verdict.severity if verdict else None) == (
        None if allowed else Severity.DENY
    )


def test_the_sound_designer_role_is_read_only_with_its_own_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert "sound-designer" in READ_ONLY_ROLES
    assert spec("sound-designer").turn == "sound" and spec("sound-designer").read_only
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setenv(ROLE_ENV, "sound-designer")
    for command in ("cat game/level.tscn", "rm -rf game", "echo x > a.txt"):
        verdict = violation(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "cwd": str(tmp_path),
            }
        )
        assert (verdict is None) == command.startswith("cat"), command


@pytest.mark.parametrize("agent", PROVIDERS)
def test_every_provider_runs_the_sound_designer_read_only(agent: str) -> None:
    designer = Bridge(agent, role="sound-designer")
    session = designer._adapter.session  # type: ignore[attr-defined]
    assert designer.role == "sound-designer"
    assert session.role == "sound-designer" and session.inspection_only


def test_the_endpoint_serves_sound_audit_and_refuses_changes() -> None:
    async def no_calls(*_: Any) -> None:
        pytest.fail("A refused request reached the gateway")

    gateway = SimpleNamespace(call=no_calls, transaction=None)

    async def run() -> None:
        endpoint = GatewayRPC(gateway, role="sound-designer")  # type: ignore[arg-type]

        async def audit(params: dict[str, Any]) -> dict[str, Any]:
            return {"headline": "2 sounds missing in level.tscn", **params}

        try:
            await endpoint.start()
            with pytest.raises(RuntimeError, match="not available"):
                await rpc(endpoint.path, "sound_audit", {})
            endpoint.sound_audit = audit
            got = await rpc(endpoint.path, "sound_audit", {"scene": "res://l.tscn"})
            assert got["headline"].startswith("2 sounds")
            with pytest.raises(RuntimeError, match="cannot submit"):
                await rpc(endpoint.path, "submit", {"summary": "x"})
        finally:
            await endpoint.close()

    asyncio.run(run())
