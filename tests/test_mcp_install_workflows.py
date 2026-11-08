"""Pinned MCP installation workflows without network or real package execution."""

import json
from pathlib import Path

import pytest

from joinery import server_install
from joinery.catalog import load_catalog

UPSTREAM = ("godot", "blender", "inkscape", "supercollider")


@pytest.fixture
def install_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JOINERY_SERVER_CACHE", str(tmp_path / "isolated server cache"))
    monkeypatch.setattr(
        server_install.shutil,
        "which",
        lambda name: "/runtime/node" if name == "node" else None,
    )
    return tmp_path


def fake_commands(
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    *,
    revision: str | None = None,
    denied: bool = False,
    missing: bool = False,
):
    preferred = load_catalog().get(tool).mcp
    calls = []

    def execute(args, cwd, timeout=600):
        calls.append((list(args), cwd))
        if args[:2] == ["git", "clone"]:
            source = Path(args[-1])
            source.mkdir()
            if tool == "blender":
                path = source / "mcp/blmcp/__main__.py"
                path.parent.mkdir(parents=True)
                path.write_text(
                    "# GPL-3.0-or-later\n" if not missing else "# unknown licence\n"
                )
            elif tool == "inkscape":
                (source / "uv.lock").write_text("version=1\n")
                if not missing:
                    path = source / "src/inkscape_mcp/main.py"
                    path.parent.mkdir(parents=True)
                    path.write_text("# pinned source\n")
            elif not missing:
                packages = {"node_modules/diffusers": {}} if denied else {}
                (source / "package-lock.json").write_text(
                    json.dumps({"packages": packages})
                )
        if args == ["git", "rev-parse", "HEAD"]:
            return revision or preferred.ref
        if args == ["npm", "run", "build"]:
            entry = cwd / (
                "dist/index.js" if tool == "supercollider" else "build/index.js"
            )
            entry.parent.mkdir()
            entry.write_text("// built pinned server\n")
        return ""

    monkeypatch.setattr(server_install, "_run", execute)
    return calls


@pytest.mark.parametrize("tool", UPSTREAM)
def test_install_uses_exact_pin_and_reuses_verified_installation(
    install_environment: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    calls = fake_commands(monkeypatch, tool)
    preferred = load_catalog().get(tool).mcp
    spec = server_install.install(tool)
    assert spec.revision == preferred.ref and spec.source == preferred.repo
    assert spec.name == preferred.server
    assert calls[0][0][:-1] == [
        "git",
        "clone",
        "--filter=blob:none",
        "--no-checkout",
        preferred.repo,
    ]
    assert calls[1][0] == ["git", "checkout", "--detach", preferred.ref]
    if tool in {"godot", "supercollider"}:
        commands = [args for args, _ in calls]
        assert ["npm", "ci", "--ignore-scripts"] in commands
        assert commands.index(["npm", "ci", "--ignore-scripts"]) < commands.index(
            ["npm", "run", "build"]
        )
        assert spec.args[0].startswith(
            str(server_install.installation(tool) / "source")
        )
    count = len(calls)
    assert server_install.install(tool) == spec
    assert len(calls) == count  # no download/build merely to reconnect
    assert not list(server_install.installation(tool).parent.glob(".install-*"))


@pytest.mark.parametrize("tool", UPSTREAM)
def test_wrong_checkout_revision_is_rejected_and_partial_install_removed(
    install_environment: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    calls = fake_commands(monkeypatch, tool, revision="f" * 40)
    with pytest.raises(ValueError, match="pinned revision"):
        server_install.install(tool)
    assert not server_install.installation(tool).exists()
    assert not list(server_install.installation(tool).parent.glob(".install-*"))
    assert not any(args[0] == "npm" for args, _ in calls)


@pytest.mark.parametrize("tool", UPSTREAM)
def test_incomplete_source_or_missing_lock_fails_before_packages_install(
    install_environment: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    calls = fake_commands(monkeypatch, tool, missing=True)
    with pytest.raises(ValueError, match="licens|incomplete|lockfile"):
        server_install.install(tool)
    assert not server_install.installation(tool).exists()
    assert not any(args[0] == "npm" for args, _ in calls)


@pytest.mark.parametrize("tool", ["godot", "supercollider"])
def test_denied_dependency_in_upstream_lock_stops_install_before_npm(
    install_environment: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    calls = fake_commands(monkeypatch, tool, denied=True)
    with pytest.raises(ValueError, match="denied package|dependency gate"):
        server_install.install(tool)
    assert not any(args[0] == "npm" for args, _ in calls)
    assert not server_install.installation(tool).exists()


@pytest.mark.parametrize("tool", ["godot", "supercollider"])
def test_missing_node_runtime_is_not_reported_as_an_installed_mcp(
    install_environment: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    fake_commands(monkeypatch, tool)
    monkeypatch.setattr(server_install.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="executable"):
        server_install.install(tool)
    assert not server_install.installation(tool).exists()


def test_failed_build_keeps_original_user_installation_and_cleans_scratch(
    install_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_commands(monkeypatch, "godot")
    run = server_install._run

    def fail(args, cwd, timeout=600):
        if args == ["npm", "run", "build"]:
            raise RuntimeError("upstream TypeScript failed")
        return run(args, cwd, timeout)

    monkeypatch.setattr(server_install, "_run", fail)
    sentinel = install_environment / "my working MCP checkout"
    sentinel.mkdir()
    (sentinel / "local edits.ts").write_text("uncommitted user code")
    with pytest.raises(RuntimeError, match="TypeScript failed"):
        server_install.install("godot")
    assert (sentinel / "local edits.ts").read_text() == "uncommitted user code"
    assert not server_install.installation("godot").exists()
    assert not list(server_install.installation("godot").parent.glob(".install-*"))
