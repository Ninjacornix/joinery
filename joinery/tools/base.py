"""What a tool module declares: its native format, its adapter and its readiness checks.

Checks are data. `joinery.readiness` is the only code that runs them, so every
front end (doctor, status, the gateway's discover_tools, the addon) and every
agent sees the same answer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

#: The offline worker most tools run in.
CREATIVE_WORKER = "joinery-creative-worker"
MCP_WORKER = "joinery-mcp-worker"
BLENDER_WORKER = "joinery-blender-worker"
INKSCAPE_WORKER = "joinery-inkscape-worker"
AUDACITY_WORKER = "joinery-audacity-worker"

#: The worker images' shared entrypoint (starts a virtual display, then runs
#: the command). Images whose own entrypoint starts a server are probed through
#: this one instead.
SANDBOX_ENTRYPOINT = "/usr/local/bin/joinery-sandbox"

#: How long a check may take, cheapest first. 0 is always live; 1 and 2 run a
#: worker container; 3 starts an MCP server; "deep" runs only on request.
Tier = Literal[0, 1, 2, 3, "deep"]

#: Empty means the check is required for the tool. Otherwise it names the
#: feature it belongs to, and failing it degrades only that feature.
REQUIRED = ""

#: What a failing check means. Most failures are "broken"; a program or
#: extension missing from an image that runs means the image is out of date.
Failure = Literal["broken", "needs_image"]


@dataclass(frozen=True)
class ImageCheck:
    """The worker image is present (tier 0)."""

    image: str
    feature: str = REQUIRED
    tier: Tier = 0


@dataclass(frozen=True)
class WorkerExec:
    """Run a command in a worker with no network and match its output."""

    image: str
    argv: tuple[str, ...]
    #: Regular expression the combined output must contain.
    expect: str
    #: Regular expression whose first group is the version, or "".
    version: str = ""
    timeout: int = 60
    tier: Tier = 1
    feature: str = REQUIRED
    #: Overrides the image's entrypoint; "" keeps it.
    entrypoint: str = ""
    #: Shown when the command runs but its output does not match.
    failure: str = ""
    on_failure: Failure = "broken"


@dataclass(frozen=True)
class HostExec:
    """Run a program on the host (only the Joinery editor itself runs there)."""

    #: Name of a resolver in joinery.readiness (such as "engine_binary").
    program: str
    args: tuple[str, ...]
    expect: str
    version: str = ""
    timeout: int = 30
    tier: Tier = 1
    feature: str = REQUIRED
    failure: str = ""


@dataclass(frozen=True)
class McpProbe:
    """Call a read-only tool on one of the project's MCP servers (tier 3)."""

    server: str
    tool: str
    args: tuple[tuple[str, Any], ...] = ()
    #: Regular expression the text result must contain; "" accepts any success.
    expect: str = ""
    #: For a document_capabilities probe: the tool id that must report both
    #: `application_installed` and `runtime_available`.
    capability: str = ""
    feature: str = REQUIRED
    tier: Tier = 3
    failure: str = ""
    #: The worker image the server runs in, for cache keys; "" for host servers.
    image: str = ""

    def arguments(self) -> dict[str, Any]:
        return dict(self.args)


@dataclass(frozen=True)
class InstallCheck:
    """The tool's pinned MCP source is installed and unchanged (tier 0)."""

    tool: str
    feature: str = REQUIRED
    tier: Tier = 0


Check = ImageCheck | WorkerExec | HostExec | McpProbe | InstallCheck


@dataclass(frozen=True)
class Feature:
    """Something a tool can do beyond its required checks."""

    id: str
    #: Short phrase for the ready summary, such as "Rigify".
    label: str
    #: Prefix shown when a check runs and fails. What a refresh fixes is worded by
    #: joinery.readiness, the same for every tool.
    broken: str = ""


#: Runs a command in the offline worker. Supplied by joinery.authoring so a
#: test can replace the worker boundary in one place.
Run = Callable[..., Awaitable[Any]]


class ToolModule:
    """One catalog tool: its native document adapter and its readiness checks.

    A module with no `script_ext` has no `operate_document` adapter; it may
    still own a typed operation (graph, vector) or be status-only.
    """

    id: str
    name: str
    #: Joinery's own MCP server that owns this tool's documents, or "".
    family: str = ""
    #: Native source suffix, such as ".blend"; "" when there is none.
    suffix: str = ""
    #: Recipe script extension for operate_document; "" means no script adapter.
    script_ext: str = ""
    #: True when Joinery edits this tool's native documents (any adapter).
    authoring: bool = False
    #: True when operate_document writes the code itself as the source text.
    text_source: bool = False
    worker_image: str = CREATIVE_WORKER
    #: Program document_capabilities looks for in the worker.
    binary: str = ""
    #: Key into joinery.desktop.TOOLS for "Open in".
    desktop: str = ""
    checks: tuple[Check, ...] = ()
    features: tuple[Feature, ...] = ()
    #: Fixed version when no check reports one (Material Maker's binary has none).
    version: str = ""
    #: Ready line; "{name}" and "{version}" are filled in.
    ready_summary: str = "{name} {version} ready (runs offline)"
    #: Prefix for a failing required check; the reason follows. What a refresh
    #: fixes is worded by joinery.readiness, the same for every tool.
    broken: str = "{name} failed its check"
    #: Optional provider of selectable parts inside a source document.
    parts_provider: str = ""
    #: (upstream MCP tool, note) pairs. The gateway adds each note to that
    #: tool's description in discovery, so every agent reads it before calling.
    mcp_notes: tuple[tuple[str, str], ...] = ()

    def script(self, source: str, base: str, code: str, exports: list[str]) -> str:
        """The recipe script that applies `code` to the native document."""
        raise ValueError(
            "Use the graph or vector document operation for this application."
        )

    def replay_argv(self, script: str) -> list[str]:
        """The worker command that replays a recipe script."""
        raise ValueError(f"No replay command for tool {self.id!r}.")

    #: False when a recipe rebuilds the source from its inputs alone, so an
    #: edit keeps no copy of the old source (an Audacity project).
    uses_preimage: bool = True

    #: The worker image a recipe replays in when the tool has its own image;
    #: "" uses the runner's image (the creative worker for document edits).
    replay_image: str = ""

    def script_inputs(self, code: str) -> tuple[str, ...]:
        """Project files the code reads, recorded as the recipe's sources."""
        return ()

    def check_destinations(self, source: str, exports: list[str]) -> None:
        """Refuse a source or export location this tool cannot write."""

    async def inspect(
        self, project: Path, path: Path, source: str, run: Run
    ) -> dict[str, Any] | None:
        """Describe a native document; None leaves it to the generic reader."""
        return None

    def capability_programs(self) -> dict[str, str]:
        """Programs document_capabilities checks for this tool."""
        return {self.id: self.binary} if self.binary else {}

    #: Further programs (names on PATH or absolute paths) the adapter runs,
    #: which document_capabilities requires alongside `binary`.
    requires: tuple[str, ...] = ()

    def capability_requirements(self) -> dict[str, tuple[str, ...]]:
        """Everything that must be executable in the worker for this tool."""
        return {self.id: (self.binary, *self.requires)} if self.binary else {}

    def feature(self, feature_id: str) -> Feature:
        for feature in self.features:
            if feature.id == feature_id:
                return feature
        raise KeyError(feature_id)
