"""Machine-checked art bible rules: parsing, measuring and reporting."""

import asyncio
import io
import json
import os
import shutil
import struct
import subprocess
import wave
import zlib
from pathlib import Path
from typing import Any

import pytest

from joinery.art_approval import approve
from joinery.bible import render
from joinery.bible_blend import MEASURE_BLEND, parse_measures
from joinery.bible_rules import (
    CVD_MATRICES,
    BibleRules,
    check_rules,
    contrast_ratio,
    cvd_delta_e,
    delta_e_2000,
    glb_triangles,
    image_size,
    linear_rgb,
    load_rules,
    measure_glb,
    palette_problems,
    parse_loudness,
    parse_rules,
    rules_text,
    simulate_cvd,
    ui_resource_problems,
    ui_resources,
    viewport_height,
    wav_format,
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
    # Holds for the first template and the second (whichever is installed).
    assert rules.audio.true_peak_max_dbtp == -1.0
    assert rules.audio.lufs == {"audio/music": -16.0, "audio/ambience": -22.0}
    assert rules.exclude == ["art/reference"]


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


@pytest.mark.parametrize(
    ("lab1", "lab2", "expected"),
    [
        # Reference pairs from Sharma, Wu and Dalal (2005), table 1.
        ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
        ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
        ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
        ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
    ],
)
def test_delta_e_2000_matches_reference_pairs(
    lab1: tuple[float, float, float], lab2: tuple[float, float, float], expected: float
) -> None:
    assert delta_e_2000(lab1, lab2) == pytest.approx(expected, abs=1e-4)


def palette_stage(tmp_path: Path, colours: str) -> Path:
    (tmp_path / "art/textures").mkdir(parents=True)
    (tmp_path / "art/bible.md").write_text(
        f"```toml\n[palette]\ncolours = {colours}\nmax_off_palette = 0.25\n```\n"
    )
    (tmp_path / "art/textures/wall.png").write_bytes(b"x")
    return tmp_path


def samples(*pixels: str) -> str:
    return "JOINERY_FILE:art/textures/wall.png\n" + "".join(pixels) + "\n"


def test_palette_counts_opaque_pixels_far_from_every_colour(tmp_path: Path) -> None:
    root = palette_stage(tmp_path, '["#ff0000", "#0000ff"]')
    near_red, blue, green, clear_green = "fa0505ff", "0000ffff", "00ff00ff", "00ff0000"
    within = samples(near_red, blue, blue, green, clear_green)
    assert (
        asyncio.run(check_rules(root, ["art/textures/wall.png"], measured(within)))
        == []
    )
    beyond = samples(near_red, green, green, blue)
    assert asyncio.run(
        check_rules(root, ["art/textures/wall.png"], measured(beyond))
    ) == [
        (
            "art/textures/wall.png: 50% of its colours are more than ΔE 12 from the "
            "palette (allowed 25%)"
        )
    ]


def test_empty_palette_checks_no_images(tmp_path: Path) -> None:
    root = palette_stage(tmp_path, "[]")
    assert (
        asyncio.run(
            check_rules(root, ["art/textures/wall.png"], measured("", ok=False))
        )
        == []
    )


def test_bad_palette_colour_is_reported(tmp_path: Path) -> None:
    (tmp_path / "art").mkdir()
    (tmp_path / "art/bible.md").write_text(
        '```toml\n[palette]\ncolours = ["red"]\n```\n'
    )
    with pytest.raises(ValueError, match="checked rules are invalid"):
        load_rules(tmp_path)


# --- format 2: parsing -------------------------------------------------------

V2_BLOCK = """schema = 2
exclude = ["art/reference"]

[audio.format."audio/sfx"]
container = "wav"
sample_rates = [44100, 48000]
channels = [1]
bit_depths = [16]

[audio.format."audio/music"]
container = "ogg"
sample_rates = [48000]
channels = [2]

[models]
applied_scale = true
pivot_tolerance_m = 0.02

[models.max_triangles]
"art/props" = 100

[models.max_bones]
"art/characters" = 2

[models.texel_density]
"art" = { px_per_m = 256, tolerance = 0.2 }

[models.pivot]
"art/props" = "bottom-centre"

[materials]
folders = ["art"]
roughness = [0.4, 0.95]
metallic = [0.0, 1.0]
metallic_requires_name = "(?i)metal"
albedo_luminance = [0.03, 0.92]
albedo_max_outside = 0.25

[naming.files]
"art" = "^[a-z0-9]+(_[a-z0-9]+)*$"

[naming.nodes]
"art" = "^[A-Z][A-Za-z0-9]*(_[A-Z][A-Za-z0-9]*){0,2}$"

[textures]
folders = ["art"]
power_of_two = true

[textures.max_size]
"art" = 512
"art/ui" = 64

[sprites.max_colours]
"art/sprites" = 4

[accessibility]
min_ui_contrast = 4.5
min_text_px = 18
ui_folders = ["ui", "game/ui"]
"""


def v2_stage(tmp_path: Path, files: dict[str, bytes], block: str = V2_BLOCK) -> Path:
    (tmp_path / "art").mkdir(exist_ok=True)
    (tmp_path / "art/bible.md").write_text(
        f"# Art Bible\n\n## 18. Checked rules\n\n```toml\n{block}```\n"
    )
    for name, data in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(data)
    return tmp_path


def run_checks(
    root: Path, paths: list[str], output: str = "", ok: bool = True
) -> list[str]:
    return asyncio.run(check_rules(root, paths, measured(output, ok)))


def test_v2_block_parses_every_family() -> None:
    rules = parse_rules(V2_BLOCK)
    assert rules.schema_version == 2
    assert rules.audio.format["audio/sfx"].bit_depths == [16]
    assert rules.models.texel_density["art"].px_per_m == 256
    assert rules.models.pivot == {"art/props": "bottom-centre"}
    assert rules.materials.albedo_luminance == (0.03, 0.92)
    assert rules.sprites.max_colours == {"art/sprites": 4}
    assert rules.accessibility.min_text_px == 18


def test_v1_block_parses_unchanged_and_new_families_are_off() -> None:
    rules = parse_rules(RULES.split("```toml\n")[1].split("```")[0])
    assert rules.schema_version == 1
    assert rules.models.max_triangles == {"art": 100, "art/props": 10}
    assert rules.models.applied_scale is False
    assert rules.materials.roughness is None
    assert rules.naming.files == {} and rules.textures.max_size == {}
    assert rules.accessibility.min_ui_contrast is None
    assert rules.exclude == ["art/reference"]


def test_newer_schema_and_typos_are_reported() -> None:
    with pytest.raises(ValueError, match="newer Joinery"):
        parse_rules("schema = 3\n")
    with pytest.raises(ValueError, match="roughnes"):
        parse_rules("[materials]\nroughnes = [0.1, 0.2]\n")
    with pytest.raises(ValueError, match="low, high"):
        parse_rules("[materials]\nroughness = [0.9, 0.2]\n")
    with pytest.raises(ValueError, match="naming pattern"):
        parse_rules('[naming.files]\n"art" = "("\n')
    with pytest.raises(ValueError, match="not valid TOML"):
        parse_rules("[audio\n")


def test_rules_block_is_the_one_under_checked_rules() -> None:
    text = (
        "# Bible\n\n```toml\n[audio]\nnot_rules = 1\n```\n\n"
        "## 18. Checked rules\n\n```toml\nschema = 2\n```\n"
    )
    assert rules_text(text) == "schema = 2\n"
    assert rules_text("```toml\nschema = 1\n```\n") == "schema = 1\n"
    assert rules_text("no block") is None


# --- fixtures made in the test -----------------------------------------------


def png(
    width: int, height: int, colours: list[tuple[int, int, int, int]] | None = None
) -> bytes:
    pixels = colours or [(120, 110, 100, 255)]
    rows = b"".join(
        b"\x00" + b"".join(bytes(pixels[(x + y) % len(pixels)]) for x in range(width))
        for y in range(height)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def jpeg_header(width: int, height: int) -> bytes:
    app0 = (
        b"\xff\xe0"
        + struct.pack(">H", 16)
        + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    )
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def wav(rate: int = 48000, channels: int = 1, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(b"\x00" * width * channels * 32)
    return buffer.getvalue()


class GlbBuilder:
    """A binary glTF written by hand: positions, UVs, indices, an embedded PNG."""

    def __init__(self) -> None:
        self.binary = bytearray()
        self.doc: dict[str, Any] = {
            "asset": {"version": "2.0"},
            "accessors": [],
            "bufferViews": [],
            "meshes": [],
            "nodes": [],
            "materials": [],
        }

    def _view(self, data: bytes) -> int:
        while len(self.binary) % 4:
            self.binary.append(0)
        self.doc["bufferViews"].append(
            {"buffer": 0, "byteOffset": len(self.binary), "byteLength": len(data)}
        )
        self.binary += data
        return len(self.doc["bufferViews"]) - 1

    def _accessor(self, values: list[tuple[float, ...]], kind: str, code: int) -> int:
        fmt = {5126: "f", 5123: "H"}[code]
        data = b"".join(struct.pack("<" + fmt * len(v), *v) for v in values)
        accessor: dict[str, Any] = {
            "bufferView": self._view(data),
            "componentType": code,
            "count": len(values),
            "type": kind,
        }
        if kind == "VEC3":
            accessor["min"] = [min(v[i] for v in values) for i in range(3)]
            accessor["max"] = [max(v[i] for v in values) for i in range(3)]
        self.doc["accessors"].append(accessor)
        return len(self.doc["accessors"]) - 1

    def texture(self, image: bytes) -> int:
        self.doc.setdefault("images", []).append(
            {"bufferView": self._view(image), "mimeType": "image/png"}
        )
        self.doc.setdefault("textures", []).append(
            {"source": len(self.doc["images"]) - 1}
        )
        return len(self.doc["textures"]) - 1

    def material(self, name: str, **pbr: Any) -> int:
        self.doc["materials"].append({"name": name, "pbrMetallicRoughness": pbr})
        return len(self.doc["materials"]) - 1

    def quad(
        self,
        name: str,
        material: int | None = None,
        size: float = 1.0,
        lift: float = 0.0,
        **node: Any,
    ) -> int:
        """A size x size quad on the XZ plane, centred, at height `lift`."""
        h = size / 2
        positions = [(-h, lift, -h), (h, lift, -h), (h, lift, h), (-h, lift, h)]
        primitive: dict[str, Any] = {
            "attributes": {
                "POSITION": self._accessor(positions, "VEC3", 5126),
                "TEXCOORD_0": self._accessor(
                    [(0, 0), (1, 0), (1, 1), (0, 1)], "VEC2", 5126
                ),
            },
            "indices": self._accessor(
                [(0,), (1,), (2,), (0,), (2,), (3,)], "SCALAR", 5123
            ),
        }
        if material is not None:
            primitive["material"] = material
        self.doc["meshes"].append({"name": name, "primitives": [primitive]})
        self.doc["nodes"].append(
            {"name": name, "mesh": len(self.doc["meshes"]) - 1, **node}
        )
        return len(self.doc["nodes"]) - 1

    def skin(self, joints: int) -> None:
        start = len(self.doc["nodes"])
        self.doc["nodes"] += [{"name": f"bone.{i}"} for i in range(joints)]
        self.doc["skins"] = [{"joints": list(range(start, start + joints))}]

    def build(self) -> bytes:
        while len(self.binary) % 4:
            self.binary.append(0)
        self.doc["buffers"] = [{"byteLength": len(self.binary)}]
        payload = json.dumps(self.doc).encode()
        payload += b" " * (-len(payload) % 4)
        total = 12 + 8 + len(payload) + 8 + len(self.binary)
        return (
            struct.pack("<4sII", b"glTF", 2, total)
            + struct.pack("<II", len(payload), 0x4E4F534A)
            + payload
            + struct.pack("<II", len(self.binary), 0x004E4942)
            + bytes(self.binary)
        )


def good_crate() -> GlbBuilder:
    builder = GlbBuilder()
    texture = builder.texture(png(256, 256))
    wood = builder.material(
        "Crate_Wood",
        baseColorTexture={"index": texture},
        roughnessFactor=0.7,
        metallicFactor=0.0,
    )
    builder.quad("Crate_Wood", wood)
    return builder


# --- models ------------------------------------------------------------------


def test_measure_glb_reads_geometry_textures_and_materials() -> None:
    builder = good_crate()
    builder.skin(3)
    measured = measure_glb(builder.build())
    assert measured.triangles == 2
    assert measured.bones == 3
    assert measured.bbox_min == pytest.approx((-0.5, 0.0, -0.5))
    assert measured.bbox_max == pytest.approx((0.5, 0.0, 0.5))
    assert measured.texel_density == {"Crate_Wood": pytest.approx(256.0)}
    assert measured.materials[0].roughness == pytest.approx(0.7)
    assert measured.materials[0].base_luminance is None  # textured
    assert [n.name for n in measured.nodes if n.joint] == ["bone.0", "bone.1", "bone.2"]


def test_a_clean_glb_passes_every_model_rule(tmp_path: Path) -> None:
    root = v2_stage(tmp_path, {"art/props/crate.glb": good_crate().build()})
    assert run_checks(root, ["art/props/crate.glb"]) == []


def test_glb_scale_pivot_and_texel_density_are_measured(tmp_path: Path) -> None:
    builder = GlbBuilder()
    texture = builder.texture(png(256, 256))
    wood = builder.material(
        "Crate_Wood",
        baseColorTexture={"index": texture},
        roughnessFactor=0.7,
        metallicFactor=0.0,
    )
    builder.quad("Crate_Wood", wood, scale=[2.0, 2.0, 2.0], translation=[0.0, 0.5, 0.0])
    root = v2_stage(tmp_path, {"art/props/crate.glb": builder.build()})
    assert run_checks(root, ["art/props/crate.glb"]) == [
        "art/props/crate.glb: scale is not applied on 'Crate_Wood'; the art bible asks for scale 1",
        (
            "art/props/crate.glb: model pivot is not bottom-centre (lowest point at y 0, "
            "centred in x and z): lowest y 0.500, centre x 0.000 z 0.000 m (tolerance 0.02 m)"
        ),
        "art/props/crate.glb: 'Crate_Wood' texel density 128 px/m, the art bible asks for 256 ± 20%",
    ]


def test_glb_bones_names_and_material_factors_are_measured(tmp_path: Path) -> None:
    builder = GlbBuilder()
    shiny = builder.material(
        "Shiny_Plastic",
        roughnessFactor=0.1,
        metallicFactor=1.0,
        baseColorFactor=[0.5, 0.5, 0.5, 1.0],
    )
    white = builder.material("Snow", roughnessFactor=0.8, metallicFactor=0.0)
    builder.quad("cube.001", shiny)
    builder.quad("Snow", white)
    builder.skin(3)
    root = v2_stage(tmp_path, {"art/characters/hero.glb": builder.build()})
    problems = run_checks(root, ["art/characters/hero.glb"])
    assert problems == [
        "art/characters/hero.glb: 3 bones in a skin, over the art bible's 2 budget",
        (
            "art/characters/hero.glb: node names 'cube.001'; mesh names 'cube.001' "
            "do not follow the art bible's naming pattern ^[A-Z][A-Za-z0-9]*(_[A-Z][A-Za-z0-9]*){0,2}$"
        ),
        "art/characters/hero.glb: material 'Shiny_Plastic' roughness 0.10, the art bible allows 0.4-0.95",
        (
            "art/characters/hero.glb: material 'Shiny_Plastic' is metallic (1.00) but its "
            "name does not say metal; the art bible keeps metallic for real metal"
        ),
        (
            "art/characters/hero.glb: material 'Snow' base colour is near pure white "
            "(luminance 1.000; the art bible allows 0.03-0.92)"
        ),
    ]


def test_unreadable_glb_is_reported_not_raised(tmp_path: Path) -> None:
    root = v2_stage(tmp_path, {"art/props/broken.glb": b"glTF but not really"})
    assert run_checks(root, ["art/props/broken.glb"]) == [
        "art/props/broken.glb: model could not be measured (Not a binary glTF file.)"
    ]


def blend_line(path: str, measure: dict[str, Any]) -> str:
    return f"JOINERY_FILE:{path}\nBlender 5.2 noise\nJOINERY_MEASURE:{json.dumps(measure)}\n"


HERO = {
    "path": "/work/art/characters/hero.blend",
    "objects": [
        {
            "name": "Hero_Body",
            "type": "MESH",
            "parent": "Hero_Rig",
            "scale": [1.0, 1.0, 1.0],
            "bbox_min": [-0.3, 0.0, -0.2],
            "bbox_max": [0.3, 1.8, 0.2],
            "triangles": 9000,
            "mesh": "Hero_Body",
            "texel_density": 250.0,
        },
        {
            "name": "Hero_Rig",
            "type": "ARMATURE",
            "parent": None,
            "scale": [1.0, 1.0, 1.0],
            "bbox_min": [-0.5, 0.0, -0.5],
            "bbox_max": [0.5, 2.0, 0.5],
            "triangles": 0,
            "mesh": None,
            "texel_density": None,
        },
    ],
    "armatures": [{"name": "Hero_Rig", "deform_bones": 2}],
    "materials": [
        {
            "name": "Hero_Cloth",
            "roughness": 0.8,
            "metallic": 0.0,
            "base_color": [0.4, 0.3, 0.2],
            "linked": [],
        }
    ],
}


def test_blend_measurements_pass_a_clean_file(tmp_path: Path) -> None:
    root = v2_stage(tmp_path, {"art/characters/hero.blend": b"BLENDER"})
    calls: list[list[str]] = []

    async def measure(argv: list[str], _root: Path) -> Result:
        calls.append(argv)
        return Result(True, blend_line("art/characters/hero.blend", HERO), "", 1.0)

    assert asyncio.run(check_rules(root, ["art/characters/hero.blend"], measure)) == []
    assert calls[0][:2] == ["sh", "-c"] and calls[0][3] == MEASURE_BLEND
    assert calls[0][4:] == ["art/characters/hero.blend"]
    # Scripts stored in a .blend never run while it is measured.
    assert "blender -b --disable-autoexec " in calls[0][2]


def test_blend_budgets_scale_pivot_and_materials_fail(tmp_path: Path) -> None:
    crate = {
        "path": "/work/art/props/crate.blend",
        "objects": [
            {
                "name": "Crate.001",
                "type": "MESH",
                "parent": None,
                "scale": [2.0, 2.0, 2.0],
                "bbox_min": [0.0, 0.0, 0.0],
                "bbox_max": [2.0, 2.0, 2.0],
                "triangles": 120,
                "mesh": "Cube",
                "texel_density": 400.0,
            }
        ],
        "armatures": [{"name": "Rig", "deform_bones": 9}],
        "materials": [
            {
                "name": "Gold",
                "roughness": 0.2,
                "metallic": 1.0,
                "base_color": [0.0, 0.0, 0.0],
                "linked": [],
            },
            {
                "name": "Painted",
                "roughness": None,
                "metallic": 0.0,
                "base_color": None,
                "linked": ["roughness", "base_color"],
            },
        ],
    }
    root = v2_stage(tmp_path, {"art/props/crate.blend": b"BLENDER"})
    problems = run_checks(
        root, ["art/props/crate.blend"], blend_line("art/props/crate.blend", crate)
    )
    assert problems == [
        "art/props/crate.blend: 120 triangles, over the art bible's 100 budget",
        "art/props/crate.blend: scale is not applied on 'Crate.001'; the art bible asks for scale 1",
        (
            "art/props/crate.blend: 'Crate.001' pivot is not bottom-centre (lowest point at "
            "y 0, centred in x and z): lowest y 0.000, centre x 1.000 z 1.000 m (tolerance 0.02 m)"
        ),
        "art/props/crate.blend: 'Crate.001' texel density 400 px/m, the art bible asks for 256 ± 20%",
        (
            "art/props/crate.blend: object names 'Crate.001' do not follow the art bible's "
            "naming pattern ^[A-Z][A-Za-z0-9]*(_[A-Z][A-Za-z0-9]*){0,2}$"
        ),
        "art/props/crate.blend: material 'Gold' roughness 0.20, the art bible allows 0.4-0.95",
        (
            "art/props/crate.blend: material 'Gold' is metallic (1.00) but its name does not "
            "say metal; the art bible keeps metallic for real metal"
        ),
        (
            "art/props/crate.blend: material 'Gold' base colour is near pure black "
            "(luminance 0.000; the art bible allows 0.03-0.92)"
        ),
    ]


def test_blend_bone_budget_counts_deform_bones(tmp_path: Path) -> None:
    rig = json.loads(json.dumps(HERO))
    rig["armatures"][0]["deform_bones"] = 3
    root = v2_stage(tmp_path, {"art/characters/hero.blend": b"BLENDER"})
    assert run_checks(
        root,
        ["art/characters/hero.blend"],
        blend_line("art/characters/hero.blend", rig),
    ) == [
        "art/characters/hero.blend: armature 'Hero_Rig' has 3 deform bones, over the art bible's 2 budget"
    ]


def test_blend_that_blender_cannot_open_is_not_a_pass(tmp_path: Path) -> None:
    root = v2_stage(tmp_path, {"art/props/broken.blend": b"nope"})
    output = "JOINERY_FILE:art/props/broken.blend\nError: Cannot read file\n"
    assert run_checks(root, ["art/props/broken.blend"], output, ok=False) == [
        "art/props/broken.blend: Blender could not measure this file"
    ]


def test_v1_triangle_budget_now_also_measures_blend_sources(tmp_path: Path) -> None:
    root = stage(tmp_path, {"art/props/crate.blend": b"BLENDER"})
    crate = {
        "objects": [
            {
                "name": "anything goes",
                "type": "MESH",
                "scale": [3.0, 1.0, 1.0],
                "bbox_min": [0, 0, 0],
                "bbox_max": [1, 1, 1],
                "triangles": 11,
            }
        ]
    }
    problems = asyncio.run(
        check_rules(
            root,
            ["art/props/crate.blend"],
            measured(blend_line("art/props/crate.blend", crate)),
        )
    )
    # Only the v1 rule applies: no scale, naming or material checks were opted into.
    assert problems == [
        "art/props/crate.blend: 11 triangles, over the art bible's 10 budget"
    ]


def test_parse_measures_skips_garbage() -> None:
    output = "JOINERY_FILE:a.blend\nJOINERY_MEASURE:{not json\nJOINERY_FILE:b.blend\n"
    assert parse_measures(output) == {}


# --- audio, images, sprites --------------------------------------------------


def test_wav_format_reads_the_fmt_chunk() -> None:
    got = wav_format(wav(44100, 2, 3))
    assert (got.container, got.sample_rate, got.channels, got.bits) == (
        "wav",
        44100,
        2,
        24,
    )
    with pytest.raises(ValueError, match="RIFF WAVE"):
        wav_format(b"OggS")


def test_audio_format_per_folder(tmp_path: Path) -> None:
    files = {
        "audio/sfx/ok.wav": wav(48000, 1, 2),
        "audio/sfx/wide.wav": wav(22050, 2, 1),
        "audio/music/theme.wav": wav(48000, 2, 2),
        "audio/music/loop.ogg": b"OggS",
        "audio/music/bad.ogg": b"OggS",
    }
    root = v2_stage(tmp_path, files)
    probe = (
        "JOINERY_FILE:audio/music/bad.ogg\n"
        '{"programs": [], "streams": [{"codec_name": "vorbis", "sample_rate": "44100", "channels": 1}]}\n'
        "JOINERY_FILE:audio/music/loop.ogg\n"
        '{"programs": [], "streams": [{"codec_name": "vorbis", "sample_rate": "48000", "channels": 2}]}\n'
    )
    problems = run_checks(root, sorted(files), probe)
    assert problems == [
        "audio/music/theme.wav: WAV file, the art bible asks for OGG in audio/music",
        "audio/sfx/wide.wav: 22050 Hz, the art bible allows 44100 Hz, 48000 Hz",
        "audio/sfx/wide.wav: 2 channel(s), the art bible allows 1",
        "audio/sfx/wide.wav: 8-bit, the art bible allows 16-bit",
        "audio/music/bad.ogg: 44100 Hz, the art bible allows 48000 Hz",
        "audio/music/bad.ogg: 1 channel(s), the art bible allows 2",
    ]


def test_unprobed_ogg_is_not_a_pass(tmp_path: Path) -> None:
    root = v2_stage(tmp_path, {"audio/music/loop.ogg": b"OggS"})
    assert run_checks(root, ["audio/music/loop.ogg"], "", ok=False) == [
        "audio/music/loop.ogg: audio format could not be read"
    ]


def test_image_size_reads_png_jpeg_and_webp_headers() -> None:
    assert image_size(png(3, 5), ".png") == (3, 5)
    assert image_size(jpeg_header(640, 480), ".jpg") == (640, 480)
    vp8x = (
        b"RIFF"
        + b"\x00" * 4
        + b"WEBPVP8X"
        + b"\x00" * 8
        + (99).to_bytes(3, "little")
        + (49).to_bytes(3, "little")
    )
    assert image_size(vp8x, ".webp") == (100, 50)
    lossless = (
        b"RIFF"
        + b"\x00" * 4
        + b"WEBPVP8L"
        + b"\x00" * 4
        + b"\x2f"
        + ((31) | (15 << 14)).to_bytes(4, "little")
    )
    assert image_size(lossless, ".webp") == (32, 16)
    assert image_size(b"not an image", ".png") is None


def test_texture_size_and_power_of_two(tmp_path: Path) -> None:
    files = {
        "art/textures/wall_albedo.png": png(256, 256),
        "art/textures/huge_normal.png": png(1024, 1024),
        "art/ui/odd_icon.jpg": jpeg_header(96, 64),
        "art/textures/broken_mask.png": b"zz",
    }
    root = v2_stage(tmp_path, files)
    samples_out = "JOINERY_FILE:art/textures/wall_albedo.png\n" + "786e64ff" * 4 + "\n"
    assert run_checks(root, sorted(files), samples_out) == [
        "art/textures/broken_mask.png: image size could not be read",
        "art/textures/huge_normal.png: 1024x1024, over the art bible's 512 px limit",
        "art/ui/odd_icon.jpg: 96x64, over the art bible's 64 px limit",
        "art/ui/odd_icon.jpg: 96x64 is not a power of two on both sides",
    ]


def test_albedo_textures_may_not_be_pure_black_or_white(tmp_path: Path) -> None:
    root = v2_stage(tmp_path, {"art/textures/wall_albedo.png": png(4, 4)})
    output = (
        "JOINERY_FILE:art/textures/wall_albedo.png\n"
        + "000000ff" * 2
        + "ffffffff"
        + "786e64ff"
        + "\n"
    )
    assert run_checks(root, ["art/textures/wall_albedo.png"], output) == [
        (
            "art/textures/wall_albedo.png: 75% of the albedo is near pure black or white "
            "(luminance outside 0.03-0.92; allowed 25%)"
        )
    ]
    fine = "JOINERY_FILE:art/textures/wall_albedo.png\n" + "786e64ff" * 4 + "\n"
    assert run_checks(root, ["art/textures/wall_albedo.png"], fine) == []


def test_sprites_count_distinct_colours(tmp_path: Path) -> None:
    root = v2_stage(
        tmp_path, {"art/sprites/hero.png": png(4, 4), "art/sprites/coin.png": png(4, 4)}
    )
    output = (
        "JOINERY_FILE:art/sprites/coin.png\nJOINERY_COLOURS:3\n"
        "JOINERY_FILE:art/sprites/hero.png\nJOINERY_COLOURS:9\n"
    )
    assert run_checks(
        root, ["art/sprites/coin.png", "art/sprites/hero.png"], output
    ) == ["art/sprites/hero.png: 9 distinct colours, over the art bible's 4 per sprite"]
    assert run_checks(root, ["art/sprites/coin.png"], "", ok=False) == [
        "art/sprites/coin.png: colours could not be counted"
    ]


def test_file_names_follow_the_folder_pattern(tmp_path: Path) -> None:
    root = v2_stage(
        tmp_path,
        {
            "art/textures/Wall Albedo.png": png(4, 4),
            "art/props/crate.glb": good_crate().build(),
        },
    )
    problems = run_checks(root, ["art/textures/Wall Albedo.png", "art/props/crate.glb"])
    assert problems == [
        (
            "art/textures/Wall Albedo.png: file name 'Wall Albedo' does not follow the "
            "art bible's pattern ^[a-z0-9]+(_[a-z0-9]+)*$"
        )
    ]


def test_reference_boards_are_never_checked(tmp_path: Path) -> None:
    files = {
        "art/reference/Dusk Board.png": png(1000, 3),
        "art/reference/quarry.blend": b"x",
    }
    root = v2_stage(tmp_path, files)
    calls: list[list[str]] = []

    async def measure(argv: list[str], _root: Path) -> Result:
        calls.append(argv)
        return Result(False, "", "", 0.0)

    assert asyncio.run(check_rules(root, sorted(files), measure)) == []
    assert calls == []


def test_palette_roles_join_the_palette(tmp_path: Path) -> None:
    (tmp_path / "art/textures").mkdir(parents=True)
    (tmp_path / "art/bible.md").write_text(
        '```toml\n[palette]\ncolours = ["#ff0000"]\nmax_off_palette = 0.0\n'
        '[palette.roles]\nsafe = "#0000ff"\n```\n'
    )
    (tmp_path / "art/textures/wall.png").write_bytes(b"x")
    blue = samples("0000ffff", "ff0000ff")
    assert run_checks(tmp_path, ["art/textures/wall.png"], blue) == []


# --- colour maths ------------------------------------------------------------


def test_wcag_contrast_matches_known_values() -> None:
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)
    assert contrast_ratio("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0)


def test_machado_simulation_applies_the_published_matrix() -> None:
    # Pure red is (1, 0, 0) in linear RGB, so the result is the first column.
    column = [row[0] for row in CVD_MATRICES["protanopia"]]
    expected = tuple(min(1.0, max(0.0, c)) for c in column)
    assert simulate_cvd("#ff0000", "protanopia") == pytest.approx(expected)
    assert linear_rgb("#ffffff") == pytest.approx((1.0, 1.0, 1.0))
    # Greys are unchanged by every simulation (rows sum to 1).
    for kind in CVD_MATRICES:
        assert simulate_cvd("#808080", kind) == pytest.approx(
            linear_rgb("#808080"), abs=1e-3
        )


def test_red_and_green_merge_for_deutans_but_blue_and_yellow_do_not() -> None:
    normal = delta_e_2000((53.2, 80.1, 67.2), (46.2, -51.7, 49.9))  # red vs green, Lab
    assert normal > 50
    assert cvd_delta_e("#d62728", "#2ca02c", "deuteranopia") < 15
    assert cvd_delta_e("#1f77b4", "#ffdd00", "deuteranopia") > 40


def test_palette_problems_report_contrast_and_colour_blind_clashes() -> None:
    rules = BibleRules.model_validate(
        {
            "palette": {
                "roles": {
                    "ui_base": "#2b2d42",
                    "ui_text": "#4a4d6a",
                    "danger": "#d62728",
                    "safe": "#2ca02c",
                }
            },
            "accessibility": {
                "min_ui_contrast": 4.5,
                "ui_pairs": [
                    ["ui_text", "ui_base"],
                    ["#ffffff", "ui_base"],
                    ["ink", "ui_base"],
                ],
                "colour_blind_distinct": ["danger", "safe", "glow"],
            },
        }
    )
    problems = palette_problems(rules)
    assert problems[0].startswith("ui_text on ui_base has contrast 1.")
    assert problems[0].endswith("below the 4.5:1 the bible asks for")
    assert (
        problems[1]
        == "accessibility.ui_pairs names 'ink', which [palette.roles] does not define"
    )
    assert problems[2] == (
        "accessibility.colour_blind_distinct names 'glow', which [palette.roles] does not define"
    )
    assert any(
        p.startswith("danger and safe merge under deuteranopia: ΔE ") for p in problems
    )
    assert all("need 10" in p for p in problems[3:])


# --- UI resources ------------------------------------------------------------

THEME = """[gd_resource type="Theme" load_steps=3 format=3 uid="uid://btheme"]

[sub_resource type="StyleBoxFlat" id="StyleBoxFlat_dark"]
bg_color = Color(0.2, 0.2, 0.2, 1)

[sub_resource type="StyleBoxFlat" id="StyleBoxFlat_light"]
bg_color = Color(0.9, 0.9, 0.9, 1)

[resource]
default_font_size = 24
Button/colors/font_color = Color(0.3, 0.3, 0.3, 1)
Button/colors/font_hover_color = Color(0, 0, 0, 1)
Button/styles/normal = SubResource("StyleBoxFlat_dark")
Button/styles/hover = SubResource("StyleBoxFlat_light")
Label/font_sizes/font_size = 10
"""

SCENE = """[gd_scene load_steps=2 format=3 uid="uid://bhud"]

[sub_resource type="StyleBoxFlat" id="StyleBoxFlat_x"]
bg_color = Color(0, 0, 0, 1)

[sub_resource type="LabelSettings" id="LabelSettings_s"]
font_size = 9

[node name="Hud" type="Control"]

[node name="Play" type="Button" parent="."]
theme_override_colors/font_color = Color(1, 1, 1, 1)
theme_override_styles/normal = SubResource("StyleBoxFlat_x")
theme_override_font_sizes/font_size = 11
text = "Play"
"""


def test_ui_theme_contrast_and_font_sizes() -> None:
    rules = parse_rules(V2_BLOCK).accessibility
    problems = ui_resource_problems("ui/theme.tres", THEME, rules, 1080)
    assert problems == [
        "ui/theme.tres: Button text on its normal background has contrast 1.5:1, below the art bible's 4.5:1",
        "ui/theme.tres: font size 10 (Label) is below the art bible's 18 px at 1080p (18 px at this project's 1080 px viewport)",
    ]


def test_ui_scene_overrides_scale_with_the_viewport() -> None:
    rules = parse_rules(V2_BLOCK).accessibility
    # 18 px at 1080p is 12 px at 720p: 11 fails, white on black passes contrast.
    assert ui_resource_problems("game/ui/hud.tscn", SCENE, rules, 720) == [
        "game/ui/hud.tscn: font size 9 (LabelSettings_s) is below the art bible's 18 px at 1080p (12 px at this project's 720 px viewport)",
        "game/ui/hud.tscn: font size 11 (Play) is below the art bible's 18 px at 1080p (12 px at this project's 720 px viewport)",
    ]
    assert ui_resource_problems("game/ui/hud.tscn", SCENE, rules, 540) == []


def test_viewport_height_comes_from_project_godot(tmp_path: Path) -> None:
    assert viewport_height(tmp_path) == 648
    (tmp_path / "project.godot").write_text(
        "[display]\n\nwindow/size/viewport_width=1280\nwindow/size/viewport_height=720\n"
    )
    assert viewport_height(tmp_path) == 720


def test_ui_resources_in_ui_folders_are_checked(tmp_path: Path) -> None:
    root = v2_stage(
        tmp_path, {"ui/theme.tres": THEME.encode(), "levels/theme.tres": THEME.encode()}
    )
    (root / "project.godot").write_text("[display]\nwindow/size/viewport_height=1080\n")
    problems = run_checks(root, ["ui/theme.tres", "levels/theme.tres"])
    assert len(problems) == 2 and all(p.startswith("ui/theme.tres") for p in problems)
    rules = parse_rules(V2_BLOCK)
    assert ui_resources(
        rules, ["ui/theme.tres", "levels/a.tscn", "game/ui/hud.tscn", "ui/x.gd"]
    ) == [
        "game/ui/hud.tscn",
        "ui/theme.tres",
    ]
    assert ui_resources(BibleRules(), ["ui/theme.tres"]) == []


# --- real tools --------------------------------------------------------------

GODOT = Path(
    os.environ.get(
        "JOINERY_TEST_GODOT",
        "/Users/ninjacornix/joinery/.tools/Godot.app/Contents/MacOS/Godot",
    )
)

GODOT_EXPORT = """extends SceneTree


func _init() -> void:
	var root := Node3D.new()
	root.name = "Crate"
	var body := MeshInstance3D.new()
	body.name = "Crate_Wood"
	var box := BoxMesh.new()
	box.size = Vector3(1.0, 1.0, 1.0)
	body.mesh = box
	body.position = Vector3(0.0, 0.5, 0.0)
	body.scale = Vector3(2.0, 1.0, 1.0)
	root.add_child(body)
	body.owner = root
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	var error := document.append_from_scene(root, state)
	if error == OK:
		error = document.write_to_filesystem(state, "res://crate.glb")
	var theme := Theme.new()
	var box_style := StyleBoxFlat.new()
	box_style.bg_color = Color(0.2, 0.2, 0.2)
	theme.set_stylebox("normal", "Button", box_style)
	theme.set_color("font_color", "Button", Color(0.3, 0.3, 0.3))
	theme.set_font_size("font_size", "Label", 10)
	if error == OK:
		error = ResourceSaver.save(theme, "res://ui/theme.tres")
	root.free()
	print("exported: %d" % error)
	quit(error)
"""


@pytest.mark.skipif(not GODOT.is_file(), reason="stock Godot is not available")
def test_files_written_by_stock_godot_are_measured(tmp_path: Path) -> None:
    project = tmp_path / "godot"
    (project / "ui").mkdir(parents=True)
    (project / "project.godot").write_text(
        'config_version=5\n\n[application]\n\nconfig/name="Fixture"\n'
    )
    (project / "export.gd").write_text(GODOT_EXPORT)
    done = subprocess.run(
        [
            str(GODOT),
            "--headless",
            "--path",
            str(project),
            "--script",
            "res://export.gd",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    measured_glb = measure_glb((project / "crate.glb").read_bytes())
    assert measured_glb.triangles == 12
    crate = next(n for n in measured_glb.nodes if n.name == "Crate_Wood")
    assert crate.scale == pytest.approx((2.0, 1.0, 1.0))
    assert measured_glb.bbox_min == pytest.approx((-1.0, 0.0, -0.5), abs=1e-4)
    assert measured_glb.bbox_max == pytest.approx((1.0, 1.0, 0.5), abs=1e-4)
    rules = parse_rules(V2_BLOCK).accessibility
    text = (project / "ui/theme.tres").read_text()
    problems = ui_resource_problems("ui/theme.tres", text, rules, 1080)
    assert any("contrast 1.5:1" in p for p in problems), text
    assert any("font size 10 (Label)" in p for p in problems), text


WORKER = os.environ.get("JOINERY_WORKER_TESTS") == "1" and shutil.which("docker")

MAKE_BLEND = r"""
import bpy, sys
bpy.ops.wm.read_factory_settings(use_empty=True)
mesh = bpy.data.meshes.new("Crate")
mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
mesh.uv_layers.new(name="UVMap")
crate = bpy.data.objects.new("Crate_Wood", mesh)
crate.scale = (2.0, 2.0, 2.0)
bpy.context.scene.collection.objects.link(crate)
material = bpy.data.materials.new("Gold")
try:
    material.use_nodes = True
except AttributeError:
    pass
bsdf = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
bsdf.inputs['Metallic'].default_value = 1.0
bsdf.inputs['Roughness'].default_value = 0.2
image = bpy.data.images.new("crate_albedo", 128, 128)
texture = material.node_tree.nodes.new('ShaderNodeTexImage')
texture.image = image
material.node_tree.links.new(texture.outputs['Color'], bsdf.inputs['Base Color'])
mesh.materials.append(material)
armature = bpy.data.armatures.new("Rig")
rig = bpy.data.objects.new("Rig", armature)
bpy.context.scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode='EDIT')
for i in range(3):
    bone = armature.edit_bones.new(f"bone_{i}")
    bone.head = (0, 0, i)
    bone.tail = (0, 0, i + 1)
    bone.use_deform = i < 2
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1])
"""


@pytest.mark.skipif(
    not WORKER, reason="set JOINERY_WORKER_TESTS=1 to run the creative worker"
)
def test_blender_measures_a_real_blend_in_the_worker(tmp_path: Path) -> None:
    from joinery.authoring import run_offline

    (tmp_path / "art/props").mkdir(parents=True)
    made = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "-v",
            f"{tmp_path}:/work",
            "-w",
            "/work",
            "joinery-creative-worker",
            "blender",
            "-b",
            "--python-exit-code",
            "1",
            "--python-expr",
            MAKE_BLEND,
            "--",
            "/work/art/props/crate.blend",
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert made.returncode == 0, made.stdout[-2000:] + made.stderr[-2000:]
    (tmp_path / "art/bible.md").write_text(
        "## 18. Checked rules\n\n```toml\nschema = 2\n"
        "[models]\napplied_scale = true\n"
        '[models.max_bones]\n"art" = 1\n'
        '[models.texel_density]\n"art" = { px_per_m = 256, tolerance = 0.2 }\n'
        '[models.max_triangles]\n"art" = 1\n'
        '[materials]\nroughness = [0.4, 0.95]\nmetallic_requires_name = "(?i)metal"\n```\n'
    )

    async def measure(argv: list[str], root: Path) -> Result:
        return await run_offline(argv, root, 600, readonly=True)

    problems = asyncio.run(check_rules(tmp_path, ["art/props/crate.blend"], measure))
    assert problems == [
        "art/props/crate.blend: 2 triangles, over the art bible's 1 budget",
        "art/props/crate.blend: armature 'Rig' has 2 deform bones, over the art bible's 1 budget",
        "art/props/crate.blend: scale is not applied on 'Crate_Wood'; the art bible asks for scale 1",
        # A 2 m quad scaled by 2 is 16 m² of 128 x 128 px: 32 px/m.
        "art/props/crate.blend: 'Crate_Wood' texel density 32 px/m, the art bible asks for 256 ± 20%",
        "art/props/crate.blend: material 'Gold' roughness 0.20, the art bible allows 0.4-0.95",
        (
            "art/props/crate.blend: material 'Gold' is metallic (1.00) but its name does not "
            "say metal; the art bible keeps metallic for real metal"
        ),
    ]


@pytest.mark.skipif(
    not WORKER, reason="set JOINERY_WORKER_TESTS=1 to run the creative worker"
)
def test_worker_scripts_measure_real_audio_and_images(tmp_path: Path) -> None:
    from joinery.authoring import run_offline

    (tmp_path / "audio/music").mkdir(parents=True)
    (tmp_path / "audio/music/theme.wav").write_bytes(wav(44100, 1, 2))
    made = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "-v",
            f"{tmp_path}:/work",
            "-w",
            "/work",
            "joinery-creative-worker",
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "audio/music/theme.wav",
            "-c:a",
            "libvorbis",
            "audio/music/theme.ogg",
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert made.returncode == 0, made.stderr[-2000:]
    (tmp_path / "audio/music/theme.wav").unlink()
    five = [(i * 40, 10, 10, 255) for i in range(5)] + [(0, 0, 0, 0)]
    files = {
        "art/sprites/hero.png": png(8, 8, five),
        "art/textures/wall_albedo.png": png(
            8, 8, [(0, 0, 0, 255), (120, 110, 100, 255)]
        ),
    }
    root = v2_stage(tmp_path, files)

    async def measure(argv: list[str], root: Path) -> Result:
        return await run_offline(argv, root, 600, readonly=True)

    problems = asyncio.run(
        check_rules(root, [*sorted(files), "audio/music/theme.ogg"], measure)
    )
    assert problems == [
        (
            "art/textures/wall_albedo.png: 50% of the albedo is near pure black or white "
            "(luminance outside 0.03-0.92; allowed 25%)"
        ),
        "art/sprites/hero.png: 5 distinct colours, over the art bible's 4 per sprite",
        "audio/music/theme.ogg: 44100 Hz, the art bible allows 48000 Hz",
        "audio/music/theme.ogg: 1 channel(s), the art bible allows 2",
    ]
