"""Several agents at once: lanes, claims, rebasing and the task scheduler."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest
from mcp.types import CallToolResult, TextContent

from bridge.crew import Crew
from bridge.protocol import Done, Message, Prompt
from joinery.claims import PathClaims, WorkerSlots, parallel_limit, project_paths
from joinery.gateway import NATIVE_OPS, OP_APPS, ProjectGateway, op_apps
from joinery.gateway_rpc import NO_NESTED_TASKS, GatewayRPC, rpc
from joinery.mcp_runtime import ServerSpec
from joinery.rebase import merge_lock
from joinery.runner import Result
from joinery.turn import TurnController

LOCK_A = 'version = 1\n\n[[asset]]\npath = "art/a.png"\nsha256 = "aa"\norigin = "tool-created"\ntool = "krita"\nrecipe = "art/a.recipe.json"\n'
LOCK_B = 'version = 1\n\n[[asset]]\npath = "art/b.png"\nsha256 = "bb"\norigin = "tool-created"\ntool = "krita"\nrecipe = "art/b.recipe.json"\n'


class FakeConnection:
    """A tool's MCP server: ``write`` writes the file named in its call; ``touch``
    writes ``a.txt`` into the folder it is given, a path the call never names;
    ``manifest`` rewrites the game manifest below the root it is given."""

    def __init__(self, spec: ServerSpec, _project: Path) -> None:
        self.spec = spec
        self.tools = {"write": object(), "touch": object()}

    async def connect(self, timeout: float = 0) -> None:
        return None

    async def call(
        self, tool: str, arguments: dict[str, Any], timeout: float = 0
    ) -> CallToolResult:
        if tool == "touch":
            target = Path(arguments["folder"]) / "a.txt"
            text = "touched\n"
        elif tool == "manifest":
            target = Path(arguments["root"]) / ".joinery/game-manifest.json"
            text = '{"features": ["controller"]}'
        else:
            target = Path(arguments["path"])
            text = arguments["text"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return CallToolResult(content=[TextContent(type="text", text="ok")])

    async def close(self) -> None:
        return None


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes/shared.txt").write_text("before\n")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    monkeypatch.setattr("joinery.gateway.approved", lambda _project: True)
    monkeypatch.setattr("joinery.gateway.MCPConnection", FakeConnection)
    monkeypatch.setattr("joinery.submission.approved_in", lambda *_: True)
    monkeypatch.setattr("joinery.submission.check_assets", lambda *_: [])

    async def offline(_argv: list[str], _root: Path, *_a: Any, **_k: Any) -> Result:
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    return tmp_path


def wire(gateway: ProjectGateway) -> ProjectGateway:
    """Give ``gateway`` the fake tool server."""

    async def connection(server: str) -> FakeConnection:
        spec = ServerSpec(name=server, command="unused", source="first-party")
        return FakeConnection(spec, gateway.project)

    gateway.connection = connection  # type: ignore[method-assign]
    return gateway


async def write(gateway: ProjectGateway, path: str, text: str) -> CallToolResult:
    return await gateway.call(
        "joinery-sound", "write", {"path": str(gateway.project / path), "text": text}
    )


def run(step: Callable[[], Awaitable[Any]]) -> Any:
    return asyncio.run(step())


# --- Lanes and claims ---------------------------------------------------------------


def test_one_agent_alone_claims_nothing(project: Path) -> None:
    async def go() -> None:
        gateway = wire(ProjectGateway(project))
        try:
            await write(gateway, "notes/a.txt", "a\n")
            assert gateway.claims.owners == {}
            assert not gateway.claims.contended()
        finally:
            await gateway.close()

    run(go)


def test_each_task_stages_its_own_change_and_one_is_accepted_alone(
    project: Path,
) -> None:
    async def go() -> None:
        main = wire(ProjectGateway(project))
        try:
            await write(main, "notes/main.txt", "main\n")
            lane = wire(main.lane("t1", "Make a lamp"))
            await write(lane, "notes/lamp.txt", "lamp\n")
            assert main.transaction is not None and lane.transaction is not None
            assert main.transaction.id != lane.transaction.id
            # Joining recorded what the main request had already staged.
            assert main.claims.held("main") == ["notes/main.txt"]
            assert main.claims.held("t1") == ["notes/lamp.txt"]
            lane.transaction.review()
            decided = await lane.decide(lane.transaction.id, True)
            assert decided["accepted"] is True
            assert (project / "notes/lamp.txt").read_text() == "lamp\n"
            # The main request's stage is untouched and still staging.
            assert main.transaction.state == "staging"
            assert not (project / "notes/main.txt").exists()
            assert main.claims.held("t1") == []
            main.transaction.review()
            await main.decide(main.transaction.id, True)
            assert (project / "notes/main.txt").read_text() == "main\n"
        finally:
            await main.close()

    run(go)


def test_a_file_another_task_claimed_is_refused_before_the_call(project: Path) -> None:
    async def go() -> None:
        main = wire(ProjectGateway(project))
        try:
            lane = wire(main.lane("t1", "Make a lamp"))
            await write(lane, "notes/shared.txt", "lamp\n")
            with pytest.raises(ValueError) as refused:
                await write(main, "notes/shared.txt", "main\n")
            assert str(refused.value) == (
                "notes/shared.txt is being changed by another task: Make a lamp. "
                "Work on something else or wait."
            )
            # Refused before anything ran: not even a stage was made.
            assert main.transaction is None
            # res:// names are recognised too.
            with pytest.raises(ValueError, match="another task: Make a lamp"):
                await main.call(
                    "joinery-sound",
                    "write",
                    {"path": "res://notes/shared.txt", "text": "x"},
                )
        finally:
            await main.close()

    run(go)


def test_a_write_the_call_did_not_name_is_put_back_and_refused(project: Path) -> None:
    async def go() -> None:
        main = wire(ProjectGateway(project))
        try:
            lane = wire(main.lane("t1", "Notes"))
            await write(lane, "notes/a.txt", "lane\n")
            with pytest.raises(ValueError, match="notes/a.txt is being changed"):
                await main.call(
                    "joinery-sound", "touch", {"folder": str(project / "notes")}
                )
            assert main.transaction is not None
            assert not (main.transaction.stage / "notes/a.txt").exists()
            assert main.claims.held("main") == []
        finally:
            await main.close()

    run(go)


def test_claims_end_with_the_decision(project: Path) -> None:
    async def go() -> None:
        main = wire(ProjectGateway(project))
        try:
            lane = wire(main.lane("t1", "Lamp"))
            await write(lane, "notes/shared.txt", "lamp\n")
            assert lane.transaction is not None
            lane.transaction.review()
            await lane.decide(lane.transaction.id, False)
            await write(main, "notes/shared.txt", "main\n")
            assert main.claims.held("main") == ["notes/shared.txt"]
        finally:
            await main.close()

    run(go)


def test_a_task_never_stages_the_scene_on_screen(project: Path) -> None:
    """It changes it live when it may, and otherwise leaves it alone."""

    async def go() -> None:
        main = wire(ProjectGateway(project))
        try:
            main.editor_context = {"scene_path": "res://scenes/level.tscn"}
            lane = wire(main.lane("t1", "Lamp"))
            with pytest.raises(ValueError, match="changing it live now"):
                await lane.call(
                    "godot", "add_node", {"scenePath": "res://scenes/level.tscn"}
                )

            async def live(_payload: dict[str, Any]) -> dict[str, Any]:
                return {"success": True}

            # With the main request idle and the scene free, the task goes live.
            lane.edit_scene = live
            with pytest.raises(ValueError, match="change it live with edit_scene"):
                await lane.call(
                    "godot", "add_node", {"scenePath": "res://scenes/level.tscn"}
                )
        finally:
            await main.close()

    run(go)


def test_closing_the_main_gateway_drops_every_lane(project: Path) -> None:
    async def go() -> None:
        main = wire(ProjectGateway(project))
        lane = wire(main.lane("t1", "Lamp"))
        await write(lane, "notes/lamp.txt", "lamp\n")
        tx = lane.transaction
        await main.close()
        assert tx is not None and tx.state == "rejected"
        assert main.lanes == {} and "t1" not in main.claims.titles

    run(go)


def test_an_unnamed_rewrite_in_place_is_caught_at_submit(project: Path) -> None:
    """A file rewritten in place that no call named slips past the stat snapshot,
    so the submit's full hash finds it: put back, and a fixable conflict."""

    async def go() -> None:
        from joinery.turn import TurnController

        main = wire(ProjectGateway(project))
        turn = TurnController(main, lambda _e: None)
        try:
            lane = wire(main.lane("t1", "Lamp"))
            await write(lane, "notes/shared.txt", "lamp\n")
            turn.begin("Notes")
            await write(main, "notes/main.txt", "main\n")
            assert main.transaction is not None
            # Behind every call's back: same folder listing, so no call sees it.
            (main.transaction.stage / "notes/shared.txt").write_text("sneaky\n")
            result = await turn.submit("Notes")
            assert result.status == "failed" and not result.final
            (problem,) = result.problems
            assert problem.check == "conflict"
            assert problem.paths == ["notes/shared.txt"]
            assert "another task: Lamp" in problem.message
            stage = main.transaction.stage
            assert (stage / "notes/shared.txt").read_text() == "before\n"
        finally:
            await main.close()

    run(go)


def test_writes_stay_fast_on_a_large_stage(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While two agents work, a write hashes what it wrote, never the whole stage."""
    from joinery.timing import TurnTiming
    from joinery.transactions import FileTransaction

    blob = b"x" * 2048
    for folder in range(60):
        base = project / f"art/set{folder}"
        base.mkdir(parents=True)
        for index in range(50):
            (base / f"tile{index}.png").write_bytes(blob)

    full: list[int] = []
    diff = FileTransaction.diff

    def counted(self: FileTransaction) -> list[dict[str, str]]:
        full.append(1)
        return diff(self)

    async def go() -> None:
        import time

        main = wire(ProjectGateway(project))
        try:
            lane = wire(main.lane("t1", "Lamp"))
            await write(main, "notes/main.txt", "main\n")
            await write(lane, "notes/lamp.txt", "lamp\n")
            monkeypatch.setattr(FileTransaction, "diff", counted)
            main.timing = TurnTiming()
            started = time.monotonic()
            for index in range(20):
                await write(main, f"art/set{index}/new{index}.png", "new")
            spent = time.monotonic() - started
            assert full == [], "a write hashed the whole stage"
            assert "claims" in main.timing.phases
            assert main.timing.phases["claims"] < 2.0
            assert spent < 10.0
            assert len(main.claims.held("main")) == 21
            # The other agent still can't take one of them.
            with pytest.raises(ValueError, match="another task: the main request"):
                await write(lane, "art/set3/new3.png", "mine")
        finally:
            await main.close()

    run(go)


def test_a_task_says_connecting_on_its_own_card(project: Path) -> None:
    async def go() -> None:
        main = wire(ProjectGateway(project))
        said: list[tuple[str, str]] = []
        main.progress = lambda text: said.append(("main", text))
        try:
            lane = main.lane("t1", "Lamp")
            lane.progress = lambda text: said.append(("t1", text))
            await lane.discover()
            assert said == [("t1", "Connecting project tools…")]
        finally:
            await main.close()

    run(go)


MANIFEST = ".joinery/game-manifest.json"


def manifest(features: list[str]) -> str:
    units = {name: {"files": [f"features/{name}.gd"]} for name in features}
    return json.dumps({"features": features, "format": 1, "units": units})


def test_game_features_are_one_claim_for_the_whole_project(project: Path) -> None:
    async def go() -> None:
        main = wire(ProjectGateway(project))
        try:
            steam = wire(main.lane("a", "Add Steam"))
            pad = wire(main.lane("b", "Controller support"))
            await write(steam, MANIFEST, manifest(["steam"]))
            await write(steam, "notes/steam.txt", "steam\n")
            assert main.claims.features_holder() == "a"
            with pytest.raises(ValueError) as refused:
                await write(pad, MANIFEST, manifest(["controller"]))
            assert str(refused.value).startswith(
                "Game features are being changed by another task: Add Steam."
            )
            # Named in the call, so refused before anything ran.
            assert pad.transaction is None
            # Written without being named: put back after the call.
            with pytest.raises(ValueError, match="another task: Add Steam"):
                await pad.call("joinery-sound", "manifest", {"root": str(project)})
            assert pad.transaction is not None
            assert not (pad.transaction.stage / MANIFEST).exists()
            # `joinery feature` in another process sees it too.
            from joinery.claims import FeatureLock

            assert FeatureLock.holder(project) == "Add Steam"
            assert steam.transaction is not None
            steam.transaction.review()
            await steam.decide(steam.transaction.id, False)
            assert FeatureLock.holder(project) == ""
            await write(pad, MANIFEST, manifest(["controller"]))
            assert main.claims.features_holder() == "b"
        finally:
            await main.close()
        assert FeatureLock.holder(project) == ""

    run(go)


def test_an_agent_waits_for_a_feature_change_applying_elsewhere(project: Path) -> None:
    from joinery.claims import FeatureLock

    async def go() -> None:
        cli = FeatureLock(project)
        assert cli.take("Add online-play") == ""
        main = wire(ProjectGateway(project))
        try:
            with pytest.raises(ValueError, match="another task: Add online-play"):
                await write(main, MANIFEST, manifest(["online-play"]))
            cli.release()
            await write(main, MANIFEST, manifest(["online-play"]))
            assert FeatureLock.holder(project) == "the main request"
        finally:
            await main.close()

    run(go)


def test_the_feature_command_names_the_agent_holding_game_features(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery import cli
    from joinery.claims import FeatureLock

    held = FeatureLock(project)
    assert held.take("Make a lamp") == ""
    try:
        code = cli.main(
            ["feature", "add", "controller", "--project", str(project), "--json"]
        )
    finally:
        held.release()
    assert code == 1
    error = json.loads(capsys.readouterr().out)["error"]
    assert error.startswith("Game features are being changed by another task:")
    assert "Make a lamp" in error


def test_the_game_manifest_is_merged_entry_by_entry(project: Path) -> None:
    from joinery.rebase import merge_manifest
    from joinery.transactions import FileTransaction

    merged = json.loads(
        merge_manifest(
            manifest(["save-load"]),
            manifest(["save-load", "steam"]),
            manifest(["controller"]),
        )
    )
    assert merged["features"] == ["steam", "controller"]
    assert set(merged["units"]) == {"steam", "controller"}
    # Accepting a change carries its feature on top of one accepted meanwhile.
    (project / ".joinery").mkdir(exist_ok=True)
    (project / MANIFEST).write_text(manifest(["save-load"]))
    tx = FileTransaction(project)
    tx.begin("Add controller support")
    (tx.stage / ".joinery").mkdir()
    (tx.stage / MANIFEST).write_text(manifest(["save-load", "controller"]))
    (tx.stage / "notes/pad.txt").write_text("pad\n")
    (project / MANIFEST).write_text(manifest(["save-load", "steam"]))
    tx.review()
    tx.accept()
    carried = json.loads((project / MANIFEST).read_text())
    assert carried["features"] == ["save-load", "steam", "controller"]
    tx.revert()
    assert json.loads((project / MANIFEST).read_text())["features"] == [
        "save-load",
        "steam",
    ]


# --- Following the project ------------------------------------------------------------


def test_an_untouched_file_follows_an_accepted_change(project: Path) -> None:
    async def go() -> None:
        main = wire(ProjectGateway(project))
        try:
            await write(main, "notes/main.txt", "main\n")
            lane = wire(main.lane("t1", "Shared"))
            await write(lane, "notes/shared.txt", "after\n")
            assert lane.transaction is not None
            lane.transaction.review()
            await lane.decide(lane.transaction.id, True)
            # The next write brings the accepted file into the main stage.
            await write(main, "notes/more.txt", "more\n")
            assert main.transaction is not None
            assert (main.transaction.stage / "notes/shared.txt").read_text() == (
                "after\n"
            )
            assert {c["path"] for c in main.transaction.diff()} == {
                "notes/main.txt",
                "notes/more.txt",
            }
        finally:
            await main.close()

    run(go)


def test_a_file_changed_underneath_is_redone_not_merged(project: Path) -> None:
    async def go() -> None:
        from joinery.turn import TurnController

        main = wire(ProjectGateway(project))
        events: list[dict[str, Any]] = []
        turn = TurnController(main, events.append)
        try:
            turn.begin("Change the notes")
            await write(main, "notes/shared.txt", "mine\n")
            # Another change to the same file was accepted meanwhile.
            (project / "notes/shared.txt").write_text("theirs\n")
            main.claims.generation += 1
            result = await turn.submit("Notes")
            assert result.status == "failed" and not result.final
            (problem,) = result.problems
            assert problem.check == "conflict"
            assert problem.paths == ["notes/shared.txt"]
            assert "redo your part" in problem.message
            assert main.transaction is not None
            assert (main.transaction.stage / "notes/shared.txt").read_text() == (
                "theirs\n"
            )
            # Redone on top of theirs, it goes to review.
            await write(main, "notes/shared.txt", "theirs and mine\n")
            again = await turn.submit("Notes")
            assert again.status == "in_review"
            await turn.decide(again.transaction_id, True)
            assert (project / "notes/shared.txt").read_text() == "theirs and mine\n"
        finally:
            await main.close()

    run(go)


def test_the_asset_lock_is_merged_between_tasks(project: Path) -> None:
    async def go() -> None:
        main = wire(ProjectGateway(project))
        try:
            lane_a = wire(main.lane("a", "Art A"))
            lane_b = wire(main.lane("b", "Art B"))
            for lane, name, lock in ((lane_a, "a", LOCK_A), (lane_b, "b", LOCK_B)):
                await write(lane, f"art/{name}.png", name)
                await write(lane, "joinery.lock", lock)
            assert lane_a.transaction is not None and lane_b.transaction is not None
            lane_a.transaction.review()
            lane_b.transaction.review()
            await lane_a.decide(lane_a.transaction.id, True)
            await lane_b.decide(lane_b.transaction.id, True)
            lock = (project / "joinery.lock").read_text()
            assert 'path = "art/a.png"' in lock and 'path = "art/b.png"' in lock
            assert (project / "art/a.png").read_text() == "a"
            assert (project / "art/b.png").read_text() == "b"
        finally:
            await main.close()

    run(go)


def test_merge_lock_keeps_entries_the_change_did_not_touch() -> None:
    merged = merge_lock(LOCK_A, LOCK_B, {"art/b.png"})
    assert 'path = "art/a.png"' in merged and 'path = "art/b.png"' in merged
    # An asset this change deleted loses its entry.
    assert 'path = "art/a.png"' not in merge_lock(
        LOCK_A, "version = 1\n", {"art/a.png"}
    )


# --- Shared pieces --------------------------------------------------------------------


def test_project_paths_finds_names_in_any_form(tmp_path: Path) -> None:
    found = project_paths(
        {
            "a": "res://scenes/a.tscn",
            "b": [str(tmp_path / "art/b.png"), "not a path", "/elsewhere/c.png"],
            "c": {"d": "./audio/d.ogg", "e": "../up.txt", "f": "word"},
        },
        tmp_path,
    )
    assert found == {"scenes/a.tscn", "art/b.png", "audio/d.ogg"}


def test_claims_skip_the_lock_and_report_the_holder() -> None:
    claims = PathClaims()
    claims.register("main", "the main request")
    claims.register("t1", "Lamp")
    assert claims.claim("t1", {"art/lamp.png", "joinery.lock"}) == {}
    assert claims.claim("main", {"art/lamp.png", "joinery.lock"}) == {
        "art/lamp.png": "Lamp"
    }
    assert claims.held("main") == []
    claims.unregister("t1")
    assert claims.claim("main", {"art/lamp.png"}) == {}


def test_parallel_limit_reads_joinery_toml(tmp_path: Path) -> None:
    assert parallel_limit(tmp_path) == 3
    (tmp_path / "joinery.toml").write_text("[agents]\nparallel = 1\n")
    assert parallel_limit(tmp_path) == 1
    (tmp_path / "joinery.toml").write_text("[agents]\nparallel = 40\n")
    assert parallel_limit(tmp_path) == 6


def test_document_operators_of_one_worker_image_take_turns() -> None:
    async def go() -> list[str]:
        slots = WorkerSlots()
        order: list[str] = []
        waited: list[str] = []

        async def op(name: str, tool: str) -> None:
            async with slots.hold(tool, lambda app: waited.append(name + " " + app)):
                order.append(name + " in")
                await asyncio.sleep(0.01)
                order.append(name + " out")

        await asyncio.gather(
            op("one", "blender"), op("two", "krita"), op("three", "godot")
        )
        assert waited == ["two Krita"]
        return order

    order = run(go)
    assert order.index("one out") < order.index("two in")
    assert order.index("three in") < order.index("one out")


def test_every_native_operation_names_the_apps_it_runs() -> None:
    assert NATIVE_OPS == frozenset(OP_APPS)
    for op in NATIVE_OPS:
        assert op_apps(op, {"tool": "gimp"}), op
    assert op_apps("paint_map", {}) == ("blender", "krita")
    assert op_apps("make_map_editable", {}) == ("blender", "krita")
    assert op_apps("make_map_editable", {"editor": "material-maker"}) == (
        "blender",
        "material-maker",
    )
    assert op_apps("operate_document", {"tool": "krita"}) == ("krita",)
    assert op_apps("operate_graph", {}) == ("material-maker",)


class SlowOps(FakeConnection):
    """Native operations that stay in their app until the test lets them finish."""

    gates: ClassVar[dict[str, asyncio.Event]] = {}
    log: ClassVar[list[str]] = []

    def __init__(self, spec: ServerSpec, project: Path) -> None:
        super().__init__(spec, project)
        self.tools = {"paint_map": object(), "operate_document": object()}

    async def call(
        self, tool: str, arguments: dict[str, Any], timeout: float = 0
    ) -> CallToolResult:
        self.log.append(tool + " in")
        await self.gates[tool].wait()
        self.log.append(tool + " out")
        return CallToolResult(content=[TextContent(type="text", text="ok")])


def test_a_krita_edit_waits_for_another_tasks_map_paint(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def imported(*_a: Any, **_k: Any) -> Result:
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.gateway.run_offline", imported)
    monkeypatch.setattr("joinery.gateway.MCPConnection", SlowOps)
    SlowOps.log = []

    async def go() -> None:
        SlowOps.gates = {
            "paint_map": asyncio.Event(),
            "operate_document": asyncio.Event(),
        }
        main = wire(ProjectGateway(project))
        said: list[tuple[str, str]] = []
        try:
            painter = wire(main.lane("t1", "Paint the crate"))
            other = wire(main.lane("t2", "Retouch the sign"))
            painter.progress = lambda text: said.append(("t1", text))
            other.progress = lambda text: said.append(("t2", text))
            paint = asyncio.create_task(
                painter.call(
                    "joinery-blender-ops",
                    "paint_map",
                    {"source": "art/sources/crate.blend", "material": "Wood"},
                )
            )
            for _ in range(200):
                if "paint_map in" in SlowOps.log or paint.done():
                    break
                await asyncio.sleep(0)
            if paint.done():
                paint.result()
            assert "paint_map in" in SlowOps.log
            edit = asyncio.create_task(
                other.call(
                    "joinery-paint",
                    "operate_document",
                    {"tool": "krita", "source": "art/sources/sign.kra"},
                )
            )
            for _ in range(20):
                await asyncio.sleep(0)
            assert ("t1", "Working in Blender and Krita…") in said
            assert ("t2", "Waiting for Krita: another task is using it…") in said
            assert "operate_document in" not in SlowOps.log
            SlowOps.gates["operate_document"].set()
            SlowOps.gates["paint_map"].set()
            await asyncio.gather(paint, edit)
            assert SlowOps.log == [
                "paint_map in",
                "paint_map out",
                "operate_document in",
                "operate_document out",
            ]
        finally:
            await main.close()

    run(go)


@pytest.fixture
def three_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apps a, b and c, each in its own worker image."""
    monkeypatch.setattr(WorkerSlots, "key", staticmethod(lambda tool: tool))


@pytest.mark.usefixtures("three_images")
def test_operations_holding_several_slots_never_wait_on_each_other() -> None:
    async def go() -> None:
        slots = WorkerSlots()
        inside: set[str] = set()

        async def op(apps: list[str]) -> None:
            async with slots.hold(apps):
                assert not inside & set(apps)
                inside.update(apps)
                await asyncio.sleep(0.01)
                inside.difference_update(apps)

        # Taken in the order asked, these three would each hold one slot and
        # wait for the next one forever.
        for _ in range(5):
            await asyncio.wait_for(
                asyncio.gather(op(["a", "b"]), op(["b", "c"]), op(["c", "a"])), 2
            )

    run(go)


@pytest.mark.usefixtures("three_images")
def test_a_cancelled_operation_gives_back_every_slot() -> None:
    async def go() -> None:
        slots = WorkerSlots()
        held = asyncio.Event()

        async def hold_forever(apps: list[str]) -> None:
            async with slots.hold(apps):
                held.set()
                await asyncio.Event().wait()

        # Cancelled while it runs.
        running = asyncio.create_task(hold_forever(["a", "b"]))
        await held.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        # Cancelled while it waits, holding a and waiting for b.
        held.clear()
        holder = asyncio.create_task(hold_forever(["b"]))
        await held.wait()
        waited: list[str] = []
        waiting = asyncio.create_task(
            slots.hold(["a", "b"], waited.append).__aenter__()
        )
        for _ in range(50):
            if waited:
                break
            await asyncio.sleep(0)
        assert waited == ["B"]
        waiting.cancel()
        holder.cancel()
        await asyncio.gather(waiting, holder, return_exceptions=True)

        async def free() -> None:
            async with slots.hold(["a", "b", "c"], lambda app: waited.append(app)):
                pass

        await asyncio.wait_for(free(), 1)
        assert waited == ["B"]

    run(go)


# --- The scheduler ------------------------------------------------------------------


class Script:
    """A fake provider for one task: each send runs the next step on its gateway."""

    def __init__(self, crew_test: CrewTest, _agent: str) -> None:
        self.test = crew_test
        self.lane: ProjectGateway | None = None

    def bind_gateway(self, path: Any) -> None:
        self.lane = path

    async def connect(self, _project: Path, _profile: str = "") -> None:
        return None

    async def send(self, prompt: Prompt) -> AsyncIterator[Any]:
        assert self.lane is not None
        self.test.prompts.append(prompt)
        step = self.test.steps.pop(0)
        async for event in step(wire(self.lane)):
            yield event

    def editor_result(self, _id: str, _result: dict[str, Any]) -> None:
        return None

    async def cancel(self) -> None:
        return None

    async def close(self) -> None:
        return None


class Endpoint:
    """The task's gateway endpoint; its path is the lane itself for the fake provider."""

    def __init__(self, lane: ProjectGateway, on_stop: Any) -> None:
        self.path = lane
        self.on_stop = on_stop

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


Step = Callable[[ProjectGateway], AsyncIterator[Any]]


class CrewTest:
    def __init__(self, project: Path, limit: int = 3) -> None:
        self.project = project
        #: The live edits the editor applied: (task id, payload).
        self.live: list[tuple[str, dict[str, Any]]] = []
        self.events: list[dict[str, Any]] = []
        self.prompts: list[Prompt] = []
        self.steps: list[Step] = []
        self.stops: list[str] = []
        self.endpoints: list[Endpoint] = []
        self.gateway = wire(ProjectGateway(project))
        self.limit = limit

    def crew(self) -> Crew:
        async def on_stop(reason: str) -> None:
            self.stops.append(reason)

        def endpoint(lane: ProjectGateway, stop: Any) -> Endpoint:
            made = Endpoint(lane, stop)
            self.endpoints.append(made)
            return made

        return Crew(
            self.gateway,
            self.events.append,
            agent="codex",
            profile="",
            limit=self.limit,
            bridge=lambda agent: Script(self, agent),
            endpoint=endpoint,
            on_stop=on_stop,
            live=self.live_edit,
        )

    async def live_edit(
        self,
        lane: ProjectGateway,
        turn: TurnController,
        payload: dict[str, Any],
        owner: str,
    ) -> dict[str, Any]:
        """The chat host's live edit, with an editor that always applies it."""
        scene = turn.admit_live_edit(lane.editor_context)
        self.live.append((owner, payload))
        turn.record_live_edit(scene, True)
        return {"success": True}

    def prompt(self) -> Prompt:
        return Prompt(text="", project_dir=str(self.project))

    def tasks(self, state: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["kind"] == "task" and e["state"] == state]


def makes(path: str, text: str, gate: asyncio.Event | None = None) -> Step:
    async def step(lane: ProjectGateway) -> AsyncIterator[Any]:
        if gate is not None:
            await gate.wait()
        await write(lane, path, text)
        assert lane.turn is not None
        result = await lane.turn.submit(path)
        yield Message(text=f"{path} is open for review ({result.status}).")
        yield Done(ok=True)

    return step


def test_tasks_run_at_the_same_time_and_each_change_is_reviewed_alone(
    project: Path,
) -> None:
    test = CrewTest(project)

    async def go() -> None:
        crew = test.crew()
        both = asyncio.Event()
        started: list[str] = []

        def waits(path: str) -> Step:
            async def step(lane: ProjectGateway) -> AsyncIterator[Any]:
                started.append(path)
                if len(started) == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), 2)
                async for event in makes(path, path)(lane):
                    yield event

            return step

        test.steps = [waits("notes/chair.txt"), waits("notes/lamp.txt")]
        one = await crew.start(
            "Chair", "Make a chair", origin="user", prompt=test.prompt()
        )
        two = await crew.start(
            "Lamp", "Make a lamp", origin="lead", prompt=test.prompt()
        )
        await asyncio.gather(*(t.job for t in (one, two) if t.job))
        assert one.state == two.state == "in_review"
        assert one.transaction_id != two.transaction_id
        assert "Make a chair" in test.prompts[0].text
        assert "Don't call start_task" in test.prompts[0].text
        # Accept one; the other's change stays in review untouched.
        decided = await crew.decide(one.transaction_id, True)
        assert decided["accepted"] and decided["task_id"] == one.id
        assert (project / "notes/chair.txt").exists()
        assert not (project / "notes/lamp.txt").exists()
        assert two.gateway.transaction is not None
        assert two.gateway.transaction.state == "review"
        assert crew.owner(two.transaction_id) is two
        await crew.decide(two.transaction_id, True)
        assert (project / "notes/lamp.txt").exists()
        assert crew.status(one.id)["state"] == "accepted"
        assert sorted(e["title"] for e in test.tasks("in_review")) == ["Chair", "Lamp"]
        # Every task event the sidebar gets is tagged with its task.
        tagged = [e for e in test.events if e["kind"] == "file_preview"]
        assert {e["task_id"] for e in tagged} == {one.id, two.id}
        await crew.close()
        await test.gateway.close()

    run(go)


def test_a_task_card_starts_with_the_provider_then_its_first_step(
    project: Path,
) -> None:
    test = CrewTest(project)

    async def go() -> None:
        crew = test.crew()
        test.steps = [makes("notes/a.txt", "a")]
        task = await crew.start("A", "Make a", origin="user", prompt=test.prompt())
        assert task.job is not None
        await task.job
        first = next(e for e in test.events if e["kind"] == "task")
        assert first["step"] == "Starting Codex CLI…"
        steps = [
            e["text"]
            for e in test.events
            if e["kind"] == "progress" and e.get("task_id") == task.id
        ]
        assert steps[0] == "Thinking through your request…"
        await crew.close()
        await test.gateway.close()

    run(go)


def goes_live(gate: asyncio.Event | None = None) -> Step:
    async def step(lane: ProjectGateway) -> AsyncIterator[Any]:
        if gate is not None:
            await gate.wait()
        assert lane.edit_scene is not None and lane.turn is not None
        try:
            await lane.edit_scene({"operations": []})
        except ValueError as exc:
            yield Message(text=str(exc))
            yield Done(ok=True)
            return
        result = await lane.turn.submit("Moved the lamp")
        yield Message(text=f"Live edit ({result.status}).")
        yield Done(ok=True)

    return step


def test_a_task_changes_the_scene_on_screen_once_the_main_request_is_idle(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("joinery.editor_ops.validate_edit", lambda *_a: None)
    test = CrewTest(project)

    async def go() -> None:
        crew = test.crew()
        main_turn = TurnController(test.gateway, lambda _e: None)
        test.gateway.editor_context = {"scene_path": "res://level.tscn"}
        prompt = test.prompt().model_copy(
            update={"editor_context": {"scene_path": "res://level.tscn"}}
        )
        hold = asyncio.Event()
        test.steps = [goes_live(), goes_live(hold)]
        lamp = await crew.start("Lamp", "Move the lamp", origin="user", prompt=prompt)
        chair = await crew.start(
            "Chair", "Move the chair", origin="user", prompt=prompt
        )
        assert lamp.job is not None
        await lamp.job
        # One live owner: the lamp's preview is on screen for the user.
        assert lamp.state == "in_review" and lamp.event()["live"] is True
        assert test.gateway.claims.live == lamp.id
        hold.set()
        assert chair.job is not None
        await chair.job
        assert chair.message.startswith(
            "The scene on screen is being changed live by another task: Lamp."
        )
        # The main request waits too.
        main_turn.begin("Paint the walls")
        with pytest.raises(ValueError, match="another task: Lamp"):
            main_turn.admit_live_edit({"scene_path": "res://level.tscn"})
        await main_turn.cancel()
        # The user accepts the preview: the scene is handed back.
        assert test.gateway.claims.release_live() == lamp.id
        assert await crew.live_decided(lamp.id, True)
        assert lamp.state == "accepted"
        main_turn.begin("Paint the walls")
        assert main_turn.admit_live_edit({"scene_path": "res://level.tscn"}) == (
            "res://level.tscn"
        )
        assert [owner for owner, _ in test.live] == [lamp.id]
        await crew.close()
        await test.gateway.close()

    run(go)


def test_a_task_stages_while_the_main_request_works(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("joinery.editor_ops.validate_edit", lambda *_a: None)
    test = CrewTest(project)

    async def go() -> None:
        crew = test.crew()
        main_turn = TurnController(test.gateway, lambda _e: None)
        main_turn.begin("Build the level")
        prompt = test.prompt().model_copy(
            update={"editor_context": {"scene_path": "res://level.tscn"}}
        )
        test.steps = [goes_live()]
        task = await crew.start("Lamp", "Move the lamp", origin="user", prompt=prompt)
        assert task.job is not None
        await task.job
        assert task.message.startswith("The main request is working")
        assert test.gateway.claims.live == "" and test.live == []
        await main_turn.cancel()
        await crew.close()
        await test.gateway.close()

    run(go)


def test_a_task_change_is_decided_while_its_agent_still_writes(
    project: Path,
) -> None:
    test = CrewTest(project)

    async def go() -> None:
        crew = test.crew()
        reviewed = asyncio.Event()
        finish = asyncio.Event()

        async def step(lane: ProjectGateway) -> AsyncIterator[Any]:
            await write(lane, "notes/a.txt", "a")
            assert lane.turn is not None
            await lane.turn.submit("Notes")
            reviewed.set()
            await finish.wait()
            yield Message(text="Done.")
            yield Done(ok=True)

        test.steps = [step]
        task = await crew.start("A", "Make a", origin="user", prompt=test.prompt())
        await asyncio.wait_for(reviewed.wait(), 5)
        identifier = task.controller.review_id
        assert crew.owner(identifier) is task
        decided = await crew.decide(identifier, True)
        assert decided["accepted"] and (project / "notes/a.txt").exists()
        finish.set()
        assert task.job is not None
        await task.job
        assert task.state == "accepted"
        assert "A" not in test.gateway.claims.titles.values()
        await crew.close()
        await test.gateway.close()

    run(go)


def test_the_limit_counts_the_chat_itself(project: Path) -> None:
    test = CrewTest(project, limit=2)

    async def go() -> None:
        crew = test.crew()
        hold = asyncio.Event()
        test.steps = [makes("notes/a.txt", "a", hold)]
        first = await crew.start("A", "Make a", origin="user", prompt=test.prompt())
        with pytest.raises(ValueError, match="1 tasks are already running"):
            await crew.start("B", "Make b", origin="lead", prompt=test.prompt())
        hold.set()
        assert first.job is not None
        await first.job
        await crew.close()
        await test.gateway.close()

    run(go)


def test_parallel_one_turns_tasks_off(project: Path) -> None:
    test = CrewTest(project, limit=1)

    async def go() -> None:
        crew = test.crew()
        with pytest.raises(ValueError, match=r"parallel = 1"):
            await crew.start("A", "Make a", origin="user", prompt=test.prompt())
        await test.gateway.close()

    run(go)


def test_a_target_another_task_holds_refuses_the_start(project: Path) -> None:
    test = CrewTest(project)

    async def go() -> None:
        crew = test.crew()
        hold = asyncio.Event()
        test.steps = [makes("notes/a.txt", "a", hold)]
        first = await crew.start(
            "A",
            "Make a",
            origin="lead",
            prompt=test.prompt(),
            targets=["res://notes/a.txt"],
        )
        with pytest.raises(
            ValueError, match="notes/a.txt is being changed by another task: A"
        ):
            await crew.start(
                "B",
                "Make b",
                origin="lead",
                prompt=test.prompt(),
                targets=["notes/a.txt"],
            )
        hold.set()
        assert first.job is not None
        await first.job
        await crew.close()
        await test.gateway.close()

    run(go)


def test_stop_all_stops_every_task_and_drops_its_stage(project: Path) -> None:
    test = CrewTest(project)

    async def go() -> None:
        crew = test.crew()
        never = asyncio.Event()

        def stuck(path: str) -> Step:
            async def step(lane: ProjectGateway) -> AsyncIterator[Any]:
                await write(lane, path, "x")
                await never.wait()
                yield Done(ok=True)

            return step

        test.steps = [stuck("notes/a.txt"), stuck("notes/b.txt")]
        tasks = [
            await crew.start(name, name, origin="user", prompt=test.prompt())
            for name in ("A", "B")
        ]
        for _ in range(50):
            if all(t.gateway.transaction is not None for t in tasks):
                break
            await asyncio.sleep(0.01)
        stages = [t.gateway.transaction for t in tasks]
        assert await crew.stop_all() == 2
        assert [t.state for t in tasks] == ["stopped", "stopped"]
        assert all(tx is not None and tx.state == "rejected" for tx in stages)
        assert crew.working() == []
        assert set(test.gateway.claims.titles) == {"main"}
        await crew.close()
        await test.gateway.close()

    run(go)


def test_a_failed_task_says_so_and_releases_everything(project: Path) -> None:
    test = CrewTest(project)

    async def go() -> None:
        crew = test.crew()

        async def breaks(_lane: ProjectGateway) -> AsyncIterator[Any]:
            raise RuntimeError("The provider went away.")
            yield Done(ok=False)  # pragma: no cover

        test.steps = [breaks]
        task = await crew.start("A", "Make a", origin="user", prompt=test.prompt())
        assert task.job is not None
        await task.job
        assert task.state == "failed" and task.message == "The provider went away."
        assert "A" not in test.gateway.claims.titles.values()
        await crew.close()
        await test.gateway.close()

    run(go)


def test_workers_get_the_policy_stop_and_cannot_start_tasks(project: Path) -> None:
    """A task's endpoint reports a Rule Zero stop like the chat's own, and refuses
    start_task (tasks are one level deep)."""
    stops: list[str] = []

    async def go() -> None:
        main = ProjectGateway(project)
        lane = main.lane("t1", "Lamp")

        async def on_stop(reason: str) -> None:
            stops.append(reason)

        endpoint = GatewayRPC(lane, on_stop, worker=True)

        async def lead(_request: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("never reached")

        endpoint.start_task = lead
        try:
            await endpoint.start()
            with pytest.raises(RuntimeError, match=NO_NESTED_TASKS):
                await rpc(
                    endpoint.path,
                    "start_task",
                    {"title": "More", "request": "More work", "targets": []},
                )
            with pytest.raises(RuntimeError, match="Only the main request"):
                await rpc(endpoint.path, "editor_edit", {})
            with pytest.raises(RuntimeError, match="Rule Zero"):
                await rpc(endpoint.path, "policy_stop", {"reason": "Rule Zero: x"})
            await asyncio.sleep(0)
        finally:
            await endpoint.close()
            # The worker's endpoint never closes the lane; the scheduler does.
            assert "t1" in main.lanes
            await main.close()

    run(go)
    assert stops == ["Rule Zero: x"]


def test_the_crew_hands_its_stop_to_every_task_endpoint(project: Path) -> None:
    test = CrewTest(project)

    async def go() -> None:
        crew = test.crew()
        test.steps = [makes("notes/a.txt", "a")]
        task = await crew.start("A", "Make a", origin="user", prompt=test.prompt())
        assert task.job is not None
        await task.job
        (endpoint,) = test.endpoints
        await endpoint.on_stop("Rule Zero")
        assert test.stops == ["Rule Zero"]
        await crew.close()
        await test.gateway.close()

    run(go)


def test_start_task_arguments_are_checked() -> None:
    from joinery.gateway_rpc import task_request

    assert task_request({"title": " Lamp ", "request": "Make a lamp"}) == {
        "title": "Lamp",
        "request": "Make a lamp",
        "targets": [],
    }
    with pytest.raises(ValueError, match="short title"):
        task_request({"title": "", "request": "x"})
    with pytest.raises(ValueError, match="does not see this conversation"):
        task_request({"title": "x", "request": ""})
    with pytest.raises(ValueError, match="project paths"):
        task_request({"title": "x", "request": "y", "targets": [1]})


def test_saved_chats_record_tasks_and_their_outcomes(project: Path) -> None:
    from bridge.chat_record import ChatRecorder
    from joinery import chats

    recorder = ChatRecorder(project, "codex")
    recorder.begin({"text": "Make a chair and a lamp"}, ())
    base = {"kind": "task", "task_id": "t1", "title": "Lamp", "origin": "lead"}
    recorder.observe({**base, "state": "working", "request": "Make a lamp"})
    recorder.observe({**base, "state": "working", "apps": ["blender"]})
    recorder.observe({"kind": "message_delta", "text": "lamp words", "task_id": "t1"})
    recorder.observe({**base, "state": "in_review", "transaction_id": "a" * 32})
    recorder.observe({**base, "state": "accepted", "transaction_id": "a" * 32})
    _, entries = chats.read(project, recorder.chat_id)
    tasks = [e for e in entries if e["type"] == "task"]
    assert [t["state"] for t in tasks] == ["started", "in_review", "accepted"]
    assert tasks[0]["request"] == "Make a lamp"
    assert not any(e["type"] == "agent" for e in entries)
    (turn,) = chats.turns(entries)
    assert turn["tasks"] == [{"task_id": "t1", "title": "Lamp", "state": "accepted"}]
    meta, _ = chats.read(project, recorder.chat_id)
    assert 'Parallel task "Lamp": its change was accepted.' in chats.summary(
        project, meta, entries
    )
    json.dumps(entries)


# --- The chat host ------------------------------------------------------------------


class HostTest:
    """The chat host with fake providers: the sidebar's lines go out as events allow."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from bridge import stdio

        self.events: list[dict[str, Any]] = []
        self.gateway: ProjectGateway | None = None
        #: (when the events so far allow it, the line the sidebar sends).
        self.lines: list[
            tuple[Callable[[list[dict[str, Any]]], bool], dict[str, Any]]
        ] = []
        self.scripts: dict[str, Step] = {}
        self.cancelled: list[str] = []
        test = self

        class FakeBridge:
            handle = ""

            def __init__(self, _agent: str, role: str = "") -> None:
                self.model_choices: list[Any] = []
                self.lane: ProjectGateway | None = None
                self.role = role

            async def resume(self, _handle: str) -> bool:
                return False

            async def new_session(self) -> None:
                return None

            def bind_gateway(self, path: Any) -> None:
                self.lane = path if isinstance(path, ProjectGateway) else None

            async def connect(self, _project: Path, _profile: str = "") -> None:
                return None

            async def send(self, prompt: Prompt) -> AsyncIterator[Any]:
                if self.role == "reviewer":
                    # The reviewer agent reads the change and finds nothing.
                    yield Message(text='{"notes": []}')
                    yield Done(ok=True)
                    return
                key = next(k for k in test.scripts if k in prompt.text)
                lane = self.lane or test.gateway
                assert lane is not None
                async for event in test.scripts[key](wire(lane)):
                    yield event

            def editor_result(self, _id: str, _result: dict[str, Any]) -> None:
                return None

            async def cancel(self) -> None:
                test.cancelled.append("lane" if self.lane else "main")

            async def close(self) -> None:
                return None

        def gateway(project: Path) -> ProjectGateway:
            self.gateway = ProjectGateway(project)
            return self.gateway

        class FakeRPC:
            def __init__(
                self, gateway: Any, stop: Any, worker: bool = False, role: str = ""
            ) -> None:
                self.gateway = gateway
                self.worker = worker or bool(role)
                self.path = gateway if self.worker else Path("gateway.sock")
                self.stop_reason = ""
                self.reports: list[dict[str, Any]] = []
                self.reproduce = None

            async def start(self) -> None:
                return None

            async def close(self) -> None:
                if not self.worker:
                    await self.gateway.close()

        async def reader(_stream: Any) -> tuple[asyncio.StreamReader, Any]:
            stream = asyncio.StreamReader()

            async def drive() -> None:
                for _ in range(500):
                    for when, line in list(self.lines):
                        if when(self.events):
                            self.lines.remove((when, line))
                            stream.feed_data(json.dumps(line).encode() + b"\n")
                    if not self.lines:
                        break
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0.1)
                stream.feed_eof()

            asyncio.get_running_loop().create_task(drive())
            return stream, type("T", (), {"close": lambda self: None})()

        monkeypatch.setattr(stdio, "emit", self.events.append)
        monkeypatch.setattr(stdio, "Bridge", FakeBridge)
        monkeypatch.setattr(stdio, "ProjectGateway", gateway)
        monkeypatch.setattr(stdio, "GatewayRPC", FakeRPC)
        monkeypatch.setattr(stdio, "Specialists", lambda *_a: _Closable())
        monkeypatch.setattr(
            stdio, "game_suggestions", lambda _p: {"kind": "suggestions"}
        )
        monkeypatch.setattr(stdio, "input_reader", reader)
        self.root = root

    def run(self) -> None:
        from bridge import stdio

        assert asyncio.run(stdio.serve(self.root, "codex")) == 0

    def task(self, state: str) -> dict[str, Any] | None:
        return next(
            (e for e in self.events if e.get("kind") == "task" and e["state"] == state),
            None,
        )


class _Closable:
    async def close(self) -> None:
        return None


def test_run_alongside_starts_a_task_whose_change_is_decided_on_its_own(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = HostTest(project, monkeypatch)
    release = asyncio.Event()

    async def main(lane: ProjectGateway) -> AsyncIterator[Any]:
        await write(lane, "notes/level.txt", "level\n")
        await release.wait()
        yield Done(ok=True)

    host.scripts = {
        "Make a lamp": makes("notes/lamp.txt", "lamp\n"),
        "Make a level": main,
    }
    host.lines = [
        (lambda _e: True, {"kind": "prompt", "text": "Make a level", "chat_id": ""}),
        (
            lambda e: any(x.get("kind") == "progress" for x in e),
            {"kind": "task_start", "text": "Make a lamp", "targets": []},
        ),
    ]

    def decide(events: list[dict[str, Any]]) -> bool:
        review = host.task("in_review")
        if review is None:
            return False
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
        return False

    def finish(events: list[dict[str, Any]]) -> bool:
        if host.task("accepted") is not None:
            release.set()
        return False

    host.lines += [(decide, {}), (finish, {})]
    host.run()
    connected = next(e for e in host.events if e.get("kind") == "connection")
    assert connected["parallel"] == 3
    accepted = host.task("accepted")
    assert accepted is not None and accepted["title"] == "Make a lamp"
    assert accepted["origin"] == "user"
    decision = next(e for e in host.events if e.get("kind") == "file_decision")
    assert decision["task_id"] == accepted["task_id"] and decision["accepted"]
    assert (project / "notes/lamp.txt").read_text() == "lamp\n"
    # The chat's own change is a separate one and never reached the project.
    assert not (project / "notes/level.txt").exists()
    untagged = [
        e for e in host.events if e.get("kind") == "done" and not e.get("task_id")
    ]
    assert len(untagged) == 1


def test_stop_with_all_stops_the_tasks_too(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = HostTest(project, monkeypatch)
    never = asyncio.Event()

    async def stuck(lane: ProjectGateway) -> AsyncIterator[Any]:
        await write(lane, f"notes/{lane.task_id}.txt", "x")
        await never.wait()
        yield Done(ok=True)

    host.scripts = {"Make a lamp": stuck, "Make a level": stuck}
    host.lines = [
        (lambda _e: True, {"kind": "prompt", "text": "Make a level", "chat_id": ""}),
        (
            lambda e: any(x.get("kind") == "progress" for x in e),
            {"kind": "task_start", "text": "Make a lamp"},
        ),
        (
            lambda e: any(x.get("kind") == "progress" and x.get("task_id") for x in e),
            {"kind": "cancel", "all": True},
        ),
    ]
    host.run()
    stopped = host.task("stopped")
    assert stopped is not None and stopped["title"] == "Make a lamp"
    assert host.cancelled.count("main") >= 1
