"""Small native host adapters; unsupported protections fail before doing work."""

from __future__ import annotations

import base64
import errno
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import BinaryIO


def lock_file(stream: BinaryIO) -> None:
    """Hold an exclusive OS lock until release or process death.

    Allow a short Windows lock handoff after process termination. Every attempt
    remains nonblocking; a live owner's lock is never bypassed or replaced.
    """
    if sys.platform == "win32":
        msvcrt = importlib.import_module("msvcrt")

        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        deadline = time.monotonic() + 1.0
        while True:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise BlockingIOError("The project lock is already held.") from exc
                time.sleep(0.01)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock_file(stream: BinaryIO) -> None:
    if sys.platform == "win32":
        msvcrt = importlib.import_module("msvcrt")

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def private_directory(directory: Path) -> None:
    """Protect IPC secrets from other local users, including on native Windows."""
    if os.name != "nt":
        directory.chmod(0o700)
        return
    _private_windows_acl(directory, "(OI)(CI)F")


def private_file(path: Path) -> None:
    """Protect an empty temporary file before writing credentials into it."""
    if os.name != "nt":
        path.chmod(0o600)
        return
    _private_windows_acl(path, "F")


def _private_windows_acl(path: Path, access: str) -> None:
    # chmod alone cannot restrict a Windows DACL. The directory is empty until
    # this succeeds, so no endpoint secret exists under inherited permissions.
    identity = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        check=False,
        timeout=10,
    )
    sid = re.search(rb"\bS-1-\d+(?:-\d+)+\b", identity.stdout)
    if identity.returncode or sid is None:
        raise ValueError(
            "Could not identify the current Windows account for private storage."
        )
    # Reset any explicit defaults, then remove inheritance and grant only the
    # owning account and SYSTEM. The directory remains empty throughout this.
    for permissions in (
        ["/reset"],
        [
            "/inheritance:r",
            "/grant:r",
            "*" + sid.group().decode("ascii") + ":" + access,
            "*S-1-5-18:" + access,
        ],
    ):
        result = subprocess.run(
            ["icacls.exe", str(path), *permissions],
            capture_output=True,
            check=False,
            timeout=10,
        )
        if result.returncode:
            raise ValueError(
                "Could not protect private Windows storage; refusing to expose it."
            )


def command_targets_project(command: str, project: Path) -> bool:
    """Match absolute editor paths, including paths containing spaces."""
    path = str(project.resolve())
    if os.name == "nt":
        command = command.replace("\\", "/").lower()
        path = path.replace("\\", "/").lower()
    return bool(
        re.search(
            r"(?:^|\s)--path(?:=|\s+)[\"']?" + re.escape(path) + r"[\"']?(?=\s|$)",
            command,
        )
        or re.search(
            r"(?:^|\s)[\"']?" + re.escape(path + "/project.godot") + r"[\"']?(?=\s|$)",
            command,
        )
    )


def editor_running(project: Path) -> bool:
    """Inspect only relevant processes; never print a process command line."""
    if os.name == "nt":
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if powershell is None:
            raise ValueError(
                "PowerShell is required to confirm the editor is closed before updating."
            )
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); Get-CimInstance Win32_Process -Filter \"Name LIKE 'godot%' OR Name = 'Joinery.exe'\" | Select-Object ExecutablePath,CommandLine | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=15,
        )
        if result.returncode:
            raise ValueError(
                "Could not confirm the editor is closed; addon update was stopped."
            )
        if not result.stdout.strip():
            return False
        try:
            rows = json.loads(result.stdout)
        except ValueError:
            raise ValueError(
                "Could not read Windows editor status; addon update was stopped."
            ) from None
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise ValueError(
                "Could not read Windows editor status; addon update was stopped."
            )
        return any(
            isinstance(row, dict)
            and (
                row.get("CommandLine") is None
                or command_targets_project(str(row["CommandLine"]), project)
            )
            for row in rows
        )
    commands = subprocess.run(
        ["ps", "-A", "-o", "pid=,comm="], capture_output=True, text=True, check=False
    )
    arguments = subprocess.run(
        ["ps", "-A", "-o", "pid=,args="], capture_output=True, text=True, check=False
    )
    if commands.returncode or arguments.returncode:
        raise ValueError(
            "Could not confirm the editor is closed; addon update was stopped."
        )
    editors = set()
    for line in commands.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and any(
            name in Path(parts[1]).name.lower() for name in ("godot", "joinery")
        ):
            editors.add(parts[0])
    for line in arguments.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if (
            len(parts) == 2
            and parts[0] in editors
            and command_targets_project(parts[1], project)
        ):
            return True
    return False


def hook_command(argv: list[str]) -> str:
    """Encode a literal PowerShell argument vector, including hostile path text.

    This is only used for our fixed, locally generated hook command. Encoding
    avoids a second cmd.exe expansion of %, &, quotes or parentheses in paths.
    No credential or model-provided command is placed in this launcher.
    """
    values = [
        "$env:CLAUDE_PROJECT_DIR"
        if part == "${CLAUDE_PROJECT_DIR}"
        else "'" + part.replace("'", "''") + "'"
        for part in argv
    ]
    # Windows PowerShell 5.1's native argument conversion loses embedded quotes.
    # ProcessStartInfo bypasses that conversion; use the Windows CRT quoting
    # rules explicitly, including a dynamic CLAUDE_PROJECT_DIR environment value.
    script = (
        r"""$ErrorActionPreference='Stop';
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);
[Console]::InputEncoding=[System.Text.UTF8Encoding]::new($false);
function JoineryQuote([string]$value) {
    $quoted=[regex]::Replace($value, '(\\*)"', '${1}${1}\"');
    $quoted=[regex]::Replace($quoted, '(\\+)$', '${1}${1}');
    return '"' + $quoted + '"';
};
$payload=[Console]::In.ReadToEnd();
$info=New-Object System.Diagnostics.ProcessStartInfo;
$info.UseShellExecute=$false; $info.RedirectStandardInput=$true;
$info.EnvironmentVariables['PYTHONUTF8']='1';
"""
        + "$info.FileName=" + values[0] + ";\n"
        + "$info.Arguments=((@( " + ", ".join(values[1:])
        + " ) | ForEach-Object { JoineryQuote $_ }) -join ' ');\n"
        + "$process=New-Object System.Diagnostics.Process; $process.StartInfo=$info;\n"
        + "[void]$process.Start(); $process.StandardInput.Write($payload); $process.StandardInput.Close();\n"
        + "$process.WaitForExit(); exit $process.ExitCode"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    return "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded


def bash_script_argv(argv: list[str]) -> list[str]:
    """Run the offline image build driver through Git Bash on native Windows."""
    if os.name != "nt" or not argv[0].endswith(".sh"):
        return argv
    candidates = [
        Path(root) / "Git/bin/bash.exe"
        for name in ("ProgramFiles", "ProgramFiles(x86)")
        if (root := os.environ.get(name))
    ]
    shell = next((str(path) for path in candidates if path.is_file()), None)
    if shell is None:
        shell = shutil.which("bash.exe")
    if shell is None or "windows/system32" in shell.replace("\\", "/").lower():
        raise ValueError(
            "Install Git for Windows with Git Bash before building offline workers."
        )
    return [shell, argv[0].replace("\\", "/"), *argv[1:]]


def npm_argv(argv: list[str]) -> list[str]:
    """Resolve the supported Windows npm layout without interpreting a shim."""
    if os.name != "nt" or argv[0].lower() not in {"npm", "npm.cmd"}:
        return argv
    return windows_npm_argv(argv)


def windows_npm_argv(argv: list[str]) -> list[str]:
    """Invoke Node's adjacent npm-cli.js directly, preserving literal arguments."""
    executable = shutil.which(argv[0])
    if executable is None:
        raise ValueError("Install Node.js with npm before installing an MCP server.")
    directory = Path(executable).resolve().parent
    adjacent_node = directory / "node.exe"
    node = str(adjacent_node) if adjacent_node.is_file() else shutil.which("node.exe")
    cli = directory / "node_modules/npm/bin/npm-cli.js"
    if node is None or not cli.is_file():
        raise ValueError(
            "Unsupported Windows npm shim layout. Install the official Node.js distribution with adjacent node_modules/npm/bin/npm-cli.js."
        )
    return [node, str(cli), *argv[1:]]


def launch_argv(executable: str, arguments: list[str]) -> list[str]:
    """Launch native/Python adapters and the known Windows Codex npm layout.

    Never pass a .cmd/.bat script to CreateProcess: Windows can interpret it
    through a shell, changing argument semantics or exposing serialized config.
    """
    path = Path(executable)
    if path.suffix.lower() == ".py":
        return [sys.executable, executable, *arguments]
    if sys.platform == "win32" and path.suffix.lower() in {".cmd", ".bat"}:
        return windows_provider_argv(path, arguments)
    return [executable, *arguments]


def windows_provider_argv(shim: Path, arguments: list[str]) -> list[str]:
    """Use Codex's published npm entrypoint, without parsing/executing its shim."""
    root = shim.resolve().parent
    node = root / "node.exe"
    executable = str(node) if node.is_file() else shutil.which("node.exe")
    cli = root / "node_modules/@openai/codex/bin/codex.js"
    if shim.stem.lower() != "codex" or executable is None or not cli.is_file():
        raise ValueError(
            "Unsupported Windows provider shim. Install the native Codex executable or install @openai/codex using npm with Node.js on PATH."
        )
    return [executable, str(cli), *arguments]
