"""A request's to-do: a few steps and "done when" items Joinery checks after the change.

For a request with several steps the agent proposes a short to-do with ``propose_todo``
(ADR 0060): the steps it will take and what "done" means, each "done when" item written so
something other than the agent can check it. The sidebar shows the list before the work
starts; the user edits or removes items and presses Go when the project asks for that
(``[agents] todo_go``: ``"multi"`` waits for Go when there are two steps or more, the
default; ``"always"``; ``"never"``). After the change passes Joinery's checks every item is
checked and the review shows Pass, Fail or Inconclusive with the evidence. A Fail goes back
to the agent once before the user sees the change.

Kinds of "done when" item and how each is checked:

- ``reach`` (target: a place, named like its node): the playtest bot heads there in a seeded
  run of the changed game and says whether, and when, the player got there.
- ``no_errors`` (seconds, 10 to 300, default 60): the same run has no crash, freeze, script or
  engine error.
- ``test`` (target: a res:// GUT test the agent wrote with the change): it ran with the
  project's tests in Joinery's checks, which pass before anything here runs.
- ``check`` (target: one of Joinery's checks: tests, Godot import, art bible, references,
  provenance, dependencies): it ran on this change.
- ``look``: nothing checks it automatically; the user looks. Always Inconclusive.

Nothing here knows which provider runs the agent: the tool, the checks and the events are
the same for Codex, Claude Code, Gemini CLI and opencode.
"""

from __future__ import annotations

import tomllib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from joinery import playtest, playtest_report

Kind = Literal["reach", "no_errors", "test", "check", "look"]
KINDS: tuple[str, ...] = ("reach", "no_errors", "test", "check", "look")
Status = Literal["pass", "fail", "inconclusive"]
#: ``[agents] todo_go``: when the agent waits for the user's Go.
GO_MODES = ("multi", "always", "never")
MAX_STEPS = 8
MAX_DONE_WHEN = 6
#: The seed every check run plays with, so a result replays exactly.
SEED = 4242
DEFAULT_SECONDS = 60.0
#: The playtest's items that count as errors for ``no_errors``.
ERROR_KINDS = frozenset(
    {"crash", "freeze", "boot", "script_error", "shader_error", "error", "quit"}
)
#: ``check`` targets in the words the submit checks record them under.
CHECK_NAMES = {
    "tests": "tests",
    "godot_import": "Godot import",
    "import": "Godot import",
    "art_bible": "art bible",
    "references": "references",
    "provenance": "provenance",
    "dependencies": "dependencies",
}


@dataclass
class Criterion:
    """One "done when" item: words for the user and what checks it."""

    id: str
    text: str
    kind: Kind = "look"
    target: str = ""
    seconds: float = DEFAULT_SECONDS

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "text": self.text, "kind": self.kind}
        if self.target:
            data["target"] = self.target
        if self.kind == "no_errors":
            data["seconds"] = self.seconds
        return data


@dataclass
class Todo:
    """A request's to-do: its steps and its "done when" items."""

    id: str
    request: str
    steps: list[str]
    done_when: list[Criterion] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "todo_id": self.id,
            "steps": list(self.steps),
            "done_when": [c.as_dict() for c in self.done_when],
        }


@dataclass
class Result:
    """How one "done when" item came out, with the evidence."""

    id: str
    text: str
    status: Status
    evidence: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "status": self.status,
            "evidence": self.evidence,
        }


def _line(value: Any, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Each {what} is a short line of words.")
    text = " ".join(value.split())
    if len(text) > limit:
        raise ValueError(f"Keep each {what} under {limit} characters.")
    return text


def proposal(params: dict[str, Any], request: str = "") -> Todo:
    """The agent's ``propose_todo`` arguments, checked: {steps, done_when}."""
    steps = params.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"steps is a list of 1 to {MAX_STEPS} short lines.")
    items = params.get("done_when") or []
    if not isinstance(items, list) or len(items) > MAX_DONE_WHEN:
        raise ValueError(f"done_when is a list of up to {MAX_DONE_WHEN} items.")
    criteria: list[Criterion] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise TypeError("Each done_when item is {text, kind, target?, seconds?}.")
        kind = item.get("kind", "look")
        if kind not in KINDS:
            raise ValueError(f"A done_when kind is one of {', '.join(KINDS)}.")
        target = item.get("target") or ""
        if not isinstance(target, str) or len(target) > 200:
            raise ValueError("A done_when target is a short name or res:// path.")
        target = target.strip()
        if kind in {"reach", "test", "check"} and not target:
            raise ValueError(
                f"A {kind} item names its target (a place, a res:// test or a check)."
            )
        if kind == "test" and not target.startswith("res://"):
            raise ValueError("A test item's target is the test's res:// path.")
        if kind == "check" and target.lower().replace(" ", "_") not in CHECK_NAMES:
            raise ValueError(
                "A check item names one of: " + ", ".join(CHECK_NAMES) + "."
            )
        seconds = item.get("seconds", DEFAULT_SECONDS)
        if (
            not isinstance(seconds, (int, float))
            or isinstance(seconds, bool)
            or not 10 <= seconds <= 300
        ):
            raise ValueError("seconds is between 10 and 300.")
        criteria.append(
            Criterion(
                id=f"d{index + 1}",
                text=_line(item.get("text"), "done_when text", 160),
                kind=kind,
                target=target,
                seconds=float(seconds),
            )
        )
    return Todo(
        id=uuid.uuid4().hex[:12],
        request=request.strip()[:4000],
        steps=[_line(step, "step", 200) for step in steps],
        done_when=criteria,
    )


def edited(todo: Todo, data: dict[str, Any]) -> Todo:
    """The to-do as the user left it on the card: steps reworded or removed, "done when"
    items reworded or removed by id. Kinds and targets stay the agent's."""
    steps = data.get("steps", todo.steps)
    if not isinstance(steps, list):
        raise TypeError("The to-do's steps are a list.")
    kept_steps = [" ".join(str(s).split())[:200] for s in steps if str(s).strip()]
    if not kept_steps:
        raise ValueError("Keep at least one step, or cancel the to-do.")
    by_id = {c.id: c for c in todo.done_when}
    items = data.get("done_when", [c.as_dict() for c in todo.done_when])
    if not isinstance(items, list):
        raise TypeError("The to-do's done-when items are a list.")
    kept: list[Criterion] = []
    for item in items:
        if not isinstance(item, dict) or str(item.get("id", "")) not in by_id:
            continue
        text = " ".join(str(item.get("text", "")).split())[:160]
        if text:
            kept.append(replace(by_id[str(item["id"])], text=text))
    return replace(todo, steps=kept_steps[:MAX_STEPS], done_when=kept)


def go_mode(project: Path) -> str:
    """``[agents] todo_go``: "multi" (the default), "always" or "never"."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "multi"
    agents = raw.get("agents", {})
    value = agents.get("todo_go", "multi") if isinstance(agents, dict) else "multi"
    return value if value in GO_MODES else "multi"


def waits_for_go(todo: Todo, mode: str) -> bool:
    """Whether the agent waits for the user's Go before changing anything."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    return len(todo.steps) >= 2


def go_prompt(todo: Todo) -> str:
    """What the agent hears when the user presses Go: the to-do as the user left it."""
    steps = "\n".join(f"{n}. {step}" for n, step in enumerate(todo.steps, 1))
    done = "\n".join(f"- {c.text}" for c in todo.done_when) or "- (none)"
    return (
        "The user pressed Go on your to-do, as they left it (they may have edited or "
        "removed items):\n"
        f"Steps:\n{steps}\nDone when:\n{done}\n"
        "Work through the steps now as one change and submit it with submit_for_review. "
        "Joinery checks each done-when item after the change passes its checks; don't "
        "propose the to-do again.\n\n"
        f"The request was: {todo.request}"
    )


Player = Callable[[playtest.Options], Awaitable[dict[str, Any]]]


async def _play(options: playtest.Options) -> dict[str, Any]:
    # Cancelling the awaiting task (a Stop) stops the game too.
    return await playtest.run_async(options)


async def verify(
    todo: Todo,
    *,
    project: Path,
    stage: Path | None,
    ran: list[str],
    skipped: list[str],
    play: Player | None = None,
) -> list[Result]:
    """Check every "done when" item of ``todo`` against the change.

    ``stage`` is the change as files (None for a live edit alone, which the playtest can't
    see until it is accepted); ``ran`` and ``skipped`` are the submit checks' lists. Every
    item that plays shares one seeded run of the stage, as long as the longest asks.
    """
    play = play or _play
    playing = [c for c in todo.done_when if c.kind in {"reach", "no_errors"}]
    report: dict[str, Any] | None = None
    failure = ""
    if playing and stage is not None and (stage / "project.godot").is_file():
        seconds = max(
            [c.seconds for c in playing if c.kind == "no_errors"] or [DEFAULT_SECONDS]
        )
        try:
            report = await play(
                playtest.Options(
                    project=stage,
                    seconds=seconds,
                    seed=SEED,
                    reach=[c.target for c in playing if c.kind == "reach"],
                    out=playtest_folder(project),
                )
            )
        except (OSError, ValueError, RuntimeError) as exc:
            failure = str(exc) or "the playtest didn't run"
    results: list[Result] = []
    for criterion in todo.done_when:
        if criterion.kind in {"reach", "no_errors"}:
            results.append(_played(criterion, report, stage, failure))
        elif criterion.kind == "test":
            results.append(_test(criterion, stage, ran, skipped))
        elif criterion.kind == "check":
            results.append(_check(criterion, ran, skipped))
        else:
            results.append(
                Result(
                    criterion.id,
                    criterion.text,
                    "inconclusive",
                    "Nothing checks this by itself; look for yourself.",
                )
            )
    return results


def playtest_folder(project: Path) -> Path:
    """A playtest run's folder in the project's own ``.joinery/playtests``."""
    return playtest._run_dir(project)


def _replay(report: dict[str, Any]) -> str:
    return f"seed {report.get('seed', SEED)}"


def _played(
    criterion: Criterion,
    report: dict[str, Any] | None,
    stage: Path | None,
    failure: str,
) -> Result:
    def result(status: Status, evidence: str) -> Result:
        return Result(criterion.id, criterion.text, status, evidence)

    if stage is None:
        return result(
            "inconclusive",
            "The change is a live preview in the editor, so the game can't be played "
            "with it yet. Play it after you accept it.",
        )
    if report is None:
        return result("inconclusive", f"The game couldn't be played: {failure}.")
    played = float(report.get("played_seconds", 0) or 0)
    clock = playtest_report.stamp(played)
    if criterion.kind == "no_errors":
        errors = [
            item for item in report.get("items", []) if item.get("kind") in ERROR_KINDS
        ]
        if errors:
            first = errors[0]
            when = (
                f" at {playtest_report.stamp(float(first['t']))}"
                if first.get("t") is not None
                else ""
            )
            where = f" ({first['where']})" if first.get("where") else ""
            more = f", and {len(errors) - 1} more" if len(errors) > 1 else ""
            return result(
                "fail",
                f"{first.get('title', 'Error')}{when}: {first.get('detail', '')}{where}"
                f"{more}. Played {clock}, {_replay(report)}.",
            )
        if played + 0.5 < criterion.seconds:
            return result(
                "inconclusive",
                f"The game ran only {clock} of the {criterion.seconds:g} s asked for.",
            )
        return result("pass", f"No errors in {clock} of play, {_replay(report)}.")
    word = playtest.reach_words([criterion.target])
    place = next(
        (
            p
            for p in report.get("reach", [])
            if isinstance(p, dict) and word and p.get("target") == word[0]
        ),
        None,
    )
    if place is None or not place.get("found"):
        return result(
            "inconclusive",
            f"Nothing in the game is named like “{criterion.target}”, so the bot "
            "couldn't head for it.",
        )
    node = str(place.get("node", "") or criterion.target)
    if place.get("reached"):
        return result(
            "pass",
            f"The bot reached {node} at "
            f"{playtest_report.stamp(float(place.get('t') or 0))}, "
            f"{_replay(report)}.",
        )
    closest = place.get("closest")
    near = f" (closest {float(closest):.1f})" if closest is not None else ""
    return result(
        "fail",
        f"The bot didn't get to {node} in {clock} of play{near}, {_replay(report)}. "
        "A bot is no person: check the way is open.",
    )


def _test(
    criterion: Criterion, stage: Path | None, ran: list[str], skipped: list[str]
) -> Result:
    def result(status: Status, evidence: str) -> Result:
        return Result(criterion.id, criterion.text, status, evidence)

    path = criterion.target.removeprefix("res://")
    if stage is None:
        return result("inconclusive", "The change has no files, so no test ran.")
    if not (stage / path).is_file():
        return result("fail", f"{criterion.target} isn't in the change.")
    if "tests" not in ran:
        why = (
            "the project has no GUT tests set up"
            if "tests" in skipped
            else "Joinery didn't run the tests for this change"
        )
        return result("inconclusive", f"{criterion.target} didn't run: {why}.")
    from joinery.submission import gut_dirs

    if not any(path.startswith(folder.rstrip("/") + "/") for folder in gut_dirs(stage)):
        return result(
            "inconclusive",
            f"{criterion.target} isn't in the project's test folder, so it didn't run.",
        )
    return result(
        "pass", f"{criterion.target} ran with the project's tests and passed."
    )


def _check(criterion: Criterion, ran: list[str], skipped: list[str]) -> Result:
    name = CHECK_NAMES[criterion.target.lower().replace(" ", "_")]
    if name in ran:
        return Result(
            criterion.id,
            criterion.text,
            "pass",
            f"Joinery's {name} check passed on this change.",
        )
    reason = (
        "didn't apply to this change"
        if name in skipped
        else "didn't run on this change"
    )
    return Result(
        criterion.id, criterion.text, "inconclusive", f"The {name} check {reason}."
    )


def failed(results: list[Result]) -> list[Result]:
    return [r for r in results if r.status == "fail"]


def fix_request(results: list[Result]) -> str:
    """What the agent hears for the items that failed."""
    lines = "\n".join(f"- {r.text}: {r.evidence}" for r in failed(results))
    return (
        "These done-when items failed when Joinery checked your change:\n"
        + lines
        + "\n"
    )


def counts_line(results: list[Result]) -> str:
    """'2 of 3 done' (passes of all items)."""
    passed = sum(1 for r in results if r.status == "pass")
    return f"{passed} of {len(results)} done"
