"""SDK bytes must be verified before installation; runtime terms stay unchanged."""

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from release import windows_build_deps as sdk


def test_verified_sdk_install_and_cache_tampering_preserve_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    archive = cache / "technical-sdk.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("upstream/include/header.h", b"technical compiler fixture\r\n")
    specification = sdk.Archive(
        archive.name, "https://example.invalid/fixture.zip",
        hashlib.sha256(archive.read_bytes()).hexdigest(), "accesskit", "upstream/",
    )
    monkeypatch.setattr(sdk, "ARCHIVES", (specification,))

    def no_network(*args: object) -> None:
        raise AssertionError("A verified cached archive must not be downloaded again")

    monkeypatch.setattr(sdk.urllib.request, "urlretrieve", no_network)
    output = tmp_path / "generated"
    sdk.provision(output, cache)
    assert (output / "accesskit/include/header.h").read_bytes() == b"technical compiler fixture\r\n"
    manifest = json.loads((output / "sdk-manifest.json").read_text())
    assert manifest[archive.name]["sha256"] == specification.sha256
    # A changed cache must fail before replacing the last valid installation.
    archive.write_bytes(b"changed bytes")
    with pytest.raises(ValueError, match="cache checksum mismatch"):
        sdk.provision(output, cache)
    assert (output / "accesskit/include/header.h").read_bytes() == b"technical compiler fixture\r\n"


def test_sdk_archive_cannot_write_outside_its_sdk_directory(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../../outside.txt", b"must not escape")
    specification = sdk.Archive(archive.name, "", "", "sdk")
    with pytest.raises(ValueError, match="Unsafe Windows SDK archive member"):
        sdk.extract(specification, archive, tmp_path / "output")
    assert not (tmp_path / "outside.txt").exists()


def test_sdk_runtime_notices_are_copied_byte_for_byte_and_pix_is_conditional(
    tmp_path: Path,
) -> None:
    dependencies = tmp_path / "dependencies"
    binary = tmp_path / "bin"
    binary.mkdir()
    for dependency, names in sdk.RUNTIME_NOTICES.items():
        folder = dependencies / dependency
        folder.mkdir(parents=True)
        for name in names:
            (folder / name).write_bytes(b"Technical notice fixture\r\n\x00" + name.encode())
    sdk.install_runtime_notices(binary, dependencies)
    for name, destination in sdk.RUNTIME_NOTICES["agility_sdk"].items():
        assert (binary / "runtime-licenses" / destination).read_bytes() == (
            dependencies / "agility_sdk" / name
        ).read_bytes()
    assert not (binary / "runtime-licenses/WINPIX-LICENSE.txt").exists()
    (binary / "WinPixEventRuntime.dll").touch()
    sdk.install_runtime_notices(binary, dependencies)
    for name, destination in sdk.RUNTIME_NOTICES["pix"].items():
        assert (binary / "runtime-licenses" / destination).read_bytes() == (
            dependencies / "pix" / name
        ).read_bytes()
