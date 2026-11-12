"""Project memory: JOURNEYMAN.md in every agent's prompt, stubs, /remember (ADR 0077)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bridge import remember as host_remember
from bridge.protocol import Prompt
from bridge.provider_process import project_map, turn_text
from joinery import memory
from joinery.agents import all_agents
from joinery.cli import main as cli_main
from joinery.gateway import ProjectGateway
from joinery.runner import Result
from joinery.transactions import FileTransaction
from joinery.wiring import STUB_MARK, write_memory_stubs
from tests.test_crew import CrewTest, FakeConnection


def godot(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    return tmp_path


@pytest.fixture
def hosted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project with the chat host's fakes (tests/test_crew.py)."""
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    monkeypatch.setattr("joinery.gateway.approved", lambda _project: True)
    monkeypatch.setattr("joinery.gateway.MCPConnection", FakeConnection)
    monkeypatch.setattr("joinery.submission.approved_in", lambda *_: True)
    monkeypatch.setattr("joinery.submission.check_assets", lambda *_: [])

    async def offline(*_a: object, **_k: object) -> Result:
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    return godot(tmp_path)


def test_a_template_only_file_adds_nothing_to_the_prompt(tmp_path: Path) -> None:
    project = godot(tmp_path)
    assert memory.block(project) == ""
    assert memory.ensure(project)
    assert not memory.ensure(project)  # Never overwritten.
    assert memory.block(project) == ""
    assert memory.status(project)["characters"] == 0


def test_rules_reach_every_provider_ahead_of_the_project_map(tmp_path: Path) -> None:
    project = godot(tmp_path)
    memory.ensure(project)
    text = memory.path(project).read_text()
    memory.path(project).write_text(
        text.replace(
            "## Rules\n", "## Rules\n- Signals are snake_case.\n<!-- hidden -->\n"
        )
    )
    block = memory.block(project)
    assert "Signals are snake_case." in block
    assert "hidden" not in block and "## Facts" not in block
    assert "## Remembered" not in block  # Empty sections are left out.
    prompt = Prompt(
        text="Make the door red",
        project_dir=str(project),
        rules="ROLE RULES",
        editor_context={"scene": "res://main.tscn", "scene_revision": "4"},
    )
    full = turn_text(prompt)
    assert (
        full.index("ROLE RULES") < full.index("snake_case") < full.index("Project map")
    )
    # Stable bytes: two turns share everything up to the turn itself.
    other = prompt.model_copy(
        update={"text": "Now blue", "editor_context": {"scene_revision": "5"}}
    )
    head = full[: full.index("Editor context:")]
    assert turn_text(other).startswith(head)
    assert "snake_case" not in project_map(prompt)


def test_a_long_file_is_cut_with_a_note(tmp_path: Path) -> None:
    project = godot(tmp_path)
    lines = "\n".join(f"- Rule number {i} is a long standing rule." for i in range(400))
    memory.path(project).write_text("## Rules\n" + lines + "\n")
    block = memory.block(project)
    assert len(block) < memory.LIMIT + 600
    assert "the rest was left out" in block
    assert memory.status(project)["over"] is True


def test_remembering_adds_a_line_under_its_section() -> None:
    text = memory.TEMPLATE.read_text()
    once = memory.remembered(text, "  The player is 1.8 m tall.\n")
    twice = memory.remembered(once, "Never touch res://addons")
    assert memory.entries(twice) == [
        "The player is 1.8 m tall.",
        "Never touch res://addons",
    ]
    assert twice.index("## Remembered") < twice.index("- The player")
    with pytest.raises(ValueError, match="already has"):
        memory.remembered(twice, "the player is 1.8 m tall.")
    with pytest.raises(ValueError, match="Write what"):
        memory.remembered(twice, "   ")
    with pytest.raises(ValueError, match="one short line"):
        memory.remembered(twice, "x" * (memory.ENTRY_LIMIT + 1))
    # No section yet: one is added at the end.
    plain = memory.remembered("# Notes\n\nKeep it tidy.\n", "Signals are snake_case.")
    assert plain.endswith("## Remembered\n- Signals are snake_case.\n")


def test_remembering_refuses_to_grow_past_what_journeyman_reads() -> None:
    full = "## Rules\n" + "\n".join("- " + "r" * 98 for _ in range(59)) + "\n"
    with pytest.raises(ValueError, match="Shorten it first"):
        memory.remembered(full, "One more rule that does not fit any more, sadly.")


def test_stubs_point_every_agent_at_the_file_and_never_overwrite(
    tmp_path: Path,
) -> None:
    (tmp_path / "CLAUDE.md").write_text("The user's own notes.\n")
    written = write_memory_stubs(tmp_path)
    assert sorted(written) == ["AGENTS.md", "GEMINI.md"]
    assert (tmp_path / "CLAUDE.md").read_text() == "The user's own notes.\n"
    for name in written:
        text = (tmp_path / name).read_text()
        assert text.startswith(STUB_MARK) and "JOURNEYMAN.md" in text
        # A pointer, not an import: a Joinery session already has the file.
        assert "@JOURNEYMAN" not in text
    assert write_memory_stubs(tmp_path) == []
    assert {a.memory_file for a in all_agents()} == {
        "CLAUDE.md",
        "AGENTS.md",
        "GEMINI.md",
    }


def test_the_cli_stages_a_line_and_keeps_it_for_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = godot(tmp_path)
    memory.ensure(project)
    code = cli_main(
        ["memory", "--project", str(project), "add", "Signals", "are", "snake_case."]
    )
    assert code == 0
    assert "waiting for your review" in capsys.readouterr().out
    assert cli_main(["memory", "--project", str(project), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["remembered"] == []
    tx = next(
        FileTransaction.load(project, p.parent.name)
        for p in (project / ".joinery/transactions").glob("*/journal.json")
    )
    assert tx.state == "review" and tx.held
    assert memory.entries(memory.path(project).read_text()) == []
    assert "Signals are snake_case." in (tx.stage / memory.FILE).read_text()
    # The next chat host keeps it waiting instead of discarding it.
    gateway = ProjectGateway(project)
    asyncio.run(gateway.close())
    assert FileTransaction.load(project, tx.id).state == "review"
    tx.accept()
    assert "- Signals are snake_case." in memory.path(project).read_text()


def test_remember_from_the_sidebar_is_a_reviewed_change(
    hosted: Path,
) -> None:
    memory.ensure(hosted)
    test = CrewTest(hosted)

    async def go() -> None:
        crew = test.crew()
        with pytest.raises(ValueError, match="Write what"):
            await host_remember.start(
                crew, hosted, {"kind": "remember", "text": ""}, test.prompt()
            )
        task = await host_remember.start(
            crew,
            hosted,
            {"kind": "remember", "text": "The player is 1.8 m tall."},
            test.prompt(),
        )
        assert task.job is not None
        await task.job
        assert task.state == "in_review", task.message
        assert task.title == "Remember: The player is 1.8 m tall."
        assert memory.entries(memory.path(hosted).read_text()) == []
        await crew.decide(task.transaction_id, True)
        assert memory.entries(memory.path(hosted).read_text()) == [
            "The player is 1.8 m tall."
        ]
        await crew.close()
        await test.gateway.close()

    asyncio.run(go())
