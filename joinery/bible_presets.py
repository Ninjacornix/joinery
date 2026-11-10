"""Starting points for an art bible's technical sections, and its rules block.

A blueprint names a bible preset (`lowpoly-3d`, `pixel-2d`, ...). The preset
fills only technical, non-taste sections, such as camera numbers, triangle
budgets and UI sizes, and never vision, references, look, palette, sound or
bans. Every filled section starts with a line saying where it came from, so the
user sees a suggestion, not a decision. The user's own answers always win.

Presets live in `templates/bible_presets/<id>.toml`:

    id = "lowpoly-3d"
    name = "Low-poly 3D"
    [sections]      # token -> markdown body, open sections only
    camera = "..."
    [rules]         # merged over the default checked-rules block

The checked-rules block is written here, from one ordered layout with a short
comment per key, so the block in a new bible reads as documentation and parses
with `bible_rules.parse_rules`.
"""

from __future__ import annotations

import copy
import json
import re
import tomllib
from functools import cache
from pathlib import Path
from typing import Any

from joinery import bible_schema
from joinery.bible_rules import BibleRules, parse_rules
from joinery.catalog import ROOT

PRESETS_DIR = ROOT / "templates" / "bible_presets"
NONE = "none"
_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_TOKEN = re.compile(r"\{\{(\w+)\}\}")


def suggested_line(name: str) -> str:
    return f"_Suggested by the {name} preset for this starting point; change freely._"


#: The default checked rules for a new bible. Empty maps are "not checked".
DEFAULT_RULES: dict[str, Any] = {
    "schema": 2,
    "exclude": ["art/reference"],
    "audio": {
        "true_peak_max_dbtp": -1.0,
        "lufs_tolerance": 1.0,
        "lufs": {"audio/music": -16.0, "audio/ambience": -22.0},
        "format": {
            "audio/sfx": {
                "container": "wav",
                "sample_rates": [44100, 48000],
                "channels": [1, 2],
                "bit_depths": [16, 24],
            },
            "audio/music": {
                "container": "ogg",
                "sample_rates": [48000],
                "channels": [2],
            },
        },
    },
    "models": {
        "applied_scale": True,
        "pivot_tolerance_m": 0.02,
        "max_triangles": {"art/characters": 15000},
        "max_bones": {"art/characters": 80},
        "texel_density": {"art": {"px_per_m": 256, "tolerance": 0.2}},
        "pivot": {"art/characters": "bottom-centre", "art/props": "bottom-centre"},
    },
    "materials": {
        "folders": ["art"],
        "roughness": [0.4, 0.95],
        "metallic": [0.0, 1.0],
        "metallic_requires_name": "(?i)metal",
        "albedo_luminance": [0.03, 0.92],
        "albedo_max_outside": 0.01,
        "albedo_suffixes": [
            "_albedo",
            "_basecolor",
            "_basecolour",
            "_diffuse",
            "_color",
            "_colour",
        ],
    },
    "naming": {
        "files": {"art": "^[a-z0-9]+(_[a-z0-9]+)*$"},
        "nodes": {"art": "^[A-Z][A-Za-z0-9]*(_[A-Z][A-Za-z0-9]*){0,2}$"},
    },
    "textures": {
        "folders": ["art"],
        "power_of_two": True,
        "max_size": {"art": 2048, "art/ui": 1024},
    },
    "sprites": {"max_colours": {}},
    "palette": {
        "colours": [],
        "max_delta_e": 12.0,
        "max_off_palette": 0.05,
        "folders": ["art"],
        "roles": {},
    },
    "accessibility": {
        "min_ui_contrast": 4.5,
        "min_text_px": 18,
        "ui_pairs": [],
        "colour_blind_distinct": [],
        "colour_blind_min_delta_e": 10.0,
        "ui_folders": ["ui", "core/ui", "game/ui", "features"],
    },
}

#: Table path -> (key, comment) in print order, plus example lines shown
#: (commented) when the table is an empty map. A path ending in ".*" is a map
#: of tables, one header per entry.
_LAYOUT: tuple[tuple[str, tuple[tuple[str, str], ...], tuple[str, ...]], ...] = (
    (
        "",
        (
            ("schema", "checked-rules format; absent = 1"),
            ("exclude", "folders no check looks at (reference boards)"),
        ),
        (),
    ),
    (
        "audio",
        (
            ("true_peak_max_dbtp", "no audio file may peak above this"),
            ("lufs_tolerance", "allowed distance from a folder's loudness target"),
        ),
        (),
    ),
    ("audio.lufs", (), ('"audio/music" = -16.0',)),
    (
        "audio.format.*",
        (
            ("container", "wav | ogg"),
            ("sample_rates", "Hz"),
            ("channels", "1 = mono, 2 = stereo"),
            ("bit_depths", "WAV only"),
        ),
        ('[audio.format."audio/sfx"]', 'container = "wav"'),
    ),
    (
        "models",
        (
            ("applied_scale", "every mesh and armature has scale 1"),
            ("pivot_tolerance_m", "how far a pivot may sit from where it should"),
        ),
        (),
    ),
    ("models.max_triangles", (), ('"art/characters" = 15000', '"art/props" = 3000')),
    ("models.max_bones", (), ('"art/characters" = 80',)),
    ("models.texel_density", (), ('"art" = { px_per_m = 256, tolerance = 0.2 }',)),
    (
        "models.pivot",
        (),
        ('"art/props" = "bottom-centre"   # bottom-centre | centre | any',),
    ),
    (
        "materials",
        (
            ("folders", "where models and albedo textures are checked"),
            ("roughness", "[low, high] for untextured roughness"),
            ("metallic", "[low, high]"),
            (
                "metallic_requires_name",
                "metallic > 0.05 only on materials named like this",
            ),
            ("albedo_luminance", "relative luminance; outside = pure black or white"),
            ("albedo_max_outside", "share of albedo texture samples allowed outside"),
            ("albedo_suffixes", "image stems that count as albedo textures"),
        ),
        (),
    ),
    ("naming.files", (), ('"art" = "^[a-z0-9]+(_[a-z0-9]+)*$"',)),
    ("naming.nodes", (), ('"art" = "^[A-Z][A-Za-z0-9]*(_[A-Z][A-Za-z0-9]*){0,2}$"',)),
    (
        "textures",
        (
            ("folders", "where image sizes are checked"),
            ("power_of_two", "both sides 2, 4, 8, ... 4096"),
        ),
        (),
    ),
    ("textures.max_size", (), ('"art" = 2048',)),
    (
        "sprites.max_colours",
        (),
        ('"art/sprites" = 32   # distinct opaque colours per image',),
    ),
    (
        "palette",
        (
            ("colours", 'e.g. ["#2b2d42", "#8d99ae", "#edf2f4", "#ef233c"]'),
            ("max_delta_e", "CIEDE2000 distance that still counts as on-palette"),
            ("max_off_palette", "share of image colours allowed beyond that"),
            ("folders", "where images are checked"),
        ),
        (),
    ),
    (
        "palette.roles",
        (),
        (
            'primary = "#2b2d42"   # roles join `colours` for the check',
            'ui_base = "#2b2d42"',
            'ui_text = "#edf2f4"',
            'accent = "#ef233c"',
        ),
    ),
    (
        "accessibility",
        (
            ("min_ui_contrast", "WCAG 2.x ratio for UI text on its background"),
            ("min_text_px", "at 1080p; scaled by the project's viewport height"),
            ("ui_pairs", 'e.g. [["ui_text", "ui_base"]], roles or hexes'),
            ("colour_blind_distinct", "roles kept apart for protan, deutan, tritan"),
            ("colour_blind_min_delta_e", "CIEDE2000 those roles must keep"),
            ("ui_folders", ".tres and .tscn checked here"),
        ),
        (),
    ),
)


#: Layout tables that hold named keys (not folder or role maps).
KEY_TABLES = frozenset(
    path for path, keys, _examples in _LAYOUT if keys and not path.endswith(".*")
)


def _value(value: Any) -> str:
    """A TOML value. Strings use JSON escapes, which TOML basic strings share."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(f"{_key(k)} = {_value(v)}" for k, v in value.items())
        return "{ " + inner + " }" if inner else "{}"
    raise TypeError(f"cannot write {value!r} as TOML")


def _key(key: str, quote: bool = False) -> str:
    bare = not quote and re.fullmatch(r"[A-Za-z0-9_-]+", key)
    return key if bare else json.dumps(key)


def _header(path: str) -> str:
    return ".".join(_key(part) for part in path.split("."))


def _lookup(rules: dict[str, Any], path: str) -> Any:
    node: Any = rules
    for part in path.split(".") if path else []:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _line(key: str, value: Any, comment: str, quote: bool = False) -> str:
    text = f"{_key(key, quote)} = {_value(value)}"
    if not comment:
        return text
    return text + " " * max(2, 32 - len(text)) + "# " + comment


def _family_chunks(rules: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """(table path, lines) per layout entry, in order."""
    chunks: list[tuple[str, list[str]]] = []
    for path, keys, examples in _LAYOUT:
        if path.endswith(".*"):
            base = path.removesuffix(".*")
            tables = _lookup(rules, base) or {}
            lines: list[str] = []
            if not tables:
                lines += [f"# {example}" for example in examples]
            for name, table in tables.items():
                if lines:
                    lines.append("")
                lines.append(f"[{_header(base)}.{json.dumps(name)}]")
                comments = dict(keys)
                for key, value in table.items():
                    lines.append(_line(key, value, comments.get(key, "")))
            chunks.append((path, lines))
            continue
        table = _lookup(rules, path)
        lines = [f"[{_header(path)}]"] if path else []
        if keys:
            for key, comment in keys:
                if isinstance(table, dict) and key in table:
                    lines.append(_line(key, table[key], comment))
        else:
            entries = table if isinstance(table, dict) else {}
            if entries:
                # Folder maps quote every key, so "art" reads like "art/ui".
                quote = path != "palette.roles"
                lines += [_line(k, v, "", quote) for k, v in entries.items()]
            else:
                lines += [f"# {example}" for example in examples]
        chunks.append((path, lines))
    return chunks


def write_rules(rules: dict[str, Any]) -> str:
    """The commented TOML text for a rules dict, in the layout's order."""
    BibleRules.model_validate(rules)  # unknown keys fail here, not silently
    blocks = ["\n".join(lines) for _path, lines in _family_chunks(rules) if lines]
    return "\n\n".join(blocks) + "\n"


def family_chunks(rules: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """Layout chunks, for the upgrade to add only what a bible lacks."""
    return _family_chunks(rules)


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Deep merge: tables merge key by key, everything else is replaced."""
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Preset:
    def __init__(
        self, preset_id: str, name: str, sections: dict[str, str], rules: dict[str, Any]
    ) -> None:
        self.id = preset_id
        self.name = name
        self.sections = sections
        self.rules = rules


def _load(path: Path) -> Preset:
    raw = tomllib.loads(path.read_text())
    unknown = set(raw) - {"id", "name", "sections", "rules"}
    if unknown:
        raise ValueError(f"{path.name}: unknown keys {sorted(unknown)}")
    preset_id = str(raw.get("id", ""))
    if preset_id != path.stem or not _ID.match(preset_id):
        raise ValueError(f"{path.name}: id must be {path.stem!r}")
    sections = {str(k): str(v) for k, v in dict(raw.get("sections", {})).items()}
    for token in sections:
        if bible_schema.section_for(token) is None:
            raise ValueError(f"{path.name}: {token!r} is not a bible section")
        if bible_schema.is_taste(token):
            raise ValueError(
                f"{path.name}: {token!r} is a matter of taste; presets never fill it"
            )
        if token == "checked_rules":
            raise ValueError(f"{path.name}: rules go under [rules]")
    rules = dict(raw.get("rules", {}))
    try:
        BibleRules.model_validate(merge(DEFAULT_RULES, rules))
    except ValueError as error:
        raise ValueError(f"{path.name}: invalid [rules]: {error}") from error
    return Preset(preset_id, str(raw.get("name", preset_id)), sections, rules)


@cache
def _presets() -> dict[str, Preset]:
    found: dict[str, Preset] = {}
    for path in sorted(PRESETS_DIR.glob("*.toml")):
        preset = _load(path)
        found[preset.id] = preset
    if NONE not in found:
        found[NONE] = Preset(NONE, "None", {}, {})
    return found


def get(preset: str | None) -> Preset:
    presets = _presets()
    key = preset or NONE
    if key not in presets:
        raise ValueError(
            f"Unknown bible preset {key!r}; known: {', '.join(sorted(presets))}"
        )
    return presets[key]


def list_presets() -> list[dict[str, str]]:
    """Every preset as {id, name}, `none` first."""
    presets = _presets()
    order = [NONE, *sorted(k for k in presets if k != NONE)]
    return [{"id": k, "name": presets[k].name} for k in order]


def apply_preset(answers: dict[str, str], preset: str | None) -> dict[str, str]:
    """Preset section bodies under the user's answers; the answers win.

    Raises ValueError when the preset would fill a taste token.
    """
    chosen = get(preset)
    values: dict[str, str] = {}
    for token, body in chosen.sections.items():
        if bible_schema.is_taste(token):
            raise ValueError(f"preset {chosen.id!r} may not fill {token!r}")
        values[token] = suggested_line(chosen.name) + "\n\n" + body.strip()
    for key, answer in answers.items():
        if str(answer).strip():
            values[key] = str(answer)
    return values


def rules_dict(preset: str | None) -> dict[str, Any]:
    return merge(DEFAULT_RULES, get(preset).rules)


def rules_block(preset: str | None) -> str:
    """The complete TOML text (no fences) for `{{checked_rules}}`."""
    text = write_rules(rules_dict(preset))
    parse_rules(text)  # never hand out a block that does not parse
    return text


def unset_marker(token: str) -> str:
    return bible_schema.unset_marker(token)


def fill(template: str, answers: dict[str, str], preset: str | None = None) -> str:
    """Render a bible template: answers, then preset suggestions, then markers.

    `{{checked_rules}}` always becomes the rules block (the default merged with
    the preset); every other token without a value becomes its unset marker,
    so no `{{token}}` survives into a project.
    """
    values = apply_preset(answers, preset)
    block = rules_block(preset).rstrip("\n")

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == "checked_rules":
            return block
        value = (values.get(key) or "").strip()
        return value if value else unset_marker(key)

    return _TOKEN.sub(substitute, template)
