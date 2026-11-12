"""The agent-neutral contract between the Journeyman sidebar and a coding agent.

Joinery's sidebar must speak to whichever agent the user runs — Claude Code,
Codex, Gemini CLI or opencode — without caring which. It does that by talking
this one protocol to a `bridge.Bridge`; a per-agent adapter (`bridge.adapters`)
translates to and from the agent's own interface. The messages here are the
stable surface the sidebar codes against.

Only the message schema and direction live here, so both the sidebar (Godot
side, over framed stdio) and the host (Python side) agree on the wire format.
The transport is in `bridge.stdio`; the agent-specific translation is in
`bridge.adapters`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bridge.models import ModelChoice, Selection


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
    target_context: tuple[dict[str, str], ...] = ()
    editor_context: dict[str, str] = {}
    selection: Selection = Selection()
    model: str = ""
    effort: str = ""
    # Images the user attached (absolute paths under .joinery/attachments): context only.
    attachments: tuple[str, ...] = ()
    # A summary of the conversation so far, on the first prompt of a fresh session.
    primer: str = ""


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


class MessageDelta(_Msg):
    kind: Literal["message_delta"] = "message_delta"
    text: str
    new_message: bool = False


class Execution(_Msg):
    kind: Literal["execution"] = "execution"
    model: str
    name: str
    effort: str = ""
    mode: str


class Models(_Msg):
    kind: Literal["models"] = "models"
    models: list[ModelChoice]


class Usage(_Msg):
    """Token use as the provider reports it; 0 means not reported.

    `context_used` is what the conversation holds now and `context_window` the
    model's limit. The token counts are this session's totals: `input_tokens`
    includes the `cached_tokens` read from the provider's cache.
    """

    kind: Literal["usage"] = "usage"
    model: str = ""
    context_used: int = 0
    context_window: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


class EditorAction(_Msg):
    kind: Literal["editor_action"] = "editor_action"
    request_id: str
    payload: dict[str, object]
    context: dict[str, str]
    targets: tuple[str, ...]
    #: The parallel task making this live edit ("" for the chat's own request).
    owner: str = ""
    #: Files the edit loads that only the turn's staged change has: project path
    #: (res://…) to the stage's loadable copy (absolute), so a model made in this
    #: change is placed in the same change (ADR 0055).
    staged: dict[str, str] = Field(default_factory=dict)


AgentEvent = (
    Step
    | ToolActivity
    | Preview
    | Message
    | MessageDelta
    | EditorAction
    | Execution
    | Models
    | Usage
    | Done
)


class Envelope(_Msg):
    """What actually crosses the socket: a session id plus one message."""

    session_id: str
    body: SidebarMessage | AgentEvent = Field(discriminator="kind")
