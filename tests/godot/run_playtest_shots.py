"""Playtest screenshots: a seeded run, replayed in a window that never takes focus.

    uv run python tests/godot/run_playtest_shots.py [--blueprint ID] [--keep DIR]

Makes a game from a blueprint (third-person-3d by default), plays it headless for 30 s
with a fixed seed, then lets ``joinery.playtest`` replay that seed in the Joinery editor
binary with ``JOINERY_BACKGROUND=1`` to save a picture of each notable moment. Checks the
pictures exist and show a real frame (many colours, not a blank window). ``--keep`` copies
them out for a look.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from joinery import playtest
from tests.blueprints.godot_run import import_project, make_project

#: A rendered game frame has many colours; a blank or black window has one or two.
MIN_COLOURS = 8


def colours(path: Path) -> int:
    """Distinct colours on a coarse grid of a PNG, read with Godot-free Python."""
    import struct
    import zlib

    data = path.read_bytes()
    width, height = struct.unpack(">II", data[16:24])
    depth, kind = data[24], data[25]
    chunks = b""
    at = 8
    while at < len(data):
        length = struct.unpack(">I", data[at : at + 4])[0]
        tag = data[at + 4 : at + 8]
        if tag == b"IDAT":
            chunks += data[at + 8 : at + 8 + length]
        at += 12 + length
    raw = zlib.decompress(chunks)
    channels = {2: 3, 6: 4}.get(kind, 3)
    stride = width * channels * depth // 8
    seen: set[bytes] = set()
    for y in range(0, height, 9):
        row = raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)]
        for x in range(0, width, 9):
            seen.add(row[x * channels : x * channels + 3])
    return len(seen)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blueprint", default="third-person-3d")
    parser.add_argument("--keep", type=Path)
    args = parser.parse_args()
    os.environ["JOINERY_BACKGROUND"] = "1"
    folder = Path(tempfile.mkdtemp(prefix="joinery playtest shots "))
    try:
        project = make_project(folder, args.blueprint)
        import_project(project)
        report = playtest.run(
            playtest.Options(project=project, seconds=30, seed=7, screenshots=True)
        )
        shots = [Path(p) for p in report["screenshots"]]
        print(playtest.playtest_report.summary_line(report))
        print("screenshots:", len(shots), report.get("screenshots_note", ""))
        problems = []
        if not shots:
            problems.append("no screenshots")
        for shot in shots:
            count = colours(shot)
            print(f"  {shot.name}: {count} colours")
            if count < MIN_COLOURS:
                problems.append(f"{shot.name} looks blank ({count} colours)")
        if args.keep:
            args.keep.mkdir(parents=True, exist_ok=True)
            for shot in shots:
                shutil.copy2(shot, args.keep / shot.name)
        if problems:
            print("playtest shots: FAILED:", "; ".join(problems))
            return 1
        print("playtest shots: ok")
        return 0
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
