"""Provision isolated software OpenGL for Windows CI, never a shipped dependency.

The third-party Mesa build is pinned to the publisher's release asset digest and
independently measured DLL hashes. 26.2.6 MSVC is deliberately avoided: its
release notes report a regression and its archive lacks the OpenGL DLLs.

Sources:
https://github.com/pal1000/mesa-dist-win/releases/tag/26.2.6
https://api.github.com/repos/pal1000/mesa-dist-win/releases/tags/26.2.6
https://docs.mesa3d.org/drivers/llvmpipe.html

Provisioning is not a graphical test. Run the native editor harness separately,
without --headless, and retain renderer logs and checked framebuffer captures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from joinery.distribution import binary_target, windows_runtime_files

VERSION = "26.2.6"
ARCHIVE_NAME = f"mesa3d-{VERSION}-release-mingw.7z"
ARCHIVE_URL = (
    f"https://github.com/pal1000/mesa-dist-win/releases/download/{VERSION}/"
    + ARCHIVE_NAME
)
ARCHIVE_SHA256 = "da0f71d9181b672c1918f3c342f11118c687f6059a46b16502845160733cc388"
# These x64 DLLs import only each other and Windows/UCRT DLLs. LLVM is static.
DLL_SHA256 = {
    "opengl32.dll": "281cf35807658dad1b9ec42f621b3b710a7d02da2057a6f8db3e5d0df333d788",
    "libgallium_wgl.dll": "240a95c244906d19ada0b78fa949589e14417bcea2bfa8034be0320da323530c",
}


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verified_archive(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / ARCHIVE_NAME
    if not archive.is_file():
        # Unique temporary file avoids exposing incomplete cache entries.
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as partial:
            temporary = Path(partial.name)
        try:
            urllib.request.urlretrieve(ARCHIVE_URL, temporary)
            if sha256(temporary) != ARCHIVE_SHA256:
                raise ValueError(
                    "Mesa release checksum does not match the pinned digest"
                )
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    if sha256(archive) != ARCHIVE_SHA256:
        raise ValueError("Mesa cache checksum does not match the pinned digest")
    return archive


def provision(engine: Path, output: Path, cache: Path) -> dict[str, str]:
    """Copy one native x64 executable and two verified DLLs to a fresh folder."""
    engine = engine.resolve(strict=True)
    output = output.resolve()
    if binary_target(engine) != "windows-x86_64":
        raise ValueError("Software OpenGL requires a native Windows x86_64 executable")
    if output.exists():
        raise ValueError("Use a fresh private output directory for CI software OpenGL")
    # Preserve only validated runtime siblings from the original engine build.
    # The native executable can import D3D12 even when CI selects OpenGL.
    runtime_files = windows_runtime_files(engine)
    extractor = shutil.which("7z") or shutil.which("7zz")
    if not extractor:
        raise ValueError("7-Zip is required to extract the verified Mesa release")
    archive = verified_archive(cache)
    with tempfile.TemporaryDirectory(prefix="joinery-mesa-extract-") as directory:
        extracted = Path(directory)
        subprocess.run(
            [
                extractor,
                "x",
                "-y",
                f"-o{extracted}",
                str(archive),
                *("x64/" + name for name in DLL_SHA256),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        for name, expected in DLL_SHA256.items():
            dll = extracted / "x64" / name
            if not dll.is_file() or sha256(dll) != expected:
                raise ValueError(f"Mesa DLL content does not match its pin: {name}")
            if binary_target(dll) != "windows-x86_64":
                raise ValueError(
                    f"Mesa DLL architecture does not match the engine: {name}"
                )
        output.mkdir(parents=True)
        target = output / engine.name
        shutil.copy2(engine, target)
        for runtime in runtime_files:
            shutil.copy2(runtime, output / runtime.name)
        for name in DLL_SHA256:
            shutil.copy2(extracted / "x64" / name, output / name)
    environment = {
        "JOINERY_GODOT_BIN": str(target),
        "JOINERY_RENDERING_DRIVER": "opengl3",
        "GALLIUM_DRIVER": "llvmpipe",
    }
    (output / "mesa-ci.json").write_text(
        json.dumps(
            {
                "mesa": VERSION,
                "source": ARCHIVE_URL,
                "sha256": ARCHIVE_SHA256,
                "dll_sha256": DLL_SHA256,
                "engine_runtime_sha256": {
                    path.name: sha256(path) for path in runtime_files
                },
                "environment": environment,
                "validation": "provisioned; native graphical harness must run separately",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return environment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument(
        "--github-env", type=Path, help="Append environment for later CI steps"
    )
    args = parser.parse_args()
    environment = provision(args.engine, args.output, args.cache)
    if args.github_env:
        with args.github_env.open("a", encoding="utf-8") as destination:
            for key, value in environment.items():
                if "\n" in value or "\r" in value:
                    raise ValueError("CI environment paths cannot contain line breaks")
                destination.write(f"{key}={value}\n")
    print(json.dumps(environment, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
