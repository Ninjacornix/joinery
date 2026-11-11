"""Staged-change folders and the providers' own tools: the shared policy hook keeps a
session's shell and file tools out of every ``.joinery/transactions/`` folder but the
one change it was given, the same for Codex, Claude Code, Gemini CLI and opencode."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bridge.claude_session import SESSION_MATCHER
from bridge.host import Bridge
from joinery.hook import violation
from joinery.policy import Severity
from joinery.roles import ROLE_ENV
from joinery.stage_access import NO_STAGE, STAGE_ENV, stage_id, touches_changes
from joinery.wiring import _gemini_settings

MINE = "1ee3d0a3bafc442f8bcc41056adf4591"
OTHER = "7e3111509cba44c184dca770da51c2e6"
PROVIDERS = ("claude", "codex", "gemini", "opencode")
#: A generative host from the denylist, spelled apart so no shell sees it whole.
GENERATIVE = "meshy" + ".ai"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("CLAUDE_PROJECT_DIR", "CODEX_PROJECT_DIR", "JOINERY_INSPECTION_ONLY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    return tmp_path


def shell(provider: str, command: str, cwd: Path) -> dict[str, Any]:
    """One shell command as each provider's hook sends it."""
    if provider == "claude":
        return {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(cwd),
        }
    if provider == "codex":
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"bash -lc '{command}'", "workdir": str(cwd)},
            "cwd": str(cwd),
        }
    if provider == "gemini":
        return {
            "hook_event_name": "BeforeTool",
            "tool_name": "run_shell_command",
            "tool_input": {"command": command, "directory": str(cwd)},
            "cwd": str(cwd),
        }
    return {"tool": "bash", "args": {"command": command}, "directory": str(cwd)}


def read(provider: str, path: str, cwd: Path) -> dict[str, Any]:
    """One native file read as each provider's hook sends it (Codex reads by shell)."""
    if provider == "claude":
        return {"tool_name": "Read", "tool_input": {"file_path": path}, "cwd": str(cwd)}
    if provider == "codex":
        return shell("codex", f"sed -n 1,80p {path}", cwd)
    if provider == "gemini":
        return {
            "tool_name": "read_many_files",
            "tool_input": {"paths": ["art/bible.md", path]},
            "cwd": str(cwd),
        }
    return {"tool": "read", "args": {"filePath": path}, "directory": str(cwd)}


def judged(
    monkeypatch: pytest.MonkeyPatch, role: str, stage: str | None, raw: dict[str, Any]
) -> Severity | None:
    if role:
        monkeypatch.setenv(ROLE_ENV, role)
    else:
        monkeypatch.delenv(ROLE_ENV, raising=False)
    if stage is None:
        monkeypatch.delenv(STAGE_ENV, raising=False)
    else:
        monkeypatch.setenv(STAGE_ENV, stage)
    verdict = violation(raw)
    return verdict.severity if verdict is not None else None


def folder(project: Path, change: str, inside: str = "stage/game/level.gd") -> str:
    return str(project / ".joinery/transactions" / change / inside)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_reviewer_reads_the_change_under_review_and_no_other(
    project: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    allowed = [
        read(provider, folder(project, MINE), project),
        shell(provider, f"cat {folder(project, MINE)}", project),
        shell(provider, f"ls .joinery/transactions/{MINE}/stage/game", project),
        read(provider, "game/level.gd", project),
    ]
    for raw in allowed:
        assert judged(monkeypatch, "reviewer", MINE, raw) is None, raw
    refused = [
        read(provider, folder(project, OTHER), project),
        shell(provider, f"cat {folder(project, OTHER)}", project),
        shell(provider, "ls .joinery/transactions", project),
        shell(provider, "find .joinery/transactions -name x.gd", project),
        shell(provider, "cat .joinery/*/x/stage/game/level.gd", project),
        shell(provider, "cd .joinery && cat transactions/x/stage/a.gd", project),
        shell(
            provider, f"cat .joinery/transactions/{MINE}/../{OTHER}/stage/a.gd", project
        ),
        shell(provider, f"cat .joinery/transactions/{MINE}0/stage/a.gd", project),
        read(provider, folder(project, MINE).replace(MINE, MINE.upper()), project),
    ]
    for raw in refused:
        assert judged(monkeypatch, "reviewer", MINE, raw) == Severity.DENY, raw
    # Given no change at all, it reaches none.
    mine = read(provider, folder(project, MINE), project)
    assert judged(monkeypatch, "reviewer", NO_STAGE, mine) == Severity.DENY


@pytest.mark.parametrize("provider", ("claude", "gemini", "opencode"))
def test_a_read_only_role_never_writes_into_a_staged_change(
    project: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    target = folder(project, MINE)
    writes: dict[str, dict[str, Any]] = {
        "claude": {"tool_name": "Write", "tool_input": {"file_path": target}},
        "gemini": {"tool_name": "write_file", "tool_input": {"file_path": target}},
        "opencode": {"tool": "edit", "args": {"filePath": target}},
    }
    raw = {**writes[provider], "cwd": str(project)}
    for role in ("reviewer", "debugger", "playtester"):
        assert judged(monkeypatch, role, MINE, raw) == Severity.DENY
        assert "can't write into a staged change" in str(violation(raw))
    command = shell(provider, f"sed -i s/a/b/ {target}", project)
    assert judged(monkeypatch, "reviewer", MINE, command) == Severity.DENY


@pytest.mark.parametrize("provider", PROVIDERS)
def test_authors_reach_their_own_change_only(
    project: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    mine = read(provider, folder(project, MINE), project)
    other = read(provider, folder(project, OTHER), project)
    plain = read(provider, "game/level.gd", project)
    for role in ("", "lead", "worker"):
        assert judged(monkeypatch, role, MINE, mine) is None
        assert judged(monkeypatch, role, MINE, other) == Severity.DENY
        # Before its change exists it has none: project paths only.
        assert judged(monkeypatch, role, NO_STAGE, mine) == Severity.DENY
        assert judged(monkeypatch, role, NO_STAGE, plain) is None


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_session_joinery_did_not_start_is_judged_as_before(
    project: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    raw = read(provider, folder(project, OTHER), project)
    assert judged(monkeypatch, "", None, raw) is None


def test_rule_zero_still_stops_first(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = f"curl https://{GENERATIVE}/x .joinery/transactions/a"
    raw = shell("claude", command, project)
    assert judged(monkeypatch, "reviewer", MINE, raw) == Severity.STOP
    assert judged(monkeypatch, "", NO_STAGE, raw) == Severity.STOP


def test_stage_ids_and_paths() -> None:
    assert stage_id(MINE) == MINE and stage_id(NO_STAGE) == "" and stage_id("") == ""
    assert stage_id("../x") == "" and stage_id("a b") == ""
    assert not touches_changes({"command": "cat game/shop_transactions.gd"})
    assert touches_changes({"paths": ["a", ".joinery\\transactions\\x\\a.gd"]})
    assert not touches_changes(
        {"path": f".joinery\\transactions\\{MINE}\\stage\\a.gd"}, MINE
    )


@pytest.mark.parametrize("agent", PROVIDERS)
def test_every_provider_session_carries_its_stage_to_the_hook(agent: str) -> None:
    reviewer = Bridge(agent, role="reviewer")
    session = reviewer._adapter.session  # type: ignore[attr-defined]
    assert session.stage == ""
    reviewer.allow_stage(MINE)
    assert session.stage == MINE
    source = Path(type(session).__module__.replace(".", "/") + ".py").read_text()
    assert "env[STAGE_ENV] = self.stage or NO_STAGE" in source


def test_the_hooks_see_the_providers_own_file_tools() -> None:
    for tool in ("Read", "Grep", "Glob", "Write", "Edit", "Bash"):
        assert tool in SESSION_MATCHER.split("|")
    hooks: Any = _gemini_settings("")["hooks"]
    matcher = str(hooks["BeforeTool"][0]["matcher"]).split("|")
    for tool in (
        "read_file",
        "read_many_files",
        "glob",
        "list_directory",
        "write_file",
    ):
        assert tool in matcher
