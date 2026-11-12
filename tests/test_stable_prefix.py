"""Every agent's prompt starts with the same bytes turn after turn (ADR 0056).

Order: Joinery's fixed instructions, the tools, the role's rules (with the art bible
and the game brief), the project map, then the turn's own parts. The same layout for
Claude Code, Codex, Gemini CLI and opencode; each role is offered only its own tools.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bridge.acp_session import ACPSession
from bridge.claude_session import DYNAMIC_SECTIONS_FLAG, cache_flags, user_input
from bridge.codex_session import registry_config
from bridge.instructions import (
    CHECK_PROMPT,
    DEBUGGER_PROMPT,
    DESIGN_PROMPT,
    PLAYTEST_PROMPT,
    REVIEWER_PROMPT,
    SOUND_DESIGNER_PROMPT,
    TASK_PROMPT,
    VARIANT_PROMPT,
    RolePrompt,
)
from bridge.protocol import Prompt
from bridge.provider_process import (
    INSTRUCTIONS,
    TURN_KEYS,
    offered_role,
    project_map,
    prompt_text,
    proxy_config,
    turn_text,
)
from joinery.roles import (
    READ_ONLY_ROLES,
    REGISTRY_TOOLS,
    ROLES,
    offered_tools,
    registry_allowed,
)
from servers.joinery_registry.proxy import make_server

#: Tools that change the project or start agents: never offered to a read-only role.
WRITERS = {
    "edit_scene",
    "submit_for_review",
    "start_task",
    "start_variants",
    "blockout",
    "grow_level",
    "inspect_in_parallel",
}

SCENE = {
    "scene_path": "res://level.tscn",
    "nodes": json.dumps([{"id": "res://level.tscn::.", "name": "Level"}]),
    "complex_models": "blender",
}


def turn(text: str, revision: int, picked: str = "Door") -> Prompt:
    return Prompt(
        text=text,
        project_dir="/tmp/game",
        targets=(f"@{picked}",),
        target_context=({"id": f"res://level.tscn::{picked}"},),
        editor_context={
            **SCENE,
            "scene_revision": str(revision),
            "selection_instances": json.dumps({picked: str(1000 + revision)}),
        },
    )


def shared(a: str, b: str) -> int:
    """Characters two texts share from the start."""
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


# --- Prefix stability -----------------------------------------------------------------


def test_two_turns_share_every_byte_up_to_the_turn() -> None:
    first = turn("Make the door red", 1, "Door")
    second = turn("Add a lamp by the window", 2, "Window")
    head = project_map(first)
    assert project_map(second) == head
    for render in (
        prompt_text,
        turn_text,
        lambda p: user_input(p).decode(),
    ):
        a, b = render(first), render(second)
        cut = a.index("This turn:\n")
        assert b.index("This turn:\n") == cut
        assert a[:cut] == b[:cut]
        assert shared(a, b) >= cut
    # Nothing per-turn reaches the project map.
    for key in TURN_KEYS:
        assert f'"{key}"' not in head
    assert "Make the door red" not in head


def test_the_project_map_is_one_spelling_whatever_the_key_order() -> None:
    a = turn("x", 1)
    b = a.model_copy(
        update={"editor_context": dict(reversed(a.editor_context.items()))}
    )
    assert project_map(a) == project_map(b)


def test_instructions_lead_and_the_turn_ends() -> None:
    text = prompt_text(turn("Make the door red", 7))
    assert text.startswith(INSTRUCTIONS)
    assert text.index("Editor context:") < text.index("This turn:")
    assert text.index('"scene_revision": "7"') > text.index("This turn:")
    assert text.endswith("Make the door red")


def test_claude_turns_leave_the_instructions_to_the_system_prompt() -> None:
    # They reach Claude through --append-system-prompt; repeating them in every turn
    # paid for them again, uncached, each time.
    text = user_input(turn("hello", 1)).decode()
    assert INSTRUCTIONS not in text
    assert text.startswith("Editor context:")


def test_role_rules_come_before_the_project_map() -> None:
    parts = DEBUGGER_PROMPT.fill(error="E", locations="a.gd:1", output="", log="")
    prompt = turn("", 1).model_copy(update=parts)
    text = turn_text(prompt)
    assert text.startswith(DEBUGGER_PROMPT.rules)
    assert text.index(DEBUGGER_PROMPT.rules) < text.index("Editor context:")
    assert text.index("Error: E") > text.index("This turn:")


def review(request: str, diff: str, round_: int) -> dict[str, str]:
    return REVIEWER_PROMPT.fill(
        request=request,
        summary="done",
        round=round_,
        previous="",
        stage="/tmp/stage",
        changes="- a.gd (modified)",
        checks="",
        diff=diff,
        bible="Palette: warm stone.",
        brief="A short quarry game.",
    )


def test_two_reviews_share_the_rules_the_bible_and_the_brief() -> None:
    a = review("Make the door red", "+red", 1)
    b = review("Add a lamp", "+lamp", 2)
    assert a["rules"] == b["rules"]
    assert a["rules"].endswith(
        "Art bible:\nPalette: warm stone.\n\nGame brief:\nA short quarry game."
    )
    first = turn_text(turn("", 1).model_copy(update=a))
    second = turn_text(turn("", 1).model_copy(update=b))
    assert first[: first.index("This turn:")] == second[: second.index("This turn:")]


def test_ways_started_together_share_the_request() -> None:
    def way(index: int, direction: str) -> str:
        parts = VARIANT_PROMPT.fill(
            index=index,
            count=3,
            direction=direction,
            others="the rest",
            request="Make a chair",
        )
        return turn_text(turn("", 1).model_copy(update=parts))

    a, b = way(1, "chunky and worn"), way(2, "slim and clean")
    assert shared(a, b) > a.index("Make a chair")


@pytest.mark.parametrize(
    ("template", "values"),
    [
        (TASK_PROMPT, {"request": "r"}),
        (PLAYTEST_PROMPT, {"minutes": "2", "focus": "jumps"}),
        (CHECK_PROMPT, {"role": "performance", "tool": "perf_check", "findings": "f"}),
        (DESIGN_PROMPT, {"found": "- none"}),
        (SOUND_DESIGNER_PROMPT, {"level": "res://l.tscn", "focus": "f", "making": "-"}),
    ],
)
def test_a_roles_rules_hold_no_value_of_the_run(
    template: RolePrompt, values: dict[str, str]
) -> None:
    # The role's name and tool are the role's own; every other value is the run's.
    fixed = {"role", "tool"}
    other = {k: v if k in fixed else v + " (another run)" for k, v in values.items()}
    first, second = template.fill(**values), template.fill(**other)
    assert first["rules"] == second["rules"]
    assert first["text"] != second["text"]


# --- What each role is offered ---------------------------------------------------------


def listed(role: str | None) -> list[str]:
    return [t.name for t in asyncio.run(make_server(Path("g.sock"), role).list_tools())]


def test_the_proxy_serves_exactly_the_known_tools() -> None:
    # A new tool must be named in joinery.roles.REGISTRY_TOOLS to be offered to anyone.
    assert sorted(listed(None)) == sorted(REGISTRY_TOOLS)


@pytest.mark.parametrize("role", ROLES)
def test_each_role_is_offered_what_it_may_call(role: str) -> None:
    names = set(listed(role))
    assert names == offered_tools(role)
    # drive_game is judged against the project's own setting (joinery.roles.may_drive).
    for tool in names - {"call_tool", "drive_game"}:
        assert registry_allowed(role, tool), tool
    if role in READ_ONLY_ROLES:
        assert not names & WRITERS
        assert {"discover_tools", "tool_schema", "inspect_editor", "call_tool"} <= names
        assert "inspect_game" in names
        assert ("drive_game" in names) == (role in {"playtester", "debugger"})


def test_read_only_roles_carry_far_fewer_schema_bytes() -> None:
    def size(role: str | None) -> int:
        tools = asyncio.run(make_server(Path("g.sock"), role).list_tools())
        return len(
            json.dumps([t.model_dump(by_alias=True, exclude_none=True) for t in tools])
        )

    everything = size(None)
    assert size("lead") < everything  # no other role's report tools
    for role in READ_ONLY_ROLES:
        assert size(role) < everything / 2, role


def test_role_specific_reports_reach_only_their_role() -> None:
    assert "report_review" in offered_tools("reviewer")
    assert "report_review" not in offered_tools("lead")
    assert {"report_diagnosis", "reproduce_error"} <= offered_tools("debugger")
    assert "report_design" in offered_tools("designer")
    assert "start_task" not in offered_tools("worker")


def test_every_provider_launches_the_proxy_with_its_role() -> None:
    assert offered_role("", False) == "lead"
    assert offered_role("reviewer", True) == "reviewer"
    # An inspection specialist keeps the full list, judged per call as before.
    assert offered_role("", True) is None
    config = proxy_config(Path("g.sock"), "reviewer")
    assert config["args"][-2:] == ["--role", "reviewer"]
    assert "--role" not in proxy_config(Path("g.sock"))["args"]


def test_codex_approves_only_the_tools_a_role_is_offered() -> None:
    reviewer = " ".join(registry_config(Path("g.sock"), "reviewer"))
    assert "tools.report_review.approval_mode" in reviewer
    assert "tools.edit_scene.approval_mode" not in reviewer
    assert '"--role", "reviewer"' in reviewer
    lead = " ".join(registry_config(Path("g.sock"), "lead"))
    assert "tools.edit_scene.approval_mode" in lead
    assert "tools.report_review.approval_mode" not in lead
    everything = " ".join(registry_config(Path("g.sock"), None))
    assert "tools.report_review.approval_mode" in everything


def test_claude_flags_where_the_cli_has_them() -> None:
    assert cache_flags("", True, True) == [DYNAMIC_SECTIONS_FLAG]
    assert cache_flags("worker", True, True) == [DYNAMIC_SECTIONS_FLAG]
    assert cache_flags("reviewer", True, True) == [
        DYNAMIC_SECTIONS_FLAG,
        "--tools",
        "Read,Grep,Glob,Bash",
    ]
    # An older CLI without them runs as before.
    assert cache_flags("reviewer", False, False) == []


# --- ACP: the instructions once per session -------------------------------------------


def test_acp_sends_the_instructions_once_while_it_can_see_the_context() -> None:
    session = ACPSession("gemini")
    first = session.prompt_blocks(turn("one", 1))[0]["text"]
    assert first.startswith(INSTRUCTIONS)
    session.instructed = True
    session._context({"used": 9000, "size": 100000})
    second = session.prompt_blocks(turn("two", 2))[0]["text"]
    assert INSTRUCTIONS not in second
    # The agent compacted its history: they go again.
    session._context({"used": 3000, "size": 100000})
    assert session.prompt_blocks(turn("three", 3))[0]["text"].startswith(INSTRUCTIONS)


def test_acp_without_a_context_report_always_sends_them() -> None:
    session = ACPSession("opencode")
    session.instructed = True
    assert session.prompt_blocks(turn("two", 2))[0]["text"].startswith(INSTRUCTIONS)
