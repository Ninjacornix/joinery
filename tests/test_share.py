"""Share your game: presets, zips and notices, sizes, page wording, media choices, templates.

Nothing here touches the network or a real itch.io account: downloads go through
fake openers and butler is a recording stub.
"""

from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path
from typing import Any

import pytest

from joinery import (
    export_templates,
    playtest,
    share,
    share_build,
    share_media,
    share_page,
)
from joinery.godot_config import GodotConfig, parse_string

PROJECT = """config_version=5

[application]

config/name="Moss Run"
run/main_scene="res://main.tscn"

[input]

jump={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":32,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}
"""

BRIEF = """# Game brief — Moss Run

## 1. Pitch
Roll a moss ball through a garden before the rain.

It grows as it rolls.

## 4. Player fantasy
Who the player gets to be, and what they get to feel.

_Not decided yet._

## 5. Core loop
- 30 s: roll, collect moss, dodge snails.

## 6. Player verbs
| Verb | Input action | Feedback |
|---|---|---|
| Jump | `jump` | Hops over a snail |
"""


def make_project(root: Path, *, brief: str = BRIEF) -> Path:
    project = root / "moss"
    project.mkdir()
    (project / "project.godot").write_text(PROJECT, encoding="utf-8")
    (project / "joinery.toml").write_text(
        '[tools]\nenabled = ["godot"]\n\n[game]\nblueprint = "platformer-2d"\n',
        encoding="utf-8",
    )
    (project / "design").mkdir()
    (project / "design/brief.md").write_text(brief, encoding="utf-8")
    return project


# --- Presets and settings -------------------------------------------------------------


def test_presets_are_added_for_every_target_and_repair_is_idempotent() -> None:
    text, changes = share_build.ensure_presets(
        "", list(share_build.TARGETS), "Moss Run"
    )
    config = GodotConfig.parse(text)
    platforms = {
        parse_string(config.get(s, "platform") or "")
        for s in config.sections()
        if s.count(".") == 1
    }
    assert platforms == {"Web", "Windows Desktop", "macOS", "Linux"}
    assert len(changes) == 4
    assert config.get("preset.0.options", "variant/thread_support") == "false"
    assert "addons/joinery/*" in (config.get("preset.0", "exclude_filter") or "")
    assert config.get("preset.2.options", "application/bundle_identifier") == (
        '"org.joinery.game.moss-run"'
    )
    again, more = share_build.ensure_presets(
        text, list(share_build.TARGETS), "Moss Run"
    )
    assert again == text and more == []


def test_repair_fills_gaps_but_keeps_the_users_choices() -> None:
    mine = (
        '[preset.0]\n\nname="My web"\nplatform="Web"\nexclude_filter="secret/*"\n\n'
        "[preset.0.options]\n\nvariant/thread_support=true\n"
    )
    text, changes = share_build.ensure_presets(mine, ["web"], "Moss Run")
    config = GodotConfig.parse(text)
    assert config.get("preset.0", "name") == '"My web"'
    assert config.get("preset.0.options", "variant/thread_support") == "true"
    assert (
        config.get("preset.0.options", "vram_texture_compression/for_desktop") == "true"
    )
    assert (config.get("preset.0", "exclude_filter") or "").startswith('"secret/*,')
    assert len(changes) == 2
    assert share_build.threads_on(text)
    assert share_build.TARGETS["web"].templates(True) == ["web_release.zip"]


def test_settings_force_compatibility_on_the_web_and_etc2_for_macs() -> None:
    text = '[rendering]\n\nrenderer/rendering_method.web="forward_plus"\n'
    out, changes = share_build.ensure_settings(text, ["web", "macos"])
    config = GodotConfig.parse(out)
    assert (
        config.get("rendering", "renderer/rendering_method.web") == '"gl_compatibility"'
    )
    assert (
        config.get("rendering", "textures/vram_compression/import_etc2_astc") == "true"
    )
    assert len(changes) == 2
    untouched, none = share_build.ensure_settings(PROJECT, ["web"])
    assert untouched == PROJECT and none == []


# --- Zips, notices and sizes ----------------------------------------------------------


def test_zip_carries_readme_licences_and_exec_bits(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / "LICENSE").write_text("All rights reserved.\n")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "Moss Run.x86_64").write_bytes(b"\x7fELF")
    extra = share_build.notices(project, "linux")
    extra["README.txt"] = share_build.readme(project, "linux", list(extra)).encode()
    archive = tmp_path / "moss-linux.zip"
    share_build.make_zip(staging, "linux", archive, extra)
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        mode = bundle.getinfo("Moss Run.x86_64").external_attr >> 16
        readme = bundle.read("README.txt").decode()
    assert {
        "LICENSE.txt",
        "LICENSES/Godot-LICENSE.txt",
        "LICENSES/Godot-third-party-notices.txt",
        "LICENSES/Starting-point-MIT.txt",
        "README.txt",
    } <= names
    assert "LICENSES/ANGLE-LICENSE.txt" not in names
    assert mode & stat.S_IXUSR
    assert "Jump: Space" in readme and "chmod +x" in readme
    assert "ANGLE" in "".join(share_build.notices(project, "windows"))


def test_macos_zip_entries_keep_their_attributes(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    inner = zipfile.ZipInfo("Moss Run.app/Contents/MacOS/Moss Run")
    inner.external_attr = (stat.S_IFREG | 0o755) << 16
    with zipfile.ZipFile(staging / "Moss Run.zip", "w") as source:
        source.writestr(inner, b"binary")
    archive = tmp_path / "moss-macos.zip"
    share_build.make_zip(staging, "macos", archive, {"README.txt": b"hi"})
    with zipfile.ZipFile(archive) as bundle:
        info = bundle.getinfo("Moss Run.app/Contents/MacOS/Moss Run")
        assert info.external_attr >> 16 & 0o777 == 0o755
        assert "Moss Run.zip" not in bundle.namelist()


def test_sizes_warn_against_itch_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "web.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("index.html", "x" * 2000)
        bundle.writestr("index.wasm", "y" * 10)
    assert share_build.sizes(archive, "web")["warnings"] == []
    monkeypatch.setattr(share_build, "WEB_MAX_FILE", 1000)
    monkeypatch.setattr(share_build, "WEB_MAX_FILES", 1)
    monkeypatch.setattr(share_build, "WEB_MAX_TOTAL", 1500)
    found = share_build.sizes(archive, "web")
    assert found["files"] == 2 and found["unpacked"] == 2010
    assert len(found["warnings"]) == 3
    assert share_build.sizes(archive, "linux")["warnings"] == []


def test_names_and_targets() -> None:
    assert share_build.file_name('Moss: "Run"!') == "Moss Run"
    assert share_build.slug("Moss Run 2") == "moss-run-2"
    assert share_build.parse_targets("linux, web") == ["web", "linux"]
    with pytest.raises(ValueError):
        share_build.parse_targets("android")


# --- Templates ------------------------------------------------------------------------


def _fake_pin(tmp_path: Path) -> tuple[export_templates.Pin, bytes]:
    files = {"version.txt": b"4.7.2.stable\n", "linux_release.x86_64": b"\x7fELF fake"}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in files.items():
            bundle.writestr("templates/" + name, data)
    archive = buffer.getvalue()
    pin = export_templates.Pin(
        version="4.7.2.stable",
        url="https://example.org/templates.tpz",
        sha512=hashlib.sha512(archive).hexdigest(),
        size=len(archive),
        files={n: hashlib.sha256(d).hexdigest() for n, d in files.items()},
    )
    return pin, archive


def test_templates_are_never_downloaded_unasked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    pin, archive = _fake_pin(tmp_path)
    cache = tmp_path / "cache"
    with pytest.raises(export_templates.TemplatesMissing):
        export_templates.prepare(["linux_release.x86_64"], cache=cache, pin=pin)
    assert not export_templates.available(["linux_release.x86_64"], cache, pin)

    def opener(url: str, timeout: float) -> io.BytesIO:
        assert url == pin.url
        return io.BytesIO(archive)

    folder = export_templates.prepare(
        ["linux_release.x86_64"],
        cache=cache,
        pin=pin,
        allow_download=True,
        opener=opener,
    )
    assert (folder / "linux_release.x86_64").read_bytes() == b"\x7fELF fake"
    assert export_templates.missing(["linux_release.x86_64"], cache, pin) == []
    assert export_templates.environment(cache)["HOME"] == str(cache / "home")


def test_templates_with_the_wrong_hash_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    pin, _ = _fake_pin(tmp_path)

    def opener(url: str, timeout: float) -> io.BytesIO:
        return io.BytesIO(b"not the release")

    with pytest.raises(export_templates.PinnedDownloadError):
        export_templates.prepare(
            ["linux_release.x86_64"],
            cache=tmp_path / "cache",
            pin=pin,
            allow_download=True,
            opener=opener,
        )
    assert not (tmp_path / "cache" / pin.archive_name).exists()


def test_policy_pins_every_template_file_a_target_needs() -> None:
    pin = export_templates.load_pin()
    needed = {f for t in share_build.TARGETS.values() for f in t.templates()}
    needed |= set(share_build.TARGETS["web"].templates(threads=True))
    assert needed <= set(pin.files)
    assert all(len(v) == 64 for v in pin.files.values())
    assert pin.url.startswith("https://github.com/godotengine/")


# --- Page -----------------------------------------------------------------------------


def test_page_draft_uses_only_what_the_project_says(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    result = share_page.write(project)
    text = result["text"]
    assert result["created"] and (project / "share/page.md").is_file()
    assert text.startswith("<!--") and "# Moss Run" in text
    assert "Roll a moss ball through a garden before the rain." in text
    assert "It grows as it rolls." in text and "Who the player gets to be" not in text
    assert "| Jump | Space |" in text
    assert "- Jump: hops over a snail" in text
    assert "_Not decided yet._" not in text
    assert result["suggestions"] == []
    (project / "share/page.md").write_text("Mine.\n")
    assert share_page.write(project)["text"] == "Mine.\n"
    assert share_page.write(project, redraft=True)["text"] != "Mine.\n"


def test_page_lint_names_filler_from_the_brief(tmp_path: Path) -> None:
    project = make_project(
        tmp_path,
        brief=BRIEF.replace("before the rain", "in an immersive, stunning garden"),
    )
    result = share_page.write(project)
    assert len(result["suggestions"]) == 1
    assert "immersive" in result["suggestions"][0]


def test_page_without_a_brief_marks_gaps(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / "design/brief.md").unlink()
    result = share_page.write(project)
    assert result["gaps"] >= 2


# --- Media ----------------------------------------------------------------------------


def _thumb(value: int, stripes: int = 0) -> bytes:
    width, height = share_media.THUMB
    return bytes(
        (value + (60 if stripes and (x // stripes) % 2 else 0)) % 256
        for _ in range(height)
        for x in range(width)
    )


def test_pick_drops_blank_frames_and_keeps_varied_views() -> None:
    thumbs = {
        100: _thumb(0),  # blank: dropped
        200: _thumb(10, 2),
        210: _thumb(11, 2),  # same view as 200
        300: _thumb(120, 4),
        400: _thumb(200, 8),
        500: _thumb(60, 3),
        600: _thumb(160, 5),
    }
    chosen = share_media.pick(thumbs)
    assert 100 not in chosen
    assert chosen == sorted(chosen)
    assert 4 <= len(chosen) <= share_media.MAX_SHOTS
    assert not {200, 210} <= set(chosen) or len(chosen) == share_media.MIN_SHOTS
    assert share_media.pick({1: _thumb(5)}) == []


def test_candidates_skip_the_first_moments_and_the_end() -> None:
    report = {
        "played_seconds": 40,
        "moments": [
            {"label": "start", "frame": 60},
            {"label": "goal", "frame": 900},
            {"label": "end", "frame": 2400},
        ],
    }
    frames = share_media.candidates(report)
    assert 60 not in frames and 900 in frames and 2400 not in frames
    assert all(f >= 90 for f in frames)


def test_trailer_is_silent_unless_all_audio_is_tool_made(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    assert share_media.game_audio(project)[0] is False
    sound = project / "audio/sfx/hop.wav"
    sound.parent.mkdir(parents=True)
    sound.write_bytes(b"RIFF")
    assert share_media.game_audio(project) == (
        False,
        "Silent: some of the game's audio isn't tool-made.",
    )
    digest = hashlib.sha256(b"RIFF").hexdigest()
    (project / "joinery.lock").write_text(
        'version = 1\n\n[[asset]]\npath = "audio/sfx/hop.wav"\n'
        f'sha256 = "{digest}"\norigin = "tool-created"\ntool = "surge-xt"\n'
        'recipe = "recipes/hop.json"\n'
    )
    assert share_media.game_audio(project)[0] is True


def test_encode_argv_has_three_outputs_and_no_audio_when_silent() -> None:
    argv = share_media.encode_argv(26.0, False, "/fonts/f.ttf")
    assert argv.count("-an") == 3
    assert [a for a in argv if a.startswith("share/media/trailer.")] == [
        "share/media/trailer.webm",
        "share/media/trailer.mp4",
        "share/media/trailer.ogv",
    ]
    graph = argv[argv.index("-filter_complex") + 1]
    assert (
        "concat=n=3:v=1:a=0" in graph
        and "textfile=/work/share/.work/title.txt" in graph
    )
    loud = share_media.encode_argv(26.0, True, "/fonts/f.ttf")
    assert "-an" not in loud and "libopus" in loud and "anullsrc" in " ".join(loud)
    assert 20 <= share_media.TITLE_SECONDS + 26 + share_media.END_SECONDS <= 40


def test_store_media_is_1920x1080_and_the_preview_stays_small() -> None:
    assert share_media.STORE_SIZE == (1920, 1080)
    assert share_media.SHOT_RESOLUTION == "1920x1080"
    argv = share_media.encode_argv(26.0, False, "/fonts/f.ttf")
    graph = argv[argv.index("-filter_complex") + 1]
    assert "scale=1920:1080" in graph and "scale=640:360[v3]" in graph
    assert "scale=1280" not in graph
    cards = [a for a in argv if a.startswith("color=")]
    assert len(cards) == 2 and all("s=1920x1080" in c for c in cards)


def test_small_screenshots_are_said_never_enlarged(tmp_path: Path) -> None:
    assert share_media.store_size_note([(1920, 1080), (2560, 1440)]) == ""
    note = share_media.store_size_note([(1920, 1080), (1280, 720)])
    assert "1280×720" in note and "at least 1920×1080" in note
    shot = tmp_path / "shot.png"
    shot.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        + (1920).to_bytes(4, "big")
        + (1080).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
    )
    assert share_media.png_size(shot) == (1920, 1080)
    assert share_media.png_size(tmp_path / "missing.png") == (0, 0)


def test_the_trailer_recording_size_is_read_from_its_header(tmp_path: Path) -> None:
    fields = [33333, 0, 0, 0, 900, 0, 1, 0, 1920, 1080]
    header = b"".join(v.to_bytes(4, "little") for v in fields)
    movie = tmp_path / "trailer.avi"
    movie.write_bytes(
        b"RIFF\x00\x00\x00\x00AVI LIST\x00\x00\x00\x00hdrlavih"
        + (56).to_bytes(4, "little")
        + header
        + bytes(16)
    )
    assert share_media._avi_frames(movie) == 900
    assert share_media._avi_size(movie) == (1920, 1080)


def test_store_screenshots_are_replayed_at_full_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "game"
    project.mkdir()
    seen: dict[str, Any] = {}

    def fake_run(options: playtest.Options) -> dict[str, Any]:
        return {"played_seconds": 40.0, "moments": []}

    def fake_shots(
        options: playtest.Options,
        frames: list[int],
        out: Path,
        window: Any,
        resolution: str = "",
        full_size: bool = False,
    ) -> dict[int, Path]:
        seen.update(resolution=resolution, full_size=full_size)
        out.mkdir(parents=True, exist_ok=True)
        found = {}
        for frame in frames:
            path = out / f"shot_{frame:06d}.png"
            path.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                + (1280).to_bytes(4, "big")
                + (720).to_bytes(4, "big")
                + b"\x08\x06\x00\x00\x00"
            )
            found[frame] = path
        return found

    async def fake_thumbs(where: Path, shots: dict[int, Path]) -> dict[int, bytes]:
        width, height = share_media.THUMB
        return {
            f: bytes(
                ((x * 7 + y * 13 + i * 50) % 256)
                for y in range(height)
                for x in range(width)
            )
            for i, f in enumerate(shots)
        }

    monkeypatch.setattr(playtest, "run", fake_run)
    monkeypatch.setattr(playtest, "replay_shots", fake_shots)
    monkeypatch.setattr(share_media, "_window", lambda: (Path("/bin/godot"), {}))
    monkeypatch.setattr(share_media, "_thumbnails", fake_thumbs)
    said: list[str] = []
    import asyncio

    got = asyncio.run(share_media.screenshots(project, 5, said.append))
    assert seen == {"resolution": "1920x1080", "full_size": True}
    # A window the screen cut down is kept as it is and named, not enlarged.
    assert got["size"] == [1280, 720]
    assert "at least 1920×1080" in got["warning"] and got["warning"] in said


def test_bot_keeps_full_frames_only_when_asked(tmp_path: Path) -> None:
    options = playtest.Options(project=tmp_path)
    small = playtest._bot_args(options, tmp_path / "out", shots=[60])
    assert not any(a.startswith("--shot-width") for a in small)
    full = playtest._bot_args(options, tmp_path / "out", shots=[60], shot_width=0)
    assert "--shot-width=0" in full


def test_cover_script_escapes_the_title() -> None:
    script = share_media.cover_script(
        "share/.work/cover-source.png", "Moss & <Rain>", "DejaVu Sans"
    )
    assert "Moss &amp; &lt;Rain&gt;" in script
    assert "share/media/cover.kra" in script and "W, H = 630, 500" in script
    compile(script, "cover.py", "exec")


def test_media_policy_pins_worker_tools() -> None:
    pins = share_media.pins()
    assert pins["ffmpeg"].startswith("7:") and pins["krita"].startswith("1:5.")


# --- Playtest on an exported pack -----------------------------------------------------


def test_playtest_plays_an_exported_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    seen: list[list[str]] = []

    def fake_play(command: list[str], *args: Any) -> tuple[str, int, None, float]:
        seen.append(command)
        return "", 0, None, 5.0

    monkeypatch.setattr(playtest, "_play", fake_play)
    monkeypatch.setattr(playtest, "godot_binary", lambda: Path("/bin/godot"))
    pack = tmp_path / "Moss Run.pck"
    report = playtest.run(
        playtest.Options(
            project=project, seconds=5, seed=3, pack=pack, out=tmp_path / "out"
        )
    )
    command = seen[0]
    assert command[command.index("--main-pack") + 1] == str(pack)
    assert "--path" not in command
    assert report["seed"] == 3


# --- CLI ------------------------------------------------------------------------------


def test_status_reports_without_touching_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = make_project(tmp_path)
    data = share.status(project)
    assert data["name"] == "Moss Run" and data["build"] is None
    assert data["templates"]["ready"] is False and data["page"] is None
    assert [t["id"] for t in data["targets"]] == ["web", "windows", "macos", "linux"]
    assert json.dumps(data)
