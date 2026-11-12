"""Typed Blender operations on a character: its body shape and the shape keys the game gets.

* ``set_body_shape``: change an MPFB2 human's macro settings (muscle, weight,
  age, ...) and detail targets ("l-upperarm-shoulder-muscle-incr") through
  MPFB2's own scripting API, then refit its clothes and rig where MPFB2 can.
* ``bake_shape_keys``: set shape key values and choose which keys reach the
  game as morph targets; every other key is baked into the exported mesh. The
  .blend keeps every key unless `in_source` asks to bake them there too.

Both are ordinary Blender edits inside the scope guard (`blender.guarded`).
"""

from __future__ import annotations

import math
import re
from typing import Any

from joinery.tools.blender import guarded
from joinery.tools.blender_export import BAKE_SHAPE_KEYS
from joinery.tools.blender_regions import MACROS

#: An MPFB2 target name: its file name under data/targets without ".target.gz".
_TARGET = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")
#: Printed by a bake in the source, so it is not read as the export's own rows.
SOURCE_SHAPES_PREFIX = "JOINERY_SOURCE_SHAPES:"

BODY_SHAPE = r"""
def _joinery_set_body_shape(name, macros, targets):
    import addon_utils
    if not addon_utils.enable('bl_ext.system.mpfb', default_set=True):
        raise RuntimeError('MPFB2 is not available in this Blender worker.')
    from bl_ext.system.mpfb.services.targetservice import TargetService
    from bl_ext.system.mpfb.services.humanservice import HumanService
    from bl_ext.system.mpfb.services.objectservice import ObjectService
    from bl_ext.system.mpfb.entities.objectproperties import HumanObjectProperties
    body = bpy.data.objects.get(name)
    if body is None or str(getattr(body, 'MPFB_GEN_object_type', '')).lower() != 'basemesh':
        raise RuntimeError(f'{name!r} is not the body mesh of an MPFB2 human.')
    rig = ObjectService.find_object_of_type_amongst_nearest_relatives(body, 'Skeleton')
    related = {body, *body.children_recursive}
    if rig is not None:
        related |= {rig, *[o for o in rig.children_recursive if not o.name.startswith('WGT-')]}
    for obj in related:
        _joinery_also.add('object:' + obj.name)
    was = TargetService.get_macro_info_dict_from_basemesh(body)
    for key, value in macros.items():
        HumanObjectProperties.set_value(key, value, entity_reference=body)
    if macros:
        TargetService.reapply_macro_details(body)
    loaded = {}
    for target, value in targets.items():
        before = TargetService.get_target_value(body, target) if TargetService.has_target(body, target) else 0.0
        if TargetService.has_target(body, target):
            TargetService.set_target_value(body, target, value, delete_target_on_zero=True)
        elif value:
            path = TargetService.target_full_path(target)
            if not path:
                raise RuntimeError(f'MPFB2 has no target named {target!r}.')
            TargetService.load_target(body, path, weight=value, name=target)
        loaded[target] = [round(float(before), 4), value]
    heads = {}
    if rig is not None:
        heads = {b.name: b.head_local.copy() for b in rig.data.bones}
    refit = 'none'
    if rig is not None or any(o is not body for o in related):
        if rig is not None and ObjectService.object_is_generated_rigify_rig(rig) and (
            ObjectService.find_rigify_metarig_by_rig(rig) is None
        ):
            refit = 'clothes only: a generated Rigify rig without its meta-rig cannot be refitted'
        else:
            refit = 'refitted'
        HumanService.refit(body)
    moved = 0.0
    if rig is not None:
        for bone in rig.data.bones:
            if bone.name in heads:
                moved = max(moved, (bone.head_local - heads[bone.name]).length)
    now = TargetService.get_macro_info_dict_from_basemesh(body)
    print('JOINERY_PART_OP:' + _joinery_json.dumps({
        'object': body.name,
        'macros': {k: [round(float(was[k]), 4), round(float(now[k]), 4)] for k in macros if k in was},
        'targets': loaded, 'refit': refit, 'rig_moved_m': round(moved, 5),
    }))
"""

SHAPE_KEYS = (
    BAKE_SHAPE_KEYS
    + r"""
def _joinery_set_shape_values(name, values):
    meshes = [o for o in bpy.data.objects if o.type == 'MESH' and o.data.shape_keys is not None]
    if name:
        meshes = [o for o in meshes if o.name == name]
        if not meshes:
            raise RuntimeError(f'There is no mesh named {name!r} with shape keys in this model.')
    done = {}
    for key, value in values.items():
        found = False
        for obj in meshes:
            block = obj.data.shape_keys.key_blocks.get(key)
            if block is None or block == obj.data.shape_keys.reference_key:
                continue
            if not block.slider_min <= value <= block.slider_max:
                raise RuntimeError(f'{key!r} goes from {block.slider_min} to {block.slider_max}.')
            done.setdefault(obj.name, {})[key] = [round(block.value, 4), value]
            block.value = value
            _joinery_also.add('object:' + obj.name)
            found = True
        if not found:
            raise RuntimeError(f'No mesh here has a shape key named {key!r}.')
    return done

def _joinery_choose_shape_keys(name, values, keep, in_source):
    done = _joinery_set_shape_values(name, values)
    if in_source:
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and obj.data.shape_keys is not None:
                _joinery_also.add('object:' + obj.name)
        _joinery_bake_shape_keys(False, keep, 'SOURCE_PREFIX')
    print('JOINERY_PART_OP:' + _joinery_json.dumps({
        'values': done, 'game_shape_keys': keep, 'baked_in_source': in_source,
    }))
""".replace("SOURCE_PREFIX", SOURCE_SHAPES_PREFIX)
)


def _unit(value: Any, what: str, low: float = 0.0, high: float = 1.0) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ValueError(f"{what} is a number from {low:g} to {high:g}.")
    return float(value)


def check_body_shape(
    macros: dict[str, Any] | None, targets: dict[str, Any] | None
) -> tuple[dict[str, float], dict[str, float]]:
    """Refuse unknown macros, malformed target names and values outside 0-1."""
    macros = dict(macros or {})
    targets = dict(targets or {})
    if not macros and not targets:
        raise ValueError("Give macros, targets or both.")
    unknown = sorted(set(macros) - set(MACROS))
    if unknown:
        raise ValueError(
            f"Unknown MPFB2 macro {', '.join(unknown)}. Use: {', '.join(MACROS)}."
        )
    if len(targets) > 32:
        raise ValueError("Set at most 32 targets at once.")
    for name in targets:
        if not isinstance(name, str) or not _TARGET.fullmatch(name):
            raise ValueError(
                f"{name!r} is not an MPFB2 target name (such as "
                "'l-upperarm-shoulder-muscle-incr')."
            )
    return (
        {k: _unit(v, k) for k, v in macros.items()},
        {k: _unit(v, k) for k, v in targets.items()},
    )


def body_shape_code(
    obj: str, macros: dict[str, float], targets: dict[str, float]
) -> str:
    """Blender code that changes an MPFB2 human's body; the human and its rig may change."""
    call = f"_joinery_set_body_shape({obj!r}, {macros!r}, {targets!r})"
    return guarded(BODY_SHAPE + "\n" + call, objects=(obj,))


def check_shape_keys(
    keep: list[str] | None, values: dict[str, Any] | None
) -> tuple[tuple[str, ...] | None, dict[str, float]]:
    """The game's keys (None for the default) and the values to set, checked."""
    if keep is not None:
        if not isinstance(keep, list) or len(keep) > 256:
            raise ValueError("keep is a list of up to 256 shape key names, or null.")
        for name in keep:
            if not isinstance(name, str) or not name.strip() or len(name) > 63:
                raise ValueError("Each kept shape key is named (1 to 63 characters).")
        keep = list(dict.fromkeys(keep))
    checked: dict[str, float] = {}
    for name, value in dict(values or {}).items():
        if not isinstance(name, str) or not name.strip() or len(name) > 63:
            raise ValueError("Name each shape key whose value is set.")
        checked[name] = _unit(value, name, -10.0, 10.0)
    return (None if keep is None else tuple(keep)), checked


def shape_keys_code(
    obj: str, values: dict[str, float], keep: tuple[str, ...] | None, in_source: bool
) -> str:
    """Blender code that sets shape key values, and with `in_source` bakes the keys the
    game does not keep into the .blend too; the meshes it touches may change."""
    keep_arg = None if keep is None else list(keep)
    call = (
        f"_joinery_choose_shape_keys({obj!r}, {values!r}, {keep_arg!r}, {in_source!r})"
    )
    return guarded(SHAPE_KEYS + "\n" + call, objects=(obj,) if obj else ())
