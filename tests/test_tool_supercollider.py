"""SuperCollider fails fast offline, and live audition never writes into the project."""

from __future__ import annotations

from pathlib import Path

import pytest

from joinery.tools import get
from joinery.tools.supercollider import (
    NO_EXIT,
    PARSE_ERROR,
    RENDER_LIMIT,
    REPLAY_TIMEOUT,
    RUN_ERROR,
    TIMEOUT,
)
from joinery.workers import AUDITION_DIR, supercollider_worker

SC = get("supercollider")
PATCH = (
    '(\nScore([[0.0,[\\c_set,0,0]]]).recordNRT(outputFilePath:"audio/a.wav",'
    "duration:0.1,action:{0.exit;});\n)\n"
)


def test_patch_runs_compiled_inside_try_with_error_hook_and_time_limit() -> None:
    script = SC.script("audio/a.scd", "", PATCH, ["audio/a.wav"])
    # The native source is recreated first, exactly as given.
    assert 'File.use("audio/a.scd", "w"' in script
    # compile (not interpret) so a syntax error is seen, not skipped.
    assert ".compile;" in script and ".interpret" not in script
    assert f'{{ "{PARSE_ERROR}".postln; 1.exit }}' in script
    assert "{ patch.value }.try { |error| error.reportError;" in script
    # Errors raised later (in recordNRT's action) end sclang too.
    assert script.index("OnError.add(") < script.index("patch.value")
    assert RUN_ERROR in script
    assert f"SystemClock.sched({RENDER_LIMIT}," in script and TIMEOUT in script
    assert script.startswith("(\nvar patch;\n") and script.endswith(")\n")


def test_patch_text_is_escaped_as_sclang_strings() -> None:
    code = 'Synthèse "quoted" \\back\n0.exit;'
    script = SC.script("audio/a.scd", "", code, [])
    assert '"Synthèse \\"quoted\\" \\\\back\\n0.exit;"' in script


def test_patch_without_exit_is_refused_by_the_script_without_rendering() -> None:
    script = SC.script("audio/a.scd", "", "(\n1 + 2;\n)\n", [])
    assert NO_EXIT in script and "1.exit" in script
    # The operation log still carries the patch; it is just never run.
    assert '"(\\n1 + 2;\\n)\\n"' in script
    assert "compile" not in script and "patch.value" not in script


@pytest.mark.parametrize("code", ["0.exit", "action:{ 0.exit; }", "exit(0)"])
def test_any_exit_call_counts(code: str) -> None:
    assert NO_EXIT not in SC.script("audio/a.scd", "", code, [])


def test_replay_runs_under_a_hard_timeout_above_the_script_limit() -> None:
    argv = SC.replay_argv("recipes/a.scd")
    assert argv == ["timeout", str(REPLAY_TIMEOUT), "sclang", "recipes/a.scd"]
    assert REPLAY_TIMEOUT > RENDER_LIMIT


def test_live_recording_is_described_as_audition_only() -> None:
    notes = dict(SC.mcp_notes)
    assert "audition only" in notes["cc_recording"]
    assert "operate_document" in notes["cc_recording"]


def _collider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *folders: str
) -> list[str]:
    source = tmp_path / "source"
    (source / "dist").mkdir(parents=True)
    (source / "dist/index.js").write_text("")
    for folder in folders:
        (source / folder).mkdir(parents=True)
        (source / folder / "kick.aif").write_bytes(b"x")
    monkeypatch.setattr("joinery.workers.installation", lambda _tool: tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    return supercollider_worker(project).args


def test_live_recordings_go_to_a_discarded_folder_in_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _collider(tmp_path, monkeypatch)
    assert AUDITION_DIR.startswith("/tmp/")
    assert f"CC_RECORDINGS_PATH={AUDITION_DIR}" in args
    assert not any(a.startswith("CC_RECORDINGS_PATH=/work") for a in args)
    # Samples are still only the project's own audio.
    assert "CC_SAMPLES_PATH=/work/audio" in args


def test_bundled_samples_and_songs_are_hidden_from_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _collider(tmp_path, monkeypatch, "samples", "tapes", "user/samples")
    mounts = [args[i + 1] for i, a in enumerate(args) if a == "--mount"]
    assert mounts == [
        f"type=tmpfs,destination=/opt/collider/{folder},tmpfs-mode=0555"
        for folder in ("samples", "tapes", "user/samples")
    ]
    # The hiding mounts come after the source mount they cover.
    source = next(i for i, a in enumerate(args) if a.endswith(":/opt/collider:ro"))
    assert all(args.index("--mount") > source for _ in mounts)


def test_missing_folders_are_not_mounted_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Docker cannot create a mount point inside the read-only source mount.
    args = _collider(tmp_path, monkeypatch, "tapes")
    mounts = [args[i + 1] for i, a in enumerate(args) if a == "--mount"]
    assert mounts == ["type=tmpfs,destination=/opt/collider/tapes,tmpfs-mode=0555"]
