"""GIMP 3 through Joinery's paint adapter, in the real offline worker.

Creates an .xcf with two named layers and a saved selection channel, inspects
it, edits only the height layer inside that channel, and checks pixels per
layer export. Every recipe replays byte-identically (.xcf and PNG), a failing
script is reported with its traceback and records nothing, and an unknown
layer name is refused by name. These are technical fixtures in a temporary
project; nothing here touches a user's game.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from joinery.authoring import IMAGE, edit_document, inspect_document, run_offline
from joinery.recipe import load_recipe, rebuild_one, recipe_paths
from joinery.runner import SandboxRunner
from joinery.tools.gimp import failure

SOURCE = "art/textures/rock.xcf"
COMPOSITE = "art/textures/rock.png"
HEIGHT = "art/textures/rock_height.png"
ALBEDO = "art/textures/rock_albedo.png"
EXPORTS = [COMPOSITE, HEIGHT, ALBEDO]

LAYER_EXPORTS = (
    f"layer_exports[{HEIGHT!r}] = 'height'\nlayer_exports[{ALBEDO!r}] = 'albedo'\n"
)
CREATE = (
    "image = Gimp.Image.new(64, 64, Gimp.ImageBaseType.RGB)\n"
    "for name, colour in (('albedo', '#ff0000'), ('height', '#808080')):\n"
    "    layer = Gimp.Layer.new(image, name, 64, 64, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)\n"
    "    image.insert_layer(layer, None, 0)\n"
    "    Gimp.context_set_foreground(Gegl.Color.new(colour))\n"
    "    layer.edit_fill(Gimp.FillType.FOREGROUND)\n"
    "image.select_rectangle(Gimp.ChannelOps.REPLACE, 0, 0, 32, 64)\n"
    "Gimp.Selection.save(image).set_name('left_half')\n"
    "Gimp.Selection.none(image)\n" + LAYER_EXPORTS
)
# Only the height layer, only inside the saved channel.
EDIT = (
    "image.select_item(Gimp.ChannelOps.REPLACE, find_channel(image, 'left_half'))\n"
    "Gimp.context_set_foreground(Gegl.Color.new('#ffffff'))\n"
    "find_layer(image, 'height').edit_fill(Gimp.FillType.FOREGROUND)\n"
    "Gimp.Selection.none(image)\n" + LAYER_EXPORTS
)
LEFT, RIGHT = (8, 8), (48, 8)
GREY, WHITE, RED = [128, 128, 128, 255], [255, 255, 255, 255], [255, 0, 0, 255]


async def pixels(stage: Path) -> dict[str, list[list[int]]]:
    """The left and right sample of each export, read in the worker."""
    code = (
        "from PyQt5.QtGui import QImage; import json; print(json.dumps({p: "
        "[list(QImage(p).pixelColor(x, y).getRgb()) for x, y in "
        + repr([LEFT, RIGHT])
        + "] for p in "
        + repr(EXPORTS)
        + "}))"
    )
    got = await run_offline(["python3", "-c", code], stage, readonly=True)
    assert got.ok, got.output
    found: dict[str, list[list[int]]] = json.loads(got.stdout.strip().splitlines()[-1])
    return found


def replay(stage: Path, path: Path) -> None:
    """Replay one recipe from scratch in a copy and require identical outputs."""
    rebuilt = rebuild_one(path, stage, SandboxRunner(image=IMAGE))
    assert rebuilt.ok, [failure(problem) for problem in rebuilt.problems]


async def main(stage: Path) -> dict[str, object]:
    (stage.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text('[tools]\nenabled=["gimp"]\n')
    (stage / "project.godot").write_text("config_version=5\n")

    await edit_document(stage, "gimp", SOURCE, CREATE, EXPORTS, "rock texture")
    info = await inspect_document(stage, "gimp", SOURCE)
    assert [layer["name"] for layer in info["layers"]] == ["height", "albedo"], info
    assert [channel["name"] for channel in info["channels"]] == ["left_half"], info
    assert info["size"] == [64, 64], info
    first = await pixels(stage)
    assert first == {COMPOSITE: [GREY, GREY], HEIGHT: [GREY, GREY], ALBEDO: [RED, RED]}
    (created,) = recipe_paths(stage)
    replay(stage, created)
    albedo_before = (stage / ALBEDO).read_bytes()

    await edit_document(stage, "gimp", SOURCE, EDIT, EXPORTS, "lighten left height")
    after = await inspect_document(stage, "gimp", SOURCE)
    assert [layer["name"] for layer in after["layers"]] == ["height", "albedo"]
    assert [channel["name"] for channel in after["channels"]] == ["left_half"]
    second = await pixels(stage)
    assert second == {
        COMPOSITE: [WHITE, GREY],
        HEIGHT: [WHITE, GREY],
        ALBEDO: [RED, RED],
    }, second
    # Only the targeted layer changed: the albedo export is the same file.
    assert (stage / ALBEDO).read_bytes() == albedo_before
    (edited,) = recipe_paths(stage)
    assert load_recipe(edited).sources, "The edit replays from its preimage"
    replay(stage, edited)

    recipes = sorted(p.name for p in (stage / "recipes").glob("*.toml"))
    try:
        await edit_document(
            stage, "gimp", SOURCE, "raise RuntimeError('JOINERY_FAIL_PROBE')", EXPORTS
        )
    except RuntimeError as error:
        assert "JOINERY_FAIL_PROBE" in str(error), error
    else:
        raise AssertionError("A failing GIMP script was recorded as a success.")
    try:
        await edit_document(
            stage,
            "gimp",
            SOURCE,
            f"layer_exports[{HEIGHT!r}] = 'heigth'",
            EXPORTS,
        )
    except RuntimeError as error:
        assert "No layer named 'heigth'" in str(error), error
    else:
        raise AssertionError("An unknown layer name was exported.")
    assert sorted(p.name for p in (stage / "recipes").glob("*.toml")) == recipes
    assert (stage / ALBEDO).read_bytes() == albedo_before
    return {
        "layers": [layer["name"] for layer in after["layers"]],
        "channels": [channel["name"] for channel in after["channels"]],
        "pixels": second,
        "replayed": 2,
        "fingerprints": load_recipe(edited).expect,
    }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="joinery-gimp-") as directory:
        stage = Path(directory) / "stage"
        stage.mkdir()
        print(json.dumps(asyncio.run(main(stage))))
        print("GIMP_NATIVE_OK")
        sys.exit(0)
