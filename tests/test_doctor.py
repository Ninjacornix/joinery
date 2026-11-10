"""Doctor renders the shared readiness core, checks tools in their workers, never installs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from joinery import doctor, readiness
from joinery.readiness import Image, ProbeResult
from tests.test_readiness import FakeHost


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "cache"
    monkeypatch.setenv("JOINERY_READINESS_CACHE", str(folder))
    return folder


@pytest.fixture
def host(tmp_path: Path) -> FakeHost:
    engine = tmp_path / "godot.macos.editor.arm64"
    engine.write_text("editor")
    return FakeHost(engine=engine)


def project(tmp_path: Path, enabled: str) -> Path:
    config = tmp_path / "joinery.toml"
    config.write_text(f"[tools]\nenabled = [{enabled}]\n")
    return config


def test_godot_project_is_healthy_without_godot_or_xvfb_on_the_host_path(
    tmp_path: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Mac this runs on has no `godot`, `Xvfb` or `ffmpeg` on PATH. Godot runs
    # as the Joinery editor on the host and in the worker; Xvfb and ffmpeg live
    # only in workers, so none of them may be reported missing.
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    text, code = doctor.report(project(tmp_path, '"godot"'), host=host)
    assert code == 0, text
    assert "MISS" not in text and "Xvfb" not in text and "xvfb" not in text
    assert "[ok  ] Godot" in text
    assert "Everything this project needs is ready." in text
    # Checks ran in the worker and against the editor binary, not on PATH.
    assert any(image == "joinery-mcp-worker" for image, _ in host.runs)
    assert any(image == "host" for image, _ in host.runs)


def test_missing_worker_points_at_setup_not_a_docker_build(
    tmp_path: Path, host: FakeHost
) -> None:
    host.images.pop("joinery-creative-worker")
    text, code = doctor.report(project(tmp_path, '"godot", "krita"'), host=host)
    assert code == 1
    assert "Krita" in text and "Joinery setup" in text
    assert "docker build" not in text and "joinery-sandbox" not in text


def test_docker_down_says_start_docker(tmp_path: Path, host: FakeHost) -> None:
    host.docker_reason = "Docker is not running."
    text, code = doctor.report(project(tmp_path, '"godot"'), host=host)
    assert code == 1
    assert "Start Docker" in text and "docker build" not in text


def test_missing_desktop_app_is_information_not_a_failure(
    tmp_path: Path, host: FakeHost
) -> None:
    text, code = doctor.report(project(tmp_path, '"godot", "surge-xt"'), host=host)
    assert code == 0, text
    assert "Open in" in text and "unavailable" in text


def test_missing_editor_binary_is_a_problem(tmp_path: Path, host: FakeHost) -> None:
    host.engine = None
    text, code = doctor.report(project(tmp_path, '"godot"'), host=host)
    assert code == 1
    assert "Godot" in text


def test_removed_tool_is_explained_not_probed(tmp_path: Path, host: FakeHost) -> None:
    text, code = doctor.report(project(tmp_path, '"godot", "aseprite"'), host=host)
    assert code == 0, text
    assert "Aseprite was removed" in text
    assert not any("aseprite" in " ".join(argv) for _, argv in host.runs)


def test_cached_answers_are_reused_and_misses_are_filled(
    tmp_path: Path, host: FakeHost
) -> None:
    host.probes[("godot", "get_project_info")] = ProbeResult(True, True, "{}")
    config = project(tmp_path, '"godot"')
    doctor.report(config, host=host)
    first = len(host.runs)
    assert first
    doctor.report(config, host=host)
    assert len(host.runs) == first  # every answer came from the cache
    # A rebuilt image has a new id, so its checks run again.
    host.images["joinery-mcp-worker"] = Image("sha256:rebuilt")
    doctor.report(config, host=host)
    assert len(host.runs) > first


def test_cache_only_mode_reports_checking_without_running_workers(
    tmp_path: Path, host: FakeHost
) -> None:
    text, code = doctor.report(project(tmp_path, '"godot"'), workers=False, host=host)
    assert code == 0
    assert host.runs == []
    assert "have not run yet" in text


def test_doctor_reads_the_shared_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []

    def status(project: Path, **_: Any) -> dict[str, Any]:
        seen.append(project)
        return {
            "checking": False,
            "tools": [
                {
                    "id": "godot",
                    "name": "Godot",
                    "state": "ready",
                    "summary": "Godot 4.7.2 tools ready (offline worker)",
                    "features": [],
                }
            ],
            "removed": [],
        }

    monkeypatch.setattr(readiness, "status", status)
    config = project(tmp_path, '"godot"')
    text, code = doctor.report(config)
    assert code == 0 and seen == [config.parent]
    assert "[ok  ] Godot" in text


def test_no_tools_selected(tmp_path: Path, host: FakeHost) -> None:
    text, code = doctor.report(tmp_path / "joinery.toml", host=host)
    assert code == 0
    assert "No tools are selected" in text
