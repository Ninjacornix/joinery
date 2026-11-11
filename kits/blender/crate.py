# Joinery kits: wooden crates. MIT licence, see kits/LICENSE.
# A recipe joins this file after common.py, which defines Shapes, Vector, Matrix
# and the other helpers used here.
# ruff: noqa: F821

import math

SPEC = {
    "id": "crate",
    "label": "Crate",
    "summary": "A wooden crate: a frame with planked sides, optional metal bands, worn as you like.",
    "folder": "art/props",
    "params": {
        "size": {"kind": "float", "default": 0.8, "min": 0.3, "max": 2.0, "step": 0.05, "unit": "m", "vary": "scale", "label": "Size", "help": "Width, depth and height in metres."},
        "height": {"kind": "float", "default": 1.0, "min": 0.5, "max": 1.5, "step": 0.05, "vary": "range", "label": "Height", "help": "Height as a share of the width."},
        "planks": {"kind": "int", "default": 4, "min": 2, "max": 8, "vary": "", "label": "Planks", "help": "Planks on each side."},
        "bands": {"kind": "int", "default": 0, "min": 0, "max": 3, "vary": "", "label": "Bands", "help": "Metal bands around the sides."},
        "wear": {"kind": "float", "default": 0.3, "min": 0.0, "max": 1.0, "step": 0.05, "vary": "range", "label": "Wear", "help": "Crooked, darker planks."},
    },
    "materials": {
        "wood": {"colour": "#a87b4f", "roughness": 0.85, "metallic": 0.0},
        "wood_worn": {"colour": "#7a5a3c", "roughness": 0.95, "metallic": 0.0},
        "frame": {"colour": "#8a6440", "roughness": 0.85, "metallic": 0.0},
        "metal": {"colour": "#5a5f66", "roughness": 0.5, "metallic": 0.7},
    },
}


def build(p, rng, materials, name):
    roles = list(materials)
    slot = {role: i for i, role in enumerate(roles)}
    shapes = Shapes()
    w = p["size"]
    h = w * p["height"]
    t = w * 0.08  # frame beam thickness
    wear = p["wear"]
    half_w, half_h = w / 2, h / 2
    # The frame: twelve beams along the edges.
    for sx in (-1, 1):
        for sy in (-1, 1):
            shapes.box((sx * (half_w - t / 2), sy * (half_w - t / 2), half_h), (t, t, h), slot["frame"])
    for z in (t / 2, h - t / 2):
        for s in (-1, 1):
            shapes.box((0, s * (half_w - t / 2), z), (w - 2 * t, t, t), slot["frame"])
            shapes.box((s * (half_w - t / 2), 0, z), (t, w - 2 * t, t), slot["frame"])
    # The bottom is one board; the sides and the lid are planks.
    shapes.box((0, 0, t * 0.25), (w - 2 * t, w - 2 * t, t * 0.5), slot["frame"])
    count = p["planks"]
    depth = t * 0.5
    gap = w * 0.012

    def plank(centre, size, axis):
        worn = rng.random() < wear * 0.4
        tilt = math.radians(rng.uniform(-4.0, 4.0) * wear)
        rotation = Matrix.Rotation(tilt, 3, axis)
        shapes.box(centre, size, slot["wood_worn" if worn else "wood"], rotation)

    inner_h = h - 2 * t
    inner_w = w - 2 * t
    step_h = inner_h / count
    step_w = inner_w / count
    inset = half_w - t * 0.55
    for i in range(count):
        z = t + step_h * (i + 0.5)
        for s in (-1, 1):
            plank((s * inset, 0, z), (depth, inner_w, step_h - gap), "X")
            plank((0, s * inset, z), (inner_w, depth, step_h - gap), "Y")
    for i in range(count):
        y = -inner_w / 2 + step_w * (i + 0.5)
        plank((0, y, h - t * 0.55), (inner_w, step_w - gap, depth), "Z")
    # Metal bands around the four sides.
    bands = p["bands"]
    for i in range(bands):
        z = h * (i + 1) / (bands + 1)
        band = h * 0.06
        out = half_w + t * 0.05
        thin = t * 0.25
        for s in (-1, 1):
            shapes.box((s * out, 0, z), (thin, w + thin * 2, band), slot["metal"])
            shapes.box((0, s * out, z), (w, thin, band), slot["metal"])
    return shapes.finish(name, [materials[r] for r in roles])
