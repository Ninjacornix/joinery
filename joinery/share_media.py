"""`joinery share media`: screenshots, a trailer and a cover, all from the game itself.

Nothing is generated. Every picture is a frame of the game, played by the
seeded playtester bot (ADR 0038):

* **Screenshots** (4 to 8): a headless run finds the notable moments; a
  windowed replay of the same seed (``JOINERY_BACKGROUND=1``, never takes
  focus) saves 1280x720 frames at those moments and at even steps; the worker's
  ffmpeg shrinks each to a small grey grid, and the most different frames are
  kept.
* **Trailer** (20 to 40 s): Godot's Movie Maker records a seeded bot run at a
  fixed 30 fps; ffmpeg in the offline creative worker adds a title card and an
  end card with the game's name and encodes WebM, MP4 and a small Ogg Theora
  preview for the editor. The game's own sound is kept only when every audio
  file in the project is tool-made (``joinery.lock``); otherwise it is silent.
* **Cover** (630x500, itch.io's size): Krita in the worker crops a screenshot
  and sets the title over it, saved as ``share/media/cover.kra`` (the native
  source) and exported as PNG.

The worker's ffmpeg and Krita must be the versions pinned in
``policy/share.toml``. Each run writes ``share/recipes/media.json`` (seeds,
frames, filter graph, tool versions, output hashes) and ``share/recipes/cover.py``
(the Krita script), so the media can be made again; the rendered files are
ignored by git.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from joinery import playtest
from joinery.authoring import run_offline
from joinery.catalog import ROOT
from joinery.lockfile import asset_files, read_assets
from joinery.pinned import digest
from joinery.share_build import SHARE, WORK, ensure_share_dir, game_name

MEDIA = SHARE / "media"
SHOTS = MEDIA / "screenshots"
RECIPES = SHARE / "recipes"
RECIPE = RECIPES / "media.json"
COVER_SCRIPT = RECIPES / "cover.py"
POLICY = ROOT / "policy" / "share.toml"
KINDS = ("screenshots", "trailer", "cover")
SHOT_SECONDS = 40.0
SHOT_RESOLUTION = "1280x720"
MIN_SHOTS = 4
MAX_SHOTS = 8
#: A thumbnail this close (mean grey difference, 0-255) to a kept one is the same view.
SAME_VIEW = 6.0
#: A thumbnail this flat (grey spread) is a blank or loading frame.
FLAT = 4.0
THUMB = (32, 18)
TRAILER_FPS = 30
TRAILER_GAME_SECONDS = 26.0
TITLE_SECONDS = 3.0
END_SECONDS = 2.5
COVER_SIZE = (630, 500)

Progress = Callable[[str], None]


class MediaError(Exception):
    """A plain-language reason media could not be made."""


def pins() -> dict[str, str]:
    with POLICY.open("rb") as f:
        worker = tomllib.load(f)["worker"]
    return {str(k): str(v) for k, v in worker.items()}


def _pin_check(package: str, version: str) -> str:
    return (
        f'test "$(dpkg-query -W -f=\'${{Version}}\' {package})" = "{version}" '
        f"|| {{ echo 'JOINERY_PIN {package}'; exit 3; }}"
    )


async def _worker(project: Path, script: str, timeout: int = 1200) -> str:
    try:
        got = await run_offline(["sh", "-c", script], project, timeout)
    except FileNotFoundError as exc:
        raise MediaError(
            "Docker isn't running, so the creative worker can't start."
        ) from exc
    except TimeoutError as exc:
        raise MediaError("The creative worker took too long.") from exc
    output = got.stdout + got.stderr
    if "JOINERY_PIN" in output:
        package = output.split("JOINERY_PIN", 1)[1].split()[0]
        raise MediaError(
            f"The creative worker's {package} isn't the pinned version "
            "(policy/share.toml). Rebuild the worker."
        )
    if not got.ok:
        raise MediaError("The creative worker failed: " + output.strip()[-400:])
    return output


def _recipe(project: Path) -> dict[str, Any]:
    try:
        data = json.loads((project / RECIPE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# --- Choosing screenshots ---------------------------------------------------------------


def _spread(thumb: bytes) -> float:
    mean = sum(thumb) / len(thumb)
    variance: float = sum((b - mean) ** 2 for b in thumb) / len(thumb)
    return float(variance**0.5)


def detail(thumb: bytes) -> float:
    """How much is going on: the mean step between neighbouring grey cells."""
    width, height = THUMB
    steps = [
        abs(thumb[y * width + x] - thumb[y * width + x + 1])
        for y in range(height)
        for x in range(width - 1)
    ] + [
        abs(thumb[y * width + x] - thumb[(y + 1) * width + x])
        for y in range(height - 1)
        for x in range(width)
    ]
    return sum(steps) / len(steps)


def _distance(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


def pick(thumbs: dict[int, bytes]) -> list[int]:
    """The frames to keep: varied views, no blank ones, 4 to 8 of them, in play order.

    Starts from the most detailed frame, then keeps adding the frame least like any
    already kept, while it still shows a different view (or until 4 are kept).
    """
    usable = {f: t for f, t in thumbs.items() if t and _spread(t) >= FLAT}
    if not usable:
        return []
    chosen = [max(usable, key=lambda f: detail(usable[f]))]
    while len(chosen) < MAX_SHOTS:
        rest = [f for f in usable if f not in chosen]
        if not rest:
            break
        far = max(
            rest, key=lambda f: min(_distance(usable[f], usable[c]) for c in chosen)
        )
        gap = min(_distance(usable[far], usable[c]) for c in chosen)
        if gap < SAME_VIEW and len(chosen) >= MIN_SHOTS:
            break
        chosen.append(far)
    return sorted(chosen)


def candidates(report: dict[str, Any], fps: int = 60) -> list[int]:
    """Frames worth a look: the run's notable moments and even steps through it."""
    played = float(
        report.get("played_seconds") or report.get("seconds") or SHOT_SECONDS
    )
    frames = {
        int(m.get("frame", 0))
        for m in report.get("moments", [])
        if m.get("label") != "end"
    }
    frames |= {int(played * fps * step / 11) for step in range(1, 11)}
    return sorted(f for f in frames if f >= fps * 1.5)[:16]


async def _thumbnails(project: Path, shots: dict[int, Path]) -> dict[int, bytes]:
    if not shots:
        return {}
    width, height = THUMB
    lines = [
        f"ffmpeg -v error -y -i '{p.relative_to(project).as_posix()}' "
        f"-vf scale={width}:{height}:flags=area -pix_fmt gray -f rawvideo "
        f"'{p.relative_to(project).as_posix()}.raw'"
        for p in shots.values()
    ]
    await _worker(
        project, _pin_check("ffmpeg", pins()["ffmpeg"]) + " && " + " && ".join(lines)
    )
    found: dict[int, bytes] = {}
    for frame, path in shots.items():
        raw = path.with_name(path.name + ".raw")
        if raw.is_file() and raw.stat().st_size == width * height:
            found[frame] = raw.read_bytes()
        raw.unlink(missing_ok=True)
    return found


def _window() -> tuple[Path, dict[str, str]]:
    found = playtest.window_binary()
    if found is None:
        raise MediaError(
            "Nothing can draw here (no display), so no pictures can be taken."
        )
    return found


async def screenshots(project: Path, seed: int, say: Progress) -> dict[str, Any]:
    say("Playing a seeded run")
    run_dir = project / WORK / "shots-run"
    shutil.rmtree(run_dir, ignore_errors=True)
    options = playtest.Options(
        project=project, seconds=SHOT_SECONDS, seed=seed, out=run_dir
    )
    report = await asyncio.to_thread(playtest.run, options)
    frames = candidates(report)
    if not frames:
        raise MediaError("The playtest run was too short for screenshots.")
    say("Replaying it in a window for pictures")
    taken = await asyncio.to_thread(
        playtest.replay_shots,
        options,
        frames,
        project / WORK / "shots",
        _window(),
        SHOT_RESOLUTION,
    )
    taken = {f: p for f, p in taken.items() if p.is_file()}
    say("Choosing varied moments")
    thumbs = await _thumbnails(project, taken)
    chosen = pick(thumbs)
    if len(chosen) < MIN_SHOTS:
        raise MediaError(
            f"Only {len(chosen)} different views turned up; the game needs more to see."
        )
    shutil.rmtree(project / SHOTS, ignore_errors=True)
    (project / SHOTS).mkdir(parents=True)
    kept = []
    for index, frame in enumerate(chosen, 1):
        target = project / SHOTS / f"shot-{index:02d}.png"
        shutil.copyfile(taken[frame], target)
        kept.append(target.relative_to(project).as_posix())
    # The cover shows the most detailed view: more of the game, less wall.
    busiest = max(chosen, key=lambda f: detail(thumbs[f]))
    return {
        "seed": seed,
        "seconds": SHOT_SECONDS,
        "candidates": frames,
        "frames": chosen,
        "files": kept,
        "cover_from": kept[chosen.index(busiest)],
    }


# --- Trailer --------------------------------------------------------------------------


def game_audio(project: Path) -> tuple[bool, str]:
    """Keep the game's sound in the trailer only when all of its audio is tool-made."""
    audio = sorted(f for f in asset_files(project) if f.startswith("audio/"))
    if not audio:
        return False, "Silent: the game has no audio files yet."
    made = {e.path for e in read_assets(project) if e.origin == "tool-created"}
    if any(f not in made for f in audio):
        return False, "Silent: some of the game's audio isn't tool-made."
    return True, "With the game's own sound (all tool-made)."


def filter_graph(game_seconds: float, audio: bool, font: str) -> str:
    """The ffmpeg graph: title card, the game, end card; split into three encodes."""
    g = f"{game_seconds:g}"
    card = (
        "drawtext=fontfile={font}:textfile=/work/{text}:fontcolor=white:fontsize={size}"
        ":x=(w-text_w)/2:y=(h-text_h)/2"
    )
    parts = [
        (
            f"[1:v]{card.format(font=font, text=(WORK / 'title.txt').as_posix(), size=72)},"
            f"fade=t=in:st=0:d=0.6,fade=t=out:st={TITLE_SECONDS - 0.6:g}:d=0.6,"
            "format=yuv420p,setsar=1[t]"
        ),
        (
            f"[0:v]trim=start=1:duration={g},setpts=PTS-STARTPTS,scale=1280:720,"
            f"fps={TRAILER_FPS},format=yuv420p,setsar=1,fade=t=in:st=0:d=0.5,"
            f"fade=t=out:st={game_seconds - 0.5:g}:d=0.5[g]"
        ),
        (
            f"[2:v]{card.format(font=font, text=(WORK / 'end.txt').as_posix(), size=60)},"
            "fade=t=in:st=0:d=0.5,format=yuv420p,setsar=1[e]"
        ),
    ]
    if audio:
        layout = "aresample=48000,aformat=channel_layouts=stereo"
        parts += [
            (
                f"[0:a]atrim=start=1:duration={g},asetpts=PTS-STARTPTS,{layout},"
                f"afade=t=in:d=0.5,afade=t=out:st={game_seconds - 0.8:g}:d=0.8[ga]"
            ),
            f"[3:a]{layout}[ta]",
            f"[4:a]{layout}[ea]",
            "[t][ta][g][ga][e][ea]concat=n=3:v=1:a=1[v][a]",
            "[a]asplit=3[a1][a2][a3]",
        ]
    else:
        parts.append("[t][g][e]concat=n=3:v=1:a=0[v]")
    parts.append("[v]split=3[v1][v2][v3s]")
    parts.append("[v3s]scale=640:360[v3]")
    return ";".join(parts)


def encode_argv(game_seconds: float, audio: bool, font: str) -> list[str]:
    movie = (WORK / "trailer.avi").as_posix()
    argv = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", movie]
    for seconds in (TITLE_SECONDS, END_SECONDS):
        argv += [
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x111317:s=1280x720:r={TRAILER_FPS}:d={seconds:g}",
        ]
    if audio:
        for seconds in (TITLE_SECONDS, END_SECONDS):
            argv += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={seconds:g}"]
    argv += ["-filter_complex", filter_graph(game_seconds, audio, font)]
    outputs = [
        ("1", ["-c:v", "libvpx-vp9", "-crf", "34", "-b:v", "0", "-deadline", "good",
               "-cpu-used", "5", "-row-mt", "1"], ["-c:a", "libopus", "-b:a", "96k"],
         "trailer.webm"),
        ("2", ["-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart"], ["-c:a", "aac", "-b:a", "128k"], "trailer.mp4"),
        ("3", ["-c:v", "libtheora", "-q:v", "6"], ["-c:a", "libvorbis", "-q:a", "3"],
         "trailer.ogv"),
    ]  # fmt: skip
    for index, video, sound, name in outputs:
        argv += ["-map", f"[v{index}]", *video]
        argv += ["-map", f"[a{index}]", *sound] if audio else ["-an"]
        argv += [
            "-map_metadata",
            "-1",
            "-fflags",
            "+bitexact",
            (MEDIA / name).as_posix(),
        ]
    return argv


def _quote(argv: list[str]) -> str:
    return " ".join("'" + a.replace("'", "'\\''") + "'" for a in argv)


async def trailer(project: Path, seed: int, say: Progress) -> dict[str, Any]:
    binary, extra = _window()
    work = project / WORK
    work.mkdir(parents=True, exist_ok=True)
    movie = work / "trailer.avi"
    movie.unlink(missing_ok=True)
    run_dir = work / "trailer-run"
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir()
    options = playtest.Options(
        project=project, seconds=TRAILER_GAME_SECONDS + 1, seed=seed
    )
    say("Recording a seeded run with Movie Maker")
    command = [
        str(binary), "--path", str(project), "--write-movie", str(movie),
        "--fixed-fps", str(TRAILER_FPS), "--resolution", "1280x720", "--disable-vsync",
        "--script", str(playtest.BOT), "--", *playtest._bot_args(options, run_dir),
    ]  # fmt: skip
    env = playtest._env(run_dir, extra)
    await asyncio.to_thread(
        playtest._play,
        command,
        env,
        options.seconds * 8 + 120,
        None,
        options.seconds,
        None,
    )
    if not movie.is_file() or movie.stat().st_size < 1024:
        raise MediaError("Movie Maker recorded nothing. Does the game start?")
    frames = _avi_frames(movie)
    game_seconds = min(TRAILER_GAME_SECONDS, frames / TRAILER_FPS - 1)
    if game_seconds < 12:
        raise MediaError("The recorded run was too short for a trailer.")
    name = game_name(project)
    (work / "title.txt").write_text(name, encoding="utf-8")
    (work / "end.txt").write_text(name, encoding="utf-8")
    audio, sound = game_audio(project)
    tools = pins()
    argv = encode_argv(game_seconds, audio, tools["font"])
    (project / MEDIA).mkdir(parents=True, exist_ok=True)
    say("Encoding the trailer in the worker")
    await _worker(
        project, _pin_check("ffmpeg", tools["ffmpeg"]) + " && " + _quote(argv)
    )
    movie.unlink(missing_ok=True)
    return {
        "seed": seed,
        "fps": TRAILER_FPS,
        "seconds": round(TITLE_SECONDS + game_seconds + END_SECONDS, 2),
        "audio": audio,
        "sound": sound,
        "ffmpeg": tools["ffmpeg"],
        "argv": argv,
        "files": [(MEDIA / n).as_posix() for n in ("trailer.webm", "trailer.mp4")],
        "preview": (MEDIA / "trailer.ogv").as_posix(),
    }


def _avi_frames(path: Path) -> int:
    """Frames in a Movie Maker AVI (its main header's total-frames field)."""
    with path.open("rb") as f:
        head = f.read(64)
    at = head.find(b"avih")
    if at < 0:
        return 0
    return int.from_bytes(head[at + 24 : at + 28], "little")


# --- Cover ------------------------------------------------------------------------------


def cover_script(source: str, title: str, family: str) -> str:
    """The Krita script that composes the cover; saved as the replayable recipe."""
    width, height = COVER_SIZE
    escaped = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    size = 56 if len(title) <= 14 else 44 if len(title) <= 22 else 34
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}pt" height="{height}pt" '
        f'viewBox="0 0 {width} {height}"><text x="32" y="{height - 44}" '
        f'style="font-family:{family};font-weight:bold;font-size:{size}px;fill:#ffffff">'
        f"{escaped}</text></svg>"
    )
    return f'''"""The itch.io cover: a screenshot of the game with its title. Made by Joinery in Krita.

Replay: joinery share media --project . --only cover (the screenshot comes from
the seeded run recorded in share/recipes/media.json).
"""
from krita import Krita, InfoObject
import os

os.chdir("/work")
app = Krita.instance()
W, H = {width}, {height}
shot = app.openDocument(os.path.abspath({source!r}))
shot.setBatchmode(True)
scale = max(W / shot.width(), H / shot.height())
nw, nh = round(shot.width() * scale), round(shot.height() * scale)
shot.scaleImage(nw, nh, int(shot.xRes()), int(shot.yRes()), "Bicubic")
shot.waitForDone()
shot.refreshProjection()
pixels = shot.pixelData((nw - W) // 2, (nh - H) // 2, W, H)
shot.close()
document = app.createDocument(W, H, "Cover", "RGBA", "U8", "", 72.0)
document.setBatchmode(True)
root = document.rootNode()
for child in root.childNodes():
    root.removeChildNode(child)
picture = document.createNode("Screenshot", "paintlayer")
picture.setPixelData(pixels, 0, 0, W, H)
root.addChildNode(picture, None)
shade = document.createNode("Shade", "paintlayer")
rows = bytearray()
for y in range(H):
    alpha = 0 if y < H * 0.55 else int(190 * (y - H * 0.55) / (H * 0.45))
    rows += bytes((0, 0, 0, alpha)) * W
shade.setPixelData(bytes(rows), 0, 0, W, H)
root.addChildNode(shade, picture)
title = document.createVectorLayer("Title")
root.addChildNode(title, shade)
title.addShapesFromSvg({svg!r})
document.refreshProjection()
document.waitForDone()
assert document.saveAs(os.path.abspath("share/media/cover.kra"))
assert document.exportImage(os.path.abspath("share/media/cover.png"), InfoObject())
document.close()
'''


async def cover(project: Path, source: str, say: Progress) -> dict[str, Any]:
    if not (project / source).is_file():
        raise MediaError("Make the screenshots first: the cover is made from one.")
    tools = pins()
    staged = WORK / "cover-source.png"
    (project / WORK).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(project / source, project / staged)
    (project / RECIPES).mkdir(parents=True, exist_ok=True)
    script = cover_script(staged.as_posix(), game_name(project), tools["font_family"])
    (project / COVER_SCRIPT).write_text(script, encoding="utf-8")
    (project / MEDIA).mkdir(parents=True, exist_ok=True)
    say("Composing the cover in Krita")
    await _worker(
        project,
        _pin_check("krita", tools["krita"])
        + " && joinery-krita "
        + COVER_SCRIPT.as_posix(),
    )
    for name in ("cover.kra", "cover.png"):
        if not (project / MEDIA / name).is_file():
            raise MediaError("Krita didn't save the cover.")
    return {
        "from": source,
        "script": COVER_SCRIPT.as_posix(),
        "krita": tools["krita"],
        "size": list(COVER_SIZE),
        "files": [(MEDIA / "cover.png").as_posix(), (MEDIA / "cover.kra").as_posix()],
    }


# --- All together -----------------------------------------------------------------------


async def make(
    project: Path,
    only: list[str],
    *,
    seed: int = 0,
    new_seed: bool = False,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Make the chosen media; returns the updated recipe (also saved)."""
    say = progress or (lambda _text: None)
    project = project.resolve()
    if not (project / "project.godot").is_file():
        raise ValueError(f"{project} is not a Godot project.")
    ensure_share_dir(project)
    recipe = _recipe(project)
    if seed <= 0:
        seed = 0 if new_seed else int(recipe.get("seed", 0) or 0)
    seed = seed or secrets.randbelow(900_000) + 100_000
    recipe.update({"version": 1, "seed": seed})
    if "screenshots" in only:
        recipe["screenshots"] = await screenshots(project, seed, say)
    if "trailer" in only:
        recipe["trailer"] = await trailer(project, seed, say)
    if "cover" in only:
        shots = recipe.get("screenshots", {})
        source = str(shots.get("cover_from", "")) if isinstance(shots, dict) else ""
        recipe["cover"] = await cover(project, source, say)
    recipe["outputs"] = {
        path.relative_to(project).as_posix(): digest(path)
        for path in sorted((project / MEDIA).rglob("*"))
        if path.is_file()
    }
    (project / RECIPES).mkdir(parents=True, exist_ok=True)
    (project / RECIPE).write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(project / WORK / "shots", ignore_errors=True)
    return recipe


def listing(project: Path) -> dict[str, Any]:
    """What media exists now, for the Share page."""
    recipe = _recipe(project)
    shots = sorted(
        p.relative_to(project).as_posix() for p in (project / SHOTS).glob("*.png")
    )
    found = {
        name: (MEDIA / name).as_posix()
        for name in (
            "trailer.webm",
            "trailer.mp4",
            "trailer.ogv",
            "cover.png",
            "cover.kra",
        )
        if (project / MEDIA / name).is_file()
    }
    trailer_info = (
        recipe.get("trailer", {}) if isinstance(recipe.get("trailer"), dict) else {}
    )
    return {
        "seed": recipe.get("seed"),
        "screenshots": shots,
        "trailer": {
            "webm": found.get("trailer.webm"),
            "mp4": found.get("trailer.mp4"),
            "preview": found.get("trailer.ogv"),
            "seconds": trailer_info.get("seconds"),
            "sound": trailer_info.get("sound"),
        },
        "cover": found.get("cover.png"),
    }
