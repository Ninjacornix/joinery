"""Bring a project's add-on up to date from inside its own editor.

A project keeps its own copy of the Joinery add-on (`addons/joinery`). After a
Joinery update that copy is older than the one Joinery ships, and it can only
be replaced while the project's editor is closed (`bootstrap.update_addon`).

`state()` says whether the copy is current, so the sidebar can offer
"Update and restart". `schedule()` starts a detached helper (this module run
as a script) and returns at once; the editor then closes itself, asking about
unsaved scenes as usual. The helper waits for that editor to exit, updates the
add-on, writes the outcome to `.joinery/addon-update.json` and opens the
project again, also when the update failed (the old add-on is put back, and
the sidebar says what went wrong). Same pattern as `update_swap.py`, without
moving the install.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from joinery.catalog import ROOT

#: How long the helper waits for the editor to close. Past it (the user chose
#: Cancel when asked about unsaved scenes) the helper gives up, so the editor is
#: never reopened hours later out of the blue.
WAIT_SECONDS = 600.0
POLL_SECONDS = 0.5
OUTCOME = ".joinery/addon-update.json"
PLAN = ".joinery/addon-update-plan.json"


def _source() -> Path:
    return ROOT / "godot_addon/joinery"


def state(project: Path) -> dict[str, Any]:
    """{"state": "current" | "stale" | "missing", ...} for the project's add-on.

    `last` is the outcome of the last update the helper ran, when it failed, so
    the sidebar can say why the add-on is still old.
    """
    from joinery.bootstrap import addon_version

    target = project / "addons/joinery"
    available = addon_version(_source())
    if not target.is_dir():
        found = "missing"
        installed = ""
    else:
        installed = addon_version(target)
        found = "current" if installed == available else "stale"
    report: dict[str, Any] = {
        "state": found,
        "installed": installed[:12],
        "available": available[:12],
    }
    last = _read(project / OUTCOME)
    if found != "current" and last and not last.get("ok", True):
        report["last_error"] = str(last.get("message", ""))
    return report


def relaunch_argv(editor: str, project: Path) -> list[str]:
    """How to open [project] again in the editor at [editor] (its executable)."""
    path = Path(editor)
    extra = ["--editor", "--path", str(project)]
    for parent in path.parents:
        if parent.suffix == ".app":
            # Opened like a double click, so it gets its own Dock icon and the
            # environment the app's launcher sets.
            return ["open", "-n", str(parent), "--args", *extra]
    return [str(path), *extra]


def schedule(
    project: Path,
    *,
    wait_pid: int,
    editor: str,
    start: Any = None,
) -> dict[str, Any]:
    """Start the helper that updates the add-on once the editor `wait_pid` exits."""
    project = project.resolve()
    if not (project / "project.godot").is_file():
        raise ValueError("Expected a Godot project.")
    if not editor:
        from joinery.distribution import engine_binary

        editor = str(engine_binary())
    plan = {
        "project": str(project),
        "wait_pid": wait_pid,
        "relaunch_argv": relaunch_argv(editor, project),
        "home": str(ROOT),
    }
    path = project / PLAN
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2) + "\n")
    helper = (start or _start_helper)(path)
    return {"state": "scheduled", "helper": helper, "project": str(project)}


def _start_helper(plan: Path) -> int:
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    child = subprocess.Popen(
        [sys.executable, "-m", "joinery.addon_refresh", str(plan)],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        start_new_session=os.name != "nt",
    )
    return child.pid


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _launch(argv: list[str], home: str) -> None:
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200
    subprocess.Popen(
        argv,
        env=dict(os.environ, JOINERY_HOME=home),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        start_new_session=os.name != "nt",
    )


def run(
    plan_path: Path,
    *,
    wait_seconds: float = WAIT_SECONDS,
    launch: Any = None,
) -> dict[str, Any]:
    """The helper: wait for the editor, update the add-on, open the project again."""
    from joinery.bootstrap import update_addon
    from joinery.update_swap import pid_running

    plan = _read(plan_path)
    project = Path(str(plan.get("project", "")))
    wait = int(plan.get("wait_pid") or 0)
    deadline = time.monotonic() + wait_seconds
    while pid_running(wait):
        if time.monotonic() > deadline:
            stayed = {
                "ok": False,
                "message": "The editor stayed open, so the add-on was not updated.",
                "finished": _now(),
            }
            (project / OUTCOME).write_text(json.dumps(stayed) + "\n")
            plan_path.unlink(missing_ok=True)
            return stayed
        time.sleep(POLL_SECONDS)
    outcome: dict[str, Any]
    try:
        done = update_addon(project)
        outcome = {"ok": True, "state": done["state"], "version": done["version"]}
    except (OSError, ValueError, RuntimeError) as error:
        outcome = {"ok": False, "message": str(error) or type(error).__name__}
    outcome["finished"] = _now()
    (project / OUTCOME).write_text(json.dumps(outcome) + "\n")
    plan_path.unlink(missing_ok=True)
    argv = [str(item) for item in plan.get("relaunch_argv", [])]
    if argv:
        # Also after a failure: the old add-on is back in place and says why.
        (launch or _launch)(argv, str(plan.get("home", ROOT)))
    return outcome


if __name__ == "__main__":
    print(json.dumps(run(Path(sys.argv[1]))))
