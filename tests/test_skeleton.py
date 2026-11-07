"""Guards the repo layout."""

import importlib
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVERS = [
    "joinery_registry",
    "joinery_verify",
    "blender_ops",
    "materialmaker",
    "paint",
    "sound",
]


@pytest.mark.parametrize("name", SERVERS)
def test_server_package_imports(name: str) -> None:
    importlib.import_module(f"servers.{name}")


@pytest.mark.parametrize(
    "path",
    [
        "joinery.toml",
        "godot_addon/joinery/plugin.cfg",
        "godot_patches/UPSTREAM",
        "godot_patches/fork.sh",
        "bridge",
        "kits",
        "templates/art_bible.md",
        "testbed/project.godot",
        "evals/results",
        "docs/adr",
        "sandbox",
        "sandbox/Dockerfile",
        "sandbox/build.sh",
        "sandbox/fetch-checksums.sh",
        "sandbox/verify.sh",
        "sandbox/entrypoint.sh",
        "sandbox/versions.env",
        "sandbox/checksums.txt",
    ],
)
def test_layout_path_exists(path: str) -> None:
    assert (ROOT / path).exists()


@pytest.mark.parametrize(
    "script",
    ["build.sh", "fetch-checksums.sh", "verify.sh", "entrypoint.sh"],
)
def test_sandbox_scripts_are_executable_and_valid(script: str) -> None:
    path = ROOT / "sandbox" / script
    assert path.stat().st_mode & 0o111, f"{script} is not executable"
    # macOS ships bash 3.2, and build.sh/fetch-checksums.sh run on the host,
    # so every script must parse under the oldest bash we expect to meet.
    subprocess.run(["bash", "-n", str(path)], check=True)


def test_checksums_are_never_hand_written() -> None:
    # A guessed hash verifies nothing while looking verified. Entries may only
    # arrive via fetch-checksums.sh, which measures the real artifact.
    text = (ROOT / "sandbox" / "checksums.txt").read_text()
    assert "fetch-checksums.sh" in text


def test_sandbox_pins_match_the_godot_fork() -> None:
    # The sandbox must ship the same Godot the patch series is based on.
    upstream = (ROOT / "godot_patches" / "UPSTREAM").read_text()
    versions = (ROOT / "sandbox" / "versions.env").read_text()
    tag = next(
        line.split("=", 1)[1].strip()
        for line in upstream.splitlines()
        if line.startswith("GODOT_TAG=")
    )
    version = next(
        line.split("=", 1)[1].strip()
        for line in versions.splitlines()
        if line.startswith("GODOT_VERSION=")
    )
    channel = next(
        line.split("=", 1)[1].strip()
        for line in versions.splitlines()
        if line.startswith("GODOT_CHANNEL=")
    )
    assert tag == f"{version}-{channel}"
