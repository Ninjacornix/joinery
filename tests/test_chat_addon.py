"""The chat refuses an add-on older than Joinery with the fix, not a command line."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery import cli


def test_chat_offers_the_add_on_update_to_an_older_add_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["chat", "--project", str(tmp_path), "--agent", "codex"]
    assert cli.main([*argv, "--addon-protocol", "1"]) == 1
    event = json.loads(capsys.readouterr().out)
    assert event["state"] == "error"
    # The sidebar recognises this sentence and shows Update and restart.
    assert event["message"].startswith(cli.ADDON_STALE)
    assert "<project>" not in event["message"]
    assert "joinery " not in event["message"]
    assert event["action"] == {"id": "update_addon", "label": "Update and restart"}
