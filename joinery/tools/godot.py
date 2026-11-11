"""Godot: the Joinery editor on the host; its MCP server and headless Godot offline."""

from __future__ import annotations

from joinery.tools.base import (
    MCP_WORKER,
    HostExec,
    ImageCheck,
    InstallCheck,
    McpProbe,
    ToolModule,
    WorkerExec,
)

#: Replaced by the project's absolute path when a probe runs.
PROJECT = "{project}"


class Godot(ToolModule):
    id = "godot"
    name = "Godot"
    worker_image = MCP_WORKER
    binary = "godot"
    desktop = "godot"
    ready_summary = "Godot {version} tools ready (offline worker)"
    needs_image = "Godot tools: worker missing. Rebuild the worker."
    needs_install = "Godot tools: not installed. Rebuild the worker."
    broken = "Godot tools: error"
    checks = (
        HostExec(
            "engine_binary",
            ("--version",),
            expect=r"\d+\.\d+",
            version=r"(\d+\.\d+(?:\.\d+)?)",
            failure="The Joinery editor binary did not start",
        ),
        ImageCheck(MCP_WORKER),
        WorkerExec(
            MCP_WORKER,
            ("godot", "--version", "--headless"),
            expect=r"\d+\.\d+",
            on_failure="needs_image",
            failure="Godot is missing from the offline worker; rebuild it",
        ),
        InstallCheck("godot"),
        # The pinned server runs in the worker, so its answer is cached against
        # the worker image it came from.
        McpProbe(
            "godot",
            "get_project_info",
            args=(("projectPath", PROJECT),),
            image=MCP_WORKER,
        ),
    )

    def replay_argv(self, script: str) -> list[str]:
        return ["godot", "--headless", "--script", script]


MODULE = Godot()
