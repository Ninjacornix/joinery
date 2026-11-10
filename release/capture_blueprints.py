"""Take one screenshot per blueprint for the new-project cards.

Each picture is made by Godot running the blueprint itself, never drawn,
generated or upscaled by anything else:

1. Make a temporary project from the blueprint and its default features.
2. Import it, then run its main scene with
   `--write-movie <tmp>/frame.png --fixed-fps 30 --quit-after N` (in a
   window on macOS and Windows, under `xvfb-run` on Linux without a display).
3. Take the last, settled frame and shrink it with Godot's `Image.resize`.
4. Write `blueprints/<id>/capture.png` and `capture.toml`, which records how
   it was made (tool, engine version, exact command, frame index, sha256).

Run: uv run python release/capture_blueprints.py [--blueprint ID ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from joinery import blueprints
from joinery.catalog import ROOT, load_catalog
from joinery.installer import Selection
from joinery.project import create_project

PATCHED_GODOT = ROOT / "godot_patches/godot-src/bin/godot.macos.editor.arm64"
STOCK_GODOT = ROOT / ".tools/Godot.app/Contents/MacOS/Godot"
FPS = 30
FRAMES = 150
WINDOW = (1280, 720)
WIDTH = 960
#: A settled frame of a real game has many colours; a blank or black window
#: has one or two.
MIN_COLOURS = 8
_FRAME = re.compile(r"^frame(\d+)\.png$")

RESIZE_SCRIPT = """extends SceneTree
## Shrinks a captured frame with Image.resize and saves it as PNG.
## Args: -- <in.png> <out.png> <width> <height>


func _initialize() -> void:
	var args: PackedStringArray = OS.get_cmdline_user_args()
	var image: Image = Image.load_from_file(args[0])
	if image == null or image.is_empty():
		print("resize: cannot read %s" % args[0])
		quit(1)
		return
	image.convert(Image.FORMAT_RGB8)
	image.resize(int(args[2]), int(args[3]), Image.INTERPOLATE_LANCZOS)
	var colours: Dictionary = {}
	for y: int in range(0, image.get_height(), 9):
		for x: int in range(0, image.get_width(), 9):
			colours[image.get_pixel(x, y).to_html(false)] = true
	print("colours: %d" % colours.size())
	if image.save_png(args[1]) != OK:
		print("resize: cannot write %s" % args[1])
		quit(1)
		return
	print("resize: ok")
	quit(0)
"""


@dataclass(frozen=True)
class Capture:
    blueprint: str
    image: Path
    note: Path
    frame: int
    sha256: str


def find_godot(explicit: str | None) -> Path:
    for candidate in (explicit, os.environ.get("JOINERY_TEST_GODOT"), PATCHED_GODOT, STOCK_GODOT):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate)
    raise SystemExit(
        "No Godot binary found. Build the editor (godot_patches/fork.sh build), "
        "set JOINERY_TEST_GODOT, or pass --godot."
    )


def engine_version(godot: Path) -> str:
    result = subprocess.run(
        [str(godot), "--version"], capture_output=True, text=True, timeout=60, check=False
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not lines:
        raise SystemExit(f"{godot} --version failed:\n{result.stdout}{result.stderr}")
    return lines[-1]


def _env(home: Path) -> dict[str, str]:
    """A HOME of its own, so `user://` and editor settings stay out of the user's."""
    home.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "GODOT_SILENCE_ROOT_WARNING": "1",
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / "share"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
    }


def _display_prefix() -> list[str]:
    """`xvfb-run` on Linux when there is no display; nothing elsewhere."""
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        if shutil.which("xvfb-run") is None:
            raise SystemExit("Linux without a display needs xvfb-run to capture frames.")
        return ["xvfb-run", "-a", "-s", f"-screen 0 {WINDOW[0]}x{WINDOW[1]}x24"]
    return []


def _run(args: Sequence[str], env: dict[str, str], timeout: float) -> str:
    result = subprocess.run(
        list(args), capture_output=True, text=True, timeout=timeout, env=env, check=False
    )
    output = result.stdout + result.stderr
    if result.returncode != 0 or "SCRIPT ERROR" in output:
        tail = "\n".join(output.splitlines()[-40:])
        raise RuntimeError(f"{' '.join(args)}\nexit {result.returncode}\n{tail}")
    return output


def _shown(args: Sequence[str], tmp: Path, godot: Path) -> str:
    """The command as recorded: the temporary folder reads `<tmp>`, the binary
    is named relative to the repository when it lives there."""
    shown: list[str] = []
    for arg in args:
        if arg == str(godot):
            arg = godot.relative_to(ROOT).as_posix() if godot.is_relative_to(ROOT) else godot.name
        elif str(tmp) in arg:
            # Paths under the temporary folder read the same on every system.
            arg = arg.replace(str(tmp), "<tmp>").replace(os.sep, "/")
        shown.append(arg)
    return " ".join(shown)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def note_text(fields: dict[str, object]) -> str:
    lines = [
        "# How capture.png was made: Godot ran this blueprint and wrote its",
        "# frames (--write-movie); the last frame was shrunk with Image.resize.",
        "# Made by release/capture_blueprints.py. Nothing else touched it.",
    ]
    lines += [f"{key} = {_toml_value(value)}" for key, value in fields.items()]
    return "\n".join(lines) + "\n"


def capture(
    blueprint_id: str, godot: Path, version: str, work: Path, frames: int = FRAMES
) -> Capture:
    blueprint = next(b for b in blueprints.list_blueprints() if b.id == blueprint_id)
    tmp = work / blueprint_id
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    project = tmp / "game"
    features = list(blueprint.default_features)
    create_project(
        project,
        blueprint.name,
        Selection(["godot"], []),
        load_catalog(),
        blueprint=blueprint_id,
        features=features,
    )
    env = _env(tmp / "home")
    _run([str(godot), "--headless", "--path", str(project), "--import"], env, 300)

    movie = tmp / "movie"
    movie.mkdir()
    command = [
        str(godot), "--path", str(project),
        "--resolution", f"{WINDOW[0]}x{WINDOW[1]}",
        "--write-movie", str(movie / "frame.png"),
        "--fixed-fps", str(FPS), "--quit-after", str(frames),
        blueprint.main_scene,
        "--", "--autostart", f"--settings-path={tmp / 'settings.cfg'}",
    ]
    _run([*_display_prefix(), *command], env, 300)
    numbered = sorted(
        (int(match.group(1)), path)
        for path in movie.iterdir()
        if (match := _FRAME.match(path.name))
    )
    if not numbered:
        raise RuntimeError(f"{blueprint_id}: Godot wrote no frames to {movie}")
    frame, settled = numbered[-1]

    resizer = tmp / "resizer"
    resizer.mkdir()
    (resizer / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (resizer / "resize.gd").write_text(RESIZE_SCRIPT, encoding="utf-8")
    width, height = _png_size(settled)
    out_height = round(WIDTH * height / width)
    shrunk = tmp / "capture.png"
    resize = [
        str(godot), "--headless", "--path", str(resizer), "--script", "res://resize.gd",
        "--", str(settled), str(shrunk), str(WIDTH), str(out_height),
    ]
    output = _run(resize, env, 120)
    if "resize: ok" not in output:
        raise RuntimeError(f"{blueprint_id}: resize failed\n{output}")
    counted = re.search(r"colours: (\d+)", output)
    colours = int(counted.group(1)) if counted else 0
    if colours < MIN_COLOURS:
        raise RuntimeError(
            f"{blueprint_id}: frame {frame} has only {colours} colours; the window "
            "probably did not render. Run on a machine with a display."
        )

    data = shrunk.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    target = blueprint.dir / blueprints.CAPTURE_IMAGE
    note = blueprint.dir / blueprints.CAPTURE_NOTE
    target.write_bytes(data)
    note.write_text(
        note_text(
            {
                "origin": "tool-created",
                "tool": "godot",
                "engine_version": version,
                "blueprint": blueprint_id,
                "blueprint_version": blueprint.version,
                "features": features,
                "command": _shown(command, tmp, godot),
                "frame": frame,
                "frames": frames,
                "fixed_fps": FPS,
                "source_size": [width, height],
                "resize": f"Image.resize({WIDTH}, {out_height}, INTERPOLATE_LANCZOS)",
                "resize_command": _shown(resize, tmp, godot),
                "size": [WIDTH, out_height],
                "sha256": digest,
            }
        ),
        encoding="utf-8",
    )
    return Capture(blueprint_id, target, note, frame, digest)


def _png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError(f"{path} is not a PNG")
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--blueprint", action="append", help="only this blueprint (repeatable)")
    parser.add_argument("--godot", help="the Godot binary (default: the built engine)")
    parser.add_argument("--frames", type=int, default=FRAMES, help="frames to run before the capture")
    parser.add_argument("--keep", action="store_true", help="keep the temporary projects")
    args = parser.parse_args(argv)

    godot = find_godot(args.godot)
    version = engine_version(godot)
    known = [b.id for b in blueprints.list_blueprints()]
    wanted = args.blueprint or known
    unknown = sorted(set(wanted) - set(known))
    if unknown:
        parser.error(f"unknown blueprint: {', '.join(unknown)}")
    work = Path(tempfile.mkdtemp(prefix="joinery-capture-"))
    failed = 0
    try:
        for blueprint_id in wanted:
            try:
                done = capture(blueprint_id, godot, version, work, args.frames)
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                failed += 1
                print(f"{blueprint_id}: failed\n{exc}", file=sys.stderr)
                continue
            print(f"{blueprint_id}: frame {done.frame} -> {done.image.relative_to(ROOT)}")
    finally:
        if args.keep:
            print(f"Kept the projects in {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
