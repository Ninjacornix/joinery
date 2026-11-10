"""One project tool gateway: real discovery, shared policy and staged mutations."""

from __future__ import annotations

import asyncio
import difflib
import json
import ntpath
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any, cast

from mcp.types import CallToolResult, TextContent

from joinery import parts as part_targets
from joinery import references
from joinery import tools as tool_modules
from joinery.art_approval import approved, review
from joinery.art_approval import fingerprint as bible_fingerprint
from joinery.authoring import run_offline
from joinery.bible_rules import check_rules, load_rules, ui_resources
from joinery.bible_schema import REFERENCE_DIR
from joinery.catalog import blocked_tools, load_catalog, load_project_tools
from joinery.deps import check_deps
from joinery.lockfile import asset_files, check_assets
from joinery.locking import record_custom_outputs
from joinery.mcp_runtime import MCPConnection, PolicyStop, validate_arguments
from joinery.policy import load_denylist
from joinery.read_access import read_only
from joinery.review import evidence
from joinery.server_install import configured, installation
from joinery.timing import TurnTiming
from joinery.transactions import DIRECTORY, FileTransaction, ProjectLease, inventory
from joinery.workers import (
    blender_worker,
    godot_worker,
    inkscape_worker,
    supercollider_worker,
)

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


#: Native document operations: each saves, exports and records a recipe, and its
#: result is imported into Godot before the next call can use it.
NATIVE_OPS = frozenset(
    {
        "operate_document",
        "transform_parts",
        "operate_graph",
        "operate_vector",
        "set_material_value",
        "adjust_map",
        "bake_height_to_normal",
    }
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


def _is_reference(path: str) -> bool:
    """Under art/reference/: the user's own boards, allowed before approval.

    `art/reference/../hero.blend` is not: PurePath does not resolve `..`.
    """
    pure = PurePosixPath(path.replace("\\", "/").removeprefix("res://"))
    return ".." not in pure.parts and pure.is_relative_to(REFERENCE_DIR)


def _reference_only(arguments: Any) -> bool:
    """Every project asset path in the arguments is a reference board, and one is.

    Lets a creative tool make a reference board before the bible is approved.
    `finish` still refuses any other asset change without approval, so this
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
    return bool(paths) and all(_is_reference(p) for p in paths)


def _approved_in(project: Path, stage: Path) -> bool:
    """The project's bible is approved and the stage carries that same bible."""
    try:
        return approved(project) and bible_fingerprint(project) == bible_fingerprint(
            stage
        )
    except ValueError:
        return False


def _ui_changes(stage: Path, changes: list[dict[str, Any]]) -> bool:
    """Changed UI resources that the bible's accessibility rules check."""
    try:
        rules = load_rules(stage)
    except ValueError:
        return False  # lint reports invalid rules; they cannot block UI work
    files = [c["path"] for c in changes if DIRECTORY not in {c["before"], c["after"]}]
    return bool(ui_resources(rules, files))


def result_text(result: CallToolResult) -> str:
    return "\n".join(
        part.text for part in result.content if isinstance(part, TextContent)
    )


def _map_paths(value: Any, project: PurePath, stage: PurePath) -> Any:
    if isinstance(value, dict):
        return {k: _map_paths(v, project, stage) for k, v in value.items()}
    if isinstance(value, list):
        return [_map_paths(v, project, stage) for v in value]
    if isinstance(value, str):
        candidate: PurePath
        original: PurePath
        if isinstance(project, PureWindowsPath):
            # PureWindowsPath also permits exercising Windows drive/UNC/case
            # behavior on other hosts without changing the host's os.name.
            candidate = PureWindowsPath(ntpath.normpath(value))
            original = PureWindowsPath(ntpath.normpath(str(project)))
        else:
            candidate = Path(value)
            original = Path(project)
        if not candidate.is_absolute():
            return value
        if isinstance(project, Path):
            candidate = Path(value).resolve()
            original = project.resolve()
        try:
            relative = candidate.relative_to(original)
        except ValueError:
            return value
        return str(stage.joinpath(*relative.parts))
    return value


class ProjectGateway:
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
        self.staged: dict[str, MCPConnection] = {}
        self.transaction: FileTransaction | None = None
        self._last_accepted: FileTransaction | None = None
        self.live_edit_pending = False
        self.request_summary = ""
        self.editor_context: dict[str, str] = {}
        #: The prompt's selection targets; part targets scope typed part edits.
        self.target_context: tuple[dict[str, str], ...] = ()
        #: Set when a call needed an approved art bible; finish() reports it.
        self.art_gate: dict[str, Any] | None = None
        self.runtime_owned = False
        self.runtime_config: str | None = None
        self.runtime_injected: str | None = None
        self.runtime_root_script = False
        self.edit_scene: (
            Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None
        #: Capture the editor's viewport as the user sees it (read-only); set by the chat host.
        self.view_editor: Callable[[], Awaitable[dict[str, Any]]] | None = None
        self._call_lock = asyncio.Lock()
        self.progress: Callable[[str], None] = lambda _text: None
        self.timing: TurnTiming | None = None
        self._discovery_cache: tuple[float, dict[str, Any]] | None = None
        self._discovery_lock = asyncio.Lock()
        #: (transaction id, stage inventory) right after a clean Godot import.
        self._imported: tuple[str, dict[str, str]] | None = None

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
        with self.timing.span("tool_connection") if self.timing else nullcontext():
            await connection.connect()
        return connection

    async def discover(self) -> dict[str, Any]:
        async with self._discovery_lock:
            if (
                self._discovery_cache
                and time.monotonic() - self._discovery_cache[0] < 15
                and all(
                    connection.readiness.connected
                    for connection in self.connections.values()
                )
            ):
                return self._discovery_cache[1]
            with self.timing.span("discovery") if self.timing else nullcontext():
                result = await self._discover()
            if all(s.get("connected") for s in result["servers"]):
                self._discovery_cache = (time.monotonic(), result)
            return result

    async def _discover(self) -> dict[str, Any]:
        self.progress("Connecting project tools…")
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
        self, server: str, tool: str, arguments: dict[str, Any]
    ) -> CallToolResult:
        async with self._call_lock:
            with self.timing.span("tools") if self.timing else nullcontext():
                return await self._call(server, tool, arguments)

    async def _call(
        self, server: str, tool: str, arguments: dict[str, Any]
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
        connection = await self.connection(server)
        if server == "godot" and tool.startswith("game_") and not self.runtime_owned:
            raise ValueError(
                "This call needs a project-owned running-game session; it cannot target an arbitrary global port."
            )
        readonly = read_only(server, tool)
        if not readonly and server == "godot":
            unsaved = json.loads(self.editor_context.get("unsaved_scenes", "[]"))
            scene_path = "res://" + str(arguments.get("scenePath", "")).removeprefix(
                "res://"
            )
            if scene_path in unsaved:
                raise ValueError(
                    "This scene has unsaved editor changes; use the live-editor tools."
                )
        if not readonly and self.live_edit_pending and server == "godot":
            scene = self.editor_context.get("scene_path", "")
            if scene and str(arguments.get("scenePath", "")).removeprefix(
                "res://"
            ) == scene.removeprefix("res://"):
                raise ValueError(
                    "This scene has a live preview; use its editor transaction rather than modifying the same scene file."
                )
        if (
            not readonly
            and server not in {"godot", "joinery-registry"}
            and not approved(self.project)
            and not _reference_only(arguments)
        ):
            # Not an error the agent should work around: the user decides. The
            # sidebar shows the art gate card when the turn finishes.
            gate = self._art_gate()
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
        if not readonly and self.transaction is None:
            self.transaction = FileTransaction(self.project)
            self.transaction.begin(self.request_summary[:160] or "Project changes")
        if self.transaction is not None:
            if self.transaction.state != "staging":
                raise ValueError("Accept or reject the pending transaction first.")
            if not readonly:
                # The Review tab names the app that made each change.
                self.transaction.note_tool(
                    str(arguments.get("tool") or server.removeprefix("joinery-"))
                    if tool in NATIVE_OPS
                    else server.removeprefix("joinery-")
                )
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
            arguments = _map_paths(arguments, self.project, self.transaction.stage)
        if connection.spec.container_name and self.transaction is not None:
            arguments = _map_paths(
                arguments, self.transaction.stage, PurePosixPath("/work")
            )
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
        if tool in NATIVE_OPS:
            owner = str(arguments.get("tool", server.removeprefix("joinery-")))
            self.progress("Working in " + owner.replace("-", " ").capitalize() + "…")
        result = await connection.call(
            tool,
            arguments,
            timeout=900 if server == "blender" or server.startswith("joinery-") else 60,
        )
        if result.is_error and result_text(result).startswith("Rule Zero STOP:"):
            raise PolicyStop(result_text(result))
        if (
            server.startswith("joinery-")
            and tool in NATIVE_OPS
            and not result.is_error
            and self.transaction is not None
        ):
            # Subsequent saved-scene operations can immediately reference the
            # new portable resources; import must precede their first load.
            self.progress("Importing the result into Godot…")
            with self.timing.span("import") if self.timing else nullcontext():
                imported = await run_offline(
                    ["godot", "--headless", "--path", "/work", "--editor", "--import"],
                    self.transaction.stage,
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
            self._imported = (
                self.transaction.id,
                inventory(self.transaction.stage, include_dirs=True),
            )
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

    def _prepare_runtime(self) -> None:
        if self.runtime_config is not None or self.transaction is None:
            return
        root = self.transaction.stage
        script = (
            installation("godot") / "source/build/scripts/mcp_interaction_server.gd"
        )
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
        pinned = (
            installation("godot") / "source/build/scripts/mcp_interaction_server.gd"
        )
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

    def _art_gate(self) -> dict[str, Any]:
        """The art gate result: the sidebar asks the user to approve, then resumes."""
        self.art_gate = {
            "kind": "art_gate",
            "needs_art_approval": True,
            "resume": self.request_summary,
            "message": ART_GATE_MESSAGE,
        }
        return self.art_gate

    async def finish(self) -> dict[str, Any] | None:
        """The review of this turn's changes, or the art gate when it stopped there.

        Returns a ``file_preview``, an ``art_gate`` result
        ``{needs_art_approval, resume, message}`` when asset work needs an
        approved art bible first (nothing is applied), or None.
        """
        gate, self.art_gate = self.art_gate, None
        preview = await self._finish()
        if preview is not None and preview.get("kind") == "art_gate":
            return preview
        if gate is not None:
            if preview is None:
                return gate
            preview["art_gate"] = {k: v for k, v in gate.items() if k != "kind"}
        return preview

    async def _finish(self) -> dict[str, Any] | None:
        tx = self.transaction
        if tx is None:
            return None
        self.progress("Preparing your review…")
        # No background app may continue writing after the reviewed snapshot.
        for connection in self.staged.values():
            await connection.close()
        self.staged.clear()
        self.runtime_owned = False
        self._strip_runtime()
        changes = tx.diff()
        if not changes:
            await self.cancel()
            return None
        for change in changes:
            if Path(change["path"]).parts[0] in PROTECTED:
                raise PermissionError(
                    "Model tools cannot change project policy or MCP configuration. Use project setup."
                )
        # A scene and the model or texture it loads are decided together: never
        # stage a scene that points at a file nobody is adding.
        gaps = references.missing(
            tx.stage,
            [c["path"] for c in changes if c["after"] not in {"", DIRECTORY}],
        )
        for scene, paths in references.users_of(
            tx.stage, [c["path"] for c in changes if not c["after"]]
        ).items():
            gaps.setdefault(scene, []).extend(paths)
        if gaps:
            raise ValueError(references.refusal(gaps))
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
        bible_ok = _approved_in(self.project, tx.stage)
        rules = False
        if asset_changes:
            # Reference boards come before the bible; they still need a lock entry.
            if not bible_ok and not all(_is_reference(p) for p in asset_changes):
                # Nothing is applied: the stage is dropped and the user is asked.
                gate = self._art_gate()
                self.art_gate = None
                await self.cancel()
                return gate
            problems = check_assets(tx.stage, load_catalog())
            if problems:
                raise ValueError("Asset validation failed: " + "; ".join(problems))
            rules = bible_ok
        else:
            # UI themes and scenes are checked for text contrast and size.
            rules = bible_ok and _ui_changes(tx.stage, changes)
        problems = check_deps(tx.stage, load_denylist())
        if problems:
            raise ValueError("Dependency validation failed: " + "; ".join(problems))
        godot = any(
            Path(change["path"]).suffix
            in {".gd", ".tscn", ".tres", ".gdshader", ".glb"}
            or change["path"] == "project.godot"
            for change in changes
        ) and not self._import_current(tx)
        await self._checks(tx, changes, rules, godot)
        text_diffs: list[str] = []
        for change in changes:
            if Path(change["path"]).suffix in {
                ".gd",
                ".tscn",
                ".tres",
                ".json",
                ".toml",
                ".gdshader",
                ".md",
            }:
                old = tx.original / change["path"]
                new = tx.stage / change["path"]
                before = (
                    old.read_text(errors="replace").splitlines()
                    if old.is_file()
                    else []
                )
                after = (
                    new.read_text(errors="replace").splitlines()
                    if new.is_file()
                    else []
                )
                text_diffs.extend(
                    difflib.unified_diff(
                        before,
                        after,
                        fromfile=change["path"],
                        tofile=change["path"],
                        lineterm="",
                    )
                )
        reviewed = tx.review()
        if any(Path(change["path"]).suffix == ".blend" for change in changes):
            self.progress("Rendering the before and after…")
        with self._span("review_media"):
            media = await evidence(
                tx,
                changes,
                [dict(t) for t in self.target_context if t.get("part_kind")],
            )
        return {
            "kind": "file_preview",
            "diff": "\n".join(text_diffs[:400]),
            "transaction_id": tx.id,
            "summary": tx.summary,
            "changes": reviewed,
            "media": media,
        }

    def _span(self, phase: str) -> AbstractContextManager[None]:
        return self.timing.span(phase) if self.timing else nullcontext()

    def _import_current(self, tx: FileTransaction) -> bool:
        """Godot already imported this exact stage cleanly (after a native export)."""
        if self._imported is None or self._imported[0] != tx.id:
            return False
        return self._imported[1] == inventory(tx.stage, include_dirs=True)

    async def _checks(
        self,
        tx: FileTransaction,
        changes: list[dict[str, str]],
        rules: bool,
        godot: bool,
    ) -> None:
        """The art bible's measured rules and Godot's import check, side by side.

        Each runs in the offline worker and takes seconds; neither writes what
        the other reads (the rules mount the stage read-only).
        """

        async def measured() -> list[str]:
            if not rules:
                return []
            with self._span("review_rules"):
                return await check_rules(
                    tx.stage,
                    [change["path"] for change in changes],
                    lambda argv, root: run_offline(argv, root, 180, readonly=True),
                )

        async def imported() -> str:
            if not godot:
                return ""
            with self._span("review_import"):
                got = await run_offline(
                    ["godot", "--headless", "--path", "/work", "--editor", "--import"],
                    tx.stage,
                    180,
                )
            if (
                not got.ok
                or "SCRIPT ERROR:" in got.output
                or "Parse Error:" in got.output
            ):
                return got.output[-4000:] or "Godot exited with an error."
            return ""

        found = await asyncio.gather(measured(), imported(), return_exceptions=True)
        for outcome in found:
            if isinstance(outcome, BaseException):
                raise outcome
        problems, failed = cast(tuple[list[str], str], tuple(found))
        if problems:
            raise ValueError("Art bible check failed: " + "; ".join(problems))
        if failed:
            raise ValueError("Godot import/script validation failed: " + failed)

    async def decide(self, identifier: str, accept: bool) -> dict[str, Any]:
        tx = self.transaction
        if tx is None or tx.id != identifier:
            raise ValueError("This transaction is not pending in the current session.")
        changes = tx.accept() if accept else []
        notice = ""
        if accept:
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

    async def close(self) -> None:
        if self.transaction is not None and self.transaction.state in {
            "staging",
            "review",
        }:
            self.transaction.reject()
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
