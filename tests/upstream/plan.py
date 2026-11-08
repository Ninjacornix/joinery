"""Explicit catalog coverage: additions require an intentional test plan."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Program:
    servers: tuple[str, ...]
    workflows: tuple[str, ...]
    native_requirement: str = ""


PROGRAMS = {
    "godot": Program(
        ("godot", "joinery-registry", "joinery-verify"),
        (
            "scene CRUD",
            "resources/scripts/signals",
            "review/undo",
            "saved scene import",
        ),
    ),
    "blender": Program(
        ("blender", "joinery-blender-ops"),
        ("named parts", "transforms/materials", "source/GLB replay", "review/revert"),
    ),
    "krita": Program(
        ("joinery-paint",),
        ("layers", "painted pixels", "native source/PNG", "review/replay"),
    ),
    "material-maker": Program(
        ("joinery-materialmaker",),
        ("graph identity", "textures/roughness", "overwrite exports", "review/replay"),
    ),
    "supercollider": Program(
        ("claude-collider", "joinery-sound"),
        ("native patches", "audible NRT", "source/audio replay", "status/discovery"),
    ),
    "inkscape": Program(
        ("inkscape", "joinery-paint"),
        (
            "SVG element identity",
            "PNG pixels",
            "native document editing",
            "review/replay",
        ),
    ),
    "aseprite": Program(
        ("aseprite", "joinery-paint"),
        ("canvas/layers", "frames/tags", "drawing", "exports/errors"),
        "User-provided licensed Aseprite executable",
    ),
    "gimp": Program(
        ("gimp",),
        (
            "canvas/layers",
            "selected image editing",
            "XCF/raster export",
            "native plugin failures",
        ),
        "GIMP plus its pinned plugin",
    ),
    "audacity": Program(
        ("audacity",),
        ("tracks/selections", "cut/paste/trim", "labels/effects", "pipe errors"),
        "Audacity with mod-script-pipe",
    ),
    "reaper": Program(
        ("reaper",),
        ("tracks/mixing", "MIDI/FX", "rendering", "project changes/errors"),
        "User-provided licensed REAPER with reapy",
    ),
    "musescore": Program(
        ("musescore",),
        (
            "notes/chords/rests",
            "measures/parts",
            "tempo/score metadata",
            "score-file round trips",
        ),
        "MuseScore with pinned WebSocket plugin",
    ),
    "surge-xt": Program(
        ("joinery-sound",),
        ("named parameters", "notes", "exact WAV replay", "review/revert"),
    ),
}

PROFILES = {
    "gimp": "sdk1",
    "aseprite": "sdk1",
    "audacity": "sdk1",
    "reaper": "sdk2",
    "musescore": "sdk2",
}

# These are application interfaces, not authoring installers. The test runner
# leaves the production server cache and provider environment alone.
CORE_SERVERS = {
    "godot",
    "blender",
    "claude-collider",
    "inkscape",
    "joinery-registry",
    "joinery-verify",
    "joinery-blender-ops",
    "joinery-paint",
    "joinery-sound",
    "joinery-materialmaker",
}
