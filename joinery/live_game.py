"""Look into a running game, and play it with timed input (``inspect_game``, ``drive_game``).

The game is the one this agent's gateway started with Godot's ``run_project``: its
runtime inspector (the Godot MCP's ``game_*`` tools, ADR 0029) answers on the game's own
socket. Two tools sit on top of it, the same for every provider:

* ``inspect_game`` only reads: the scene tree, one node's details and properties, the
  visible UI and a screenshot (saved under ``.joinery/live/``; context, never an asset).
  Every role may call it, read-only ones too.
* ``drive_game`` plays a sequence of timed inputs: an input action, a key, a gamepad
  button or a stick axis is tapped (pressed, held for its seconds, released), held down
  until a later release, or released, with waits between. Whatever is still held when the
  sequence ends is released, and an error mid-way releases everything too. The lead and
  workers may drive; a read-only role only where the project lets it
  (``[agents] game_input_roles``, ``joinery.roles.may_drive``).

Nothing here changes a file; the game's state is the running game's, gone when it stops.
Timing is wall-clock through the inspector: each event lands within a frame or two of
its planned time, and the result says how far off the worst one was.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp.types import CallToolResult, ImageContent, TextContent

#: The inspector's reads; nothing else is reachable through inspect_game.
GAME_READS = frozenset(
    {
        "game_get_scene_tree",
        "game_get_node_info",
        "game_get_property",
        "game_get_ui",
        "game_screenshot",
    }
)
#: The inspector's input; nothing else is reachable through drive_game.
GAME_INPUTS = frozenset(
    {"game_key_press", "game_key_hold", "game_key_release", "game_gamepad"}
)
FOLDER = Path(".joinery") / "live"
#: Screenshots kept; older ones are removed.
KEEP_SHOTS = 20
MAX_STEPS = 64
MAX_SECONDS = 30.0
MAX_STEP_SECONDS = 10.0
#: A tap with no seconds holds this long: about six frames.
TAP_SECONDS = 0.1
MAX_PROPERTIES = 12
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_:/.]{0,80}$")
_NODE = re.compile(r"^[^\x00-\x1f]{1,300}$")
_KEY = re.compile(r"^[A-Za-z0-9_+ ]{1,32}$")

Call = Callable[[str, dict[str, Any]], Awaitable[CallToolResult]]
Sleep = Callable[[float], Awaitable[None]]
NO_GAME = (
    "No game is running for this agent. Start it with call_tool godot run_project "
    "(the lead or a task), then look or play; stop_project ends it."
)
NOTE = (
    "This is the running game, not the project: nothing here is saved, and screenshots "
    "are context only, never assets."
)


def _text(result: CallToolResult) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


def _value(result: CallToolResult) -> Any:
    """A result as data: its structured content, else its text read as JSON or kept."""
    if result.structured_content is not None:
        return result.structured_content
    text = _text(result)
    try:
        return json.loads(text)
    except ValueError:
        return text


def _check(result: CallToolResult, what: str) -> CallToolResult:
    if result.is_error:
        raise ValueError(f"The running game couldn't {what}: {_text(result)[:300]}")
    return result


def _picture(result: CallToolResult) -> bytes:
    """The PNG a screenshot result carries: an image part, or base64 in its text."""
    for part in result.content:
        if isinstance(part, ImageContent):
            return base64.b64decode(part.data)
    value = _value(result)
    candidates: list[Any] = [value]
    if isinstance(value, dict):
        candidates = [
            value.get(k) for k in ("image", "data", "screenshot", "base64", "png")
        ]
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        data = (
            candidate.split(",", 1)[1] if candidate.startswith("data:") else candidate
        )
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            continue
        if raw.startswith(b"\x89PNG"):
            return raw
    raise ValueError("The running game's screenshot had no picture in it.")


def save_shot(project: Path, png: bytes) -> Path:
    folder = project / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    shots = sorted(folder.glob("game-*.png"))
    for old in shots[: max(0, len(shots) - KEEP_SHOTS + 1)]:
        old.unlink(missing_ok=True)
    path = (
        folder
        / f"game-{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 10**6:06d}.png"
    )
    path.write_bytes(png)
    return path


def inspect_params(params: dict[str, Any]) -> dict[str, Any]:
    """``inspect_game``'s arguments, checked: {tree, node, properties, ui, screenshot}."""
    node = str(params.get("node", "") or "")
    if node and not _NODE.match(node):
        raise ValueError(
            "node is a node path in the running game, like /root/Main/Player."
        )
    properties = params.get("properties") or []
    if (
        not isinstance(properties, list)
        or len(properties) > MAX_PROPERTIES
        or not all(isinstance(p, str) and _NAME.match(p) for p in properties)
    ):
        raise ValueError(f"properties is up to {MAX_PROPERTIES} property names.")
    if properties and not node:
        raise ValueError("Name the node whose properties to read.")
    return {
        "tree": bool(params.get("tree", not node)),
        "node": node,
        "properties": list(properties),
        "ui": bool(params.get("ui", False)),
        "screenshot": bool(params.get("screenshot", False)),
    }


async def inspect(call: Call, project: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Read the running game: what ``params`` asks for, nothing else."""
    wanted = inspect_params(params)
    data: dict[str, Any] = {}
    if wanted["tree"]:
        data["tree"] = _value(
            _check(await call("game_get_scene_tree", {}), "list its scene tree")
        )
    if wanted["node"]:
        info = await call("game_get_node_info", {"nodePath": wanted["node"]})
        data["node"] = _value(_check(info, f"find {wanted['node']}"))
        values: dict[str, Any] = {}
        for name in wanted["properties"]:
            got = await call(
                "game_get_property", {"nodePath": wanted["node"], "property": name}
            )
            values[name] = _value(_check(got, f"read {name} on {wanted['node']}"))
        if values:
            data["properties"] = values
    if wanted["ui"]:
        data["ui"] = _value(_check(await call("game_get_ui", {}), "list its UI"))
    if wanted["screenshot"]:
        shot = _check(await call("game_screenshot", {}), "take a screenshot")
        data["screenshot"] = str(save_shot(project, _picture(shot)))
    data["note"] = NOTE
    return data


def _target(step: dict[str, Any]) -> tuple[str, Any]:
    """What a step presses: ("action", "jump"), ("key", "Space"), ("button", 0), ("axis", 1)."""
    named = [k for k in ("action", "key", "button", "axis") if step.get(k) is not None]
    if len(named) != 1:
        raise ValueError("Each input step names one of action, key, button or axis.")
    kind = named[0]
    value = step[kind]
    if kind == "action" and not (isinstance(value, str) and _NAME.match(value)):
        raise ValueError(
            "action is an input action of the game's input map, like jump."
        )
    if kind == "key" and not (isinstance(value, str) and _KEY.match(value)):
        raise ValueError("key is a key name, like Space, W or Escape.")
    if kind == "button" and not (isinstance(value, int) and 0 <= value <= 20):
        raise ValueError("button is a gamepad button index from 0 to 20.")
    if kind == "axis" and not (isinstance(value, int) and 0 <= value <= 9):
        raise ValueError("axis is a gamepad axis index from 0 to 9.")
    return kind, value


def drive_params(params: dict[str, Any]) -> list[dict[str, Any]]:
    """``drive_game``'s steps, checked; each {do, target, value, device, seconds}."""
    steps = params.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"steps is a list of 1 to {MAX_STEPS} inputs.")
    checked: list[dict[str, Any]] = []
    total = 0.0
    for raw in steps:
        if not isinstance(raw, dict):
            raise TypeError("Each step is an object.")
        do = raw.get("do")
        if do not in {"tap", "hold", "release", "wait"}:
            raise ValueError("A step does tap, hold, release or wait.")
        seconds = raw.get("seconds")
        if seconds is None:
            seconds = TAP_SECONDS if do == "tap" else 0.0
        if (
            not isinstance(seconds, (int, float))
            or isinstance(seconds, bool)
            or not 0 <= seconds <= MAX_STEP_SECONDS
        ):
            raise ValueError(f"seconds is between 0 and {MAX_STEP_SECONDS:g}.")
        if do == "wait" and seconds <= 0:
            raise ValueError("A wait lasts some seconds.")
        step: dict[str, Any] = {"do": do, "seconds": float(seconds)}
        if do != "wait":
            step["kind"], step["target"] = _target(raw)
            value = raw.get("value", 1.0)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not -1 <= value <= 1
            ):
                raise ValueError("value is an axis position from -1 to 1.")
            device = raw.get("device", 0)
            if (
                not isinstance(device, int)
                or isinstance(device, bool)
                or not 0 <= device <= 7
            ):
                raise ValueError("device is a gamepad index from 0 to 7.")
            step["value"] = float(value)
            step["device"] = device
        total += step["seconds"]
        checked.append(step)
    if total > MAX_SECONDS:
        raise ValueError(
            f"A sequence lasts at most {MAX_SECONDS:g} seconds ({total:g} asked)."
        )
    return checked


def _event(
    kind: str, target: Any, down: bool, value: float, device: int
) -> tuple[str, dict[str, Any]]:
    """The inspector call that puts ``target`` down or up."""
    if kind in {"action", "key"}:
        return ("game_key_hold" if down else "game_key_release"), {kind: target}
    if kind == "button":
        return "game_gamepad", {
            "type": "button",
            "index": target,
            "value": 1 if down else 0,
            "device": device,
        }
    return "game_gamepad", {
        "type": "axis",
        "index": target,
        "value": value if down else 0.0,
        "device": device,
    }


def _name(kind: str, target: Any) -> str:
    return f"{kind} {target}"


async def drive(
    call: Call,
    params: dict[str, Any],
    *,
    sleep: Sleep = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Play the checked steps; returns {sent, planned_seconds, took_seconds, late_ms_max}."""
    steps = drive_params(params)
    held: dict[tuple[str, Any, int], dict[str, Any]] = {}
    sent: list[dict[str, Any]] = []
    start = clock()
    planned = 0.0

    async def send(step: dict[str, Any], down: bool, at: float) -> None:
        tool, arguments = _event(
            step["kind"], step["target"], down, step["value"], step["device"]
        )
        result = await call(tool, arguments)
        now = clock() - start
        if result.is_error:
            raise ValueError(
                f"The running game refused {_name(step['kind'], step['target'])}: "
                f"{_text(result)[:300]}"
            )
        sent.append(
            {
                "input": _name(step["kind"], step["target"]),
                "down": down,
                "planned": round(at, 3),
                "at": round(now, 3),
            }
        )

    async def wait_until(at: float) -> None:
        delay = at - (clock() - start)
        if delay > 0:
            await sleep(delay)

    try:
        for step in steps:
            key = (step.get("kind", ""), step.get("target"), step.get("device", 0))
            if step["do"] == "wait":
                planned += step["seconds"]
                await wait_until(planned)
                continue
            if step["do"] == "release":
                await send(step, False, planned)
                held.pop(key, None)
                planned += step["seconds"]
                await wait_until(planned)
                continue
            await send(step, True, planned)
            if step["do"] == "hold":
                held[key] = step
                planned += step["seconds"]
                await wait_until(planned)
                continue
            planned += step["seconds"]
            await wait_until(planned)
            await send(step, False, planned)
    finally:
        # Nothing stays pressed after the sequence, whatever happened in it.
        released = []
        for step in list(held.values()):
            tool, arguments = _event(
                step["kind"], step["target"], False, 0.0, step["device"]
            )
            try:
                await call(tool, arguments)
                released.append(_name(step["kind"], step["target"]))
            except (OSError, RuntimeError, ValueError, TimeoutError):
                continue
    late = max((s["at"] - s["planned"] for s in sent), default=0.0)
    return {
        "sent": sent,
        "released_at_end": released,
        "planned_seconds": round(planned, 3),
        "took_seconds": round(clock() - start, 3),
        "late_ms_max": round(max(late, 0.0) * 1000, 1),
        "note": NOTE,
    }
