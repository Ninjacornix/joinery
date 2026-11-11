"""Variants side by side: one request made several ways, one kept (ADR 0042)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from bridge.crew import Crew
from bridge.protocol import Done, Message, Prompt
from bridge.variants import (
    LEAD,
    Variants,
    directions_for,
    kind_of,
    parse_command,
)
from joinery import timeline
from joinery.claims import MAIN, PathClaims
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import variants_request
from joinery.roles import registry_allowed
from joinery.turn import TurnController
from tests.test_crew import CrewTest, Endpoint, Step, project, run, wire, write

__all__ = ["project"]


class Keyed:
    """A fake provider: the step whose key its prompt names runs on its lane."""

    def __init__(self, test: VariantsTest, _agent: str) -> None:
        self.test = test
        self.lane: ProjectGateway | None = None

    def bind_gateway(self, path: Any) -> None:
        self.lane = path

    async def connect(self, _project: Path, _profile: str = "") -> None:
        return None

    async def send(self, prompt: Prompt) -> AsyncIterator[Any]:
        assert self.lane is not None
        self.test.prompts.append(prompt)
        key = next(k for k in self.test.keyed if k in prompt.text)
        async for event in self.test.keyed[key](wire(self.lane)):
            yield event

    def editor_result(self, _id: str, _result: dict[str, Any]) -> None:
        return None

    async def cancel(self) -> None:
        return None

    async def close(self) -> None:
        return None


class VariantsTest(CrewTest):
    def __init__(self, project: Path, limit: int = 5, lead_busy: bool = False) -> None:
        super().__init__(project, limit)
        self.keyed: dict[str, Step] = {}
        self.lead_busy = lead_busy
        self.reviewers: list[str] = []

    def crew(self) -> Crew:
        async def on_stop(reason: str) -> None:
            self.stops.append(reason)

        def endpoint(lane: ProjectGateway, stop: Any) -> Endpoint:
            return Endpoint(lane, stop)

        def reviewer(lane: ProjectGateway, _turn: TurnController) -> None:
            self.reviewers.append(lane.task_id)

        return Crew(
            self.gateway,
            self.events.append,
            agent="codex",
            profile="",
            limit=self.limit,
            bridge=lambda agent: Keyed(self, agent),
            endpoint=endpoint,
            on_stop=on_stop,
            reviewer=reviewer,
            lead_busy=lambda: self.lead_busy,
        )

    def groups(self, state: str) -> list[dict[str, Any]]:
        return [
            e for e in self.events if e["kind"] == "variants" and e["state"] == state
        ]


def door(text: str, gate: asyncio.Event | None = None) -> Step:
    """A way that rewrites the same door file, as every variant of one request does."""

    async def step(lane: ProjectGateway) -> AsyncIterator[Any]:
        if gate is not None:
            await gate.wait()
        await write(lane, "notes/door.txt", text)
        assert lane.turn is not None
        result = await lane.turn.submit(
            f"Door: {text.strip()}", "agent", [text.strip()]
        )
        yield Message(text=f"Door is open for review ({result.status}).")
        yield Done(ok=True)

    return step


#: Each way's prompt names its own number ("Yours is way 2 of 3: slim and clean").
WAYS = {
    "way 1 of": door("chunky\n"),
    "way 2 of": door("slim\n"),
    "way 3 of": door("ornate\n"),
}


async def settle(crew: Crew) -> None:
    """Wait until no task works or waits."""
    for _ in range(500):
        jobs = [t.job for t in crew.tasks.values() if t.job and not t.job.done()]
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        elif not crew.queued():
            return
        await asyncio.sleep(0)


# --- Claims ---------------------------------------------------------------------------


def test_variants_of_one_request_share_files_but_no_one_else_does() -> None:
    claims = PathClaims()
    for task, title in [("v1", "Door · 1"), ("v2", "Door · 2"), ("t", "Lamp")]:
        claims.register(task, title)
    claims.join("v1", "g")
    claims.join("v2", "g")
    assert claims.claim("v1", {"art/door.png"}) == {}
    # Another way of the same request: an alternative, not a clash.
    assert claims.claim("v2", {"art/door.png"}) == {}
    assert claims.held("v2") == ["art/door.png"]
    # Anyone else is refused, naming a holder.
    assert claims.claim("t", {"art/door.png"}) == {"art/door.png": "Door · 1"}
    assert claims.claim("main", {"art/door.png"}) == {"art/door.png": "Door · 1"}
    # One way discarded: the file stays claimed by the other.
    claims.unregister("v1")
    assert claims.held("v2") == ["art/door.png"]
    assert claims.claim("t", {"art/door.png"}) == {"art/door.png": "Door · 2"}
    claims.unregister("v2")
    assert claims.claim("t", {"art/door.png"}) == {}


def test_a_variant_writes_a_file_its_sibling_holds_but_not_one_a_task_holds(
    project: Path,
) -> None:
    async def go() -> None:
        main = wire(ProjectGateway(project))
        try:
            main.claims.join("v1", "g")
            main.claims.join("v2", "g")
            one = wire(main.lane("v1", "Door · 1"))
            two = wire(main.lane("v2", "Door · 2"))
            lamp = wire(main.lane("t", "Lamp"))
            await write(one, "notes/door.txt", "one\n")
            await write(two, "notes/door.txt", "two\n")
            assert one.transaction is not None and two.transaction is not None
            assert (two.transaction.stage / "notes/door.txt").read_text() == "two\n"
            with pytest.raises(ValueError, match="another task: Door · 1"):
                await write(lamp, "notes/door.txt", "lamp\n")
        finally:
            await main.close()

    run(go)


# --- Directions and the command -------------------------------------------------------


def test_the_command_reads_a_count_a_request_and_directions() -> None:
    assert parse_command("/variants 2 a wooden door") == (2, "a wooden door", [])
    assert parse_command("/variants a wooden door") == (3, "a wooden door", [])
    assert parse_command("/variants 3 a door | chunky | slim |  ") == (
        3,
        "a door",
        ["chunky", "slim"],
    )
    assert parse_command("a door") == (3, "a door", [])


def test_directions_fit_what_is_asked_and_never_repeat() -> None:
    assert kind_of("a creaky door sound") == "sound"
    assert kind_of("make the jump floatier", ["scripts/player.gd"]) == "code"
    assert kind_of("a wooden door") == "look"
    # Whole words only: a label's theme override is a look, not music.
    assert kind_of("set theme_override_colors/font_color in scenes/sign.tscn") == "look"
    assert kind_of("arrange the crates in scenes/dock.tscn") == "scene"
    assert kind_of("restyle the greeting label with a theme colour") == "look"
    assert directions_for("a wooden door", 3) == [
        "chunky and worn",
        "slim and clean",
        "ornate and detailed",
    ]
    picked = directions_for("a wooden door", 3, given=["slim and clean"])
    assert picked[0] == "slim and clean" and len(set(picked)) == 3
    assert len(directions_for("battle music", 4)) == 4


def test_start_variants_arguments_are_checked() -> None:
    checked = variants_request(
        {"title": "Door", "request": "A door", "directions": [" a ", "b"]}
    )
    assert checked["directions"] == ["a", "b"]
    with pytest.raises(ValueError, match="2 to 4 short notes"):
        variants_request({"title": "Door", "request": "A door", "directions": ["a"]})
    # Only the lead starts more agents.
    assert registry_allowed("lead", "start_variants")
    assert not registry_allowed("worker", "start_variants")
    assert not registry_allowed("reviewer", "start_variants")


# --- Picking --------------------------------------------------------------------------


def test_picking_one_way_accepts_it_and_discards_the_rest_at_once(
    project: Path,
) -> None:
    test = VariantsTest(project)
    test.keyed = dict(WAYS)

    async def go() -> None:
        crew = test.crew()
        variants = Variants(crew)
        group = await variants.start("A wooden door", prompt=test.prompt(), count=3)
        await settle(crew)
        tasks = [crew.tasks[t] for t in group.members]
        assert [t.state for t in tasks] == ["in_review"] * 3
        assert [t.direction for t in tasks] == [
            "chunky and worn",
            "slim and clean",
            "ornate and detailed",
        ]
        # Each way was told its own direction and the others'.
        told = next(p.text for p in test.prompts if "way 2 of 3" in p.text)
        assert "slim and clean" in told and "chunky and worn" in told
        assert "A wooden door" in told
        # Every way is read by its own reviewer, like any change.
        assert sorted(test.reviewers) == sorted(group.members)
        decisions = await variants.pick(group.id, tasks[1].id)
        assert [d["accepted"] for d in decisions] == [True, False, False]
        assert (project / "notes/door.txt").read_text() == "slim\n"
        assert [t.state for t in tasks] == ["discarded", "accepted", "discarded"]
        assert group.state == "picked" and group.picked == tasks[1].id
        assert all(not crew.gateway.claims.held(t.id) for t in tasks)
        # The group says it was picked once, with every other way already final.
        (final,) = test.groups("picked")
        assert [w["state"] for w in final["ways"]] == [
            "discarded",
            "accepted",
            "discarded",
        ]
        assert final["picked"] == tasks[1].id
        assert [w["direction"] for w in final["ways"]][1] == "slim and clean"
        with pytest.raises(ValueError, match="no longer open"):
            await variants.pick(group.id, tasks[0].id)
        await crew.close()
        await test.gateway.close()

    run(go)


def test_a_failed_pick_touches_nothing(project: Path) -> None:
    test = VariantsTest(project)
    hold = asyncio.Event()
    test.keyed = {**WAYS, "way 3 of": door("ornate\n", hold)}

    async def go() -> None:
        crew = test.crew()
        variants = Variants(crew)
        group = await variants.start("A wooden door", prompt=test.prompt(), count=3)
        while sum(crew.tasks[t].state == "in_review" for t in group.members) < 2:
            await asyncio.sleep(0.01)
        working = crew.tasks[group.members[2]]
        with pytest.raises(ValueError, match="no change waiting yet"):
            await variants.pick(group.id, working.id)
        assert group.state == "working"
        assert [crew.tasks[t].state for t in group.members[:2]] == ["in_review"] * 2
        assert not (project / "notes/door.txt").exists()
        # The way still working is stopped when another is picked.
        decisions = await variants.pick(group.id, group.members[0])
        assert [d["accepted"] for d in decisions] == [True, False]
        assert working.state == "stopped"
        assert (project / "notes/door.txt").read_text() == "chunky\n"
        hold.set()
        await crew.close()
        await test.gateway.close()

    run(go)


def test_discard_all_is_one_step(project: Path) -> None:
    test = VariantsTest(project)
    test.keyed = dict(WAYS)

    async def go() -> None:
        crew = test.crew()
        variants = Variants(crew)
        group = await variants.start("A wooden door", prompt=test.prompt(), count=3)
        await settle(crew)
        decisions = await variants.discard_all(group.id)
        assert [d["accepted"] for d in decisions] == [False, False, False]
        assert not (project / "notes/door.txt").exists()
        assert group.state == "discarded"
        assert crew.gateway.claims.owners == {}
        await crew.close()
        await test.gateway.close()

    run(go)


def test_accepting_a_way_in_the_review_tab_is_a_pick(project: Path) -> None:
    test = VariantsTest(project)
    test.keyed = dict(WAYS)

    async def go() -> None:
        crew = test.crew()
        variants = Variants(crew)
        group = await variants.start("A wooden door", prompt=test.prompt(), count=2)
        await settle(crew)
        chosen = crew.tasks[group.members[0]]
        await crew.decide(chosen.transaction_id, True)
        rest = await variants.decided(chosen, True)
        assert [d["accepted"] for d in rest] == [False]
        assert group.state == "picked"
        assert crew.tasks[group.members[1]].state == "discarded"
        await crew.close()
        await test.gateway.close()

    run(go)


# --- Mix --------------------------------------------------------------------------------


def test_mix_starts_one_more_way_with_both_notes(project: Path) -> None:
    test = VariantsTest(project)
    test.keyed = {**WAYS, "combines them": door("mixed\n")}

    async def go() -> None:
        crew = test.crew()
        variants = Variants(crew)
        group = await variants.start("A wooden door", prompt=test.prompt(), count=3)
        await settle(crew)
        mixed = await variants.mix(
            group.id, "door shape from 2, colour from 3", test.prompt()
        )
        await settle(crew)
        assert mixed.variant == 4 and mixed.group == group.id
        assert mixed.direction == "Mix: door shape from 2, colour from 3"
        told = test.prompts[-1].text
        assert "door shape from 2, colour from 3" in told
        assert "2. slim and clean" in told and "3. ornate and detailed" in told
        assert mixed.state == "in_review"
        assert len(test.groups("working")[-1]["ways"]) == 4
        await variants.pick(group.id, mixed.id)
        assert (project / "notes/door.txt").read_text() == "mixed\n"
        with pytest.raises(ValueError, match="no longer open"):
            await variants.mix(group.id, "more", test.prompt())
        await crew.close()
        await test.gateway.close()

    run(go)


# --- Limits -----------------------------------------------------------------------------


def test_ways_over_the_limit_wait_their_turn(project: Path) -> None:
    # [agents] parallel = 3 with the chat's own request working: two at once.
    test = VariantsTest(project, limit=3, lead_busy=True)
    test.keyed = dict(WAYS)

    async def go() -> None:
        crew = test.crew()
        variants = Variants(crew)
        group = await variants.start("A wooden door", prompt=test.prompt(), count=3)
        states = [crew.tasks[t].state for t in group.members]
        assert states == ["working", "working", "queued"]
        waiting = crew.tasks[group.members[2]].event()
        assert waiting["step"] == "Waiting for a free agent…"
        await settle(crew)
        assert [crew.tasks[t].state for t in group.members] == ["in_review"] * 3
        # The queued way only started once another was done.
        started = [
            e["task_id"]
            for e in test.events
            if e["kind"] == "task" and e["state"] == "working"
        ]
        assert started.index(group.members[2]) > 1
        await variants.discard_all(group.id)
        await crew.close()
        await test.gateway.close()

    run(go)


def test_the_number_of_ways_and_the_parallel_setting_are_respected(
    project: Path,
) -> None:
    async def go() -> None:
        test = VariantsTest(project)
        variants = Variants(test.crew())
        for count in (1, 5):
            with pytest.raises(ValueError, match="2 to 4 ways"):
                await variants.start("A door", prompt=test.prompt(), count=count)
        with pytest.raises(ValueError, match="Say what to try"):
            await variants.start("  ", prompt=test.prompt())
        await test.gateway.close()
        off = VariantsTest(project, limit=1)
        with pytest.raises(ValueError, match="parallel = 1"):
            await Variants(off.crew()).start("A door", prompt=off.prompt())
        await off.gateway.close()

    run(go)


def test_variants_stage_the_scene_on_screen_instead_of_editing_it_live(
    project: Path,
) -> None:
    test = VariantsTest(project)
    test.keyed = dict(WAYS)

    async def go() -> None:
        crew = test.crew()
        variants = Variants(crew)
        prompt = test.prompt().model_copy(
            update={"editor_context": {"scene_path": "res://level.tscn"}}
        )
        group = await variants.start("Tweak the level", prompt=prompt, count=2)
        lane = crew.tasks[group.members[0]].gateway
        assert "scene_path" not in lane.editor_context
        assert lane.editor_context["scene_on_screen"] == "res://level.tscn"
        assert lane.edit_scene is None
        await variants.discard_all(group.id)
        await crew.close()
        await test.gateway.close()

    run(go)


# --- Try other ways -----------------------------------------------------------------------


def test_try_other_ways_keeps_the_change_in_review_as_one_way(project: Path) -> None:
    test = VariantsTest(project)
    test.keyed = dict(WAYS)

    async def go() -> None:
        crew = test.crew()
        lead = TurnController(test.gateway, test.events.append)
        lead.begin("A wooden door")
        await write(test.gateway, "notes/door.txt", "first\n")
        submitted = await lead.submit("Door: first")
        assert submitted.status == "in_review"
        decided: list[tuple[str, bool]] = []

        async def lead_decide(tx: str, accept: bool) -> dict[str, Any]:
            decided.append((tx, accept))
            return await lead.decide(tx, accept)

        variants = Variants(crew, lead_decide)
        group = await variants.start(
            "A wooden door",
            prompt=test.prompt(),
            count=2,
            lead_tx=lead.review_id,
            lead_summary="Door: first",
        )
        await settle(crew)
        # The other ways change the same file as the change in review.
        assert [crew.tasks[t].state for t in group.members] == ["in_review"] * 2
        event = variants.event(group)
        assert event["ways"][0]["task_id"] == LEAD
        assert event["ways"][0]["direction"] == "As made"
        await variants.pick(group.id, group.members[1])
        assert (project / "notes/door.txt").read_text() == "slim\n"
        assert decided == [(event["ways"][0]["transaction_id"], False)]
        assert MAIN not in test.gateway.claims.groups
        await crew.close()
        await test.gateway.close()

    run(go)


# --- Timeline and saved chats -------------------------------------------------------------


def git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.skipif(timeline.git_executable() is None, reason="git is not installed")
def test_a_pick_is_one_timeline_commit(
    project: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: Any
) -> None:
    home = tmp_path_factory.mktemp("git-home")
    (home / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))
    git(project, "init", "-q")
    git(project, "config", "user.name", "Ada Lovelace")
    git(project, "config", "user.email", "ada@example.com")
    timeline.start(project)
    before = git(project, "rev-list", "--count", "HEAD").strip()
    test = VariantsTest(project)
    test.keyed = dict(WAYS)

    async def go() -> None:
        crew = test.crew()
        variants = Variants(crew)
        group = await variants.start("A wooden door", prompt=test.prompt(), count=3)
        await settle(crew)
        await variants.pick(group.id, group.members[2])
        await crew.close()
        await test.gateway.close()

    run(go)
    after = git(project, "rev-list", "--count", "HEAD").strip()
    assert int(after) == int(before) + 1
    assert git(project, "show", "--format=", "--name-only", "HEAD").split() == [
        "notes/door.txt"
    ]


def test_saved_chats_record_the_group_and_the_pick(project: Path) -> None:
    from bridge.chat_record import ChatRecorder
    from joinery import chats

    recorder = ChatRecorder(project, "codex")
    recorder.begin({"text": "/variants 3 A wooden door"}, ())
    test = VariantsTest(project)
    test.keyed = dict(WAYS)

    def both(event: dict[str, Any]) -> None:
        test.events.append(event)
        recorder.observe(event)

    async def go() -> None:
        crew = test.crew()
        crew._out = both
        variants = Variants(crew)
        group = await variants.start("A wooden door", prompt=test.prompt(), count=3)
        await settle(crew)
        await variants.pick(group.id, group.members[1])
        await crew.close()
        await test.gateway.close()

    run(go)
    meta, entries = chats.read(project, recorder.chat_id)
    groups = [e for e in entries if e["type"] == "variants"]
    assert [g["state"] for g in groups] == ["started", "picked"]
    assert groups[0]["ways"] == [
        "chunky and worn",
        "slim and clean",
        "ornate and detailed",
    ]
    assert groups[1]["picked"]["direction"] == "slim and clean"
    tasks = [e for e in entries if e["type"] == "task" and e.get("direction")]
    assert {t["direction"] for t in tasks} == set(groups[0]["ways"])
    (turn,) = chats.turns(entries)
    assert turn["variants"][0]["state"] == "picked"
    assert (
        'Tried 3 ways of "A wooden door" (chunky and worn; slim and clean; ornate and '
        "detailed): the user kept slim and clean."
    ) in chats.summary(project, meta, entries)
    json.dumps(entries)


# --- The chat host ------------------------------------------------------------------------


def test_the_host_starts_variants_and_a_pick_decides_them(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_crew import HostTest

    host = HostTest(project, monkeypatch)
    host.scripts = dict(WAYS)
    host.lines = [
        (
            lambda _e: True,
            {
                "kind": "variants_start",
                "text": "/variants 3 A wooden door",
                "targets": [],
                "chat_id": "",
            },
        )
    ]

    def ready(events: list[dict[str, Any]]) -> bool:
        groups = [e for e in events if e.get("kind") == "variants"]
        if not groups:
            return False
        last = groups[-1]
        if all(w["state"] == "in_review" for w in last["ways"]):
            host.lines.append(
                (
                    lambda _e: True,
                    {
                        "kind": "variants_pick",
                        "group_id": last["group_id"],
                        "task_id": last["ways"][2]["task_id"],
                    },
                )
            )
            return True
        return False

    host.lines.append((ready, {"kind": "art_review"}))
    host.run()
    groups = [e for e in host.events if e.get("kind") == "variants"]
    assert groups[-1]["state"] == "picked"
    assert (project / "notes/door.txt").read_text() == "ornate\n"
    decisions = [e for e in host.events if e.get("kind") == "file_decision"]
    assert sorted(d["accepted"] for d in decisions) == [False, False, True]
    # The request is saved like any other, then the group.
    chat = next(e for e in host.events if e.get("kind") == "chat")
    assert chat["mode"] == "new"
