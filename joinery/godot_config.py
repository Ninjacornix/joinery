"""Round-trip editor for Godot's ConfigFile text (project.godot, export_presets.cfg).

Godot rewrites these files whenever the editor saves, so edits made here follow
the layout Godot itself writes: sections in alphabetical order, a blank line
after each header and between sections, `key=value` with no spaces. Everything
not touched is kept byte for byte, including comments and values this module
does not understand. Values are raw variant text; multi-line values (`{…}`,
`[…]`, `(…)` and strings with literal newlines) are kept whole.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

__all__ = [
    "KEYS",
    "MOUSE_BUTTONS",
    "GodotConfig",
    "action_deadzone",
    "action_events",
    "action_value",
    "event_identity",
    "key_event",
    "mouse_event",
    "packed_strings",
    "pad_axis_event",
    "pad_button_event",
    "parse_string",
    "to_variant",
]


@dataclass
class _Item:
    """One line, or one entry spanning several lines, with its exact text."""

    text: str
    key: str | None = None
    raw: str = ""


@dataclass
class _Section:
    name: str
    header: str
    items: list[_Item] = field(default_factory=list)

    def entries(self) -> list[_Item]:
        return [item for item in self.items if item.key is not None]


def _value_end(text: str, start: int) -> int:
    """Index just past the value that starts at `start` (excluding its newline)."""
    depth = 0
    index = start
    in_string = False
    while index < len(text):
        char = text[index]
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "\n" and depth == 0:
            return index
        index += 1
    return index


def _blank(item: _Item) -> bool:
    return item.key is None and item.text.strip() == ""


class GodotConfig:
    """A parsed ConfigFile that dumps back to identical text when unchanged."""

    def __init__(self) -> None:
        # The unnamed first section holds the preamble and top-level keys.
        self._sections: list[_Section] = [_Section("", "")]

    @classmethod
    def parse(cls, text: str) -> GodotConfig:
        config = cls()
        current = config._sections[0]
        index = 0
        while index < len(text):
            newline = text.find("\n", index)
            line_end = len(text) if newline < 0 else newline
            line = text[index:line_end]
            stripped = line.strip()
            following = line_end + 1 if newline >= 0 else line_end
            if not stripped or stripped[0] in ";#":
                current.items.append(_Item(text[index:following]))
                index = following
                continue
            if stripped.startswith("[") and stripped.endswith("]") and "=" not in line.split("]")[0]:
                current = _Section(stripped[1:-1], text[index:following])
                config._sections.append(current)
                index = following
                continue
            equals = line.find("=")
            if equals < 0:
                current.items.append(_Item(text[index:following]))
                index = following
                continue
            end = _value_end(text, index + equals + 1)
            stop = end + 1 if end < len(text) else end
            current.items.append(
                _Item(
                    text[index:stop],
                    key=line[:equals].strip(),
                    raw=text[index + equals + 1 : end].strip(),
                )
            )
            index = stop
        return config

    def dump(self) -> str:
        return "".join(
            section.header + "".join(item.text for item in section.items)
            for section in self._sections
        )

    def _section(self, name: str) -> _Section | None:
        for section in self._sections:
            if section.name == name:
                return section
        return None

    def sections(self) -> list[str]:
        return [section.name for section in self._sections if section.name]

    def has_section(self, name: str) -> bool:
        return self._section(name) is not None

    def keys(self, section: str) -> list[str]:
        found = self._section(section)
        return [] if found is None else [str(item.key) for item in found.entries()]

    def get(self, section: str, key: str) -> str | None:
        found = self._section(section)
        if found is None:
            return None
        for item in found.entries():
            if item.key == key:
                return item.raw
        return None

    def set(self, section: str, key: str, raw: str) -> None:
        line = f"{key}={raw}\n"
        found = self._section(section)
        if found is None:
            self.add_section(section, [(key, raw)])
            return
        entries = found.entries()
        for item in entries:
            if item.key == key:
                if item.raw != raw:
                    item.text, item.raw = line, raw
                return
        new = _Item(line, key=key, raw=raw)
        if entries:
            found.items.insert(found.items.index(entries[-1]) + 1, new)
            return
        position = 0
        while position < len(found.items) and _blank(found.items[position]):
            position += 1
        if position == 0:
            found.items.insert(0, _Item("\n"))
            position = 1
        found.items.insert(position, new)

    def erase(self, section: str, key: str) -> None:
        found = self._section(section)
        if found is None:
            return
        found.items = [item for item in found.items if item.key != key]

    def add_section(
        self,
        name: str,
        entries: Mapping[str, str] | Sequence[tuple[str, str]],
        *,
        sort: bool = True,
    ) -> None:
        """Add a section where Godot would put it: sorted among named sections
        (project.godot), or at the end with `sort=False` (export presets)."""
        if not name or self._section(name) is not None:
            raise ValueError(f"section [{name}] already exists")
        pairs = list(entries.items()) if isinstance(entries, Mapping) else list(entries)
        items = [_Item("\n")] + [_Item(f"{k}={v}\n", key=k, raw=v) for k, v in pairs]
        named = [s for s in self._sections if s.name]
        after = [s for s in named if s.name > name] if sort else []
        if after:
            items.append(_Item("\n"))
            index = self._sections.index(after[0])
        else:
            last = self._sections[-1]
            if last.items or last.header:
                if last.items and not last.items[-1].text.endswith("\n"):
                    last.items[-1].text += "\n"
                if not (last.items and _blank(last.items[-1])):
                    last.items.append(_Item("\n"))
            index = len(self._sections)
        self._sections.insert(index, _Section(name, f"[{name}]\n", items))

    def remove_section(self, name: str) -> None:
        found = self._section(name)
        if found is None or not name:
            return
        was_last = self._sections[-1] is found
        self._sections.remove(found)
        if was_last:
            last = self._sections[-1]
            while last.items and _blank(last.items[-1]):
                last.items.pop()

    def rename_section(self, old: str, new: str) -> None:
        """Rename in place, keeping the position (export preset renumbering)."""
        found = self._section(old)
        if found is None:
            raise ValueError(f"no section [{old}]")
        if self._section(new) is not None:
            raise ValueError(f"section [{new}] already exists")
        found.name = new
        found.header = found.header.replace(f"[{old}]", f"[{new}]", 1)


# --- Variant text ----------------------------------------------------------------


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def to_variant(value: bool | float | str | list[str]) -> str:
    """Python value -> Godot variant text, as Godot writes it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(value)
        return text if any(c in text for c in ".en") else text + ".0"
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return "PackedStringArray(" + ", ".join(_quote(v) for v in value) + ")"
    raise TypeError(f"no Godot variant for {value!r}")


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _strings(raw: str) -> list[str]:
    found: list[str] = []
    index = 0
    while index < len(raw):
        if raw[index] != '"':
            index += 1
            continue
        index += 1
        chars: list[str] = []
        while index < len(raw) and raw[index] != '"':
            if raw[index] == "\\" and index + 1 < len(raw):
                chars.append(_ESCAPES.get(raw[index + 1], raw[index + 1]))
                index += 2
                continue
            chars.append(raw[index])
            index += 1
        found.append("".join(chars))
        index += 1
    return found


def packed_strings(raw: str) -> list[str]:
    """`PackedStringArray("a", "b")` or `["a", "b"]` -> ["a", "b"]."""
    text = raw.strip()
    if not text:
        return []
    if not text.startswith(("PackedStringArray(", "[")):
        raise ValueError(f"not a string array: {raw[:60]}")
    return _strings(text)


def parse_string(raw: str) -> str | None:
    """`"text"` -> text; anything that is not a plain string -> None."""
    text = raw.strip()
    if len(text) < 2 or text[0] != '"' or text[-1] != '"':
        return None
    values = _strings(text)
    return values[0] if len(values) == 1 else None


# --- Input events ----------------------------------------------------------------

KEYS: dict[str, int] = {
    **{chr(code): code for code in range(65, 91)},
    **{str(digit): 48 + digit for digit in range(10)},
    "Space": 32,
    "Escape": 4194305,
    "Tab": 4194306,
    "Backspace": 4194308,
    "Enter": 4194309,
    "Left": 4194319,
    "Up": 4194320,
    "Right": 4194321,
    "Down": 4194322,
    "Shift": 4194325,
    "Ctrl": 4194326,
    "Alt": 4194328,
    **{f"F{n}": 4194331 + n for n in range(1, 13)},
}
MOUSE_BUTTONS: dict[str, int] = {
    "left": 1,
    "right": 2,
    "middle": 3,
    "wheel_up": 4,
    "wheel_down": 5,
}
_AXIS = re.compile(r"^([0-5])([+-])$")
_MODIFIERS = (
    '"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false'
)
_BASE = '"resource_local_to_scene":false,"resource_name":"","device":-1'


def key_event(name: str) -> str:
    if name not in KEYS:
        raise ValueError(f"unknown key {name!r}; use one of {', '.join(KEYS)}")
    return (
        f'Object(InputEventKey,{_BASE},"window_id":0,{_MODIFIERS},"pressed":false,'
        f'"keycode":0,"physical_keycode":{KEYS[name]},"key_label":0,"unicode":0,'
        '"location":0,"echo":false,"script":null)'
    )


def mouse_event(name: str) -> str:
    if name not in MOUSE_BUTTONS:
        raise ValueError(f"unknown mouse button {name!r}")
    return (
        f'Object(InputEventMouseButton,{_BASE},"window_id":0,{_MODIFIERS},'
        '"button_mask":0,"position":Vector2(0, 0),"global_position":Vector2(0, 0),'
        f'"factor":1.0,"button_index":{MOUSE_BUTTONS[name]},"canceled":false,'
        '"pressed":false,"double_click":false,"script":null)'
    )


def pad_button_event(index: int) -> str:
    if not 0 <= index <= 127:
        raise ValueError(f"pad button out of range: {index}")
    return (
        f'Object(InputEventJoypadButton,{_BASE},"button_index":{index},'
        '"pressure":0.0,"pressed":false,"script":null)'
    )


def pad_axis_event(spec: str) -> str:
    match = _AXIS.match(spec)
    if match is None:
        raise ValueError(f"pad axis is like '0-' or '5+': {spec!r}")
    value = "1.0" if match.group(2) == "+" else "-1.0"
    return (
        f'Object(InputEventJoypadMotion,{_BASE},"axis":{match.group(1)},'
        f'"axis_value":{value},"script":null)'
    )


def _split_top(text: str) -> list[str]:
    """Split comma-separated items at depth 0 (outside strings)."""
    parts: list[str] = []
    depth = 0
    start = 0
    in_string = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
        index += 1
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


def _dict_value(raw: str, key: str) -> str | None:
    text = raw.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    for part in _split_top(text[1:-1]):
        name, _, value = part.partition(":")
        if name.strip() == f'"{key}"':
            return value.strip()
    return None


def action_events(raw: str) -> list[str]:
    """The `Object(InputEvent…)` texts of an `[input]` action value."""
    events = _dict_value(raw, "events")
    if events is None or not events.startswith("["):
        return []
    return _split_top(events[1:-1])


def action_deadzone(raw: str) -> float:
    value = _dict_value(raw, "deadzone")
    try:
        return float(value) if value is not None else 0.5
    except ValueError:
        return 0.5


def _float_text(value: float) -> str:
    return to_variant(float(value))


def action_value(deadzone: float, events: Iterable[str]) -> str:
    """An `[input]` action value in the exact layout Godot 4.7 writes."""
    listed = list(events)
    body = "".join(f"{event}\n" if i == 0 else f", {event}\n" for i, event in enumerate(listed))
    events_text = "[" + body + "]" if listed else "[]"
    return '{\n"deadzone": ' + _float_text(deadzone) + ',\n"events": ' + events_text + "\n}"


_FIELD = re.compile(r'"(\w+)":([^,()]+|Vector2\([^)]*\))')
_IDENTITY_FIELDS = {
    "InputEventKey": ("keycode", "physical_keycode", "key_label", "alt_pressed",
                      "shift_pressed", "ctrl_pressed", "meta_pressed"),
    "InputEventMouseButton": ("button_index", "alt_pressed", "shift_pressed",
                              "ctrl_pressed", "meta_pressed"),
    "InputEventJoypadButton": ("button_index",),
    "InputEventJoypadMotion": ("axis", "axis_value"),
}


def event_identity(text: str) -> str:
    """What makes two events the same binding, ignoring device and layout noise."""
    match = re.match(r"\s*Object\((\w+),", text)
    if match is None:
        return " ".join(text.split())
    kind = match.group(1)
    fields = dict(_FIELD.findall(text))
    wanted = _IDENTITY_FIELDS.get(kind)
    if wanted is None:
        return " ".join(text.split())
    return kind + ":" + ",".join(f"{name}={fields.get(name, '').strip()}" for name in wanted)
