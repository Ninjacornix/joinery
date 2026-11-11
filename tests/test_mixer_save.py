"""The Mixer tab's Save mix in the chat host (bridge/mixer_save.py)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from bridge import mixer_save
from joinery import mixer, music_track
from joinery.mcp_runtime import ServerSpec
from tests.test_crew import CrewTest, FakeConnection
from tests.test_crew import project as crew_project  # noqa: F401 - the fixture
from tests.test_mixer import GODOT_SAVED


def _track() -> music_track.MusicTrack:
    layers = [
        music_track.Layer(name="pads", role="pads"),
        music_track.Layer(name="drums", role="percussion"),
    ]
    stems = {
        name: f"res://audio/music/theme/main_{name}.ogg" for name in ("pads", "drums")
    }
    return music_track.MusicTrack(
        name="theme",
        key="D minor",
        tempo_bpm=112,
        layers=layers,
        sections=[music_track.Section(name="main", bars=8, stems=stems)],
        states=music_track.default_states(layers, ["main"]),
    )


def _planted(root: Path) -> Path:
    (root / "project.godot").write_text("config_version=5\n")
    (root / "default_bus_layout.tres").write_text(GODOT_SAVED)
    (root / "music").mkdir(exist_ok=True)
    (root / "music/theme.tres").write_text(music_track.to_tres(_track()))
    return root


def _message() -> dict[str, Any]:
    return {
        "kind": "mixer_save",
        "bus_layout": {
            "path": "res://default_bus_layout.tres",
            "buses": [
                {"name": "Music", "volume_db": -6.0},
                {"name": "Voice", "mute": False},
            ],
            "ducking": "SFX",
        },
        "tracks": {
            "res://music/theme.tres": {
                "layers": {"pads": {"volume_db": -2.0}, "drums": {"muted": True}}
            }
        },
    }


def test_plan_writes_both_files_through_godot_with_plain_facts(tmp_path: Path) -> None:
    project = _planted(tmp_path)
    planned = mixer_save.plan(project, _message())
    assert planned.title == "Save the mix"
    assert planned.targets == ["default_bus_layout.tres", "music/theme.tres"]
    assert [(c.server, c.tool) for c in planned.calls] == [("godot", "write_file")] * 2
    layout_call, track_call = planned.calls
    assert layout_call.arguments["projectPath"] == str(project)
    assert layout_call.arguments["filePath"] == "default_bus_layout.tres"
    layout = mixer.from_tres(layout_call.arguments["content"])
    assert mixer.ducking(layout) == "SFX"
    music = layout.bus("Music")
    assert music is not None and music.volume_db == -6.0
    # The chorus the Mixer doesn't manage is still there.
    assert "AudioEffectChorus_snu11" in layout_call.arguments["content"]
    track = music_track.from_tres(track_call.arguments["content"])
    pads, drums = track.layers
    assert pads.volume_db == -2.0 and drums.muted and not pads.muted
    assert track.states == _track().states
    assert planned.facts == [
        "Music −6.0 dB",
        "Voice unmuted",
        "Music ducks under SFX",
        "theme: pads −2.0 dB; drums muted",
    ]
    assert "bus levels" in planned.request and "music layer mix" in planned.request


def test_plan_leaves_out_files_that_would_not_change(tmp_path: Path) -> None:
    project = _planted(tmp_path)
    message = _message()
    message["bus_layout"] = {"buses": [{"name": "Music", "volume_db": -3.5}]}
    (project / "default_bus_layout.tres").write_text(
        mixer.to_tres(mixer.from_tres(GODOT_SAVED))
    )
    planned = mixer_save.plan(project, message)
    assert planned.targets == ["music/theme.tres"]
    message.pop("tracks")
    with pytest.raises(ValueError, match="Nothing to save"):
        mixer_save.plan(project, message)


def test_a_project_without_a_layout_gets_one(tmp_path: Path) -> None:
    project = _planted(tmp_path)
    (project / "default_bus_layout.tres").unlink()
    planned = mixer_save.plan(
        project,
        {
            "bus_layout": {
                "buses": [{"name": "Music", "volume_db": -1.0}],
                "ducking": "Voice",
            }
        },
    )
    layout = mixer.from_tres(planned.calls[0].arguments["content"])
    assert [b.name for b in layout.buses] == ["Master", "Music"]
    assert mixer.ducking(layout) == "Voice"


@pytest.mark.parametrize(
    ("tracks", "error"),
    [
        ({"../outside/theme.tres": {}}, "Not a project file"),
        ({"audio/music/theme.tres": {}}, "live in music/"),
        ({"music/theme.ogg": {}}, "Not a Godot text resource"),
        ({"music/missing.tres": {"layers": {}}}, "No music track"),
        ({"music/theme.tres": {"layers": {"bass": {"muted": True}}}}, "no layer bass"),
    ],
)
def test_plan_refuses_paths_and_layers_it_cannot_write(
    tmp_path: Path, tracks: dict[str, Any], error: str
) -> None:
    project = _planted(tmp_path)
    with pytest.raises(ValueError, match=error):
        mixer_save.plan(project, {"tracks": tracks})


class GodotConnection(FakeConnection):
    """Godot's MCP server as far as Save mix uses it: ``write_file``."""

    def __init__(self, spec: ServerSpec, project: Path) -> None:
        super().__init__(spec, project)
        self.tools["write_file"] = object()

    async def call(
        self, tool: str, arguments: dict[str, Any], timeout: float = 0
    ) -> CallToolResult:
        if tool != "write_file":
            return await super().call(tool, arguments, timeout)
        target = Path(arguments["projectPath"]) / arguments["filePath"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(arguments["content"])
        return CallToolResult(content=[TextContent(type="text", text="ok")])


def test_save_mix_is_a_reviewed_change(
    crew_project: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _planted(crew_project)
    before = (project / "default_bus_layout.tres").read_text()
    test = CrewTest(project)

    async def connection(self: Any, server: str) -> GodotConnection:
        return GodotConnection(
            ServerSpec(name=server, command="x", source="first-party"), project
        )

    monkeypatch.setattr("joinery.gateway.ProjectGateway.connection", connection)
    # The change is made in the task's stage, by its own Godot worker.
    monkeypatch.setattr("joinery.gateway.MCPConnection", GodotConnection)

    class Installed:
        revision = "test"

    # The worker's spec names the pinned server; no installed worker is needed.
    monkeypatch.setattr(
        "joinery.server_install.load_installed", lambda _tool: Installed()
    )

    async def go() -> None:
        crew = test.crew()
        task = await mixer_save.start(crew, project, _message(), test.prompt())
        assert task.job is not None
        await task.job
        assert task.state == "in_review", task.message
        # Nothing is written before the user accepts.
        assert (project / "default_bus_layout.tres").read_text() == before
        preview = next(e for e in test.events if e["kind"] == "file_preview")
        assert "Music ducks under SFX" in preview["facts"]
        steps = [e["text"] for e in test.events if e["kind"] == "progress"]
        assert "Write the bus layout in Godot…" in steps
        await crew.decide(task.transaction_id, True)
        assert (
            mixer.ducking(
                mixer.from_tres((project / "default_bus_layout.tres").read_text())
            )
            == "SFX"
        )
        track = music_track.from_tres((project / "music/theme.tres").read_text())
        assert track.layers[1].muted
        await crew.close()
        await test.gateway.close()

    asyncio.run(go())
