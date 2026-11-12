"""Console errors: read, grouped, ignored, offered, and fixed as reviewed changes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from bridge.error_fixer import GAVE_UP, HELD, ErrorFixer
from bridge.protocol import Prompt
from joinery import cli
from joinery import console_errors as ce
from joinery.debugging import DebugRequest, Diagnosis
from joinery.gateway import ProjectGateway
from tests.test_crew import CrewTest, makes, project  # noqa: F401  (fixture)

NULL = "SCRIPT ERROR: Invalid get index 'health' (on base: 'null instance')."
NULL_AT = NULL + "\n          at: _process (res://scripts/player.gd:42)\n"
OUTPUT = (
    "Godot Engine v4.7.stable\n"
    'res://scripts/enemy.gd:12 - Parse Error: Identifier "speed" not declared in the current scope.\n'
    + NULL_AT
    + 'E 0:00:03:0412   hud.gd:7 @ _ready(): Node not found: "Score" (relative to "/root/HUD").\n'
    "  <GDScript Source>hud.gd:7 @ _ready()\n"
    'WARNING: The parameter "delta" is never used.\n'
    "     at: _physics_process (res://scripts/enemy.gd:30)\n"
    "res://addons/joinery/plugin.gd:3 - Parse Error: x\n"
    "ERROR: ObjectDB instances leaked at exit (run with --verbose for details).\n"
)
GUT = (
    "res://test/unit/test_player.gd\n"
    "* test_jump_height\n"
    "    [Failed]:  Expected [3] to equal [4]\n"
    "          at line 18\n"
    "* test_walk\n"
)


def run(step: Callable[[], Any]) -> Any:
    return asyncio.run(step())


# --- reading -----------------------------------------------------------------------------


def test_console_text_is_read_into_errors_warnings_and_failed_tests() -> None:
    found = ce.parse(OUTPUT + GUT)
    shown = [(e.kind, e.message, e.where) for e in found if not ce.ignored(e)]
    assert shown == [
        (
            "error",
            'Parse Error: Identifier "speed" not declared in the current scope.',
            "res://scripts/enemy.gd:12",
        ),
        (
            "error",
            "Invalid get index 'health' (on base: 'null instance').",
            "res://scripts/player.gd:42",
        ),
        (
            "error",
            'Node not found: "Score" (relative to "/root/HUD").',
            "res://hud.gd:7",
        ),
        (
            "warning",
            'The parameter "delta" is never used.',
            "res://scripts/enemy.gd:30",
        ),
        (
            "test",
            "test_jump_height: Expected [3] to equal [4]",
            "res://test/unit/test_player.gd:18",
        ),
    ]
    # Joinery's own lines and engine noise are read but never listed.
    assert sum(1 for e in found if ce.ignored(e)) == 2
    assert all(e.context for e in found)


def test_the_same_error_groups_into_one_with_a_count(tmp_path: Path) -> None:
    book = ce.Book(tmp_path)
    for _ in range(3):
        for entry in ce.parse(NULL_AT):
            book.add(entry)
    # The same message somewhere else is another error; run-to-run ids don't split one.
    elsewhere = NULL + "\n   at: _ready (res://scripts/boss.gd:9)\n"
    book.add(ce.parse(elsewhere)[0])
    first = ce.parse("ERROR: Can't free <Node#123456789>.")[0]
    again = ce.parse("ERROR: Can't free <Node#987654321>.")[0]
    assert ce.group_id(first) == ce.group_id(again)
    groups = sorted(book.groups.values(), key=lambda g: g.where)
    assert [(g.where, g.count) for g in groups] == [
        ("res://scripts/boss.gd:9", 1),
        ("res://scripts/player.gd:42", 3),
    ]
    # Output and the game's log print the same error: it counts once per sighting.
    player = groups[1]
    for entry in ce.parse(NULL_AT, "game"):
        book.add(entry)
    assert player.count == 3 and set(player.counts) == {"output", "game"}
    assert player.fixed_title() == (
        "Fixed: Invalid get index 'health' (on base: 'null instance'). (player.gd:42)"
        " — 3 times"
    )
    book.save()
    loaded = ce.Book.load(tmp_path)
    assert loaded.groups[player.id].count == 3 and loaded.totals["seen"] == 5


def test_the_ignore_list_and_warnings_come_from_joinery_toml(tmp_path: Path) -> None:
    (tmp_path / "joinery.toml").write_text(
        "[agents]\n"
        'fix_errors = "auto"\n'
        "fix_warnings = true\n"
        'ignore_errors = ["Node not found", "*never used*"]\n'
        "fix_errors_at_once = 9\n"
    )
    settings = ce.settings(tmp_path)
    assert settings.mode == "auto" and settings.warnings and settings.at_once == 3
    kept = [e.message for e in ce.parse(OUTPUT) if not ce.ignored(e, settings.ignore)]
    assert kept == [
        'Parse Error: Identifier "speed" not declared in the current scope.',
        "Invalid get index 'health' (on base: 'null instance').",
    ]
    ce.set_ignore(tmp_path, ["Score"])
    ce.set_mode(tmp_path, "off")
    again = ce.settings(tmp_path)
    assert again.ignore == ("Score",) and again.mode == "off"
    with pytest.raises(ValueError):
        ce.set_mode(tmp_path, "sometimes")
    assert ce.settings(tmp_path / "missing").mode == "ask"


def test_a_playtests_console_items_are_entries() -> None:
    items = [
        {
            "kind": "script_error",
            "detail": "Invalid call (×2)",
            "where": "res://a.gd:3",
            "count": 2,
        },
        {"kind": "softlock", "detail": "Stuck"},
        {"kind": "warning", "detail": "Unused", "where": ""},
    ]
    found = ce.from_playtest(items)
    assert [(e.kind, e.message, e.source) for e in found] == [
        ("error", "Invalid call", "playtest"),
        ("error", "Invalid call", "playtest"),
        ("warning", "Unused", "playtest"),
    ]


# --- deciding, with a fake crew ------------------------------------------------------------


@dataclass
class FakeTask:
    id: str
    title: str
    state: str = "working"
    transaction_id: str = ""
    message: str = ""
    review_title: str = ""


@dataclass
class FakeCrew:
    """Records each debug start; the test moves its tasks along."""

    free: int = 3
    observers: list[Callable[[Any], None]] = field(default_factory=list)
    started: list[tuple[DebugRequest, FakeTask]] = field(default_factory=list)

    def capacity(self) -> int:
        return self.free

    def working(self) -> list[Any]:
        return [t for _, t in self.started if t.state == "working"]

    async def debug(
        self,
        request: DebugRequest,
        prompt: Prompt,
        *,
        title: str = "",
        at_once: int = 1,
    ) -> FakeTask:
        if len(self.working()) >= at_once:
            raise ValueError("Journeyman is already debugging an error; wait for it.")
        task = FakeTask(f"t{len(self.started) + 1}", title)
        self.started.append((request, task))
        self.tell(task)
        return task

    def tell(self, task: FakeTask, state: str = "", transaction_id: str = "") -> None:
        if state:
            task.state = state
        if transaction_id:
            task.transaction_id = transaction_id
        for observer in list(self.observers):
            observer(task)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Desk:
    """A fixer with a fake crew, a fake clock and the changes waiting for review."""

    def __init__(self, root: Path, toml: str) -> None:
        (root / "joinery.toml").write_text("[agents]\n" + toml)
        self.events: list[dict[str, Any]] = []
        self.crew = FakeCrew()
        self.clock = Clock()
        self.pending: dict[str, list[str]] = {}
        self.fixer = ErrorFixer(
            root,
            self.crew,
            self.events.append,
            lambda: Prompt(text="", project_dir=str(root)),
            pending=lambda: self.pending,
            clock=self.clock,
        )

    def lists(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["kind"] == "errors"]

    def last(self) -> dict[str, Any]:
        return self.lists()[-1]

    def group(self, where: str) -> ce.Group:
        return next(
            g for g in self.fixer.book.groups.values() if g.where.endswith(where)
        )


def error_at(place: str, message: str = "Invalid get index 'health' on null") -> str:
    return f"SCRIPT ERROR: {message}\n   at: f (res://{place})\n"


def test_ask_offers_each_new_error_once_and_starts_nothing(tmp_path: Path) -> None:
    desk = Desk(tmp_path, "")

    async def go() -> None:
        await desk.fixer.take_text("output", error_at("player.gd:42"))
        await desk.fixer.take_text("output", error_at("player.gd:42"))
        await desk.fixer.take_text("game", error_at("player.gd:42"))

    run(go)
    offers = [e["offer"] for e in desk.lists() if "offer" in e]
    assert len(offers) == 1 and offers[0]["place"] == "player.gd:42"
    assert not desk.crew.started
    (row,) = desk.last()["groups"]
    assert row["count"] == 2 and row["status"] == "open"
    assert desk.last()["totals"]["seen"] == 3


def test_auto_starts_a_fix_per_new_error_within_the_limits(tmp_path: Path) -> None:
    desk = Desk(tmp_path, 'fix_errors = "auto"\nfix_errors_cooldown = 30\n')

    async def go() -> None:
        await desk.fixer.take_text("output", error_at("player.gd:42"))
        ((request, task),) = desk.crew.started
        assert task.title == "Fixing: Invalid get index 'health' on null (player.gd:42)"
        assert "res://player.gd:42" in request.output and request.error.startswith(
            "SCRIPT"
        )
        # More of it while it is being fixed: counted, never a second fix.
        await desk.fixer.take_text("output", error_at("player.gd:42") * 2)
        assert len(desk.crew.started) == 1
        assert (
            task.review_title
            == "Fixed: Invalid get index 'health' on null (player.gd:42) — 3 times"
        )
        # A second error waits: one fix at a time by default.
        await desk.fixer.take_text("output", error_at("enemy.gd:7"))
        assert len(desk.crew.started) == 1
        desk.crew.tell(task, "in_review", "tx1")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # Its slot is free, but the pause between automatic starts isn't over.
        assert len(desk.crew.started) == 1 and desk.fixer.cooldown_left() == 30
        desk.clock.now += 31
        await desk.fixer.pump()
        assert [t.title for _, t in desk.crew.started][-1].endswith("(enemy.gd:7)")
        # The project's turns are all taken: nothing more starts.
        desk.crew.free = 0
        await desk.fixer.take_text("output", error_at("hud.gd:3"))
        desk.clock.now += 31
        await desk.fixer.pump()
        assert len(desk.crew.started) == 2

    run(go)
    statuses = {g["place"]: g["status"] for g in desk.last()["groups"]}
    assert statuses == {
        "player.gd:42": "in_review",
        "enemy.gd:7": "fixing",
        "hud.gd:3": "open",
    }


def test_a_fix_reaches_review_and_only_the_user_accepts_it(tmp_path: Path) -> None:
    desk = Desk(tmp_path, 'fix_errors = "auto"\n')

    async def go() -> None:
        await desk.fixer.take_text("output", error_at("player.gd:42"))
        ((_, task),) = desk.crew.started
        desk.crew.tell(task, "in_review", "tx1")
        assert desk.group("player.gd:42").status == "in_review"
        desk.crew.tell(task, "accepted")

    run(go)
    group = desk.group("player.gd:42")
    assert group.status == "fixed" and desk.last()["groups"] == []
    totals = ce.Book.load(tmp_path).totals
    assert (totals["fixes"], totals["ready"], totals["accepted"]) == (1, 1, 1)


def test_after_two_failed_fixes_it_stops_and_tells_the_user(tmp_path: Path) -> None:
    desk = Desk(tmp_path, 'fix_errors = "auto"\nfix_errors_cooldown = 0\n')

    async def go() -> None:
        await desk.fixer.take_text("output", error_at("player.gd:42"))
        ((_, first),) = desk.crew.started
        first.message = "The debugger couldn't find the cause."
        desk.crew.tell(first, "failed")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # Tried again on its own, once.
        assert len(desk.crew.started) == 2
        _, second = desk.crew.started[1]
        # This time the fix is accepted, but the error comes back: that fix failed too.
        desk.crew.tell(second, "in_review", "tx2")
        desk.crew.tell(second, "accepted")
        await desk.fixer.take_text("game", error_at("player.gd:42"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(desk.crew.started) == 2

    run(go)
    group = desk.group("player.gd:42")
    assert group.status == "stopped" and group.failures == 2 and group.note == GAVE_UP
    notices = [e for e in desk.events if e["kind"] == "notice"]
    assert len(notices) == 1 and "after 2 tries" in notices[0]["text"]

    # The user can still ask: Fix tries once more.
    async def ask() -> None:
        await desk.fixer.fix([group.id])

    run(ask)
    assert len(desk.crew.started) == 3 and group.status == "fixing"


def test_a_discarded_fix_is_not_started_again_on_its_own(tmp_path: Path) -> None:
    desk = Desk(tmp_path, 'fix_errors = "auto"\nfix_errors_cooldown = 0\n')

    async def go() -> None:
        await desk.fixer.take_text("output", error_at("player.gd:42"))
        ((_, task),) = desk.crew.started
        desk.crew.tell(task, "in_review", "tx1")
        desk.crew.tell(task, "discarded")
        await asyncio.sleep(0)
        await desk.fixer.take_text("output", error_at("player.gd:42"))
        await desk.fixer.pump()

    run(go)
    group = desk.group("player.gd:42")
    assert len(desk.crew.started) == 1 and group.status == "open" and group.declined
    assert group.failures == 0 and ce.Book.load(tmp_path).totals["discarded"] == 1


def test_errors_a_pending_change_brought_are_never_fixed(tmp_path: Path) -> None:
    desk = Desk(tmp_path, 'fix_errors = "auto"\nfix_errors_cooldown = 0\n')
    desk.pending = {"tx9": ["scripts/player.gd"]}

    async def go() -> None:
        # It shows while a change to its own file waits for review: that change's.
        await desk.fixer.take_text("output", error_at("scripts/player.gd:42"))
        assert not desk.crew.started
        assert desk.group("player.gd:42").status == "from_change"
        # Discarded: it leaves with the change.
        desk.fixer.decided({"transaction_id": "tx9", "accepted": False})
        assert desk.group("player.gd:42").status == "fixed"

        # An error already listed, whose file a change in review now holds, waits.
        desk.pending = {}
        await desk.fixer.take_text("output", error_at("scripts/enemy.gd:3"))
        ((_, task),) = desk.crew.started
        desk.crew.tell(task, "failed")
        desk.pending = {"tx10": ["scripts/enemy.gd"]}
        await desk.fixer.pump()
        assert len(desk.crew.started) == 1
        assert desk.group("enemy.gd:3").note == HELD
        # Accepted: it is the project's error now and waits for a fix like any other.
        desk.pending = {"tx11": ["scripts/hud.gd"]}
        await desk.fixer.take_text("output", error_at("scripts/hud.gd:5"))
        desk.pending = {}
        desk.fixer.decided({"transaction_id": "tx11", "accepted": True})
        assert desk.group("hud.gd:5").status == "open"

    run(go)


def test_fix_all_and_ignore_in_ask_mode(tmp_path: Path) -> None:
    desk = Desk(tmp_path, "fix_errors_at_once = 2\n")

    async def go() -> None:
        for place in ("a.gd:1", "b.gd:2", "c.gd:3", "d.gd:4"):
            await desk.fixer.take_text("output", error_at(place))
        desk.fixer.ignore(desk.group("d.gd:4").id)
        assert not desk.crew.started
        await desk.fixer.fix()
        # Two at once; the third waits its turn and starts when one is done.
        assert len(desk.crew.started) == 2
        assert desk.group("c.gd:3").status == "queued"
        _, first = desk.crew.started[0]
        desk.crew.tell(first, "in_review", "tx1")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(desk.crew.started) == 3

    run(go)
    statuses = [g["status"] for g in desk.last()["groups"]]
    assert statuses == ["fixing", "fixing", "in_review", "ignored"]


def test_off_watches_nothing_and_warnings_wait_for_opt_in(tmp_path: Path) -> None:
    desk = Desk(tmp_path, 'fix_errors = "off"\n')

    async def go() -> None:
        await desk.fixer.take_text("output", OUTPUT)
        assert not desk.lists() and not desk.fixer.book.groups
        ce.set_mode(tmp_path, "auto")
        await desk.fixer.take_text(
            "output", "WARNING: Unused\n   at: f (res://a.gd:1)\n"
        )
        assert not desk.fixer.book.groups
        await desk.fixer.set_mode("ask")

    run(go)
    assert desk.last()["mode"] == "ask"


def test_always_fix_errors_like_this_switches_on_auto_and_fixes_it(
    tmp_path: Path,
) -> None:
    desk = Desk(tmp_path, "")

    async def go() -> None:
        await desk.fixer.take_text("output", error_at("player.gd:42"))
        offer = next(e["offer"] for e in desk.lists() if "offer" in e)
        await desk.fixer.set_mode("auto", offer["id"])

    run(go)
    assert ce.settings(tmp_path).mode == "auto" and len(desk.crew.started) == 1


# --- end to end, with the real crew and a fake agent --------------------------------------


def test_an_automatic_fix_is_a_reviewed_change_titled_with_the_error(
    project: Path,  # noqa: F811
) -> None:
    test = CrewTest(project)
    ce.set_mode(project, "auto")

    async def diagnose(
        _lane: ProjectGateway, request: DebugRequest, _sel: Any
    ) -> Diagnosis:
        assert "player.gd:42" in request.output
        return Diagnosis(
            cause="health is read before the player is ready.",
            fix="In notes/shared.txt, write fixed.",
            targets=["res://notes/shared.txt"],
        )

    async def go() -> None:
        crew = test.crew()
        crew._debugger = diagnose
        test.steps = [makes("notes/shared.txt", "fixed\n")]
        events: list[dict[str, Any]] = []
        fixer = ErrorFixer(project, crew, events.append, test.prompt)
        await fixer.take_text("output", error_at("player.gd:42") * 3)
        (task,) = crew.tasks.values()
        assert task.job is not None
        await task.job
        assert task.state == "in_review"
        assert task.title == (
            "Fixed: Invalid get index 'health' on null (player.gd:42) — 3 times"
        )
        card = test.tasks("in_review")[-1]
        assert card["title"] == task.title and card["origin"] == "debug"
        # Nothing is accepted for the user.
        assert (project / "notes/shared.txt").read_text() == "before\n"
        await crew.decide(task.transaction_id, True)
        await crew.close()
        await test.gateway.close()
        rows = [e for e in events if e["kind"] == "errors"]
        assert rows[-1]["groups"] == [] and rows[-1]["totals"]["accepted"] == 1

    run(go)
    assert (project / "notes/shared.txt").read_text() == "fixed\n"


def test_the_cli_reads_and_sets_the_mode_and_metrics_count_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(["fix-errors", "--project", str(tmp_path), "--json", "--set", "auto"])
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["fix_errors"] == "auto" and data["auto"] is True
    book = ce.Book(tmp_path)
    book.add(ce.parse(NULL_AT)[0])
    book.totals["accepted"] = 1
    book.save()
    assert cli.main(["metrics", "--project", str(tmp_path), "--errors", "--json"]) == 0
    counts = json.loads(capsys.readouterr().out)["errors"]
    assert counts["seen"] == 1 and counts["accepted"] == 1
    assert cli.main(["metrics", "--project", str(tmp_path), "--errors"]) == 0
    assert "Errors seen: 1 (1 distinct)" in capsys.readouterr().out
