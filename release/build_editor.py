"""Native release build from the exact upstream base plus the committed patches."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from joinery.catalog import ROOT
from joinery.distribution import binary_target
from joinery.platform_support import bash_script_argv


def command(argv: list[str], cwd: Path | None = None) -> None:
    subprocess.run(argv, cwd=cwd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-env", type=Path)
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 2, 4))
    args = parser.parse_args()
    source = ROOT / "godot_patches/godot-src"
    if not source.is_dir():
        command(bash_script_argv([str(ROOT / "godot_patches/fork.sh"), "sync"]))
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if dirty.strip():
        raise ValueError(
            "The engine checkout contains local edits; refusing a release build."
        )
    system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linuxbsd")
    arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x86_64"
    cache = Path(
        os.environ.get("JOINERY_SCONS_CACHE", str(Path.home() / ".cache/joinery/scons"))
    )
    cache.mkdir(parents=True, exist_ok=True)
    argv = [
        "uv",
        "run",
        "--frozen",
        "--project",
        str(ROOT),
        "scons",
        "target=editor",
        "platform=" + system,
        "arch=" + arch,
        "dev_build=no",
        "debug_symbols=no",
        "lto=none",
        "cache_path=" + str(cache),
        "cache_limit=3",
        "-j" + str(args.jobs),
    ]
    if system == "macos":
        from release.macos_build_deps import provision as provision_macos

        argv += ["vulkan=no", "macos_deployment_target=13.0"]
        argv += provision_macos(
            ROOT / "out/macos-build-deps",
            Path.home() / ".cache/joinery/macos-build-deps",
            arch,
        )
    elif system == "windows":
        from release.windows_build_deps import provision

        if arch != "x86_64":
            raise ValueError("The Windows release SDK pins currently support x86_64 only.")
        argv += provision(
            ROOT / "out/windows-build-deps",
            Path.home() / ".cache/joinery/windows-build-deps",
        )
    command(argv, source)
    suffix = ".exe" if system == "windows" else ""
    binary = source / "bin" / f"godot.{system}.editor.{arch}{suffix}"
    if not binary.is_file():
        raise ValueError(
            "The native build did not produce the expected editor: " + str(binary)
        )
    if system == "windows":
        from release.windows_build_deps import install_runtime_notices

        if not all(
            (binary.parent / name).is_file()
            for name in ("D3D12Core.dll", "d3d12SDKLayers.dll")
        ):
            raise ValueError("The Windows D3D12 build did not produce its required runtime DLLs.")
        install_runtime_notices(binary.parent, ROOT / "out/windows-build-deps")
    version = subprocess.run(
        [str(binary), "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    metadata = {
        "path": str(binary),
        "target": binary_target(binary),
        "engine": version,
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "source_tree": subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=source,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
    }
    if system == "windows":
        metadata["build_dependencies"] = json.loads(
            (ROOT / "out/windows-build-deps/sdk-manifest.json").read_text()
        )
    elif system == "macos":
        metadata["build_dependencies"] = json.loads(
            (ROOT / "out/macos-build-deps/sdk-manifest.json").read_text()
        )
    output = ROOT / "out/build-engine.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(metadata, indent=2) + "\n")
    if args.github_env:
        with args.github_env.open("a") as env:
            env.write(
                "JOINERY_GODOT_BIN="
                + str(binary)
                + "\nJOINERY_RELEASE_ENGINE="
                + str(binary)
                + "\n"
            )
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
