"""Turn a project into the game the new-project wizard describes.

One path for every front end: the Project Manager's wizard (`joinery game
apply`), `joinery new --blueprint` and `joinery blueprints --json` all go
through here, so a project made from the command line is the same project the
wizard makes: the blueprint and its features, the game brief, the art bible
draft with the blueprint's preset, and, when Steam is on, its settings and the
pinned GodotSteam extension.

Nothing here talks to a model or makes an asset. The one download is GodotSteam,
which the user approved: a pinned release checked against a SHA-256 recorded in
the repo, fetched only into projects with Steam on (`joinery.godotsteam`). A
failed download is reported and the project is still made; Steam calls do
nothing until the extension is there.
"""

from __future__ import annotations

import json
import os
import shlex
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from joinery import bible, blueprints, brief, godotsteam
from joinery.catalog import ROOT
from joinery.transactions import FileTransaction, clone_tree, inventory

STEAM = "steam"
ONLINE = "online-play"
TRANSLATIONS = "translations"
CONNECT_CHOICES = ("steam", "direct")
#: Steam app IDs are unsigned 32-bit numbers.
MAX_APP_ID = 2**32 - 1
CAPTURE = "capture.png"
_PROJECT_TEMPLATE = ROOT / "templates" / "project" / "project.godot"

State = Literal["done", "skipped", "failed"]
Report = Callable[[dict[str, Any]], None]


#: What a Steam step can fail with; none of them stops the project being made.
STEAM_ERRORS = (OSError, ValueError, RuntimeError, godotsteam.SteamSetupError)


# --- Wizard catalogue ---------------------------------------------------------


def _capture(blueprint_id: str) -> str | None:
    image = blueprints.BLUEPRINTS_DIR / blueprint_id / CAPTURE
    return str(image) if image.is_file() else None


def _scratch_project(folder: Path) -> Path:
    project = folder / "project"
    project.mkdir()
    (project / "project.godot").write_text(
        _PROJECT_TEMPLATE.read_text().replace("{{name}}", "Preview")
    )
    return project


def _setting_keys(plan: blueprints.Plan) -> set[str]:
    return {f"{edit.section}/{edit.key}" for edit in plan.config_edits}


def _feature_entry(
    feature: dict[str, Any],
    blueprint: dict[str, Any],
    project: Path,
    base: blueprints.Plan,
) -> dict[str, Any]:
    resolution = blueprints.resolve(blueprint["id"], [feature["id"]])
    available = not resolution.problems
    adds: dict[str, list[str]] = {"files": [], "settings": [], "features": []}
    if available:
        planned = blueprints.plan(project, blueprint["id"], [feature["id"]])
        if planned.conflicts:
            available = False
            resolution = blueprints.Resolution(
                resolution.blueprint,
                resolution.features,
                resolution.added,
                list(planned.conflicts),
                resolution.core,
            )
        else:
            base_files = {w.path for w in base.writes}
            adds = {
                "files": sorted({w.path for w in planned.writes} - base_files),
                "settings": sorted(_setting_keys(planned) - _setting_keys(base)),
                "features": sorted(resolution.added),
            }
    return {
        "id": feature["id"],
        "name": feature["name"],
        "summary": feature["summary"],
        "default": feature["id"] in blueprint.get("default_features", []),
        "available": available,
        "reason": "" if available else resolution.problems[0],
        "requires": list(feature.get("requires", [])),
        "conflicts": list(feature.get("conflicts", [])),
        "external": list(feature.get("external", [])),
        "adds": adds,
    }


def wizard_catalogue() -> dict[str, Any]:
    """`joinery blueprints --json`: the catalogue, each card's capture and features.

    Each blueprint carries `capture` (its screenshot, or None), `files` (how
    many files it writes) and `features`:
    every feature with whether it can be chosen for this blueprint, why not,
    and what choosing it adds (files, settings and the features it pulls in),
    measured by planning it into an empty scratch project.
    """
    data = blueprints.catalogue_json()
    features: list[dict[str, Any]] = data.get("features", [])
    with tempfile.TemporaryDirectory(prefix="joinery-catalogue-") as tmp:
        project = _scratch_project(Path(tmp))
        for blueprint in data.get("blueprints", []):
            blueprint["capture"] = _capture(blueprint["id"])
            base = blueprints.plan(project, blueprint["id"], [])
            # The files the starting point writes, without Godot's .uid sidecars.
            blueprint["files"] = len(
                {w.path for w in base.writes if not w.path.endswith(".uid")}
            )
            blueprint["features"] = [
                _feature_entry(feature, blueprint, project, base)
                for feature in features
            ]
    return data


def describe(blueprint_id: str) -> dict[str, Any]:
    """One blueprint as the wizard sees it (`joinery blueprints describe`)."""
    for blueprint in wizard_catalogue()["blueprints"]:
        if blueprint["id"] == blueprint_id:
            found: dict[str, Any] = blueprint
            return found
    raise ValueError(f"Unknown blueprint: {blueprint_id}")


# --- Choices ------------------------------------------------------------------


def check_app_id(value: str | None) -> str:
    """A Steam app ID: digits only. Empty means it is added later."""
    text = (value or "").strip()
    # ASCII only: str.isdigit() also passes digits like "²" that int() refuses.
    if text and not (text.isascii() and text.isdigit()):
        raise ValueError("The Steam app ID is a number, digits only.")
    if text and int(text) > MAX_APP_ID:
        raise ValueError("That Steam app ID is too big. Check it on Steamworks.")
    return text


def check_choices(
    blueprint: str,
    features: Sequence[str],
    steam_app_id: str | None = None,
    connect: str | None = None,
) -> tuple[list[str], str, str | None]:
    """Refuse a bad mix before anything is written.

    Returns the resolved feature ids (with what they require), the app ID
    text and the connect choice, which is only kept when both online play and
    Steam are on (and then defaults to Steam).
    """
    resolution = blueprints.resolve(blueprint, features)
    if resolution.problems:
        raise ValueError("\n".join(resolution.problems))
    chosen = [f.id for f in resolution.features]
    app_id = check_app_id(steam_app_id)
    if app_id and STEAM not in chosen:
        raise ValueError("A Steam app ID needs the Steam feature.")
    if connect is not None and connect not in CONNECT_CHOICES:
        raise ValueError("Connect players through steam or direct.")
    if STEAM in chosen and ONLINE in chosen:
        return chosen, app_id, connect or "steam"
    return chosen, app_id, None


def _step(
    report: Report | None, step: str, state: State, message: str, **extra: Any
) -> dict[str, Any]:
    entry = {"step": step, "state": state, "message": message, **extra}
    if report is not None:
        report(entry)
    return entry


def _names(ids: Sequence[str]) -> str:
    known = {f.id: f.name for f in blueprints.list_features()}
    names = [known.get(i, i) for i in ids]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def install_addon(project: Path, report: Report | None = None) -> dict[str, Any]:
    """Copy the Joinery addon (with its MIT licence) in and turn the plugin on."""
    from joinery.bootstrap import update_addon

    outcome = update_addon(project)
    return _step(
        report,
        "addon",
        "done",
        "Added the Joinery editor addon.",
        version=outcome["version"],
    )


def write_brief(
    project: Path,
    blueprint_id: str,
    features: Sequence[str],
    report: Report | None = None,
) -> dict[str, Any]:
    """design/brief.md from the wizard's answers, or from the blueprint alone."""
    if (project / brief.BRIEF_PATH).exists():
        (project / brief.ANSWERS_FILE).unlink(missing_ok=True)
        return _step(
            report, "brief", "skipped", "Kept the game brief that is already there."
        )
    described = blueprints.describe(blueprint_id)
    written = brief.write_from_answers_file(project, described, features)
    if written is None:
        brief.write_brief(project, {}, described, features)
    return _step(report, "brief", "done", "Wrote the game brief.")


def write_bible(
    project: Path, blueprint_id: str, report: Report | None = None
) -> dict[str, Any]:
    """art/bible.md with the blueprint's preset, from the wizard's answers if any."""
    preset = blueprints.describe(blueprint_id).get("bible_preset") or None
    if (project / bible.BIBLE_PATH).exists():
        (project / bible.ANSWERS_FILE).unlink(missing_ok=True)
        return _step(
            report, "bible", "skipped", "Kept the art bible that is already there."
        )
    written = bible.write_from_answers_file(project, preset)
    if written is None:
        bible.write_bible(project, {}, preset)
    return _step(
        report,
        "bible",
        "done",
        "Wrote the art bible draft. Finish and approve it before asset work.",
    )


def install_steam(project: Path, report: Report | None = None) -> dict[str, Any]:
    """Download the pinned GodotSteam extension. A failure is reported, not raised.

    JOINERY_NO_DOWNLOADS=1 skips it (test runs and offline machines); the
    project then works as it does after a failed download.
    """
    retry = shlex.join(
        ["joinery", "feature", "add", "steam", "--project", str(project)]
    )
    if os.environ.get("JOINERY_NO_DOWNLOADS"):
        return _step(
            report,
            "steam-download",
            "skipped",
            "Skipped the GodotSteam download. Steam calls do nothing until it is "
            f"installed. To install it, run: {retry}",
        )
    try:
        outcome = godotsteam.install(project)
    except godotsteam.DownloadFailed:
        # Offline or the server did not answer: the plain message, no detail.
        return _step(
            report,
            "steam-download",
            "failed",
            "Could not download GodotSteam. The game still runs, and Steam calls "
            f"do nothing until it is installed. To try again, run: {retry}",
        )
    except STEAM_ERRORS as exc:
        return _step(
            report,
            "steam-download",
            "failed",
            f"Could not install GodotSteam: {exc} The game still runs, and Steam "
            f"calls do nothing until it is installed. To try again, run: {retry}",
        )
    message = (
        "The GodotSteam extension is already in place."
        if outcome.already
        else "Downloaded the GodotSteam extension."
    )
    return _step(report, "steam-download", "done", message, version=outcome.version)


def apply_choices(
    project: Path,
    blueprint: str,
    features: Sequence[str] = (),
    steam_app_id: str | None = None,
    connect: str | None = None,
    *,
    addon: bool = True,
    steam_setup: bool = True,
    report: Report | None = None,
) -> list[dict[str, Any]]:
    """Apply the wizard's choices to an existing project, step by step.

    Order: addon, blueprint and features, game brief, art bible, Steam
    settings, GodotSteam download (both left out without `steam_setup`). A bad
    choice raises ValueError before any write; a failed download does not
    raise. Returns every step's outcome;
    `report` also hears each one as it finishes.
    """
    chosen, app_id, how = check_choices(blueprint, features, steam_app_id, connect)
    if not (project / "project.godot").is_file():
        raise ValueError(f"No project.godot in {project}")
    steps: list[dict[str, Any]] = []
    if addon:
        steps.append(install_addon(project, report))
    applied = blueprints.apply(project, blueprint, chosen)
    name = blueprints.describe(blueprint)["name"]
    message = f"Made the {name} game"
    message += f" with {_names(applied.features)}." if applied.features else "."
    steps.append(_step(report, "blueprint", "done", message, features=applied.features))
    steps.append(write_brief(project, blueprint, applied.features, report))
    steps.append(write_bible(project, blueprint, report))
    if STEAM in applied.features and steam_setup:
        # Without online play, how players connect does not matter yet.
        through: godotsteam.Connect = "direct" if how == "direct" else "steam"
        try:
            godotsteam.configure(project, int(app_id) if app_id else None, through)
        except godotsteam.SteamSetupError as exc:
            raise ValueError(str(exc)) from None
        steps.append(
            _step(
                report,
                "steam",
                "done",
                f"Set up Steam with app ID {app_id}."
                if app_id
                else "Set up Steam without an app ID. Add it later as "
                "steam/app_id in Project Settings.",
                app_id=app_id,
                connect=how,
            )
        )
        steps.append(install_steam(project, report))
    if TRANSLATIONS in applied.features:
        steps.append(
            _step(
                report,
                "translations",
                "done",
                "Translations are ready.",
                offer=pot_command(project),
            )
        )
    return steps


def pot_command(project: Path) -> list[str]:
    """The command that rebuilds locale/messages.pot, offered after a change."""
    return [
        "godot",
        "--headless",
        "--path",
        str(project),
        "--script",
        "res://features/translations/tools/extract_pot.gd",
    ]


# --- Feature add and remove, as reviewed changes ------------------------------

FOLLOW_UP = "follow-up.json"


def _edits_json(edits: Sequence[blueprints.ConfigEdit]) -> list[dict[str, Any]]:
    return [
        {
            "file": e.file,
            "section": e.section,
            "key": e.key,
            "before": e.before,
            "after": e.after,
        }
        for e in edits
    ]


def _record(project: Path) -> blueprints.GameRecord:
    record = blueprints.read_record(project)
    if record is None:
        raise ValueError(
            "This project was not started from a blueprint, so it has no features to change."
        )
    return record


def plan_feature(project: Path, action: str, feature_id: str) -> dict[str, Any]:
    """What adding or removing a feature would change. Writes nothing.

    The sidebar shows this as the before and after: the paths, the
    project.godot edits and the notes. Removal is tried in a scratch copy.
    """
    record = _record(project)
    if action == "add":
        planned = blueprints.plan(
            project, record.blueprint, [*record.features, feature_id]
        )
        return {
            "action": action,
            "feature": feature_id,
            "changes": planned.changes,
            "config_edits": _edits_json(planned.config_edits),
            "notes": list(planned.notes),
            "conflicts": list(planned.conflicts),
            "adds": sorted(planned.resolution.added),
        }
    with tempfile.TemporaryDirectory(prefix="joinery-plan-") as tmp:
        scratch = Path(tmp) / "project"
        clone_tree(project, scratch)
        before = inventory(scratch)
        removed = blueprints.remove_feature(project, feature_id, root=scratch)
        after = inventory(scratch)
    return {
        "action": action,
        "feature": feature_id,
        "changes": sorted(
            p for p in before.keys() | after.keys() if before.get(p) != after.get(p)
        ),
        "config_edits": _edits_json(removed.config_edits),
        "notes": list(removed.notes),
        "conflicts": [],
        "adds": [],
    }


def stage_change(
    project: Path,
    summary: str,
    edit: Callable[[Path], object],
    follow_up: dict[str, Any] | None = None,
) -> FileTransaction:
    """Run `edit` on a staged copy and put the result up for review.

    Returns the transaction in review. Nothing in the project changes until it
    is accepted; a failed edit, or one that changes nothing, is rejected.
    """
    tx = FileTransaction(project)
    tx.begin(summary)
    try:
        edit(tx.stage)
        tx.review()
        if follow_up:
            (tx.directory / FOLLOW_UP).write_text(json.dumps(follow_up) + "\n")
    except BaseException:
        if tx.state in {"staging", "review"}:
            tx.reject()
        raise
    return tx


def steam_missing(project: Path, action: str, feature_id: str) -> bool:
    """True when `feature add steam` should only fetch GodotSteam.

    That is when the project already has Steam on, typically after a failed or
    skipped download at creation. The download is a no-op when the pinned
    release is already in place.
    """
    if action != "add" or feature_id != STEAM:
        return False
    record = blueprints.read_record(project)
    return record is not None and STEAM in record.features


def stage_feature(project: Path, action: str, feature_id: str) -> FileTransaction:
    """Add or remove a feature in a staged copy, ready for review."""
    _record(project)
    name = next(
        (f.name for f in blueprints.list_features() if f.id == feature_id), feature_id
    )
    edit: Callable[[Path], object]
    if action == "add":
        summary = f"Add {name}"

        def edit(stage: Path) -> object:
            return blueprints.add_feature(project, feature_id, root=stage)
    else:
        summary = f"Remove {name}"

        def edit(stage: Path) -> object:
            return blueprints.remove_feature(project, feature_id, root=stage)

    return stage_change(
        project,
        summary,
        edit,
        {"kind": "feature", "action": action, "feature": feature_id},
    )


def follow_up(tx: FileTransaction) -> dict[str, Any] | None:
    """The work a staged change asked for once it is accepted, if any."""
    try:
        data = json.loads((tx.directory / FOLLOW_UP).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def after_accept(
    project: Path, task: dict[str, Any] | None, report: Report | None = None
) -> list[dict[str, Any]]:
    """Finish an accepted change: GodotSteam in or out, and the POT offer."""
    if not task or task.get("kind") != "feature":
        return []
    steps: list[dict[str, Any]] = []
    feature = str(task.get("feature", ""))
    if feature == STEAM and task.get("action") == "add":
        steps.append(install_steam(project, report))
    elif feature == STEAM and task.get("action") == "remove":
        try:
            godotsteam.uninstall(project)
            steps.append(
                _step(
                    report, "steam-remove", "done", "Removed the GodotSteam extension."
                )
            )
        except STEAM_ERRORS as exc:
            steps.append(
                _step(
                    report,
                    "steam-remove",
                    "failed",
                    f"Could not remove the GodotSteam extension. {exc}",
                )
            )
    record = blueprints.read_record(project)
    if record is not None and TRANSLATIONS in record.features:
        steps.append(
            _step(
                report,
                "translations",
                "done",
                "Rebuild the list of text to translate?",
                offer=pot_command(project),
            )
        )
    return steps
