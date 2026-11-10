"""Local agent sign-in. Credentials stay with the provider's CLI."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from joinery.agents import all_agents, get
from joinery.api_profiles import list_public
from joinery.platform_support import launch_argv
from joinery.provider_support import require_supported, usability

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
            launch_argv(argv[0], argv[1:]),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 1, ""
    # Never return this text to the UI: a provider may include account details.
    return result.returncode, result.stdout + result.stderr


def status(agent_id: str) -> dict[str, Any]:
    agent = get(agent_id)
    support = usability(agent_id)
    executable = shutil.which(agent_id)
    key_set = any(os.environ.get(name) for name in KEY_VARS[agent_id])
    connected = False
    method = ""
    if not support["supported"]:
        pass  # Do not probe unusable providers or their credentials.
    elif executable and agent_id == "codex":
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
    if (
        support["supported"]
        and executable
        and key_set
        and agent_id != "codex"
        and not connected
    ):
        connected, method = True, "API key configured"
    check = _verification(agent_id)
    # A passed test message counts only for the sign-in it was made with.
    verified = bool(
        connected
        and check is not None
        and check.get("state") == "ok"
        and check.get("method") == method
    )
    return {
        "id": agent_id,
        "name": agent.name,
        "installed": executable is not None,
        "connected": connected,
        "authenticated": connected if support["supported"] else None,
        **support,
        "method": method if connected else "",
        "api_key_configured": key_set,
        "requires_key_confirmation": method == "API key configured",
        "key_vars": list(KEY_VARS[agent_id]),
        "install_url": INSTALL_URLS[agent_id],
        "verified": verified,
        "verification": _public(check),
        "summary": _summary(
            agent_id,
            installed=executable is not None,
            connected=connected,
            method=method,
            verified=verified,
            reason=support["reason"],
        ),
    }


# --- Readiness wording and the test-message check ----------------------------

#: Short names for status lines ("Claude: signed in (Claude account)").
SHORT_NAMES = {"claude": "Claude", "codex": "Codex"}
#: What a verification asks. Tiny, and it needs no tools.
VERIFY_PROMPT = (
    "Joinery connection check. Reply with the single word OK and nothing else. "
    "Do not use any tools."
)
VERIFY_TIMEOUT = 30
#: Starting the bridge (project gateway, provider process) gets its own budget.
CONNECT_TIMEOUT = 90
VerifyState = Literal["ok", "quota", "auth", "error", "not-installed", "unsupported"]
_QUOTA = re.compile(
    r"quota|rate[ _-]?limit|usage limit|too many requests|\b429\b|credit balance|"
    r"insufficient[ _]funds|billing",
    re.IGNORECASE,
)
_AUTH = re.compile(
    r"\b401\b|\b403\b|unauthori[sz]ed|forbidden|not (?:logged|signed) in|"
    r"log ?in again|sign ?in|authenticat|invalid api key|expired (?:token|session)",
    re.IGNORECASE,
)


def short_name(agent_id: str) -> str:
    return SHORT_NAMES.get(agent_id, get(agent_id).name)


def _summary(
    agent_id: str,
    *,
    installed: bool,
    connected: bool,
    method: str,
    verified: bool,
    reason: str,
) -> str:
    name = short_name(agent_id)
    if reason:
        return f"{name}: {reason}"
    if not installed:
        return f"{name}: not installed: {INSTALL_URLS[agent_id]}"
    if not connected:
        return f"{name}: installed, not signed in"
    return f"{name}: signed in ({method})" + (", verified" if verified else "")


def checks_path() -> Path:
    """Where test-message results are kept: states and times, never provider text."""
    root = os.environ.get("JOINERY_READINESS_CACHE")
    base = Path(root) if root else Path.home() / ".cache/joinery"
    return base / "agent-checks.json"


def _load_checks() -> dict[str, Any]:
    try:
        data = json.loads(checks_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _verification(agent_id: str) -> dict[str, Any] | None:
    entry = _load_checks().get(agent_id)
    return entry if isinstance(entry, dict) else None


def _public(check: dict[str, Any] | None) -> dict[str, str] | None:
    if check is None:
        return None
    return {key: str(check.get(key, "")) for key in ("state", "message", "checked_at")}


def _save_check(agent_id: str, record: dict[str, str]) -> None:
    path = checks_path()
    data = _load_checks()
    data[agent_id] = record
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(
            prefix=".agent-checks-", dir=path.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(data, indent=1, sort_keys=True) + "\n")
        os.replace(filename, path)
    except OSError:
        pass


@dataclass(frozen=True)
class TurnResult:
    """What one test message through the bridge produced.

    `text` is used only to classify the outcome; it is never stored or shown,
    because a provider may put account details in its messages.
    """

    connected: bool
    done: bool
    ok: bool
    text: str = ""
    timed_out: bool = False


Turn = Callable[[str, Path, float], TurnResult]


def classify(result: TurnResult) -> VerifyState:
    if result.ok and result.done:
        return "ok"
    if _QUOTA.search(result.text):
        return "quota"
    if _AUTH.search(result.text):
        return "auth"
    return "error"


def _message(agent_id: str, state: VerifyState, result: TurnResult | None) -> str:
    name = short_name(agent_id)
    if state == "ok":
        return f"{name}: verified. It answered a test message."
    if state == "quota":
        return f"{name}: signed in, but the account is out of quota or rate limited."
    if state == "auth":
        return f"{name}: the provider refused the sign-in. Sign in again."
    if state == "not-installed":
        return f"{name}: not installed: {INSTALL_URLS[agent_id]}"
    if result is not None and result.timed_out:
        return f"{name}: no answer to the test message in time."
    if result is not None and not result.connected:
        if "policy hook" in result.text:
            return f"{name}: the project's policy hook is not set up. Run Joinery project setup."
        return f"{name}: the chat bridge could not start."
    return f"{name}: the test message failed."


def verify(
    agent_id: str,
    project: Path,
    *,
    timeout: float = VERIFY_TIMEOUT,
    turn: Turn | None = None,
) -> dict[str, Any]:
    """Send one tiny, tool-free message through the real chat bridge.

    "Connected" only means credentials were found; this tells it apart from an
    account that can actually answer. The result (ok, quota, auth or error) is
    recorded so `status` can show a "verified" badge. This makes one small model
    call, so it runs only when someone asks for it.
    """
    current = status(agent_id)
    result: TurnResult | None = None
    state: VerifyState
    if not current["supported"]:
        state = "unsupported"
        message = f"{short_name(agent_id)}: {current['reason']}"
    elif not current["installed"]:
        state = "not-installed"
        message = _message(agent_id, state, None)
    elif not current["connected"]:
        state = "auth"
        message = f"{short_name(agent_id)}: installed, not signed in. Sign in first."
    else:
        result = (turn or bridge_turn)(agent_id, project.resolve(), timeout)
        state = classify(result)
        message = _message(agent_id, state, result)
    record = {
        "state": state,
        "message": message,
        "method": current["method"],
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _save_check(agent_id, record)
    return {
        "id": agent_id,
        "name": current["name"],
        "state": state,
        "verified": state == "ok",
        "message": message,
        "checked_at": record["checked_at"],
    }


def bridge_turn(agent_id: str, project: Path, timeout: float) -> TurnResult:
    """Run `joinery chat` (the editor's own bridge) and send one test message."""
    return asyncio.run(_bridge_turn(agent_id, project, timeout))


def chat_command(agent_id: str, project: Path) -> list[str]:
    """The same `joinery chat` the editor starts for its sidebar."""
    return [
        sys.executable,
        "-c",
        "import sys; from joinery.cli import main; sys.exit(main())",
        "chat",
        "--project",
        str(project),
        "--agent",
        agent_id,
        "--addon-protocol",
        "2",
    ]


async def _bridge_turn(agent_id: str, project: Path, timeout: float) -> TurnResult:
    from joinery.catalog import ROOT

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(ROOT), env.get("PYTHONPATH", "")) if p
    )
    process = await asyncio.create_subprocess_exec(
        *chat_command(agent_id, project),
        cwd=ROOT,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=os.name != "nt",
        limit=16_000_000,
    )
    assert process.stdin is not None and process.stdout is not None
    texts: list[str] = []
    connected = False

    async def next_event() -> dict[str, Any] | None:
        assert process.stdout is not None
        while line := await process.stdout.readline():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                return event
        return None

    try:
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                while (event := await next_event()) is not None:
                    if event.get("kind") != "connection":
                        continue
                    if event.get("state") == "connected":
                        connected = True
                        break
                    return TurnResult(
                        False, False, False, str(event.get("message", ""))
                    )
        except TimeoutError:
            return TurnResult(False, False, False, timed_out=True)
        if not connected:
            return TurnResult(False, False, False)
        prompt = {"kind": "prompt", "text": VERIFY_PROMPT}
        process.stdin.write((json.dumps(prompt) + "\n").encode())
        await process.stdin.drain()
        try:
            async with asyncio.timeout(timeout):
                while (event := await next_event()) is not None:
                    kind = event.get("kind")
                    if kind in {"message", "message_delta"}:
                        texts.append(str(event.get("text", "")))
                    elif kind == "connection" and event.get("state") == "error":
                        texts.append(str(event.get("message", "")))
                        break
                    elif kind == "done":
                        return TurnResult(
                            True, True, bool(event.get("ok")), "".join(texts)
                        )
        except TimeoutError:
            return TurnResult(True, False, False, "".join(texts), timed_out=True)
        return TurnResult(True, False, False, "".join(texts))
    finally:
        with contextlib.suppress(OSError, RuntimeError):
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 10)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()


def all_statuses() -> dict[str, Any]:
    result: dict[str, Any] = {"agents": [status(agent.id) for agent in all_agents()]}
    try:
        result["profiles"] = list_public()
    except ValueError:
        result["profiles"] = []
        result["profiles_error"] = "Could not read saved API connections."
    return result


def login(agent_id: str) -> dict[str, Any]:
    require_supported(agent_id)
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
    require_supported(agent_id)
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
