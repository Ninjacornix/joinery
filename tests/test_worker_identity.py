"""Native Linux workers must write as their host owner with no host home mount."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from joinery.authoring import run_offline
from joinery.runner import Result, SandboxRunner
from joinery.worker_identity import docker_owner_args
from joinery.workers import (
    blender_worker,
    godot_worker,
    inkscape_worker,
    supercollider_worker,
)


@pytest.fixture
def linux_owner() -> object:
    with (
        patch("joinery.worker_identity.sys.platform", "linux"),
        patch("joinery.worker_identity.os.getuid", return_value=1001, create=True),
        patch("joinery.worker_identity.os.getgid", return_value=1002, create=True),
    ):
        yield


def assert_owner(arguments: list[str]) -> None:
    assert arguments[arguments.index("--user") + 1] == "1001:1002"
    assert "HOME=/tmp/joinery-home" in arguments
    assert "JOINERY_EPHEMERAL_HOME=1" in arguments
    assert "XDG_CONFIG_HOME=/tmp/joinery-home/.config" in arguments
    assert "XDG_DATA_HOME=/tmp/joinery-home/.local/share" in arguments
    assert "XDG_RUNTIME_DIR=/tmp/joinery-home/runtime" in arguments
    assert arguments[arguments.index("--network") + 1] == "none"


def test_linux_recipe_and_all_mcp_launches_use_same_unprivileged_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linux_owner: object
) -> None:
    captured: list[str] = []

    def timed(command: list[str], timeout: int) -> Result:
        captured.extend(command)
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.runner._timed", timed)
    SandboxRunner().run(["blender", "--version"], tmp_path)
    assert_owner(captured)
    source = tmp_path / "source"
    for name in ("build/index.js", "dist/index.js", "mcp/blmcp/__main__.py"):
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
    monkeypatch.setattr("joinery.workers.installation", lambda _tool: tmp_path)
    monkeypatch.setattr(
        "joinery.server_install.load_installed",
        lambda _tool: type("Spec", (), {"revision": "pinned"})(),
    )
    for factory in (
        godot_worker,
        blender_worker,
        supercollider_worker,
        inkscape_worker,
    ):
        assert_owner(factory(tmp_path).args)
    assert (
        f"{tmp_path.resolve()}:/work:ro"
        in inkscape_worker(tmp_path, readonly=True).args
    )


def test_linux_native_document_launch_and_readonly_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linux_owner: object
) -> None:
    commands: list[list[str]] = []

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

        async def wait(self) -> int:
            return 0

    async def launch(*arguments: str, **_kwargs: object) -> Process:
        commands.append(list(arguments))
        return Process()

    monkeypatch.setattr("joinery.authoring.asyncio.create_subprocess_exec", launch)
    asyncio.run(run_offline(["blender", "--version"], tmp_path, readonly=True))
    assert_owner(commands[0])
    assert f"{tmp_path.resolve()}:/work:ro" in commands[0]


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_docker_desktop_retains_image_user(platform: str) -> None:
    with patch("joinery.worker_identity.sys.platform", platform):
        assert docker_owner_args() == []


def test_linux_root_does_not_make_model_workers_root() -> None:
    with (
        patch("joinery.worker_identity.sys.platform", "linux"),
        patch("joinery.worker_identity.os.getuid", return_value=0, create=True),
        patch("joinery.worker_identity.os.getgid", return_value=0, create=True),
        pytest.raises(ValueError, match="non-root"),
    ):
        docker_owner_args()


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_supercollider_worker_can_boot_and_answer_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    (tmp_path / "source/dist").mkdir(parents=True)
    (tmp_path / "source/dist/index.js").write_text("")
    monkeypatch.setattr("joinery.workers.installation", lambda _tool: tmp_path)
    monkeypatch.setattr(
        "joinery.server_install.load_installed",
        lambda _tool: type("Spec", (), {"revision": "pinned"})(),
    )
    with (
        patch("joinery.worker_identity.sys.platform", platform),
        patch("joinery.worker_identity.os.getuid", return_value=1001, create=True),
        patch("joinery.worker_identity.os.getgid", return_value=1002, create=True),
    ):
        args = supercollider_worker(tmp_path).args
    # A mount into the image home leaves root-owned folders there, and sclang
    # then never finishes starting.
    assert not any("/home/joiner" in argument for argument in args)
    # JACK needs more shared memory than Docker's 64 MB default.
    assert args[args.index("--shm-size") + 1] == "512m"
    assert "SCLANG_PATH=/tmp/joinery-sclang" in args
    start = args[-1]
    # Readline echo is off (IDE mode) and each line is run (newline -> form feed).
    assert "sclang -i joinery -l /tmp/sclang_conf.yaml" in start
    assert 'tr "\\n" "\\f"' in start
    assert "jackd --no-realtime -d dummy" in start
    assert start.endswith("exec node /opt/collider/dist/index.js")
