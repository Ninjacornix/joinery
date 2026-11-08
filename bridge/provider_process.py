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

from bridge.protocol import Prompt
from joinery.agents import get
from joinery.catalog import ROOT
from joinery.connection import status
from joinery.wiring import _command_str

INSTRUCTIONS = (
    "You operate Joinery's current Godot project. Use the connected joinery-registry MCP gateway: "
    "inspect_editor for live scene identities, edit_scene for reversible live node changes, and "
    "discover_tools/tool_schema/call_tool for native creative apps and staged saved-file edits. "
    "Do not give manual editor steps when a connected tool can do the requested work. "
    "Raw project files are read-only in this agent process. The gateway owns mutations and user review. "
    "Keep unsaved live scenes separate from saved files and game_* runtime calls. "
    "Inspect existing sources before editing; preserve named parts. No creative asset work before user-approved art direction. "
    "To approve art direction the user types /art and clicks Approve this version; accepting a saved draft alone is not approval. "
    "Never claim staged changes are accepted or saved. The user accepts or rejects in the Review panel below the viewport. "
    "Use focused tool searches, cache discovered schemas within the session, and write compact native scripts. "
    "Document operators automatically export, import and record provenance; final validation and visual review happen at turn completion. "
    "Avoid repeating those checks after every call. If the user asks to place an asset in their scene, create the scene instance too. "
    "Never generate assets or fetch assets. Operate actual tools. A Rule Zero STOP halts the turn."
)


def prompt_text(prompt: Prompt) -> str:
    return (
        INSTRUCTIONS
        + "\n\nEditor context:\n"
        + json.dumps(prompt.editor_context)
        + "\nSelection:\n"
        + json.dumps(prompt.target_context)
        + "\n\nUser:\n"
        + prompt.text
    )


async def require_provider(agent: str, project: Path) -> str:
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
        raise ValueError(
            "This provider does not yet have a native Windows read-only host boundary. Use Codex on Windows, or run this provider in a supported Linux/macOS host."
        )
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


def proxy_config(socket: Path) -> dict[str, Any]:
    return {
        "command": sys.executable,
        "args": ["-m", "servers.joinery_registry.proxy", "--socket", str(socket)],
        "env": {"PYTHONPATH": str(ROOT)},
    }


async def stop_process(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    if os.name == "nt":
        # Stop only the tree rooted at this owned child. This also handles a
        # provider launched by a user-installed CLI wrapper without orphaning it.
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
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), 5)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
