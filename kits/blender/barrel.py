# Joinery kits: wooden barrels. MIT licence, see kits/LICENSE.
# A recipe joins this file after common.py, which defines Shapes, Vector, Matrix
# and the other helpers used here.
# ruff: noqa: F821

import math

SPEC = {
    "id": "barrel",
    "label": "Barrel",
    "summary": "A bulging wooden barrel of staves held by metal bands.",
    "folder": "art/props",
    "params": {
        "height": {"kind": "float", "default": 1.0, "min": 0.4, "max": 2.0, "step": 0.05, "unit": "m", "vary": "scale", "label": "Height", "help": "Height in metres."},
        "radius": {"kind": "float", "default": 0.35, "min": 0.15, "max": 1.0, "step": 0.01, "unit": "m", "vary": "scale", "label": "Radius", "help": "Radius at the middle, in metres."},
        "staves": {"kind": "int", "default": 12, "min": 6, "max": 24, "vary": "", "label": "Staves", "help": "Wooden boards around the barrel."},
        "bands": {"kind": "int", "default": 2, "min": 0, "max": 4, "vary": "", "label": "Bands", "help": "Metal bands."},
        "bulge": {"kind": "float", "default": 0.12, "min": 0.0, "max": 0.3, "step": 0.01, "vary": "range", "label": "Bulge", "help": "How much wider the middle is than the ends."},
        "wear": {"kind": "float", "default": 0.3, "min": 0.0, "max": 1.0, "step": 0.05, "vary": "range", "label": "Wear", "help": "Uneven, darker staves."},
    },
    "materials": {
        "wood": {"colour": "#a0703f", "roughness": 0.85, "metallic": 0.0},
        "wood_worn": {"colour": "#73522f", "roughness": 0.95, "metallic": 0.0},
        "metal": {"colour": "#4f545a", "roughness": 0.55, "metallic": 0.7},
    },
}

RINGS = 4


def build(p, rng, materials, name):
    roles = list(materials)
    slot = {role: i for i, role in enumerate(roles)}
    shapes = Shapes()
    height, radius, bulge = p["height"], p["radius"], p["bulge"]

    def profile(z):
        return radius * (1.0 - bulge + bulge * math.sin(math.pi * z / height))

    count = p["staves"]
    step = math.tau / count
    gap = step * 0.04
    thick = radius * 0.06
    for i in range(count):
        worn = rng.random() < p["wear"] * 0.4
        out = rng.uniform(-1.0, 1.0) * p["wear"] * thick * 0.4
        start, end = i * step + gap, (i + 1) * step - gap
        outer, inner = [], []
        for k in range(RINGS + 1):
            z = height * k / RINGS
            r = profile(z) + out
            for angle, row in ((start, outer), (end, outer)):
                row.append((math.cos(angle) * r, math.sin(angle) * r, z))
            for angle in (start, end):
                inner.append((math.cos(angle) * (r - thick), math.sin(angle) * (r - thick), z))
        material = slot["wood_worn" if worn else "wood"]
        for k in range(RINGS):
            a, b = outer[2 * k], outer[2 * k + 1]
            c, d = outer[2 * k + 3], outer[2 * k + 2]
            shapes.polygon([a, b, c, d], material)
            # The stave's two edges, seen through the gaps.
            shapes.polygon([inner[2 * k], a, d, inner[2 * k + 2]], material)
            shapes.polygon([b, inner[2 * k + 1], inner[2 * k + 3], c], material)
    # Lids, set a little into the ends.
    for z, flip in ((height * 0.04, True), (height * 0.96, False)):
        r = profile(z) - thick
        points = [(math.cos(i * step) * r, math.sin(i * step) * r, z) for i in range(count)]
        shapes.polygon(points[::-1] if flip else points, slot["wood_worn"])
    bands = p["bands"]
    for j in range(bands):
        z = height * (0.12 + 0.76 * (j / (bands - 1) if bands > 1 else 0.5))
        band = height * 0.035
        r_low, r_high = profile(z - band) + thick * 0.6, profile(z + band) + thick * 0.6
        shapes.tube((0, 0, z - band), (0, 0, z + band), r_low, r_high, count, slot["metal"], caps=False)
    return shapes.finish(name, [materials[r] for r in roles])
