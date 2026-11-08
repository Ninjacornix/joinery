"""Bounded, cancellable inspection agents behind a read-only gateway endpoint."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from bridge.host import Bridge
from bridge.models import Selection
from bridge.protocol import Done, Message, MessageDelta, Prompt
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC


class Specialists:
    def __init__(
        self,
        gateway: ProjectGateway,
        agent: str,
        profile: str,
        on_stop: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.gateway = gateway
        self.agent = agent
        self.profile = profile
        self.tasks: set[asyncio.Task[dict[str, str]]] = set()
        self.limit = asyncio.Semaphore(2)
        self.on_stop = on_stop

    async def inspect(self, tasks: list[dict[str, str]]) -> list[dict[str, str]]:
        if not 1 <= len(tasks) <= 2:
            raise ValueError("Delegate at most two independent inspection questions.")
        jobs = [asyncio.create_task(self._run(job)) for job in tasks]
        self.tasks.update(jobs)
        try:
            return await asyncio.gather(*jobs)
        finally:
            for job in jobs:
                if not job.done():
                    job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            self.tasks.difference_update(jobs)

    async def _run(self, job: dict[str, str]) -> dict[str, str]:
        async with self.limit:
            child = Bridge(self.agent, inspection_only=True)
            endpoint = GatewayRPC(self.gateway, self.on_stop, inspection_only=True)
            try:
                async with asyncio.timeout(90):
                    self.gateway.progress("Inspecting in parallel…")
                    await endpoint.start()
                    child.bind_gateway(endpoint.path)
                    await child.connect(self.gateway.project, self.profile)
                    parts: list[str] = []
                    success = False
                    prompt = Prompt(
                        text="Read-only inspection. Return concise findings and evidence. Never make changes or delegate. Question:\n"
                        + job["question"],
                        project_dir=str(self.gateway.project),
                        editor_context=self.gateway.editor_context,
                        selection=Selection(mode="fast"),
                    )
                    async for event in child.send(prompt):
                        if isinstance(event, (Message, MessageDelta)):
                            parts.append(event.text)
                            if sum(len(part) for part in parts) > 8000:
                                raise ValueError(
                                    "Specialist exceeded its concise response limit."
                                )
                        elif isinstance(event, Done):
                            success = event.ok
                    return {
                        "name": job["name"],
                        "status": "done" if success else "failed",
                        "text": "".join(parts),
                    }
            except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                return {"name": job["name"], "status": "failed", "text": str(exc)}
            finally:
                await child.close()
                await endpoint.close()

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
