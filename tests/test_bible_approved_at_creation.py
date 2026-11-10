"""A bible drafted from the user's own answers is approved when the project is made.

Later changes lapse the approval (by fingerprint), a project without such a
draft still needs the user's Approve, and no model tool can approve.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from joinery.art_approval import (
    APPROVED_AT_CREATION,
    AT_CREATION,
    approve,
    approved,
)
from joinery.catalog import load_catalog
from joinery.cli import main
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.installer import Selection
from joinery.project import create_project
from servers.joinery_registry.server import make_server

#: Every question the wizard asks, answered: nothing required is left unset.
ANSWERS = {
    "vision": "Cosy and hand-made; never grim.",
    "references": "art/reference/dusk_board.kra: the warm low light",
    "look": "low-poly, flat colours",
    "palette": "warm earth tones, one bright accent",
    "sound": "soft synths, wooden foley",
    "bans": "gore, neon",
}


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOINERY_NO_DOWNLOADS", "1")


def new(tmp_path: Path) -> Path:
    """An empty project, the way `joinery new` makes one without a blueprint."""
    project = tmp_path / "game"
    create_project(
        project, "G", Selection(["godot"], []), load_catalog(), wizard=True
    )
    return project


def apply(project: Path, answers: dict[str, str] | None) -> dict[str, Any]:
    """The wizard's game step; returns the bible step it reported."""
    if answers is not None:
        (project / ".joinery-bible-answers.json").write_text(json.dumps(answers))
    steps: list[dict[str, Any]] = []
    from joinery import game_setup

    game_setup.apply_choices(project, "top-down", report=steps.append)
    return next(step for step in steps if step["step"] == "bible")


def record(project: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (project / ".joinery/art-approval.json").read_text()
    )
    return data


def art_direction(project: Path) -> dict[str, Any]:
    async def run() -> Any:
        async with Client(make_server(project)) as client:
            return await client.call_tool("art_direction", {})

    result = asyncio.run(run())
    assert not result.is_error
    data: dict[str, Any] = json.loads(result.content[0].text)
    return data


def test_wizard_answers_make_an_approved_bible(tmp_path: Path) -> None:
    project = new(tmp_path)
    step = apply(project, ANSWERS)
    assert step["state"] == "done" and step["message"] == APPROVED_AT_CREATION
    assert approved(project)
    assert record(project)["approved_by"] == AT_CREATION
    # The art gate reads the same record: the agent sees an approved bible.
    assert art_direction(project)["approved"] is True


def test_editing_the_bible_lapses_the_approval_until_the_user_approves(
    tmp_path: Path,
) -> None:
    project = new(tmp_path)
    apply(project, ANSWERS)
    bible = project / "art/bible.md"
    bible.write_text(bible.read_text() + "\nA later change.\n")
    assert not approved(project)
    assert art_direction(project)["approved"] is False
    approve(project, "tester")
    assert approved(project)


def test_skipped_answers_still_start_approved(tmp_path: Path) -> None:
    """The user's decision: a bible with open sections counts as approved at creation."""
    project = new(tmp_path)
    step = apply(project, {"references": "Hob: chunky shapes"})
    assert step["message"] == APPROVED_AT_CREATION
    assert approved(project)
    assert record(project)["approved_by"] == AT_CREATION


def test_a_bible_drafted_without_answers_starts_approved(tmp_path: Path) -> None:
    project = new(tmp_path)
    step = apply(project, None)
    assert step["message"] == APPROVED_AT_CREATION
    assert approved(project)


def test_the_approve_button_still_needs_a_finished_bible(tmp_path: Path) -> None:
    """Only creation accepts open sections; a later approval needs them filled."""
    from joinery.art_approval import approve

    project = new(tmp_path)
    apply(project, None)
    (project / ".joinery/art-approval.json").unlink()
    with pytest.raises(ValueError, match="Finish the art direction"):
        approve(project, "user")


def test_a_project_without_a_bible_is_not_approved(tmp_path: Path) -> None:
    project = new(tmp_path)
    assert not (project / "art/bible.md").exists()
    assert not approved(project)
    assert art_direction(project)["approved"] is False


def test_joinery_bible_from_answers_approves_at_creation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = new(tmp_path)
    (project / ".joinery-bible-answers.json").write_text(json.dumps(ANSWERS))
    assert main(["bible", "--project", str(project)]) == 0
    assert APPROVED_AT_CREATION in capsys.readouterr().out
    assert record(project)["approved_by"] == AT_CREATION


def test_no_model_tool_can_approve(tmp_path: Path) -> None:
    project = new(tmp_path)
    apply(project, {"references": "Hob: chunky shapes"})
    (project / ".joinery/art-approval.json").unlink()

    async def tools() -> list[str]:
        async with Client(make_server(project)) as client:
            return [tool.name for tool in (await client.list_tools()).tools]

    assert not [name for name in asyncio.run(tools()) if "approv" in name.lower()]

    async def ask() -> None:
        server = GatewayRPC(ProjectGateway(project))
        await server.start()
        try:
            for method in ("art_approval", "approve"):
                with pytest.raises(RuntimeError, match="no approval"):
                    await rpc(server.path, method, {})
        finally:
            await server.close()
            await server.gateway.close()

    asyncio.run(ask())
    assert not approved(project)
