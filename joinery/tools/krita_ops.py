"""Typed Krita operations for a texture map painted as a .kra document.

A map made editable (`make_map_editable`) is a Krita document whose bottom
layer holds the model's pixels. Each typed operation adds its own layer above,
so earlier work is never painted over and every step stays a layer the user
can hide or delete in Krita:

* ``fill``: a paint layer filled with one colour, everywhere or in a rectangle.
* ``stroke``: a vector layer with one stroked path (round caps and joins),
  drawn by Krita's own vector renderer.
* ``adjust``: a Krita filter layer (invert, levels, hue/saturation/value, blur,
  desaturate) that changes everything below it.
* ``palette_map``: a paint layer that covers every pixel further than
  ``max_delta_e`` (CIEDE2000) from the palette with its nearest palette colour;
  pixels already close enough stay as they are, showing through.
* ``resize`` (``edit_image`` only, never a model's map): scales the whole
  document, every layer, to a new size.

Colours are sRGB, 0 to 1; positions and sizes are in pixels from the top left.

``operate_layer`` (LAYER_OPS) works on the stack itself, by layer name: add a
paint or group layer, rename, reorder, hide, set opacity or blending mode,
merge down, duplicate, delete, and add a fill layer or a filter layer with the
same typed filters as ``adjust``.
"""

from __future__ import annotations

import inspect
import math
import re
from typing import Any

from joinery import bible_rules

#: Each operation's parameters; `op` is the operation itself.
PAINT_OPS: dict[str, frozenset[str]] = {
    "fill": frozenset({"color", "opacity", "rect", "name"}),
    "stroke": frozenset({"points", "color", "width", "opacity", "name"}),
    "adjust": frozenset({"filter", "params", "opacity", "name"}),
    "palette_map": frozenset({"colours", "max_delta_e", "opacity", "name"}),
}
#: A Krita image of its own (``edit_image``) may also be resized.
IMAGE_OPS: dict[str, frozenset[str]] = {
    **PAINT_OPS,
    "resize": frozenset({"width", "height", "name"}),
}
MAX_SIDE = 16384
MAX_PALETTE = 64
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
#: Filter layers `adjust` makes: Joinery's name -> (Krita filter id, parameters).
FILTERS: dict[str, tuple[str, frozenset[str]]] = {
    "invert": ("invert", frozenset()),
    "levels": (
        "levels",
        frozenset({"in_low", "in_high", "gamma", "out_low", "out_high"}),
    ),
    "hue_saturation": ("hsvadjustment", frozenset({"hue", "saturation", "value"})),
    "blur": ("gaussian blur", frozenset({"radius"})),
    "desaturate": ("desaturate", frozenset()),
}
#: The bottom layer: the map's pixels as the model had them.
BASE_LAYER = "Map"
MAX_OPS = 32
MAX_POINTS = 512


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _colour(value: Any) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) not in (3, 4)
        or not all(_finite(x) and 0 <= x <= 1 for x in value)
    ):
        raise ValueError("color is three or four numbers from 0 to 1 (sRGB).")
    return [float(x) for x in value]


def _unit(value: Any, what: str) -> float:
    if not _finite(value) or not 0 <= value <= 1:
        raise ValueError(f"{what} is a number from 0 to 1.")
    return float(value)


def _filter(name: Any, params: Any) -> tuple[str, dict[str, float]]:
    if name not in FILTERS:
        raise ValueError(f"filter is one of: {', '.join(FILTERS)}.")
    given = dict(params or {})
    unknown = set(given) - FILTERS[name][1]
    if unknown:
        raise ValueError(
            f"{name} does not take {', '.join(sorted(unknown))}. "
            f"It takes: {', '.join(sorted(FILTERS[name][1])) or 'nothing'}."
        )
    if not all(_finite(v) for v in given.values()):
        raise ValueError(f"{name} parameters are numbers.")
    checked = {k: float(v) for k, v in given.items()}
    for key in ("in_low", "in_high", "out_low", "out_high"):
        if key in checked and not 0 <= checked[key] <= 1:
            raise ValueError(f"{key} is from 0 to 1.")
    if checked.get("gamma", 1.0) <= 0:
        raise ValueError("gamma must be above 0.")
    if not -0.5 <= checked.get("hue", 0) <= 0.5:
        raise ValueError("hue turns from -0.5 to 0.5.")
    for key in ("saturation", "value"):
        if not -1 <= checked.get(key, 0) <= 1:
            raise ValueError(f"{key} changes from -1 to 1.")
    if not 0 < checked.get("radius", 1) <= 100:
        raise ValueError("radius is above 0 and at most 100 pixels.")
    return name, checked


def check_ops(
    ops: Any, allowed: dict[str, frozenset[str]] = PAINT_OPS
) -> list[dict[str, Any]]:
    """Refuse unknown operations and bad values before Krita starts."""
    if not isinstance(ops, list) or not 1 <= len(ops) <= MAX_OPS:
        raise ValueError(f"ops is a list of 1 to {MAX_OPS} operations.")
    checked: list[dict[str, Any]] = []
    for number, given in enumerate(ops, 1):
        if not isinstance(given, dict) or given.get("op") not in allowed:
            raise ValueError(f"Operation {number}: op is one of {', '.join(allowed)}.")
        op = str(given["op"])
        unknown = set(given) - {"op"} - allowed[op]
        if unknown:
            raise ValueError(
                f"Operation {number} ({op}) does not take {', '.join(sorted(unknown))}."
            )
        row: dict[str, Any] = {"op": op}
        name = given.get(
            "name",
            {"palette_map": "Palette map", "resize": "Resize"}.get(
                op, f"{op.capitalize()} {number}"
            ),
        )
        if not isinstance(name, str) or not name.strip() or len(name) > 63:
            raise ValueError(f"Operation {number}: name is 1 to 63 characters.")
        row["name"] = name.strip()
        row["opacity"] = _unit(given.get("opacity", 1.0), "opacity")
        if op in {"fill", "stroke"}:
            if "color" not in given:
                raise ValueError(f"Operation {number} ({op}) needs a color.")
            row["color"] = _colour(given["color"])
        if op == "fill" and given.get("rect") is not None:
            rect = given["rect"]
            if (
                not isinstance(rect, list)
                or len(rect) != 4
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in rect)
                or rect[2] <= 0
                or rect[3] <= 0
            ):
                raise ValueError(
                    f"Operation {number}: rect is [x, y, width, height] in whole pixels."
                )
            row["rect"] = list(rect)
        if op == "stroke":
            points = given.get("points")
            if (
                not isinstance(points, list)
                or not 2 <= len(points) <= MAX_POINTS
                or not all(
                    isinstance(p, list) and len(p) == 2 and all(_finite(v) for v in p)
                    for p in points
                )
            ):
                raise ValueError(
                    f"Operation {number}: points is 2 to {MAX_POINTS} [x, y] pairs in pixels."
                )
            row["points"] = [[float(x), float(y)] for x, y in points]
            width = given.get("width", 4)
            if not _finite(width) or not 0 < width <= 1024:
                raise ValueError(f"Operation {number}: width is above 0, in pixels.")
            row["width"] = float(width)
        if op == "adjust":
            row["filter"], row["params"] = _filter(
                given.get("filter"), given.get("params")
            )
        if op == "palette_map":
            colours = given.get("colours")
            if (
                not isinstance(colours, list)
                or not 1 <= len(colours) <= MAX_PALETTE
                or not all(isinstance(c, str) and _HEX.match(c) for c in colours)
            ):
                raise ValueError(
                    f'Operation {number}: colours is 1 to {MAX_PALETTE} "#rrggbb" colours.'
                )
            row["colours"] = [c.lower() for c in colours]
            limit = given.get("max_delta_e", 12.0)
            if not _finite(limit) or not 0 <= limit <= 100:
                raise ValueError(f"Operation {number}: max_delta_e is from 0 to 100.")
            row["max_delta_e"] = float(limit)
        if op == "resize":
            for key in ("width", "height"):
                value = given.get(key)
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not 1 <= value <= MAX_SIDE
                ):
                    raise ValueError(
                        f"Operation {number}: {key} is whole pixels, 1 to {MAX_SIDE}."
                    )
                row[key] = value
            row.pop("opacity")
        checked.append(row)
    return checked


# Shared by every Krita map operation. Krita's RGBA U8 layers store B, G, R, A.
HELPERS = r"""
from krita import Selection as _JoinerySelection

def _joinery_top(document):
    nodes = document.rootNode().childNodes()
    return nodes[-1] if nodes else None

def _joinery_add(document, node, opacity):
    node.setOpacity(round(opacity * 255))
    document.rootNode().addChildNode(node, _joinery_top(document))

def _joinery_byte(value):
    return max(0, min(255, round(value * 255)))

def _joinery_fill(document, name, color, opacity, rect):
    width, height = document.width(), document.height()
    x, y, w, h = rect or [0, 0, width, height]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    if x1 <= x0 or y1 <= y0:
        raise RuntimeError(f'The fill {name!r} lies outside the {width}x{height} map.')
    layer = document.createNode(name, 'paintlayer')
    r, g, b = (_joinery_byte(c) for c in color[:3])
    a = _joinery_byte(color[3]) if len(color) > 3 else 255
    layer.setPixelData(bytes([b, g, r, a]) * ((x1 - x0) * (y1 - y0)), x0, y0, x1 - x0, y1 - y0)
    _joinery_add(document, layer, opacity)

def _joinery_stroke(document, name, points, color, width, opacity):
    # Krita reads vector shapes in points; the document's resolution turns
    # pixels into points, so the stroke lands on the pixels it names.
    scale = 72.0 / document.resolution()
    path = 'M ' + ' L '.join(f'{x * scale:.4f} {y * scale:.4f}' for x, y in points)
    r, g, b = (_joinery_byte(c) for c in color[:3])
    alpha = color[3] if len(color) > 3 else 1.0
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{document.width() * scale:.4f}pt" height="{document.height() * scale:.4f}pt">'
        f'<path d="{path}" fill="none" stroke="#{r:02x}{g:02x}{b:02x}" stroke-opacity="{alpha:.4f}" '
        f'stroke-width="{width * scale:.4f}" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )
    layer = document.createVectorLayer(name)
    _joinery_add(document, layer, opacity)
    if not layer.addShapesFromSvg(svg):
        raise RuntimeError(f'Krita could not draw the stroke {name!r}.')

def _joinery_adjust(document, name, kind, params, opacity):
    _joinery_add(document, _joinery_filter_layer(document, name, kind, params), opacity)

def _joinery_filter_layer(document, name, kind, params):
    if kind == 'invert':
        filter_id, config = 'invert', {}
    elif kind == 'levels':
        low, high = params.get('in_low', 0.0), params.get('in_high', 1.0)
        gamma = params.get('gamma', 1.0)
        out_low, out_high = params.get('out_low', 0.0), params.get('out_high', 1.0)
        filter_id = 'levels'
        config = {
            'mode': 'lightness', 'lightness': f'{low};{high};{gamma};{out_low};{out_high}',
            'blackvalue': round(low * 255), 'whitevalue': round(high * 255),
            'gammavalue': gamma, 'outblackvalue': round(out_low * 255),
            'outwhitevalue': round(out_high * 255),
        }
    elif kind == 'hue_saturation':
        filter_id = 'hsvadjustment'
        config = {
            'type': 1, 'h': round(params.get('hue', 0.0) * 360),
            's': round(params.get('saturation', 0.0) * 100),
            'v': round(params.get('value', 0.0) * 100),
        }
    elif kind == 'blur':
        radius = params.get('radius', 2.0)
        filter_id, config = 'gaussian blur', {'horizRadius': radius, 'vertRadius': radius}
    elif kind == 'desaturate':
        filter_id, config = 'desaturate', {}
    else:
        raise RuntimeError(f'Unknown adjustment {kind!r}.')
    adjustment = Krita.instance().filter(filter_id)
    if adjustment is None:
        raise RuntimeError(f'This Krita has no {filter_id!r} filter.')
    settings = adjustment.configuration()
    for key, value in config.items():
        settings.setProperty(key, value)
    adjustment.setConfiguration(settings)
    everywhere = _JoinerySelection()
    everywhere.select(0, 0, document.width(), document.height(), 255)
    return document.createFilterLayer(name, adjustment, everywhere)
"""


#: CIEDE2000 inside Krita: the very functions the art bible measures with.
COLOUR_MATHS = "\n".join(
    [
        "import math",
        "Sequence = list",
        *(
            inspect.getsource(function)
            for function in (
                bible_rules._channel_linear,
                bible_rules._lab_linear,
                bible_rules._lab,
                bible_rules.delta_e_2000,
            )
        ),
    ]
)

PALETTE_HELPERS = r"""
def _joinery_palette_map(document, name, colours, limit, opacity):
    # Every pixel further than `limit` from the palette gets its nearest palette
    # colour on a layer of its own; the rest stays transparent, so the painting
    # below shows wherever it already matches. Colours are judged on a 6-bit
    # grid, so a large texture stays quick.
    document.refreshProjection()
    document.waitForDone()
    width, height = document.width(), document.height()
    data = bytes(document.pixelData(0, 0, width, height))
    palette = [(int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)) for c in colours]
    labs = [_lab(p) for p in palette]
    cache = {}
    out = bytearray(len(data))
    changed = 0
    for i in range(0, len(data), 4):
        alpha = data[i + 3]
        if alpha < 8:
            continue
        key = (data[i + 2] & 0xFC, data[i + 1] & 0xFC, data[i] & 0xFC)
        hit = cache.get(key, False)
        if hit is False:
            lab = _lab((key[0] | 2, key[1] | 2, key[2] | 2))
            distances = [delta_e_2000(lab, p) for p in labs]
            best = min(range(len(labs)), key=distances.__getitem__)
            hit = cache[key] = palette[best] if distances[best] > limit else None
        if hit is not None:
            out[i] = hit[2]
            out[i + 1] = hit[1]
            out[i + 2] = hit[0]
            out[i + 3] = alpha
            changed += 1
    layer = document.createNode(name, 'paintlayer')
    layer.setPixelData(bytes(out), 0, 0, width, height)
    _joinery_add(document, layer, opacity)
    print('JOINERY_PALETTE_MAP:%d/%d' % (changed, width * height))

def _joinery_resize(document, width, height):
    document.scaleImage(width, height, int(document.xRes()), int(document.yRes()), 'Bicubic')
    document.waitForDone()
"""


def paint_code(ops: list[dict[str, Any]]) -> str:
    """Krita code (for the recipe's open `document`) that applies checked operations."""
    lines = [HELPERS]
    if any(row["op"] in {"palette_map", "resize"} for row in ops):
        # The palette helper only calls the colour maths when it runs.
        lines += [COLOUR_MATHS, PALETTE_HELPERS]
    for row in ops:
        if row["op"] == "resize":
            lines.append(
                f"_joinery_resize(document, {row['width']!r}, {row['height']!r})"
            )
        elif row["op"] == "palette_map":
            lines.append(
                f"_joinery_palette_map(document, {row['name']!r}, {row['colours']!r}, "
                f"{row['max_delta_e']!r}, {row['opacity']!r})"
            )
        elif row["op"] == "fill":
            lines.append(
                f"_joinery_fill(document, {row['name']!r}, {row['color']!r}, "
                f"{row['opacity']!r}, {row.get('rect')!r})"
            )
        elif row["op"] == "stroke":
            lines.append(
                f"_joinery_stroke(document, {row['name']!r}, {row['points']!r}, "
                f"{row['color']!r}, {row['width']!r}, {row['opacity']!r})"
            )
        else:
            lines.append(
                f"_joinery_adjust(document, {row['name']!r}, {row['filter']!r}, "
                f"{row['params']!r}, {row['opacity']!r})"
            )
    lines += ["document.refreshProjection()", "document.waitForDone()"]
    return "\n".join(lines) + "\n"


def seed_code(image: str) -> str:
    """Krita code that starts a map's document from the model's own pixels.

    `image` is the immutable copy (under recipes/inputs/) the recipe lists as
    its source, so the document replays from exactly those pixels.
    """
    return "\n".join(
        [
            f"document = app.openDocument(os.path.abspath({image!r}))",
            f"assert document is not None, 'Krita could not open ' + {image!r}",
            "document.waitForDone()",
            f"document.rootNode().childNodes()[0].setName({BASE_LAYER!r})",
            "",
        ]
    )


# --- operate_layer: the layer stack of a Krita document ----------------------------

#: Each layer operation's parameters; `op` is the operation itself. Layers are
#: named; a name must pick out exactly one layer anywhere in the document.
LAYER_OPS: dict[str, frozenset[str]] = {
    "add": frozenset({"name", "kind", "above", "into", "opacity", "blend"}),
    "rename": frozenset({"layer", "to"}),
    "reorder": frozenset({"layer", "above", "position"}),
    "hide": frozenset({"layer", "hidden"}),
    "opacity": frozenset({"layer", "opacity"}),
    "blend": frozenset({"layer", "mode"}),
    "merge": frozenset({"layer"}),
    "duplicate": frozenset({"layer", "name"}),
    "delete": frozenset({"layer"}),
    "fill_layer": frozenset({"name", "color", "above", "into", "opacity", "blend"}),
    "filter_layer": frozenset(
        {"name", "filter", "params", "above", "into", "opacity", "blend"}
    ),
}
#: Operations that make a new layer, named by "name"; the rest change "layer".
NEW_LAYER_OPS = frozenset({"add", "fill_layer", "filter_layer"})
#: Blending modes by Joinery's name -> Krita's composite op id. Krita accepts
#: any text as a mode, so only these are let through.
BLEND_MODES: dict[str, str] = {
    "normal": "normal",
    "multiply": "multiply",
    "screen": "screen",
    "overlay": "overlay",
    "add": "add",
    "subtract": "subtract",
    "darken": "darken",
    "lighten": "lighten",
    "color_dodge": "dodge",
    "color_burn": "burn",
    "soft_light": "soft_light",
    "hard_light": "hard_light",
    "difference": "diff",
    "hue": "hue",
    "saturation": "saturation",
    "color": "color",
    "luminosity": "luminize",
    "erase": "erase",
}
LAYER_KINDS = ("paint", "group")


def _layer_name(value: Any, what: str, number: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 63:
        raise ValueError(
            f"Operation {number}: {what} is a layer name, 1 to 63 characters."
        )
    return value.strip()


def _blend(value: Any, number: int) -> str:
    if value not in BLEND_MODES:
        raise ValueError(
            f"Operation {number}: the blend mode is one of: {', '.join(BLEND_MODES)}."
        )
    return BLEND_MODES[str(value)]


def check_layer_ops(ops: Any) -> list[dict[str, Any]]:
    """Refuse unknown layer operations and bad values before Krita starts."""
    if not isinstance(ops, list) or not 1 <= len(ops) <= MAX_OPS:
        raise ValueError(f"ops is a list of 1 to {MAX_OPS} operations.")
    checked: list[dict[str, Any]] = []
    for number, given in enumerate(ops, 1):
        if not isinstance(given, dict) or given.get("op") not in LAYER_OPS:
            raise ValueError(
                f"Operation {number}: op is one of {', '.join(LAYER_OPS)}."
            )
        op = str(given["op"])
        unknown = set(given) - {"op"} - LAYER_OPS[op]
        if unknown:
            raise ValueError(
                f"Operation {number} ({op}) does not take {', '.join(sorted(unknown))}."
            )
        row: dict[str, Any] = {"op": op}
        for key in ("layer", "name", "to", "above", "into"):
            if key in LAYER_OPS[op] and given.get(key) is not None:
                row[key] = _layer_name(given[key], key, number)
        needed = "name" if op in NEW_LAYER_OPS else "layer"
        if needed not in row:
            raise ValueError(f"Operation {number} ({op}) needs {needed}.")
        if op == "rename" and "to" not in row:
            raise ValueError(f"Operation {number} (rename) needs to, the new name.")
        if "above" in row and "into" in row:
            raise ValueError(f"Operation {number}: give above or into, not both.")
        if op == "add":
            kind = given.get("kind", "paint")
            if kind not in LAYER_KINDS:
                raise ValueError(f"Operation {number}: kind is paint or group.")
            row["kind"] = kind
        if op == "reorder":
            position = given.get("position")
            if ("above" in row) == (position is not None):
                raise ValueError(
                    f"Operation {number} (reorder): give above (a layer) or "
                    'position ("top" or "bottom").'
                )
            if position is not None:
                if position not in ("top", "bottom"):
                    raise ValueError(
                        f'Operation {number}: position is "top" or "bottom".'
                    )
                row["position"] = position
            if row.get("above") == row["layer"]:
                raise ValueError(f"Operation {number}: a layer cannot go above itself.")
        if op == "hide":
            hidden = given.get("hidden", True)
            if not isinstance(hidden, bool):
                raise ValueError(f"Operation {number}: hidden is true or false.")
            row["hidden"] = hidden
        if op == "opacity" and "opacity" not in given:
            raise ValueError(f"Operation {number} (opacity) needs an opacity.")
        if "opacity" in LAYER_OPS[op] and "opacity" in given:
            row["opacity"] = _unit(given["opacity"], "opacity")
        if op == "blend":
            row["mode"] = _blend(given.get("mode"), number)
        elif "blend" in given:
            row["mode"] = _blend(given["blend"], number)
        if op == "duplicate":
            row.setdefault("name", (row["layer"] + " copy")[:63])
        if op == "fill_layer":
            colour = given.get("color")
            if (
                not isinstance(colour, list)
                or len(colour) != 3
                or not all(_finite(x) and 0 <= x <= 1 for x in colour)
            ):
                raise ValueError(
                    f"Operation {number}: color is three numbers from 0 to 1 (sRGB); "
                    "set how much it covers with opacity."
                )
            row["color"] = [float(x) for x in colour]
        if op == "filter_layer":
            row["filter"], row["params"] = _filter(
                given.get("filter"), given.get("params")
            )
        checked.append(row)
    return checked


LAYER_HELPERS = r"""
from krita import InfoObject as _JoineryInfo

def _joinery_nodes(node):
    for child in node.childNodes():
        yield child
        yield from _joinery_nodes(child)

def _joinery_layer(document, name):
    found = [n for n in _joinery_nodes(document.rootNode()) if n.name() == name]
    if not found:
        names = ', '.join(n.name() for n in _joinery_nodes(document.rootNode()))
        raise RuntimeError(f'There is no layer {name!r}. Layers: {names}.')
    if len(found) > 1:
        raise RuntimeError(f'{len(found)} layers are called {name!r}; rename one first.')
    return found[0]

def _joinery_free(document, name):
    if any(n.name() == name for n in _joinery_nodes(document.rootNode())):
        raise RuntimeError(f'A layer is already called {name!r}.')

def _joinery_on_top(parent, node):
    # A child added with no layer to go above lands on top of its parent.
    parent.addChildNode(node, None)

def _joinery_place(document, node, row):
    if row.get('above') is not None:
        anchor = _joinery_layer(document, row['above'])
        anchor.parentNode().addChildNode(node, anchor)
    elif row.get('into') is not None:
        group = _joinery_layer(document, row['into'])
        if group.type() != 'grouplayer':
            raise RuntimeError(f"{row['into']!r} is not a group layer.")
        _joinery_on_top(group, node)
    else:
        _joinery_on_top(document.rootNode(), node)

def _joinery_look(node, row):
    if 'opacity' in row:
        node.setOpacity(round(row['opacity'] * 255))
    if 'mode' in row:
        node.setBlendingMode(row['mode'])
        if node.blendingMode() != row['mode']:
            raise RuntimeError(f"Krita did not take the blending mode {row['mode']!r}.")

def _joinery_move(node, parent, above):
    # Taking a group out and adding it back loses its layers in Krita 5.2, so
    # a layer moves as its own copy (children, masks, settings and all).
    copy = node.duplicate()
    copy.setName(node.name())
    parent.addChildNode(copy, above)
    node.remove()
    return copy

def _joinery_inside(node, other):
    while other is not None:
        if other == node:
            return True
        other = other.parentNode()
    return False

def _joinery_rgb_xml(document, color):
    # A colour as Krita itself writes one into a fill layer's settings.
    if document.colorModel() != 'RGBA':
        raise RuntimeError('A fill layer needs an RGBA document; this one is ' + document.colorModel() + '.')
    r, g, b = color
    return (
        '<!DOCTYPE color><color>'
        f'<RGB r="{r!r}" g="{g!r}" b="{b!r}" space="{document.colorProfile()}"/>'
        '</color>'
    )

def _joinery_layer_op(document, row):
    op = row['op']
    if op in ('add', 'fill_layer', 'filter_layer'):
        _joinery_free(document, row['name'])
        if op == 'add':
            kind = 'paintlayer' if row['kind'] == 'paint' else 'grouplayer'
            node = document.createNode(row['name'], kind)
        elif op == 'fill_layer':
            settings = _JoineryInfo()
            settings.setProperty('color', _joinery_rgb_xml(document, row['color']))
            everywhere = _JoinerySelection()
            everywhere.select(0, 0, document.width(), document.height(), 255)
            node = document.createFillLayer(row['name'], 'color', settings, everywhere)
        else:
            node = _joinery_filter_layer(document, row['name'], row['filter'], row['params'])
        if node is None:
            raise RuntimeError(f"Krita could not make the layer {row['name']!r}.")
        _joinery_place(document, node, row)
        _joinery_look(node, row)
        document.waitForDone()
        return
    node = _joinery_layer(document, row['layer'])
    parent = node.parentNode()
    if op == 'rename':
        if row['to'] != row['layer']:
            _joinery_free(document, row['to'])
        node.setName(row['to'])
    elif op == 'reorder':
        if row.get('above') is not None:
            anchor = _joinery_layer(document, row['above'])
            if _joinery_inside(node, anchor):
                raise RuntimeError(f"{row['layer']!r} cannot go inside itself.")
            _joinery_move(node, anchor.parentNode(), anchor)
        elif row['position'] == 'top':
            _joinery_move(node, parent, None)
        else:
            first = parent.childNodes()[0]
            if first != node:
                # Krita places a layer above another or on top, never at the
                # bottom: put it above the lowest layer, then that one above it.
                moved = _joinery_move(node, parent, first)
                _joinery_move(first, parent, moved)
    elif op == 'hide':
        node.setVisible(not row['hidden'])
    elif op in ('opacity', 'blend'):
        _joinery_look(node, row)
    elif op == 'merge':
        siblings = parent.childNodes()
        index = next(i for i, n in enumerate(siblings) if n == node)
        if index == 0:
            raise RuntimeError(f"{row['layer']!r} has no layer below it to merge into.")
        below = siblings[index - 1].name()
        node.mergeDown()
        document.waitForDone()
        _joinery_layer(document, below)
    elif op == 'duplicate':
        _joinery_free(document, row['name'])
        copy = node.duplicate()
        copy.setName(row['name'])
        parent.addChildNode(copy, node)
    elif op == 'delete':
        if parent == document.rootNode() and len(parent.childNodes()) == 1:
            raise RuntimeError('A document keeps at least one layer.')
        node.remove()
    document.waitForDone()
"""


def layer_code(ops: list[dict[str, Any]]) -> str:
    """Krita code (for the recipe's open `document`) that applies checked layer operations."""
    return "\n".join(
        [
            HELPERS,
            LAYER_HELPERS,
            f"for _row in {ops!r}:",
            "    _joinery_layer_op(document, _row)",
            "document.refreshProjection()",
            "document.waitForDone()",
            "",
        ]
    )


def describe_layer_op(row: dict[str, Any]) -> str:
    """One layer operation, as the recipe's note lists it."""
    op = row["op"]
    if op in NEW_LAYER_OPS:
        kind = {"add": row.get("kind", "paint"), "fill_layer": "fill"}.get(
            op, str(row.get("filter"))
        )
        return f"add {kind} layer {row['name']!r}"
    if op == "rename":
        return f"rename {row['layer']!r} to {row['to']!r}"
    if op == "reorder":
        where = (
            f"above {row['above']!r}" if "above" in row else f"to the {row['position']}"
        )
        return f"move {row['layer']!r} {where}"
    if op == "hide":
        return f"{'hide' if row['hidden'] else 'show'} {row['layer']!r}"
    if op == "opacity":
        return f"opacity of {row['layer']!r} {row['opacity']:g}"
    if op == "blend":
        return f"blend {row['layer']!r} {row['mode']}"
    if op == "duplicate":
        return f"duplicate {row['layer']!r} as {row['name']!r}"
    return f"{op} {row['layer']!r}"
