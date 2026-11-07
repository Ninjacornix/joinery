"""The agent-neutral contract between the Journeyman sidebar and a coding agent.

Joinery's sidebar must speak to whichever agent the user runs — Claude Code,
Codex, Gemini CLI or opencode — without caring which. It does that by talking
this one protocol to a `bridge.Bridge`; a per-agent adapter (`bridge.adapters`)
translates to and from the agent's own interface. The messages here are the
stable surface the sidebar codes against.

Only the message schema and direction live here, so both the sidebar (Godot
side, over a local socket) and the host (Python side) agree on the wire format.
The transport is in `bridge.host`; the agent-specific translation is in
`bridge.adapters`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Msg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---- sidebar -> agent -------------------------------------------------------


class Prompt(_Msg):
    """A user instruction, with the editor targets it refers to (chips)."""

    kind: Literal["prompt"] = "prompt"
    text: str
    # Resolved selection chips, e.g. "@hero > Coat", "@Door.material".
    targets: tuple[str, ...] = ()
    # The game project this turn operates on; the gate reads its joinery.toml.
    project_dir: str


class Decision(_Msg):
    """The user's accept/reject on a proposed before/after transaction."""

    kind: Literal["decision"] = "decision"
    transaction_id: str
    accept: bool


class Cancel(_Msg):
    """Stop the current turn."""

    kind: Literal["cancel"] = "cancel"


SidebarMessage = Prompt | Decision | Cancel


# ---- agent -> sidebar -------------------------------------------------------


class Step(_Msg):
    """One planned or in-progress step, shown in the sidebar step list."""

    kind: Literal["step"] = "step"
    text: str
    tool: str = ""  # Owning tool, e.g. "blender", if this step drives one.
    status: Literal["planned", "running", "done", "failed"] = "planned"


class ToolActivity(_Msg):
    """A tool call the agent is about to make, surfaced for transparency.

    `allowed` mirrors the creation-only gate's verdict, so the sidebar can show
    a blocked call and its reason rather than failing silently.
    """

    kind: Literal["tool_activity"] = "tool_activity"
    server: str
    tool: str
    allowed: bool = True
    reason: str = ""


class Preview(_Msg):
    """A before/after artifact for review: image path, clip, or audio A/B."""

    kind: Literal["preview"] = "preview"
    transaction_id: str
    before: str  # Path/URI to the "before" evidence.
    after: str  # Path/URI to the "after" evidence.
    media: Literal["image", "clip", "audio", "scene"] = "image"


class Message(_Msg):
    """Free-text from the agent to the user (explanations, honest refusals)."""

    kind: Literal["message"] = "message"
    text: str
    level: Literal["info", "warn", "refuse"] = "info"


class Done(_Msg):
    """The turn finished."""

    kind: Literal["done"] = "done"
    ok: bool = True


AgentEvent = Step | ToolActivity | Preview | Message | Done


class Envelope(_Msg):
    """What actually crosses the socket: a session id plus one message."""

    session_id: str
    body: SidebarMessage | AgentEvent = Field(discriminator="kind")
