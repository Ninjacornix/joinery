"""Export ownership follows actual changes and the same native source only, and
an edit neither drops a sound's audio edits nor duplicates its preimage."""

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from joinery.authoring import _scripted_export, edit_document
from joinery.runner import Result
from servers.sound.ops import EDITS_MARKER


def test_native_export_does_not_claim_unchanged_unrelated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exports = tmp_path / "art/export"
    exports.mkdir(parents=True)
    (exports / "own.tres").write_text("unchanged deterministic export")
    (exports / "other.tres").write_text("another material's export")
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes/own.toml").write_text(
        'version=1\ntool="material-maker"\nscript="recipes/own.py"\noutputs=["art/own.ptex","art/export/own.tres"]\n'
    )
    (tmp_path / "recipes/other.toml").write_text(
        'version=1\ntool="material-maker"\nscript="recipes/other.py"\noutputs=["art/other.ptex","art/export/other.tres"]\n'
    )

    async def execute(*_args: object, **_kwargs: object) -> Result:
        (tmp_path / "art/own.ptex").write_text("native graph")
        (exports / "new.png").write_bytes(b"new native export")
        return Result(True, "", "", 0.1)

    record = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr("joinery.authoring.run_offline", execute)
    monkeypatch.setattr("joinery.authoring._record", record)
    asyncio.run(
        _scripted_export(
            tmp_path,
            "material-maker",
            "art/own.ptex",
            "# logged operation",
            "art/export",
        )
    )
    recipe = record.await_args.args[1]
    assert set(recipe.outputs) == {
        "art/own.ptex",
        "art/export/own.tres",
        "art/export/new.png",
    }
    assert (exports / "other.tres").read_text() == "another material's export"


def _stage(tmp_path: Path, tools: str) -> Path:
    """A project open in a transaction, as edit_document requires."""
    stage = tmp_path / "stage"
    stage.mkdir()
    (tmp_path / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text(f"[tools]\nenabled=[{tools}]\n")
    (stage / "project.godot").write_text("config_version=5\n")
    (stage / "recipes").mkdir()
    return stage


def test_render_over_audio_edits_is_refused_with_a_plain_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _stage(tmp_path, '"supercollider"')
    (stage / "audio").mkdir()
    (stage / "audio/a.scd").write_text("// patch\n0.exit;\n")
    edits = {"base_outputs": ["audio/a.scd", "audio/a.wav"], "edits": [{}]}
    (stage / "recipes/edited.scd").write_text(
        "// " + EDITS_MARKER + json.dumps(edits) + "\n(\n)\n"
    )
    (stage / "recipes/edited.toml").write_text(
        'version=1\ntool="supercollider"\nscript="recipes/edited.scd"\n'
        'outputs=["audio/a.scd","audio/a.wav"]\n'
    )
    run = AsyncMock()
    monkeypatch.setattr("joinery.authoring.run_offline", run)
    with pytest.raises(ValueError) as refused:
        asyncio.run(
            edit_document(
                stage, "supercollider", "audio/a.scd", "0.exit;", ["audio/a.wav"]
            )
        )
    message = str(refused.value)
    assert message.startswith("audio/a.scd has audio edits that a new render")
    assert "set_surge_parameters" in message and "process_audio" in message
    run.assert_not_called()
    # Nothing was written: no script, no preimage, the source is unchanged.
    assert sorted(p.name for p in (stage / "recipes").iterdir()) == [
        "edited.scd",
        "edited.toml",
    ]
    assert (stage / "audio/a.scd").read_text() == "// patch\n0.exit;\n"


def test_render_without_audio_edits_is_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _stage(tmp_path, '"supercollider"')
    (stage / "recipes/plain.scd").write_text("(\n)\n")
    (stage / "recipes/plain.toml").write_text(
        'version=1\ntool="supercollider"\nscript="recipes/plain.scd"\n'
        'outputs=["audio/a.scd","audio/a.wav"]\n'
    )
    monkeypatch.setattr(
        "joinery.authoring.run_offline",
        AsyncMock(return_value=Result(True, "", "", 0.1)),
    )
    record = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr("joinery.authoring._record", record)
    asyncio.run(
        edit_document(stage, "supercollider", "audio/a.scd", "0.exit;", ["audio/a.wav"])
    )
    record.assert_awaited_once()


def test_edits_of_the_same_source_share_one_content_named_preimage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _stage(tmp_path, '"krita"')
    (stage / "art").mkdir()
    document = b"PK krita document bytes"
    (stage / "art/a.kra").write_bytes(document)
    monkeypatch.setattr(
        "joinery.authoring.run_offline",
        AsyncMock(return_value=Result(True, "", "", 0.1)),
    )
    record = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr("joinery.authoring._record", record)
    for code in ("first = 1", "second = 2"):
        asyncio.run(edit_document(stage, "krita", "art/a.kra", code, ["art/a.png"]))
    first, second = (call.args[1] for call in record.await_args_list)
    name = hashlib.sha256(document).hexdigest() + ".kra"
    assert first.sources == second.sources == (f"recipes/inputs/{name}",)
    stored = [p.name for p in (stage / "recipes/inputs").iterdir()]
    assert sorted(stored) == [".gdignore", name]
    assert (stage / "recipes/inputs" / name).read_bytes() == document
