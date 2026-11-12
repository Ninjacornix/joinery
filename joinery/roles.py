"""Agent roles, the same for every provider (Codex, Claude Code, Gemini CLI, opencode).

Every agent Joinery runs has one role:

* ``lead``: the chat's own request. Changes the project, starts tasks.
* ``worker``: a parallel task, or the fix a debugger proposed. Changes staged files;
  can't start tasks.
* ``reviewer``: reads a submitted change and reports findings. Read-only.
* ``debugger``: reads errors and logs, reproduces offline and proposes a fix for a
  worker to make. Read-only.
* ``playtester``: plays the game headless with ``playtest`` and writes a report of
  bugs, softlocks and balance notes (ADR 0038). Read-only.
* ``accessibility`` and ``performance``: read their check (``access_check``,
  ``perf_check``) and the code it points at, and word the fix a worker makes for a
  finding that needs code (ADR 0049, ADR 0050). Read-only.
* ``designer``: reads the brief, design documents, scenes and scripts for what breaks
  the game or goes against its own brief, and reports each finding with evidence
  through ``report_design`` (the design check). Read-only.
* ``sound-designer``: listens to the level with ``sound_audit`` and writes a short
  report of what is missing and what the sound pass makes. Read-only; the sounds
  themselves are made by scripted tasks, each a reviewed change.

The role reaches the provider's policy hook as ``JOINERY_AGENT_ROLE`` and the
gateway endpoint as its ``role``; both judge each call against the same tables
here, so no provider can widen what a role may do. No role can approve, accept or
discard a change, or approve or edit the art bible: approval is absent from the
model-facing API, and the read-only roles can't write anything at all.
"""

from __future__ import annotations

import re
import shlex
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from joinery.policy import Severity, Verdict
from joinery.read_access import read_only

Role = Literal[
    "lead",
    "worker",
    "reviewer",
    "debugger",
    "playtester",
    "accessibility",
    "performance",
    "designer",
    "sound-designer",
]
ROLES: tuple[Role, ...] = get_args(Role)
#: The policy hook reads the role from here; absent means ``lead``.
ROLE_ENV = "JOINERY_AGENT_ROLE"
READ_ONLY_ROLES: frozenset[str] = frozenset(
    {
        "reviewer",
        "debugger",
        "playtester",
        "accessibility",
        "performance",
        "designer",
        "sound-designer",
    }
)

#: joinery-registry tools every read-only role may call.
REGISTRY_READS = frozenset(
    {"discover_tools", "tool_schema", "inspect_editor", "view_editor"}
)
#: Tools that only one role reports through.
REVIEW_REPORT = "report_review"
DIAGNOSIS_REPORT = "report_diagnosis"
REPRODUCE = "reproduce_error"
DESIGN_REPORT = "report_design"
#: Play the game headless: read-only, so the lead, workers and the playtester may.
PLAYTEST = "playtest"
#: The accessibility and performance checks: read-only, so the lead and workers may too.
ACCESS_CHECK = "access_check"
PERF_CHECK = "perf_check"
#: What the level is missing to sound like a game: read-only, so any role may.
SOUND_AUDIT = "sound_audit"
ROLE_ONLY = frozenset({REVIEW_REPORT, DIAGNOSIS_REPORT, REPRODUCE, DESIGN_REPORT})
#: Only the lead starts more agents: parallel tasks and variants.
LEAD_ONLY = frozenset({"start_task", "start_variants"})


@dataclass(frozen=True)
class RoleSpec:
    """What one role is: its turn and what it may call."""

    role: Role
    #: How Joinery runs its turn: the chat's own, a task, a timed review or a diagnosis.
    turn: Literal[
        "chat", "task", "review", "diagnosis", "playtest", "check", "design", "sound"
    ]
    read_only: bool
    #: joinery-registry tools beyond the shared ones ("*" for every tool).
    registry: frozenset[str]
    #: Seconds a turn of this role may take (0: no limit of its own).
    seconds: int
    #: What the step on its card says while it works.
    step: str


SPECS: dict[str, RoleSpec] = {
    "lead": RoleSpec(
        "lead", "chat", False, frozenset({"*"}) - ROLE_ONLY, 0, "Thinking…"
    ),
    "worker": RoleSpec(
        "worker",
        "task",
        False,
        frozenset({"*"}) - ROLE_ONLY - LEAD_ONLY,
        0,
        "Working…",
    ),
    "reviewer": RoleSpec(
        "reviewer",
        "review",
        True,
        REGISTRY_READS | {REVIEW_REPORT},
        90,
        "Reviewing the change…",
    ),
    "debugger": RoleSpec(
        "debugger",
        "diagnosis",
        True,
        REGISTRY_READS | {DIAGNOSIS_REPORT, REPRODUCE},
        240,
        "Reading the error…",
    ),
    "playtester": RoleSpec(
        "playtester",
        "playtest",
        True,
        REGISTRY_READS | {PLAYTEST},
        # A run of up to 10 minutes of game time, its screenshots and the write-up.
        1200,
        "Playing the game…",
    ),
    "accessibility": RoleSpec(
        "accessibility",
        "check",
        True,
        REGISTRY_READS | {ACCESS_CHECK},
        300,
        "Reading the accessibility check…",
    ),
    "performance": RoleSpec(
        "performance",
        "check",
        True,
        REGISTRY_READS | {PERF_CHECK, PLAYTEST},
        # A check may play the game twice (headless, then in a window) and read code.
        600,
        "Reading the performance profile…",
    ),
    "designer": RoleSpec(
        "designer",
        "design",
        True,
        REGISTRY_READS | {DESIGN_REPORT},
        300,
        "Reading the design…",
    ),
    "sound-designer": RoleSpec(
        "sound-designer",
        "sound",
        True,
        REGISTRY_READS | {SOUND_AUDIT},
        240,
        "Listening to the level…",
    ),
}


def spec(role: str) -> RoleSpec:
    """The role's spec; an unknown role is refused rather than read as the lead."""
    try:
        return SPECS[role or "lead"]
    except KeyError:
        raise ValueError(f"Unknown agent role: {role!r}") from None


def registry_allowed(role: str, tool: str) -> bool:
    """May ``role`` call the joinery-registry tool ``tool``?"""
    allowed = spec(role).registry
    if "*" in allowed:
        excluded = ROLE_ONLY | (LEAD_ONLY if role == "worker" else set())
        return tool not in excluded
    return tool in allowed


# --- What each role is offered --------------------------------------------------------

#: Every tool the joinery-registry MCP proxy serves (servers.joinery_registry.proxy).
REGISTRY_TOOLS: tuple[str, ...] = (
    "discover_tools",
    "tool_schema",
    "edit_scene",
    "inspect_editor",
    "view_editor",
    "inspect_in_parallel",
    "submit_for_review",
    "precheck",
    "start_task",
    "start_variants",
    "task_status",
    "playtest",
    "access_check",
    "perf_check",
    "blockout",
    "sound_audit",
    "report_review",
    "report_design",
    "report_diagnosis",
    "reproduce_error",
    "call_tool",
)
#: Judged per call by what it calls: a read-only role reaches audited reads only.
CALL_TOOL = "call_tool"


def offered_tools(role: str) -> frozenset[str]:
    """The joinery-registry tools listed to ``role``'s agent: what it may call, so a
    read-only role never sees a writer's schema. Listing is not the gate: the policy
    hook and the gateway still judge every call (``role_violation``, GatewayRPC)."""
    if spec(role).read_only:
        return frozenset(spec(role).registry | {CALL_TOOL}) & frozenset(REGISTRY_TOOLS)
    return frozenset(t for t in REGISTRY_TOOLS if registry_allowed(role, t))


# --- Provider built-in tools offered to read-only roles -------------------------------

#: Claude Code's built-in tools a read-only role is offered (``claude --tools``): its
#: reads, and Bash, which the hook holds to read-only commands.
CLAUDE_READ_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob", "Bash")


# --- Read-only shell ------------------------------------------------------------------

_SEGMENTS = re.compile(r"&&|\|\||[;|\n]")
#: Programs that only read. Anything else (an editor, a package manager, a network
#: client, an interpreter) is refused for the read-only roles.
_READERS = frozenset(
    {
        "cat",
        "head",
        "tail",
        "nl",
        "wc",
        "ls",
        "grep",
        "egrep",
        "rg",
        "find",
        "sed",
        "stat",
        "file",
        "pwd",
        "echo",
        "sort",
        "uniq",
        "cut",
        "diff",
        "tree",
        "true",
    }
)
_GIT_READS = frozenset({"log", "show", "diff", "status", "blame", "ls-files"})
_FIND_WRITES = frozenset(
    {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprintf", "-fls"}
)
#: ``sed -n 1,80p``-style scripts: printing only.
_SED_PRINT = re.compile(r"^(?:\d+|\$|/[^/]*/)(?:,(?:\d+|\$|/[^/]*/))?p$")
_WRAPPERS = re.compile(r"^(?:/bin/|/usr/bin/)?(?:ba|z)?sh\s+-l?c\s+")


def _unwrap(command: str) -> str:
    """``bash -lc '…'`` (how Codex runs a command) -> the command inside."""
    text = command.strip()
    if _WRAPPERS.match(text):
        try:
            parts = shlex.split(text)
        except ValueError:
            return text
        if len(parts) >= 3:
            return parts[2]
    return text


def _reads(segment: str) -> bool:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", tokens[0]):
        tokens = tokens[1:]  # LC_ALL=C grep …
    if not tokens:
        return True
    program = Path(tokens[0]).name
    args = tokens[1:]
    if program == "git":
        return bool(args) and args[0] in _GIT_READS
    if program not in _READERS:
        return False
    if program == "find":
        return not any(arg in _FIND_WRITES for arg in args)
    if program == "sed":
        flags = [a for a in args if a.startswith("-")]
        if any(f not in {"-n", "-E", "-r", "-e"} for f in flags):
            return False
        scripts = [a for a in args if not a.startswith("-")]
        return bool(scripts) and bool(_SED_PRINT.match(scripts[0]))
    return True


def read_only_command(command: str) -> bool:
    """True when every part of ``command`` only reads: no redirection, no
    substitution, nothing run in the background, only known readers."""
    inner = _unwrap(command)
    if re.search(r"[<>`]|\$\(|&(?!&)", inner.replace("&&", "")):
        return False
    return all(_reads(part.strip()) for part in _SEGMENTS.split(inner))


# --- The policy -----------------------------------------------------------------------

#: Native tools of the four providers that write or delegate; refused for read-only roles.
_WRITERS = frozenset(
    {
        "write",
        "edit",
        "multiedit",
        "notebookedit",
        "apply_patch",
        "write_file",
        "replace",
        "patch",
        "task",
        "agent",
    }
)


def native_writer(native: str) -> bool:
    """A provider's own tool that writes or delegates (Write, apply_patch, Task…)."""
    return native.lower() in _WRITERS


def refusal(role: str) -> str:
    if role == "reviewer":
        return (
            "The reviewer only reads. Report what you found with report_review; "
            "the author agent makes the fixes and the user decides."
        )
    if role == "debugger":
        return (
            "The debugger only reads and reproduces offline. Report the cause and the "
            "fix with report_diagnosis; a worker makes the change."
        )
    if role == "playtester":
        return (
            "The playtester only plays and reads. Call playtest, then write your "
            "report; Journeyman makes the fixes the user asks for."
        )
    if role == "designer":
        return (
            "The design check only reads. Report each finding with its evidence "
            "through report_design; Journeyman makes the fixes the user asks for."
        )
    if role in {"accessibility", "performance"}:
        return (
            f"The {role} agent only reads. Write the fix as your reply; a worker "
            "makes the change and the user reviews it."
        )
    if role == "sound-designer":
        return (
            "The sound designer only listens and reads. Call sound_audit, then write "
            "your report; Joinery makes the sounds as reviewed changes."
        )
    return "This agent's role can't make that call."


def role_violation(
    role: str,
    kind: str,
    server: str,
    tool: str,
    command: str = "",
    native: str = "",
    arguments: Mapping[str, Any] | None = None,
) -> Verdict | None:
    """What ``role`` may not do, beyond Rule Zero (which the caller judges first).

    ``kind`` is the hook's canonical event kind: "mcp", "bash", "fetch" or "other";
    ``native`` is the provider's own tool name for "other".
    """
    role = role or "lead"
    if role not in SPECS:
        return Verdict(Severity.DENY, f"Unknown agent role {role!r}.")
    registry = server.replace("_", "-") == "joinery-registry"
    if not spec(role).read_only:
        if kind == "mcp" and registry and not registry_allowed(role, tool):
            return Verdict(
                Severity.DENY,
                "A task can't start further tasks; do this part yourself."
                if tool in LEAD_ONLY
                else "That tool belongs to another agent role.",
            )
        return None
    if kind == "mcp":
        if registry:
            if registry_allowed(role, tool):
                return None
            # call_tool is judged by what it calls: only audited reads pass.
            values = arguments or {}
            if tool == "call_tool" and read_only(
                str(values.get("server", "")), str(values.get("tool", ""))
            ):
                return None
            return Verdict(Severity.DENY, refusal(role))
        return Verdict(Severity.DENY, refusal(role))
    if kind == "bash":
        return (
            None
            if read_only_command(command)
            else Verdict(Severity.DENY, refusal(role))
        )
    if kind == "fetch":
        return Verdict(Severity.DENY, "Read-only agents work offline; no web access.")
    if native_writer(native):
        return Verdict(Severity.DENY, refusal(role))
    return None


def call_allowed(role: str, server: str, tool: str) -> bool:
    """A ``call_tool`` through the registry: read-only roles reach audited reads only."""
    return not spec(role).read_only or read_only(server, tool)


# --- Settings -------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewerSettings:
    """``[agents] reviewer`` and ``reviewer_model`` in joinery.toml."""

    enabled: bool = True
    #: "" = the same model as the agent whose change it reviews.
    model: str = ""
    #: Times the reviewer may send a change back before it opens with notes.
    rounds: int = 2
    seconds: int = 90


def reviewer_settings(project: Path) -> ReviewerSettings:
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ReviewerSettings()
    table = raw.get("agents", {})
    if not isinstance(table, dict):
        return ReviewerSettings()
    enabled = table.get("reviewer", True)
    model = table.get("reviewer_model", "")
    rounds = table.get("review_rounds", 2)
    return ReviewerSettings(
        enabled=enabled if isinstance(enabled, bool) else True,
        model=model.strip()[:120] if isinstance(model, str) else "",
        rounds=max(0, min(rounds, 3))
        if isinstance(rounds, int) and not isinstance(rounds, bool)
        else 2,
    )


_AGENTS_TABLE = re.compile(r"(?ms)^\[agents\][^\n]*\n(.*?)(?=^\[|\Z)")


def set_agents_values(project: Path, values: dict[str, str]) -> None:
    """Write ``key = value`` lines (TOML values as written) into ``[agents]`` of
    joinery.toml, keeping every other line. Raises ValueError when the result
    would not read back."""
    path = project / "joinery.toml"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    match = _AGENTS_TABLE.search(text)
    if match:
        body = match.group(1)
        for key, value in values.items():
            line = re.compile(rf"(?m)^{key}\s*=.*$")
            if line.search(body):
                body = line.sub(f"{key} = {value}", body, count=1)
            else:
                body = (
                    body.rstrip("\n")
                    + ("\n" if body.strip() else "")
                    + (f"{key} = {value}\n")
                )
        if not body.endswith("\n"):
            body += "\n"
        updated = text[: match.start(1)] + body + text[match.end(1) :]
    else:
        lead = text.rstrip("\n") + "\n\n" if text.strip() else ""
        updated = (
            lead + "[agents]\n" + "".join(f"{k} = {v}\n" for k, v in values.items())
        )
    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"joinery.toml can't be read: {exc}") from None
    path.write_text(updated, encoding="utf-8")


def set_reviewer(project: Path, *, enabled: bool | None, model: str | None) -> None:
    """Save the reviewer choices, keeping every other line of joinery.toml."""
    values: dict[str, str] = {}
    if enabled is not None:
        values["reviewer"] = "true" if enabled else "false"
    if model is not None:
        clean = model.strip()
        if len(clean) > 120 or any(c in clean for c in '"\\\n'):
            raise ValueError("Name the reviewer's model in one plain line.")
        values["reviewer_model"] = '"' + clean + '"'
    set_agents_values(project, values)
    saved = reviewer_settings(project)
    if (enabled is not None and saved.enabled is not enabled) or (
        model is not None and saved.model != model.strip()
    ):
        raise ValueError("joinery.toml keeps [agents] in a form Joinery can't edit.")
