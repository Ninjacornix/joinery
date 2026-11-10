"""The wizard's game step: blueprint, brief, bible and Steam, one path for all."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from joinery import blueprints, game_setup, godotsteam
from joinery.catalog import load_catalog
from joinery.installer import Selection
from joinery.project import create_project


class SteamCalls:
    """Records the GodotSteam calls instead of downloading anything."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.fail = fail
        monkeypatch.setattr(godotsteam, "configure", self.configure)
        monkeypatch.setattr(godotsteam, "install", self.install)
        monkeypatch.setattr(godotsteam, "uninstall", self.uninstall)

    def configure(
        self, project: Path, app_id: int | None, connect: str = "steam"
    ) -> None:
        self.calls.append(("configure", (app_id, connect)))

    def install(self, project: Path, **_: Any) -> godotsteam.Installed:
        self.calls.append(("install", project))
        if self.fail:
            raise godotsteam.DownloadFailed()
        return godotsteam.Installed(
            project / "addons/godotsteam", "4.23", 3, already=False
        )

    def uninstall(self, project: Path, **_: Any) -> bool:
        self.calls.append(("uninstall", project))
        return True

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def new(tmp_path: Path, name: str = "game", **choices: Any) -> Path:
    """A project made the way `joinery new` makes it."""
    project = tmp_path / name
    create_project(
        project, "G", Selection(["godot"], []), load_catalog(), wizard=True, **choices
    )
    return project


def test_steam_selected_configures_and_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steam = SteamCalls(monkeypatch)
    seen: list[dict[str, Any]] = []
    project = new(
        tmp_path,
        blueprint="blank",
        features=["steam", "online-play"],
        steam_app_id="480",
        report=seen.append,
    )
    assert steam.names() == ["configure", "install"]
    # Online play is on, so the connect choice is kept and defaults to Steam.
    assert steam.calls[0][1] == (480, "steam")
    assert [s["step"] for s in seen] == [
        "addon",
        "blueprint",
        "brief",
        "bible",
        "steam",
        "steam-download",
    ]
    assert all(s["state"] == "done" for s in seen)
    record = blueprints.read_record(project)
    assert record is not None and set(record.features) == {"steam", "online-play"}


def test_connect_direct_is_passed_and_empty_app_id_means_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steam = SteamCalls(monkeypatch)
    new(
        tmp_path, blueprint="blank", features=["steam", "online-play"], connect="direct"
    )
    assert steam.calls[0] == ("configure", (None, "direct"))


def test_non_steam_project_never_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steam = SteamCalls(monkeypatch)
    new(tmp_path, blueprint="blank", features=["controller"])
    new(tmp_path, "plain")
    assert steam.calls == []


def test_game_only_projects_skip_the_addon_and_steam_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Harnesses that only boot the game get the blueprint, brief and bible.
    steam = SteamCalls(monkeypatch)
    project = tmp_path / "game"
    create_project(
        project,
        "G",
        Selection(["godot"], []),
        load_catalog(),
        blueprint="blank",
        features=["steam"],
    )
    assert steam.calls == []
    assert not (project / "addons").exists()
    assert (project / "design/brief.md").is_file() and (
        project / "art/bible.md"
    ).is_file()


def test_failed_download_is_reported_and_the_project_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    SteamCalls(monkeypatch, fail=True)
    seen: list[dict[str, Any]] = []
    project = new(tmp_path, blueprint="blank", features=["steam"], report=seen.append)
    assert (project / "project.godot").is_file()
    failed = [s for s in seen if s["state"] == "failed"]
    assert [s["step"] for s in failed] == ["steam-download"]
    assert "GodotSteam" in failed[0]["message"]


@pytest.mark.parametrize("app_id", ["48O", "12 34", "-5", "1e3"])
def test_non_digit_app_id_is_refused_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app_id: str
) -> None:
    steam = SteamCalls(monkeypatch)
    with pytest.raises(ValueError, match="digits only"):
        new(tmp_path, blueprint="blank", features=["steam"], steam_app_id=app_id)
    assert not (tmp_path / "game").exists()
    assert steam.calls == []


@pytest.mark.parametrize(
    "app_id", ["4294967296", "9999999999", "\u00b2", "\u0664\u0668\u0660"]
)
def test_app_id_the_wizard_lets_through_is_refused_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app_id: str
) -> None:
    # The wizard field allows up to 10 digits; Steam app IDs stop at 2**32 - 1,
    # and str.isdigit() also passes non-ASCII digits that int() refuses.
    monkeypatch.setenv("JOINERY_NO_DOWNLOADS", "1")
    project = new(tmp_path)
    before = sorted(p.relative_to(project) for p in project.rglob("*"))
    with pytest.raises(ValueError, match="Steam app ID"):
        game_setup.apply_choices(project, "blank", ["steam"], app_id, addon=False)
    assert sorted(p.relative_to(project) for p in project.rglob("*")) == before


def test_largest_app_id_is_kept() -> None:
    assert game_setup.check_app_id(" 4294967295 ") == "4294967295"


def test_app_id_without_steam_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="needs the Steam feature"):
        new(tmp_path, blueprint="blank", features=[], steam_app_id="480")
    assert not (tmp_path / "game").exists()


def test_cli_refuses_non_digit_app_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery.cli import main

    steam = SteamCalls(monkeypatch)
    project = tmp_path / "game"
    argv = ["new", str(project), "--tools", "godot", "--blueprint", "blank"]
    argv += ["--features", "steam", "--steam-app-id", "abc"]
    assert main(argv) == 1
    assert "digits only" in capsys.readouterr().err
    assert not project.exists()
    assert steam.calls == []


def test_game_apply_prints_one_json_line_per_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery.cli import main

    steam = SteamCalls(monkeypatch)
    project = new(tmp_path)
    (project / ".joinery-brief-answers.json").write_text(
        json.dumps({"pitch": "Flip it"})
    )
    (project / ".joinery-bible-answers.json").write_text(
        json.dumps({"references": "Hob: chunky shapes"})
    )
    argv = ["game", "apply", "--project", str(project), "--blueprint", "top-down"]
    argv += ["--features", "steam", "--steam-app-id", "480"]
    assert main(argv) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["step"] for line in lines] == [
        "addon",
        "blueprint",
        "brief",
        "bible",
        "steam",
        "steam-download",
        "finished",
    ]
    assert steam.names() == ["configure", "install"]
    assert "Flip it" in (project / "design/brief.md").read_text()
    bible = (project / "art/bible.md").read_text()
    assert "Hob: chunky shapes" in bible
    # The top-down preset filled the technical sections as suggestions.
    assert "starting point; change freely" in bible
    assert not (project / ".joinery-brief-answers.json").exists()
    assert not (project / ".joinery-bible-answers.json").exists()


def test_game_apply_reports_a_bad_choice_as_one_failed_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery.cli import main

    project = new(tmp_path)
    before = (project / "project.godot").read_text()
    argv = ["game", "apply", "--project", str(project), "--blueprint", "blank"]
    assert main([*argv, "--features", "nope"]) == 1
    line = json.loads(capsys.readouterr().out.strip())
    assert line == {
        "step": "error",
        "state": "failed",
        "message": "Unknown feature: nope",
    }
    assert (project / "project.godot").read_text() == before


def test_existing_brief_and_bible_are_kept(tmp_path: Path) -> None:
    project = new(tmp_path)
    (project / "design/brief.md").write_text("mine\n")
    (project / "art/bible.md").write_text("my bible\n")
    steps = game_setup.apply_choices(project, "blank", [], addon=False)
    states = {s["step"]: s["state"] for s in steps}
    assert states["brief"] == "skipped" and states["bible"] == "skipped"
    assert (project / "design/brief.md").read_text() == "mine\n"
    assert (project / "art/bible.md").read_text() == "my bible\n"


def test_translations_offer_the_pot_rebuild(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []
    project = new(
        tmp_path, blueprint="blank", features=["translations"], report=seen.append
    )
    offer = next(s for s in seen if s["step"] == "translations")["offer"]
    assert offer[-1] == "res://features/translations/tools/extract_pot.gd"
    assert str(project) in offer


def test_wizard_catalogue_lists_features_per_blueprint() -> None:
    data = game_setup.wizard_catalogue()
    first = next(b for b in data["blueprints"] if b["id"] == "first-person-3d")
    assert first["capture"] is None or Path(first["capture"]).name == "capture.png"
    by_id = {f["id"]: f for f in first["features"]}
    assert set(by_id) == {f["id"] for f in data["features"]}
    controller = by_id["controller"]
    assert controller["default"] is True
    assert controller["available"] is True and controller["reason"] == ""
    assert "autoload/Controller" in controller["adds"]["settings"]
    assert any(
        p.startswith("features/controller/") for p in controller["adds"]["files"]
    )
    assert by_id["steam"]["external"][0]["name"] == "GodotSteam GDExtension"


# --- A folder that already holds a game ---------------------------------------------

_WIZARD_GODOT = (
    "config_version=5\n\n[application]\n\n"
    'config/name="New Game"\n'
    'config/features=PackedStringArray("4.7", "Forward Plus")\n'
    'config/icon="res://icon.svg"\n'
)


def _wizard_folder(tmp_path: Path) -> Path:
    """What the new-project wizard has written when it runs `joinery game apply`."""
    project = tmp_path / "wizard"
    project.mkdir()
    (project / "project.godot").write_text(_WIZARD_GODOT)
    (project / "icon.svg").write_text("<svg/>\n")
    (project / ".editorconfig").write_text("root = true\n")
    (project / ".gitignore").write_text(".godot/\n")
    return project


def _existing_game(tmp_path: Path, language: str) -> Path:
    """A Godot game someone else made: a main scene, its scripts and scenes."""
    project = tmp_path / f"their-{language}-game"
    (project / "scenes").mkdir(parents=True)
    (project / "scripts").mkdir()
    features = '"4.7", "C#", "Forward Plus"' if language == "cs" else '"4.7"'
    (project / "project.godot").write_text(
        "config_version=5\n\n[application]\n\n"
        'config/name="Their Game"\n'
        'run/main_scene="res://scenes/main.tscn"\n'
        f"config/features=PackedStringArray({features})\n\n"
        "[input]\n\njump={}\n"
    )
    (project / "scenes/main.tscn").write_text(
        '[gd_scene format=3]\n\n[node name="Main" type="Node"]\n'
    )
    if language == "cs":
        (project / "scripts/Player.cs").write_text("public partial class Player {}\n")
        (project / "TheirGame.csproj").write_text('<Project Sdk="Godot.NET.Sdk/4.7.2" />\n')
    else:
        (project / "scripts/player.gd").write_text("extends Node\n")
    (project / "export_presets.cfg").write_text('[preset.0]\n\nname="Linux"\n')
    return project


def _snapshot(project: Path) -> dict[str, bytes]:
    return {
        p.relative_to(project).as_posix(): p.read_bytes()
        for p in sorted(project.rglob("*"))
        if p.is_file()
    }


def test_game_apply_on_a_fresh_wizard_folder_keeps_a_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery.cli import main

    project = _wizard_folder(tmp_path)
    argv = ["game", "apply", "--project", str(project), "--blueprint", "blank"]
    assert main([*argv, "--no-addon", "--features="]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    step = next(line for line in lines if line["step"] == "blueprint")
    assert step["state"] == "done"
    assert step["backup"].startswith(".joinery/backup/")
    assert f"copied to {step['backup']}" in step["message"]
    # The old project.godot is kept byte for byte; the new one has the game.
    assert (project / step["backup"] / "project.godot").read_text() == _WIZARD_GODOT
    assert "run/main_scene" in (project / "project.godot").read_text()
    # Backups are for recovering, not for the project's history.
    assert (project / ".joinery/backup/.gitignore").read_text() == "*\n"


@pytest.mark.parametrize("language", ["cs", "gd"])
def test_game_apply_refuses_an_existing_game_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], language: str
) -> None:
    from joinery.cli import main

    project = _existing_game(tmp_path, language)
    before = _snapshot(project)
    argv = ["game", "apply", "--project", str(project), "--blueprint", "blank"]
    assert main(argv) == 1
    line = json.loads(capsys.readouterr().out.strip())
    assert line["step"] == "error" and line["state"] == "failed"
    message = line["message"]
    assert message.startswith("This folder already holds a game Joinery didn't make: ")
    assert "Joinery won't change an existing game this way." in message
    assert "project.godot already starts the scene res://scenes/main.tscn" in message
    if language == "cs":
        assert "project.godot is set up for C#" in message
        assert "C# code (TheirGame.csproj, scripts/Player.cs)" in message
    else:
        assert "scripts or scenes (scenes/main.tscn, scripts/player.gd)" in message
    # Not the addon, not a backup, not one byte.
    assert _snapshot(project) == before


def test_apply_and_plan_refuse_an_existing_game(tmp_path: Path) -> None:
    project = _existing_game(tmp_path, "gd")
    before = _snapshot(project)
    with pytest.raises(ValueError, match="won't change an existing game"):
        blueprints.apply(project, "blank")
    conflicts = blueprints.plan(project, "blank", []).conflicts
    assert any("existing game" in c for c in conflicts)
    assert _snapshot(project) == before


def test_a_csharp_file_alone_is_an_existing_game(tmp_path: Path) -> None:
    project = _wizard_folder(tmp_path)
    (project / "Game.sln").write_text("\n")
    refusal = blueprints.existing_game(project, "blank")
    assert refusal is not None and "C# code (Game.sln)" in refusal


def test_an_interrupted_apply_can_run_again(tmp_path: Path) -> None:
    """Files left by an earlier run of the same starting point are not someone's game."""
    project = _wizard_folder(tmp_path)
    blueprints.apply(project, "blank")
    # As if the run stopped before it recorded anything.
    (project / blueprints.MANIFEST).unlink()
    (project / "joinery.toml").unlink()
    assert blueprints.existing_game(project, "blank") is None
    assert blueprints.apply(project, "blank").blueprint == "blank"


def test_a_project_joinery_made_is_not_refused(tmp_path: Path) -> None:
    project = new(tmp_path, blueprint="blank")
    assert blueprints.existing_game(project, "blank") is None
    assert "controller" in blueprints.add_feature(project, "controller").features


def test_game_apply_saves_the_history_choice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery.cli import main
    from joinery.transactions import HistoryPolicy, load_policy

    project = new(tmp_path)
    argv = ["game", "apply", "--project", str(project), "--blueprint", "blank"]
    assert main([*argv, "--features=", "--history", "latest"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[-2] == {
        "step": "history",
        "state": "done",
        "message": "Keeping older versions: Only the newest.",
    }
    assert lines[-1]["step"] == "finished"
    assert load_policy(project) == HistoryPolicy(keep=1, max_mb=1024)
    # The rest of joinery.toml (the game the wizard chose) is still there.
    assert '[game]' in (project / "joinery.toml").read_text()


def test_game_apply_refuses_a_bad_history_choice_before_changing_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery.cli import main

    project = new(tmp_path)
    before = (project / "joinery.toml").read_text()
    argv = ["game", "apply", "--project", str(project), "--blueprint", "blank"]
    assert main([*argv, "--history", "keep-10", "--history-keep", "3"]) == 1
    line = json.loads(capsys.readouterr().out.strip())
    assert line["state"] == "failed" and "not both" in line["message"]
    assert (project / "joinery.toml").read_text() == before
