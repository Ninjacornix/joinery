"""Private project IPC. Approval is deliberately absent from the model-facing API."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from joinery import compact, design, live_game, skills
from joinery.blockout import stage as blockout_stage
from joinery.debugging import diagnosis_from
from joinery.gateway import ProjectGateway
from joinery.mcp_runtime import PolicyStop
from joinery.peer_review import notes_from
from joinery.platform_support import private_directory
from joinery.read_access import read_only
from joinery.roles import (
    NO_DRIVING,
    READ_ONLY_ROLES,
    may_drive,
    refusal,
    registry_allowed,
    spec,
)

#: Methods whose answer reaches the agent as a summary with a details reference when
#: it is long (joinery.compact). The editor context has its own outline; schemas,
#: reports and details pages are short or must stay whole.
COMPACTED = frozenset(
    {
        "inventory",
        "call",
        "submit",
        "precheck",
        "delegate",
        "reproduce",
        "blockout",
        "playtest",
        "access_check",
        "perf_check",
        "sound_audit",
        "task_status",
    }
)


def search_tools(data: dict[str, Any], query: str) -> dict[str, Any]:
    """Rank words rather than requiring an entire sentence to be a substring."""
    words = set(re.findall(r"[a-z0-9]+", query.lower())) - {
        "a",
        "an",
        "and",
        "the",
        "to",
        "for",
        "with",
        "in",
        "of",
    }
    candidates = []
    for tool in data["tools"]:
        haystack = set(
            re.findall(
                r"[a-z0-9]+",
                (
                    tool["server"]
                    + " "
                    + tool["name"]
                    + " "
                    + tool.get("description", "")
                ).lower(),
            )
        )
        score = len(words & haystack)
        if not words or score:
            candidates.append((score, tool))
    candidates.sort(key=lambda entry: -entry[0])
    return {
        "servers": [
            {
                **{k: v for k, v in state.items() if k != "tools"},
                "tool_count": len(state.get("tools", [])),
            }
            for state in data["servers"]
        ],
        "tools": [
            {
                "server": tool["server"],
                "name": tool["name"],
                "description": tool.get("description", "")[:500],
            }
            for _, tool in candidates[:24]
        ],
        "total_matches": len(candidates),
        "hint": "Search by application or operation to narrow results; read tool_schema before calling. Native document operations save, export, import and record provenance automatically. Call submit_for_review when the change is complete; Joinery checks it then and opens the review.",
    }


#: A task asked to change the scene on screen.
TASK_LIVE = (
    "Only the main request changes the scene on screen. Change scenes that aren't "
    "open as staged files, or leave this to the main request."
)
NO_NESTED_TASKS = "A task can't start further tasks; do this part yourself."


def task_request(params: dict[str, Any]) -> dict[str, Any]:
    """start_task's arguments, checked: {title, request, targets}."""
    title, request = params.get("title"), params.get("request")
    targets = params.get("targets", [])
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
        raise ValueError("Give the task a short title (1 to 80 characters).")
    if not isinstance(request, str) or not 1 <= len(request.strip()) <= 4000:
        raise ValueError(
            "Describe the task completely in request (1 to 4000 characters); "
            "it does not see this conversation."
        )
    if (
        not isinstance(targets, list)
        or len(targets) > 20
        or any(not isinstance(t, str) or not 1 <= len(t) <= 400 for t in targets)
    ):
        raise ValueError("targets is a list of up to 20 project paths.")
    return {"title": title.strip(), "request": request.strip(), "targets": targets}


def variants_request(params: dict[str, Any]) -> dict[str, Any]:
    """start_variants' arguments, checked: {title, request, directions, targets}."""
    checked = task_request(params)
    directions = params.get("directions", [])
    if (
        not isinstance(directions, list)
        or not 2 <= len(directions) <= 4
        or any(
            not isinstance(d, str) or not 1 <= len(d.strip()) <= 80 for d in directions
        )
    ):
        raise ValueError(
            "directions is a list of 2 to 4 short notes (up to 80 characters each), "
            "one per way."
        )
    return {**checked, "directions": [d.strip() for d in directions]}


class GatewayRPC:
    def __init__(
        self,
        gateway: ProjectGateway,
        on_stop: Callable[[str], Awaitable[None]] | None = None,
        *,
        transport: Literal["unix", "tcp"] | None = None,
        inspection_only: bool = False,
        worker: bool = False,
        role: str = "",
    ) -> None:
        self.gateway = gateway
        #: The agent role this endpoint serves (joinery.roles); "" for a specialist.
        self.role = spec(role).role if role else ""
        if self.role in READ_ONLY_ROLES:
            inspection_only = True
        if self.role == "worker":
            worker = True
        self.transport = transport or ("tcp" if os.name == "nt" else "unix")
        # Unix socket names have a short OS limit. Windows uses its user's temp
        # directory instead; its ACL is tightened before creating the endpoint.
        self.directory = Path(
            tempfile.mkdtemp(
                prefix="joinery-rpc-", dir=None if os.name == "nt" else "/tmp"
            )
        )
        try:
            private_directory(self.directory)
        except BaseException:
            shutil.rmtree(self.directory, ignore_errors=True)
            raise
        self.path = self.directory / (
            "gateway.endpoint.json" if self.transport == "tcp" else "gateway.sock"
        )
        self._token = secrets.token_urlsafe(32) if self.transport == "tcp" else ""
        self.server: asyncio.Server | None = None
        self.tasks: set[asyncio.Task[Any]] = set()
        self.on_stop = on_stop
        self.stop_reason = ""
        self.inspection_only = inspection_only
        #: Serves a parallel task: it can't start tasks, and its lane outlives it.
        self.worker = worker
        self.delegate: (
            Callable[[list[dict[str, str]]], Awaitable[list[dict[str, str]]]] | None
        ) = None
        #: Hand an independent part of the request to a parallel task (the lead only).
        self.start_task: (
            Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None
        self.task_status: Callable[[str], dict[str, Any]] | None = None
        #: Make the request a few ways side by side (the lead only, ADR 0042).
        self.start_variants: (
            Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None
        #: What a read-only role reported (report_review, report_diagnosis,
        #: report_design).
        self.reports: list[dict[str, Any]] = []
        #: Run the project headless offline to reproduce an error (the debugger only).
        self.reproduce: Callable[[str], Awaitable[dict[str, Any]]] | None = None
        #: Play the game headless with the playtest bot (read-only, so every agent may).
        self.playtest: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = (
            None
        )
        #: What the level is missing to sound like a game (read-only, any agent may).
        self.sound_audit: (
            Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None
        #: The accessibility and performance checks (read-only): kind, params -> report.
        self.checks: (
            Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None

    def may_drive(self) -> bool:
        """May this endpoint's agent send input to the running game? A specialist with
        no role only reads; a role as joinery.roles.may_drive says."""
        if not self.role:
            return not self.inspection_only
        return may_drive(self.role, self.gateway.project)

    async def start(self) -> None:
        if self.transport == "tcp":
            self.server = await asyncio.start_server(
                self._client, "127.0.0.1", 0, limit=4_000_000
            )
            port = self.server.sockets[0].getsockname()[1]
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(
                    {
                        "version": 1,
                        "host": "127.0.0.1",
                        "port": port,
                        "token": self._token,
                    },
                    stream,
                )
        else:
            if sys.platform == "win32":
                raise ValueError("Native Windows gateways require local TCP transport.")
            else:
                self.server = await asyncio.start_unix_server(
                    self._client, str(self.path), limit=4_000_000
                )
                os.chmod(self.path, 0o600)

    async def _client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self.tasks.add(task)
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            request = json.loads(line)
            if not isinstance(request, dict):
                raise TypeError("Invalid gateway request.")
            if self._token:
                token = request.get("token")
                if not isinstance(token, str) or not secrets.compare_digest(
                    token, self._token
                ):
                    raise ValueError("Gateway authentication failed.")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise TypeError("Invalid gateway parameters.")
            if self.stop_reason:
                raise PolicyStop(self.stop_reason)
            if method == "policy_stop":
                # The policy hook of the agent this gateway serves (main or
                # specialist) stopped a Rule Zero call: the whole turn stops.
                reason = params.get("reason")
                if not isinstance(reason, str) or not reason:
                    raise ValueError("Missing policy-stop reason.")
                raise PolicyStop(reason[:1000])
            if method == "inventory":
                data = await self.gateway.discover()
                if self.inspection_only:
                    data = {
                        **data,
                        "tools": [
                            t
                            for t in data["tools"]
                            if read_only(t["server"], t["name"])
                        ],
                    }
                data = search_tools(data, str(params.get("query", "")))
            elif method == "schema":
                connection = await self.gateway.connection(str(params["server"]))
                data = connection.tools[str(params["tool"])].model_dump(
                    by_alias=True, exclude_none=True
                )
            elif method == "call":
                if self.inspection_only and not read_only(
                    str(params["server"]), str(params["tool"])
                ):
                    raise ValueError(
                        "Specialists can only inspect project tools; mutations stay on the main thread."
                    )
                arguments = params.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be an object.")
                result = await self.gateway.call(
                    str(params["server"]),
                    str(params["tool"]),
                    arguments,
                    view=self.inspection_only,
                )
                data = self.gateway.serialise(result)
                if str(params["server"]) == "godot" and (
                    str(params["tool"]).startswith("game_")
                    or params["tool"] in {"run_project", "stop_project"}
                ):
                    data["joinery"] = {
                        "target": "running-game",
                        "persistent": False,
                        "requires_review": bool(
                            self.gateway.transaction and self.gateway.transaction.diff()
                        ),
                    }
                elif self.gateway.transaction is not None:
                    data["joinery"] = {
                        "status": "staged",
                        "requires_review": True,
                        "next": "Call submit_for_review when the change is complete.",
                    }
            elif method == "editor_context":
                node = str(params.get("node", "") or "")
                data = (
                    compact.editor_nodes(self.gateway.editor_context, node)
                    if node
                    else {
                        "editor": compact.editor_outline(
                            self.gateway.editor_context, compact.INSPECT_NODES
                        )
                    }
                )
            elif method == "details":
                data = compact.details(
                    self.gateway.project,
                    str(params.get("ref", "")),
                    str(params.get("path", "") or ""),
                    int(params.get("offset", 0) or 0),
                )
            elif method == "find_recipe":
                if self.role and not registry_allowed(self.role, method):
                    raise ValueError(refusal(self.role))
                data = skills.find(
                    self.gateway.project,
                    str(params.get("request", "")),
                    int(params.get("limit", 3) or 3),
                )
            elif method == "editor_view":
                # Read-only, so specialists may look too.
                if self.gateway.view_editor is None:
                    raise ValueError("No live editor is connected to this gateway.")
                data = await self.gateway.view_editor()
            elif method == "inspect_game":
                # Reads only, so every agent may look, specialists too.
                data = await live_game.inspect(
                    self.gateway.game_call, self.gateway.project, params
                )
            elif method == "drive_game":
                if not self.may_drive():
                    raise ValueError(NO_DRIVING)
                data = await live_game.drive(self.gateway.game_call, params)
                if params.get("screenshot_after"):
                    shot = await live_game.inspect(
                        self.gateway.game_call,
                        self.gateway.project,
                        {"tree": False, "screenshot": True},
                    )
                    data["screenshot"] = shot["screenshot"]
            elif method == "editor_edit":
                if self.inspection_only:
                    raise ValueError("Specialists cannot change the live editor.")
                if self.gateway.edit_scene is None:
                    raise ValueError(
                        TASK_LIVE
                        if self.worker
                        else "No live editor is connected to this gateway."
                    )
                data = await self.gateway.edit_scene(params)
            elif method == "submit":
                if self.inspection_only:
                    raise ValueError(
                        "Specialists cannot submit changes; the main thread does."
                    )
                if self.gateway.turn is None:
                    raise ValueError("No user request is open.")
                summary = params.get("summary")
                if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 160:
                    raise ValueError(
                        "Give one short line (1 to 160 characters) naming what changed."
                    )
                facts = params.get("facts") or []
                if not isinstance(facts, list) or not all(
                    isinstance(fact, str) for fact in facts
                ):
                    raise ValueError("facts is a list of short phrases.")
                submitted = await self.gateway.turn.submit(summary, "agent", facts)
                data = submitted.as_tool_result()
            elif method == "precheck":
                if self.inspection_only:
                    raise ValueError(
                        "Specialists cannot precheck changes; the main thread does."
                    )
                if self.gateway.turn is None:
                    raise ValueError("No user request is open.")
                data = await self.gateway.turn.precheck()
            elif method == "propose_todo":
                if self.worker or self.inspection_only or self.role not in {"", "lead"}:
                    raise ValueError(
                        "Only Journeyman's own request proposes a to-do; do the work."
                    )
                if self.gateway.turn is None:
                    raise ValueError("No user request is open.")
                data = self.gateway.turn.propose_todo(params)
            elif method == "delegate":
                if self.inspection_only or self.delegate is None:
                    raise ValueError("Nested specialist delegation is unavailable.")
                jobs = params.get("tasks")
                if (
                    not isinstance(jobs, list)
                    or not 1 <= len(jobs) <= 2
                    or any(
                        not isinstance(job, dict)
                        or set(job) != {"name", "question"}
                        or any(
                            not isinstance(v, str) or not 1 <= len(v) <= 4000
                            for v in job.values()
                        )
                        for job in jobs
                    )
                ):
                    raise ValueError(
                        "Supply one or two named read-only inspection questions (up to 4000 characters)."
                    )
                data = {"results": await self.delegate(jobs)}
            elif method == "start_task":
                if self.worker or self.inspection_only:
                    raise ValueError(NO_NESTED_TASKS)
                if self.start_task is None:
                    raise ValueError("Parallel tasks are not available here.")
                data = await self.start_task(task_request(params))
            elif method == "start_variants":
                if self.worker or self.inspection_only:
                    raise ValueError(NO_NESTED_TASKS)
                if self.start_variants is None:
                    raise ValueError("Variants are not available here.")
                data = await self.start_variants(variants_request(params))
            elif method == "report_review":
                if self.role != "reviewer":
                    raise ValueError("Only the reviewer reports a review.")
                notes = notes_from(params)
                self.reports.append({"notes": [note.as_dict() for note in notes]})
                data = {
                    "recorded": len(notes),
                    "next": "Your review is recorded. End your turn now.",
                }
            elif method == "report_diagnosis":
                if self.role != "debugger":
                    raise ValueError("Only the debugger reports a diagnosis.")
                found = diagnosis_from(params)
                self.reports.append(
                    {
                        "cause": found.cause,
                        "fix": found.fix,
                        "targets": found.targets,
                        "reproduced": found.reproduced,
                    }
                )
                data = {
                    "recorded": True,
                    "next": "Your diagnosis is recorded. End your turn now; a worker "
                    "makes the fix and the user reviews it.",
                }
            elif method == "report_design":
                if self.role != "designer":
                    raise ValueError("Only the design check reports design findings.")
                kept, dropped = design.agent_findings(self.gateway.project, params)
                self.reports.append(
                    {
                        "findings": [f.model_dump() for f in kept],
                        "dropped": dropped,
                    }
                )
                data = {
                    "recorded": len(kept),
                    "dropped": dropped,
                    "next": (
                        "Recorded. Findings whose evidence didn't hold were dropped; "
                        "report them again with the right file, line and words if they "
                        "are real, or end your turn now."
                        if dropped
                        else "Recorded. End your turn now."
                    ),
                }
            elif method == "reproduce":
                if self.role != "debugger" or self.reproduce is None:
                    raise ValueError("Only the debugger reproduces errors.")
                data = await self.reproduce(str(params.get("scene", "") or ""))
            elif method == "blockout":
                if self.inspection_only:
                    raise ValueError(
                        "Specialists cannot build blockouts; the main thread does."
                    )
                data = await blockout_stage.tool(self.gateway, params)
            elif method == "playtest":
                if self.playtest is None:
                    raise ValueError("Playtests are not available here.")
                data = await self.playtest(params)
            elif method in {"access_check", "perf_check"}:
                if self.checks is None:
                    raise ValueError("The checks are not available here.")
                if self.role and not registry_allowed(self.role, method):
                    raise ValueError(refusal(self.role))
                data = await self.checks(method.removesuffix("_check"), params)
            elif method == "sound_audit":
                if self.sound_audit is None:
                    raise ValueError("The sound pass is not available here.")
                data = await self.sound_audit(params)
            elif method == "task_status":
                if self.task_status is None:
                    raise ValueError("Parallel tasks are not available here.")
                data = self.task_status(str(params.get("id", "")))
            else:
                raise ValueError(
                    "The model interface supports inventory, schema, call, submit, propose_todo, blockout, playtest, the checks, sound_audit and the running game's inspect_game and drive_game; no approval methods."
                )
            project = getattr(self.gateway, "project", None)
            if method in COMPACTED and isinstance(project, Path):
                try:
                    data = compact.compact(project, str(method), data)
                except OSError:
                    pass  # Nowhere to keep the full result: the agent gets it whole.
            response = {"ok": True, "result": data}
        except PolicyStop as exc:
            first = not self.stop_reason
            self.stop_reason = str(exc)
            response = {"ok": False, "stop": True, "error": self.stop_reason}
            if first and self.on_stop is not None:
                asyncio.ensure_future(self.on_stop(self.stop_reason))
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            RuntimeError,
            TimeoutError,
        ) as exc:
            response = {"ok": False, "error": str(exc)}
        except asyncio.CancelledError:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            return
        finally:
            if task is not None:
                self.tasks.discard(task)
        try:
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if not self.inspection_only and not self.worker:
            await self.gateway.close()
        shutil.rmtree(self.directory, ignore_errors=True)


async def rpc(path: Path, method: str, params: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"method": method, "params": params}
    if path.name.endswith(".endpoint.json"):
        endpoint = json.loads(path.read_text())
        if (
            not isinstance(endpoint, dict)
            or endpoint.get("version") != 1
            or endpoint.get("host") != "127.0.0.1"
            or type(endpoint.get("port")) is not int
            or not 1 <= endpoint["port"] <= 65535
            or not isinstance(endpoint.get("token"), str)
            or len(endpoint["token"]) < 32
        ):
            raise ValueError("Invalid local gateway endpoint.")
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", endpoint["port"], limit=4_000_000
        )
        payload["token"] = endpoint["token"]
    else:
        if sys.platform == "win32":
            raise ValueError("Native Windows requires a local gateway endpoint file.")
        else:
            reader, writer = await asyncio.open_unix_connection(
                str(path), limit=4_000_000
            )
    try:
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
        data = json.loads(await asyncio.wait_for(reader.readline(), timeout=900))
        if not data["ok"]:
            raise RuntimeError(data["error"])
        result: dict[str, Any] = data["result"]
        return result
    finally:
        writer.close()
        await writer.wait_closed()
