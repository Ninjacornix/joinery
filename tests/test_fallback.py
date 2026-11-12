"""Which errors mean a provider ran out of quota, and what the next agent is told."""

from __future__ import annotations

from pathlib import Path

import pytest

from joinery.fallback import candidates, continue_text, is_quota, setting, staged_lines


@pytest.mark.parametrize(
    "text",
    [
        "You've hit your usage limit. Try again at 4:12 PM.",
        "429 Too Many Requests",
        "RESOURCE_EXHAUSTED: Quota exceeded for quota metric",
        "Rate limit reached for requests",
        "Your credit balance is too low",
    ],
)
def test_quota_words_are_recognised(text: str) -> None:
    assert is_quota(text)


@pytest.mark.parametrize(
    "text", ["", "The scene could not be saved.", "Permission denied", "Unauthorized"]
)
def test_other_errors_are_not_quota(text: str) -> None:
    assert not is_quota(text)


def test_the_setting_names_a_known_agent_only(tmp_path: Path) -> None:
    assert setting(tmp_path) == ""
    toml = tmp_path / "joinery.toml"
    toml.write_text('[agents]\nfallback_provider = "claude"\n')
    assert setting(tmp_path) == "claude"
    toml.write_text('[agents]\nfallback_provider = "someone-else"\n')
    assert setting(tmp_path) == ""
    toml.write_text("[agents\n")
    assert setting(tmp_path) == ""


def test_candidates_leave_out_the_current_agent_and_the_signed_out() -> None:
    found = candidates("codex", lambda agent: agent in {"codex", "gemini"})
    assert [c["id"] for c in found] == ["gemini"]


def test_the_next_agent_hears_the_request_and_the_staged_files() -> None:
    changes = [
        {"path": "a.gd", "before": "", "after": "h1"},
        {"path": "b.tscn", "before": "h2", "after": "h3"},
        {"path": "c.png", "before": "h4", "after": ""},
    ]
    text = continue_text("Codex", " Make an ashtray ", "Earlier: a table.", changes)
    assert text.startswith("Codex ran out of quota")
    assert "The user's request: Make an ashtray" in text
    assert "- a.gd (new)" in text and "- b.tscn (changed)" in text
    assert "- c.png (removed)" in text and "Earlier: a table." in text
    assert staged_lines([]) == "Nothing is staged yet."
    many = staged_lines([{"path": f"f{i}", "after": "x"} for i in range(45)])
    assert many.endswith("and 5 more files")
