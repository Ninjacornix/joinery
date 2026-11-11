"""Turn a sound pass into small reviewed changes, one parallel task each.

Findings are grouped by the file they change: the player scene (its Footsteps),
the level (floor surfaces and the ambience), each prop scene (a SoundCue), and
project.godot (the UI sounds). No file is ever in two tasks, so tasks never
clash on claims; props of one kind share a task, a few scenes at a time. Each
task makes its own sounds (one `joinery-sound make_sound` call each, named
after the scene that plays them, so no two tasks make the same file) and then
writes each of its files once through Godot's `write_file` with the patched
text. `bridge.crew` runs every task as a normal staged change: checks,
reviewer, the glanceable review with the facts written here. Nothing here
writes a file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from joinery import scene_text, sound_kit
from joinery.bible_rules import load_rules
from joinery.scene_text import Scene, quote, string_name
from joinery.sound_pass import (
    AMBIENCE_ZONE,
    CATEGORIES,
    FOOTSTEPS,
    SOUND_CUE,
    Audit,
    Finding,
    Placement,
    PlannedSound,
    rel,
)
from joinery.tools.audio_chain import folder_value

#: Prop scenes per task: small enough to review at a glance.
MAX_FILES = 3
#: Godot's resource type of each export.
STREAM_TYPES = {".wav": "AudioStreamWAV", ".ogg": "AudioStreamOggVorbis"}
APP_NAMES = {"supercollider": "SuperCollider", "surge-xt": "Surge XT"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Step(_Model):
    server: str
    tool: str
    arguments: dict[str, Any]
    label: str
    apps: list[str]


class SoundTask(_Model):
    key: str
    title: str
    #: The category the card's row follows, and every category it covers.
    category: str
    categories: list[str]
    request: str
    targets: list[str]
    apps: list[str]
    steps: list[Step]
    findings: list[str]
    sounds: list[str]
    #: A few plain words for the glanceable review ("Footsteps on 3 surfaces").
    facts: list[str]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _outputs(sound: PlannedSound) -> list[str]:
    spec = sound_kit.SoundSpec(
        kind=sound.kind,  # type: ignore[arg-type]
        name=sound.name,
        surface=sound.surface,  # type: ignore[arg-type]
        variant=sound.variant,
    )
    paths = [spec.source_path(), sound.export, spec.render_path(sound.export)]
    if sound.export.endswith(".ogg"):
        paths.append(sound.export + ".import")
    return list(dict.fromkeys(paths))


def _stream(scene: Scene, sound: PlannedSound) -> str:
    kind = STREAM_TYPES[Path(sound.export).suffix]
    identifier = scene.add_ext_resource(
        kind, "res://" + sound.export, "snd_" + sound.name
    )
    return f'ExtResource("{identifier}")'


def _script(scene: Scene, path: str) -> str:
    identifier = scene.add_ext_resource("Script", path, "snd_" + Path(path).stem)
    return f'ExtResource("{identifier}")'


def patch_scene(
    text: str, placements: list[tuple[Placement, list[PlannedSound]]]
) -> str:
    """The scene text with every placement of one file applied."""
    scene = Scene(text)
    steps = [p for p in placements if p[0].what == "footsteps"]
    if steps:
        _footsteps(scene, steps)
    for placement, sounds in placements:
        if placement.what == "surfaces":
            for path, surface in sorted(placement.detail["floors"].items()):
                scene.set_props(path, {"metadata/surface": quote(surface)})
        elif placement.what == "sound_cue":
            sound = sounds[0]
            label = sound.kind.replace("_", " ").title().replace(" ", "")
            props = {
                "script": _script(scene, SOUND_CUE),
                "stream": _stream(scene, sound),
                "trigger": string_name(placement.detail["trigger"]),
                "only_group": string_name(placement.detail.get("only_group", "")),
            }
            scene.add_node(f"{label}Sound", "Node", placement.node, props)
        elif placement.what == "ambience_zone":
            _ambience_zone(scene, placement, sounds[0])
        elif placement.what == "ambience_player":
            scene.add_node(
                "Ambience",
                "AudioStreamPlayer",
                placement.node,
                {
                    "stream": _stream(scene, sounds[0]),
                    "autoplay": "true",
                    "bus": string_name("Ambience"),
                },
            )
    return scene.text()


def _footsteps(
    scene: Scene, placements: list[tuple[Placement, list[PlannedSound]]]
) -> None:
    """One Footsteps node under the player body: its surfaces, jump and land."""
    first = placements[0][0]
    props: dict[str, str] = {}
    existing = any(p.detail.get("existing") for p, _ in placements)
    surfaces: dict[str, str] = {}
    if existing:
        node = scene.node(first.node)
        if node is not None:
            surfaces = scene_text.dictionary_keys(node.props().get("surfaces", ""))
    for placement, sounds in placements:
        for sound in sounds:
            if sound.kind == "footstep" and sound.surface:
                surfaces[sound.surface] = _stream(scene, sound)
            elif sound.kind in {"jump", "land"}:
                props[sound.kind] = _stream(scene, sound)
    ordered = {s: surfaces[s] for s in sound_kit.SURFACES if s in surfaces}
    ordered.update({s: v for s, v in surfaces.items() if s not in ordered})
    if any(sound.kind == "footstep" for _, sounds in placements for sound in sounds):
        props = {"surfaces": scene_text.dictionary(ordered), **props}
    if existing:
        scene.set_props(first.node, props)
    else:
        scene.add_node(
            "Footsteps",
            "Node",
            first.node,
            {"script": _script(scene, FOOTSTEPS), **props},
        )


def _ambience_zone(scene: Scene, placement: Placement, sound: PlannedSound) -> None:
    box = placement.detail["bounds"]
    cx, cy, cz = box["centre"]
    shape = scene.add_sub_resource(
        "BoxShape3D",
        "BoxShape3D_ambience",
        {
            "size": "Vector3("
            + ", ".join(scene_text.number(v) for v in box["size"])
            + ")"
        },
    )
    path = scene.add_node(
        "Ambience",
        "Area3D",
        placement.node,
        {
            "transform": "Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, "
            + ", ".join(scene_text.number(v) for v in (cx, cy, cz))
            + ")",
            "collision_layer": "0",
            "collision_mask": str(placement.detail.get("mask", "2")),
            "monitorable": "false",
            "script": _script(scene, AMBIENCE_ZONE),
            "stream": _stream(scene, sound),
        },
    )
    scene.add_node(
        "Shape", "CollisionShape3D", path, {"shape": f'SubResource("{shape}")'}
    )


def patch_settings(text: str, placement: Placement, sounds: list[PlannedSound]) -> str:
    for sound in sounds:
        key = sound.kind.removeprefix("ui_")
        text = scene_text.set_setting(
            text, "audio", f"ui/{key}", quote("res://" + sound.export)
        )
    return text


def patched(project: Path, file: str, findings: list[Finding]) -> str:
    """One file's new text with every finding on it placed."""
    text = (project / file).read_text(encoding="utf-8")
    placements = [(f.placement, f.sounds) for f in findings if f.placement is not None]
    if file == "project.godot":
        for placement, sounds in placements:
            text = patch_settings(text, placement, sounds)
        return text
    return patch_scene(text, placements)


def _loudness(project: Path, sounds: list[PlannedSound]) -> str:
    rules = load_rules(project)
    targets = sorted(
        {
            folder_value(rules.audio.lufs, s.export) or sound_kit.DEFAULT_LUFS[s.kind]
            for s in sounds
        }
    )
    if not targets:
        return ""
    words = (
        f"{targets[0]:g}" if len(targets) == 1 else f"{targets[0]:g} to {targets[-1]:g}"
    )
    return words.replace("-", "−") + " LUFS"


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _title_and_facts(
    findings: list[Finding], sounds: list[PlannedSound], files: list[str]
) -> tuple[str, list[str]]:
    categories = {f.category for f in findings}
    steps = [s for s in sounds if s.kind == "footstep"]
    facts: list[str] = []
    if categories <= {"footsteps", "jump_land"} and any(
        f.placement and f.placement.what == "footsteps" for f in findings
    ):
        parts = []
        if steps:
            parts.append("footsteps")
            facts.append(f"Footsteps on {_plural(len(steps), 'surface')}")
        if "jump_land" in categories:
            parts += ["jump", "land"]
            facts.append("Jump and land")
        floors = [f for f in findings if f.placement and f.placement.what == "surfaces"]
        if floors:
            count = sum(
                len(f.placement.detail["floors"]) for f in floors if f.placement
            )
            facts.append(f"{_plural(count, 'floor')} tagged")
        title = (
            ("Add " + _join(parts) + " to the player")
            if parts
            else "Tag the floors' surfaces"
        )
        return title, facts
    if "ambience" in categories or any(
        f.placement and f.placement.what == "surfaces" for f in findings
    ):
        name = Path(files[0]).name
        parts = []
        for finding in findings:
            if finding.category == "ambience" and finding.sounds:
                kind = finding.sounds[0].kind.replace("_", " ")
                parts.append(f"{kind} ambience")
                facts.append(f"{kind.capitalize()} loop, {sound_kit.LOOP_SECONDS:g} s")
        floors = [f for f in findings if f.placement and f.placement.what == "surfaces"]
        if floors:
            count = sum(
                len(f.placement.detail["floors"]) for f in floors if f.placement
            )
            parts.append("floor surfaces")
            facts.append(f"{_plural(count, 'floor')} tagged")
        return "Add " + _join(parts) + f" to {name}", facts
    if categories == {"ui"}:
        return "Add UI click and hover sounds", ["Every button clicks"]
    kinds = list(dict.fromkeys(s.kind for s in sounds))
    names = [Path(f).stem.replace("_", " ") for f in files]
    if len(files) == 1:
        noun = "sound" if len(kinds) == 1 else "sounds"
        title = f"Add {_join(kinds)} {noun} to {names[0]}"
    else:
        title = f"Add {_join(kinds)} sounds to {_plural(len(files), 'prop')}"
    for finding in findings:
        if finding.placement is not None and finding.placement.what == "sound_cue":
            who = Path(rel(finding.scene)).stem.replace("_", " ").capitalize()
            when = finding.placement.detail["trigger"].replace("_", " ")
            facts.append(f"{who} on {when}"[:40])
    return title, facts


def plan(
    project: Path, audit: Audit, *, categories: list[str] | None = None
) -> list[SoundTask]:
    """The tasks that give the game the sounds ``audit`` found missing."""
    wanted = set(categories or CATEGORIES)
    placeable = [
        f for f in audit.findings if f.placement is not None and f.category in wanted
    ]
    by_file: dict[str, list[Finding]] = {}
    for finding in placeable:
        assert finding.placement is not None
        by_file.setdefault(finding.placement.file, []).append(finding)
    order = list(CATEGORIES)
    level = rel(audit.level)
    player = rel(audit.player)
    groups: dict[str, list[str]] = {}
    for file, items in by_file.items():
        if file in {player, level, "project.godot"}:
            groups.setdefault(file, []).append(file)
            continue
        category = min((f.category for f in items), key=order.index)
        groups.setdefault(f"props:{category}", []).append(file)
    tasks: list[SoundTask] = []
    for key, files in sorted(
        groups.items(), key=lambda item: _group_order(item[0], player, level)
    ):
        files = sorted(files)
        for start in range(0, len(files), MAX_FILES):
            chunk = files[start : start + MAX_FILES]
            found = [f for file in chunk for f in by_file[file]]
            sounds = list({s.name: s for f in found for s in f.sounds}.values())
            steps = [
                Step(
                    server="joinery-sound",
                    tool="make_sound",
                    arguments={
                        k: v
                        for k, v in {
                            "kind": s.kind,
                            "name": s.name,
                            "surface": s.surface,
                            "variant": s.variant,
                            "export": s.export,
                        }.items()
                        if v is not None
                    },
                    label=f"Make {s.words[:1].lower() + s.words[1:]} in {APP_NAMES[s.tool]}",
                    apps=[s.tool],
                )
                for s in sounds
            ]
            for file in chunk:
                steps.append(
                    Step(
                        server="godot",
                        tool="write_file",
                        arguments={
                            "projectPath": str(project),
                            "filePath": file,
                            "content": patched(project, file, by_file[file]),
                        },
                        label=f"Place the sounds in {Path(file).name}",
                        apps=["godot"],
                    )
                )
            title, facts = _title_and_facts(found, sounds, chunk)
            loud = _loudness(project, sounds)
            if loud:
                facts.append(loud)
            covered = sorted({f.category for f in found}, key=order.index)
            lines = [
                f"- {rel(f.scene)}{' ' + f.node if f.node and f.node != '.' else ''}: {f.missing}"
                + (f"; make {', '.join(s.name for s in f.sounds)}" if f.sounds else "")
                for f in found
            ]
            request = (
                f"{title}. Joinery's sound pass found:\n"
                + "\n".join(lines)
                + "\nEach sound is made from Joinery's sound kit in SuperCollider or "
                "Surge XT with a replayable recipe, and placed with the game core's "
                "Footsteps, SoundCue or AmbienceZone nodes."
            )
            targets = list(chunk)
            for sound in sounds:
                targets += _outputs(sound)
            tasks.append(
                SoundTask(
                    key=f"{key.replace('/', '_')}-{start // MAX_FILES}",
                    title=title,
                    category=covered[0],
                    categories=[str(c) for c in covered],
                    request=request,
                    targets=sorted(set(targets)),
                    apps=sorted({app for step in steps for app in step.apps}),
                    steps=steps,
                    findings=[f.id for f in found],
                    sounds=[s.name for s in sounds],
                    facts=list(dict.fromkeys(facts))[:3],
                )
            )
    return tasks


def _group_order(key: str, player: str, level: str) -> tuple[int, str]:
    if key == player:
        return 0, key
    if key == level:
        return 1, key
    if key == "project.godot":
        return 9, key
    category = key.removeprefix("props:")
    return 2 + list(CATEGORIES).index(category) if category in CATEGORIES else 8, key
