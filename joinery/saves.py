"""Old saves keep loading: sample saves from accepted versions, loaded in every change
that could break them (ADR 0066).

The playtester's runs save through the game's own save system (the save-load
feature's ``SaveGame``; ``.joinery/playtests/<run>/saves/slot_<n>.json``). Saves worth
keeping are copied into the game's tests::

    joinery saves add <slot file|run folder> --project P [--name N] [--version V]
    joinery saves list --project P
    joinery saves check --project P            # load each kept save here and compare

Each kept save is ``tests/saves/<version>/<name>.json`` (``<version>`` is the game's
``application/config/version``, else ``save-v<data version>``), with
``<name>.summary.json`` beside it: what the game held after loading it when it was
kept (the level, play time, every saveable node's state, the extra data and the slot
list's one line), in plain JSON a person can read.

Loading one (``save_check.gd``, run from outside the game with ``--script``) copies it
into slot 1, loads it the way the Load page does and prints the save after the game's
migration steps beside what the game holds once the level is ready. A save **fails**
when the game refuses it (damaged, too old with no migration step, its level gone) or
the level never starts; it **loses data** when a value it saved isn't in the game
after loading (a node gone, a key gone, a value changed) or the slot list's line
changed from the one kept with it.

Every staged change that touches save-relevant code or data (:func:`relevant`) loads
each kept save in the changed game, in the offline worker, when ``[checks] saves`` is
on (the default once ``tests/saves/`` has any). What a save does on the worker before
the change is the baseline (cached in ``.joinery/saves/``): only what the change makes
worse sends it back, naming the save and what broke, and asking for a migration step
(``res://game/save_migrations.gd``) in the same change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from joinery.runner import Result

FOLDER = Path("tests") / "saves"
SUMMARY = ".summary.json"
CHECK_SCRIPT = Path(__file__).with_name("save_check.gd")
RESULT_PREFIX = "JOINERY_SAVE "
BASELINES = Path(".joinery") / "saves"
MIGRATIONS = "game/save_migrations.gd"
TIMEOUT = 120
_ERROR = re.compile(r"^(SCRIPT ERROR|USER SCRIPT ERROR|ERROR:|USER ERROR)", re.MULTILINE)
_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_VERSION = re.compile(r'^config/version\s*=\s*"([^"]*)"', re.MULTILINE)
_DATA_VERSION = re.compile(r"^save/data_version\s*=\s*(\d+)", re.MULTILINE)
#: Words in a script that make it save-relevant.
_SAVE_WORDS = ("save_state", "load_state", "SaveGame", "saveable", "save_id")
#: Findings listed at most per save.
LIMIT = 12


# --- Kept saves -------------------------------------------------------------------------


@dataclass
class Kept:
    """A kept save: ``tests/saves/<version>/<name>.json`` and its summary."""

    path: Path
    version: str
    data: dict[str, Any]
    summary: dict[str, Any]

    @property
    def name(self) -> str:
        return f"{self.version}/{self.path.stem}"

    @property
    def relative(self) -> str:
        return f"{FOLDER.as_posix()}/{self.version}/{self.path.name}"

    @property
    def data_version(self) -> int:
        return int(self.data.get("version", 1) or 1)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.relative,
            "level": self.data.get("level", ""),
            "data_version": self.data_version,
            "playtime": round(float(self.data.get("playtime", 0.0) or 0.0), 1),
            "nodes": len(self.data.get("nodes") or {}),
            "line": self.summary.get("line", ""),
            "loaded": bool(self.summary.get("loaded", False)),
        }


def _envelope(path: Path) -> dict[str, Any]:
    """A save file's envelope; raises ValueError in words when it isn't one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"{path.name} isn't a readable save file.") from None
    shaped = (
        isinstance(data, dict)
        and isinstance(data.get("format"), int | float)
        and isinstance(data.get("nodes"), dict)
        and isinstance(data.get("level"), str)
    )
    if not shaped:
        raise ValueError(f"{path.name} isn't a save made by the game's save system.")
    return dict(data)


def folder(project: Path) -> Path:
    return project / FOLDER


def kept(project: Path) -> list[Kept]:
    """The project's kept saves, oldest version folder first."""
    where = folder(project)
    if not where.is_dir():
        return []
    found: list[Kept] = []
    for path in sorted(where.glob("*/*.json")):
        if path.name.endswith(SUMMARY):
            continue
        try:
            data = _envelope(path)
        except ValueError:
            data = {}
        summary_file = path.with_name(path.stem + SUMMARY)
        try:
            summary = json.loads(summary_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            summary = {}
        found.append(
            Kept(path, path.parent.name, data, summary if isinstance(summary, dict) else {})
        )
    return found


def config(project: Path) -> dict[str, Any]:
    """``[checks] saves``: on by default once the project keeps any save."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    checks = raw.get("checks", {})
    checks = checks if isinstance(checks, dict) else {}
    setting = checks.get("saves")
    have = bool(kept(project))
    return {"saves": have if not isinstance(setting, bool) else setting and have}


def game_version(project: Path, data: dict[str, Any] | None = None) -> str:
    """The folder a save made now is kept under: the game's version, else the save's."""
    try:
        text = (project / "project.godot").read_text(encoding="utf-8")
    except OSError:
        text = ""
    found = _VERSION.search(text)
    if found and found.group(1).strip():
        return _NAME.sub("-", found.group(1).strip()).strip("-.") or "v"
    version = int((data or {}).get("version", 0) or 0)
    if not version:
        setting = _DATA_VERSION.search(text)
        version = int(setting.group(1)) if setting else 1
    return f"save-v{version}"


def _sources(source: Path) -> list[Path]:
    """Slot files in ``source``: a file, a folder of slots, or a playtest run folder."""
    if source.is_file():
        return [source]
    for where in (source / "saves", source):
        if where.is_dir():
            found = sorted(where.glob("slot_*.json"))
            if found:
                return found
    raise ValueError(f"No save files (slot_<n>.json) in {source}.")


def add(
    project: Path,
    source: Path,
    name: str = "",
    version: str = "",
    *,
    load: bool = True,
    godot: Path | None = None,
) -> list[Kept]:
    """Keep the saves in ``source`` as tests; with ``load``, each must load in the game as
    it is now, and what it loaded as becomes its summary."""
    added: list[Kept] = []
    files = _sources(source)
    for index, path in enumerate(files):
        data = _envelope(path)
        label = _NAME.sub("-", version).strip("-.") if version else game_version(project, data)
        stem = name or (source.name if source.is_dir() else path.stem)
        base = _NAME.sub("-", stem).strip("-.") or "save"
        if len(files) > 1:
            base += "-" + path.stem.replace("slot_", "slot")
        target = folder(project) / label / (base.removesuffix(".json") + ".json")
        if target.exists() and target.resolve() != path.resolve():
            raise ValueError(
                f"{FOLDER.as_posix()}/{label}/{target.name} already exists; pick another --name."
            )
        summary: dict[str, Any] = {
            "loaded": False,
            "level": data.get("level", ""),
            "playtime": data.get("playtime", 0.0),
            "line": "",
        }
        if load:
            outcome = run_file(project, path, godot=godot)
            if outcome.raw.get("status") != "loaded":
                raise ValueError(
                    f"{path.name} doesn't load in the game as it is: {outcome.detail}"
                )
            summary = {"loaded": True, **outcome.raw.get("loaded", {})}
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() != path.resolve():
            shutil.copyfile(path, target)
        summary_file = target.with_name(target.stem + SUMMARY)
        summary_file.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        added.append(Kept(target, label, data, summary))
    return added


# --- Comparing --------------------------------------------------------------------------


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return math.isclose(float(a), float(b), rel_tol=1e-4, abs_tol=1e-4)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def show(value: Any, limit: int = 60) -> str:
    """A value as a person reads it, kept short."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _walk(prefix: str, saved: Any, loaded: Any, out: list[str]) -> None:
    """What of ``saved`` is missing or different in ``loaded``, by key path."""
    if isinstance(saved, dict) and isinstance(loaded, dict):
        for key, value in saved.items():
            where = f"{prefix}.{key}" if prefix else str(key)
            if key not in loaded:
                out.append(f"{where} is gone (was {show(value)})")
            else:
                _walk(where, value, loaded[key], out)
        return
    if not _same(saved, loaded):
        out.append(f"{prefix} was {show(saved)}, loads as {show(loaded)}")


def compare(
    raw: dict[str, Any], summary: dict[str, Any] | None = None
) -> list[str]:
    """What a loaded save lost: its own values the game doesn't hold after loading,
    and the slot list's line against the one kept with it."""
    saved = raw.get("saved") or {}
    loaded = raw.get("loaded") or {}
    found: list[str] = []
    level = str(saved.get("level", ""))
    if level and level != loaded.get("level", ""):
        found.append(f"loads into {loaded.get('level') or 'no level'} instead of {level}")
    nodes = loaded.get("nodes") or {}
    for node, state in (saved.get("nodes") or {}).items():
        if node not in nodes:
            found.append(f"node {node} isn't in the game after loading (saved {show(state)})")
            continue
        _walk(node, state, nodes[node], found)
    extra = loaded.get("extra") or {}
    for key, value in (saved.get("extra") or {}).items():
        if key not in extra:
            found.append(f"extra data {key} isn't restored (saved {show(value)})")
        else:
            _walk(f"extra {key}", value, extra[key], found)
    kept_line = str((summary or {}).get("line", "") or "")
    now_line = str(loaded.get("line", "") or "")
    if kept_line and now_line != kept_line:
        found.append(f"the slot list said “{kept_line}” and now says “{now_line}”")
    return found


@dataclass
class Outcome:
    """What one kept save did: ``passed``, ``lost`` (data lost) or ``failed``."""

    name: str
    path: str
    status: str
    detail: str = ""
    lost: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    version: int = 1
    data_version: int = 1
    note: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    def line(self) -> str:
        if self.ok:
            return f"{self.name}: loads with all its data."
        text = f"{self.path}: {self.detail}"
        if self.lost:
            text += " " + "; ".join(self.lost[:LIMIT]) + "."
        if self.errors:
            text += " Errors: " + "; ".join(self.errors[:3]) + "."
        return text

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        data["ok"] = self.ok
        return data


def judge(save: Kept, console: str, code: int | None = None) -> Outcome:
    """One load's outcome from its console: the check's line, compared."""
    raw: dict[str, Any] | None = None
    for line in console.splitlines():
        if line.startswith(RESULT_PREFIX):
            try:
                parsed = json.loads(line[len(RESULT_PREFIX) :])
            except ValueError:
                continue
            if isinstance(parsed, dict):
                raw = parsed
    errors = [line.strip() for line in console.splitlines() if _ERROR.match(line)][:10]
    outcome = Outcome(save.name, save.relative, "failed", errors=errors)
    if raw is None:
        tail = "\n".join(console.strip().splitlines()[-6:])
        outcome.detail = "The game stopped before the save loaded" + (
            f" (exit code {code})" if code else ""
        ) + (f": {tail}" if tail else ".")
        return outcome
    outcome.raw = raw
    outcome.version = int(raw.get("version", 1) or 1)
    outcome.data_version = int(raw.get("data_version", 1) or 1)
    if raw.get("status") != "loaded":
        outcome.detail = "fails to load: " + str(raw.get("reason") or "the game refused it.")
        if outcome.version < outcome.data_version:
            outcome.detail += (
                f" It is a version {outcome.version} save and the game is at "
                f"{outcome.data_version}: a migration step is missing."
            )
        return outcome
    outcome.lost = compare(raw, save.summary)
    if outcome.lost:
        outcome.status = "lost"
        outcome.detail = "loads but loses data:"
    elif errors:
        outcome.status = "failed"
        outcome.detail = "loads with script errors."
    else:
        outcome.status = "passed"
    return outcome


# --- Running ---------------------------------------------------------------------------


def command(project_arg: str, save_arg: str, work: str, *, godot: str, script: str) -> list[str]:
    return [
        godot,
        "--headless",
        "--path",
        project_arg,
        "--fixed-fps",
        "60",
        "--script",
        script,
        "--",
        f"--joinery-save={save_arg}",
        f"--saves-dir={work}/saves",
        f"--settings-path={work}/settings.cfg",
    ]


def godot_binary() -> Path:
    from joinery import replays

    return replays.godot_binary()


def run_file(
    project: Path, path: Path, *, godot: Path | None = None, save: Kept | None = None
) -> Outcome:
    """Load one save file in the game on this machine (``save``: the kept save it is,
    whose summary it is compared with)."""
    project = project.resolve()
    save = save or Kept(path, path.parent.name, {}, {})
    binary = godot or godot_binary()
    from joinery import playtest

    with tempfile.TemporaryDirectory(prefix="joinery-save-") as temp:
        work = Path(temp)
        (work / "saves").mkdir()
        script = work / "save_check.gd"
        shutil.copyfile(CHECK_SCRIPT, script)
        env = playtest._env(work, {})
        playtest._ensure_imported(binary, project, env)
        argv = command(
            str(project), str(path.resolve()), str(work), godot=str(binary), script=str(script)
        )
        try:
            done = subprocess.run(
                argv, env=env, capture_output=True, text=True, timeout=TIMEOUT, check=False
            )
        except subprocess.TimeoutExpired:
            return Outcome(save.name, str(path), "failed", f"It didn't load in {TIMEOUT} s.")
    return judge(save, done.stdout + done.stderr, done.returncode)


def check(project: Path, *, godot: Path | None = None) -> list[Outcome]:
    """Load every kept save here and compare it with its summary."""
    return [run_file(project, save.path, godot=godot, save=save) for save in kept(project)]


# --- Which changes can break saves -------------------------------------------------------


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def relevant(stage: Path, original: Path, changes: Iterable[dict[str, str]]) -> bool:
    """True when a change touches what saves are made of: the project settings, the save
    system, the migrations, or a script or scene that saves or is saved (``save_state``,
    ``load_state``, ``SaveGame``, the ``saveable`` group), before or after the change."""
    for change in changes:
        rel = change["path"]
        if rel == "project.godot" or rel == MIGRATIONS or rel.startswith("features/save-load/"):
            return True
        if not rel.endswith((".gd", ".tscn", ".tres", ".scn")):
            continue
        for root in (stage, original):
            text = _text(root / rel)
            if any(word in text for word in _SAVE_WORDS):
                return True
    return False


# --- The staged-change check -----------------------------------------------------------

OfflineRun = Callable[[list[str], Path, int], Awaitable[Result]]
#: Brings a worker copy's import cache up to date before loading.
IMPORT = "godot --headless --path /work --editor --import >/dev/null 2>&1; "
_SCRIPT_PATH = "/tmp/joinery-save-check/save_check.gd"


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def _script(saves: list[Kept], tag: str) -> str:
    """One shell script that loads each kept save in the worker, each result marked."""
    source = CHECK_SCRIPT.read_text(encoding="utf-8")
    parts = [
        "mkdir -p /tmp/joinery-save-check",
        f"printf '%s' {_quote(source)} > {_SCRIPT_PATH}",
    ]
    for index, save in enumerate(saves):
        work = f"/tmp/{tag}-save-{index}"
        argv = command("/work", f"/work/{save.relative}", work, godot="godot", script=_SCRIPT_PATH)
        parts.append(
            f"mkdir -p {_quote(work + '/saves')}; "
            f"echo {_quote('SAVE_BEGIN ' + save.name)}; "
            + " ".join(_quote(a) for a in argv)
            + f" 2>&1; echo {_quote('SAVE_END ' + save.name)}"
        )
    return "; ".join(parts)


def split_runs(output: str) -> dict[str, str]:
    runs: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in output.splitlines():
        if line.startswith("SAVE_BEGIN "):
            current, lines = line.split(" ", 1)[1].strip(), []
        elif line.startswith("SAVE_END ") and current is not None:
            runs[current] = "\n".join(lines)
            current = None
        elif current is not None:
            lines.append(line)
    return runs


def _findings(outcome: Outcome) -> list[str]:
    """What is wrong with one load, comparable before and after a change."""
    if outcome.status == "failed":
        return ["failed: " + outcome.detail.split(" It is a version")[0]]
    return list(outcome.lost)


async def check_stage(
    project: Path,
    stage: Path,
    runner: OfflineRun,
    timeout: int = 600,
    *,
    imported: bool = False,
) -> list[Outcome]:
    """Load the kept saves in the staged change, in the offline worker; only what the
    change made worse than the same game before it fails."""
    saves = [s for s in kept(stage) if s.data]
    if not saves:
        return []
    got = await runner(
        ["sh", "-c", ("" if imported else IMPORT) + _script(saves, "after")], stage, timeout
    )
    after = split_runs(got.output)
    baseline = await _baseline(project, saves, runner, timeout)
    outcomes: list[Outcome] = []
    for save in saves:
        console = after.get(save.name)
        if console is None:
            outcomes.append(
                Outcome(save.name, save.relative, "failed", "It didn't run: " + got.output[-600:])
            )
            continue
        outcome = judge(save, console)
        before = baseline.get(save.name)
        if not outcome.ok and isinstance(before, list):
            new = [f for f in _findings(outcome) if f not in before]
            if not new:
                outcome.status = "passed"
                outcome.note = "Did the same before this change."
            elif outcome.status == "lost":
                outcome.lost = [f for f in outcome.lost if f not in before]
        outcomes.append(outcome)
    return outcomes


def fingerprint(root: Path) -> str:
    from joinery import replays

    return replays.fingerprint(root)


async def _baseline(
    project: Path, saves: list[Kept], runner: OfflineRun, timeout: int
) -> dict[str, list[str]]:
    """What each save does in the project without the change (cached per game state)."""
    cache = project / BASELINES / f"baseline-{fingerprint(project)}.json"
    try:
        stored = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    keys = {
        s.name: hashlib.sha256(
            s.path.read_bytes() + json.dumps(s.summary, sort_keys=True).encode()
        ).hexdigest()[:16]
        for s in saves
    }
    missing = [s for s in saves if (stored.get(s.name) or {}).get("key") != keys[s.name]]
    if missing:
        from joinery.transactions import clone_tree

        scratch = Path(tempfile.mkdtemp(prefix="joinery-save-base-"))
        try:
            copy = scratch / "project"
            clone_tree(project, copy)
            shutil.rmtree(copy / ".godot", ignore_errors=True)
            prepared = []
            for save in missing:
                target = copy / save.relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(save.path, target)
                prepared.append(Kept(target, save.version, save.data, save.summary))
            got = await runner(
                ["sh", "-c", IMPORT + _script(prepared, "before")], copy, timeout
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        runs = split_runs(got.output)
        for save in missing:
            console = runs.get(save.name)
            if console is None:
                continue
            stored[save.name] = {
                "key": keys[save.name],
                "findings": _findings(judge(save, console)),
            }
        cache.parent.mkdir(parents=True, exist_ok=True)
        for old in cache.parent.glob("baseline-*.json"):
            if old != cache:
                old.unlink(missing_ok=True)
        cache.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    return {
        name: list(entry.get("findings", []))
        for name, entry in stored.items()
        if isinstance(entry, dict)
    }


def problems_text(outcomes: list[Outcome]) -> str:
    """The send-back message for kept saves a staged change broke."""
    failed = [o for o in outcomes if not o.ok]
    version = max((o.data_version for o in failed), default=1)
    return (
        "An old save no longer loads as it did: "
        + " ".join(o.line() for o in failed)
        + f" Players have saves like these. Raise save/data_version in project.godot to "
        f"{version + 1} and add a step for version {version} in res://{MIGRATIONS} "
        "(func migrations() -> Dictionary: {"
        f"{version}: Callable"
        "}; the step takes the decoded save {nodes, extra, options, level} and returns "
        "it in the new shape), in this change."
    )


# --- CLI -------------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    add_cmd = actions.add_parser("add", help="keep saves as tests in tests/saves/<version>/")
    add_cmd.add_argument("source", type=Path, help="a slot file, or a playtest run folder")
    add_cmd.add_argument("--project", type=Path, required=True)
    add_cmd.add_argument("--name", default="", help="the save's name in tests/saves/")
    add_cmd.add_argument("--version", default="", help="the folder (default: the game's version)")
    add_cmd.add_argument(
        "--no-load", action="store_true", help="keep it without loading it first"
    )
    add_cmd.add_argument("--json", action="store_true")
    list_cmd = actions.add_parser("list", help="list the kept saves")
    list_cmd.add_argument("--project", type=Path, required=True)
    list_cmd.add_argument("--json", action="store_true")
    check_cmd = actions.add_parser("check", help="load every kept save and compare")
    check_cmd.add_argument("--project", type=Path, required=True)
    check_cmd.add_argument("--json", action="store_true")


def main(args: argparse.Namespace) -> int:
    project: Path = args.project.resolve()
    if not (project / "project.godot").is_file():
        print(f"{project} is not a Godot project.", file=sys.stderr)
        return 2
    if args.action == "add":
        try:
            added = add(project, args.source, args.name, args.version, load=not args.no_load)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps([k.describe() for k in added], indent=2))
        for item in [] if args.json else added:
            info = item.describe()
            print(
                f"Kept {info['path']} ({info['level']}, {info['nodes']} saved nodes"
                + (f", “{info['line']}”" if info["line"] else "")
                + ")."
            )
        return 0
    if args.action == "list":
        listed = [k.describe() for k in kept(project)]
        if args.json:
            print(json.dumps(listed, indent=2))
        elif not listed:
            print("No kept saves. After a playtest: joinery saves add <run folder>.")
        for entry in [] if args.json else listed:
            print(
                f"- {entry['name']}: version {entry['data_version']} save in "
                f"{entry['level']}" + (f", “{entry['line']}”" if entry["line"] else "")
            )
        return 0
    outcomes = check(project)
    if args.json:
        print(json.dumps([o.as_dict() for o in outcomes], indent=2))
    elif not outcomes:
        print("No kept saves in tests/saves/.")
    for outcome in [] if args.json else outcomes:
        print(outcome.line())
    return 0 if all(o.ok for o in outcomes) else 1
