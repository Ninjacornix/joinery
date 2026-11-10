"""Every `joinery` subcommand the wizard, the sidebar and the docs rely on."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from joinery import blueprints, cli, connection
from joinery.catalog import load_catalog
from joinery.cli import main
from joinery.installer import Selection
from joinery.project import create_project
from joinery.transactions import list_transactions
from tests.blueprints.conftest import build_catalogue


@pytest.fixture
def catalogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    built = build_catalogue(tmp_path / "catalogue")
    monkeypatch.setattr(blueprints, "BLUEPRINTS_DIR", built.blueprints)
    monkeypatch.setattr(blueprints, "FEATURES_DIR", built.features)
    return built.blueprints


@pytest.fixture
def game(tmp_path: Path, catalogue: Path) -> Path:
    project = tmp_path / "game"
    create_project(
        project,
        "G",
        Selection(["godot"], []),
        load_catalog(),
        blueprint="alpha",
        features=["controller"],
    )
    return project


def out_json(capsys: pytest.CaptureFixture[str]) -> Any:
    return json.loads(capsys.readouterr().out)


# --- Blueprints and features ---------------------------------------------------


def test_blueprints_json_carries_capture_and_features(
    catalogue: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (catalogue / "alpha" / "capture.png").write_bytes(b"\x89PNG\r\n")
    assert main(["blueprints", "--json"]) == 0
    data = out_json(capsys)
    assert {"blueprints", "features", "presets"} <= set(data)
    alpha, beta = data["blueprints"]
    assert (alpha["id"], beta["id"]) == ("alpha", "beta")
    assert alpha["capture"] == str(catalogue / "alpha" / "capture.png")
    assert beta["capture"] is None
    keys = {
        "id",
        "name",
        "summary",
        "default",
        "available",
        "reason",
        "requires",
        "conflicts",
        "external",
        "adds",
    }
    for blueprint in (alpha, beta):
        assert [f["id"] for f in blueprint["features"]] == [
            "controller",
            "lobby",
            "net",
            "solo",
            "words",
        ]
        assert all(set(f) == keys for f in blueprint["features"])
    alpha_by = {f["id"]: f for f in alpha["features"]}
    beta_by = {f["id"]: f for f in beta["features"]}
    assert alpha_by["controller"]["default"] is True
    assert alpha_by["solo"]["default"] is False
    # Lobby only works with alpha, and pulls in online play.
    assert alpha_by["lobby"]["available"] is True
    assert alpha_by["lobby"]["adds"]["features"] == ["net"]
    assert "features/lobby/lobby.gd" in alpha_by["lobby"]["adds"]["files"]
    assert beta_by["lobby"]["available"] is False
    assert beta_by["lobby"]["reason"] == "Lobby does not work with the Beta blueprint."
    assert beta_by["lobby"]["adds"] == {"files": [], "settings": [], "features": []}
    assert "online/default_port" in alpha_by["net"]["adds"]["settings"]
    assert alpha_by["net"]["external"][0]["name"] == "Nothing"


def test_blueprints_text_and_describe(
    catalogue: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["blueprints"]) == 0
    assert "alpha" in capsys.readouterr().out
    assert main(["blueprints", "describe", "beta"]) == 0
    described = out_json(capsys)
    assert described["id"] == "beta" and "features" in described
    assert main(["blueprints", "describe", "nope"]) == 1
    assert out_json(capsys) == {"error": "Unknown blueprint: nope"}


def test_features_list(catalogue: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["features", "--json"]) == 0
    ids = [f["id"] for f in out_json(capsys)["features"]]
    assert ids == ["controller", "lobby", "net", "solo", "words"]
    assert main(["features"]) == 0
    assert "Solo mode: Offline only." in capsys.readouterr().out


# --- Feature add and remove ----------------------------------------------------


def test_feature_add_plan_writes_nothing(
    game: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (game / "project.godot").read_text()
    assert main(["feature", "add", "lobby", "--project", str(game), "--plan"]) == 0
    plan = out_json(capsys)
    assert plan["action"] == "add" and plan["feature"] == "lobby"
    assert "features/lobby/lobby.gd" in plan["changes"]
    assert "project.godot" in plan["changes"]
    assert plan["adds"] == ["net"]
    assert plan["conflicts"] == []
    assert {
        "file": "project.godot",
        "section": "autoload",
        "key": "Lobby",
        "before": None,
        "after": '"*res://features/lobby/lobby.gd"',
    } in plan["config_edits"]
    assert (game / "project.godot").read_text() == before
    assert not (game / "features/lobby").exists()
    assert list_transactions(game) == []


def test_feature_remove_plan_writes_nothing(
    game: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = (game / blueprints.MANIFEST).read_text()
    assert (
        main(["feature", "remove", "controller", "--project", str(game), "--plan"]) == 0
    )
    plan = out_json(capsys)
    assert "features/controller/pad.gd" in plan["changes"]
    assert any(e["section"] == "input" for e in plan["config_edits"])
    assert (game / "features/controller/pad.gd").is_file()
    assert (game / blueprints.MANIFEST).read_text() == manifest


def test_feature_add_waits_for_review_then_accepts(
    game: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["feature", "add", "net", "--project", str(game), "--json"]) == 0
    pending = out_json(capsys)
    assert pending["state"] == "review"
    assert any(c["path"] == "features/net/net.gd" for c in pending["changes"])
    assert not (game / "features/net").exists()
    accept = ["transactions", "accept", "--project", str(game), "--id"]
    assert main([*accept, pending["transaction_id"]]) == 0
    decided = out_json(capsys)
    assert decided["state"] == "accepted"
    assert (game / "features/net/net.gd").is_file()
    record = blueprints.read_record(game)
    assert record is not None and record.features == ["controller", "net"]
    assert main(["feature", "list", "--project", str(game), "--json"]) == 0
    assert out_json(capsys)["game"]["features"] == ["controller", "net"]


def test_feature_reject_changes_nothing(
    game: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (game / "joinery.toml").read_text()
    assert main(["feature", "add", "words", "--project", str(game), "--json"]) == 0
    identifier = out_json(capsys)["transaction_id"]
    assert (
        main(["transactions", "reject", "--project", str(game), "--id", identifier])
        == 0
    )
    assert out_json(capsys)["state"] == "rejected"
    assert (game / "joinery.toml").read_text() == before
    assert not (game / "locale").exists()


def test_feature_add_yes_runs_follow_up(
    game: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["feature", "add", "net", "--project", str(game), "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Add Online play" in out and "Done." in out
    assert (game / "features/net/net.gd").is_file()
    assert main(["feature", "remove", "net", "--project", str(game), "--yes"]) == 0
    capsys.readouterr()
    assert not (game / "features/net").exists()


def test_feature_add_steam_again_only_downloads(
    game: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The retry the failed-download message offers: no review, just the fetch."""
    toml = game / "joinery.toml"
    toml.write_text(
        toml.read_text().replace(
            'features = ["controller"]', 'features = ["controller", "steam"]'
        )
    )
    calls: list[Path] = []

    def install(project: Path, report: object = None) -> dict[str, Any]:
        calls.append(project)
        return {"step": "steam-download", "state": "done", "message": "Downloaded."}

    monkeypatch.setattr(cli.game_setup, "install_steam", install)
    assert main(["feature", "add", "steam", "--project", str(game)]) == 0
    assert capsys.readouterr().out == "Downloaded.\n"
    assert calls == [game.resolve()]
    assert list_transactions(game) == []


def test_feature_errors_are_readable(
    game: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["feature", "add", "solo", "--project", str(game), "--json"]) == 0
    capsys.readouterr()  # Solo alone is fine; with online play it is not.
    assert main(["feature", "add", "net", "--project", str(game), "--plan"]) == 0
    assert main(["feature", "remove", "lobby", "--project", str(game), "--json"]) == 1
    assert "not part of this project" in capsys.readouterr().out
    plain = tmp_path / "plain"
    create_project(plain, "P", Selection(["godot"], []), load_catalog())
    assert main(["feature", "add", "net", "--project", str(plain)]) == 1
    assert "not started from a blueprint" in capsys.readouterr().err


# --- Checks --------------------------------------------------------------------


def test_check_catalogue_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(blueprints, "validate_catalogue", list)
    assert main(["check", "catalogue"]) == 0
    assert capsys.readouterr().out == "check catalogue: ok\n"
    problems = ["blueprints/x: missing blueprint.toml", "steam: bad uid"]
    monkeypatch.setattr(blueprints, "validate_catalogue", lambda: problems)
    assert main(["check", "catalogue"]) == 1
    assert capsys.readouterr().out.splitlines() == [
        *problems,
        "check catalogue: FAILED",
    ]


def test_check_bible_prints_the_lint_summary(
    game: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check", "bible", "--project", str(game)]) == 0
    summary = out_json(capsys)
    assert {"version", "problems", "gaps", "references", "suggestions"} <= set(summary)
    assert summary["problems"] == []


# --- Passthroughs --------------------------------------------------------------


def test_status_passes_its_flags_to_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from joinery import readiness

    seen: list[list[str]] = []

    def fake(argv: list[str]) -> int:
        seen.append(list(argv))
        return 0

    monkeypatch.setattr(readiness, "main", fake)
    assert main(["status", "--project", str(tmp_path), "--catalog", "--json"]) == 0
    assert main(["status", "--tool", "blender", "--refresh", "--deep", "--text"]) == 0
    assert main(["status", "--background"]) == 0
    assert seen == [
        ["--project", str(tmp_path), "--catalog"],
        ["--tool", "blender", "--refresh", "--deep", "--text"],
        ["--background"],
    ]


def test_status_returns_the_readiness_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from joinery import readiness

    monkeypatch.setattr(readiness, "main", lambda argv: 2)
    assert main(["status"]) == 2


def test_doctor_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[Path, bool, bool]] = []

    def report(
        config: Path, *, workers: bool = True, refresh: bool = False
    ) -> tuple[str, int]:
        calls.append((config, workers, refresh))
        return "report", 1 if refresh else 0

    monkeypatch.setattr(cli, "doctor_report", report)
    base = ["doctor", "--project", str(tmp_path)]
    assert main(base) == 0
    assert main([*base, "--workers"]) == 0
    assert main([*base, "--cached"]) == 0
    assert main([*base, "--refresh"]) == 1
    config = tmp_path / "joinery.toml"
    assert calls == [
        (config, True, False),
        (config, True, False),
        (config, False, False),
        (config, True, True),
    ]
    with pytest.raises(SystemExit):
        main([*base, "--workers", "--cached"])


@pytest.mark.parametrize(("state", "code"), [("ok", 0), ("quota", 1), ("auth", 1)])
def test_connect_verify_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state: str,
    code: int,
) -> None:
    seen: list[tuple[str, Path]] = []

    def verify(agent: str, project: Path) -> dict[str, Any]:
        seen.append((agent, project))
        return {"id": agent, "state": state, "verified": state == "ok"}

    monkeypatch.setattr(connection, "verify", verify)
    argv = ["connect", "verify", "--agent", "codex", "--project", str(tmp_path)]
    assert main(argv) == code
    assert out_json(capsys)["state"] == state
    assert seen == [("codex", tmp_path)]


def test_connect_verify_defaults_to_the_current_folder_and_needs_an_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[Path] = []

    def verify(agent: str, project: Path) -> dict[str, str]:
        seen.append(project)
        return {"state": "ok"}

    monkeypatch.setattr(connection, "verify", verify)
    monkeypatch.chdir(tmp_path)
    assert main(["connect", "verify", "--agent", "claude"]) == 0
    assert seen == [tmp_path]
    capsys.readouterr()
    assert main(["connect", "verify"]) == 1
    assert out_json(capsys) == {"error": "Choose an agent first."}


@pytest.mark.parametrize(
    ("state", "code"),
    [("ready", 0), ("runtime-missing", 1), ("not-installed", 1), ("error", 1)],
)
def test_mcp_status_counts_runtime_missing_as_a_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state: str,
    code: int,
) -> None:
    from joinery import server_install

    async def inspect(project: Path, probe: bool) -> list[dict[str, str]]:
        return [{"name": "blender", "state": state}]

    monkeypatch.setattr(server_install, "inspect_servers", inspect)
    assert main(["mcp", "status", "--project", str(tmp_path)]) == code
    assert out_json(capsys)["servers"][0]["state"] == state


def test_history_usage_and_compact_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery import history

    calls: list[str] = []

    def usage(project: Path) -> dict[str, Any]:
        calls.append(f"usage {project}")
        return {"total_bytes": 7, "problems": []}

    def compact(project: Path) -> dict[str, Any]:
        calls.append(f"compact {project}")
        return {"freed_bytes": 0, "total_bytes": 7, "problems": []}

    monkeypatch.setattr(history, "usage", usage)
    monkeypatch.setattr(history, "compact", compact)
    assert main(["history", "usage", "--json", "--project", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["total_bytes"] == 7
    assert main(["history", "compact", "--project", str(tmp_path)]) == 0
    assert capsys.readouterr().out.startswith("Freed ")
    assert calls == [f"usage {tmp_path}", f"compact {tmp_path}"]


def test_parts_is_wired_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class Parts:
        @staticmethod
        def register(
            commands: argparse._SubParsersAction[argparse.ArgumentParser],
        ) -> None:
            parser = commands.add_parser("parts")
            parser.add_argument("asset")

        @staticmethod
        def run(args: argparse.Namespace) -> int:
            seen.append(args.asset)
            return 3

    monkeypatch.setattr(cli, "_parts", lambda: Parts)
    assert main(["parts", "model.glb"]) == 3
    assert seen == ["model.glb"]
    monkeypatch.setattr(cli, "_parts", lambda: None)
    with pytest.raises(SystemExit):
        main(["parts", "model.glb"])


# --- Brief and bible -------------------------------------------------------------


def test_brief_questions_and_answers(
    game: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["brief", "--project", str(game), "--questions"]) == 0
    pages = out_json(capsys)["pages"]
    assert pages[0]["questions"][0]["key"] == "pitch"
    (game / "design/brief.md").unlink()
    (game / ".joinery-brief-answers.json").write_text(
        json.dumps({"pitch": "Lean and run"})
    )
    assert main(["brief", "--project", str(game)]) == 0
    text = (game / "design/brief.md").read_text()
    # The project's [game] record supplies the starting point and the controls.
    assert "Lean and run" in text
    assert "from the Alpha starting point" in text
    assert "| Jump (`jump`) | Space / Left click | A |" in text
    assert main(["brief", "--project", str(game)]) == 0
    assert "nothing written" in capsys.readouterr().out
    # Without setup answers, the brief is drafted from the starting point.
    (game / "design/brief.md").unlink()
    assert main(["brief", "--project", str(game)]) == 0
    assert "from the Alpha starting point" in (game / "design/brief.md").read_text()
    capsys.readouterr()
    assert main(["brief", "--project", str(game), "--json"]) == 0
    assert out_json(capsys)["exists"] is True


def test_bible_questions_ask_for_references(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert main(["bible", "--project", str(tmp_path), "--questions"]) == 0
    keys = [q["key"] for q in out_json(capsys)["pages"][0]["questions"]]
    assert keys[:2] == ["vision", "references"]
