import json
from pathlib import Path

from joinery.catalog import ROOT
from joinery.deps import check_deps
from joinery.policy import load_denylist


def test_repo_is_clean() -> None:
    assert check_deps(ROOT, load_denylist()) == []


def test_finds_denied_in_each_manifest(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["Diffusers>=0.3"]\n'
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "audiocraft"\nversion = "1"\n'
    )
    (tmp_path / "requirements-dev.txt").write_text("# note\nstability-sdk==1\n")
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"replicate": "1"}})
    )
    lock = {"packages": {"node_modules/a/node_modules/@fal-ai/client": {}}}
    (tmp_path / "package-lock.json").write_text(json.dumps(lock))
    errors = check_deps(tmp_path, load_denylist())
    assert len(errors) == 5


def test_skips_vendored_godot(tmp_path: Path) -> None:
    vendored = tmp_path / "godot_patches" / "godot-src"
    vendored.mkdir(parents=True)
    (vendored / "requirements.txt").write_text("diffusers\n")
    assert check_deps(tmp_path, load_denylist()) == []
