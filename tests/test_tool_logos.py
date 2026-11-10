"""The tool rail's app logos are the pinned official files, small, square and inert."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "godot_addon/joinery/icons/apps"
TOOLS = ROOT / "godot_addon/joinery/icons/tools"
#: Built-in tools whose logo Joinery ships; Blender's terms need the word mark beside it.
LOGOS = {
    "godot",
    "krita",
    "gimp",
    "inkscape",
    "material-maker",
    "surge-xt",
    "supercollider",
}
#: Anything that could run, fetch or embed: none of it belongs in a rail icon.
UNSAFE = re.compile(
    r"<script|<foreignObject|<image\b|\bon\w+\s*=|data:|href\s*=\s*\"(?!#)|url\((?!#)",
    re.IGNORECASE,
)


def _manifest() -> list[dict[str, str]]:
    manifest: list[dict[str, str]] = json.loads(
        (APPS / "sources.json").read_text(encoding="utf-8")
    )
    return manifest


def test_every_logo_is_pinned_with_its_source_hash_and_licence() -> None:
    manifest = _manifest()
    assert {entry["file"] for entry in manifest} == {p.name for p in APPS.glob("*.svg")}
    for entry in manifest:
        data = (APPS / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["file"]
        assert re.fullmatch(r"[0-9a-f]{64}", entry["source_sha256"]), entry["file"]
        assert entry["source"].startswith("https://"), entry["file"]
        assert entry["licence"] and entry["pin"], entry["file"]
    documented = (ROOT / "licenses/tool-logos.md").read_text(encoding="utf-8")
    for entry in manifest:
        assert entry["source_sha256"] in documented, entry["file"]


def test_each_built_in_tool_has_its_logo_and_blender_keeps_the_in_house_glyph() -> None:
    assert {p.stem for p in APPS.glob("*.svg")} - {"inkscape-dark"} == LOGOS
    assert not (APPS / "blender.svg").exists()
    assert "In-house Joinery glyph" in (TOOLS / "blender.svg").read_text(
        encoding="utf-8"
    )
    # Removed tools (ADR 0025) have no icons left behind.
    for gone in ("aseprite", "audacity", "musescore", "reaper"):
        assert (
            not (TOOLS / f"{gone}.svg").exists() and not (APPS / f"{gone}.svg").exists()
        )


def test_logos_are_small_square_and_inert() -> None:
    for path in sorted(APPS.glob("*.svg")):
        text = path.read_text(encoding="utf-8")
        assert len(text) < 24_000, path.name
        assert not UNSAFE.search(text), path.name
        root = re.search(r"<svg\b[^>]*>", text)
        assert root, path.name
        view = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', root.group(0))
        assert view and abs(float(view[1]) - float(view[2])) < 0.01, path.name
        assert 'width="20"' in root.group(0) and 'height="20"' in root.group(0), (
            path.name
        )
