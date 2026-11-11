"""Shared kits without Blender: what goes in, how it is checked, where it lands."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import stat
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import pytest

from joinery import kit_archive, kit_share_cli, kits, shared_kits
from joinery.catalog import load_catalog
from joinery.kit_library import project_kits
from joinery.lockfile import (
    CustomOutput,
    HumanImport,
    KitAsset,
    asset_files,
    check_assets,
    read_assets,
    record_imports,
    write_assets,
)
from joinery.locking import record_lock
from joinery.policy import user_only_violation
from joinery.recipe import load_recipe
from joinery.share_build import notices
from joinery.share_page import draft
from joinery.transactions import list_transactions

MARKER = kits.MARKER + json.dumps(
    {
        "kit": "rock",
        "name": "rocks",
        "params": {"moss": 0.5},
        "seed": 3,
        "colours": {},
        "folder": "art/environment",
        "models": [
            {
                "glb": "art/environment/rocks_01.glb",
                "blend": "art/sources/kits/rocks_01.blend",
            }
        ],
    }
)
SCRIPT = f"""# Made by Joinery's rock kit
{MARKER}
import bpy, os
os.chdir('/work')
MODELS = json.loads('[{{"blend": "art/sources/kits/rocks_01.blend", "glb": "art/environment/rocks_01.glb"}}]')
TEXTURE = "res://art/environment/rocks_01.glb"
NOT_MINE = "start/art/x"
"""
PREFAB = """[gd_scene load_steps=2 format=3 uid="uid://b1"]

[ext_resource type="PackedScene" uid="uid://c2" path="res://art/environment/rocks_01.glb" id="1_rock"]
[ext_resource type="Texture2D" path="res://art/textures/brick.png" id="2_brick"]

[node name="Harbour" type="Node3D"]

[node name="Rock" parent="." instance=ExtResource("1_rock")]
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(project: Path, rel: str, data: bytes | str) -> None:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())


def _recipe(
    project: Path,
    name: str,
    tool: str,
    script: str,
    outputs: dict[str, bytes],
    **extra: Any,
) -> None:
    _write(project, script, extra.pop("code", "# recipe\n"))
    for rel, data in outputs.items():
        _write(project, rel, data)
    rows = [
        "version = 1",
        f"tool = {json.dumps(tool)}",
        f"script = {json.dumps(script)}",
        f"outputs = {json.dumps(list(outputs))}",
    ]
    if extra.get("sources"):
        rows.append(f"sources = {json.dumps(extra['sources'])}")
    rows += ["", "[expect]"]
    rows += [f'"{rel}" = "sha256:{_sha(data)}"' for rel, data in outputs.items()]
    _write(project, f"recipes/{name}.toml", "\n".join(rows) + "\n")


@pytest.fixture(autouse=True)
def _author(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shared_kits, "author", lambda _project: "Ana Harbour")


def project_a(root: Path) -> Path:
    project = root / "a"
    _write(project, "project.godot", "config_version=5\n")
    _write(
        project,
        "joinery.toml",
        '[tools]\nenabled=["godot","blender","supercollider"]\n',
    )
    _write(project, "joinery.lock", "version = 1\n")
    _write(project, "LICENSE", "Creative Commons Attribution 4.0 International\n")
    _write(project, "recipes/inputs/abc.blend", b"BLENDER-base")
    _recipe(
        project,
        "r1",
        "blender",
        "recipes/r1.py",
        {
            "art/sources/kits/rocks_01.blend": b"BLENDER-rock",
            "art/environment/rocks_01.glb": b"glTF-rock",
        },
        code=SCRIPT,
        sources=["recipes/inputs/abc.blend"],
    )
    _recipe(
        project,
        "s1",
        "supercollider",
        "recipes/s1.scd",
        {
            "audio/sfx/splash.scd": b'score.recordNRT(outputFilePath:"audio/sfx/splash.wav");',
            "audio/sfx/splash.wav": b"RIFF-splash",
        },
        code='score.recordNRT(outputFilePath:"audio/sfx/splash.wav");\n',
    )
    _write(project, "art/textures/brick.png", b"PNG-brick")
    _write(project, "art/reference/look.png", b"PNG-look")
    _write(project, "art/props/odd.glb", b"glTF-odd")
    text, _problems = record_lock(project)
    (project / "joinery.lock").write_text(text)
    record_imports(
        project,
        [
            HumanImport(
                path="art/textures/brick.png",
                sha256=_sha(b"PNG-brick"),
                origin="human-import",
                imported_by="Ana",
                licence="CC0-1.0",
            ),
            HumanImport(
                path="art/reference/look.png",
                sha256=_sha(b"PNG-look"),
                origin="human-import",
                imported_by="Ana",
                licence=kit_archive.REFERENCE_ONLY,
            ),
        ],
    )
    write_assets(
        project,
        set(),
        [
            CustomOutput(
                path="art/props/odd.glb",
                sha256=_sha(b"glTF-odd"),
                origin="custom-tool",
                tool="mytool",
            )
        ],
    )
    _write(project, "scenes/harbour.tscn", PREFAB)
    return project


def project_b(root: Path, name: str = "b") -> Path:
    project = root / name
    _write(project, "project.godot", "config_version=5\n")
    _write(
        project,
        "joinery.toml",
        '[tools]\nenabled=["godot","blender","supercollider"]\n',
    )
    _write(project, "joinery.lock", "version = 1\n")
    return project


def make_kit(root: Path, project: Path, **extra: Any) -> Path:
    manifest, files = shared_kits.export_plan(
        project,
        extra.pop("name", "Harbour"),
        include=extra.pop("include", ["scenes/harbour.tscn", "audio/sfx/splash.wav"]),
        **extra,
    )
    out = root / f"{manifest.name}-{manifest.version}.joinerykit"
    shared_kits.export(project, out, manifest, files)
    return out


def cli(project: Path, *argv: str) -> tuple[int, dict[str, Any]]:
    parser = argparse.ArgumentParser()
    kit_share_cli.add_arguments(parser)
    args = parser.parse_args([*argv, "--project", str(project), "--json"])
    out = io.StringIO()
    import contextlib

    with contextlib.redirect_stdout(out):
        code = kit_share_cli.main(args)
    return code, json.loads(out.getvalue())


# --- export ---------------------------------------------------------------------------


def test_export_brings_everything_a_scene_needs_to_be_made_again(
    tmp_path: Path,
) -> None:
    project = project_a(tmp_path)
    manifest, files = shared_kits.export_plan(
        project, "Harbour", include=["res://scenes/harbour.tscn"]
    )
    rows = {c.path: c for c in manifest.contents}
    assert set(rows) == {
        "scenes/harbour.tscn",
        "art/environment/rocks_01.glb",
        "art/sources/kits/rocks_01.blend",
        "art/textures/brick.png",
        "recipes/r1.toml",
        "recipes/r1.py",
        "recipes/inputs/abc.blend",
    }
    assert rows["scenes/harbour.tscn"].kind == "scene"
    assert rows["art/environment/rocks_01.glb"].origin == "tool-created"
    assert rows["art/environment/rocks_01.glb"].recipe == "recipes/r1.toml"
    assert rows["art/sources/kits/rocks_01.blend"].tool == "blender"
    assert rows["art/textures/brick.png"].origin == "human-import"
    assert rows["art/textures/brick.png"].licence == "CC0-1.0"
    assert rows["recipes/inputs/abc.blend"].kind == "input"
    assert rows["recipes/r1.py"].kind == "script"
    for content in manifest.contents:
        assert content.sha256 == _sha(files[content.path])
        assert content.size == len(files[content.path])
    assert manifest.author == "Ana Harbour"
    assert manifest.licence == "CC-BY-4.0"  # the game's licence by default


@pytest.mark.parametrize(
    ("include", "message"),
    [
        (["art/reference/look.png"], "reference image"),
        (["art/props/odd.glb"], "custom tool"),
        (["art/nothing.glb"], "Nothing in the project matches"),
        (["../outside.glb"], "not a path inside the project"),
        (["joinery.toml"], "not an asset with a joinery.lock entry"),
    ],
)
def test_export_refuses_what_rule_zero_does_not_allow(
    tmp_path: Path, include: list[str], message: str
) -> None:
    project = project_a(tmp_path)
    with pytest.raises(ValueError, match=message):
        shared_kits.export_plan(project, "Harbour", include=include)


def test_export_refuses_unlocked_assets_and_game_code(tmp_path: Path) -> None:
    project = project_a(tmp_path)
    _write(project, "art/props/loose.glb", b"glTF-loose")
    with pytest.raises(ValueError, match="no joinery.lock entry"):
        shared_kits.export_plan(project, "Harbour", include=["art/props/loose.glb"])
    _write(
        project,
        "scenes/scripted.tscn",
        '[ext_resource type="Script" path="res://scenes/door.gd" id="1"]\n',
    )
    _write(project, "scenes/door.gd", "extends Node\n")
    with pytest.raises(ValueError, match="no game code"):
        shared_kits.export_plan(project, "Harbour", include=["scenes/scripted.tscn"])


def test_custom_tool_assets_go_only_when_ticked_and_stay_marked(tmp_path: Path) -> None:
    project = project_a(tmp_path)
    manifest, _files = shared_kits.export_plan(
        project, "Odd", include=["art/props/odd.glb"], allow_custom=True
    )
    (content,) = manifest.contents
    assert content.custom and content.origin == "custom-tool"
    shared = shared_kits.describe(manifest)
    assert shared["custom"] == 1


def test_manifest_writes_the_same_and_reads_back(tmp_path: Path) -> None:
    project = project_a(tmp_path)
    manifest, files = shared_kits.export_plan(
        project,
        "Harbour",
        include=["scenes/harbour.tscn"],
        generators=["rock"],
        presets=['Harbour rock=rock:{"moss": 0.8, "size": 2.0}'],
        licence="CC-BY-4.0",
        description="Rocks and a splash.",
    )
    assert kit_archive.manifest_text(manifest) == kit_archive.manifest_text(manifest)
    out = tmp_path / "harbour.joinerykit"
    kit_archive.write(out, manifest, files)
    read = kit_archive.read(out)
    assert read.manifest == manifest
    assert read.files == files
    names = [p.name for p in manifest.presets]
    assert names == ["Harbour rock", "Rocks"]  # given, then the set's own values
    assert manifest.presets[1].params == {"moss": 0.5}
    assert manifest.generators[0].file == "generators/rock.py"
    assert files["generators/rock.py"] == kits.get("rock").source.encode()


def test_export_cli_plans_without_writing(tmp_path: Path) -> None:
    project = project_a(tmp_path)
    code, data = cli(
        project,
        "export",
        "--name",
        "Harbour",
        "--include",
        "scenes/harbour.tscn",
        "--plan",
    )
    assert code == 0, data
    assert data["counts"]["asset"] == 3 and data["size"] > 0
    assert data["holds"] == "1 model, 1 picture and 1 scene"
    assert not list(tmp_path.glob("*.joinerykit"))
    code, data = cli(
        project, "export", "--name", "X", "--include", "art/reference/look.png"
    )
    assert code == 1 and "reference" in data["error"]


# --- reading a kit safely ------------------------------------------------------------


def _zip(path: Path, entries: list[tuple[str, bytes, int]]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return path


def _tiny(
    tmp_path: Path, path: str = "art/a.png", data: bytes = b"x", **extra: Any
) -> str:
    content = kit_archive.Content(
        path=path,
        kind=extra.pop("kind", "asset"),
        sha256=_sha(data),
        size=len(data),
        origin=extra.pop("origin", "human-import"),
        licence="CC0-1.0",
        **extra,
    )
    manifest = kit_archive.Manifest(
        name="Tiny", version="1", author="Ana", licence="CC0-1.0", joinery="0.5.0",
        contents=(content,),
    )  # fmt: skip
    return kit_archive.manifest_text(manifest)


REGULAR = stat.S_IFREG | 0o644


def test_a_changed_file_is_refused(tmp_path: Path) -> None:
    project = project_a(tmp_path)
    good = make_kit(tmp_path, project)
    bad = tmp_path / "bad.joinerykit"
    with zipfile.ZipFile(good) as source, zipfile.ZipFile(bad, "w") as target:
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "files/art/environment/rocks_01.glb":
                data = b"glTF-evil"
            target.writestr(info, data)
    with pytest.raises(ValueError, match="doesn't match its SHA-256"):
        kit_archive.read(bad)


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([("files/../evil.png", b"x", REGULAR)], "safe path"),
        ([("/etc/evil.png", b"x", REGULAR)], "outside the kit"),
        ([("files/art/link.png", b"/etc/passwd", stat.S_IFLNK | 0o777)], "links"),
        ([("files/art/a.png", b"x", stat.S_IFREG | 0o755)], "executable"),
        ([("files/art/a.png", b"x", REGULAR), ("files/art/b.png", b"y", REGULAR)], "not in its manifest"),
        ([("files/art/a.png", b"\0" * (3 << 20), REGULAR)], "inflates too far"),
        ([], "is in the manifest but not in the kit"),
    ],
)  # fmt: skip
def test_unsafe_zips_are_refused(
    tmp_path: Path, entries: list[tuple[str, bytes, int]], message: str
) -> None:
    manifest = _tiny(tmp_path)
    path = _zip(
        tmp_path / "unsafe.joinerykit",
        [("kit.toml", manifest.encode(), REGULAR), *entries],
    )
    with pytest.raises(ValueError, match=message):
        kit_archive.read(path)


@pytest.mark.parametrize(
    ("path", "extra", "message"),
    [
        ("scenes/door.gd", {"kind": "resource", "origin": ""}, "no game code"),
        ("art/run.sh", {}, "can't carry"),
        ("art/tool.py", {}, "can't carry"),
        ("recipes/x.exe", {"kind": "script", "origin": ""}, "can't carry"),
        ("art/a.glb", {"origin": "tool-created"}, "no recipe"),
        ("art/a.glb", {"origin": "custom-tool"}, "not marked"),
    ],
)
def test_manifest_rules(
    tmp_path: Path, path: str, extra: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _tiny(tmp_path, path, **extra)


def test_a_generator_with_code_in_its_spec_is_refused() -> None:
    with pytest.raises(ValueError, match="not plain data"):
        kit_archive.read_spec(
            "SPEC = __import__('os').system('x')\ndef build(): pass\n"
        )
    with pytest.raises(ValueError, match="no build"):
        kit_archive.read_spec(
            kits.get("rock").source.replace("def build(", "def make(")
        )


# --- import ---------------------------------------------------------------------------


def test_import_is_one_reviewed_change_with_paths_moved(tmp_path: Path) -> None:
    project = project_a(tmp_path)
    kit = make_kit(tmp_path, project)
    target = project_b(tmp_path)
    code, data = cli(target, "import", str(kit), "--plan")
    assert code == 0, data
    assert data["folder"] == "kits/harbour" and data["update"] is None
    assert not (target / "kits").exists()
    code, data = cli(target, "import", str(kit))
    assert code == 0 and data["state"] == "review", data
    assert not (target / "kits").exists()  # nothing changes before accept
    (tx,) = list_transactions(target)
    tx.accept()
    folder = target / "kits/harbour"
    assert (folder / "art/environment/rocks_01.glb").read_bytes() == b"glTF-rock"
    scene = (folder / "scenes/harbour.tscn").read_text()
    assert 'path="res://kits/harbour/art/environment/rocks_01.glb"' in scene
    assert "uid://" not in scene
    recipe = load_recipe(target / "recipes/harbour__r1.toml")
    assert recipe.script == "recipes/harbour__r1.py"
    assert recipe.outputs == (
        "kits/harbour/art/sources/kits/rocks_01.blend",
        "kits/harbour/art/environment/rocks_01.glb",
    )
    assert recipe.sources == ("kits/harbour/recipes/inputs/abc.blend",)
    assert set(recipe.expect) == set(recipe.outputs)
    script = (target / "recipes/harbour__r1.py").read_text()
    assert '"glb": "kits/harbour/art/environment/rocks_01.glb"' in script
    assert "res://kits/harbour/art/environment/rocks_01.glb" in script
    assert "start/art/x" in script  # not a path of the kit
    assert kits.MARKER not in script  # Kit settings stay with the project that made it
    sound = load_recipe(target / "recipes/harbour__s1.toml")
    assert "kits/harbour/audio/sfx/splash.wav" in sound.outputs
    patch = (target / "kits/harbour/audio/sfx/splash.scd").read_bytes()
    assert b'outputFilePath:"kits/harbour/audio/sfx/splash.wav"' in patch
    assert sound.expect["kits/harbour/audio/sfx/splash.scd"] == "sha256:" + _sha(patch)
    entries = {e.path: e for e in read_assets(target)}
    glb = entries["kits/harbour/art/environment/rocks_01.glb"]
    assert isinstance(glb, KitAsset)
    assert (glb.kit, glb.kit_version, glb.kit_author, glb.licence) == (
        "Harbour",
        "1.0.0",
        "Ana Harbour",
        "CC-BY-4.0",
    )
    assert glb.made_as == "tool-created" and glb.recipe == "recipes/harbour__r1.toml"
    brick = entries["kits/harbour/art/textures/brick.png"]
    assert isinstance(brick, KitAsset) and brick.made_as == "human-import"
    assert brick.asset_licence == "CC0-1.0" and brick.recipe is None
    assert "kits/harbour/art/environment/rocks_01.glb" in asset_files(target)
    assert check_assets(target, load_catalog()) == []
    assert (folder / "art/sources/kits/.gdignore").is_file()
    assert (folder / "art/environment/rocks_01.glb.import").is_file()
    # Making the lock again keeps the kit's entries the kit's.
    text, problems = record_lock(target)
    assert (
        problems == []
        and 'origin = "kit"' in text
        and 'origin = "tool-created"' not in text
    )


def test_same_names_get_their_own_namespace(tmp_path: Path) -> None:
    project = project_a(tmp_path)
    first = make_kit(tmp_path, project)
    other = make_kit(
        tmp_path, project, name="Harbour!", include=["audio/sfx/splash.wav"]
    )
    target = project_b(tmp_path)
    assert cli(target, "import", str(first), "--yes")[0] == 0
    code, data = cli(target, "import", str(other), "--yes")
    assert code == 0, data
    assert data["folder"] == "kits/harbour_2"
    assert (target / "recipes/harbour_2__s1.toml").is_file()
    assert check_assets(target, load_catalog()) == []
    code, data = cli(target, "import", str(other), "--plan", "--into", "kits/../x")
    assert code == 0  # the same kit again is an update, in its own folder
    assert data["folder"] == "kits/harbour_2" and data["update"]["from"] == "1.0.0"


def test_into_must_be_a_free_folder_of_kits(tmp_path: Path) -> None:
    project = project_a(tmp_path)
    kit = make_kit(tmp_path, project)
    target = project_b(tmp_path)
    for into in ("art/x", "kits/../../etc", "kits/a/b"):
        code, data = cli(target, "import", str(kit), "--plan", "--into", into)
        assert code == 1 and "folder of kits/" in data["error"], into
    code, data = cli(target, "import", str(kit), "--plan", "--into", "kits/props")
    assert code == 0 and data["folder"] == "kits/props"


def test_imported_generators_are_kits_the_agents_can_use(tmp_path: Path) -> None:
    project = project_a(tmp_path)
    kit = make_kit(
        tmp_path,
        project,
        generators=["rock"],
        presets=['Harbour rock=rock:{"moss": 0.8}'],
    )
    target = project_b(tmp_path)
    assert cli(target, "import", str(kit), "--yes")[0] == 0
    found = project_kits(target)
    assert list(found) == ["harbour_rock"]
    rock = kits.get("harbour_rock", target)
    assert rock.credit == "Harbour 1.0.0 by Ana Harbour, CC-BY-4.0"
    assert rock.label == "Harbour rock"  # not a second "Rock" in the Kits list
    assert rock.limits is None  # the generator's code never runs on the host
    assert [p["name"] for p in rock.presets] == ["Harbour rock", "Rocks"]
    listed = {k["id"]: k for k in kits.describe(target)["kits"]}
    assert listed["harbour_rock"]["from"] == rock.credit
    made = kits.plan(target, "harbour_rock", {"moss": 0.8}, name="harbour_rocks")
    assert "from Harbour 1.0.0 by Ana Harbour" in kits.script(made)
    assert "harbour_rock" not in kits.every(None)
    generator = target / "kits/harbour/generators/rock.py"
    generator.write_text(generator.read_text() + "\n# changed\n")
    assert "harbour_rock" not in project_kits(target)  # changed since import


def test_a_newer_version_updates_in_place_and_shows_what_changed(
    tmp_path: Path,
) -> None:
    project = project_a(tmp_path)
    first = make_kit(tmp_path, project)
    target = project_b(tmp_path)
    assert cli(target, "import", str(first), "--yes")[0] == 0
    newer = make_kit(
        tmp_path, project, include=["scenes/harbour.tscn"], version="1.1.0"
    )
    code, data = cli(target, "import", str(newer), "--plan")
    assert code == 0, data
    assert data["update"] == {"from": "1.0.0", "to": "1.1.0"}
    changes = {row["path"]: row["change"] for row in data["changes"]}
    assert changes["kits/harbour/audio/sfx/splash.wav"] == "removed"
    assert changes["kits/harbour/art/environment/rocks_01.glb"] == "unchanged"
    code, data = cli(target, "import", str(newer), "--yes")
    assert code == 0 and data["summary"].startswith("Update the Harbour kit"), data
    assert not (target / "kits/harbour/audio").exists()
    assert not (target / "recipes/harbour__s1.toml").exists()
    assert "kits/harbour/audio/sfx/splash.wav" not in {
        e.path for e in read_assets(target)
    }
    assert check_assets(target, load_catalog()) == []


def test_remove_is_refused_while_a_scene_uses_the_kit(tmp_path: Path) -> None:
    project = project_a(tmp_path)
    kit = make_kit(tmp_path, project)
    target = project_b(tmp_path)
    assert cli(target, "import", str(kit), "--yes")[0] == 0
    level = '[ext_resource type="PackedScene" path="res://kits/harbour/scenes/harbour.tscn" id="1"]\n'
    _write(target, "scenes/level.tscn", level)
    code, data = cli(target, "list")
    assert data["installed"][0]["used_by"] == ["scenes/level.tscn"]
    code, data = cli(target, "remove", "--name", "Harbour")
    assert code == 1 and "scenes/level.tscn" in data["error"]
    (target / "scenes/level.tscn").unlink()
    code, data = cli(target, "remove", "--name", "Harbour", "--yes")
    assert code == 0, data
    assert not (target / "kits/harbour").exists()
    assert not list((target / "recipes").glob("harbour__*"))
    assert read_assets(target) == []
    assert check_assets(target, load_catalog()) == []


def test_listing_shows_installed_kits_and_what_could_be_shared(tmp_path: Path) -> None:
    project = project_a(tmp_path)
    data = shared_kits.listing(project)
    assets = {a["path"]: a for a in data["assets"]}
    assert assets["art/reference/look.png"]["refused"] == "a reference image"
    assert assets["art/props/odd.glb"]["custom"] is True
    assert data["scenes"] == ["scenes/harbour.tscn"]
    assert data["licence"] == "CC-BY-4.0" and data["author"] == "Ana Harbour"
    assert "rock" in {g["id"] for g in data["generators"]}


def test_share_notes_and_credits_name_each_kit(tmp_path: Path) -> None:
    project = project_a(tmp_path)
    kit = make_kit(tmp_path, project, description="Rocks and a splash.")
    target = project_b(tmp_path)
    assert cli(target, "import", str(kit), "--yes")[0] == 0
    text = notices(target, "web")["LICENSES/Kits.txt"].decode()
    assert "Harbour 1.0.0 by Ana Harbour" in text
    assert "Licence: CC-BY-4.0" in text
    assert "kits/harbour/art/textures/brick.png: CC0-1.0" in text
    assert "The Harbour kit 1.0.0 by Ana Harbour: CC-BY-4.0." in draft(target)


def test_agents_cannot_import_export_or_remove_kits() -> None:
    for command in (
        "uv run joinery kit import --project . harbour.joinerykit",
        "joinery kit export --project . --name x --out x.joinerykit",
        "cd game && uv run joinery kit remove --name Harbour --yes",
        "python -m joinery.cli kit import x.joinerykit",
    ):
        verdict = user_only_violation(command)
        assert verdict is not None and "Kits section" in verdict.reason, command
    for command in ("joinery kit list --json", "joinery kits make --kit rock"):
        assert user_only_violation(command) is None, command


def test_installed_record_reads_back(tmp_path: Path) -> None:
    project = project_a(tmp_path)
    kit = make_kit(tmp_path, project)
    target = project_b(tmp_path)
    assert cli(target, "import", str(kit), "--yes")[0] == 0
    raw = tomllib.loads((target / "kits/harbour/installed.toml").read_text())
    assert raw["folder"] == "kits/harbour" and raw["slug"] == "harbour"
    assert (target / "kits/harbour/kit.toml").read_bytes() == kit_archive.read(kit).raw
