# Joinery kits: simple buildings and huts. MIT licence, see kits/LICENSE.
# A recipe joins this file after common.py, which defines Shapes, Vector, Matrix
# and the other helpers used here.
# ruff: noqa: F821

import math

SPEC = {
    "id": "hut",
    "label": "Hut",
    "summary": "A simple building: walls on a plinth, a roof, doors and windows.",
    "folder": "art/environment",
    "params": {
        "width": {"kind": "float", "default": 4.0, "min": 2.0, "max": 16.0, "step": 0.1, "unit": "m", "vary": "range", "label": "Width", "help": "Footprint along X, in metres."},
        "depth": {"kind": "float", "default": 3.5, "min": 2.0, "max": 16.0, "step": 0.1, "unit": "m", "vary": "range", "label": "Depth", "help": "Footprint along Z in Godot, in metres. The door is on the front."},
        "floors": {"kind": "int", "default": 1, "min": 1, "max": 3, "vary": "", "label": "Floors", "help": "Storeys."},
        "floor_height": {"kind": "float", "default": 2.6, "min": 2.2, "max": 4.0, "step": 0.1, "unit": "m", "vary": "", "label": "Floor height", "help": "Height of each storey in metres."},
        "roof": {"kind": "choice", "default": "gable", "choices": ["gable", "hip", "shed", "flat"], "vary": "", "label": "Roof", "help": "Roof shape."},
        "roof_pitch": {"kind": "float", "default": 35.0, "min": 10.0, "max": 60.0, "step": 1.0, "unit": "°", "vary": "range", "label": "Roof pitch", "help": "Roof slope in degrees (gable, hip and shed)."},
        "overhang": {"kind": "float", "default": 0.3, "min": 0.0, "max": 1.0, "step": 0.05, "unit": "m", "vary": "", "label": "Overhang", "help": "How far the roof reaches past the walls."},
        "doors": {"kind": "int", "default": 1, "min": 0, "max": 2, "vary": "", "label": "Doors", "help": "Doors on the front."},
        "windows": {"kind": "int", "default": 2, "min": 0, "max": 24, "vary": "", "label": "Windows", "help": "Windows, spread over the walls and floors."},
        "chimney": {"kind": "bool", "default": False, "vary": "", "label": "Chimney", "help": "A chimney on the roof."},
    },
    "materials": {
        "wall": {"colour": "#d4c4a0", "roughness": 0.9, "metallic": 0.0},
        "roof": {"colour": "#8c4a3c", "roughness": 0.85, "metallic": 0.0},
        "trim": {"colour": "#6b4a33", "roughness": 0.85, "metallic": 0.0},
        "glass": {"colour": "#2f3a45", "roughness": 0.3, "metallic": 0.0},
    },
}

PLINTH = 0.2
SLOT = 1.6


def doors_at(p):
    """Door centres along the front wall."""
    return {0: [], 1: [0.0], 2: [-p["width"] / 4, p["width"] / 4]}[p["doors"]]


def window_slots(p):
    """Every place a window can go: (floor, wall, offset along the wall)."""
    walls = (("front", p["width"]), ("right", p["depth"]), ("back", p["width"]), ("left", p["depth"]))
    doors = doors_at(p)
    slots = []
    for floor in range(p["floors"]):
        for wall, span in walls:
            count = max(1, int(span / SLOT))
            for k in range(count):
                offset = -span / 2 + span * (k + 0.5) / count
                if floor == 0 and wall == "front" and any(abs(offset - d) < 0.95 for d in doors):
                    continue
                slots.append((floor, wall, offset))
    return slots


def limits(p):
    room = len(window_slots(p))
    if p["windows"] > room:
        return [f"windows: this building has room for {room} windows; make it larger or add a floor"]
    return []


def build(p, rng, materials, name):
    roles = list(materials)
    slot = {role: i for i, role in enumerate(roles)}
    shapes = Shapes()
    w, d = p["width"], p["depth"]
    height = p["floors"] * p["floor_height"]
    shapes.box((0, 0, PLINTH / 2), (w + 0.2, d + 0.2, PLINTH), slot["trim"])
    shapes.box((0, 0, PLINTH + height / 2), (w, d, height), slot["wall"])
    for floor in range(1, p["floors"]):
        z = PLINTH + floor * p["floor_height"]
        shapes.box((0, 0, z), (w + 0.06, d + 0.06, 0.12), slot["trim"])
    door_h = min(2.1, p["floor_height"] - 0.3)
    for x in doors_at(p):
        shapes.box((x, -d / 2 - 0.02, PLINTH + door_h / 2), (0.9, 0.08, door_h), slot["trim"])
        shapes.box((x, -d / 2 - 0.04, PLINTH + door_h + 0.06), (1.1, 0.1, 0.12), slot["trim"])
    slots = window_slots(p)
    count = p["windows"]
    chosen = [slots[round(i * len(slots) / count)] for i in range(count)] if count else []
    for floor, wall, offset in chosen:
        z = PLINTH + floor * p["floor_height"] + min(1.35, p["floor_height"] * 0.52)
        if wall in ("front", "back"):
            y = (-1 if wall == "front" else 1) * (d / 2 + 0.01)
            shapes.box((offset, y, z), (0.8, 0.06, 0.9), slot["glass"])
            shapes.box((offset, y * 1.0 + (-0.04 if wall == "front" else 0.04), z - 0.5), (0.95, 0.12, 0.08), slot["trim"])
        else:
            x = (1 if wall == "right" else -1) * (w / 2 + 0.01)
            shapes.box((x, offset, z), (0.06, 0.8, 0.9), slot["glass"])
            shapes.box((x + (0.04 if wall == "right" else -0.04), offset, z - 0.5), (0.12, 0.95, 0.08), slot["trim"])
    top = PLINTH + height
    _roof(shapes, slot, p, w, d, top)
    return shapes.finish(name, [materials[r] for r in roles])


def _roof(shapes, slot, p, w, d, top):
    o = p["overhang"]
    pitch = math.radians(p["roof_pitch"])
    thick = 0.14
    roof, wall = slot["roof"], slot["wall"]
    if p["roof"] == "flat":
        shapes.box((0, 0, top + 0.1), (w + 2 * o, d + 2 * o, 0.2), roof)
        ridge = top + 0.2
    elif p["roof"] == "gable":
        rise = d / 2 * math.tan(pitch)
        ridge = top + rise
        run = d / 2 + o
        length = run / math.cos(pitch)
        for side in (-1, 1):
            normal = (0.0, side * math.sin(pitch), math.cos(pitch))
            centre = (
                -normal[0] * thick / 2,
                side * run / 2 - normal[1] * thick / 2,
                ridge + thick - run / 2 * math.tan(pitch) - normal[2] * thick / 2,
            )
            rotation = Matrix.Rotation(-side * pitch, 3, "X")
            shapes.box(centre, (w + 2 * o, length, thick), roof, rotation)
        for side in (-1, 1):
            x = side * w / 2
            points = [(x, -d / 2, top), (x, d / 2, top), (x, 0.0, ridge)]
            shapes.polygon(points if side > 0 else points[::-1], wall)
    elif p["roof"] == "shed":
        rise = d * math.tan(pitch) * 0.5
        ridge = top + rise
        run = d + 2 * o
        slope = math.atan2(rise, d)
        length = run / math.cos(slope)
        rotation = Matrix.Rotation(slope, 3, "X")
        shapes.box((0, 0, top + rise / 2 + thick / 2), (w + 2 * o, length, thick), roof, rotation)
        for side in (-1, 1):
            x = side * w / 2
            points = [(x, -d / 2, top), (x, d / 2, top), (x, d / 2, ridge)]
            shapes.polygon(points if side > 0 else points[::-1], wall)
        shapes.polygon([(-w / 2, d / 2, top), (w / 2, d / 2, top), (w / 2, d / 2, ridge), (-w / 2, d / 2, ridge)][::-1], wall)
    else:  # hip
        long, short = (w, d) if w >= d else (d, w)
        rise = short / 2 * math.tan(pitch)
        ridge = top + rise
        half = (long - short) / 2
        a, b = long / 2 + o, short / 2 + o

        def turn(x, y, z):
            return (x, y, z) if w >= d else (-y, x, z)

        e1, e2, e3, e4 = (turn(-a, -b, top), turn(a, -b, top), turn(a, b, top), turn(-a, b, top))
        r1, r2 = turn(-half, 0.0, ridge), turn(half, 0.0, ridge)
        shapes.polygon([e1, e2, r2, r1], roof)
        shapes.polygon([e3, e4, r1, r2], roof)
        shapes.polygon([e2, e3, r2], roof)
        shapes.polygon([e4, e1, r1], roof)
        shapes.polygon([e4, e3, e2, e1], roof)
    if p["chimney"]:
        tall = ridge - top + 0.6
        shapes.box((w * 0.25, d * 0.18, top + tall / 2), (0.5, 0.5, tall), wall)
