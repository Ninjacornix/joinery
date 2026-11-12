"""``joinery blockout``: check a layout, rebuild its scene, draw its plan, walk it.

The layout file is the blockout's recipe, so rebuilding replays it exactly: the same
layout always writes the same scene bytes.

    joinery blockout check design/blockouts/level.toml --project P
    joinery blockout build design/blockouts/level.toml --project P [--scene res://…]
    joinery blockout walk design/blockouts/level.toml --project P
    joinery blockout grow --sample art/reference/my_map.png --size 32x24 --seed 7
    joinery blockout grow design/blockouts/grown.toml   # grow it again from its recipe

``grow`` writes a new layout grown from the user's own example (ADR 0069); check,
build and walk then treat it like any other layout.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from joinery import blockout, playtest
from joinery.blockout import grow
from joinery.blockout.grow import GrowError
from joinery.blockout.layout import LayoutError


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=["check", "build", "walk", "grow"])
    parser.add_argument(
        "layout",
        type=Path,
        nargs="?",
        help="a layout file (TOML); for grow, a grown layout to grow again",
    )
    parser.add_argument(
        "--sample", default="", help="grow: your own example (PNG, grid or layout TOML)"
    )
    parser.add_argument("--size", default="32x24", help="grow: WxH in cells")
    parser.add_argument("--seed", type=int, default=1, help="grow: the seed")
    parser.add_argument("--name", default="grown", help="grow: the layout's name")
    parser.add_argument(
        "--mode", choices=["3d", "2d-topdown", "2d-side"], help="grow: the level's view"
    )
    parser.add_argument("--cell", type=float, help="grow: plan units per cell")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--scene", default="", help="the res:// scene to build into")
    parser.add_argument(
        "--blueprint", default="", help="check with this starting point's player"
    )
    parser.add_argument("--picture", type=Path, help="also write the plan as PNG")
    parser.add_argument("--json", action="store_true")


def _summary(built: blockout.Built) -> dict[str, Any]:
    reach = built.checked.reach
    return {
        "ok": built.ok,
        "scene": built.scene,
        "layout_file": built.layout_file,
        "counts": built.counts(),
        "fact": reach.fact() if reach is not None else "",
        "problems": [p.as_dict() for p in built.checked.problems],
    }


def _size(text: str) -> tuple[int, int]:
    width, _, height = text.lower().partition("x")
    try:
        return int(width), int(height)
    except ValueError:
        raise GrowError(
            f"--size {text}: write it as WIDTHxHEIGHT, such as 32x24."
        ) from None


def _grow(args: argparse.Namespace, project: Path) -> int:
    """``joinery blockout grow``: grow a layout from a sample, or again from its recipe."""
    try:
        if args.layout is not None:
            path: Path = args.layout
            if not path.is_absolute() and not path.is_file():
                path = project / path
            made = grow.regrow(
                project,
                path.read_text(encoding="utf-8"),
                blueprint=args.blueprint or None,
            )
        else:
            if not args.sample:
                raise GrowError("grow needs --sample (your own example) or a layout.")
            width, height = _size(args.size)
            made = grow.make(
                project,
                args.sample,
                width=width,
                height=height,
                seed=args.seed,
                name=args.name,
                mode=args.mode,
                blueprint=args.blueprint or None,
                scene=args.scene or None,
                cell=args.cell,
            )
    except (OSError, GrowError, LayoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    built = made.built
    text = built.files[built.layout_file]
    target = project / built.layout_file
    same = target.is_file() and target.read_bytes() == text
    if args.layout is None or not same:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text)
    if args.picture:
        args.picture.write_bytes(built.plan_png)
    data = _summary(built)
    recipe = made.grown.layout.grown
    assert recipe is not None
    data["grown"] = recipe.model_dump()
    data["same_as_before"] = same
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(
            f"Grew {built.layout_file} ({recipe.size[0]}x{recipe.size[1]} cells) from "
            f"{recipe.sample} at seed {recipe.seed} (try {recipe.tries})."
        )
        if data["fact"]:
            print(data["fact"])
        if args.layout is not None:
            print(
                "Same bytes as before." if same else "Differs from the file; rewritten."
            )
    return 0 if built.ok else 1


def main(args: argparse.Namespace) -> int:
    project: Path = args.project.resolve()
    if args.action == "grow":
        return _grow(args, project)
    if args.layout is None:
        print(f"blockout {args.action} needs a layout file.", file=sys.stderr)
        return 2
    layout_path: Path = args.layout
    if not layout_path.is_absolute() and not layout_path.is_file():
        layout_path = project / layout_path
    try:
        built = blockout.make(
            project,
            layout_path.read_text(encoding="utf-8"),
            scene=args.scene or None,
            blueprint=args.blueprint or None,
        )
    except (OSError, LayoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.picture:
        args.picture.write_bytes(built.plan_png)
    data = _summary(built)
    if built.ok and args.action == "build":
        for relative, content in built.files.items():
            target = project / relative
            if relative == blockout.SCRIPT_FILE and target.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        data["written"] = sorted(built.files)
    if built.ok and args.action == "walk":
        data["walk"] = _walk(project, built)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        for problem in data["problems"]:
            print(f"[{problem['severity']}] {problem['text']}")
        if data["fact"]:
            print(data["fact"])
        for target in data.get("walk", {}).get("route", []):
            state = "reached" if target.get("reached") else "missed"
            print(f"- {target.get('kind')} {target.get('id')}: {state} in Godot")
        for written in data.get("written", []):
            print(f"Wrote {written}")
    return 0 if built.ok else 1


def _walk(project: Path, built: blockout.Built) -> dict[str, Any]:
    """Walk the built scene in Godot (it must be in the project already)."""
    with tempfile.TemporaryDirectory(prefix="joinery-walk-") as folder:
        route = Path(folder) / "route.json"
        route.write_text(json.dumps(blockout.route(built)), encoding="utf-8")
        report = playtest.run(
            playtest.Options(
                project=project,
                seconds=min(25.0 * max(len(built.checked.world.markers), 1) + 10, 150),
                seed=1,
                scene=built.scene,
                route=route,
                out=Path(folder) / "run",
            )
        )
    return {"status": report.get("status"), "route": report.get("route", [])}
