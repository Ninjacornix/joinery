"""Verified Windows SDKs matching the pinned Godot build's upstream installers.

Versions/layout: misc/scripts/install_accesskit.py and
misc/scripts/install_d3d12_sdk_windows.py in Godot 4.7.2-stable.
AccessKit/NIR hashes match their published GitHub release asset digests;
NuGet hashes were measured from the exact versioned official package downloads.
No installer runs or second network download occurs after verification.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Archive:
    filename: str
    url: str
    sha256: str
    directory: str
    prefix: str = ""


ARCHIVES = (
    Archive(
        "accesskit-c-0.22.3.zip",
        "https://github.com/godotengine/godot-accesskit-c-static/releases/download/0.22.3/accesskit-c-0.22.3.zip",
        "41b30b1382d03b225b6f7e8721269a32557a68065804f3797bdffc34a3273498",
        "accesskit",
        "accesskit-c-0.22.3/",
    ),
    Archive(
        "godot-nir-static-x86_64-msvc-release.zip",
        "https://github.com/godotengine/godot-nir-static/releases/download/25.3.1-3/godot-nir-static-x86_64-msvc-release.zip",
        "f7796c97657fab348774906dc938bb46fbfd1e45434244141fc8886e5e4b5530",
        "mesa-x86_64-msvc",
    ),
    Archive(
        "Microsoft.Direct3D.D3D12-1.618.5.nupkg",
        "https://www.nuget.org/api/v2/package/Microsoft.Direct3D.D3D12/1.618.5",
        "0027fc24f947c48dbded13ada7d280be221eb651644e23a8a476f0f1f0a079dd",
        "agility_sdk",
    ),
    Archive(
        "WinPixEventRuntime-1.0.240308001.nupkg",
        "https://www.nuget.org/api/v2/package/WinPixEventRuntime/1.0.240308001",
        "726acc93d6968e2146261a1e415521747d50ad69894c2b42b5d0d4c29fd66ec4",
        "pix",
    ),
)

RUNTIME_NOTICES = {
    "agility_sdk": {
        "LICENSE.txt": "MICROSOFT-DIRECTX-LICENSE.txt",
        "LICENSE-CODE.txt": "MICROSOFT-DIRECTX-CODE-LICENSE.txt",
        "distributable files.txt": "MICROSOFT-DIRECTX-DISTRIBUTABLES.txt",
        "README.md": "MICROSOFT-DIRECTX-README.md",
    },
    "pix": {
        "license.txt": "WINPIX-LICENSE.txt",
        "ThirdPartyNotices.txt": "WINPIX-THIRD-PARTY-NOTICES.txt",
    },
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verified_archive(archive: Archive, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / archive.filename
    if not path.is_file():
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
            partial = Path(stream.name)
        try:
            urllib.request.urlretrieve(archive.url, partial)
            if digest(partial) != archive.sha256:
                raise ValueError("Windows SDK download checksum mismatch: " + archive.filename)
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
    if digest(path) != archive.sha256:
        raise ValueError("Windows SDK cache checksum mismatch: " + archive.filename)
    return path


def extract(archive: Archive, source: Path, output: Path) -> None:
    """Extract only verified archive members within their designated SDK root."""
    output.mkdir(parents=True)
    root = output.resolve()
    with zipfile.ZipFile(source) as package:
        for member in package.infolist():
            if archive.prefix and not member.filename.startswith(archive.prefix):
                raise ValueError("Unexpected Windows SDK archive prefix")
            relative = member.filename.removeprefix(archive.prefix)
            if not relative:
                continue
            target = output / relative
            if (
                not target.resolve().is_relative_to(root)
                or stat.S_IFMT(member.external_attr >> 16) == stat.S_IFLNK
            ):
                raise ValueError("Unsafe Windows SDK archive member")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as incoming, target.open("wb") as destination:
                    shutil.copyfileobj(incoming, destination)


def provision(output: Path, cache: Path) -> list[str]:
    """Re-extract validated bytes into a dedicated generated build directory."""
    sources = [(archive, verified_archive(archive, cache)) for archive in ARCHIVES]
    if output.exists() and not (output / "sdk-manifest.json").is_file():
        raise ValueError("Refusing to replace an unrecognized Windows SDK directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".windows-sdk-", dir=output.parent) as temporary:
        prepared = Path(temporary) / "deps"
        prepared.mkdir()
        for archive, source in sources:
            extract(archive, source, prepared / archive.directory)
        (prepared / "sdk-manifest.json").write_text(
            json.dumps(
                {archive.filename: {"url": archive.url, "sha256": archive.sha256} for archive in ARCHIVES},
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        if output.exists():
            shutil.rmtree(output)
        prepared.replace(output)
    return [
        "accesskit=yes",
        "d3d12=yes",
        "accesskit_sdk_path=" + str(output / "accesskit"),
        "mesa_libs=" + str(output / "mesa"),
        "agility_sdk_path=" + str(output / "agility_sdk"),
        "pix_path=" + str(output / "pix"),
    ]


def install_runtime_notices(binary_directory: Path, dependencies: Path) -> None:
    """Preserve exact upstream terms beside the generated native runtime DLLs."""
    notices = binary_directory / "runtime-licenses"
    selected = ["agility_sdk"]
    if (binary_directory / "WinPixEventRuntime.dll").is_file():
        selected.append("pix")
    notices.mkdir(exist_ok=True)
    for dependency in selected:
        for source, name in RUNTIME_NOTICES[dependency].items():
            shutil.copyfile(dependencies / dependency / source, notices / name)
