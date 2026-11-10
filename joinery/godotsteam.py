"""GodotSteam: the one pinned download a Steam game project gets.

The release (URL, version, SHA-256, licence, archive layout) lives in
`policy/godotsteam.toml`. It is downloaded only into projects whose
`joinery.toml [game] features` include `steam`, into a per-user cache, and its
hash is checked before the archive is opened. Only `addons/godotsteam/**` is
extracted, never an absolute path, a `..` or a symlink. The licence is written
beside the extension and the install is recorded in `joinery.lock`
(`[[extensions]]`, origin `pinned-download`).

Nothing here talks to Steam: Joinery never uploads or publishes anything there.
See docs/adr/0027-godotsteam.md.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
import tomllib
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import IO, Annotated, Literal
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field

from joinery.catalog import ROOT
from joinery.godot_config import GodotConfig, to_variant
from joinery.lockfile import (
    Extension,
    forget_extension,
    read_extensions,
    record_extension,
    tree_sha256,
)
from joinery.policy import load_denylist, url_violation

POLICY_PATH = ROOT / "policy" / "godotsteam.toml"
FEATURE = "steam"
LICENSE_NAME = "LICENSE"
CONNECT_CHOICES = ("steam", "direct")
OFFLINE_MESSAGE = (
    "Couldn't download GodotSteam. Steam starts working once it's installed: "
    "run joinery feature add steam again."
)
_TIMEOUT = 60
_CHUNK = 1 << 20

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Connect = Literal["steam", "direct"]
#: `urlopen`-shaped: takes a URL and a timeout, returns a readable response.
Opener = Callable[..., IO[bytes]]


class SteamSetupError(Exception):
    """A plain-language reason the extension was not installed or changed."""


class DownloadFailed(SteamSetupError):
    """The release could not be downloaded (offline, server down)."""

    def __init__(self) -> None:
        super().__init__(OFFLINE_MESSAGE)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Layout(_Strict):
    root: str
    exclude: list[str] = []
    required: list[str] = []


class Release(_Strict):
    name: str
    version: str
    tag: str
    steamworks: str
    godot_min: str
    url: str = Field(pattern=r"^https://")
    sha256: Sha256
    size: int = Field(gt=0)
    source: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    licence: str
    licence_file: str
    layout: Layout

    @property
    def archive_name(self) -> str:
        return PurePosixPath(urlparse(self.url).path).name


@dataclass(frozen=True)
class Installed:
    """What `install` did: where the extension is and whether it was already there."""

    path: Path
    version: str
    files: int
    already: bool


def load_release(path: Path = POLICY_PATH) -> Release:
    with path.open("rb") as f:
        return Release.model_validate(tomllib.load(f))


def default_cache() -> Path:
    """`~/.cache/joinery/godotsteam` (`$XDG_CACHE_HOME` when set)."""
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "joinery" / "godotsteam"


def steam_enabled(project: Path) -> bool:
    """True when `joinery.toml [game] features` lists steam."""
    config = project / "joinery.toml"
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    game = data.get("game")
    features = game.get("features") if isinstance(game, dict) else None
    return isinstance(features, list) and FEATURE in features


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_url(url: str) -> None:
    if urlparse(url).scheme != "https":
        raise SteamSetupError("GodotSteam is only downloaded over https.")
    verdict = url_violation(url, load_denylist())
    if verdict is not None:
        raise SteamSetupError(f"GodotSteam was not downloaded: {verdict}.")


class _CheckedRedirects(HTTPRedirectHandler):
    """Follows a redirect only to an https URL the host denylist allows."""

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> Request | None:
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]


def urlopen(url: str, timeout: float) -> IO[bytes]:
    """`urllib.request.urlopen`, but every redirect is checked like the first URL."""
    response: IO[bytes] = build_opener(_CheckedRedirects()).open(url, timeout=timeout)
    return response


def _copy_capped(source: IO[bytes], out: IO[bytes], limit: int) -> None:
    """Copy at most `limit` bytes; more than that is not the pinned release."""
    copied = 0
    while chunk := source.read(_CHUNK):
        copied += len(chunk)
        if copied > limit:
            raise SteamSetupError(
                "The GodotSteam download was larger than the pinned release, so it "
                "was deleted and nothing was installed."
            )
        out.write(chunk)


def fetch(release: Release, cache: Path, opener: Opener | None = None) -> Path:
    """The release archive in the cache, downloaded when missing, hash-checked.

    A cached or downloaded file whose SHA-256 differs from the policy is
    deleted and an error raised; nothing is opened. Redirects are held to the
    same https and denylist rules as the pinned URL, and no more than the
    pinned size is read.
    """
    folder = cache / release.version
    archive = folder / release.archive_name
    if archive.is_file():
        if _sha256(archive) == release.sha256:
            return archive
        archive.unlink()
    _check_url(release.url)
    open_url = opener or urlopen
    folder.mkdir(parents=True, exist_ok=True)
    partial = archive.with_name(archive.name + ".part")
    try:
        with (
            open_url(release.url, timeout=_TIMEOUT) as response,
            partial.open("wb") as out,
        ):
            _copy_capped(response, out, release.size)
    except SteamSetupError:
        partial.unlink(missing_ok=True)
        raise
    except (URLError, OSError, TimeoutError) as exc:
        partial.unlink(missing_ok=True)
        raise DownloadFailed() from exc
    if _sha256(partial) != release.sha256:
        partial.unlink()
        raise SteamSetupError(
            "The GodotSteam download did not match its recorded hash, so it was "
            "deleted and nothing was installed."
        )
    partial.replace(archive)
    return archive


def _unsafe(name: str) -> bool:
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        return True
    return ".." in re.split(r"[\\/]", name)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _excluded(rel: str, layout: Layout) -> bool:
    return any(
        fnmatchcase(rel, pattern)
        or (pattern.endswith("/**") and rel.startswith(pattern[:-2]))
        for pattern in layout.exclude
    )


def members(
    archive: zipfile.ZipFile, layout: Layout
) -> list[tuple[zipfile.ZipInfo, str]]:
    """The entries to extract, as (entry, path relative to the layout root).

    Every entry in the archive is checked first: one absolute path, `..` or
    symlink refuses the whole archive.
    """
    infos = archive.infolist()
    for info in infos:
        if _unsafe(info.filename):
            raise SteamSetupError(
                f"The GodotSteam archive has an unsafe path ({info.filename}); "
                "nothing was installed."
            )
        if _is_symlink(info):
            raise SteamSetupError(
                f"The GodotSteam archive has a link ({info.filename}); nothing was installed."
            )
    prefix = layout.root.rstrip("/") + "/"
    chosen: list[tuple[zipfile.ZipInfo, str]] = []
    for info in infos:
        if info.is_dir() or not info.filename.startswith(prefix):
            continue
        rel = info.filename[len(prefix) :]
        if rel and not _excluded(rel, layout):
            chosen.append((info, rel))
    missing = sorted(set(layout.required) - {rel for _, rel in chosen})
    if missing:
        raise SteamSetupError(
            "The GodotSteam archive is missing "
            + ", ".join(missing)
            + "; nothing was installed."
        )
    return chosen


def _recorded(project: Path, release: Release) -> Extension | None:
    for entry in read_extensions(project):
        if entry.name == release.name:
            return entry
    return None


def _current(project: Path, release: Release) -> bool:
    """Installed at this version and untouched since."""
    entry = _recorded(project, release)
    folder = project / release.layout.root
    return (
        entry is not None
        and entry.version == release.version
        and entry.sha256 == release.sha256
        and entry.path == release.layout.root
        and folder.is_dir()
        and tree_sha256(folder) == entry.files_sha256
    )


def _extract(archive: Path, release: Release, staging: Path) -> int:
    with zipfile.ZipFile(archive) as bundle:
        chosen = members(bundle, release.layout)
        licence_rel = release.licence_file.removeprefix(
            release.layout.root.rstrip("/") + "/"
        )
        licence: bytes | None = None
        for info, rel in chosen:
            target = staging.joinpath(*PurePosixPath(rel).parts)
            if not target.resolve().is_relative_to(staging.resolve()):
                raise SteamSetupError("The GodotSteam archive has an unsafe path.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out, _CHUNK)
            if rel == licence_rel:
                licence = target.read_bytes()
    if licence is None:
        raise SteamSetupError(
            "The GodotSteam archive has no licence; nothing was installed."
        )
    (staging / LICENSE_NAME).write_bytes(licence)
    return len(chosen) + 1


def install(
    project: Path,
    *,
    cache: Path | None = None,
    release: Release | None = None,
    opener: Opener | None = None,
) -> Installed:
    """Download (once), verify and install GodotSteam into a Steam project.

    Refuses projects without the steam feature before touching the network.
    Running it again on an installed, untouched copy does nothing.
    """
    project = project.resolve()
    if not steam_enabled(project):
        raise SteamSetupError(
            "Steam is not switched on for this project, so GodotSteam was not downloaded."
        )
    release = release or load_release()
    target = project / release.layout.root
    if _current(project, release):
        count = sum(1 for p in target.rglob("*") if p.is_file())
        _list_extensions(project, target, add=True)
        return Installed(target, release.version, count, already=True)
    archive = fetch(release, cache or default_cache(), opener)
    if _sha256(archive) != release.sha256:  # re-checked right before opening
        archive.unlink()
        raise SteamSetupError(
            "The cached GodotSteam download did not match its recorded hash, so it "
            "was deleted and nothing was installed."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".godotsteam-", dir=target.parent))
    try:
        files = _extract(archive, release, staging)
        if target.exists() or target.is_symlink():
            _remove(target)
        staging.replace(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    record_extension(
        project,
        Extension(
            name=release.name,
            version=release.version,
            url=release.url,
            sha256=release.sha256,
            licence=release.licence,
            origin="pinned-download",
            path=release.layout.root,
            files_sha256=tree_sha256(target),
        ),
    )
    _list_extensions(project, target, add=True)
    return Installed(target, release.version, files, already=False)


#: Godot's list of extensions to load at startup, inside the project's cache.
EXTENSION_LIST = ".godot/extension_list.cfg"


def _list_extensions(project: Path, target: Path, *, add: bool) -> None:
    """Add GodotSteam to (or take it off) the extensions Godot loads at startup.

    Without the entry, the first import finds the extension mid-scan and
    loads it then; stock Godot 4.7.2 crashes on exit after that.
    """
    paths = sorted(
        "res://" + p.relative_to(project).as_posix()
        for p in target.glob("*.gdextension")
    ) or ["res://" + target.relative_to(project).as_posix() + "/godotsteam.gdextension"]
    listing = project / EXTENSION_LIST
    lines = listing.read_text().splitlines() if listing.is_file() else []
    kept = [line for line in lines if line.strip() and line.strip() not in paths]
    if add:
        kept += paths
    if kept == lines:
        return
    if not kept:
        listing.unlink(missing_ok=True)
        return
    listing.parent.mkdir(parents=True, exist_ok=True)
    listing.write_text("".join(line + "\n" for line in kept))


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def uninstall(project: Path, *, release: Release | None = None) -> bool:
    """Remove the extension's files and its lock entry; False when it was not recorded.

    Only a copy Joinery installed (one with a lock entry) is deleted; a folder
    the user put there themselves is left alone.
    """
    project = project.resolve()
    release = release or load_release()
    target = project / release.layout.root
    if _recorded(project, release) is None:
        return False
    # Even with the folder already gone, Godot must stop loading it at startup.
    _list_extensions(project, target, add=False)
    if target.exists() or target.is_symlink():
        _remove(target)
    forget_extension(project, release.name)
    return True


# --- [game.steam] and project.godot --------------------------------------------

_STEAM_HEADER = re.compile(
    r"^[ \t]*\[[ \t]*game[ \t]*\.[ \t]*steam[ \t]*\][ \t]*(?:#.*)?$"
)
_ANY_HEADER = re.compile(r"^[ \t]*\[")


def _steam_table(app_id: int, connect: str) -> str:
    return f'[game.steam]\napp_id = {app_id}\nconnect = "{connect}"\n'


def steam_table_text(text: str, app_id: int, connect: str) -> str:
    """`joinery.toml` text with its `[game.steam]` table set; all else kept."""
    lines = text.splitlines(keepends=True)
    start = next(
        (i for i, line in enumerate(lines) if _STEAM_HEADER.match(line.rstrip("\r\n"))),
        None,
    )
    block = _steam_table(app_id, connect)
    if start is None:
        body = text.rstrip("\n")
        return (body + "\n\n" if body else "") + block
    end = next(
        (i for i in range(start + 1, len(lines)) if _ANY_HEADER.match(lines[i])),
        len(lines),
    )
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return "".join(lines[:start]) + block + "".join(lines[end:])


def configure(project: Path, app_id: int | None, connect: Connect = "steam") -> None:
    """Record the Steam app id and how players connect.

    Writes `joinery.toml [game.steam]` (app_id, connect) and project.godot
    `steam/app_id` and `steam/connect`. An app id of None or 0 means "add it
    later". Nothing is sent anywhere.
    """
    app = 0 if app_id is None else app_id
    if isinstance(app, bool) or not isinstance(app, int) or app < 0 or app >= 2**32:
        raise SteamSetupError("The Steam app ID must be digits only.")
    if connect not in CONNECT_CHOICES:
        raise SteamSetupError("Players connect through Steam or Direct (IP).")
    config_path = project / "joinery.toml"
    godot_path = project / "project.godot"
    if not godot_path.is_file():
        raise SteamSetupError("This folder has no project.godot.")
    text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    new_text = steam_table_text(text, app, connect)
    tomllib.loads(new_text)  # never write a joinery.toml that does not read back
    godot_text = godot_path.read_text(encoding="utf-8")
    godot = GodotConfig.parse(godot_text)
    godot.set("steam", "app_id", to_variant(app))
    godot.set("steam", "connect", to_variant(connect))
    if new_text != text:
        config_path.write_text(new_text, encoding="utf-8")
    if godot.dump() != godot_text:
        godot_path.write_text(godot.dump(), encoding="utf-8")


def read_steam(project: Path) -> dict[str, object]:
    """`joinery.toml [game.steam]` as written by `configure` ({} when absent)."""
    try:
        data = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    game = data.get("game")
    steam = game.get("steam") if isinstance(game, dict) else None
    return dict(steam) if isinstance(steam, dict) else {}
