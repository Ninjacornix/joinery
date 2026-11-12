"""Framed local chat transport used by the editor's polled native pipes."""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import json
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from bridge import compaction, mixer_save
from bridge.chat_record import ChatRecorder
from bridge.check_runner import Checks
from bridge.crew import Crew
from bridge.designer import Designer
from bridge.host import Bridge
from bridge.instructions import expand_command
from bridge.match_runner import MatchRun
from bridge.models import Selection
from bridge.playtester import Playtester, gameplay_change
from bridge.protocol import Done, EditorAction, Message, Prompt
from bridge.reviewer import Debugger, ReviewAgent
from bridge.sound_pass import SoundPassRun
from bridge.specialists import Specialists
from bridge.turn_runner import run_turn, settle_compaction
from bridge.variants import Variants, parse_command
from joinery import (
    bible_lint,
    blueprints,
    brief,
    chat_attachments,
    chats,
    complex_models,
    playtest,
)
from joinery.art_approval import approve, review
from joinery.authoring import run_offline
from joinery.claims import parallel_limit
from joinery.debugging import debug_request
from joinery.editor_ops import validate_edit
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC
from joinery.timing import TurnTiming
from joinery.turn import TurnController

#: The open chat's recorder while a host serves; it sees every event the sidebar does.
_recorder: ChatRecorder | None = None


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event), flush=True)
    if _recorder is not None:
        _recorder.observe(event)


_FEATURE_OFFER = re.compile(r"\s*Add (?:it|them) with (/feature add [a-z0-9-]+)\?")


def game_suggestions(project: Path) -> dict[str, Any]:
    """Features the game brief asks for that the game doesn't have yet, as one-line offers.

    Only offers: the sidebar shows them one at a time and runs nothing until the user
    chooses, and then only the plan (`/feature add <id>`), never the change itself.
    """
    try:
        lines = brief.consistency(project, blueprints.project_game(project))
    except (OSError, ValueError):
        lines = []
    items: list[dict[str, str]] = []
    for line in lines:
        found = _FEATURE_OFFER.search(line)
        if found is None:
            continue
        text = line[: found.start()].strip()
        items.append({"text": text, "command": found.group(1)})
    return {"kind": "suggestions", "items": items}


def art_review_event(project: Path) -> dict[str, Any]:
    """The art direction for the review dialog: the bible's exact bytes and hash, plus
    its format version, reference board and the one-line suggestions from the lint."""
    event: dict[str, Any] = dict(review(project))
    try:
        report = bible_lint.summary(project)
    except (OSError, ValueError):
        report = {}
    event["version"] = int(report.get("version", 0) or 0)
    event["references"] = [
        {
            "id": str(row.get("id", "")),
            "reference": str(row.get("reference", "")),
            "kind": str(row.get("kind", "")),
            "take": str(row.get("take", "")),
            "avoid": str(row.get("avoid", "")),
            "file": str(row.get("file", "")) if row.get("file_exists") else "",
        }
        for row in report.get("references", [])
    ]
    event["suggestions"] = [str(line) for line in report.get("suggestions", [])]
    return event


def editor_view(project: Path, result: dict[str, Any]) -> dict[str, Any]:
    """The sidebar's answer to a view request, checked: the screenshot must be one it saved
    under the attachment folder. Returns {path, view, width, height, camera}."""
    if not result.get("success"):
        raise ValueError(
            str(result.get("message") or "The editor view could not be captured.")
        )
    (path,) = chat_attachments.validate(project, [str(result.get("path", ""))])
    camera = result.get("camera")
    return {
        "path": path,
        "view": str(result.get("view", "")),
        "width": int(result.get("width", 0) or 0),
        "height": int(result.get("height", 0) or 0),
        "camera": camera if isinstance(camera, dict) else {},
        "note": "A screenshot of the user's editor for context, not an asset.",
    }


class ThreadInput:
    """Read synchronous Windows anonymous stdin pipes without blocking asyncio.

    Godot uses CreatePipe, not overlapped named pipes. Proactor connect_read_pipe
    cannot safely register those handles. This daemon owns no editor state and
    exits on EOF; shutdown never waits for a blocked executor thread.
    """

    def __init__(self, stream: BinaryIO, reader: asyncio.StreamReader) -> None:
        self.closed = threading.Event()
        loop = asyncio.get_running_loop()

        def deliver(callback: Callable[..., None], *values: Any) -> None:
            if not self.closed.is_set():
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(callback, *values)

        def pump() -> None:
            try:
                while not self.closed.is_set():
                    line = stream.readline(1_000_001)
                    if len(line) > 1_000_000:
                        deliver(
                            reader.set_exception,
                            ValueError("Chat input exceeds the message size limit."),
                        )
                        return
                    if not line:
                        deliver(reader.feed_eof)
                        return
                    deliver(reader.feed_data, line)
            except OSError:
                deliver(reader.set_exception, ConnectionError("Chat input closed."))

        self.thread = threading.Thread(target=pump, name="joinery-stdin", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.closed.set()


class InputTransport(Protocol):
    def close(self) -> None: ...


async def input_reader(stream: BinaryIO) -> tuple[asyncio.StreamReader, InputTransport]:
    reader = asyncio.StreamReader(limit=1_000_000)
    if os.name == "nt":
        return reader, ThreadInput(stream, reader)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), stream
    )
    return reader, transport


async def serve(project: Path, agent_id: str, profile_id: str = "") -> int:
    global _recorder
    recorder = _recorder = ChatRecorder(project, agent_id, profile_id)
    try:
        return await _serve(project, agent_id, profile_id, recorder)
    finally:
        _recorder = None


async def _serve(
    project: Path, agent_id: str, profile_id: str, recorder: ChatRecorder
) -> int:
    bridge: Bridge | None = None
    active: asyncio.Task[None] | None = None
    specialists: Specialists | None = None
    crew: Crew | None = None
    variants: Variants | None = None
    playtester: Playtester | None = None
    designer: Designer | None = None
    matcher: MatchRun | None = None
    checks: Checks | None = None
    sounder: SoundPassRun | None = None
    #: The last request's model choice, which the tasks it starts use too.
    selection: dict[str, Any] = {}
    try:
        gateway = ProjectGateway(project)
    except (OSError, ValueError) as exc:
        emit(
            {
                "kind": "connection",
                "state": "error",
                "agent": agent_id,
                "message": str(exc),
            }
        )
        return 1
    editor_calls: dict[str, asyncio.Future[dict[str, Any]]] = {}
    gateway.progress = lambda text: emit({"kind": "progress", "text": text})
    controller = TurnController(gateway, emit, agent_id)
    session_start = time.time()

    async def live_edit(
        lane: ProjectGateway,
        turn: TurnController,
        payload: dict[str, Any],
        owner: str = "",
    ) -> dict[str, Any]:
        """One live edit of the scene on screen, by the chat's request or a task."""
        context = lane.editor_context
        known = json.loads(context.get("selection_instances", "{}"))
        edit = validate_edit(project, payload, tuple(known))
        if not owner and (active is None or active.done()):
            raise ValueError("Editor actions require an active user request.")
        scene = turn.admit_live_edit(context)
        request_id = uuid.uuid4().hex
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        ok = False
        try:
            payload = edit.model_dump(mode="json")
            # A model made in this change is loaded from its stage (ADR 0054).
            staged = await lane.staged_files(payload)
            editor_calls[request_id] = future
            emit(
                EditorAction(
                    request_id=request_id,
                    payload=payload,
                    context=context,
                    targets=tuple(known),
                    owner=owner,
                    staged=staged,
                ).model_dump()
            )
            result = await asyncio.wait_for(future, 30)
            ok = bool(result.get("success"))
            return result
        finally:
            turn.record_live_edit(scene, ok, edit.model_dump(mode="json"))
            editor_calls.pop(request_id, None)

    async def edit_scene(payload: dict[str, Any]) -> dict[str, Any]:
        return await live_edit(gateway, controller, payload)

    gateway.edit_scene = edit_scene
    view_calls: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def view_editor() -> dict[str, Any]:
        """Ask the sidebar for a screenshot of the editor's viewport (read-only)."""
        if (active is None or active.done()) and not (crew and crew.working()):
            raise ValueError("Looking at the editor requires an active user request.")
        request_id = uuid.uuid4().hex
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        view_calls[request_id] = future
        emit({"kind": "editor_view", "request_id": request_id})
        try:
            return editor_view(project, await asyncio.wait_for(future, 15))
        finally:
            view_calls.pop(request_id, None)

    gateway.view_editor = view_editor

    async def policy_stop(reason: str) -> None:
        # A Rule Zero stop in any agent stops every agent: no task routes around it.
        if matcher is not None:
            await matcher.stop()
        if checks is not None:
            for run in checks.runs.values():
                await run.stop()
        if sounder is not None:
            await sounder.stop()
        if crew is not None:
            await crew.stop_all()
        if playtester is not None:
            await playtester.stop()
        if designer is not None:
            await designer.stop()
        if bridge is not None:
            with contextlib.suppress(Exception):
                await bridge.cancel()
        if active is not None and not active.done():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        await controller.cancel("stopped")
        emit(
            Message(
                text="Stopped by project policy: " + reason, level="warn"
            ).model_dump()
        )
        emit(Done(ok=False).model_dump())

    def emit_task(event: dict[str, Any]) -> None:
        emit(event)

    def task_prompt(data: dict[str, Any] | None = None) -> Prompt:
        """A task's prompt context: the user's own (Run alongside) or the lead's."""
        if data is None:
            return Prompt(
                text="",
                project_dir=str(project),
                target_context=gateway.target_context,
                editor_context=dict(gateway.editor_context),
                selection=Selection.model_validate(selection or {}),
            )
        context = data.get("targets", [])
        targets = tuple(
            {str(k): str(v) for k, v in entry.items()}
            for entry in (context if isinstance(context, list) else [])
            if isinstance(entry, dict)
        )
        editor = data.get("editor_context", {})
        return Prompt(
            text="",
            project_dir=str(project),
            targets=tuple(entry.get("id", "") for entry in targets),
            target_context=targets,
            editor_context=complex_models.with_setting(
                project,
                {str(k): str(v) for k, v in editor.items()}
                if isinstance(editor, dict)
                else {},
            ),
            selection=Selection.model_validate(data.get("selection", {})),
            attachments=chat_attachments.validate(project, data.get("attachments", [])),
        )

    rpc_server: GatewayRPC | None = None
    try:
        rpc_server = GatewayRPC(gateway, policy_stop)
        reader, transport = await input_reader(sys.stdin.buffer)
    except (OSError, ValueError) as exc:
        if rpc_server is not None:
            await rpc_server.close()
        else:
            await gateway.close()
        emit(
            {
                "kind": "connection",
                "state": "error",
                "agent": agent_id,
                "message": str(exc),
            }
        )
        return 1
    assert rpc_server is not None

    async def turn(data: dict[str, Any]) -> None:
        assert bridge is not None
        agent = bridge
        timing = TurnTiming()
        gateway.timing = timing
        outcome = "failed"
        try:
            context = data.get("targets", [])
            if not isinstance(context, list):
                raise TypeError("Editor selection is invalid.")
            targets = tuple(
                {str(k): str(v) for k, v in entry.items()}
                for entry in context
                if isinstance(entry, dict)
            )
            editor = data.get("editor_context", {})
            if not isinstance(editor, dict):
                raise TypeError("Editor scene context is invalid.")
            attachments = chat_attachments.validate(
                project, data.get("attachments", [])
            )
            # A summary still being written belongs to the chat open now: it
            # finishes before the sidebar's chat is opened.
            await settle_compaction(controller)
            # The sidebar names the chat ("" for a new one); without one, the same chat.
            if "chat_id" in data:
                switched = await recorder.switch(str(data["chat_id"]), bridge)
                if switched is not None:
                    emit(switched)
            text, started = recorder.begin(data, attachments)
            if started is not None:
                emit(started)
            # A sidebar command that is a request ("/blockout") reaches the agent in words.
            raw = str(data.get("text", ""))
            if text.endswith(raw) and expand_command(raw) != raw:
                text = text[: len(text) - len(raw)] + expand_command(raw)
            prompt = Prompt(
                text=text,
                project_dir=str(project),
                targets=tuple(entry.get("id", "") for entry in targets),
                target_context=targets,
                editor_context=complex_models.with_setting(
                    project, {str(k): str(v) for k, v in editor.items()}
                ),
                selection=Selection.model_validate(data.get("selection", {})),
                attachments=attachments,
            )
            gateway.editor_context = prompt.editor_context
            gateway.target_context = prompt.target_context
            gateway.request_summary = str(data["text"])
            gateway.attachments = attachments
            selection.clear()
            selection.update(prompt.selection.model_dump())
            outcome = await run_turn(
                controller,
                bridge,
                prompt,
                emit,
                timing,
                lambda: compaction.compact(
                    controller,
                    agent,
                    recorder,
                    project,
                    emit,
                    session_start,
                    chat_id=recorder.chat_id,
                ),
                request=str(data["text"]),
                chat_id=recorder.chat_id,
            )
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except (
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            TimeoutError,
        ) as exc:
            await controller.cancel("failed")
            with contextlib.suppress(Exception):
                await bridge.cancel()
            emit(
                Message(
                    text=str(exc) or "Codex could not complete this message.",
                    level="warn",
                ).model_dump()
            )
            emit(Done(ok=False).model_dump())
            if isinstance(exc, OSError):
                emit({"kind": "connection", "state": "error", "message": str(exc)})
        finally:
            if specialists is not None:
                await specialists.close()
            gateway.timing = None
            if crew is not None:
                # The chat's own turn is over: a variant waiting for it may start.
                asyncio.get_running_loop().call_soon(crew.pump)
            # Diagnostics must never turn successful project work into failure.
            try:
                emit(timing.save(project, outcome))
            except (OSError, ValueError, UnicodeError):
                emit(timing.report(outcome))
            recorder.finish(bridge)

    try:
        bridge = Bridge(agent_id)
        if not project.is_dir() or not (project / "project.godot").is_file():
            raise ValueError("Open a Godot project before connecting chat.")
        await rpc_server.start()
        bridge.bind_gateway(rpc_server.path)
        connection_timing = TurnTiming()
        await bridge.connect(project, profile_id)
        specialists = Specialists(gateway, agent_id, profile_id, policy_stop)

        def role_bridge(agent: str, role: str) -> Bridge:
            return Bridge(agent, role=role)

        def role_endpoint(lane: ProjectGateway, role: str) -> GatewayRPC:
            return GatewayRPC(lane, policy_stop, role=role)

        def reviewer_for(lane: ProjectGateway, turn: TurnController) -> ReviewAgent:
            """The reviewer agent that reads ``turn``'s submitted changes."""
            agent = ReviewAgent(
                lane,
                agent=agent_id,
                profile=profile_id,
                bridge=role_bridge,
                endpoint=role_endpoint,
            )
            agent.equip(turn)
            return agent

        reviewer_for(gateway, controller)
        debugger = Debugger(
            agent=agent_id,
            profile=profile_id,
            bridge=role_bridge,
            endpoint=role_endpoint,
        )

        async def delegate(jobs: list[dict[str, str]]) -> list[dict[str, str]]:
            assert specialists is not None
            if active is None or active.done():
                raise ValueError("Specialists require an active user request.")
            return await specialists.inspect(jobs)

        rpc_server.delegate = delegate
        # The playtester plays in its own read-only role (joinery.roles).
        playtester = Playtester(
            project,
            emit,
            agent=agent_id,
            profile=profile_id,
            bridge=lambda agent: role_bridge(agent, "playtester"),
            endpoint=lambda: role_endpoint(gateway, "playtester"),
        )
        rpc_server.playtest = playtester.tool
        # The design check's agent pass reads in its own read-only role too.
        designer = Designer(
            project,
            emit,
            agent=agent_id,
            profile=profile_id,
            bridge=lambda agent: role_bridge(agent, "designer"),
            endpoint=lambda: role_endpoint(gateway, "designer"),
        )

        def task_endpoint(lane: ProjectGateway, stop: Any) -> GatewayRPC:
            endpoint = GatewayRPC(lane, stop, role="worker")
            if playtester is not None:
                endpoint.playtest = playtester.tool
            if checks is not None:
                endpoint.checks = checks.tool
            if sounder is not None:
                endpoint.sound_audit = sounder.tool
            return endpoint

        def after_decision(decision: dict[str, Any]) -> None:
            """``[agents] playtest_after_change``: play after an accepted gameplay change,
            the checks' ``[agents] access_check_every`` / ``perf_check_every``, and the
            design check's ``design_check_every``."""
            if checks is not None:
                checks.after_decision(decision)
            if designer is not None:
                designer.after_decision(decision)
            if (
                playtester is None
                or not decision.get("accepted")
                or playtester.busy()
                or not gameplay_change(list(decision.get("changes", [])))
                or not playtest.config(project)["playtest_after_change"]
            ):
                return
            playtester.start(origin="after_change")

        crew = Crew(
            gateway,
            emit_task,
            agent=agent_id,
            profile=profile_id,
            limit=parallel_limit(project),
            bridge=lambda agent: Bridge(agent, role="worker"),
            endpoint=task_endpoint,
            on_stop=policy_stop,
            live=live_edit,
            reviewer=reviewer_for,
            debugger=debugger.diagnose,
            lead_busy=lambda: active is not None and not active.done(),
        )

        async def lead_decide(transaction_id: str, accept: bool) -> dict[str, Any]:
            """The chat's own change as one of a group's ways (Try other ways)."""
            if not controller.review_open:
                raise ValueError("That change is no longer waiting.")
            return await controller.decide(transaction_id, accept)

        variants = Variants(crew, lead_decide)

        def emit_decisions(decisions: list[dict[str, Any]]) -> None:
            for decision in decisions:
                emit(decision)
                if "art_review" in decision:
                    emit(art_review_event(project))
                after_decision(decision)

        async def start_variants(data: dict[str, Any], origin: str) -> dict[str, Any]:
            """``/variants``, Try 3 ways, Try other ways, or the lead's start_variants."""
            assert variants is not None
            count, request, given = parse_command(str(data.get("text", "")))
            if data.get("count"):
                count = int(data["count"])
            directions = data.get("directions") or given
            keep = str(data.get("keep_transaction_id", "") or "")
            if keep and (controller.review_id != keep or not controller.review_open):
                raise ValueError("That change is no longer waiting.")
            if origin == "user" and (active is None or active.done()):
                # Saved chats record the request like any other.
                if "chat_id" in data and bridge is not None:
                    switched = await recorder.switch(str(data["chat_id"]), bridge)
                    if switched is not None:
                        emit(switched)
                _, started = recorder.begin(
                    {
                        "text": f"/variants {count} {request}",
                        "targets": data.get("targets", []),
                    },
                    (),
                )
                if started is not None:
                    emit(started)
            prompt = task_prompt(data) if origin == "user" else task_prompt()
            group = await variants.start(
                request,
                prompt=prompt,
                count=count,
                directions=[str(d) for d in directions],
                targets=[str(t) for t in data.get("paths", [])],
                origin="lead" if origin == "lead" else "user",
                title=str(data.get("title", "")),
                lead_tx=keep,
                lead_summary=str(
                    data.get("keep_summary") or gateway.request_summary or ""
                )
                if keep
                else "",
            )
            return variants.event(group)

        async def lead_start(request: dict[str, Any]) -> dict[str, Any]:
            assert crew is not None
            if active is None or active.done():
                raise ValueError("Tasks start from an active user request.")
            task = await crew.start(
                request["title"],
                request["request"],
                origin="lead",
                prompt=task_prompt(),
                targets=request["targets"],
            )
            return {
                "task_id": task.id,
                "state": task.state,
                "next": "The task works on its own and its change is reviewed "
                "separately. Carry on with your part; check it with task_status.",
            }

        rpc_server.start_task = lead_start
        # "Make it match": the audit's fixes as scripted tasks of the crew (ADR 0045).
        matcher = MatchRun(
            crew,
            project,
            emit,
            lambda argv, root: run_offline(argv, root, 600, readonly=True),
            task_prompt,
        )

        # The sound pass: its sounds as scripted tasks, its report by the
        # sound designer in its own read-only role (joinery.roles).
        sounder = SoundPassRun(
            crew,
            project,
            emit,
            task_prompt,
            agent=agent_id,
            profile=profile_id,
            bridge=lambda agent: role_bridge(agent, "sound-designer"),
            endpoint=lambda: role_endpoint(gateway, "sound-designer"),
        )
        rpc_server.sound_audit = sounder.tool

        # Accessibility and performance (ADR 0049, 0050): the checks' tools and fixes.
        checks = Checks(
            crew,
            project,
            emit,
            task_prompt,
            agent=agent_id,
            profile=profile_id,
            bridge=role_bridge,
            endpoint=lambda role: role_endpoint(gateway, role),
        )
        rpc_server.checks = checks.tool

        async def variants_action(data: dict[str, Any]) -> None:
            """Pick, Mix or Discard all on a variant strip. A refusal goes back to
            that strip, which puts its ways back as they were."""
            assert variants is not None
            group = str(data.get("group_id", ""))
            kind = str(data.get("kind", ""))
            try:
                if kind == "variants_pick":
                    emit_decisions(
                        await variants.pick(group, str(data.get("task_id", "")))
                    )
                elif kind == "variants_mix":
                    await variants.mix(
                        group, str(data.get("note", "")), task_prompt(data)
                    )
                elif kind == "variants_discard":
                    emit_decisions(await variants.discard_all(group))
                else:
                    raise ValueError("Unknown chat message.")
            except (OSError, ValueError) as exc:
                emit(
                    {
                        "kind": "notice",
                        "level": "warn",
                        "text": str(exc),
                        "group_id": group,
                    }
                )

        async def lead_variants(request: dict[str, Any]) -> dict[str, Any]:
            if active is None or active.done():
                raise ValueError("Variants start from an active user request.")
            event = await start_variants(
                {
                    "text": request["request"],
                    "title": request["title"],
                    "count": len(request["directions"]),
                    "directions": request["directions"],
                    "paths": request["targets"],
                },
                "lead",
            )
            return {
                "group_id": event["group_id"],
                "ways": len(event["ways"]),
                "next": "Each way is made by its own agent and shown side by side; "
                "the user picks one. Don't make the request yourself as well.",
            }

        rpc_server.start_variants = lead_variants
        rpc_server.task_status = crew.status
        emit(
            {
                "kind": "connection",
                "state": "connected",
                "agent": agent_id,
                "mode": "reviewed-scene-edits",
                "models": [model.model_dump() for model in bridge.model_choices],
                "parallel": crew.limit,
                "elapsed_ms": connection_timing.report("connected")["total_ms"],
            }
        )
        emit(game_suggestions(project))
        assert crew is not None
        while line := await reader.readline():
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    raise TypeError("Invalid chat message.")
                owned = crew.owner(str(data.get("transaction_id", "")))
                assert variants is not None
                if data.get("kind") == "decision" and owned is not None:
                    try:
                        if owned.group and bool(data.get("accept", False)):
                            # Accepting one way is picking it: the rest go.
                            emit_decisions(await variants.pick(owned.group, owned.id))
                            continue
                        decided = await crew.decide(
                            owned.transaction_id, bool(data.get("accept", False))
                        )
                    except (OSError, ValueError) as exc:
                        # The task's row in the Review tab says why, in place.
                        decided = {
                            "kind": "notice",
                            "level": "warn",
                            "text": str(exc),
                            "task_id": owned.id,
                            "transaction_id": owned.transaction_id,
                        }
                    emit(decided)
                    after_decision(decided)
                elif data.get("kind") == "decision":
                    # Allowed while the agent still writes its last words: once a
                    # change is in review the agent can't change anything more.
                    if not controller.review_open:
                        raise ValueError("No change is waiting for your decision.")
                    decision = await controller.decide(
                        str(data.get("transaction_id", "")),
                        bool(data.get("accept", False)),
                    )
                    emit(decision)
                    if "art_review" in decision:
                        emit(art_review_event(project))
                    after_decision(decision)
                    # The change was one of a group's ways (Try other ways).
                    emit_decisions(
                        await variants.lead_decided(
                            str(data.get("transaction_id", "")),
                            bool(decision.get("accepted")),
                        )
                    )
                elif data.get("kind") == "rollback_decision":
                    emit(gateway.rollback_decision(str(data.get("transaction_id", ""))))
                elif data.get("kind") == "art_review":
                    emit(art_review_event(project))
                elif data.get("kind") == "models_request":
                    if active is not None and not active.done():
                        raise ValueError(
                            "Wait for the current response before refreshing models."
                        )
                    await bridge.refresh_models()
                    emit(
                        {
                            "kind": "models",
                            "models": [
                                model.model_dump() for model in bridge.model_choices
                            ],
                        }
                    )
                    gateway.progress("Available models updated")
                elif data.get("kind") == "art_approval":
                    expected_sha256 = str(data.get("sha256", ""))
                    if not expected_sha256:
                        raise ValueError(
                            "Review the art direction before approving it."
                        )
                    approve(project, getpass.getuser(), expected_sha256)
                    # The sidebar resumes a request the art gate held.
                    emit({"kind": "art_approved", "sha256": expected_sha256})
                elif data.get("kind") == "live_decision":
                    # The preview on screen was decided: its owner hears, and the
                    # scene is free for the next agent.
                    owner = gateway.claims.release_live()
                    if not await crew.live_decided(
                        owner, bool(data.get("accepted", True))
                    ):
                        controller.live_decided()
                elif data.get("kind") == "editor_result":
                    result = data.get("result")
                    if not isinstance(result, dict):
                        raise TypeError("Invalid editor tool result.")
                    request_id = str(data.get("request_id", ""))
                    if request_id in editor_calls:
                        editor_calls[request_id].set_result(result)
                    elif not crew.editor_result(request_id, result):
                        controller.live_result(
                            request_id, bool(result.get("success", False))
                        )
                        bridge.editor_result(request_id, result)
                elif data.get("kind") == "editor_view_result":
                    result = data.get("result")
                    if not isinstance(result, dict):
                        raise TypeError("Invalid editor view result.")
                    waiting = view_calls.get(str(data.get("request_id", "")))
                    if waiting is not None and not waiting.done():
                        waiting.set_result(result)
                elif data.get("kind") == "cancel" and data.get("task_id"):
                    await crew.stop(str(data["task_id"]))
                elif data.get("kind") == "cancel_tasks":
                    await crew.stop_all()
                elif data.get("kind") == "cancel":
                    if data.get("all"):
                        await crew.stop_all()
                    if specialists is not None:
                        await specialists.close()
                    if active is not None and not active.done():
                        with contextlib.suppress(Exception):
                            await bridge.cancel()
                        active.cancel()
                        await asyncio.gather(active, return_exceptions=True)
                        emit(Done(ok=False).model_dump())
                    await controller.cancel()
                elif data.get("kind") == "playtest_start":
                    playtester.start(str(data.get("focus", "")))
                elif data.get("kind") == "playtest_cancel":
                    await playtester.stop(str(data.get("playtest_id", "")))
                elif data.get("kind") == "design_start":
                    assert designer is not None
                    designer.start(deep=bool(data.get("deep", True)))
                elif data.get("kind") == "design_cancel":
                    assert designer is not None
                    await designer.stop(str(data.get("design_id", "")))
                elif data.get("kind") == "task_start":
                    text = str(data.get("text", "")).strip()
                    if not text:
                        raise ValueError("Write what the task should do.")
                    await crew.start(
                        chats.title_from(text),
                        text,
                        origin="user",
                        prompt=task_prompt(data),
                    )
                elif data.get("kind") == "match_fix":
                    assert matcher is not None
                    matcher.start(
                        [str(a) for a in data.get("areas") or []],
                        [str(f) for f in data.get("findings") or []],
                    )
                elif data.get("kind") == "mixer_save":
                    # The Mixer tab's Save mix: a reviewed change of the bus layout
                    # and the music tracks' layer mix (bridge/mixer_save.py).
                    await mixer_save.start(crew, project, data, task_prompt())
                elif data.get("kind") == "match_cancel":
                    assert matcher is not None
                    await matcher.stop()
                elif data.get("kind") == "check_cancel":
                    assert checks is not None
                    await checks.cancel(data)
                elif checks is not None and checks.handle(data):
                    pass
                elif data.get("kind") == "variants_start":
                    # /variants N <request>, Try 3 ways, or Try other ways on a change.
                    await start_variants(data, "user")
                elif str(data.get("kind", "")).startswith("variants_"):
                    await variants_action(data)
                elif data.get("kind") == "sound_pass_start":
                    assert sounder is not None
                    sounder.start(
                        str(data.get("focus", "") or ""),
                        str(data.get("scene", "") or ""),
                    )
                elif data.get("kind") == "sound_pass_cancel":
                    assert sounder is not None
                    await sounder.stop()
                elif data.get("kind") == "debug_start":
                    # /debug, or Debug on the offer after a new error in Output.
                    await crew.debug(debug_request(data), task_prompt(data))
                elif data.get("kind") == "prompt":
                    if active is not None and not active.done():
                        emit(
                            Message(
                                text="Codex is still responding.", level="warn"
                            ).model_dump()
                        )
                    elif str(data.get("text", "")).strip():
                        rpc_server.stop_reason = ""
                        active = asyncio.create_task(turn(data))
                else:
                    raise ValueError("Unknown chat message.")
            except (TypeError, ValueError, OSError, RuntimeError, TimeoutError) as exc:
                emit(Message(text=str(exc), level="warn").model_dump())
        return 0
    except (
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        TimeoutError,
    ) as exc:
        emit(
            {
                "kind": "connection",
                "state": "error",
                "agent": agent_id,
                "message": str(exc),
            }
        )
        return 1
    finally:
        if matcher is not None:
            await matcher.stop()
        if checks is not None:
            await checks.close()
        if sounder is not None:
            await sounder.stop()
        if crew is not None:
            await crew.close()
        if playtester is not None:
            await playtester.close()
        if designer is not None:
            await designer.close()
        if active is not None and not active.done():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        if controller.compaction is not None:
            controller.compaction.cancel()
            await asyncio.gather(controller.compaction, return_exceptions=True)
        if bridge is not None:
            await bridge.close()
        if specialists is not None:
            await specialists.close()
        await rpc_server.close()
        transport.close()


def run(project: Path, agent_id: str, profile_id: str = "") -> int:
    return asyncio.run(serve(project.resolve(), agent_id, profile_id))
