"""Body regions of a character, from the bones its vertices follow.

A character's body is one mesh, so "the shoulders" is not an object or a
material: it is the vertices weighted to the shoulder bones. Each vertex
belongs to the region of the deform bone (or vertex group) that weighs it most,
named by `region_of` from Rigify's, MPFB2's and the common hand-made rig names.
The parts list offers the regions as parts; the GLB carries the same bones per
region (extras `joinery_regions` on the mesh), so the editor can highlight and
pick a region on the game's copy without reading the source again.

`region_of` is plain Python: Joinery calls it, and the Blender scripts embed
its source (`REGION_CODE`), so both sides name a bone's region the same way.
"""

from __future__ import annotations

import inspect

#: Regions in list order (head to toe), with how each reads.
REGIONS: dict[str, str] = {
    "head": "Head",
    "neck": "Neck",
    "shoulders": "Shoulders",
    "torso": "Torso",
    "hips": "Hips",
    "arms": "Arms",
    "hands": "Hands",
    "legs": "Legs",
    "feet": "Feet",
}

#: MPFB2 (`bl_ext.system.mpfb`) object types that are not the body: picking
#: one picks the object, not a region of it.
WORN_ROLES = frozenset(
    {
        "clothes",
        "hair",
        "eyes",
        "eyebrows",
        "eyelashes",
        "teeth",
        "tongue",
        "proxymeshes",
    }
)

#: MPFB2's macro settings (HumanObjectProperties), 0 to 1, with how each reads.
MACROS: dict[str, str] = {
    "gender": "Gender",
    "age": "Age",
    "muscle": "Muscle",
    "weight": "Weight",
    "height": "Height",
    "proportions": "Proportions",
    "cupsize": "Cup size",
    "firmness": "Firmness",
    "african": "African",
    "asian": "Asian",
    "caucasian": "Caucasian",
}

#: MPFB2 detail targets (file names under its data/targets, without
#: ".target.gz") an agent reaches for when the user names a region. A target
#: with "-l-"/"l-" has an "r-" twin; set both for a symmetric change. These are
#: hints, not a limit: set_body_shape takes any MPFB2 target.
REGION_TARGETS: dict[str, tuple[str, ...]] = {
    "head": (
        "head-scale-horiz-incr",
        "head-scale-vert-incr",
        "head-fat-incr",
        "head-age-incr",
        "chin-width-incr",
        "nose-scale-horiz-incr",
    ),
    "neck": ("neck-scale-horiz-incr", "neck-scale-vert-incr", "measure-neck-circ-incr"),
    "shoulders": (
        "l-upperarm-shoulder-muscle-incr",
        "r-upperarm-shoulder-muscle-incr",
        "measure-shoulder-dist-incr",
        "torso-vshape-incr",
    ),
    "torso": (
        "torso-scale-horiz-incr",
        "torso-scale-depth-incr",
        "torso-muscle-pectoral-incr",
        "torso-muscle-dorsi-incr",
        "stomach-pregnant-incr",
        "measure-waist-circ-incr",
    ),
    "hips": ("hip-scale-horiz-incr", "buttocks-volume-incr", "measure-hips-circ-incr"),
    "arms": (
        "l-upperarm-muscle-incr",
        "r-upperarm-muscle-incr",
        "l-upperarm-fat-incr",
        "r-upperarm-fat-incr",
        "l-lowerarm-muscle-incr",
        "r-lowerarm-muscle-incr",
    ),
    "hands": (
        "l-hand-scale-incr",
        "r-hand-scale-incr",
        "l-hand-fingers-length-incr",
        "r-hand-fingers-length-incr",
    ),
    "legs": (
        "l-upperleg-muscle-incr",
        "r-upperleg-muscle-incr",
        "l-upperleg-fat-incr",
        "r-upperleg-fat-incr",
        "l-lowerleg-muscle-incr",
        "r-lowerleg-muscle-incr",
    ),
    "feet": ("l-foot-scale-incr", "r-foot-scale-incr"),
}

#: Macro settings that change a region most, in the order to try them.
REGION_MACROS: dict[str, tuple[str, ...]] = {
    "head": ("age",),
    "neck": ("muscle", "weight"),
    "shoulders": ("muscle", "proportions"),
    "torso": ("muscle", "weight", "cupsize"),
    "hips": ("weight", "proportions"),
    "arms": ("muscle", "weight"),
    "hands": ("weight",),
    "legs": ("muscle", "weight", "height"),
    "feet": ("height",),
}


def region_of(name: str) -> str:
    """The body region a bone or vertex group is part of, or "" for none.

    "DEF-upper_arm.L" is arms, "DEF-spine.004" (Rigify's neck) is neck,
    "shoulder01.R" is shoulders, "joint-head" (an MPFB2 helper group) is "".
    """
    import re

    base = name.strip().lower()
    for prefix in ("def-", "def_", "org-", "mixamorig:", "mixamorig_"):
        base = base.removeprefix(prefix)
    if base.startswith(("joint-", "helper-", "mch-", "wgt-")) or base in {
        "helpergeometry",
        "jointcubes",
        "body",
        "left",
        "mid",
        "right",
        "root",
    }:
        return ""
    # Rigify's spine is one chain numbered from the hips up to the head.
    rigify = re.fullmatch(r"spine(?:\.(\d{3}))?", base)
    if rigify:
        number = int(rigify.group(1) or 0)
        return (
            "hips"
            if number == 0
            else "torso"
            if number <= 3
            else "neck"
            if number <= 5
            else "head"
        )
    words = re.sub(r"[^a-z]+", " ", base)
    words = " " + re.sub(r"\b(l|r|left|right)\b", " ", words) + " "
    table = (
        ("feet", ("foot", "toe", "ankle", "heel")),
        (
            "hands",
            (
                "hand",
                "finger",
                "thumb",
                "palm",
                "f index",
                "f middle",
                "f ring",
                "f pinky",
                "metacarpal",
                "wrist",
            ),
        ),
        ("shoulders", ("shoulder", "clavicle", "scapula", "collar")),
        ("arms", ("arm", "elbow")),
        ("neck", ("neck",)),
        (
            "head",
            (
                "head",
                "jaw",
                "eye",
                " ear",
                "tongue",
                "teeth",
                "lid",
                "brow",
                "lip",
                "nose",
                "cheek",
                "chin",
                "forehead",
                "temple",
                "face",
                "skull",
                "mouth",
            ),
        ),
        ("legs", ("thigh", "shin", "leg", "knee", "calf")),
        ("hips", ("pelvis", "hip", "butt", "glute")),
        (
            "torso",
            (
                "spine",
                "chest",
                "breast",
                "rib",
                "torso",
                "abdomen",
                "belly",
                "stomach",
                "back",
                "waist",
            ),
        ),
    )
    for region, keys in table:
        if any(key in words for key in keys):
            return region
    return ""


# Per mesh: each vertex goes to the region of the group weighing it most. A
# mesh deformed by an armature counts only that armature's deform bones (MPFB2
# meshes also carry helper and joint groups); a mesh without one counts every
# group `region_of` names.
REGION_CODE = (
    "\n"
    + inspect.getsource(region_of)
    + r'''
def _joinery_region_groups(obj):
    rigs = [m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object is not None]
    if rigs:
        deform = {b.name for rig in rigs for b in rig.data.bones if b.use_deform}
        groups = [g for g in obj.vertex_groups if g.name in deform]
    else:
        groups = list(obj.vertex_groups)
    return {g.index: (g.name, region_of(g.name)) for g in groups if region_of(g.name)}

def _joinery_dominant(obj):
    """Each vertex's heaviest named group (index into the mesh's groups), or -1."""
    named = _joinery_region_groups(obj)
    found = []
    for vertex in obj.data.vertices:
        best, weight = -1, 0.0
        for element in vertex.groups:
            if element.group in named and element.weight > weight:
                best, weight = element.group, element.weight
        found.append(best)
    return named, found

def _joinery_mesh_regions(obj):
    """{region: {'bones': [...], 'vertices': n}} for a mesh, empty when no group names a region."""
    if obj.type != 'MESH' or not obj.vertex_groups:
        return {}
    named, dominant = _joinery_dominant(obj)
    regions = {}
    for group in dominant:
        if group < 0:
            continue
        name, region = named[group]
        row = regions.setdefault(region, {'bones': set(), 'vertices': 0})
        row['bones'].add(name)
        row['vertices'] += 1
    for index, (name, region) in named.items():
        regions.get(region, {'bones': set()})['bones'].add(name)
    return {
        region: {'bones': sorted(row['bones']), 'vertices': row['vertices']}
        for region, row in regions.items()
    }

def _joinery_key_regions(obj, limit=64):
    """{shape key: [regions it moves]} for a mesh's first `limit` keys."""
    keys = obj.data.shape_keys
    if keys is None or len(keys.key_blocks) < 2 or not obj.vertex_groups:
        return {}
    import numpy as _np
    named, dominant = _joinery_dominant(obj)
    owner = _np.array([named[g][1] if g >= 0 else '' for g in dominant], dtype=object)
    count = len(obj.data.vertices)
    found = {}
    for key in list(keys.key_blocks)[1 : limit + 1]:
        reference = key.relative_key or keys.reference_key
        moved = _np.empty(count * 3, dtype=_np.float32)
        rest = _np.empty(count * 3, dtype=_np.float32)
        key.data.foreach_get('co', moved)
        reference.data.foreach_get('co', rest)
        distance = _np.linalg.norm((moved - rest).reshape(-1, 3), axis=1)
        touched = owner[distance > 1e-4]
        touched = touched[touched != '']
        if not len(touched):
            found[key.name] = []
            continue
        names, counts = _np.unique(touched, return_counts=True)
        share = counts / counts.sum()
        found[key.name] = [str(n) for n, s in sorted(zip(names, share), key=lambda r: -r[1]) if s >= 0.1]
    return found

def _joinery_mpfb_role(obj):
    """MPFB2's object type ("basemesh", "clothes", "skeleton", ...) when MPFB2 is enabled, else ""."""
    return str(getattr(obj, 'MPFB_GEN_object_type', '') or '').lower()

def _joinery_enable_mpfb():
    """Turn MPFB2 on (when the worker has it) for a file that holds an MPFB2 human."""
    def human(obj):
        keys = obj.data.shape_keys if obj.type == 'MESH' else None
        return obj.vertex_groups.get('HelperGeometry') is not None or (
            keys is not None and any(k.name.startswith('$md-') for k in keys.key_blocks)
        )
    if not any(human(o) for o in bpy.data.objects if o.type == 'MESH'):
        return False
    import addon_utils
    try:
        return bool(addon_utils.enable('bl_ext.system.mpfb', default_set=True))
    except Exception:
        return False

def _joinery_mpfb_macros(obj):
    """MPFB2's macro settings of a basemesh (MPFB2 enabled), else {}."""
    if _joinery_mpfb_role(obj) != 'basemesh':
        return {}
    try:
        from bl_ext.system.mpfb.services.targetservice import TargetService
        info = TargetService.get_macro_info_dict_from_basemesh(obj)
    except Exception:
        return {}
    macros = {k: round(float(v), 4) for k, v in info.items() if not isinstance(v, dict)}
    macros.update({k: round(float(v), 4) for k, v in info.get('race', {}).items()})
    return macros
'''
)


def regions_hint(region: str) -> str:
    """What to change for a region of an MPFB2 human, for the agent."""
    targets = ", ".join(REGION_TARGETS.get(region, ()))
    macros = ", ".join(REGION_MACROS.get(region, ()))
    return f"MPFB2 targets such as {targets}; macros {macros}" if targets else ""
