"""`joinery share build`: export the game for the web and desktops, zipped for players.

Each target is exported by stock Godot 4.7 with an export preset Joinery keeps
in the project's ordinary `export_presets.cfg` (added when missing, repaired
when a setting the build needs is missing), using the pinned export templates
(`joinery.export_templates`). The web build uses the Compatibility renderer
and no threads, which is what itch.io serves without special headers.

Every build is zipped with a README (how to start it, the controls) and the
licence notes: the game's own licence, the MIT notice for the starting-point
code Joinery copied into it, Godot's licence and its third-party notices, and
those of any pinned extension. Sizes are checked against itch.io's limits. The
build for this computer's own system is started once, headless, and the
playtester plays its exported pack. Builds land in `share/builds/`, which git
ignores; they are remade from the project at any time.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from joinery import export_templates, playtest, playtest_report
from joinery.brief import (
    action_label,
    input_actions,
    key_label,
    mouse_label,
    pad_label,
    project_name,
)
from joinery.catalog import ROOT
from joinery.godot_config import GodotConfig, parse_string, to_variant
from joinery.lockfile import read_assets, read_extensions

SHARE = Path("share")
BUILDS = SHARE / "builds"
WORK = SHARE / ".work"
REPORT = BUILDS / "report.json"
#: The seed the build's playtest plays, so two builds of the same game compare.
PLAYTEST_SEED = 4242
PLAYTEST_SECONDS = 30.0
BOOT_FRAMES = 300
#: Files Joinery keeps out of every export: the editor add-on, store material,
#: recipes, design documents and tests are not part of the game.
EXCLUDES = ("addons/joinery/*", "share/*", "recipes/*", "design/*", "tests/*")
#: itch.io's limits for a game played in the browser, and for one download.
WEB_MAX_TOTAL = 500 * 1024 * 1024
WEB_MAX_FILES = 1000
WEB_MAX_FILE = 200 * 1024 * 1024
DOWNLOAD_WARN = 1024 * 1024 * 1024
_ERRORS = re.compile(
    r"^(SCRIPT ERROR|USER SCRIPT ERROR|ERROR:|Parse Error)", re.MULTILINE
)

Progress = Callable[[str], None]


@dataclass(frozen=True)
class Target:
    id: str
    label: str
    #: Godot's platform name in export_presets.cfg, also the preset's name.
    platform: str
    #: The itch.io channel butler pushes this build to.
    channel: str

    def templates(self, threads: bool = False) -> list[str]:
        return {
            "web": ["web_release.zip" if threads else "web_nothreads_release.zip"],
            "windows": [
                "windows_release_x86_64.exe",
                "windows_release_x86_64_console.exe",
            ],
            "macos": ["macos.zip"],
            "linux": ["linux_release.x86_64"],
        }[self.id]

    def product(self, name: str) -> str:
        return {
            "web": "index.html",
            "windows": name + ".exe",
            "macos": name + ".zip",
            "linux": name + ".x86_64",
        }[self.id]


TARGETS = {
    "web": Target("web", "Web", "Web", "web"),
    "windows": Target("windows", "Windows", "Windows Desktop", "windows"),
    "macos": Target("macos", "macOS", "macOS", "mac"),
    "linux": Target("linux", "Linux", "Linux", "linux"),
}


def parse_targets(text: str) -> list[str]:
    chosen = [t.strip().lower() for t in text.split(",") if t.strip()]
    unknown = [t for t in chosen if t not in TARGETS]
    if unknown or not chosen:
        raise ValueError("Targets are web, windows, macos and linux, comma-separated.")
    return [t for t in TARGETS if t in chosen]


def host_target() -> str | None:
    """The desktop build this computer can start, or None (no x86_64 Linux here)."""
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    if sys.platform.startswith("linux") and os.uname().machine in {"x86_64", "AMD64"}:
        return "linux"
    return None


# --- Names ---------------------------------------------------------------------------


def game_name(project: Path) -> str:
    return project_name(project).strip() or project.name


def file_name(name: str) -> str:
    """The game's name as a file name every system accepts."""
    cleaned = re.sub(r"[^A-Za-z0-9 _.-]+", "", name).strip(" .")
    return cleaned or "Game"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "game"


def game_version(project: Path) -> str:
    text = (project / "project.godot").read_text(encoding="utf-8")
    raw = GodotConfig.parse(text).get("application", "config/version")
    value = parse_string(raw) if raw else None
    return value or "0.1.0"


# --- Presets and project settings ----------------------------------------------------


def _preset_sections(config: GodotConfig) -> dict[str, str]:
    """{platform: section} for each `[preset.N]` (the first preset per platform wins)."""
    found: dict[str, str] = {}
    for section in config.sections():
        if re.fullmatch(r"preset\.\d+", section):
            platform = parse_string(config.get(section, "platform") or '""') or ""
            found.setdefault(platform, section)
    return found


def _required_options(target: Target, name: str) -> dict[str, str]:
    """Options the build needs: set when missing, never changed when the user set them."""
    if target.id == "web":
        return {
            "variant/thread_support": "false",
            "vram_texture_compression/for_desktop": "true",
            "vram_texture_compression/for_mobile": "false",
            "progressive_web_app/enabled": "false",
        }
    if target.id == "windows":
        return {
            "binary_format/architecture": to_variant("x86_64"),
            "binary_format/embed_pck": "true",
            "codesign/enable": "false",
            # Changing the .exe's icon and details needs rcedit, which Joinery does not install.
            "application/modify_resources": "false",
        }
    if target.id == "macos":
        return {
            "binary_format/architecture": to_variant("universal"),
            "application/bundle_identifier": to_variant(
                "org.joinery.game." + slug(name)
            ),
            "application/short_version": to_variant("0.1.0"),
            "application/version": to_variant("1"),
            # Godot's own ad-hoc signature: the game opens, with the usual first-run prompt.
            "codesign/codesign": "1",
            "notarization/notarization": "0",
        }
    return {
        "binary_format/architecture": to_variant("x86_64"),
        "binary_format/embed_pck": "true",
    }


def ensure_presets(text: str, targets: list[str], name: str) -> tuple[str, list[str]]:
    """export_presets.cfg text with a working preset per target, and what changed."""
    config = GodotConfig.parse(text)
    changes: list[str] = []
    existing = _preset_sections(config)
    numbers = [
        int(s.split(".")[1])
        for s in config.sections()
        if re.fullmatch(r"preset\.\d+", s)
    ]
    for target_id in targets:
        target = TARGETS[target_id]
        section = existing.get(target.platform)
        if section is None:
            number = max(numbers, default=-1) + 1
            numbers.append(number)
            section = f"preset.{number}"
            config.add_section(
                section,
                [
                    ("name", to_variant(target.platform)),
                    ("platform", to_variant(target.platform)),
                    ("runnable", "true"),
                    ("export_filter", to_variant("all_resources")),
                    ("include_filter", to_variant("")),
                    ("exclude_filter", to_variant(",".join(EXCLUDES))),
                    ("export_path", to_variant("")),
                ],
                sort=False,
            )
            config.add_section(
                section + ".options",
                list(_required_options(target, name).items()),
                sort=False,
            )
            changes.append(f"Added the {target.label} export preset")
            continue
        current = parse_string(config.get(section, "exclude_filter") or '""') or ""
        parts = [p.strip() for p in current.split(",") if p.strip()]
        added = [e for e in EXCLUDES if e not in parts]
        if added:
            config.set(section, "exclude_filter", to_variant(",".join(parts + added)))
            changes.append(
                f"{target.label}: left Joinery's own files out of the export"
            )
        options = section + ".options"
        if not config.has_section(options):
            config.add_section(options, [], sort=False)
        filled: list[str] = []
        for key, value in _required_options(target, name).items():
            if config.get(options, key) in (None, '""'):
                config.set(options, key, value)
                filled.append(key)
        if filled:
            changes.append(f"{target.label}: filled in {', '.join(filled)}")
    return config.dump(), changes


def threads_on(text: str) -> bool:
    config = GodotConfig.parse(text)
    section = _preset_sections(config).get("Web")
    return (
        section is not None
        and config.get(section + ".options", "variant/thread_support") == "true"
    )


def ensure_settings(text: str, targets: list[str]) -> tuple[str, list[str]]:
    """project.godot text with what the chosen builds need, and what changed."""
    config = GodotConfig.parse(text)
    changes: list[str] = []
    if "web" in targets:
        web = config.get("rendering", "renderer/rendering_method.web")
        if web is not None and parse_string(web) != "gl_compatibility":
            config.set(
                "rendering",
                "renderer/rendering_method.web",
                to_variant("gl_compatibility"),
            )
            changes.append(
                "The web build uses the Compatibility renderer (browsers need it)"
            )
    if "macos" in targets:
        key = "textures/vram_compression/import_etc2_astc"
        if config.get("rendering", key) != "true":
            config.set("rendering", key, "true")
            changes.append(
                "Turned on ETC2/ASTC textures, which Apple Silicon Macs need"
            )
    return config.dump(), changes


def _join(values: Sequence[str]) -> str:
    unique = list(dict.fromkeys(v for v in values if v))
    return " / ".join(unique)


def controls(
    actions: Mapping[str, Mapping[str, Sequence[object]]],
) -> list[tuple[str, str, str]]:
    """(action, keyboard and mouse, gamepad) for each of the game's own actions."""
    rows = []
    for action, bindings in actions.items():
        if action.startswith("ui_"):
            continue
        keyboard = [key_label(str(k)) for k in bindings.get("keys", ())]
        keyboard += [mouse_label(str(m)) for m in bindings.get("mouse", ())]
        pad = [pad_label(p) for p in bindings.get("pad", ())]
        rows.append((action_label(action), _join(keyboard), _join(pad)))
    return rows


def controls_text(project: Path) -> str:
    """The controls as plain lines, for a README."""
    return "\n".join(
        f"{action}: {keys or '-'}" + (f" (gamepad: {pad})" if pad else "")
        for action, keys, pad in controls(input_actions(project))
    )


# --- Licence notes and README ----------------------------------------------------------


def notices(project: Path, target: str) -> dict[str, bytes]:
    """Licence files for the zip: {path inside the zip: content}."""
    found: dict[str, bytes] = {}
    for candidate in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING"):
        path = project / candidate
        if path.is_file():
            found["LICENSE.txt"] = path.read_bytes()
            break
    if (project / "joinery.toml").is_file() and "[game]" in (
        project / "joinery.toml"
    ).read_text(encoding="utf-8"):
        found["LICENSES/Starting-point-MIT.txt"] = (
            b"Parts of this game's code come from Joinery's starting points and features,\n"
            b"released under the MIT licence below. The game, its art and its audio\n"
            b"belong to the game's author.\n\n"
            + (ROOT / "blueprints/LICENSE").read_bytes()
        )
    found["LICENSES/Godot-LICENSE.txt"] = (
        ROOT / "licenses/GODOT-LICENSE.txt"
    ).read_bytes()
    found["LICENSES/Godot-third-party-notices.txt"] = (
        ROOT / "licenses/GODOT-COPYRIGHT.txt"
    ).read_bytes()
    if target in {"windows", "macos"}:
        found["LICENSES/ANGLE-LICENSE.txt"] = (
            ROOT / "licenses/ANGLE-LICENSE.txt"
        ).read_bytes()
    for extension in read_extensions(project):
        for licence in ("LICENSE", "LICENSE.md", "license.md"):
            path = project / extension.path / licence
            if path.is_file():
                found[f"LICENSES/{file_name(extension.name)}-LICENSE.txt"] = (
                    path.read_bytes()
                )
                break
    imported = [
        f"{entry.path}: {entry.licence} (added by {entry.imported_by})"
        for entry in read_assets(project)
        if entry.origin == "human-import"
    ]
    if imported:
        found["LICENSES/Imported-assets.txt"] = ("\n".join(imported) + "\n").encode()
    return found


_HOW_TO = {
    "web": "Open index.html through a web server (itch.io does this for you).",
    "windows": "Double-click {exe}.exe. Windows may ask you to confirm: More info, then Run anyway.",
    "macos": (
        "Move {exe}.app to Applications, then right-click it and choose Open the first time "
        "(it is not notarized by Apple)."
    ),
    "linux": "Run ./{exe}.x86_64 (mark it executable first if needed: chmod +x {exe}.x86_64).",
}


def readme(project: Path, target: str, licence_files: list[str]) -> str:
    name = game_name(project)
    lines = [
        name,
        "=" * len(name),
        "",
        "How to start",
        "",
        _HOW_TO[target].format(exe=file_name(name)),
    ]
    keys = controls_text(project)
    if keys:
        lines += ["", "Controls", "", keys]
    lines += [
        "",
        "Licences",
        "",
        "Made with the Godot Engine (godotengine.org), MIT licence.",
        *[f"- {path}" for path in sorted(licence_files)],
        "",
    ]
    return "\n".join(lines)


# --- Zips and sizes ---------------------------------------------------------------------


def _info(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def make_zip(
    staging: Path, target: str, destination: Path, extra: dict[str, bytes]
) -> None:
    """Zip `staging` (a macOS export zip is copied entry by entry, attributes kept) with `extra`."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as out:
        for path in sorted(p for p in staging.rglob("*") if p.is_file()):
            rel = path.relative_to(staging).as_posix()
            if target == "macos" and path.suffix == ".zip":
                with zipfile.ZipFile(path) as source:
                    for entry in source.infolist():
                        out.writestr(entry, source.read(entry))
                continue
            executable = target in {"linux", "windows"} and path.suffix in {
                ".x86_64",
                ".exe",
            }
            out.writestr(_info(rel, 0o755 if executable else 0o644), path.read_bytes())
        for rel, data in sorted(extra.items()):
            out.writestr(_info(rel, 0o644), data)
    partial.replace(destination)


def sizes(archive: Path, target: str) -> dict[str, Any]:
    """The zip's size, what it unpacks to, and warnings against itch.io's limits."""
    with zipfile.ZipFile(archive) as bundle:
        infos = [i for i in bundle.infolist() if not i.is_dir()]
    unpacked = sum(i.file_size for i in infos)
    largest = max((i.file_size for i in infos), default=0)
    warnings: list[str] = []
    if target == "web":
        if unpacked > WEB_MAX_TOTAL:
            warnings.append(
                "Over 500 MB unpacked: itch.io won't play it in the browser."
            )
        if len(infos) > WEB_MAX_FILES:
            warnings.append(
                "More than 1,000 files: itch.io won't play it in the browser."
            )
        if largest > WEB_MAX_FILE:
            warnings.append(
                "A file is over 200 MB: itch.io won't serve it in the browser."
            )
    if archive.stat().st_size > DOWNLOAD_WARN:
        warnings.append("Over 1 GB: players will wait a long time for the download.")
    return {
        "size": archive.stat().st_size,
        "unpacked": unpacked,
        "files": len(infos),
        "warnings": warnings,
    }


# --- Export, boot, playtest ---------------------------------------------------------------


def godot() -> Path:
    """The Godot that exports: `JOINERY_SHARE_GODOT`, else the playtester's stock Godot."""
    override = os.environ.get("JOINERY_SHARE_GODOT")
    if override and Path(override).is_file():
        return Path(override)
    return playtest.godot_binary()


def _run(
    argv: list[str], env: dict[str, str], cwd: Path | None = None, timeout: int = 900
) -> tuple[int, str]:
    try:
        result = subprocess.run(
            argv,
            env={**os.environ, **env},
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return 124, str(exc.stdout or "") + str(exc.stderr or "")
    return result.returncode, result.stdout + result.stderr


def _first_error(log: str) -> str:
    for line in log.splitlines():
        if _ERRORS.match(line.strip()):
            return line.strip()[:300]
    return ""


def export(
    project: Path, target: Target, staging: Path, name: str, binary: Path
) -> Path:
    staging.mkdir(parents=True, exist_ok=True)
    product = staging / target.product(file_name(name))
    code, log = _run(
        [
            str(binary),
            "--headless",
            "--path",
            str(project),
            "--export-release",
            target.platform,
            str(product),
        ],
        export_templates.environment(),
    )
    log_path = project / WORK / f"export-{target.id}.log"
    log_path.write_text(log, encoding="utf-8")
    if code != 0 or not product.is_file():
        raise RuntimeError(
            (_first_error(log) or f"Godot couldn't export the {target.label} build.")
            + f" Details are in {log_path.relative_to(project).as_posix()}."
        )
    return product


def _extract(archive: Path, root: Path) -> None:
    root = root.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            path = (root / info.filename).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Unsafe path in the build.")
            bundle.extract(info, root)
            mode = info.external_attr >> 16 & 0o777
            if not info.is_dir() and mode:
                path.chmod(mode)


def _executable(root: Path, target: str, name: str) -> tuple[Path, Path]:
    """(program to start, pack the playtester plays) inside an unpacked build."""
    if target == "macos":
        app = next(root.glob("*.app"))
        program = next((app / "Contents/MacOS").iterdir())
        pack = next((app / "Contents/Resources").glob("*.pck"))
        return program, pack
    program = root / (file_name(name) + (".exe" if target == "windows" else ".x86_64"))
    return program, program


def check_desktop(
    project: Path, archive: Path, target: str, name: str, progress: Progress, play: bool
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Start the host's build once, headless; then let the playtester play its pack."""
    with tempfile.TemporaryDirectory(prefix="joinery-share-") as temporary:
        root = Path(temporary) / "game"
        _extract(archive, root)
        program, pack = _executable(root, target, name)
        home = Path(temporary) / "home"
        home.mkdir()
        progress("Starting the build once")
        code, log = _run(
            [str(program), "--headless", "--quit-after", str(BOOT_FRAMES)],
            {"HOME": str(home), "APPDATA": str(home), "XDG_DATA_HOME": str(home)},
            cwd=program.parent,
            timeout=180,
        )
        error = _first_error(log)
        boot = {
            "ran": True,
            "ok": code == 0 and not error,
            "detail": error
            or (
                "Started and quit cleanly."
                if code == 0
                else f"Exited with code {code}."
            ),
        }
        if not play or not boot["ok"]:
            return boot, None
        progress("Playtesting the build")
        out = project / WORK / "playtest"
        shutil.rmtree(out, ignore_errors=True)
        report = playtest.run(
            playtest.Options(
                project=project,
                seconds=PLAYTEST_SECONDS,
                seed=PLAYTEST_SEED,
                pack=pack,
                out=out,
            )
        )
        return boot, {
            "status": report.get("status"),
            "seed": report.get("seed"),
            "summary": playtest_report.summary_line(report),
            "counts": report.get("counts", {}),
            "report": (out / "report.json").relative_to(project).as_posix(),
        }


def _not_run(target: str) -> dict[str, Any]:
    if target == "web":
        return {
            "ran": False,
            "ok": None,
            "detail": "Plays in a browser; try it on itch.io.",
        }
    label = TARGETS[target].label
    return {
        "ran": False,
        "ok": None,
        "detail": f"Built here; start it on a {label} computer to try it.",
    }


def ensure_share_dir(project: Path) -> Path:
    """`share/`, which Godot never imports, with its built files kept out of git."""
    folder = project / SHARE
    folder.mkdir(exist_ok=True)
    (folder / ".gdignore").touch()
    ignore = folder / ".gitignore"
    if not ignore.is_file():
        ignore.write_text(
            "# Builds and rendered media are remade from the project and share/recipes.\n"
            "builds/\n.work/\nmedia/screenshots/\nmedia/*.png\nmedia/*.webm\n"
            "media/*.mp4\nmedia/*.ogv\n",
            encoding="utf-8",
        )
    return folder


def build(
    project: Path,
    targets: list[str],
    *,
    download_templates: bool = False,
    play: bool = True,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Export, zip, measure and check each target; returns (and saves) the report."""
    say = progress or (lambda _text: None)
    project = project.resolve()
    if not (project / "project.godot").is_file():
        raise ValueError(f"{project} is not a Godot project.")
    ensure_share_dir(project)
    name = game_name(project)
    presets_path = project / "export_presets.cfg"
    old = presets_path.read_text(encoding="utf-8") if presets_path.is_file() else ""
    new, changes = ensure_presets(old, targets, name)
    if new != old:
        presets_path.write_text(new, encoding="utf-8")
    settings_path = project / "project.godot"
    old_settings = settings_path.read_text(encoding="utf-8")
    new_settings, setting_changes = ensure_settings(old_settings, targets)
    if new_settings != old_settings:
        settings_path.write_text(new_settings, encoding="utf-8")
    changes += setting_changes
    needed = [f for t in targets for f in TARGETS[t].templates(threads_on(new))]
    say("Checking the export templates")
    export_templates.prepare(needed, allow_download=download_templates)
    binary = godot()
    report: dict[str, Any] = {
        "version": 1,
        "time": int(time.time()),
        "name": name,
        "game_version": game_version(project),
        "engine": export_templates.load_pin().version,
        "changes": changes,
        "targets": {},
    }
    work = project / WORK / "build"
    host = host_target()
    for target_id in targets:
        target = TARGETS[target_id]
        say(f"Exporting {target.label}")
        staging = work / target_id
        shutil.rmtree(staging, ignore_errors=True)
        result: dict[str, Any] = {"label": target.label, "channel": target.channel}
        try:
            export(project, target, staging, name, binary)
            archive = project / BUILDS / f"{slug(name)}-{target_id}.zip"
            extra = notices(project, target_id)
            extra["README.txt"] = readme(project, target_id, list(extra)).encode()
            say(f"Zipping {target.label}")
            make_zip(staging, target_id, archive, extra)
            result.update({"ok": True, "zip": archive.relative_to(project).as_posix()})
            result.update(sizes(archive, target_id))
            if target_id == host:
                boot, played = check_desktop(
                    project, archive, target_id, name, say, play
                )
                result["boot"] = boot
                if played is not None:
                    result["playtest"] = played
            else:
                result["boot"] = _not_run(target_id)
        except (RuntimeError, OSError, ValueError, StopIteration) as exc:
            result.update({"ok": False, "error": str(exc) or "The build failed."})
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        report["targets"][target_id] = result
    (project / REPORT).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def last_report(project: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((project / REPORT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def zip_entries(archive: Path) -> list[str]:
    with zipfile.ZipFile(archive) as bundle:
        return [PurePosixPath(i.filename).as_posix() for i in bundle.infolist()]
