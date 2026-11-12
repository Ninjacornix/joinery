"""The chat host's side of continuing a turn with another agent on a quota error.

``bridge.turn_runner`` notices the provider's usage or quota limit and asks
:meth:`ProviderSwitch.offer`. The sidebar shows one action ("Continue with
Codex"); the turn waits, its staged change untouched, until the user answers,
stops the request, or ten minutes pass. With ``[agents] fallback_provider`` set
the switch happens without asking and the transcript says so. The other agent
gets its own provider session through the same project gateway, so it sees and
builds on the same stage, and starts from a summary of the chat and the list
of staged files. It serves the rest of that request only; the next request goes
to the agent the user chose in the sidebar (ADR 0080).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from joinery.agents import get
from joinery.fallback import candidates, continue_text, setting

#: How long an offer waits for the user, seconds.
WAIT_SECONDS = 600.0


class CarryBridge(Protocol):
    def send(self, message: Any) -> Any: ...

    def editor_result(self, request_id: str, result: dict[str, Any]) -> None: ...

    async def connect(self, project: Path, profile_id: str = "") -> Any: ...

    async def cancel(self) -> None: ...

    async def close(self) -> None: ...


def name_of(agent_id: str) -> str:
    try:
        return get(agent_id).name
    except KeyError:
        return agent_id


class ProviderSwitch:
    def __init__(
        self,
        project: Path,
        agent_id: str,
        emit: Callable[[dict[str, Any]], None],
        make_bridge: Callable[[str], CarryBridge],
        *,
        request: Callable[[], str],
        summary: Callable[[], str],
        staged: Callable[[], list[dict[str, Any]]],
        ready: Callable[[str], bool] | None = None,
        wait_seconds: float = WAIT_SECONDS,
    ) -> None:
        self.project = project
        self.agent_id = agent_id
        self.emit = emit
        self.make_bridge = make_bridge
        self.request = request
        self.summary = summary
        self.staged = staged
        self.ready = ready
        self.wait_seconds = wait_seconds
        #: Bridges opened for this request; closed when it ends.
        self.carried: list[CarryBridge] = []
        #: The agent working on the request now, and those already out of quota.
        self.current = agent_id
        self.tried = {agent_id}
        self._answer: asyncio.Future[str] | None = None

    async def offer(self, reason: str) -> tuple[CarryBridge, str] | None:
        """Offer another signed-in agent; open it if the user (or the project) says
        so. Returns its bridge and first prompt, or None. ``reason`` is the
        provider's own words, already shown in the transcript."""
        del reason
        stopped = name_of(self.current)
        found = await asyncio.to_thread(candidates, self.current, self.ready)
        choices = [c for c in found if c["id"] not in self.tried]
        if not choices:
            self.emit(
                {
                    "kind": "notice",
                    "level": "warn",
                    "text": f"{stopped} ran out of quota, and no other agent is signed "
                    "in to carry the request on. Sign one in under Settings, then ask "
                    "again.",
                }
            )
            return None
        ids = {c["id"] for c in choices}
        automatic = setting(self.project)
        chosen = automatic if automatic in ids else ""
        if not chosen:
            chosen = await self._ask(stopped, choices[0])
            if chosen not in ids:
                self.emit({"kind": "provider_switch", "state": "declined"})
                return None
        other = self.make_bridge(chosen)
        try:
            await other.connect(self.project, "")
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            with contextlib.suppress(Exception):
                await other.close()
            self.emit(
                {
                    "kind": "notice",
                    "level": "warn",
                    "text": f"{name_of(chosen)} could not start: {exc}",
                }
            )
            return None
        self.carried.append(other)
        self.tried.add(chosen)
        self.current = chosen
        said = (
            f"{name_of(chosen)} carries this request on from where {stopped} stopped."
        )
        if chosen == automatic:
            said += " It is this project's fallback agent."
        self.emit(
            {
                "kind": "provider_switch",
                "state": "switched",
                "from": self.agent_id,
                "to": chosen,
                "to_name": name_of(chosen),
                "message": said,
            }
        )
        text = continue_text(stopped, self.request(), self.summary(), self.staged())
        return other, text

    async def _ask(self, stopped: str, first: dict[str, str]) -> str:
        self._answer = asyncio.get_running_loop().create_future()
        self.emit(
            {
                "kind": "provider_switch",
                "state": "offer",
                "from": self.current,
                "to": first["id"],
                "to_name": first["name"],
                "message": f"{stopped} ran out of quota. The change so far stays staged.",
            }
        )
        try:
            return await asyncio.wait_for(self._answer, self.wait_seconds)
        except TimeoutError:
            return ""
        finally:
            self._answer = None

    def waiting(self) -> bool:
        return self._answer is not None and not self._answer.done()

    def answer(self, agent: str) -> None:
        """The sidebar's answer: an agent id, or "" for no."""
        if self._answer is None or self._answer.done():
            raise ValueError("No request is waiting for another agent.")
        self._answer.set_result(agent)

    def editor_result(self, request_id: str, result: dict[str, Any]) -> None:
        for other in self.carried:
            other.editor_result(request_id, result)

    async def cancel(self) -> None:
        if self._answer is not None and not self._answer.done():
            self._answer.set_result("")
        for other in self.carried:
            with contextlib.suppress(Exception):
                await other.cancel()

    async def close(self) -> None:
        """The request ended: close the bridges it opened; the next starts over."""
        carried, self.carried = self.carried, []
        for other in carried:
            with contextlib.suppress(Exception):
                await other.close()
        self.current = self.agent_id
        self.tried = {self.agent_id}
