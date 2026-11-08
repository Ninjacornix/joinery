"""Install exact catalogued MCP source revisions, without changing user clones."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from joinery.catalog import ROOT, load_catalog, load_project_tools, selection_ids
from joinery.deps import check_deps
from joinery.mcp_runtime import MCPConnection, Readiness, ServerSpec
from joinery.platform_support import npm_argv, private_file
from joinery.policy import command_violation, load_denylist


def cache_root() -> Path:
    return Path(
        os.environ.get(
            "JOINERY_SERVER_CACHE", str(Path.home() / ".cache/joinery/servers")
        )
    )


def installation(tool_id: str) -> Path:
    tool = load_catalog().get(tool_id)
    if tool.mcp is None:
        raise ValueError(f"{tool.name} has no preferred server implementation.")
    return cache_root() / tool_id / tool.mcp.ref


def _run(args: list[str], cwd: Path, timeout: int = 600) -> str:
    result = subprocess.run(
        npm_argv(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, check=False
    )
    if result.returncode:
        # Subprocess output can contain user/private configuration. Keep it local.
        log = cache_root() / "install-failure.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(prefix=".install-log-", dir=log.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                private_file(Path(filename))
                stream.write(result.stdout + "\n" + result.stderr)
            os.replace(filename, log)
        finally:
            Path(filename).unlink(missing_ok=True)
        raise RuntimeError(f"Installation command {args[0]} failed; see {log}.")
    return result.stdout.strip()


def install(tool_id: str) -> ServerSpec:
    catalog = load_catalog()
    tool = catalog.get(tool_id)
    if tool.mcp is None:
        raise ValueError(f"{tool.name} has no preferred server implementation.")
    destination = installation(tool_id)
    if (destination / "server.json").is_file():
        return load_installed(tool_id)
    if tool_id not in {"godot", "blender", "supercollider", "inkscape"}:
        raise ValueError(
            f"{tool.name}: its app-specific pinned installer is not implemented yet."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=destination.parent))
    try:
        source = temporary / "source"
        _run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                tool.mcp.repo,
                str(source),
            ],
            temporary,
        )
        _run(["git", "checkout", "--detach", tool.mcp.ref], source)
        if _run(["git", "rev-parse", "HEAD"], source) != tool.mcp.ref:
            raise ValueError("The MCP checkout does not match the pinned revision.")
        if check_deps(source, load_denylist()):
            raise ValueError(
                "Pinned server dependencies fail the shared dependency gate."
            )
        if tool_id == "inkscape":
            lock = source / "uv.lock"
            if (
                not lock.is_file()
                or not (source / "src/inkscape_mcp/main.py").is_file()
            ):
                raise ValueError("Pinned Inkscape source/lock is incomplete.")
            spec = ServerSpec(
                name=tool.mcp.server,
                command="docker",
                source=tool.mcp.repo,
                revision=tool.mcp.ref,
            )
            (temporary / "server.json").write_text(
                json.dumps(
                    {
                        "spec": spec.model_dump(),
                        "tree_sha256": tree_hash(source),
                        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
                    },
                    indent=2,
                )
                + "\n"
            )
            temporary.rename(destination)
            return spec
        if tool_id == "blender":
            # The official source module is blmcp. Never resolve the unrelated,
            # denied PyPI package with the same project display name.
            if not (source / "mcp/blmcp/__main__.py").is_file():
                raise ValueError("Pinned official Blender source is incomplete.")
            headers = (source / "mcp/blmcp/__main__.py").read_text()
            if "GPL-3.0-or-later" not in headers:
                raise ValueError("Pinned Blender source license could not be verified.")
            spec = ServerSpec(
                name=tool.mcp.server,
                command="docker",
                source=tool.mcp.repo,
                revision=tool.mcp.ref,
            )
            (temporary / "server.json").write_text(
                json.dumps(
                    {"spec": spec.model_dump(), "tree_sha256": tree_hash(source)},
                    indent=2,
                )
                + "\n"
            )
            temporary.rename(destination)
            return spec
        lock = source / "package-lock.json"
        if not lock.is_file():
            raise ValueError("Pinned server has no dependency lockfile.")
        packages = json.loads(lock.read_text()).get("packages", {})
        for package in packages:
            verdict = command_violation(
                "npm install " + package.split("node_modules/")[-1], load_denylist()
            )
            if verdict is not None:
                raise ValueError(
                    "The pinned server dependency lock contains a denied package."
                )
        _run(["npm", "ci", "--ignore-scripts"], source)
        _run(["npm", "run", "build"], source)
        node = shutil.which("node")
        entry = "dist/index.js" if tool_id == "supercollider" else "build/index.js"
        if not node or not (source / entry).is_file():
            raise ValueError("MCP build did not produce its executable.")
        target_source = destination / "source"
        spec = ServerSpec(
            name=tool.mcp.server,
            command=node,
            args=[str(target_source / entry)],
            source=tool.mcp.repo,
            revision=tool.mcp.ref,
        )
        metadata = {
            "spec": spec.model_dump(),
            "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
            "tree_sha256": tree_hash(source),
        }
        (temporary / "server.json").write_text(json.dumps(metadata, indent=2) + "\n")
        temporary.rename(destination)
        return spec
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def load_installed(tool_id: str) -> ServerSpec:
    path = installation(tool_id)
    metadata = json.loads((path / "server.json").read_text())
    spec = ServerSpec.model_validate(metadata["spec"])
    tool = load_catalog().get(tool_id)
    assert tool.mcp is not None
    if (
        spec.revision != tool.mcp.ref
        or spec.source != tool.mcp.repo
        or spec.name != tool.mcp.server
    ):
        raise ValueError("Installed MCP source identity differs from the selected pin.")
    if (
        metadata.get("tree_sha256")
        and tree_hash(path / "source") != metadata["tree_sha256"]
    ):
        raise ValueError("Installed MCP source or build changed. Reinstall it.")
    if tool_id in {"blender", "inkscape"}:
        return spec
    lock = path / "source/package-lock.json"
    if (
        not lock.is_file()
        or hashlib.sha256(lock.read_bytes()).hexdigest() != metadata["lock_sha256"]
    ):
        raise ValueError("Installed MCP dependency lock has changed. Reinstall it.")
    if not all(Path(arg).is_file() for arg in spec.args if arg.endswith(".js")):
        raise ValueError("Installed MCP executable is missing. Reinstall it.")
    return spec


def tree_hash(source: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(
            part in {".git", "node_modules", "__pycache__"} for part in relative.parts
        ):
            continue
        if path.is_file():
            digest.update(relative.as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def configured(project: Path) -> tuple[list[ServerSpec], list[Readiness]]:
    catalog = load_catalog()
    chosen = load_project_tools(project / "joinery.toml")
    specs = []
    missing = []
    for tool_id in selection_ids(catalog, chosen):
        tool = catalog.get(tool_id)
        if tool.builtin_server:
            continue
        try:
            spec = load_installed(tool_id)
            if tool_id == "godot":
                from joinery.distribution import engine_binary

                binary = engine_binary()
                spec.env.update(
                    GODOT_PATH=str(binary),
                    GODOT_MCP_ALLOWED_DIRS=str(project.resolve()),
                )
            elif tool_id == "blender":
                from joinery.workers import blender_worker

                # Inspection gets a disposable empty workspace, never a writable
                # mount of the user's project. Mutations use transaction staging.
                workspace = cache_root() / "inspection/blender"
                workspace.mkdir(parents=True, exist_ok=True)
                spec = blender_worker(workspace)
            elif tool_id == "supercollider":
                from joinery.workers import supercollider_worker

                workspace = cache_root() / "inspection/supercollider"
                workspace.mkdir(parents=True, exist_ok=True)
                spec = supercollider_worker(workspace)
            elif tool_id == "inkscape":
                from joinery.workers import inkscape_worker

                spec = inkscape_worker(project, readonly=True)
            specs.append(spec)
        except (OSError, ValueError, KeyError):
            missing.append(
                Readiness(
                    name=tool.mcp.server if tool.mcp else tool_id,
                    state="not-installed",
                    detail=f"{tool.name} has been selected but its server is not installed.",
                )
            )
    for custom in chosen.custom:
        specs.append(
            ServerSpec(
                name=custom.server,
                command=custom.command,
                args=list(custom.args),
                cwd=str(project),
            )
        )
    specs.append(
        ServerSpec(
            name="joinery-registry",
            command=sys.executable,
            args=[
                "-m",
                "servers.joinery_registry",
                "--project",
                str(project.resolve()),
            ],
            cwd=str(ROOT),
            source="first-party",
        )
    )
    selected = set(selection_ids(catalog, chosen))
    families = []
    if "blender" in selected:
        families.append(("joinery-blender-ops", "blender_ops"))
    if selected & {"krita", "aseprite", "inkscape"}:
        families.append(("joinery-paint", "paint"))
    if "supercollider" in selected:
        families.append(("joinery-sound", "sound"))
    if "material-maker" in selected:
        families.append(("joinery-materialmaker", "materialmaker"))
    families.append(("joinery-verify", "joinery_verify"))
    for name, package in families:
        specs.append(
            ServerSpec(
                name=name,
                command=sys.executable,
                args=["-m", "servers." + package, "--project", str(project.resolve())],
                cwd=str(ROOT),
                source="first-party",
            )
        )
    return specs, missing


async def inspect_servers(project: Path, probe: bool = False) -> list[dict[str, Any]]:
    specs, missing = configured(project)
    result = [item.model_dump() for item in missing]
    for spec in specs:
        connection = MCPConnection(spec, project)
        try:
            await connection.connect()
            if probe and spec.name == "godot":
                await connection.probe(
                    "get_project_info", {"projectPath": str(project.resolve())}
                )
            elif probe and spec.name == "blender":
                await connection.probe("get_blendfile_summary_datablocks", {})
            elif probe and spec.name == "inkscape":
                await connection.probe("inkscape_system", {"operation": "diagnostics"})
            result.append(connection.readiness.model_dump())
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            result.append(
                Readiness(name=spec.name, state="error", detail=str(exc)).model_dump()
            )
        finally:
            await connection.close()
    return result
