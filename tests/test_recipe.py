"""Recipes replay and verify. A fake runner stands in for the real tools."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from joinery.recipe import (
    Recipe,
    load_recipe,
    rebuild_one,
    recipe_paths,
    record,
)
from joinery.runner import Result


def put(project: Path, rel: str, data: bytes) -> None:
    """Create an output as if the tool had just made it."""
    target = project / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


@dataclass
class FakeRunner:
    """Writes a scripted payload instead of running a tool."""

    name: str = "fake"
    #: What each run writes: output path -> bytes.
    writes: dict[str, bytes] = field(default_factory=dict)
    ok: bool = True
    #: Signature handed back when asked for a .blend signature.
    signature: str = "deadbeef"
    calls: list[list[str]] = field(default_factory=list)

    def available(self) -> bool:
        return True

    def run(self, argv: list[str], project: Path, timeout: int = 900) -> Result:
        self.calls.append(argv)
        if "--python-expr" in argv:  # a signature query, not a build
            return Result(True, f"JOINERY_SIG:{self.signature}", "", 0.1)
        for rel, data in self.writes.items():
            target = project / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return Result(self.ok, "built", "" if self.ok else "boom", 0.2)


def write_recipe(project: Path, name: str, body: str) -> Path:
    (project / "recipes").mkdir(parents=True, exist_ok=True)
    path = project / "recipes" / f"{name}.toml"
    path.write_text(body)
    return path


def wav_recipe(project: Path) -> Path:
    (project / "recipes" / "tone.scd").parent.mkdir(parents=True, exist_ok=True)
    (project / "recipes" / "tone.scd").write_text("// patch")
    return write_recipe(
        project,
        "tone",
        'version = 1\ntool = "supercollider"\nscript = "recipes/tone.scd"\n'
        'outputs = ["audio/sfx/tone.wav"]\n',
    )


def test_exact_output_passes_when_reproduced(tmp_path: Path) -> None:
    path = wav_recipe(tmp_path)
    runner = FakeRunner(writes={"audio/sfx/tone.wav": b"RIFFsame"})
    put(tmp_path, "audio/sfx/tone.wav", b"RIFFsame")
    record(path, tmp_path, runner)  # measures the real file
    result = rebuild_one(path, tmp_path, runner)
    assert result.ok, result.problems
    digest = hashlib.sha256(b"RIFFsame").hexdigest()
    assert load_recipe(path).expect["audio/sfx/tone.wav"] == f"sha256:{digest}"


def test_changed_output_fails(tmp_path: Path) -> None:
    path = wav_recipe(tmp_path)
    runner = FakeRunner(writes={"audio/sfx/tone.wav": b"RIFFone"})
    put(tmp_path, "audio/sfx/tone.wav", b"RIFFone")
    record(path, tmp_path, runner)
    runner.writes["audio/sfx/tone.wav"] = b"RIFFtwo"
    result = rebuild_one(path, tmp_path, runner)
    assert not result.ok
    assert any("expected" in p for p in result.problems)


def test_blend_uses_a_semantic_signature(tmp_path: Path) -> None:
    """A .blend is not byte-reproducible, so it is compared by content."""
    (tmp_path / "recipes").mkdir(parents=True)
    (tmp_path / "recipes" / "m.py").write_text("# bpy ops")
    path = write_recipe(
        tmp_path,
        "m",
        'version = 1\ntool = "blender"\nscript = "recipes/m.py"\n'
        'outputs = ["art/m.blend"]\n',
    )
    runner = FakeRunner(writes={"art/m.blend": b"BLENDER-v500"}, signature="abc123")
    put(tmp_path, "art/m.blend", b"BLENDER-v500")
    record(path, tmp_path, runner)
    assert load_recipe(path).expect["art/m.blend"] == "blend:abc123"

    # Different bytes, same model: still passes.
    runner.writes["art/m.blend"] = b"BLENDER-v500-different-bytes"
    assert rebuild_one(path, tmp_path, runner).ok

    # Different model: fails.
    runner.signature = "zzz999"
    assert not rebuild_one(path, tmp_path, runner).ok


def test_missing_expectation_is_a_failure(tmp_path: Path) -> None:
    # A recipe with no recorded fingerprint verifies nothing, so it cannot pass.
    path = wav_recipe(tmp_path)
    runner = FakeRunner(writes={"audio/sfx/tone.wav": b"x"})
    result = rebuild_one(path, tmp_path, runner)
    assert not result.ok
    assert any("records no expected fingerprint" in p for p in result.problems)


def test_tool_failure_is_reported(tmp_path: Path) -> None:
    path = wav_recipe(tmp_path)
    runner = FakeRunner(writes={}, ok=False)
    result = rebuild_one(path, tmp_path, runner)
    assert not result.ok
    assert any("supercollider failed" in p for p in result.problems)


def test_output_not_produced_is_reported(tmp_path: Path) -> None:
    path = wav_recipe(tmp_path)
    runner = FakeRunner(writes={})
    result = rebuild_one(path, tmp_path, runner)
    assert not result.ok
    assert any("not produced" in p for p in result.problems)


def test_noop_recipe_cannot_pass_using_existing_outputs(tmp_path: Path) -> None:
    path = wav_recipe(tmp_path)
    put(tmp_path, "audio/sfx/tone.wav", b"existing accepted audio")
    runner = FakeRunner()
    record(path, tmp_path, runner)
    result = rebuild_one(path, tmp_path, runner)
    assert not result.ok
    assert "audio/sfx/tone.wav: not produced" in result.problems
    assert (tmp_path / "audio/sfx/tone.wav").read_bytes() == b"existing accepted audio"


def test_clean_replay_retains_preimage_and_never_changes_working_outputs(
    tmp_path: Path,
) -> None:
    path = wav_recipe(tmp_path)
    path.write_text(path.read_text() + 'sources=["recipes/inputs/source.bin"]\n')
    put(tmp_path, "recipes/inputs/source.bin", b"immutable original")
    put(tmp_path, "audio/sfx/tone.wav", b"accepted output")

    class SourceRunner(FakeRunner):
        def run(self, argv: list[str], project: Path, timeout: int = 900) -> Result:
            assert project != tmp_path
            assert (
                project / "recipes/inputs/source.bin"
            ).read_bytes() == b"immutable original"
            assert not (project / "audio/sfx/tone.wav").exists()
            return super().run(argv, project, timeout)

    runner = SourceRunner(writes={"audio/sfx/tone.wav": b"accepted output"})
    record(path, tmp_path, runner)
    assert rebuild_one(path, tmp_path, runner).ok
    assert (tmp_path / "audio/sfx/tone.wav").read_bytes() == b"accepted output"
    assert (
        tmp_path / "recipes/inputs/source.bin"
    ).read_bytes() == b"immutable original"


def test_replay_refuses_symlink_escape_before_running_or_removing(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = wav_recipe(project)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"manual external work")
    (project / "audio/sfx").mkdir(parents=True)
    (project / "audio/sfx/tone.wav").symlink_to(outside)
    runner = FakeRunner()
    result = rebuild_one(path, project, runner)
    assert not result.ok
    assert any("cannot link outside" in problem for problem in result.problems)
    assert not runner.calls
    assert outside.read_bytes() == b"manual external work"


def test_replay_does_not_follow_absolute_internal_parent_link_back_to_original(
    tmp_path: Path,
) -> None:
    path = wav_recipe(tmp_path)
    put(tmp_path, "stored/tone.wav", b"accepted original")
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio/sfx").symlink_to(tmp_path / "stored", target_is_directory=True)
    runner = FakeRunner()
    result = rebuild_one(path, tmp_path, runner)
    assert not result.ok
    assert any("outside the scratch copy" in problem for problem in result.problems)
    assert not runner.calls
    assert (tmp_path / "stored/tone.wav").read_bytes() == b"accepted original"


def test_missing_script_is_reported(tmp_path: Path) -> None:
    path = write_recipe(
        tmp_path,
        "gone",
        'version = 1\ntool = "blender"\nscript = "recipes/gone.py"\n'
        'outputs = ["art/x.blend"]\n',
    )
    result = rebuild_one(path, tmp_path, FakeRunner())
    assert not result.ok
    assert any("not found" in p for p in result.problems)


def test_unknown_tool_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "recipes").mkdir(parents=True)
    (tmp_path / "recipes" / "x.txt").write_text("")
    path = write_recipe(
        tmp_path,
        "x",
        'version = 1\ntool = "photoshop"\nscript = "recipes/x.txt"\n'
        'outputs = ["art/x.png"]\n',
    )
    result = rebuild_one(path, tmp_path, FakeRunner())
    assert not result.ok
    assert any("photoshop" in p for p in result.problems)


def test_render_must_be_an_image(tmp_path: Path) -> None:
    # The loose check exists for camera renders only; it cannot be applied to a
    # .wav to dodge an exact comparison.
    path = write_recipe(
        tmp_path,
        "bad",
        'version = 1\ntool = "supercollider"\nscript = "recipes/tone.scd"\n'
        'outputs = ["audio/sfx/tone.wav"]\nrenders = ["audio/sfx/tone.wav"]\n',
    )
    (tmp_path / "recipes" / "tone.scd").write_text("// patch")
    runner = FakeRunner(writes={"audio/sfx/tone.wav": b"x"})
    put(tmp_path, "audio/sfx/tone.wav", b"x")
    with pytest.raises(ValueError, match="only an image"):
        record(path, tmp_path, runner)


def test_recipe_model_requires_outputs() -> None:
    with pytest.raises(ValueError):
        Recipe(tool="blender", script="s.py", outputs=())


def test_recipe_paths_are_sorted(tmp_path: Path) -> None:
    for name in ("b", "a", "c"):
        write_recipe(
            tmp_path,
            name,
            f'version = 1\ntool = "blender"\nscript = "recipes/{name}.py"\n'
            f'outputs = ["art/{name}.blend"]\n',
        )
    assert [p.stem for p in recipe_paths(tmp_path)] == ["a", "b", "c"]
