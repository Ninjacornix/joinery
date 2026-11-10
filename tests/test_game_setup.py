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
