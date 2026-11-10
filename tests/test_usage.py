"""Token use as each provider reports it, turned into the sidebar's usage events."""

from __future__ import annotations

from bridge.acp_session import ACPUsage
from bridge.claude_session import ClaudeUsage
from bridge.codex_session import token_usage
from bridge.protocol import Usage


def test_codex_thread_token_usage() -> None:
    usage = token_usage(
        {
            "last": {
                "inputTokens": 30_000,
                "cachedInputTokens": 20_000,
                "outputTokens": 1_200,
                "reasoningOutputTokens": 300,
                "totalTokens": 31_200,
            },
            "total": {
                "inputTokens": 90_000,
                "cachedInputTokens": 60_000,
                "outputTokens": 4_000,
                "reasoningOutputTokens": 900,
                "totalTokens": 94_000,
            },
            "modelContextWindow": 272_000,
        },
        "gpt-test",
    )
    assert usage == Usage(
        model="gpt-test",
        context_used=31_200,
        context_window=272_000,
        input_tokens=90_000,
        output_tokens=4_000,
        cached_tokens=60_000,
    )
    unknown = token_usage({"last": {}, "total": {}, "modelContextWindow": None})
    assert unknown is not None and unknown.context_window == 0
    assert token_usage(None) is None


def test_claude_stream_json_usage() -> None:
    usage = ClaudeUsage()
    assert (
        usage.read({"type": "system", "subtype": "init", "model": "claude-x"}) is None
    )
    assistant = {
        "type": "assistant",
        "message": {
            "model": "claude-x",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 2_000,
                "cache_read_input_tokens": 18_000,
                "output_tokens": 500,
            },
        },
    }
    assert usage.read(assistant) is None
    result = {
        "type": "result",
        "subtype": "success",
        "usage": {
            "input_tokens": 40,
            "cache_creation_input_tokens": 2_000,
            "cache_read_input_tokens": 36_000,
            "output_tokens": 900,
        },
        "modelUsage": {
            "claude-x": {"outputTokens": 900, "contextWindow": 200_000},
            "claude-small": {"outputTokens": 20, "contextWindow": 100_000},
        },
    }
    first = usage.read(result)
    assert first == Usage(
        model="claude-x",
        context_used=20_510,
        context_window=200_000,
        input_tokens=38_040,
        output_tokens=900,
        cached_tokens=36_000,
    )
    second = usage.read(result)
    assert second is not None and second.output_tokens == 1_800
    assert ClaudeUsage().read({"type": "result", "subtype": "success"}) is None


def test_acp_usage_when_reported_and_nothing_otherwise() -> None:
    usage = ACPUsage()
    assert usage.turn(None) is None
    assert usage.context({}) is None
    context = usage.context(
        {"sessionUpdate": "usage_update", "used": 5_000, "size": 1_000_000}
    )
    assert context is not None
    assert (context.context_used, context.context_window) == (5_000, 1_000_000)
    turn = usage.turn(
        {
            "inputTokens": 4_000,
            "cachedReadTokens": 1_000,
            "outputTokens": 200,
            "thoughtTokens": 50,
            "totalTokens": 5_250,
        }
    )
    assert turn is not None
    assert (turn.input_tokens, turn.output_tokens, turn.cached_tokens) == (
        5_000,
        250,
        1_000,
    )
    assert turn.context_used == 5_000
    only_turn = ACPUsage().turn(
        {"inputTokens": 100, "outputTokens": 5, "totalTokens": 105}
    )
    assert only_turn is not None and only_turn.context_used == 105
