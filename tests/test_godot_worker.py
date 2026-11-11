"""The pinned Godot MCP runs in the offline MCP worker, not on the host."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from joinery import server_install, workers
from joinery.catalog import ROOT, load_catalog
from joinery.mcp_runtime import (
    MCPConnection,
    ServerSpec,
    map_paths,
    rewrite_root,
    worker_root,
)

PIN = load_catalog().get("godot").mcp
assert PIN is not None
SCRIPT = b"extends Node\n# pinned in-game inspector\n"


def image_files(
    *,
    ref: str | None = None,
    denied: bool = False,
    lock_record: str | None = None,
) -> bytes:
    """What `tar` returns from the worker image's /opt/godot-mcp."""
    packages: dict[str, Any] = {"": {"name": "godot-mcp"}}
    if denied:
        packages["node_modules/diffusers"] = {"version": "1.0.0"}
    lock = json.dumps({"lockfileVersion": 3, "packages": packages}).encode()
    pin = {
        "repo": PIN.repo,
        "ref": ref or PIN.ref,
        "lock_sha256": lock_record or hashlib.sha256(lock).hexdigest(),
    }
    files = {
        "joinery-pin.json": json.dumps(pin).encode(),
        "package.json": b'{"name": "godot-mcp", "dependencies": {}}',
        "package-lock.json": lock,
        "build/index.js": b"// built pinned server\n",
        "build/scripts/mcp_interaction_server.gd": SCRIPT,
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def fake_worker(monkeypatch: pytest.MonkeyPatch, **options: Any) -> list[Any]:
    """Replace the one Docker call the Godot install makes."""
    calls: list[Any] = []

    def files(image: str, directory: str, names: tuple[str, ...]) -> bytes:
        calls.append((image, directory, names))
        return image_files(**options)

    monkeypatch.setattr(server_install, "_worker_files", files)
    return calls


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "server cache"
    monkeypatch.setenv("JOINERY_SERVER_CACHE", str(root))
    # No host Node, npm or git is needed any more.
    monkeypatch.setattr(server_install.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        server_install,
        "_run",
        lambda *a, **k: pytest.fail("no host command may run for Godot"),
    )
    return root


# --- Install: check the image's pin, record it ---------------------------------


def test_install_checks_the_worker_and_records_its_pin(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = fake_worker(monkeypatch)
    spec = server_install.install("godot")
    assert (spec.command, spec.worker_image) == ("docker", "joinery-mcp-worker")
    assert (spec.revision, spec.source, spec.name) == (PIN.ref, PIN.repo, "godot")
    assert calls[0][:2] == ("joinery-mcp-worker", "/opt/godot-mcp")
    root = server_install.installation("godot")
    assert not (root / "source").exists(), "no source copy on the host"
    assert workers.godot_runtime_script().read_bytes() == SCRIPT
    assert server_install.load_installed("godot") == spec
    assert server_install.install("godot") == spec
    assert len(calls) == 1  # reconnecting does not ask Docker again
    assert not list(root.parent.glob(".install-*"))


@pytest.mark.parametrize(
    "options,message",
    [
        ({"ref": "f" * 40}, "different Godot MCP"),
        ({"denied": True}, "dependency gate|denied package"),
        ({"lock_record": "0" * 64}, "lock differs"),
    ],
)
def test_install_refuses_a_worker_that_does_not_match_the_pin(
    cache: Path, monkeypatch: pytest.MonkeyPatch, options: dict[str, Any], message: str
) -> None:
    fake_worker(monkeypatch, **options)
    with pytest.raises(ValueError, match=message):
        server_install.install("godot")
    root = server_install.installation("godot")
    assert not root.exists()
    assert not list(root.parent.glob(".install-*"))


def test_install_without_docker_says_needs_docker(cache: Path) -> None:
    with pytest.raises(ValueError, match="^Needs Docker"):
        server_install.install("godot")


@pytest.mark.parametrize(
    "stderr,message",
    [
        (b"Error response from daemon: No such image: joinery-mcp-worker", "not built"),
        (b"Cannot connect to the Docker daemon", "^Needs Docker"),
        (b"tar: joinery-pin.json: Cannot stat", "no pinned Godot MCP"),
    ],
)
def test_worker_file_failures_say_what_to_do(
    monkeypatch: pytest.MonkeyPatch, stderr: bytes, message: str
) -> None:
    monkeypatch.setattr(server_install.shutil, "which", lambda _name: "/bin/docker")
    seen: list[list[str]] = []

    def run(argv: list[str], **_kwargs: Any) -> Any:
        seen.append(argv)
        return type("Done", (), {"returncode": 1, "stderr": stderr, "stdout": b""})

    monkeypatch.setattr(server_install.subprocess, "run", run)
    with pytest.raises(ValueError, match=message):
        server_install._worker_files("joinery-mcp-worker", "/opt/godot-mcp", ("x",))
    argv = seen[0]
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--pull") + 1] == "never", "never fetch a worker"


def test_a_host_install_from_an_older_joinery_must_move_to_the_worker(
    cache: Path,
) -> None:
    root = server_install.installation("godot")
    (root / "source/build").mkdir(parents=True)
    (root / "source/build/index.js").write_text("// built\n")
    host = ServerSpec(
        name="godot",
        command="/opt/homebrew/bin/node",
        args=[str(root / "source/build/index.js")],
        source=PIN.repo,
        revision=PIN.ref,
    )
    (root / "server.json").write_text(
        json.dumps({"spec": host.model_dump(), "tree_sha256": "a", "lock_sha256": "b"})
    )
    with pytest.raises(server_install.ReinstallRequired, match="offline worker"):
        server_install.load_installed("godot")


def test_a_changed_inspector_script_must_be_reinstalled(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_worker(monkeypatch)
    server_install.install("godot")
    workers.godot_runtime_script().write_text("extends Node # edited\n")
    with pytest.raises(server_install.ReinstallRequired, match="changed"):
        server_install.load_installed("godot")


# --- Launch: the image's server, the project at /work, no network ---------------


def test_the_server_runs_from_the_image_with_the_project_at_work(
    cache: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_worker(monkeypatch)
    server_install.install("godot")
    project = tmp_path / "my game"
    project.mkdir()
    spec = workers.godot_worker(project)
    args = spec.args
    assert spec.command == "docker" and spec.worker_image == "joinery-mcp-worker"
    assert args[args.index("--network") + 1] == "none"
    mounts = [args[i + 1] for i, arg in enumerate(args) if arg == "-v"]
    assert mounts == [f"{project.resolve()}:/work"], "no host source is mounted"
    assert args[-3:] == ["joinery-mcp-worker", "node", "/opt/godot-mcp/build/index.js"]
    assert "GODOT_PATH=/usr/local/bin/godot" in args
    assert "GODOT_MCP_ALLOWED_DIRS=/work" in args
    assert spec.revision == PIN.ref
    assert worker_root(spec) == project.resolve()
    readonly = workers.godot_worker(project, readonly=True)
    assert f"{project.resolve()}:/work:ro" in readonly.args
    assert worker_root(readonly) == project.resolve()


def test_configured_inspects_the_project_read_only_in_the_worker(
    cache: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_worker(monkeypatch)
    server_install.install("godot")
    project = tmp_path / "game"
    project.mkdir()
    (project / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    specs, missing = server_install.configured(project)
    godot = next(spec for spec in specs if spec.name == "godot")
    assert not [item for item in missing if item.name == "godot"]
    assert godot.command == "docker"
    assert f"{project.resolve()}:/work:ro" in godot.args


def test_configured_without_the_install_record_says_so(
    cache: Path, tmp_path: Path
) -> None:
    project = tmp_path / "game"
    project.mkdir()
    (project / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    _specs, missing = server_install.configured(project)
    godot = next(item for item in missing if item.name == "godot")
    assert godot.state == "not-installed"


def test_host_servers_have_no_worker_root() -> None:
    assert worker_root(ServerSpec(name="x", command="node", args=["-v", "a:/work"])) is None
    assert (
        worker_root(ServerSpec(name="x", command="docker", args=["-v", "/s:/opt/x:ro"]))
        is None
    )


# --- Paths: host paths in, worker paths out ------------------------------------


def test_paths_map_to_work_and_answers_map_back(tmp_path: Path) -> None:
    project = (tmp_path / "game").resolve()
    arguments = {
        "projectPath": str(project),
        "scenePath": "res://main.tscn",
        "files": [str(project / "art/a.png"), "/elsewhere/b.png"],
    }
    mapped = map_paths(arguments, project, PurePosixPath("/work"))
    assert mapped == {
        "projectPath": "/work",
        "scenePath": "res://main.tscn",
        "files": ["/work/art/a.png", "/elsewhere/b.png"],
    }
    answer = CallToolResult(
        content=[
            TextContent(
                type="text",
                text="Project /work: scene /work/scenes/main.tscn, "
                "unrelated /home/u/work/x and res://work/y and /workers.",
            )
        ],
        structured_content={"path": "/work/project.godot", "list": ["/work"]},
    )
    back = rewrite_root(answer, "/work", project)
    text = back.content[0].text  # type: ignore[union-attr]
    assert f"Project {project}:" in text
    assert str(project / "scenes/main.tscn") in text
    assert "/home/u/work/x" in text and "res://work/y" in text and "/workers." in text
    assert back.structured_content == {
        "path": str(project / "project.godot"),
        "list": [str(project)],
    }


def test_a_worker_connection_maps_paths_both_ways(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = (tmp_path / "game").resolve()
    project.mkdir()
    spec = ServerSpec(
        name="godot",
        command="docker",
        args=["run", "-v", f"{project}:/work:ro", "joinery-mcp-worker"],
    )
    connection = MCPConnection(spec, project)
    connection.tools["get_project_info"] = Tool(
        name="get_project_info",
        input_schema={"type": "object", "properties": {"projectPath": {"type": "string"}}},
    )
    monkeypatch.setattr("joinery.mcp_runtime.mcp_violation", lambda *a, **k: None)
    sent: list[dict[str, Any]] = []

    async def serve() -> None:
        request = await connection._queue.get()
        assert request is not None
        sent.append(request.arguments)
        request.future.set_result(
            CallToolResult(
                content=[TextContent(type="text", text="Godot project at /work")]
            )
        )

    async def main() -> CallToolResult:
        connection._task = asyncio.create_task(asyncio.sleep(3600))
        server = asyncio.create_task(serve())
        try:
            return await connection.call(
                "get_project_info", {"projectPath": str(project)}
            )
        finally:
            await server
            connection._task.cancel()

    result = asyncio.run(main())
    assert sent == [{"projectPath": "/work"}]
    assert result.content[0].text == f"Godot project at {project}"  # type: ignore[union-attr]


# --- One pin, written down in agreement ----------------------------------------


def test_the_image_the_versions_and_the_catalog_pin_the_same_commit() -> None:
    dockerfile = (ROOT / "sandbox/mcp.Dockerfile").read_text()
    versions = (ROOT / "sandbox/versions.env").read_text()
    checksums = (ROOT / "sandbox/checksums.txt").read_text()
    assert f"ARG GODOT_MCP_REF={PIN.ref}" in dockerfile
    assert f"GODOT_MCP_REF={PIN.ref}" in versions
    assert PIN.repo + "/archive/${GODOT_MCP_REF}.tar.gz" in dockerfile
    assert re.search(r"^godotmcp [0-9a-f]{64}$", checksums, re.MULTILINE)
    assert "joinery-verify-download /tmp/godot-mcp.tar.gz godotmcp" in dockerfile
    assert "npm ci --ignore-scripts" in dockerfile
    # The build tools stay in the build stage.
    final = dockerfile.rsplit("\nFROM ", 1)[1]
    assert "npm" not in final.replace("godot-mcp", "")
    assert "COPY --from=godot-mcp /opt/godot-mcp /opt/godot-mcp" in final
