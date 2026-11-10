"""The online-play feature: host, join, LAN discovery and a dedicated server.

The Godot tests run two real game processes on localhost (stock Godot,
headless) with `res://tests/features/online-play/pair_test.gd`:

- host and join: the joining player's movement reaches the host, a world
  action the joining player asks for runs on the server and reaches both, the
  starting point's own objects replicate (its adapter's `blueprint_check.gd`),
  and a player who leaves is despawned on the host;
- the same against a dedicated server (`--server`, no local player, no menus);
- a client that finds the host through its LAN beacon on 127.0.0.1;
- a client with another protocol version is refused with a reason.

`lobby_test.gd` drives the "Play online" page and the session switch in one
process. Nothing here downloads anything; ENet ships with Godot.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.godot_run import (
    Completed,
    boot,
    free_port,
    make_project,
    needs_godot,
    run_godot,
    run_pair,
)
from tests.blueprints.test_core import check_overlay

FEATURE = "online-play"
FEATURE_DIR = blueprints.FEATURES_DIR / FEATURE
ROOTS = ("features/online-play/", "tests/features/online-play/")
PAIR = "res://tests/features/online-play/pair_test.gd"
LOBBY = "res://tests/features/online-play/lobby_test.gd"
SETTINGS = "--settings-path=user://test_settings.cfg"
CHECKS_PASSED = "online: starting-point checks passed"
PRESET = "Linux Dedicated Server"
OTHER_FEATURES = ("controller", "save-load", "steam", "translations")


def _blueprint_ids() -> list[str]:
    return sorted(b.id for b in blueprints.list_blueprints())


def _feature() -> blueprints.Feature:
    return blueprints.load_feature(FEATURE_DIR)


# --- Static ------------------------------------------------------------------


def test_feature_declares_the_contract() -> None:
    feature = _feature()
    assert feature.name == "Online play"
    assert feature.blueprints == ["*"]
    assert feature.requires == [] and feature.conflicts == []
    assert feature.external == []
    assert feature.merge.autoload == {
        "Net": "res://features/online-play/net.gd",
        "Lobby": "res://features/online-play/lobby.gd",
    }
    settings = feature.merge.settings
    assert settings["online/protocol_version"] == 1
    assert settings["online/default_port"] == 24565
    assert settings["online/lan_port"] == 47990
    assert settings["online/lan_broadcast"] == "255.255.255.255"
    assert set(feature.adapters) == {"top-down"}
    top_down = feature.adapters["top-down"].settings
    assert top_down["online/spawn_parents"] == ["Enemies"]
    assert top_down["online/spawnable_scenes"] == ["res://game/enemies/chaser.tscn"]


def test_overlays_are_code_only() -> None:
    assert check_overlay(FEATURE_DIR / "overlay", ROOTS) == []
    for adapter in sorted((FEATURE_DIR / "adapters").iterdir()):
        assert check_overlay(adapter / "overlay", ROOTS) == [], adapter.name


def test_every_blueprint_has_an_adapter() -> None:
    """Each starting point ships online checks; runtime adapters extend NetAdapter."""
    for blueprint in _blueprint_ids():
        overlay = FEATURE_DIR / "adapters" / blueprint / "overlay"
        check = overlay / "tests/features/online-play/blueprint_check.gd"
        assert check.is_file(), blueprint
        assert check.read_text().startswith(
            'extends "res://tests/features/online-play/pair_check.gd"'
        ), blueprint
        runtime = overlay / "features/online-play/adapter.gd"
        if runtime.is_file():
            assert runtime.read_text().startswith("extends NetAdapter"), blueprint
    for adapter in (FEATURE_DIR / "adapters").iterdir():
        assert adapter.name in _blueprint_ids(), (
            f"adapter for unknown blueprint {adapter.name}"
        )


def test_feature_passes_catalogue_checks() -> None:
    problems = [p for p in blueprints.validate_catalogue() if FEATURE in p]
    assert problems == []


def test_online_play_combines_with_every_blueprint() -> None:
    for blueprint in _blueprint_ids():
        resolution = blueprints.resolve(blueprint, [FEATURE])
        assert resolution.problems == [], (blueprint, resolution.problems)
        assert [f.id for f in resolution.features] == [FEATURE]


def _presets(project: Path) -> list[str]:
    path = project / "export_presets.cfg"
    if not path.is_file():
        return []
    return re.findall(r'^name="([^"]*)"', path.read_text(), re.MULTILINE)


def test_apply_then_remove_leaves_the_project_as_it_was(tmp_path: Path) -> None:
    project = make_project(tmp_path, "top-down")
    before = (project / "project.godot").read_text()
    presets_before = _presets(project)
    blueprints.add_feature(project, FEATURE)
    text = (project / "project.godot").read_text()
    assert 'Net="*res://features/online-play/net.gd"' in text
    assert 'Lobby="*res://features/online-play/lobby.gd"' in text
    assert "protocol_version=1" in text
    assert 'spawn_parents=PackedStringArray("Enemies")' in text
    for path in (
        "features/online-play/net.gd",
        "features/online-play/net_adapter.gd",
        "features/online-play/adapter.gd",
        "tests/features/online-play/pair_test.gd",
        "tests/features/online-play/blueprint_check.gd",
    ):
        assert (project / path).is_file(), path
    assert _presets(project) == [*presets_before, PRESET]
    preset_text = (project / "export_presets.cfg").read_text()
    assert 'platform="Linux"' in preset_text
    assert "dedicated_server=true" in preset_text
    assert 'custom_features="dedicated_server"' in preset_text
    assert 'export_path="build/server/server.x86_64"' in preset_text
    again = blueprints.plan(project, "top-down", [FEATURE])
    assert again.conflicts == [] and again.changes == []

    removed = blueprints.remove_feature(project, FEATURE)
    assert removed.kept == []
    assert (project / "project.godot").read_text() == before
    assert _presets(project) == presets_before
    assert not (project / "features/online-play").exists()
    assert not (project / "tests/features/online-play").exists()


# --- Godot -------------------------------------------------------------------

ProjectFor = Callable[[str], Path]


@pytest.fixture(scope="module")
def project_for(tmp_path_factory: pytest.TempPathFactory) -> ProjectFor:
    """Projects with online play, made once per module: "<blueprint>[+feature…]"."""
    made: dict[str, Path] = {}

    def get(key: str) -> Path:
        if key not in made:
            blueprint, *extra = key.split("+")
            made[key] = make_project(
                tmp_path_factory.mktemp("net"), blueprint, [FEATURE, *extra]
            )
        return made[key]

    return get


def _pair(
    project: Path, host: list[str], client: list[str]
) -> tuple[Completed, Completed]:
    return run_pair(
        project,
        [*host, SETTINGS],
        [*client, SETTINGS],
        timeout=180,
        script=PAIR,
    )


def _checked(host: Completed, client: Completed) -> None:
    for side in (host, client):
        assert CHECKS_PASSED in side.output, side.output[-2000:]


@needs_godot
@pytest.mark.parametrize("blueprint", _blueprint_ids())
def test_host_and_join_replicate(project_for: ProjectFor, blueprint: str) -> None:
    project = project_for(blueprint)
    lan = [f"--lan-port={free_port()}", "--lan-broadcast=127.0.0.1"]
    host, client = _pair(
        project,
        ["--host", "--port={port}", "--autostart", *lan],
        ["--join=127.0.0.1:{port}", "--autostart", *lan],
    )
    assert "online_host: ok" in host.output
    assert "online_client: ok" in client.output
    _checked(host, client)


@needs_godot
@pytest.mark.parametrize("blueprint", _blueprint_ids())
def test_dedicated_server(project_for: ProjectFor, blueprint: str) -> None:
    project = project_for(blueprint)
    server, client = _pair(
        project,
        [
            "--server",
            "--port={port}",
            f"--lan-port={free_port()}",
            "--lan-broadcast=127.0.0.1",
        ],
        ["--join=127.0.0.1:{port}", "--autostart"],
    )
    assert "online_server: ok" in server.output
    assert "Online: dedicated server listening on port" in server.output
    _checked(server, client)


@needs_godot
def test_client_finds_the_host_on_the_lan(
    project_for: ProjectFor,
) -> None:
    project = project_for("platformer-2d")
    lan = [f"--lan-port={free_port()}", "--lan-broadcast=127.0.0.1"]
    host, client = _pair(
        project,
        ["--host", "--port={port}", "--autostart", *lan],
        ["--find-lan", "--port={port}", "--autostart", *lan],
    )
    _checked(host, client)


@needs_godot
def test_mismatched_version_is_refused(
    project_for: ProjectFor,
) -> None:
    project = project_for("blank")
    lan = [f"--lan-port={free_port()}", "--lan-broadcast=127.0.0.1"]
    host, client = _pair(
        project,
        ["--host", "--port={port}", "--autostart", "--expect-refused", *lan],
        [
            "--join=127.0.0.1:{port}",
            "--autostart",
            "--expect-refused",
            "--protocol-version=99",
        ],
    )
    assert "online_host: ok" in host.output
    assert "online_client: ok" in client.output


@needs_godot
@pytest.mark.parametrize("blueprint", ["platformer-2d", "puzzle-cards"])
def test_lobby_page_hosts_and_leaves(project_for: ProjectFor, blueprint: str) -> None:
    project = project_for(blueprint)
    run_godot(
        project,
        LOBBY,
        (
            f"--port={free_port()}",
            f"--lan-port={free_port()}",
            "--lan-broadcast=127.0.0.1",
            SETTINGS,
        ),
        timeout=180,
        marker="online_lobby",
    )


@needs_godot
@pytest.mark.parametrize("blueprint", _blueprint_ids())
def test_offline_boot_is_quiet(project_for: ProjectFor, blueprint: str) -> None:
    """With online play added, a local game boots with no warning or error."""
    project = project_for(blueprint)
    output = boot(project, frames=120).output
    noisy = [
        line for line in output.splitlines() if "WARNING" in line or "ERROR" in line
    ]
    assert noisy == [], "\n".join(noisy[:10])


@needs_godot
def test_with_every_other_feature(
    project_for: ProjectFor,
) -> None:
    """Online play next to every other feature still replicates."""
    extra = [
        f for f in OTHER_FEATURES if f in {x.id for x in blueprints.list_features()}
    ]
    resolution = blueprints.resolve("platformer-2d", [FEATURE, *extra])
    if resolution.problems:
        pytest.skip("features do not combine yet: " + "; ".join(resolution.problems))
    project = project_for("+".join(["platformer-2d", *extra]))
    lan = [f"--lan-port={free_port()}", "--lan-broadcast=127.0.0.1"]
    host, client = _pair(
        project,
        ["--host", "--port={port}", "--autostart", *lan],
        ["--join=127.0.0.1:{port}", "--autostart", *lan],
    )
    _checked(host, client)
