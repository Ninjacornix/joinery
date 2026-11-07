"""Doctor reports honestly on what is installed, and never installs anything."""

from pathlib import Path

import pytest

from joinery import doctor
from joinery.catalog import load_catalog
from joinery.doctor import CORE, PROBES, Probe, findings, report


def project(tmp_path: Path, enabled: str) -> Path:
    config = tmp_path / "joinery.toml"
    config.write_text(f"[tools]\nenabled = [{enabled}]\n")
    return config


def test_every_available_tool_has_a_probe() -> None:
    # A tool that can be selected must be checkable, or doctor would stay quiet
    # about a program the project actually needs.
    for tool in load_catalog().tool:
        if tool.status == "available":
            assert tool.id in PROBES, tool.id


def test_reports_present_program(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/stub")
    monkeypatch.setattr(doctor, "PROBES", {"godot": Probe("godot", version_flag="")})
    results = findings(load_catalog(), project(tmp_path, '"godot"'))
    assert all(f.ok for f in results), [f.detail for f in results]


def test_missing_program_is_flagged_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
    text, code = report(project(tmp_path, '"godot"'))
    assert code == 1
    assert "MISS" in text and "godot" in text
    # Points at the sandbox rather than offering to install anything.
    assert "docker build" in text and "--network none" in text


def test_byo_tool_is_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Aseprite and REAPER are user-licensed, so their absence is not a fault.
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
    monkeypatch.setattr(doctor, "CORE", {})
    results = findings(load_catalog(), project(tmp_path, '"aseprite", "godot"'))
    byo = {f.name: f for f in results}
    assert byo["aseprite"].required is False
    assert byo["godot"].required is True


def test_core_programs_are_always_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
    names = {f.name for f in findings(load_catalog(), project(tmp_path, ""))}
    assert set(CORE) <= names


def test_version_flag_failure_still_reports_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A program that refuses --version is still installed, so it counts.
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/blender")

    def boom(*_: object, **__: object) -> None:
        raise OSError("cannot exec")

    monkeypatch.setattr(doctor.subprocess, "run", boom)
    assert doctor._version(Probe("blender")) == "/usr/bin/blender"
