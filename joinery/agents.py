"""Supported coding agents and how each one is wired to Joinery's policy gate.

Joinery is agent-agnostic. The provenance gate (Rule Zero) must hold whichever
agent a user runs, so every supported agent gets the *same* `joinery hook`
command, wired through that agent's own hook mechanism. The agents disagree on
three things this module pins down:

1. where hook config lives and in what format,
2. the name of the "before a tool runs" event and how tool names are spelled,
3. how the hook learns the project directory.

One policy brain (`joinery.hook`), four mouths (`.claude`, `.codex`, `.gemini`,
opencode plugin). The gate is defense-in-depth per agent; the real guarantee is
the offline sandbox worker plus the CI asset/deps gates, which no agent can
bypass. See docs/adr/0006-agent-agnostic-enforcement.md.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# How an agent spells an MCP tool call in its PreToolUse payload.
#   "dunder"  -> mcp__<server>__<tool>   (Claude Code, Codex)
#   "single"  -> mcp_<server>_<tool>     (Gemini CLI)
#   "fields"  -> tool name + separate server field (opencode plugin)
McpNaming = Literal["dunder", "single", "fields"]

# Where a hook command reads the project directory from, in priority order.
#   env var name is tried first, then the stdin "cwd" field, then git root, ROOT.
ProjectDirEnv = Literal["CLAUDE_PROJECT_DIR", "CODEX_PROJECT_DIR", ""]

# How an agent can be told to halt the whole turn from a pre-tool hook.
#   "json" -> exit 0 with a JSON decision carrying `continue: false`. Verified
#             for Claude Code and Gemini CLI. The same payload also carries a
#             deny decision, so if `continue` is ignored the call is still
#             refused: it cannot fail open.
#   "exit"  -> no verified JSON stop path, so use exit 2, which reliably refuses
#             the call, and say loudly in stderr that Rule Zero was hit.
StopMode = Literal["json", "exit"]


class Agent(BaseModel):
    """One coding agent and the facts needed to enforce the gate inside it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    # Config file, relative to the project root, that carries the hook wiring.
    config_path: str
    config_format: Literal["json", "toml", "js"]
    # The "about to call a tool" lifecycle event this agent fires.
    pre_tool_event: str
    mcp_naming: McpNaming
    # What this agent calls its shell tool; normalised to "Bash" internally.
    shell_tool: str
    # Its web-fetch tool, if any; normalised to "WebFetch". Empty = none to gate.
    fetch_tool: str
    project_dir_env: ProjectDirEnv
    # How to halt the whole turn on a Rule Zero violation.
    stop_mode: StopMode
    # True when a blocking PreToolUse hook is available and reliable enough to
    # be the project's gate. False means Joinery must refuse to rely on it
    # (fail-closed) and say why.
    blocking_hook: bool
    # Honest, documented limits of this agent's gate. Shown to the user.
    gate_limits: tuple[str, ...] = ()
    # The instructions file this agent reads from the project root on its own
    # (Claude Code: CLAUDE.md; Codex and opencode: AGENTS.md; Gemini CLI:
    # GEMINI.md). Joinery writes a stub there pointing at JOURNEYMAN.md (ADR 0077).
    memory_file: str = "AGENTS.md"


CLAUDE = Agent(
    id="claude",
    name="Claude Code",
    config_path=".claude/settings.json",
    config_format="json",
    pre_tool_event="PreToolUse",
    mcp_naming="dunder",
    shell_tool="Bash",
    fetch_tool="WebFetch",
    project_dir_env="CLAUDE_PROJECT_DIR",
    stop_mode="json",
    blocking_hook=True,
    memory_file="CLAUDE.md",
    gate_limits=(
        "The hook is a guard rail, not a sandbox: offline workers are the real boundary.",
    ),
)

CODEX = Agent(
    id="codex",
    name="Codex CLI",
    config_path=".codex/hooks.json",
    config_format="json",
    pre_tool_event="PreToolUse",
    mcp_naming="dunder",
    shell_tool="Bash",
    fetch_tool="",  # WebSearch is a hosted tool; see note on the gate limit.
    project_dir_env="",  # No guaranteed env var; hooks run in the session cwd.
    stop_mode="exit",
    blocking_hook=True,
    gate_limits=(
        (
            "Hosted WebSearch does not reach a PreToolUse hook. It stays allowed: "
            "it reads the web, it does not create assets, and the host denylist "
            "and offline sandbox still bound it. Rule Zero gates asset tools, not "
            "reading."
        ),
        (
            "A PreToolUse hook timeout (not an error) could let a tool run; the hook "
            "is pure-Python and fast, and fails closed on any exception."
        ),
    ),
)

GEMINI = Agent(
    id="gemini",
    name="Gemini CLI",
    config_path=".gemini/settings.json",
    config_format="json",
    pre_tool_event="BeforeTool",
    mcp_naming="single",
    shell_tool="run_shell_command",
    fetch_tool="web_fetch",
    project_dir_env="",  # Uses the stdin "cwd" field.
    stop_mode="json",
    blocking_hook=True,
    memory_file="GEMINI.md",
    gate_limits=(
        (
            "A Rule Zero hit stops the turn via `continue: false`; an ordinary "
            "'not enabled here' refusal blocks just that call and the turn goes on."
        ),
    ),
)

OPENCODE = Agent(
    id="opencode",
    name="opencode",
    config_path=".opencode/plugin/joinery-gate.js",
    config_format="js",
    pre_tool_event="tool.execute.before",
    mcp_naming="fields",
    shell_tool="bash",
    fetch_tool="webfetch",
    project_dir_env="",  # The plugin passes directory in its payload.
    stop_mode="exit",
    blocking_hook=True,
    gate_limits=(
        (
            "tool.execute.before does not intercept subagent tool calls (issue "
            "#5894). Nothing is disabled to compensate; those calls are covered "
            "by the offline sandbox and the CI asset gate."
        ),
    ),
)

AGENTS: tuple[Agent, ...] = (CLAUDE, CODEX, GEMINI, OPENCODE)
_BY_ID = {a.id: a for a in AGENTS}


def get(agent_id: str) -> Agent:
    try:
        return _BY_ID[agent_id]
    except KeyError:
        raise KeyError(f"unknown agent: {agent_id!r}") from None


def display_name(agent_id: str) -> str:
    """The agent's name as the user knows it ("Claude Code"); the id when unknown."""
    agent = _BY_ID.get(agent_id)
    return agent.name if agent is not None else agent_id


def all_agents() -> tuple[Agent, ...]:
    return AGENTS
