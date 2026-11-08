"""Reproducible server/runtime setup and versioned addon delivery."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from joinery.catalog import ROOT, load_catalog, load_project_tools, selection_ids
from joinery.server_install import install
from joinery.transactions import FileTransaction, atomic_json
from joinery.wiring import _args, _command_str, _opencode_plugin


def _run(argv: list[str], timeout: int = 1800) -> None:
    result = subprocess.run(argv, cwd=ROOT, check=False, timeout=timeout)
    if result.returncode:
        raise ValueError("Setup command failed: " + argv[0])


def setup(project: Path) -> dict[str, Any]:
    selected = selection_ids(
        load_catalog(), load_project_tools(project / "joinery.toml")
    )
    if shutil.which("docker") is None:
        raise ValueError(
            "Install and start Docker to run the offline creative workers."
        )
    result = subprocess.run(["docker", "info"], capture_output=True, check=False)
    if result.returncode:
        raise ValueError("Start Docker, then run Joinery setup again.")
    image = subprocess.run(
        ["docker", "image", "inspect", "joinery-sandbox-runtime"],
        capture_output=True,
        check=False,
    )
    if image.returncode:
        _run(
            [
                str(ROOT / "sandbox/build.sh"),
                "--all",
                "--tag",
                "joinery-sandbox-runtime",
            ],
            3600,
        )
    for dockerfile, tag in [
        ("mcp.Dockerfile", "joinery-mcp-worker"),
        ("creative.Dockerfile", "joinery-creative-worker"),
    ]:
        _run(
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "-t",
                tag,
                "-f",
                "sandbox/" + dockerfile,
                "sandbox",
            ]
        )
    ready = []
    unavailable = []
    for tool in selected:
        entry = load_catalog().get(tool)
        if entry.mcp is None:
            continue
        try:
            install(tool)
            worker = {"blender": "blender", "inkscape": "inkscape"}.get(tool)
            if worker:
                _run(
                    [
                        "docker",
                        "build",
                        "--platform",
                        "linux/amd64",
                        "-t",
                        "joinery-" + worker + "-worker",
                        "-f",
                        "sandbox/" + worker + ".Dockerfile",
                        "sandbox",
                    ]
                )
            ready.append(tool)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            unavailable.append({"tool": tool, "reason": str(exc)})
    return {
        "installed": ready,
        "unavailable": unavailable,
        "note": "Run joinery mcp status --probe for actual connected operation readiness.",
    }


def addon_version(source: Path | None = None) -> str:
    source = source or ROOT / "godot_addon/joinery"
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix != ".uid" and path.name != ".DS_Store":
            digest.update(
                path.relative_to(source).as_posix().encode() + b"\0" + path.read_bytes()
            )
    return digest.hexdigest()


def update_addon(project: Path) -> dict[str, str]:
    """Call with the project editor closed; stage first, keep a local backup."""
    if not (project / "project.godot").is_file():
        raise ValueError("Expected a Godot project.")
    processes = subprocess.run(
        ["ps", "-A", "-o", "comm=,args="], capture_output=True, text=True, check=False
    )
    for line in processes.stdout.splitlines():
        command = line.split(maxsplit=1)[0].lower() if line.strip() else ""
        if "godot" in command and ("--path " + str(project.resolve())) in line:
            raise ValueError(
                "Close this project's editor before updating its addon; this preserves unsaved work and avoids script hot-reload during a live session."
            )
    target = project / "addons/joinery"
    version = addon_version()
    uids_present = all(
        (target / uid.name).is_file()
        for uid in (ROOT / "godot_addon/joinery").glob("*.gd.uid")
    )
    if target.is_dir() and addon_version(target) == version and uids_present:
        refresh_managed_gates(project)
        return {"version": version, "state": "current"}
    backups = project / ".joinery/addon-backups"
    backups.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=".addon-", dir=target.parent if target.parent.is_dir() else project
        )
    )
    backup = backups / (
        str(time.time_ns())
        + "-"
        + (addon_version(target)[:12] if target.is_dir() else "new")
    )
    try:
        shutil.copytree(
            ROOT / "godot_addon/joinery",
            staging,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".DS_Store"),
        )
        target.parent.mkdir(exist_ok=True)
        if target.exists():
            target.rename(backup)
        try:
            staging.rename(target)
        except BaseException:
            if backup.exists():
                backup.rename(target)
            raise
        (project / ".joinery/addon-version.json").write_text(
            json.dumps({"version": version}) + "\n"
        )
        refresh_managed_gates(project)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"version": version, "state": "updated", "backup": str(backup)}


def refresh_managed_gates(project: Path) -> None:
    """Migrate only Joinery-owned commands, preserving unrelated user settings."""
    for agent, filename, event in [
        ("claude", ".claude/settings.json", "PreToolUse"),
        ("codex", ".codex/hooks.json", "PreToolUse"),
        ("gemini", ".gemini/settings.json", "BeforeTool"),
    ]:
        path = project / filename
        if not path.is_file():
            continue
        data = json.loads(path.read_text())
        changed = False
        for group in data.get("hooks", {}).get(event, []):
            for hook in group.get("hooks", []):
                command = str(hook.get("command", ""))
                arguments = hook.get("args", [])
                legacy = command == "uv" and arguments == _args(str(ROOT), agent)
                owned = ("joinery hook --agent " + agent) in command
                if legacy or owned:
                    expected = _command_str(str(ROOT), agent)
                    if command != expected or "args" in hook or "onFailure" in hook:
                        hook["command"] = expected
                        hook.pop("args", None)
                        hook.pop("onFailure", None)
                        changed = True
        if changed:
            backup = (
                project
                / ".joinery/config-backups"
                / (str(time.time_ns()) + "-" + agent + ".json")
            )
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            atomic_json(path, data)
    plugin = project / ".opencode/plugin/joinery-gate.js"
    if plugin.is_file() and plugin.read_text().startswith("// Generated by Joinery."):
        plugin.write_text(_opencode_plugin(str(ROOT)))


def transactions(
    project: Path, action: str, identifier: str = ""
) -> list[dict[str, str]]:
    paths = (
        [project / ".joinery/transactions" / identifier / "journal.json"]
        if identifier
        else sorted((project / ".joinery/transactions").glob("*/journal.json"))
    )
    result = []
    for path in paths:
        tx = FileTransaction.load(project, path.parent.name)
        if action == "recover":
            tx.recover()
            if tx.state in {"staging", "review"}:
                tx.reject()
        elif action == "revert":
            tx.revert()
        result.append({"id": tx.id, "state": tx.state, "summary": tx.summary})
    return result
