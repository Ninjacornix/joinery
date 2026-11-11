"""`joinery share itch`: publish the built game to itch.io with butler, by the user's hand only.

butler is itch.io's own upload tool. It is a third-party binary, so it follows
the GodotSteam model (ADR 0027, ADR 0046): one version pinned in
`policy/butler.toml` with each platform's SHA-256, downloaded from itch.io's
official server only when the user passes `--install-butler` (or presses the
install button), hash-checked before the archive is opened, and kept in
Joinery's cache, never in a project.

Credentials stay the user's: butler reads their own `BUTLER_API_KEY`, or the
login they made with `butler login` in a terminal. Joinery never asks for,
stores or passes a key.

No agent can run this: the shared policy hook refuses `joinery share itch` and
`butler push`/`login` in every agent's shell (`joinery.policy`), so a publish
only ever starts from the user's click or their own terminal. `--dry-run`
shows exactly what would be pushed and needs neither butler nor a login.
"""

from __future__ import annotations

import json
import os
import platform
import re
import stat
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from joinery.catalog import ROOT
from joinery.pinned import Opener, PinnedDownloadError, digest, download
from joinery.roles import ROLE_ENV
from joinery.share_build import SHARE, TARGETS, game_version, last_report

POLICY_PATH = ROOT / "policy" / "butler.toml"
SETTINGS = SHARE / "itch.json"
TARGET = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_-]+$")
CHANNEL = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
INSTALL_HINT = "butler isn't installed. Allow Joinery to install it (--install-butler)."
LOGIN_HINT = (
    "Sign in to itch.io first: set BUTLER_API_KEY, or run `butler login` in a terminal."
)


class PublishError(Exception):
    """A plain-language reason nothing was published."""


@dataclass(frozen=True)
class Download:
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Butler:
    version: str
    licence: str
    platforms: dict[str, Download]


def load_butler(path: Path = POLICY_PATH) -> Butler:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return Butler(
        version=str(raw["version"]),
        licence=str(raw["licence"]),
        platforms={
            key: Download(
                str(v["url"]), str(v.get("sha256", "")), int(v.get("size", 0))
            )
            for key, v in raw["platforms"].items()
        },
    )


def platform_key() -> str:
    """broth's name for this system (butler runs on Apple Silicon through Rosetta)."""
    if sys.platform == "darwin":
        return "darwin-amd64"
    if os.name == "nt":
        return "windows-amd64"
    return "linux-amd64"


def default_cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "joinery" / "butler"


def _program(folder: Path) -> Path:
    return folder / ("butler.exe" if os.name == "nt" else "butler")


def installed(cache: Path | None = None, release: Butler | None = None) -> Path | None:
    """The installed, verified butler, or None. `JOINERY_BUTLER` overrides (tests)."""
    override = os.environ.get("JOINERY_BUTLER")
    if override:
        return Path(override) if Path(override).is_file() else None
    release = release or load_butler()
    folder = (cache or default_cache()) / release.version
    marker = folder / ".verified"
    program = _program(folder)
    pinned = release.platforms.get(platform_key())
    if (
        pinned is None
        or not program.is_file()
        or not marker.is_file()
        or marker.read_text(encoding="utf-8").strip() != pinned.sha256
    ):
        return None
    return program


def install(
    cache: Path | None = None,
    release: Butler | None = None,
    opener: Opener | None = None,
) -> Path:
    """Download the pinned butler for this system, verify it, unpack it into the cache."""
    release = release or load_butler()
    key = platform_key()
    pinned = release.platforms.get(key)
    if (
        pinned is None
        or not re.fullmatch(r"[0-9a-f]{64}", pinned.sha256)
        or pinned.size <= 0
    ):
        raise PublishError(
            f"butler {release.version} isn't pinned for {key} yet (policy/butler.toml has "
            "no SHA-256 for it), so Joinery won't download it. Install butler yourself "
            "and set JOINERY_BUTLER to its path, or record the hash in a reviewed change."
        )
    folder = (cache or default_cache()) / release.version
    archive = folder / f"butler-{key}.zip"
    if not (archive.is_file() and digest(archive) == pinned.sha256):
        archive.unlink(missing_ok=True)
        try:
            download(
                pinned.url,
                archive,
                what="butler",
                size=pinned.size,
                expected=pinned.sha256,
                opener=opener,
            )
        except PinnedDownloadError as exc:
            raise PublishError(str(exc)) from exc
    _unpack(archive, folder)
    program = _program(folder)
    if not program.is_file():
        raise PublishError("The butler download has no butler program in it.")
    program.chmod(program.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (folder / ".verified").write_text(pinned.sha256 + "\n", encoding="utf-8")
    return program


def _unpack(archive: Path, folder: Path) -> None:
    """Extract every entry flat into `folder`; any absolute path, `..` or link refuses all."""
    with zipfile.ZipFile(archive) as bundle:
        infos = [i for i in bundle.infolist() if not i.is_dir()]
        for info in infos:
            parts = PurePosixPath(info.filename.replace("\\", "/")).parts
            if (
                info.filename.startswith(("/", "\\"))
                or re.match(r"^[A-Za-z]:", info.filename)
                or ".." in parts
                or stat.S_ISLNK(info.external_attr >> 16)
            ):
                raise PublishError(
                    "The butler archive has an unsafe entry; nothing was installed."
                )
        for info in infos:
            target = folder / PurePosixPath(info.filename).name
            target.write_bytes(bundle.read(info))


def signed_in() -> str | None:
    """How butler will sign in: 'BUTLER_API_KEY', 'butler login', or None."""
    if os.environ.get("BUTLER_API_KEY"):
        return "BUTLER_API_KEY"
    if sys.platform == "darwin":
        creds = Path.home() / "Library/Application Support/itch/butler_creds"
    elif os.name == "nt":
        creds = Path(os.environ.get("APPDATA", str(Path.home()))) / "itch/butler_creds"
    else:
        creds = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        creds = creds / "itch/butler_creds"
    return "butler login" if creds.is_file() else None


def channels_for(project: Path, wanted: list[str] | None) -> list[tuple[str, Path]]:
    """(channel, zip) for each successful build, or just the wanted channels."""
    report = last_report(project) or {}
    built = {
        str(result.get("channel")): project / str(result["zip"])
        for target_id, result in report.get("targets", {}).items()
        if target_id in TARGETS and result.get("ok") and result.get("zip")
    }
    if not built:
        raise PublishError("Build the game first (joinery share build).")
    if not wanted:
        return sorted(built.items())
    by_target = {TARGETS[t].channel: TARGETS[t].channel for t in TARGETS}
    by_target.update({t: TARGETS[t].channel for t in TARGETS})
    chosen = []
    for name in wanted:
        channel = by_target.get(name, name)
        if not CHANNEL.match(channel):
            raise PublishError(f"{name!r} isn't a channel name.")
        if channel not in built:
            raise PublishError(
                f"There's no {channel} build to publish. Build it first."
            )
        chosen.append((channel, built[channel]))
    return chosen


def plan(
    project: Path, target: str, wanted: list[str] | None, butler: Path | None = None
) -> list[dict[str, Any]]:
    """Exactly what a publish runs: one `butler push` per channel."""
    if not TARGET.match(target):
        raise PublishError(
            "The itch.io target is your user name and the game's, as user/game."
        )
    version = game_version(project)
    program = str(butler) if butler is not None else "butler"
    steps = []
    for channel, archive in channels_for(project, wanted):
        if not archive.is_file():
            raise PublishError(f"{archive.name} is missing. Build the game again.")
        steps.append(
            {
                "channel": channel,
                "file": archive.relative_to(project).as_posix(),
                "size": archive.stat().st_size,
                "argv": [
                    program,
                    "push",
                    str(archive),
                    f"{target}:{channel}",
                    "--userversion",
                    version,
                    "--if-changed",
                ],
            }
        )
    return steps


def remember(project: Path, target: str, channels: list[str]) -> None:
    path = project / SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"target": target, "channels": channels}, indent=2) + "\n",
        encoding="utf-8",
    )


def settings(project: Path) -> dict[str, Any]:
    try:
        data = json.loads((project / SETTINGS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def status() -> dict[str, Any]:
    release = load_butler()
    pinned = release.platforms.get(platform_key())
    return {
        "butler": str(installed()) if installed() else None,
        "version": release.version,
        "pinned": bool(pinned and pinned.sha256),
        "signed_in": signed_in(),
        "host": platform.system(),
    }


def publish(
    project: Path,
    target: str,
    wanted: list[str] | None,
    *,
    dry_run: bool = False,
    install_butler: bool = False,
) -> dict[str, Any]:
    """Push each channel's zip with butler, or (dry run) say exactly what would be pushed."""
    if os.environ.get(ROLE_ENV):
        raise PublishError(
            "Only you can publish, from the Share page or your own terminal."
        )
    project = project.resolve()
    butler = installed()
    steps = plan(project, target, wanted, butler)
    result: dict[str, Any] = {
        "target": target,
        "dry_run": dry_run,
        "steps": steps,
        "butler": str(butler) if butler else None,
        "signed_in": signed_in(),
    }
    if dry_run:
        notes = []
        if butler is None:
            notes.append(INSTALL_HINT)
        if result["signed_in"] is None:
            notes.append(LOGIN_HINT)
        result["notes"] = notes
        return result
    if butler is None:
        if not install_butler:
            raise PublishError(INSTALL_HINT)
        butler = install()
        steps = plan(project, target, wanted, butler)
        result.update({"steps": steps, "butler": str(butler)})
    if result["signed_in"] is None:
        raise PublishError(LOGIN_HINT)
    for step in steps:
        done = subprocess.run(
            step["argv"],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=3600,
        )
        step["ok"] = done.returncode == 0
        step["output"] = (done.stdout + done.stderr).strip()[-1200:]
        if not step["ok"]:
            break
    result["ok"] = all(step.get("ok") for step in steps)
    remember(project, target, [str(s["channel"]) for s in steps])
    return result
