"""The unused files, each with what goes with it; and removing them as one change.

A file only one unused file uses goes with that file (a texture of an unused model, the
script of an unused scene); so do a kit model's LODs. Its provenance goes when nothing
left needs it: the ``joinery.lock`` entry of every removed asset, and a recipe whose
game outputs are all removed, with its script, its native sources (.blend, .kra,
.mmg…) and the history recipes of the same outputs. A recipe that also makes a file
the game keeps stays, and the finding says so.

:func:`plan` turns chosen findings into the files to delete and the text edits
(project.godot without an action or autoload, a scene without a node, a scene with an
empty script taken off), refusing when a file that stays would still load one that
goes. :func:`apply` makes it on a folder: the stage of a reviewed change. Nothing
here touches the art bible, the brief, the user's references, ``[tidy] keep`` or
``[match] final``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict

from joinery import lockfile
from joinery.godot_config import GodotConfig, packed_strings, to_variant
from joinery.recipe import HISTORY_DIR, Recipe, load_recipe
from joinery.tidy import (
    Companion,
    Finding,
    Graph,
    Report,
    candidate,
    finding_id,
    human_size,
    kept,
    kind_of,
    protected,
    sidecars,
    size_of,
)
from joinery.tidy_nodes import (
    detach_script,
    remove_actions,
    remove_autoloads,
    remove_nodes,
)

_LOD = re.compile(r"^(?P<stem>.+)_lod\d+$")


@dataclass
class Provenance:
    """Who made what: lock entries, and recipes by their outputs."""

    lock: dict[str, lockfile.Entry] = field(default_factory=dict)
    recipes: dict[str, Recipe] = field(default_factory=dict)
    history: dict[str, Recipe] = field(default_factory=dict)
    makes: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def read(cls, project: Path) -> Provenance:
        found = cls()
        try:
            found.lock = {e.path: e for e in lockfile.read_assets(project)}
        except (OSError, ValueError):
            found.lock = {}
        folder = project / "recipes"
        for path in sorted(folder.rglob("*.toml")) if folder.is_dir() else []:
            rel = path.relative_to(project).as_posix()
            try:
                recipe = load_recipe(path)
            except (OSError, ValueError):
                continue
            if rel.startswith(HISTORY_DIR + "/"):
                found.history[rel] = recipe
                continue
            found.recipes[rel] = recipe
            for output in recipe.outputs:
                found.makes.setdefault(output, []).append(rel)
        return found

    def recipes_of(self, rel: str) -> list[str]:
        entry = self.lock.get(rel)
        named = getattr(entry, "recipe", None) if entry is not None else None
        out = list(self.makes.get(rel, []))
        if named and named in self.recipes and named not in out:
            out.append(named)
        return out


def _game_outputs(graph: Graph, recipe: Recipe) -> list[str]:
    return [o for o in recipe.outputs if o in graph.present and candidate(graph, o)]


def provenance(
    graph: Graph, prov: Provenance, files: set[str], removed: set[str]
) -> tuple[list[Companion], list[str]]:
    """What goes with ``files`` when everything in ``removed`` goes: recipes whose
    game outputs all go, with their scripts, sources and history; and notes for a
    recipe that stays."""
    out: list[Companion] = []
    notes: list[str] = []
    gone_recipes: set[str] = set()
    for rel in sorted(files):
        for recipe_path in prov.recipes_of(rel):
            recipe = prov.recipes[recipe_path]
            outputs = _game_outputs(graph, recipe)
            staying = [o for o in outputs if o not in removed]
            if staying:
                notes.append(
                    f"Its recipe {recipe_path} stays: it also makes {staying[0]}"
                    + (f" and {len(staying) - 1} more" if len(staying) > 1 else "")
                    + "."
                )
                continue
            gone_recipes.add(recipe_path)
    other_needs: set[str] = set()
    for path, recipe in prov.recipes.items():
        if path in gone_recipes:
            continue
        if not set(_game_outputs(graph, recipe)) <= removed or not _game_outputs(
            graph, recipe
        ):
            other_needs |= {recipe.script, *recipe.outputs, *recipe.sources}
    for recipe_path in sorted(gone_recipes):
        recipe = prov.recipes[recipe_path]
        out.append(
            Companion(
                path=recipe_path,
                size=size_of(graph.project, recipe_path),
                what="recipe",
            )
        )
        if recipe.script not in other_needs and recipe.script in graph.present:
            out.append(
                Companion(
                    path=recipe.script,
                    size=size_of(graph.project, recipe.script),
                    what="recipe script",
                )
            )
        for native in (*recipe.outputs, *recipe.sources):
            if (
                native in removed
                or native in other_needs
                or native not in graph.present
            ):
                continue
            if candidate(graph, native):
                continue  # a game file another finding decides about
            out.append(
                Companion(
                    path=native,
                    size=size_of(graph.project, native),
                    what="native source",
                )
            )
            out += [
                Companion(
                    path=s, size=size_of(graph.project, s), what="import settings"
                )
                for s in sidecars(graph, native)
            ]
    taken = removed | {c.path for c in out}
    for path, recipe in prov.history.items():
        if recipe.outputs and set(recipe.outputs) <= taken:
            out.append(
                Companion(
                    path=path, size=size_of(graph.project, path), what="recipe history"
                )
            )
    seen: set[str] = set()
    unique = [c for c in out if not (c.path in seen or seen.add(c.path))]  # type: ignore[func-returns-value]
    return unique, notes


# --- the unused files ------------------------------------------------------------------


def _strong(graph: Graph, rel: str) -> set[str]:
    return {s for s in graph.referrers.get(rel, set()) if rel in graph.refs[s].files}


def file_findings(
    graph: Graph, prov: Provenance, keep: list[str], final: list[str]
) -> tuple[list[Finding], list[str]]:
    """The unused and maybe-used game files, each with what goes with it."""
    held: list[str] = []
    unused: set[str] = set()
    maybe: set[str] = set()
    for rel in graph.files:
        if not candidate(graph, rel) or rel in graph.used or rel in graph.by_tests:
            continue
        if kept(rel, rel, keep, final):
            held.append(rel)
            continue
        (maybe if rel in graph.maybe else unused).add(rel)
    owner: dict[str, str] = {}
    for rel in unused | maybe:
        users = _strong(graph, rel)
        if len(users) == 1:
            (user,) = users
            if user in unused | maybe and (user in maybe) == (rel in maybe):
                owner[rel] = user
                continue
        lod = _LOD.match(PurePosixPath(rel).stem)
        if lod and not users:
            base = str(
                PurePosixPath(rel).with_name(lod["stem"] + PurePosixPath(rel).suffix)
            )
            if base in unused | maybe and base != rel:
                owner[rel] = base

    def top(rel: str) -> str:
        seen = {rel}
        while rel in owner and owner[rel] not in seen:
            rel = owner[rel]
            seen.add(rel)
        return rel

    groups: dict[str, list[str]] = {}
    for rel in sorted(unused | maybe):
        groups.setdefault(top(rel), []).append(rel)
    findings: list[Finding] = []
    for head, members in sorted(groups.items()):
        files = set(members)
        goes: list[Companion] = []
        for rel in sorted(files):
            if rel != head:
                lod = _LOD.match(PurePosixPath(rel).stem)
                goes.append(
                    Companion(
                        path=rel,
                        size=size_of(graph.project, rel),
                        what="LOD"
                        if lod and not _strong(graph, rel)
                        else "only it uses it",
                    )
                )
            goes += [
                Companion(
                    path=s,
                    size=size_of(graph.project, s),
                    what="import settings" if s.endswith(".import") else "uid",
                )
                for s in sidecars(graph, rel)
            ]
        made, notes = provenance(graph, prov, files, files)
        goes += made
        locked = sorted(p for p in files | {c.path for c in made} if p in prov.lock)
        size = size_of(graph.project, head) + sum(c.size for c in goes)
        confidence = "maybe" if head in maybe else "unused"
        kind = kind_of(graph, head)
        referrers = sorted(_strong(graph, head))
        if confidence == "maybe":
            built = graph.maybe[head]
            why = (
                f"Nothing names res://{head}, but {built.source} builds paths like "
                f"res://{built.prefix}…{built.suffix}, which could load it."
            )
            evidence = [built.where() + ": " + built.text]
        elif referrers:
            why = (
                f"Only {', '.join(referrers[:2])} use{'s' if len(referrers) == 1 else ''} it, "
                "and nothing in the game uses "
                + ("that" if len(referrers) == 1 else "those")
                + "."
            )
            evidence = [
                f"Used by {u}, which the game never loads." for u in referrers[:4]
            ]
        else:
            why = (
                f"Nothing in the game loads it: no scene, script, resource or setting "
                f"names res://{head}."
            )
            evidence = [
                "Searched every scene, script, resource, setting and design file."
            ]
        if any(graph.referrers.get(head, set()) - _strong(graph, head)):
            evidence.append(
                "Only project.godot's list of files for translation names it."
            )
        if locked:
            evidence.append(f"joinery.lock entries go with it: {', '.join(locked[:3])}")
        findings.append(
            Finding(
                id=finding_id("file", head),
                key=head,
                kind=kind,  # type: ignore[arg-type]
                path=head,
                title=PurePosixPath(head).name,
                confidence=confidence,  # type: ignore[arg-type]
                size=size,
                why=why,
                evidence=evidence + notes,
                goes_with=goes,
                edits=["joinery.lock: " + ", ".join(locked)] if locked else [],
            )
        )
    return findings, held


# --- removing -----------------------------------------------------------------------------


class Removal(BaseModel):
    """One removal, ready to make: what goes, what is edited, and why it is safe."""

    model_config = ConfigDict(extra="forbid")

    ids: list[str]
    #: Files deleted, with what each is.
    delete: list[Companion] = []
    #: path -> the text it gets.
    edits: dict[str, str] = {}
    #: joinery.lock entries dropped.
    lock: list[str] = []
    size: int = 0
    #: One line per finding, for the review: "crate.glb (2.1 MB, with 3 files)".
    lines: list[str] = []
    problems: list[str] = []

    def summary(self) -> str:
        count = len(self.ids)
        return f"Remove {count} unused thing{'s' if count != 1 else ''}" + (
            f", {human_size(self.size)}" if self.size else ""
        )


def plan(graph: Graph, report: Report, ids: Iterable[str] | None = None) -> Removal:
    """The change that removes the chosen findings (every sure one by default)."""
    chosen_ids = list(ids or [])
    by_id = {f.id: f for f in report.findings}
    unknown = [i for i in chosen_ids if i not in by_id]
    chosen = (
        [by_id[i] for i in chosen_ids if i in by_id]
        if chosen_ids
        else [f for f in report.findings if f.confidence == "unused"]
    )
    result = Removal(ids=[f.id for f in chosen])
    if unknown:
        result.problems.append(
            "Some of these were not found any more; check again: "
            + ", ".join(unknown[:3])
        )
    project = graph.project
    files: dict[str, Companion] = {}
    actions: list[str] = []
    autoloads: list[str] = []
    nodes: dict[str, list[str]] = {}
    scripts: list[str] = []
    for finding in chosen:
        if finding.kind == "input":
            actions.append(finding.subject)
        elif finding.kind == "autoload":
            autoloads.append(finding.subject)
            target = _autoload_path(graph, finding.subject)
            if (
                target
                and target in graph.present
                and _strong(graph, target) <= {"project.godot"}
            ):
                files[target] = Companion(
                    path=target, size=size_of(project, target), what="autoload script"
                )
                for s in sidecars(graph, target):
                    files[s] = Companion(path=s, size=size_of(project, s), what="uid")
        elif finding.kind == "node":
            nodes.setdefault(finding.path, []).append(finding.subject)
        elif finding.subject == "empty":
            scripts.append(finding.path)
            files[finding.path] = Companion(
                path=finding.path,
                size=size_of(project, finding.path),
                what="empty script",
            )
            for s in sidecars(graph, finding.path):
                files[s] = Companion(path=s, size=size_of(project, s), what="uid")
        else:
            files[finding.path] = Companion(
                path=finding.path,
                size=size_of(project, finding.path),
                what=finding.kind,
            )
            for companion in finding.goes_with:
                if companion.what not in {
                    "recipe",
                    "recipe script",
                    "native source",
                    "recipe history",
                }:
                    files[companion.path] = companion
    prov = Provenance.read(project)
    made, _notes = provenance(graph, prov, set(files), set(files))
    for companion in made:
        files.setdefault(companion.path, companion)
    for rel in list(files):
        if protected(rel):
            result.problems.append(f"{rel} is never removed.")
            files.pop(rel)
    edits: dict[str, str] = {}
    if actions or autoloads or any(_pot_names(graph, files)):
        text = graph.text("project.godot")
        if actions:
            text = remove_actions(text, actions)
        if autoloads:
            text = remove_autoloads(text, autoloads)
        text = _without_pot(text, set(files))
        edits["project.godot"] = text
    for scene, paths in nodes.items():
        edits[scene] = remove_nodes(edits.get(scene, graph.text(scene)), paths)
    for script in scripts:
        for user in sorted(graph.referrers.get(script, set())):
            edits[user] = detach_script(edits.get(user, graph.text(user)), script)
    for rel in [r for r in edits if r in files]:
        edits.pop(rel)
    gone = set(files)
    for rel in sorted(gone):
        for user in sorted(_strong(graph, rel) - gone):
            edited = edits.get(user)
            if edited is not None and not _names(graph, edited, rel):
                continue
            if user.startswith(("tests/", "test/")):
                continue
            result.problems.append(
                f"{user} still uses {rel}; remove it too, or keep {PurePosixPath(rel).name}."
            )
    result.delete = sorted(files.values(), key=lambda c: c.path)
    result.edits = edits
    result.lock = sorted(p for p in gone if p in prov.lock)
    result.size = sum(c.size for c in result.delete)
    for finding in chosen:
        extra = len(finding.goes_with)
        detail = ", ".join(
            part
            for part in (
                human_size(finding.size) if finding.size else "",
                f"with {extra} file{'s' if extra != 1 else ''}" if extra else "",
            )
            if part
        )
        result.lines.append(finding.title + (f" ({detail})" if detail else ""))
    return result


def _autoload_path(graph: Graph, name: str) -> str:
    from joinery.tidy_nodes import autoload_targets

    return autoload_targets(graph).get(name, "")


def _names(graph: Graph, text: str, rel: str) -> bool:
    if f"res://{rel}" in text:
        return True
    return any(uid in text for uid, path in graph.uids.items() if path == rel)


def _pot_names(graph: Graph, files: Iterable[str]) -> list[str]:
    weak = graph.refs.get("project.godot")
    return [f for f in files if weak is not None and f in weak.weak]


def _without_pot(text: str, gone: set[str]) -> str:
    config = GodotConfig.parse(text)
    section, key = "internationalization", "locale/translations_pot_files"
    raw = config.get(section, key)
    if raw is None:
        return config.dump()
    listed = packed_strings(raw)
    kept_paths = [p for p in listed if p.removeprefix("res://") not in gone]
    if kept_paths != listed:
        if kept_paths:
            config.set(section, key, to_variant(kept_paths))
        else:
            config.erase(section, key)
    return config.dump()


def apply(root: Path, removal: Removal) -> list[str]:
    """Make ``removal`` in ``root`` (a stage): delete, edit, drop the lock entries.
    Returns the files deleted."""
    if removal.problems:
        raise ValueError(" ".join(removal.problems))
    deleted: list[str] = []
    for companion in removal.delete:
        if protected(companion.path):
            raise ValueError(f"{companion.path} is never removed.")
        target = root / companion.path
        if target.is_file() and not target.is_symlink():
            target.unlink()
            deleted.append(companion.path)
    for rel, text in removal.edits.items():
        path = root / rel
        if path.is_file():
            path.write_text(text, encoding="utf-8")
    if removal.lock and (root / lockfile.LOCK_NAME).is_file():
        lockfile.write_assets(root, set(removal.lock), [])
    for rel in deleted:
        folder = (root / rel).parent
        while folder != root and len(folder.relative_to(root).parts) > 1:
            try:
                if any(folder.iterdir()):
                    break
                folder.rmdir()
            except OSError:
                break
            folder = folder.parent
    return deleted


def describe(removal: Removal) -> list[str]:
    """The review's words: what goes, what is edited, how much is freed."""
    words = [f"{removal.summary()}."]
    words += [f"- {line}" for line in removal.lines]
    if removal.edits:
        words.append("Edits " + ", ".join(sorted(removal.edits)) + ".")
    if removal.lock:
        words.append(
            f"Drops {len(removal.lock)} joinery.lock entr{'ies' if len(removal.lock) != 1 else 'y'}."
        )
    return words


def keep_key(report: Report, ident: str) -> str:
    """A finding id, key or path -> the ``[tidy] keep`` key."""
    for finding in report.findings:
        if ident in {finding.id, finding.key, "res://" + finding.key}:
            return finding.key
    return ident.removeprefix("res://")
