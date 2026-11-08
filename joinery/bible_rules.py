"""Machine-checked art bible rules, run on changed assets before review.

The approved bible carries a fenced ```toml block under "Checked rules". The
user approves these numbers together with the prose, so a check can never use
a rule the user has not seen. Rules cover what can be measured without taste:
loudness and true peak for audio, triangle budgets for exported models, and
how far image colours stray from the palette (CIEDE2000).
"""

from __future__ import annotations

import json
import math
import re
import struct
import tomllib
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from joinery.runner import Result

_BLOCK = re.compile(r"^```toml[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_AUDIO = {".wav", ".ogg"}
_MODELS = {".glb"}
_IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
#: Images are sampled on a grid, nearest pixel, so no blended colour appears.
SAMPLE = 64


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AudioRules(_Strict):
    true_peak_max_dbtp: float | None = None
    lufs_tolerance: float = Field(default=1.0, ge=0)
    #: Integrated loudness target per folder, such as "audio/music" = -16.
    lufs: dict[str, float] = {}


class ModelRules(_Strict):
    #: Triangle budget per folder, such as "art/props" = 5000.
    max_triangles: dict[str, int] = {}


class PaletteRules(_Strict):
    #: Palette colours as "#rrggbb". Empty means colours are not checked.
    colours: list[str] = []
    max_delta_e: float = Field(default=12.0, gt=0)
    #: Share of opaque pixels allowed beyond max_delta_e (accents, highlights).
    max_off_palette: float = Field(default=0.05, ge=0, le=1)
    #: Folders whose images are checked.
    folders: list[str] = ["art"]

    def model_post_init(self, _context: object) -> None:
        bad = [c for c in self.colours if not _HEX.match(c)]
        if bad:
            raise ValueError("palette colours are #rrggbb: " + ", ".join(bad))


class BibleRules(_Strict):
    audio: AudioRules = AudioRules()
    models: ModelRules = ModelRules()
    palette: PaletteRules = PaletteRules()


def load_rules(project: Path) -> BibleRules:
    """Rules from the bible's first ```toml block, or none if it has no block."""
    path = project / "art/bible.md"
    if not path.is_file():
        return BibleRules()
    match = _BLOCK.search(path.read_text())
    if match is None:
        return BibleRules()
    try:
        return BibleRules.model_validate(tomllib.loads(match.group(1)))
    except (tomllib.TOMLDecodeError, ValueError) as error:
        raise ValueError(
            f"The art bible's checked rules are invalid: {error}"
        ) from error


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


def glb_triangles(data: bytes) -> int:
    """Triangles drawn by a binary glTF, counting each mesh once per node using it."""
    magic, _version, _length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("Not a binary glTF file.")
    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != 0x4E4F534A:  # "JSON"
        raise ValueError("The glTF file has no JSON chunk.")
    document = json.loads(data[20 : 20 + chunk_length])
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

Measure = Callable[[list[str], Path], Awaitable[Result]]


async def check_rules(
    stage: Path, changed: Iterable[str], measure: Measure
) -> list[str]:
    """Problems with changed assets under the approved bible's checked rules."""
    rules = load_rules(stage)
    paths = sorted({p for p in changed if (stage / p).is_file()})
    problems: list[str] = []
    for path in paths:
        if PurePosixPath(path).suffix.lower() not in _MODELS:
            continue
        budget = _folder_value(rules.models.max_triangles, path)
        if budget is None:
            continue
        triangles = glb_triangles((stage / path).read_bytes())
        if triangles > budget:
            problems.append(
                f"{path}: {triangles} triangles, over the art bible's {budget} budget"
            )
    problems += await _palette_problems(stage, paths, rules.palette, measure)
    audio = [
        p
        for p in paths
        if PurePosixPath(p).suffix.lower() in _AUDIO
        and (
            rules.audio.true_peak_max_dbtp is not None
            or _folder_value(rules.audio.lufs, p) is not None
        )
    ]
    if not audio:
        return problems
    got = await measure(["sh", "-c", _MEASURE, "sh", *audio], stage)
    measured = parse_loudness(got.output) if got.ok else {}
    for path in audio:
        values = measured.get(path, {})
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


def _lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """sRGB (0-255) to CIE L*a*b*, D65."""

    def linear(channel: int) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


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


#: Prints each image as RGBA hex samples on a SAMPLE x SAMPLE nearest grid.
_SAMPLE_IMAGES = (
    'for f in "$@"; do echo "JOINERY_FILE:$f"; '
    f'ffmpeg -v error -i "$f" -vf scale={SAMPLE}:{SAMPLE}:flags=neighbor '
    "-frames:v 1 -f rawvideo -pix_fmt rgba - | od -An -v -tx1 | tr -d ' \\n'; "
    "echo; done"
)


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


async def _palette_problems(
    stage: Path, paths: list[str], rules: PaletteRules, measure: Measure
) -> list[str]:
    images = [
        p
        for p in paths
        if rules.colours
        and PurePosixPath(p).suffix.lower() in _IMAGES
        and _folder_value(dict.fromkeys(rules.folders, True), p)
    ]
    if not images:
        return []
    palette = [
        _lab((int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))) for c in rules.colours
    ]
    got = await measure(["sh", "-c", _SAMPLE_IMAGES, "sh", *images], stage)
    sampled = parse_samples(got.output) if got.ok else {}
    problems: list[str] = []
    for path in images:
        if path not in sampled:
            problems.append(f"{path}: colours could not be measured")
            continue
        pixels = sampled[path]
        if not pixels:
            continue
        cache: dict[tuple[int, int, int], bool] = {}
        far = 0
        for rgb in pixels:
            if rgb not in cache:
                lab = _lab(rgb)
                cache[rgb] = (
                    min(delta_e_2000(lab, p) for p in palette) > rules.max_delta_e
                )
            far += cache[rgb]
        share = far / len(pixels)
        if share > rules.max_off_palette:
            problems.append(
                f"{path}: {share:.0%} of its colours are more than ΔE "
                f"{rules.max_delta_e:g} from the palette (allowed {rules.max_off_palette:.0%})"
            )
    return problems
