"""Offline MCP worker specifications; model credentials never enter workers."""

from __future__ import annotations

import uuid
from pathlib import Path

from joinery.mcp_runtime import ServerSpec
from joinery.server_install import installation
from joinery.worker_identity import docker_owner_args

WORKER_IMAGE = "joinery-mcp-worker"


def godot_worker(project: Path) -> ServerSpec:
    source = installation("godot") / "source"
    if not (source / "build/index.js").is_file():
        raise ValueError("Install the pinned Godot MCP first.")
    name = "joinery-mcp-" + uuid.uuid4().hex
    return ServerSpec(
        name="godot",
        revision=source.parent.name,
        command="docker",
        container_name=name,
        cwd=str(project),
        source="sandbox",
        worker_image=WORKER_IMAGE,
        args=[
            "run",
            "--rm",
            *docker_owner_args(),
            "-i",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--name",
            name,
            "--pids-limit",
            "512",
            "--memory",
            "4g",
            "-v",
            f"{source}:/opt/godot-mcp:ro",
            "-v",
            f"{project.resolve()}:/work",
            "-w",
            "/work",
            "-e",
            "GODOT_PATH=/usr/local/bin/godot",
            "-e",
            "GODOT_MCP_ALLOWED_DIRS=/work",
            WORKER_IMAGE,
            "node",
            "/opt/godot-mcp/build/index.js",
        ],
    )


def blender_worker(project: Path) -> ServerSpec:
    source = installation("blender") / "source"
    if not (source / "mcp/blmcp/__main__.py").is_file():
        raise ValueError("Install the pinned official Blender MCP first.")
    name = "joinery-mcp-" + uuid.uuid4().hex
    return ServerSpec(
        name="blender",
        revision=source.parent.name,
        command="docker",
        container_name=name,
        cwd=str(project),
        source="sandbox",
        worker_image="joinery-blender-worker",
        args=[
            "run",
            "--rm",
            *docker_owner_args(),
            "-i",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--name",
            name,
            "--pids-limit",
            "512",
            "--memory",
            "4g",
            "-v",
            f"{source}:/opt/blender-mcp:ro",
            "-v",
            f"{project.resolve()}:/work",
            "-w",
            "/work",
            "joinery-blender-worker",
        ],
    )


# Shell run inside the SuperCollider worker before the MCP server starts.
# - The extension loads through a library config (-l), not a mount into the
#   home: Docker would create those parent folders as root, and sclang then
#   cannot create its synthdefs folder and never starts.
# - Linux sclang echoes input through readline, which the MCP's parser reads as
#   the reply. Naming an IDE (-i) turns readline off; sclang then runs code on a
#   form feed, so the wrapper turns each newline into one. exec keeps the
#   wrapper's process ID, so stopping it stops sclang.
# - scsynth needs a running JACK server and the container has no sound card,
#   so JACK runs its dummy driver. Audio is captured as recordings, never played.
_SUPERCOLLIDER_START = (
    "printf 'includePaths:\\n  - /opt/collider/ClaudeCollider\\n' "
    "> /tmp/sclang_conf.yaml && "
    "printf '%s\\n' '#!/bin/bash' "
    '\'exec /usr/bin/sclang -i joinery -l /tmp/sclang_conf.yaml "$@" '
    '< <(stdbuf -o0 tr "\\n" "\\f")\' > /tmp/joinery-sclang && '
    "chmod +x /tmp/joinery-sclang && "
    "{ jackd --no-realtime -d dummy -r 48000 -p 1024 >/tmp/jackd.log 2>&1 & } && "
    "exec node /opt/collider/dist/index.js"
)


#: Live recordings stay inside the container and vanish with it. Live audio is
#: real time and cannot be replayed, so it is for auditioning only; final
#: assets are rendered offline by joinery-sound (NRT) with a recipe.
AUDITION_DIR = "/tmp/joinery-audition"
#: Folders of the pinned claude-collider checkout hidden from the worker: drum
#: samples and example songs of unknown provenance (Rule Zero).
COLLIDER_HIDDEN = ("samples", "tapes", "user/samples")


def supercollider_worker(project: Path) -> ServerSpec:
    source = installation("supercollider") / "source"
    if not (source / "dist/index.js").is_file():
        raise ValueError("Install the pinned SuperCollider MCP first.")
    # An empty read-only tmpfs over each folder hides it. Docker can only mount
    # over a folder that exists in the read-only source mount.
    hidden = [
        argument
        for folder in COLLIDER_HIDDEN
        if (source / folder).is_dir()
        for argument in (
            "--mount",
            f"type=tmpfs,destination=/opt/collider/{folder},tmpfs-mode=0555",
        )
    ]
    name = "joinery-mcp-" + uuid.uuid4().hex
    return ServerSpec(
        name="claude-collider",
        revision=source.parent.name,
        command="docker",
        container_name=name,
        cwd=str(project),
        source="sandbox",
        worker_image=WORKER_IMAGE,
        args=[
            "run",
            "--rm",
            *docker_owner_args(),
            "-i",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--name",
            name,
            "--pids-limit",
            "512",
            "--memory",
            "4g",
            # JACK keeps its audio buffers in shared memory; Docker's 64 MB
            # default makes jackd die with a bus error.
            "--shm-size",
            "512m",
            "-v",
            f"{source}:/opt/collider:ro",
            *hidden,
            "-v",
            f"{project.resolve()}:/work",
            "-w",
            "/work",
            "-e",
            "SCLANG_PATH=/tmp/joinery-sclang",
            "-e",
            "SC_BOOT_TIMEOUT=30000",
            "-e",
            "CC_SAMPLES_PATH=/work/audio",
            "-e",
            f"CC_RECORDINGS_PATH={AUDITION_DIR}",
            WORKER_IMAGE,
            "sh",
            "-c",
            _SUPERCOLLIDER_START,
        ],
    )


def inkscape_worker(project: Path, readonly: bool = False) -> ServerSpec:
    source = installation("inkscape") / "source"
    name = "joinery-mcp-" + uuid.uuid4().hex
    setup = "from pathlib import Path; import os; Path('/tmp/inkscape.yml').write_text('allowed_directories: [/work]\\ntemp_directory: /tmp/inkscape-mcp\\n'); os.execv('/opt/inkscape-python/bin/python', ['/opt/inkscape-python/bin/python', '-m', 'inkscape_mcp.main', '--mode', 'stdio', '--config', '/tmp/inkscape.yml'])"
    return ServerSpec(
        name="inkscape",
        revision=source.parent.name,
        command="docker",
        container_name=name,
        cwd=str(project),
        source="sandbox",
        worker_image="joinery-inkscape-worker",
        args=[
            "run",
            "--rm",
            *docker_owner_args(),
            "-i",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--name",
            name,
            "--pids-limit",
            "512",
            "--memory",
            "4g",
            "-v",
            f"{source}:/opt/inkscape-mcp:ro",
            "-v",
            f"{project.resolve()}:/work" + (":ro" if readonly else ""),
            "-w",
            "/work",
            "joinery-inkscape-worker",
            "/opt/inkscape-python/bin/python",
            "-c",
            setup,
        ],
    )
