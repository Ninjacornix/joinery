"""Guards the repo layout."""

import importlib
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
    ],
)
def test_layout_path_exists(path: str) -> None:
    assert (ROOT / path).exists()
