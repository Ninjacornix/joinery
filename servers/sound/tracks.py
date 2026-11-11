"""Adaptive music for joinery-sound: compose_track, edit_track, inspect_track.

`compose_track` writes a track plan's layers as MIDI (rule-based composition,
`joinery.tools.compose`), renders every layer of every section to a synced,
loop-seamless Ogg stem in the offline worker (`joinery.tools.stems`), writes
Godot's import settings for each, and the track description the game core's
`Music` autoload and the editor's Mixer read (`joinery.music_track`).

`edit_track` changes the plan the recipe carries (tempo, key, mood, layers,
voices, density, variation, sections) and renders again; muting a layer only
changes the description. A layer's fader, mute, solo and the state levels a
user set in the Mixer are kept across edits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from joinery import authoring
from joinery.bible_rules import load_rules
from joinery.mcp_runtime import validate_arguments
from joinery.music_track import (
    Layer,
    MusicTrack,
    Section,
    State,
    default_states,
    from_tres,
    to_tres,
)
from joinery.recipe import load_recipe, recipe_paths
from joinery.tools import audio_chain, compose, midi, stems
from servers.errors import tool_errors
from servers.sound.ops import _check_format, _output, _run


class TrackChange(BaseModel):
    """Typed edits to a track's plan; anything left out stays as it is."""

    model_config = ConfigDict(extra="forbid")

    tempo_bpm: float | None = Field(default=None, ge=40, le=220)
    key: str | None = None
    mood: compose.Mood | None = None
    bars: int | None = Field(default=None, ge=2, le=64)
    density: float | None = Field(default=None, ge=0.0, le=1.0)
    #: A new melody and new fills with the same harmony.
    variation: int | None = Field(default=None, ge=0, le=999)
    seed: int | None = Field(default=None, ge=0, lt=2**31)
    sections: list[str] | None = None
    stingers: list[str] | None = None
    add_layers: list[compose.LayerPlan | str] = []
    remove_layers: list[str] = []
    #: Layer -> {"program": GM 0-127} or {"surge": "audio/…json"}: another instrument.
    revoice: dict[str, dict[str, Any]] = {}
    #: Layer -> 0-1.
    layer_density: dict[str, float] = {}
    #: Layer -> muted; changes only the description (no render).
    mute: dict[str, bool] = {}

    def renders(self) -> bool:
        data = self.model_dump(exclude_defaults=True)
        return bool(set(data) - {"mute"})


def _description_path(name: str) -> str:
    return f"music/{name}.tres"


def _previous(project: Path, name: str) -> MusicTrack | None:
    path = project / _description_path(name)
    if not path.is_file():
        return None
    try:
        return from_tres(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def describe(plan: compose.TrackPlan, previous: MusicTrack | None) -> MusicTrack:
    """The track description for ``plan``, keeping what the user set before."""
    where = stems.paths(plan)
    kept = {layer.name: layer for layer in (previous.layers if previous else [])}
    layers = [
        kept[layer.name].model_copy(update={"role": layer.role})
        if layer.name in kept
        else Layer(name=layer.name, role=layer.role)
        for layer in plan.layers
    ]
    states = default_states(layers, plan.sections)
    if previous is not None:
        for state, old in previous.states.items():
            fresh = states[state]
            section = old.section if old.section in plan.sections else fresh.section
            levels = dict(fresh.layers)
            levels.update({k: v for k, v in old.layers.items() if k in levels})
            states[state] = State(section=section, layers=levels)
    return MusicTrack(
        name=plan.name,
        mood=plan.mood,
        key=str(plan.parsed_key),
        tempo_bpm=plan.tempo_bpm,
        beats_per_bar=plan.beats_per_bar,
        layers=layers,
        sections=[
            Section(
                name=s,
                bars=plan.bars,
                stems={k: "res://" + v for k, v in where.stems[s].items()},
            )
            for s in plan.sections
        ],
        states=states,
        default_state=previous.default_state if previous else "explore",
        change_on=previous.change_on if previous else "bar",
        fade_beats=previous.fade_beats if previous else 2.0,
        stingers={e: "res://" + paths[1] for e, paths in where.stingers.items()},
        bus=previous.bus if previous else "Music",
    )


def _write_imports(project: Path, plan: compose.TrackPlan) -> list[str]:
    where = stems.paths(plan)
    written = []
    beats = round(plan.bars * plan.beats_per_bar)
    for layers in where.stems.values():
        for export in layers.values():
            path = authoring.confined(project, export + ".import")
            text = path.read_text() if path.is_file() else None
            path.write_text(
                audio_chain.godot_import(
                    text,
                    export,
                    loop=True,
                    loop_offset=0.0,
                    bpm=plan.tempo_bpm,
                    beat_count=beats,
                    bar_beats=plan.beats_per_bar,
                )
            )
            written.append(path.relative_to(project).as_posix())
    for _source, export in where.stingers.values():
        path = authoring.confined(project, export + ".import")
        text = path.read_text() if path.is_file() else None
        path.write_text(
            audio_chain.godot_import(text, export, loop=False, loop_offset=0.0)
        )
        written.append(path.relative_to(project).as_posix())
    return written


def _owning_recipe(project: Path, name: str) -> tuple[Path, compose.TrackPlan] | None:
    """The recipe that renders track ``name``, and the plan it carries."""
    folder = f"audio/music/{name}/"
    for path in recipe_paths(project):
        recipe = load_recipe(path)
        if recipe.tool != "surge-xt" or not any(
            o.startswith(folder) for o in recipe.outputs
        ):
            continue
        plan = stems.plan_from_script((project / recipe.script).read_text())
        if plan is not None and plan.name == name:
            return path, plan
    return None


async def _render(
    project: Path, plan: compose.TrackPlan, lufs: float | None, note: str
) -> dict[str, Any]:
    authoring.enabled(project, "surge-xt")
    where = stems.paths(plan)
    rules = load_rules(project)
    rule = None
    for export in where.oggs():
        rule = _check_format(rules.audio.format, export) or rule
    (resolved,) = audio_chain.resolve_targets(
        [audio_chain.Normalise(op="normalise", lufs=lufs)],
        where.oggs()[0],
        rules.audio.lufs,
        rules.audio.true_peak_max_dbtp,
        default_lufs=midi.DEFAULT_MUSIC_LUFS,
    )
    assert isinstance(resolved, audio_chain.Normalise)
    assert resolved.lufs is not None and resolved.true_peak is not None
    output = _output({"channels": 2}, rule)
    songs = compose.compose(plan)
    stingers = {e: compose.stinger(plan, e) for e in plan.stingers}
    patches: dict[str, dict[str, Any]] = {}
    for song in songs.values():
        patches.update(midi.surge_parameters(project, song))
    script = stems.render_script(
        plan,
        songs,
        stingers,
        patches,
        lufs=resolved.lufs,
        true_peak=resolved.true_peak,
        output=output,
    )
    outputs = where.outputs()
    # A track that lost a layer, section or stinger: its old recipe still claims
    # those files. Retire it and its leftovers; put both back if the render fails.
    owner = _owning_recipe(project, plan.name)
    retired: Path | None = None
    removed: dict[Path, bytes] = {}
    if owner is not None:
        old = load_recipe(owner[0])
        stale = [o for o in old.outputs if o not in outputs]
        if stale:
            history = project / "recipes/history"
            history.mkdir(exist_ok=True)
            retired = history / owner[0].name
            owner[0].rename(retired)
            for output_path in stale:
                for path in (
                    project / output_path,
                    project / (output_path + ".import"),
                ):
                    if path.is_file():
                        removed[path] = path.read_bytes()
                        path.unlink()
    instruments = sorted(
        {
            "FluidSynth with " + midi.SOUNDFONT_CREDIT
            if layer.surge is None
            else "Surge XT patch " + layer.surge
            for layer in plan.layers
        }
    )
    summary = (
        f"Adaptive track {plan.name}: {len(plan.layers)} layer(s) "
        f"({', '.join(layer.name for layer in plan.layers)}) x "
        f"{len(plan.sections)} section(s) of {plan.bars} bars at "
        f"{plan.tempo_bpm:g} BPM in {plan.parsed_key}, {plan.mood}; "
        f"stingers: {', '.join(plan.stingers) or 'none'}. Instruments: "
        + "; ".join(instruments)
        + "."
    )
    try:
        result = await _run(
            project,
            "surge-xt",
            script,
            outputs,
            (),
            summary + (f" {note}" if note else ""),
        )
    except BaseException:
        for path, data in removed.items():
            path.write_bytes(data)
        if retired is not None and owner is not None:
            retired.rename(owner[0])
        raise
    imports = _write_imports(project, plan)
    description = describe(plan, _previous(project, plan.name))
    _write_description(project, where.description, description)
    result.update(
        track=summary,
        description=where.description,
        godot_import=imports,
        stems={s: list(layers.values()) for s, layers in where.stems.items()},
        stingers=[export for _source, export in where.stingers.values()],
        removed=sorted(p.relative_to(project).as_posix() for p in removed),
    )
    return result


async def compose_track(
    project: Path,
    name: str,
    layers: list[Any],
    mood: str = "adventurous",
    key: str = "D minor",
    tempo_bpm: float = 110.0,
    bars: int = 8,
    sections: list[str] | None = None,
    stingers: list[str] | None = None,
    density: float | None = None,
    seed: int = 1,
    beats_per_bar: int = 4,
    lufs: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    validate_arguments({"layers": layers, "note": note})
    data: dict[str, Any] = {
        "name": name,
        "layers": layers,
        "mood": mood,
        "key": key,
        "tempo_bpm": tempo_bpm,
        "bars": bars,
        "seed": seed,
        "beats_per_bar": beats_per_bar,
        "density": density,
    }
    if sections is not None:
        data["sections"] = sections
    if stingers is not None:
        data["stingers"] = stingers
    plan = compose.TrackPlan.model_validate(data)
    if _owning_recipe(project, name) is not None:
        raise ValueError(
            f"Track {name} already exists; change it with edit_track, which keeps the "
            "mix the user set."
        )
    folder = project / f"audio/music/{name}"
    if folder.is_dir() and any(folder.iterdir()):
        raise ValueError(
            f"audio/music/{name}/ already holds files Joinery didn't write."
        )
    return await _render(project, plan, lufs, note)


def apply_change(plan: compose.TrackPlan, change: TrackChange) -> compose.TrackPlan:
    data = plan.model_dump()
    for field in (
        "tempo_bpm",
        "key",
        "mood",
        "bars",
        "density",
        "variation",
        "seed",
        "sections",
        "stingers",
    ):
        value = getattr(change, field)
        if value is not None:
            data[field] = value
    names = [layer["name"] for layer in data["layers"]]
    unknown = (
        set(change.remove_layers) | set(change.revoice) | set(change.layer_density)
    ) - set(names)
    if unknown:
        raise ValueError(
            "No layer named "
            + ", ".join(sorted(unknown))
            + "; the track has "
            + ", ".join(names)
        )
    layers = [
        layer for layer in data["layers"] if layer["name"] not in change.remove_layers
    ]
    for layer in layers:
        voice = change.revoice.get(layer["name"])
        if voice is not None:
            if set(voice) - {"program", "surge"} or len(voice) != 1:
                raise ValueError(
                    'revoice a layer with {"program": n} or {"surge": path}'
                )
            layer.update({"program": None, "surge": None, **voice})
        if layer["name"] in change.layer_density:
            layer["density"] = change.layer_density[layer["name"]]
    for added in change.add_layers:
        layers.append(
            {"name": added, "role": added}
            if isinstance(added, str)
            else added.model_dump()
        )
    data["layers"] = layers
    if not layers:
        raise ValueError("an edit cannot remove every layer")
    return compose.TrackPlan.model_validate(data)


async def edit_track(
    project: Path,
    name: str,
    changes: dict[str, Any],
    lufs: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    validate_arguments({"changes": changes, "note": note})
    change = TrackChange.model_validate(changes)
    owner = _owning_recipe(project, name)
    if owner is None:
        raise ValueError(f"No track named {name}; make one with compose_track.")
    plan = apply_change(owner[1], change)
    if change.renders() or lufs is not None:
        result = await _render(project, plan, lufs, note)
    else:
        result = {"status": "staged", "requires_review": True, "rendered": False}
    if change.mute:
        where = stems.paths(plan)
        track = _previous(project, name) or describe(plan, None)
        missing = set(change.mute) - {layer.name for layer in track.layers}
        if missing:
            raise ValueError("No layer named " + ", ".join(sorted(missing)))
        track = track.model_copy(
            update={
                "layers": [
                    layer.model_copy(
                        update={"muted": change.mute.get(layer.name, layer.muted)}
                    )
                    for layer in track.layers
                ]
            }
        )
        _write_description(project, where.description, track)
        result["description"] = where.description
    return result


def inspect_track(project: Path, name: str) -> dict[str, Any]:
    owner = _owning_recipe(project, name)
    if owner is None:
        raise ValueError(f"No track named {name}.")
    plan = owner[1]
    track = _previous(project, name)
    return {
        "plan": plan.model_dump(),
        "description": track.model_dump() if track else None,
        "stem_seconds": round(stems.stem_seconds(plan), 4),
        "recipe": owner[0].relative_to(project).as_posix(),
    }


def register(server: MCPServer[Any], project: Path) -> None:
    """Add the adaptive music operations to the joinery-sound server."""

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def inspect_track(name: str) -> dict[str, Any]:
        """Read an adaptive music track: its plan (mood, key, tempo, bars, layers, sections, seed) and its description (layer faders, mutes, intensity-state levels). Read it before edit_track."""
        return _inspect(project, name)

    @server.tool()
    @tool_errors
    async def compose_track(
        name: str,
        layers: list[Any],
        mood: str = "adventurous",
        key: str = "D minor",
        tempo_bpm: float = 110.0,
        bars: int = 8,
        sections: list[str] | None = None,
        stingers: list[str] | None = None,
        density: float | None = None,
        seed: int = 1,
        beats_per_bar: int = 4,
        lufs: float | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Compose an adaptive music track by rules (no generative model) and render it as synced stems for the game's Music system. layers: role names ("pads", "bass", "percussion", "melody", "tension") or {"name", "role", "program" (General MIDI) | "surge" ("audio/…json"), "density", "volume"}. mood: calm, adventurous, heroic, mysterious, tense, dark, playful, sad, epic (picks mode, chords, instruments, density). key like "D minor" or "F#" (the mood's mode). sections: ["main"] or ["main", "combat"] (combat plays the second: a horizontal switch). stingers: victory, checkpoint, fail. Same arguments and seed, same music. Writes audio/music/<name>/<section>.mid, <section>_<layer>.ogg (every stem of a section the same length, loop-seamless; the section mix meets the art bible's music loudness with one gain for all stems), stinger_<event>.ogg and <name>.tres (the description the Music autoload and the Mixer read). Requires approved art direction and user review."""
        return await _compose(
            project,
            name,
            layers,
            mood,
            key,
            tempo_bpm,
            bars,
            sections,
            stingers,
            density,
            seed,
            beats_per_bar,
            lufs,
            note,
        )

    @server.tool()
    @tool_errors
    async def edit_track(
        name: str, changes: dict[str, Any], lufs: float | None = None, note: str = ""
    ) -> dict[str, Any]:
        """Change an adaptive track and render it again; everything not mentioned stays. changes = {"tempo_bpm", "key", "mood", "bars", "density", "variation" (new melody and fills, same harmony), "seed", "sections", "stingers", "add_layers": [role or layer], "remove_layers": [name], "revoice": {layer: {"program": n} | {"surge": path}}, "layer_density": {layer: 0-1}, "mute": {layer: bool} (description only, no render)}. Layer faders, mutes, solos and state levels set in the Mixer are kept."""
        return await _edit(project, name, changes, lufs, note)


_inspect = inspect_track
_compose = compose_track
_edit = edit_track


def _write_description(project: Path, path: str, track: MusicTrack) -> None:
    target = authoring.confined(project, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_tres(track), encoding="utf-8")
