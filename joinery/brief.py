"""The game brief: what the game is, drafted from a pitch and the game's setup.

Every game project can have a `design/brief.md`: pitch, core loop, verbs,
controls, platforms, online, saving, scope. The art bible says how the game
looks and sounds; the brief says what it is and how it plays. It sits outside
`art/` and `audio/`, so it is not an asset and needs no lock entry, and it is
never an approval gate: it directs, it does not block.

The draft comes from three places, and nothing else:

- the user's own answers (the wizard asks only for a one-line pitch and,
  optionally, the audience);
- the starting point (blueprint) the project was made from, which says how
  the game it contains plays: genre, core loop, camera, verbs, input map;
- the features the user picked, which decide facts such as online play,
  saving, gamepad support and languages.

Nothing here invents taste. Whatever none of those three answers stays
`_Not decided yet._`, and Journeyman asks about it when it matters.

The last section holds the same decisions as a small TOML block (the facts), so
Journeyman can compare the brief with the game as it is set up and offer one-
line suggestions ("The brief says online play, but online play is not added").
Suggestions never change anything; adding a feature is a user action.

This module never imports `joinery.blueprints`: callers pass the dict that
`blueprints.describe(id)` returns and the `[game]` record of the project, so the
brief works the same with or without the starting-point catalogue.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from joinery.catalog import ROOT

TEMPLATE = ROOT / "templates" / "game_brief.md"
BRIEF_PATH = Path("design") / "brief.md"
ANSWERS_FILE = ".joinery-brief-answers.json"
UNSET = "_Not decided yet._"
EDIT_HINT = "Changing the brief is a reviewed edit of design/brief.md."
FACTS_SCHEMA = 1

#: The wizard's page for the brief. One short page: the pitch is the only thing
#: worth asking before the game exists; everything else is drafted from the
#: starting point and features, or left open.
GROUPS: tuple[tuple[str, str], ...] = (("pitch", "Your game in one line"),)

QUESTIONS: tuple[dict[str, object], ...] = (
    {
        "key": "pitch",
        "group": "pitch",
        "label": "Pitch",
        "hint": "One line: what the player does, and why it is fun.",
        "multiline": False,
        "optional": False,
    },
    {
        "key": "audience",
        "group": "pitch",
        "label": "Audience",
        "hint": "Optional. Who it is for: age, how much they play, what else they play.",
        "multiline": False,
        "optional": True,
    },
)

#: Section headings in order, with the template tokens each one holds. The
#: template must contain exactly these tokens (plus `title`); a test enforces it.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Pitch", ("pitch", "pitch_long")),
    ("Genre and references", ("genre", "design_references")),
    ("Audience and content", ("audience", "content_notes")),
    ("Player fantasy", ("fantasy",)),
    ("Core loop", ("core_loop",)),
    ("Player verbs", ("verbs",)),
    ("Controls per device", ("controls",)),
    ("Camera", ("camera",)),
    ("Win, lose, progression", ("progression",)),
    ("Session length", ("session",)),
    ("Platforms and stores", ("platforms",)),
    ("Online", ("online",)),
    ("Saving", ("saving",)),
    ("Languages", ("languages",)),
    ("Accessibility goals", ("accessibility_goals",)),
    ("Monetisation", ("monetisation",)),
    ("Scope and milestones", ("scope",)),
    ("Open questions", ("open_questions",)),
    ("Facts (checked)", ("facts",)),
)
TOKENS: tuple[str, ...] = ("title",) + tuple(
    token for _, tokens in SECTIONS for token in tokens
)
#: Tokens computed from the setup that an answer cannot replace.
_COMPUTED_ONLY = frozenset({"facts"})
#: Tokens that render as nothing, not as the unset marker, when unanswered
#: (they sit beside a token that already carries the marker).
_QUIET = frozenset({"pitch_long"})

#: What a gap prompts Journeyman to ask, in priority order. Monetisation is
#: deliberately absent: it is never suggested unless the user raises it.
_GAP_PROMPTS: tuple[tuple[str, str], ...] = (
    (
        "Pitch",
        "The brief has no pitch yet. Ask for one line: what the player does, and why it is fun.",
    ),
    (
        "Player fantasy",
        "The brief's player fantasy is open: who does the player get to be?",
    ),
    (
        "Core loop",
        "The brief's core loop is open: what does the player do every 30 seconds, every 5 minutes, every session?",
    ),
    (
        "Audience and content",
        "Audience or content notes are open: who is it for, and which themes, violence or rating to aim for?",
    ),
    ("Session length", "Session length is open: how long does one sitting last?"),
    (
        "Win, lose, progression",
        "Win, lose and progression are open: how does the player finish, fail and move on?",
    ),
    (
        "Genre and references",
        "Genre or design references are open: which games play the way this one should, and what to take from each?",
    ),
    (
        "Scope and milestones",
        "Scope is open: team size, what counts as a prototype, and what is out of scope?",
    ),
    ("Camera", "The camera is open: what does the player see, and from where?"),
    (
        "Player verbs",
        "Some player verbs have no feedback yet: what does the player see and hear when each one happens?",
    ),
    (
        "Accessibility goals",
        "Accessibility goals beyond the built-in settings are open.",
    ),
    ("Platforms and stores", "Stores are open: where will people get the game?"),
)
_MAX_GAP_PROMPTS = 3
_TOKEN = re.compile(r"\{\{(\w+)\}\}")
_ACTION_NAME = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")

Platform = Literal["windows", "macos", "linux", "web", "android", "ios", "steam-deck"]
Store = Literal["steam", "itch"]
InputKind = Literal["keyboard_mouse", "gamepad", "touch"]
_LANGUAGE = re.compile(r"^[a-z]{2,3}([_-][A-Za-z0-9]{2,8})*$")

_PLATFORM_NAMES = {
    "windows": "Windows",
    "macos": "macOS",
    "linux": "Linux",
    "web": "the web",
    "android": "Android",
    "ios": "iOS",
    "steam-deck": "Steam Deck",
}
_STORE_NAMES = {"steam": "Steam", "itch": "itch.io"}
_INPUT_NAMES = {
    "keyboard_mouse": "keyboard and mouse",
    "gamepad": "gamepad",
    "touch": "touch",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def _unique[T](values: list[T]) -> list[T]:
    seen: list[T] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


class BriefFacts(_Strict):
    """The brief's decisions in a form that can be compared with the setup."""

    schema_version: int = Field(default=FACTS_SCHEMA, alias="schema")
    genre: list[str] = []
    #: "" = not decided.
    dimension: Literal["2d", "3d", "ui", ""] = ""
    platforms: list[Platform] = ["windows", "macos", "linux"]
    stores: list[Store] = []
    input: list[InputKind] = ["keyboard_mouse"]
    online: Literal["none", "lan", "online"] = "none"
    max_players: int = Field(default=1, ge=1, le=256)
    host_model: Literal["listen", "dedicated", "both"] = "listen"
    #: 0 = not decided.
    session_minutes: int = Field(default=0, ge=0)
    languages: list[str] = ["en"]
    save: Literal["none", "checkpoints", "slots", "both"] = "none"
    #: "" = not decided.
    monetisation: str = ""
    rating_target: str = ""

    @field_validator("genre", "platforms", "stores", "input", "languages")
    @classmethod
    def _dedupe(cls, value: list[Any]) -> list[Any]:
        return _unique(value)

    @field_validator("genre")
    @classmethod
    def _genre_words(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("genre entries cannot be empty")
        return value

    @field_validator("languages")
    @classmethod
    def _language_codes(cls, value: list[str]) -> list[str]:
        bad = [code for code in value if not _LANGUAGE.match(code)]
        if bad:
            raise ValueError(
                "languages are codes such as en, pt_BR or zh-Hans: " + ", ".join(bad)
            )
        return value

    @model_validator(mode="after")
    def _known_schema(self) -> BriefFacts:
        if self.schema_version > FACTS_SCHEMA:
            raise ValueError(
                f"facts schema {self.schema_version} was made by a newer Joinery"
            )
        if self.schema_version < 1:
            raise ValueError("facts schema starts at 1")
        return self

    def as_json(self) -> dict[str, Any]:
        data: dict[str, Any] = self.model_dump(mode="json", by_alias=True)
        return data


class Brief(_Strict):
    text: str
    facts: BriefFacts | None
    #: Headings of sections that still carry the unset marker.
    gaps: list[str]
    #: Bullets from the "Open questions" section.
    open_questions: list[str] = []
    #: Things that make the brief unreadable by machine (the facts block).
    problems: list[str] = []


# --------------------------------------------------------------------------
# Wizard


def questions() -> list[dict[str, object]]:
    return [dict(q) for q in QUESTIONS]


def grouped() -> list[dict[str, object]]:
    """The wizard's questions as pages: one group per page, in order."""
    return [
        {
            "key": key,
            "label": label,
            "questions": [dict(q) for q in QUESTIONS if q.get("group") == key],
        }
        for key, label in GROUPS
    ]


# --------------------------------------------------------------------------
# Facts from the setup


def draft_facts(
    blueprint: Mapping[str, Any] | None, features: Sequence[str]
) -> BriefFacts:
    """The facts the starting point and features decide; the rest stay default."""
    chosen = set(features)
    brief = _brief_defaults(blueprint)
    genre = [str(g) for g in _as_list(brief.get("genre")) if str(g).strip()]
    dimension = str(blueprint.get("dimension", "")) if blueprint else ""
    if dimension not in ("2d", "3d", "ui"):
        dimension = ""
    online = "none"
    max_players = 1
    if "online-play" in chosen:
        online = "lan"
        max_players = 4
    stores: list[str] = []
    if "steam" in chosen:
        stores.append("steam")
        if "online-play" in chosen:
            online = "online"
    inputs = ["keyboard_mouse"]
    if "controller" in chosen:
        inputs.append("gamepad")
    save = "both" if "save-load" in chosen else "none"
    return BriefFacts.model_validate(
        {
            "schema": FACTS_SCHEMA,
            "genre": genre,
            "dimension": dimension,
            "stores": stores,
            "input": inputs,
            "online": online,
            "max_players": max_players,
            "save": save,
        }
    )


def facts_toml(facts: BriefFacts) -> str:
    """The facts as a fenced TOML block, with the allowed values as comments."""

    def strings(values: Iterable[str]) -> str:
        return "[" + ", ".join(json.dumps(v) for v in values) + "]"

    lines = [
        ("schema", str(facts.schema_version), ""),
        ("genre", strings(facts.genre), ""),
        ("dimension", json.dumps(facts.dimension), '2d | 3d | ui ("" = not decided)'),
        (
            "platforms",
            strings(facts.platforms),
            "windows macos linux web android ios steam-deck",
        ),
        ("stores", strings(facts.stores), "steam itch"),
        ("input", strings(facts.input), "keyboard_mouse gamepad touch"),
        ("online", json.dumps(facts.online), "none | lan | online"),
        ("max_players", str(facts.max_players), ""),
        ("host_model", json.dumps(facts.host_model), "listen | dedicated | both"),
        ("session_minutes", str(facts.session_minutes), "0 = not decided"),
        ("languages", strings(facts.languages), "en, fr, pt_BR, ..."),
        ("save", json.dumps(facts.save), "none | checkpoints | slots | both"),
        ("monetisation", json.dumps(facts.monetisation), '"" = not decided'),
        ("rating_target", json.dumps(facts.rating_target), '"" = not decided'),
    ]
    body = []
    for key, value, comment in lines:
        entry = f"{key} = {value}"
        if comment:
            entry = f"{entry:<44} # {comment}"
        body.append(entry)
    return "```toml\n" + "\n".join(body) + "\n```"


# --------------------------------------------------------------------------
# Controls

#: Godot `Key` codes for the keys a blueprint may name (contract key table,
#: checked against stock Godot by the tests).
_SPECIAL = 1 << 22
_SPECIAL_KEYS: dict[str, int] = {
    "Escape": _SPECIAL | 0x01,
    "Tab": _SPECIAL | 0x02,
    "Backspace": _SPECIAL | 0x04,
    "Enter": _SPECIAL | 0x05,
    "Insert": _SPECIAL | 0x07,
    "Delete": _SPECIAL | 0x08,
    "Home": _SPECIAL | 0x0D,
    "End": _SPECIAL | 0x0E,
    "Left": _SPECIAL | 0x0F,
    "Up": _SPECIAL | 0x10,
    "Right": _SPECIAL | 0x11,
    "Down": _SPECIAL | 0x12,
    "PageUp": _SPECIAL | 0x13,
    "PageDown": _SPECIAL | 0x14,
    "Shift": _SPECIAL | 0x15,
    "Ctrl": _SPECIAL | 0x16,
    "Meta": _SPECIAL | 0x17,
    "Alt": _SPECIAL | 0x18,
    "CapsLock": _SPECIAL | 0x19,
    **{f"F{n}": (_SPECIAL | 0x1C) + n - 1 for n in range(1, 13)},
}
_KEY_BY_CODE = {code: name for name, code in _SPECIAL_KEYS.items()}
_KEY_LABELS = {
    "Left": "Left arrow",
    "Right": "Right arrow",
    "Up": "Up arrow",
    "Down": "Down arrow",
    "Escape": "Esc",
    "PageUp": "Page Up",
    "PageDown": "Page Down",
    "CapsLock": "Caps Lock",
}
_MOUSE_BY_INDEX = {
    1: "left",
    2: "right",
    3: "middle",
    4: "wheel_up",
    5: "wheel_down",
    6: "wheel_left",
    7: "wheel_right",
    8: "back",
    9: "forward",
}
_MOUSE_LABELS = {
    "left": "Left click",
    "right": "Right click",
    "middle": "Middle click",
    "wheel_up": "Wheel up",
    "wheel_down": "Wheel down",
    "wheel_left": "Wheel left",
    "wheel_right": "Wheel right",
    "back": "Mouse back",
    "forward": "Mouse forward",
}
#: Godot `JoyButton` indices, named for the common (Xbox-style) layout.
_PAD_BUTTONS = {
    0: "A",
    1: "B",
    2: "X",
    3: "Y",
    4: "Back",
    5: "Guide",
    6: "Start",
    7: "Left stick click",
    8: "Right stick click",
    9: "LB",
    10: "RB",
    11: "D-pad up",
    12: "D-pad down",
    13: "D-pad left",
    14: "D-pad right",
}
#: Godot `JoyAxis` index and direction.
_PAD_AXES = {
    (0, "-"): "Left stick left",
    (0, "+"): "Left stick right",
    (1, "-"): "Left stick up",
    (1, "+"): "Left stick down",
    (2, "-"): "Right stick left",
    (2, "+"): "Right stick right",
    (3, "-"): "Right stick up",
    (3, "+"): "Right stick down",
    (4, "+"): "Left trigger",
    (5, "+"): "Right trigger",
}
_PAD_BUTTON_SPEC = re.compile(r"^(?:button[:_ ]?)?(\d+)$", re.IGNORECASE)
_PAD_AXIS_SPEC = re.compile(r"^(?:axis[:_ ]?)?(\d+)\s*([+-])$", re.IGNORECASE)
#: The core's pause action (keys Escape, P; pad Start), shown when the
#: blueprint description does not list it.
_CORE_PAUSE = {"keys": ["Escape", "P"], "mouse": [], "pad": ["6"]}


def key_label(name: str) -> str:
    return _KEY_LABELS.get(name, name)


def mouse_label(name: str) -> str:
    return _MOUSE_LABELS.get(name, name.replace("_", " ").capitalize())


def pad_label(spec: object) -> str:
    """'0' -> 'A', '5+' -> 'Right trigger'; anything already readable passes through."""
    text = str(spec).strip()
    button = _PAD_BUTTON_SPEC.match(text)
    if button:
        index = int(button.group(1))
        return _PAD_BUTTONS.get(index, f"Button {index}")
    axis = _PAD_AXIS_SPEC.match(text)
    if axis:
        index, sign = int(axis.group(1)), axis.group(2)
        return _PAD_AXES.get((index, sign), f"Axis {index}{sign}")
    return text


def action_label(action: str) -> str:
    return action.replace("_", " ").strip().capitalize()


def _cell(values: Iterable[str]) -> str:
    joined = " / ".join(v.replace("|", "\\|") for v in _unique(list(values)) if v)
    return joined or "—"


def controls_table(actions: Mapping[str, Mapping[str, Sequence[object]]]) -> str:
    """A `Action | Keyboard and mouse | Gamepad | Touch` table, one row per action.

    `actions` is the `input` of `blueprints.describe()` (or `input_actions()`
    of an applied project): `{action: {"keys": [...], "mouse": [...], "pad": [...]}}`
    with key names from the contract key table, mouse names such as `left`, and
    pad specs such as `0` (button) or `5+` (axis and direction).
    """
    rows = [
        "| Action | Keyboard and mouse | Gamepad | Touch |",
        "|---|---|---|---|",
    ]
    for action, bindings in actions.items():
        if action.startswith("ui_"):
            continue
        keyboard = [key_label(str(k)) for k in bindings.get("keys", ())]
        keyboard += [mouse_label(str(m)) for m in bindings.get("mouse", ())]
        pad = [pad_label(p) for p in bindings.get("pad", ())]
        touch = [str(t) for t in bindings.get("touch", ())]
        rows.append(
            f"| {action_label(action)} (`{action}`) | {_cell(keyboard)} | "
            f"{_cell(pad)} | {_cell(touch)} |"
        )
    return "\n".join(rows)


# --------------------------------------------------------------------------
# Reading the applied input map from project.godot

_HEADER = re.compile(r"^\[([^\]\s]+)\]\s*$")
_ENTRY = re.compile(r"^([A-Za-z0-9_./\-]+)\s*=\s*(.*)$", re.DOTALL)
_OBJECT_START = re.compile(r"Object\((\w+),")
_FIELD = re.compile(r'"(\w+)":("(?:[^"\\]|\\.)*"|\w+\([^()]*\)|[^,]*)')


def _balance(text: str) -> int:
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
    return depth


def godot_section(text: str, section: str) -> dict[str, str]:
    """Raw `key -> value` text of one section of a Godot ConfigFile text.

    Values may span lines (`{ ... }` input maps); brackets inside strings are
    ignored. Comments and other sections are skipped.
    """
    entries: dict[str, str] = {}
    current: str | None = None
    pending_key = ""
    pending: list[str] = []
    depth = 0
    for line in text.splitlines():
        if pending:
            pending.append(line)
            depth += _balance(line)
            if depth <= 0:
                entries[pending_key] = "\n".join(pending)
                pending = []
            continue
        header = _HEADER.match(line)
        if header:
            current = header.group(1)
            continue
        if current != section or not line.strip() or line.lstrip().startswith(";"):
            continue
        entry = _ENTRY.match(line)
        if not entry:
            continue
        key, value = entry.group(1), entry.group(2)
        depth = _balance(value)
        if depth > 0:
            pending_key = key
            pending = [value]
        else:
            entries[key] = value
    if pending:
        entries[pending_key] = "\n".join(pending)
    return entries


def _objects(raw: str) -> list[tuple[str, str]]:
    """`(class, fields text)` of every `Object(Class, ...)` in a variant text.

    Fields may hold nested constructors such as `Vector2(0, 0)`, so the end of
    each object is found by balancing brackets outside strings.
    """
    found: list[tuple[str, str]] = []
    position = 0
    while match := _OBJECT_START.search(raw, position):
        depth = 1
        index = match.end()
        in_string = escaped = False
        while index < len(raw) and depth:
            char = raw[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            index += 1
        found.append((match.group(1), raw[match.end() : index - 1]))
        position = index
    return found


def _event_fields(raw: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in _FIELD.finditer(raw)}


def _int(value: str | None) -> int:
    try:
        return int(float(value or "0"))
    except ValueError:
        return 0


def _key_name(code: int) -> str:
    if code in _KEY_BY_CODE:
        return _KEY_BY_CODE[code]
    if code == 32:
        return "Space"
    if 33 <= code <= 126:
        return chr(code).upper()
    return f"Key {code}"


def parse_input_map(text: str) -> dict[str, dict[str, list[str]]]:
    """The `[input]` section of a project.godot as `{action: {keys, mouse, pad}}`.

    Names use the same vocabulary as blueprint `[input]` tables, so the result
    feeds `controls_table` directly.
    """
    actions: dict[str, dict[str, list[str]]] = {}
    for action, raw in godot_section(text, "input").items():
        keys: list[str] = []
        mouse: list[str] = []
        pad: list[str] = []
        for kind, body in _objects(raw):
            fields = _event_fields(body)
            if kind == "InputEventKey":
                code = _int(fields.get("physical_keycode")) or _int(
                    fields.get("keycode")
                )
                if code:
                    keys.append(_key_name(code))
            elif kind == "InputEventMouseButton":
                index = _int(fields.get("button_index"))
                mouse.append(_MOUSE_BY_INDEX.get(index, f"button_{index}"))
            elif kind == "InputEventJoypadButton":
                pad.append(str(_int(fields.get("button_index"))))
            elif kind == "InputEventJoypadMotion":
                try:
                    value = float(fields.get("axis_value", "0") or 0)
                except ValueError:  # a hand-edited value: read it as the + side
                    value = 0.0
                sign = "-" if value < 0 else "+"
                pad.append(f"{_int(fields.get('axis'))}{sign}")
        actions[action] = {
            "keys": _unique(keys),
            "mouse": _unique(mouse),
            "pad": _unique(pad),
        }
    return actions


def input_actions(project: Path) -> dict[str, dict[str, list[str]]]:
    """The project's applied input map, or `{}` without a project.godot."""
    path = project / "project.godot"
    if not path.is_file():
        return {}
    return parse_input_map(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Drafting


def _as_list(value: object) -> list[Any]:
    if isinstance(value, list | tuple):
        return list(value)
    if value is None or value == "":
        return []
    return [value]


def _brief_defaults(blueprint: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not blueprint:
        return {}
    brief = blueprint.get("brief")
    return brief if isinstance(brief, Mapping) else {}


def _from_starting_point(name: str) -> str:
    return f"_From the {name} starting point; change freely._"


def _loop_text(loop: str) -> str:
    """'30 s: a. 5 min: b. Session: c.' as one bullet per loop."""
    parts = re.split(
        r"(?<=[.!?])\s+(?=(?:\d+\s*(?:s|sec|min|minutes?|h)|Session)\s*:)", loop
    )
    if len(parts) < 2:
        return loop
    return "\n".join(f"- {part.strip()}" for part in parts if part.strip())


def _verbs_table(
    blueprint: Mapping[str, Any] | None,
    actions: Mapping[str, Mapping[str, Sequence[object]]],
) -> str:
    rows: list[tuple[str, str, str]] = []
    for entry in _as_list(_brief_defaults(blueprint).get("verbs")):
        cells = [str(c) for c in _as_list(entry)] + ["", "", ""]
        rows.append((cells[0], cells[1], cells[2]))
    if not rows:
        rows = [
            (action_label(action), action, "")
            for action in actions
            if action != "pause" and not action.startswith("ui_")
        ]
    if not rows:
        return UNSET
    lines = ["| Verb | Input action | Feedback |", "|---|---|---|"]
    for verb, action, feedback in rows:
        names = [a for a in re.split(r"\s*/\s*|,\s*", action.strip()) if a]
        # Action names render as code; words such as "mouse" stay plain text.
        code = ", ".join(
            f"`{a}`" if a in actions or not actions and _ACTION_NAME.match(a) else a
            for a in names
        )
        lines.append(
            f"| {verb.replace('|', '/')} | {code or UNSET} | "
            f"{(feedback.replace('|', '/') or UNSET)} |"
        )
    return "\n".join(lines)


def _join_names(names: Sequence[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _platforms_text(facts: BriefFacts, features: set[str]) -> str:
    platforms = _join_names([_PLATFORM_NAMES[p] for p in facts.platforms])
    inputs = ", ".join(_INPUT_NAMES[i] for i in facts.input)
    stores = (
        _join_names([_STORE_NAMES[s] for s in facts.stores]) + "."
        if facts.stores
        else UNSET
    )
    lines = [
        f"- Platforms: {platforms} (the desktop default; change freely).",
        f"- Input: {inputs}.",
        f"- Stores: {stores}",
    ]
    if "steam" in features:
        lines.append(
            "- Steam features need the GodotSteam extension, installed separately "
            "with your approval; without it the game runs, and Steam calls do nothing."
        )
    return "\n".join(lines)


def _online_text(facts: BriefFacts) -> str:
    if facts.online == "none":
        if facts.max_players > 1:
            return f"No online play. Up to {facts.max_players} players on one device."
        return "Single player. Online play is not part of this game's setup."
    where = {
        "lan": "over the local network",
        "online": "online through Steam lobbies, or over the local network",
    }[facts.online]
    host = {
        "listen": "one player hosts (listen server)",
        "dedicated": "a dedicated server hosts",
        "both": "one player hosts, or a dedicated server",
    }[facts.host_model]
    return f"Up to {facts.max_players} players {where}; {host}."


def _saving_text(facts: BriefFacts, features: set[str]) -> str:
    if "save-load" in features:
        return (
            "Save slots, and an autosave at each checkpoint. Save and load from "
            "the pause menu; Continue on the main menu picks up the latest save. "
            "Online, only the host saves."
        )
    if facts.save == "none":
        return "No saving. The save-load feature is not added."
    return {
        "checkpoints": "Progress is kept at checkpoints.",
        "slots": "Save slots the player picks.",
        "both": "Save slots, and an autosave at each checkpoint.",
    }[facts.save]


def _languages_text(features: set[str]) -> str:
    if "translations" in features:
        return (
            "English. Every piece of text goes through translation files "
            "(`locale/`), so more languages can be added later."
        )
    return "English only. Text is not set up for translation (the translations feature is not added)."


def _accessibility_text(blueprint: Mapping[str, Any] | None, features: set[str]) -> str:
    built_in: list[str] = []
    if blueprint is not None:
        built_in.append(
            "Menus work with keyboard or gamepad alone; text is at least 18 px at 1080p."
        )
        built_in.append(
            "Settings for volume per bus, screen shake strength and reduced motion."
        )
    if "controller" in features:
        built_in.append(
            "Remappable keyboard and gamepad controls, stick deadzone, invert look, rumble on or off."
        )
    if "translations" in features:
        built_in.append("Pseudolocalisation to check that translated text still fits.")
    if not built_in:
        return UNSET
    lines = ["Built in:"] + [f"- {item}" for item in built_in]
    lines.append(
        f"\nFurther goals (subtitles, colour-blind support, difficulty): {UNSET}"
    )
    return "\n".join(lines)


def _scope_text() -> str:
    milestones = ("Prototype", "Vertical slice", "Content complete", "Release")
    lines = [
        f"- Team size: {UNSET}",
        "",
        "| Milestone | Done when | Target |",
        "|---|---|---|",
    ]
    lines += [f"| {m} | {UNSET} | {UNSET} |" for m in milestones]
    lines += ["", f"- Out of scope: {UNSET}"]
    return "\n".join(lines)


def _open_questions(facts: BriefFacts, features: set[str]) -> list[str]:
    asked: list[str] = []
    if facts.online != "none":
        asked.append("Is playing together co-op, competitive, or both?")
    if "translations" in features:
        asked.append("Which languages besides English?")
    return asked


def draft(
    answers: Mapping[str, str],
    blueprint: Mapping[str, Any] | None,
    features: Sequence[str],
    *,
    actions: Mapping[str, Mapping[str, Sequence[object]]] | None = None,
) -> str:
    """Render the brief template; no `{{token}}` survives.

    `blueprint` is the dict `blueprints.describe(id)` returns (or None),
    `features` the chosen feature ids. `actions` is the applied input map
    (`input_actions(project)`); without it the controls come from the
    blueprint description, with pad bindings only when the controller feature
    is chosen.
    """
    chosen = set(features)
    facts = draft_facts(blueprint, features)
    brief = _brief_defaults(blueprint)
    name = str(blueprint.get("name") or blueprint.get("id") or "") if blueprint else ""
    credit = _from_starting_point(name) if name else ""

    controls: dict[str, Mapping[str, Sequence[object]]] = {}
    if actions:
        controls = dict(actions)
    elif blueprint:
        described = blueprint.get("input")
        if isinstance(described, Mapping):
            for action, bindings in described.items():
                if not isinstance(bindings, Mapping):
                    continue
                entry = {k: list(_as_list(v)) for k, v in bindings.items()}
                if "controller" not in chosen:
                    entry["pad"] = []
                controls[str(action)] = entry
        if "pause" not in controls:
            pause = dict(_CORE_PAUSE)
            if "controller" not in chosen:
                pause["pad"] = []
            controls["pause"] = pause

    values: dict[str, str] = {}
    if controls:
        values["controls"] = (
            controls_table(controls)
            + "\n\nMenus also use Godot's built-in navigation: arrow keys and Enter, "
            "the d-pad and A, or the mouse."
        )
    genre = ", ".join(facts.genre)
    if genre:
        values["genre"] = f"{genre} (from the {name} starting point)" if name else genre
    if brief.get("core_loop"):
        values["core_loop"] = _loop_text(str(brief["core_loop"])) + f"\n\n{credit}"
    if brief.get("camera"):
        values["camera"] = f"{brief['camera']}\n\n{credit}"
    if brief.get("progression"):
        values["progression"] = f"{brief['progression']}\n\n{credit}"
    verbs = _verbs_table(blueprint, controls)
    if verbs != UNSET:
        values["verbs"] = verbs + (f"\n\n{credit}" if credit else "")
    values["platforms"] = _platforms_text(facts, chosen)
    values["online"] = _online_text(facts)
    values["saving"] = _saving_text(facts, chosen)
    values["languages"] = _languages_text(chosen)
    values["accessibility_goals"] = _accessibility_text(blueprint, chosen)
    values["scope"] = _scope_text()
    asked = _open_questions(facts, chosen)
    values["open_questions"] = (
        "\n".join(f"- {q}" for q in asked) if asked else "None yet."
    )
    values["facts"] = facts_toml(facts)

    for key, value in answers.items():
        cleaned = str(value or "").strip()
        if not cleaned or key in _COMPUTED_ONLY:
            continue
        if key in ("pitch", "title"):
            cleaned = " ".join(cleaned.split())
        values[str(key)] = cleaned
    values.setdefault("title", "Untitled game")

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in values:
            return values[key]
        return "" if key in _QUIET else UNSET

    text = _TOKEN.sub(substitute, TEMPLATE.read_text(encoding="utf-8"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip("\n") + "\n"


def _project_name(project: Path) -> str:
    path = project / "project.godot"
    if not path.is_file():
        return ""
    raw = godot_section(path.read_text(encoding="utf-8"), "application").get(
        "config/name", ""
    )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    return value if isinstance(value, str) else ""


def write_brief(
    project: Path,
    answers: Mapping[str, str],
    blueprint: Mapping[str, Any] | None,
    features: Sequence[str],
) -> Path:
    """Write `<project>/design/brief.md`. Refuses to overwrite an existing brief.

    Controls come from the project's applied input map when it has one, so
    they show exactly what the features bound (gamepad buttons included).
    """
    destination = project / BRIEF_PATH
    if destination.exists():
        raise FileExistsError(
            f"{destination} already exists; changing the brief is a reviewed edit, "
            "not something project setup should overwrite"
        )
    filled = dict(answers)
    if not str(filled.get("title") or "").strip():
        name = _project_name(project)
        if name:
            filled["title"] = name
    text = draft(filled, blueprint, features, actions=input_actions(project) or None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return destination


def write_from_answers_file(
    project: Path,
    blueprint: Mapping[str, Any] | None,
    features: Sequence[str],
) -> Path | None:
    """Consume `<project>/.joinery-brief-answers.json` and write the brief.

    The file is removed whatever happens, so answers never linger in the
    project. Returns None when there is no answers file.
    """
    source = project / ANSWERS_FILE
    if not source.is_file():
        return None
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}: not valid JSON: {exc}") from exc
    finally:
        source.unlink(missing_ok=True)
    if not isinstance(raw, dict):
        # A ValueError like the JSON error above: both are a bad answers file.
        raise ValueError(f"{source}: expected a JSON object of answers")  # noqa: TRY004
    answers = {
        str(k): str(v) for k, v in raw.items() if isinstance(v, str | int | float)
    }
    return write_brief(project, answers, blueprint, features)


# --------------------------------------------------------------------------
# Reading

_SECTION = re.compile(r"^##\s+(?:\d+\.\s*)?(.+?)\s*$", re.MULTILINE)
_BLOCK = re.compile(r"^```toml[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_BULLET = re.compile(r"^\s*[-*]\s+(.+?)\s*$", re.MULTILINE)


def sections(text: str) -> list[tuple[str, str]]:
    """`(heading, body)` for every `## ` section, numbers stripped."""
    found = list(_SECTION.finditer(text))
    return [
        (
            match.group(1),
            text[
                match.end() : found[i + 1].start() if i + 1 < len(found) else len(text)
            ],
        )
        for i, match in enumerate(found)
    ]


def _validation_messages(error: ValidationError) -> list[str]:
    messages = []
    for item in error.errors():
        where = ".".join(str(part) for part in item["loc"])
        message = str(item["msg"]).removeprefix("Value error, ")
        messages.append(f"{where}: {message}" if where else message)
    return messages


def parse_facts(text: str) -> tuple[BriefFacts | None, list[str]]:
    """Facts from the first ```toml block of the Facts section, and problems."""
    body = text
    for heading, section_body in sections(text):
        if heading.lower().startswith("facts"):
            body = section_body
            break
    match = _BLOCK.search(body)
    if match is None:
        return None, ["The brief has no facts block (a ```toml block under Facts)."]
    try:
        data = tomllib.loads(match.group(1))
    except tomllib.TOMLDecodeError as exc:
        return None, [f"The brief's facts block is not valid TOML: {exc}"]
    try:
        return BriefFacts.model_validate(data), []
    except ValidationError as exc:
        return None, [
            f"The brief's facts block: {message}"
            for message in _validation_messages(exc)
        ]


def parse(text: str) -> Brief:
    facts, problems = parse_facts(text)
    gaps: list[str] = []
    asked: list[str] = []
    for heading, body in sections(text):
        if heading.lower().startswith("facts"):
            continue
        if UNSET in body:
            gaps.append(heading)
        if heading == "Open questions":
            asked = [
                bullet
                for bullet in _BULLET.findall(body)
                if bullet != UNSET and not bullet.startswith("_")
            ]
    return Brief(
        text=text, facts=facts, gaps=gaps, open_questions=asked, problems=problems
    )


def load_brief(project: Path) -> Brief:
    """Read `design/brief.md`. Raises FileNotFoundError when there is none."""
    path = project / BRIEF_PATH
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist")
    return parse(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Suggestions


def _features_of(game: Mapping[str, Any]) -> set[str]:
    return {str(f) for f in _as_list(game.get("features"))}


def consistency(project: Path, game: Mapping[str, Any] | None) -> list[str]:
    """One-line suggestions where the brief and the game's setup disagree.

    `game` is `blueprints.project_game(project)` (the `[game]` record). Without
    a record the project has no starting point to add features to, so only
    facts that contradict themselves are reported. Nothing is changed.
    """
    try:
        brief = load_brief(project)
    except FileNotFoundError:
        return []
    if brief.facts is None:
        return []
    return _consistency(brief.facts, game)


def _consistency(facts: BriefFacts, game: Mapping[str, Any] | None) -> list[str]:
    found: list[str] = []
    if facts.online != "none" and facts.max_players < 2:
        found.append(
            "The brief says online play but max_players is 1. How many players can join?"
        )
    if game is None:
        return found
    features = _features_of(game)
    where = {"lan": "LAN play", "online": "online play"}
    if facts.online != "none" and "online-play" not in features:
        found.append(
            f"The brief says {where[facts.online]}, but online play is not added. "
            "Add it with /feature add online-play?"
        )
    if facts.online == "none" and "online-play" in features:
        found.append(
            "Online play is added, but the brief says no online play. "
            "Update the brief's Online section, or remove the feature?"
        )
    if "gamepad" in facts.input and "controller" not in features:
        found.append(
            "The brief lists gamepad input, but controller support is not added. "
            "Add it with /feature add controller?"
        )
    if "controller" in features and "gamepad" not in facts.input:
        found.append(
            "Controller support is added, but the brief's input list has no gamepad. "
            "Add gamepad to the brief's facts?"
        )
    if len(facts.languages) > 1 and "translations" not in features:
        found.append(
            f"The brief lists {len(facts.languages)} languages, but translations are "
            "not added. Add them with /feature add translations?"
        )
    if "steam" in facts.stores and "steam" not in features:
        found.append(
            "The brief plans a Steam release, but the Steam feature is not added. "
            "Add it with /feature add steam? (It needs the GodotSteam extension, "
            "installed separately.)"
        )
    if facts.save != "none" and "save-load" not in features:
        found.append(
            "The brief says the game saves, but saving is not added. "
            "Add it with /feature add save-load?"
        )
    if facts.save == "none" and "save-load" in features:
        found.append(
            "Saving is added, but the brief says the game does not save. "
            "Update the brief's Saving section?"
        )
    dimension = str(game.get("dimension") or "")
    if facts.dimension and dimension and facts.dimension != dimension:
        found.append(
            f"The brief says {facts.dimension.upper()}, but the starting point is "
            f"{dimension.upper()}. Which is right?"
        )
    return found


def _gap_prompts(gaps: Sequence[str]) -> list[str]:
    open_set = set(gaps)
    prompts = [prompt for heading, prompt in _GAP_PROMPTS if heading in open_set]
    return prompts[:_MAX_GAP_PROMPTS]


def summary(project: Path, game: Mapping[str, Any] | None) -> dict[str, Any]:
    """JSON-safe brief for the registry: text, facts, gaps and suggestions.

    Suggestions come in order: disagreements with the setup first, then the
    brief's own open questions, then the most useful open sections (at most
    three). Journeyman offers one when it is relevant to the user's request
    and never acts on it unasked.
    """
    try:
        brief = load_brief(project)
    except FileNotFoundError:
        return {
            "path": BRIEF_PATH.as_posix(),
            "exists": False,
            "text": "No game brief has been written for this project.",
            "facts": None,
            "gaps": [],
            "open_questions": [],
            "problems": [],
            "suggestions": [
                (
                    "This project has no game brief yet. Offer to draft "
                    "design/brief.md from a one-line pitch and the game's setup (/brief)."
                )
            ],
        }
    suggestions: list[str] = list(brief.problems)
    if brief.facts is not None:
        suggestions += _consistency(brief.facts, game)
    suggestions += [f"Open question in the brief: {q}" for q in brief.open_questions]
    suggestions += _gap_prompts(brief.gaps)
    return {
        "path": BRIEF_PATH.as_posix(),
        "exists": True,
        "text": brief.text,
        "facts": brief.facts.as_json() if brief.facts else None,
        "gaps": list(brief.gaps),
        "open_questions": list(brief.open_questions),
        "problems": list(brief.problems),
        "suggestions": suggestions,
    }
