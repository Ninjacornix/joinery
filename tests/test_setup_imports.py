"""Reference images and design documents the user adds at setup or later.

Copied into art/reference/ and design/docs/, locked as the user's import, put on
the art bible's board and listed in the brief; refused plainly when they can't be.
"""

from __future__ import annotations

import asyncio
import json
import struct
import tomllib
import zlib
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from joinery import gdd, setup_imports
from joinery.art_approval import approved
from joinery.catalog import load_catalog
from joinery.cli import main
from joinery.installer import Selection
from joinery.lockfile import check_assets
from joinery.policy import Severity, user_only_violation
from joinery.project import create_project
from servers.joinery_registry.server import make_server

GDD = """# Harbour keeper

Some intro the brief has no section for.

## Elevator pitch
A lighthouse keeper fixes the harbour before a storm.

## Core loop
Every 30 s: patch a leak.

### Every 5 minutes
Sail to the next pier.

## Target audience
Adults who play short evening sessions.

```
## not a heading inside code
```

Story
-----
The storm is coming.
"""


def png(width: int = 2, height: int = 2, shade: int = 128) -> bytes:
    raw = b"".join(b"\x00" + bytes([shade, 64, 32]) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOINERY_NO_DOWNLOADS", "1")


@pytest.fixture
def picked(tmp_path: Path) -> Path:
    """The user's own folder of material, outside any project."""
    folder = tmp_path / "my stuff"
    folder.mkdir()
    (folder / "Dusk Board.png").write_bytes(png(shade=10))
    (folder / "quarry.png").write_bytes(png(shade=20))
    (folder / "gdd.md").write_text(GDD)
    (folder / "pitch.pdf").write_bytes(b"%PDF-1.7\n%\xe2\xe3\n")
    (folder / "notes.docx").write_bytes(b"PK\x03\x04")
    (folder / "fake.png").write_text("not really a picture")
    (folder / "secret.txt").write_text("next to the picked files; never copied")
    return folder


def made(tmp_path: Path, *extra: str) -> Path:
    project = tmp_path / "game"
    code = main(
        ["new", str(project), "--tools", "godot", "--blueprint", "blank", *extra]
    )
    assert code == 0
    return project


def lock_entries(project: Path) -> dict[str, dict[str, Any]]:
    raw = tomllib.loads((project / "joinery.lock").read_text())
    return {entry["path"]: entry for entry in raw.get("asset", [])}


# --- At creation -----------------------------------------------------------------


def test_new_copies_locks_boards_and_lists_everything(
    tmp_path: Path, picked: Path
) -> None:
    project = made(
        tmp_path,
        "--reference",
        f"{picked / 'Dusk Board.png'}=the warm low light | not the haze",
        "--reference",
        str(picked / "quarry.png"),
        "--design-doc",
        str(picked / "gdd.md"),
        "--design-doc",
        str(picked / "pitch.pdf"),
    )
    reference = project / "art/reference"
    assert sorted(p.name for p in reference.iterdir()) == [
        ".gdignore",
        "Dusk Board.png",
        "quarry.png",
    ]
    assert (reference / "quarry.png").read_bytes() == (
        picked / "quarry.png"
    ).read_bytes()
    # Only the picked files: nothing else from the user's folder.
    assert not list(project.rglob("secret.txt"))
    entries = lock_entries(project)
    entry = entries["art/reference/Dusk Board.png"]
    assert entry["origin"] == "human-import"
    assert entry["licence"] == setup_imports.LICENCE
    assert check_assets(project, load_catalog()) == []

    bible = (project / "art/bible.md").read_text()
    assert (
        "| R1 | Dusk Board |  | the warm low light \\| not the haze |  | "
        "art/reference/Dusk Board.png |" in bible
    )
    assert "| R2 | quarry |  |  |  | art/reference/quarry.png |" in bible
    # The board rows were in place before the creation approval.
    assert approved(project)

    docs = project / "design/docs"
    assert (docs / "gdd.md").read_text() == GDD
    assert (docs / "pitch.pdf").is_file()
    text = (project / "design/brief.md").read_text()
    assert text.index("## Design documents") < text.index("## 1. Pitch")
    assert "- `design/docs/gdd.md` (Markdown): drafted " in text
    assert "- `design/docs/pitch.pdf` (PDF): not copied into the brief" in text
    assert "Every 30 s: patch a leak." in text
    assert "**Every 5 minutes**" in text
    assert '_From design/docs/gdd.md, "Core loop"._' in text
    # A heading the brief has no section for stays in the document only.
    assert "The storm is coming." not in text


def test_game_apply_reads_the_wizards_files_and_removes_them(
    tmp_path: Path, picked: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "game"
    create_project(project, "G", Selection(["godot"], []), load_catalog(), wizard=True)
    (project / setup_imports.REFERENCES_FILE).write_text(
        json.dumps(
            [{"path": str(picked / "quarry.png"), "note": "warm stone\nat dusk"}]
        )
    )
    (project / setup_imports.DOCUMENTS_FILE).write_text(
        json.dumps([str(picked / "gdd.md")])
    )
    capsys.readouterr()
    code = main(
        [
            "game",
            "apply",
            "--project",
            str(project),
            "--blueprint",
            "blank",
            "--no-addon",
        ]
    )
    assert code == 0
    steps = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    by_step = {step["step"]: step for step in steps}
    assert by_step["references"]["message"] == (
        "Added 1 reference image to art/reference/ and the art bible's reference "
        "board. Say what to avoid in each in /art."
    )
    assert by_step["design-docs"]["message"].startswith(
        "Added 1 design document to design/docs/ and drafted "
    )
    assert not (project / setup_imports.REFERENCES_FILE).exists()
    assert not (project / setup_imports.DOCUMENTS_FILE).exists()
    assert (
        "| R1 | quarry |  | warm stone at dusk |"
        in (project / "art/bible.md").read_text()
    )


def test_bible_and_brief_commands_take_the_wizards_files_without_a_starting_point(
    tmp_path: Path, picked: Path
) -> None:
    project = tmp_path / "game"
    create_project(project, "G", Selection(["godot"], []), load_catalog(), wizard=True)
    (project / ".joinery-bible-answers.json").write_text(
        json.dumps({"vision": "Cosy."})
    )
    (project / setup_imports.REFERENCES_FILE).write_text(
        json.dumps([str(picked / "quarry.png")])
    )
    assert main(["bible", "--project", str(project)]) == 0
    assert "art/reference/quarry.png |" in (project / "art/bible.md").read_text()
    assert approved(project)
    assert "art/reference/quarry.png" in lock_entries(project)


def test_new_refuses_material_without_a_starting_point(
    tmp_path: Path, picked: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "new",
            str(tmp_path / "g"),
            "--tools",
            "godot",
            "--design-doc",
            str(picked / "gdd.md"),
        ]
    )
    assert code == 1
    assert "Add --blueprint" in capsys.readouterr().err
    assert not (tmp_path / "g").exists()


@pytest.mark.parametrize(
    ("flag", "name", "message"),
    [
        ("--reference", "notes.docx", "notes.docx isn't a picture."),
        (
            "--reference",
            "gdd.md",
            "If it describes the game, add it under Design documents.",
        ),
        ("--reference", "fake.png", "fake.png doesn't hold a PNG, JPEG or WebP image."),
        ("--reference", "missing.png", "Can't find missing.png."),
        ("--design-doc", "notes.docx", "notes.docx can't be a design document."),
        ("--design-doc", "quarry.png", "quarry.png can't be a design document."),
    ],
)
def test_refusals_say_why_and_write_nothing(
    tmp_path: Path,
    picked: Path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    name: str,
    message: str,
) -> None:
    project = tmp_path / "game"
    code = main(
        [
            "new",
            str(project),
            "--tools",
            "godot",
            "--blueprint",
            "blank",
            flag,
            str(picked / name),
        ]
    )
    assert code == 1
    assert message in capsys.readouterr().err
    assert not project.exists()


def test_a_folder_a_fake_pdf_and_too_many_are_refused(
    tmp_path: Path, picked: Path
) -> None:
    project = tmp_path / "game"
    project.mkdir()
    with pytest.raises(ValueError, match="is a folder"):
        setup_imports.plan(project, [(picked, "")])
    (picked / "bad.pdf").write_text("hello")
    with pytest.raises(ValueError, match="isn't a PDF inside"):
        setup_imports.plan(project, documents=[picked / "bad.pdf"])
    (picked / "latin1.txt").write_bytes("caf\xe9".encode("latin-1"))
    with pytest.raises(ValueError, match="isn't plain text"):
        setup_imports.plan(project, documents=[picked / "latin1.txt"])
    many = [(picked / "quarry.png", "")] * (setup_imports.MAX_REFERENCES + 1)
    with pytest.raises(ValueError, match="Add up to 20 references"):
        setup_imports.plan(project, many)
    docs = [picked / "gdd.md"] * (setup_imports.MAX_DOCUMENTS + 1)
    with pytest.raises(ValueError, match="Add up to 10 design documents"):
        setup_imports.plan(project, documents=docs)


# --- Names, duplicates and where files may go -------------------------------------


def test_names_stay_inside_their_folder(tmp_path: Path) -> None:
    assert (
        setup_imports.safe_name("../../etc/pass wd.PNG", "reference") == "pass wd.png"
    )
    assert setup_imports.safe_name("..\\..\\boot.ini", "document") == "boot.ini"
    assert setup_imports.safe_name(".hidden.png", "reference") == "hidden.png"
    assert setup_imports.safe_name("a/b:c*?.jpg", "reference") == "b_c.jpg"
    assert setup_imports.safe_name("....png", "reference") == "reference.png"


def test_duplicates_are_copied_once_and_names_never_overwrite(
    tmp_path: Path, picked: Path
) -> None:
    project = tmp_path / "game"
    (project / "art/reference").mkdir(parents=True)
    (project / "art/reference/quarry.png").write_bytes(png(shade=99))
    other = tmp_path / "other"
    other.mkdir()
    (other / "quarry.png").write_bytes(png(shade=20))  # same bytes as picked/quarry.png
    plan = setup_imports.plan(
        project,
        [
            (picked / "quarry.png", "a"),
            (other / "quarry.png", "b"),
            (picked / "Dusk Board.png", ""),
        ],
    )
    # Same bytes twice: one entry. A different file already called quarry.png: kept.
    assert [p.destination for p in plan.references] == [
        "art/reference/quarry-2.png",
        "art/reference/Dusk Board.png",
    ]
    setup_imports.apply(project, plan)
    assert (project / "art/reference/quarry.png").read_bytes() == png(shade=99)
    # The same file again later is recognised by its bytes and not copied.
    again = setup_imports.plan(project, [(picked / "quarry.png", "")])
    assert [(p.destination, p.copy) for p in again.references] == [
        ("art/reference/quarry-2.png", False)
    ]


def test_a_reference_folder_linked_outside_the_project_is_refused(
    tmp_path: Path, picked: Path
) -> None:
    project = tmp_path / "game"
    (project / "art").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (project / "art/reference").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="points outside this project"):
        setup_imports.plan(project, [(picked / "quarry.png", "")])
    assert list(outside.iterdir()) == []


def test_a_path_with_an_equals_sign_still_splits_its_note(tmp_path: Path) -> None:
    odd = tmp_path / "a=b.png"
    odd.write_bytes(png())
    assert setup_imports.parse_reference(str(odd)) == (odd, "")
    assert setup_imports.parse_reference(f"{odd}=the light") == (odd, "the light")
    assert setup_imports.parse_reference(f"{tmp_path / 'c.png'}=x") == (
        tmp_path / "c.png=x",
        "",
    )


# --- Existing projects: a reviewed change -----------------------------------------


def test_references_add_plans_then_applies_as_a_reviewed_change(
    tmp_path: Path, picked: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = made(tmp_path)
    before = (project / "art/bible.md").read_text()
    args = [
        "references",
        "add",
        "--project",
        str(project),
        "--reference",
        f"{picked / 'quarry.png'}=warm stone",
        "--design-doc",
        str(picked / "gdd.md"),
        "--json",
    ]
    capsys.readouterr()
    assert main([*args, "--plan"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["changes"] == [
        "art/reference/quarry.png",
        "design/docs/gdd.md",
        "joinery.lock",
        "art/bible.md",
        "design/brief.md",
    ]
    assert any("approve it again" in note for note in planned["notes"])
    assert (project / "art/bible.md").read_text() == before
    assert not (project / "art/reference/quarry.png").exists()

    assert main([*args, "--yes"]) == 0
    done = json.loads(capsys.readouterr().out)
    assert done["state"] == "accepted"
    assert {step["step"] for step in done["steps"]} == {"references", "design-docs"}
    assert "| R1 | quarry |  | warm stone |" in (project / "art/bible.md").read_text()
    assert "art/reference/quarry.png" in lock_entries(project)
    assert check_assets(project, load_catalog()) == []
    # Adding rows changes the bible, so the user approves it again.
    assert not approved(project)


def test_references_add_without_files_or_with_a_bad_one(
    tmp_path: Path, picked: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = made(tmp_path)
    capsys.readouterr()
    assert main(["references", "add", "--project", str(project), "--json"]) == 1
    assert "Pick at least one" in json.loads(capsys.readouterr().out)["error"]
    bad = ["--reference", str(picked / "notes.docx"), "--json"]
    assert main(["references", "add", "--project", str(project), *bad]) == 1
    assert "isn't a picture" in json.loads(capsys.readouterr().out)["error"]


def test_agents_cannot_add_references_through_the_shell() -> None:
    for command in (
        "uv run joinery references add --project . --reference x.png --yes",
        "joinery new /tmp/g --blueprint blank --design-doc gdd.md",
        "cd game && joinery game apply --project . --blueprint blank --reference a.png",
    ):
        verdict = user_only_violation(command)
        assert verdict is not None and verdict.severity == Severity.DENY, command
    assert user_only_violation("joinery brief --project . --json") is None


# --- Drafting the brief from a design document -------------------------------------


def test_outline_reads_atx_and_setext_headings_outside_code() -> None:
    headings = [(p.heading, p.level) for p in gdd.outline(GDD)]
    assert headings == [
        ("Harbour keeper", 1),
        ("Elevator pitch", 2),
        ("Core loop", 2),
        ("Every 5 minutes", 3),
        ("Target audience", 2),
        ("Story", 2),
    ]
    core = next(p for p in gdd.outline(GDD) if p.heading == "Core loop")
    assert "Sail to the next pier." in core.body


def test_only_open_sections_change_and_only_word_for_word() -> None:
    brief_text = (
        "# Game brief\n\n## 1. Pitch\nMy own pitch.\n\n## 5. Core loop\n_Not decided yet._\n\n"
        "## 3. Audience and content\n- Audience: _Not decided yet._\n\n## 8. Camera\n_Not decided yet._\n"
    )
    out, drafted = gdd.draft_open_sections(brief_text, [("design/docs/gdd.md", GDD)])
    assert drafted == ["Core loop", "Audience and content"]
    assert "My own pitch." in out and "harbour before a storm" not in out
    assert (
        "- Audience: _Not decided yet._\n\nAdults who play short evening sessions."
        in out
    )
    # The document has no camera heading: the camera stays open.
    assert out.endswith("## 8. Camera\n_Not decided yet._\n")


def test_a_long_section_is_cut_and_points_at_the_file() -> None:
    long_gdd = "## Core loop\n" + "\n\n".join(["word " * 60] * 20)
    out, _ = gdd.draft_open_sections(
        "## 5. Core loop\n_Not decided yet._\n", [("design/docs/long.md", long_gdd)]
    )
    assert "(The rest is in design/docs/long.md.)" in out
    assert len(out) < gdd.MAX_BLOCK + 300


# --- What agents get -------------------------------------------------------------


def call(project: Path, tool: str) -> Any:
    async def run() -> Any:
        async with Client(make_server(project)) as client:
            return await client.call_tool(tool, {})

    result = asyncio.run(run())
    assert not result.is_error, result.content[0].text
    return result


def test_game_brief_returns_the_design_documents(tmp_path: Path, picked: Path) -> None:
    project = made(
        tmp_path,
        "--design-doc",
        str(picked / "gdd.md"),
        "--design-doc",
        str(picked / "pitch.pdf"),
    )
    data = json.loads(call(project, "game_brief").content[0].text)
    documents = {d["path"]: d for d in data["design_documents"]}
    assert documents["design/docs/gdd.md"]["text"] == GDD
    assert documents["design/docs/gdd.md"]["truncated"] is False
    assert documents["design/docs/pitch.pdf"]["text"] is None


def test_design_document_text_is_capped(tmp_path: Path) -> None:
    docs = tmp_path / "design/docs"
    docs.mkdir(parents=True)
    (docs / "big.md").write_text("x" * (setup_imports.MAX_DOC_TEXT + 10))
    (docs / ".gdignore").write_text("")
    [entry] = setup_imports.design_documents(tmp_path)
    assert entry["truncated"] is True
    assert len(str(entry["text"])) == setup_imports.MAX_DOC_TEXT


def test_art_direction_shows_the_reference_images(tmp_path: Path, picked: Path) -> None:
    project = made(tmp_path, "--reference", str(picked / "quarry.png"))
    result = call(project, "art_direction")
    data = json.loads(result.content[0].text)
    assert data["reference_images"] == [
        {"path": "art/reference/quarry.png", "shown": True}
    ]
    images = [c for c in result.content if c.type == "image"]
    assert len(images) == 1 and images[0].mime_type == "image/png"
