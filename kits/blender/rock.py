# Joinery kits: rocks and boulders. MIT licence, see kits/LICENSE.
# A recipe joins this file after common.py, which defines Shapes, Vector, Matrix
# and the other helpers used here.
# ruff: noqa: F821

SPEC = {
    "id": "rock",
    "label": "Rock",
    "summary": "Faceted boulders and stones, with moss on the parts that face up.",
    "folder": "art/environment",
    "params": {
        "size": {"kind": "float", "default": 1.0, "min": 0.1, "max": 6.0, "step": 0.05, "unit": "m", "vary": "scale", "label": "Size", "help": "Widest side in metres."},
        "roughness": {"kind": "float", "default": 0.45, "min": 0.0, "max": 1.0, "step": 0.05, "vary": "range", "label": "Roughness", "help": "How lumpy and chipped the surface is."},
        "facets": {"kind": "int", "default": 2, "min": 1, "max": 4, "vary": "", "label": "Facets", "help": "Detail: 1 is blocky, 4 is smooth. Each step is four times the triangles."},
        "flatness": {"kind": "float", "default": 0.25, "min": 0.0, "max": 0.8, "step": 0.05, "vary": "range", "label": "Flatness", "help": "0 is round, 0.8 is a flat slab."},
        "moss": {"kind": "float", "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05, "vary": "range", "label": "Moss", "help": "How much of the top is covered in moss."},
    },
    "materials": {
        "stone": {"colour": "#8a8580", "roughness": 0.9, "metallic": 0.0},
        "moss": {"colour": "#5d7a3a", "roughness": 1.0, "metallic": 0.0},
    },
}


def build(p, rng, materials, name):
    roles = list(materials)
    slot = {role: i for i, role in enumerate(roles)}
    shapes = Shapes()
    radius = p["size"] / 2
    verts = shapes.blob((0, 0, 0), radius, p["facets"] + 1, slot["stone"])
    rough = p["roughness"]
    # Lumps: a few directions the rock bulges towards.
    lumps = []
    for _ in range(6):
        direction = Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1)))
        if direction.length < 1e-6:
            direction = Vector((0, 0, 1))
        lumps.append((direction.normalized(), rng.uniform(0.4, 1.0)))
    # Cuts: flat faces where the rock split, the look of a real boulder.
    cuts = []
    for _ in range(3 + rng.randint(0, 3)):
        direction = Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 0.6)))
        if direction.length < 1e-6:
            direction = Vector((1, 0, 0))
        depth = radius * (1.0 - (0.08 + 0.3 * rough) * rng.uniform(0.4, 1.0))
        cuts.append((direction.normalized(), depth))
    stretch = (rng.uniform(0.85, 1.15), rng.uniform(0.85, 1.15))
    for vert in verts:
        normal = vert.co.normalized()
        bulge = sum(a * max(0.0, normal.dot(d)) ** 3 for d, a in lumps) / 3
        jitter = rng.uniform(-1.0, 1.0)
        vert.co = vert.co * (1.0 + rough * (0.35 * bulge + 0.12 * jitter) - 0.1 * rough)
        for direction, depth in cuts:
            over = vert.co.dot(direction) - depth
            if over > 0:
                vert.co -= direction * over
        vert.co.x *= stretch[0]
        vert.co.y *= stretch[1]
        vert.co.z *= 1.0 - p["flatness"]
    # A flat underside, so the rock sits on the ground.
    floor = min(v.co.z for v in verts) * 0.75
    for vert in verts:
        vert.co.z = max(vert.co.z, floor)
    shapes.bm.normal_update()
    moss = p["moss"]
    if moss > 0:
        for face in shapes.bm.faces:
            threshold = 1.0 - 1.4 * moss + rng.uniform(-0.12, 0.12) * moss
            if face.normal.z > threshold:
                face.material_index = slot["moss"]
    return shapes.finish(name, [materials[r] for r in roles])
