"""Check a pinned installed Codex executable without authentication or inference.

Run from CI after installing @openai/codex@0.167.0. Each process receives a fresh
home/config directory and an allowlisted environment. Only initialize and the
initialized notification are sent; no account, thread, turn or model API is used.
Both normal stdin EOF and production process cancellation must stop the server.

Protocol: https://learn.chatgpt.com/docs/app-server#initialization
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import tempfile
from pathlib import Path
from typing import Any

from bridge.provider_process import stop_process
from joinery.platform_support import launch_argv

CODEX_VERSION = "0.167.0"


def isolated_environment(directory: Path) -> dict[str, str]:
    """Keep executable discovery/Windows startup variables, never provider secrets."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC"}
    }
    paths = {
        "HOME": directory / "home",
        "USERPROFILE": directory / "home",
        "CODEX_HOME": directory / "codex",
        "APPDATA": directory / "appdata",
        "LOCALAPPDATA": directory / "localappdata",
        "XDG_CONFIG_HOME": directory / "config",
        "XDG_CACHE_HOME": directory / "cache",
        "XDG_DATA_HOME": directory / "data",
        "TMP": directory / "temp",
        "TEMP": directory / "temp",
        "TMPDIR": directory / "temp",
    }
    for key, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        environment[key] = str(path)
    environment["PYTHONUTF8"] = "1"
    return environment


async def launch(
    executable: str, arguments: list[str], directory: Path
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *launch_argv(executable, arguments),
        cwd=directory,
        env=isolated_environment(directory),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name != "nt",
        limit=1024 * 1024,
    )


async def cleanup(process: asyncio.subprocess.Process) -> None:
    try:
        await asyncio.wait_for(stop_process(process), 12)
    finally:
        if process.returncode is None:
            process.kill()
            await asyncio.wait_for(process.wait(), 3)


async def drain(stream: asyncio.StreamReader) -> None:
    # Discard diagnostics instead of treating them as protocol or retaining
    # potentially unbounded output. The smoke report contains no raw logs.
    while await stream.read(65536):
        pass


async def handshake(executable: str, directory: Path, mode: str) -> dict[str, Any]:
    directory.mkdir()
    process = await launch(
        executable,
        [
            "-c",
            'cli_auth_credentials_store="file"',
            "-c",
            "analytics.enabled=false",
            "-c",
            "feedback.enabled=false",
            "app-server",
            "--listen",
            "stdio://",
        ],
        directory,
    )
    assert process.stdin is not None
    assert process.stdout is not None and process.stderr is not None
    readers = [asyncio.create_task(drain(process.stderr))]
    try:
        request = {
            "id": 0,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "joinery", "version": "0.1.0-alpha.1"},
                "capabilities": {"experimentalApi": True},
            },
        }
        process.stdin.write(json.dumps(request).encode() + b"\n")
        await asyncio.wait_for(process.stdin.drain(), 5)

        async def initialized() -> dict[str, Any]:
            assert process.stdout is not None
            while line := await process.stdout.readline():
                message = json.loads(line)
                if message.get("id") != 0:
                    continue
                result = message.get("result")
                if "error" in message or not isinstance(result, dict):
                    raise RuntimeError("Codex rejected the initialize handshake")
                if (
                    not isinstance(result.get("userAgent"), str)
                    or not result["userAgent"]
                ):
                    raise RuntimeError("Codex initialize response lacks its user agent")
                return result
            raise RuntimeError("Codex exited before completing initialize")

        result = await asyncio.wait_for(initialized(), 20)
        if (
            result.get("codexHome")
            and Path(result["codexHome"]).resolve() != (directory / "codex").resolve()
        ):
            raise RuntimeError("Codex did not use the isolated smoke-test home")
        process.stdin.write(b'{"method":"initialized","params":{}}\n')
        await asyncio.wait_for(process.stdin.drain(), 5)
        readers.append(asyncio.create_task(drain(process.stdout)))
        if mode == "eof":
            process.stdin.close()
            await asyncio.wait_for(process.wait(), 12)
            if process.returncode != 0:
                raise RuntimeError("Codex did not exit cleanly after stdin EOF")
        else:
            if process.returncode is not None:
                raise RuntimeError("Codex exited before the cancellation check")
            await asyncio.wait_for(stop_process(process), 12)
            if process.returncode is None:
                raise RuntimeError("Production cancellation left Codex running")
        return {
            "handshake": "initialize/initialized",
            "response_fields": sorted(result),
            "shutdown": mode,
            "exit_code": process.returncode,
        }
    finally:
        await cleanup(process)
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


async def smoke(
    executable: str, expected_version: str = CODEX_VERSION
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="joinery-native-provider-") as temporary:
        directory = Path(temporary)
        process = await launch(executable, ["--version"], directory)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 12)
            version = output.decode("utf-8", errors="replace").strip()
            if process.returncode or version != "codex-cli " + expected_version:
                raise RuntimeError(
                    f"Expected codex-cli {expected_version}; installed version differs"
                )
        finally:
            await cleanup(process)
        cases = []
        for mode in ("eof", "cancel"):
            cases.append(await handshake(executable, directory / mode, mode))
    return {
        "provider": "codex",
        "version": version,
        "platform": platform.system(),
        "architecture": platform.machine(),
        "authentication": "not requested; isolated empty credential store",
        "inference": "not requested",
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex", help="Installed CLI path; defaults to PATH discovery"
    )
    parser.add_argument("--expected-version", default=CODEX_VERSION)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    executable = args.codex or shutil.which("codex")
    if executable is None:
        parser.error("Install the pinned Codex CLI before running this smoke test")
    report = asyncio.run(smoke(executable, args.expected_version))
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
