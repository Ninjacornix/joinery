"""The checks a change passes before the user sees it, as problems rather than errors.

The agent hands its change over with ``submit_for_review``; every check here
runs then, and each failure comes back to the agent as a ``Problem`` it can fix
in the same turn. Nothing here raises for a failed check, and nothing here
decides what happens next: that is the turn controller's job.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from joinery import references
from joinery.art_approval import approved
from joinery.art_approval import fingerprint as bible_fingerprint
from joinery.authoring import run_offline
from joinery.bible_rules import check_rules, load_rules, ui_resources
from joinery.bible_schema import REFERENCE_DIR
from joinery.catalog import load_catalog
from joinery.deps import check_deps
from joinery.lockfile import check_assets
from joinery.policy import load_denylist
from joinery.transactions import DIRECTORY, FileTransaction
from joinery.turn import Problem, scene_route

if TYPE_CHECKING:
    from joinery.gateway import ProjectGateway

PROTECTED = {
    "joinery.toml",
    ".mcp.json",
    "policy",
    ".claude",
    ".codex",
    ".gemini",
    ".opencode",
}
#: Asset files wherever they land in the project: a change to one needs an
#: approved art bible and passes the provenance gate, like art/ and audio/.
ASSET_SUFFIXES = frozenset(
    {
        ".blend",
        ".glb",
        ".gltf",
        ".fbx",
        ".obj",
        ".dae",
        ".kra",
        ".xcf",
        ".psd",
        ".aseprite",
        ".ase",
        ".ptex",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".svg",
        ".bmp",
        ".tga",
        ".gif",
        ".exr",
        ".hdr",
        ".tif",
        ".tiff",
        ".dds",
        ".ktx",
        ".ktx2",
        ".wav",
        ".ogg",
        ".mp3",
        ".flac",
        ".opus",
        ".aif",
        ".aiff",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".mp4",
        ".webm",
        ".ogv",
    }
)
#: Files Godot parses on import: a change to one is import-checked.
GODOT_SUFFIXES = frozenset({".gd", ".tscn", ".tres", ".gdshader", ".glb"})
OFFLINE_SECONDS = 180


def is_reference(path: str) -> bool:
    """Under art/reference/: the user's own boards, allowed before approval.

    `art/reference/../hero.blend` is not: PurePath does not resolve `..`.
    """
    pure = PurePosixPath(path.replace("\\", "/").removeprefix("res://"))
    return ".." not in pure.parts and pure.is_relative_to(REFERENCE_DIR)


def approved_in(project: Path, stage: Path) -> bool:
    """The project's bible is approved and the stage carries that same bible."""
    try:
        return approved(project) and bible_fingerprint(project) == bible_fingerprint(
            stage
        )
    except ValueError:
        return False


def ui_changes(stage: Path, changes: list[dict[str, Any]]) -> bool:
    """Changed UI resources that the bible's accessibility rules check."""
    try:
        rules = load_rules(stage)
    except ValueError:
        return False  # lint reports invalid rules; they cannot block UI work
    files = [c["path"] for c in changes if DIRECTORY not in {c["before"], c["after"]}]
    return bool(ui_resources(rules, files))


def _paths(lines: list[str]) -> list[str]:
    """The file each "path: what is wrong" line names."""
    return list(dict.fromkeys(line.split(":", 1)[0] for line in lines if ":" in line))


def _broken(output: str, ok: bool) -> bool:
    return not ok or "SCRIPT ERROR:" in output or "Parse Error:" in output


@dataclass
class Validation:
    changes: list[dict[str, str]] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    #: The art gate when asset work needs an approved art bible first.
    gate: dict[str, Any] | None = None


async def validate(
    gateway: ProjectGateway, tx: FileTransaction, live_scenes: set[str]
) -> Validation:
    """Every check for the staged change in ``tx``.

    The protected-path and live-conflict problems are final: no fix in this turn
    passes them. The art gate is the user's call, so it is returned as ``gate``.
    The rest are problems the agent can fix and submit again.
    """
    changes = tx.diff()
    found = Validation(changes=changes)
    if not changes:
        return found
    protected = [c["path"] for c in changes if Path(c["path"]).parts[0] in PROTECTED]
    if protected:
        found.problems.append(
            Problem(
                "protected",
                "Model tools cannot change project policy or MCP configuration. "
                "Use project setup.",
                protected,
                fixable=False,
            )
        )
        return found
    conflict = [
        c["path"]
        for c in changes
        if Path(c["path"]).suffix == ".tscn"
        and (
            "res://" + c["path"] in live_scenes
            or scene_route(c["path"], gateway.editor_context) == "live"
        )
    ]
    if conflict:
        found.problems.append(
            Problem(
                "live_conflict",
                "This turn changed the same scene live in the editor and as a saved "
                "file; one change can't do both.",
                conflict,
                fixable=False,
            )
        )
        return found
    # A scene and the model or texture it loads are decided together: never
    # review a scene that points at a file nobody is adding.
    gaps = references.missing(
        tx.stage,
        [c["path"] for c in changes if c["after"] not in {"", DIRECTORY}],
    )
    for scene, paths in references.users_of(
        tx.stage, [c["path"] for c in changes if not c["after"]]
    ).items():
        gaps.setdefault(scene, []).extend(paths)
    if gaps:
        found.problems.append(
            Problem("references", references.refusal(gaps), sorted(gaps))
        )
    asset_changes = [
        change["path"]
        for change in changes
        if (
            Path(change["path"]).parts[0] in {"art", "audio"}
            and change["path"] != "art/bible.md"
            or Path(change["path"]).suffix.lower() in ASSET_SUFFIXES
        )
        and DIRECTORY not in {change["before"], change["after"]}
    ]
    bible_ok = approved_in(gateway.project, tx.stage)
    rules = False
    if asset_changes:
        # Reference boards come before the bible; they still need a lock entry.
        if not bible_ok and not all(is_reference(p) for p in asset_changes):
            found.gate = gateway.art_gate_event()
            return found
        problems = check_assets(tx.stage, load_catalog())
        if problems:
            found.problems.append(
                Problem(
                    "assets",
                    "Asset validation failed: " + "; ".join(problems),
                    _paths(problems),
                )
            )
        rules = bible_ok
    else:
        # UI themes and scenes are checked for text contrast and size.
        rules = bible_ok and ui_changes(tx.stage, changes)
    problems = check_deps(tx.stage, load_denylist())
    if problems:
        found.problems.append(
            Problem(
                "dependencies",
                "Dependency validation failed: " + "; ".join(problems),
                _paths(problems),
            )
        )
    godot = any(
        Path(change["path"]).suffix in GODOT_SUFFIXES
        or change["path"] == "project.godot"
        for change in changes
    )
    found.problems += await checks(
        tx,
        changes,
        rules=rules,
        godot=godot and not gateway.import_current(tx),
        span=gateway.span,
    )
    return found


async def checks(
    tx: FileTransaction,
    changes: list[dict[str, str]],
    *,
    rules: bool,
    godot: bool,
    span: Any = None,
) -> list[Problem]:
    """The art bible's measured rules beside Godot's import check.

    Each runs in the offline worker and takes seconds; neither writes what the
    other reads (the rules mount the stage read-only).
    """

    def timed(phase: str) -> AbstractContextManager[None]:
        return span(phase) if span is not None else nullcontext()

    async def measured() -> list[Problem]:
        if not rules:
            return []
        with timed("review_rules"):
            found = await check_rules(
                tx.stage,
                [change["path"] for change in changes],
                lambda argv, root: run_offline(
                    argv, root, OFFLINE_SECONDS, readonly=True
                ),
            )
        if not found:
            return []
        return [
            Problem(
                "art_bible",
                "Art bible check failed: " + "; ".join(found),
                _paths(found),
            )
        ]

    async def godot_checks() -> list[Problem]:
        if not godot:
            return []
        with timed("review_import"):
            got = await run_offline(
                ["godot", "--headless", "--path", "/work", "--editor", "--import"],
                tx.stage,
                OFFLINE_SECONDS,
            )
        if not _broken(got.output, got.ok):
            return []
        return [
            Problem(
                "godot_import",
                "Godot import/script validation failed: "
                + (got.output[-4000:] or "Godot exited with an error."),
            )
        ]

    outcomes = await asyncio.gather(measured(), godot_checks(), return_exceptions=True)
    problems: list[Problem] = []
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
        problems += outcome
    return problems
