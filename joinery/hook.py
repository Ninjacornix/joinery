"""PreToolUse policy hook, shared by every supported agent.

One command, `joinery hook`, enforces Rule Zero for Claude Code, Codex, Gemini
CLI and opencode. Each agent sends a slightly different JSON payload on stdin;
`normalise_event` folds them into one canonical shape before the policy check
runs.

Nothing is disabled or removed: every tool stays callable and is judged per
call. A refusal lands one of two ways (see `joinery.policy.Severity`):

* **stop** — a Rule Zero violation, something tried to generate an asset. Where
  the agent supports it (`stop_mode == "json"`), the hook halts the whole turn
  with `continue: false` so the user is told, instead of letting the agent
  quietly try another route. The same payload also denies the call, so if
  `continue` is ignored the call is still refused; it cannot fail open.
* **deny** — not available here (a server the project did not enable, a narrow
  escape-hatch tool). Exit 2 refuses that one call and the turn carries on.

Any internal error also exits 2 (fail-closed).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from joinery.agents import StopMode
from joinery.agents import get as get_agent
from joinery.catalog import (
    ROOT,
    allowed_servers,
    blocked_operation,
    blocked_tools,
    load_catalog,
)
from joinery.policy import (
    Severity,
    Verdict,
    command_violation,
    generative_names,
    load_denylist,
    normalise,
    url_violation,
    user_only_violation,
)
from joinery.read_access import read_only

# Env vars an agent may set to tell the hook its project directory, in order.
_PROJECT_DIR_ENVS = ("CLAUDE_PROJECT_DIR", "CODEX_PROJECT_DIR")
# Shell tool names across agents, normalised to "Bash".
_SHELL_TOOLS = {"bash", "run_shell_command", "shell", "exec_command"}
# Web-fetch tool names across agents, normalised to "WebFetch".
_FETCH_TOOLS = {"webfetch", "web_fetch", "fetch"}


@dataclass(frozen=True)
class Event:
    """One tool call, canonicalised across agents."""

    kind: str  # "mcp", "bash", "fetch" or "other"
    server: str  # MCP server, for kind == "mcp"
    tool: str  # bare MCP tool name, for kind == "mcp"
    command: str  # shell command, for kind == "bash"
    url: str  # URL, for kind == "fetch"
    project_dir: Path
    #: The MCP call's arguments, so operation-level blocks apply to every agent.
    arguments: Mapping[str, Any] = field(default_factory=dict, compare=False)


def _split_mcp(tool_name: str, explicit_server: str) -> tuple[str, str]:
    """Return (server, tool) from any agent's MCP tool spelling.

    Claude/Codex use ``mcp__server__tool``; Gemini uses ``mcp_server_tool``;
    opencode may pass the server in a separate field. A single-underscore name
    is ambiguous (servers and tools can contain ``_``), so prefer the explicit
    field, then the unambiguous dunder form, then a best-effort single split.
    """
    if explicit_server:
        tool = tool_name
        for prefix in (f"mcp__{explicit_server}__", f"mcp_{explicit_server}_"):
            if tool.startswith(prefix):
                tool = tool[len(prefix) :]
                break
        return explicit_server, tool
    if tool_name.startswith("mcp__"):
        server, _, tool = tool_name.removeprefix("mcp__").partition("__")
        return server, tool
    if tool_name.startswith("mcp_"):
        server, _, tool = tool_name.removeprefix("mcp_").partition("_")
        return server, tool
    return "", tool_name


def _resolve_project_dir(cwd: str) -> Path:
    for env in _PROJECT_DIR_ENVS:
        value = os.environ.get(env)
        if value:
            return Path(value)
    start = Path(cwd) if cwd else Path.cwd()
    try:
        root = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        if root:
            return Path(root)
    except (subprocess.SubprocessError, OSError):
        pass
    return start if cwd else ROOT


def normalise_event(raw: dict[str, Any]) -> Event:
    """Fold any supported agent's PreToolUse payload into an Event.

    Agents name the fields differently but overlap enough to read uniformly:
    Claude/Codex/Gemini use ``tool_name``/``tool_input``; opencode's plugin
    bridge forwards ``tool``/``args``/``server``. Missing fields read as empty,
    which the policy check treats as "nothing to block" rather than crashing.
    """
    tool_name = str(raw.get("tool_name") or raw.get("tool") or "")
    tool_input = raw.get("tool_input") or raw.get("args") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    explicit_server = str(raw.get("server") or raw.get("mcp_server") or "")
    cwd = str(raw.get("cwd") or raw.get("project_dir") or raw.get("directory") or "")
    project_dir = _resolve_project_dir(cwd)

    lowered = tool_name.lower()
    if tool_name.startswith(("mcp__", "mcp_")) or explicit_server:
        server, tool = _split_mcp(tool_name, explicit_server)
        return Event("mcp", server, tool, "", "", project_dir, tool_input)
    if lowered in _SHELL_TOOLS:
        command = str(tool_input.get("command") or tool_input.get("cmd") or "")
        return Event("bash", "", "", command, "", project_dir)
    if lowered in _FETCH_TOOLS:
        url = str(tool_input.get("url") or "")
        return Event("fetch", "", "", "", url, project_dir)
    return Event("other", "", "", "", "", project_dir)


def mcp_violation(
    server: str,
    tool: str,
    project_dir: Path,
    arguments: Mapping[str, Any] | None = None,
) -> Verdict | None:
    if not server:
        return Verdict(Severity.DENY, "MCP call with no resolvable server name")
    catalog = load_catalog()
    config = project_dir / "joinery.toml"
    enabled = allowed_servers(catalog, config)
    if server not in enabled:
        # Some providers normalize MCP namespaces from '-' to '_'. Resolve
        # only an unambiguous enabled identity; this grants no new server.
        aliases = [name for name in enabled if name.replace("-", "_") == server]
        if len(aliases) == 1:
            server = aliases[0]
    if server not in enabled:
        # A server named after a known generative service is a Rule Zero
        # attempt, not a configuration gap, so it stops the turn.
        if normalise(server) in generative_names(load_denylist()):
            return Verdict(Severity.STOP, f"{server} is a generative asset service")
        return Verdict(
            Severity.DENY, f"MCP server {server!r} is not enabled for this project"
        )
    if tool in blocked_tools(catalog, server):
        return Verdict(
            Severity.DENY, f"{tool} is blocked on the preferred {server} MCP"
        )
    operation = blocked_operation(catalog, server, tool, arguments or {})
    if operation:
        return Verdict(
            Severity.DENY,
            f"{tool} {operation} is blocked on the preferred {server} MCP",
        )
    return None


def violation(raw: dict[str, Any]) -> Verdict | None:
    event = normalise_event(raw)
    if os.environ.get("JOINERY_INSPECTION_ONLY") == "1":
        values = raw.get("tool_input") or raw.get("args") or {}
        allowed = event.kind == "other"
        if event.kind == "mcp" and event.server.replace("_", "-") == "joinery-registry":
            allowed = event.tool in {
                "discover_tools",
                "tool_schema",
                "inspect_editor",
                "view_editor",
            }
            if event.tool == "call_tool" and isinstance(values, dict):
                allowed = read_only(
                    str(values.get("server", "")), str(values.get("tool", ""))
                )
        if not allowed:
            # Still check Rule Zero first: a forbidden asset service stops the
            # entire user turn, even when attempted by an inspection specialist.
            verdict = (
                mcp_violation(
                    event.server, event.tool, event.project_dir, event.arguments
                )
                if event.kind == "mcp"
                else command_violation(event.command, load_denylist())
                if event.kind == "bash"
                else url_violation(event.url, load_denylist())
                if event.kind == "fetch"
                else None
            )
            if verdict and verdict.severity == Severity.STOP:
                return verdict
            return Verdict(
                Severity.DENY,
                "This specialist is read-only. Use the main thread for changes.",
            )
    if event.kind == "mcp":
        return mcp_violation(
            event.server, event.tool, event.project_dir, event.arguments
        )
    if event.kind == "bash":
        return command_violation(event.command, load_denylist()) or user_only_violation(
            event.command
        )
    if event.kind == "fetch":
        return url_violation(event.url, load_denylist())
    return None


_RULE_ZERO = (
    "Joinery is creation, not generation: assets are made by operating real "
    "tools, never generated. Tell the user what was attempted and agree on a "
    "tool-based approach instead."
)


def _stop_payload(verdict: Verdict, event_name: str) -> dict[str, Any]:
    """A decision that halts the turn *and* denies the call.

    Both signals go in one payload on purpose. `continue: false` is the stop;
    the deny fields are the floor, so an agent that does not honour `continue`
    still refuses the call rather than letting it through.
    """
    message = f"Blocked by Joinery policy: {verdict.reason}. {_RULE_ZERO}"
    return {
        "continue": False,
        "stopReason": message,
        "decision": "deny",
        "reason": message,
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        },
    }


def _emit(verdict: Verdict, stop_mode: StopMode, event_name: str) -> int:
    if verdict.severity is Severity.STOP and stop_mode == "json":
        # Exit 0 is required for the agent to read the decision from stdout.
        print(json.dumps(_stop_payload(verdict, event_name)))
        return 0
    prefix = "STOP, Rule Zero" if verdict.severity is Severity.STOP else "Blocked"
    detail = f" {_RULE_ZERO}" if verdict.severity is Severity.STOP else ""
    print(f"{prefix} by Joinery policy: {verdict.reason}.{detail}", file=sys.stderr)
    return 2


def main(agent_id: str | None = None) -> int:
    """Run the gate. `agent_id` comes from `--agent`; absent means exit-2 mode."""
    try:
        raw = json.load(sys.stdin)
        verdict = violation(raw)
        if verdict is None:
            return 0
        endpoint = os.environ.get("JOINERY_POLICY_ENDPOINT")
        if verdict.severity == Severity.STOP and endpoint:
            # Inform the chat host directly: the provider ending its turn must
            # not let Joinery submit or continue it, and stopping a specialist
            # must not let the parent retry through another tool.
            from joinery.gateway_rpc import rpc

            async def notify() -> None:
                await asyncio.wait_for(
                    rpc(
                        Path(endpoint),
                        "policy_stop",
                        {
                            "reason": "STOP, Rule Zero by Joinery policy: "
                            + verdict.reason
                        },
                    ),
                    5,
                )

            try:
                asyncio.run(notify())
            except (OSError, RuntimeError, TimeoutError):
                pass  # The original hook verdict still stops/denies the child.
        stop_mode: StopMode = "exit"
        if agent_id:
            stop_mode = get_agent(agent_id).stop_mode
        event_name = str(raw.get("hook_event_name") or "PreToolUse")
        return _emit(verdict, stop_mode, event_name)
    except Exception as exc:  # noqa: BLE001 - a broken policy check must block, not pass.
        print(f"Joinery policy check failed: {exc}", file=sys.stderr)
        return 2
