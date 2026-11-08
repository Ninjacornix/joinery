"""Verified ANGLE libraries preserve Compatibility rendering in macOS VMs.

The version/layout follows Godot 4.7.2's misc/scripts/install_angle.py.
The archive was downloaded and measured; its SHA-256 matches the publisher's
GitHub release digest. The exact upstream BSD license ships in licenses/.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from joinery.catalog import ROOT
from release.windows_build_deps import Archive, digest, extract, verified_archive

ANGLE = Archive(
    "godot-angle-static-arm64-macos-release.zip",
    "https://github.com/godotengine/godot-angle-static/releases/download/chromium/7219/godot-angle-static-arm64-macos-release.zip",
    "9d85adda5a7459e3ae30d6c33c56beac091908f156034af63845efe005f06012",
    "angle-arm64-macos",
)
LICENSE_SHA256 = "bf4da21bd20bcfb5b60b7ecc67fa864a79be049e21d6178076887f178dd6c71a"


def provision(output: Path, cache: Path, arch: str) -> list[str]:
    if arch != "arm64":
        raise ValueError("The macOS alpha ANGLE pin currently supports ARM64 only.")
    license_path = ROOT / "licenses/ANGLE-LICENSE.txt"
    if digest(license_path) != LICENSE_SHA256:
        raise ValueError("The ANGLE license does not match the pinned upstream text.")
    source = verified_archive(ANGLE, cache)
    if output.exists() and not (output / "sdk-manifest.json").is_file():
        raise ValueError("Refusing to replace an unrecognized macOS SDK directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".macos-sdk-", dir=output.parent) as temporary:
        prepared = Path(temporary) / "deps"
        prepared.mkdir()
        extract(ANGLE, source, prepared / ANGLE.directory)
        (prepared / "sdk-manifest.json").write_text(
            json.dumps(
                {
                    ANGLE.filename: {"url": ANGLE.url, "sha256": ANGLE.sha256},
                    "ANGLE-LICENSE.txt": {"sha256": LICENSE_SHA256},
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        if output.exists():
            shutil.rmtree(output)
        prepared.replace(output)
    return ["angle=yes", "angle_libs=" + str(output / "angle")]
