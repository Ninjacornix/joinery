"""Install exact catalogued MCP source revisions, without changing user clones."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from joinery import tools
from joinery.catalog import ROOT, load_catalog, load_project_tools, selection_ids
from joinery.deps import check_deps
from joinery.mcp_runtime import MCPConnection, Readiness, ServerSpec
from joinery.platform_support import npm_argv, private_file
from joinery.policy import command_violation, load_denylist
from joinery.tools.base import WorkerExec


class ReinstallRequired(ValueError):
    """An installed pin cannot be trusted as it is; `install` replaces it."""


class _CannotStart(ValueError):
    """A selected server is installed but something it needs on the host is not."""


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
        try:
            return load_installed(tool_id)
        except ValueError:
            # Changed, swapped or recorded without a source hash: install the
            # pin again. The old copy is replaced only once the new one is built.
            pass
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
            _place(temporary, destination)
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
            _place(temporary, destination)
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
        _place(temporary, destination)
        return spec
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _place(temporary: Path, destination: Path) -> None:
    """Move a finished install into place, replacing an untrusted older copy."""
    if destination.exists():
        stale = destination.with_name(f".stale-{uuid.uuid4().hex}")
        destination.rename(stale)
        try:
            temporary.rename(destination)
        except OSError:
            stale.rename(destination)
            raise
        shutil.rmtree(stale, ignore_errors=True)
        return
    temporary.rename(destination)


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
        raise ReinstallRequired(
            "Installed MCP source identity differs from the selected pin."
        )
    if not metadata.get("tree_sha256"):
        # Without a recorded hash the source cannot be checked, so it is not
        # trusted: an older Joinery wrote this install.
        raise ReinstallRequired(
            "Installed MCP has no recorded source hash, so it cannot be checked. "
            "Reinstall it with Joinery setup."
        )
    if tree_hash(path / "source") != metadata["tree_sha256"]:
        raise ReinstallRequired("Installed MCP source or build changed. Reinstall it.")
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

                try:
                    binary = engine_binary()
                except ValueError as exc:
                    raise _CannotStart(f"the Joinery editor was not found. {exc}") from exc
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
        except (OSError, ValueError, KeyError) as exc:
            detail = f"{tool.name} has been selected but its server is not installed."
            if isinstance(exc, (ReinstallRequired, _CannotStart)):
                detail = f"{tool.name} has been selected but {_lower(str(exc))}"
            missing.append(
                Readiness(
                    name=tool.mcp.server if tool.mcp else tool_id,
                    state="not-installed",
                    detail=detail,
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
    # Each selected tool starts the Joinery server that owns its documents:
    # its module's family, or the catalog's builtin_server for status-only tools.
    wanted = set(tools.families_for(selected)) | {
        catalog.get(t).builtin_server for t in selected if catalog.get(t).builtin_server
    }
    families = [
        (name, package) for name, package in tools.FAMILY_PACKAGES.items() if name in wanted
    ]
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


def _lower(text: str) -> str:
    return text[:1].lower() + text[1:]


#: Names people see for Joinery's own servers.
SERVER_LABELS = {
    "godot": "Godot tools",
    "joinery-registry": "Project registry",
    "joinery-verify": "Checks",
    "joinery-blender-ops": "Blender editing",
    "joinery-paint": "Paint and vector editing",
    "joinery-sound": "Sound editing",
    "joinery-materialmaker": "Material editing",
}
#: Servers with no external program; a connection is the whole check.
_FIRST_PARTY_STATUS = {"joinery-registry", "joinery-verify"}


def server_label(name: str) -> str:
    if name in SERVER_LABELS:
        return SERVER_LABELS[name]
    for tool in load_catalog().tool:
        if tool.mcp is not None and tool.mcp.server == name:
            return f"{tool.name} tools"
    return name


def describe(item: dict[str, Any], *, offline: bool = False) -> str:
    """One line for people: "Godot tools ready (157 operations, offline worker)"."""
    label = server_label(str(item.get("name", "")))
    state = item.get("state", "")
    detail = str(item.get("detail", "")).strip()
    if state == "discovered":
        if item.get("name") in _FIRST_PARTY_STATUS:
            return f"{label}: ready"
        count = len(item.get("tools", []))
        where = ", offline worker" if offline else ""
        return f"{label} ready ({count} operations{where})"
    if state == "runtime-missing":
        return f"{label}: worker missing. Run Joinery setup."
    if state == "not-installed":
        return f"{label}: not installed. Run Joinery setup." + (
            f" ({detail})" if detail and "not installed" not in detail else ""
        )
    if state == "error":
        return f"{label}: error: {detail}" if detail else f"{label}: error"
    return f"{label}: {state}"


def _write_worker(
    spec: ServerSpec, servers: dict[str, str]
) -> tuple[str, WorkerExec | None]:
    """The offline worker a host-run server's writes need, and its version check.

    Godot's read-only server runs on the host next to the editor, but every
    staged write and game run happens in its worker image, so the server is
    only usable when that image exists.
    """
    if spec.worker_image or spec.command == "docker":
        return "", None
    module = tools.REGISTRY.get(servers.get(spec.name, ""))
    if module is None or not module.worker_image:
        return "", None
    check = next(
        (
            c
            for c in module.checks
            if isinstance(c, WorkerExec)
            and not c.feature
            and c.image == module.worker_image
        ),
        None,
    )
    return module.worker_image, check


async def _image_present(image: str) -> bool:
    try:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "image",
            "inspect",
            image,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return False
    try:
        return await asyncio.wait_for(process.wait(), 15) == 0
    except TimeoutError:
        process.kill()
        await process.wait()
        return False


async def _worker_run(check: WorkerExec) -> tuple[int, str]:
    """Run a module's version check in its worker, offline."""
    command = ["docker", "run", "--rm", "--platform", "linux/amd64", "--network", "none"]
    if check.entrypoint:
        command += ["--entrypoint", check.entrypoint]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            check.image,
            *check.argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return 127, str(exc)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), check.timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return 124, f"timed out after {check.timeout} s"
    return process.returncode or 0, output.decode(errors="replace")


#: The read-only call that proves each upstream server can see its documents.
PROBES: dict[str, tuple[str, dict[str, Any]]] = {
    "godot": ("get_project_info", {"projectPath": "{project}"}),
    "blender": ("get_blendfile_summary_datablocks", {}),
    "inkscape": ("inkscape_system", {"operation": "diagnostics"}),
}


async def inspect_servers(project: Path, probe: bool = False) -> list[dict[str, Any]]:
    specs, missing = configured(project)
    result = [
        {**item.model_dump(), "summary": describe(item.model_dump())}
        for item in missing
    ]
    servers = {
        t.mcp.server: t.id for t in load_catalog().tool if t.mcp is not None
    }
    for spec in specs:
        image, version_check = _write_worker(spec, servers)
        offline = bool(image) or spec.command == "docker"
        if image and not await _image_present(image):
            item = Readiness(
                name=spec.name,
                state="runtime-missing",
                installed=True,
                revision=spec.revision,
                detail=f"Offline image {image} is unavailable or Docker is not "
                "running. Run Joinery setup.",
            ).model_dump()
            result.append({**item, "summary": describe(item)})
            continue
        connection = MCPConnection(spec, project)
        try:
            await connection.connect()
            if probe and spec.name in PROBES:
                tool, arguments = PROBES[spec.name]
                answer = await connection.probe(
                    tool,
                    {
                        key: str(project.resolve()) if value == "{project}" else value
                        for key, value in arguments.items()
                    },
                )
                if answer.is_error:
                    text = " ".join(
                        getattr(block, "text", "") for block in answer.content
                    ).strip()
                    raise RuntimeError(
                        f"{spec.name}.{tool} failed: {text[:300] or 'no detail'}"
                    )
            item = connection.readiness.model_dump()
            if probe and version_check is not None:
                code, output = await _worker_run(version_check)
                if code or not re.search(version_check.expect, output):
                    item["state"] = (
                        "runtime-missing"
                        if version_check.on_failure == "needs_image"
                        else "error"
                    )
                    item["detail"] = version_check.failure or output.strip()[:300]
            result.append({**item, "summary": describe(item, offline=offline)})
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            item = Readiness(name=spec.name, state="error", detail=str(exc)).model_dump()
            result.append({**item, "summary": describe(item)})
        finally:
            await connection.close()
    return result
