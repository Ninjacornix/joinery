"""GodotSteam: pinned, hash-checked, Steam projects only, safely extracted."""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import stat
import tomllib
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse

import pytest

from joinery import godotsteam, lockfile
from joinery.catalog import load_catalog
from joinery.godot_config import GodotConfig
from joinery.godotsteam import (
    OFFLINE_MESSAGE,
    DownloadFailed,
    Release,
    SteamSetupError,
    configure,
    install,
    load_release,
    uninstall,
)
from joinery.lockfile import check_assets, read_extensions
from joinery.policy import load_denylist, url_violation

LICENCE = b"MIT License\n\nCopyright (c) GodotSteam\n"
GDEXTENSION = b'[configuration]\nentry_symbol = "godotsteam_init"\n'
LOCK = "version = 1\n"


def _zip(entries: list[tuple[str, bytes]], links: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in entries:
            bundle.writestr(name, data)
        for name in links:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(info, "../../outside")
    return buffer.getvalue()


GOOD = [
    ("addons/", b""),
    ("addons/godotsteam/godotsteam.gdextension", GDEXTENSION),
    ("addons/godotsteam/license.md", LICENCE),
    ("addons/godotsteam/readme.md", b"# GodotSteam\n"),
    (
        "addons/godotsteam/osx/libgodotsteam.macos.template_debug.universal.dylib",
        b"\x00bin",
    ),
    ("addons/godotsteam/plugin.cfg", b"[plugin]\n"),
    ("addons/godotsteam/godotsteam_plugin.gd", b"@tool\n"),
    ("addons/godotsteam/editor/updates/updates.gd", b"@tool\n"),
    ("readme-outside.txt", b"not extracted"),
]


#: Releases the tests pin, on top of the repo's own (see `pins`).
PINS: set[tuple[str, str, str]] = set()


def _release(data: bytes) -> Release:
    release = load_release().model_copy(
        update={"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    )
    PINS.add((release.name, release.url, release.sha256))
    return release


@pytest.fixture(autouse=True)
def pins(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lock check accepts the test releases as pinned, and only those."""
    PINS.clear()
    real = lockfile._pinned
    monkeypatch.setattr(lockfile, "_pinned", lambda: real() | PINS)


def _opener(data: bytes, calls: list[str] | None = None) -> Callable[..., Any]:
    def open_url(url: str, timeout: float = 0) -> io.BytesIO:
        if calls is not None:
            calls.append(url)
        return io.BytesIO(data)

    return open_url


def _never(url: str, timeout: float = 0) -> io.BytesIO:
    raise AssertionError(f"opened {url}")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "game"
    root.mkdir()
    (root / "project.godot").write_text(
        'config_version=5\n\n[application]\n\nconfig/name="Game"\n'
    )
    (root / "joinery.toml").write_text(
        '[tools]\nenabled = ["godot"]\n\n[game]\nblueprint = "blank"\n'
        'blueprint_version = 1\nfeatures = ["steam"]\nfeature_versions = { steam = 1 }\n'
    )
    (root / "joinery.lock").write_text(LOCK)
    return root


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    return tmp_path / "cache"


def _nothing_installed(project: Path) -> None:
    assert not (project / "addons/godotsteam").exists()
    assert (project / "joinery.lock").read_text() == LOCK
    assert not list(project.glob("addons/.godotsteam-*"))


# --- policy ---------------------------------------------------------------------


def test_policy_pins_one_release() -> None:
    raw = tomllib.loads(godotsteam.POLICY_PATH.read_text())
    assert re.fullmatch(r"[0-9a-f]{64}", raw["sha256"])
    release = load_release()
    assert release.licence == "MIT"
    assert release.layout.root == "addons/godotsteam"
    assert urlparse(release.url).scheme == "https"
    assert url_violation(release.url, load_denylist()) is None
    assert release.version in release.url and release.tag in release.url
    assert release.archive_name.endswith(".zip")
    # The release's self-updater stays out: it would bypass the pin.
    assert "editor/**" in release.layout.exclude
    assert "plugin.cfg" in release.layout.exclude


# --- refusals -------------------------------------------------------------------


def test_project_without_steam_never_opens_a_url(
    project: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "joinery.toml").write_text(
        '[game]\nblueprint = "blank"\nblueprint_version = 1\nfeatures = ["online-play"]\n'
    )
    monkeypatch.setattr(godotsteam, "urlopen", _never)
    with pytest.raises(SteamSetupError, match="not switched on"):
        install(project, cache=cache)
    assert not cache.exists()
    _nothing_installed(project)


def test_project_without_game_table_never_opens_a_url(
    project: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    monkeypatch.setattr(godotsteam, "urlopen", _never)
    with pytest.raises(SteamSetupError):
        install(project, cache=cache)
    _nothing_installed(project)


def test_hash_mismatch_raises_and_extracts_nothing(project: Path, cache: Path) -> None:
    data = _zip(GOOD)
    release = _release(data)
    tampered = _zip([*GOOD, ("addons/godotsteam/extra.gd", b"print('hi')")])
    release = release.model_copy(update={"size": len(tampered)})
    with pytest.raises(SteamSetupError, match="did not match its recorded hash"):
        install(project, cache=cache, release=release, opener=_opener(tampered))
    _nothing_installed(project)
    assert not any(p.is_file() for p in cache.rglob("*"))


def test_oversized_download_is_cut_off(project: Path, cache: Path) -> None:
    data = _zip(GOOD)
    release = _release(data)
    with pytest.raises(SteamSetupError, match="larger than the pinned release"):
        install(
            project, cache=cache, release=release, opener=_opener(data + b"\0" * 10)
        )
    _nothing_installed(project)
    assert not any(p.is_file() for p in cache.rglob("*"))


def test_redirects_are_held_to_https() -> None:
    from email.message import Message
    from urllib.request import Request

    handler = godotsteam._CheckedRedirects()
    with pytest.raises(SteamSetupError, match="https"):
        handler.redirect_request(
            Request("https://codeberg.org/a.zip"),
            io.BytesIO(),
            302,
            "Found",
            Message(),
            "http://codeberg.org/a.zip",
        )


def test_tampered_cache_is_deleted_and_downloaded_again(
    project: Path, cache: Path
) -> None:
    data = _zip(GOOD)
    release = _release(data)
    cached = cache / release.version / release.archive_name
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"swapped")
    calls: list[str] = []
    install(project, cache=cache, release=release, opener=_opener(data, calls))
    assert calls == [release.url]
    assert cached.read_bytes() == data


def test_offline_gives_a_plain_message(project: Path, cache: Path) -> None:
    def offline(url: str, timeout: float = 0) -> io.BytesIO:
        raise URLError("no network")

    with pytest.raises(DownloadFailed) as caught:
        install(project, cache=cache, release=_release(_zip(GOOD)), opener=offline)
    assert str(caught.value) == OFFLINE_MESSAGE
    assert str(caught.value) == (
        "Couldn't download GodotSteam. Steam starts working once it's installed: "
        "run joinery feature add steam again."
    )
    _nothing_installed(project)
    assert not any(p.is_file() for p in cache.rglob("*"))


@pytest.mark.parametrize(
    "bad",
    [
        "addons/godotsteam/../../escape.txt",
        "../escape.txt",
        "/tmp/escape.txt",
        "addons\\godotsteam\\..\\..\\escape.txt",
        "C:/escape.txt",
    ],
)
def test_unsafe_paths_are_refused(project: Path, cache: Path, bad: str) -> None:
    data = _zip([*GOOD, (bad, b"escaped")])
    with pytest.raises(SteamSetupError, match="unsafe path"):
        install(project, cache=cache, release=_release(data), opener=_opener(data))
    _nothing_installed(project)
    assert not (project.parent / "escape.txt").exists()
    assert not (project / "escape.txt").exists()


def test_symlinks_are_refused(project: Path, cache: Path) -> None:
    data = _zip(GOOD, links=("addons/godotsteam/osx/link.dylib",))
    with pytest.raises(SteamSetupError, match="link"):
        install(project, cache=cache, release=_release(data), opener=_opener(data))
    _nothing_installed(project)


def test_archive_without_the_extension_is_refused(project: Path, cache: Path) -> None:
    data = _zip([("addons/godotsteam/license.md", LICENCE)])
    with pytest.raises(SteamSetupError, match="missing godotsteam.gdextension"):
        install(project, cache=cache, release=_release(data), opener=_opener(data))
    _nothing_installed(project)


# --- install, uninstall ---------------------------------------------------------


def test_install_writes_files_licence_and_lock_entry(
    project: Path, cache: Path
) -> None:
    data = _zip(GOOD)
    release = _release(data)
    done = install(project, cache=cache, release=release, opener=_opener(data))
    folder = project / "addons/godotsteam"
    assert done.path == folder and not done.already and done.version == release.version
    assert (folder / "godotsteam.gdextension").read_bytes() == GDEXTENSION
    assert (folder / "LICENSE").read_bytes() == LICENCE
    assert (folder / "osx/libgodotsteam.macos.template_debug.universal.dylib").is_file()
    # Only addons/godotsteam/**, without the self-updater.
    assert not (folder / "plugin.cfg").exists()
    assert not (folder / "godotsteam_plugin.gd").exists()
    assert not (folder / "editor").exists()
    assert not (project / "readme-outside.txt").exists()
    assert not list((project / "addons").glob(".godotsteam-*"))
    [entry] = read_extensions(project)
    assert entry.model_dump() | {"files_sha256": ""} == {
        "name": "GodotSteam GDExtension",
        "version": release.version,
        "url": release.url,
        "sha256": release.sha256,
        "licence": "MIT",
        "origin": "pinned-download",
        "path": "addons/godotsteam",
        "files_sha256": "",
    }
    assert check_assets(project, load_catalog()) == []
    # The archive stays in the per-user cache, keyed by version.
    assert (cache / release.version / release.archive_name).read_bytes() == data


def test_install_is_idempotent(project: Path, cache: Path) -> None:
    data = _zip(GOOD)
    release = _release(data)
    install(project, cache=cache, release=release, opener=_opener(data))
    lock = (project / "joinery.lock").read_text()
    again = install(project, cache=cache, release=release, opener=_never)
    assert again.already
    assert (project / "joinery.lock").read_text() == lock
    assert lock.count("[[extensions]]") == 1


def test_install_repairs_changed_files_from_the_cache(
    project: Path, cache: Path
) -> None:
    data = _zip(GOOD)
    release = _release(data)
    install(project, cache=cache, release=release, opener=_opener(data))
    gdextension = project / "addons/godotsteam/godotsteam.gdextension"
    gdextension.write_text("changed")
    assert check_assets(project, load_catalog()) != []
    done = install(project, cache=cache, release=release, opener=_never)
    assert not done.already
    assert gdextension.read_bytes() == GDEXTENSION
    assert check_assets(project, load_catalog()) == []
    assert (project / "joinery.lock").read_text().count("[[extensions]]") == 1


def test_uninstall_restores_the_project(project: Path, cache: Path) -> None:
    data = _zip(GOOD)
    release = _release(data)
    install(project, cache=cache, release=release, opener=_opener(data))
    assert uninstall(project, release=release)
    assert not (project / "addons/godotsteam").exists()
    assert (project / "joinery.lock").read_text() == LOCK
    assert read_extensions(project) == []
    assert not uninstall(project, release=release)


def test_install_lists_the_extension_for_startup(project: Path, cache: Path) -> None:
    """Godot loads it at startup, not mid-scan on the first import."""
    data = _zip(GOOD)
    release = _release(data)
    listing = project / ".godot/extension_list.cfg"
    listing.parent.mkdir()
    listing.write_text("res://addons/other/other.gdextension\n")
    install(project, cache=cache, release=release, opener=_opener(data))
    install(project, cache=cache, release=release, opener=_never)
    assert listing.read_text().splitlines() == [
        "res://addons/other/other.gdextension",
        "res://addons/godotsteam/godotsteam.gdextension",
    ]
    uninstall(project, release=release)
    assert listing.read_text() == "res://addons/other/other.gdextension\n"


def test_uninstall_unlists_an_extension_whose_folder_is_gone(
    project: Path, cache: Path
) -> None:
    data = _zip(GOOD)
    release = _release(data)
    install(project, cache=cache, release=release, opener=_opener(data))
    listing = project / ".godot/extension_list.cfg"
    assert listing.is_file()
    shutil.rmtree(project / "addons/godotsteam")
    assert uninstall(project, release=release)
    assert not listing.exists()
    assert read_extensions(project) == []


def test_uninstall_leaves_a_copy_joinery_did_not_install(project: Path) -> None:
    own = project / "addons/godotsteam/godotsteam.gdextension"
    own.parent.mkdir(parents=True)
    own.write_bytes(GDEXTENSION)
    assert not uninstall(project, release=_release(_zip(GOOD)))
    assert own.read_bytes() == GDEXTENSION


def test_uninstall_keeps_other_lock_entries(project: Path, cache: Path) -> None:
    (project / "art").mkdir()
    (project / "art/door.blend").write_bytes(b"door")
    (project / "recipes").mkdir()
    (project / "recipes/door.toml").write_text("ops = []\n")
    sha = hashlib.sha256(b"door").hexdigest()
    lock = (
        f'version = 1\n\n[[asset]]\npath = "art/door.blend"\nsha256 = "{sha}"\n'
        'origin = "tool-created"\ntool = "blender"\nrecipe = "recipes/door.toml"\n'
    )
    (project / "joinery.lock").write_text(lock)
    data = _zip(GOOD)
    release = _release(data)
    install(project, cache=cache, release=release, opener=_opener(data))
    assert check_assets(project, load_catalog()) == []
    uninstall(project, release=release)
    assert (project / "joinery.lock").read_text() == lock


# --- configure ------------------------------------------------------------------


def _godot(project: Path) -> GodotConfig:
    return GodotConfig.parse((project / "project.godot").read_text())


def test_configure_writes_joinery_toml_and_project_godot(project: Path) -> None:
    configure(project, 480, "steam")
    data = tomllib.loads((project / "joinery.toml").read_text())
    assert data["game"]["steam"] == {"app_id": 480, "connect": "steam"}
    assert data["game"]["features"] == ["steam"]
    assert data["tools"] == {"enabled": ["godot"]}
    godot = _godot(project)
    assert godot.get("steam", "app_id") == "480"
    assert godot.get("steam", "connect") == '"steam"'
    assert godot.get("application", "config/name") == '"Game"'


def test_configure_again_replaces_the_table(project: Path) -> None:
    configure(project, 480, "steam")
    configure(project, None, "direct")
    text = (project / "joinery.toml").read_text()
    assert text.count("[game.steam]") == 1
    assert tomllib.loads(text)["game"]["steam"] == {"app_id": 0, "connect": "direct"}
    godot = _godot(project)
    assert godot.get("steam", "app_id") == "0"
    assert godot.get("steam", "connect") == '"direct"'


def test_configure_keeps_tables_after_it(project: Path) -> None:
    configure(project, 480, "steam")
    path = project / "joinery.toml"
    path.write_text(path.read_text() + '\n[export]\nname = "x"\n')
    configure(project, 12345, "steam")
    data = tomllib.loads(path.read_text())
    assert data["game"]["steam"]["app_id"] == 12345
    assert data["export"] == {"name": "x"}


@pytest.mark.parametrize("bad", [-1, 2**32, True])
def test_configure_refuses_bad_app_ids(project: Path, bad: int) -> None:
    before = (project / "joinery.toml").read_text()
    with pytest.raises(SteamSetupError, match="digits only"):
        configure(project, bad, "steam")
    assert (project / "joinery.toml").read_text() == before


def test_configure_refuses_unknown_connect(project: Path) -> None:
    with pytest.raises(SteamSetupError):
        configure(project, 480, "lan")  # type: ignore[arg-type]


def test_steam_table_does_not_break_the_lock_check(project: Path, cache: Path) -> None:
    configure(project, 480, "steam")
    assert check_assets(project, load_catalog()) == []
    data = _zip(GOOD)
    release = _release(data)
    install(project, cache=cache, release=release, opener=_opener(data))
    assert install(project, cache=cache, release=release, opener=_never).already
