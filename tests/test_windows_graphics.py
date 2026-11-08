"""CI graphics provisioning must stay pinned, native and application-local."""

import hashlib
import json
import shutil
import struct
from pathlib import Path

import pytest

from release import windows_graphics as graphics


def pe(path: Path, machine: int = 0x8664) -> Path:
    header = bytearray(256)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 60, 128)
    header[128:132] = b"PE\0\0"
    struct.pack_into("<H", header, 132, machine)
    path.write_bytes(header)
    return path


def test_corrupt_cached_archive_is_rejected_before_extraction(tmp_path: Path) -> None:
    (tmp_path / graphics.ARCHIVE_NAME).write_bytes(b"untrusted")
    with pytest.raises(ValueError, match="cache checksum"):
        graphics.verified_archive(tmp_path)


def test_failed_download_cannot_become_a_cached_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def download(url: str, path: Path) -> None:
        assert url == graphics.ARCHIVE_URL
        path.write_bytes(b"corrupt mirror response")

    monkeypatch.setattr(graphics.urllib.request, "urlretrieve", download)
    with pytest.raises(ValueError, match="release checksum"):
        graphics.verified_archive(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_provision_uses_fresh_private_exe_folder_and_only_required_x64_dlls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = pe(tmp_path / "editor.exe")
    source_digest = graphics.sha256(engine)
    sample = pe(tmp_path / "sample.dll")
    expected = hashlib.sha256(sample.read_bytes()).hexdigest()
    monkeypatch.setattr(
        graphics, "DLL_SHA256", {name: expected for name in graphics.DLL_SHA256}
    )
    monkeypatch.setattr(graphics.shutil, "which", lambda _: "7z")
    monkeypatch.setattr(
        graphics, "verified_archive", lambda _: tmp_path / "verified.7z"
    )

    def extract(argv: list[str], **kwargs: object) -> None:
        assert argv[:3] == ["7z", "x", "-y"]
        assert argv[5:] == ["x64/opengl32.dll", "x64/libgallium_wgl.dll"]
        destination = Path(argv[3][2:]) / "x64"
        destination.mkdir()
        for name in graphics.DLL_SHA256:
            shutil.copyfile(sample, destination / name)

    monkeypatch.setattr(graphics.subprocess, "run", extract)
    output = tmp_path / "isolated graphics"
    env = graphics.provision(engine, output, tmp_path / "cache")
    assert env == {
        "JOINERY_GODOT_BIN": str(output / "editor.exe"),
        "JOINERY_RENDERING_DRIVER": "opengl3",
        "GALLIUM_DRIVER": "llvmpipe",
    }
    assert sorted(p.name for p in output.iterdir()) == [
        "editor.exe",
        "libgallium_wgl.dll",
        "mesa-ci.json",
        "opengl32.dll",
    ]
    assert graphics.sha256(engine) == source_digest
    report = json.loads((output / "mesa-ci.json").read_text())
    assert "must run separately" in report["validation"]
    with pytest.raises(ValueError, match="fresh private"):
        graphics.provision(engine, output, tmp_path / "cache")


def test_non_native_engine_is_rejected_without_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = pe(tmp_path / "arm64.exe", machine=0xAA64)
    with pytest.raises(ValueError, match="Windows x86_64"):
        graphics.provision(engine, tmp_path / "private", tmp_path / "cache")


def test_invalid_extraction_cannot_create_a_deployment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = pe(tmp_path / "editor.exe")
    monkeypatch.setattr(graphics.shutil, "which", lambda _: "7z")
    monkeypatch.setattr(
        graphics, "verified_archive", lambda _: tmp_path / "verified.7z"
    )
    monkeypatch.setattr(graphics.subprocess, "run", lambda *args, **kwargs: None)
    output = tmp_path / "private"
    with pytest.raises(ValueError, match="DLL content"):
        graphics.provision(engine, output, tmp_path / "cache")
    assert not output.exists()
