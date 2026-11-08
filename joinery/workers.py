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


def supercollider_worker(project: Path) -> ServerSpec:
    source = installation("supercollider") / "source"
    if not (source / "dist/index.js").is_file():
        raise ValueError("Install the pinned SuperCollider MCP first.")
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
            "-v",
            f"{source}:/opt/collider:ro",
            "-v",
            f"{source}/ClaudeCollider:/home/joiner/.local/share/SuperCollider/Extensions/ClaudeCollider:ro",
            "-v",
            f"{project.resolve()}:/work",
            "-w",
            "/work",
            "-e",
            "SCLANG_PATH=/usr/bin/sclang",
            "-e",
            "CC_SAMPLES_PATH=/work/audio",
            "-e",
            "CC_RECORDINGS_PATH=/work/audio/recordings",
            WORKER_IMAGE,
            "node",
            "/opt/collider/dist/index.js",
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
