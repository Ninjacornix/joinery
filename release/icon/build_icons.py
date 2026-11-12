"""Build every Joinery icon raster from the hand-written SVGs beside this file.

    uv run python release/icon/build_icons.py                       # write dist/, update the lock
    uv run python release/icon/build_icons.py --godot-src godot_patches/godot-src
    uv run python release/icon/build_icons.py --check               # rebuild, compare with the lock

Sources (drawn by hand as SVG code, no generated imagery):
  joinery.svg        the app icon, a brass dovetail on a dark rounded square
  joinery-small.svg  the same mark on a 16-unit pixel grid, used at 16 and 32 px
  joinery-mono.svg   one-colour mark for menu bars and trays

The only rasteriser is rsvg-convert at the pinned version below; ICO and ICNS
containers are assembled here byte for byte, so the outputs are reproducible and
their hashes are recorded in icons.lock.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
LOCK = HERE / "icons.lock.json"
RSVG_VERSION = "2.60.0"

APP = HERE / "joinery.svg"
SMALL = HERE / "joinery-small.svg"
MONO = HERE / "joinery-mono.svg"
SOURCES = (APP, SMALL, MONO)

# Apple's grid: the rounded square is 824 of 1024 units, centred.
MACOS_INSET = 100
LINUX_SIZES = (16, 32, 48, 64, 128, 256, 512)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
MONO_SIZES = (16, 32, 64)
# ICNS PNG slots: (type, pixel size).
ICNS_SLOTS = (
    ("icp4", 16),
    ("ic11", 32),
    ("icp5", 32),
    ("ic12", 64),
    ("ic07", 128),
    ("ic13", 256),
    ("ic08", 256),
    ("ic14", 512),
    ("ic09", 512),
    ("ic10", 1024),
)
# The editor's boot splash is shown unscaled on a background of the same dark
# grey as the icon's square, so only the dovetail reads.
SPLASH_SIZE = (800, 600)
SPLASH_MARK = 192
SPLASH_BG = "#202123"

DESKTOP_ENTRY = """\
[Desktop Entry]
Name=Joinery
GenericName=Game editor
Comment=Make games by directing the tools that make them
Exec=Joinery %f
Icon=joinery
Terminal=false
Type=Application
Categories=Development;IDE;Graphics;
StartupWMClass=Godot
"""


def rsvg() -> str:
    tool = shutil.which("rsvg-convert")
    if tool is None:
        raise SystemExit(
            "rsvg-convert is not installed (librsvg " + RSVG_VERSION + ")."
        )
    version = subprocess.run(
        [tool, "--version"], check=True, capture_output=True, text=True
    ).stdout
    found = re.search(r"(\d+\.\d+\.\d+)", version)
    if found is None or found.group(1) != RSVG_VERSION:
        raise SystemExit(
            f"rsvg-convert {RSVG_VERSION} is pinned; found {version.strip()}. "
            "Another version may anti-alias differently and change the hashes."
        )
    return tool


def render(tool: str, svg: Path, width: int, height: int | None = None) -> bytes:
    """Rasterise one SVG to PNG bytes at an exact pixel size."""
    return subprocess.run(
        [tool, "--width", str(width), "--height", str(height or width), str(svg)],
        check=True,
        capture_output=True,
    ).stdout


def _body(svg: Path) -> tuple[str, str]:
    """The viewBox and inner markup of a hand-written SVG."""
    text = svg.read_text()
    view = re.search(r'viewBox="([^"]+)"', text)
    inner = re.search(r"<svg[^>]*>(.*)</svg>", text, re.DOTALL)
    if view is None or inner is None:
        raise ValueError(f"{svg.name} needs a viewBox and one root <svg>.")
    return view.group(1), inner.group(1)


def placed(
    svg: Path, canvas: tuple[int, int], box: tuple[int, int, int], bg: str = ""
) -> str:
    """An SVG of the given canvas with `svg` drawn into box = (x, y, size)."""
    view, inner = _body(svg)
    x, y, size = box
    fill = f'<rect width="{canvas[0]}" height="{canvas[1]}" fill="{bg}"/>' if bg else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas[0]}" height="{canvas[1]}">'
        f'{fill}<svg x="{x}" y="{y}" width="{size}" height="{size}" viewBox="{view}">'
        f"{inner}</svg></svg>"
    )


def ico(images: dict[int, bytes]) -> bytes:
    """A Windows .ico holding one PNG per size (Vista and later read PNG entries)."""
    sizes = sorted(images)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = len(header) + 16 * len(sizes)
    entries, blobs = b"", b""
    for size in sizes:
        data = images[size]
        side = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def icns(slots: list[tuple[str, bytes]]) -> bytes:
    """A macOS .icns of PNG slots (read natively since macOS 10.7)."""
    body = b"".join(
        kind.encode("ascii") + struct.pack(">I", 8 + len(data)) + data
        for kind, data in slots
    )
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def build(tool: str, scratch: Path) -> dict[str, bytes]:
    """Every output, keyed by its path: dist/... for packaging, godot-src/... for the fork."""

    def from_markup(
        name: str, markup: str, width: int, height: int | None = None
    ) -> bytes:
        path = scratch / name
        path.write_text(markup)
        return render(tool, path, width, height)

    def app(size: int) -> bytes:
        return render(tool, SMALL if size <= 32 else APP, size)

    def macos(size: int) -> bytes:
        if size <= 32:  # the small mark already fills its pixels; insetting blurs it
            return render(tool, SMALL, size)
        inset = placed(
            APP, (1024, 1024), (MACOS_INSET, MACOS_INSET, 1024 - 2 * MACOS_INSET)
        )
        return from_markup("macos.svg", inset, size)

    files: dict[str, bytes] = {}
    for size in LINUX_SIZES:
        files[f"dist/joinery-{size}.png"] = app(size)
    for size in MONO_SIZES:
        files[f"dist/joinery-mono-{size}.png"] = render(tool, MONO, size)
    files["dist/joinery.ico"] = ico({size: app(size) for size in ICO_SIZES})
    files["dist/joinery.icns"] = icns(
        [(kind, macos(size)) for kind, size in ICNS_SLOTS]
    )
    files["dist/joinery.desktop"] = DESKTOP_ENTRY.encode()

    w, h = SPLASH_SIZE
    splash = placed(
        APP, SPLASH_SIZE, ((w - SPLASH_MARK) // 2, (h - SPLASH_MARK) // 2, SPLASH_MARK)
    )
    files["godot-src/main/splash_editor.png"] = from_markup("splash.svg", splash, w, h)
    files["godot-src/main/app_icon_editor.png"] = app(256)
    files["godot-src/main/app_icon_editor_macos.png"] = macos(256)
    files["godot-src/platform/windows/godot.ico"] = files["dist/joinery.ico"]
    # The Project Manager's mark beside the wordmark: the full icon as an editor
    # icon (an SVG the editor rasterises at its own scale), 24 px at 100 %.
    files["godot-src/editor/icons/JoineryLogo.svg"] = (
        APP.read_text()
        .replace('width="1024" height="1024"', 'width="24" height="24"', 1)
        .encode()
    )
    return files


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lock_for(files: dict[str, bytes]) -> dict[str, object]:
    return {
        "origin": "tool-created",
        "recipe": {
            "script": "release/icon/build_icons.py",
            "tool": "rsvg-convert " + RSVG_VERSION,
            "sources": {svg.name: digest(svg.read_bytes()) for svg in SOURCES},
        },
        "outputs": {name: digest(data) for name, data in sorted(files.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0] if __doc__ else None
    )
    parser.add_argument(
        "--godot-src", type=Path, help="Also install the fork's icons here."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Rebuild in memory and compare with icons.lock.json.",
    )
    args = parser.parse_args()
    tool = rsvg()
    with tempfile.TemporaryDirectory() as scratch:
        files = build(tool, Path(scratch))
    lock = lock_for(files)
    if args.check:
        recorded = json.loads(LOCK.read_text())
        if recorded != lock:
            before: dict[str, str] = recorded.get("outputs", {})
            after = {name: digest(data) for name, data in files.items()}
            changed = sorted(
                n for n in before.keys() | after.keys() if before.get(n) != after.get(n)
            )
            print(
                "icons differ from icons.lock.json: " + ", ".join(changed or ["recipe"])
            )
            return 1
        print(f"{len(files)} icon outputs match icons.lock.json")
        return 0
    for name, data in files.items():
        if name.startswith("dist/"):
            target = HERE / name
        elif args.godot_src is not None:
            target = args.godot_src / name.removeprefix("godot-src/")
        else:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    LOCK.write_text(json.dumps(lock, indent=2) + "\n")
    print(
        f"wrote {len(files)} icon outputs; hashes in {LOCK.relative_to(HERE.parent.parent)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
