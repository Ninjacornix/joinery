"""The steam feature: a GodotSteam wrapper that is a quiet no-op without it.

Most tests use stock Godot without the extension.
`res://tests/features/steam/steam_test.gd` checks the Steam-absent path (every
call false or "", nothing printed), then registers a fake `Steam` singleton
and checks that calls are routed, lobby signals re-emitted, the app id gate,
the connect setting and the achievement on level completion. The online-play
bridge is checked against a stub `NetTransport` and, once online play is in
the catalogue, against the real one.

With the pinned GodotSteam release already in the per-user cache (it is never
downloaded here) and a platform it has binaries for, one more test installs it
into a project and boots headless to confirm every name the wrapper uses.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from pathlib import Path
from typing import IO

import pytest

from joinery import blueprints, godotsteam
from tests.blueprints.godot_run import (
    boot,
    free_port,
    godot_binary,
    godot_env,
    import_project,
    make_project,
    needs_godot,
    run_godot,
)
from tests.blueprints.test_core import check_overlay

FEATURE = "steam"
STEAM_TEST = "res://tests/features/steam/steam_test.gd"
SERVICE = "res://features/steam/steam_service.gd"
EXTENSION_CHECK = "res://tests/features/steam/extension_check.gd"
ROOTS = ("features/steam/", "tests/features/steam/")
# A stand-in for online play's transport base class, written only into test
# projects that do not have the online-play feature.
STUB_NET_TRANSPORT = """class_name NetTransport
extends RefCounted

var last_error: Error = OK


func create_host(_port: int, _max_clients: int) -> MultiplayerPeer:
\tlast_error = ERR_UNAVAILABLE
\treturn null


func create_client(_address: String, _port: int) -> MultiplayerPeer:
\tlast_error = ERR_UNAVAILABLE
\treturn null


func describe() -> String:
\treturn "none"
"""


def _feature() -> blueprints.Feature:
    return blueprints.load_feature(blueprints.FEATURES_DIR / FEATURE)


def _has_feature(feature_id: str) -> bool:
    return any(f.id == feature_id for f in blueprints.list_features())


def _feature_complete(feature_id: str) -> bool:
    """In the catalogue, with every autoload it declares shipped."""
    found = [f for f in blueprints.list_features() if f.id == feature_id]
    if not found:
        return False
    overlay = found[0].dir / "overlay"
    return all(
        (overlay / path.removeprefix("res://")).is_file()
        for path in found[0].merge.autoload.values()
    )


def _quiet(output: str) -> None:
    """No warning or error lines at all (the feature must stay silent)."""
    noisy = [
        line
        for line in output.splitlines()
        if "WARNING" in line or "ERROR" in line or line.startswith("FAIL")
    ]
    assert noisy == [], "\n".join(noisy[:10])


def _stock_project(tmp_path: Path, blueprint: str, features: list[str]) -> Path:
    """A project as stock Godot sees it without GodotSteam.

    Creating a Steam project installs the pinned extension when it can (from
    the cache or the network); these tests are about the path without it, so
    it is taken out again.
    """
    project = make_project(tmp_path, blueprint, features)
    godotsteam.uninstall(project)
    assert not (project / "addons/godotsteam").exists()
    return project


def _settings(tmp_path: Path) -> str:
    return f"--settings-path={tmp_path / 'settings.cfg'}"


def test_steam_feature_declares_the_pinned_extension() -> None:
    feature = _feature()
    assert feature.name == "Steam"
    assert feature.blueprints == ["*"]
    assert feature.requires == [] and feature.conflicts == []
    assert feature.enhances == ["online-play"]
    assert [e.model_dump() for e in feature.external] == [
        {
            "name": "GodotSteam GDExtension",
            "source": "https://codeberg.org/godotsteam/godotsteam (release v4.23-gde, MIT)",
            "version": "4.23",
            "install": "downloaded by Joinery, pinned and hash-checked",
            "required": False,
            "licence": "MIT",
        }
    ]
    assert feature.merge.autoload == {"SteamService": SERVICE}
    assert feature.merge.settings == {
        "steam/app_id": 0,
        "steam/connect": "steam",
        "steam/achievement_on_complete": "",
        "steam/embed_callbacks": False,
    }
    assert feature.merge.input == {}


def test_steam_overlay_is_code_only() -> None:
    feature_dir = blueprints.FEATURES_DIR / FEATURE
    assert check_overlay(feature_dir / "overlay", ROOTS) == []
    shipped = [p for p in feature_dir.rglob("*") if p.is_file()]
    assert not [
        p for p in shipped if p.suffix in {".gdextension", ".so", ".dll", ".dylib"}
    ]
    service = (feature_dir / "overlay/features/steam/steam_service.gd").read_text()
    # Held as a plain Object, so stock Godot parses the script.
    assert "Engine.has_singleton(SINGLETON)" in service
    assert "Untested against the real extension" not in service
    assert "Checked against GodotSteam 4.23" in service
    assert "push_error" not in service and "push_warning" not in service
    transport = (feature_dir / "overlay/features/steam/steam_transport.gd").read_text()
    assert transport.startswith("extends NetTransport")
    assert "class_name" not in transport


def test_steam_feature_passes_catalogue_checks() -> None:
    problems = [
        p
        for p in blueprints.validate_catalogue()
        if FEATURE in p.split(":", 1)[0] or "steam" in p.lower()
    ]
    if not _has_feature("online-play"):
        problems = [
            p for p in problems if "enhances unknown feature online-play" not in p
        ]
    assert problems == []


def test_steam_combines_with_every_blueprint() -> None:
    for blueprint in blueprints.list_blueprints():
        resolution = blueprints.resolve(blueprint.id, [FEATURE])
        assert resolution.problems == [], (blueprint.id, resolution.problems)
        assert [f.id for f in resolution.features] == [FEATURE]
        assert resolution.added == {}


def test_apply_then_remove_leaves_the_project_as_it_was(tmp_path: Path) -> None:
    project = make_project(tmp_path, "blank")
    before = (project / "project.godot").read_text()
    blueprints.add_feature(project, FEATURE)
    text = (project / "project.godot").read_text()
    assert 'SteamService="*res://features/steam/steam_service.gd"' in text
    assert "[steam]" in text and "app_id=0" in text
    assert 'achievement_on_complete=""' in text
    assert (project / "features/steam/steam_service.gd").is_file()
    assert (project / "features/steam/steam_transport.gd").is_file()
    assert (project / "tests/features/steam/steam_test.gd").is_file()
    game = blueprints.project_game(project)
    assert game is not None and FEATURE in game["features"]
    again = blueprints.plan(project, "blank", [FEATURE])
    assert again.conflicts == [] and again.changes == []

    removed = blueprints.remove_feature(project, FEATURE)
    assert removed.kept == []
    assert (project / "project.godot").read_text() == before
    assert not (project / "features/steam").exists()
    assert not (project / "tests/features/steam").exists()


@needs_godot
def test_steam_absent_and_fake_singleton(tmp_path: Path) -> None:
    """Stock Godot: no extension, quiet no-ops; then a fake singleton."""
    project = _stock_project(tmp_path, "blank", [FEATURE])
    result = run_godot(
        project,
        STEAM_TEST,
        ("--autostart", _settings(tmp_path)),
        timeout=180,
        marker="steam",
    )
    _quiet(result.output)
    assert "steam bridge: checked" not in result.output


@needs_godot
def test_steam_absent_boot_prints_at_most_one_verbose_line(tmp_path: Path) -> None:
    project = _stock_project(tmp_path, "blank", [FEATURE])
    _quiet(boot(project, frames=90).output)
    binary = godot_binary()
    assert binary is not None
    import_project(project)
    process = subprocess.run(
        [
            str(binary),
            "--headless",
            "--verbose",
            "--path",
            str(project),
            "--fixed-fps",
            "60",
            "--quit-after",
            "30",
            "--",
            "--autostart",
            _settings(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=godot_env(project),
        check=False,
    )
    assert process.returncode == 0, process.stdout[-2000:] + process.stderr[-2000:]
    steam_lines = [
        line
        for line in (process.stdout + process.stderr).splitlines()
        if line.startswith("Steam:")
    ]
    assert steam_lines == [
        "Steam: the GodotSteam extension is not installed; Steam calls do nothing."
    ]


@needs_godot
def test_steam_bridge_with_a_stub_transport(tmp_path: Path) -> None:
    """The bridge, against online play's NetTransport contract (stubbed).

    The project has no online play, so the stub class and a stub `Net` stand
    in for it and the whole host and join flow runs.
    """
    project = _stock_project(tmp_path, "blank", [FEATURE])
    stub = project / "tests/features/steam/stub/net_transport.gd"
    stub.parent.mkdir(parents=True)
    stub.write_text(STUB_NET_TRANSPORT)
    result = run_godot(
        project,
        STEAM_TEST,
        ("--autostart", _settings(tmp_path)),
        timeout=180,
        marker="steam",
    )
    assert "steam bridge: checked" in result.output
    _quiet(result.output)


@needs_godot
def test_steam_with_online_play(tmp_path: Path) -> None:
    """With online play: ENet stays without a Steam peer, a failed Steam host
    puts ENet back, and with a peer the online page offers Steam, hosting a
    lobby hosts online play under the Steam name and leaving ends the bridge.
    """
    if not _feature_complete("online-play"):
        pytest.skip("online play is not in the catalogue yet")
    resolution = blueprints.resolve("blank", ["online-play", FEATURE])
    if resolution.problems:
        pytest.skip("features do not combine yet: " + "; ".join(resolution.problems))
    project = _stock_project(tmp_path, "blank", ["online-play", FEATURE])
    # Hosting starts online play's LAN beacon; keep it on this machine.
    lan = (f"--lan-port={free_port()}", "--lan-broadcast=127.0.0.1")
    result = run_godot(
        project,
        STEAM_TEST,
        ("--autostart", _settings(tmp_path), *lan),
        timeout=180,
        marker="steam",
    )
    assert "steam bridge: checked" in result.output
    _quiet(result.output)


def _blueprint_ids() -> list[str]:
    return sorted(b.id for b in blueprints.list_blueprints())


@needs_godot
@pytest.mark.parametrize("blueprint", _blueprint_ids())
def test_every_blueprint_boots_quietly_with_steam(
    tmp_path: Path, blueprint: str
) -> None:
    if blueprints.resolve(blueprint, [FEATURE]).problems:
        pytest.skip(f"{blueprint} does not resolve yet")
    project = make_project(tmp_path, blueprint, [FEATURE])
    output = boot(project, frames=90).output
    noisy = [line for line in output.splitlines() if "steam" in line.lower()]
    assert noisy == [], "\n".join(noisy[:10])


def test_feature_names_the_release_in_the_policy() -> None:
    release = godotsteam.load_release()
    [external] = _feature().external
    assert external.version == release.version
    assert release.tag in external.source and release.licence in external.source
    assert external.licence == release.licence
    service = (
        blueprints.FEATURES_DIR / FEATURE / "overlay/features/steam/steam_service.gd"
    ).read_text()
    assert f"GodotSteam {release.version}" in service


def test_configured_steam_table_keeps_the_project_readable(tmp_path: Path) -> None:
    """[game.steam] sits beside [game]; reading and rewriting [game] keeps it."""
    project = make_project(tmp_path, "blank", [FEATURE])
    godotsteam.configure(project, 480, "direct")
    assert godotsteam.read_steam(project) == {"app_id": 480, "connect": "direct"}
    assert 'connect="direct"' in (project / "project.godot").read_text()
    record = blueprints.read_record(project)
    assert record is not None and FEATURE in record.features
    blueprints.remove_feature(project, FEATURE)
    assert godotsteam.read_steam(project) == {"app_id": 480, "connect": "direct"}


def _cached_release() -> Path | None:
    """The pinned archive in the per-user cache, when it is there and intact."""
    release = godotsteam.load_release()
    archive = godotsteam.default_cache() / release.version / release.archive_name
    if not archive.is_file():
        return None
    if hashlib.sha256(archive.read_bytes()).hexdigest() != release.sha256:
        return None
    return godotsteam.default_cache()


def _release_has_binaries() -> bool:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        return True  # universal dylib
    if sys.platform.startswith("linux"):
        return machine in {"x86_64", "amd64", "aarch64", "arm64"}
    if sys.platform == "win32":
        return machine in {"amd64", "x86_64"}
    return False


@needs_godot
def test_real_extension_has_every_name_the_wrapper_uses(tmp_path: Path) -> None:
    """Boots headless with the real GodotSteam: the init methods, the other
    singleton methods, the lobby signals and their argument counts, and
    SteamMultiplayerPeer.create_host/create_client all exist. Steam is never
    started (the app id stays 0). Skipped unless the pinned release is in the
    cache already; the test never downloads it.
    """
    cache = _cached_release()
    if cache is None:
        pytest.skip("the pinned GodotSteam release is not in the cache")
    if not _release_has_binaries():
        pytest.skip("GodotSteam has no binaries for this platform")
    project = make_project(tmp_path, "blank", [FEATURE])
    godotsteam.install(project, cache=cache, opener=_no_download)
    result = run_godot(project, EXTENSION_CHECK, timeout=180, marker="steam extension")
    assert "FAIL" not in result.output
    # With the extension but no app id, the game boots quietly.
    output = boot(project, frames=60).output
    assert [line for line in output.splitlines() if line.startswith("Steam:")] == []


def _no_download(url: str, timeout: float = 0) -> IO[bytes]:
    raise AssertionError(f"tests never download ({url})")
