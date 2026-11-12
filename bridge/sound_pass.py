"""The sound pass in the chat host: listen, plan, then the sounds as parallel tasks.

``/sound-pass [focus]`` in the sidebar sends ``sound_pass_start {focus, scene}``. The
host audits the level (`joinery.sound_pass`, no model), plans the changes by file
(`joinery.sound_plan`) and starts each as a scripted task of the crew, as many at
once as ``[agents] parallel`` leaves free beside the chat's own request; apps still
take turns per worker. Each change is checked, read by the reviewer and decided by
the user in the Review tab; nothing is accepted here.

Beside the tasks, the chat's own provider (Codex, Claude Code, Gemini CLI or
opencode) runs in the read-only ``sound-designer`` role (`joinery.roles`): it calls
``sound_audit`` and writes a short report of the level's sound, what was missing and
what is being made. The tasks never wait for it: if the agent fails, the sounds are
still made and the card still has the audit's facts.

Events, all ``kind: "sound_pass"``: ``auditing``, then ``making`` with the audit,
the per-category rows and the planned tasks, an update as each task starts or ends
and when the report is written, then ``done`` (or ``failed`` or ``stopped``).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bridge.crew import WORKING, Crew, ScriptedCall, Task
from bridge.instructions import SOUND_DESIGNER_PROMPT
from bridge.playtester import PlaytestAgent, PlaytestEndpoint
from bridge.protocol import Done, Message, MessageDelta, Prompt
from joinery import sound_pass, sound_plan
from joinery.gateway import ART_GATE_MESSAGE
from joinery.roles import spec

Emit = Callable[[dict[str, Any]], None]
#: Seconds between looks at the running tasks.
POLL = 0.25
#: The sound designer's own words, at most.
REPORT_LIMIT = 6_000
BUSY = "The sound pass is already running; wait for it to finish."
#: Words in the focus that pick categories ("footsteps", "ambience only").
FOCUS_WORDS: dict[str, tuple[str, ...]] = {
    "footsteps": ("footstep", "step", "walk", "surface", "floor"),
    "jump_land": ("jump", "land"),
    "pickups": ("pickup", "coin", "collect", "item"),
    "hits": ("hit", "damage", "hurt", "enemy", "combat"),
    "switches": ("door", "lever", "switch", "button", "plate", "gate"),
    "ambience": ("ambience", "ambient", "atmosphere", "wind", "background"),
    "ui": ("ui", "menu", "click", "hover", "button"),
}


def focus_categories(focus: str) -> list[str]:
    """The categories ``focus`` names; none named means all of them."""
    words = re.findall(r"[a-z]+", focus.lower())
    chosen = [
        category
        for category, keys in FOCUS_WORDS.items()
        if any(word.startswith(key) for word in words for key in keys)
    ]
    return chosen


def audit_for_card(audit: sound_pass.Audit) -> dict[str, Any]:
    """What the card shows: the headline, categories and the top findings."""
    return {
        "headline": audit.headline,
        "level": audit.level,
        "categories": audit.categories,
        "present": audit.present,
        "surfaces": audit.surfaces,
        "playtest": audit.playtest,
        "findings": [
            {
                "id": f.id,
                "category": f.category,
                "scene": f.scene,
                "node": f.node,
                "missing": f.missing,
                "sounds": [s.name for s in f.sounds],
                "needs_you": f.needs_you,
                "evidence": f.evidence,
            }
            for f in audit.findings
        ],
    }


def audit_for_agent(audit: sound_pass.Audit) -> dict[str, Any]:
    """``sound_audit``'s answer: everything, with the plan in words."""
    data = audit.model_dump()
    data["note_for_you"] = (
        "Each finding names the scene and node, what is missing, the sounds Joinery's "
        "kit would make (make_sound on joinery-sound) and where they go (the game "
        "core's Footsteps, SoundCue or AmbienceZone). The sound pass makes them as "
        "reviewed changes; this call changed nothing."
    )
    return data


class SoundPassRun:
    """One project's sound pass: at most one at a time."""

    def __init__(
        self,
        crew: Crew,
        project: Path,
        emit: Emit,
        prompt: Callable[[], Prompt],
        *,
        agent: str = "",
        profile: str = "",
        bridge: Callable[[str], PlaytestAgent] | None = None,
        endpoint: Callable[[], PlaytestEndpoint] | None = None,
    ) -> None:
        self.crew = crew
        self.project = project
        self.emit = emit
        self.prompt = prompt
        self.agent = agent
        self.profile = profile
        self.bridge = bridge
        self.endpoint = endpoint
        self.state = "idle"
        self.focus = ""
        self.scene = ""
        self.rows: list[dict[str, Any]] = []
        self.summary = ""
        self.message = ""
        self.audit: dict[str, Any] | None = None
        self.job: asyncio.Task[None] | None = None
        self._designer: asyncio.Task[None] | None = None
        self._tasks: dict[str, Task] = {}

    def busy(self) -> bool:
        return self.job is not None and not self.job.done()

    def start(self, focus: str = "", scene: str = "") -> None:
        if self.busy():
            raise ValueError(BUSY)
        if scene and not scene.startswith("res://"):
            raise ValueError("Name the level as res://… .")
        self.focus = focus.strip()[:200]
        self.scene = scene.strip()
        self.rows = []
        self.summary = ""
        self.message = ""
        self.audit = None
        self._tasks = {}
        self.job = asyncio.create_task(self._run())

    async def tool(self, params: dict[str, Any]) -> dict[str, Any]:
        """The ``sound_audit`` tool, for whichever agent calls it."""
        scene = str(params.get("scene", "") or self.scene or "")
        if len(scene) > 300 or (scene and not scene.startswith("res://")):
            raise ValueError("scene is a level as res://… .")
        audit = await asyncio.to_thread(sound_pass.audit, self.project, scene)
        return audit_for_agent(audit)

    async def stop(self) -> None:
        """Stop the queued and working changes; changes in review stay."""
        for job in (self.job, self._designer):
            if job is not None and not job.done():
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
        for task in list(self._tasks.values()):
            if task.state == WORKING:
                with contextlib.suppress(ValueError):
                    await self.crew.stop(task.id)

    def event(self, state: str = "", **extra: Any) -> dict[str, Any]:
        if state:
            self.state = state
        data: dict[str, Any] = {
            "kind": "sound_pass",
            "state": self.state,
            "focus": self.focus,
            "tasks": self.rows,
        }
        if self.audit is not None:
            data["audit"] = self.audit
        if self.summary:
            data["summary"] = self.summary
        if self.message:
            data["message"] = self.message
        data.update(extra)
        return data

    def _event(self, state: str = "", **extra: Any) -> None:
        self.emit(self.event(state, **extra))

    async def _run(self) -> None:
        try:
            self._event("auditing")
            audit = await asyncio.to_thread(sound_pass.audit, self.project, self.scene)
            self.audit = audit_for_card(audit)
            if not audit.approved:
                self.message = ART_GATE_MESSAGE
                self._event("failed", art_gate=True)
                return
            if not audit.runtime:
                self.message = (
                    "This game's core has no sound nodes yet ("
                    + ", ".join(Path(p).name for p in audit.runtime_missing)
                    + "). Update the game core, then run the sound pass again."
                )
                self._event("failed")
                return
            categories = focus_categories(self.focus)
            planned = sound_plan.plan(
                self.project, audit, categories=categories or None
            )
            self.rows = [
                {
                    "key": task.key,
                    "title": task.title,
                    "category": task.category,
                    "categories": task.categories,
                    "apps": task.apps,
                    "sounds": task.sounds,
                    "facts": task.facts,
                    "task_id": "",
                    "state": "queued",
                    "message": "",
                }
                for task in planned
            ]
            if self.bridge is not None and self.endpoint is not None:
                self._designer = asyncio.create_task(self._design(audit))
            self._event("making")
            if planned:
                await self._schedule(planned)
            elif not audit.findings:
                self.message = "Nothing is missing: every sound has its place."
            else:
                self.message = "Nothing Joinery can place by itself."
            if self._designer is not None:
                # The report is short; the sounds never wait for it, the card does.
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        self._designer, spec("sound-designer").seconds
                    )
            self._event("done")
        except asyncio.CancelledError:
            for row in self.rows:
                if row["state"] == "queued":
                    row["state"] = "stopped"
            self._event("stopped")
            raise
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            self.message = str(exc)
            self._event("failed")

    def _free(self) -> int:
        """Turns free for the sounds: the chat's own request keeps its slot."""
        return max(1, self.crew.limit - 1) - len(self.crew.working())

    async def _schedule(self, planned: list[sound_plan.SoundTask]) -> None:
        queue = list(planned)
        rows = {row["key"]: row for row in self.rows}
        running: dict[str, Task] = {}
        while queue or running:
            while queue and self._free() > 0:
                job = queue.pop(0)
                row = rows[job.key]
                try:
                    task = await self.crew.start_scripted(
                        job.title,
                        job.request,
                        [
                            ScriptedCall(s.server, s.tool, s.arguments, s.label)
                            for s in job.steps
                        ],
                        prompt=self.prompt(),
                        targets=job.targets,
                        facts=job.facts,
                    )
                except ValueError as exc:
                    row.update(state="failed", message=str(exc))
                    self._event()
                    continue
                running[job.key] = self._tasks[job.key] = task
                row.update(task_id=task.id, state=task.state)
                self._event()
            await asyncio.sleep(POLL)
            for key, task in list(running.items()):
                if task.state == WORKING:
                    continue
                running.pop(key)
                rows[key].update(state=task.state, message=task.message)
                self._event()

    async def _design(self, audit: sound_pass.Audit) -> None:
        """The sound designer's report, in its read-only role."""
        assert self.bridge is not None and self.endpoint is not None
        child: PlaytestAgent | None = None
        endpoint: Any = None
        try:
            child = self.bridge(self.agent)
            endpoint = self.endpoint()
            endpoint.sound_audit = self.tool
            await endpoint.start()
            child.bind_gateway(endpoint.path)
            await child.connect(self.project, self.profile)
            making = "\n".join(
                f"- {row['title']}: {', '.join(row['sounds']) or 'placement only'}"
                for row in self.rows
            )
            parts = SOUND_DESIGNER_PROMPT.fill(
                level=audit.level,
                focus=self.focus or "the whole level",
                making=making or "- nothing: every sound has its place",
            )
            prompt = Prompt(
                text=parts["text"],
                rules=parts["rules"],
                project_dir=str(self.project),
            )
            messages: list[str] = []
            async for event in child.send(prompt):
                if isinstance(event, MessageDelta):
                    if event.new_message or not messages:
                        messages.append("")
                    messages[-1] += event.text
                elif isinstance(event, Message) and event.level != "refuse":
                    messages.append(event.text)
                elif isinstance(event, Done):
                    break
            written = next((m for m in reversed(messages) if m.strip()), "")
            self.summary = written.strip()[:REPORT_LIMIT]
            if self.summary:
                self._event()
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            # The sounds go on; the card keeps the audit's own facts.
            self.message = f"The sound designer couldn't write its report: {exc}"
        finally:
            for closer in (child, endpoint):
                if closer is not None:
                    with contextlib.suppress(Exception):
                        await closer.close()
