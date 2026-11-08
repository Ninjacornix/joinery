"""Release provider checks must neither inherit credentials nor accept another CLI."""

import json
import sys
from pathlib import Path

import pytest

from release.provider_smoke import isolated_environment, main


def test_native_smoke_environment_excludes_auth_and_uses_empty_private_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "ACCESS_TOKEN",
        "CODEX_HOME",
        "HOME",
        "APPDATA",
        "USERPROFILE",
        "LOCALAPPDATA",
        "NODE_OPTIONS",
        "PYTHONPATH",
        "CODEX_CONFIG",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.setenv(key, "must-not-reach-provider")
    environment = isolated_environment(tmp_path)
    assert "must-not-reach-provider" not in environment.values()
    assert not any("KEY" in name or "TOKEN" in name for name in environment)
    for key in ("HOME", "CODEX_HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        path = Path(environment[key])
        assert path.is_relative_to(tmp_path) and path.is_dir()
        assert list(path.iterdir()) == []


def test_smoke_retains_failure_evidence_for_wrong_native_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A real subprocess with the wrong version must fail at preflight, before
    # app-server can be invoked. No Python protocol simulator is involved.
    destination = tmp_path / "evidence" / "provider.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["provider_smoke", "--codex", sys.executable, "--output", str(destination)],
    )
    with pytest.raises(RuntimeError, match="Expected codex-cli"):
        main()
    report = json.loads(destination.read_text())
    assert report["status"] == "failed" and report["error_type"] == "RuntimeError"
    assert "Expected codex-cli" in report["error"]
    assert "stdout" not in report and "stderr" not in report


def _native_cleanup_error(code: int) -> OSError:
    error = OSError("native directory removal failure")
    error.winerror = code  # type: ignore[attr-defined]
    return error


def test_windows_cleanup_waits_only_after_validated_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from release import provider_smoke

    home = tmp_path / "isolated"
    home.mkdir()
    (home / "tail.txt").write_text("must be removed")
    remove = provider_smoke.shutil.rmtree
    attempts = 0
    sleeps = []

    def locked_once(directory: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _native_cleanup_error(32)
        assert (home / "tail.txt").read_text() == "must be removed"
        remove(directory)

    monkeypatch.setattr(provider_smoke.sys, "platform", "win32")
    monkeypatch.setattr(provider_smoke.shutil, "rmtree", locked_once)
    monkeypatch.setattr(provider_smoke.time, "sleep", sleeps.append)
    report = provider_smoke.remove_isolated_home(
        home, lifecycle_complete=True, process_ids=[17]
    )
    assert attempts == 2 and sleeps == [0.025]
    assert report["directory_removed"] and report["sharing_violations"] == 1
    assert not home.exists()


@pytest.mark.parametrize(
    ("native_platform", "lifecycle_complete", "error_code"),
    [("win32", False, 32), ("win32", True, 5), ("darwin", True, 32)],
)
def test_cleanup_never_retries_other_failures_or_unvalidated_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native_platform: str,
    lifecycle_complete: bool,
    error_code: int,
) -> None:
    from release import provider_smoke

    error = _native_cleanup_error(error_code)
    calls = []

    def fail(directory: Path) -> None:
        calls.append(directory)
        raise error

    monkeypatch.setattr(provider_smoke.sys, "platform", native_platform)
    monkeypatch.setattr(provider_smoke.shutil, "rmtree", fail)
    monkeypatch.setattr(provider_smoke.time, "sleep", pytest.fail)
    with pytest.raises(OSError) as failure:
        provider_smoke.remove_isolated_home(
            tmp_path, lifecycle_complete=lifecycle_complete, process_ids=[]
        )
    assert failure.value is error and calls == [tmp_path]


def test_windows_cleanup_deadline_keeps_hard_failure_and_lock_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from release import provider_smoke, windows_lock_diagnostics

    error = _native_cleanup_error(32)
    clock = iter([0.0, 0.5, 2.0])
    attempts = []
    sleeps = []
    diagnostics = []

    def fail(directory: Path) -> None:
        attempts.append(directory)
        raise error

    def diagnose(resource: Path, process_ids: list[int]) -> dict[str, object]:
        diagnostics.append((resource, process_ids))
        return {"owned_process_ids": process_ids, "lock_owners": []}

    monkeypatch.setattr(provider_smoke.sys, "platform", "win32")
    monkeypatch.setattr(provider_smoke.shutil, "rmtree", fail)
    monkeypatch.setattr(provider_smoke.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(provider_smoke.time, "sleep", sleeps.append)
    monkeypatch.setattr(windows_lock_diagnostics, "diagnose_locks", diagnose)
    with pytest.raises(provider_smoke.IsolatedHomeCleanupError) as failure:
        provider_smoke.remove_isolated_home(
            tmp_path, lifecycle_complete=True, process_ids=[17, 23]
        )
    assert failure.value.__cause__ is error
    assert attempts == [tmp_path, tmp_path] and sleeps == [0.025]
    assert diagnostics == [(tmp_path, [17, 23])]
    assert failure.value.diagnostics["owned_process_ids"] == [17, 23]
    assert tmp_path.exists()
