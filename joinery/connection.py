"""Local agent sign-in. Credentials stay with the provider's CLI."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from joinery.agents import all_agents, get
from joinery.api_profiles import list_public
from joinery.platform_support import launch_argv

KEY_VARS = {
    "claude": ("ANTHROPIC_API_KEY",),
    "codex": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "opencode": ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"),
}
LOGIN_ARGS = {
    "claude": ("auth", "login"),
    "codex": ("login",),
    "gemini": (),
    "opencode": (),  # Provider selection happens inside its /connect flow.
}
INSTALL_URLS = {
    "claude": "https://code.claude.com/docs/en/setup",
    "codex": "https://developers.openai.com/codex/cli",
    "gemini": "https://geminicli.com/docs/get-started/installation/",
    "opencode": "https://opencode.ai/docs/",
}


def _probe(argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            launch_argv(argv[0], argv[1:]), capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 1, ""
    # Never return this text to the UI: a provider may include account details.
    return result.returncode, result.stdout + result.stderr


def status(agent_id: str) -> dict[str, Any]:
    agent = get(agent_id)
    executable = shutil.which(agent_id)
    key_set = any(os.environ.get(name) for name in KEY_VARS[agent_id])
    connected = False
    method = ""
    if executable and agent_id == "codex":
        code, text = _probe([executable, "login", "status"])
        connected = code == 0 and "logged in" in text.lower()
        method = "ChatGPT account" if "chatgpt" in text.lower() else "API key"
    elif executable and agent_id == "claude":
        code, text = _probe([executable, "auth", "status", "--json"])
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = {}
        if isinstance(data, dict):
            connected = code == 0 and data.get("loggedIn") is True
            method = (
                "API key" if data.get("authMethod") == "api_key" else "Claude account"
            )
    elif executable and agent_id == "gemini":
        # This detects local configuration; it never makes a billable model call.
        path = Path.home() / ".gemini" / "oauth_creds.json"
        try:
            credentials = json.loads(path.read_text())
        except (OSError, ValueError):
            credentials = None
        connected = isinstance(credentials, dict) and bool(
            credentials.get("refresh_token") or credentials.get("access_token")
        )
        method = "Saved Google sign-in"
    elif executable and agent_id == "opencode":
        code, text = _probe([executable, "auth", "list"])
        plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
        connected = code == 0 and bool(
            re.search(r"[1-9]\d* credentials?", plain, re.IGNORECASE)
        )
        method = "Saved provider sign-in"
    if executable and key_set and agent_id != "codex" and not connected:
        connected, method = True, "API key configured"
    return {
        "id": agent_id,
        "name": agent.name,
        "installed": executable is not None,
        "connected": connected,
        "method": method if connected else "",
        "api_key_configured": key_set,
        "requires_key_confirmation": method == "API key configured",
        "key_vars": list(KEY_VARS[agent_id]),
        "install_url": INSTALL_URLS[agent_id],
    }


def all_statuses() -> dict[str, Any]:
    result: dict[str, Any] = {"agents": [status(agent.id) for agent in all_agents()]}
    try:
        result["profiles"] = list_public()
    except ValueError:
        result["profiles"] = []
        result["profiles_error"] = "Could not read saved API connections."
    return result


def login(agent_id: str) -> dict[str, Any]:
    get(agent_id)
    executable = shutil.which(agent_id)
    if executable is None:
        raise ValueError("Install this agent's CLI first.")
    argv = launch_argv(executable, list(LOGIN_ARGS[agent_id]))
    # Interactive provider-owned login needs a terminal. No key goes in argv.
    if sys.platform == "darwin":
        descriptor, filename = tempfile.mkstemp(
            prefix="joinery-sign-in-", suffix=".command"
        )
        script_path = Path(filename)
        script_path.write_text(
            "#!/bin/sh\n"
            f"cd {shlex.quote(tempfile.gettempdir())}\n"
            f"{shlex.join(argv)}\n"
            "result=$?\n"
            f"rm -- {shlex.quote(filename)}\n"
            'exit "$result"\n'
        )
        os.close(descriptor)
        script_path.chmod(0o700)
        result = subprocess.run(
            ["/usr/bin/open", "-a", "Terminal", filename],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            script_path.unlink(missing_ok=True)
            raise ValueError("Could not open Terminal for the provider's sign-in.")
    elif sys.platform == "win32":
        subprocess.Popen(
            argv,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
        )
    else:
        terminal = next(
            (
                path
                for name in ("x-terminal-emulator", "gnome-terminal", "konsole")
                if (path := shutil.which(name))
            ),
            None,
        )
        if terminal is None:
            raise ValueError(
                f"Run {shlex.join(argv)} in a terminal, then check the connection again."
            )
        separator = "--" if Path(terminal).name == "gnome-terminal" else "-e"
        subprocess.Popen([terminal, separator, *argv])
    return {
        "started": True,
        "message": (
            "Use /connect in opencode to choose a provider, then return here."
            if agent_id == "opencode"
            else "Finish signing in with your agent, then return here."
        ),
    }


def use_key(agent_id: str) -> dict[str, Any]:
    get(agent_id)
    executable = shutil.which(agent_id)
    key = next(
        (os.environ[name] for name in KEY_VARS[agent_id] if os.environ.get(name)), ""
    )
    if executable is None or not key:
        raise ValueError("Configure the agent's API key in your environment first.")
    if agent_id == "codex":
        try:
            result = subprocess.run(
                launch_argv(executable, ["login", "--with-api-key"]),
                input=key,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("The agent could not save its API-key login.") from exc
        if result.returncode:
            raise ValueError("The agent did not accept its API-key login.")
    return {"started": True, "message": "Using the API key configured for this agent."}
