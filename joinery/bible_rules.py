"""Machine-checked art bible rules, run on changed assets before review.

The approved bible carries a fenced ```toml block under "Checked rules". The
user approves these numbers together with the prose, so a check can never use
a rule the user has not seen. Rules cover what can be measured without taste:
loudness and true peak for audio, and triangle budgets for exported models.
"""

from __future__ import annotations

import json
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


class BibleRules(_Strict):
    audio: AudioRules = AudioRules()
    models: ModelRules = ModelRules()


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
