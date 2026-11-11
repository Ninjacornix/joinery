"""Publishing to itch.io: the user's act only, butler pinned and verified, dry runs exact.

butler here is a recording stub and every download a fake opener: nothing in
these tests reaches the network or an itch.io account.
"""

from __future__ import annotations

import hashlib
import io
import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest

from joinery import hook, policy, share_itch
from joinery.policy import Severity
from joinery.share_itch import Butler, Download, PublishError

FAKE_KEY = "not-a-real-key"


def built_project(root: Path) -> Path:
    project = root / "moss"
    builds = project / "share/builds"
    builds.mkdir(parents=True)
    (project / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Moss Run"\nconfig/version="1.2.0"\n'
    )
    targets = {}
    for target, channel in (("web", "web"), ("macos", "mac")):
        archive = builds / f"moss-run-{target}.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("README.txt", "Moss Run")
        targets[target] = {
            "ok": True,
            "channel": channel,
            "zip": archive.relative_to(project).as_posix(),
        }
    targets["linux"] = {"ok": False, "channel": "linux", "error": "failed"}
    (builds / "report.json").write_text(json.dumps({"targets": targets}))
    return project


# --- Agents can't publish ---------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "uv run joinery share itch --project . --target me/moss",
        "uv run --frozen joinery share itch --project . --target me/moss --dry-run",
        "cd game && joinery share itch --project . --target me/moss --yes",
        "butler push share/builds/moss-run-web.zip me/moss:web",
        "~/.cache/joinery/butler/15.24.0/butler push x me/moss:web",
        "butler login",
        'BUTLER_API_KEY=x "butler" push build me/moss:web',
    ],
)
def test_policy_refuses_publishing_for_every_agent(command: str) -> None:
    verdict = policy.user_only_violation(command)
    assert verdict is not None and verdict.severity is Severity.DENY
    assert "Publish" in verdict.reason
    raw = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/tmp"}
    assert hook.violation(raw) is not None


@pytest.mark.parametrize(
    "command",
    [
        "uv run joinery share build --project . --targets web",
        "uv run joinery share media --project .",
        "uv run joinery share page --project .",
        "uv run joinery share status --project . --json",
        "echo butler is itch's tool",
    ],
)
def test_policy_allows_the_rest_of_share(command: str) -> None:
    assert policy.user_only_violation(command) is None


def test_publish_refuses_inside_an_agent_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOINERY_AGENT_ROLE", "reviewer")
    with pytest.raises(PublishError, match="Only you"):
        share_itch.publish(built_project(tmp_path), "me/moss", None, dry_run=True)


# --- butler: pinned, verified ------------------------------------------------------------


def _fake_butler_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("butler", "#!/bin/sh\necho butler\n")
        bundle.writestr("7z.so", "lib")
    return buffer.getvalue()


def _release(data: bytes, sha: str | None = None) -> Butler:
    pinned = Download(
        "https://broth.itch.zone/butler/test/archive/default",
        sha if sha is not None else hashlib.sha256(data).hexdigest(),
        len(data),
    )
    return Butler("15.24.0", "MIT", {share_itch.platform_key(): pinned})


def test_butler_installs_only_the_pinned_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JOINERY_BUTLER", raising=False)
    data = _fake_butler_zip()
    release = _release(data)
    asked: list[str] = []

    def opener(url: str, timeout: float) -> io.BytesIO:
        asked.append(url)
        return io.BytesIO(data)

    assert share_itch.installed(tmp_path, release) is None
    program = share_itch.install(tmp_path, release, opener)
    assert asked == ["https://broth.itch.zone/butler/test/archive/default"]
    assert program.read_text().startswith("#!/bin/sh")
    assert program.stat().st_mode & stat.S_IXUSR
    assert (program.parent / "7z.so").is_file()
    assert share_itch.installed(tmp_path, release) == program


def test_butler_with_a_wrong_hash_is_deleted(tmp_path: Path) -> None:
    data = _fake_butler_zip()
    release = _release(data, sha="0" * 64)
    with pytest.raises(PublishError, match="did not match"):
        share_itch.install(tmp_path, release, lambda url, timeout: io.BytesIO(data))
    assert not any(p.is_file() for p in tmp_path.rglob("*"))


def test_unpinned_butler_is_never_downloaded(tmp_path: Path) -> None:
    def opener(url: str, timeout: float) -> io.BytesIO:
        raise AssertionError("no download for an unpinned platform")

    with pytest.raises(PublishError, match="isn't pinned"):
        share_itch.install(tmp_path, _release(b"", sha=""), opener)


def test_butler_archive_with_a_link_or_parent_path_is_refused(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("../butler", "x")
    data = buffer.getvalue()
    with pytest.raises(PublishError, match="unsafe"):
        share_itch.install(
            tmp_path, _release(data), lambda url, timeout: io.BytesIO(data)
        )


def test_repo_policy_names_official_urls_for_each_platform() -> None:
    release = share_itch.load_butler()
    assert set(release.platforms) == {"darwin-amd64", "linux-amd64", "windows-amd64"}
    for key, pinned in release.platforms.items():
        assert (
            pinned.url
            == f"https://broth.itch.zone/butler/{key}/{release.version}/archive/default"
        )


# --- Dry run and a fake butler -------------------------------------------------------------


def test_dry_run_shows_exactly_what_would_be_pushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JOINERY_BUTLER", raising=False)
    monkeypatch.delenv("BUTLER_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    project = built_project(tmp_path)
    result = share_itch.publish(project, "me/moss", ["web", "macos"], dry_run=True)
    assert [s["channel"] for s in result["steps"]] == ["web", "mac"]
    web = result["steps"][0]
    assert web["argv"] == [
        "butler",
        "push",
        str(project.resolve() / "share/builds/moss-run-web.zip"),
        "me/moss:web",
        "--userversion",
        "1.2.0",
        "--if-changed",
    ]
    assert len(result["notes"]) == 2
    with pytest.raises(PublishError, match="no linux build"):
        share_itch.plan(project, "me/moss", ["linux"])
    with pytest.raises(PublishError, match="user/game"):
        share_itch.plan(project, "not a target", None)


@pytest.mark.skipif(sys.platform == "win32", reason="the stub is a shell script")
def test_publish_runs_butler_with_the_users_own_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = built_project(tmp_path)
    log = tmp_path / "calls.txt"
    stub = tmp_path / "butler"
    stub.write_text(f'#!/bin/sh\necho "$BUTLER_API_KEY $@" >> "{log}"\necho pushed\n')
    stub.chmod(0o755)
    monkeypatch.setenv("JOINERY_BUTLER", str(stub))
    monkeypatch.delenv("BUTLER_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    with pytest.raises(PublishError, match="Sign in"):
        share_itch.publish(project, "me/moss", None)
    assert not log.exists()
    monkeypatch.setenv("BUTLER_API_KEY", FAKE_KEY)
    result = share_itch.publish(project, "me/moss", ["web"])
    assert result["ok"] and result["steps"][0]["output"] == "pushed"
    call = log.read_text().strip()
    assert call == (
        f"{FAKE_KEY} push {project.resolve() / 'share/builds/moss-run-web.zip'} "
        "me/moss:web --userversion 1.2.0 --if-changed"
    )
    assert FAKE_KEY not in "".join(
        p.read_text() for p in (project / "share").glob("*.json")
    )
    assert share_itch.settings(project) == {"target": "me/moss", "channels": ["web"]}


def test_publish_without_butler_asks_for_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JOINERY_BUTLER", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    with pytest.raises(PublishError, match="install-butler"):
        share_itch.publish(built_project(tmp_path), "me/moss", None)
    assert not any(p.is_file() for p in (tmp_path / "cache").rglob("*"))
