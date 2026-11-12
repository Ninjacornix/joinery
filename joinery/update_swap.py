"""Swap a verified, staged Joinery into place once the running one has exited.

`joinery update --install` (joinery/updater.py) downloads a release, checks its
digest and signature, unpacks it next to the install, then copies this file out
of the install and starts it with ``python -I update_swap.py <plan.json>``.
It must not import Joinery: the folder it came from is the one it moves.

The plan says what to wait for, what to move where, which projects get the new
addon, and how to start Joinery again. Steps:

1. Wait for the process that asked (the editor or Project Manager) to exit, and
   for every other Joinery process started from the same install.
2. Move the install aside to the backup slot, then move the staged copy into
   its place. A running .exe on Windows locks its folder, so a refused move is
   retried for a while instead of failing.
3. If the second move fails, put the backup back: the old version keeps working.
4. Keep only this one backup (for "Go back to"), update each listed project's
   addon through the new install's own `joinery update-addon`, and start
   Joinery again if the user pressed Restart now (plan ``relaunch``).

Everything it did goes into the plan's state file as ``last_install``.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

#: How long a refused move is retried (Windows keeps a running program's folder locked).
RETRY_SECONDS = 24 * 3600
POLL_SECONDS = 1.0


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def started_from(root: Path) -> list[int]:
    """Process ids whose program lives inside ``root`` (best effort, never raises)."""
    prefix = str(root.resolve())
    found: list[int] = []
    try:
        if os.name == "nt":
            powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
            if powershell is None:
                return []
            query = (
                "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath } | "
                "Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress"
            )
            result = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", query],
                capture_output=True,
                text=True,
                check=False,
                timeout=45,
            )
            rows = json.loads(result.stdout or "[]")
            rows = [rows] if isinstance(rows, dict) else rows
            for row in rows:
                path = str(row.get("ExecutablePath") or "").lower()
                if path.startswith(prefix.lower() + "\\"):
                    found.append(int(row["ProcessId"]))
        elif Path("/proc/self/exe").exists():
            # Linux: ps shortens program names, /proc has the real path.
            for entry in Path("/proc").iterdir():
                if entry.name.isdigit():
                    try:
                        exe = os.readlink(entry / "exe")
                    except OSError:
                        continue
                    if exe.startswith(prefix + "/"):
                        found.append(int(entry.name))
        else:
            result = subprocess.run(
                ["ps", "-A", "-o", "pid=,comm="],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            for line in result.stdout.splitlines():
                pid, _, command = line.strip().partition(" ")
                if command.strip().startswith(prefix + "/") and pid.isdigit():
                    found.append(int(pid))
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    return [pid for pid in found if pid != os.getpid()]


def _move(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, destination)
    except OSError as error:
        # Another volume (EXDEV) copies; a locked folder must be retried, not copied.
        if getattr(error, "errno", None) != 18:
            raise
        shutil.move(str(source), str(destination))


def _retrying(action: Any, deadline: float) -> None:
    while True:
        try:
            action()
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(POLL_SECONDS * 2)
        except OSError as error:
            # Windows: ERROR_SHARING_VIOLATION (32) and ERROR_ACCESS_DENIED (5).
            if (
                getattr(error, "winerror", None) not in {5, 32}
                or time.monotonic() > deadline
            ):
                raise
            time.sleep(POLL_SECONDS * 2)


def swap(plan: dict[str, Any]) -> dict[str, Any]:
    """Move the install aside and the staged copy into its place; undo on failure."""
    root = Path(plan["install_root"])
    staged = Path(plan["staged"])
    backup = Path(plan["backup"])
    deadline = time.monotonic() + float(plan.get("retry_seconds", RETRY_SECONDS))
    if not staged.exists():
        raise FileNotFoundError(f"The new version is missing from {staged}.")
    if backup.exists():
        shutil.rmtree(backup)
    _retrying(lambda: _move(root, backup), deadline)
    try:
        _move(staged, root)
    except BaseException:
        _move(backup, root)
        raise
    # Only one older version is kept, the one just moved aside.
    for older in (
        backup.parent.parent.iterdir() if backup.parent.parent.is_dir() else []
    ):
        if older != backup.parent and older.is_dir():
            shutil.rmtree(older, ignore_errors=True)
    return {"version": plan["version"], "previous": plan["current"]}


def update_addons(plan: dict[str, Any]) -> list[dict[str, str]]:
    """Each open project gets the new addon through the new install's safe path."""
    results = []
    uv = shutil.which("uv")
    runtime = plan.get("runtime", "")
    for project in plan.get("projects", []):
        if uv is None or not runtime:
            results.append(
                {"project": project, "state": "skipped", "message": "uv not found"}
            )
            continue
        env = dict(os.environ)
        env.pop("JOINERY_HOME", None)
        env.update(
            UV_NO_DEV="1",
            UV_PROJECT_ENVIRONMENT=str(
                Path(plan["home"]) / ("python-" + plan["version"])
            ),
        )
        command = plan.get("addon_command") or [
            uv,
            "run",
            "--frozen",
            "--quiet",
            "--project",
            runtime,
            "joinery",
            "update-addon",
            "--project",
        ]
        try:
            done = subprocess.run(
                [*command, project],
                env=env,
                cwd=plan["home"],
                capture_output=True,
                text=True,
                check=False,
                timeout=900,
            )
            ok = done.returncode == 0
            message = (done.stdout or done.stderr).strip().splitlines()[-1:] or [""]
            results.append(
                {
                    "project": project,
                    "state": "updated" if ok else "failed",
                    "message": message[0],
                }
            )
        except (OSError, subprocess.SubprocessError) as error:
            results.append(
                {"project": project, "state": "failed", "message": str(error)}
            )
    return results


def relaunch(argv: list[str]) -> None:
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        start_new_session=os.name != "nt",
    )


def run(plan_path: Path) -> dict[str, Any]:
    plan = _read_json(plan_path)
    state_path = Path(plan["state"])
    wait = int(plan.get("wait_pid") or 0)
    while pid_running(wait):
        time.sleep(POLL_SECONDS)
    root = Path(plan["install_root"])
    waited = time.monotonic()
    while started_from(root) and time.monotonic() - waited < float(
        plan.get("retry_seconds", RETRY_SECONDS)
    ):
        time.sleep(POLL_SECONDS * 2)
    if not plan_path.is_file():
        # `joinery update --cancel` removed the plan while this waited.
        return {"version": plan["version"], "ok": False, "message": "cancelled"}
    outcome: dict[str, Any] = {
        "version": plan["version"],
        "kind": plan.get("kind", "install"),
        "finished": "",
    }
    try:
        swap(plan)
        outcome["ok"] = True
        outcome["addons"] = update_addons(plan)
    except Exception as error:  # noqa: BLE001 - every failure is reported, none kills the old install.
        outcome.update(ok=False, message=str(error) or type(error).__name__)
    outcome["finished"] = _now()
    state = _read_json(state_path)
    state["last_install"] = outcome
    state.pop("pending", None)
    if outcome["ok"]:
        state["previous"] = {
            "version": plan["current"],
            "path": plan["backup"],
        }
        state["installed"] = plan["version"]
    _write_json(state_path, state)
    # The relaunch choice is read last: Restart now may be pressed after staging.
    plan = _read_json(plan_path) or plan
    if outcome["ok"] and plan.get("relaunch") and plan.get("relaunch_argv"):
        relaunch(list(plan["relaunch_argv"]))
    elif not outcome["ok"] and plan.get("relaunch") and plan.get("fallback_argv"):
        relaunch(list(plan["fallback_argv"]))
    try:
        plan_path.unlink()
    except OSError:
        pass
    return outcome


if __name__ == "__main__":
    print(json.dumps(run(Path(sys.argv[1]))))
