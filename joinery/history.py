"""How much history a project keeps, and compacting it.

History is three things: restore data for accepted changes (so they can be
reverted), the immutable documents incremental edits start from
(``recipes/inputs``) and the recipes later edits replaced (``recipes/history``).

The project's policy (``[history]`` in joinery.toml, ``policy``) bounds the
first: older versions of models, textures and sounds a change replaced stay
only for the last ``keep`` accepted changes, within ``max_mb``. The other two
are what replay needs or old recipes, and only ``compact`` removes them.

``compact`` makes the current sources the new base. It drops every
transaction's restore data (journals stay, marked not revertible), replaced
recipes, and starting documents no current recipe needs. The documents the
current recipes do need are renamed to their content hash, so identical ones
are stored once, and those recipes are rewritten to match: replaying the
current state (``joinery rebuild``) keeps working.

The command line entry is :func:`register`, wired into ``joinery`` by cli.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from joinery.recipe import (
    HISTORY_DIR,
    PREIMAGE_DIR,
    load_recipe,
    recipe_paths,
)
from joinery.transactions import (
    PENDING,
    POLICY_PRESETS,
    TRANSACTIONS,
    HistoryLock,
    HistoryPolicy,
    apply_retention,
    copy_file,
    list_transactions,
    load_policy,
    tree_bytes,
)

#: A preimage reference inside a recipe or its script.
_PREIMAGE = re.compile(r"recipes/inputs/[A-Za-z0-9._-]+")
#: A transaction folder without a journal is only removed once it is this old,
#: so a change that is starting right now is never touched.
_ORPHAN_AGE = 24 * 60 * 60


def _project(path: Path) -> Path:
    project = path.resolve()
    if not (project / "project.godot").is_file():
        raise ValueError("Expected a Godot project.")
    return project


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _relative(project: Path, path: Path) -> str:
    return path.relative_to(project).as_posix()


@dataclass
class Plan:
    """What compacting would change. Paths are project-relative."""

    #: Current recipe files, and the scripts they run.
    recipes: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    #: Replaced recipes and the scripts only they used.
    superseded: list[str] = field(default_factory=list)
    #: Preimages no current recipe refers to.
    unused: list[str] = field(default_factory=list)
    #: Preimage still in use -> its content-addressed path.
    renames: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def preimage_savings(self, project: Path) -> int:
        """Bytes freed by renaming identical preimages onto one file."""
        kept: dict[str, int] = {}
        total = 0
        for old, new in self.renames.items():
            size = _size(project / old)
            total += size
            kept[new] = size
        return total - sum(kept.values())


def _references(text: str) -> set[str]:
    return set(_PREIMAGE.findall(text))


def plan(project: Path) -> Plan:
    """Work out what compacting would change, without changing anything."""
    project = _project(project)
    result = Plan()
    referenced: set[str] = set()
    certain = True
    for path in recipe_paths(project):
        result.recipes.append(_relative(project, path))
        referenced |= _references(path.read_text(errors="replace"))
        try:
            recipe = load_recipe(path)
        except Exception as exc:  # noqa: BLE001 - reported, and nothing is pruned.
            result.problems.append(f"{_relative(project, path)}: unreadable: {exc}")
            certain = False
            continue
        referenced |= {s for s in recipe.sources if s.startswith(PREIMAGE_DIR + "/")}
        script = project / recipe.script
        result.scripts.append(recipe.script)
        if script.is_file():
            referenced |= _references(script.read_text(errors="replace"))
    keep = set(result.recipes) | set(result.scripts)
    for path in sorted((project / HISTORY_DIR).glob("*.toml")):
        result.superseded.append(_relative(project, path))
        try:
            old = load_recipe(path).script
        except Exception:  # noqa: BLE001 - an unreadable old recipe is still history.
            old = ""
        if (
            old
            and old not in keep
            and old not in result.superseded
            and Path(old).parts[:1] == ("recipes",)
            and Path(old).parent.as_posix() != PREIMAGE_DIR
            and (project / old).is_file()
        ):
            result.superseded.append(old)
    folder = project / PREIMAGE_DIR
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        if not path.is_file() or path.is_symlink() or path.name == ".gdignore":
            continue
        name = _relative(project, path)
        if name not in referenced:
            if certain:
                result.unused.append(name)
            continue
        new = f"{PREIMAGE_DIR}/{_digest(path)}{path.suffix}"
        if new != name:
            result.renames[name] = new
    return result


def usage(project: Path) -> dict[str, Any]:
    """Bytes of history a project keeps, and how much compacting would free."""
    project = _project(project)
    found = list_transactions(project)
    restore = leftover = staging = 0
    revertible = pending = 0
    for tx in found:
        if tx.state in PENDING:
            pending += 1
            staging += tree_bytes(tx.directory)
            continue
        leftover += tx.leftover_bytes()
        restore += tx.restore_bytes()
        if tx.state == "accepted" and tx.revertible:
            revertible += 1
    # Journals of finished changes, plus the live-scene edit records beside them.
    folder = project / TRANSACTIONS
    records = folder.glob("*.json") if folder.is_dir() else iter(())
    journals = sum(_size(path) for path in records) + sum(
        _size(tx.journal) for tx in found if tx.state not in PENDING
    )
    preimages = tree_bytes(project / PREIMAGE_DIR)
    recipes = plan(project)
    superseded = sum(_size(project / path) for path in recipes.superseded)
    unused = sum(_size(project / path) for path in recipes.unused)
    problems = list(recipes.problems)
    try:
        policy = _policy_data(load_policy(project))
    except ValueError as exc:
        policy = {}
        problems.append(str(exc))
    total = restore + leftover + journals + preimages + superseded
    return {
        "project": str(project),
        "total_bytes": total,
        "restore_bytes": restore,
        "leftover_bytes": leftover,
        "journal_bytes": journals,
        "preimage_bytes": preimages,
        "history_recipe_bytes": superseded,
        "staging_bytes": staging,
        "reclaimable_bytes": restore
        + leftover
        + superseded
        + unused
        + recipes.preimage_savings(project),
        "transactions": {
            "total": len(found),
            "revertible": revertible,
            "pending": pending,
        },
        "policy": policy,
        "problems": problems,
    }


# --- Policy -------------------------------------------------------------------------

#: The ``[history]`` table, up to the next table header.
_HISTORY_TABLE = re.compile(
    r"^\[history\][ \t]*(?:#[^\n]*)?\n(?:(?!\[)[^\n]*(?:\n|$))*", re.MULTILINE
)


def _policy_data(policy: HistoryPolicy) -> dict[str, Any]:
    return {**policy.model_dump(), "preset": policy.preset, "label": policy.label}


def make_policy(
    preset: str | None = None,
    keep: int | None = None,
    max_mb: int | None = None,
    base: HistoryPolicy | None = None,
) -> HistoryPolicy | None:
    """A policy from a preset id or numbers (unset numbers come from ``base``).

    None when nothing was chosen. Raises ValueError with a plain reason.
    """
    numbers = keep is not None or max_mb is not None
    if preset is None and not numbers:
        return None
    if preset not in (None, "custom"):
        if preset not in POLICY_PRESETS:
            raise ValueError(
                f"Unknown history choice: {preset}. Choose one of "
                + ", ".join([*POLICY_PRESETS, "custom"])
                + "."
            )
        if numbers:
            raise ValueError("Choose a history preset or your own numbers, not both.")
        return POLICY_PRESETS[preset]
    if not numbers:
        raise ValueError(
            "A custom history needs how many changes to keep, the space, or both."
        )
    start = base or HistoryPolicy()
    if keep is not None and keep < 1:
        raise ValueError("Keep at least 1 change: the newest always stays revertible.")
    if max_mb is not None and max_mb < 1:
        raise ValueError("Allow at least 1 MB of history.")
    return HistoryPolicy(
        keep=start.keep if keep is None else keep,
        max_mb=start.max_mb if max_mb is None else max_mb,
    )


def write_policy(project: Path, policy: HistoryPolicy) -> None:
    """Save ``[history]`` in joinery.toml, keeping everything else as it was."""
    path = project / "joinery.toml"
    text = path.read_text() if path.is_file() else ""
    block = (
        "[history]\n"
        "# Older versions kept so accepted changes can be reverted.\n"
        f"keep = {policy.keep}\n"
        f"max_mb = {policy.max_mb}\n"
    )
    match = _HISTORY_TABLE.search(text)
    if match:
        rest = text[match.end() :]
        updated = text[: match.start()] + block + ("\n" + rest if rest else "")
    else:
        lead = text.rstrip("\n") + "\n\n" if text.strip() else ""
        updated = lead + block
    try:
        saved = tomllib.loads(updated).get("history", {})
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"joinery.toml can't be read: {exc}") from None
    if HistoryPolicy.model_validate(saved) != policy:
        raise ValueError("joinery.toml keeps [history] in a form Joinery can't edit.")
    descriptor, temp = tempfile.mkstemp(prefix=".joinery-", dir=project)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(updated)
        if path.is_file():
            os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def set_policy(
    project: Path, policy: HistoryPolicy | None, dry_run: bool = False
) -> dict[str, Any]:
    """Read the policy, or save one and prune what it no longer keeps.

    With ``dry_run`` nothing changes; the result says what saving would free.
    """
    project = project.resolve()
    try:
        previous: HistoryPolicy | None = load_policy(project)
    except ValueError:
        previous = None  # An unreadable [history] is replaced by the new one.
    chosen = policy or previous or HistoryPolicy()
    result: dict[str, Any] = {
        "project": str(project),
        "policy": _policy_data(chosen),
        "previous": _policy_data(previous) if previous else None,
        "changed": policy is not None and policy != previous,
        "dry_run": dry_run,
        "pruned": 0,
        "freed_bytes": 0,
    }
    if policy is None:
        return result
    if not dry_run:
        write_policy(project, policy)
    outcome = apply_retention(project, policy, dry_run=dry_run)
    result["pruned"] = outcome["pruned"]
    result["freed_bytes"] = outcome["pruned_bytes"]
    return result


def describe_policy(data: dict[str, Any]) -> str:
    """One sentence for people: what is kept and, after a change, what it freed."""
    line = f"Keeping older versions: {data['policy']['label']}."
    pruned = int(data["pruned"])
    if pruned:
        changes = f"{pruned} older change{'s' if pruned != 1 else ''}"
        size = human_size(int(data["freed_bytes"]))
        if data["dry_run"]:
            line += f" This frees {size}; {changes} could no longer be reverted."
        else:
            line += f" Freed {size}; {changes} can no longer be reverted."
    return line


def add_policy_arguments(parser: argparse.ArgumentParser) -> None:
    """``--history PRESET`` or ``--history-keep N`` / ``--history-max-mb M``."""
    parser.add_argument(
        "--history",
        choices=[*POLICY_PRESETS, "custom"],
        help="older versions to keep: keep-50 (default), keep-10, latest, or custom",
    )
    parser.add_argument("--history-keep", type=int, metavar="N")
    parser.add_argument("--history-max-mb", type=int, metavar="M")


def policy_from_args(
    args: argparse.Namespace, base: HistoryPolicy | None = None
) -> HistoryPolicy | None:
    """The policy :func:`add_policy_arguments` asked for, or None."""
    return make_policy(args.history, args.history_keep, args.history_max_mb, base)


def _rewrite(path: Path, renames: dict[str, str]) -> bool:
    text = path.read_text()
    # Whole references only: one name is never rewritten inside a longer one.
    updated = _PREIMAGE.sub(lambda match: renames.get(match[0], match[0]), text)
    if updated == text:
        return False
    descriptor, temp = tempfile.mkstemp(prefix=".joinery-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(updated)
        os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return True


def compact(project: Path) -> dict[str, Any]:
    """Make the current sources the new base and drop older history.

    Every step leaves replay working if it is interrupted: content-addressed
    copies are made first, recipes are rewritten one file at a time, and only
    then is anything deleted.
    """
    project = _project(project)
    before = usage(project)["total_bytes"]
    known = set(verify(project))
    with HistoryLock(project):
        found = list_transactions(project)
        if any(tx.state in PENDING for tx in found):
            raise ValueError(
                "A change is waiting for review. Accept or reject it, then compact."
            )
        work = plan(project)
        if work.problems:
            raise ValueError(
                "Some recipes can't be read, so nothing was removed: "
                + "; ".join(work.problems)
            )
        for old, new in work.renames.items():
            target = project / new
            if not target.is_file() or _digest(target) != target.name[:64]:
                copy_file(project / old, target)
        rewritten = 0
        for relative in [*work.recipes, *work.scripts]:
            path = project / relative
            if path.is_file() and _rewrite(path, work.renames):
                rewritten += 1
        for relative in work.recipes:
            load_recipe(project / relative)  # A rewrite must not break a recipe.
        removed = [*work.superseded, *work.unused, *work.renames]
        for relative in removed:
            (project / relative).unlink(missing_ok=True)
        history = project / HISTORY_DIR
        if history.is_dir() and not any(history.iterdir()):
            history.rmdir()
        pruned = 0
        for tx in found:
            if tx.state == "accepted" and tx.revertible:
                pruned += 1
            tx.drop_restore_data("compacted")
        orphans = 0
        folder = project / TRANSACTIONS
        for path in folder.iterdir() if folder.is_dir() else []:
            if (
                path.is_dir()
                and not path.is_symlink()
                and not (path / "journal.json").exists()
                and time.time() - path.stat().st_mtime > _ORPHAN_AGE
            ):
                shutil.rmtree(path, ignore_errors=True)
                orphans += 1
    # Only report what compacting broke; older problems are not its doing.
    problems = [problem for problem in verify(project) if problem not in known]
    after = usage(project)
    return {
        "project": str(project),
        "total_bytes": after["total_bytes"],
        "freed_bytes": max(0, before - after["total_bytes"]),
        "transactions_pruned": pruned,
        "recipes_removed": len(work.superseded),
        "preimages_removed": len(work.unused),
        "preimages_renamed": len(work.renames),
        "files_rewritten": rewritten,
        "orphans_removed": orphans,
        "problems": problems,
    }


def verify(project: Path) -> list[str]:
    """Every current recipe can still find its script and starting documents."""
    problems = []
    for path in recipe_paths(project):
        try:
            recipe = load_recipe(path)
        except Exception as exc:  # noqa: BLE001 - reported to the caller.
            problems.append(f"{_relative(project, path)}: unreadable: {exc}")
            continue
        for needed in (recipe.script, *recipe.sources):
            if not (project / needed).is_file():
                problems.append(f"{_relative(project, path)}: {needed} is missing")
    return problems


def human_size(count: int) -> str:
    """Bytes for people, in the units the settings page uses."""
    value = float(count)
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while value >= 1000 and index < len(units) - 1:
        value /= 1000
        index += 1
    digits = 1 if index >= 3 and value < 10 else 0
    return f"{value:.{digits}f} {units[index]}"


def _describe(action: str, data: dict[str, Any]) -> str:
    if action == "usage":
        line = f"History uses {human_size(data['total_bytes'])}"
        changes = data["transactions"]["revertible"]
        line += (
            f"; {changes} accepted change{'s' if changes != 1 else ''} can be reverted."
        )
        if data["reclaimable_bytes"]:
            line += f" Compacting frees about {human_size(data['reclaimable_bytes'])}."
        return line
    line = f"Freed {human_size(data['freed_bytes'])}. History now uses {human_size(data['total_bytes'])}."
    if data["problems"]:
        line += " Problems: " + "; ".join(data["problems"])
    return line


def _policy(args: argparse.Namespace) -> dict[str, Any]:
    project = _project(args.project)
    try:
        current = load_policy(project)
    except ValueError:
        current = None
    chosen = make_policy(None, args.keep, args.max_mb, current)
    return set_policy(project, chosen, dry_run=args.dry_run)


def run(args: argparse.Namespace) -> int:
    """Run ``joinery history <action>``. Errors print as JSON and return 1."""
    actions = {"usage": usage, "compact": compact}
    try:
        if args.action == "policy":
            data = _policy(args)
        else:
            if args.keep is not None or args.max_mb is not None or args.dry_run:
                raise ValueError(
                    "--keep, --max-mb and --dry-run go with: history policy"
                )
            data = actions[args.action](args.project)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(data))
    elif args.action == "policy":
        print(describe_policy(data))
    else:
        print(_describe(args.action, data))
    return 1 if data.get("problems") and args.action == "compact" else 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``history usage|compact|policy --project P [--json]``; the handler is ``args.handler``."""
    parser = subparsers.add_parser(
        "history",
        help="show how much restore data and recipe history a project keeps, "
        "how much it keeps, or compact it",
    )
    parser.add_argument("action", choices=["usage", "compact", "policy"])
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "--keep",
        type=int,
        metavar="N",
        help="policy: accepted changes that keep their older versions (at least 1)",
    )
    parser.add_argument(
        "--max-mb",
        type=int,
        metavar="M",
        help="policy: most space those older versions may use, in megabytes",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="policy: say what the new numbers would free, change nothing",
    )
    parser.add_argument(
        "--json", action="store_true", help="machine-readable, for a UI front end"
    )
    parser.set_defaults(handler=run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="joinery")
    register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
