"""Unhurried runs (ADR 0072): the "when idle" queue, with a fake clock and fake runs.

No agent runs here. None of the four agent CLIs exposes a provider batch API, so a Fix or
Check may wait in a queue that starts it in off-peak hours once the editor is quiet, on
the light model when the project has one.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from bridge.idle_queue import IdleQueue
from joinery import cli, idle_runs, metrics
from joinery.project import TOUR_PENDING, mark_tour_pending


def toml(project: Path, text: str) -> Path:
    (project / "joinery.toml").write_text("[agents]\n" + text)
    return project


# --- Settings ------------------------------------------------------------------------


def test_runs_go_at_once_unless_the_project_or_the_run_asks_to_wait(
    tmp_path: Path,
) -> None:
    assert idle_runs.settings(tmp_path) == idle_runs.IdleSettings(
        "now", "22:00-07:00", 15
    )
    assert not idle_runs.wanted(tmp_path, {"kind": "match_fix"})
    assert idle_runs.wanted(tmp_path, {"kind": "match_fix", "when": "idle"})
    toml(tmp_path, 'unhurried = "idle"\nidle_hours = "01:00-05:30"\nidle_minutes = 5\n')
    assert idle_runs.settings(tmp_path) == idle_runs.IdleSettings(
        "idle", "01:00-05:30", 5
    )
    assert idle_runs.wanted(tmp_path, {"kind": "match_fix"})
    assert not idle_runs.wanted(tmp_path, {"kind": "match_fix", "when": "now"})
    toml(
        tmp_path, 'unhurried = "later"\nidle_hours = "25:00-07:00"\nidle_minutes = -1\n'
    )
    assert idle_runs.settings(tmp_path) == idle_runs.IdleSettings()


def test_off_peak_hours_wrap_past_midnight() -> None:
    def at(hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 11, 12, hour, minute)

    assert idle_runs.in_hours(at(23), "22:00-07:00")
    assert idle_runs.in_hours(at(3), "22:00-07:00")
    assert not idle_runs.in_hours(at(7), "22:00-07:00")
    assert not idle_runs.in_hours(at(12), "22:00-07:00")
    assert idle_runs.in_hours(at(1, 30), "01:00-05:30")
    assert not idle_runs.in_hours(at(5, 30), "01:00-05:30")
    assert idle_runs.in_hours(at(12), "")
    config = idle_runs.IdleSettings("idle", "22:00-07:00", 15)
    assert idle_runs.due(config, at(23), 15 * 60)
    assert not idle_runs.due(config, at(23), 14 * 60)
    assert not idle_runs.due(config, at(12), 3600)


def test_no_agent_cli_reaches_a_batch_api_and_each_finding_says_how_it_was_found() -> (
    None
):
    assert set(idle_runs.BATCH_SUPPORT) == {"claude", "codex", "gemini", "opencode"}
    assert {row["batch"] for row in idle_runs.BATCH_SUPPORT.values()} == {"no"}
    assert idle_runs.BATCH_SUPPORT["claude"]["how"] == "verified"
    assert idle_runs.BATCH_SUPPORT["gemini"]["how"] == "assumed"


# --- The queue -----------------------------------------------------------------------


def test_the_queue_keeps_one_run_of_each_kind_and_survives_a_restart(
    tmp_path: Path,
) -> None:
    first = idle_runs.add(
        tmp_path, "match", {"areas": ["palette"], "when": "idle"}, now=10
    )
    assert first.data == {"areas": ["palette"]}
    idle_runs.add(tmp_path, "access", {"check": "access"}, now=11)
    newer = idle_runs.add(tmp_path, "match", {"areas": ["scale"]}, now=12)
    waiting = idle_runs.queued(tmp_path)
    assert [(j.kind, j.data.get("areas")) for j in waiting] == [
        ("access", None),
        ("match", ["scale"]),
    ]
    taken = idle_runs.take(tmp_path, now=20)
    assert taken is not None and taken.kind == "access" and taken.started_at == 20
    assert [j.id for j in idle_runs.queued(tmp_path)] == [newer.id]
    assert idle_runs.remove(tmp_path, newer.id) == 1
    assert idle_runs.queued(tmp_path) == [] and idle_runs.take(tmp_path) is None
    with pytest.raises(ValueError, match="one of"):
        idle_runs.add(tmp_path, "benchmark", {})
    # The started run is kept for the record.
    assert [j.state for j in idle_runs.jobs(tmp_path)] == ["started"]


def test_a_started_run_is_measured_with_its_wait_and_model(tmp_path: Path) -> None:
    idle_runs.add(tmp_path, "perf", {"check": "perf"}, now=100)
    job = idle_runs.take(tmp_path, now=4000)
    assert job is not None
    idle_runs.record(tmp_path, job, "haiku")
    (summary,) = metrics.changes(tmp_path)
    assert summary["idle"] == "perf" and summary["outcome"] == "started"
    spans = metrics.read(next(metrics.metrics_dir(tmp_path).glob("*.jsonl")))
    assert spans[0]["attributes"][metrics.IDLE_WAITED_S] == 3900
    assert spans[0]["attributes"][metrics.MODEL_TIER] == "light"
    assert "ran when idle: perf" in metrics.render([summary])


# --- The host's queue ----------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.seconds = 0.0
        self.when = datetime(2026, 11, 12, 23, 0)

    def __call__(self) -> float:
        return self.seconds


def host(
    project: Path, *, busy: bool = False, light: str = "haiku"
) -> tuple[IdleQueue, list[dict[str, Any]], list[tuple[str, str]], Clock]:
    events: list[dict[str, Any]] = []
    started: list[tuple[str, str]] = []
    clock = Clock()

    def dispatch(job: idle_runs.Job, model: str) -> None:
        if job.kind == "design" and job.data.get("break"):
            raise ValueError("The design check is already running.")
        started.append((job.kind, model))

    queue = IdleQueue(
        project,
        events.append,
        dispatch,
        lambda: busy,
        lambda: light,
        clock=clock,
        now=lambda: clock.when,
    )
    return queue, events, started, clock


def test_a_waiting_run_starts_off_peak_once_the_editor_is_quiet_on_the_light_model(
    tmp_path: Path,
) -> None:
    toml(tmp_path, 'unhurried = "idle"\n')
    queue, events, started, clock = host(tmp_path)
    queue.queue("match", {"kind": "match_fix", "areas": []})
    (notice,) = [e for e in events if e["kind"] == "notice"]
    assert notice["text"].startswith(
        "Make it match waits for when you're away: it runs between 22:00 and 07:00"
    )
    assert "on the light model (haiku)" in notice["text"]
    assert events[-1] == {
        "kind": "idle_queue",
        "jobs": [idle_runs.queued(tmp_path)[0].event()],
    }
    assert queue.tick() is None  # The user was here a moment ago.
    clock.seconds = 14 * 60
    assert queue.tick() is None
    clock.seconds = 15 * 60
    clock.when = datetime(2026, 11, 12, 12, 0)
    assert queue.tick() is None  # Quiet, but not off-peak.
    clock.when = datetime(2026, 11, 13, 2, 0)
    job = queue.tick()
    assert job is not None and started == [("match", "haiku")]
    assert idle_runs.queued(tmp_path) == []
    # The user comes back: the quiet time starts again.
    idle_runs.add(tmp_path, "access", {"check": "access"})
    queue.touch()
    assert queue.tick() is None


def test_nothing_starts_while_anything_works_and_now_starts_everything(
    tmp_path: Path,
) -> None:
    queue, events, started, clock = host(tmp_path, busy=True, light="")
    queue.queue("access", {"check": "access"})
    queue.queue("design", {"break": True})
    clock.seconds = 3600
    assert queue.tick() is None
    assert queue.run_now() == 2
    # No light model: the chat's own model ("").
    assert started == [("access", "")]
    assert any("couldn't start" in e.get("text", "") for e in events)
    assert "Nothing is waiting" in queue.status()
    queue.queue("perf", {"check": "perf"})
    assert "Performance fixes" in queue.status()
    assert queue.clear() == 1


def test_a_waiting_run_settles_its_card() -> None:
    assert idle_runs.settled("match")["state"] == "done"
    assert "/idle now" in idle_runs.settled("perf")["message"]
    assert idle_runs.settled("design") == {
        "kind": "design",
        "state": "stopped",
        "origin": "idle",
    }


# --- CLI -----------------------------------------------------------------------------


def test_the_cli_lists_adds_removes_and_reports_batch_support(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = str(tmp_path)
    assert cli.main(["idle", "--project", project]) == 0
    assert "Nothing is waiting." in capsys.readouterr().out
    assert (
        cli.main(["idle", "--project", project, "add", "perf", "--areas", "fps"]) == 0
    )
    assert "Queued Performance fixes" in capsys.readouterr().out
    (job,) = idle_runs.queued(tmp_path)
    assert job.data == {"areas": ["fps"], "check": "perf"}
    assert cli.main(["idle", "--project", project, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["kind"] == "perf"
    assert cli.main(["idle", "--project", project, "add", "benchmark"]) == 2
    capsys.readouterr()
    assert cli.main(["idle", "--project", project, "clear"]) == 0
    assert "Removed 1 waiting run." in capsys.readouterr().out
    assert cli.main(["idle", "--project", project, "batch", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["codex"]["batch"] == "no"


# --- The tour marker -----------------------------------------------------------------


def test_a_new_project_asks_the_editor_for_the_tour_once(tmp_path: Path) -> None:
    mark_tour_pending(tmp_path)
    assert (tmp_path / TOUR_PENDING).is_file()
    path = tmp_path / "Lighthouse"
    assert cli.main(["new", str(path), "--name", "Lighthouse", "--tools", "godot"]) == 0
    assert (path / TOUR_PENDING).is_file()
