"""Reproducible server/runtime setup and versioned addon delivery."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from joinery import metrics, readiness, references, team
from joinery.catalog import ROOT, load_catalog, load_project_tools, selection_ids
from joinery.platform_support import bash_script_argv, editor_running
from joinery.server_install import install
from joinery.transactions import FileTransaction, atomic_json
from joinery.wiring import _args, _command_str, _opencode_plugin


def _run(argv: list[str], timeout: int = 1800) -> None:
    result = subprocess.run(
        bash_script_argv(argv), cwd=ROOT, check=False, timeout=timeout
    )
    if result.returncode:
        raise ValueError("Setup command failed: " + argv[0])


def _docker_build(tag: str, dockerfile: str, revision: str) -> None:
    """Build one worker image, stamped with the sandbox revision it came from."""
    _run(
        [
            "docker",
            "build",
            "--platform",
            "linux/amd64",
            "--label",
            f"{readiness.SETUP_LABEL}={revision}",
            "-t",
            tag,
            "-f",
            "sandbox/" + dockerfile,
            "sandbox",
        ]
    )


def _stamp_runtime(tag: str, revision: str) -> None:
    """Add the revision label to an image sandbox/build.sh made.

    A one-line build on top of the image: no new layers, nothing downloaded,
    and the previous image is left as it is (nothing is pruned).
    """
    result = subprocess.run(
        [
            "docker",
            "build",
            "--platform",
            "linux/amd64",
            "--label",
            f"{readiness.SETUP_LABEL}={revision}",
            "-t",
            tag,
            "-",
        ],
        input=f"FROM {tag}\n",
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    if result.returncode:
        raise ValueError("Setup command failed: docker build (label " + tag + ")")


def setup(project: Path) -> dict[str, Any]:
    """Build the offline workers and install the pinned servers for a project.

    While it builds, `~/.cache/joinery/setup.json` names the image in progress,
    so every readiness screen says "Downloading…" instead of "Not available".
    Each image is labelled with the sandbox revision, and a readiness refresh
    starts once the build is done. No image or build cache is ever removed.
    """
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
    revision = readiness.sandbox_revision()
    os.environ["JOINERY_SANDBOX_REV"] = revision
    ready = []
    unavailable = []
    try:
        image = subprocess.run(
            ["docker", "image", "inspect", "joinery-sandbox-runtime"],
            capture_output=True,
            check=False,
        )
        if image.returncode:
            readiness.mark_setup("build", "joinery-sandbox-runtime")
            _run(
                [
                    str(ROOT / "sandbox/build.sh"),
                    "--all",
                    "--tag",
                    "joinery-sandbox-runtime",
                ],
                3600,
            )
            _stamp_runtime("joinery-sandbox-runtime", revision)
        for dockerfile, tag in [
            ("mcp.Dockerfile", "joinery-mcp-worker"),
            ("creative.Dockerfile", "joinery-creative-worker"),
        ]:
            readiness.mark_setup("build", tag)
            _docker_build(tag, dockerfile, revision)
        for tool in selected:
            entry = load_catalog().get(tool)
            if entry.mcp is None:
                continue
            try:
                install(tool)
                worker = {
                    "blender": "blender",
                    "inkscape": "inkscape",
                    "audacity": "audacity",
                }.get(tool)
                if worker:
                    tag = "joinery-" + worker + "-worker"
                    readiness.mark_setup("build", tag)
                    _docker_build(tag, worker + ".Dockerfile", revision)
                ready.append(tool)
            except (
                OSError,
                ValueError,
                RuntimeError,
                subprocess.SubprocessError,
            ) as exc:
                unavailable.append({"tool": tool, "reason": str(exc)})
    finally:
        readiness.clear_setup()
    # Check the new images now, in the background, so screens update by themselves.
    refreshing = readiness.spawn_refresh(project, full=True, catalog=True)
    return {
        "installed": ready,
        "unavailable": unavailable,
        "revision": revision,
        "refreshing": refreshing,
        "note": "Run joinery mcp status --probe for actual connected operation readiness.",
    }


#: Files Godot writes next to the add-on's own in a project; never part of its version.
_GENERATED = {".uid", ".import"}


def addon_version(source: Path | None = None) -> str:
    source = source or ROOT / "godot_addon/joinery"
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if (
            path.is_file()
            and path.suffix not in _GENERATED
            and path.name != ".DS_Store"
        ):
            digest.update(
                path.relative_to(source).as_posix().encode() + b"\0" + path.read_bytes()
            )
    return digest.hexdigest()


def update_addon(project: Path) -> dict[str, str]:
    """Call with the project editor closed; stage first, keep a local backup."""
    if not (project / "project.godot").is_file():
        raise ValueError("Expected a Godot project.")
    target = project / "addons/joinery"
    # Without the addon no editor can be running it (a brand-new project), so skip
    # the process check, which is slow on Windows.
    if target.is_dir() and editor_running(project):
        raise ValueError(
            "Close this project's editor before updating its addon; this preserves unsaved work and avoids script hot-reload during a live session."
        )
    version = addon_version()
    uids_present = all(
        (target / uid.name).is_file()
        for uid in (ROOT / "godot_addon/joinery").glob("*.gd.uid")
    )
    if target.is_dir() and addon_version(target) == version and uids_present:
        refresh_managed_gates(project)
        enable_plugin(project)
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
        enable_plugin(project)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"version": version, "state": "updated", "backup": str(backup)}


PLUGIN = "res://addons/joinery/plugin.cfg"
_ENABLED = re.compile(r"^enabled\s*=\s*PackedStringArray\((.*?)\)\s*$", re.MULTILINE)


def enable_plugin(project: Path) -> bool:
    """Turn the Joinery addon on in project.godot, keeping other plugins.

    The Project Manager does this when it creates a project; projects created
    from the command line would otherwise open without the sidebar. Returns
    True when the file changed.
    """
    path = project / "project.godot"
    text = path.read_text()
    start = text.find("[editor_plugins]")
    if start == -1:
        path.write_text(
            text.rstrip("\n")
            + f'\n\n[editor_plugins]\n\nenabled=PackedStringArray("{PLUGIN}")\n'
        )
        return True
    end = text.find("\n[", start + 1)
    end = len(text) if end == -1 else end
    section = text[start:end]
    match = _ENABLED.search(section)
    if match is None:
        section = section.rstrip("\n") + f'\nenabled=PackedStringArray("{PLUGIN}")\n'
    else:
        entries = [e.strip() for e in match.group(1).split(",") if e.strip()]
        if f'"{PLUGIN}"' in entries:
            return False
        joined = ", ".join([*entries, f'"{PLUGIN}"'])
        section = (
            section[: match.start()]
            + f"enabled=PackedStringArray({joined})"
            + section[match.end() :]
        )
    path.write_text(text[:start] + section + text[end:])
    return True


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


#: Changed paths listed per transaction; the count says how many there are in all.
LISTED_FILES = 20


def transactions(
    project: Path, action: str, identifier: str = ""
) -> list[dict[str, Any]]:
    paths = (
        [project / ".joinery/transactions" / identifier / "journal.json"]
        if identifier
        else sorted((project / ".joinery/transactions").glob("*/journal.json"))
    )
    result: list[dict[str, Any]] = []
    for path in paths:
        tx = FileTransaction.load(project, path.parent.name)
        warning = ""
        if action == "recover":
            tx.recover()
            if tx.state in {"staging", "review"}:
                tx.reject()
        elif action == "revert":
            tx.revert()
            warning = references.orphan_notice(
                references.users_of(project, references.created(tx.changes)),
                references.REVERTED,
            )
        # A pruned change keeps its journal but can no longer be reverted.
        result.append(
            {
                "id": tx.id,
                "state": tx.state,
                "summary": tx.summary,
                "revertible": tx.revertible,
                # For the editor's Review tab: when, which apps, and what changed.
                "created": tx.created,
                "decided": tx.decided,
                "tools": tx.tools,
                # The reviewer agent's notes and how its review went.
                "review_notes": tx.review_notes,
                "reviewer": tx.reviewer,
                # What the change cost to make, when it was measured (joinery.metrics).
                "cost": metrics.cost(project, tx.id),
                "file_count": len(tx.changes),
                "files": [
                    {"path": change["path"], "change": _change_kind(change)}
                    for change in tx.changes[:LISTED_FILES]
                ],
                **({"warning": warning} if warning else {}),
                **team.conflict_info(tx),
            }
        )
    return result


def _change_kind(change: dict[str, str]) -> str:
    if not change.get("before"):
        return "created"
    if not change.get("after"):
        return "deleted"
    return "modified"
