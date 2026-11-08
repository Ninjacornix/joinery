"""Reviews show the game's own art and audio, not Joinery's bookkeeping files."""

import asyncio
from pathlib import Path

from joinery.review import evidence
from joinery.transactions import FileTransaction


def test_recipe_snapshots_are_not_reviewed_as_assets(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    tx = FileTransaction(tmp_path)
    tx.begin("Make the handle longer")
    for path in ("art/textures/hammer/Head.png", "recipes/inputs/abc.png"):
        (tx.stage / path).parent.mkdir(parents=True, exist_ok=True)
        (tx.stage / path).write_bytes(b"image")
    changes = [
        {"path": "art/textures/hammer/Head.png"},
        {"path": "recipes/inputs/abc.png"},
        {"path": ".joinery/previews/x.png"},
    ]
    media = asyncio.run(evidence(tx, changes))
    assert [item["path"] for item in media] == ["art/textures/hammer/Head.png"]
