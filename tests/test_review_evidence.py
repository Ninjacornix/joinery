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


def test_a_texture_target_adds_a_texture_view_before_the_full_model(
    tmp_path: Path, monkeypatch
) -> None:
    from joinery import review
    from joinery.runner import Result

    (tmp_path / "project.godot").write_text("config_version=5\n")
    for path in ("art/sources/hammer.blend", "art/textures/hammer/steel_rough.png"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(b"before")
    tx = FileTransaction(tmp_path)
    tx.begin("Make the steel rougher")
    for path in ("art/sources/hammer.blend", "art/textures/hammer/steel_rough.png"):
        (tx.stage / path).write_bytes(b"after")
    scripts: list[str] = []

    async def render(argv: list[str], root: Path, *_args: object) -> Result:
        scripts.append(argv[-1])
        output = argv[-1].split("\n", 1)[0].split("=", 1)[1].strip("'")
        target = root / output.removeprefix("/work/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"png")
        return Result(True, "", "", 0.0)

    monkeypatch.setattr(review, "run_offline", render)
    target = {
        "id": "res://art/models/hammer.glb#material=Steel&image=steel_rough",
        "label": "Roughness · Steel",
        "part_kind": "texture",
        "owner_source": "art/sources/hammer.blend",
        "material": "Steel",
        "image": "steel_rough",
        "object": "Head.001",
    }
    changes = [
        {"path": "art/sources/hammer.blend"},
        {"path": "art/textures/hammer/steel_rough.png"},
    ]
    media = asyncio.run(evidence(tx, changes, [target]))
    # Map before/after, then the map on the model, then the full model.
    assert [m["path"] for m in media] == [
        "art/textures/hammer/steel_rough.png",
        "art/sources/hammer.blend",
        "art/sources/hammer.blend",
    ]
    assert media[0]["before"].endswith("steel_rough.png")
    assert media[1]["part"] == target["id"] and media[1]["label"] == "Roughness · Steel"
    assert "part" not in media[2]
    part_scripts = [s for s in scripts if "color_type = 'TEXTURE'" in s]
    assert len(part_scripts) == 2, "Before and after each get the texture view"
    assert "IMAGE='steel_rough'" in part_scripts[0]
    assert "OBJECTS=['Head.001']" in part_scripts[0]
    assert "tree.nodes.active = chosen" in part_scripts[0]
    full = [s for s in scripts if "color_type = 'MATERIAL'" in s]
    assert len(full) == 2


def test_object_targets_and_other_sources_add_no_texture_view(
    tmp_path: Path, monkeypatch
) -> None:
    from joinery.review import _parts_in

    targets = [
        {
            "id": "a",
            "part_kind": "object",
            "object": "Head",
            "owner_source": "art/sources/h.blend",
        },
        {
            "id": "b",
            "part_kind": "material",
            "material": "Steel",
            "owner_source": "art/sources/x.blend",
        },
        {
            "id": "c",
            "part_kind": "material",
            "material": "Steel",
            "source": "res://art/sources/h.blend",
        },
    ]
    assert [p["id"] for p in _parts_in("art/sources/h.blend", targets)] == ["c"]


def test_a_full_model_is_shown_from_two_angles(tmp_path: Path, monkeypatch) -> None:
    from joinery import review
    from joinery.runner import Result

    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "art/sources").mkdir(parents=True)
    (tmp_path / "art/sources/door.blend").write_bytes(b"before")
    tx = FileTransaction(tmp_path)
    tx.begin("Make the door wider")
    (tx.stage / "art/sources/door.blend").write_bytes(b"after")

    async def render(argv: list[str], root: Path, *_args: object) -> Result:
        output = argv[-1].split("\n", 1)[0].split("=", 1)[1].strip("\x27")
        target = root / output.removeprefix("/work/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"png")
        assert "BACK_SUFFIX" in argv[-1]
        target.with_name(target.stem + review.BACK_SUFFIX).write_bytes(b"png")
        return Result(True, "", "", 0.0)

    monkeypatch.setattr(review, "run_offline", render)
    media = asyncio.run(evidence(tx, [{"path": "art/sources/door.blend"}]))
    item = media[0]
    for side in ("before", "after"):
        assert item[side + "_back"].endswith("-back.png")
        assert Path(item[side + "_back"]).is_file()
