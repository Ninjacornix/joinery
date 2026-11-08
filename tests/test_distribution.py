"""Release labels, executable layouts, notices and archive contents must agree."""

import hashlib
import json
import os
import plistlib
import struct
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

from joinery import __version__, distribution


def executable(path: Path, target: str) -> Path:
    header = bytearray(256)
    if target.startswith("macos-"):
        header[:4] = b"\xcf\xfa\xed\xfe"
        struct.pack_into(
            "<I", header, 4, 0x100000C if target.endswith("arm64") else 0x1000007
        )
    elif target.startswith("linux-"):
        header[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", header, 18, 183 if target.endswith("arm64") else 62)
    else:
        header[:2] = b"MZ"
        struct.pack_into("<I", header, 60, 128)
        header[128:132] = b"PE\0\0"
        struct.pack_into("<H", header, 132, 0x8664)
    path.write_bytes(header)
    path.chmod(0o755)
    return path


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "source with spaces"
    root.mkdir()
    for name in (
        "pyproject.toml",
        "uv.lock",
        "joinery.toml",
        "README.md",
        "CLAUDE.md",
        ".gitattributes",
        "godot_patches/UPSTREAM",
        "godot_patches/fork.sh",
        "licenses/GODOT-LICENSE.txt",
        "licenses/GODOT-COPYRIGHT.txt",
        "godot_addon/joinery/plugin.gd",
    ):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture: " + name)
    for name in (
        "testbed/.joinery/private.json",
        "testbed/.godot/cache",
        "testbed/.private/notes.txt",
        "testbed/.venv/pyvenv.cfg",
        "testbed/.git/config",
        "testbed/.env",
        "docs/.DS_Store",
    ):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("must not ship")
    monkeypatch.setattr(distribution, "ROOT", root)
    monkeypatch.setattr(
        distribution.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, stdout="4.7.2.stable.fixture\n"
        ),
    )
    return root


@pytest.mark.parametrize(
    "target",
    ["macos-arm64", "macos-x86_64", "linux-x86_64", "linux-arm64", "windows-x86_64"],
)
def test_labels_come_from_actual_executable_headers(
    tmp_path: Path, target: str
) -> None:
    assert distribution.binary_target(executable(tmp_path / "engine", target)) == target


def test_wrong_target_cannot_be_packaged(source: Path, tmp_path: Path) -> None:
    binary = executable(source / "engine", "macos-arm64")
    with pytest.raises(ValueError, match="executable is macos-arm64"):
        distribution.package(
            tmp_path / "wrong.zip", binary=binary, target="windows-x86_64"
        )


def test_macos_desktop_bundle_has_working_layout_and_measured_manifest(
    source: Path, tmp_path: Path
) -> None:
    binary = executable(source / "engine", "macos-arm64")
    output = tmp_path / "Joinery.zip"
    result = distribution.package(output, desktop=True, binary=binary)
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        runtime = "Joinery.app/Contents/Resources/joinery/"
        manifest = json.loads(archive.read(runtime + "manifest.json"))
        assert manifest["joinery"] == __version__
        assert manifest["target"] == "macos-arm64"
        assert manifest["engine"] == "4.7.2.stable.fixture"
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        assert all(
            ".joinery/" not in name
            and ".godot/" not in name
            and ".DS_Store" not in name
            and not {".private", ".venv", ".git", ".env"}.intersection(Path(name).parts)
            for name in names
        )
        assert runtime + "licenses/GODOT-LICENSE.txt" in names
        assert runtime + "testbed/addons/joinery/plugin.gd" in names
        launcher = "Joinery.app/Contents/MacOS/Joinery"
        assert archive.getinfo(launcher).external_attr >> 16 & 0o111
        assert "../Resources/joinery" in archive.read(launcher).decode()
        metadata = plistlib.loads(archive.read("Joinery.app/Contents/Info.plist"))
        assert metadata["CFBundleExecutable"] == "Joinery"
        assert archive.read("Joinery.app/Contents/MacOS/Godot") == binary.read_bytes()
    assert (
        output.with_name(output.name + ".sha256")
        .read_text()
        .startswith(result["sha256"])
    )


def test_windows_symlink_placeholder_does_not_conflict_with_addon_directory(
    source: Path, tmp_path: Path
) -> None:
    placeholder = source / "testbed/addons/joinery"
    placeholder.parent.mkdir(parents=True)
    placeholder.write_text("../../godot_addon/joinery")
    output = tmp_path / "windows.zip"
    distribution.package(
        output,
        desktop=True,
        binary=executable(source / "godot.windows.editor.x86_64.exe", "windows-x86_64"),
    )
    with zipfile.ZipFile(output) as archive:
        assert "joinery/testbed/addons/joinery" not in archive.namelist()
        assert "joinery/testbed/addons/joinery/plugin.gd" in archive.namelist()
        assert "joinery/Joinery.cmd" in archive.namelist()


def test_linux_desktop_launcher_and_source_modes_survive_tar(
    source: Path, tmp_path: Path
) -> None:
    output = tmp_path / "linux.tar.gz"
    distribution.package(
        output,
        desktop=True,
        binary=executable(source / "godot.linuxbsd.editor.x86_64", "linux-x86_64"),
    )
    with tarfile.open(output) as archive:
        assert archive.getmember("joinery/Joinery").mode & 0o111
        assert (
            archive.getmember("joinery/bin/godot.linuxbsd.editor.x86_64").mode & 0o111
        )


def test_release_version_matches_project_metadata() -> None:
    from joinery.catalog import ROOT

    assert (
        tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        == __version__
    )


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX symlink confinement; Windows placeholder is tested separately",
)
def test_release_refuses_files_linking_outside_source(
    source: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "private.txt"
    outside.write_text("must not ship")
    (source / "docs/escape.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="outside this installation"):
        distribution.package(
            tmp_path / "bad.zip", binary=executable(source / "engine", "linux-x86_64")
        )
