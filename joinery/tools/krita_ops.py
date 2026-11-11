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
    layer = document.createFilterLayer(name, adjustment, everywhere)
    _joinery_add(document, layer, opacity)
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
