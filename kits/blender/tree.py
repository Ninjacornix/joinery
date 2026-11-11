# Joinery kits: low-poly stylised trees. MIT licence, see kits/LICENSE.
# A recipe joins this file after common.py, which defines Shapes, Vector, Matrix
# and the other helpers used here.
# ruff: noqa: F821

import math

SPEC = {
    "id": "tree",
    "label": "Tree",
    "summary": "A low-poly stylised tree: a trunk, branches, a canopy and leaf cards.",
    "folder": "art/environment",
    "params": {
        "height": {"kind": "float", "default": 5.0, "min": 1.0, "max": 25.0, "step": 0.1, "unit": "m", "vary": "scale", "label": "Height", "help": "Height in metres."},
        "trunk": {"kind": "float", "default": 0.2, "min": 0.04, "max": 1.2, "step": 0.01, "unit": "m", "vary": "scale", "label": "Trunk", "help": "Trunk radius at the base, in metres."},
        "branch_levels": {"kind": "int", "default": 2, "min": 0, "max": 3, "vary": "", "label": "Branch levels", "help": "How many times branches split."},
        "branches": {"kind": "int", "default": 4, "min": 2, "max": 6, "vary": "", "label": "Branches", "help": "Branches at each split."},
        "canopy": {"kind": "choice", "default": "round", "choices": ["round", "cone", "tall", "flat", "none"], "vary": "", "label": "Canopy", "help": "Canopy shape: broadleaf, conifer, poplar, acacia, or bare."},
        "canopy_size": {"kind": "float", "default": 1.0, "min": 0.3, "max": 2.0, "step": 0.05, "vary": "range", "label": "Canopy size", "help": "Canopy size relative to the tree."},
        "leaf_cards": {"kind": "int", "default": 40, "min": 0, "max": 300, "vary": "", "label": "Leaf cards", "help": "Flat leaf cards around the canopy."},
        "lean": {"kind": "float", "default": 0.05, "min": 0.0, "max": 0.4, "step": 0.01, "vary": "range", "label": "Lean", "help": "How far the trunk leans and bends."},
    },
    "materials": {
        "bark": {"colour": "#6b4f3a", "roughness": 0.95, "metallic": 0.0},
        "leaves": {"colour": "#4f7a3a", "roughness": 0.9, "metallic": 0.0},
        "leaf_card": {"colour": "#6a9a48", "roughness": 0.9, "metallic": 0.0, "double_sided": True},
    },
}

SIDES = 6


def _branch(shapes, rng, bark, start, direction, length, radius, level, levels, count, tips):
    end = start + direction * length
    sides = max(3, SIDES - 2 * level)
    shapes.tube(start, end, radius, radius * 0.6, sides, bark, caps=False)
    if level >= levels:
        tips.append((end, direction, length))
        return
    side = direction.cross(Vector((0.0, 0.0, 1.0)))
    if side.length < 1e-4:
        side = Vector((1.0, 0.0, 0.0))
    side.normalize()
    other = direction.cross(side).normalized()
    turn = rng.uniform(0, math.tau)
    for i in range(count):
        at = start + direction * length * rng.uniform(0.55, 0.95)
        azimuth = turn + i * math.tau / count + rng.uniform(-0.3, 0.3)
        spread = math.radians(rng.uniform(30, 55))
        outward = side * math.cos(azimuth) + other * math.sin(azimuth)
        child = (direction * math.cos(spread) + outward * math.sin(spread)).normalized()
        child.z = max(child.z, 0.15)
        child.normalize()
        _branch(shapes, rng, bark, at, child, length * rng.uniform(0.5, 0.65), radius * 0.55, level + 1, levels, count, tips)


def build(p, rng, materials, name):
    roles = list(materials)
    slot = {role: i for i, role in enumerate(roles)}
    shapes = Shapes()
    height, radius = p["height"], p["trunk"]
    canopy = p["canopy"]
    trunk_share = {"round": 0.55, "cone": 0.95, "tall": 0.5, "flat": 0.7, "none": 0.6}[canopy]
    lean = p["lean"]
    # The trunk: three segments, bending by the lean.
    tips = []
    point = Vector((0.0, 0.0, 0.0))
    heading = Vector((rng.uniform(-1, 1) * lean, rng.uniform(-1, 1) * lean, 1.0)).normalized()
    segment = height * trunk_share / 3
    r = radius
    for _ in range(3):
        end = point + heading * segment
        shapes.tube(point, end, r, r * 0.8, SIDES, slot["bark"], caps=False)
        point, r = end, r * 0.8
        heading = (heading + Vector((rng.uniform(-1, 1) * lean, rng.uniform(-1, 1) * lean, 0.0))).normalized()
    levels = p["branch_levels"] if canopy != "cone" else 0
    if levels:
        _branch(shapes, rng, slot["bark"], point, heading, height * 0.3, r, 1, levels, p["branches"], tips)
    else:
        tips.append((point, heading, segment))
    size = p["canopy_size"]
    blobs = []
    if canopy == "cone":
        tiers = 4
        base = height * 0.25
        for k in range(tiers):
            z0 = base + (height - base) * k / tiers * 0.85
            spread = height * 0.28 * size * (1 - k / (tiers + 0.5))
            shapes.tube((0, 0, z0), (0, 0, z0 + (height - base) / tiers * 1.5), spread, 0.0, 7, slot["leaves"], caps=True)
            blobs.append((Vector((0, 0, z0 + spread * 0.6)), spread * 0.8))
    elif canopy != "none":
        shape = {"round": (1.0, 1.0, 0.8), "tall": (0.75, 0.75, 2.4), "flat": (1.5, 1.5, 0.4)}[canopy]
        blob_r = height * 0.19 * size
        chosen = tips if len(tips) <= 8 else [tips[round(i * len(tips) / 8)] for i in range(8)]
        if canopy == "tall":
            chosen = []
        # One crown over the top of the trunk, and a clump on each branch tip.
        crown = blob_r * (1.0 if canopy == "tall" else 1.2)
        top = point + Vector((0, 0, crown * shape[2] * (0.9 if canopy == "tall" else 0.5)))
        if canopy != "flat" or not levels:
            shape_blob(shapes, rng, top, crown, shape, slot["leaves"], 0.22)
            blobs.append((top, crown))
        for tip, _direction, _length in chosen:
            radius_here = blob_r * rng.uniform(0.75, 1.0)
            centre = tip + Vector((0, 0, radius_here * shape[2] * 0.3))
            shape_blob(shapes, rng, centre, radius_here, shape, slot["leaves"], 0.25)
            blobs.append((centre, radius_here))
    leaf_cards(shapes, rng, blobs, p["leaf_cards"], slot["leaf_card"])
    return shapes.finish(name, [materials[r] for r in roles])
