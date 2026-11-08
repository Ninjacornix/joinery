"""Export a portable technical GLB fixture and run each platform's native game.

The geometry is ordinary greybox scene scaffolding, not finished artwork. No
user game or art approval is modified by these release checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from joinery.catalog import ROOT
from joinery.distribution import binary_target, engine_binary

TEMPLATE_NAME = "Godot_v4.7.2-stable_export_templates.tpz"
TEMPLATE_SHA512 = "ca4d71c4d7b81dfc15d1a98baa07534aa95b03fdda78a0075b06672e1648d2e5f40980c9adc28d23e1b92e732ee7bf3461997aa804af74ec2fcd7a93ccb84079"
TEMPLATE_URL = (
    "https://github.com/godotengine/godot-builds/releases/download/4.7.2-stable/"
    + TEMPLATE_NAME
)
DESKTOP_TEMPLATES = {
    "linux_release.x86_64",
    "windows_release_x86_64.exe",
    "windows_release_x86_64_console.exe",
    "macos.zip",
    "version.txt",
}
EXPORTS = {
    "linux": ("Linux", "Linux", "linux.x86_64", "linux_release.x86_64"),
    "windows": (
        "Windows",
        "Windows Desktop",
        "windows.exe",
        "windows_release_x86_64.exe",
    ),
    "macos": ("macOS", "macOS", "macos.zip", "macos.zip"),
}


def checked_run(argv: list[str], cwd: Path | None = None, timeout: int = 180) -> str:
    result = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )
    output = result.stdout + result.stderr
    if (
        result.returncode
        or "SCRIPT ERROR:" in output
        or "Parse Error:" in output
        or "ERROR:" in output
    ):
        raise RuntimeError(
            "Release check failed: " + " ".join(argv[:3]) + "\n" + output[-6000:]
        )
    return output


def templates(cache: Path) -> Path:
    """Download exact upstream code, verify its published digest, extract desktop files."""
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / TEMPLATE_NAME
    if not archive.is_file():
        temporary = archive.with_suffix(".download")
        try:
            urllib.request.urlretrieve(TEMPLATE_URL, temporary)
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    digest = hashlib.sha512()
    with archive.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != TEMPLATE_SHA512:
        raise ValueError(
            "Export template checksum does not match the pinned official release."
        )
    target = cache / "4.7.2.stable-desktop"
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        if package.read("templates/version.txt").decode().strip() != "4.7.2.stable":
            raise ValueError(
                "The export template version does not match the editor base."
            )
        for name in DESKTOP_TEMPLATES:
            destination = target / name
            data = package.read("templates/" + name)
            if (
                not destination.is_file()
                or hashlib.sha256(destination.read_bytes()).digest()
                != hashlib.sha256(data).digest()
            ):
                destination.write_bytes(data)
            if name != "version.txt":
                destination.chmod(0o755)
    return target


def fixture(project: Path, binary: Path) -> None:
    project.mkdir(parents=True)
    (project / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Joinery Export Test"\nrun/main_scene="res://main.tscn"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\ntextures/vram_compression/import_etc2_astc=true\ntextures/vram_compression/import_s3tc_bptc=true\n'
    )
    (project / "make_fixture.gd").write_text("""extends SceneTree
func _initialize() -> void:
    var root := Node3D.new()
    var mesh := MeshInstance3D.new()
    mesh.name = "FixtureMesh"
    mesh.mesh = BoxMesh.new()
    root.add_child(mesh)
    mesh.owner = root
    var doc := GLTFDocument.new()
    var state := GLTFState.new()
    var error := doc.append_from_scene(root, state)
    DirAccess.make_dir_recursive_absolute("res://art")
    if error == OK:
        error = doc.write_to_filesystem(state, "res://art/fixture.glb")
    root.free()
    quit(0 if error == OK else 1)
""")
    checked_run(
        [
            str(binary),
            "--headless",
            "--path",
            str(project),
            "--script",
            "make_fixture.gd",
        ]
    )
    (project / "main.gd").write_text("""extends Node3D
func _ready() -> void:
    var meshes := $Model.find_children("*", "MeshInstance3D", true, false)
    if meshes.is_empty() or meshes[0].mesh.get_surface_count() == 0:
        push_error("Portable GLB geometry did not survive export")
        get_tree().quit(1)
        return
    print("JOINERY_EXPORT_OK geometry=", meshes.size())
    get_tree().quit(0)
""")
    (project / "main.tscn").write_text(
        '[gd_scene load_steps=3 format=3]\n[ext_resource type="Script" path="res://main.gd" id="1"]\n[ext_resource type="PackedScene" path="res://art/fixture.glb" id="2"]\n[node name="ExportTest" type="Node3D"]\nscript=ExtResource("1")\n[node name="Model" parent="." instance=ExtResource("2")]\n'
    )
    (project / "make_fixture.gd").unlink()
    checked_run(
        [str(binary), "--headless", "--editor", "--path", str(project), "--import"]
    )


def presets(project: Path, directory: Path, selected: list[str]) -> None:
    rows = []
    for index, target in enumerate(selected):
        name, engine_platform, _, template = EXPORTS[target]
        options = [
            f"custom_template/release={json.dumps((directory / template).as_posix())}",
            'binary_format/architecture="universal"'
            if target == "macos"
            else 'binary_format/architecture="x86_64"',
            "binary_format/embed_pck=true",
        ]
        if target == "macos":
            options += [
                'application/bundle_identifier="org.joinery.export-test"',
                'application/short_version="0.1.0"',
                "codesign/codesign=1",
                "notarization/notarization=0",
            ]
        elif target == "windows":
            options += ["codesign/enable=false", "application/modify_resources=false"]
        rows += [
            f"[preset.{index}]",
            f"name={json.dumps(name)}",
            f"platform={json.dumps(engine_platform)}",
            "runnable=true",
            'export_filter="all_resources"',
            'include_filter=""',
            'exclude_filter="make_fixture.gd,addons/*,recipes/*,art/sources/*,joinery.toml,joinery.lock"',
            'export_path=""',
            "",
            f"[preset.{index}.options]",
            *options,
            "",
        ]
    (project / "export_presets.cfg").write_text("\n".join(rows))


def export_all(output: Path, cache: Path, selected: list[str]) -> dict[str, object]:
    binary = engine_binary()
    template_dir = templates(cache)
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "editor": checked_run([str(binary), "--version"]).strip(),
        "template_sha512": TEMPLATE_SHA512,
        "exports": {},
    }
    exported = {}
    with tempfile.TemporaryDirectory(prefix="joinery export fixture ") as temporary:
        project = Path(temporary) / "project"
        fixture(project, binary)
        presets(project, template_dir, selected)
        for target in selected:
            name, _, filename, _ = EXPORTS[target]
            destination = output / target / filename
            destination.parent.mkdir(exist_ok=True)
            log = checked_run(
                [
                    str(binary),
                    "--headless",
                    "--path",
                    str(project),
                    "--export-release",
                    name,
                    str(destination),
                ],
                timeout=300,
            )
            if not destination.is_file():
                raise RuntimeError(
                    "The exporter produced no executable: "
                    + target
                    + "\n"
                    + log[-2000:]
                )
            if target != "macos":
                assert binary_target(destination) == target + "-x86_64"
            else:
                with zipfile.ZipFile(destination) as archive:
                    if not any(
                        "/Contents/MacOS/" in name for name in archive.namelist()
                    ):
                        raise RuntimeError("macOS export has no application executable")
            exported[target] = {
                "path": str(destination),
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "size": destination.stat().st_size,
            }
    report["exports"] = exported
    (output / "exports.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def smoke(output: Path) -> None:
    if sys.platform == "darwin":
        archive_path = output / "macos/macos.zip"
        with tempfile.TemporaryDirectory(prefix="joinery exported game ") as temporary:
            root = Path(temporary).resolve()
            with zipfile.ZipFile(archive_path) as archive:
                for info in archive.infolist():
                    if not (root / info.filename).resolve().is_relative_to(root):
                        raise ValueError("Unsafe exported application path")
                    archive.extract(info, root)
                    path = root / info.filename
                    if (
                        not info.is_dir()
                        and stat.S_IFMT(info.external_attr >> 16) != stat.S_IFLNK
                    ):
                        path.chmod(info.external_attr >> 16 & 0o777 or 0o644)
            binary = next(root.glob("*.app/Contents/MacOS/*"))
            log = checked_run(
                [str(binary), "--headless", "--quit-after", "30"], cwd=root
            )
    else:
        target = "windows" if os.name == "nt" else "linux"
        binary = output / target / EXPORTS[target][2]
        binary.chmod(0o755)
        log = checked_run(
            [str(binary), "--headless", "--quit-after", "30"], cwd=binary.parent
        )
    if "JOINERY_EXPORT_OK geometry=" not in log:
        raise RuntimeError(
            "Exported runtime did not prove portable scene loading: " + log[-3000:]
        )
    print("Native exported game smoke test: PASS (" + platform.system() + ")")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "out/game-exports")
    parser.add_argument(
        "--cache", type=Path, default=Path.home() / ".cache/joinery/export-templates"
    )
    parser.add_argument("--targets", default="linux,windows,macos")
    parser.add_argument("--smoke-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if not args.smoke_only:
        selected = args.targets.split(",")
        if not selected or any(target not in EXPORTS for target in selected):
            raise ValueError("Targets must be linux,windows,macos.")
        print(json.dumps(export_all(output, args.cache, selected), indent=2))
    smoke(output)


if __name__ == "__main__":
    main()
