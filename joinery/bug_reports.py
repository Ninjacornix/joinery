"""Bug reports players save from the game (ADR 0059).

"Report a bug" in the game's pause menu, or F8, writes a folder to the game's
``user://joinery_reports/<id>/``: ``report.json`` (the note, versions, seed, scene,
frame), ``replay.replay`` (the session up to the report, its last seconds marked) and
``log.txt``. Nothing is uploaded; a player sends the folder.

    joinery reports list --project P          # reports on this machine and in .joinery/reports/
    joinery reports open <id> --project P     # the note, versions, log tail and replay
    joinery reports import <folder> --project P

Reports are found in the game's user data folder on this machine (where editor runs and
local builds save them) and in the project's ``.joinery/reports/`` (where a player's
report goes; ``import`` copies it there). ``/debug report <id>`` in the sidebar hands one
to the debugger, which proposes a fix as a reviewed change.

A crash reporter that uploads (Sentry or the like) is a later opt-in, not part of this.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from joinery.godot_config import GodotConfig, parse_string

FOLDER_NAME = "joinery_reports"
IMPORTED = Path(".joinery") / "reports"
LOG_TAIL = 8000
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


@dataclass(frozen=True)
class Report:
    id: str
    folder: Path
    data: dict[str, Any]

    @property
    def note(self) -> str:
        return str(self.data.get("note", ""))

    @property
    def replay(self) -> Path | None:
        name = str(self.data.get("replay", "") or "")
        path = self.folder / name if name else None
        return path if path is not None and path.is_file() else None

    def log(self) -> str:
        try:
            return (self.folder / "log.txt").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def summary(self) -> dict[str, Any]:
        keys = ("created", "game", "game_version", "joinery", "build", "os", "seed", "scene")
        return {
            "id": self.id,
            "folder": str(self.folder),
            "note": self.note,
            **{key: self.data.get(key, "") for key in keys},
            "frame": self.data.get("frame"),
            "seconds": self.data.get("seconds"),
            "errors": self.data.get("errors", 0),
            "replay": str(self.replay) if self.replay else "",
        }


def user_data_dir(project: Path) -> Path | None:
    """The game's ``user://`` on this machine, as Godot names it for the project."""
    settings = _settings(project)
    name = str(settings.get("application/config/name", "") or "")
    custom = bool(settings.get("application/config/use_custom_user_dir", False))
    custom_name = str(settings.get("application/config/custom_user_dir_name", "") or "")
    folder_name = _safe(custom_name or name) if custom else _safe(name)
    if not folder_name:
        return None
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local" / "share")))
    if custom:
        return base / folder_name
    godot = "godot" if not (sys.platform == "darwin" or sys.platform.startswith("win")) else "Godot"
    return base / godot / "app_userdata" / folder_name


def _settings(project: Path) -> dict[str, Any]:
    """The ``[application]`` values the user folder depends on."""
    try:
        config = GodotConfig.parse((project / "project.godot").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found: dict[str, Any] = {}
    for key in ("config/name", "config/use_custom_user_dir", "config/custom_user_dir_name"):
        raw = config.get("application", key)
        if raw is None:
            continue
        found["application/" + key] = (
            raw.strip() == "true" if key.endswith("_dir") else (parse_string(raw) or "")
        )
    return found


def _safe(name: str) -> str:
    """Godot's ``validate_filename`` for the user folder: no path characters."""
    return re.sub(r'[:/\\?*"|%<>]', "_", name).strip()


def folders(project: Path, extra: list[Path] | None = None) -> list[Path]:
    """Where reports for this project are looked for, in order."""
    found: list[Path] = [project / IMPORTED]
    user = user_data_dir(project)
    if user is not None:
        found.append(user / FOLDER_NAME)
    found.extend(extra or [])
    return found


def load(folder: Path) -> Report:
    try:
        data = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"{folder} has no readable report.json.") from None
    if not isinstance(data, dict) or data.get("format") != "joinery-bug-report":
        raise ValueError(f"{folder} is not a Joinery bug report.")
    return Report(str(data.get("id") or folder.name), folder, data)


def list_reports(project: Path, extra: list[Path] | None = None) -> list[Report]:
    """Every report found, newest first; one id once."""
    seen: dict[str, Report] = {}
    for root in folders(project, extra):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            try:
                report = load(child)
            except ValueError:
                continue
            seen.setdefault(report.id, report)
    return sorted(seen.values(), key=lambda r: str(r.data.get("created", r.id)), reverse=True)


def find(project: Path, report_id: str, extra: list[Path] | None = None) -> Report:
    """A report by its id (or a unique start of it); raises ValueError in words."""
    report_id = report_id.strip()
    if not _ID.match(report_id):
        raise ValueError("A report id looks like 20261112-153012-a3f9.")
    reports = list_reports(project, extra)
    exact = [r for r in reports if r.id == report_id]
    matches = exact or [r for r in reports if r.id.startswith(report_id)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(
            f"There's no bug report {report_id}. `joinery reports list` shows what there is."
        )
    raise ValueError(f"{report_id} matches {len(matches)} reports; give more of the id.")


def import_report(project: Path, source: Path) -> Report:
    """Copy a player's report folder into ``.joinery/reports/``."""
    report = load(source)
    target = project / IMPORTED / _safe(report.id)
    if target.exists():
        raise ValueError(f"Report {report.id} is already in the project.")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return load(target)


def debug_text(report: Report) -> dict[str, str]:
    """What the debugger is given: the error to start from, the note and the log."""
    log = report.log()
    error = next(
        (
            line.split("] ", 1)[-1]
            for line in reversed(log.splitlines())
            if "ERROR" in line.split(":", 1)[0]
        ),
        "",
    )
    note = report.note or "(no note)"
    facts = [
        f"Bug report {report.id} from a player: {note}",
        f"Game {report.data.get('game', '')} {report.data.get('game_version', '')}".rstrip(),
        f"Joinery {report.data.get('joinery', '')}, engine {report.data.get('build', '')}",
        "Seed {}, scene {}, frame {}".format(
            report.data.get("seed", ""), report.data.get("scene", ""), report.data.get("frame", "")
        ),
    ]
    if report.replay is not None:
        facts.append(
            f"The session is recorded in {report.replay} (the last "
            f"{report.data.get('seconds', 60)} s before the report are the part to look "
            "at). `joinery replay add` keeps it as a regression test once fixed."
        )
    return {
        "error": error or f"Player report: {note}"[:300],
        "output": "\n".join(facts),
        "log": log[-LOG_TAIL:],
        "scene": str(report.data.get("scene", "")),
    }


# --- CLI -----------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    list_cmd = actions.add_parser("list", help="list bug reports players saved")
    list_cmd.add_argument("--project", type=Path, required=True)
    list_cmd.add_argument("--dir", type=Path, action="append", help="look here too")
    list_cmd.add_argument("--json", action="store_true")
    open_cmd = actions.add_parser("open", help="show one report: note, versions, log")
    open_cmd.add_argument("id")
    open_cmd.add_argument("--project", type=Path, required=True)
    open_cmd.add_argument("--dir", type=Path, action="append", help="look here too")
    open_cmd.add_argument("--json", action="store_true")
    import_cmd = actions.add_parser("import", help="copy a player's report into the project")
    import_cmd.add_argument("folder", type=Path)
    import_cmd.add_argument("--project", type=Path, required=True)


def main(args: argparse.Namespace) -> int:
    project: Path = args.project.resolve()
    try:
        if args.action == "import":
            report = import_report(project, args.folder)
            print(f"Imported report {report.id} into {IMPORTED.as_posix()}/.")
            return 0
        if args.action == "list":
            reports = list_reports(project, args.dir)
            if args.json:
                print(json.dumps([r.summary() for r in reports], indent=2))
                return 0
            if not reports:
                print("No bug reports yet.")
            for report in reports:
                note = report.note.splitlines()[0] if report.note else "(no note)"
                print(f"- {report.id}  {note[:70]}")
            return 0
        report = find(project, args.id, args.dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    summary = report.summary()
    if args.json:
        print(json.dumps({**summary, "log": report.log()[-LOG_TAIL:]}, indent=2))
        return 0
    print(f"Report {report.id} ({summary['created']})")
    print(f"Note: {report.note or '(none)'}")
    print(
        f"Game {summary['game']} {summary['game_version']} · Joinery {summary['joinery']} "
        f"· {summary['build']}"
    )
    print(f"Seed {summary['seed']} · scene {summary['scene']} · frame {summary['frame']}")
    print(f"Replay: {summary['replay'] or 'none (this build did not record)'}")
    print(f"Folder: {report.folder}")
    log = report.log().strip().splitlines()
    if log:
        print("Log (last lines):")
        for line in log[-20:]:
            print("  " + line)
    print(f"Debug it from the sidebar: /debug report {report.id}")
    return 0
