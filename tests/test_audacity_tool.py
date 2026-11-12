"""Audacity's tool module: recipes from typed steps, replayed in its own worker."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from joinery import authoring, recipe
from joinery.recipe import Recipe
from joinery.runner import Result, SandboxRunner
from joinery.tools import get
from joinery.tools.base import AUDACITY_WORKER

AUDACITY = get("audacity")
STEPS = [
    {"op": "import", "path": "audio/takes/door.wav", "name": "Door"},
    {"op": "noise_reduction", "track": 0, "profile": [0, 0.4], "reduction_db": 12},
    {
        "op": "spectral_edit",
        "track": 0,
        "time": [1, 1.5],
        "low_hz": 3000,
        "high_hz": 6000,
    },
    {"op": "label", "at": 0.5, "text": "slam"},
    {"op": "import", "path": "audio/takes/door.wav", "name": "Door again"},
]


@pytest.fixture
def stage(tmp_path: Path) -> Path:
    project = tmp_path / "transaction" / "stage"
    (project / "audio/takes").mkdir(parents=True)
    (project / "audio/takes/door.wav").write_bytes(b"RIFF user's own recording")
    (project.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (project / "joinery.toml").write_text('[tools]\nenabled=["godot","audacity"]\n')
    return project


def test_recipe_is_the_whole_step_list_with_its_outputs() -> None:
    script = json.loads(
        AUDACITY.script(
            "audio/door.aup3",
            "",
            json.dumps({"steps": STEPS}),
            ["audio/door.wav", "audio/door.txt"],
        )
    )
    assert script == {
        "joinery_audacity": 1,
        "steps": STEPS,
        "audio": [{"path": "audio/door.wav", "channels": 2}],
        "labels": "audio/door.txt",
        "project": "audio/door.aup3",
    }
    # A bare list is accepted too.
    assert (
        json.loads(AUDACITY.script("audio/d.aup3", "", json.dumps(STEPS), []))["steps"]
        == STEPS
    )


def test_imported_recordings_are_the_recipes_sources() -> None:
    assert AUDACITY.script_inputs(json.dumps(STEPS)) == ("audio/takes/door.wav",)


@pytest.mark.parametrize(
    "code, message",
    [
        ("not json", "JSON step list"),
        ('{"steps": []}', "at least one step"),
        ('[{"op": "transcribe"}]', "Unknown Audacity step"),
        ('[{"op": "import", "path": "/etc/passwd"}]', "under audio/"),
        ('[{"op": "import", "path": "art/x.wav"}]', "under audio/"),
    ],
)
def test_bad_step_lists_are_refused(code: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        AUDACITY.script("audio/d.aup3", "", code, [])


@pytest.mark.parametrize(
    "source, exports, message",
    [
        ("art/d.aup3", [], "under audio/"),
        ("audio/d.aup3", ["audio/d.ogg"], "WAV"),
        ("audio/d.aup3", ["audio/a.txt", "audio/b.txt"], "one label"),
    ],
)
def test_destinations_are_audio_wav_and_one_label_file(
    source: str, exports: list[str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        AUDACITY.check_destinations(source, exports)


def test_an_edit_runs_in_the_audacity_worker_and_records_the_recordings(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (stage / "audio/door.aup3").write_bytes(b"SQLite format 3\0 old project")
    execute = AsyncMock(return_value=Result(True, "JOINERY_AUDACITY_OK", "", 0.1))
    record = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr(authoring, "run_offline", execute)
    monkeypatch.setattr(authoring, "_record", record)
    asyncio.run(
        authoring.edit_document(
            stage, "audacity", "audio/door.aup3", json.dumps(STEPS), ["audio/door.wav"]
        )
    )
    assert execute.await_args.kwargs == {"image": AUDACITY_WORKER}
    assert execute.await_args.args[0][0] == "joinery-audacity"
    made: Recipe = record.await_args.args[1]
    assert made.outputs == ("audio/door.aup3", "audio/door.wav")
    # The recording is a source; the old project is not kept as one.
    assert made.sources == ("audio/takes/door.wav",)
    assert not list((stage / "recipes").glob("*.aup3*"))


def test_rebuild_replays_in_the_audacity_worker(tmp_path: Path) -> None:
    seen: list[str] = []

    class Spy(SandboxRunner):
        def run(self, argv: list[str], project: Path, timeout: int = 60) -> Result:
            seen.append(self.image)
            return Result(True, "", "", 0.0)

    made = Recipe(tool="audacity", script="recipes/a.json", outputs=("audio/a.aup3",))
    recipe.run_recipe(made, tmp_path, Spy())
    other = Recipe(tool="surge-xt", script="recipes/s.py", outputs=("audio/s.json",))
    recipe.run_recipe(other, tmp_path, Spy(image="joinery-sandbox"))
    assert seen == [AUDACITY_WORKER, "joinery-sandbox"]


def test_inspect_returns_the_step_list_that_made_the_project(stage: Path) -> None:
    (stage / "recipes").mkdir()
    (stage / "recipes/abc.json").write_text(
        AUDACITY.script("audio/door.aup3", "", json.dumps(STEPS), ["audio/door.wav"])
    )
    found = asyncio.run(
        AUDACITY.inspect(
            stage, stage / "audio/door.aup3", "audio/door.aup3", AsyncMock()
        )
    )
    assert found is not None and found["steps"] == STEPS
    assert found["recipe"] == "recipes/abc.json"
