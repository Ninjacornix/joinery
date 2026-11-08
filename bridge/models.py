"""Account-discovered models and deterministic, provider-neutral turn routing."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ModelChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    efforts: tuple[str, ...] = ()
    default_effort: str = ""
    is_default: bool = False


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["auto", "fast", "thorough"] = "auto"
    model: str = ""
    effort: str = ""


def route(
    selection: Selection, text: str, choices: list[ModelChoice]
) -> tuple[ModelChoice, str, str]:
    """Never invent a model or effort absent from the connected provider catalog.

    Auto only treats short, single-purpose edits/questions as fast. Ambiguous
    requests retain the provider default; creative and multi-step work is thorough.
    Model family hints rank *discovered* choices, never establish entitlement.
    """
    default = next((c for c in choices if c.is_default), None)
    default = default or (
        choices[0] if choices else ModelChoice(id="", name="Provider default")
    )
    mode = selection.mode
    if mode == "auto":
        words = text.lower().split()
        if (
            re.search(
                r"\b(debug|implement|design|model|hammer|texture|rig|animate|material|compose|refactor|sculpt)\b",
                text.lower(),
            )
            or len(words) > 45
        ):
            mode = "thorough"
        elif (
            len(words) <= 18
            and re.match(
                r"^(hello|hi\b|hey\b|what\b|where\b|list\b|show\b|rename\b|move\b|delete\b|remove\b|rotate\b|scale\b|duplicate\b)",
                text.lower(),
            )
            and not re.search(r"\b(and|then|all|every)\b", text.lower())
        ):
            mode = "fast"
    explicit = next((c for c in choices if c.id == selection.model), None)
    if selection.model and explicit is None:
        raise ValueError(
            "The selected model is no longer in this provider's model list. Choose Automatic or refresh the connection."
        )
    model = explicit or default
    if explicit is None and mode == "fast":
        model = next(
            (
                c
                for c in choices
                if re.search(r"luna|mini|flash|haiku|small", c.id, re.IGNORECASE)
            ),
            default,
        )
    elif explicit is None and mode == "thorough":
        model = next(
            (
                c
                for c in choices
                if re.search(r"sol|opus|pro(?!vider)", c.id, re.IGNORECASE)
            ),
            default,
        )
    effort = selection.effort or (
        model.default_effort if model.default_effort in model.efforts else ""
    )
    if selection.effort and selection.effort not in model.efforts:
        raise ValueError("This model does not support the selected reasoning effort.")
    if not selection.effort and model.efforts:
        order = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
        supported = [e for e in order if e in model.efforts]
        if supported and mode == "fast":
            effort = supported[0]
        elif mode == "thorough":
            effort = (
                "high"
                if "high" in supported
                else supported[-1]
                if supported
                else effort
            )
    return model, effort, mode
