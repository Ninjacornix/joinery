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
    "gimp": Program(
        ("joinery-paint",),
        ("worker presence", "python-fu batch", "XCF layers", "PNG export"),
    ),
    "surge-xt": Program(
        ("joinery-sound",),
        ("named parameters", "notes", "exact WAV replay", "review/revert"),
    ),
}

# Community MCPs whose pinned upstream tests run in an isolated SDK profile.
# None today: Aseprite, REAPER, MuseScore and Audacity were removed from the
# catalog, and GIMP is driven by Joinery's own paint server. A new community
# MCP adds its id here with a profile under tests/upstream/.
PROFILES: dict[str, str] = {}
#: Profile used when a lane runs a tool not listed above.
DEFAULT_PROFILE = "sdk1"

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
