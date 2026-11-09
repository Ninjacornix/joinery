"""Self-contained source/engine bundles with a measured file manifest."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import plistlib
import struct
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from joinery import __version__
from joinery.bootstrap import addon_version
from joinery.catalog import ROOT


def engine_binary() -> Path:
    override = os.environ.get("JOINERY_GODOT_BIN")
    if override and Path(override).is_file():
        return Path(override).resolve()
    if ROOT.parent.name == "Resources" and ROOT.parent.parent.name == "Contents":
        bundled = ROOT.parent.parent / "MacOS/Godot"
        if bundled.is_file():
            return bundled
    for folder in (ROOT / "bin", ROOT / "godot_patches/godot-src/bin"):
        for path in sorted(folder.glob("godot.*.editor.*")):
            if (
                path.is_file()
                and not path.name.endswith(".console.exe")
                and os.access(path, os.X_OK)
            ):
                return path
    raise ValueError(
        "Build the Joinery editor with godot_patches/fork.sh build, or set JOINERY_GODOT_BIN."
    )


def binary_target(binary: Path) -> str:
    """Read executable headers so a release cannot silently mislabel its target."""
    with binary.open("rb") as source:
        header = source.read(4096)
    if header.startswith(b"\x7fELF") and len(header) >= 20:
        endian = "<" if header[5] == 1 else ">"
        machine = struct.unpack_from(endian + "H", header, 18)[0]
        arch = {62: "x86_64", 183: "arm64"}.get(machine)
        if arch:
            return "linux-" + arch
    if header.startswith(b"MZ") and len(header) >= 64:
        offset = struct.unpack_from("<I", header, 60)[0]
        if offset + 6 <= len(header) and header[offset : offset + 4] == b"PE\0\0":
            machine = struct.unpack_from("<H", header, offset + 4)[0]
            arch = {0x8664: "x86_64", 0xAA64: "arm64"}.get(machine)
            if arch:
                return "windows-" + arch
    if len(header) >= 8 and header[:4] in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"}:
        endian = "<" if header[0] == 0xCF else ">"
        cpu = struct.unpack_from(endian + "I", header, 4)[0]
        arch = {0x1000007: "x86_64", 0x100000C: "arm64"}.get(cpu)
        if arch:
            return "macos-" + arch
    if header[:4] == b"\xca\xfe\xba\xbe" and len(header) >= 48:
        count = struct.unpack_from(">I", header, 4)[0]
        if count == 2 and {
            struct.unpack_from(">I", header, 8 + i * 20)[0] for i in range(2)
        } == {0x1000007, 0x100000C}:
            return "macos-universal"
    raise ValueError("Unsupported or invalid editor executable header.")


def windows_runtime_files(binary: Path) -> list[Path]:
    """Select only matching original Godot runtime DLLs, never CI Mesa DLLs."""
    names = ("D3D12Core.dll", "d3d12SDKLayers.dll", "WinPixEventRuntime.dll")
    files = [binary.with_name(name) for name in names if binary.with_name(name).is_file()]
    if any(path.name == names[0] for path in files) != any(
        path.name == names[1] for path in files
    ):
        raise ValueError("The native Windows D3D12 runtime is incomplete.")
    target = binary_target(binary)
    for runtime in files:
        if binary_target(runtime) != target:
            raise ValueError("Windows runtime DLL architecture mismatch: " + runtime.name)
    return files


def windows_runtime_notices(binary: Path) -> list[Path]:
    """Shipping runtime SDK binaries requires their accompanying upstream terms."""
    names = []
    if binary.with_name("D3D12Core.dll").is_file():
        names += [
            "MICROSOFT-DIRECTX-LICENSE.txt", "MICROSOFT-DIRECTX-CODE-LICENSE.txt",
            "MICROSOFT-DIRECTX-DISTRIBUTABLES.txt", "MICROSOFT-DIRECTX-README.md",
        ]
    if binary.with_name("WinPixEventRuntime.dll").is_file():
        names += ["WINPIX-LICENSE.txt", "WINPIX-THIRD-PARTY-NOTICES.txt"]
    notices = [binary.parent / "runtime-licenses" / name for name in names]
    if any(not path.is_file() for path in notices):
        raise ValueError("Windows runtime SDK notices are missing from the build output.")
    return notices


def _source_files() -> dict[str, Path]:
    files: dict[str, Path] = {}
    folders = [
        "joinery",
        "bridge",
        "servers",
        "policy",
        "templates",
        "blueprints",
        "features",
        "kits",
        "godot_addon",
        "godot_patches/patches",
        "sandbox",
        "docs",
        "tests",
        "evals",
        "testbed",
        "licenses",
        "release",
    ]
    for folder in folders:
        for path in (ROOT / folder).rglob("*"):
            if path.relative_to(ROOT).as_posix() == "testbed/addons/joinery":
                continue  # Windows Git can materialize the directory symlink as text.
            if path.is_file() and not {
                "__pycache__",
                ".DS_Store",
                ".pytest_cache",
                ".godot",
                ".joinery",
                ".private",
                ".venv",
                ".git",
                ".env",
            }.intersection(path.parts):
                if not path.resolve().is_relative_to(ROOT):
                    raise ValueError(
                        "Distribution inputs cannot link outside this installation."
                    )
                files[path.relative_to(ROOT).as_posix()] = path
    # pathlib does not recurse through the test project's internal addon symlink.
    # Store real files so the sample remains usable in a plain extracted bundle.
    for relative, path in list(files.items()):
        if relative.startswith("godot_addon/joinery/"):
            files[relative.replace("godot_addon/", "testbed/addons/", 1)] = path
    for relative in [
        "pyproject.toml",
        "uv.lock",
        "joinery.toml",
        "README.md",
        "LICENSE",
        "LICENSING.md",
        "godot_patches/LICENSE",
        "CLAUDE.md",
        ".gitattributes",
        "godot_patches/UPSTREAM",
        "godot_patches/fork.sh",
    ]:
        files[relative] = ROOT / relative
    for required in ("licenses/GODOT-LICENSE.txt", "licenses/GODOT-COPYRIGHT.txt"):
        if required not in files:
            raise ValueError(
                "The release is missing required Godot notices: " + required
            )
    return files


@dataclass(frozen=True)
class _Entry:
    source: Path | bytes
    executable: bool = False

    def read(self) -> bytes:
        return (
            self.source.read_bytes() if isinstance(self.source, Path) else self.source
        )


def _archive(output: Path, entries: dict[str, _Entry]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix == ".zip":
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, entry in sorted(entries.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o100755 if entry.executable else 0o100644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, entry.read())
        return
    if not output.name.endswith(".tar.gz"):
        raise ValueError("Use a .zip or .tar.gz release archive.")
    with (
        output.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for name, entry in sorted(entries.items()):
            data = entry.read()
            tar_info = tarfile.TarInfo(name)
            tar_info.size = len(data)
            tar_info.mode = 0o755 if entry.executable else 0o644
            archive.addfile(tar_info, io.BytesIO(data))


def _launcher(binary: str, *, macos: bool = False) -> bytes:
    root = '"$launcher_dir/../Resources/joinery"' if macos else '"$launcher_dir"'
    executable = (
        '"$launcher_dir/Godot"' if macos else '"$JOINERY_HOME/bin/' + binary + '"'
    )
    return (
        "#!/bin/sh\nset -eu\n"
        'launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        "JOINERY_HOME=" + root + "\nexport JOINERY_HOME\n"
        'PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"\nexport PATH\n'
        'runtime_id=$(printf "%s" "$JOINERY_HOME" | cksum | cut -d " " -f 1)\n'
        + (
            'joinery_cache="$HOME/Library/Caches/Joinery"\n'
            if macos
            else 'joinery_cache="${XDG_CACHE_HOME:-$HOME/.cache}/joinery"\n'
        )
        + 'UV_PROJECT_ENVIRONMENT="$joinery_cache/python-'
        + __version__
        + '-$runtime_id"\n'
        "UV_NO_DEV=1\nexport UV_PROJECT_ENVIRONMENT UV_NO_DEV\n"
        "if ! command -v uv >/dev/null 2>&1; then\n"
        + (
            '  /usr/bin/osascript -e \'display alert "Joinery needs uv" message "Install uv from astral.sh/uv, then reopen Joinery. Setup instructions are included in the application README."\'\n'
            if macos
            else '  printf "%s\\n" "Joinery needs uv. Install it from astral.sh/uv, then reopen Joinery." >&2\n'
        )
        + "  exit 1\nfi\n"
        'if [ "$#" -eq 0 ]; then set -- --project-manager; fi\n'
        "exec " + executable + ' "$@"\n'
    ).encode()


def package(
    output: Path, *, desktop: bool = False, target: str = "", binary: Path | None = None
) -> dict[str, Any]:
    """Package on the target host; its binary must execute and identify itself."""
    output = output.resolve()
    binary = (binary or engine_binary()).resolve()
    actual_target = binary_target(binary)
    if target and target != actual_target:
        raise ValueError(f"Requested {target}, but the executable is {actual_target}.")
    version = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if not version:
        raise ValueError("The editor returned no version; refusing to package it.")
    root = "joinery/"
    executable_name = root + "bin/" + binary.name
    if desktop and actual_target.startswith("macos-"):
        root = "Joinery.app/Contents/Resources/joinery/"
        executable_name = "Joinery.app/Contents/MacOS/Godot"
    entries = {
        root + name: _Entry(path, os.access(path, os.X_OK))
        for name, path in _source_files().items()
    }
    entries[executable_name] = _Entry(binary, True)
    if actual_target.startswith("windows-"):
        console = binary.with_name(binary.stem + ".console.exe")
        if console.is_file():
            entries[root + "bin/" + console.name] = _Entry(console, True)
        for runtime in windows_runtime_files(binary):
            entries[root + "bin/" + runtime.name] = _Entry(runtime)
        for notice in windows_runtime_notices(binary):
            entries[root + "licenses/" + notice.name] = _Entry(notice)
    if desktop and actual_target.startswith("macos-"):
        entries["Joinery.app/Contents/MacOS/Joinery"] = _Entry(
            _launcher(binary.name, macos=True), True
        )
        entries["Joinery.app/Contents/Info.plist"] = _Entry(
            plistlib.dumps(
                {
                    "CFBundleName": "Joinery",
                    "CFBundleDisplayName": "Joinery Alpha",
                    "CFBundleIdentifier": "org.joinery.editor",
                    "CFBundleExecutable": "Joinery",
                    "CFBundlePackageType": "APPL",
                    "CFBundleShortVersionString": "0.1.0",
                    "CFBundleVersion": "1",
                    "NSHighResolutionCapable": True,
                    "LSMinimumSystemVersion": "13.0",
                    "NSPrincipalClass": "NSApplication",
                }
            )
        )
    elif desktop and actual_target.startswith("windows-"):
        launcher = (
            '@echo off\r\nsetlocal\r\nset "JOINERY_HOME=%~dp0"\r\nset "UV_NO_DEV=1"\r\nset "UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\\Joinery\\python-'
            + __version__
            + '"\r\nwhere uv >nul 2>nul\r\nif errorlevel 1 (\r\n echo Joinery needs uv. Install it from astral.sh/uv, then reopen Joinery.\r\n pause\r\n exit /b 1\r\n)\r\nif "%~1"=="" (\r\n "%~dp0bin\\'
            + binary.name
            + '" --project-manager\r\n) else (\r\n "%~dp0bin\\'
            + binary.name
            + '" %*\r\n)\r\n'
        )
        entries[root + "Joinery.cmd"] = _Entry(launcher.encode())
    elif desktop:
        entries[root + "Joinery"] = _Entry(_launcher(binary.name), True)
    manifest = {
        name: hashlib.sha256(entry.read()).hexdigest()
        for name, entry in sorted(entries.items())
    }
    metadata = {
        "joinery": __version__,
        "engine": version,
        "target": actual_target,
        "addon": addon_version(),
        "files": manifest,
    }
    entries[root + "manifest.json"] = _Entry(
        json.dumps(metadata, sort_keys=True, indent=2).encode()
    )
    _archive(output, entries)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + ".sha256").write_text(
        digest + "  " + output.name + "\n"
    )
    return {
        "path": str(output),
        "sha256": digest,
        "joinery": __version__,
        "target": actual_target,
        "engine": version,
        "files": len(manifest),
    }


def launch(project: Path | None) -> dict[str, Any]:
    binary = engine_binary()
    env = dict(os.environ, JOINERY_HOME=str(ROOT))
    if project is not None:
        from joinery.bootstrap import update_addon

        update_addon(project.resolve())
        args = [str(binary), "--editor", "--path", str(project.resolve())]
    else:
        args = [str(binary), "--project-manager"]
    child = subprocess.Popen(
        args,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {
        "started": True,
        "pid": child.pid,
        "mode": "editor" if project else "project-manager",
    }
