"""Offline container reuse, mount identity and timeout/cancellation teardown."""

import asyncio
from pathlib import Path

import pytest

from joinery import warm_workers
from joinery.runner import Result


@pytest.fixture
def commands(monkeypatch):
    calls = []
    monkeypatch.setattr(warm_workers, "_containers", {})
    monkeypatch.setattr(warm_workers, "_locks", {})
    monkeypatch.setenv("JOINERY_WORKER_SCOPE", "a" * 32)

    async def command(argv, timeout):
        calls.append(argv)
        return Result(True, "fixture", "", 0.01)

    monkeypatch.setattr(warm_workers, "_command", command)
    return calls


def test_same_scope_project_and_mount_reuse_only_the_container(
    tmp_path: Path, commands
):
    async def run():
        for _ in range(2):
            assert (
                await warm_workers.execute(
                    ["blender", "--background"], tmp_path, 30, False, "fixture-image"
                )
            ).ok

    asyncio.run(run())
    starts = [c for c in commands if c[1] == "run"]
    executions = [c for c in commands if c[1] == "exec"]
    assert len(starts) == 1 and len(executions) == 2
    assert starts[0][starts[0].index("--network") + 1] == "none"
    assert "joinery.scope=" + "a" * 32 in starts[0]
    assert all("bash" in argv and "--background" in argv for argv in executions)


def test_other_projects_readonly_mounts_and_images_never_share_a_worker(
    tmp_path: Path, commands
):
    async def run():
        for project, readonly, image in [
            (tmp_path, False, "one"),
            (tmp_path, True, "one"),
            (tmp_path / "other", False, "one"),
            (tmp_path, False, "two"),
        ]:
            await warm_workers.execute(["true"], project, 30, readonly, image)

    asyncio.run(run())
    assert len([c for c in commands if c[1] == "run"]) == 4
    assert any(str(tmp_path) + ":/work:ro" in c for c in commands)


@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
def test_interrupted_native_command_destroys_its_worker_before_reuse(
    tmp_path: Path, commands, monkeypatch, failure
):
    original = warm_workers._command

    async def command(argv, timeout):
        if argv[1] == "exec":
            commands.append(argv)
            raise failure()
        return await original(argv, timeout)

    monkeypatch.setattr(warm_workers, "_command", command)
    with pytest.raises(failure):
        asyncio.run(warm_workers.execute(["blender"], tmp_path, 30, False, "image"))
    assert not warm_workers._containers
    assert commands[-1][1:3] == ["rm", "--force"]


def test_unowned_scope_cannot_start_or_reuse_a_container(
    tmp_path: Path, commands, monkeypatch
):
    monkeypatch.setenv("JOINERY_WORKER_SCOPE", "unowned")
    with pytest.raises(ValueError, match="scope"):
        asyncio.run(warm_workers.execute(["true"], tmp_path, 30, False, "image"))
    assert not commands
