"""Self-contained source/engine bundles with a measured file manifest."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from joinery.bootstrap import addon_version
from joinery.catalog import ROOT


def engine_binary() -> Path:
    override = os.environ.get("JOINERY_GODOT_BIN")
    if override and Path(override).is_file():
        return Path(override).resolve()
    for folder in (ROOT / "bin", ROOT / "godot_patches/godot-src/bin"):
        for path in sorted(folder.glob("godot.*.editor.*")):
            if path.is_file() and os.access(path, os.X_OK):
                return path
    raise ValueError(
        "Build the Joinery editor with godot_patches/fork.sh build, or set JOINERY_GODOT_BIN."
    )


def package(output: Path) -> dict[str, Any]:
    output = output.resolve()
    binary = engine_binary()
    files: dict[str, Path] = {}
    folders = [
        "joinery",
        "bridge",
        "servers",
        "policy",
        "templates",
        "godot_addon",
        "godot_patches/patches",
        "sandbox",
        "docs",
        "tests",
        "evals",
        "testbed",
    ]
    for folder in folders:
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and not {
                "__pycache__",
                ".DS_Store",
                ".pytest_cache",
                ".godot",
                ".joinery",
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
        "CLAUDE.md",
        ".gitattributes",
        "godot_patches/UPSTREAM",
        "godot_patches/fork.sh",
    ]:
        files[relative] = ROOT / relative
    files["bin/" + binary.name] = binary
    manifest = {
        relative: hashlib.sha256(path.read_bytes()).hexdigest()
        for relative, path in sorted(files.items())
    }
    version = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    metadata = json.dumps(
        {"engine": version, "addon": addon_version(), "files": manifest},
        sort_keys=True,
        indent=2,
    ).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        output.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for relative, path in sorted(files.items()):
            data = path.read_bytes()
            info = tarfile.TarInfo("joinery/" + relative)
            info.size = len(data)
            info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
            archive.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo("joinery/manifest.json")
        info.size = len(metadata)
        archive.addfile(info, io.BytesIO(metadata))
    return {
        "path": str(output),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "engine": version,
        "files": len(files),
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
