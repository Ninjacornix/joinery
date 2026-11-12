"""Provider-owned authentication, process supervision and a read-only project boundary."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import sys
from pathlib import Path
from typing import Any

from bridge.instructions import GAME_GUIDE, REVIEW_GUIDE
from bridge.protocol import Prompt
from joinery import memory
from joinery.agents import get
from joinery.catalog import ROOT
from joinery.chat_attachments import listed
from joinery.compact import editor_outline
from joinery.connection import status
from joinery.project_map import cached as cached_map
from joinery.project_map import near_line
from joinery.provider_support import WINDOWS_BOUNDARY_REASON, require_supported
from joinery.wiring import _command_str

INSTRUCTIONS = (
    "You operate Joinery's current Godot project. Use the connected joinery-registry MCP gateway: "
    "inspect_editor for live scene identities, edit_scene for reversible live node changes, and "
    "discover_tools/tool_schema/call_tool for native creative apps and staged saved-file edits. "
    "Do not give manual editor steps when a connected tool can do the requested work. "
    "Raw project files are read-only in this agent process. The gateway owns mutations and user review. "
    "Keep unsaved live scenes separate from saved files and game_* runtime calls. "
    "Inspect existing sources before editing; preserve named parts. No creative asset work before user-approved art direction. "
    + REVIEW_GUIDE
    + " Never claim staged changes are accepted or saved. "
    "Use focused tool searches, cache discovered schemas within the session, and write compact native scripts. "
    "Document operators automatically export, import and record provenance; precheck runs the same checks early without submitting; submit_for_review runs the final checks and opens the review. "
    "Avoid repeating those checks after every call. If the user asks to place an asset in their scene, create the scene instance too. "
    "Long tool results arrive as summaries with a details ref: read more with details only when you need it. "
    "Before making a new asset, find_recipe shows this project's closest accepted recipe to start from. "
    "To change part of a file, send find/replace patch pairs rather than the whole file. "
    "Never generate assets or fetch assets. Operate actual tools. A Rule Zero STOP halts the turn. "
    + GAME_GUIDE
)


PRIMER = "Earlier in this chat (summary):\n"

#: Editor-context keys that change on every turn (the editor's revision counter, live
#: instance ids). They go with the turn, after the project map (ADR 0056).
TURN_KEYS = ("scene_revision", "selection_instances")


def primed(prompt: Prompt) -> str:
    """The summary a fresh session starts from, as a block before the turn ("" without)."""
    return PRIMER + prompt.primer.strip() + "\n\n" if prompt.primer.strip() else ""


def _json(value: object) -> str:
    """One fixed spelling: the same value is always the same bytes."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def project_map(prompt: Prompt) -> str:
    """The project's ranked map (``joinery map``, the same bytes while its files are
    the same), then the editor context without its per-turn keys, its node list as an
    outline (ADR 0063): the same bytes while the scene on screen is unchanged."""
    stable = {k: v for k, v in prompt.editor_context.items() if k not in TURN_KEYS}
    summary = ""
    if prompt.project_dir:
        try:
            summary = cached_map(Path(prompt.project_dir))
        except OSError:
            summary = ""  # The map is context only; a turn never waits on it.
    return (
        (summary + "\n\n" if summary else "")
        + "Editor context:\n"
        + _json(editor_outline(stable))
        + "\n\n"
    )


def nearby(prompt: Prompt) -> str:
    """The files near the selection and the open scene (ADR 0080), as a line after
    the selection: per turn, so the stable map above keeps its cached bytes."""
    if not prompt.project_dir:
        return ""
    selected = [
        str(entry.get(key, ""))
        for entry in prompt.target_context
        for key in ("id", "resource", "source")
    ]
    selected.append(str(prompt.editor_context.get("scene_path", "")))
    line = near_line(Path(prompt.project_dir), [s for s in selected if s])
    return "\n" + line if line else ""


def turn_part(prompt: Prompt, images_inline: bool = False) -> str:
    """What only this turn has: the summary, the editor's per-turn keys, the
    selection and the files near it, the request and the attached images (listed
    by path unless the provider takes them inline)."""
    volatile = {
        k: prompt.editor_context[k] for k in TURN_KEYS if k in prompt.editor_context
    }
    return (
        primed(prompt)
        + "This turn:\n"
        + _json(volatile)
        + "\nSelection:\n"
        + _json(prompt.target_context)
        + nearby(prompt)
        + "\n\nUser:\n"
        + prompt.text
        + ("" if images_inline else listed(prompt.attachments))
    )


def project_memory(prompt: Prompt) -> str:
    """The project's JOURNEYMAN.md as a block (ADR 0077), "" without one: the same
    bytes until the user accepts a change to it."""
    if not prompt.project_dir:
        return ""
    found = memory.block(Path(prompt.project_dir))
    return found + "\n\n" if found else ""


def turn_text(prompt: Prompt, images_inline: bool = False) -> str:
    """The turn as every provider gets it, stable parts first: the role's rules
    (with the art bible and brief), the project memory, the project map, then the
    turn (ADR 0056, ADR 0077)."""
    rules = prompt.rules.strip()
    return (
        (rules + "\n\n" if rules else "")
        + project_memory(prompt)
        + project_map(prompt)
        + turn_part(prompt, images_inline)
    )


def prompt_text(
    prompt: Prompt, images_inline: bool = False, instructions: bool = True
) -> str:
    """The turn with Joinery's instructions ahead of it, for a provider without a
    system prompt of its own; ``instructions=False`` when the session already holds
    them."""
    return (INSTRUCTIONS + "\n\n" if instructions else "") + turn_text(
        prompt, images_inline
    )


async def require_provider(agent: str, project: Path) -> str:
    require_supported(agent)
    executable = shutil.which(agent)
    if executable is None:
        raise ValueError(f"{get(agent).name} CLI is not installed.")
    data = await asyncio.to_thread(status, agent)
    if not data["connected"]:
        raise ValueError(f"Sign in with {get(agent).name} before connecting this chat.")
    gate = project / get(agent).config_path
    valid_gate = False
    if gate.is_file() and agent in {"claude", "gemini"}:
        try:
            groups = (
                json.loads(gate.read_text())
                .get("hooks", {})
                .get(get(agent).pre_tool_event, [])
            )
            expected = _command_str(str(ROOT), agent)
            valid_gate = any(
                hook.get("type") == "command" and hook.get("command") == expected
                for group in groups
                for hook in group.get("hooks", [])
            )
        except (ValueError, TypeError, AttributeError):
            valid_gate = False
    elif gate.is_file() and agent == "opencode":
        text = gate.read_text()
        valid_gate = (
            "tool.execute.before" in text
            and json.dumps(
                [
                    "uv",
                    "run",
                    "--frozen",
                    "--quiet",
                    "--project",
                    str(ROOT),
                    "joinery",
                    "hook",
                    "--agent",
                    "opencode",
                ]
            )
            in text
        )
    if not valid_gate:
        raise ValueError(
            "The shared project policy hook is missing. Run Joinery project setup first."
        )
    return executable


def readonly_command(argv: list[str], project: Path) -> list[str]:
    """The API process may use its credential store, but cannot save game files."""
    if sys.platform == "win32":
        raise ValueError(WINDOWS_BOUNDARY_REASON)
    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
        profile = (
            "(version 1)(allow default)(deny file-write* (subpath "
            + json.dumps(str(project.resolve()))
            + "))"
        )
        return ["/usr/bin/sandbox-exec", "-p", profile, *argv]
    bwrap = shutil.which("bwrap")
    if bwrap:
        return [
            bwrap,
            "--die-with-parent",
            "--bind",
            "/",
            "/",
            "--ro-bind",
            str(project.resolve()),
            str(project.resolve()),
            "--",
            *argv,
        ]
    raise ValueError(
        "This provider needs the read-only project boundary (sandbox-exec on macOS or bubblewrap on Linux)."
    )


def offered_role(role: str, inspection_only: bool) -> str | None:
    """The role whose tools the proxy lists: the session's role, the lead when it has
    none, and None (every tool, judged per call) for an inspection specialist."""
    if role:
        return role
    return None if inspection_only else "lead"


def proxy_args(socket: Path, role: str | None) -> list[str]:
    """How the joinery-registry proxy runs for one agent (``python <args>``)."""
    args = ["-m", "servers.joinery_registry.proxy", "--socket", str(socket)]
    return args + ["--role", role] if role else args


def proxy_config(socket: Path, role: str | None = None) -> dict[str, Any]:
    return {
        "command": sys.executable,
        "args": proxy_args(socket, role),
        "env": {"PYTHONPATH": str(ROOT)},
    }


async def stop_process(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    if sys.platform == "win32":
        from joinery.windows_process import WindowsProcessTree

        tree = await asyncio.to_thread(WindowsProcessTree, process.pid)
        waiting = False
        # Stop only the tree rooted at this owned child. This also handles a
        # provider launched by a user-installed CLI wrapper without orphaning it.
        try:
            cleanup = await asyncio.create_subprocess_exec(
                "taskkill.exe",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(cleanup.wait(), 5)
            except TimeoutError:
                cleanup.kill()
                await cleanup.wait()
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await process.wait()
            # taskkill returns after requesting child termination. The Node
            # wrapper can exit before its native provider has released files.
            # Busy machines (shared CI runners) can take more than 5s here.
            waiter = asyncio.create_task(asyncio.to_thread(tree.wait_and_close, 15))
            waiting = True
            try:
                await asyncio.shield(waiter)
            except asyncio.CancelledError:
                await waiter
                raise
        finally:
            if not waiting:
                tree.close()
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), 5)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
