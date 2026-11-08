"""One project tool gateway: real discovery, shared policy and staged mutations."""

from __future__ import annotations

import asyncio
import difflib
import json
import ntpath
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any, cast

from mcp.types import CallToolResult, TextContent

from joinery.art_approval import approved, review
from joinery.art_approval import fingerprint as bible_fingerprint
from joinery.authoring import run_offline
from joinery.catalog import load_catalog, load_project_tools
from joinery.deps import check_deps
from joinery.lockfile import asset_files, check_assets
from joinery.locking import record_custom_outputs
from joinery.mcp_runtime import MCPConnection, PolicyStop, validate_arguments
from joinery.policy import load_denylist
from joinery.review import evidence
from joinery.server_install import configured, installation
from joinery.transactions import DIRECTORY, FileTransaction, ProjectLease, inventory
from joinery.workers import (
    blender_worker,
    godot_worker,
    inkscape_worker,
    supercollider_worker,
)

GODOT_READS = {
    "get_godot_version",
    "get_project_info",
    "list_project_files",
    "read_file",
    "read_scene",
    "read_project_settings",
    "get_uid",
    "validate_script",
    "validate_scripts",
}
BLENDER_READS = {
    "get_blendfile_summary_datablocks",
    "get_objects_summary",
    "get_blendfile_summary_missing_files",
    "get_blendfile_summary_linked_libraries",
    "get_blendfile_summary_paths",
    "get_python_api_docs",
    "search_api_docs",
    "search_manual_docs",
}
FIRST_PARTY_READS = {
    "document_capabilities",
    "inspect_document",
    "validate_project",
    "transaction_status",
}
PROTECTED = {
    "joinery.toml",
    ".mcp.json",
    "policy",
    ".claude",
    ".codex",
    ".gemini",
    ".opencode",
}


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
        self.runtime_owned = False
        self.runtime_config: str | None = None
        self.runtime_injected: str | None = None
        self.runtime_root_script = False
        self.edit_scene: (
            Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None
        self._call_lock = asyncio.Lock()
        self.progress: Callable[[str], None] = lambda _text: None

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
        await connection.connect()
        return connection

    async def discover(self) -> dict[str, Any]:
        self.progress("Connecting project tools…")
        tools = []
        states = [state.model_dump() for state in self.unavailable]
        for spec in self.specs:
            connection = self.connections.get(spec.name)
            if connection is None:
                if spec.source == "first-party":
                    spec.worker_scope = uuid.uuid4().hex
                    spec.env["JOINERY_WORKER_SCOPE"] = spec.worker_scope
                connection = MCPConnection(spec, self.project)
                self.connections[spec.name] = connection
            try:
                await connection.connect()
                tools += [
                    {
                        "server": spec.name,
                        **tool.model_dump(by_alias=True, exclude_none=True),
                    }
                    for tool in connection.tools.values()
                ]
                states.append(connection.readiness.model_dump())
            except PolicyStop:
                raise
            except (OSError, RuntimeError, ValueError, TimeoutError):
                states.append(connection.readiness.model_dump())
        return {"servers": states, "tools": tools}

    async def call(
        self, server: str, tool: str, arguments: dict[str, Any]
    ) -> CallToolResult:
        async with self._call_lock:
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
        readonly = (
            (server == "godot" and tool in GODOT_READS)
            or (server == "blender" and tool in BLENDER_READS)
            or server == "joinery-registry"
            or (server == "claude-collider" and tool == "cc_status")
            or (server.startswith("joinery-") and tool in FIRST_PARTY_READS)
        )
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
        ):
            raise PermissionError(
                "Approve the project art bible before creative asset work."
            )
        if not readonly and self.transaction is None:
            self.transaction = FileTransaction(self.project)
            self.transaction.begin(self.request_summary[:160] or "Project changes")
        if self.transaction is not None:
            if self.transaction.state != "staging":
                raise ValueError("Accept or reject the pending transaction first.")
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
            arguments = _map_paths(arguments, self.transaction.stage, PurePosixPath("/work"))
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
        if tool in {
            "operate_document",
            "transform_parts",
            "operate_graph",
            "operate_vector",
        }:
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
            and tool
            in {
                "operate_document",
                "transform_parts",
                "operate_graph",
                "operate_vector",
            }
            and not result.is_error
            and self.transaction is not None
        ):
            # Subsequent saved-scene operations can immediately reference the
            # new portable resources; import must precede their first load.
            self.progress("Importing the result into Godot…")
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

    async def finish(self) -> dict[str, Any] | None:
        tx = self.transaction
        if tx is None:
            return None
        self.progress("Checking project changes…")
        # No background app may continue writing after the reviewed snapshot.
        for connection in self.staged.values():
            await connection.close()
        self.staged.clear()
        self.runtime_owned = False
        self._strip_runtime()
        if not tx.diff():
            await self.cancel()
            return None
        changes = tx.diff()
        for change in changes:
            if Path(change["path"]).parts[0] in PROTECTED:
                raise PermissionError(
                    "Model tools cannot change project policy or MCP configuration. Use project setup."
                )
        assets_changed = any(
            (
                Path(change["path"]).parts[0] in {"art", "audio"}
                and change["path"] != "art/bible.md"
                or Path(change["path"]).suffix.lower()
                in {
                    ".blend",
                    ".glb",
                    ".gltf",
                    ".kra",
                    ".aseprite",
                    ".ptex",
                    ".wav",
                    ".ogg",
                    ".png",
                    ".jpg",
                    ".svg",
                }
            )
            and DIRECTORY not in {change["before"], change["after"]}
            for change in changes
        )
        if assets_changed:
            if not approved(self.project) or bible_fingerprint(
                self.project
            ) != bible_fingerprint(tx.stage):
                raise PermissionError(
                    "Approve the project art bible before asset work."
                )
            problems = check_assets(tx.stage, load_catalog())
            if problems:
                raise ValueError("Asset validation failed: " + "; ".join(problems))
        problems = check_deps(tx.stage, load_denylist())
        if problems:
            raise ValueError("Dependency validation failed: " + "; ".join(problems))
        if any(
            Path(change["path"]).suffix
            in {".gd", ".tscn", ".tres", ".gdshader", ".glb"}
            or change["path"] == "project.godot"
            for change in changes
        ):
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
                raise ValueError(
                    "Godot import/script validation failed: " + got.output[-4000:]
                )
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
        self.progress("Preparing the visual review…")
        return {
            "kind": "file_preview",
            "diff": "\n".join(text_diffs[:400]),
            "transaction_id": tx.id,
            "summary": tx.summary,
            "changes": tx.review(),
            "media": await evidence(tx, changes),
        }

    async def decide(self, identifier: str, accept: bool) -> dict[str, Any]:
        tx = self.transaction
        if tx is None or tx.id != identifier:
            raise ValueError("This transaction is not pending in the current session.")
        changes = tx.accept() if accept else []
        if accept:
            self._last_accepted = tx
        if not accept:
            tx.reject()
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
        if (
            accept
            and any(change["path"] == "art/bible.md" for change in changes)
            and (self.project / "art/bible.md").is_file()
            and not approved(self.project)
        ):
            event["message"] = (
                "Art direction draft saved. Review it and click Approve this version "
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
        return {
            "kind": "file_decision",
            "transaction_id": identifier,
            "accepted": False,
            "changes": [],
            "message": "The live preview changed before acceptance. File changes were restored; review the scene before retrying.",
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
