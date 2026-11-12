"""precheck: the submit's checks before submitting, and the reasons they give.

Fake agents only: no inference runs here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from bridge.instructions import REVIEW_GUIDE
from joinery import metrics, reasons
from joinery.cli import main
from joinery.peer_review import PeerReview, ReviewNote, ReviewRequest, notes_from
from joinery.timing import TurnTiming
from joinery.transactions import inventory
from joinery.turn import TurnController, TurnState
from tests.test_submit_for_review import SCENE, Session

KEYS = ["rule", "file", "node", "measured", "expected", "fix"]


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    return Session(tmp_path, monkeypatch)


def broken(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """A change that fails every kind of check that can be fixed."""
    session.import_output = (
        "SCRIPT ERROR: Parse Error: Unexpected token\n   at: res://player.gd:2"
    )
    monkeypatch.setattr(
        "joinery.submission.check_assets",
        lambda *_: ["art/models/a.glb: no joinery.lock entry"],
    )
    monkeypatch.setattr(
        "joinery.submission.check_deps",
        lambda *_: ["package.json: denied package stability-sdk"],
    )

    async def rules(*_a: Any) -> list[str]:
        return ["art/models/a.glb: 9000 triangles, over the art bible's 600 budget"]

    monkeypatch.setattr("joinery.submission.check_rules", rules)
    session.turn.begin("Ashtray")
    session.stage(
        {
            "scenes/ashtray.tscn": SCENE,
            "player.gd": "extends Node\nfunc (",
            "art/models/a.glb": "glTF",
        }
    )


# --- precheck is the submit's checks --------------------------------------------


def test_precheck_finds_what_submit_would_and_changes_nothing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken(session, monkeypatch)
    reviewed: list[ReviewRequest] = []

    async def reviewer(request: ReviewRequest) -> PeerReview:
        reviewed.append(request)
        return PeerReview("reviewed")

    session.turn.reviewer = reviewer
    tx = session.gateway.transaction
    assert tx is not None
    before = inventory(tx.stage)

    async def both() -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
        checked = await session.turn.precheck()
        after = inventory(tx.stage)
        assert session.turn.state is TurnState.WORKING
        assert session.turn.attempts == 0 and session.turn.last_result is None
        submitted = await session.turn.submit("Ashtray")
        return checked, submitted.as_tool_result(), after

    checked, submitted, after = session.run(both)
    assert after == before, "precheck leaves the stage as it was"
    assert checked["status"] == "problems"
    assert checked["problems"] == submitted["problems"]
    assert {p["check"] for p in checked["problems"]} == {
        "references",
        "assets",
        "dependencies",
        "art_bible",
        "godot_import",
    }
    assert checked["attempts_left"] == 3 and submitted["attempts_left"] == 2
    assert not reviewed, "neither starts the reviewer while checks fail"
    # Only the submit announced itself: precheck moves the turn nowhere.
    assert session.kinds() == ["working", "submitting", "check_problems", "working"]
    assert session.turn.prechecks == 1


def test_a_clean_precheck_never_starts_the_reviewer(session: Session) -> None:
    reviewed: list[ReviewRequest] = []

    async def reviewer(request: ReviewRequest) -> PeerReview:
        reviewed.append(request)
        return PeerReview("reviewed")

    session.turn.reviewer = reviewer
    session.turn.begin("Make an ashtray")
    session.stage({"a.gd": "extends Node\n"})
    checked = session.run(session.turn.precheck)
    assert checked["status"] == "clean" and checked["problems"] == []
    assert "Godot import" in checked["ran"] and "tests" in checked["skipped"]
    assert checked["changes"] == [{"path": "a.gd", "change": "created"}]
    assert not reviewed and session.turn.review_rounds == 0
    assert session.state == "staging"


def test_precheck_reports_the_art_gate_and_keeps_the_stage(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("joinery.submission.approved_in", lambda *_: False)
    session.turn.begin("Texture the crate")
    session.stage({"art/props/crate.png": "png"})
    checked = session.run(session.turn.precheck)
    assert checked["status"] == "needs_user"
    (problem,) = checked["problems"]
    assert problem["items"][0]["rule"] == "art_gate.unapproved"
    assert session.state == "staging" and session.gateway.art_gate is None
    assert not any(e["kind"] == "art_gate" for e in session.events)


def test_nothing_to_precheck(session: Session) -> None:
    session.turn.begin("What is in the scene?")
    checked = session.run(session.turn.precheck)
    assert checked["status"] == "nothing_to_check" and session.turn.prechecks == 0


def test_precheck_is_refused_once_the_change_is_in_review(session: Session) -> None:
    session.turn.begin("Make an ashtray")
    session.stage({"a.gd": "extends Node\n"})

    async def go() -> None:
        assert (await session.turn.submit("Ashtray")).status == "in_review"
        with pytest.raises(ValueError, match="submitted for review"):
            await session.turn.precheck()

    session.run(go)


def test_a_clean_precheck_spares_the_submit_its_import(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs: list[str] = []
    from joinery.runner import Result

    async def offline(argv: list[str], *_a: Any, **_k: Any) -> Result:
        runs.append("import" if "--import" in argv else argv[0])
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    session.turn.begin("Make an ashtray")
    session.stage({"a.gd": "extends Node\n"})

    async def go() -> str:
        await session.turn.precheck()
        return (await session.turn.submit("Ashtray")).status

    assert session.run(go) == "in_review"
    assert runs == ["import"], "the unchanged stage is imported once"


def test_the_agent_prechecks_through_the_gateway_rpc(session: Session) -> None:
    from joinery.gateway_rpc import GatewayRPC, rpc

    session.turn.begin("Make an ashtray")
    session.stage({"scenes/ashtray.tscn": SCENE})

    async def go() -> dict[str, Any]:
        server = GatewayRPC(session.gateway)
        await server.start()
        try:
            return await rpc(server.path, "precheck", {})
        finally:
            await server.close()
            await session.gateway.close()

    data = asyncio.run(go())
    assert data["status"] == "problems"
    (problem,) = data["problems"]
    (item,) = problem["items"]
    assert item["rule"] == "references.missing_file"
    assert item["measured"] == "loads art/models/ashtray.glb"


def test_the_proxy_offers_a_read_only_precheck() -> None:
    from servers.joinery_registry.proxy import make_server

    tools = asyncio.run(make_server(Path("gateway.sock")).list_tools())
    (tool,) = [t for t in tools if t.name == "precheck"]
    assert tool.annotations is not None and tool.annotations.read_only_hint
    assert not tool.input_schema.get("required")
    assert tool.description is not None and "no submit attempt" in tool.description


def test_every_agent_is_told_to_precheck_before_submitting() -> None:
    from bridge.provider_process import INSTRUCTIONS

    assert "call precheck once before submitting" in REVIEW_GUIDE
    assert "assets, scenes or code" in REVIEW_GUIDE
    assert "precheck" in INSTRUCTIONS


# --- a fake agent that prechecks submits once -------------------------------------


def fix(session: Session, problems: list[dict[str, Any]]) -> None:
    """What a fake agent does with the items: add each missing file."""
    tx = session.gateway.transaction
    assert tx is not None
    for problem in problems:
        for item in problem["items"]:
            assert item["rule"] == "references.missing_file"
            missing = item["measured"].removeprefix("loads ")
            (tx.stage / missing).parent.mkdir(parents=True, exist_ok=True)
            (tx.stage / missing).write_text("glTF")


def agent(session: Session, prechecks: bool) -> Callable[[], Awaitable[int]]:
    async def turn() -> int:
        session.stage({"scenes/ashtray.tscn": SCENE})
        if prechecks:
            checked = await session.turn.precheck()
            fix(session, checked["problems"])
        submits = 0
        while True:
            submits += 1
            result = (await session.turn.submit("Ashtray")).as_tool_result()
            if result["status"] != "failed" or result["final"]:
                assert result["status"] == "in_review"
                return submits
            fix(session, result["problems"])

    return turn


@pytest.mark.parametrize(("prechecks", "submits"), [(True, 1), (False, 2)])
def test_an_agent_that_prechecks_and_fixes_submits_once(
    session: Session, prechecks: bool, submits: int
) -> None:
    session.gateway.timing = TurnTiming()
    session.turn.begin("Make an ashtray on the desk")
    assert session.run(agent(session, prechecks)) == submits
    assert session.turn.attempts == submits
    trace = session.gateway.timing.trace
    session.turn.close_send_back()
    backs = [
        s for s in trace.spans if s["attributes"][metrics.KIND] == metrics.SEND_BACK
    ]
    assert len(backs) == submits - 1


# --- reasons have one shape ---------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "rule", "measured", "expected", "node"),
    [
        (
            "art/t/brick.png: 2048x2048, over the art bible's 1024 px limit",
            "art_bible.texture_size",
            "2048x2048",
            "1024 px",
            "",
        ),
        (
            "art/m/a.glb: 9000 triangles, over the art bible's 600 budget",
            "art_bible.triangles",
            "9000 triangles",
            "600",
            "",
        ),
        (
            "audio/sfx/hit.wav: true peak -0.2 dBTP, above the art bible's -1.0 dBTP",
            "art_bible.true_peak",
            "-0.2 dBTP",
            "-1.0 dBTP",
            "",
        ),
        (
            "audio/music/a.ogg: -9.0 LUFS, the art bible asks for -16.0 ± 1.0",
            "art_bible.loudness",
            "-9.0 LUFS",
            "-16.0 ± 1.0",
            "",
        ),
        (
            "art/m/a.glb: material 'Wood' roughness 0.05, the art bible allows 0.3-1",
            "art_bible.roughness",
            "0.05",
            "0.3-1",
            "Wood",
        ),
        (
            (
                "ui/theme.tres: font size 9 (Label) is below the art bible's 18 px at "
                "1080p (18 px at this project's 1080 px viewport)"
            ),
            "art_bible.text_size",
            "9",
            "18 px at 1080p (18 px at this project's 1080 px viewport)",
            "Label",
        ),
        (
            (
                "art/m/a.blend: armature 'Rig' has 90 deform bones, over the art bible's "
                "64 budget"
            ),
            "art_bible.bones",
            "90 deform bones",
            "64",
            "Rig",
        ),
        (
            "art/m/a.glb: something new the bible checks",
            "art_bible.rule",
            "something new the bible checks",
            "",
            "",
        ),
    ],
)
def test_art_bible_lines_become_reasons(
    line: str, rule: str, measured: str, expected: str, node: str
) -> None:
    reason = reasons.art_bible(line)
    assert reason.rule == rule and reason.file == line.split(":", 1)[0]
    assert reason.measured == measured and reason.expected == expected
    assert reason.node == node and reason.fix


def test_godot_errors_name_their_file_and_line() -> None:
    (reason,) = reasons.godot_import(
        "SCRIPT ERROR: Parse Error: Unexpected token\n   at: res://player.gd:2\n"
    )
    assert reason.rule == "godot_import.parse_error"
    assert (reason.file, reason.node) == ("player.gd", "line 2")
    (fallback,) = reasons.godot_import("")
    assert fallback.rule == "godot_import.failed"


def test_every_problem_reaches_the_agent_in_one_shape(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken(session, monkeypatch)
    checked = session.run(session.turn.precheck)
    for problem in checked["problems"]:
        assert list(problem) == ["check", "message", "paths", "items"]
        assert problem["message"], "the plain words stay"
        for item in problem["items"]:
            assert list(item) == KEYS
            assert item["rule"].startswith(problem["check"].replace("assets", "prov"))
            assert item["fix"] and "\n" not in item["fix"]
    rules = {i["rule"] for p in checked["problems"] for i in p["items"]}
    assert rules == {
        "references.missing_file",
        "provenance.no_lock_entry",
        "dependencies.denied",
        "art_bible.triangles",
        "godot_import.parse_error",
    }


def test_a_reviewer_objection_carries_its_rule() -> None:
    (note,) = notes_from(
        {"notes": [{"severity": "block", "text": "Too tall.", "rule": "art_bible"}]}
    )
    assert note == ReviewNote("block", "Too tall.", "", "art_bible")
    assert (
        reasons.reviewer(note.text, note.file, note.rule).rule == "reviewer.art_bible"
    )
    assert reasons.reviewer("x").rule == "reviewer.other"
    with pytest.raises(ValueError, match="rule is one of"):
        notes_from({"notes": [{"severity": "block", "text": "x", "rule": "taste"}]})


def test_the_complex_model_rule_names_the_object() -> None:
    from joinery.complex_models import Finding, Shape, problem

    found = problem(
        [
            Finding(
                "scenes/desk.tscn",
                "Lamp",
                (
                    Shape("Lamp/Base", "Base", (0, 0, 0), (1, 1, 1)),
                    Shape("Lamp/Shade", "Shade", (0, 1, 0), (1, 1, 1)),
                ),
            )
        ]
    )
    assert found is not None
    (item,) = found.as_dict()["items"]
    assert item["rule"] == "complex_models.placeholder_shapes"
    assert (item["file"], item["node"]) == ("scenes/desk.tscn", "Lamp")
    assert item["measured"].startswith("2 placeholder shapes")


# --- metrics count the rules ----------------------------------------------------------


def test_metrics_count_send_backs_per_rule(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    timing = TurnTiming()
    session.gateway.timing = timing
    session.turn.begin("Make an ashtray on the desk")
    session.stage({"scenes/ashtray.tscn": SCENE})

    async def go() -> None:
        await session.turn.precheck()
        await session.turn.submit("Ashtray")  # sent back: the model is missing
        await session.turn.submit("Ashtray")  # and again, unchanged
        fix(session, [p.as_dict() for p in session.turn.last_result.problems])  # type: ignore[union-attr]
        assert (await session.turn.submit("Ashtray")).status == "in_review"

    session.run(go)
    session.turn.close_send_back()
    timing.trace.save(session.root)
    (row,) = metrics.rules(session.root)
    assert row == {
        "rule": "references.missing_file",
        "sent_back": 2,
        "final": 0,
        "prechecked": 1,
        "changes": 1,
    }
    found = [
        s
        for s in metrics.read(next((session.root / ".joinery/metrics").iterdir()))
        if s["attributes"][metrics.KIND] == metrics.FINDINGS
    ]
    assert [s["attributes"][metrics.FOUND_BY] for s in found] == [
        "precheck",
        "checks",
        "checks",
    ]
    # Rule ids only: no message text reaches the file.
    assert "isn't in the project" not in json.dumps(found)
    assert main(["metrics", "--project", str(session.root), "--rules"]) == 0
    text = capsys.readouterr().out
    assert "references.missing_file" in text and "sent back" in text
    assert main(["metrics", "--project", str(session.root), "--rules", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rules"] == [row]


def test_a_dropped_change_counts_as_final(tmp_path: Path) -> None:
    trace = metrics.Trace()
    trace.add(
        metrics.FINDINGS,
        "findings checks",
        1,
        1,
        {
            metrics.FOUND_BY: "checks",
            metrics.FINAL: True,
            metrics.RULES: "tests.failed,tests.failed",
        },
    )
    trace.add(
        metrics.FINDINGS,
        "findings reviewer",
        2,
        2,
        {metrics.FOUND_BY: "reviewer", metrics.FINAL: False, metrics.RULES: "x.y"},
    )
    trace.save(tmp_path)
    assert [
        (r["rule"], r["final"], r["sent_back"]) for r in metrics.rules(tmp_path)
    ] == [
        ("tests.failed", 2, 0),
        ("x.y", 0, 1),
    ]
    assert "No check has turned" in metrics.render_rules([])


def test_the_controller_needs_no_timing_to_precheck(session: Session) -> None:
    assert session.gateway.timing is None
    session.turn.begin("Make an ashtray")
    session.stage({"a.gd": "extends Node\n"})
    assert session.run(session.turn.precheck)["status"] == "clean"
    assert isinstance(session.turn, TurnController)
