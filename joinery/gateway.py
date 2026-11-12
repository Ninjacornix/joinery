"""One project tool gateway: real discovery, shared policy and staged mutations."""

from __future__ import annotations

import asyncio
import difflib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from mcp.types import CallToolResult, TextContent

from joinery import metrics, references, sound_kit, staged_assets, submission
from joinery import parts as part_targets
from joinery import tools as tool_modules
from joinery.art_approval import approved, review
from joinery.authoring import run_offline
from joinery.catalog import blocked_tools, load_catalog, load_project_tools
from joinery.claims import (
    FEATURES,
    MAIN,
    MAIN_TITLE,
    PathClaims,
    WorkerSlots,
    features_refusal,
    project_paths,
    refusal,
)
from joinery.lockfile import asset_files
from joinery.locking import record_custom_outputs
from joinery.mcp_runtime import (
    CHANGE_FOLDERS,
    MCPConnection,
    PolicyStop,
    godot_relative,
    names_changes,
    rewrite_root,
    validate_arguments,
)
from joinery.mcp_runtime import map_paths as _map_paths
from joinery.read_access import read_only
from joinery.rebase import Rebase, rebase, refresh_review
from joinery.review import Render, plan, render
from joinery.server_install import configured
from joinery.stage_watch import StageWatch
from joinery.submission import is_reference
from joinery.text_patch import apply_patch
from joinery.timing import TurnTiming
from joinery.transactions import (
    DIRECTORY,
    FileTransaction,
    ProjectLease,
    _sha256,
    copy_file,
    inventory,
)
from joinery.turn import Problem, scene_route
from joinery.workers import (
    blender_worker,
    godot_runtime_script,
    godot_worker,
    inkscape_worker,
    supercollider_worker,
)

if TYPE_CHECKING:
    from joinery.turn import TurnController

#: Native document operations: each saves, exports and records a recipe, and its
#: result is imported into Godot before the next call can use it. Each names every
#: app it runs, so a parallel task wanting one of them waits its turn (WorkerSlots).
OP_APPS: dict[str, tuple[str, ...]] = {
    # The call's own "tool" argument names the app.
    "operate_document": (),
    "transform_parts": ("blender",),
    "operate_graph": ("material-maker",),
    "operate_vector": ("inkscape",),
    "set_material_value": ("blender",),
    "adjust_map": ("blender",),
    "bake_height_to_normal": ("blender",),
    # Plus its editor: Krita, or Material Maker.
    "make_map_editable": ("blender",),
    "paint_map": ("blender", "krita"),
    "replace_map": ("blender",),
    "split_material": ("blender",),
    "edit_bone": ("blender",),
    "edit_shape_key": ("blender",),
    # Whole-model operations (joinery match) and Krita paintings of their own.
    "apply_scale": ("blender",),
    "set_pivot": ("blender",),
    "reduce_triangles": ("blender",),
    "set_texel_density": ("blender",),
    # Several of the Blender operations above on one model, in one Blender run.
    "blender_batch": ("blender",),
    "edit_image": ("krita",),
    # Parametric kits run Blender. (scatter only writes a scene file.)
    "kit_make": ("blender",),
    "kit_batch": ("blender",),
    "kit_edit": ("blender",),
    # Animations.
    "make_cycle": ("blender",),
    "edit_action": ("blender",),
    "animation_pass": ("blender",),
    # Adaptive music: every stem of a track through FluidSynth and Surge XT.
    "compose_track": ("surge-xt",),
    "edit_track": ("surge-xt",),
    # The sound kit: SuperCollider or Surge XT, by the kind of sound.
    "make_sound": (),
}
NATIVE_OPS = frozenset(OP_APPS)
#: Key prefix of a read-only agent's connection on the stage, beside the staged ones.
VIEW = "view:"


def op_apps(tool: str, arguments: dict[str, Any]) -> tuple[str, ...]:
    """Every app a native operation runs, in a stable order."""
    apps = list(OP_APPS.get(tool, ()))
    if tool == "operate_document":
        apps.append(str(arguments.get("tool") or ""))
    elif tool == "make_map_editable":
        apps.append(str(arguments.get("editor") or "krita"))
    elif tool == "make_sound":
        apps.append(sound_kit.app_for(arguments))
    return tuple(dict.fromkeys(app for app in apps if app))


#: Files whose line-by-line diff the review's Details show.
TEXT_SUFFIXES = frozenset(
    {".gd", ".tscn", ".tres", ".json", ".toml", ".gdshader", ".md"}
)
LIVE_SCENE = (
    "This scene is on screen or has unsaved edits; change it live with edit_scene. "
    "edit_scene's instantiate_scene places a model made in this change too (pass "
    "replace to swap a placeholder), and it is reviewed with the model as one change."
)
#: A task tried to stage the scene on screen while it can't change it live.
LANE_LIVE_SCENE = (
    "This scene is on screen or has unsaved edits, and the main request or another "
    "task is changing it live now. Change a scene that isn't open, or wait."
)
ART_GATE_MESSAGE = (
    "Before Journeyman makes anything, agree how your game should look. "
    "Review the look, then approve these rules."
)


def _upstream_presentation(server: str) -> tuple[frozenset[str], dict[str, str]]:
    """The tools policy blocks on `server`, and the notes its tool module adds.

    Discovery leaves blocked tools out, so no agent is offered a tool that the
    shared policy would deny (calls are still judged by the hook and runtime).
    """
    catalog = load_catalog()
    notes: dict[str, str] = {}
    for entry in catalog.tool:
        if (
            entry.mcp
            and entry.mcp.server == server
            and entry.id in tool_modules.REGISTRY
        ):
            notes.update(tool_modules.get(entry.id).mcp_notes)
    return frozenset(blocked_tools(catalog, server)), notes


def _with_note(tool: dict[str, Any], note: str) -> dict[str, Any]:
    if note:
        tool["description"] = (
            str(tool.get("description", "")).rstrip() + "\n\n" + note
        ).strip()
    return tool


def _reference_only(arguments: Any) -> bool:
    """Every project asset path in the arguments is a reference board, and one is.

    Lets a creative tool make a reference board before the bible is approved.
    The submit checks still refuse any other asset change without approval, so this
    only decides whether the call may start.
    """
    paths: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            text = value.replace("\\", "/").removeprefix("res://")
            if text.startswith(("art/", "audio/")):
                paths.append(text)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list | tuple):
            for item in value:
                collect(item)

    collect(arguments)
    return bool(paths) and all(is_reference(p) for p in paths)


def result_text(result: CallToolResult) -> str:
    return "\n".join(
        part.text for part in result.content if isinstance(part, TextContent)
    )


class ProjectGateway:
    """The project's tools for one turn's agent.

    The chat's own gateway owns the project: the lease, recovery and the app
    connections. A parallel task works through a *lane* of it (:meth:`lane`):
    the same project and connections, but its own staged change, staged apps,
    turn and lock, so several changes can be staged at once (ADR 0036).
    """

    def __init__(self, project: Path) -> None:
        self.project = project.resolve()
        self.specs, self.unavailable = configured(self.project)
        self._lease = ProjectLease(self.project)
        try:
            for journal in (self.project / ".joinery/transactions").glob(
                "*/journal.json"
            ):
                previous = FileTransaction.load(self.project, journal.parent.name)
                if previous.state == "committing":
                    previous.recover()
                elif previous.state in {"staging", "review"}:
                    previous.reject()
        except BaseException:
            self._lease.close()
            raise
        self.connections: dict[str, MCPConnection] = {}
        self.edit_scene: (
            Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None
        #: Capture the editor's viewport as the user sees it (read-only); set by the chat host.
        self.view_editor: Callable[[], Awaitable[dict[str, Any]]] | None = None
        self._discovery_cache: tuple[float, dict[str, Any]] | None = None
        self._discovery_lock = asyncio.Lock()
        #: One connect at a time per app: lanes share the project's connections.
        self._connecting: dict[str, asyncio.Lock] = {}
        #: Which task changes which file, shared by every lane of this project.
        self.claims = PathClaims(self.project)
        self.slots = WorkerSlots()
        self.task_id = MAIN
        self.title = MAIN_TITLE
        self._root = self
        #: The parallel tasks' lanes, by task id (on the chat's own gateway).
        self.lanes: dict[str, ProjectGateway] = {}
        self.claims.register(MAIN, MAIN_TITLE)
        self.editor_context: dict[str, str] = {}
        self._lane_state()

    def _lane_state(self) -> None:
        """What each turn's agent has for itself."""
        self.staged: dict[str, MCPConnection] = {}
        self.transaction: FileTransaction | None = None
        self._last_accepted: FileTransaction | None = None
        #: The turn this gateway serves; it decides when the project may change.
        self.turn: TurnController | None = None
        self.request_summary = ""
        #: The prompt's selection targets; part targets scope typed part edits.
        self.target_context: tuple[dict[str, str], ...] = ()
        #: The prompt's attached images (a sketch to build a blockout from).
        self.attachments: tuple[str, ...] = ()
        #: Joinery's own review facts and media for a staged change, by transaction id.
        self.review_extras: dict[str, dict[str, Any]] = {}
        #: Set when a call needed an approved art bible; the turn's end reports it.
        self.art_gate: dict[str, Any] | None = None
        self.runtime_owned = False
        self.runtime_config: str | None = None
        self.runtime_injected: str | None = None
        self.runtime_root_script = False
        self._call_lock = asyncio.Lock()
        self.progress: Callable[[str], None] = lambda _text: None
        self.timing: TurnTiming | None = None
        #: (transaction id, stage inventory) right after a clean Godot import.
        self._imported: tuple[str, dict[str, str]] | None = None
        #: (transaction id, review media, renders still to run) from open_review.
        self._media_jobs: tuple[str, list[dict[str, Any]], list[Render]] = ("", [], [])
        self._media_task: asyncio.Task[None] | None = None
        #: The accepted-change count (claims.generation) the stage last followed.
        self._synced = 0
        #: While others work: the stage's stat snapshot and the files it changed.
        self._watch: StageWatch | None = None
        self._changed: set[str] = set()

    @property
    def is_lane(self) -> bool:
        return self._root is not self

    def lane(self, task_id: str, title: str) -> ProjectGateway:
        """A gateway for a parallel task: shared project and apps, its own change."""
        root = self._root
        if task_id in root.lanes or task_id == MAIN:
            raise ValueError("That task already has a gateway.")
        child = ProjectGateway.__new__(ProjectGateway)
        for name in (
            "project",
            "specs",
            "unavailable",
            "_lease",
            "connections",
            "view_editor",
            "_discovery_cache",
            "_discovery_lock",
            "_connecting",
            "claims",
            "slots",
            "lanes",
        ):
            setattr(child, name, getattr(root, name))
        child._root = root
        child.task_id = task_id
        child.title = title
        child.edit_scene = None
        child.editor_context = dict(root.editor_context)
        child._lane_state()
        child._synced = root.claims.generation
        # From now on staged files are claimed as they are written; record
        # what the others staged before there was anyone to clash with.
        for other in [root, *root.lanes.values()]:
            other.claim_stage(strict=False)
        root.lanes[task_id] = child
        root.claims.register(task_id, title)
        return child

    def lead_idle(self) -> bool:
        """The chat's own request has nothing running and nothing on screen."""
        turn = self._root.turn
        return turn is None or turn.idle()

    def may_go_live(self) -> bool:
        """This agent may change the scene on screen live now (ADR 0036)."""
        if not self.is_lane:
            return True
        return (
            self.edit_scene is not None
            and self.lead_idle()
            and self.claims.live in {"", self.task_id}
        )

    @property
    def call_lock(self) -> asyncio.Lock:
        """Held for every tool call; a submit holds it while the change is checked."""
        return self._call_lock

    async def connection(self, server: str) -> MCPConnection:
        """Start just the requested server; unrelated creative apps stay idle."""
        connection = self.connections.get(server)
        if connection is None:
            spec = next((spec for spec in self.specs if spec.name == server), None)
            if spec is None:
                raise ValueError(f"{server} is not installed and connected.")
            spec = spec.model_copy(deep=True)
            if spec.source == "first-party":
                spec.worker_scope = uuid.uuid4().hex
                spec.env["JOINERY_WORKER_SCOPE"] = spec.worker_scope
            connection = MCPConnection(spec, self.project)
            self.connections[server] = connection
        lock = self._connecting.setdefault(server, asyncio.Lock())
        with self.span("tool_connection", server):
            async with lock:
                await connection.connect()
        return connection

    async def discover(self) -> dict[str, Any]:
        """The project's tools. Lanes share the chat's connections and cache, but
        say "Connecting project tools…" on their own task's card."""
        root = self._root
        async with root._discovery_lock:
            cache = root._discovery_cache
            if (
                cache
                and time.monotonic() - cache[0] < 15
                and all(
                    connection.readiness.connected
                    for connection in self.connections.values()
                )
            ):
                return cache[1]
            with self.span("discovery"):
                result = await root._discover(self.progress)
            if all(s.get("connected") for s in result["servers"]):
                root._discovery_cache = (time.monotonic(), result)
            return result

    async def _discover(self, progress: Callable[[str], None]) -> dict[str, Any]:
        progress("Connecting project tools…")
        tools = []
        states = [state.model_dump() for state in self.unavailable]
        limit = asyncio.Semaphore(3)

        async def start(spec: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            async with limit:
                connection = self.connections.get(spec.name)
                if connection is None:
                    if spec.source == "first-party":
                        spec.worker_scope = uuid.uuid4().hex
                        spec.env["JOINERY_WORKER_SCOPE"] = spec.worker_scope
                    connection = MCPConnection(spec, self.project)
                    self.connections[spec.name] = connection
                try:
                    await connection.connect()
                    blocked, notes = _upstream_presentation(spec.name)
                    discovered = [
                        _with_note(
                            {
                                "server": spec.name,
                                **tool.model_dump(by_alias=True, exclude_none=True),
                            },
                            notes.get(tool.name, ""),
                        )
                        for tool in connection.tools.values()
                        if tool.name not in blocked
                    ]
                    return discovered, connection.readiness.model_dump()
                except PolicyStop:
                    raise
                except (OSError, RuntimeError, ValueError, TimeoutError):
                    return [], connection.readiness.model_dump()

        tasks = [asyncio.create_task(start(spec)) for spec in self.specs]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for discovered, state in results:
            tools += discovered
            states.append(state)
        return {"servers": states, "tools": tools}

    async def call(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        view: bool = False,
    ) -> CallToolResult:
        """Run one tool call. ``view`` serves an agent that only reads (a reviewer,
        a debugger, a specialist): an audited read, answered from a read-only view
        of this gateway's staged change while there is one (ADR 0037, 0041)."""
        async with self._call_lock:
            with self.span(
                "tools",
                f"execute_tool {server}.{tool}",
                {
                    metrics.OPERATION: "execute_tool",
                    metrics.TOOL_NAME: f"{server}.{tool}",
                    metrics.TASK: self.task_id,
                },
            ) as attrs:
                result = await self._call(server, tool, arguments, view=view)
                attrs.update(metrics.worker_split(result.meta))
                if result.is_error:
                    attrs[metrics.ERROR_TYPE] = "tool_error"
                return result

    def _project_arguments(
        self, server: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """The call's arguments in this project's own terms.

        A path into this gateway's own stage names the project file it stages; a
        path into any change folder is refused, so no agent reads another task's
        stage. The pinned Godot MCP joins its file arguments onto the project
        folder itself, so a ``res://`` there is made project-relative.
        """
        tx = self.transaction
        if tx is not None:
            arguments = _map_paths(arguments, tx.stage, self.project)
        if names_changes(arguments):
            raise ValueError(CHANGE_FOLDERS)
        return godot_relative(arguments) if server == "godot" else arguments

    def _patched(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """A Godot ``write_file`` given ``patch`` pairs: the staged file with them
        applied, sent on as ``content`` (joinery.text_patch)."""
        tx = self.transaction
        assert tx is not None
        if "content" in arguments:
            raise ValueError("Give write_file either content or patch, not both.")
        relative = str(arguments.get("filePath", "")).removeprefix("res://")
        stage = tx.stage.resolve()
        target = (stage / relative).resolve()
        if not relative or not target.is_relative_to(stage) or not target.is_file():
            raise ValueError(
                "patch changes a file the project already has; write a new file with content."
            )
        content = apply_patch(target.read_text(encoding="utf-8"), arguments["patch"])
        validate_arguments(content)
        return {
            **{k: v for k, v in arguments.items() if k != "patch"},
            "content": content,
        }

    def _view(self, server: str) -> MCPConnection | None:
        """A read-only connection on this gateway's stage; None for an app whose
        worker can't mount it read-only (its reads use the staged connection)."""
        tx = self.transaction
        assert tx is not None
        key = VIEW + server
        view = self.staged.get(key)
        if view is None:
            if server == "godot":
                spec = godot_worker(tx.stage, readonly=True)
            elif server == "inkscape":
                spec = inkscape_worker(tx.stage, readonly=True)
            else:
                return None
            view = MCPConnection(spec, self.project)
            self.staged[key] = view
        return view

    async def _call(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        view: bool = False,
    ) -> CallToolResult:
        custom = next(
            (
                item
                for item in load_project_tools(self.project / "joinery.toml").custom
                if item.server == server
            ),
            None,
        )
        if custom is None:
            validate_arguments(arguments)
        readonly = read_only(server, tool)
        if view and not readonly:
            raise ValueError(
                "This agent only reads: call one of the project tools' read-only tools."
            )
        arguments = self._project_arguments(server, arguments)
        if not readonly and self.turn is not None:
            self.turn.admit_mutation()
        scene = str(arguments.get("scenePath", "") or "")
        scene_server = server == "godot"
        if server == "joinery-blender-ops" and tool == "scatter":
            # Scatter writes a saved scene, like the staged Godot tools.
            scene, scene_server = str(arguments.get("scene", "") or ""), True
        if (
            not readonly
            and scene_server
            and scene
            and (
                scene_route(scene, self.editor_context) == "live"
                or (self.turn is not None and self.turn.edited_live(scene))
            )
        ):
            raise ValueError(LIVE_SCENE if self.may_go_live() else LANE_LIVE_SCENE)
        named = project_paths(arguments, self.project) if not readonly else set()
        if not readonly and self.claims.contended():
            path, title = self.claims.first_conflict(named, self.task_id)
            if path:
                raise ValueError(refusal(path, title))
        connection = await self.connection(server)
        if server == "godot" and tool.startswith("game_") and not self.runtime_owned:
            raise ValueError(
                "This call needs a project-owned running-game session; it cannot target an arbitrary global port."
            )
        if (
            not readonly
            and server not in {"godot", "joinery-registry"}
            and not approved(self.project)
            and not _reference_only(arguments)
        ):
            # Not an error the agent should work around: the user decides. The
            # sidebar shows the art gate card when the turn finishes.
            gate = self.art_gate_event()
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=gate["message"] + " Stop here and tell the user.",
                    )
                ],
                structured_content={k: v for k, v in gate.items() if k != "kind"},
                is_error=True,
            )
        if not readonly:
            self.staging()
        if self.transaction is not None and self.transaction.state == "staging":
            if not readonly:
                # The Review tab names the app that made each change.
                self.transaction.note_tool(
                    str(arguments.get("tool") or server.removeprefix("joinery-"))
                    if tool in NATIVE_OPS
                    else server.removeprefix("joinery-")
                )
            staged = self._view(server) if view else None
            if staged is None:
                staged = self.staged.get(server)
            if staged is None:
                spec = connection.spec.model_copy(deep=True)
                if spec.source != "first-party":
                    spec.cwd = str(self.transaction.stage)
                else:
                    spec.worker_scope = uuid.uuid4().hex
                    spec.env["JOINERY_WORKER_SCOPE"] = spec.worker_scope
                if server == "godot":
                    spec = godot_worker(self.transaction.stage)
                elif server == "blender":
                    spec = blender_worker(self.transaction.stage)
                elif server == "claude-collider":
                    spec = supercollider_worker(self.transaction.stage)
                elif server == "inkscape":
                    spec = inkscape_worker(self.transaction.stage)
                else:
                    spec.args = _map_paths(
                        spec.args, self.project, self.transaction.stage
                    )
                    spec.env = _map_paths(
                        spec.env, self.project, self.transaction.stage
                    )
                staged = MCPConnection(spec, self.project)
                self.staged[server] = staged
            connection = staged
            if server == "godot" and tool == "write_file" and "patch" in arguments:
                arguments = self._patched(arguments)
            arguments = _map_paths(arguments, self.project, self.transaction.stage)
            if not readonly and self.claims.contended():
                self._watch_stage()
            if server == "godot" and not readonly:
                # The stage starts without Godot's import cache: a staged scene
                # that instances a model (new in this change or not) needs it.
                await self._import_for(staged_assets.loaded_paths(arguments, bare=True))
        if server == "godot" and tool == "run_project":
            self._prepare_runtime()
        definition = connection.tools.get(tool)
        # First connect establishes schemas before the staged call.
        if definition is None:
            await connection.connect()
        before_custom = (
            inventory(self.transaction.stage)
            if custom is not None and self.transaction is not None
            else {}
        )
        before_assets = (
            asset_files(self.transaction.stage)
            if custom is not None and self.transaction is not None
            else set()
        )
        if (
            server.startswith("joinery-")
            and not readonly
            and self.transaction is not None
        ):
            # The tool server of this stage reads the prompt's part targets.
            part_targets.write_targets(self.transaction.stage, self.target_context)
        apps = op_apps(tool, arguments) if tool in NATIVE_OPS else ()
        if apps:
            names = [self.slots.app_name(app) for app in apps]
            self.progress("Working in " + " and ".join(names) + "…")
        async with self.slots.hold(
            apps,
            lambda app: self.progress(f"Waiting for {app}: another task is using it…"),
        ):
            result = await connection.call(
                tool,
                arguments,
                timeout=900
                if server == "blender" or server.startswith("joinery-")
                else 60,
            )
        if self.transaction is not None and any(
            connection is self.staged.get(key) for key in (server, VIEW + server)
        ):
            # The agent asked about project paths; answer in them, not the stage's.
            stage = self.transaction.stage
            for staged_root in dict.fromkeys((str(stage), str(stage.resolve()))):
                result = rewrite_root(result, staged_root, self.project)
        if result.is_error and result_text(result).startswith("Rule Zero STOP:"):
            raise PolicyStop(result_text(result))
        if not readonly:
            self._claim_features()
        if not readonly and self.claims.contended():
            self._claim_written(named)
        if (
            server.startswith("joinery-")
            and tool in NATIVE_OPS
            and not result.is_error
            and self.transaction is not None
        ):
            # Subsequent saved-scene operations can immediately reference the
            # new portable resources; import must precede their first load.
            await self.import_stage(self.transaction)
        if custom is not None and self.transaction is not None and not result.is_error:
            root = self.transaction.stage
            after = inventory(root)
            outputs = asset_files(root) | before_assets
            changed = [
                name for name in outputs if before_custom.get(name) != after.get(name)
            ]
            record_custom_outputs(root, custom.id, changed)
        if server == "godot" and tool == "run_project" and not result.is_error:
            deadline = asyncio.get_running_loop().time() + 20
            while asyncio.get_running_loop().time() < deadline:
                state = await connection.call("game_get_scene_tree", {}, timeout=5)
                if not state.is_error:
                    self.runtime_owned = True
                    break
                await asyncio.sleep(0.25)
            if not self.runtime_owned:
                raise RuntimeError(
                    "The owned game process did not connect its runtime inspector. No persistent scene edit was made."
                )
        elif server == "godot" and tool == "stop_project" and not result.is_error:
            self.runtime_owned = False
        return result

    def staging(self) -> FileTransaction:
        """This turn's staged change, begun on its first write and brought up to date
        with changes accepted since; refused while a change waits for the user."""
        if self.transaction is None:
            self.transaction = FileTransaction(self.project)
            self.transaction.begin(self.request_summary[:160] or "Project changes")
            self._synced = self.claims.generation
        elif self._synced < self.claims.generation:
            # Another task's change was accepted: follow it before writing.
            self.follow_project(take_theirs=False)
        if self.transaction.state != "staging":
            raise ValueError("Accept or reject the pending transaction first.")
        return self.transaction

    def claim_stage(self, *, strict: bool) -> None:
        """Claim every file this turn's stage changed, hashing the whole stage.

        Used when another agent joins, and at submit; between them each call's
        writes are claimed from a stat snapshot (:meth:`_claim_written`). With
        ``strict`` a file another task already holds is put back as the project
        has it and the call is refused, in words the agent can act on.
        """
        refused = self._claim_full(restore=strict)
        if refused and strict:
            raise ValueError(self._refused(refused))

    def _claim_full(self, *, restore: bool) -> dict[str, str]:
        """Claim the stage's changes from a full hash; returns the files another
        task holds (put back as the project has them with ``restore``)."""
        tx = self.transaction
        if tx is None or tx.state not in {"staging", "review"}:
            return {}
        with self.span("claims"):
            try:
                changed = {
                    c["path"]
                    for c in (tx.diff() if tx.state == "staging" else tx.changes)
                    if DIRECTORY not in {c["before"], c["after"]}
                }
            except (OSError, ValueError):
                return {}
            if tx.state == "staging":
                # From here on the snapshot tells what each call wrote.
                self._watch = StageWatch(tx.stage, tx.id)
            self._changed = changed
            return self._claim(tx, restore=restore)

    def _claim_features(self) -> None:
        """A stage that changed game features holds the project's one claim on them.

        Another agent, or ``joinery feature`` in another process, holding it
        means the call is refused and the stage's game manifest put back.
        """
        tx = self.transaction
        if tx is None or tx.state != "staging" or not (tx.stage / FEATURES).is_file():
            return
        holder = self.claims.claim_features(self.task_id)
        if not holder:
            return
        (tx.stage / FEATURES).unlink(missing_ok=True)
        raise ValueError(
            features_refusal(holder)
            + " The game manifest was left as the project has it; the rest of the "
            "call stands."
        )

    def _watch_stage(self) -> None:
        """Before a call while others work: know the stage as it is now."""
        tx = self.transaction
        if tx is None or tx.state != "staging":
            return
        if self._watch is None or self._watch.id != tx.id:
            with self.span("claims"):
                self._watch = StageWatch(tx.stage, tx.id)

    def _claim_written(self, named: set[str]) -> None:
        """After a call while others work: claim what it wrote, hashing only that.

        The files the call named and those in folders whose listing changed are
        hashed and compared with the stage's start; nothing else is read.
        """
        tx, watch = self.transaction, self._watch
        if tx is None or tx.state != "staging":
            return
        if watch is None or watch.id != tx.id:
            self.claim_stage(strict=True)
            return
        with self.span("claims"):
            for path in watch.changes(named):
                before = tx.base.get(path, "")
                if before == DIRECTORY:
                    continue
                target = tx.stage / path
                try:
                    now = _sha256(target) if target.is_file() else ""
                except OSError:
                    continue
                if now != before:
                    self._changed.add(path)
                else:
                    self._changed.discard(path)
            refused = self._claim(tx, restore=True)
        if refused:
            raise ValueError(self._refused(refused))

    def _claim(self, tx: FileTransaction, *, restore: bool) -> dict[str, str]:
        """Claim ``self._changed``. Returns the files another task holds, with its
        title; with ``restore`` they are put back as the project has them."""
        refused = self.claims.claim(self.task_id, self._changed)
        if not refused or not restore or tx.state != "staging":
            return refused
        for path in refused:
            original = self.project / path
            target = tx.stage / path
            if original.is_file():
                copy_file(original, target)
            else:
                target.unlink(missing_ok=True)
            self._changed.discard(path)
        if self._watch is not None:
            self._watch.changes(set(refused))
        return refused

    @staticmethod
    def _refused(refused: dict[str, str]) -> str:
        path = min(refused)
        return (
            refusal(path, refused[path])
            + " That file was left as the project has it; the rest of the call stands."
        )

    def follow_project(self, *, take_theirs: bool) -> Rebase:
        """Bring the stage up to date with changes accepted since it was made."""
        self._synced = self.claims.generation
        tx = self.transaction
        if tx is None or tx.state != "staging":
            return Rebase()
        return rebase(tx, take_theirs=take_theirs)

    def _prepare_runtime(self) -> None:
        if self.runtime_config is not None or self.transaction is None:
            return
        root = self.transaction.stage
        script = godot_runtime_script()
        root_script = root / "mcp_interaction_server.gd"
        if root_script.is_file() and root_script.read_bytes() != script.read_bytes():
            raise ValueError(
                "A different mcp_interaction_server.gd already exists. The runtime inspector will not replace that source."
            )
        self.runtime_root_script = not root_script.exists()
        self.runtime_config = (root / "project.godot").read_text()
        # The pinned upstream injects its own autoload inside this isolated
        # worker. Snapshot it here; do not add a second listener on its port.

    def _strip_runtime(self) -> None:
        if self.transaction is None or self.runtime_config is None:
            return
        import re

        config = self.transaction.stage / "project.godot"
        text = config.read_text()
        if "McpInteractionServer=" not in self.runtime_config:
            text = re.sub(
                r'^McpInteractionServer="\*res://mcp_interaction_server\.gd"\n?',
                "",
                text,
                flags=re.MULTILINE,
            )
            if "[autoload]" not in self.runtime_config:
                text = re.sub(r"\[autoload\]\s*(?=\[|$)", "", text)
        config.write_text(
            self.runtime_config if text.strip() == self.runtime_config.strip() else text
        )
        root_script = self.transaction.stage / "mcp_interaction_server.gd"
        pinned = godot_runtime_script()
        if self.runtime_root_script:
            if (
                root_script.is_file()
                and root_script.read_bytes() == pinned.read_bytes()
            ):
                root_script.unlink()
            uid = root_script.with_suffix(".gd.uid")
            if not (self.transaction.original / uid.name).exists():
                uid.unlink(missing_ok=True)
        elif not root_script.exists():
            original = self.transaction.original / root_script.name
            if original.is_file():
                root_script.write_bytes(original.read_bytes())
        self.runtime_root_script = False
        self.runtime_config = None
        self.runtime_injected = None

    def art_gate_event(self) -> dict[str, Any]:
        """The art gate result: the sidebar asks the user to approve, then resumes."""
        self.art_gate = {
            "kind": "art_gate",
            "needs_art_approval": True,
            "resume": self.request_summary,
            "message": ART_GATE_MESSAGE,
        }
        return self.art_gate

    async def submit_checks(self, live_scenes: set[str]) -> submission.Validation:
        """Check the staged change as it stands now (``submission.validate``).

        Staged apps are closed first, so no background app writes while the
        change is checked; they start again if the agent keeps working.
        """
        tx = self.transaction
        self.progress("Checking the change…")
        for connection in self.staged.values():
            await connection.close()
        self.staged.clear()
        self.runtime_owned = False
        self._strip_runtime()
        if tx is None or tx.state != "staging":
            return submission.Validation()
        moved: list[Problem] = []
        if self.claims.contended():
            # The full hash: catches a file rewritten in place that no call named.
            refused = self._claim_full(restore=True)
            if refused:
                path = min(refused)
                moved.append(
                    Problem(
                        "conflict",
                        refusal(path, refused[path])
                        + " Your stage has that file as the project has it again.",
                        sorted(refused),
                    )
                )
        if self._synced < self.claims.generation:
            conflicts = self.follow_project(take_theirs=True).conflicts
            if conflicts:
                moved.append(
                    Problem(
                        "conflict",
                        "While you worked the user accepted another change to "
                        + ", ".join(conflicts[:5])
                        + ". Your stage now has that version; redo your part on top "
                        "of it, then submit again.",
                        conflicts,
                    )
                )
        with self.span("submit"):
            checked = await submission.validate(self, tx, live_scenes)
        checked.problems[:0] = moved
        return checked

    async def precheck_checks(
        self, live_scenes: set[str]
    ) -> tuple[submission.Validation, list[str]]:
        """The submit's checks on the stage as it is, changing nothing the agent made.

        Unlike :meth:`submit_checks` the staged apps stay open, a running game keeps
        its inspector, no file is put back for another task and the stage does not
        follow changes accepted meanwhile: those are said in the returned notes
        instead. Godot may fill the stage's import cache, as any staged Godot
        call does; a clean import here spares the submit its own.
        """
        tx = self.transaction
        notes: list[str] = []
        if tx is None or tx.state != "staging":
            return submission.Validation(), notes
        moved: list[Problem] = []
        if self.claims.contended():
            changed = {
                c["path"]
                for c in tx.diff()
                if DIRECTORY not in {c["before"], c["after"]}
            }
            held = {
                path: title
                for path in sorted(changed)
                if (title := self.claims.holder(path, self.task_id))
            }
            if held:
                path = min(held)
                moved.append(
                    Problem(
                        "conflict",
                        refusal(path, held[path])
                        + " Submitting puts that file back as the project has it.",
                        sorted(held),
                    )
                )
        if self._synced < self.claims.generation:
            notes.append(
                "The user accepted another change while you worked; submit first "
                "brings your stage up to date with it, which can change what the "
                "checks find."
            )
        if self.runtime_config is not None:
            notes.append(
                "The game is running from the stage; submit stops it first, so its "
                "files in project.godot are not part of your change."
            )
        gate = self.art_gate
        with self.span("precheck"):
            checked = await submission.validate(self, tx, live_scenes)
        # The art gate is the submit's to raise: a precheck only reports it.
        self.art_gate = gate
        checked.problems[:0] = moved
        return checked, notes

    def mark_imported(self, tx: FileTransaction) -> None:
        """Godot just imported ``tx``'s stage cleanly; the next check may skip that."""
        if tx is self.transaction and tx.state == "staging":
            try:
                self._imported = (tx.id, inventory(tx.stage, include_dirs=True))
            except (OSError, ValueError):
                self._imported = None

    def text_diff(self) -> list[str]:
        """The staged change's text files as unified diff lines."""
        tx = self.transaction
        if tx is None:
            return []
        lines: list[str] = []
        for change in tx.diff():
            if Path(change["path"]).suffix not in TEXT_SUFFIXES:
                continue
            old = tx.original / change["path"]
            new = tx.stage / change["path"]
            before = (
                old.read_text(errors="replace").splitlines() if old.is_file() else []
            )
            after = (
                new.read_text(errors="replace").splitlines() if new.is_file() else []
            )
            lines.extend(
                difflib.unified_diff(
                    before,
                    after,
                    fromfile=change["path"],
                    tofile=change["path"],
                    lineterm="",
                )
            )
        return lines

    def open_review(self, summary: str) -> dict[str, Any]:
        """Put the checked stage in review and return its ``file_preview``.

        ``summary`` is the agent's one line naming the change. The before/after
        renders are planned here and run afterwards (:meth:`start_media`); the
        preview already names the files they write, with ``media_pending``.
        """
        tx = self.transaction
        if tx is None:
            raise ValueError("Nothing is staged for review.")
        if summary.strip():
            tx.summary = summary.strip()[:160]
        text_diffs = self.text_diff()
        reviewed = tx.review()
        media, jobs = plan(
            tx, reviewed, [dict(t) for t in self.target_context if t.get("part_kind")]
        )
        self._media_jobs = (tx.id, media, jobs)
        extras = self.review_extras.pop(tx.id, {})
        self.review_extras.clear()
        return {
            "joinery_facts": list(extras.get("facts", [])),
            "kind": "file_preview",
            "diff": "\n".join(text_diffs[:400]),
            "transaction_id": tx.id,
            "summary": tx.summary,
            "changes": reviewed,
            "media": [*extras.get("media", []), *media],
            "media_pending": bool(jobs),
        }

    def start_media(self, emit: Callable[[dict[str, Any]], None]) -> None:
        """Render the review's before and after in the background.

        Emits ``review_media`` with the finished items; a failed render marks its
        item with ``error`` instead of failing the review.
        """
        identifier, media, jobs = self._media_jobs
        self._media_jobs = ("", [], [])
        if not jobs:
            return

        # Taken now: the turn (and its timing) may be over when the renders start.
        span = self.span("review_media")

        async def rendered() -> None:
            with span:
                await render(jobs)
            emit({"kind": "review_media", "transaction_id": identifier, "media": media})

        self._media_task = asyncio.create_task(rendered())

    async def stop_media(self) -> None:
        """Stop renders still running: Blender must never read a stage being moved."""
        task, self._media_task = self._media_task, None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    def span(
        self, phase: str, name: str = "", attributes: dict[str, Any] | None = None
    ) -> AbstractContextManager[dict[str, Any]]:
        """Time a phase of this turn (and record it as a span of the change)."""
        if self.timing is None:
            return nullcontext({})
        return self.timing.span(phase, name, attributes)

    def import_current(self, tx: FileTransaction) -> bool:
        """Godot already imported this exact stage cleanly (after a native export)."""
        if self._imported is None or self._imported[0] != tx.id:
            return False
        return self._imported[1] == inventory(tx.stage, include_dirs=True)

    async def import_stage(self, tx: FileTransaction) -> None:
        """Import the stage in Godot (offline worker), so its files can be loaded.

        The stage starts without Godot's import cache, so a model or texture the
        change made, or one the project had, loads in the stage only after this.
        """
        self.progress("Importing the result into Godot…")
        with self.span("import"):
            imported = await run_offline(
                ["godot", "--headless", "--path", "/work", "--editor", "--import"],
                tx.stage,
                180,
            )
        if (
            not imported.ok
            or "SCRIPT ERROR:" in imported.output
            or "Parse Error:" in imported.output
        ):
            raise ValueError(
                "The native export could not be imported into Godot: "
                + imported.output[-2000:]
            )
        # The review's own import check is skipped while nothing changed since.
        self._imported = (tx.id, inventory(tx.stage, include_dirs=True))

    async def _import_for(self, paths: list[str]) -> None:
        """Import the stage when one of ``paths`` has no current import there."""
        tx = self.transaction
        if tx is None or tx.state != "staging":
            return
        if staged_assets.needs_import(tx.stage, paths):
            await self.import_stage(tx)

    async def staged_files(self, payload: dict[str, Any]) -> dict[str, str]:
        """The files a live scene edit loads that only this turn's change has.

        Returns each one's project path (``res://art/tv.glb``) with the stage's
        own loadable copy (its imported scene, as an absolute path), importing
        the stage first when needed. The editor loads that copy and keeps the
        project path in the scene, so a model made in this change is placed in
        the same change and shows in its review. Raises, in words the agent can
        act on, when a named file exists nowhere or can't be imported.
        """
        tx = self.transaction
        paths = staged_assets.loaded_paths(payload)
        if not paths:
            return {}
        missing = [
            path
            for path in paths
            if not (self.project / path).is_file()
            and (tx is None or not (tx.stage / path).is_file())
        ]
        if missing:
            raise ValueError(staged_assets.MISSING.format(path="res://" + missing[0]))
        if tx is None or tx.state != "staging":
            return {}
        made = staged_assets.staged_only(self.project, tx.stage, paths)
        if not made:
            return {}
        async with self._call_lock:
            if staged_assets.needs_import(tx.stage, made):
                try:
                    await self.import_stage(tx)
                except ValueError:
                    raise ValueError(
                        staged_assets.NOT_IMPORTED.format(path="res://" + made[0])
                    ) from None
        found: dict[str, str] = {}
        for path in made:
            loadable = staged_assets.imported_file(tx.stage, path)
            if loadable is None:
                raise ValueError(
                    staged_assets.NOT_IMPORTED.format(path="res://" + path)
                )
            found["res://" + path] = loadable.resolve().as_posix()
        return found

    async def decide(self, identifier: str, accept: bool) -> dict[str, Any]:
        tx = self.transaction
        if tx is None or tx.id != identifier:
            raise ValueError("This transaction is not pending in the current session.")
        await self.stop_media()
        if accept and self._synced < self.claims.generation:
            # Another change was accepted since this one was staged.
            refresh_review(tx)
        changes = tx.accept() if accept else []
        notice = ""
        self.claims.release(self.task_id)
        if accept:
            self.claims.generation += 1
            self._last_accepted = tx
        if not accept:
            added = references.created(tx.changes)
            tx.reject()
            notice = references.orphan_notice(
                references.users_of(self.project, added), references.DISCARDED
            )
        self.transaction = None
        self.runtime_owned = False
        for connection in self.staged.values():
            await connection.close()
        self.staged.clear()
        event: dict[str, Any] = {
            "kind": "file_decision",
            "transaction_id": identifier,
            "accepted": accept,
            "changes": changes,
        }
        if notice:
            event["message"] = "Change discarded. " + notice
        if accept and tx.timeline.get("state") == "failed":
            # The change is kept either way; say why the Timeline lacks it.
            event["message"] = "Change accepted. " + tx.timeline.get("message", "")
        if (
            accept
            and any(change["path"] == "art/bible.md" for change in changes)
            and (self.project / "art/bible.md").is_file()
            and not approved(self.project)
        ):
            event["message"] = (
                "Art direction draft saved. Review it and click Approve these rules "
                "to allow asset work."
            )
            event["art_review"] = review(self.project)
        return event

    def rollback_decision(self, identifier: str) -> dict[str, Any]:
        """Compensate user acceptance when an accompanying live preview went stale.

        Only trusted editor input can request this; it is absent from model RPC.
        Normal revert conflict checks preserve any newer manual file edits.
        """
        tx = self._last_accepted
        if tx is None or tx.id != identifier:
            raise ValueError(
                "This file decision cannot be rolled back in this session."
            )
        tx.revert()
        self._last_accepted = None
        notice = references.orphan_notice(
            references.users_of(self.project, references.created(tx.changes)),
            references.REVERTED,
        )
        return {
            "kind": "file_decision",
            "transaction_id": identifier,
            "accepted": False,
            "changes": [],
            "message": "The live preview changed before acceptance. File changes were restored; review the scene before retrying."
            + (" " + notice if notice else ""),
        }

    async def cancel(self) -> None:
        await self.stop_media()
        self.runtime_owned = False
        self.runtime_config = None
        self.runtime_injected = None
        for connection in self.staged.values():
            await connection.close()
        self.staged.clear()
        if self.transaction is not None and self.transaction.state in {
            "staging",
            "review",
        }:
            self.transaction.reject()
        self.transaction = None
        self.claims.release(self.task_id)

    async def close(self) -> None:
        await self.stop_media()
        if self.transaction is not None and self.transaction.state in {
            "staging",
            "review",
        }:
            self.transaction.reject()
        if self.is_lane:
            # A task's lane: its own apps and claims go; the project's stay.
            for connection in self.staged.values():
                await connection.close()
            self.staged.clear()
            self.transaction = None
            self.claims.unregister(self.task_id)
            self.lanes.pop(self.task_id, None)
            return
        for lane in list(self.lanes.values()):
            await lane.close()
        for connection in [*self.connections.values(), *self.staged.values()]:
            await connection.close()
        self.connections.clear()
        self.staged.clear()
        self._lease.close()

    def serialise(self, result: CallToolResult) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            json.loads(result.model_dump_json(by_alias=True, exclude_none=True)),
        )
