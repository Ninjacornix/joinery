"""The ``blockout`` tool: check a layout, stage its scene, walk it in Godot.

Shared by every agent through the gateway RPC method ``blockout`` (ADR 0043). A layout
with problems changes nothing and comes back with the problems to fix and a picture
of the plan. A sound one is written into the turn's staged change: the layout file,
the scene and (once per project) the level script. Then the playtest bot walks the
staged copy from the spawn to every goal in stock Godot, and the review gets the
outcome as its first fact and the blockout's plan beside the user's sketch.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from joinery import blockout, chat_attachments, playtest
from joinery.blockout import plan as plan_picture
from joinery.blockout.layout import LayoutError

if TYPE_CHECKING:
    from joinery.gateway import ProjectGateway

#: Where the agent's check pictures go (kept out of git and Godot's import).
CHECKS = ".joinery/blockouts"
PREVIEWS = ".joinery/previews"
FIX = "Fix every error in the layout, then call blockout again with the whole layout."
NEXT = (
    "The blockout is staged. Call submit_for_review with a short summary and these "
    "facts; the user sees the plan beside the sketch."
)
#: Seconds of game time per target for the walk, and the most it may take.
WALK_SECONDS = 25.0
WALK_LIMIT = 150.0

Walker = Any


def _player_scene(root: Path) -> bool:
    """The game spawns a player into levels (the core's ``game/hooks/player_scene``)."""
    try:
        text = (root / "project.godot").read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        if line.startswith(("hooks/player_scene=", "game/hooks/player_scene=")):
            return line.split("=", 1)[1].strip().strip('"') != ""
    return False


def _sketch(gateway: ProjectGateway, asked: str) -> str:
    """The sketch to show beside the blockout: the one named, else the prompt's first."""
    candidates = [asked] if asked else list(gateway.attachments)
    for candidate in candidates:
        try:
            (path,) = chat_attachments.validate(gateway.project, [candidate])
        except (TypeError, ValueError):
            continue
        return path
    return ""


async def _walk(
    stage: Path, built: blockout.Built, progress: Any
) -> dict[str, Any] | None:
    """The playtest bot's walk of the staged blockout, or None when it can't run."""
    route = blockout.route(built)
    if not route.get("targets") or not _player_scene(stage):
        return None
    try:
        playtest.godot_binary()
    except (OSError, ValueError):
        return None
    out = Path(tempfile.mkdtemp(prefix="joinery-walk-"))
    try:
        route_file = out / "route.json"
        route_file.write_text(json.dumps(route), encoding="utf-8")
        seconds = min(WALK_SECONDS * len(route["targets"]) + 10, WALK_LIMIT)
        progress("Walking the blockout in Godot…")
        report = await playtest.run_async(
            playtest.Options(
                project=stage,
                seconds=seconds,
                seed=1,
                scene=built.scene,
                route=route_file,
                out=out / "run",
            )
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return {"ran": False, "error": str(exc)}
    finally:
        shutil.rmtree(out, ignore_errors=True)
    return {
        "ran": True,
        "status": report.get("status", ""),
        "targets": report.get("route", []),
        "errors": [
            f"{item['severity']} {item['title']}: {item['detail']}"
            for item in report.get("items", [])
            if item.get("severity") in {"critical", "high"}
        ][:5],
    }


def _fact(reached: int, total: int) -> str:
    noun = "goal" if total == 1 else "goals"
    return f"Walkable: spawn reaches {reached} of {total} {noun}"


async def tool(gateway: ProjectGateway, params: dict[str, Any]) -> dict[str, Any]:
    """Check, stage and walk one layout; see the module docstring."""
    if gateway.turn is not None:
        gateway.turn.admit_mutation()
    scene = params.get("scene") or None
    blueprint = params.get("blueprint") or None
    if scene is not None and not isinstance(scene, str):
        raise ValueError("scene is a res:// path.")
    if blueprint is not None and not isinstance(blueprint, str):
        raise ValueError("blueprint is a starting point id, such as third-person-3d.")
    try:
        built = blockout.make(
            gateway.project, params.get("layout"), scene=scene, blueprint=blueprint
        )
    except LayoutError as exc:
        return {"status": "problems", "problems": exc.problems, "next": FIX}
    checks = gateway.project / CHECKS
    checks.mkdir(parents=True, exist_ok=True)
    picture = checks / f"{built.layout.name}.png"
    picture.write_bytes(built.plan_png)
    problems = [p.as_dict() for p in built.checked.problems]
    reach = built.checked.reach
    if not built.ok:
        return {
            "status": "problems",
            "problems": [p for p in problems if p["severity"] == "error"],
            "warnings": [p["text"] for p in problems if p["severity"] == "warning"],
            "picture": str(picture),
            "next": FIX,
        }
    from joinery.turn import scene_route

    if scene_route(built.scene, gateway.editor_context) == "live":
        raise ValueError(
            f"{built.scene} is open in the editor; close it there, or build the blockout "
            "into another scene."
        )
    async with gateway.call_lock:
        tx = gateway.staging()
        tx.note_tool("blockout")
        previous = gateway.project / built.layout_file
        before: bytes | None = None
        if previous.is_file():
            try:
                old = blockout.make(
                    gateway.project, previous.read_text(encoding="utf-8")
                )
                before = old.plan_png
            except (LayoutError, OSError, ValueError):
                before = None
        for relative, data in built.files.items():
            target = tx.stage / relative
            if relative == blockout.SCRIPT_FILE and target.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if gateway.claims.contended():
            gateway.claim_stage(strict=True)
        previews = tx.stage / PREVIEWS
        previews.mkdir(parents=True, exist_ok=True)
        after_png = previews / f"blockout-{built.layout.name}.png"
        walked = await _walk(tx.stage, built, gateway.progress)
        assert reach is not None
        total = len(reach.targets)
        warnings = [p["text"] for p in problems if p["severity"] == "warning"]
        if walked and walked.get("ran"):
            results = walked.get("targets", [])
            reached = sum(1 for r in results if r.get("reached"))
            for result in results:
                if not result.get("reached"):
                    warnings.append(
                        f"In Godot the player didn't reach {result.get('kind')} "
                        f"{result.get('id')} (closest {result.get('closest')}); check the "
                        "way there is wide and low enough, or move it."
                    )
            warnings.extend(walked.get("errors", []))
            fact = _fact(reached, total)
            how = "walked in Godot by the playtest bot"
        else:
            fact = _fact(reach.reached, total)
            how = (
                "checked on the plan (Godot didn't run: "
                + (str(walked.get("error")) if walked else "no player or no Godot")
                + ")"
            )
        after_png.write_bytes(plan_picture.render(built.checked.world, reach))
        item: dict[str, Any] = {
            "path": built.layout_file,
            "media": "image",
            "label": "Blockout",
            "after": str(after_png),
        }
        if before is not None:
            before_png = previews / f"blockout-{built.layout.name}-before.png"
            before_png.write_bytes(before)
            item["before"] = str(before_png)
        sketch = _sketch(gateway, str(params.get("sketch", "") or ""))
        if sketch:
            item["reference"] = sketch
        counts = built.counts()
        gateway.review_extras[tx.id] = {"facts": [fact], "media": [item]}
    return {
        "status": "staged",
        "scene": built.scene,
        "layout_file": built.layout_file,
        "counts": counts,
        "walk": {
            "fact": fact,
            "how": how,
            "targets": (walked or {}).get("targets")
            or [
                {"id": t.id, "kind": t.kind, "reached": t.reached}
                for t in reach.targets
            ],
        },
        "facts": [fact],
        "warnings": warnings,
        "picture": str(picture),
        "next": NEXT,
    }
