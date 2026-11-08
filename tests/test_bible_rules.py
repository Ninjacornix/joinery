"""Machine-checked art bible rules: parsing, measuring and reporting."""

import asyncio
import json
import struct
from pathlib import Path

import pytest

from joinery.art_approval import approve
from joinery.bible import render
from joinery.bible_rules import (
    check_rules,
    glb_triangles,
    load_rules,
    parse_loudness,
)
from joinery.runner import Result

# Real ffmpeg ebur128 summary lines, as the sandbox prints them.
MEASURED = """JOINERY_FILE:audio/sfx/loud.wav
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:          -3.8 LUFS
    Threshold: -13.8 LUFS

  Loudness range:
    LRA:         0.0 LU

  True peak:
    Peak:       -0.1 dBFS
JOINERY_FILE:audio/music/quiet.wav
[Parsed_ebur128_0 @ 0x2] Summary:

  Integrated loudness:
    I:         -29.7 LUFS
    Threshold: -39.7 LUFS

  True peak:
    Peak:      -26.0 dBFS
"""

RULES = """# Art Bible

```toml
[audio]
true_peak_max_dbtp = -1.0
[audio.lufs]
"audio/music" = -16.0
"audio/music/quiet-layers" = -30.0
[models.max_triangles]
"art" = 100
"art/props" = 10
```
"""


def glb(triangles_per_mesh: list[int], nodes: list[int] | None) -> bytes:
    accessors = [{"count": n * 3} for n in triangles_per_mesh]
    document: dict[str, object] = {
        "accessors": accessors,
        "meshes": [
            {"primitives": [{"attributes": {"POSITION": i}, "indices": i}]}
            for i in range(len(triangles_per_mesh))
        ],
    }
    if nodes is not None:
        document["nodes"] = [{"mesh": index} for index in nodes]
    payload = json.dumps(document).encode()
    payload += b" " * (-len(payload) % 4)
    header = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(payload))
    return header + struct.pack("<II", len(payload), 0x4E4F534A) + payload


def stage(tmp_path: Path, files: dict[str, bytes]) -> Path:
    (tmp_path / "art").mkdir()
    (tmp_path / "art/bible.md").write_text(RULES)
    for name, data in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(data)
    return tmp_path


def measured(output: str = MEASURED, ok: bool = True):  # type: ignore[no-untyped-def]
    async def measure(_argv: list[str], _root: Path) -> Result:
        return Result(ok, output, "", 0.1)

    return measure


def test_template_rules_are_loaded_from_the_rendered_bible(tmp_path: Path) -> None:
    (tmp_path / "art").mkdir()
    (tmp_path / "art/bible.md").write_text(render({"title": "Fixture"}))
    rules = load_rules(tmp_path)
    assert rules.audio.true_peak_max_dbtp == -1.0
    assert rules.audio.lufs == {"audio/music": -16.0, "audio/ambience": -22.0}
    assert rules.models.max_triangles == {}


def test_template_rules_do_not_block_approval(tmp_path: Path) -> None:
    (tmp_path / "art").mkdir()
    answers = {
        key: "Set"
        for key in ("title", "vision", "references", "look", "palette", "sound", "bans")
    }
    (tmp_path / "art/bible.md").write_text(render(answers))
    approve(tmp_path, "tester")


def test_bible_without_rules_checks_nothing(tmp_path: Path) -> None:
    (tmp_path / "art").mkdir()
    (tmp_path / "art/bible.md").write_text("# Art Bible\nProse only.\n")
    assert load_rules(tmp_path).models.max_triangles == {}


def test_invalid_rules_are_reported_not_ignored(tmp_path: Path) -> None:
    (tmp_path / "art").mkdir()
    (tmp_path / "art/bible.md").write_text("```toml\n[audio]\nunknown = 1\n```\n")
    with pytest.raises(ValueError, match="checked rules are invalid"):
        load_rules(tmp_path)


def test_glb_triangles_count_each_node_instance() -> None:
    assert glb_triangles(glb([4, 2], None)) == 6
    assert glb_triangles(glb([4, 2], [0, 0, 1])) == 10
    with pytest.raises(ValueError, match="binary glTF"):
        glb_triangles(b"not a model at all")


def test_parse_loudness_reads_integrated_and_true_peak() -> None:
    assert parse_loudness(MEASURED) == {
        "audio/sfx/loud.wav": {"lufs": -3.8, "true_peak": -0.1},
        "audio/music/quiet.wav": {"lufs": -29.7, "true_peak": -26.0},
    }


def test_models_use_the_most_specific_folder_budget(tmp_path: Path) -> None:
    root = stage(
        tmp_path,
        {"art/props/crate.glb": glb([12], None), "art/hall.glb": glb([90], None)},
    )
    problems = asyncio.run(
        check_rules(root, ["art/props/crate.glb", "art/hall.glb"], measured(""))
    )
    assert problems == [
        "art/props/crate.glb: 12 triangles, over the art bible's 10 budget"
    ]


def test_audio_peak_and_loudness_violations_are_named(tmp_path: Path) -> None:
    root = stage(
        tmp_path,
        {"audio/sfx/loud.wav": b"x", "audio/music/quiet.wav": b"x"},
    )
    problems = asyncio.run(
        check_rules(root, ["audio/sfx/loud.wav", "audio/music/quiet.wav"], measured())
    )
    assert problems == [
        "audio/music/quiet.wav: -29.7 LUFS, the art bible asks for -16.0 ± 1.0",
        "audio/sfx/loud.wav: true peak -0.1 dBTP, above the art bible's -1.0 dBTP",
    ]


def test_unmeasurable_audio_fails_rather_than_passing(tmp_path: Path) -> None:
    root = stage(tmp_path, {"audio/sfx/new.wav": b"x"})
    problems = asyncio.run(
        check_rules(root, ["audio/sfx/new.wav"], measured("", ok=False))
    )
    assert problems == ["audio/sfx/new.wav: loudness could not be measured"]


def test_unchanged_and_deleted_assets_are_not_measured(tmp_path: Path) -> None:
    root = stage(tmp_path, {"audio/sfx/old.wav": b"x"})
    calls: list[list[str]] = []

    async def measure(argv: list[str], _root: Path) -> Result:
        calls.append(argv)
        return Result(True, "", "", 0.0)

    assert asyncio.run(check_rules(root, ["audio/sfx/deleted.wav"], measure)) == []
    assert calls == []
