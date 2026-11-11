"""Godot export templates for `joinery share build`: pinned, verified, never fetched unasked.

The release is pinned in `policy/export_templates.toml` (version, URL, the
archive's SHA-512 and each needed file's SHA-256). Joinery keeps the files it
exports with in its own cache, inside a private Godot data folder
(`~/.cache/joinery/export-templates/home`), and runs the exporter with HOME
pointing there; the user's own Godot settings are never touched and no
absolute template path is written into the project.

A file is taken, in order, from: that folder; templates the user installed
with Godot's own manager; the archive already in the cache (the one the
release checks use). Each must match its pinned hash. Downloading the 1.2 GB
archive happens only when the caller passes ``download=True``, which only the
user's explicit action does (ADR 0046).
"""

from __future__ import annotations

import os
import shutil
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from joinery.catalog import ROOT
from joinery.pinned import Opener, PinnedDownloadError, digest, download

POLICY_PATH = ROOT / "policy" / "export_templates.toml"


class TemplatesMissing(Exception):
    """The templates are not on this computer and the user has not asked to download them."""


@dataclass(frozen=True)
class Pin:
    version: str
    url: str
    sha512: str
    size: int
    files: dict[str, str]

    @property
    def archive_name(self) -> str:
        return PurePosixPath(urlparse(self.url).path).name


def load_pin(path: Path = POLICY_PATH) -> Pin:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return Pin(
        version=str(raw["version"]),
        url=str(raw["url"]),
        sha512=str(raw["sha512"]),
        size=int(raw["size"]),
        files={str(k): str(v) for k, v in raw["files"].items()},
    )


def default_cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    return (
        (Path(base) if base else Path.home() / ".cache")
        / "joinery"
        / "export-templates"
    )


def _data_dir(home: Path) -> Path:
    """Godot's user data folder for a given HOME, as Godot itself finds it."""
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Godot"
    if os.name == "nt":
        return home / "AppData" / "Roaming" / "Godot"
    return home / ".local" / "share" / "godot"


def home(cache: Path | None = None) -> Path:
    return (cache or default_cache()) / "home"


def template_dir(pin: Pin, cache: Path | None = None) -> Path:
    return _data_dir(home(cache)) / "export_templates" / pin.version


def environment(cache: Path | None = None) -> dict[str, str]:
    """Variables that make Godot use Joinery's private data folder (and its templates)."""
    root = home(cache)
    return {
        "HOME": str(root),
        "XDG_DATA_HOME": str(root / ".local" / "share"),
        "XDG_CONFIG_HOME": str(root / ".config"),
        "XDG_CACHE_HOME": str(root / ".cache"),
        "APPDATA": str(root / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(root / "AppData" / "Local"),
    }


def _users_templates(pin: Pin) -> Path:
    return _data_dir(Path.home()) / "export_templates" / pin.version


def _good(path: Path, sha256: str) -> bool:
    return path.is_file() and digest(path) == sha256


def missing(
    needed: list[str], cache: Path | None = None, pin: Pin | None = None
) -> list[str]:
    """The needed template files not yet ready in Joinery's folder (hash-checked)."""
    pin = pin or load_pin()
    folder = template_dir(pin, cache)
    return [name for name in needed if not _good(folder / name, pin.files[name])]


def available(
    needed: list[str], cache: Path | None = None, pin: Pin | None = None
) -> bool:
    """True when a build needs no download: every file is ready, installed or in the archive."""
    pin = pin or load_pin()
    users = _users_templates(pin)
    rest = [n for n in missing(needed, cache, pin) if not _good(users / n, pin.files[n])]
    return not rest or archive_ready(cache, pin)


def archive_ready(cache: Path | None = None, pin: Pin | None = None) -> bool:
    pin = pin or load_pin()
    archive = (cache or default_cache()) / pin.archive_name
    return archive.is_file() and archive.stat().st_size == pin.size


def prepare(
    needed: list[str],
    *,
    cache: Path | None = None,
    pin: Pin | None = None,
    allow_download: bool = False,
    opener: Opener | None = None,
) -> Path:
    """Joinery's template folder holding every file in `needed`, each hash-verified.

    Raises `TemplatesMissing` when a file can only come from a download the user
    has not asked for, and `PinnedDownloadError` when a download fails or does
    not match.
    """
    pin = pin or load_pin()
    cache = cache or default_cache()
    unknown = sorted(set(needed) - set(pin.files))
    if unknown:
        raise ValueError("Not a pinned template file: " + ", ".join(unknown))
    folder = template_dir(pin, cache)
    folder.mkdir(parents=True, exist_ok=True)
    wanted = [name for name in ["version.txt", *needed] if name in pin.files]
    todo = [
        name
        for name in dict.fromkeys(wanted)
        if not _good(folder / name, pin.files[name])
    ]
    users = _users_templates(pin)
    for name in list(todo):
        if _good(users / name, pin.files[name]):
            shutil.copyfile(users / name, folder / name)
            todo.remove(name)
    if not todo:
        return _finish(folder)
    archive = cache / pin.archive_name
    if not archive.is_file():
        if not allow_download:
            raise TemplatesMissing(
                f"Godot {pin.version.removesuffix('.stable')} export templates aren't on "
                f"this computer. Download them ({pin.size / 1e9:.1f} GB) to build."
            )
        download(
            pin.url,
            archive,
            what="the Godot export templates",
            size=pin.size,
            expected=pin.sha512,
            algorithm="sha512",
            opener=opener,
        )
    elif digest(archive, "sha512") != pin.sha512:
        archive.unlink()
        raise PinnedDownloadError(
            "The export templates in the cache did not match the pinned release, so "
            "they were deleted. Download them again."
        )
    with zipfile.ZipFile(archive) as bundle:
        for name in todo:
            target = folder / name
            partial = target.with_name(name + ".part")
            with bundle.open("templates/" + name) as source, partial.open("wb") as out:
                shutil.copyfileobj(source, out, 1 << 20)
            if digest(partial) != pin.files[name]:
                partial.unlink()
                raise PinnedDownloadError(
                    f"{name} in the export templates did not match its pinned hash."
                )
            partial.replace(target)
    return _finish(folder)


def _finish(folder: Path) -> Path:
    for path in folder.iterdir():
        if path.is_file() and path.name != "version.txt" and path.suffix != ".zip":
            path.chmod(0o755)
    return folder
