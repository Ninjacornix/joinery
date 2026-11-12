"""Project commands with parameters: ``.joinery/commands/<name>.toml`` (ADR 0077).

A command is a request the team makes often ("new enemy", "new room"), written once
with blanks to fill in::

    title = "New enemy"
    description = "Add an enemy with a behaviour"
    prompt = "Make an enemy called {{name}} that {{behaviour}}s at {{speed}} m/s."

    [[parameters]]
    name = "name"
    description = "What it's called"

    [[parameters]]
    name = "behaviour"
    type = "choice"
    choices = ["patrol", "chase", "guard"]
    default = "patrol"

    [[parameters]]
    name = "speed"
    type = "number"
    default = 2.5

    # Optional: what a finished run must show (checked by `joinery command run`).
    [[checks]]
    file = "enemies/{{name}}.tscn"        # this file is in the change
    [[checks]]
    reply = "(?i)enemy"                     # the agent's reply matches

    # Optional: other commands whose requests come first, in order.
    [[subcommands]]
    name = "new-room"
    params = { theme = "cave" }

Parameter types are ``string`` (the default), ``integer``, ``number``, ``boolean``
and ``choice``. A parameter without a ``default`` must be given. The name is the
file's stem: lower case, digits and dashes, and not one of the sidebar's own
commands. In the sidebar each shows as ``/<name>`` and opens a small form; on the
command line ``joinery command run <name> --param k=v`` runs it headless
(``joinery.headless``), leaving its change for review, then runs its checks.

The commands live under ``.joinery/commands/`` and are shared through git like the
rest of the project (``timeline.IGNORE_RULES`` keeps them in).
"""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

FOLDER = ".joinery/commands"
NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
#: The sidebar's own commands (slash_commands.gd COMMANDS): a project command
#: can't take their names.
RESERVED = frozenset(
    {
        "access",
        "agents",
        "art",
        "blockout",
        "brief",
        "check",
        "debug",
        "design",
        "doctor",
        "feature",
        "focus",
        "help",
        "idle",
        "match",
        "models",
        "perf",
        "playtest",
        "rebuild",
        "record",
        "remember",
        "setup",
        "sound-pass",
        "tidy",
        "toolbar",
        "tools",
        "tour",
        "variant",
        "variants",
    }
)
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
#: How deep sub-commands may nest.
DEPTH = 4
Kind = Literal["string", "integer", "number", "boolean", "choice"]


class Parameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
    type: Kind = "string"
    description: str = ""
    default: str | int | float | bool | None = None
    choices: list[str] = []

    @model_validator(mode="after")
    def _fits(self) -> Parameter:
        if self.type == "choice" and not self.choices:
            raise ValueError(f"{self.name}: a choice needs its choices.")
        if self.type != "choice" and self.choices:
            raise ValueError(f"{self.name}: only a choice has choices.")
        if self.default is not None:
            self.default = self.value(self.default)
        return self

    @property
    def required(self) -> bool:
        return self.default is None

    def value(self, given: object) -> str | int | float | bool:
        """``given`` as this parameter's type; a ValueError says what it wants."""
        text = str(given).strip()
        try:
            if self.type == "integer":
                if isinstance(given, bool):
                    raise ValueError
                return int(given) if isinstance(given, int) else int(text)
            if self.type == "number":
                if isinstance(given, bool):
                    raise ValueError
                number = float(given) if isinstance(given, int | float) else float(text)
                return int(number) if number.is_integer() else number
            if self.type == "boolean":
                if isinstance(given, bool):
                    return given
                if text.lower() in {"true", "yes", "on", "1"}:
                    return True
                if text.lower() in {"false", "no", "off", "0"}:
                    return False
                raise ValueError
        except ValueError:
            raise ValueError(f"{self.name} is {_article(self.type)}.") from None
        if self.type == "choice" and text not in self.choices:
            raise ValueError(f"{self.name} is one of: {', '.join(self.choices)}.")
        return text


def _article(kind: str) -> str:
    return {
        "integer": "a whole number",
        "number": "a number",
        "boolean": "yes or no",
    }.get(kind, kind)


class Check(BaseModel):
    """One thing a finished run must show; exactly one of its fields."""

    model_config = ConfigDict(extra="forbid")

    #: A project path (``res://`` optional) the change creates or keeps.
    file: str = ""
    #: A glob some file the change touches matches ("enemies/*.gd").
    changed: str = ""
    #: A regular expression the agent's reply matches.
    reply: str = ""

    @model_validator(mode="after")
    def _one(self) -> Check:
        if sum(bool(v) for v in (self.file, self.changed, self.reply)) != 1:
            raise ValueError("A check has one of file, changed or reply.")
        if self.reply:
            re.compile(self.reply)
        return self


class Sub(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    params: dict[str, str | int | float | bool] = {}


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    title: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=80)
    prompt: str = Field(min_length=1, max_length=8000)
    parameters: list[Parameter] = []
    checks: list[Check] = []
    subcommands: list[Sub] = []

    @model_validator(mode="after")
    def _placeholders(self) -> Command:
        names = [p.name for p in self.parameters]
        if len(set(names)) != len(names):
            raise ValueError("Two parameters have the same name.")
        texts = [self.prompt, *(c.file or c.changed for c in self.checks)]
        texts += [str(v) for s in self.subcommands for v in s.params.values()]
        for text in texts:
            for found in PLACEHOLDER.findall(text):
                if found not in names:
                    raise ValueError(f"{{{{{found}}}}} isn't one of its parameters.")
        return self

    def summary(self) -> dict[str, Any]:
        """What the sidebar's menu and form need."""
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description or self.title,
            "parameters": [
                {**p.model_dump(), "required": p.required} for p in self.parameters
            ],
            "checks": len(self.checks),
            "subcommands": [s.name for s in self.subcommands],
        }


def folder(project: Path) -> Path:
    return project / FOLDER


def load_file(path: Path) -> Command:
    name = path.stem
    if not NAME.match(name):
        raise ValueError(
            f"{path.name}: a command's name is lower case letters, digits and dashes."
        )
    if name in RESERVED:
        raise ValueError(f"{path.name}: /{name} is one of the sidebar's own commands.")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{path.name}: {exc}") from None
    try:
        command = Command.model_validate({**data, "name": name})
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        message = str(first.get("msg", "")).removeprefix("Value error, ")
        raise ValueError(
            f"{path.name}: {where + ': ' if where else ''}{message}"
        ) from None
    return command


def load(project: Path) -> tuple[dict[str, Command], list[str]]:
    """Every command in the project by name, and a line for each file that's wrong."""
    found: dict[str, Command] = {}
    problems: list[str] = []
    root = folder(project)
    if not root.is_dir() or root.is_symlink():
        return found, problems
    for path in sorted(root.glob("*.toml")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            command = load_file(path)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        found[command.name] = command
    for command in found.values():
        for sub in command.subcommands:
            if sub.name not in found:
                problems.append(f"{command.name}.toml: no command called {sub.name}.")
    return found, problems


def get(project: Path, name: str) -> Command:
    found, problems = load(project)
    if name in found:
        return found[name]
    wrong = [p for p in problems if p.startswith(f"{name}.toml")]
    raise ValueError(wrong[0] if wrong else f"No command called {name}.")


def values(command: Command, given: dict[str, Any]) -> dict[str, Any]:
    """Every parameter's value: given ones checked and typed, defaults for the rest."""
    known = {p.name: p for p in command.parameters}
    unknown = sorted(set(given) - set(known))
    if unknown:
        raise ValueError(f"/{command.name} has no parameter {unknown[0]}.")
    result: dict[str, Any] = {}
    for parameter in command.parameters:
        raw = given.get(parameter.name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if parameter.required:
                raise ValueError(f"/{command.name} needs {parameter.name}.")
            result[parameter.name] = parameter.default
        else:
            result[parameter.name] = parameter.value(raw)
    return result


def fill(template: str, filled: dict[str, Any]) -> str:
    def put(match: re.Match[str]) -> str:
        value = filled[match.group(1)]
        if isinstance(value, bool):
            return "yes" if value else "no"
        return str(value)

    return PLACEHOLDER.sub(put, template)


def render(
    commands: dict[str, Command],
    name: str,
    given: dict[str, Any],
    _seen: tuple[str, ...] = (),
) -> list[str]:
    """The requests ``name`` makes, its sub-commands' first, each filled in."""
    if name in _seen:
        raise ValueError("Commands call each other in a loop: " + " → ".join(_seen))
    if len(_seen) >= DEPTH:
        raise ValueError(f"Sub-commands nest deeper than {DEPTH}.")
    if name not in commands:
        raise ValueError(f"No command called {name}.")
    command = commands[name]
    filled = values(command, given)
    parts: list[str] = []
    for sub in command.subcommands:
        params = {k: fill(str(v), filled) for k, v in sub.params.items()}
        parts += render(commands, sub.name, params, (*_seen, name))
    parts.append(fill(command.prompt, filled).strip())
    return parts


def request(project: Path, name: str, given: dict[str, Any]) -> str:
    """One request for the agent: a single prompt as it is, or the steps in order."""
    commands, _ = load(project)
    if name not in commands:
        get(project, name)  # Raises with the file's own problem.
    parts = render(commands, name, given)
    if len(parts) == 1:
        return parts[0]
    steps = "\n".join(f"{i}. {p}" for i, p in enumerate(parts, 1))
    return f"Do these in order, as one change:\n{steps}"


def check(
    project: Path,
    command: Command,
    given: dict[str, Any],
    files: list[str],
    reply: str,
    stage: Path | None,
) -> list[dict[str, Any]]:
    """The command's checks against a run: the files its change touched, the staged
    copy (or the project) and the agent's reply. [{check, ok, why}]."""
    filled = values(command, given)
    results: list[dict[str, Any]] = []
    for item in command.checks:
        if item.file:
            target = fill(item.file, filled).removeprefix("res://").strip("/")
            root = stage if stage is not None and stage.is_dir() else project
            ok = (root / target).is_file()
            results.append(
                {
                    "check": f"file {target}",
                    "ok": ok,
                    "why": ""
                    if ok
                    else f"{target} isn't in the project after the run.",
                }
            )
        elif item.changed:
            pattern = fill(item.changed, filled).removeprefix("res://").strip("/")
            ok = any(fnmatch.fnmatch(f, pattern) for f in files)
            results.append(
                {
                    "check": f"changed {pattern}",
                    "ok": ok,
                    "why": "" if ok else f"The change touches nothing like {pattern}.",
                }
            )
        else:
            ok = re.search(item.reply, reply) is not None
            results.append(
                {
                    "check": f"reply {item.reply}",
                    "ok": ok,
                    "why": "" if ok else "The reply doesn't say what it should.",
                }
            )
    return results


def parse_params(pairs: list[str]) -> dict[str, str]:
    given: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"Write each parameter as name=value, not {pair!r}.")
        given[key.strip()] = value
    return given


# --- CLI ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``joinery command list|show|render|run``."""
    from joinery import headless

    parser = subparsers.add_parser(
        "command", help="the project's own commands (.joinery/commands/*.toml)"
    )
    actions = parser.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="every command in the project")
    listing.add_argument("--project", type=Path, required=True)
    listing.add_argument("--json", action="store_true")
    for action, words in (
        ("render", "print the request a command makes"),
        ("run", "run a command without the editor; its change waits for review"),
    ):
        sub = actions.add_parser(action, help=words)
        sub.add_argument("name")
        sub.add_argument(
            "--param", action="append", default=[], help="name=value (repeat)"
        )
        if action == "run":
            headless.add_arguments(sub)
        else:
            sub.add_argument("--project", type=Path, required=True)
            sub.add_argument("--json", action="store_true")


def _say(as_json: bool, data: dict[str, Any], text: str) -> None:
    print(json.dumps(data) if as_json else text, flush=True)


def run(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    if args.action == "list":
        found, problems = load(project)
        if args.json:
            print(
                json.dumps(
                    {
                        "commands": [c.summary() for c in found.values()],
                        "problems": problems,
                    }
                )
            )
            return 0
        for command in found.values():
            print(f"/{command.name:20} {command.description or command.title}")
        for problem in problems:
            print(f"! {problem}")
        if not found and not problems:
            print(f"No commands yet: add one under {FOLDER}/<name>.toml.")
        return 0
    try:
        given = parse_params(args.param)
        text = request(project, args.name, given)
    except ValueError as exc:
        _say(args.json, {"error": str(exc)}, str(exc))
        return 2
    if args.action == "render":
        _say(args.json, {"name": args.name, "request": text}, text)
        return 0
    return _run(args, project, given, text)


def _run(
    args: argparse.Namespace, project: Path, given: dict[str, Any], text: str
) -> int:
    from joinery import headless
    from joinery.transactions import FileTransaction

    command = get(project, args.name)
    try:
        pin = headless.pin_from(args, project)
        outcome = asyncio.run(
            headless.run_request(
                project,
                text,
                pin,
                on_event=headless.printer(args.json),
                timeout=args.timeout,
            )
        )
    except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
        outcome = headless.Outcome(state="failed", error=str(exc))
    code = headless.finish(outcome, project, args.json)
    if not command.checks or outcome.state == "failed":
        return code
    files: list[str] = []
    stage: Path | None = None
    if outcome.transaction_id:
        try:
            tx = FileTransaction.load(project, outcome.transaction_id)
            files = [c["path"] for c in tx.changes]
            stage = tx.stage
        except (OSError, ValueError, KeyError):
            pass
    results = check(project, command, given, files, outcome.reply, stage)
    failed = [r for r in results if not r["ok"]]
    if args.json:
        print(json.dumps({"kind": "command_checks", "checks": results}), flush=True)
    else:
        for result in results:
            print(("ok    " if result["ok"] else "FAIL  ") + result["check"])
            if result["why"]:
                print("      " + result["why"])
    return 3 if failed else code
