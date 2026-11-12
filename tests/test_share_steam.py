"""Steam store and library art: the policy's sizes, title layout, the Krita script, checks.

Krita runs in the offline worker; here it is a stub that writes PNGs of the
planned sizes, except in the last test, which runs the real worker when its
Docker image is on this computer.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Any

import pytest

from joinery import authoring, share, share_media, share_steam

TITLE = "Harbour Keeper"


def png(path: Path, width: int, height: int, rgba: tuple[int, int, int, int]) -> None:
    """A plain RGBA PNG of one colour."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + bytes(rgba) * width
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def project_with_shots(
    root: Path, name: str = TITLE, size: tuple[int, int] = (1280, 720)
) -> Path:
    project = root / "harbour"
    project.mkdir()
    (project / "project.godot").write_text(
        f'config_version=5\n\n[application]\n\nconfig/name="{name}"\n', encoding="utf-8"
    )
    files = []
    for index in range(1, 5):
        shot = project / f"share/media/screenshots/shot-{index:02d}.png"
        png(shot, size[0], size[1], (40 + index * 10, 60, 80, 255))
        files.append(shot.relative_to(project).as_posix())
    recipe = {
        "version": 1,
        "seed": 777,
        "screenshots": {"files": files, "cover_from": files[2]},
    }
    (project / "share/recipes").mkdir(parents=True)
    (project / "share/recipes/media.json").write_text(json.dumps(recipe))
    return project


# --- Policy ------------------------------------------------------------------------------


def test_policy_lists_every_steam_image_at_steamworks_sizes() -> None:
    policy = share_steam.load_policy()
    sizes = {a.id: (a.width, a.height) for a in policy.assets}
    assert sizes == {
        "header": (920, 430),
        "small": (462, 174),
        "main": (1232, 706),
        "vertical": (748, 896),
        "library_capsule": (600, 900),
        "library_hero": (3840, 1240),
        "library_logo": (1280, 360),
    }
    assert "Verify against Steamworks" in policy.note
    assert all(
        s.startswith("https://partner.steamgames.com/doc/") for s in policy.sources
    )
    by_id = {a.id: a for a in policy.assets}
    # Valve's 860x380 hero safe area on its 1920x620 template, doubled and centred.
    assert by_id["library_hero"].safe == (1060, 240, 2780, 1000)
    assert by_id["library_hero"].text == "none"
    assert by_id["library_logo"].transparent and by_id["library_logo"].text == "logo"
    # Whatever Joinery chose itself is marked as such.
    assert "safe_inset" in by_id["header"].assumed


# --- Layout ------------------------------------------------------------------------------


def test_titles_fit_their_safe_areas_and_the_hero_has_none() -> None:
    policy = share_steam.load_policy()
    for asset in policy.assets:
        place = share_steam.layout(asset, TITLE)
        if asset.text == "none":
            assert place is None
            continue
        assert place is not None
        x, y, w, h = place["box"]
        x0, y0, x1, y1 = asset.safe
        assert x0 - 1 <= x and x + w <= x1 + 1 and y0 - 1 <= y and y + h <= y1 + 1, (
            asset.id
        )


def test_a_long_name_takes_two_lines_on_the_small_capsule() -> None:
    small = next(a for a in share_steam.load_policy().assets if a.id == "small")
    place = share_steam.layout(small, TITLE)
    assert place is not None and place["lines"] == ["Harbour", "Keeper"]
    assert share_steam.layout(small, "Moss")["lines"] == ["Moss"]  # type: ignore[index]


def test_plan_varies_views_and_keeps_the_logo_and_hero_text_rules() -> None:
    policy = share_steam.load_policy()
    shots = [f"share/media/screenshots/shot-0{i}.png" for i in range(1, 5)]
    items = {
        i["id"]: i
        for i in share_steam.plan(policy, shots, shots[2], TITLE, "DejaVu Sans")
    }
    assert items["header"]["source"] == items["main"]["source"] == shots[2]
    assert (
        len({items[k]["source"] for k in ("header", "vertical", "library_hero")}) == 3
    )
    assert (
        items["library_logo"]["source"] is None
        and items["library_logo"]["plate"] is None
    )
    assert (
        items["library_hero"]["svg"] is None and items["library_hero"]["title"] is None
    )
    assert "stroke" in items["library_logo"]["svg"]
    with pytest.raises(share_media.MediaError):
        share_steam.plan(policy, [], "", TITLE, "DejaVu Sans")


def test_krita_script_composes_and_saves_every_image() -> None:
    policy = share_steam.load_policy()
    shots = ["share/media/screenshots/shot-01.png"]
    items = share_steam.plan(policy, shots, shots[0], "Moss & <Rain>", "DejaVu Sans")
    script = share_steam.krita_script(
        items, {shots[0]: "share/.work/steam/source-1.png"}
    )
    compile(script, "steam_art.py", "exec")
    for item in items:
        assert item["png"] in script and item["kra"] in script
    assert "&amp;" in script and "<Rain>" not in script
    assert "createVectorLayer" in script and "saveAs" in script


# --- Checks ------------------------------------------------------------------------------


def _render(
    project: Path, items: list[dict[str, Any]], plate: int, sized: bool = True
) -> None:
    for item in items:
        w, h = item["size"]
        alpha = 0 if item["transparent"] else 255
        png(project / item["png"], w if sized else w + 2, h, (30, 30, 30, alpha))
        if item.get("plate"):
            bw, bh = item["title"]["box"][2:]
            (project / item["plate"]).parent.mkdir(parents=True, exist_ok=True)
            (project / item["plate"]).write_bytes(
                bytes((plate, plate, plate, 255)) * bw * bh
            )


def _items(title: str = TITLE) -> list[dict[str, Any]]:
    shots = ["s.png"]
    return share_steam.plan(
        share_steam.load_policy(), shots, "s.png", title, "DejaVu Sans"
    )


def test_checks_pass_dark_art_and_name_every_problem(tmp_path: Path) -> None:
    policy = share_steam.load_policy()
    items = _items()
    _render(tmp_path, items, plate=25)
    results = {r["id"]: r for r in share_steam.check(tmp_path, policy, items, {})}
    assert all(r["status"] == "ok" for r in results.values()), results
    assert results["library_logo"]["facts"]["corner_alpha"] == [0, 0, 0, 0]
    assert results["library_hero"]["facts"]["text"] == "none"

    _render(tmp_path, items, plate=235, sized=False)
    results = {r["id"]: r for r in share_steam.check(tmp_path, policy, items, {})}
    header = results["header"]
    assert header["status"] == "failed"
    assert any("Steam asks for 920×430" in p for p in header["problems"])
    assert any("contrast" in p for p in header["problems"])


def test_an_opaque_logo_a_tiny_title_and_soft_art_are_reported(tmp_path: Path) -> None:
    policy = share_steam.load_policy()
    items = _items("The Very Long Name Of A Harbour Keeping Game")
    _render(tmp_path, items, plate=25)
    logo = next(i for i in items if i["id"] == "library_logo")
    png(tmp_path / logo["png"], 1280, 360, (30, 30, 30, 255))
    results = {
        r["id"]: r
        for r in share_steam.check(tmp_path, policy, items, {"library_hero": 3.0})
    }
    assert "Its background isn't transparent." in results["library_logo"]["problems"]
    assert results["small"]["status"] == "failed"
    assert any("A shorter name" in p for p in results["small"]["problems"])
    assert results["library_hero"]["status"] == "warning"
    assert "enlarged 3.0×" in results["library_hero"]["problems"][0]
    assert "at least 1920×1080" in results["library_hero"]["problems"][0]
    # 1920x1080 store screenshots fill the 3840 px hero at exactly 2x: not soft.
    sharp = {
        r["id"]: r
        for r in share_steam.check(tmp_path, policy, items, {"library_hero": 2.0})
    }
    assert sharp["library_hero"]["facts"]["upscaled"] == 2.0
    assert not any("enlarged" in p for p in sharp["library_hero"]["problems"])


def test_png_alpha_corners_reads_filtered_rows(tmp_path: Path) -> None:
    path = tmp_path / "a.png"
    png(path, 3, 2, (1, 2, 3, 0))
    assert share_steam.png_alpha_corners(path) == [0, 0, 0, 0]
    line = bytearray(b"\x01\x01\x01\x01\x02\x02\x02\x02")
    share_steam._unfilter(1, line, bytearray(8), 4)  # Sub: each byte adds its left one.
    assert bytes(line) == b"\x01\x01\x01\x01\x03\x03\x03\x03"


# --- Making the set ----------------------------------------------------------------------


def test_make_records_a_replayable_recipe_and_status_lists_the_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = project_with_shots(tmp_path)
    scripts: list[str] = []

    async def worker(where: Path, script: str, timeout: int = 1200) -> str:
        scripts.append(script)
        items = share_steam.plan(
            share_steam.load_policy(),
            [f"share/media/screenshots/shot-0{i}.png" for i in range(1, 5)],
            "share/media/screenshots/shot-03.png",
            TITLE,
            "DejaVu Sans",
        )
        _render(where, items, plate=20)
        for item in items:
            (where / item["kra"]).write_bytes(b"kra")
        return ""

    monkeypatch.setattr(share_steam, "_worker", worker)
    entry = asyncio.run(share_steam.make(project))
    assert "joinery-krita share/recipes/steam_art.py" in scripts[0]
    assert "dpkg-query" in scripts[0]  # The worker's Krita must be the pinned one.
    assert (project / "share/recipes/steam_art.py").is_file()
    statuses = {c["id"]: c["status"] for c in entry["checks"]}
    # 1280x720 screenshots are enlarged 3x for the 3840 px hero; the rest fit.
    assert statuses.pop("library_hero") == "warning"
    assert set(statuses.values()) == {"ok"}
    assert "share/media/steam/header.kra" in entry["outputs"]
    recipe = json.loads((project / "share/recipes/media.json").read_text())
    assert recipe["steam_art"]["policy"]["note"].startswith("Verify")
    assert recipe["screenshots"]["cover_from"].endswith("shot-03.png")
    assert "media/steam/*.png" in (project / "share/.gitignore").read_text()
    assert not (project / "share/.work/steam").exists()

    assert "1280×720" in entry["screenshots"]["warning"]
    listed = share.status(project)["steam"]
    assert listed["ready"] and len(listed["images"]) == 7
    assert "at least 1920×1080" in listed["screenshots_warning"]
    assert all(i["path"] and i["status"] in {"ok", "warning"} for i in listed["images"])


def test_1920x1080_screenshots_make_a_set_with_no_soft_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = project_with_shots(tmp_path, size=(1920, 1080))

    async def worker(where: Path, script: str, timeout: int = 1200) -> str:
        items = share_steam.plan(
            share_steam.load_policy(),
            [f"share/media/screenshots/shot-0{i}.png" for i in range(1, 5)],
            "share/media/screenshots/shot-03.png",
            TITLE,
            "DejaVu Sans",
        )
        _render(where, items, plate=20)
        for item in items:
            (where / item["kra"]).write_bytes(b"kra")
        return ""

    monkeypatch.setattr(share_steam, "_worker", worker)
    entry = asyncio.run(share_steam.make(project))
    statuses = {c["id"]: c["status"] for c in entry["checks"]}
    assert set(statuses.values()) == {"ok"}, entry["checks"]
    assert entry["screenshots"] == {"size": [1920, 1080], "warning": ""}
    assert share.status(project)["steam"]["screenshots_warning"] == ""


def test_steam_art_needs_screenshots_first(tmp_path: Path) -> None:
    project = tmp_path / "empty"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    with pytest.raises(share_media.MediaError, match="screenshots first"):
        asyncio.run(share_steam.make(project))
    assert share_steam.listing(project)["ready"] is False


def _worker_image() -> bool:
    if shutil.which("docker") is None:
        return False
    found = subprocess.run(
        ["docker", "image", "inspect", authoring.IMAGE],
        capture_output=True,
        check=False,
    )
    return found.returncode == 0


@pytest.mark.skipif(
    not _worker_image(), reason="the creative worker's image isn't built here"
)
def test_krita_in_the_worker_composes_a_set_that_passes_its_checks(
    tmp_path: Path,
) -> None:
    project = project_with_shots(tmp_path)
    # Screenshots with something to see: a sky, a floor and a lamp post.
    for index, shot in enumerate(
        sorted((project / "share/media/screenshots").glob("*.png"))
    ):
        rows = bytearray()
        for y in range(720):
            for x in range(1280):
                if 600 < x < 640 and 200 < y < 560:
                    rows += bytes((200, 160, 60, 255))
                elif y > 520:
                    rows += bytes((50 + index * 8, 45, 40, 255))
                else:
                    rows += bytes((40, 70, 110 + index * 10, 255))
        data = b"".join(
            b"\x00" + bytes(rows[y * 5120 : (y + 1) * 5120]) for y in range(720)
        )

        def chunk(kind: bytes, body: bytes) -> bytes:
            return (
                struct.pack(">I", len(body))
                + kind
                + body
                + struct.pack(">I", zlib.crc32(kind + body))
            )

        shot.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1280, 720, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(data))
            + chunk(b"IEND", b"")
        )
    entry = asyncio.run(share_steam.make(project))
    statuses = {c["id"]: c["status"] for c in entry["checks"]}
    assert all(s != "failed" for s in statuses.values()), entry["checks"]
    # 1280x720 screenshots are enlarged 3x for the 3840 px hero: said, not hidden.
    assert statuses["library_hero"] == "warning"
    for image in entry["images"]:
        assert (project / image["png"]).is_file() and (project / image["kra"]).is_file()
