"""Typed sound operations for joinery-sound: songs from MIDI, Surge XT parameter
edits that keep the rest of a patch, and audio edits (trim, fade, loudness, loop
points, format) through ffmpeg.

Every operation writes a recipe owned by the tool that made the sound (Surge XT
or SuperCollider), runs it in the offline creative worker, and records it the
same way `operate_document` does, so the result is staged for review and
`joinery rebuild` replays it byte for byte.

An audio edit is chained onto the recipe that rendered its input: the new
recipe replays that render and then edits the fresh output, so editing a file
in place never leaves two recipes claiming it. The edits a recipe carries are
listed on one marker line, so a later patch or song edit renders again and
re-applies them instead of silently dropping them.
"""

from __future__ import annotations

import ast
import json
import re
import uuid
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from joinery import authoring, tools
from joinery.bible_rules import AudioFormatRule, load_rules
from joinery.locking import claimed_outputs
from joinery.mcp_runtime import validate_arguments
from joinery.recipe import Recipe, load_recipe, store_preimage
from joinery.tools import audio_chain, midi
from joinery.tools.surge_xt import MODULE as SURGE
from servers.errors import tool_errors

#: Tools whose rendered audio the audio edits can rebuild.
SOUND_TOOLS = ("surge-xt", "supercollider")
#: Marker line (after the comment prefix) listing a recipe's audio edits.
EDITS_MARKER = "JOINERY_AUDIO_EDITS "
#: Marker line in a song recipe with its render options.
RENDER_LINE = "RENDER_JSON = "
#: Music renders run Surge twice per Surge track; allow for slow emulation.
RENDER_TIMEOUT = 1200


# --- Recipes ---------------------------------------------------------------------


def _comment(tool: str) -> str:
    return "// " if tool == "supercollider" else "# "


def recipe_edits(text: str) -> dict[str, Any]:
    """The audio edits a recipe script carries: {"base_outputs", "edits"}."""
    for line in text.splitlines():
        for prefix in ("# ", "// "):
            if line.startswith(prefix + EDITS_MARKER):
                found = json.loads(line.removeprefix(prefix + EDITS_MARKER))
                if isinstance(found, dict):
                    return found
    return {}


def _render_options(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        if line.startswith(RENDER_LINE):
            found = json.loads(ast.literal_eval(line.removeprefix(RENDER_LINE)))
            if isinstance(found, dict):
                return found
    return {}


def _chain(tool: str, previous: str, payload: dict[str, Any]) -> str:
    """Wrap `previous` so its output is edited as `payload` says."""
    steps = audio_chain.parse_steps(payload["steps"])
    post = audio_chain.program(
        payload["source"],
        payload["export"],
        steps,
        audio_chain.Output.model_validate(payload["output"]),
    )
    if tool == "surge-xt":
        return SURGE.chain_script(previous, post, payload["source"])
    if tool == "supercollider":
        return audio_chain.supercollider_chain(previous, post, payload["source"])
    raise ValueError(f"{tool} has no audio edit chain.")


def _with_edits(
    tool: str, base: str, base_outputs: list[str], edits: list[dict[str, Any]]
) -> tuple[str, list[str]]:
    """The base render script with every edit applied in order, and its outputs."""
    script = base
    outputs = list(base_outputs)
    for edit in edits:
        script = _chain(tool, script, edit)
        if edit["export"] not in outputs:
            outputs.append(edit["export"])
    if edits:
        marker = {"base_outputs": base_outputs, "edits": edits}
        script = (
            _comment(tool)
            + EDITS_MARKER
            + json.dumps(marker, sort_keys=True)
            + "\n"
            + script
        )
    return script, outputs


def _owner(project: Path, path: str) -> tuple[str, Recipe, str] | None:
    """The tool, recipe and script text that currently produce `path`."""
    claim = claimed_outputs(project).get(path)
    if claim is None:
        return None
    tool, recipe_path = claim
    recipe = load_recipe(project / recipe_path)
    return tool, recipe, (project / recipe.script).read_text()


def _tail(text: str) -> str:
    return text.strip()[-1500:] or "no output"


async def _run(
    project: Path,
    tool: str,
    script_text: str,
    outputs: list[str],
    sources: tuple[str, ...],
    note: str,
    *,
    chained: bool = False,
) -> dict[str, Any]:
    """Run a recipe script in the worker, then record it like operate_document."""
    module = tools.get(tool)
    # Refuse before rendering if another recipe would be left half-replaced.
    authoring._replacements(project, tuple(outputs))
    identifier = uuid.uuid4().hex
    recipe_dir = project / "recipes"
    recipe_dir.mkdir(exist_ok=True)
    script = recipe_dir / (identifier + module.script_ext)
    script.write_text(script_text)
    for output in outputs:
        authoring.confined(project, output).parent.mkdir(parents=True, exist_ok=True)
    recipe = Recipe(
        tool=tool,
        script=script.relative_to(project).as_posix(),
        outputs=tuple(outputs),
        sources=sources,
        note=note,
    )
    result = await authoring.run_offline(
        module.replay_argv(recipe.script), project, RENDER_TIMEOUT
    )
    failed = not result.ok or "JOINERY_POST_FAILED" in result.output
    if chained and tool == "supercollider" and "JOINERY_POST_OK" not in result.output:
        failed = True
    missing = [o for o in outputs if not (project / o).is_file()]
    if failed or missing:
        script.unlink(missing_ok=True)
        detail = _tail(result.output)
        if missing and not failed:
            detail = "it did not write " + ", ".join(missing)
        raise RuntimeError(f"{module.name} did not complete the operation: {detail}")
    report = audio_chain.parse_report(result.stdout)
    recorded = await authoring._record(
        project, recipe, recipe_dir / (identifier + ".toml")
    )
    return {**recorded, "measured": report}


def _check_format(
    rules_format: dict[str, AudioFormatRule], export: str
) -> AudioFormatRule | None:
    rule = audio_chain.folder_value(rules_format, export)
    suffix = Path(export).suffix.lower().lstrip(".")
    if rule is not None and rule.container != suffix:
        raise ValueError(
            f"The art bible asks for {rule.container.upper()} files in "
            f"{Path(export).parent.as_posix()}; export {Path(export).with_suffix('.' + rule.container).as_posix()} instead."
        )
    return rule


def _output(
    raw: dict[str, Any] | None, rule: AudioFormatRule | None
) -> audio_chain.Output:
    values = dict(raw or {})
    if rule is not None:
        if "sample_rate" not in values and rule.sample_rates:
            values["sample_rate"] = rule.sample_rates[0]
        if "channels" not in values and rule.channels:
            values["channels"] = rule.channels[0]
        if "bit_depth" not in values and rule.bit_depths:
            values["bit_depth"] = rule.bit_depths[0]
    return audio_chain.Output.model_validate(values)


def _write_import(
    project: Path,
    export: str,
    report: dict[str, Any],
    *,
    bpm: float | None = None,
    beat_count: int | None = None,
    bar_beats: int | None = None,
) -> str | None:
    """Godot's import settings for an Ogg export, with its loop written in."""
    if Path(export).suffix.lower() != ".ogg":
        return None
    loop = report.get("loop")
    path = authoring.confined(project, export + ".import")
    text = path.read_text() if path.is_file() else None
    path.write_text(
        audio_chain.godot_import(
            text,
            export,
            loop=bool(loop),
            loop_offset=float(loop["start_seconds"]) if loop else 0.0,
            bpm=bpm,
            beat_count=beat_count,
            bar_beats=bar_beats,
        )
    )
    return path.relative_to(project).as_posix()


def _audio_path(project: Path, path: str, what: str) -> str:
    authoring.confined(project, path)
    if Path(path).parts[:1] != ("audio",):
        raise ValueError(f"The {what} belongs under audio/.")
    return Path(path).as_posix()


# --- Audio edits -------------------------------------------------------------------


async def process_audio(
    project: Path,
    source: str,
    steps: list[dict[str, Any]],
    export: str | None = None,
    output: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    validate_arguments({"steps": steps, "output": output or {}, "note": note})
    parsed = audio_chain.parse_steps(steps)
    source = _audio_path(project, source, "audio file")
    export = _audio_path(project, export or source, "export")
    audio_chain.check_paths(source, export)
    owner = _owner(project, source)
    if owner is None:
        raise ValueError(
            f"{source} has no recipe (it was imported or made by hand). Audio edits "
            "rebuild sound that Surge XT or SuperCollider rendered; ask the user "
            "before changing an imported file."
        )
    tool, recipe, text = owner
    if tool not in SOUND_TOOLS:
        raise ValueError(
            f"{source} was made by {tool}; audio edits need Surge XT or SuperCollider."
        )
    authoring.enabled(project, tool)
    if not (project / source).is_file():
        raise ValueError(f"{source} is missing.")
    rules = load_rules(project)
    rule = _check_format(rules.audio.format, export)
    resolved = audio_chain.resolve_targets(
        parsed, export, rules.audio.lufs, rules.audio.true_peak_max_dbtp
    )
    payload = {
        "source": source,
        "export": export,
        "steps": [step.model_dump() for step in resolved],
        "output": _output(output, rule).model_dump(),
    }
    carried = recipe_edits(text)
    if carried:
        base_outputs = list(carried["base_outputs"])
        edits = [*carried["edits"], payload]
        base = _base_script(text, tool)
    else:
        base_outputs = list(recipe.outputs)
        edits = [payload]
        base = text
    script, outputs = _with_edits(tool, base, base_outputs, edits)
    summary = ", ".join(step.op for step in resolved)
    if export != source:
        summary += f", written to {export}"
    result = await _run(
        project,
        tool,
        script,
        outputs,
        recipe.sources,
        f"Audio edit of {source}: {summary}." + (f" {note}" if note else ""),
        chained=True,
    )
    imported = _write_import(project, export, result["measured"])
    if imported:
        result["godot_import"] = imported
    return result


#: Where each chain level keeps the script it replays.
_PREVIOUS = {
    "surge-xt": re.compile(
        r"^exec\(compile\((.*), 'previous-step', 'exec'\)", re.MULTILINE
    ),
    "supercollider": re.compile(
        r"^thisProcess\.interpreter\.interpret\((.*)\);\n\)\n\Z",
        re.MULTILINE | re.DOTALL,
    ),
}


def _base_script(text: str, tool: str) -> str:
    """The render script under a recipe's audio edits (the script itself if none)."""
    levels = len(recipe_edits(text).get("edits", []))
    for _ in range(levels):
        found = _PREVIOUS[tool].search(text)
        if found is None:
            raise ValueError("This recipe's audio edits could not be read back.")
        literal = found.group(1)
        text = (
            ast.literal_eval(literal)
            if tool == "surge-xt"
            else audio_chain.sc_unquote(literal)
        )
    return text


# --- Surge XT ------------------------------------------------------------------------


async def set_surge_parameters(
    project: Path,
    source: str,
    parameters: dict[str, Any],
    notes: list[dict[str, Any]] | None = None,
    seconds: float | None = None,
    exports: list[str] | None = None,
    note: str = "",
) -> dict[str, Any]:
    validate_arguments({"parameters": parameters, "notes": notes or [], "note": note})
    authoring.enabled(project, "surge-xt")
    source = _audio_path(project, source, "Surge source")
    path = project / source
    if path.suffix != ".json" or not path.is_file():
        raise ValueError("Give an existing Surge XT .json source under audio/.")
    patch = json.loads(path.read_text())
    if not isinstance(patch, dict) or patch.get("synth") != "surge-xt":
        raise ValueError(f"{source} is not a Surge XT source.")
    if not isinstance(parameters, dict):
        raise ValueError(  # noqa: TRY004 - reported to the agent as a tool error
            "parameters maps Surge parameter names to values."
        )
    merged = dict(patch.get("parameters", {}))
    for name, value in parameters.items():
        if value is None:
            merged.pop(name, None)  # back to the plugin's default
        else:
            merged[name] = value
    patch["parameters"] = merged
    if notes is not None:
        patch["notes"] = notes
    if seconds is not None:
        patch["seconds"] = seconds
    code = json.dumps(patch, indent=2)
    owner = _owner(project, source)
    edits: list[dict[str, Any]] = []
    if owner is not None:
        tool, recipe, text = owner
        if tool != "surge-xt":
            raise ValueError(f"{source} is made by {tool}, not Surge XT.")
        carried = recipe_edits(text)
        base_outputs = list(carried.get("base_outputs", recipe.outputs))
        edits = list(carried.get("edits", []))
        wav = [o for o in base_outputs if o != source]
    else:
        wav = []
    if exports is not None:
        wav = [_audio_path(project, e, "export") for e in exports]
    if not wav:
        raise ValueError("Give the WAV exports this patch renders.")
    base = SURGE.script(source, "", code, wav)
    script, outputs = _with_edits("surge-xt", base, [source, *wav], edits)
    sources = (store_preimage(project, path),)
    changed = sorted(parameters)
    result = await _run(
        project,
        "surge-xt",
        script,
        outputs,
        sources,
        "Surge parameters: "
        + ", ".join(changed[:12])
        + (" …" if len(changed) > 12 else "")
        + (f"; {len(edits)} audio edit(s) re-applied" if edits else "")
        + (f". {note}" if note else ""),
        chained=bool(edits),
    )
    result["parameters"] = merged
    return result


# --- Music ---------------------------------------------------------------------------


def _music_paths(project: Path, source: str, export: str) -> tuple[str, str]:
    source = _audio_path(project, source, "song source")
    export = _audio_path(project, export, "export")
    if Path(source).suffix.lower() not in {".mid", ".midi"}:
        raise ValueError("A song's source is a .mid file under audio/.")
    audio_chain.check_paths(export, export)
    return source, export


async def _render_song(
    project: Path,
    source: str,
    export: str,
    song: midi.Song,
    loop: bool,
    lufs: float | None,
    note: str,
) -> dict[str, Any]:
    authoring.enabled(project, "surge-xt")
    rules = load_rules(project)
    rule = _check_format(rules.audio.format, export)
    steps = audio_chain.resolve_targets(
        [audio_chain.Normalise(op="normalise", lufs=lufs)],
        export,
        rules.audio.lufs,
        rules.audio.true_peak_max_dbtp,
        default_lufs=midi.DEFAULT_MUSIC_LUFS,
    )
    output = _output({"channels": 2}, rule)
    if output.sample_rate is not None and output.sample_rate != song.sample_rate:
        song = song.model_copy(update={"sample_rate": output.sample_rate})
        midi.Song.model_validate(song.model_dump())
    patches = midi.surge_parameters(project, song)
    base = (
        RENDER_LINE
        + repr(
            json.dumps({"export": export, "loop": loop, "lufs": lufs}, sort_keys=True)
        )
        + "\n"
        + midi.render_script(
            song, source, export, patches, loop=loop, steps=steps, output=output
        )
    )
    owner = _owner(project, source)
    sources: tuple[str, ...] = ()
    edits: list[dict[str, Any]] = []
    if owner is not None:
        tool, recipe, text = owner
        if tool != "surge-xt":
            raise ValueError(
                f"{source} is made by {tool}; its song cannot be rendered here."
            )
        edits = list(recipe_edits(text).get("edits", []))
        sources = recipe.sources
    elif (project / source).is_file():
        # Rewriting an imported MIDI file: keep the original as the starting document.
        sources = (store_preimage(project, project / source),)
    script, outputs = _with_edits("surge-xt", base, [source, export], edits)
    surge_tracks = sorted({t.surge for t in song.tracks if t.surge})
    instruments = []
    if any(t.kind != "surge" for t in song.tracks):
        instruments.append("FluidSynth with " + midi.SOUNDFONT_CREDIT)
    if surge_tracks:
        instruments.append("Surge XT patches " + ", ".join(surge_tracks))
    summary = (
        f"Song: {len(song.tracks)} track(s), {song.bars} bars of "
        f"{song.beats_per_bar}/{song.beat_unit} at {song.tempo_bpm:g} BPM"
        + (", looping" if loop else "")
        + ". Instruments: "
        + "; ".join(instruments)
        + "."
    )
    result = await _run(
        project,
        "surge-xt",
        script,
        outputs,
        sources,
        summary + (f" {note}" if note else ""),
        chained=bool(edits),
    )
    imported = _write_import(
        project,
        export,
        result["measured"],
        bpm=song.tempo_bpm,
        beat_count=round(song.length_beats) if loop else 0,
        bar_beats=round(song.bar_beats),
    )
    if imported:
        result["godot_import"] = imported
    result["song"] = summary
    return result


async def compose_music(
    project: Path,
    source: str,
    song: dict[str, Any],
    export: str,
    loop: bool = True,
    lufs: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    validate_arguments({"song": song, "note": note})
    source, export = _music_paths(project, source, export)
    owner = _owner(project, source)
    if owner is None and (project / source).is_file():
        raise ValueError(
            f"{source} already exists and Joinery did not write it; change it with "
            "edit_music (which keeps its notes) or render it with render_music."
        )
    return await _render_song(
        project, source, export, midi.Song.model_validate(song), loop, lufs, note
    )


def _current_song(project: Path, source: str) -> tuple[midi.Song, dict[str, Any]]:
    owner = _owner(project, source)
    if owner is not None:
        tool, _recipe, text = owner
        song = midi.song_from_script(_base_script(text, tool))
        if tool != "surge-xt" or song is None:
            raise ValueError(f"{source} was not written as a Joinery song.")
        return song, _render_options(_base_script(text, tool))
    path = project / source
    if not path.is_file():
        raise ValueError(f"{source} is missing.")
    return midi.read_midi(path.read_bytes()), {}


async def edit_music(
    project: Path,
    source: str,
    changes: dict[str, Any],
    export: str | None = None,
    loop: bool | None = None,
    lufs: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    validate_arguments({"changes": changes, "note": note})
    song, options = _current_song(project, _audio_path(project, source, "song source"))
    export = export or options.get("export")
    if not export:
        raise ValueError(
            "Give the export this song renders to, such as audio/music/theme.ogg."
        )
    source, export = _music_paths(project, source, export)
    changed = midi.apply_change(song, midi.SongChange.model_validate(changes))
    return await _render_song(
        project,
        source,
        export,
        changed,
        options.get("loop", True) if loop is None else loop,
        options.get("lufs") if lufs is None else lufs,
        note,
    )


def inspect_music(project: Path, source: str) -> dict[str, Any]:
    source = _audio_path(project, source, "song source")
    song, options = _current_song(project, source)
    return {
        "source": source,
        "song": song.model_dump(),
        "render": options,
        "seconds": round(song.length_seconds, 3),
        "written_by_joinery": bool(options),
    }


async def render_music(
    project: Path,
    source: str,
    export: str,
    steps: list[dict[str, Any]] | None = None,
    output: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    validate_arguments({"steps": steps or [], "note": note})
    source, export = _music_paths(project, source, export)
    authoring.enabled(project, "surge-xt")
    if _owner(project, source) is not None:
        raise ValueError(f"{source} is a Joinery song; change it with edit_music.")
    path = project / source
    if not path.is_file():
        raise ValueError(f"{source} is missing.")
    rules = load_rules(project)
    rule = _check_format(rules.audio.format, export)
    parsed = audio_chain.parse_steps(steps or [{"op": "normalise"}])
    resolved = audio_chain.resolve_targets(
        parsed,
        export,
        rules.audio.lufs,
        rules.audio.true_peak_max_dbtp,
        default_lufs=midi.DEFAULT_MUSIC_LUFS,
    )
    out = _output(output, rule)
    preimage = store_preimage(project, path)
    script = midi.file_script(preimage, export, resolved, out)
    result = await _run(
        project,
        "surge-xt",
        script,
        [export],
        (preimage,),
        f"{source} rendered as it is through FluidSynth with {midi.SOUNDFONT_CREDIT}."
        + (f" {note}" if note else ""),
    )
    imported = _write_import(project, export, result["measured"])
    if imported:
        result["godot_import"] = imported
    return result


# --- MCP registration ----------------------------------------------------------------


def register(server: MCPServer[Any], project: Path) -> None:
    """Add the typed sound operations to the joinery-sound server."""
    # Built on the helpers here: adaptive tracks and the sound kit.
    from servers.sound import make, tracks

    tracks.register(server, project)
    make.register(server, project)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def inspect_music(source: str) -> dict[str, Any]:
        """Read a song (.mid under audio/) as a typed score: tempo, metre, bars and each track's instrument and notes (beats are quarter notes). Read it before edit_music."""
        return _inspect(project, source)

    @server.tool()
    @tool_errors
    async def compose_music(
        source: str,
        song: dict[str, Any],
        export: str,
        loop: bool = True,
        lufs: float | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Write a new song as a MIDI source (audio/music/<name>.mid) and render it offline to an OGG (or WAV) export. song = {"tempo_bpm", "beats_per_bar", "beat_unit", "bars", "tail_beats", "tracks": [{"name", "program": General MIDI 0-127 | "drums": true | "surge": "audio/sources/<patch>.json", "volume", "pan", "notes": [{"pitch", "beat", "beats", "velocity"}]}]}; beat positions and lengths are quarter notes from the start. General MIDI tracks play through FluidSynth (FluidR3_GM); Surge tracks through that Surge XT patch. A looping song is cut at exactly `bars` with its release tail folded into the start, and its Ogg import loops. Loudness follows the art bible (music -16 LUFS when it sets none). Requires approved art direction and user review."""
        return await _compose(project, source, song, export, loop, lufs, note)

    @server.tool()
    @tool_errors
    async def edit_music(
        source: str,
        changes: dict[str, Any],
        export: str | None = None,
        loop: bool | None = None,
        lufs: float | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Change an existing song and render it again, keeping everything not mentioned. changes = {"tempo_bpm", "transpose" (semitones, drums excluded), "velocity_scale", "tail_beats", "tracks": {"<track name>": {"program" | "surge", "volume", "pan", "transpose", "remove"}}}. To change notes, inspect_music and send the whole song to compose_music. Audio edits on the export are re-applied."""
        return await _edit(project, source, changes, export, loop, lufs, note)

    @server.tool()
    @tool_errors
    async def render_music(
        source: str,
        export: str,
        steps: list[dict[str, Any]] | None = None,
        output: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Render an imported MIDI file exactly as it is (every event, including pedal and bends) through FluidSynth's General MIDI SoundFont; the .mid is not changed. Optional audio steps as in process_audio (default: normalise to the art bible)."""
        return await _render_file(project, source, export, steps, output, note)

    @server.tool()
    @tool_errors
    async def process_audio(
        source: str,
        steps: list[dict[str, Any]],
        export: str | None = None,
        output: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Edit rendered audio (WAV or OGG under audio/ that Surge XT or SuperCollider made) with typed steps, in order: {"op": "trim", "start", "end"}, {"op": "trim_silence", "threshold_db", "keep_seconds"}, {"op": "fade", "in_seconds", "out_seconds", "curve"}, {"op": "gain", "db"}, {"op": "normalise", "lufs", "true_peak"} (defaults from the art bible), {"op": "loop", "start", "end", "crossfade", "fold_tail"} (only gain and normalise may follow it). export defaults to editing in place; a different suffix converts the format (output: {"sample_rate", "channels", "quality", "bit_depth"}). Loop points go into the file (WAV smpl chunk, Ogg LOOPSTART/LOOPLENGTH) and Godot's Ogg import settings. The render is replayed and edited, so the edit survives a rebuild."""
        return await _process(project, source, steps, export, output, note)

    @server.tool()
    @tool_errors
    async def set_surge_parameters(
        source: str,
        parameters: dict[str, Any],
        notes: list[dict[str, Any]] | None = None,
        seconds: float | None = None,
        exports: list[str] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Change some parameters of an existing Surge XT source (in Surge's display units) and render it again; every other parameter, the notes and the exports stay as they are (null resets a parameter to the plugin default). Audio edits on its exports are re-applied. Prefer this to operate_document for partial patch edits."""
        return await _surge(project, source, parameters, notes, seconds, exports, note)


_inspect = inspect_music
_compose = compose_music
_edit = edit_music
_render_file = render_music
_process = process_audio
_surge = set_surge_parameters
