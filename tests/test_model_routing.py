"""Model routing: a light model for light work, risk-based reviews (ADR 0061).

Fake providers only: no inference runs here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from bridge import compaction
from bridge.protocol import Done, Message, Prompt
from joinery import metrics
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import rpc
from joinery.peer_review import PeerReview, ReviewNote, one_line
from joinery.roles import reviewer_settings
from joinery.routing import (
    Risk,
    RoutingSettings,
    change_risk,
    review_route,
    routing_settings,
    set_routing,
)
from joinery.timing import TurnTiming
from joinery.turn import TurnController
from tests.test_crew import project, wire, write
from tests.test_review_agents import agent_for, previews

__all__ = ["project"]


def run(step: Callable[[], Any]) -> Any:
    return asyncio.run(step())


def _toml(path: Path, agents: str) -> None:
    (path / "joinery.toml").write_text(
        '[tools]\nenabled = ["godot"]\n\n[agents]\n' + agents
    )


# --- Settings ---------------------------------------------------------------------------


def test_settings_default_to_todays_behaviour(tmp_path: Path) -> None:
    settings = routing_settings(tmp_path)
    assert settings.review == "always" and settings.light_model("claude") == ""
    _toml(tmp_path, 'review = "sometimes"\nlight_model = 3\n')
    assert routing_settings(tmp_path) == RoutingSettings()


def test_light_model_is_one_name_or_one_per_provider(tmp_path: Path) -> None:
    _toml(tmp_path, 'light_model = "small-1"\n')
    settings = routing_settings(tmp_path)
    assert {settings.light_model(a) for a in ("claude", "codex", "gemini")} == {
        "small-1"
    }
    _toml(
        tmp_path,
        'review = "risky"\nlight_model = { claude = "haiku", codex = "mini", x = "y" }\n',
    )
    settings = routing_settings(tmp_path)
    assert settings.review == "risky"
    assert settings.light_model("claude") == "haiku"
    assert settings.light_model("codex") == "mini"
    assert settings.light_model("gemini") == ""
    assert "x" not in settings.light_models


def test_the_cli_saves_review_and_light_model_keeping_other_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery import cli

    _toml(tmp_path, 'parallel = 2\nreviewer_model = "big"\n')
    base = ["reviewer", "--project", str(tmp_path), "--json"]
    assert cli.main([*base, "--review", "risky", "--light-model", "small"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["review"] == "risky" and data["light_model"] == {"*": "small"}
    assert cli.main([*base, "--light-model", "haiku", "--provider", "claude"]) == 0
    data = json.loads(capsys.readouterr().out)
    # One name for every provider became each provider's, then claude's changed.
    assert data["light_model"]["claude"] == "haiku"
    assert data["light_model"]["codex"] == "small"
    text = (tmp_path / "joinery.toml").read_text()
    assert "parallel = 2" in text and text.count("[agents]") == 1
    assert reviewer_settings(tmp_path).model == "big"
    assert cli.main([*base, "--light-model", ""]) == 0
    assert json.loads(capsys.readouterr().out)["light_model"] == {}
    assert cli.main([*base, "--light-model", 'a"b']) == 1
    assert "error" in json.loads(capsys.readouterr().out)
    with pytest.raises(ValueError):
        set_routing(tmp_path, review="sometimes")


# --- Risk -------------------------------------------------------------------------------


def _scene(nodes: int) -> str:
    body = '[gd_scene format=3]\n\n[node name="Root" type="Node3D"]\n'
    return body + "".join(
        f'\n[node name="N{i}" type="Node3D" parent="."]\n' for i in range(nodes - 1)
    )


@pytest.mark.parametrize(
    ("changes", "level"),
    [
        # A texture recolour within the palette, with its lock and recipe.
        (
            [
                ("art/textures/lamp.png", "modified"),
                ("art/textures/lamp.kra", "modified"),
                ("art/textures/lamp.png.import", "modified"),
                ("joinery.lock", "modified"),
                ("recipes/lamp.json", "created"),
                ("recipes/history/lamp-1.json", "created"),
            ],
            "low",
        ),
        # A loudness fix.
        ([("audio/sfx/door.wav", "modified"), ("joinery.lock", "modified")], "low"),
        ([("art/materials/wood.tres", "modified")], "low"),
        ([("scenes/small.tscn", "modified")], "low"),
        ([("scripts/player.gd", "modified")], "high"),
        ([("art/shaders/water.gdshader", "modified")], "high"),
        ([("scenes/big.tscn", "modified")], "high"),
        ([("scenes/new.tscn", "created")], "high"),
        ([("art/models/lamp.blend", "created")], "high"),
        ([("art/models/lamp.glb", "modified")], "high"),
        ([("art/bible.md", "modified")], "high"),
        ([("design/brief.md", "modified")], "high"),
        ([("project.godot", "modified")], "high"),
        ([("art/textures/old.png", "deleted")], "high"),
        ([("art/textures/new.png", "created")], "high"),
        ([("notes/lamp.md", "modified")], "high"),
        ([(f"art/t/{i}.png", "modified") for i in range(9)], "high"),
    ],
)
def test_risk_is_read_from_the_staged_change(
    tmp_path: Path, changes: list[tuple[str, str]], level: str
) -> None:
    (tmp_path / "scenes").mkdir()
    (tmp_path / "scenes/small.tscn").write_text(_scene(4))
    (tmp_path / "scenes/big.tscn").write_text(_scene(30))
    risk = change_risk(tmp_path, [{"path": p, "change": c} for p, c in changes])
    assert risk.level == level, risk.reasons
    assert risk.reasons


def test_the_route_follows_the_settings_and_the_risk() -> None:
    low, high = Risk("low", ("texture edit",)), Risk("high", ("code: a.gd",))
    light = RoutingSettings(light_models={"claude": "haiku"})
    assert review_route(RoutingSettings(), low, "claude") == "main"
    assert review_route(light, low, "claude") == "light"
    assert review_route(light, low, "codex") == "main"
    assert review_route(light, high, "claude") == "main"
    risky = RoutingSettings(review="risky", light_models={"*": "small"})
    assert review_route(risky, low, "gemini") == "skip"
    assert review_route(risky, high, "gemini") == "main"
    never = RoutingSettings(review="never")
    assert review_route(never, high, "claude") == "skip"


# --- The reviewer -----------------------------------------------------------------------


def _reports(*notes: dict[str, str]) -> Callable[[Path, Prompt], Any]:
    async def act(path: Path, _prompt: Prompt) -> str:
        await rpc(path, "report_review", {"notes": list(notes)})
        return "Reviewed."

    return act


def _recolour(project: Path) -> None:
    (project / "art").mkdir(exist_ok=True)
    (project / "art/lamp.png").write_text("old colours")


async def _submit(
    project: Path, act: Callable[[Path, Prompt], Any], path: str, text: str
) -> tuple[dict[str, Any], list[Prompt], ProjectGateway, TurnController]:
    events: list[dict[str, Any]] = []
    gateway = wire(ProjectGateway(project))
    gateway.timing = TurnTiming()
    progress: list[str] = []
    gateway.progress = progress.append
    controller = TurnController(gateway, events.append, "codex")
    prompts: list[Prompt] = []
    agent_for(gateway, act, prompts).equip(controller)
    controller.selection = {"mode": "auto", "model": "big", "effort": "high"}
    controller.begin("Make the lamp warmer")
    await write(gateway, path, text)
    result = await controller.submit("Warmer lamp")
    assert result.status == "in_review"
    (preview,) = previews(events)
    preview["progress"] = progress
    return preview, prompts, gateway, controller


def test_a_low_risk_change_is_read_by_the_light_model_first(project: Path) -> None:
    _recolour(project)
    _toml(project, 'light_model = { codex = "mini" }\n')

    async def go() -> None:
        preview, prompts, gateway, _ = await _submit(
            project,
            _reports({"severity": "info", "text": "Within the palette."}),
            "art/lamp.png",
            "warm colours",
        )
        try:
            (prompt,) = prompts
            assert prompt.selection.model == "mini" and prompt.selection.effort == ""
            reviewer = preview["reviewer"]
            assert reviewer["status"] == "reviewed" and reviewer["tier"] == "light"
            assert "escalated" not in reviewer
            assert gateway.timing is not None
            spans = gateway.timing.trace.spans
            (turn,) = [
                s for s in spans if s["attributes"][metrics.KIND] == metrics.AGENT_TURN
            ]
            assert turn["attributes"][metrics.MODEL_TIER] == "light"
            assert turn["attributes"][metrics.REQUEST_MODEL] == "Model One"
            (span,) = [
                s for s in spans if s["attributes"][metrics.KIND] == metrics.REVIEWER
            ]
            assert span["attributes"][metrics.REVIEW_ROUTE] == "light"
            assert span["attributes"][metrics.REVIEW_RISK] == "low"
            summary = metrics.summarise("x", spans)
            assert summary["models"] == [
                {"role": "reviewer", "model": "Model One", "tier": "light", "turns": 1}
            ]
            assert summary["review_routes"] == {"light": 1}
        finally:
            await gateway.close()

    run(go)


def test_a_light_pass_that_objects_or_is_unsure_goes_to_the_main_model(
    project: Path,
) -> None:
    _recolour(project)
    _toml(project, 'light_model = "mini"\n')
    calls: list[str] = []

    async def act(path: Path, prompt: Prompt) -> str:
        calls.append(prompt.selection.model)
        if prompt.selection.model == "mini":
            note = {"severity": "block", "text": "The red is off the palette."}
        else:
            note = {"severity": "info", "text": "The red is in the palette."}
        await rpc(path, "report_review", {"notes": [note]})
        return "Reviewed."

    async def go() -> None:
        preview, _prompts, gateway, controller = await _submit(
            project, act, "art/lamp.png", "warm colours"
        )
        try:
            # The light model's block never reached the author: the main model's
            # review is the one that counts.
            assert calls == ["mini", "big"]
            assert controller.review_rounds == 0
            assert preview["review_notes"] == [
                {"severity": "info", "text": "The red is in the palette."}
            ]
            assert preview["reviewer"]["tier"] == "main"
            assert preview["reviewer"]["escalated"] is True
            assert gateway.timing is not None
            tiers = [
                s["attributes"].get(metrics.MODEL_TIER)
                for s in gateway.timing.trace.spans
                if s["attributes"][metrics.KIND] == metrics.AGENT_TURN
            ]
            assert tiers == ["light", "main"]
            summary = metrics.summarise("x", gateway.timing.trace.spans)
            assert summary["escalations"] == 1
        finally:
            await gateway.close()

    run(go)

    async def mumbles(path: Path, prompt: Prompt) -> str:
        calls.append(prompt.selection.model)
        if prompt.selection.model == "mini":
            return "Looks fine to me."  # no findings: unsure
        await rpc(path, "report_review", {"notes": []})
        return "Reviewed."

    calls.clear()

    async def again() -> None:
        preview, _p, gateway, _c = await _submit(
            project, mumbles, "art/lamp.png", "warmer colours"
        )
        try:
            assert calls == ["mini", "big"]
            assert preview["reviewer"]["status"] == "reviewed"
            assert preview["reviewer"]["escalated"] is True
        finally:
            await gateway.close()

    run(again)


def test_risky_code_goes_straight_to_the_main_model(project: Path) -> None:
    _toml(project, 'light_model = "mini"\nreview = "risky"\n')
    (project / "scripts").mkdir()
    (project / "scripts/lamp.gd").write_text("extends Node\n")

    async def go() -> None:
        preview, prompts, gateway, _ = await _submit(
            project,
            _reports(),
            "scripts/lamp.gd",
            "extends Node3D\n",
        )
        try:
            (prompt,) = prompts
            assert prompt.selection.model == "big"
            assert preview["reviewer"]["tier"] == "main"
        finally:
            await gateway.close()

    run(go)


def test_risky_skips_the_reviewer_when_the_checks_cover_the_change(
    project: Path,
) -> None:
    _recolour(project)
    _toml(project, 'review = "risky"\n')

    async def never(_path: Path, _prompt: Prompt) -> str:
        pytest.fail("A covered change is not reviewed")

    async def go() -> None:
        preview, prompts, gateway, _ = await _submit(
            project, never, "art/lamp.png", "warm colours"
        )
        try:
            assert prompts == []
            assert "Reviewing the change…" not in preview["progress"]
            reviewer = preview["reviewer"]
            assert reviewer["status"] == "skipped"
            assert "texture edit" in reviewer["message"]
            assert one_line([], reviewer) == reviewer["message"]
            assert gateway.timing is not None
            (span,) = [
                s
                for s in gateway.timing.trace.spans
                if s["attributes"][metrics.KIND] == metrics.REVIEWER
            ]
            assert span["attributes"][metrics.REVIEW_ROUTE] == "skip"
        finally:
            await gateway.close()

    run(go)


def test_never_skips_every_review(project: Path) -> None:
    _toml(project, 'review = "never"\n')

    async def never(_path: Path, _prompt: Prompt) -> str:
        pytest.fail("Reviews are off")

    async def go() -> None:
        preview, prompts, gateway, _ = await _submit(
            project, never, "notes/lamp.md", "lamp"
        )
        try:
            assert prompts == []
            assert preview["reviewer"]["status"] == "skipped"
        finally:
            await gateway.close()

    run(go)


def test_a_light_review_is_unsure_when_it_warns_or_does_not_finish() -> None:
    assert not PeerReview("reviewed", [ReviewNote("info", "ok")]).unsure()
    assert PeerReview("reviewed", [ReviewNote("warn", "hm")]).unsure()
    assert PeerReview("reviewed", [ReviewNote("block", "no")]).unsure()
    assert PeerReview("timeout").unsure() and PeerReview("error").unsure()


# --- Summaries and check wording --------------------------------------------------------


class SummaryBridge:
    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies
        self.models: list[str] = []
        self.fresh = False

    async def send(self, prompt: Prompt) -> Any:
        self.models.append(prompt.selection.model)
        yield Message(text=self.replies.get(prompt.selection.model, ""))
        yield Done(ok=True)

    def editor_result(self, _id: str, _result: dict[str, Any]) -> None:
        return None

    async def cancel(self) -> None:
        return None

    async def new_session(self) -> None:
        self.fresh = True


def test_the_session_summary_uses_the_light_model_then_the_main_one(
    project: Path,
) -> None:
    from joinery.session_summary import FileSummaryStore

    _toml(project, 'light_model = { codex = "mini" }\n')

    async def go(replies: dict[str, str]) -> tuple[list[str], str]:
        gateway = wire(ProjectGateway(project))
        controller = TurnController(gateway, lambda _e: None, "codex")
        bridge = SummaryBridge(replies)
        try:
            summary = await compaction.compact(
                controller, bridge, FileSummaryStore(project), project, lambda _e: None
            )
            return bridge.models, summary.text
        finally:
            await gateway.close()

    models, text = run(lambda: go({"mini": "Light summary."}))
    assert models == ["mini"] and "Light summary." in text
    models, text = run(lambda: go({"": "Main summary."}))
    assert models == ["mini", ""] and "Main summary." in text


def test_check_wording_uses_the_light_model_then_the_main_one(
    project: Path,
) -> None:
    from bridge.check_runner import Checks
    from joinery import checks_core

    _toml(project, 'light_model = "mini"\n')
    asked: list[str] = []

    class Child:
        def bind_gateway(self, _path: Any) -> None:
            return None

        async def connect(self, _project: Any, _profile: str = "") -> None:
            return None

        async def send(self, prompt: Prompt) -> Any:
            asked.append(prompt.selection.model)
            yield Message(text="NONE" if prompt.selection.model == "mini" else "Fix.")
            yield Done(ok=True)

        async def close(self) -> None:
            return None

    class Point:
        path = Path("unused")
        checks: Any = None

        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

    checks = Checks(
        crew=None,  # type: ignore[arg-type]
        project=project,
        emit=lambda _e: None,
        prompt=lambda: Prompt(text=""),
        agent="claude",
        bridge=lambda _a, _r: Child(),
        endpoint=lambda _r: Point(),
    )
    task = checks_core.CheckTask(
        key="k",
        title="t",
        area="a",
        kind="agent",
        request="Make text bigger.",
        targets=[],
        apps=[],
        steps=[],
        findings=[],
        facts=[],
    )
    assert run(lambda: checks.advise("access", task)) == "Fix."
    assert asked == ["mini", ""]
