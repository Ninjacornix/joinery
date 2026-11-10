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
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from bridge.provider_process import stop_process
from joinery import __version__
from joinery.platform_support import launch_argv

CODEX_VERSION = "0.167.0"


class IsolatedHomeCleanupError(RuntimeError):
    def __init__(self, diagnostics: dict[str, Any]) -> None:
        super().__init__("The isolated provider home remained locked after shutdown")
        self.diagnostics = diagnostics


def remove_isolated_home(
    directory: Path, *, lifecycle_complete: bool, process_ids: list[int]
) -> dict[str, Any]:
    """Wait only for Windows' observed post-exit directory sharing handoff.

    Provider initialization, process-handle waits and output EOF are never
    retried. The directory-release allowance applies only after all those gates
    passed, and only to the native sharing-violation error (WinError 32).
    """
    deadline = time.monotonic() + 2.0
    sharing_violations = 0
    while True:
        try:
            shutil.rmtree(directory)
            break
        except OSError as exc:
            if not (
                sys.platform == "win32"
                and lifecycle_complete
                and getattr(exc, "winerror", None) == 32
            ):
                raise
            sharing_violations += 1
            if time.monotonic() >= deadline:
                from release.windows_lock_diagnostics import diagnose_locks

                resource = Path(exc.filename) if exc.filename else directory
                if not resource.is_relative_to(directory):
                    resource = directory
                raise IsolatedHomeCleanupError(
                    diagnose_locks(resource, process_ids)
                ) from exc
            time.sleep(0.025)
    if directory.exists():
        raise RuntimeError("The isolated provider home was not removed")
    return {
        "directory_removed": True,
        "sharing_violations": sharing_violations,
        "release_deadline_seconds": 2.0,
    }


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
                "clientInfo": {"name": "joinery", "version": __version__},
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
            "process_id": process.pid,
        }
    finally:
        try:
            await cleanup(process)
            # A wrapper exiting is insufficient: an orphaned native child can
            # retain inherited pipes. Require real EOF before deleting its home.
            try:
                await asyncio.wait_for(asyncio.gather(*readers), 5)
            except TimeoutError as exc:
                raise RuntimeError(
                    "Provider output pipes remained open after process shutdown"
                ) from exc
        finally:
            for task in readers:
                task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)


async def smoke(
    executable: str, expected_version: str = CODEX_VERSION
) -> dict[str, Any]:
    directory = Path(tempfile.mkdtemp(prefix="joinery-native-provider-"))
    lifecycle_complete = False
    process_ids: list[int] = []
    try:
        process = await launch(executable, ["--version"], directory)
        process_ids.append(process.pid)
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
            case = await handshake(executable, directory / mode, mode)
            cases.append(case)
            process_ids.append(case["process_id"])
        lifecycle_complete = True
    finally:
        release = await asyncio.to_thread(
            remove_isolated_home,
            directory,
            lifecycle_complete=lifecycle_complete,
            process_ids=process_ids,
        )
    return {
        "status": "passed",
        "provider": "codex",
        "version": version,
        "platform": platform.system(),
        "architecture": platform.machine(),
        "authentication": "not requested; isolated empty credential store",
        "inference": "not requested",
        "cases": cases,
        "isolated_home_release": release,
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
    try:
        report = asyncio.run(smoke(executable, args.expected_version))
    except Exception as exc:
        # Keep failed native evidence too. No provider stderr or credentials are
        # included, and the original exception still fails the CI step below.
        failure = {
            "status": "failed",
            "provider": "codex",
            "expected_version": args.expected_version,
            "platform": platform.system(),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        if isinstance(exc, IsolatedHomeCleanupError):
            failure["lock_diagnostics"] = exc.diagnostics
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(failure, indent=2) + "\n", encoding="utf-8"
            )
        raise
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
