"""Model availability, turn overrides and privacy of performance evidence."""

import asyncio
import json
from pathlib import Path

import pytest

from bridge.acp_session import ACPSession
from bridge.codex_session import CodexSession
from bridge.models import ModelChoice, Selection, route
from bridge.protocol import Prompt
from joinery.timing import TurnTiming

CHOICES = [
    ModelChoice(
        id="available-sol",
        name="Strong",
        efforts=("low", "medium", "high"),
        default_effort="medium",
        is_default=True,
    ),
    ModelChoice(
        id="available-luna",
        name="Quick",
        efforts=("low", "high"),
        default_effort="high",
    ),
]


@pytest.mark.parametrize(
    "text,model,effort,mode",
    [
        ("hello", "available-luna", "low", "fast"),
        ("rename this box", "available-luna", "low", "fast"),
        ("move the selected object 2 metres", "available-luna", "low", "fast"),
        ("create a low poly hammer", "available-sol", "high", "thorough"),
        ("debug this script", "available-sol", "high", "thorough"),
        ("delete everything and rebuild it", "available-sol", "medium", "auto"),
        ("make this better", "available-sol", "medium", "auto"),
    ],
)
def test_auto_routes_only_recognized_simple_requests_to_the_discovered_fast_model(
    text, model, effort, mode
):
    choice, selected_effort, selected_mode = route(Selection(), text, CHOICES)
    assert (choice.id, selected_effort, selected_mode) == (model, effort, mode)


def test_explicit_model_and_effort_override_automatic_routing():
    choice, effort, _ = route(
        Selection(model="available-sol", effort="high", mode="fast"), "hello", CHOICES
    )
    assert choice.id == "available-sol" and effort == "high"


@pytest.mark.parametrize(
    "selection",
    [
        Selection(model="not-entitled"),
        Selection(model="available-luna", effort="ultra"),
    ],
)
def test_stale_or_unsupported_overrides_never_silently_switch_models(selection):
    with pytest.raises(ValueError):
        route(selection, "hello", CHOICES)


def test_single_model_endpoints_and_missing_inventory_keep_provider_defaults():
    for choices in [[], [ModelChoice(id="custom", name="Custom", is_default=True)]]:
        choice, effort, _ = route(Selection(mode="fast"), "hello", choices)
        assert choice.id == ("custom" if choices else "") and effort == ""


def test_routing_preserves_original_prompt_and_selection_snapshot():
    prompt = Prompt(
        text="rename this", project_dir="/fixture", selection=Selection(mode="fast")
    )
    choice, effort, _ = route(prompt.selection, prompt.text, CHOICES)
    routed = prompt.model_copy(update={"model": choice.id, "effort": effort})
    assert prompt.model == "" and routed.selection == prompt.selection


def test_codex_models_read_all_pages_and_exclude_hidden_duplicates(monkeypatch):
    session = CodexSession()
    pages = iter(
        [
            {
                "data": [
                    {
                        "model": "one",
                        "displayName": "One",
                        "isDefault": True,
                        "supportedReasoningEfforts": [{"reasoningEffort": "low"}],
                    }
                ],
                "nextCursor": "second",
            },
            {
                "data": [
                    {"model": "hidden", "hidden": True},
                    {"model": "one"},
                    {"model": "two"},
                ]
            },
        ]
    )
    calls = []

    async def request(method, args):
        calls.append((method, args))
        return next(pages)

    monkeypatch.setattr(session, "request", request)
    models = asyncio.run(session.models())
    assert [m.id for m in models] == ["one", "two"]
    assert models[0].efforts == ("low",) and models[0].is_default
    assert calls[1][1]["cursor"] == "second"


def test_codex_custom_api_profile_never_substitutes_subscription_catalog(monkeypatch):
    session = CodexSession()
    session.profile_model = "verified-endpoint-model"

    async def forbidden(*_):
        pytest.fail("Custom profile must keep its verified endpoint model")

    monkeypatch.setattr(session, "request", forbidden)
    assert asyncio.run(session.models())[0].id == "verified-endpoint-model"


def test_timing_records_monotonic_spans_first_response_and_bounds_history(
    tmp_path: Path, monkeypatch
):
    clock = [10.0]
    monkeypatch.setattr("joinery.timing.time.monotonic", lambda: clock[0])
    trace = TurnTiming()
    clock[0] = 10.25
    trace.response()
    with trace.span("tools"):
        clock[0] = 10.5
    trace.response()
    report = trace.save(tmp_path, "done")
    assert report["total_ms"] == 500 and report["first_response_ms"] == 250
    assert report["phases_ms"] == {"tools": 250}
    assert set(report) == {
        "kind",
        "id",
        "outcome",
        "model",
        "effort",
        "mode",
        "total_ms",
        "first_response_ms",
        "phases_ms",
    }
    for _ in range(205):
        trace.save(tmp_path, "cancelled")
    history = (tmp_path / ".joinery/latency.jsonl").read_text().splitlines()
    assert len(history) == 200 and json.loads(history[-1])["outcome"] == "cancelled"


def test_codex_turn_model_override_does_not_restart_or_replace_the_thread(monkeypatch):
    session = CodexSession()
    session.thread_id = "existing-thread"
    calls = []

    async def request(method, params):
        calls.append((method, params))
        await session._events.put(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "existing-thread",
                    "turn": {"id": "turn-one", "status": "completed"},
                },
            }
        )
        return {"turn": {"id": "turn-one"}}

    monkeypatch.setattr(session, "request", request)

    async def run():
        return [
            event
            async for event in session.send(
                Prompt(
                    text="hello",
                    project_dir="/fixture",
                    model="available-luna",
                    effort="low",
                )
            )
        ]

    assert asyncio.run(run())[-1].ok
    assert len(calls) == 1 and calls[0][0] == "turn/start"
    assert calls[0][1]["threadId"] == "existing-thread"
    assert calls[0][1]["model"] == "available-luna" and calls[0][1]["effort"] == "low"


def acp_config(model="quick", effort="medium", levels=("low", "medium", "high")):
    return [
        {
            "id": "provider-model",
            "category": "model",
            "type": "select",
            "currentValue": model,
            "options": [
                {
                    "group": "available",
                    "options": [
                        {"value": "quick", "name": "Quick"},
                        {"value": "strong", "name": "Strong"},
                    ],
                }
            ],
        },
        {
            "id": "provider-thinking",
            "category": "thought_level",
            "type": "select",
            "currentValue": effort,
            "options": [{"value": level, "name": level} for level in levels],
        },
    ]


def test_acp_preferred_config_models_and_model_dependent_efforts_are_revalidated(
    monkeypatch,
):
    session = ACPSession("gemini")
    session.session_id = "existing"
    session.load_config(acp_config())
    assert [m.id for m in session.model_choices] == ["quick", "strong"]
    calls = []

    async def request(method, params, *args):
        calls.append((method, params))
        if method == "session/set_config_option":
            # Changing the model removes low effort; automatic routing must use
            # the newly reported levels instead of sending the stale value.
            return {"configOptions": acp_config("strong", "high", ("high",))}
        return {"stopReason": "end_turn"}

    monkeypatch.setattr(session, "request", request)

    async def run():
        return [
            event
            async for event in session.send(
                Prompt(
                    text="hello",
                    project_dir="/fixture",
                    model="strong",
                    effort="low",
                    selection=Selection(mode="fast", model="strong"),
                )
            )
        ]

    events = asyncio.run(run())
    assert events[-1].ok
    assert [m for m, _ in calls] == ["session/set_config_option", "session/prompt"]
    assert next(e for e in events if e.kind == "execution").effort == "high"
    assert calls[0][1] == {
        "sessionId": "existing",
        "configId": "provider-model",
        "value": "strong",
    }


def test_acp_rejects_explicit_effort_if_the_model_change_removes_it(monkeypatch):
    session = ACPSession("opencode")
    session.session_id = "existing"
    session.load_config(acp_config())

    async def request(method, params, *args):
        assert method == "session/set_config_option"
        return {"configOptions": acp_config("strong", "high", ("high",))}

    monkeypatch.setattr(session, "request", request)

    async def run():
        return [
            event
            async for event in session.send(
                Prompt(
                    text="hello",
                    project_dir="/fixture",
                    model="strong",
                    selection=Selection(model="strong", effort="low"),
                )
            )
        ]

    with pytest.raises(ValueError, match="reasoning effort"):
        asyncio.run(run())


def test_latency_evidence_refuses_symlinks_outside_the_project(tmp_path: Path):
    project = tmp_path / "project"
    (project / ".joinery").mkdir(parents=True)
    protected = tmp_path / "existing.txt"
    protected.write_text("untouched")
    (project / ".joinery/latency.jsonl").symlink_to(protected)
    with pytest.raises(ValueError):
        TurnTiming().save(project, "done")
    assert protected.read_text() == "untouched"


def test_acp_api_profile_filters_other_configured_provider_models():
    session = ACPSession("opencode")
    session.profile_model = "verified-model"
    session.load_config(
        [
            {
                "id": "model",
                "category": "model",
                "type": "select",
                "currentValue": "joinery/verified-model",
                "options": [
                    {"value": "joinery/verified-model", "name": "Verified API model"},
                    {"value": "other-provider/model", "name": "Other account"},
                ],
            }
        ]
    )
    assert [choice.id for choice in session.model_choices] == ["joinery/verified-model"]
