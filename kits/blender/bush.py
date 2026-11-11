# Joinery kits: low-poly stylised bushes. MIT licence, see kits/LICENSE.
# A recipe joins this file after common.py, which defines Shapes, Vector, Matrix
# and the other helpers used here.
# ruff: noqa: F821

import math

SPEC = {
    "id": "bush",
    "label": "Bush",
    "summary": "A low-poly stylised bush: lumpy clumps of leaves with leaf cards.",
    "folder": "art/environment",
    "params": {
        "size": {"kind": "float", "default": 1.0, "min": 0.3, "max": 4.0, "step": 0.05, "unit": "m", "vary": "scale", "label": "Size", "help": "Width in metres."},
        "clumps": {"kind": "int", "default": 4, "min": 1, "max": 8, "vary": "", "label": "Clumps", "help": "Leafy clumps the bush is made of."},
        "flatness": {"kind": "float", "default": 0.3, "min": 0.0, "max": 0.7, "step": 0.05, "vary": "range", "label": "Flatness", "help": "0 is round, 0.7 is a low spreading bush."},
        "roughness": {"kind": "float", "default": 0.3, "min": 0.0, "max": 0.6, "step": 0.05, "vary": "range", "label": "Roughness", "help": "How lumpy the clumps are."},
        "leaf_cards": {"kind": "int", "default": 24, "min": 0, "max": 150, "vary": "", "label": "Leaf cards", "help": "Flat leaf cards around the clumps."},
    },
    "materials": {
        "leaves": {"colour": "#4a7334", "roughness": 0.9, "metallic": 0.0},
        "leaf_card": {"colour": "#628f42", "roughness": 0.9, "metallic": 0.0, "double_sided": True},
    },
}


def build(p, rng, materials, name):
    roles = list(materials)
    slot = {role: i for i, role in enumerate(roles)}
    shapes = Shapes()
    size = p["size"]
    squash = 1.0 - p["flatness"]
    count = p["clumps"]
    blobs = []
    for i in range(count):
        if count == 1:
            offset = Vector((0.0, 0.0, 0.0))
        else:
            angle = i * math.tau / count + rng.uniform(-0.4, 0.4)
            reach = size * rng.uniform(0.18, 0.28)
            offset = Vector((math.cos(angle) * reach, math.sin(angle) * reach, 0.0))
        radius = size * rng.uniform(0.28, 0.4) * (1.15 if count == 1 else 1.0)
        centre = offset + Vector((0.0, 0.0, radius * squash * 0.8))
        shape_blob(shapes, rng, centre, radius, (1.0, 1.0, squash), slot["leaves"], p["roughness"])
        blobs.append((centre, radius))
    leaf_cards(shapes, rng, blobs, p["leaf_cards"], slot["leaf_card"])
    return shapes.finish(name, [materials[r] for r in roles])
