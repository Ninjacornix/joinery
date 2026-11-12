"""Working together over git: two clones of one project and a local bare remote.

No network and no real remote: each test makes a bare repository in its own
temporary folder, Ana's project pushes to it and Ben's clone pulls from it.
"""

from __future__ import annotations

import struct
import subprocess
import time
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from joinery import art_approval, cli, team, timeline
from joinery.gateway import ProjectGateway
from joinery.transactions import FileTransaction

pytestmark = pytest.mark.skipif(
    timeline.git_executable() is None, reason="git is not installed"
)


@pytest.fixture(autouse=True)
def _isolated_git(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No user or system git config: identity and hooks come only from the test."""
    home = tmp_path_factory.mktemp("git-home")
    config = home / "gitconfig"
    config.write_text("[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))
    team._CLAIM_CACHE.clear()


def git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True, check=True
    ).stdout


def png(colour: tuple[int, int, int], size: int = 4) -> bytes:
    rgba = bytes([*colour, 255]) * size * size
    return team._png(size, size, rgba)


def who(project: Path, name: str, email: str) -> None:
    git(project, "config", "user.name", name)
    git(project, "config", "user.email", email)


def accept(
    project: Path, summary: str, edits: dict[str, bytes | str | None]
) -> FileTransaction:
    """An accepted change: its files become one commit by the project's user."""
    tx = FileTransaction(project)
    tx.begin(summary)
    for path, data in edits.items():
        target = tx.stage / path
        if data is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            target.write_text(data)
        else:
            target.write_bytes(data)
    tx.review()
    tx.accept()
    return tx


def staged(project: Path, summary: str, edits: dict[str, str]) -> FileTransaction:
    """A change waiting for review."""
    tx = FileTransaction(project)
    tx.begin(summary)
    for path, text in edits.items():
        (tx.stage / path).parent.mkdir(parents=True, exist_ok=True)
        (tx.stage / path).write_text(text)
    tx.review()
    return tx


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Ana's project shared through a bare remote, and Ben's clone of it."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True
    )
    ana = tmp_path / "ana"
    ana.mkdir()
    (ana / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Harbour"\n'
    )
    (ana / "main.gd").write_text("extends Node\n")
    (ana / "art").mkdir()
    (ana / "art/door.png").write_bytes(png((176, 122, 69)))
    (ana / "notes.txt").write_text("one\ntwo\nthree\nfour\nfive\n")
    git(ana, "init", "-q")
    who(ana, "Ana Lima", "ana@example.com")
    timeline.start(ana)
    connected = team.connect(ana, str(remote))
    assert connected["connected"] and connected["reachable"]
    assert team.push(ana)["state"] == "pushed"
    ben = tmp_path / "ben"
    team.clone(str(remote), ben)
    who(ben, "Ben Okafor", "ben@example.com")
    return remote, ana, ben


# --- Connecting and status ----------------------------------------------------------


def test_connect_sets_the_remote_and_checks_lfs(pair: tuple[Path, Path, Path]) -> None:
    remote, ana, ben = pair
    assert team.remote_url(ana) == str(remote.resolve())
    lfs = team.lfs_state(ana)
    if lfs["installed"]:
        # Art and audio go to LFS; the rules were committed with the project.
        assert lfs["tracked"]
        assert "filter=lfs" in git(ana, "show", "HEAD:.gitattributes")
        assert "art/door.png" in git(ben, "lfs", "ls-files", "--name-only")
    assert (ben / "art/door.png").read_bytes() == png((176, 122, 69))
    status = team.status(ben, fetch=True)
    assert status["connected"] and status["behind"] == 0 and status["ahead"] == 0
    assert [person["name"] for person in status["teammates"]] == ["Ana Lima"]


def test_status_of_a_project_not_shared(tmp_path: Path) -> None:
    project = tmp_path / "solo"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    git(project, "init", "-q")
    who(project, "Ana Lima", "ana@example.com")
    timeline.start(project)
    data = team.status(project)
    assert not data["connected"] and data["message"] == team.NOT_SHARED
    with pytest.raises(team.TeamError, match="isn't shared"):
        team.pull(project)


# --- Pull and push --------------------------------------------------------------------


def test_pull_brings_a_teammates_change_with_their_name(
    pair: tuple[Path, Path, Path],
) -> None:
    _remote, ana, ben = pair
    accept(ben, "Add a lamp script", {"lamp.gd": "extends Node3D\n"})
    pushed = team.push(ben)
    assert pushed == {"state": "pushed", "count": 1, "message": "Pushed 1 change."}
    assert team.status(ana, fetch=True)["behind"] == 1
    pulled = team.pull(ana)
    assert pulled["state"] == "pulled" and pulled["count"] == 1
    assert pulled["authors"] == ["Ben Okafor"]
    assert pulled["message"] == "Pulled 1 change from Ben."
    assert (ana / "lamp.gd").read_text() == "extends Node3D\n"
    newest = timeline.entries(ana)["entries"][0]
    assert newest["subject"] == "Add a lamp script"
    assert newest["author"] == "Ben Okafor"
    assert newest["teammate"] and newest["pulled"]
    assert team.pull(ana)["message"] == "You have everything your teammates pushed."


def test_push_waits_for_a_pull_when_a_teammate_pushed_first(
    pair: tuple[Path, Path, Path],
) -> None:
    _remote, ana, ben = pair
    accept(ana, "Ana's change", {"a.gd": "extends Node\n"})
    team.push(ana)
    accept(ben, "Ben's change", {"b.gd": "extends Node\n"})
    with pytest.raises(team.TeamError, match="Pull, then push"):
        team.push(ben)
    pulled = team.pull(ben)
    assert pulled["state"] == "pulled" and not pulled["conflicts"]
    # Both changes, and a merge made with Ben's own identity.
    assert (ben / "a.gd").is_file() and (ben / "b.gd").is_file()
    assert git(ben, "log", "-1", "--format=%an %s").strip() == (
        "Ben Okafor Bring in Ana's changes"
    )
    assert team.push(ben)["state"] == "pushed"
    team.pull(ana)
    assert (ana / "b.gd").is_file()


def test_pull_waits_when_own_unsaved_edits_would_be_overwritten(
    pair: tuple[Path, Path, Path],
) -> None:
    _remote, ana, ben = pair
    accept(ana, "Tune main", {"main.gd": "extends Node2D\n"})
    team.push(ana)
    (ben / "main.gd").write_text("extends Control\n")
    with pytest.raises(team.TeamError, match="aren't in the Timeline yet"):
        team.pull(ben)
    assert (ben / "main.gd").read_text() == "extends Control\n"


# --- Conflicts ------------------------------------------------------------------------------


def test_binary_conflict_keeps_mine_and_offers_theirs_with_pictures(
    pair: tuple[Path, Path, Path],
) -> None:
    _remote, ana, ben = pair
    dark, light = png((107, 68, 35)), png((230, 200, 160))
    accept(ana, "Darken the door", {"art/door.png": dark})
    team.push(ana)
    accept(ben, "Lighten the door", {"art/door.png": light})
    pulled = team.pull(ben)
    assert pulled["conflicts"] == [{"path": "art/door.png", "kind": "binary"}]
    assert "You both changed door.png" in pulled["message"]
    # Never merged: Ben's file stays until he chooses.
    assert (ben / "art/door.png").read_bytes() == light
    assert "<<<<" not in git(ben, "status", "--porcelain")
    tx = FileTransaction.load(ben, pulled["review_id"])
    assert tx.state == "review" and tx.summary == "Ana's door.png or yours"
    info = team.conflict_info(tx)["team"]
    assert info["theirs"] == "Ana Lima"
    entry = info["files"][0]
    assert Path(entry["mine_picture"]).read_bytes() == light
    assert Path(entry["theirs_picture"]).read_bytes() == dark
    # The listing the Review tab reads carries it.
    status = team.status(ben)
    assert status["conflicts"][0]["id"] == tx.id
    with pytest.raises(team.TeamError, match="in Review first"):
        team.push(ben)
    tx.accept()  # "Use Ana's"
    assert (ben / "art/door.png").read_bytes() == dark
    assert team.push(ben)["state"] == "pushed"
    team.pull(ana)
    assert (ana / "art/door.png").read_bytes() == dark


def test_keeping_mine_discards_theirs(pair: tuple[Path, Path, Path]) -> None:
    _remote, ana, ben = pair
    accept(ana, "Darken the door", {"art/door.png": png((10, 10, 10))})
    team.push(ana)
    mine = png((250, 250, 250))
    accept(ben, "Lighten the door", {"art/door.png": mine})
    tx = FileTransaction.load(ben, team.pull(ben)["review_id"])
    tx.reject()  # "Keep mine"
    assert (ben / "art/door.png").read_bytes() == mine
    assert team.push(ben)["state"] == "pushed"


def test_text_conflict_merges_the_rest_and_reviews_the_clash(
    pair: tuple[Path, Path, Path],
) -> None:
    _remote, ana, ben = pair
    accept(ana, "Ana edits", {"notes.txt": "one\ntwo\nthree\nfour\nfive by Ana\n"})
    team.push(ana)
    accept(ben, "Ben edits", {"notes.txt": "one by Ben\ntwo\nthree\nfour\nfive\n"})
    pulled = team.pull(ben)
    # Separate lines merge as git merges them.
    assert not pulled["conflicts"]
    assert (
        ben / "notes.txt"
    ).read_text() == "one by Ben\ntwo\nthree\nfour\nfive by Ana\n"
    team.push(ben)
    team.pull(ana)
    accept(
        ana,
        "Ana again",
        {"notes.txt": "one by Ben\ntwo\nTHREE (Ana)\nfour\nfive by Ana\n"},
    )
    team.push(ana)
    accept(
        ben,
        "Ben again",
        {"notes.txt": "one by Ben\ntwo\nthree (Ben)\nfour\nfive by Ana\n"},
    )
    pulled = team.pull(ben)
    assert pulled["conflicts"] == [{"path": "notes.txt", "kind": "text"}]
    assert "three (Ben)" in (ben / "notes.txt").read_text()
    tx = FileTransaction.load(ben, pulled["review_id"])
    assert tx.changes[0]["path"] == "notes.txt"
    tx.accept()
    assert (ben / "notes.txt").read_text() == (
        "one by Ben\ntwo\nTHREE (Ana)\nfour\nfive by Ana\n"
    )


def test_a_change_waiting_for_review_follows_or_says_what_conflicts(
    pair: tuple[Path, Path, Path],
) -> None:
    _remote, ana, ben = pair
    waiting = staged(ben, "Rename the main node", {"main.gd": "extends Node3D\n"})
    other = staged(ben, "Change the notes", {"notes.txt": "mine\n"})
    accept(ana, "Ana's lamp", {"lamp.gd": "extends Node\n"})
    accept(ana, "Ana's notes", {"notes.txt": "theirs\n"})
    team.push(ana)
    pulled = team.pull(ben)
    # Never touched: both still wait, with their own files.
    assert FileTransaction.load(ben, waiting.id).state == "review"
    assert FileTransaction.load(ben, other.id).state == "review"
    assert (ben / "main.gd").read_text() == "extends Node\n"
    conflicting = pulled["reviews"]["conflicting"]
    assert [item["id"] for item in conflicting] == [other.id]
    assert conflicting[0]["paths"] == ["notes.txt"]
    assert "“Change the notes” waits for review" in pulled["message"]
    # The one that doesn't conflict can still be accepted on the new state.
    FileTransaction.load(ben, waiting.id).accept()
    assert (ben / "main.gd").read_text() == "extends Node3D\n"
    assert (ben / "lamp.gd").is_file()


def test_pull_waits_while_a_change_is_being_made(
    pair: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _remote, _ana, ben = pair
    tx = FileTransaction(ben)
    tx.begin("Working")
    monkeypatch.setattr(team, "_still_working", lambda _project: True)
    with pytest.raises(team.TeamError, match="still making a change"):
        team.pull(ben)


# --- Claims and recent changes ------------------------------------------------------------


def test_claims_live_in_the_shared_remote_and_expire(
    pair: tuple[Path, Path, Path],
) -> None:
    remote, ana, ben = pair
    made = team.claim(ana, ["art/door.blend"], hours=2, note="re-rigging the hinge")
    assert made["claimed"] == ["art/door.blend"]
    assert "joinery-claims" in git(remote, "branch", "--list")
    # Ben's agents see it after a fetch; his own claims never stop him.
    team.claims_list(ben)
    held = team.teammate_claim(ben, {"art/door.blend", "main.gd"})
    assert held is not None and held["name"] == "Ana Lima"
    refusal = team.claim_refusal(held)
    assert refusal.startswith("art/door.blend is claimed by Ana until ")
    assert "(re-rigging the hinge)" in refusal
    assert team.teammate_claim(ana, {"art/door.blend"}) is None
    with pytest.raises(team.TeamError, match="claimed by Ana"):
        team.claim(ben, ["art/door.blend"])
    # Expired claims stop counting.
    assert team.active_claims(ben, now=time.time() + 3 * 3600) == []
    assert team.release(ana)["released"] == ["art/door.blend"]
    team._CLAIM_CACHE.clear()
    team.claims_list(ben)
    assert team.teammate_claim(ben, {"art/door.blend"}) is None
    assert team.claim(ben, ["art/door.blend"])["claimed"] == ["art/door.blend"]


def test_a_teammates_recent_change_is_a_warning(pair: tuple[Path, Path, Path]) -> None:
    _remote, ana, ben = pair
    accept(ana, "Darken the door", {"art/door.png": png((1, 2, 3))})
    team.push(ana)
    team.status(ben, fetch=True)
    found = team.recent(ben, ["art/door.png", "lamp.gd"])
    assert [entry["path"] for entry in found] == ["art/door.png"]
    assert not found[0]["pulled"]
    note = team.recent_note(found[0], now=found[0]["time"] + 600)
    assert note == (
        "Ana changed art/door.png 10 min ago, and that change isn't pulled yet. "
        "Pull before changing it."
    )
    team.pull(ben)
    pulled = team.recent(ben, ["art/door.png"])[0]
    assert team.recent_note(pulled, now=pulled["time"] + 600) == (
        "Ana changed art/door.png 10 min ago."
    )
    # Ana's own change is no warning to Ana.
    team.refresh_recent(ana)
    assert team.recent(ana, ["art/door.png"]) == []


def test_agents_hear_the_warning_and_respect_claims(
    pair: tuple[Path, Path, Path],
) -> None:
    _remote, ana, ben = pair
    accept(ana, "Darken the door", {"art/door.png": png((9, 9, 9))})
    team.push(ana)
    team.claim(ana, ["main.gd"], hours=1)
    team.status(ben, fetch=True)
    said: list[str] = []
    lane = SimpleNamespace(
        project=ben, _team_warned=set(), _team_notes=[], progress=said.append
    )
    ProjectGateway._team_check(lane, {"art/door.png"})  # type: ignore[arg-type]
    assert lane._team_notes == said
    assert said[0].startswith("Ana changed art/door.png ")
    ProjectGateway._team_check(lane, {"art/door.png"})  # type: ignore[arg-type]
    assert len(said) == 1  # Said once per turn.
    with pytest.raises(ValueError, match="main.gd is claimed by Ana"):
        ProjectGateway._team_check(lane, {"main.gd"})  # type: ignore[arg-type]


# --- Shared art bible and brief -------------------------------------------------------------


def test_a_teammates_bible_or_brief_change_needs_approval_again(
    pair: tuple[Path, Path, Path],
) -> None:
    _remote, ana, ben = pair
    accept(ana, "Write the bible", {"art/bible.md": "# Art bible\n\nWarm wood.\n"})
    team.push(ana)
    team.pull(ben)
    art_approval.approve(ben, "Ben")
    assert art_approval.approved(ben)
    accept(ana, "Cooler bible", {"art/bible.md": "# Art bible\n\nCold steel.\n"})
    # Ana approves her own change; the approval file travels too.
    art_approval.approve(ana, "Ana")
    timeline.commit_paths(ana, [".joinery/art-approval.json"], "Approve")
    team.push(ana)
    pulled = team.pull(ben)
    assert pulled["documents"] == ["art/bible.md"]
    assert "The art bible changed: approve it" in pulled["message"]
    assert not art_approval.approved(ben)
    assert art_approval.status(ben) == {
        "state": "needs_approval",
        "by_teammate": "Ana Lima",
    }
    assert team.gate_message(ben).startswith("Ana changed the art bible.")
    with pytest.raises(team.TeamError, match="/art"):
        team.approve_document(ben, "art/bible.md")
    art_approval.approve(ben, "Ben")
    assert art_approval.approved(ben)
    # The brief: not an approval of its own, but a teammate's change waits for Ben.
    accept(ana, "Pitch change", {"design/brief.md": "# Brief\n\nA ferry game.\n"})
    team.push(ana)
    team.pull(ben)
    assert not art_approval.approved(ben)
    assert art_approval.status(ben)["state"] == "approved"
    assert team.pending_documents(ben)[0]["label"] == "game brief"
    team.approve_document(ben, "design/brief.md")
    assert art_approval.approved(ben)
    # Ben's own change to the brief needs no second approval.
    accept(
        ben, "Ben's pitch", {"design/brief.md": "# Brief\n\nA ferry game at dusk.\n"}
    )
    assert team.pending_documents(ben) == []
    team.push(ben)
    team.pull(ana)
    assert [doc["author"] for doc in team.pending_documents(ana)] == ["Ben Okafor"]


# --- Pictures and the command line ----------------------------------------------------------


def test_blend_preview_becomes_a_picture() -> None:
    width, height = 2, 2
    pixels = bytes([255, 0, 0, 255]) * 2 + bytes([0, 0, 255, 255]) * 2
    body = struct.pack("<ii", width, height) + pixels
    head = struct.pack("<4siQii", b"TEST", len(body), 0, 0, 1)
    blend = b"BLENDER-v405" + head + body + struct.pack("<4siQii", b"ENDB", 0, 0, 0, 0)
    shown = team.picture(blend, "art/door.blend")
    assert shown is not None and shown[1] == "png"
    data = shown[0]
    assert data.startswith(b"\x89PNG")
    raw = zlib.decompress(data[data.index(b"IDAT") + 4 : data.index(b"IEND") - 8])
    # The bottom row comes first in Blender: blue is the top row of the picture.
    assert raw[1:5] == bytes([0, 0, 255, 255])
    assert team.picture(b"no preview", "audio/creak.wav") is None


def test_command_line(
    pair: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _remote, ana, ben = pair
    accept(ana, "Ana's lamp", {"lamp.gd": "extends Node\n"})
    assert cli.main(["team", "push", "--project", str(ana), "--json"]) == 0
    capsys.readouterr()
    assert cli.main(["team", "status", "--project", str(ben), "--fetch"]) == 0
    out = capsys.readouterr().out
    assert "To pull: 1  To push: 0" in out and "Teammates: Ana Lima" in out
    assert cli.main(["team", "pull", "--project", str(ben)]) == 0
    assert capsys.readouterr().out.strip() == "Pulled 1 change from Ana."
    assert cli.main(["team", "push", "--project", str(ben), "--json"]) == 0
    assert '"Nothing new to push."' in capsys.readouterr().out
    listing = timeline.entries(ben)
    assert listing["team"]["connected"] and listing["team"]["behind"] == 0
