"""Machine-checked art bible rules, run on changed assets before review.

The approved bible carries a fenced ```toml block under "Checked rules". The
user approves these numbers together with the prose, so a check can never use
a rule the user has not seen. Rules cover what can be measured without taste:

- audio: loudness, true peak, and file format per folder;
- models: triangle and bone budgets, applied scale, pivots, texel density, in
  exported `.glb` files and in the `.blend` sources themselves;
- materials: roughness and metallic ranges, metallic only on metal, albedo
  never pure black or white (factors and albedo textures);
- names of files, objects, meshes and materials;
- textures: size limits and power-of-two sides; distinct colours per sprite;
- how far image colours stray from the palette (CIEDE2000);
- accessibility of UI resources: text contrast and minimum text size.

`.glb`, `.wav`, image headers and Godot text resources are read here in Python.
Everything that needs a real decoder (loudness, pixels, Ogg streams, `.blend`)
runs in the offline creative worker through the `Measure` callable. A file that
cannot be measured is reported, never passed. Rules are optional one by one: an
absent table or an empty map means "not checked". Unknown keys are errors, so a
typo is reported rather than silently ignored.
"""

from __future__ import annotations

import json
import math
import re
import struct
import tomllib
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from joinery.bible_blend import (
    MEASURE_BLEND,
    BlendMeasure,
    measure_argv,
    parse_measures,
)
from joinery.runner import Result

#: The Blender script that measures `.blend` files (kept in bible_blend).
_MEASURE_BLEND = MEASURE_BLEND
#: Newest checked-rules schema this Joinery understands.
SCHEMA = 2
_BLOCK = re.compile(r"^```toml[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_RULES_HEADING = re.compile(
    r"^#{1,6}[^\n]*checked rules[^\n]*$", re.MULTILINE | re.IGNORECASE
)
_AUDIO = {".wav", ".ogg"}
_MODELS = {".glb", ".blend"}
_IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
_UI_RESOURCES = {".tres", ".tscn"}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
#: Images are sampled on a grid, nearest pixel, so no blended colour appears.
SAMPLE = 64
#: Scale counts as applied within this distance of 1.
SCALE_TOLERANCE = 0.001
#: Metallic above this counts as metal.
METAL_THRESHOLD = 0.05
#: Never part of an asset check: prose and sidecars.
_NOT_CHECKED = {"art/bible.md"}
_SIDECARS = {".import", ".uid"}
#: Recipe bookkeeping: preimages under recipes/inputs/ are the document as it was
#: before an edit, kept so the recipe replays, never an asset of the game.
_RECIPES = "recipes/"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _hex_problems(values: Iterable[str]) -> list[str]:
    return [v for v in values if not _HEX.match(v)]


def _range(value: tuple[float, float] | None, name: str) -> None:
    if value is not None and value[0] > value[1]:
        raise ValueError(f"{name} is [low, high]; got {list(value)}")


# --- rule models -------------------------------------------------------------


class AudioFormatRule(_Strict):
    container: Literal["wav", "ogg"]
    sample_rates: list[int] = []
    channels: list[int] = []
    #: Bits per sample, WAV only.
    bit_depths: list[int] = []


class AudioRules(_Strict):
    true_peak_max_dbtp: float | None = None
    lufs_tolerance: float = Field(default=1.0, ge=0)
    #: Integrated loudness target per folder, such as "audio/music" = -16.
    lufs: dict[str, float] = {}
    #: File format per folder, most specific folder wins.
    format: dict[str, AudioFormatRule] = {}


class TexelRule(_Strict):
    px_per_m: float = Field(gt=0)
    #: Allowed relative distance from px_per_m, 0.2 = ±20 %.
    tolerance: float = Field(default=0.2, ge=0)


class ModelRules(_Strict):
    #: Triangle budget per folder, such as "art/props" = 5000.
    max_triangles: dict[str, int] = {}
    #: Deform bones per armature (.blend) or joints per skin (.glb).
    max_bones: dict[str, int] = {}
    #: Every mesh (and armature) has scale 1 on all axes.
    applied_scale: bool = False
    pivot_tolerance_m: float = Field(default=0.02, ge=0)
    #: Where the origin sits relative to the model's bounding box.
    pivot: dict[str, Literal["bottom-centre", "centre", "any"]] = {}
    texel_density: dict[str, TexelRule] = {}


class MaterialRules(_Strict):
    folders: list[str] = ["art"]
    roughness: tuple[float, float] | None = None
    metallic: tuple[float, float] | None = None
    #: Metallic above 0.05 only on materials whose name matches this regex.
    metallic_requires_name: str | None = None
    #: Relative luminance bounds; outside counts as pure black or white.
    albedo_luminance: tuple[float, float] | None = None
    #: Share of albedo texture samples allowed outside albedo_luminance.
    albedo_max_outside: float = Field(default=0.01, ge=0, le=1)
    albedo_suffixes: list[str] = [
        "_albedo",
        "_basecolor",
        "_basecolour",
        "_diffuse",
        "_color",
        "_colour",
    ]

    def model_post_init(self, _context: object) -> None:
        _range(self.roughness, "materials.roughness")
        _range(self.metallic, "materials.metallic")
        _range(self.albedo_luminance, "materials.albedo_luminance")
        if self.metallic_requires_name is not None:
            # re.error is not a ValueError: unconverted, it would escape
            # parse_rules and crash the lint and the gateway.
            try:
                re.compile(self.metallic_requires_name)
            except re.error as error:
                raise ValueError(
                    f"materials.metallic_requires_name: {error}"
                ) from error


class NamingRules(_Strict):
    #: Regex on the file stem, per folder.
    files: dict[str, str] = {}
    #: Regex on object, mesh and material names inside models, per folder.
    nodes: dict[str, str] = {}

    def model_post_init(self, _context: object) -> None:
        # Both tables, not one merged dict: they usually share a folder ("art"),
        # and a merge would hide the file pattern behind the node pattern.
        for table, patterns in (("files", self.files), ("nodes", self.nodes)):
            for folder, pattern in patterns.items():
                try:
                    re.compile(pattern)
                except re.error as error:
                    raise ValueError(
                        f"naming pattern for {folder!r} ({table}): {error}"
                    ) from error


class TextureRules(_Strict):
    folders: list[str] = ["art"]
    power_of_two: bool = False
    #: Longest side in pixels, per folder.
    max_size: dict[str, int] = {}


class SpriteRules(_Strict):
    #: Distinct opaque colours per image, per folder.
    max_colours: dict[str, int] = {}


class PaletteRules(_Strict):
    #: Palette colours as "#rrggbb". Empty means colours are not checked.
    colours: list[str] = []
    max_delta_e: float = Field(default=12.0, gt=0)
    #: Share of opaque pixels allowed beyond max_delta_e (accents, highlights).
    max_off_palette: float = Field(default=0.05, ge=0, le=1)
    #: Folders whose images are checked.
    folders: list[str] = ["art"]
    #: Named roles ("ui_text" = "#edf2f4"). They join `colours` for the check.
    roles: dict[str, str] = {}

    def model_post_init(self, _context: object) -> None:
        bad = _hex_problems([*self.colours, *self.roles.values()])
        if bad:
            raise ValueError("palette colours are #rrggbb: " + ", ".join(bad))

    def all_colours(self) -> list[str]:
        return list(dict.fromkeys([*self.colours, *self.roles.values()]))


class AccessibilityRules(_Strict):
    #: WCAG 2.x contrast ratio for text on its background.
    min_ui_contrast: float | None = Field(default=None, ge=1, le=21)
    #: Smallest text at 1080p, scaled by the project's viewport height.
    min_text_px: int | None = Field(default=None, gt=0)
    #: Text/background pairs, as palette role names or "#rrggbb".
    ui_pairs: list[tuple[str, str]] = []
    #: Palette roles that must stay apart for protan, deutan and tritan vision.
    colour_blind_distinct: list[str] = []
    colour_blind_min_delta_e: float = Field(default=10.0, gt=0)
    #: Where UI resources (.tres, .tscn) are checked.
    ui_folders: list[str] = ["ui", "core/ui", "game/ui", "features"]


#: Godot's Environment.tonemap_mode values, by name.
TONEMAPS = {"linear": 0, "reinhard": 1, "filmic": 2, "aces": 3, "agx": 4}


class EnvironmentRules(_Strict):
    """The look of every Environment (WorldEnvironment) in the project."""

    #: Where Environment resources (.tres) and scenes holding one (.tscn) are checked.
    folders: list[str] = [""]
    tonemap: Literal["linear", "reinhard", "filmic", "aces", "agx"] | None = None
    exposure: tuple[float, float] | None = None
    #: Fog on (true) or off (false); absent = not checked.
    fog: bool | None = None
    glow: bool | None = None
    #: Colour grading (adjustment_*) ranges; 1 is unchanged.
    brightness: tuple[float, float] | None = None
    contrast: tuple[float, float] | None = None
    saturation: tuple[float, float] | None = None

    def model_post_init(self, _context: object) -> None:
        for name in ("exposure", "brightness", "contrast", "saturation"):
            _range(getattr(self, name), f"environment.{name}")


class UiRules(_Strict):
    """UI themes against the bible: their fonts and their colours."""

    #: Font files UI resources may use, such as "ui/fonts/inter.ttf". Empty: not checked.
    fonts: list[str] = []
    #: Theme and StyleBox colours stay within palette.max_delta_e of the palette.
    palette_colours: bool = False


class BibleRules(_Strict):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: int = Field(default=1, alias="schema", ge=1)
    #: Folders no check looks at. Reference boards live in art/reference.
    exclude: list[str] = ["art/reference"]
    audio: AudioRules = AudioRules()
    models: ModelRules = ModelRules()
    materials: MaterialRules = MaterialRules()
    naming: NamingRules = NamingRules()
    textures: TextureRules = TextureRules()
    sprites: SpriteRules = SpriteRules()
    palette: PaletteRules = PaletteRules()
    accessibility: AccessibilityRules = AccessibilityRules()
    environment: EnvironmentRules = EnvironmentRules()
    ui: UiRules = UiRules()

    def model_post_init(self, _context: object) -> None:
        if self.schema_version > SCHEMA:
            raise ValueError(
                f"schema {self.schema_version} was made by a newer Joinery "
                f"(this one reads up to {SCHEMA}); update Joinery to check it"
            )


def parse_rules(toml_text: str) -> BibleRules:
    """The checked rules in a TOML text. Raises ValueError, naming the problem."""
    try:
        return BibleRules.model_validate(tomllib.loads(toml_text))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"not valid TOML: {error}") from error
    except ValidationError as error:
        details = "; ".join(
            ".".join(str(p) for p in e["loc"]) + ": " + e["msg"]
            if e["loc"]
            else e["msg"]
            for e in error.errors()
        )
        raise ValueError(details) from error


def rules_text(bible: str) -> str | None:
    """The first ```toml block under the "Checked rules" heading.

    A first-format bible without that heading falls back to the first block
    anywhere, as before.
    """
    heading = _RULES_HEADING.search(bible)
    if heading is not None:
        match = _BLOCK.search(bible, heading.end())
        if match is not None:
            return match.group(1)
    match = _BLOCK.search(bible)
    return None if match is None else match.group(1)


def load_rules(project: Path) -> BibleRules:
    """Rules from the bible's checked-rules block, or none if it has no block."""
    path = project / "art/bible.md"
    if not path.is_file():
        return BibleRules()
    text = rules_text(path.read_text())
    if text is None:
        return BibleRules()
    try:
        return parse_rules(text)
    except ValueError as error:
        raise ValueError(
            f"The art bible's checked rules are invalid: {error}"
        ) from error


# --- folders -----------------------------------------------------------------


def _folder_value[T](rules: dict[str, T], path: str) -> T | None:
    """The rule for the most specific folder that contains the path."""
    best: tuple[int, T] | None = None
    target = PurePosixPath(path)
    for folder, value in rules.items():
        prefix = PurePosixPath(folder.strip("/"))
        if target.is_relative_to(prefix) and (
            best is None or len(prefix.parts) > best[0]
        ):
            best = (len(prefix.parts), value)
    return None if best is None else best[1]


def _in_folders(folders: Sequence[str], path: str) -> bool:
    return _folder_value(dict.fromkeys(folders, True), path) is not None


def _suffix(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def _few(names: Sequence[str], limit: int = 3) -> str:
    shown = ", ".join(repr(n) for n in names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


# --- colour maths ------------------------------------------------------------


def _channel_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def linear_rgb(colour: str) -> tuple[float, float, float]:
    r, g, b = hex_rgb(colour)
    return _channel_linear(r / 255), _channel_linear(g / 255), _channel_linear(b / 255)


def luminance_linear(rgb: Sequence[float]) -> float:
    """Relative luminance (WCAG, Rec. 709) of linear RGB in 0-1."""
    r, g, b = (min(1.0, max(0.0, c)) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def relative_luminance(colour: str) -> float:
    return luminance_linear(linear_rgb(colour))


def contrast_ratio(first: str, second: str) -> float:
    """WCAG 2.x contrast ratio between two "#rrggbb" colours, 1 to 21."""
    a, b = sorted((relative_luminance(first), relative_luminance(second)))
    return (b + 0.05) / (a + 0.05)


def _lab_linear(rgb: Sequence[float]) -> tuple[float, float, float]:
    """Linear RGB (0-1) to CIE L*a*b*, D65."""
    r, g, b = (min(1.0, max(0.0, c)) for c in rgb[:3])
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """sRGB (0-255) to CIE L*a*b*, D65."""
    return _lab_linear([_channel_linear(c / 255) for c in rgb])


def delta_e_2000(
    lab1: tuple[float, float, float], lab2: tuple[float, float, float]
) -> float:
    """CIEDE2000 colour difference (Sharma, Wu and Dalal 2005)."""
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c_bar = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(c_bar**7 / (c_bar**7 + 25**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if c2p else 0.0
    dl, dc = l2 - l1, c2p - c1p
    if c1p * c2p == 0:
        dh = 0.0
    elif abs(h2p - h1p) <= 180:
        dh = h2p - h1p
    else:
        dh = h2p - h1p - 360 if h2p > h1p else h2p - h1p + 360
    dh_big = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh / 2))
    l_bar, c_bar_p = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        h_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        h_bar = (h1p + h2p) / 2
    else:
        h_bar = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    t = (
        1
        - 0.17 * math.cos(math.radians(h_bar - 30))
        + 0.24 * math.cos(math.radians(2 * h_bar))
        + 0.32 * math.cos(math.radians(3 * h_bar + 6))
        - 0.20 * math.cos(math.radians(4 * h_bar - 63))
    )
    s_l = 1 + 0.015 * (l_bar - 50) ** 2 / math.sqrt(20 + (l_bar - 50) ** 2)
    s_c = 1 + 0.045 * c_bar_p
    s_h = 1 + 0.015 * c_bar_p * t
    r_t = (
        -2
        * math.sqrt(c_bar_p**7 / (c_bar_p**7 + 25**7))
        * math.sin(math.radians(60 * math.exp(-(((h_bar - 275) / 25) ** 2))))
    )
    return math.sqrt(
        (dl / s_l) ** 2
        + (dc / s_c) ** 2
        + (dh_big / s_h) ** 2
        + r_t * (dc / s_c) * (dh_big / s_h)
    )


#: Machado, Oliveira and Fernandes (2009), severity 1.0, applied to linear RGB.
CVD_MATRICES: dict[str, tuple[tuple[float, float, float], ...]] = {
    "protanopia": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deuteranopia": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritanopia": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}


def simulate_cvd(colour: str, kind: str) -> tuple[float, float, float]:
    """Linear RGB a person with full protan, deutan or tritan vision sees."""
    matrix = CVD_MATRICES[kind]
    rgb = linear_rgb(colour)
    r, g, b = (
        min(1.0, max(0.0, sum(row[i] * rgb[i] for i in range(3)))) for row in matrix
    )
    return r, g, b


def cvd_delta_e(first: str, second: str, kind: str) -> float:
    """CIEDE2000 between two colours as seen with the given colour vision."""
    return delta_e_2000(
        _lab_linear(simulate_cvd(first, kind)), _lab_linear(simulate_cvd(second, kind))
    )


def _resolve_colour(name: str, roles: dict[str, str]) -> str | None:
    if _HEX.match(name):
        return name
    return roles.get(name)


def palette_problems(rules: BibleRules) -> list[str]:
    """Problems in the bible's own palette: contrast and colour-blind clashes.

    These are about the bible itself, so they are lint for the user to fix, not
    reasons to refuse an asset.
    """
    access = rules.accessibility
    roles = rules.palette.roles
    problems: list[str] = []
    for text, background in access.ui_pairs:
        fg, bg = _resolve_colour(text, roles), _resolve_colour(background, roles)
        missing = [n for n, c in ((text, fg), (background, bg)) if c is None]
        if missing:
            problems.append(
                "accessibility.ui_pairs names "
                + ", ".join(repr(m) for m in missing)
                + ", which [palette.roles] does not define"
            )
            continue
        assert fg is not None and bg is not None
        if access.min_ui_contrast is not None:
            ratio = contrast_ratio(fg, bg)
            if ratio < access.min_ui_contrast:
                problems.append(
                    f"{text} on {background} has contrast {ratio:.1f}:1, "
                    f"below the {access.min_ui_contrast:g}:1 the bible asks for"
                )
    distinct = list(dict.fromkeys(access.colour_blind_distinct))
    unknown = [r for r in distinct if _resolve_colour(r, roles) is None]
    if unknown:
        problems.append(
            "accessibility.colour_blind_distinct names "
            + ", ".join(repr(u) for u in unknown)
            + ", which [palette.roles] does not define"
        )
    known = [r for r in distinct if r not in unknown]
    for i, first in enumerate(known):
        for second in known[i + 1 :]:
            a = _resolve_colour(first, roles)
            b = _resolve_colour(second, roles)
            assert a is not None and b is not None
            for kind in CVD_MATRICES:
                delta = cvd_delta_e(a, b, kind)
                if delta < access.colour_blind_min_delta_e:
                    problems.append(
                        f"{first} and {second} merge under {kind}: ΔE {delta:.1f}, "
                        f"need {access.colour_blind_min_delta_e:g}"
                    )
    return problems


# --- binary glTF -------------------------------------------------------------

_COMPONENTS: dict[int, tuple[str, int, float]] = {
    5120: ("b", 1, 127.0),
    5121: ("B", 1, 255.0),
    5122: ("h", 2, 32767.0),
    5123: ("H", 2, 65535.0),
    5125: ("I", 4, 4294967295.0),
    5126: ("f", 4, 1.0),
}
_WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
Matrix = list[list[float]]
_IDENTITY: Matrix = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def _glb_chunks(data: bytes) -> tuple[dict[str, Any], bytes]:
    if len(data) < 20:
        raise ValueError("Not a binary glTF file.")
    magic, _version, _length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("Not a binary glTF file.")
    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != 0x4E4F534A:  # "JSON"
        raise ValueError("The glTF file has no JSON chunk.")
    document: dict[str, Any] = json.loads(data[20 : 20 + chunk_length])
    offset = 20 + chunk_length
    binary = b""
    if offset + 8 <= len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        if kind == 0x004E4942:  # "BIN\0"
            binary = data[offset + 8 : offset + 8 + length]
    return document, binary


def _accessor(document: dict[str, Any], binary: bytes, index: int) -> list[list[float]]:
    """An accessor's elements as float lists, normalised integers scaled to 0-1."""
    accessor = document["accessors"][index]
    if "bufferView" not in accessor or "sparse" in accessor:
        raise ValueError("sparse or empty accessors are not measured")
    view = document["bufferViews"][accessor["bufferView"]]
    if view.get("buffer", 0) != 0:
        raise ValueError("external buffers are not measured")
    code, size, scale = _COMPONENTS[int(accessor["componentType"])]
    width = _WIDTHS[accessor["type"]]
    stride = int(view.get("byteStride", 0)) or size * width
    start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    normalised = bool(accessor.get("normalized")) and code != "f"
    out: list[list[float]] = []
    fmt = "<" + code * width
    for i in range(int(accessor["count"])):
        values = struct.unpack_from(fmt, binary, start + i * stride)
        out.append([v / scale if normalised else float(v) for v in values])
    return out


def _matmul(a: Matrix, b: Matrix) -> Matrix:
    return [
        [sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)
    ]


def _node_matrix(node: dict[str, Any]) -> Matrix:
    if "matrix" in node:
        m = [float(v) for v in node["matrix"]]  # column-major
        return [[m[col * 4 + row] for col in range(4)] for row in range(4)]
    tx, ty, tz = (float(v) for v in node.get("translation", (0, 0, 0)))
    qx, qy, qz, qw = (float(v) for v in node.get("rotation", (0, 0, 0, 1)))
    sx, sy, sz = (float(v) for v in node.get("scale", (1, 1, 1)))
    rotation = [
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
    ]
    scale = (sx, sy, sz)
    out = [
        [rotation[r][c] * scale[c] for c in range(3)] + [t]
        for r, t in enumerate((tx, ty, tz))
    ]
    return [*out, [0.0, 0.0, 0.0, 1.0]]


def _apply(matrix: Matrix, point: Sequence[float]) -> tuple[float, float, float]:
    x, y, z = point[0], point[1], point[2]
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
    )


def _matrix_scale(matrix: Matrix) -> tuple[float, float, float]:
    sx, sy, sz = (
        math.sqrt(sum(matrix[row][col] ** 2 for row in range(3))) for col in range(3)
    )
    return sx, sy, sz


class GlbNode(_Strict):
    name: str
    mesh: str | None = None
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    #: Nodes that are joints of a skin: bones, never name-checked.
    joint: bool = False


class GlbMaterial(_Strict):
    name: str
    #: None when a texture drives the value (checked through the texture).
    roughness: float | None = None
    metallic: float | None = None
    #: Relative luminance of the base colour factor (linear RGB).
    base_luminance: float | None = None


class GlbMeasure(_Strict):
    triangles: int
    #: Largest joint count over the file's skins.
    bones: int = 0
    nodes: list[GlbNode] = []
    meshes: list[str] = []
    materials: list[GlbMaterial] = []
    #: Bounding box of every mesh in scene space (+Y up, metres).
    bbox_min: tuple[float, float, float] | None = None
    bbox_max: tuple[float, float, float] | None = None
    #: Base colour texture pixels per metre, per mesh node with a texture.
    texel_density: dict[str, float] = {}


def glb_triangles(data: bytes) -> int:
    """Triangles drawn by a binary glTF, counting each mesh once per node using it."""
    document, _binary = _glb_chunks(data)
    return _triangles(document)


def _triangles(document: dict[str, Any]) -> int:
    accessors = document.get("accessors", [])
    per_mesh = []
    for mesh in document.get("meshes", []):
        total = 0
        for primitive in mesh.get("primitives", []):
            mode = primitive.get("mode", 4)
            source = primitive.get(
                "indices", primitive.get("attributes", {}).get("POSITION")
            )
            if source is None:
                continue
            count = int(accessors[source]["count"])
            if mode == 4:
                total += count // 3
            elif mode in (5, 6):
                total += max(0, count - 2)
        per_mesh.append(total)
    uses = [node["mesh"] for node in document.get("nodes", []) if "mesh" in node]
    return sum(per_mesh[index] for index in uses) if uses else sum(per_mesh)


def _world_matrices(document: dict[str, Any]) -> dict[int, Matrix]:
    nodes = document.get("nodes", [])
    children = {c for node in nodes for c in node.get("children", [])}
    world: dict[int, Matrix] = {}
    stack: list[tuple[int, Matrix]] = [
        (i, _IDENTITY) for i in range(len(nodes)) if i not in children
    ]
    while stack:
        index, parent = stack.pop()
        if index in world:
            continue
        world[index] = _matmul(parent, _node_matrix(nodes[index]))
        stack.extend((c, world[index]) for c in nodes[index].get("children", []))
    return world


def _embedded_image_size(
    document: dict[str, Any], binary: bytes, texture: int
) -> tuple[int, int] | None:
    try:
        source = document["textures"][texture]["source"]
        image = document["images"][source]
        view = document["bufferViews"][image["bufferView"]]
    except (KeyError, IndexError, TypeError):
        return None
    start = int(view.get("byteOffset", 0))
    data = binary[start : start + int(view["byteLength"])]
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(
        str(image.get("mimeType", "")), ""
    )
    return image_size(data, suffix)


def _texel_density(
    document: dict[str, Any], binary: bytes, mesh: dict[str, Any], matrix: Matrix
) -> float | None:
    materials = document.get("materials", [])
    uv_px = 0.0
    world = 0.0
    for primitive in mesh.get("primitives", []):
        if primitive.get("mode", 4) != 4 or "material" not in primitive:
            continue
        base = (
            materials[primitive["material"]]
            .get("pbrMetallicRoughness", {})
            .get("baseColorTexture")
        )
        if base is None:
            continue
        size = _embedded_image_size(document, binary, int(base["index"]))
        attributes = primitive.get("attributes", {})
        uv_key = f"TEXCOORD_{int(base.get('texCoord', 0))}"
        if size is None or uv_key not in attributes or "POSITION" not in attributes:
            continue
        positions = [
            _apply(matrix, p)
            for p in _accessor(document, binary, attributes["POSITION"])
        ]
        uvs = _accessor(document, binary, attributes[uv_key])
        if "indices" in primitive:
            order = [
                int(i[0]) for i in _accessor(document, binary, primitive["indices"])
            ]
        else:
            order = list(range(len(positions)))
        for t in range(0, len(order) - 2, 3):
            i, j, k = order[t], order[t + 1], order[t + 2]
            a, b, c = positions[i], positions[j], positions[k]
            ab = [b[n] - a[n] for n in range(3)]
            ac = [c[n] - a[n] for n in range(3)]
            cross = (
                ab[1] * ac[2] - ab[2] * ac[1],
                ab[2] * ac[0] - ab[0] * ac[2],
                ab[0] * ac[1] - ab[1] * ac[0],
            )
            world += math.sqrt(sum(v * v for v in cross)) / 2
            p, q, r = uvs[i], uvs[j], uvs[k]
            area = (
                abs((q[0] - p[0]) * (r[1] - p[1]) - (r[0] - p[0]) * (q[1] - p[1])) / 2
            )
            uv_px += area * size[0] * size[1]
    if world <= 0 or uv_px <= 0:
        return None
    return math.sqrt(uv_px / world)


def _mesh_bounds(
    document: dict[str, Any], mesh: dict[str, Any]
) -> tuple[list[float], list[float]] | None:
    low = [math.inf] * 3
    high = [-math.inf] * 3
    for primitive in mesh.get("primitives", []):
        index = primitive.get("attributes", {}).get("POSITION")
        if index is None:
            continue
        accessor = document["accessors"][index]
        if "min" not in accessor or "max" not in accessor:
            return None
        low = [min(a, float(b)) for a, b in zip(low, accessor["min"], strict=True)]
        high = [max(a, float(b)) for a, b in zip(high, accessor["max"], strict=True)]
    if math.isinf(low[0]):
        return None
    return low, high


def measure_glb(data: bytes) -> GlbMeasure:
    """What the art bible checks in a binary glTF, read without a decoder."""
    document, binary = _glb_chunks(data)
    nodes = document.get("nodes", [])
    meshes = document.get("meshes", [])
    joints = {j for skin in document.get("skins", []) for j in skin.get("joints", [])}
    world = _world_matrices(document)
    out_nodes: list[GlbNode] = []
    low = [math.inf] * 3
    high = [-math.inf] * 3
    density: dict[str, float] = {}
    for index, node in enumerate(nodes):
        matrix = world.get(index, _node_matrix(node))
        mesh_name = None
        name = str(node.get("name", f"node {index}"))
        if "mesh" in node:
            mesh = meshes[node["mesh"]]
            mesh_name = str(mesh.get("name", f"mesh {node['mesh']}"))
            bounds = _mesh_bounds(document, mesh)
            if bounds is not None:
                for corner in range(8):
                    point = [bounds[(corner >> axis) & 1][axis] for axis in range(3)]
                    moved = _apply(matrix, point)
                    low = [min(a, b) for a, b in zip(low, moved, strict=True)]
                    high = [max(a, b) for a, b in zip(high, moved, strict=True)]
            try:
                value = _texel_density(document, binary, mesh, matrix)
            except (KeyError, IndexError, ValueError, struct.error):
                value = None
            if value is not None:
                density[name] = value
        out_nodes.append(
            GlbNode(
                name=name,
                mesh=mesh_name,
                scale=_matrix_scale(_node_matrix(node)),
                joint=index in joints,
            )
        )
    materials: list[GlbMaterial] = []
    for index, material in enumerate(document.get("materials", [])):
        pbr = material.get("pbrMetallicRoughness", {})
        textured = "metallicRoughnessTexture" in pbr
        factor = pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
        materials.append(
            GlbMaterial(
                name=str(material.get("name", f"material {index}")),
                roughness=None if textured else float(pbr.get("roughnessFactor", 1.0)),
                metallic=None if textured else float(pbr.get("metallicFactor", 1.0)),
                base_luminance=None
                if "baseColorTexture" in pbr
                else luminance_linear([float(c) for c in factor]),
            )
        )
    has_box = not math.isinf(low[0])
    return GlbMeasure(
        triangles=_triangles(document),
        bones=max(
            (len(s.get("joints", [])) for s in document.get("skins", [])), default=0
        ),
        nodes=out_nodes,
        meshes=[str(m.get("name", f"mesh {i}")) for i, m in enumerate(meshes)],
        materials=materials,
        bbox_min=(low[0], low[1], low[2]) if has_box else None,
        bbox_max=(high[0], high[1], high[2]) if has_box else None,
        texel_density=density,
    )


# --- audio and image headers -------------------------------------------------


class AudioFormat(_Strict):
    container: Literal["wav", "ogg"]
    sample_rate: int
    channels: int
    #: Bits per sample; None for compressed streams.
    bits: int | None = None
    codec: str = ""


def wav_format(data: bytes) -> AudioFormat:
    """Sample rate, channels and bit depth from a RIFF WAVE `fmt ` chunk."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("Not a RIFF WAVE file.")
    offset = 12
    while offset + 8 <= len(data):
        chunk, size = struct.unpack_from("<4sI", data, offset)
        if chunk == b"fmt " and size >= 16:
            _tag, channels, rate, _bytes, _align, bits = struct.unpack_from(
                "<HHIIHH", data, offset + 8
            )
            return AudioFormat(
                container="wav",
                sample_rate=rate,
                channels=channels,
                bits=bits,
                codec="pcm",
            )
        offset += 8 + size + (size & 1)
    raise ValueError("The WAVE file has no fmt chunk.")


_JPEG_SOF = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


def image_size(data: bytes, suffix: str) -> tuple[int, int] | None:
    """Width and height from a PNG, JPEG or WebP header; None if unreadable.

    The format is read from the bytes; `suffix` is only a hint for callers and
    is not trusted, so a misnamed file is still measured correctly.
    """
    del suffix
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            width, height = struct.unpack_from(">II", data, 16)
            return width, height
        if data[:2] == b"\xff\xd8":
            offset = 2
            while offset + 4 <= len(data):
                if data[offset] != 0xFF:
                    return None
                marker = data[offset + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    offset += 2
                    continue
                (length,) = struct.unpack_from(">H", data, offset + 2)
                if marker in _JPEG_SOF:
                    height, width = struct.unpack_from(">HH", data, offset + 5)
                    return width, height
                offset += 2 + length
            return None
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            kind = data[12:16]
            if kind == b"VP8X":
                width = 1 + int.from_bytes(data[24:27], "little")
                height = 1 + int.from_bytes(data[27:30], "little")
                return width, height
            if kind == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if kind == b"VP8 ":
                width, height = struct.unpack_from("<HH", data, 26)
                return width & 0x3FFF, height & 0x3FFF
    except (struct.error, IndexError):
        return None
    return None


# --- Godot UI resources ------------------------------------------------------

_SECTION = re.compile(r"^\[(\w+)([^\]]*)\]\s*$")
_ATTR = re.compile(r'(\w+)="([^"]*)"')
_COLOR = re.compile(
    r"Color\(\s*([-\d.e]+)\s*,\s*([-\d.e]+)\s*,\s*([-\d.e]+)(?:\s*,\s*([-\d.e]+))?\s*\)"
)
_SUB = re.compile(r'SubResource\(\s*"([^"]+)"\s*\)')
#: Godot's text colour for each style state; disabled text is exempt (WCAG).
_STATES = {
    "normal": "font_color",
    "hover": "font_hover_color",
    "pressed": "font_pressed_color",
}


def _sections(text: str) -> list[tuple[str, dict[str, str], dict[str, str]]]:
    sections: list[tuple[str, dict[str, str], dict[str, str]]] = []
    for line in text.splitlines():
        header = _SECTION.match(line.strip())
        if header:
            sections.append((header.group(1), dict(_ATTR.findall(header.group(2))), {}))
            continue
        if sections and "=" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition("=")
            sections[-1][2][key.strip()] = value.strip()
    return sections


def _godot_colour(value: str) -> str | None:
    match = _COLOR.search(value)
    if match is None:
        return None
    alpha = float(match.group(4)) if match.group(4) else 1.0
    if alpha < 0.999:
        return None  # blended with whatever is behind it; not measurable here
    r, g, b = (min(255, max(0, round(float(match.group(i)) * 255))) for i in (1, 2, 3))
    return f"#{r:02x}{g:02x}{b:02x}"


def viewport_height(project: Path) -> int:
    """The project's base viewport height from project.godot (Godot's default 648)."""
    path = project / "project.godot"
    if path.is_file():
        section = ""
        for line in path.read_text(errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                section = stripped
            elif section == "[display]" and stripped.startswith(
                "window/size/viewport_height="
            ):
                try:
                    return int(stripped.partition("=")[2].strip())
                except ValueError:
                    break
    return 648


def ui_resource_problems(
    path: str, text: str, rules: AccessibilityRules, height: int
) -> list[str]:
    """Text contrast and size problems in one Godot `.tres` or `.tscn` text."""
    sections = _sections(text)
    boxes: dict[str, str] = {}
    for kind, attrs, values in sections:
        if kind == "sub_resource" and attrs.get("type") == "StyleBoxFlat":
            colour = _godot_colour(values.get("bg_color", "Color(0.6, 0.6, 0.6, 1)"))
            if colour is not None and values.get("draw_center", "true") != "false":
                boxes[attrs.get("id", "")] = colour
    problems: list[str] = []
    minimum = None
    if rules.min_text_px is not None:
        minimum = rules.min_text_px * height / 1080
    for kind, attrs, values in sections:
        owner = attrs.get("name") or attrs.get("id") or kind
        if kind == "resource" or attrs.get("type") == "Theme" or kind == "node":
            colours: dict[tuple[str, str], str] = {}
            styles: dict[tuple[str, str], str] = {}
            for key, value in values.items():
                parts = key.split("/")
                if len(parts) == 3 and parts[1] in {"colors", "styles"}:
                    group, item = parts[0], parts[2]
                elif len(parts) == 2 and parts[0] == "theme_override_colors":
                    group, item = owner, parts[1]
                    parts = [owner, "colors", item]
                elif len(parts) == 2 and parts[0] == "theme_override_styles":
                    group, item = owner, parts[1]
                    parts = [owner, "styles", item]
                else:
                    continue
                if parts[1] == "colors":
                    colour = _godot_colour(value)
                    if colour is not None:
                        colours[(group, item)] = colour
                else:
                    ref = _SUB.search(value)
                    if ref is not None and ref.group(1) in boxes:
                        styles[(group, item)] = boxes[ref.group(1)]
            if rules.min_ui_contrast is not None:
                for (group, state), background in sorted(styles.items()):
                    if state not in _STATES:
                        continue
                    text_colour = colours.get((group, _STATES[state])) or colours.get(
                        (group, "font_color")
                    )
                    if text_colour is None:
                        continue
                    ratio = contrast_ratio(text_colour, background)
                    if ratio < rules.min_ui_contrast:
                        problems.append(
                            f"{path}: {group} text on its {state} background has "
                            f"contrast {ratio:.1f}:1, below the art bible's "
                            f"{rules.min_ui_contrast:g}:1"
                        )
        if minimum is None:
            continue
        for key, value in values.items():
            sized = (
                key == "default_font_size"
                or "/font_sizes/" in key
                or key.startswith("theme_override_font_sizes/")
                or (key == "font_size" and attrs.get("type") == "LabelSettings")
            )
            if not sized:
                continue
            try:
                size = int(float(value))
            except ValueError:
                continue
            if size < minimum - 1e-9:
                where = owner if kind != "resource" else key.split("/")[0]
                problems.append(
                    f"{path}: font size {size} ({where}) is below the art bible's "
                    f"{rules.min_text_px} px at 1080p ({minimum:g} px at this "
                    f"project's {height} px viewport)"
                )
    return problems


# --- worker output parsing ---------------------------------------------------


def parse_loudness(output: str) -> dict[str, dict[str, float]]:
    """Integrated loudness and true peak per file from the measuring script."""
    results: dict[str, dict[str, float]] = {}
    current = ""
    section = ""
    for line in output.splitlines():
        if line.startswith("JOINERY_FILE:"):
            current = line.removeprefix("JOINERY_FILE:").strip()
            results[current] = {}
            continue
        stripped = line.strip()
        if stripped.startswith(("Integrated loudness:", "True peak:")):
            section = stripped
            continue
        number = re.match(r"(I|Peak):\s+(-?inf|-?[\d.]+)\s+(LUFS|dBFS)", stripped)
        if current and number:
            value = float(number.group(2))
            if number.group(1) == "I" and section.startswith("Integrated"):
                results[current]["lufs"] = value
            elif number.group(1) == "Peak" and section.startswith("True peak"):
                results[current]["true_peak"] = value
    return results


#: The loop measures every changed file in one offline worker run.
_MEASURE = (
    'for f in "$@"; do echo "JOINERY_FILE:$f"; '
    'ffmpeg -nostats -hide_banner -i "$f" -af ebur128=peak=true -f null - 2>&1 '
    "| sed -n '/Summary:/,$p'; done"
)

#: Prints each image as RGBA hex samples on a SAMPLE x SAMPLE nearest grid.
_SAMPLE_IMAGES = (
    'for f in "$@"; do echo "JOINERY_FILE:$f"; '
    f'ffmpeg -v error -i "$f" -vf scale={SAMPLE}:{SAMPLE}:flags=neighbor '
    "-frames:v 1 -f rawvideo -pix_fmt rgba - | od -An -v -tx1 | tr -d ' \\n'; "
    "echo; done"
)

#: Counts distinct opaque colours at full resolution, one line per image.
_COUNT_COLOURS = (
    'for f in "$@"; do echo "JOINERY_FILE:$f"; '
    'ffmpeg -v error -i "$f" -frames:v 1 -f rawvideo -pix_fmt rgba - | python3 -I -c '
    '"import sys; d = sys.stdin.buffer.read(); '
    "print('JOINERY_COLOURS:%d' % len({d[i:i + 3] for i in range(0, len(d) - 3, 4) "
    "if d[i + 3] >= 128}) if d else '')\"; done"
)

#: Ogg stream format, as JSON per file.
_PROBE_AUDIO = (
    'for f in "$@"; do echo "JOINERY_FILE:$f"; '
    "ffprobe -v error -show_entries stream=codec_name,sample_rate,channels "
    "-of json=compact=1 \"$f\" | tr -d '\\n'; echo; done"
)

Measure = Callable[[list[str], Path], Awaitable[Result]]


def parse_samples(output: str) -> dict[str, list[tuple[int, int, int]]]:
    """Opaque RGB samples per file from the sampling script."""
    samples: dict[str, list[tuple[int, int, int]]] = {}
    current = ""
    for line in output.splitlines():
        if line.startswith("JOINERY_FILE:"):
            current = line.removeprefix("JOINERY_FILE:").strip()
            continue
        line = line.strip()
        if current and line and re.fullmatch(r"[0-9a-f]+", line) and len(line) % 8 == 0:
            data = bytes.fromhex(line)
            samples[current] = [
                (data[i], data[i + 1], data[i + 2])
                for i in range(0, len(data), 4)
                if data[i + 3] >= 128
            ]
    return samples


def parse_colour_counts(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    current = ""
    for line in output.splitlines():
        if line.startswith("JOINERY_FILE:"):
            current = line.removeprefix("JOINERY_FILE:").strip()
        elif current and line.startswith("JOINERY_COLOURS:"):
            counts[current] = int(line.removeprefix("JOINERY_COLOURS:"))
    return counts


def parse_probe(output: str) -> dict[str, AudioFormat]:
    formats: dict[str, AudioFormat] = {}
    current = ""
    for line in output.splitlines():
        if line.startswith("JOINERY_FILE:"):
            current = line.removeprefix("JOINERY_FILE:").strip()
            continue
        if not current or not line.strip().startswith("{"):
            continue
        try:
            streams = json.loads(line).get("streams", [])
            stream = next(s for s in streams if s.get("sample_rate"))
            formats[current] = AudioFormat(
                container="ogg",
                sample_rate=int(stream["sample_rate"]),
                channels=int(stream["channels"]),
                codec=str(stream.get("codec_name", "")),
            )
        except (ValueError, StopIteration, KeyError, TypeError):
            continue
    return formats


# --- checks ------------------------------------------------------------------


def _material_problems(
    path: str,
    name: str,
    roughness: float | None,
    metallic: float | None,
    luminance: float | None,
    rules: MaterialRules,
) -> list[str]:
    problems: list[str] = []
    if roughness is not None and rules.roughness is not None:
        low, high = rules.roughness
        if not low - 1e-6 <= roughness <= high + 1e-6:
            problems.append(
                f"{path}: material {name!r} roughness {roughness:.2f}, the art "
                f"bible allows {low:g}-{high:g}"
            )
    if metallic is not None:
        if rules.metallic is not None:
            low, high = rules.metallic
            if not low - 1e-6 <= metallic <= high + 1e-6:
                problems.append(
                    f"{path}: material {name!r} metallic {metallic:.2f}, the art "
                    f"bible allows {low:g}-{high:g}"
                )
        pattern = rules.metallic_requires_name
        if (
            pattern is not None
            and metallic > METAL_THRESHOLD
            and not re.search(pattern, name)
        ):
            problems.append(
                f"{path}: material {name!r} is metallic ({metallic:.2f}) but its "
                "name does not say metal; the art bible keeps metallic for real metal"
            )
    if luminance is not None and rules.albedo_luminance is not None:
        low, high = rules.albedo_luminance
        if luminance < low or luminance > high:
            shade = "black" if luminance < low else "white"
            problems.append(
                f"{path}: material {name!r} base colour is near pure {shade} "
                f"(luminance {luminance:.3f}; the art bible allows {low:g}-{high:g})"
            )
    return problems


def _name_problems(
    path: str, names: dict[str, Iterable[str]], rules: NamingRules
) -> list[str]:
    """One line naming each kind of name (objects, meshes, materials) that is off."""
    pattern = _folder_value(rules.nodes, path)
    if pattern is None:
        return []
    compiled = re.compile(pattern)
    bad = []
    for kind, found in names.items():
        off = sorted({n for n in found if n and not compiled.fullmatch(n)})
        if off:
            bad.append(f"{kind} {_few(off)}")
    if not bad:
        return []
    return [
        (
            f"{path}: {'; '.join(bad)} do not follow the art bible's naming "
            f"pattern {pattern}"
        )
    ]


def _pivot_problem(
    path: str,
    what: str,
    low: Sequence[float],
    high: Sequence[float],
    mode: str,
    tolerance: float,
) -> str | None:
    centre = [(a + b) / 2 for a, b in zip(low, high, strict=True)]
    if mode == "bottom-centre":
        off = max(abs(low[1]), abs(centre[0]), abs(centre[2]))
        expected = "bottom-centre (lowest point at y 0, centred in x and z)"
        detail = f"lowest y {low[1]:.3f}, centre x {centre[0]:.3f} z {centre[2]:.3f}"
    elif mode == "centre":
        off = max(abs(c) for c in centre)
        expected = "at the centre of its bounds"
        detail = f"centre {centre[0]:.3f}, {centre[1]:.3f}, {centre[2]:.3f}"
    else:
        return None
    if off <= tolerance:
        return None
    return (
        f"{path}: {what} pivot is not {expected}: {detail} m "
        f"(tolerance {tolerance:g} m)"
    )


def _texel_problem(path: str, what: str, density: float, rule: TexelRule) -> str | None:
    if abs(density - rule.px_per_m) <= rule.tolerance * rule.px_per_m:
        return None
    return (
        f"{path}: {what} texel density {density:.0f} px/m, the art bible asks "
        f"for {rule.px_per_m:g} ± {rule.tolerance:.0%}"
    )


def _scale_off(scale: Sequence[float]) -> bool:
    return any(abs(s - 1) > SCALE_TOLERANCE for s in scale)


def _glb_problems(path: str, data: bytes, rules: BibleRules) -> list[str]:
    models = rules.models
    budget = _folder_value(models.max_triangles, path)
    bones = _folder_value(models.max_bones, path)
    pivot = _folder_value(models.pivot, path)
    texel = _folder_value(models.texel_density, path)
    names = _folder_value(rules.naming.nodes, path)
    materials = _in_folders(rules.materials.folders, path) and (
        rules.materials.roughness is not None
        or rules.materials.metallic is not None
        or rules.materials.metallic_requires_name is not None
        or rules.materials.albedo_luminance is not None
    )
    wanted = (
        budget is not None
        or bones is not None
        or models.applied_scale
        or (pivot is not None and pivot != "any")
        or texel is not None
        or names is not None
        or materials
    )
    if not wanted:
        return []
    try:
        measured = measure_glb(data)
    except (ValueError, KeyError, IndexError, TypeError, struct.error) as error:
        return [f"{path}: model could not be measured ({error})"]
    problems: list[str] = []
    if budget is not None and measured.triangles > budget:
        problems.append(
            f"{path}: {measured.triangles} triangles, over the art bible's {budget} budget"
        )
    if bones is not None and measured.bones > bones:
        problems.append(
            f"{path}: {measured.bones} bones in a skin, over the art bible's {bones} budget"
        )
    if models.applied_scale:
        scaled = [n.name for n in measured.nodes if n.mesh and _scale_off(n.scale)]
        if scaled:
            problems.append(
                f"{path}: scale is not applied on {_few(scaled)}; the art bible "
                "asks for scale 1"
            )
    if pivot is not None and measured.bbox_min and measured.bbox_max:
        problem = _pivot_problem(
            path,
            "model",
            measured.bbox_min,
            measured.bbox_max,
            pivot,
            models.pivot_tolerance_m,
        )
        if problem:
            problems.append(problem)
    if texel is not None:
        for node, density in sorted(measured.texel_density.items()):
            problem = _texel_problem(path, repr(node), density, texel)
            if problem:
                problems.append(problem)
    problems += _name_problems(
        path,
        {
            "node names": [n.name for n in measured.nodes if not n.joint],
            "mesh names": measured.meshes,
            "material names": [m.name for m in measured.materials],
        },
        rules.naming,
    )
    if materials:
        for material in measured.materials:
            problems += _material_problems(
                path,
                material.name,
                material.roughness,
                material.metallic,
                material.base_luminance,
                rules.materials,
            )
    return problems


def _blend_wanted(path: str, rules: BibleRules) -> bool:
    models = rules.models
    material = rules.materials
    return (
        _folder_value(models.max_triangles, path) is not None
        or _folder_value(models.max_bones, path) is not None
        or models.applied_scale
        or _folder_value(models.pivot, path) not in (None, "any")
        or _folder_value(models.texel_density, path) is not None
        or _folder_value(rules.naming.nodes, path) is not None
        or (
            _in_folders(material.folders, path)
            and (
                material.roughness is not None
                or material.metallic is not None
                or material.metallic_requires_name is not None
                or material.albedo_luminance is not None
            )
        )
    )


def blend_problems(path: str, measured: BlendMeasure, rules: BibleRules) -> list[str]:
    """Art bible problems in one measured `.blend` file."""
    models = rules.models
    problems: list[str] = []
    # Rig control shapes (Rigify's WGT- objects) stay in the source to draw the
    # rig and never reach the GLB: the checks judge what ships.
    objects = [o for o in measured.objects if not o.widget]
    meshes = [o for o in objects if o.type == "MESH"]
    budget = _folder_value(models.max_triangles, path)
    triangles = sum(o.triangles for o in meshes)
    if budget is not None and triangles > budget:
        problems.append(
            f"{path}: {triangles} triangles, over the art bible's {budget} budget"
        )
    bones = _folder_value(models.max_bones, path)
    if bones is not None:
        for armature in measured.armatures:
            if armature.deform_bones > bones:
                problems.append(
                    f"{path}: armature {armature.name!r} has {armature.deform_bones} "
                    f"deform bones, over the art bible's {bones} budget"
                )
    if models.applied_scale:
        scaled = [o.name for o in objects if _scale_off(o.scale)]
        if scaled:
            problems.append(
                f"{path}: scale is not applied on {_few(scaled)}; the art bible "
                "asks for scale 1"
            )
    pivot = _folder_value(models.pivot, path)
    if pivot is not None and pivot != "any":
        armatures = {a.name for a in measured.armatures}
        for obj in meshes:
            if obj.parent is not None and obj.parent not in armatures:
                continue  # a part: its origin is relative to its parent
            problem = _pivot_problem(
                path,
                repr(obj.name),
                obj.bbox_min,
                obj.bbox_max,
                pivot,
                models.pivot_tolerance_m,
            )
            if problem:
                problems.append(problem)
    texel = _folder_value(models.texel_density, path)
    if texel is not None:
        for obj in meshes:
            if obj.texel_density is not None:
                problem = _texel_problem(path, repr(obj.name), obj.texel_density, texel)
                if problem:
                    problems.append(problem)
    problems += _name_problems(
        path,
        {
            "object names": [o.name for o in objects],
            "mesh data names": [o.mesh for o in meshes if o.mesh],
            "material names": [m.name for m in measured.materials],
        },
        rules.naming,
    )
    if _in_folders(rules.materials.folders, path):
        for material in measured.materials:
            luminance = (
                luminance_linear(material.base_color)
                if material.base_color is not None
                else None
            )
            problems += _material_problems(
                path,
                material.name,
                material.roughness,
                material.metallic,
                luminance,
                rules.materials,
            )
    return problems


def _format_problems(
    path: str, got: AudioFormat, rule: AudioFormatRule, folder: str
) -> list[str]:
    problems: list[str] = []
    if got.container != rule.container:
        problems.append(
            f"{path}: {got.container.upper()} file, the art bible asks for "
            f"{rule.container.upper()} in {folder}"
        )
        return problems
    if rule.sample_rates and got.sample_rate not in rule.sample_rates:
        problems.append(
            f"{path}: {got.sample_rate} Hz, the art bible allows "
            + ", ".join(f"{r} Hz" for r in rule.sample_rates)
        )
    if rule.channels and got.channels not in rule.channels:
        problems.append(
            f"{path}: {got.channels} channel(s), the art bible allows "
            + " or ".join(str(c) for c in rule.channels)
        )
    if rule.bit_depths and got.bits is not None and got.bits not in rule.bit_depths:
        problems.append(
            f"{path}: {got.bits}-bit, the art bible allows "
            + " or ".join(f"{b}-bit" for b in rule.bit_depths)
        )
    return problems


def _format_folder(rules: dict[str, AudioFormatRule], path: str) -> str:
    """The most specific folder with a format rule for the path, or ""."""
    return _folder_value({folder: folder for folder in rules}, path) or ""


def _texture_problems(path: str, data: bytes, rules: TextureRules) -> list[str]:
    limit = _folder_value(rules.max_size, path)
    if limit is None and not rules.power_of_two:
        return []
    size = image_size(data, _suffix(path))
    if size is None:
        return [f"{path}: image size could not be read"]
    width, height = size
    problems: list[str] = []
    if limit is not None and max(width, height) > limit:
        problems.append(
            f"{path}: {width}x{height}, over the art bible's {limit} px limit"
        )
    if rules.power_of_two and not all(v > 0 and v & (v - 1) == 0 for v in size):
        problems.append(f"{path}: {width}x{height} is not a power of two on both sides")
    return problems


def _is_albedo(path: str, rules: MaterialRules) -> bool:
    stem = PurePosixPath(path).stem.lower()
    return (
        rules.albedo_luminance is not None
        and _in_folders(rules.folders, path)
        and any(stem.endswith(s.lower()) for s in rules.albedo_suffixes)
    )


def _excluded(path: str, rules: BibleRules) -> bool:
    return _in_folders(rules.exclude, path)


async def check_rules(
    stage: Path, changed: Iterable[str], measure: Measure
) -> list[str]:
    """Problems with changed assets under the approved bible's checked rules."""
    rules = load_rules(stage)
    paths = sorted(
        {
            p
            for p in changed
            if (stage / p).is_file()
            and p not in _NOT_CHECKED
            and not p.startswith(_RECIPES)
            and _suffix(p) not in _SIDECARS
            and not PurePosixPath(p).name.startswith(".")
            and not _excluded(p, rules)
        }
    )
    problems: list[str] = []
    blends: list[str] = []
    oggs: list[str] = []
    height: int | None = None
    access = rules.accessibility
    for path in paths:
        suffix = _suffix(path)
        pattern = _folder_value(rules.naming.files, path)
        stem = PurePosixPath(path).name.split(".")[0]
        if pattern is not None and not re.fullmatch(pattern, stem):
            problems.append(
                f"{path}: file name {stem!r} does not follow the art bible's "
                f"pattern {pattern}"
            )
        if suffix == ".glb":
            problems += _glb_problems(path, (stage / path).read_bytes(), rules)
        elif suffix == ".blend" and _blend_wanted(path, rules):
            blends.append(path)
        elif suffix in _AUDIO:
            folder = _format_folder(rules.audio.format, path)
            if folder:
                rule = rules.audio.format[folder]
                if suffix == ".ogg":
                    oggs.append(path)
                    continue
                try:
                    found = wav_format((stage / path).read_bytes())
                except ValueError:
                    problems.append(f"{path}: audio format could not be read")
                    continue
                problems += _format_problems(path, found, rule, folder)
        elif suffix in _IMAGES and _in_folders(rules.textures.folders, path):
            problems += _texture_problems(
                path, (stage / path).read_bytes(), rules.textures
            )
        elif (
            suffix in _UI_RESOURCES
            and _in_folders(access.ui_folders, path)
            and (access.min_ui_contrast is not None or access.min_text_px is not None)
        ):
            if height is None:
                height = viewport_height(stage)
            problems += ui_resource_problems(
                path, (stage / path).read_text(errors="replace"), access, height
            )
    if blends:
        got = await measure(measure_argv(blends), stage)
        measured = parse_measures(got.output)
        for path in blends:
            if path not in measured:
                problems.append(f"{path}: Blender could not measure this file")
                continue
            problems += blend_problems(path, measured[path], rules)
    problems += await _image_problems(stage, paths, rules, measure)
    problems += await _sprite_problems(stage, paths, rules.sprites, measure)
    if oggs:
        got = await measure(["sh", "-c", _PROBE_AUDIO, "sh", *oggs], stage)
        probed = parse_probe(got.output)
        for path in oggs:
            folder = _format_folder(rules.audio.format, path)
            if path not in probed:
                problems.append(f"{path}: audio format could not be read")
                continue
            problems += _format_problems(
                path, probed[path], rules.audio.format[folder], folder
            )
    audio = [
        p
        for p in paths
        if _suffix(p) in _AUDIO
        and (
            rules.audio.true_peak_max_dbtp is not None
            or _folder_value(rules.audio.lufs, p) is not None
        )
    ]
    if not audio:
        return problems
    got = await measure(["sh", "-c", _MEASURE, "sh", *audio], stage)
    loudness = parse_loudness(got.output) if got.ok else {}
    for path in audio:
        values = loudness.get(path, {})
        if "true_peak" not in values or "lufs" not in values:
            problems.append(f"{path}: loudness could not be measured")
            continue
        ceiling = rules.audio.true_peak_max_dbtp
        if ceiling is not None and values["true_peak"] > ceiling:
            problems.append(
                f"{path}: true peak {values['true_peak']:.1f} dBTP, above the art "
                f"bible's {ceiling:.1f} dBTP"
            )
        target = _folder_value(rules.audio.lufs, path)
        if (
            target is not None
            and abs(values["lufs"] - target) > rules.audio.lufs_tolerance
        ):
            problems.append(
                f"{path}: {values['lufs']:.1f} LUFS, the art bible asks for "
                f"{target:.1f} ± {rules.audio.lufs_tolerance:.1f}"
            )
    return problems


async def _image_problems(
    stage: Path, paths: list[str], rules: BibleRules, measure: Measure
) -> list[str]:
    """Palette distance and albedo bounds, from one sampling run."""
    palette_rules = rules.palette
    colours = palette_rules.all_colours()
    palette_images = [
        p
        for p in paths
        if colours and _suffix(p) in _IMAGES and _in_folders(palette_rules.folders, p)
    ]
    albedo_images = [
        p for p in paths if _suffix(p) in _IMAGES and _is_albedo(p, rules.materials)
    ]
    images = sorted(set(palette_images) | set(albedo_images))
    if not images:
        return []
    got = await measure(["sh", "-c", _SAMPLE_IMAGES, "sh", *images], stage)
    sampled = parse_samples(got.output) if got.ok else {}
    problems: list[str] = []
    palette = [_lab(hex_rgb(c)) for c in colours]
    for path in images:
        if path not in sampled:
            problems.append(f"{path}: colours could not be measured")
            continue
        pixels = sampled[path]
        if not pixels:
            continue
        if path in palette_images:
            cache: dict[tuple[int, int, int], bool] = {}
            far = 0
            for rgb in pixels:
                if rgb not in cache:
                    lab = _lab(rgb)
                    cache[rgb] = (
                        min(delta_e_2000(lab, p) for p in palette)
                        > palette_rules.max_delta_e
                    )
                far += cache[rgb]
            share = far / len(pixels)
            if share > palette_rules.max_off_palette:
                problems.append(
                    f"{path}: {share:.0%} of its colours are more than ΔE "
                    f"{palette_rules.max_delta_e:g} from the palette (allowed "
                    f"{palette_rules.max_off_palette:.0%})"
                )
        if path in albedo_images and rules.materials.albedo_luminance is not None:
            low, high = rules.materials.albedo_luminance
            outside = sum(
                not low
                <= luminance_linear([_channel_linear(c / 255) for c in rgb])
                <= high
                for rgb in pixels
            )
            share = outside / len(pixels)
            if share > rules.materials.albedo_max_outside:
                problems.append(
                    f"{path}: {share:.0%} of the albedo is near pure black or white "
                    f"(luminance outside {low:g}-{high:g}; allowed "
                    f"{rules.materials.albedo_max_outside:.0%})"
                )
    return problems


async def _sprite_problems(
    stage: Path, paths: list[str], rules: SpriteRules, measure: Measure
) -> list[str]:
    images = [
        p
        for p in paths
        if _suffix(p) in _IMAGES and _folder_value(rules.max_colours, p) is not None
    ]
    if not images:
        return []
    got = await measure(["sh", "-c", _COUNT_COLOURS, "sh", *images], stage)
    counts = parse_colour_counts(got.output)
    problems: list[str] = []
    for path in images:
        limit = _folder_value(rules.max_colours, path)
        assert limit is not None
        if path not in counts:
            problems.append(f"{path}: colours could not be counted")
        elif counts[path] > limit:
            problems.append(
                f"{path}: {counts[path]} distinct colours, over the art bible's "
                f"{limit} per sprite"
            )
    return problems


def ui_resources(rules: BibleRules, paths: Iterable[str]) -> list[str]:
    """Changed `.tres`/`.tscn` files the accessibility rules look at."""
    access = rules.accessibility
    if access.min_ui_contrast is None and access.min_text_px is None:
        return []
    return sorted(
        p
        for p in paths
        if _suffix(p) in _UI_RESOURCES
        and _in_folders(access.ui_folders, p)
        and not _excluded(p, rules)
    )
