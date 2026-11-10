"""History usage and compaction. A fake runner stands in for the real tools."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from joinery.history import compact, main, make_policy, plan, usage, write_policy
from joinery.recipe import load_recipe, rebuild_all, record, store_preimage
from joinery.runner import Result
from joinery.transactions import (
    FileTransaction,
    HistoryPolicy,
    list_transactions,
    load_policy,
)

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
    assert data["policy"] == {
        "keep": 50,
        "max_mb": 2048,
        "preset": "keep-50",
        "label": "Last 50 changes, up to 2 GB",
    }
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


# --- Policy -------------------------------------------------------------------------


def test_policy_reads_the_default_and_names_its_preset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = fixture(tmp_path)
    assert main(["history", "policy", "--project", str(project), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["policy"]["preset"] == "keep-50"
    assert data["changed"] is False and data["pruned"] == 0
    assert not (project / "joinery.toml").exists(), "reading never writes"
    assert main(["history", "policy", "--project", str(project)]) == 0
    assert capsys.readouterr().out.strip() == (
        "Keeping older versions: Last 50 changes, up to 2 GB."
    )


def test_policy_saves_history_and_keeps_the_rest_of_joinery_toml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = fixture(tmp_path)
    config = project / "joinery.toml"
    config.write_text(
        '# my notes\n[tools]\nenabled = ["godot"]\n\n'
        "[history]\nkeep = 5\n\n"
        '[game]\nblueprint = "blank"\n'
    )
    argv = ["history", "policy", "--project", str(project), "--max-mb", "512"]
    assert main(argv) == 0
    # Only the size changed; keep came from the saved policy.
    assert load_policy(project) == HistoryPolicy(keep=5, max_mb=512)
    text = config.read_text()
    assert text.startswith('# my notes\n[tools]\nenabled = ["godot"]\n\n[history]')
    assert text.endswith('\n\n[game]\nblueprint = "blank"\n')
    assert text.count("[history]") == 1
    assert "Last 5 changes, up to 512 MB" in capsys.readouterr().out
    # A project without [history] gets it appended.
    config.write_text('[tools]\nenabled = ["godot"]\n')
    write_policy(project, HistoryPolicy(keep=10, max_mb=1024))
    assert config.read_text().startswith('[tools]\nenabled = ["godot"]\n\n[history]\n')
    assert load_policy(project).preset == "keep-10"


def test_a_tighter_policy_prunes_and_says_what_it_freed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = fixture(tmp_path)
    for index in range(3):
        (project / f"model{index}.blend").write_bytes(b"v1" * 1000)
        accept_change(project, f"model{index}.blend", "v2")
    assert usage(project)["transactions"]["revertible"] == 3
    argv = ["history", "policy", "--project", str(project), "--keep", "1", "--json"]
    assert main([*argv, "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert (preview["pruned"], preview["freed_bytes"]) == (2, 4000)
    assert usage(project)["transactions"]["revertible"] == 3, (
        "a dry run changes nothing"
    )
    assert not (project / "joinery.toml").exists()
    assert main(argv) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["policy"]["preset"] == "latest"
    assert (data["pruned"], data["freed_bytes"]) == (2, 4000)
    assert usage(project)["transactions"]["revertible"] == 1
    # The newest change still reverts; the older two keep their journals only.
    newest = list_transactions(project)[-1]
    assert newest.revertible and newest.summary == "Edit model2.blend"
    assert main(["history", "policy", "--project", str(project), "--keep", "50"]) == 0
    assert capsys.readouterr().out.strip() == (
        "Keeping older versions: Last 50 changes, up to 2 GB."
    )


def test_policy_refuses_nonsense_and_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = fixture(tmp_path)
    for flag, value in (("--keep", "0"), ("--max-mb", "0")):
        argv = ["history", "policy", "--project", str(project), flag, value]
        assert main(argv) == 1
        assert "error" in json.loads(capsys.readouterr().out.splitlines()[0])
    assert main(["history", "usage", "--project", str(project), "--keep", "3"]) == 1
    assert not (project / "joinery.toml").exists()
    with pytest.raises(ValueError, match="not both"):
        make_policy("keep-10", keep=3)
    with pytest.raises(ValueError, match="custom history needs"):
        make_policy("custom")
    assert make_policy() is None
    assert make_policy("custom", keep=20, max_mb=4096) == HistoryPolicy(
        keep=20, max_mb=4096
    )
    assert HistoryPolicy(keep=20, max_mb=4096).label == "Last 20 changes, up to 4 GB"


def test_the_setup_screens_offer_the_cli_presets() -> None:
    """The settings page, Project apps and the wizard name the CLI's presets exactly."""
    from joinery.transactions import POLICY_LABELS, POLICY_PRESETS

    root = Path(__file__).resolve().parents[1]
    addon = (root / "godot_addon/joinery/history_policy.gd").read_text()
    for name, policy in POLICY_PRESETS.items():
        entry = re.search(r'\{\s*"id": "' + name + r'"[^}]*\}', addon)
        assert entry, f"history_policy.gd offers {name}"
        assert f'"keep": {policy.keep},' in entry[0]
        assert f'"max_mb": {policy.max_mb}' in entry[0]
        assert POLICY_LABELS[name].split(",")[0] in entry[0]
