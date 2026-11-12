"""Provider-neutral MCP tools that connect to the current editor's project gateway."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from joinery import chat_attachments, roles
from joinery.blockout.layout import Layout
from joinery.editor_ops import SceneEdit
from joinery.gateway_rpc import rpc
from servers.errors import tool_errors


class ReviewNoteArgument(BaseModel):
    """One finding of a review."""

    severity: Literal["info", "warn", "block"]
    text: Annotated[str, Field(min_length=1, max_length=400)]
    file: str | None = None
    rule: (
        Literal["request", "art_bible", "brief", "broken", "work_lost", "other"] | None
    ) = None


class DoneWhenArgument(BaseModel):
    """One item of a to-do's "done when": what done means, and what checks it."""

    text: Annotated[str, Field(min_length=1, max_length=160)]
    kind: Literal["reach", "no_errors", "test", "check", "look"] = "look"
    target: Annotated[str, Field(max_length=200)] | None = None
    seconds: Annotated[float, Field(ge=10, le=300)] | None = None


class DesignEvidenceArgument(BaseModel):
    """One place in the project a design finding rests on."""

    path: Annotated[str, Field(min_length=1, max_length=300)]
    line: Annotated[int, Field(ge=0)] = 0
    node: Annotated[str, Field(max_length=200)] = ""
    quote: Annotated[str, Field(max_length=300)] = ""


class DesignFindingArgument(BaseModel):
    """One design finding: what breaks the game or goes against its own brief."""

    severity: Literal["high", "medium", "low"]
    title: Annotated[str, Field(min_length=1, max_length=140)]
    player: Annotated[str, Field(min_length=1, max_length=400)]
    why: Annotated[str, Field(min_length=1, max_length=600)]
    basis: Literal["brief", "gdd", "broken"]
    evidence: Annotated[list[DesignEvidenceArgument], Field(min_length=1, max_length=6)]
    fix: Annotated[str, Field(max_length=600)] = ""


#: The server's instructions: the same bytes for every agent of a role.
WRITER_INSTRUCTIONS = (
    "Use discover_tools to check real connected capabilities, then tool_schema before calling a tool. "
    "Mutations are staged; call submit_for_review when the change is complete. "
    "Never claim staged changes are accepted or saved."
)
READER_INSTRUCTIONS = (
    "Use discover_tools to check real connected capabilities, then tool_schema before calling a tool. "
    "This agent only reads: call_tool reaches read-only project tools."
)


def make_server(socket: Path, role: str | None = None) -> MCPServer[Any]:
    """The proxy's tools. With ``role`` (joinery.roles), only what that role may call
    is listed, so a read-only agent never carries a writer's schemas; None lists all."""
    reader = role is not None and roles.spec(role).read_only
    server: MCPServer[Any] = MCPServer(
        "joinery-registry",
        instructions=READER_INSTRUCTIONS if reader else WRITER_INSTRUCTIONS,
    )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def discover_tools(query: str = "") -> dict[str, Any]:
        """Find installed and connected project tools by purpose, with readiness and missing-tool reasons."""
        return await rpc(socket, "inventory", {"query": query})

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def tool_schema(server: str, tool: str) -> dict[str, Any]:
        """Read the actual MCP input schema for a project tool before invoking it."""
        return await rpc(socket, "schema", {"server": server, "tool": tool})

    @server.tool()
    @tool_errors
    async def edit_scene(edit: SceneEdit) -> dict[str, Any]:
        """Preview native changes in the current live Godot editor, including unsaved scenes. Reusable create/delete/duplicate/rename/reparent/property operations. Uses stable context identities. instantiate_scene places a model by its res:// path, also one you made earlier in this change: it is reviewed together with the model, in one change. To swap a placeholder, pass replace (the placeholder's identity; the model takes its parent and transform) and fit_size true to keep its size. The user accepts or rejects; never claim it is saved."""
        return await rpc(socket, "editor_edit", edit.model_dump(mode="json"))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def inspect_editor(
        node: Annotated[str, Field(max_length=300)] = "",
    ) -> dict[str, Any]:
        """Read the current project's live editor scene as an outline (one line per node: path, type, position, script), node identities, selection and unsaved scene context. Use these identities for edit_scene. node: a node's id, path or name to get its properties. Saved-file MCP tools and game_* runtime tools are different targets."""
        return await rpc(socket, "editor_context", {"node": node} if node else {})

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def details(
        ref: Annotated[str, Field(max_length=20)],
        path: Annotated[str, Field(max_length=300)] = "",
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Read more of a long tool result that came back as a summary (it has details.ref). path: one part of it, keys and list indexes joined by '/' ('content/0/text', 'problems/12'). offset: the line to start from (next_offset of the last page). Ask only for what you need."""
        return await rpc(
            socket, "details", {"ref": ref, "path": path, "offset": offset}
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def view_editor() -> CallToolResult:
        """See the editor's current 3D or 2D viewport exactly as the user sees it, with the camera position. Use it when the user asks you to look at something. The screenshot is context only, never an asset."""
        data = await rpc(socket, "editor_view", {})
        media, encoded = chat_attachments.encoded(Path(str(data["path"])))
        return CallToolResult(
            content=[
                ImageContent(type="image", data=encoded, mime_type=media),
                TextContent(type="text", text=json.dumps(data)),
            ]
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def inspect_in_parallel(tasks: list[dict[str, str]]) -> dict[str, Any]:
        """Delegate one or two independent read-only inspection/validation questions to fast specialist agents. Each task has name and question. No edits or nested delegation; use the main agent for all changes. Results can fail independently and must be checked."""
        return await rpc(socket, "delegate", {"tasks": tasks})

    @server.tool()
    @tool_errors
    async def submit_for_review(
        summary: Annotated[str, Field(min_length=1, max_length=160)],
        facts: Annotated[list[str], Field(max_length=3)] | None = None,
    ) -> dict[str, Any]:
        """Hand your finished change to Joinery. Joinery checks it (art bible rules, Godot import, missing res:// files, asset provenance, project tests). status 'in_review': the user now sees it; make no more changes this turn. 'failed' and not final: fix every problem listed and submit again (attempts_left). 'needs_user', or final: stop and tell the user plainly what failed. summary: one short line in the user's words naming what changed. facts: optional, up to 3 tiny plain phrases of what visibly changed, with numbers when known ('door 20% wider', 'wood darker')."""
        return await rpc(socket, "submit", {"summary": summary, "facts": facts or []})

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def precheck() -> dict[str, Any]:
        """Run the checks submit_for_review runs (protected files, missing res:// files, objects built from placeholder shapes, asset provenance, dependencies, art bible rules, Godot import, project tests) on your change as it is now, without submitting: no reviewer, no submit attempt used, nothing changed. status 'clean', 'problems', 'needs_user' (stop and tell the user), 'nothing_to_check' or 'error'. Each problem has items {rule, file, node, measured, expected, fix}: fix them, then submit. Call it once before submit_for_review when your change touched assets, scenes or code."""
        return await rpc(socket, "precheck", {})

    @server.tool()
    @tool_errors
    async def propose_todo(
        steps: Annotated[list[str], Field(min_length=1, max_length=8)],
        done_when: Annotated[list[DoneWhenArgument], Field(max_length=6)] | None = None,
    ) -> dict[str, Any]:
        """For a request that takes several steps, before changing anything: show the user your to-do. steps: 1 to 8 short lines in the user's words. done_when: up to 6 items that say when the request is done, each checked by Joinery after your change passes its checks, with its result shown in the review; a failed item comes back to you once to fix. Each item is {text, kind, target?, seconds?}: kind 'reach' (target: a place named like its node, 'key room'; the playtest bot must get there), 'no_errors' (seconds of play with no crash or script error, default 60), 'test' (target: the res:// GUT test you write in the change, e.g. that the door opens when the lever is pulled), 'check' (target: tests, godot_import, art_bible, references, provenance or dependencies), or 'look' (only the user can judge). status 'waiting_for_go': end your turn now without changing anything; the user's Go starts the work. status 'go': carry on. Skip it for small, one-step requests."""
        return await rpc(
            socket,
            "propose_todo",
            {
                "steps": steps,
                "done_when": [
                    item.model_dump(exclude_none=True) for item in done_when or []
                ],
            },
        )

    @server.tool()
    @tool_errors
    async def start_task(
        title: Annotated[str, Field(min_length=1, max_length=80)],
        request: Annotated[str, Field(min_length=1, max_length=4000)],
        targets: list[str] | None = None,
    ) -> dict[str, Any]:
        """Hand an independent part of the user's request to a parallel agent. It works in its own staged change on different files (another asset, another scene) and the user reviews its change separately. request must be complete on its own: the task does not see this conversation. targets: project paths the task will create or change (res://… or relative); they are reserved for it. Returns the task id. Don't split dependent steps or work on the same files; tasks can't start tasks."""
        return await rpc(
            socket,
            "start_task",
            {"title": title, "request": request, "targets": targets or []},
        )

    @server.tool()
    @tool_errors
    async def start_variants(
        title: Annotated[str, Field(min_length=1, max_length=80)],
        request: Annotated[str, Field(min_length=1, max_length=4000)],
        directions: Annotated[list[str], Field(min_length=2, max_length=4)],
        targets: list[str] | None = None,
    ) -> dict[str, Any]:
        """Make the user's request a few different ways side by side, one parallel agent per direction, so the user can compare and keep one. Only when the user asked for options or accepted your offer. request must be complete on its own. directions: 2 to 4 short, clearly different notes ('chunky and worn', 'slim and clean'). targets: project paths every way will change. The user picks one way and the others are discarded; don't make the request yourself as well."""
        return await rpc(
            socket,
            "start_variants",
            {
                "title": title,
                "request": request,
                "directions": directions,
                "targets": targets or [],
            },
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def task_status(id: str) -> dict[str, Any]:
        """How a parallel task started with start_task is doing: state (working, in_review, accepted, discarded, done, failed, stopped), its current step, and its change. Check now and then; never wait in a loop."""
        return await rpc(socket, "task_status", {"id": id})

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def playtest(
        minutes: Annotated[float | None, Field(ge=0.25, le=10)] = None,
        seed: Annotated[int | None, Field(ge=0)] = None,
        focus: Annotated[str, Field(max_length=200)] = "",
        reach: Annotated[list[str], Field(max_length=8)] | None = None,
    ) -> CallToolResult:
        """Play the game headless in stock Godot with a bot that uses the game's own controls, for `minutes` of game time (default from the project), and report what went wrong: script errors, crashes, freezes, softlocks, falls out of the world, frame hitches, broken save/load and text that doesn't fit, plus balance notes from timings and deaths. The bot explores toward the parts of the level it hasn't seen: coverage says what share of the floor it reached and where the biggest unreached areas are, and a map marks them. Each item has a severity and repro steps with the seed; pass the same seed to replay a run exactly. focus: words naming what to aim for. reach: places to head for first, named like their nodes ('key room' finds KeyRoom); reach in the result says whether and when each was reached. Changes nothing. Screenshots and the map are context only, never assets."""
        data = await rpc(
            socket,
            "playtest",
            {"minutes": minutes, "seed": seed, "focus": focus, "reach": reach or []},
        )
        result = CallToolResult(
            content=[TextContent(type="text", text=json.dumps(data))]
        )
        pictures = [str(p) for p in data.get("screenshots", [])][:3]
        picture = str((data.get("coverage") or {}).get("map") or "")
        if picture:
            pictures.append(picture)
        for shot in pictures:
            try:
                media, encoded = chat_attachments.encoded(Path(shot))
            except (OSError, ValueError):
                continue
            result.content.append(
                ImageContent(type="image", data=encoded, mime_type=media)
            )
        return result

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def access_check(areas: list[str] | None = None) -> dict[str, Any]:
        """Check the game's accessibility (not the editor's): stock Godot plays it headless and reads every text and control on the HUD and menus. Returns scores per area (contrast, colour, text size, subtitles, controls, motion, pause, assists, screen readers) and findings, each with what was measured, what it should be, where it was seen (scene, node, seed) and how it gets fixed (typed, agent or feature). areas: only these. Changes nothing."""
        return await rpc(socket, "access_check", {"areas": areas or []})

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def perf_check(
        target: Literal["", "high", "mid", "low", "steam-deck", "web"] = "",
    ) -> dict[str, Any]:
        """Measure how the game runs: stock Godot plays a seeded run headless and in a background window, and returns frame times (p50/p95/p99, scaled to the target machine), draw calls, primitives, texture memory, shader compilations, nodes, physics bodies, load time, memory growth and each script's cost, with findings that say where (scene, node, seed) and how each is fixed. target: the project's own by default. Changes nothing."""
        return await rpc(socket, "perf_check", {"target": target})

    @server.tool()
    @tool_errors
    async def blockout(
        layout: Layout,
        scene: Annotated[str, Field(max_length=200)] = "",
        blueprint: Annotated[str, Field(max_length=40)] = "",
        sketch: Annotated[str, Field(max_length=1000)] = "",
    ) -> CallToolResult:
        """Build a greybox level from a layout: rooms, walls with doors and windows, corridors, stairs, platforms, a spawn and goal/coin/pickup/checkpoint/hazard markers. Read the user's sketch (an attached image; a reference only) and write the layout: plan x right (east), y down (south); 3D in metres (plan x,y become Godot X,Z; heights in metres), 2D in tiles (2d-side for platformers, 2d-topdown). Rect = [x, y, width, height]; rooms that touch share an edge line; a corridor cuts the room walls it crosses; openings sit on a room wall (north/east/south/west, or e0.. for polygons) at a distance from its west or north end. No size given: a door gap is about 1 m (3D) or 2 tiles (2D). Joinery checks the layout (closed rooms, sizes, every goal reachable from the spawn) and returns problems to fix and a picture of the plan; when sound it stages design/blockouts/<name>.toml (the source to edit for changes; call again with the whole layout) and the scene (default res://game/<name>_blockout.tscn), walks spawn to goals in Godot, and returns facts for submit_for_review. scene: another res:// target. blueprint: the starting point whose player to check with (default the project's). sketch: the attachment path shown beside the plan in the review (default the message's first image)."""
        data = await rpc(
            socket,
            "blockout",
            {
                "layout": layout.model_dump(mode="json", exclude_none=True),
                "scene": scene,
                "blueprint": blueprint,
                "sketch": sketch,
            },
        )
        result = CallToolResult(
            content=[TextContent(type="text", text=json.dumps(data))]
        )
        picture = str(data.get("picture", "") or "")
        if picture:
            try:
                media, encoded = chat_attachments.encoded(Path(picture))
            except (OSError, ValueError):
                return result
            result.content.append(
                ImageContent(type="image", data=encoded, mime_type=media)
            )
        return result

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def sound_audit(
        scene: Annotated[str, Field(max_length=300)] = "",
    ) -> dict[str, Any]:
        """Listen to the game without playing it: which sounds the level and its player are missing (footsteps per floor surface, jump and land, pickups, hits, doors and switches, ambience, UI clicks), what is already there, the sound Joinery's kit would make for each and where it would go, ranked by the latest playtest. scene: a level as res://…, default the project's level. Changes nothing."""
        return await rpc(socket, "sound_audit", {"scene": scene})

    @server.tool()
    @tool_errors
    async def report_review(notes: list[ReviewNoteArgument]) -> dict[str, Any]:
        """Reviewer agents only: report your review of the submitted change, once, then end your turn. notes: up to 12 findings, most important first, each {severity, text, file?, rule?}. severity 'block': the change is wrong and the author must fix it (breaks the request, the art bible, the game brief or the game); 'warn': worth the user's attention; 'info': a small remark. rule: what a block breaks: request, art_bible, brief, broken (the game), work_lost, or other. An empty list means nothing to note."""
        return await rpc(
            socket,
            "report_review",
            {"notes": [note.model_dump(exclude_none=True) for note in notes]},
        )

    @server.tool()
    @tool_errors
    async def report_design(findings: list[DesignFindingArgument]) -> dict[str, Any]:
        """Design check agents only: report what breaks the game or goes against its own brief or design document, then end your turn. findings: up to 8, most serious first, each {severity high|medium|low, title (one short line), player (what the player experiences), why, basis 'brief'|'gdd'|'broken', evidence [{path, line?, node?, quote?}], fix (a request Journeyman could act on)}. Evidence must hold: the file exists, the line is in it, the quoted words are on it, the node is in the scene; a finding resting on the brief or a design document cites its line under design/. Findings that don't hold are dropped and listed back. Never report taste."""
        return await rpc(
            socket,
            "report_design",
            {"findings": [f.model_dump() for f in findings]},
        )

    @server.tool()
    @tool_errors
    async def report_diagnosis(
        cause: Annotated[str, Field(min_length=1, max_length=1000)],
        fix: Annotated[str, Field(max_length=4000)] = "",
        targets: list[str] | None = None,
        reproduced: bool = False,
    ) -> dict[str, Any]:
        """Debugger agents only: report what causes the error, once, then end your turn. cause: what goes wrong and why, in plain words. fix: the complete change a worker agent should make (it does not see this conversation); empty when no project change fixes it. targets: project paths the fix changes. reproduced: you saw the error in reproduce_error's run."""
        return await rpc(
            socket,
            "report_diagnosis",
            {
                "cause": cause,
                "fix": fix,
                "targets": targets or [],
                "reproduced": reproduced,
            },
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @tool_errors
    async def reproduce_error(scene: str = "") -> dict[str, Any]:
        """Debugger agents only: run the project headless in Joinery's offline worker (no network, a throwaway copy) for a few seconds and return the errors and output it printed. scene: a res:// scene to run, or empty for the main scene."""
        return await rpc(socket, "reproduce", {"scene": scene})

    @server.tool()
    @tool_errors
    async def call_tool(
        server: str, tool: str, arguments: dict[str, Any]
    ) -> CallToolResult:
        """Operate a connected project tool; changes are staged, policy-checked, and require user review."""
        data = await rpc(
            socket,
            "call",
            {"server": server, "tool": tool, "arguments": arguments},
        )
        result = CallToolResult.model_validate(data)
        if "details" in data:
            result.content.append(
                TextContent(type="text", text="Joinery: " + json.dumps(data["details"]))
            )
        if "joinery" in data:
            result.content.append(
                TextContent(type="text", text="Joinery: " + str(data["joinery"]))
            )
        return result

    if role is not None:
        offered = roles.offered_tools(role)
        for name in roles.REGISTRY_TOOLS:
            if name not in offered:
                server.remove_tool(name)
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--role", choices=roles.ROLES, default=None)
    args = parser.parse_args()
    make_server(args.socket, args.role).run()


if __name__ == "__main__":
    main()
