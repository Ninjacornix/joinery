"""Document ownership, desktop apps and offline worker installation contracts."""

import json
from pathlib import Path

import pytest

from joinery import desktop, server_install, workers
from joinery.catalog import load_catalog
from joinery.mcp_runtime import ServerSpec
from joinery.workspace import index, open_asset

PROGRAMS = tuple(desktop.TOOLS)
UPSTREAM = ("godot", "blender", "supercollider", "inkscape")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.lock").write_text("version=1\n")
    (tmp_path / "art").mkdir()
    (tmp_path / "audio").mkdir()
    (tmp_path / "recipes").mkdir()
    monkeypatch.setattr(
        desktop.shutil, "which", lambda command: "/applications/" + command
    )
    return tmp_path


def select(project: Path, *tools: str) -> None:
    (project / "joinery.toml").write_text(
        "[tools]\nenabled=" + json.dumps(list(tools)) + "\n"
    )


@pytest.mark.parametrize("tool", PROGRAMS)
def test_open_native_document_in_each_program_without_executing_or_modifying_it(
    project: Path, tool: str
) -> None:
    select(project, "godot", tool)
    relative = "art/user document" + desktop.TOOLS[tool].native[0]
    native = project / relative
    native.write_bytes(b"user-owned source, never a recipe execution")
    before = {
        p.relative_to(project): p.read_bytes()
        for p in project.rglob("*")
        if p.is_file()
    }
    argv = open_asset(project, relative, tool, dry_run=True)
    assert argv[0] == "/applications/" + desktop.TOOLS[tool].command
    assert argv[-1] == str(native)
    if tool == "godot":
        assert argv[1:-1] == ["--editor", "--path", str(project)]
    if tool == "supercollider":
        assert argv[0].endswith("/scide") and "sclang" not in argv
    assert before == {
        p.relative_to(project): p.read_bytes()
        for p in project.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize("tool", PROGRAMS)
def test_missing_desktop_app_explains_configuration_and_does_not_change_files(
    project: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    select(project, "godot", tool)
    relative = "art/source" + desktop.TOOLS[tool].native[0]
    (project / relative).write_bytes(b"native source")
    monkeypatch.setattr(desktop.shutil, "which", lambda _: None)
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    with pytest.raises(ValueError, match="desktop." + tool):
        open_asset(project, relative, tool, dry_run=True)
    assert (project / relative).read_bytes() == b"native source"


@pytest.mark.parametrize(
    "tool,source,export",
    [
        ("blender", "art/sources/hammer.blend", "art/models/hammer.glb"),
        ("krita", "art/portrait.kra", "art/portrait.png"),
        ("gimp", "art/portrait.xcf", "art/portrait.png"),
        ("aseprite", "art/walk.aseprite", "art/walk.png"),
        ("inkscape", "art/ui.svg", "art/ui.png"),
        ("material-maker", "art/stone.ptex", "art/stone.tres"),
        ("supercollider", "audio/sound.scd", "audio/sound.wav"),
        ("audacity", "audio/dialogue.aup3", "audio/dialogue.wav"),
        ("reaper", "audio/score.rpp", "audio/score.wav"),
        ("musescore", "audio/theme.mscz", "audio/theme.mid"),
    ],
)
def test_edit_export_opens_its_original_native_source_not_an_unrelated_asset(
    project: Path, tool: str, source: str, export: str
) -> None:
    select(project, "godot", tool)
    for relative in (source, export):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    (project / "recipes/build.py").write_text("# recorded source operation\n")
    (project / "recipes/asset.toml").write_text(
        f'tool={json.dumps(tool)}\nscript="recipes/build.py"\noutputs={json.dumps([source, export])}\n'
    )
    assets = {asset["path"]: asset for asset in index(project)["assets"]}
    assert source in assets[export]["sources"]
    assert open_asset(project, export, tool, dry_run=True)[-1] == str(project / source)


@pytest.fixture
def installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "servers with spaces"
    monkeypatch.setenv("JOINERY_SERVER_CACHE", str(cache))
    for tool in UPSTREAM:
        root = server_install.installation(tool)
        source = root / "source"
        source.mkdir(parents=True)
        for entry in {
            "godot": ["build/index.js"],
            "blender": ["mcp/blmcp/__main__.py"],
            "supercollider": ["dist/index.js"],
            "inkscape": ["src/inkscape_mcp/main.py", "uv.lock"],
        }[tool]:
            path = source / entry
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# exact pinned source\n")
    return cache


@pytest.mark.parametrize("tool", UPSTREAM)
def test_each_upstream_worker_is_offline_scoped_and_mounts_exact_source(
    installed: Path, project: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    monkeypatch.setattr(workers, "docker_owner_args", lambda: ["--user", "1001:1001"])
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-secret-never-in-worker")
    factory = getattr(
        workers, {"supercollider": "supercollider_worker"}.get(tool, tool + "_worker")
    )
    spec = factory(project)
    args = spec.args
    assert spec.command == "docker"
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--user") + 1] == "1001:1001"
    assert args[args.index("--memory") + 1] == "4g"
    assert args[args.index("--pids-limit") + 1] == "512"
    assert args[args.index("--name") + 1] == spec.container_name
    mounts = [args[i + 1] for i, arg in enumerate(args) if arg == "-v"]
    assert any(
        m.startswith(str(server_install.installation(tool) / "source") + ":")
        and m.endswith(":ro")
        for m in mounts
    )
    assert str(project.resolve()) + ":/work" in mounts
    assert "unit-test-secret-never-in-worker" not in str(spec.model_dump())
    assert "--privileged" not in args and "/var/run/docker.sock" not in str(args)
    assert spec.revision == load_catalog().get(tool).mcp.ref


def test_inkscape_inspection_mount_cannot_modify_original_project(
    installed: Path, project: Path
) -> None:
    spec = workers.inkscape_worker(project, readonly=True)
    assert str(project.resolve()) + ":/work:ro" in spec.args


@pytest.mark.parametrize("tool", ("godot", "blender", "supercollider"))
def test_missing_built_entry_is_reported_before_launch(
    project: Path, installed: Path, tool: str
) -> None:
    entry = {
        "godot": "build/index.js",
        "blender": "mcp/blmcp/__main__.py",
        "supercollider": "dist/index.js",
    }[tool]
    (server_install.installation(tool) / "source" / entry).unlink()
    with pytest.raises(ValueError, match="Install the pinned"):
        getattr(workers, tool + "_worker")(project)


@pytest.mark.parametrize("tool", UPSTREAM)
def test_pinned_install_load_detects_source_tampering(
    installed: Path, tool: str
) -> None:
    root = server_install.installation(tool)
    source = root / "source"
    preferred = load_catalog().get(tool).mcp
    assert preferred is not None
    spec = ServerSpec(
        name=preferred.server,
        command="docker",
        source=preferred.repo,
        revision=preferred.ref,
    )
    metadata = {
        "spec": spec.model_dump(),
        "tree_sha256": server_install.tree_hash(source),
    }
    if tool in {"godot", "supercollider"}:
        import hashlib

        lock = source / "package-lock.json"
        lock.write_text('{"packages":{}}')
        metadata["lock_sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest()
        metadata["tree_sha256"] = server_install.tree_hash(source)
    (root / "server.json").write_text(json.dumps(metadata))
    assert server_install.load_installed(tool).revision == preferred.ref
    next(p for p in source.rglob("*") if p.is_file()).write_text(
        "unreviewed source edit"
    )
    with pytest.raises(ValueError, match="changed"):
        server_install.load_installed(tool)


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "different-server"),
        ("revision", "0" * 40),
        ("source", "https://example.invalid/repository"),
    ],
)
def test_pinned_install_identity_cannot_be_swapped(
    installed: Path, field: str, value: str
) -> None:
    root = server_install.installation("blender")
    preferred = load_catalog().get("blender").mcp
    spec = ServerSpec(
        name=preferred.server,
        command="docker",
        source=preferred.repo,
        revision=preferred.ref,
    )
    data = spec.model_dump()
    data[field] = value
    (root / "server.json").write_text(
        json.dumps(
            {"spec": data, "tree_sha256": server_install.tree_hash(root / "source")}
        )
    )
    with pytest.raises(ValueError, match="identity"):
        server_install.load_installed("blender")


@pytest.mark.parametrize(
    "tool,family",
    [
        ("blender", "joinery-blender-ops"),
        ("krita", "joinery-paint"),
        ("aseprite", "joinery-paint"),
        ("inkscape", "joinery-paint"),
        ("supercollider", "joinery-sound"),
        ("material-maker", "joinery-materialmaker"),
    ],
)
def test_project_setup_exposes_owning_first_party_mcp_only_when_selected(
    project: Path, monkeypatch: pytest.MonkeyPatch, tool: str, family: str
) -> None:
    select(project, tool)

    def not_installed(_tool: str):
        raise ValueError("not installed in unit fixture")

    monkeypatch.setattr(server_install, "load_installed", not_installed)
    specs, missing = server_install.configured(project)
    names = {spec.name for spec in specs}
    assert names == {"joinery-registry", "joinery-verify", family}
    expected_missing = {"godot"}
    if not load_catalog().get(tool).builtin_server:
        expected_missing.add(load_catalog().get(tool).mcp.server)
    assert {item.name for item in missing} == expected_missing
    assert all(item.state == "not-installed" for item in missing)
    select(project, "godot")
    unselected, _ = server_install.configured(project)
    assert {spec.name for spec in unselected} == {"joinery-registry", "joinery-verify"}


@pytest.mark.parametrize("tool", ["gimp", "audacity", "reaper", "musescore"])
def test_unimplemented_app_mcp_is_truthfully_missing_not_silently_connected(
    project: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    select(project, tool)
    monkeypatch.setenv("JOINERY_SERVER_CACHE", str(project / "empty server cache"))
    specs, missing = server_install.configured(project)
    assert {s.name for s in specs} == {"joinery-registry", "joinery-verify"}
    readiness = {item.name: item for item in missing}
    assert set(readiness) == {"godot", load_catalog().get(tool).mcp.server}
    assert all(not item.connected and not item.installed for item in missing)
    with pytest.raises(ValueError, match="not implemented yet"):
        server_install.install(tool)
