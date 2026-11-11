"""The project's audio buses, as Godot's AudioBusLayout text resource.

Godot keeps the buses in ``default_bus_layout.tres`` (project setting
``audio/buses/default_bus_layout``): one ``bus/N/…`` block per bus in
``[resource]`` and one ``[sub_resource]`` per effect. The Mixer tab changes a
few things in it: each bus's level, mute, solo and effect bypass, a reverb and
a compressor with one amount each, and whether music ducks under voice or
sound effects (the Music compressor's sidechain). Everything else in the file
(other effects, their settings, external resources) is kept as it was.

The text written is deterministic and loads in stock Godot.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: The game core's buses, in index order (Master is always bus 0).
STANDARD_BUSES = ("Master", "Music", "SFX", "Ambience", "UI", "Voice")
#: Where Godot looks when the project names no layout.
DEFAULT_PATH = "default_bus_layout.tres"
#: What music may duck under.
DUCK_SOURCES = ("Voice", "SFX")
COMPRESSOR = "AudioEffectCompressor"
REVERB = "AudioEffectReverb"
#: Godot's defaults for the effect values the Mixer writes, in the order written.
COMPRESSOR_DEFAULTS: dict[str, float | str] = {
    "threshold": 0.0,
    "ratio": 4.0,
    "gain": 0.0,
    "attack_us": 20.0,
    "release_ms": 250.0,
    "mix": 1.0,
    "sidechain": "",
}
REVERB_DEFAULTS: dict[str, float | str] = {
    "predelay_msec": 150.0,
    "predelay_feedback": 0.4,
    "room_size": 0.8,
    "damping": 0.5,
    "spread": 1.0,
    "hipass": 0.0,
    "dry": 1.0,
    "wet": 0.5,
}
#: The ducking compressor on Music and the Ambience reverb, as the game core sets them.
DUCKING = {
    "threshold": -24.0,
    "ratio": 4.0,
    "attack_us": 20000.0,
    "release_ms": 250.0,
    "gain": 0.0,
}
ROOM = {"room_size": 0.6, "damping": 0.5, "wet": 0.25, "dry": 1.0}

_SECTION = re.compile(r"^\[(\w+)(.*)\]\s*$")
_ATTR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
_PROPERTY = re.compile(r"^([\w/]+)\s*=\s*(.*)$")
_BUS_KEY = re.compile(r"^bus/(\d+)/(.+)$")
_EFFECT_KEY = re.compile(r"^effect/(\d+)/(effect|enabled)$")
_SUB = re.compile(r'^SubResource\(\s*"([^"]+)"\s*\)$')


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Effect(_Model):
    """One effect slot of a bus; its settings as Godot literals, in file order."""

    type: str
    id: str = ""
    enabled: bool = True
    #: Property name -> Godot literal text ("-24.0", '&"Voice"').
    props: dict[str, str] = {}
    #: The sub-resource's body as written, for effects the Mixer doesn't manage.
    body: str = ""

    def number(self, name: str) -> float:
        defaults = COMPRESSOR_DEFAULTS if self.type == COMPRESSOR else REVERB_DEFAULTS
        raw = self.props.get(name)
        return float(raw) if raw is not None else float(defaults.get(name, 0.0))

    def set_number(self, name: str, value: float) -> None:
        self.props[name] = _float(value)

    @property
    def sidechain(self) -> str:
        return _unquote(self.props.get("sidechain", '&""'))

    @sidechain.setter
    def sidechain(self, value: str) -> None:
        self.props["sidechain"] = _string_name(value)


class Bus(_Model):
    name: str = Field(min_length=1)
    volume_db: float = 0.0
    mute: bool = False
    solo: bool = False
    bypass_fx: bool = False
    send: str = "Master"
    effects: list[Effect] = []

    def first(self, kind: str) -> Effect | None:
        return next((e for e in self.effects if e.type == kind), None)


class BusLayout(_Model):
    buses: list[Bus]
    #: The header's uid, kept so references by uid still resolve.
    uid: str = ""
    #: ``[ext_resource]`` lines, kept as written.
    external: list[str] = []
    #: Sub-resources no bus uses, kept as written (header and body).
    orphans: list[str] = []
    #: ``[resource]`` lines that are not bus settings, kept as written.
    extra: list[str] = []

    def bus(self, name: str) -> Bus | None:
        return next((b for b in self.buses if b.name == name), None)


# --- Reading ---------------------------------------------------------------------------


def _float(value: float) -> str:
    text = repr(float(value))
    return text if "e" not in text else f"{float(value):.6f}"


def _string_name(value: str) -> str:
    return '&"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unquote(raw: str) -> str:
    raw = raw.strip()
    raw = raw.removeprefix("&")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return raw


def _sections(text: str) -> list[tuple[str, dict[str, str], str, list[str]]]:
    """(kind, attributes, header line, body lines) per ``[section]``."""
    out: list[tuple[str, dict[str, str], str, list[str]]] = []
    for line in text.splitlines():
        found = _SECTION.match(line)
        if found:
            attributes = {k: _unquote(v) for k, v in _ATTR.findall(found.group(2))}
            out.append((found.group(1), attributes, line.rstrip(), []))
        elif out:
            out[-1][3].append(line.rstrip())
    return out


def _properties(lines: list[str]) -> list[tuple[str, str]]:
    """``key = value`` pairs; a value Godot spread over lines joins its first line."""
    pairs: list[tuple[str, str]] = []
    for line in lines:
        found = _PROPERTY.match(line)
        if found:
            pairs.append((found.group(1), found.group(2).strip()))
        elif pairs and line.strip():
            key, value = pairs[-1]
            pairs[-1] = (key, value + "\n" + line)
    return pairs


def _trimmed(lines: list[str]) -> str:
    return "\n".join(lines).strip("\n")


def from_tres(text: str) -> BusLayout:
    """Read an AudioBusLayout resource, also one Godot or a person wrote."""
    sections = _sections(text)
    if not sections or sections[0][0] != "gd_resource":
        raise ValueError("not a Godot text resource")
    if sections[0][1].get("type") != "AudioBusLayout":
        raise ValueError("not an audio bus layout (type AudioBusLayout)")
    uid = sections[0][1].get("uid", "")
    external: list[str] = []
    subs: dict[str, tuple[str, str, list[str]]] = {}
    order: list[str] = []
    resource: list[str] = []
    for kind, attributes, header, body in sections[1:]:
        if kind == "ext_resource":
            external.append(header)
        elif kind == "sub_resource":
            key = attributes.get("id", f"_{len(order)}")
            subs[key] = (attributes.get("type", ""), header, body)
            order.append(key)
        elif kind == "resource":
            resource = body
    buses: dict[int, dict[str, Any]] = {}
    extra: list[str] = []
    for key, value in _properties(resource):
        found = _BUS_KEY.match(key)
        if not found:
            extra.append(f"{key} = {value}")
            continue
        entry = buses.setdefault(int(found.group(1)), {"effects": {}})
        field = found.group(2)
        effect = _EFFECT_KEY.match(field)
        if effect:
            entry["effects"].setdefault(int(effect.group(1)), {})[effect.group(2)] = (
                value
            )
        elif field in {"name", "send"}:
            entry[field] = _unquote(value)
        elif field in {"mute", "solo", "bypass_fx"}:
            entry[field] = value == "true"
        elif field == "volume_db":
            entry[field] = float(value)
    used: set[str] = set()
    layout_buses: list[Bus] = []
    for index in sorted(buses):
        data = buses[index]
        effects: list[Effect] = []
        slots: dict[int, dict[str, str]] = data.pop("effects")
        for slot in sorted(slots):
            raw = slots[slot]
            found_sub = _SUB.match(raw.get("effect", ""))
            if not found_sub or found_sub.group(1) not in subs:
                continue
            key = found_sub.group(1)
            used.add(key)
            kind, _header, body = subs[key]
            effects.append(
                _effect_from(kind, key, body, raw.get("enabled", "true") == "true")
            )
        data.setdefault("name", "Master" if index == 0 else f"Bus {index}")
        layout_buses.append(Bus(effects=effects, **data))
    if not layout_buses or layout_buses[0].name != "Master":
        layout_buses.insert(0, Bus(name="Master"))
    for bus in layout_buses:
        if bus.name == "Master":
            bus.send = ""
    orphans = [
        _trimmed([subs[key][1], *subs[key][2]]) for key in order if key not in used
    ]
    return BusLayout(
        buses=layout_buses, uid=uid, external=external, orphans=orphans, extra=extra
    )


def _effect_from(kind: str, key: str, body: list[str], enabled: bool) -> Effect:
    if kind in {COMPRESSOR, REVERB}:
        return Effect(type=kind, id=key, enabled=enabled, props=dict(_properties(body)))
    return Effect(type=kind, id=key, enabled=enabled, body=_trimmed(body))


# --- Writing ---------------------------------------------------------------------------


def _ids(layout: BusLayout) -> None:
    """Give new effects a stable id: their type and bus ("AudioEffectReverb_ambience")."""
    taken = {e.id for b in layout.buses for e in b.effects if e.id}
    for orphan in layout.orphans:
        found = _ATTR.findall(orphan.splitlines()[0])
        taken.update(_unquote(v) for k, v in found if k == "id")
    for bus in layout.buses:
        for effect in bus.effects:
            if effect.id:
                continue
            stem = f"{effect.type}_{re.sub(r'[^a-z0-9]+', '_', bus.name.lower())}"
            candidate, count = stem, 2
            while candidate in taken:
                candidate, count = f"{stem}_{count}", count + 1
            effect.id = candidate
            taken.add(candidate)


def _effect_text(effect: Effect) -> str:
    header = f'[sub_resource type="{effect.type}" id="{effect.id}"]'
    if effect.type not in {COMPRESSOR, REVERB}:
        return _trimmed([header, effect.body])
    defaults = COMPRESSOR_DEFAULTS if effect.type == COMPRESSOR else REVERB_DEFAULTS
    known = [name for name in defaults if name in effect.props]
    other = [name for name in effect.props if name not in defaults]
    lines = [header] + [f"{name} = {effect.props[name]}" for name in known + other]
    return "\n".join(lines)


def to_tres(layout: BusLayout) -> str:
    """The layout as Godot text (deterministic: same layout, same bytes)."""
    layout = layout.model_copy(deep=True)
    _ids(layout)
    uid = f' uid="{layout.uid}"' if layout.uid else ""
    blocks = [f'[gd_resource type="AudioBusLayout" format=3{uid}]']
    if layout.external:
        blocks.append("\n".join(layout.external))
    blocks += layout.orphans
    blocks += [_effect_text(e) for b in layout.buses for e in b.effects]
    lines = ["[resource]", *layout.extra]
    for index, bus in enumerate(layout.buses):
        prefix = f"bus/{index}/"
        lines += [
            f"{prefix}name = {_string_name(bus.name)}",
            f"{prefix}solo = {str(bus.solo).lower()}",
            f"{prefix}mute = {str(bus.mute).lower()}",
            f"{prefix}bypass_fx = {str(bus.bypass_fx).lower()}",
            f"{prefix}volume_db = {_float(bus.volume_db)}",
            f"{prefix}send = {_string_name('' if index == 0 else bus.send)}",
        ]
        for slot, effect in enumerate(bus.effects):
            lines += [
                f'{prefix}effect/{slot}/effect = SubResource("{effect.id}")',
                f"{prefix}effect/{slot}/enabled = {str(effect.enabled).lower()}",
            ]
    blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


# --- The game core's buses and the Mixer's edits ---------------------------------------


def compressor(**values: float | str) -> Effect:
    """A compressor set up for ducking (threshold −24 dB, ratio 4, 20 ms, 250 ms)."""
    effect = Effect(type=COMPRESSOR)
    for name, value in {**DUCKING, **values}.items():
        if name == "sidechain":
            effect.sidechain = str(value)
        else:
            effect.set_number(name, float(value))
    return effect


def reverb(**values: float) -> Effect:
    """A room reverb (room 0.6, damping 0.5, wet 0.25, dry 1)."""
    effect = Effect(type=REVERB)
    for name, value in {**ROOM, **values}.items():
        effect.set_number(name, value)
    return effect


def standard_bus(name: str) -> Bus:
    """One of the game core's buses, as the core's layout sets it up."""
    bus = Bus(name=name, send="" if name == "Master" else "Master")
    if name == "Music":
        bus.effects = [compressor(sidechain="Voice")]
    elif name == "Ambience":
        bus.effects = [reverb()]
    return bus


def standard_layout() -> BusLayout:
    return BusLayout(buses=[standard_bus(name) for name in STANDARD_BUSES])


def add_missing_buses(layout: BusLayout) -> list[str]:
    """Add the game core's buses the layout lacks, after the others. Returns their names."""
    added = [name for name in STANDARD_BUSES if layout.bus(name) is None]
    layout.buses += [standard_bus(name) for name in added]
    return added


def ducking(layout: BusLayout) -> str:
    """What music ducks under ("Voice", "SFX"), or "" when it doesn't."""
    music = layout.bus("Music")
    effect = music.first(COMPRESSOR) if music is not None else None
    if effect is None or not effect.enabled:
        return ""
    return effect.sidechain


def set_ducking(layout: BusLayout, source: str) -> None:
    """Duck music under ``source`` with the Music compressor. "" turns ducking off:
    a compressor keyed to another bus is switched off (it keeps its settings); one
    that only compresses the music itself stays as it is."""
    if source and source not in DUCK_SOURCES:
        raise ValueError(f"Music ducks under Voice or SFX, not {source}.")
    music = layout.bus("Music")
    if music is None:
        if not source:
            return
        raise ValueError("The project has no Music bus to duck.")
    effect = music.first(COMPRESSOR)
    if effect is None or not (source or effect.sidechain):
        if not source:
            return
        effect = compressor()
        music.effects.insert(0, effect)
    effect.sidechain = source
    effect.enabled = bool(source)


def apply_mix(layout: BusLayout, mix: dict[str, Any]) -> list[str]:
    """Apply the Mixer's values to ``layout``. Returns plain words of what changed.

    ``mix``: {"buses": [{name, volume_db?, mute?, solo?, bypass_fx?, send?,
    reverb?: {enabled, wet?}, compressor?: {enabled, threshold?}}], "ducking"?: str}.
    A bus missing from the layout is added; effects the Mixer doesn't manage stay.
    """
    changed: list[str] = []
    for raw in mix.get("buses", []):
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError("A bus needs a name.")
        bus = layout.bus(name)
        added = bus is None
        if bus is None:
            bus = standard_bus(name) if name in STANDARD_BUSES else Bus(name=name)
            layout.buses.append(bus)
            changed.append(f"Added the {name} bus")
        before = bus.model_dump()
        if "volume_db" in raw:
            bus.volume_db = round(max(-80.0, min(24.0, float(raw["volume_db"]))), 1)
        for flag in ("mute", "solo", "bypass_fx"):
            if flag in raw:
                setattr(bus, flag, bool(raw[flag]))
        if "send" in raw and name != "Master":
            bus.send = str(raw["send"]) or "Master"
        _apply_effect(bus, REVERB, raw.get("reverb"), "wet", 0.0, 1.0)
        _apply_effect(bus, COMPRESSOR, raw.get("compressor"), "threshold", -60.0, 0.0)
        if bus.model_dump() != before and not added:
            changed.append(_bus_words(bus, before))
    if "ducking" in mix:
        was = ducking(layout)
        set_ducking(layout, str(mix["ducking"] or ""))
        now = ducking(layout)
        if now != was:
            changed.append(
                f"Music ducks under {now}" if now else "Music no longer ducks"
            )
    return changed


def _apply_effect(
    bus: Bus, kind: str, raw: Any, amount: str, low: float, high: float
) -> None:
    if not isinstance(raw, dict):
        return
    effect = bus.first(kind)
    if effect is None:
        if not raw.get("enabled", False):
            return
        effect = reverb() if kind == REVERB else compressor()
        bus.effects.append(effect)
    effect.enabled = bool(raw.get("enabled", effect.enabled))
    if amount in raw:
        effect.set_number(amount, round(max(low, min(high, float(raw[amount]))), 3))


def db_text(value: float) -> str:
    """ "−3.0 dB", "+1.5 dB", "0.0 dB"."""
    sign = "−" if value < 0 else "+" if value > 0 else ""
    return f"{sign}{abs(value):.1f} dB"


def _bus_words(bus: Bus, before: dict[str, Any]) -> str:
    words: list[str] = []
    if bus.volume_db != before["volume_db"]:
        words.append(db_text(bus.volume_db))
    if bus.mute != before["mute"]:
        words.append("muted" if bus.mute else "unmuted")
    if bus.solo != before["solo"]:
        words.append("solo" if bus.solo else "solo off")
    if bus.bypass_fx != before["bypass_fx"]:
        words.append("effects bypassed" if bus.bypass_fx else "effects on")
    if not words:
        words.append("effects")
    return f"{bus.name} " + ", ".join(words)


def layout_path(project: Path) -> str:
    """The layout's project-relative path (project setting
    ``audio/buses/default_bus_layout``, else Godot's default)."""
    settings = project / "project.godot"
    if settings.is_file():
        section = ""
        for line in settings.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("["):
                section = line
            elif section == "[audio]" and line.startswith("buses/default_bus_layout"):
                value = _unquote(line.split("=", 1)[1])
                if value.startswith("res://"):
                    return value.removeprefix("res://")
    return DEFAULT_PATH
