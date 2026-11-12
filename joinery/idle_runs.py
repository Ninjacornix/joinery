"""Unhurried runs: fixes and checks that wait until the user is away (ADR 0072).

Some runs need no one watching: "Make it match" (``/match``), the accessibility and
performance fixes (``/access``, ``/perf``) and the design check (``/design``). The
providers sell cheaper, slower capacity for that kind of work (Anthropic's Message
Batches, OpenAI's Batch API and Flex processing, Gemini's Batch Mode), but none of the
four agent CLIs Joinery drives exposes a batch API: an agent turn is a live loop of
tool calls, and the CLIs send each step as one streaming request (see
:data:`BATCH_SUPPORT`). So Joinery offers what it can do the same way for every
provider: a *when idle* queue. A queued run starts when the editor has been quiet for a
while inside the project's off-peak hours, on the project's light model (ADR 0061)
when one is set, and its changes wait in the Review tab like any others.

Settings, ``[agents]`` in joinery.toml:

* ``unhurried = "now" | "idle"``: ``"now"`` (the default) runs Fix and Check at once,
  as before; ``"idle"`` queues them. A single run can ask either way regardless.
* ``idle_hours = "22:00-07:00"``: local off-peak hours (may wrap past midnight);
  ``""`` means any hour.
* ``idle_minutes = 15``: how long the editor must have been quiet first.

The queue is one JSON file in ``.joinery/`` (git-ignored), so a run queued before the
editor closed still runs the next time it is open and idle. Nothing here knows which
provider runs the agents.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import tomllib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from joinery import metrics

#: What may wait: the sidebar message each kind is.
KINDS = ("match", "access", "perf", "design")
MODES = ("now", "idle")
DEFAULT_HOURS = "22:00-07:00"
DEFAULT_MINUTES = 15
#: Finished jobs kept for the record.
KEEP_DONE = 20
QUEUE = ".joinery/idle-queue.json"
_HOURS = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")

#: Whether each agent CLI lets Joinery use its provider's batch API, and how that was
#: found out ("verified": read from the installed CLI; "assumed": the CLI isn't
#: installed here, so from its documentation as of this writing).
BATCH_SUPPORT: dict[str, dict[str, str]] = {
    "claude": {
        "batch": "no",
        "how": "verified",
        "note": "Claude Code 2.1 has no flag or setting for Message Batches; its "
        "bundled SDK has the endpoint, but the agent loop streams each step.",
    },
    "codex": {
        "batch": "no",
        "how": "verified",
        "note": "Codex CLI 0.167 has no batch mode. Its config has a service_tier "
        "key (fast, flex seen in the binary): Flex processing is cheaper and "
        "slower, not the Batch API, and Joinery doesn't set it (unverified, and it "
        "bills only API-key accounts).",
    },
    "gemini": {
        "batch": "no",
        "how": "assumed",
        "note": "Gemini CLI streams generateContent; Batch Mode is an API of its own.",
    },
    "opencode": {
        "batch": "no",
        "how": "assumed",
        "note": "opencode streams through its providers' chat APIs; no batch mode.",
    },
}


@dataclass(frozen=True)
class IdleSettings:
    unhurried: str = "now"
    hours: str = DEFAULT_HOURS
    minutes: int = DEFAULT_MINUTES


@dataclass
class Job:
    id: str
    kind: str
    #: The sidebar message that asked for the run, as it came (areas, findings, …).
    data: dict[str, Any]
    queued_at: float
    state: str = "queued"
    started_at: float = 0.0
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def event(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": LABELS.get(self.kind, self.kind),
            "state": self.state,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
        }


LABELS = {
    "match": "Make it match",
    "access": "Accessibility fixes",
    "perf": "Performance fixes",
    "design": "Design check",
}


def settings(project: Path) -> IdleSettings:
    """Read each time: joinery.toml may change while a chat runs."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return IdleSettings()
    table = raw.get("agents", {})
    if not isinstance(table, dict):
        return IdleSettings()
    mode = table.get("unhurried", "now")
    hours = table.get("idle_hours", DEFAULT_HOURS)
    minutes = table.get("idle_minutes", DEFAULT_MINUTES)
    return IdleSettings(
        unhurried=mode if mode in MODES else "now",
        hours=hours if isinstance(hours, str) and valid_hours(hours) else DEFAULT_HOURS,
        minutes=minutes
        if isinstance(minutes, int)
        and not isinstance(minutes, bool)
        and 0 <= minutes <= 720
        else DEFAULT_MINUTES,
    )


def valid_hours(hours: str) -> bool:
    return hours == "" or bool(_HOURS.match(hours.replace(" ", "")))


def in_hours(now: datetime, hours: str) -> bool:
    """``now`` (local) falls in ``hours`` ("22:00-07:00" wraps past midnight)."""
    if not hours:
        return True
    found = _HOURS.match(hours.replace(" ", ""))
    if found is None:
        return False
    start = int(found.group(1)) * 60 + int(found.group(2))
    end = int(found.group(3)) * 60 + int(found.group(4))
    minute = now.hour * 60 + now.minute
    if start == end:
        return True
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def due(config: IdleSettings, now: datetime, quiet_s: float) -> bool:
    """A queued run may start: off-peak hours, and the editor quiet long enough."""
    return in_hours(now, config.hours) and quiet_s >= config.minutes * 60


def wanted(project: Path, data: dict[str, Any]) -> bool:
    """Whether a Fix or Check the sidebar sent should wait for idle: its own ``when``
    ("idle" or "now") wins, otherwise the project's ``unhurried`` setting."""
    when = str(data.get("when", "") or "")
    if when in MODES:
        return when == "idle"
    return settings(project).unhurried == "idle"


def settled(kind: str) -> dict[str, Any]:
    """The event that settles a run's sidebar card when the run waits instead."""
    if kind == "design":
        return {"kind": "design", "state": "stopped", "origin": "idle"}
    return {
        "kind": kind,
        "state": "done",
        "tasks": [],
        "message": "Waits for when you're away. Type /idle now to run it now.",
    }


def hours_words(config: IdleSettings) -> str:
    if not config.hours:
        return f"once the editor has been quiet for {config.minutes} min"
    start, end = config.hours.replace(" ", "").split("-")
    return (
        f"between {start} and {end} once the editor has been quiet for "
        f"{config.minutes} min"
    )


# --- The queue ---------------------------------------------------------------------


def _path(project: Path) -> Path:
    return project / QUEUE


def jobs(project: Path) -> list[Job]:
    try:
        raw = json.loads(_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found: list[Job] = []
    for entry in raw.get("jobs", []) if isinstance(raw, dict) else []:
        if not isinstance(entry, dict) or entry.get("kind") not in KINDS:
            continue
        try:
            found.append(
                Job(
                    id=str(entry["id"]),
                    kind=str(entry["kind"]),
                    data=dict(entry.get("data") or {}),
                    queued_at=float(entry.get("queued_at", 0)),
                    state=str(entry.get("state", "queued")),
                    started_at=float(entry.get("started_at", 0)),
                    message=str(entry.get("message", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return found


def _save(project: Path, found: list[Job]) -> None:
    done = [j for j in found if j.state != "queued"][-KEEP_DONE:]
    kept = [j for j in found if j.state == "queued"] + done
    kept.sort(key=lambda j: j.queued_at)
    target = _path(project)
    if target.is_symlink():
        raise ValueError("The idle queue must stay inside this project.")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "jobs": [{k: v for k, v in asdict(j).items() if k != "extra"} for j in kept]
    }
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def queued(project: Path) -> list[Job]:
    return [j for j in jobs(project) if j.state == "queued"]


def add(
    project: Path, kind: str, data: dict[str, Any], now: float | None = None
) -> Job:
    """Queue one run; the same kind already waiting is replaced by the newer ask."""
    if kind not in KINDS:
        raise ValueError("A run that waits is one of: " + ", ".join(KINDS) + ".")
    clean = {k: v for k, v in data.items() if k not in {"when", "selection"}}
    found = [j for j in jobs(project) if not (j.state == "queued" and j.kind == kind)]
    job = Job(uuid.uuid4().hex[:12], kind, clean, time.time() if now is None else now)
    found.append(job)
    _save(project, found)
    return job


def take(project: Path, now: float | None = None) -> Job | None:
    """The oldest queued run, marked started; None when nothing waits."""
    found = jobs(project)
    for job in found:
        if job.state == "queued":
            job.state = "started"
            job.started_at = time.time() if now is None else now
            _save(project, found)
            return job
    return None


def remove(project: Path, job_id: str = "") -> int:
    """Drop one queued run (or every one when ``job_id`` is ""); returns how many."""
    found = jobs(project)
    kept = [
        j for j in found if not (j.state == "queued" and (not job_id or j.id == job_id))
    ]
    if len(kept) != len(found):
        _save(project, kept)
    return len(found) - len(kept)


def record(project: Path, job: Job, model: str) -> None:
    """The run started: one line in the change metrics (ADR 0054), never content."""
    trace = metrics.Trace()
    trace.attributes.update(
        {
            metrics.OUTCOME: "started",
            metrics.IDLE_KIND: job.kind,
            metrics.IDLE_WAITED_S: round(max(0.0, job.started_at - job.queued_at)),
            metrics.REQUEST_MODEL: model,
            metrics.MODEL_TIER: "light" if model else "main",
        }
    )
    try:
        trace.save(project)
    except (OSError, ValueError):
        pass  # Measuring never fails the work it measures.


# --- CLI ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``joinery idle --project P [list|add KIND|remove ID|clear|batch] [--json]``."""
    parser = subparsers.add_parser(
        "idle", help="runs that wait until you're away: list, add, remove, clear"
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=("list", "add", "remove", "clear", "batch"),
    )
    parser.add_argument("what", nargs="?", default="", help="KIND to add, ID to remove")
    parser.add_argument("--areas", default="", help="areas to fix, comma-separated")
    parser.add_argument("--json", action="store_true", help="print JSON")


def run(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    if args.action == "batch":
        if args.json:
            print(json.dumps(BATCH_SUPPORT, indent=2))
            return 0
        for agent, row in BATCH_SUPPORT.items():
            print(f"{agent:9} batch API: {row['batch']} ({row['how']}). {row['note']}")
        return 0
    if args.action == "add":
        data: dict[str, Any] = {}
        areas = [a.strip() for a in str(args.areas).split(",") if a.strip()]
        if areas:
            data["areas"] = areas
        if args.what in {"access", "perf"}:
            data["check"] = args.what
        try:
            job = add(project, args.what, data)
        except (OSError, ValueError) as exc:
            print(str(exc))
            return 2
        print(
            f"Queued {LABELS[job.kind]} ({job.id}): it runs {hours_words(settings(project))}."
        )
        return 0
    if args.action in {"remove", "clear"}:
        count = remove(project, args.what if args.action == "remove" else "")
        print(f"Removed {count} waiting run{'s' if count != 1 else ''}.")
        return 0 if count or args.action == "clear" else 1
    found = jobs(project)
    if args.json:
        print(json.dumps([j.event() for j in found], indent=2))
        return 0
    config = settings(project)
    if not found:
        print("Nothing is waiting.")
    for job in found:
        when = datetime.fromtimestamp(job.queued_at).strftime("%Y-%m-%d %H:%M")
        print(f"{job.id}  {job.state:8} {LABELS[job.kind]:22} queued {when}")
    print(f'Runs start {hours_words(config)}; unhurried = "{config.unhurried}".')
    return 0
