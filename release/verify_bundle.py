"""Validate the actual archive, then use its runtime from an isolated extraction."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from joinery import __version__
from joinery.distribution import binary_target


def extract(archive: Path, destination: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as package:
            for info in package.infolist():
                target = destination / info.filename
                if (
                    not target.resolve().is_relative_to(destination.resolve())
                    or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK
                ):
                    raise ValueError("Unsafe bundle member: " + info.filename)
                package.extract(info, destination)
                if not info.is_dir():
                    target.chmod(info.external_attr >> 16 & 0o777 or 0o644)
    else:
        with tarfile.open(archive) as package:
            package.extractall(destination, filter="data")


def verify(archive: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="joinery clean bundle ") as temporary:
        root = Path(temporary).resolve()
        extract(archive, root)
        manifests = list(root.rglob("manifest.json"))
        if len(manifests) != 1:
            raise ValueError("Expected one release file manifest.")
        manifest = json.loads(manifests[0].read_text())
        assert manifest["joinery"] == __version__
        for name, expected in manifest["files"].items():
            file = root / name
            if not file.resolve().is_relative_to(root) or not file.is_file():
                raise ValueError("Missing or unsafe manifest member: " + name)
            if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                raise ValueError("Bundle file hash mismatch: " + name)
        shipped = {
            p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
        }
        if shipped != set(manifest["files"]) | {
            manifests[0].relative_to(root).as_posix()
        }:
            raise ValueError("The bundle contains unmeasured files.")
        if any(
            part in {".private", ".venv", ".joinery", ".DS_Store", ".env"}
            for name in shipped
            for part in Path(name).parts
        ):
            raise ValueError("Private development state entered the release.")
        runtime = manifests[0].parent
        assert (runtime / "licenses/GODOT-LICENSE.txt").is_file()
        assert (runtime / "licenses/GODOT-COPYRIGHT.txt").is_file()
        if manifest["target"].startswith("macos-") and "Joinery.app" in runtime.parts:
            binary = runtime.parent.parent / "MacOS/Godot"
            launcher = runtime.parent.parent / "MacOS/Joinery"
        else:
            binary = next(
                p
                for p in (runtime / "bin").glob("godot.*.editor.*")
                if not p.name.endswith(".console.exe")
            )
            launcher = runtime / ("Joinery.cmd" if os.name == "nt" else "Joinery")
        assert binary_target(binary) == manifest["target"]
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "JOINERY_GODOT_BIN",
                "JOINERY_HOME",
                "UV_PROJECT_ENVIRONMENT",
                "UV_NO_DEV",
            }
        }
        env["UV_PROJECT_ENVIRONMENT"] = str(root / "private-python")
        env["UV_NO_DEV"] = "1"
        commands = [
            [str(binary), "--version"],
            [
                "uv",
                "run",
                "--frozen",
                "--project",
                str(runtime),
                "joinery",
                "--version",
            ],
            [
                "uv",
                "run",
                "--frozen",
                "--project",
                str(runtime),
                "joinery",
                "new",
                str(root / "fresh project"),
                "--name",
                "Alpha Bundle Check",
                "--tools",
                "godot,blender",
            ],
            [
                "uv",
                "run",
                "--frozen",
                "--project",
                str(runtime),
                "joinery",
                "update-addon",
                "--project",
                str(root / "fresh project"),
            ],
            [
                "uv",
                "run",
                "--frozen",
                "--project",
                str(runtime),
                "joinery",
                "check",
                "all",
            ],
        ]
        for argv in commands:
            result = subprocess.run(
                argv,
                cwd=runtime,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode:
                raise RuntimeError(
                    "Extracted bundle command failed:\n"
                    + result.stdout[-2000:]
                    + result.stderr[-2000:]
                )
        assert (root / "fresh project/addons/joinery/plugin.gd").is_file()
        if launcher.is_file() and os.name != "nt":
            result = subprocess.run(
                [str(launcher), "--version"],
                cwd=root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert manifest["engine"] in result.stdout
        assert not (runtime / ".venv").exists()
        return {
            "archive": str(archive),
            "target": manifest["target"],
            "files": len(manifest["files"]),
            "clean_install": "passed",
            "native_version": manifest["engine"],
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    if shutil.which("uv") is None:
        raise ValueError("Install uv to exercise the extracted runtime.")
    print(json.dumps(verify(args.archive.resolve()), indent=2))


if __name__ == "__main__":
    main()
