"""Reviewer and debugger agents, with fake providers: no inference runs here."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from bridge.protocol import Done, Execution, Message, Prompt
from bridge.reviewer import Debugger, ReviewAgent
from bridge.turn_runner import run_turn
from joinery.bootstrap import transactions
from joinery.debugging import DebugRequest, Diagnosis, debug_request
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.peer_review import PeerReview, ReviewNote, ReviewRequest, one_line
from joinery.roles import ReviewerSettings
from joinery.timing import TurnTiming
from joinery.turn import TurnController
from tests.test_crew import CrewTest, makes, project, wire, write

__all__ = ["project"]

BLOCK = ReviewNote(
    "block", "The lamp is 3 m tall; the art bible says 1.5 m.", "notes/lamp.md"
)


def run(step: Callable[[], Any]) -> Any:
    return asyncio.run(step())


def previews(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("kind") == "file_preview"]


class Author:
    """The author agent: each prompt runs the next step on the chat's gateway."""

    def __init__(self, gateway: ProjectGateway, steps: list[Any]) -> None:
        self.gateway = gateway
        self.steps = steps
        self.prompts: list[Prompt] = []

    async def send(self, prompt: Prompt) -> AsyncIterator[Any]:
        self.prompts.append(prompt)
        step = self.steps.pop(0)
        async for event in step(self.gateway):
            yield event

    def editor_result(self, _id: str, _result: dict[str, Any]) -> None:
        return None


def scripted(reviews: list[PeerReview], seen: list[ReviewRequest]) -> Any:
    async def reviewer(request: ReviewRequest) -> PeerReview:
        seen.append(request)
        return reviews.pop(0) if len(reviews) > 1 else reviews[0]

    return reviewer


def test_a_block_goes_back_to_the_author_who_fixes_it_then_the_review_opens(
    project: Path,
) -> None:
    """The reviewer's block reaches the author as a fixable problem in the same turn;
    the fixed change opens with the notes left, also in `joinery transactions list`."""

    async def go() -> None:
        events: list[dict[str, Any]] = []
        gateway = wire(ProjectGateway(project))
        gateway.progress = lambda text: events.append(
            {"kind": "progress", "text": text}
        )
        controller = TurnController(gateway, events.append, "codex")
        seen: list[ReviewRequest] = []
        controller.reviewer = scripted(
            [
                PeerReview("reviewed", [BLOCK]),
                PeerReview("reviewed", [ReviewNote("info", "Scale now matches.")]),
            ],
            seen,
        )
        results: list[dict[str, Any]] = []

        async def first(lane: ProjectGateway) -> AsyncIterator[Any]:
            await write(lane, "notes/lamp.md", "height 3m\n")
            assert lane.turn is not None
            results.append((await lane.turn.submit("A lamp")).as_tool_result())
            # The agent ends its turn without fixing it: Joinery asks once more.
            yield Message(text="Submitted.")
            yield Done(ok=True)

        async def fixes(lane: ProjectGateway) -> AsyncIterator[Any]:
            await write(lane, "notes/lamp.md", "height 1.5m\n")
            assert lane.turn is not None
            results.append((await lane.turn.submit("A lamp")).as_tool_result())
            yield Message(text="The lamp is open for review.")
            yield Done(ok=True)

        author = Author(gateway, [first, fixes])
        try:
            outcome = await run_turn(
                controller,
                author,
                Prompt(text="Make a lamp", project_dir=str(project)),
                events.append,
                TurnTiming(),
                request="Make a lamp",
            )
            assert outcome == "done"
            sent_back, opened = results
            assert sent_back["status"] == "failed" and not sent_back["final"]
            assert sent_back["problems"] == [
                {"check": "reviewer", "message": BLOCK.text, "paths": ["notes/lamp.md"]}
            ]
            # A send-back costs no submit attempt.
            assert sent_back["attempts_left"] == 3
            assert "reviewer" in author.prompts[1].text.lower() or "did not pass" in (
                author.prompts[1].text
            )
            assert opened["status"] == "in_review"
            # The reviewer read the diff, then checked its block was fixed.
            assert "+height 3m" in seen[0].diff and seen[0].request == "Make a lamp"
            assert seen[1].round == 2 and seen[1].previous == (BLOCK,)
            (preview,) = previews(events)
            assert preview["review_notes"] == [
                {"severity": "info", "text": "Scale now matches."}
            ]
            assert preview["reviewer"]["status"] == "reviewed"
            # The glanceable review reads reviewer_status for its status line.
            assert preview["reviewer_status"] == "reviewed"
            assert preview["reviewer"]["rounds"] == 1
            progress = [e["text"] for e in events if e.get("kind") == "progress"]
            assert "Reviewing the change…" in progress
            listed = transactions(project, "list")
            (entry,) = [e for e in listed if e["id"] == preview["transaction_id"]]
            assert entry["review_notes"] == preview["review_notes"]
            assert entry["reviewer"]["status"] == "reviewed"
        finally:
            await gateway.close()

    run(go)


def test_rounds_are_bounded_and_a_reviewer_never_drops_a_change(project: Path) -> None:
    """After two send-backs the change opens with the reviewer's remaining notes; an
    author who never fixes it still gets it in front of the user."""

    async def go() -> None:
        events: list[dict[str, Any]] = []
        gateway = wire(ProjectGateway(project))
        controller = TurnController(gateway, events.append, "claude")
        seen: list[ReviewRequest] = []
        controller.reviewer = scripted([PeerReview("reviewed", [BLOCK])], seen)
        try:
            controller.begin("Make a lamp")
            statuses = []
            for text in ("a", "b", "c"):
                await write(gateway, "notes/lamp.md", text)
                statuses.append((await controller.submit("A lamp")).status)
            assert statuses == ["failed", "failed", "in_review"]
            (preview,) = previews(events)
            assert preview["review_notes"] == [BLOCK.as_dict()]
            assert preview["reviewer"]["rounds"] == 2
            assert one_line([BLOCK], preview["reviewer"]) == "Reviewer: 1 unresolved"
        finally:
            await gateway.close()

    run(go)

    async def never_fixes() -> None:
        events: list[dict[str, Any]] = []
        gateway = wire(ProjectGateway(project))
        controller = TurnController(gateway, events.append, "gemini")
        seen: list[ReviewRequest] = []
        controller.reviewer = scripted([PeerReview("reviewed", [BLOCK])], seen)

        async def leaves(lane: ProjectGateway) -> AsyncIterator[Any]:
            await write(lane, "notes/lamp.md", "3m")
            yield Done(ok=True)

        author = Author(gateway, [leaves, leaves])
        try:
            outcome = await run_turn(
                controller,
                author,
                Prompt(text="Make a lamp", project_dir=str(project)),
                events.append,
                TurnTiming(),
            )
            assert outcome == "done"
            (preview,) = previews(events)
            assert preview["review_notes"] == [BLOCK.as_dict()]
            # The unchanged stage kept its review: the reviewer read it once.
            assert len(seen) == 1
            assert not [e for e in events if e.get("kind") == "notice"]
        finally:
            await gateway.close()

    run(never_fixes)


class FakeRole:
    """A provider running one role: ``act`` gets the role's endpoint path."""

    def __init__(self, act: Callable[[Path, Prompt], Any], record: list[Any]) -> None:
        self.act = act
        self.record = record
        self.path: Path | None = None
        self.stage: str | None = None

    def bind_gateway(self, path: Path) -> None:
        self.path = path

    def allow_stage(self, stage: str) -> None:
        self.stage = stage

    async def connect(self, _project: Path, _profile: str = "") -> None:
        return None

    async def send(self, prompt: Prompt) -> AsyncIterator[Any]:
        assert self.path is not None and self.stage is not None
        self.record.append(prompt)
        yield Execution(model="m1", name="Model One", effort="", mode="auto")
        reply = await self.act(self.path, prompt)
        if reply:
            yield Message(text=reply)
        yield Done(ok=True)

    async def close(self) -> None:
        return None


def agent_for(
    gateway: ProjectGateway, act: Callable[[Path, Prompt], Any], record: list[Any]
) -> ReviewAgent:
    roles: list[str] = []
    children: list[FakeRole] = []

    def bridge(_agent: str, role: str) -> FakeRole:
        roles.append(role)
        children.append(FakeRole(act, record))
        return children[-1]

    def endpoint(lane: ProjectGateway, role: str) -> GatewayRPC:
        return GatewayRPC(lane, role=role)

    made = ReviewAgent(
        gateway, agent="codex", profile="", bridge=bridge, endpoint=endpoint
    )
    made.roles = roles  # type: ignore[attr-defined]
    made.children = children  # type: ignore[attr-defined]
    return made


def test_the_reviewer_agent_reads_the_change_and_reports_through_its_tool(
    project: Path,
) -> None:
    (project / "art").mkdir()
    (project / "art/bible.md").write_text("# Art bible\nLamps are 1.5 m tall.\n")
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
    prompts: list[Prompt] = []
    refused: list[str] = []

    async def act(path: Path, _prompt: Prompt) -> str:
        # It can't hand the change in, or change anything.
        for method, params in (
            ("submit", {"summary": "x"}),
            ("editor_edit", {}),
            ("call", {"server": "godot", "tool": "write_file", "arguments": {}}),
        ):
            try:
                await rpc(path, method, params)
            except RuntimeError as exc:
                refused.append(str(exc))
        await rpc(
            path,
            "report_review",
            {
                "notes": [
                    {
                        "severity": "warn",
                        "text": "The shade is brighter than the palette.",
                    }
                ]
            },
        )
        return "Reviewed."

    async def go() -> None:
        events: list[dict[str, Any]] = []
        gateway = wire(ProjectGateway(project))
        controller = TurnController(gateway, events.append, "codex")
        reviewer = agent_for(gateway, act, prompts)
        reviewer.equip(controller)
        controller.selection = {"mode": "auto", "model": "m1", "effort": ""}
        try:
            controller.begin("Make a lamp")
            await write(gateway, "notes/lamp.md", "lamp")
            await gateway.call(
                "joinery-sound",
                "write",
                {"path": str(project / "art/lamp.png"), "text": ""},
            )
            assert gateway.transaction is not None
            (gateway.transaction.stage / "art/lamp.png").write_bytes(png)
            result = await controller.submit("A lamp")
            assert result.status == "in_review"
            (preview,) = previews(events)
            assert preview["review_notes"] == [
                {"severity": "warn", "text": "The shade is brighter than the palette."}
            ]
            assert preview["reviewer"]["status"] == "reviewed"
            assert preview["reviewer"]["model"] == "Model One"
            assert reviewer.roles == ["reviewer"]  # type: ignore[attr-defined]
            # Its own shell and file tools may read the change under review only.
            (child,) = reviewer.children  # type: ignore[attr-defined]
            assert child.stage == preview["transaction_id"]
        finally:
            await gateway.close()

    run(go)
    assert len(refused) == 3
    (prompt,) = prompts
    # The art bible is part of the reviewer's fixed rules, ahead of the change.
    assert "Make a lamp" in prompt.text and "Lamps are 1.5 m tall." in prompt.rules
    assert "+lamp" in prompt.text and "notes/lamp.md (created)" in prompt.text
    assert prompt.attachments
    assert Path(prompt.attachments[0]).as_posix().endswith("art/lamp.png")
    assert prompt.selection.model == "m1"


def test_a_reviewer_that_times_out_or_fails_opens_the_review_without_it(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bridge.reviewer.reviewer_settings",
        lambda _project: ReviewerSettings(seconds=1),
    )

    async def hangs(_path: Path, _prompt: Prompt) -> str:
        await asyncio.Event().wait()
        return ""

    async def breaks(_path: Path, _prompt: Prompt) -> str:
        raise RuntimeError("provider went away")

    async def mumbles(_path: Path, _prompt: Prompt) -> str:
        return "Looks fine to me."

    async def writes_json(_path: Path, _prompt: Prompt) -> str:
        return '```json\n{"notes": [{"severity": "info", "text": "Fine."}]}\n```'

    async def one(act: Any) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        gateway = wire(ProjectGateway(project))
        controller = TurnController(gateway, events.append, "opencode")
        agent_for(gateway, act, []).equip(controller)
        try:
            controller.begin("Make a lamp")
            await write(gateway, "notes/lamp.md", "lamp")
            result = await controller.submit("A lamp")
            assert result.status == "in_review"
            (preview,) = previews(events)
            await controller.decide(preview["transaction_id"], False)
            return preview
        finally:
            await gateway.close()

    timed_out = run(lambda: one(hangs))
    assert timed_out["reviewer"]["status"] == "timeout"
    assert "ran out of time" in timed_out["reviewer"]["message"]
    assert one_line([], timed_out["reviewer"]).startswith(
        "The reviewer ran out of time"
    )
    assert run(lambda: one(breaks))["reviewer"]["status"] == "error"
    assert run(lambda: one(mumbles))["reviewer"]["status"] == "error"
    assert run(lambda: one(writes_json))["review_notes"] == [
        {"severity": "info", "text": "Fine."}
    ]


def test_the_reviewer_switched_off_starts_no_agent(project: Path) -> None:
    (project / "joinery.toml").write_text(
        '[tools]\nenabled=["godot"]\n\n[agents]\nreviewer = false\n'
    )

    async def never(_path: Path, _prompt: Prompt) -> str:
        pytest.fail("The reviewer is off")

    async def go() -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        gateway = wire(ProjectGateway(project))
        gateway.progress = lambda text: events.append(
            {"kind": "progress", "text": text}
        )
        controller = TurnController(gateway, events.append, "codex")
        agent_for(gateway, never, []).equip(controller)
        try:
            controller.begin("Make a lamp")
            await write(gateway, "notes/lamp.md", "lamp")
            await controller.submit("A lamp")
            progress = [e["text"] for e in events if e.get("kind") == "progress"]
            assert "Reviewing the change…" not in progress
            return previews(events)[0]
        finally:
            await gateway.close()

    preview = run(go)
    assert preview["reviewer"] == {"status": "off", "rounds": 0, "elapsed_s": 0.0}
    assert preview["review_notes"] == []


def test_a_task_change_is_reviewed_too(project: Path) -> None:
    test = CrewTest(project)
    seen: list[ReviewRequest] = []

    async def go() -> None:
        crew = test.crew()
        crew._reviewer = lambda _lane, _controller: scripted(
            [PeerReview("reviewed", [ReviewNote("warn", "Check the cord length.")])],
            seen,
        )
        test.steps = [makes("notes/lamp.md", "lamp")]
        task = await crew.start(
            "Lamp", "Make a lamp", origin="user", prompt=test.prompt()
        )
        assert task.job is not None
        await task.job
        assert task.state == "in_review"
        (preview,) = [e for e in test.events if e["kind"] == "file_preview"]
        assert preview["task_id"] == task.id
        assert preview["review_notes"][0]["text"] == "Check the cord length."
        await crew.close()
        await test.gateway.close()

    run(go)
    assert seen and seen[0].request == "Make a lamp"


# --- Debugging --------------------------------------------------------------------------


ERROR = "SCRIPT ERROR: Invalid call. Nonexistent function 'jump' in base 'Node2D'."


def test_debug_requests_are_read_from_the_output() -> None:
    found = debug_request(
        {
            "output": "Godot started\n"
            + ERROR
            + "\n   at: _process (res://player.gd:12)\n",
            "log": "",
        }
    )
    assert found.error == ERROR
    assert found.title.startswith("Debugging: Invalid call.")
    assert found.locations() == ["res://player.gd:12"]
    with pytest.raises(ValueError, match="no error"):
        debug_request({"output": "all good"})
    assert debug_request({"error": "x", "scene": "../../etc"}).scene == ""


def test_a_debugger_finds_the_cause_and_a_worker_stages_the_fix(project: Path) -> None:
    """From an error to a staged fix: the debugger reads (read-only) and reports, a
    worker makes the fix, it is checked and reviewed, and one card follows both."""
    test = CrewTest(project)
    diagnosed: list[DebugRequest] = []

    async def diagnose(
        _lane: ProjectGateway, request: DebugRequest, _sel: Any
    ) -> Diagnosis:
        diagnosed.append(request)
        return Diagnosis(
            cause="player.gd calls jump() on a Node2D; the method is on Player.",
            fix="In notes/player.txt, call move() instead of jump().",
            targets=["res://notes/player.txt"],
        )

    async def go() -> None:
        crew = test.crew()
        crew._debugger = diagnose
        test.steps = [makes("notes/player.txt", "move()")]
        request = debug_request({"error": ERROR, "output": ERROR})
        task = await crew.debug(request, test.prompt())
        with pytest.raises(ValueError, match="already debugging"):
            await crew.debug(request, test.prompt())
        assert task.job is not None
        await task.job
        assert task.state == "in_review" and task.origin == "debug"
        cards = [e for e in test.events if e["kind"] == "task"]
        assert cards[0]["title"] == request.title and cards[0]["role"] == "debugger"
        assert cards[-1]["role"] == "worker" and cards[-1]["cause"].startswith(
            "player.gd"
        )
        (prompt,) = test.prompts
        assert "call move() instead of jump()" in prompt.text and ERROR in prompt.text
        assert "submit_for_review" in prompt.rules
        await crew.decide(task.transaction_id, True)
        assert (project / "notes/player.txt").read_text() == "move()"
        await crew.close()
        await test.gateway.close()

    run(go)
    assert diagnosed[0].error == ERROR


def test_a_cause_with_no_project_fix_ends_the_card_with_the_cause(
    project: Path,
) -> None:
    test = CrewTest(project)

    async def diagnose(*_: Any) -> Diagnosis:
        return Diagnosis(cause="The GPU driver crashed; nothing in the project.")

    async def fails(*_: Any) -> Diagnosis:
        raise ValueError("The debugger couldn't find the cause.")

    async def go() -> None:
        crew = test.crew()
        crew._debugger = diagnose
        task = await crew.debug(debug_request({"error": ERROR}), test.prompt())
        assert task.job is not None
        await task.job
        assert task.state == "done" and task.message.startswith("Cause: The GPU")
        crew._debugger = fails
        task = await crew.debug(debug_request({"error": ERROR}), test.prompt())
        assert task.job is not None
        await task.job
        assert task.state == "failed" and "couldn't find" in task.message
        assert not test.prompts  # no worker ran
        await crew.close()
        await test.gateway.close()

    run(go)


def test_the_debugger_agent_reproduces_offline_and_reports_through_its_tool(
    project: Path,
) -> None:
    prompts: list[Prompt] = []
    runs: list[tuple[Path, str]] = []

    async def reproduce(root: Path, scene: str) -> dict[str, Any]:
        runs.append((root, scene))
        return {"ok": False, "errors": [ERROR], "output": ERROR, "seconds": 1.0}

    async def act(path: Path, _prompt: Prompt) -> str:
        ran = await rpc(path, "reproduce", {"scene": ""})
        assert ran["errors"] == [ERROR]
        for method, params in (
            ("submit", {"summary": "x"}),
            ("report_review", {"notes": []}),
            ("call", {"server": "godot", "tool": "write_file", "arguments": {}}),
        ):
            with pytest.raises(RuntimeError):
                await rpc(path, method, params)
        await rpc(
            path,
            "report_diagnosis",
            {
                "cause": "jump() is not defined.",
                "fix": "Define jump() in player.gd.",
                "targets": ["res://player.gd"],
                "reproduced": True,
            },
        )
        return ""

    roles: list[str] = []

    def bridge(_agent: str, role: str) -> FakeRole:
        roles.append(role)
        return FakeRole(act, prompts)

    async def go() -> Diagnosis:
        gateway = wire(ProjectGateway(project))
        lane = gateway.lane("t1", "Debugging")
        try:
            debugger = Debugger(
                agent="claude",
                profile="",
                bridge=bridge,
                endpoint=lambda gw, role: GatewayRPC(gw, role=role),
                reproduce=reproduce,
            )
            request = debug_request(
                {
                    "error": ERROR,
                    "output": "at: res://player.gd:3",
                    "scene": "res://main.tscn",
                }
            )
            return await debugger.diagnose(lane, request)
        finally:
            await gateway.close()

    found = run(go)
    assert found.cause == "jump() is not defined." and found.reproduced
    assert found.targets == ["res://player.gd"]
    assert roles == ["debugger"]
    assert runs == [(project.resolve(), "res://main.tscn")]
    assert ERROR in prompts[0].text and "res://player.gd:3" in prompts[0].text


def test_debug_from_the_sidebar_ends_in_a_reviewed_fix_the_user_accepts(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chat host end to end: debug_start -> the debugger's diagnosis -> a worker's
    staged fix -> the reviewer -> the user's Accept."""
    from bridge import stdio
    from tests.test_crew import HostTest

    host = HostTest(project, monkeypatch)

    class FakeDebugger:
        def __init__(self, **_kwargs: Any) -> None:
            return None

        async def diagnose(
            self, _lane: ProjectGateway, request: DebugRequest, _selection: Any
        ) -> Diagnosis:
            assert request.error == ERROR
            return Diagnosis(
                cause="jump() is missing.",
                fix="Write move() into notes/player.md.",
                targets=["notes/player.md"],
            )

    monkeypatch.setattr(stdio, "Debugger", FakeDebugger)
    host.scripts = {"Write move()": makes("notes/player.md", "move()\n")}
    host.lines = [
        (
            lambda _e: True,
            {"kind": "debug_start", "error": ERROR, "output": ERROR, "log": ""},
        )
    ]

    def decide(_events: list[dict[str, Any]]) -> bool:
        review = host.task("in_review")
        if review is not None:
            host.lines.append(
                (
                    lambda _e: True,
                    {
                        "kind": "decision",
                        "transaction_id": review["transaction_id"],
                        "accept": True,
                    },
                )
            )
            return True
        return False

    host.lines.append((decide, {"kind": "art_review"}))
    host.run()
    first = next(e for e in host.events if e.get("kind") == "task")
    assert first["role"] == "debugger" and first["title"].startswith("Debugging: ")
    accepted = host.task("accepted")
    assert accepted is not None and accepted["origin"] == "debug"
    assert accepted["cause"] == "jump() is missing."
    (preview,) = previews(host.events)
    assert preview["reviewer"]["status"] == "reviewed"
    assert (project / "notes/player.md").read_text() == "move()\n"
