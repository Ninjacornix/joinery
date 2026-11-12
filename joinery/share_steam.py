"""`joinery share steam-art`: Steam's store capsules and library images, from the game itself.

Every image is a crop of the game's own store screenshots (``joinery share
media``), composed in Krita in the offline worker with the game's name as its
only text, the way the itch.io cover is made (ADR 0046):

* **Store**: the header, small, main and vertical capsules.
* **Library**: the library capsule, the hero (art only, no text) and the logo
  (the title alone, on transparency).

Sizes, text rules and safe areas come from ``policy/steam_art.toml``, read
from Valve's public Steamworks documentation; the policy says to verify them
against Steamworks before uploading. After Krita renders, each image is
checked: its exact size, transparency where Steam asks for it, no text where
Steam allows none, the title inside the safe area, large enough where the
image is shown smallest, and in enough contrast with the art behind it.

Each run writes ``share/recipes/steam_art.py`` (the Krita script) and records
the plan, checks and output hashes under ``steam_art`` in
``share/recipes/media.json``; ``share/media/steam/*.kra`` are the native
sources. Nothing is uploaded: Steam takes these files through Steamworks, by
the user.
"""

from __future__ import annotations

import json
import shutil
import tomllib
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from joinery.catalog import ROOT
from joinery.pinned import digest
from joinery.share_build import SHARE, WORK, ensure_share_dir, game_name
from joinery.share_media import (
    MEDIA,
    RECIPE,
    RECIPES,
    MediaError,
    Progress,
    _pin_check,
    _recipe,
    _worker,
    pins,
)

POLICY = ROOT / "policy" / "steam_art.toml"
STEAM = MEDIA / "steam"
SCRIPT = RECIPES / "steam_art.py"
PLATES = WORK / "steam"
#: A source upscaled more than this is soft on Steam; the check says so.
UPSCALE_WARN = 2.0
#: Share of the image height one line of title may take, per asset.
TITLE_HEIGHT = {
    "header": 0.2,
    "small": 0.42,
    "main": 0.13,
    "vertical": 0.09,
    "library_capsule": 0.085,
    "library_logo": 0.55,
}
#: Centred both ways (the small capsule's title nearly fills it; the logo is only a title).
CENTRED = {"small", "library_logo"}
#: Bold sans advance widths in em, rounded up so an estimate never undershoots.
_ADVANCE = {"upper": 0.76, "lower": 0.64, "digit": 0.7, "space": 0.35, "other": 0.55}
_ASCENT = 0.93
_DESCENT = 0.24
_CAP = 0.73
_LINE = 1.15


@dataclass(frozen=True)
class Asset:
    id: str
    label: str
    group: str
    width: int
    height: int
    text: str
    safe: tuple[int, int, int, int]
    shown_width: int
    transparent: bool = False
    assumed: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Policy:
    checked: str
    note: str
    sources: tuple[str, ...]
    min_title_px: float
    min_contrast: float
    min_contrast_bright: float
    assets: tuple[Asset, ...]


def load_policy(path: Path = POLICY) -> Policy:
    with path.open("rb") as f:
        data = tomllib.load(f)
    assets = []
    for raw in data["asset"]:
        width, height = int(raw["width"]), int(raw["height"])
        if "safe_width" in raw:
            sw, sh = int(raw["safe_width"]), int(raw["safe_height"])
            safe = (
                (width - sw) // 2,
                (height - sh) // 2,
                (width + sw) // 2,
                (height + sh) // 2,
            )
        else:
            inset = float(raw.get("safe_inset", 0.0))
            dx, dy = round(width * inset), round(height * inset)
            safe = (dx, dy, width - dx, height - dy)
        assumed = raw.get("assumed", [])
        assets.append(
            Asset(
                id=str(raw["id"]),
                label=str(raw["label"]),
                group=str(raw["group"]),
                width=width,
                height=height,
                text=str(raw["text"]),
                safe=safe,
                shown_width=int(raw.get("shown_width", width)),
                transparent=bool(raw.get("transparent", False)),
                assumed=tuple(str(a) for a in assumed)
                if isinstance(assumed, list)
                else (),
            )
        )
    readable = data["readability"]
    return Policy(
        checked=str(data["checked"]),
        note=str(data["note"]),
        sources=tuple(str(s) for s in data["sources"]),
        min_title_px=float(readable["min_title_px"]),
        min_contrast=float(readable["min_contrast"]),
        min_contrast_bright=float(readable["min_contrast_bright"]),
        assets=tuple(assets),
    )


# --- Laying out the title -----------------------------------------------------------------


def text_em(text: str) -> float:
    """The width of [text] in em, in a bold sans, estimated generously."""
    total = 0.0
    for char in text:
        if char == " ":
            total += _ADVANCE["space"]
        elif char.isdigit():
            total += _ADVANCE["digit"]
        elif char.isupper():
            total += _ADVANCE["upper"]
        elif char.isalpha():
            total += _ADVANCE["lower"]
        else:
            total += _ADVANCE["other"]
    return total


def _splits(title: str) -> list[list[str]]:
    """One line, and the two-line split nearest the middle (at a space)."""
    options = [[title]]
    spaces = [i for i, c in enumerate(title) if c == " "]
    if spaces:
        middle = min(
            spaces, key=lambda i: abs(text_em(title[:i]) - text_em(title[i + 1 :]))
        )
        options.append([title[:middle], title[middle + 1 :]])
    return options


def layout(asset: Asset, title: str) -> dict[str, Any] | None:
    """Where the title goes and how large: {size, lines, box: [x, y, w, h], centred}."""
    if asset.text == "none" or not title.strip():
        return None
    x0, y0, x1, y1 = asset.safe
    room_w, room_h = x1 - x0, y1 - y0
    cap = asset.height * TITLE_HEIGHT.get(asset.id, 0.12)
    best: tuple[float, list[str]] | None = None
    for lines in _splits(title.strip()):
        widest = max(text_em(line) for line in lines)
        tall = (len(lines) - 1) * _LINE + _ASCENT + _DESCENT
        size = min(cap, room_w / widest, room_h / tall)
        # Two lines only when clearly larger: one line reads faster.
        if best is None or size > best[0] * 1.2:
            best = (size, lines)
    assert best is not None
    size, lines = best
    size = float(int(size))
    width = max(text_em(line) for line in lines) * size
    height = ((len(lines) - 1) * _LINE + _ASCENT + _DESCENT) * size
    centred = asset.id in CENTRED
    if centred:
        x = (asset.width - width) / 2
        y = (asset.height - height) / 2
    else:
        x, y = float(x0), y1 - height
    return {
        "size": size,
        "lines": lines,
        "box": [round(x), round(y), round(width), round(height)],
        "centred": centred,
    }


def _svg(asset: Asset, place: dict[str, Any], family: str) -> str:
    size = place["size"]
    x, y, width, _ = place["box"]
    anchor = "middle" if place["centred"] else "start"
    left = x + width / 2 if place["centred"] else x
    stroke = ""
    if asset.transparent:
        # A logo sits on art Joinery doesn't see: a thin dark edge keeps it readable.
        stroke = f";stroke:#101215;stroke-width:{max(1.0, size * 0.05):.1f}px"
    texts = []
    for index, line in enumerate(place["lines"]):
        baseline = y + _ASCENT * size + index * _LINE * size
        escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        texts.append(
            f'<text x="{left:.0f}" y="{baseline:.0f}" text-anchor="{anchor}" '
            f'style="font-family:{family};font-weight:bold;font-size:{size:.0f}px;'
            f'fill:#ffffff{stroke}">{escaped}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{asset.width}pt" '
        f'height="{asset.height}pt" viewBox="0 0 {asset.width} {asset.height}">'
        + "".join(texts)
        + "</svg>"
    )


# --- The plan and the Krita script ---------------------------------------------------------


def plan(
    policy: Policy, shots: list[str], busiest: str, title: str, family: str
) -> list[dict[str, Any]]:
    """Per image: its source screenshot, title layout and files. Varied views where it helps."""
    if not shots:
        raise MediaError("Make the screenshots first: the Steam art is made from them.")
    first = busiest if busiest in shots else shots[0]
    others = [s for s in shots if s != first] or [first]
    # The capsules people see side by side in the store share the strongest view; the
    # portrait ones and the hero show other moments of the game.
    choice = {
        "header": first,
        "small": first,
        "main": first,
        "vertical": others[0],
        "library_capsule": others[1 % len(others)],
        "library_hero": others[2 % len(others)],
    }
    items = []
    for asset in policy.assets:
        place = layout(asset, title)
        items.append(
            {
                "id": asset.id,
                "label": asset.label,
                "group": asset.group,
                "size": [asset.width, asset.height],
                "source": None if asset.transparent else choice.get(asset.id, first),
                "title": place,
                "svg": _svg(asset, place, family) if place else None,
                "shade": "full"
                if asset.id == "small"
                else ("none" if not place else "foot"),
                "transparent": asset.transparent,
                "png": (STEAM / f"{asset.id}.png").as_posix(),
                "kra": (STEAM / f"{asset.id}.kra").as_posix(),
                "plate": (
                    (PLATES / f"{asset.id}.raw").as_posix()
                    if place and not asset.transparent
                    else None
                ),
            }
        )
    return items


def krita_script(items: list[dict[str, Any]], staged: dict[str, str]) -> str:
    """The Krita script that composes every image; saved as the replayable recipe."""
    jobs = [
        {
            "id": item["id"],
            "w": item["size"][0],
            "h": item["size"][1],
            "source": staged.get(str(item["source"])) if item["source"] else None,
            "svg": item["svg"],
            "shade": item["shade"],
            "box": item["title"]["box"] if item["title"] else None,
            "png": item["png"],
            "kra": item["kra"],
            "plate": item["plate"],
        }
        for item in items
    ]
    return f'''"""Steam store and library art: the game's screenshots and its title. Made by Joinery in Krita.

Replay: joinery share steam-art --project . (the screenshots come from the seeded
run recorded in share/recipes/media.json; sizes from policy/steam_art.toml).
"""
from krita import Krita, InfoObject
import json
import os

os.chdir("/work")
app = Krita.instance()
JOBS = json.loads({json.dumps(json.dumps(jobs))})


def crop(source, w, h):
    shot = app.openDocument(os.path.abspath(source))
    shot.setBatchmode(True)
    scale = max(w / shot.width(), h / shot.height())
    nw, nh = round(shot.width() * scale), round(shot.height() * scale)
    shot.scaleImage(nw, nh, int(shot.xRes()), int(shot.yRes()), "Bicubic")
    shot.waitForDone()
    shot.refreshProjection()
    pixels = shot.pixelData((nw - w) // 2, (nh - h) // 2, w, h)
    shot.close()
    return pixels


def shade(job):
    w, h = job["w"], job["h"]
    rows = bytearray()
    if job["shade"] == "full":
        rows = bytearray(bytes((0, 0, 0, 120)) * (w * h))
    else:
        top = max(0, job["box"][1] - h * 0.18)
        for y in range(h):
            alpha = 0 if y < top else int(200 * min(1.0, (y - top) / max(1.0, h - top)))
            rows += bytes((0, 0, 0, alpha)) * w
    return bytes(rows)


for job in JOBS:
    w, h = job["w"], job["h"]
    document = app.createDocument(w, h, job["id"], "RGBA", "U8", "", 72.0)
    document.setBatchmode(True)
    root = document.rootNode()
    for child in root.childNodes():
        root.removeChildNode(child)
    below = None
    if job["source"]:
        picture = document.createNode("Screenshot", "paintlayer")
        picture.setPixelData(crop(job["source"], w, h), 0, 0, w, h)
        root.addChildNode(picture, None)
        below = picture
        if job["shade"] != "none":
            layer = document.createNode("Shade", "paintlayer")
            layer.setPixelData(shade(job), 0, 0, w, h)
            root.addChildNode(layer, below)
            below = layer
    document.refreshProjection()
    document.waitForDone()
    if job["plate"] and job["box"]:
        # The art behind the title, for the readability check.
        x, y, bw, bh = job["box"]
        x, y = max(0, x), max(0, y)
        bw, bh = max(1, min(bw, w - x)), max(1, min(bh, h - y))
        os.makedirs(os.path.dirname(os.path.abspath(job["plate"])), exist_ok=True)
        with open(os.path.abspath(job["plate"]), "wb") as plate:
            plate.write(bytes(document.pixelData(x, y, bw, bh)))
    if job["svg"]:
        title = document.createVectorLayer("Title")
        root.addChildNode(title, below)
        title.addShapesFromSvg(job["svg"])
    document.refreshProjection()
    document.waitForDone()
    os.makedirs(os.path.dirname(os.path.abspath(job["kra"])), exist_ok=True)
    assert document.saveAs(os.path.abspath(job["kra"]))
    info = InfoObject()
    info.setProperty("alpha", True)
    info.setProperty("compression", 6)
    assert document.exportImage(os.path.abspath(job["png"]), info)
    document.close()
'''


# --- Checks ---------------------------------------------------------------------------------


def png_header(path: Path) -> tuple[int, int, int, int]:
    """(width, height, bit depth, colour type) of a PNG."""
    data = path.read_bytes()[:33]
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError(f"{path.name} is not a PNG.")
    return (
        int.from_bytes(data[16:20], "big"),
        int.from_bytes(data[20:24], "big"),
        data[24],
        data[25],
    )


def png_alpha_corners(path: Path) -> list[int]:
    """The alpha of a PNG's four corner pixels (8-bit RGBA, not interlaced)."""
    data = path.read_bytes()
    width, height, depth, colour = png_header(path)
    if depth != 8 or colour != 6 or data[28] != 0:
        return [255] * 4
    chunks = bytearray()
    at = 8
    while at < len(data):
        length = int.from_bytes(data[at : at + 4], "big")
        if data[at + 4 : at + 8] == b"IDAT":
            chunks += data[at + 8 : at + 8 + length]
        at += 12 + length
    raw = zlib.decompress(bytes(chunks))
    stride = width * 4
    previous = bytearray(stride)
    rows: dict[int, bytearray] = {}
    for y in range(height):
        start = y * (stride + 1)
        line = bytearray(raw[start + 1 : start + 1 + stride])
        _unfilter(raw[start], line, previous, 4)
        if y in (0, height - 1):
            rows[y] = line
        previous = line
    return [rows[y][x * 4 + 3] for y in (0, height - 1) for x in (0, width - 1)]


def _unfilter(kind: int, line: bytearray, previous: bytearray, bpp: int) -> None:
    if kind == 0:
        return
    for i in range(len(line)):
        left = line[i - bpp] if i >= bpp else 0
        up = previous[i]
        if kind == 1:
            line[i] = (line[i] + left) & 0xFF
        elif kind == 2:
            line[i] = (line[i] + up) & 0xFF
        elif kind == 3:
            line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
        elif kind == 4:
            corner = previous[i - bpp] if i >= bpp else 0
            p = left + up - corner
            pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
            pick = left if pa <= pb and pa <= pc else up if pb <= pc else corner
            line[i] = (line[i] + pick) & 0xFF


def _linear(channel: int) -> float:
    value = channel / 255.0
    return (
        value / 12.92 if value <= 0.04045 else float(((value + 0.055) / 1.055) ** 2.4)
    )


def contrast_with_white(plate: bytes) -> tuple[float, float]:
    """WCAG contrast of white against the art (Krita's BGRA bytes): on average, and against
    its brightest tenth."""
    lums = sorted(
        0.2126 * _linear(plate[i + 2])
        + 0.7152 * _linear(plate[i + 1])
        + 0.0722 * _linear(plate[i])
        for i in range(0, len(plate) - 3, 4)
    )
    if not lums:
        return 0.0, 0.0
    mean = sum(lums) / len(lums)
    bright = lums[min(len(lums) - 1, int(len(lums) * 0.9))]
    return 1.05 / (mean + 0.05), 1.05 / (bright + 0.05)


def check(
    project: Path, policy: Policy, items: list[dict[str, Any]], scales: dict[str, float]
) -> list[dict[str, Any]]:
    """Each image against the policy: {id, status: ok|warning|failed, problems, facts}."""
    results: list[dict[str, Any]] = []
    by_id = {asset.id: asset for asset in policy.assets}
    for item in items:
        asset = by_id[item["id"]]
        problems: list[str] = []
        notes: list[str] = []
        facts: dict[str, Any] = {}
        png = project / item["png"]
        if not png.is_file():
            results.append(
                {
                    "id": asset.id,
                    "status": "failed",
                    "problems": ["Krita didn't save it."],
                }
            )
            continue
        width, height, _, colour = png_header(png)
        facts["size"] = [width, height]
        if (width, height) != (asset.width, asset.height):
            problems.append(
                f"It is {width}×{height}; Steam asks for {asset.width}×{asset.height}."
            )
        if asset.transparent:
            corners = png_alpha_corners(png)
            facts["corner_alpha"] = corners
            if colour != 6 or any(a != 0 for a in corners):
                problems.append("Its background isn't transparent.")
        place = item["title"]
        if asset.text == "none":
            facts["text"] = "none"
            if place is not None:
                problems.append("It carries text; Steam allows none here.")
        elif place is None:
            problems.append("It has no title; Steam asks for a readable one.")
        else:
            x, y, w, h = place["box"]
            x0, y0, x1, y1 = asset.safe
            if x < x0 - 1 or y < y0 - 1 or x + w > x1 + 1 or y + h > y1 + 1:
                problems.append("The title runs outside the safe area.")
            shown = place["size"] * _CAP * asset.shown_width / asset.width
            facts["title_px_shown"] = round(shown, 1)
            if shown < policy.min_title_px:
                problems.append(
                    f"The title is {shown:.0f} px tall where Steam shows this "
                    f"{asset.shown_width} px wide; it needs {policy.min_title_px:.0f}. "
                    "A shorter name would be larger."
                )
            plate = project / str(item["plate"]) if item.get("plate") else None
            if plate is not None and plate.is_file():
                average, bright = contrast_with_white(plate.read_bytes())
                facts["contrast"] = [round(average, 2), round(bright, 2)]
                if average < policy.min_contrast or bright < policy.min_contrast_bright:
                    problems.append(
                        f"The title's contrast with the art is {average:.1f}:1 "
                        f"(needs {policy.min_contrast:g}:1)."
                    )
            elif not asset.transparent:
                problems.append("The art behind the title couldn't be measured.")
        scale = scales.get(asset.id, 1.0)
        facts["upscaled"] = round(scale, 2)
        if scale > UPSCALE_WARN:
            notes.append(
                f"The screenshot is enlarged {scale:.1f}×, so it looks soft; "
                "larger screenshots would be sharper."
            )
        status = "failed" if problems else ("warning" if notes else "ok")
        results.append(
            {
                "id": asset.id,
                "status": status,
                "problems": problems + notes,
                "facts": facts,
            }
        )
    return results


# --- Making the set -------------------------------------------------------------------------


def _shot_size(path: Path) -> tuple[int, int]:
    width, height, _, _ = png_header(path)
    return width, height


def _ignore_renders(project: Path) -> None:
    ignore = project / SHARE / ".gitignore"
    line = "media/steam/*.png"
    text = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
    if line not in text.splitlines():
        ignore.write_text(
            text + ("" if text.endswith("\n") or not text else "\n") + line + "\n"
        )


async def make(project: Path, progress: Progress | None = None) -> dict[str, Any]:
    """Compose and check every Steam image; returns the recipe entry (also saved)."""
    say = progress or (lambda _text: None)
    project = project.resolve()
    if not (project / "project.godot").is_file():
        raise ValueError(f"{project} is not a Godot project.")
    ensure_share_dir(project)
    _ignore_renders(project)
    recipe = _recipe(project)
    shots_entry = recipe.get("screenshots", {})
    shots_entry = shots_entry if isinstance(shots_entry, dict) else {}
    shots = [s for s in shots_entry.get("files", []) if (project / str(s)).is_file()]
    policy = load_policy()
    tools = pins()
    items = plan(
        policy,
        shots,
        str(shots_entry.get("cover_from", "")),
        game_name(project),
        tools["font_family"],
    )
    staged: dict[str, str] = {}
    scales: dict[str, float] = {}
    (project / PLATES).mkdir(parents=True, exist_ok=True)
    for item in items:
        source = item["source"]
        if not source:
            continue
        if source not in staged:
            target = PLATES / f"source-{len(staged) + 1}.png"
            shutil.copyfile(project / source, project / target)
            staged[source] = target.as_posix()
        sw, sh = _shot_size(project / source)
        scales[item["id"]] = max(item["size"][0] / sw, item["size"][1] / sh)
    for old in (project / STEAM).glob("*.png") if (project / STEAM).is_dir() else []:
        old.unlink()
    (project / STEAM).mkdir(parents=True, exist_ok=True)
    (project / RECIPES).mkdir(parents=True, exist_ok=True)
    (project / SCRIPT).write_text(krita_script(items, staged), encoding="utf-8")
    say("Composing the Steam art in Krita")
    await _worker(
        project,
        _pin_check("krita", tools["krita"]) + " && joinery-krita " + SCRIPT.as_posix(),
    )
    say("Checking sizes, safe areas and the title")
    checks = check(project, policy, items, scales)
    entry: dict[str, Any] = {
        "policy": {
            "checked": policy.checked,
            "note": policy.note,
            "sources": list(policy.sources),
        },
        "script": SCRIPT.as_posix(),
        "krita": tools["krita"],
        "images": [
            {k: v for k, v in item.items() if k not in {"svg", "plate"}}
            for item in items
        ],
        "checks": checks,
        "outputs": {
            path.relative_to(project).as_posix(): digest(path)
            for path in sorted((project / STEAM).iterdir())
            if path.is_file()
        },
    }
    recipe = _recipe(project)
    recipe["steam_art"] = entry
    outputs = (
        recipe.get("outputs", {}) if isinstance(recipe.get("outputs"), dict) else {}
    )
    outputs.update(entry["outputs"])
    recipe["outputs"] = outputs
    (project / RECIPE).write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(project / PLATES, ignore_errors=True)
    return entry


def listing(project: Path) -> dict[str, Any]:
    """The Steam art as it is now, for the Share page: every image with its check."""
    policy = load_policy()
    recipe = _recipe(project)
    entry = (
        recipe.get("steam_art", {}) if isinstance(recipe.get("steam_art"), dict) else {}
    )
    checks = {
        str(c.get("id")): c for c in entry.get("checks", []) if isinstance(c, dict)
    }
    images = []
    for asset in policy.assets:
        path = STEAM / f"{asset.id}.png"
        made = (project / path).is_file()
        found = checks.get(asset.id, {})
        images.append(
            {
                "id": asset.id,
                "label": asset.label,
                "group": asset.group,
                "size": [asset.width, asset.height],
                "path": path.as_posix() if made else None,
                "status": str(found.get("status", "")) if made else "",
                "problems": list(found.get("problems", [])) if made else [],
            }
        )
    shots = recipe.get("screenshots", {})
    return {
        "note": policy.note,
        "checked": policy.checked,
        "ready": bool(isinstance(shots, dict) and shots.get("files")),
        "images": images,
    }
