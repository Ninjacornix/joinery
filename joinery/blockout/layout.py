"""The blockout layout: a typed, deterministic plan of a level (ADR 0043).

A layout is the source of truth for a greybox level. It lives in the project as
``design/blockouts/<name>.toml`` and Joinery builds the scene from it, the same bytes
every time. Coordinates are a plan seen from above (3D and top-down 2D) or from the
side (side-view 2D): ``x`` grows to the right (east), ``y`` grows down the page
(south in a plan, down in a side view). In 3D, plan ``x, y`` become Godot ``X, Z``
and heights are ``Y``, in metres.

Plan coordinates and sizes are multiplied by ``scale`` and are in ``units``:
metres in 3D, tiles or pixels in 2D. Heights (``floor``, ``top``, ``wall_height``,
door heights) are always metres and only matter in 3D.
"""

from __future__ import annotations

import math
import re
import tomllib
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError

Mode = Literal["3d", "2d-topdown", "2d-side"]
Edge = Annotated[str, Field(pattern=r"^(north|south|east|west|e[0-9]{1,2})$")]
Ident = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")]
Number = FiniteFloat
#: [x, y]
Point = Annotated[list[Number], Field(min_length=2, max_length=2)]
#: [x, y, width, height]
Rect = Annotated[list[Number], Field(min_length=4, max_length=4)]
Direction = Literal["north", "south", "east", "west"]
MarkerKind = Literal["goal", "pickup", "coin", "checkpoint", "hazard"]
#: Marker kinds the player should be able to walk to from the spawn.
TARGETS: tuple[str, ...] = ("goal", "pickup", "coin", "checkpoint")
#: Rect edge names in the order a rect room lists them.
RECT_EDGES: tuple[str, ...] = ("north", "east", "south", "west")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Room(_Strict):
    """A room: a rect or a polygon (corners in order). Every edge gets a wall unless
    it is listed in ``open``. Rect edges are north, east, south and west; polygon
    edges are e0 (first corner to second), e1, …"""

    id: Ident
    rect: Rect | None = None
    polygon: Annotated[list[Point], Field(min_length=3, max_length=24)] | None = None
    #: Floor height in metres (3D).
    floor: Number = 0.0
    #: Edges with no wall at all (open to a neighbour or a ledge).
    open: list[Edge] = Field(default_factory=list, max_length=24)


class Opening(_Strict):
    """A door, window or plain gap in one wall of a room."""

    room: Ident
    wall: Edge
    #: Distance along the wall from its start to the opening's centre (north and south
    #: walls start at their west end, east and west walls at their north end, polygon
    #: edges at their first corner). Omitted: the middle of the wall.
    at: Number | None = None
    #: Default: the layout's door_width (a window: window_width).
    width: Number | None = None
    kind: Literal["door", "window", "gap"] = "door"
    #: Door height in metres, or a window's top (3D). Default: door_height / 2.0.
    height: Number | None = None
    #: A window's sill height in metres (3D).
    sill: Number = 0.9


class Corridor(_Strict):
    """A straight walkway (a rect). Its two long sides get walls; its ends stay open,
    and a corridor cuts through any room wall it crosses."""

    id: Ident
    rect: Rect
    floor: Number = 0.0
    walls: bool = True


class Stairs(_Strict):
    """Stairs or a ramp over ``rect``, rising toward ``up``. Heights come from the
    rooms named in ``from_room`` (bottom) and ``to_room`` (top), or from ``bottom`` and
    ``top``. Built as a ramp the players can walk."""

    id: Ident
    rect: Rect
    up: Direction
    from_room: Ident | None = None
    to_room: Ident | None = None
    bottom: Number | None = None
    top: Number | None = None


class Platform(_Strict):
    """A solid block. 3D: a raised platform whose top is ``top`` metres up. Side-view
    2D: solid ground or a ledge. Top-down 2D: an obstacle."""

    id: Ident
    rect: Rect
    top: Number = 1.0
    thickness: Number | None = None


class Wall(_Strict):
    """A free-standing wall from ``start`` to ``end``."""

    id: Ident
    start: Point
    end: Point
    thickness: Number | None = None
    height: Number | None = None
    floor: Number = 0.0


class Spawn(_Strict):
    at: Point
    facing: Direction = "north"


class Marker(_Strict):
    """Something placed in the level: the goal, a pickup or coin, a checkpoint, or a
    hazard. Its id names its node; omitted: kind and number (coin_1)."""

    kind: MarkerKind
    at: Point
    id: Ident | None = None
    size: Number | None = None


class Layout(_Strict):
    """A whole level blockout."""

    version: Literal[1] = 1
    #: Names the file (design/blockouts/<name>.toml) and the scene.
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")] = "level"
    mode: Mode = "3d"
    #: Plan units: metres (3D), tiles or pixels (2D).
    units: Literal["m", "tiles", "px"] = "m"
    #: Pixels per tile in 2D (default: the starting point's tile).
    tile: Number | None = None
    #: Plan units per sketch unit: plan coordinates and sizes are multiplied by it.
    scale: Number = 1.0
    wall_height: Number = 3.0
    #: Default: 0.2 m in 3D, half a tile in 2D.
    wall_thickness: Number | None = None
    #: Default: 1 m in 3D, 2 tiles in 2D.
    door_width: Number | None = None
    door_height: Number = 2.2
    window_width: Number | None = None
    rooms: list[Room] = Field(default_factory=list, max_length=64)
    openings: list[Opening] = Field(default_factory=list, max_length=128)
    corridors: list[Corridor] = Field(default_factory=list, max_length=64)
    stairs: list[Stairs] = Field(default_factory=list, max_length=32)
    platforms: list[Platform] = Field(default_factory=list, max_length=128)
    walls: list[Wall] = Field(default_factory=list, max_length=64)
    spawn: Spawn | None = None
    markers: list[Marker] = Field(default_factory=list, max_length=64)


class LayoutError(ValueError):
    """A layout that can't be read: each line names a field and what is wrong."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _location(loc: tuple[int | str, ...]) -> str:
    text = ""
    for part in loc:
        text += f"[{part}]" if isinstance(part, int) else ("." if text else "") + part
    return text or "layout"


def parse(data: Any) -> Layout:
    """A layout from a dict or TOML text; raises :class:`LayoutError` with one line per
    problem, each naming the field (``rooms[1].rect``)."""
    if isinstance(data, str):
        try:
            data = tomllib.loads(data)
        except tomllib.TOMLDecodeError as exc:
            raise LayoutError([f"The layout is not valid TOML: {exc}"]) from None
    if not isinstance(data, dict):
        raise LayoutError(["The layout must be an object (a TOML document)."])
    try:
        return Layout.model_validate(data)
    except ValidationError as exc:
        problems = []
        for error in exc.errors()[:20]:
            problems.append(f"{_location(tuple(error['loc']))}: {error['msg']}")
        raise LayoutError(problems) from None


# --- Canonical TOML ------------------------------------------------------------------

#: Top-level keys in the order the file lists them.
_SCALARS = (
    "version",
    "name",
    "mode",
    "units",
    "tile",
    "scale",
    "wall_height",
    "wall_thickness",
    "door_width",
    "door_height",
    "window_width",
)
_TABLES = ("rooms", "openings", "corridors", "stairs", "platforms", "walls", "markers")


def number(value: float) -> str:
    """A number as the layout file writes it: up to 4 decimals, no trailing zeros."""
    if not math.isfinite(value):
        raise ValueError("Numbers in a layout must be finite.")
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return number(float(value))
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    raise TypeError(f"A layout can't hold {type(value).__name__}.")


def _fields(model: BaseModel) -> list[str]:
    """``key = value`` lines for the fields that differ from their defaults."""
    lines = []
    defaults = type(model).model_fields
    for key, info in defaults.items():
        value = getattr(model, key)
        if value is None or (
            not info.is_required()
            and value == info.get_default(call_default_factory=True)
        ):
            continue
        lines.append(f"{key} = {_value(value)}")
    return lines


def to_toml(layout: Layout) -> str:
    """The layout as canonical TOML: the same layout always gives the same bytes."""
    lines = [
        "# A level blockout. Joinery builds the greybox scene from this file;",
        "# edit it and rebuild (ADR 0043). Plan: x right (east), y down (south).",
    ]
    for key in _SCALARS:
        value = getattr(layout, key)
        if value is None:
            continue
        if key not in {"version", "name", "mode"} and value == Layout.model_fields[
            key
        ].get_default(call_default_factory=True):
            continue
        lines.append(f"{key} = {_value(value)}")
    if layout.spawn is not None:
        lines += ["", "[spawn]", *_fields(layout.spawn)]
    for table in _TABLES:
        for item in getattr(layout, table):
            lines += ["", f"[[{table}]]", *_fields(item)]
    return "\n".join(lines) + "\n"


def slug(text: str) -> str:
    """A layout or node name from free text ("Room B" -> "room_b")."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = "level_" + cleaned
    return cleaned[:32].rstrip("_")
