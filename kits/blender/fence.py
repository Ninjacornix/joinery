# Joinery kits: fences and railings. MIT licence, see kits/LICENSE.
# A recipe joins this file after common.py, which defines Shapes, Vector, Matrix
# and the other helpers used here.
# ruff: noqa: F821

import math

SPEC = {
    "id": "fence",
    "label": "Fence",
    "summary": "A straight run of fence or railing: posts, rails or pickets, some posts broken if you like.",
    "folder": "art/environment",
    "params": {
        "length": {"kind": "float", "default": 6.0, "min": 1.0, "max": 40.0, "step": 0.5, "unit": "m", "vary": "", "label": "Length", "help": "Length of the run in metres, along X."},
        "post_spacing": {"kind": "float", "default": 2.0, "min": 0.5, "max": 6.0, "step": 0.1, "unit": "m", "vary": "", "label": "Post spacing", "help": "Distance between posts in metres."},
        "height": {"kind": "float", "default": 1.1, "min": 0.4, "max": 3.0, "step": 0.05, "unit": "m", "vary": "scale", "label": "Height", "help": "Height of the posts in metres."},
        "rails": {"kind": "int", "default": 2, "min": 1, "max": 4, "vary": "", "label": "Rails", "help": "Horizontal rails between posts."},
        "style": {"kind": "choice", "default": "rails", "choices": ["rails", "pickets"], "vary": "", "label": "Style", "help": "Rails only, or a picket fence."},
        "post_size": {"kind": "float", "default": 0.12, "min": 0.05, "max": 0.4, "step": 0.01, "unit": "m", "vary": "range", "label": "Post size", "help": "Thickness of the posts in metres."},
        "broken": {"kind": "int", "default": 0, "min": 0, "max": 20, "vary": "", "label": "Broken posts", "help": "Posts snapped off, with their rails down."},
    },
    "materials": {
        "wood": {"colour": "#9c7a55", "roughness": 0.9, "metallic": 0.0},
        "wood_worn": {"colour": "#6e5840", "roughness": 0.95, "metallic": 0.0},
    },
}


def posts_for(length, spacing):
    """How many posts a run has: one at each end, none further apart than spacing."""
    return max(2, math.ceil(length / spacing - 1e-6) + 1)


def limits(p):
    posts = posts_for(p["length"], p["post_spacing"])
    if p["broken"] > posts:
        return [f"broken: this fence has {posts} posts, so at most {posts} can be broken"]
    return []


def build(p, rng, materials, name):
    roles = list(materials)
    slot = {role: i for i, role in enumerate(roles)}
    shapes = Shapes()
    length, height, size = p["length"], p["height"], p["post_size"]
    count = posts_for(length, p["post_spacing"])
    span = length / (count - 1)
    xs = [-length / 2 + i * span for i in range(count)]
    broken = set(rng.sample(range(count), min(p["broken"], count)))
    tops = {}
    for i, x in enumerate(xs):
        if i in broken:
            top = height * rng.uniform(0.3, 0.6)
            shapes.box((x, 0, top / 2), (size, size, top), slot["wood_worn"])
            # A jagged snapped end.
            tip = (x + rng.uniform(-0.3, 0.3) * size, rng.uniform(-0.3, 0.3) * size, top + size * 0.8)
            shapes.tube((x, 0, top), tip, size * 0.6, 0.0, 4, slot["wood_worn"], caps=True)
        else:
            top = height
            shapes.box((x, 0, top / 2), (size, size, top), slot["wood"])
            shapes.tube((x, 0, top), (x, 0, top + size * 0.6), size * 0.72, 0.0, 4, slot["wood"])
        tops[i] = top
    rail = (size * 0.35, size * 0.6)
    levels = [height * (j + 1) / (p["rails"] + 1) for j in range(p["rails"])]
    if p["style"] == "pickets":
        levels = [height * 0.25, height * 0.7]
    for i in range(count - 1):
        a, b = xs[i], xs[i + 1]
        down = i in broken or (i + 1) in broken
        for z in levels:
            if down and z > min(tops[i], tops[i + 1]):
                # The rail fell: one end on the ground, the other on the post.
                if rng.random() < 0.5:
                    continue
                drop = math.atan2(z - rail[1] / 2, span) * (1 if i in broken else -1)
                rotation = Matrix.Rotation(drop, 3, "Y")
                shapes.box(((a + b) / 2, size * 0.6, z / 2), (span * 1.02, rail[0], rail[1]), slot["wood_worn"], rotation)
                continue
            shapes.box(((a + b) / 2, size * 0.45, z), (span, rail[0], rail[1]), slot["wood"])
        if p["style"] == "pickets" and not down:
            width = size * 0.6
            pickets = max(1, int(span / (width * 2.2)))
            for k in range(pickets):
                x = a + span * (k + 0.5) / pickets
                tall = height * 0.85
                shapes.box((x, size * 0.75, tall / 2), (width, size * 0.2, tall), slot["wood"])
                shapes.tube((x, size * 0.75, tall), (x, size * 0.75, tall + width * 0.7), width * 0.7, 0.0, 4, slot["wood"])
    return shapes.finish(name, [materials[r] for r in roles])
