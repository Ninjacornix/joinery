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
    "audacity": Program(
        ("audacity", "joinery-sound"),
        (
            "typed steps from the user's recording",
            "noise profile and spectral edits",
            "exact WAV/label/.aup3 replay",
            "transcription refused",
        ),
        "joinery-audacity-worker",
    ),
}

# Community MCPs whose pinned upstream tests run in an isolated SDK profile.
# None today: Aseprite, REAPER and MuseScore were removed from the catalog,
# GIMP is driven by Joinery's own paint server, and Audacity's pinned MCP is
# exercised natively in its worker (tests/integration/audacity_native.py). A
# new community MCP adds its id here with a profile under tests/upstream/.
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
    "audacity",
    "joinery-registry",
    "joinery-verify",
    "joinery-blender-ops",
    "joinery-paint",
    "joinery-sound",
    "joinery-materialmaker",
}
