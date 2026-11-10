"""History usage and compaction. A fake runner stands in for the real tools."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from joinery.history import compact, main, plan, usage
from joinery.recipe import load_recipe, rebuild_all, record, store_preimage
from joinery.runner import Result
from joinery.transactions import FileTransaction, list_transactions

OLD = "0" * 32
CURRENT = "1" * 32


class ReplayRunner:
    """Replays a Krita-style recipe: reads its preimage, writes the export."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def available(self) -> bool:
        return True

    def run(self, argv: list[str], project: Path, timeout: int = 900) -> Result:
        self.calls.append(argv)
        script = (project / argv[-1]).read_text()
        base = re.search(r"recipes/inputs/[A-Za-z0-9._-]+", script)
        assert base, script
        source = project / base[0]
        if not source.is_file():
            return Result(False, f"missing {base[0]}", "", 0.1)
        output = re.search(r"EXPORT=(\S+)", script)
        assert output
        target = project / output[1]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes() + b"+edit")
        return Result(True, "built", "", 0.1)


def write_recipe(project: Path, folder: str, identifier: str, base: str) -> Path:
    script = project / "recipes" / f"{identifier}.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        f"document = app.openDocument(os.path.abspath({base!r}))\n"
        "EXPORT=art/thing.txt\n"
    )
    path = project / folder / f"{identifier}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'version = 1\ntool = "krita"\n'
        f'script = "recipes/{identifier}.py"\n'
        'outputs = ["art/thing.txt"]\n'
        f"sources = [{json.dumps(base)}]\n"
    )
    return path


def fixture(tmp_path: Path) -> Path:
    """Two edits of one document: the first recipe is history, the second current."""
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    inputs = project / "recipes/inputs"
    inputs.mkdir(parents=True)
    (inputs / ".gdignore").write_text("")
    (inputs / f"{OLD}.kra").write_bytes(b"first state" * 1000)
    (inputs / f"{CURRENT}.kra").write_bytes(b"second state")
    write_recipe(project, "recipes/history", OLD, f"recipes/inputs/{OLD}.kra")
    current = write_recipe(project, "recipes", CURRENT, f"recipes/inputs/{CURRENT}.kra")
    (project / "art").mkdir()
    (project / "art/thing.txt").write_bytes(b"second state+edit")
    record(current, project, ReplayRunner())
    return project


def accept_change(project: Path, name: str, text: str) -> FileTransaction:
    tx = FileTransaction(project)
    tx.begin("Edit " + name)
    (tx.stage / name).write_text(text)
    tx.review()
    tx.accept()
    return tx


def test_compact_keeps_rebuild_passing_and_drops_history(tmp_path: Path) -> None:
    project = fixture(tmp_path)
    assert all(result.ok for result in rebuild_all(project, ReplayRunner()))
    tx = accept_change(project, "notes.txt", "hello")
    before = usage(project)
    assert before["history_recipe_bytes"] > 0
    assert before["transactions"]["revertible"] == 1

    result = compact(project)

    assert result["problems"] == []
    assert result["freed_bytes"] > 0
    rebuilt = rebuild_all(project, ReplayRunner())
    assert [r.ok for r in rebuilt] == [True], [r.problems for r in rebuilt]
    assert not (project / "recipes/history").exists()
    assert not (project / f"recipes/{OLD}.py").exists()
    digest = hashlib.sha256(b"second state").hexdigest()
    names = sorted(p.name for p in (project / "recipes/inputs").iterdir())
    assert names == [".gdignore", f"{digest}.kra"]
    recipe = load_recipe(project / f"recipes/{CURRENT}.toml")
    assert recipe.sources == (f"recipes/inputs/{digest}.kra",)
    assert digest in (project / recipe.script).read_text()
    # The journal stays; the change can no longer be reverted.
    loaded = FileTransaction.load(project, tx.id)
    assert loaded.state == "accepted" and not loaded.revertible
    with pytest.raises(ValueError, match="can no longer be reverted"):
        loaded.revert()
    after = usage(project)
    assert after["total_bytes"] == result["total_bytes"]
    assert after["reclaimable_bytes"] == 0
    assert compact(project)["freed_bytes"] == 0, "Compacting twice changes nothing"


def test_compact_stores_identical_preimages_once(tmp_path: Path) -> None:
    project = fixture(tmp_path)
    twin = "2" * 32
    (project / f"recipes/inputs/{twin}.kra").write_bytes(b"second state")
    write_recipe(project, "recipes", twin, f"recipes/inputs/{twin}.kra")
    work = plan(project)
    assert len(work.renames) == 2 and len(set(work.renames.values())) == 1
    assert usage(project)["reclaimable_bytes"] > 0
    compact(project)
    files = [p for p in (project / "recipes/inputs").iterdir() if p.suffix == ".kra"]
    assert len(files) == 1
    for name in (CURRENT, twin):
        assert load_recipe(project / f"recipes/{name}.toml").sources == (
            f"recipes/inputs/{files[0].name}",
        )


def test_compact_waits_for_a_pending_review(tmp_path: Path) -> None:
    project = fixture(tmp_path)
    tx = FileTransaction(project)
    tx.begin("Pending")
    (tx.stage / "notes.txt").write_text("waiting")
    tx.review()
    with pytest.raises(ValueError, match="waiting for review"):
        compact(project)
    assert (project / "recipes/history").is_dir()
    assert tx.stage.is_dir()


def test_compact_removes_nothing_when_a_recipe_is_unreadable(tmp_path: Path) -> None:
    project = fixture(tmp_path)
    (project / "recipes/broken.toml").write_text("not = [valid")
    assert usage(project)["problems"]
    with pytest.raises(ValueError, match="can't be read"):
        compact(project)
    assert (project / f"recipes/inputs/{OLD}.kra").is_file()


def test_usage_counts_restore_data_and_preimages(tmp_path: Path) -> None:
    project = fixture(tmp_path)
    empty = usage(project)
    assert empty["restore_bytes"] == 0
    (project / "notes.txt").write_text("x" * 5000)
    accept_change(project, "notes.txt", "y")
    data = usage(project)
    assert data["restore_bytes"] == 5000
    assert data["preimage_bytes"] == len(b"first state" * 1000) + len(b"second state")
    assert data["total_bytes"] >= data["restore_bytes"] + data["preimage_bytes"]
    assert data["transactions"] == {"total": 1, "revertible": 1, "pending": 0}
    assert data["policy"] == {"keep": 50, "max_mb": 2048}
    assert len(list_transactions(project)) == 1


def test_usage_refuses_a_folder_that_is_not_a_project(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Godot project"):
        usage(tmp_path)


def test_cli_prints_one_json_line_with_total_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = fixture(tmp_path)
    assert main(["history", "usage", "--json", "--project", str(project)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["total_bytes"] > 0
    assert main(["history", "compact", "--project", str(project)]) == 0
    assert capsys.readouterr().out.startswith("Freed ")
    assert main(["history", "usage", "--project", str(project)]) == 0
    assert capsys.readouterr().out.startswith("History uses ")


def test_cli_reports_errors_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["history", "usage", "--json", "--project", str(tmp_path)]) == 1
    out = capsys.readouterr()
    assert "error" in json.loads(out.out.splitlines()[0])
    assert "Godot project" in out.err


def test_store_preimage_is_content_addressed(tmp_path: Path) -> None:
    (tmp_path / "art").mkdir()
    document = tmp_path / "art/scene.blend"
    document.write_bytes(b"BLENDER state")
    first = store_preimage(tmp_path, document)
    second = store_preimage(tmp_path, document)
    digest = hashlib.sha256(b"BLENDER state").hexdigest()
    assert first == second == f"recipes/inputs/{digest}.blend"
    assert (tmp_path / first).read_bytes() == b"BLENDER state"
    assert (tmp_path / "recipes/inputs/.gdignore").is_file()
    document.write_bytes(b"BLENDER later")
    assert store_preimage(tmp_path, document) != first


def test_joinery_cli_dispatches_history(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The settings Storage row runs these through the main `joinery` command.
    from joinery.cli import main as joinery

    project = fixture(tmp_path)
    assert joinery(["history", "usage", "--json", "--project", str(project)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["total_bytes"] > 0
    assert joinery(["history", "compact", "--project", str(project)]) == 0
    assert capsys.readouterr().out.startswith("Freed ")
