"""Tool readiness: states, features, tiers and cache, with Docker replaced at its boundary.

These tests never start a container. They check that the one shared readiness
core turns check results into honest states and wording, so every front end
and every agent reads the same answer.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from joinery import readiness
from joinery.catalog import load_catalog
from joinery.readiness import Image, ProbeResult, lines, status
from joinery.tools.base import (
    BLENDER_WORKER,
    CREATIVE_WORKER,
    INKSCAPE_WORKER,
    MCP_WORKER,
    McpProbe,
)

IMAGES = (CREATIVE_WORKER, MCP_WORKER, BLENDER_WORKER, INKSCAPE_WORKER)

#: What a healthy worker prints, keyed by a fragment of the command.
HEALTHY: dict[str, tuple[int, str]] = {
    "JOINERY_RIGIFY_OK": (0, "Blender 5.2.1\nJOINERY_RIGIFY_OK\n"),
    "JOINERY_MPFB_OK": (0, "JOINERY_MPFB_OK\n"),
    "blender --version": (0, "Blender 5.2.1\nbuild date: 2026-10-01\n"),
    "joinery-krita": (
        0,
        "/usr/bin/krita\n/usr/local/bin/joinery-krita\n1:5.2.9+dfsg-1\n",
    ),
    "gimp-console --version": (0, "GNU Image Manipulation Program version 3.0.4\n"),
    "JOINERY_GIMP_OK": (0, "JOINERY_GIMP_OK 3.0.4\n"),
    "inkscape --version": (0, "Inkscape 1.4.2 (ebf0e940d0, 2025-05-08)\n"),
    "JOINERY_MM_OK": (0, "JOINERY_MM_OK\n"),
    "sclang -v": (0, "sclang 3.13.0 (Built from tag 'Version-3.13.0')\n"),
    "Surge XT.vst3": (0, "1.3.4\n"),
    "godot --version": (0, "4.7.2.stable.official.abcdef\n"),
    "joinery-surge --list": (0, '{"a/volume": 0.5}\n'),
    "JOINERY_AUDIO_EDIT_OK": (0, "4\n1\nJOINERY_AUDIO_EDIT_OK\n"),
    "FluidR3_GM": (
        0,
        (
            "FluidSynth runtime version 2.4.4\n"
            "74594e8f4250680adf590507a306655a299935343583256f3b722c48a1bc1cb0"
            "  /usr/share/sounds/sf2/FluidR3_GM.sf2\nJOINERY_MIDI_OK\n"
        ),
    ),
}


@dataclass
class FakeHost:
    docker_reason: str = ""
    images: dict[str, Image] = field(
        default_factory=lambda: {name: Image("sha256:" + name) for name in IMAGES}
    )
    outputs: dict[str, tuple[int, str]] = field(default_factory=lambda: dict(HEALTHY))
    missing_installs: dict[str, str] = field(default_factory=dict)
    setup_data: dict[str, Any] | None = None
    probes: dict[tuple[str, str], ProbeResult] = field(default_factory=dict)
    desktops: set[str] = field(default_factory=set)
    engine: Path | None = None
    engine_output: tuple[int, str] = (0, "4.7.2.stable.custom_build\n")
    revision: str = ""
    runs: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    probed: list[tuple[str, str]] = field(default_factory=list)

    def docker(self) -> str:
        return self.docker_reason

    def image(self, name: str) -> Image | None:
        return self.images.get(name)

    def run(
        self, image: str, argv: Sequence[str], entrypoint: str, timeout: int
    ) -> tuple[int, str]:
        self.runs.append((image, tuple(argv)))
        joined = " ".join(argv)
        for needle, result in self.outputs.items():
            if needle in joined:
                return result
        return 127, f"{argv[0]}: not found"

    def host_program(self, program: str) -> Path | None:
        return self.engine

    def host_run(
        self, binary: Path, args: Sequence[str], timeout: int
    ) -> tuple[int, str]:
        self.runs.append(("host", (str(binary), *args)))
        return self.engine_output

    def installed(self, tool: str) -> str:
        return self.missing_installs.get(tool, "")

    def tree(self, tool: str) -> str:
        return "tree-" + tool

    def setup(self) -> dict[str, Any] | None:
        return self.setup_data

    def expected_revision(self) -> str:
        return self.revision

    def mcp(self, project: Path, probe: McpProbe) -> ProbeResult:
        self.probed.append((probe.server, probe.tool))
        return self.probes.get((probe.server, probe.tool), ProbeResult(False, False))

    def desktop(self, project: Path | None, key: str) -> tuple[bool, str]:
        if key in self.desktops:
            return True, f"/apps/{key}"
        return (
            False,
            f"Application not found; configure [desktop.{key}] in joinery.toml",
        )


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "cache"
    monkeypatch.setenv("JOINERY_READINESS_CACHE", str(folder))
    return folder


@pytest.fixture
def host(tmp_path: Path) -> FakeHost:
    engine = tmp_path / "godot.macos.editor.arm64"
    engine.write_text("editor")
    return FakeHost(engine=engine)


def by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in report["tools"]}


def features(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {feature["id"]: feature for feature in entry["features"]}


def project(tmp_path: Path, enabled: list[str], extra: str = "") -> Path:
    root = tmp_path / "game"
    root.mkdir(exist_ok=True)
    (root / "joinery.toml").write_text(
        "[tools]\nenabled = " + json.dumps(enabled) + "\n" + extra
    )
    return root


def test_every_catalog_tool_is_ready_in_healthy_workers(host: FakeHost) -> None:
    tools = by_id(status(catalog=True, refresh=True, host=host))
    assert set(tools) == {tool.id for tool in load_catalog().tool}
    assert {entry["state"] for entry in tools.values()} == {"ready"}
    assert {i: tools[i]["version"] for i in tools} == {
        "godot": "4.7.2",
        "blender": "5.2.1",
        "krita": "5.2.9",
        "gimp": "3.0.4",
        "inkscape": "1.4.2",
        "material-maker": "1.8",
        "supercollider": "3.13.0",
        "surge-xt": "1.3.4",
    }
    assert tools["blender"]["summary"].startswith(
        "Blender 5.2.1 ready (offline worker). Rigify, MPFB2 humans"
    )
    # Surge XT shows what it can really do: MIDI music and audio edits.
    assert tools["surge-xt"]["summary"] == (
        "Surge XT 1.3.4 ready: renders offline in the creative worker. "
        "MIDI music (General MIDI and Surge XT), audio edits (trim, fade, "
        "loudness, loops)."
    )
    surge = features(tools["surge-xt"])
    assert surge["music"]["status"] == "ready" and surge["audio-edit"]["status"] == "ready"
    assert all(entry["status"] == "ready" for entry in tools.values())
    assert all(entry["action"] is None for entry in tools.values())
    assert all(entry["status_reason"] == "" for entry in tools.values())
    assert not any(entry["checking"] for entry in tools.values())
    # Every worker command runs offline in a worker, never on the host PATH.
    assert all(image in IMAGES or image == "host" for image, _ in host.runs)


def test_docker_down_is_needs_docker_and_runs_nothing(host: FakeHost) -> None:
    host.docker_reason = "Docker is not running."
    report = status(catalog=True, refresh=True, host=host)
    tools = by_id(report)
    assert report["docker"] == "Docker is not running."
    for tool_id in ("blender", "krita", "gimp", "surge-xt", "godot"):
        assert tools[tool_id]["state"] == "needs_docker"
        assert tools[tool_id]["status"] == "needs_docker"
        assert tools[tool_id]["action"] == {"id": "start_docker", "label": "Start Docker"}
    assert tools["krita"]["summary"] == (
        "Krita runs offline in Docker. Start Docker Desktop, then check again."
    )
    assert tools["krita"]["status_reason"] == tools["krita"]["summary"]
    assert tools["krita"]["status_label"] == "Needs Docker"
    assert [run for run in host.runs if run[0] != "host"] == []


def test_missing_worker_image_needs_setup_with_the_tools_own_wording(
    host: FakeHost,
) -> None:
    del host.images[CREATIVE_WORKER]
    tools = by_id(status(catalog=True, refresh=True, host=host))
    assert tools["blender"]["state"] == "needs_image"
    assert (
        tools["blender"]["summary"]
        == "Blender worker image not built. Rebuild the worker."
    )
    assert tools["gimp"]["summary"].startswith("GIMP runs in the creative worker")
    assert tools["blender"]["status"] == "unavailable"
    assert tools["blender"]["action"] == {"id": "rebuild", "label": "Rebuild"}
    # Features on that image are not ready either; no command ran in it.
    assert features(tools["blender"])["mpfb"]["state"] == "needs_image"
    assert not any(image == CREATIVE_WORKER for image, _ in host.runs)


def test_mpfb_missing_degrades_only_that_feature(host: FakeHost) -> None:
    host.outputs["JOINERY_MPFB_OK"] = (
        1,
        "Traceback (most recent call last):\nAssertionError",
    )
    blender = by_id(status(tool="blender", refresh=True, host=host))["blender"]
    assert blender["state"] == "ready"
    mpfb = features(blender)["mpfb"]
    assert mpfb["state"] == "needs_image"
    assert "worker image predates the fix" in mpfb["detail"]
    assert mpfb["action"] == {"id": "rebuild", "label": "Rebuild"}
    assert mpfb["status"] == "unavailable"
    assert features(blender)["rigify"]["state"] == "ready"
    assert "MPFB2" not in blender["summary"]
    assert "MPFB2 humans unavailable" in "\n".join(lines({"tools": [blender]}))


def test_program_missing_from_a_present_image_names_the_rebuild(host: FakeHost) -> None:
    host.outputs["Surge XT.vst3"] = (1, "")
    surge = by_id(status(tool="surge-xt", refresh=True, host=host))["surge-xt"]
    assert surge["state"] == "needs_image"
    assert surge["summary"] == (
        "Surge XT: the plugin is missing from the worker; rebuild the creative worker"
    )


def test_gimp_missing_from_the_worker_needs_a_rebuild_not_broken(
    host: FakeHost,
) -> None:
    # A creative worker built without GIMP: the version check fails first, so
    # the Python-Fu probe never runs and cannot turn this into "failed to start".
    del host.outputs["gimp-console --version"]
    del host.outputs["JOINERY_GIMP_OK"]
    gimp = by_id(status(tool="gimp", refresh=True, host=host))["gimp"]
    assert gimp["state"] == "needs_image"
    assert gimp["summary"] == "GIMP is missing from the creative worker; rebuild it"
    assert not any("JOINERY_GIMP_OK" in " ".join(argv) for _, argv in host.runs)


def test_failing_required_check_is_broken_with_its_reason(host: FakeHost) -> None:
    host.engine_output = (139, "Segmentation fault\n")
    godot = by_id(status(tool="godot", refresh=True, host=host))["godot"]
    assert godot["state"] == "broken"
    assert (
        godot["summary"]
        == "Godot tools: error: The Joinery editor binary did not start"
    )


def test_failing_check_shows_the_first_line_of_output(host: FakeHost) -> None:
    # GIMP's Python-Fu is its adapter, so a scripting failure breaks the tool.
    host.outputs["JOINERY_GIMP_OK"] = (
        64,
        "\nbatch command experienced a calling error: RuntimeError: boom\n",
    )
    gimp = by_id(status(tool="gimp", refresh=True, host=host))["gimp"]
    assert gimp["state"] == "broken"
    assert gimp["summary"] == (
        "GIMP failed to start: batch command experienced a calling error: "
        "RuntimeError: boom"
    )


def test_missing_pinned_install_needs_setup(host: FakeHost) -> None:
    host.missing_installs["godot"] = (
        "Installed MCP source or build changed. Reinstall it."
    )
    host.missing_installs["blender"] = "not installed"
    tools = by_id(status(catalog=True, refresh=True, host=host))
    assert tools["godot"]["state"] == "needs_image"
    assert tools["godot"]["summary"] == "Godot tools: not installed. Rebuild the worker."
    mcp = features(tools["blender"])["mcp"]
    assert mcp["state"] == "needs_image"
    assert mcp["detail"].startswith("Blender MCP not installed. Rebuild the worker.")
    assert tools["blender"]["state"] == "ready"


def test_setup_in_progress_is_downloading(host: FakeHost) -> None:
    host.setup_data = {"phase": "build", "image": CREATIVE_WORKER, "pid": os.getpid()}
    tools = by_id(status(catalog=True, refresh=True, host=host))
    assert tools["krita"]["state"] == "downloading"
    assert tools["krita"]["summary"] == "Krita: setting up the offline worker…"
    assert tools["krita"]["status"] == "preparing"
    assert tools["krita"]["status_label"] == "Downloading…"
    assert tools["krita"]["action"] is None
    # Godot's own worker is not the one being built.
    assert tools["godot"]["state"] == "ready"


def test_stale_image_revision_needs_setup(host: FakeHost) -> None:
    host.revision = "rev-2"
    host.images[CREATIVE_WORKER] = Image("sha256:old", revision="rev-1")
    assert by_id(status(tool="krita", refresh=True, host=host))["krita"]["state"] == (
        "needs_image"
    )


def test_cache_miss_reports_checking_without_running_a_worker(host: FakeHost) -> None:
    tools = by_id(status(catalog=True, host=host))
    assert host.runs == []
    assert all(entry["checking"] for entry in tools.values())
    assert tools["krita"]["state"] == "ready"
    assert tools["krita"]["summary"].startswith("Checking Krita…")


def test_cached_results_are_reused_until_the_image_changes(
    host: FakeHost, cache: Path
) -> None:
    status(catalog=True, refresh=True, host=host)
    assert (cache / "readiness.json").is_file()
    host.runs.clear()
    tools = by_id(status(catalog=True, host=host))
    assert host.runs == []
    assert not any(entry["checking"] for entry in tools.values())
    assert tools["blender"]["version"] == "5.2.1"
    # A rebuilt image has a new id, so its checks are unknown again.
    host.images[CREATIVE_WORKER] = Image("sha256:rebuilt")
    tools = by_id(status(catalog=True, host=host))
    assert tools["krita"]["checking"] is True
    assert host.runs == []


def test_cached_failure_keeps_its_wording(host: FakeHost) -> None:
    host.outputs["Surge XT.vst3"] = (1, "")
    status(tool="surge-xt", refresh=True, host=host)
    surge = by_id(status(tool="surge-xt", host=host))["surge-xt"]
    assert surge["summary"].startswith("Surge XT: the plugin is missing")


def test_timed_out_check_is_reported_but_never_cached(host: FakeHost) -> None:
    # A slow emulated start says nothing lasting about the image; caching it
    # would leave the tool broken until someone forced a refresh.
    host.outputs["Surge XT.vst3"] = (124, "timed out after 60 s")
    surge = by_id(status(tool="surge-xt", refresh=True, host=host))["surge-xt"]
    # Not "the plugin is missing": the check never got to answer.
    assert surge["state"] == "broken"
    assert surge["summary"] == "Surge XT failed its check: timed out after 60 s"
    surge = by_id(status(tool="surge-xt", host=host))["surge-xt"]
    assert surge["checking"] is True
    # A real failure with the same exit code is still an answer about the image.
    host.outputs["Surge XT.vst3"] = (124, "surge: exit 124\n")
    status(tool="surge-xt", refresh=True, host=host)
    surge = by_id(status(tool="surge-xt", host=host))["surge-xt"]
    assert surge["checking"] is False
    assert surge["state"] == "needs_image"


def test_background_refresh_releases_its_lock_when_done(
    host: FakeHost, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    monkeypatch.setattr(readiness, "DockerHost", lambda: host)
    cache.mkdir(parents=True, exist_ok=True)
    lock = cache / "readiness.pid"
    lock.write_text(str(os.getpid()))
    assert readiness.main(["--tool", "krita", "--refresh", "--quiet"]) == 0
    assert not lock.exists()
    # Another process's lock is left alone.
    lock.write_text(str(os.getpid() + 1))
    assert readiness.main(["--tool", "krita", "--refresh", "--quiet"]) == 0
    assert lock.exists()


def test_project_lists_its_selected_tools_custom_tools_and_removed_notices(
    host: FakeHost, tmp_path: Path
) -> None:
    root = project(
        tmp_path,
        ["krita", "aseprite"],
        '[[tools.custom]]\nid = "my-synth"\nname = "My Synth"\ndomain = "audio"\n'
        'server = "my-synth"\ncommand = "my-synth-mcp"\n',
    )
    report = status(root, refresh=True, host=host)
    tools = by_id(report)
    assert list(tools) == ["godot", "krita", "my-synth"]
    assert tools["krita"]["selected"] is True
    assert tools["my-synth"]["custom"] is True
    assert "Joinery does not check it" in tools["my-synth"]["summary"]
    assert len(report["removed"]) == 1
    assert report["removed"][0].startswith("Aseprite was removed")
    assert "Use Krita for pixel art" in report["removed"][0]


def test_catalog_view_marks_what_the_project_selected(
    host: FakeHost, tmp_path: Path
) -> None:
    tools = by_id(status(project(tmp_path, ["krita"]), catalog=True, host=host))
    assert tools["krita"]["selected"] and tools["godot"]["selected"]
    assert not tools["blender"]["selected"]


def test_mcp_probe_runs_only_for_a_selected_tool_and_checks_capabilities(
    host: FakeHost, tmp_path: Path
) -> None:
    capabilities = {
        "tools": [{"id": "krita", "application_installed": True}],
        "runtime_available": True,
    }
    host.probes[("joinery-paint", "document_capabilities")] = ProbeResult(
        True, True, json.dumps(capabilities)
    )
    root = project(tmp_path, ["krita"])
    krita = by_id(status(root, tool="krita", refresh=True, host=host))["krita"]
    assert krita["state"] == "ready"
    assert ("joinery-paint", "document_capabilities") in host.probed
    capabilities["tools"] = [{"id": "krita", "application_installed": False}]
    host.probes[("joinery-paint", "document_capabilities")] = ProbeResult(
        True, True, json.dumps(capabilities)
    )
    krita = by_id(status(root, tool="krita", refresh=True, host=host))["krita"]
    assert krita["state"] == "needs_image"
    host.probed.clear()
    status(catalog=True, refresh=True, host=host)
    assert host.probed == []


def test_failed_mcp_probe_is_broken_and_never_cached(
    host: FakeHost, tmp_path: Path
) -> None:
    root = project(tmp_path, ["supercollider"])
    host.probes[("claude-collider", "cc_status")] = ProbeResult(
        True,
        False,
        "claude-collider could not connect or complete a request (TimeoutError).",
    )
    sc = by_id(status(root, tool="supercollider", refresh=True, host=host))[
        "supercollider"
    ]
    live = features(sc)["live-audition"]
    assert live["state"] == "broken"
    assert "could not connect" in live["detail"]
    sc = by_id(status(root, tool="supercollider", host=host))["supercollider"]
    assert features(sc)["live-audition"]["checking"] is True


def test_mcp_probe_expectation_must_match(host: FakeHost, tmp_path: Path) -> None:
    root = project(tmp_path, ["supercollider"])
    host.probes[("claude-collider", "cc_status")] = ProbeResult(
        True, True, "Server: stopped"
    )
    sc = by_id(status(root, tool="supercollider", refresh=True, host=host))[
        "supercollider"
    ]
    assert features(sc)["live-audition"]["state"] == "broken"
    host.probes[("claude-collider", "cc_status")] = ProbeResult(
        True, True, "Server: running"
    )
    sc = by_id(status(root, tool="supercollider", refresh=True, host=host))[
        "supercollider"
    ]
    assert features(sc)["live-audition"]["state"] == "ready"
    assert "Live audition" in sc["summary"]


def test_desktop_app_is_separate_and_never_changes_state(host: FakeHost) -> None:
    tools = by_id(status(catalog=True, refresh=True, host=host))
    assert tools["krita"]["state"] == "ready"
    assert tools["krita"]["desktop"] == {
        "available": False,
        "path": "",
        "label": "Desktop Krita not found: Open in Krita is unavailable. Editing still works offline.",
    }
    host.desktops.add("krita")
    krita = by_id(status(tool="krita", host=host))["krita"]
    assert krita["desktop"]["available"] is True
    assert krita["desktop"]["label"] == "Open in Krita"


def test_deep_checks_run_only_on_request(host: FakeHost) -> None:
    status(tool="surge-xt", refresh=True, host=host)
    assert not any("--list" in argv for _, argv in host.runs)
    status(tool="surge-xt", refresh=True, deep=True, host=host)
    assert any("--list" in argv for _, argv in host.runs)


def test_unknown_tool_is_refused(host: FakeHost) -> None:
    with pytest.raises(ValueError, match="Unknown tool"):
        status(tool="aseprite", host=host)


def test_json_entry_point_prints_the_report(
    host: FakeHost, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(readiness, "DockerHost", lambda: host)
    assert readiness.main(["--tool", "krita", "--refresh"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["docker"] == "ok"
    assert [entry["id"] for entry in data["tools"]] == ["krita"]
    assert data["tools"][0]["state"] == "ready"
    assert readiness.main(["--tool", "nope"]) == 2
    assert "Unknown tool" in json.loads(capsys.readouterr().out)["error"]
    assert readiness.main(["--tool", "krita", "--text"]) == 0
    assert "[ok  ] Krita" in capsys.readouterr().out


def test_background_refresh_is_spawned_once(
    host: FakeHost, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    started: list[list[str]] = []

    class Process:
        pid = os.getpid()

    def popen(argv: list[str], **options: object) -> Process:
        assert options["start_new_session"] is True
        started.append(argv)
        return Process()

    monkeypatch.setattr(readiness.subprocess, "Popen", popen)
    assert readiness.spawn_refresh(None) is True
    # Only the missing checks run in the background; a full re-check is asked for.
    assert started[0][1:4] == ["-m", "joinery.readiness", "--fill"]
    # The lock names a live process, so a second request waits for it.
    assert readiness.spawn_refresh(None) is False
    assert len(started) == 1


def test_real_worker_command_is_offline_and_amd64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def run(argv: list[str], **_: object) -> Done:
        seen.append(argv)
        return Done()

    monkeypatch.setattr(readiness.subprocess, "run", run)
    code, _ = readiness.DockerHost().run(
        CREATIVE_WORKER, ["sclang", "-v"], "/usr/local/bin/joinery-sandbox", 30
    )
    assert code == 0
    argv = seen[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--platform") + 1] == "linux/amd64"
    assert argv[argv.index("--entrypoint") + 1] == "/usr/local/bin/joinery-sandbox"
    assert argv[-3:] == [CREATIVE_WORKER, "sclang", "-v"]


# --- One source for every screen -------------------------------------------------


def test_one_fake_status_flows_unchanged_into_every_surface(
    host: FakeHost, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The setup menu, the asset workspace and doctor all show the core's answer."""
    from joinery import doctor, workspace
    from joinery.catalog import load_project_tools
    from joinery.installer import tool_menu

    monkeypatch.setattr(readiness, "DockerHost", lambda: host)
    monkeypatch.setattr(readiness, "spawn_refresh", lambda *_a, **_k: False)
    # One distinctive answer: Krita's worker predates its launcher.
    host.outputs["joinery-krita"] = (1, "")
    root = project(tmp_path, ["krita"])
    (root / "project.godot").write_text("config_version=5\n")
    report = by_id(status(root, refresh=True, host=host))
    krita = report["krita"]
    assert krita["status"] == "unavailable"
    assert krita["action"] == {"id": "rebuild", "label": "Rebuild"}
    fields = ("status", "status_label", "status_reason", "action", "checked_at")
    expected = {key: krita[key] for key in fields}

    menu = tool_menu(load_catalog(), load_project_tools(root / "joinery.toml"))
    row = next(t for t in menu["tools"] if t["id"] == "krita")  # type: ignore[union-attr]
    assert {key: row[key] for key in fields} == expected

    tools = {t["id"]: t for t in workspace.index(root)["tools"]}
    assert {key: tools["krita"][key] for key in fields} == expected

    text, code = doctor.report(root / "joinery.toml", host=host)
    assert krita["summary"] in text
    assert code == 1


def test_fresh_snapshot_answers_without_asking_docker(host: FakeHost) -> None:
    status(catalog=True, refresh=True, host=host)
    host.docker_reason = "Docker is not running."
    report = readiness.current(catalog=True, host=host, background=False)
    assert report["cached"] is True
    assert {t["status"] for t in report["tools"]} == {"ready"}
    # Past its age, the cheap checks run again and see Docker is down.
    report = readiness.current(catalog=True, host=host, max_age=-1, background=False)
    assert "cached" not in report
    assert {t["status"] for t in report["tools"]} == {"needs_docker"}


def test_snapshot_is_skipped_while_setup_builds(host: FakeHost) -> None:
    status(catalog=True, refresh=True, host=host)
    host.setup_data = {"phase": "build", "image": CREATIVE_WORKER, "pid": os.getpid()}
    tools = by_id(readiness.current(catalog=True, host=host, background=False))
    assert tools["krita"]["status"] == "preparing"


def test_current_refreshes_missing_checks_in_the_background(
    host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        readiness, "spawn_refresh", lambda *a, **k: spawned.append(a) is None
    )
    report = readiness.current(tool="krita", host=host)
    assert report["checking"] is True and report["refreshing"] is True
    assert host.runs == [] and spawned


def test_cached_tools_reads_the_last_answer_and_nothing_else(
    host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert readiness.cached_tools() == {}
    status(tool="krita", refresh=True, host=host)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cached_tools never starts a process")

    monkeypatch.setattr(readiness.subprocess, "run", refuse)
    cached = readiness.cached_tools()
    assert cached["krita"]["status"] == "ready"
    assert cached["krita"]["checked_at"].endswith("+00:00")


def test_fill_runs_only_what_the_cache_lacks(host: FakeHost) -> None:
    status(tool="krita", refresh=True, host=host)
    host.runs.clear()
    status(tool="krita", fill=True, host=host)
    assert host.runs == []
    status(tool="blender", fill=True, host=host)
    assert any(image == BLENDER_WORKER for image, _ in host.runs)


def test_background_flag_answers_from_the_snapshot(
    host: FakeHost, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(readiness, "DockerHost", lambda: host)
    status(catalog=True, refresh=True, host=host)
    host.docker_reason = "Docker is not running."
    assert readiness.main(["--catalog", "--background", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["cached"] is True and data["checked_at"]
    monkeypatch.setattr(
        readiness,
        "agents",
        lambda: {"codex": {"summary": "Codex: signed in", "verified": True}},
    )
    assert readiness.main(["--tool", "krita", "--agents"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["agents"]["codex"]["verified"] is True


def test_setup_marker_is_read_while_its_process_lives(cache: Path) -> None:
    assert readiness.DockerHost().setup() is None
    readiness.mark_setup("build", CREATIVE_WORKER)
    data = readiness.DockerHost().setup()
    assert data is not None
    assert data["image"] == CREATIVE_WORKER and data["pid"] == os.getpid()
    assert data["phase"] == "build" and data["started"] > 0
    readiness.clear_setup()
    assert not (cache / "setup.json").exists()


def test_sandbox_revision_follows_the_build_sources(tmp_path: Path) -> None:
    sources = tmp_path / "sandbox"
    sources.mkdir()
    assert readiness.sandbox_revision(sources) == ""
    (sources / "mcp.Dockerfile").write_text("FROM base\n")
    first = readiness.sandbox_revision(sources)
    assert len(first) == 16
    (sources / "mcp.Dockerfile").write_text("FROM base\nRUN true\n")
    assert readiness.sandbox_revision(sources) != first
    assert readiness.sandbox_revision() == readiness.sandbox_revision(readiness.SANDBOX_DIR)


def test_setup_marks_progress_labels_images_and_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    from joinery import bootstrap

    root = project(tmp_path, ["krita"])
    builds: list[list[str]] = []
    seen_markers: list[dict[str, Any]] = []
    refreshed: list[tuple[Any, ...]] = []

    class Done:
        returncode = 0

    def run(argv: list[str], **_: object) -> Done:
        if argv[:3] == ["docker", "image", "inspect"]:
            Done.returncode = 1
            return Done()
        Done.returncode = 0
        builds.append(argv)
        return Done()

    def build(argv: list[str], timeout: int = 1800) -> None:
        builds.append(argv)
        seen_markers.append(json.loads((cache / "setup.json").read_text()))

    monkeypatch.setattr(bootstrap.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    monkeypatch.setattr(bootstrap, "_run", build)
    monkeypatch.setattr(bootstrap, "install", lambda _tool: None)
    monkeypatch.setattr(
        readiness, "spawn_refresh", lambda *a, **k: refreshed.append((a, k)) is None
    )
    monkeypatch.delenv("JOINERY_SANDBOX_REV", raising=False)
    result = bootstrap.setup(root)
    revision = readiness.sandbox_revision()
    assert result["revision"] == revision and result["refreshing"] is True
    assert os.environ["JOINERY_SANDBOX_REV"] == revision
    images = [marker["image"] for marker in seen_markers]
    assert images == [
        "joinery-sandbox-runtime",
        "joinery-mcp-worker",
        "joinery-creative-worker",
    ]
    assert all(marker["pid"] == os.getpid() for marker in seen_markers)
    label = f"{readiness.SETUP_LABEL}={revision}"
    docker_builds = [argv for argv in builds if argv[:2] == ["docker", "build"]]
    assert len(docker_builds) == 3  # the runtime stamp and two workers
    assert all(label in argv for argv in docker_builds)
    # Setup never removes images or build cache.
    assert not any("prune" in argv or "rmi" in argv for argv in builds)
    assert not (cache / "setup.json").exists()
    assert refreshed and refreshed[0][1] == {"full": True, "catalog": True}
    monkeypatch.delenv("JOINERY_SANDBOX_REV", raising=False)


def test_mcp_probes_come_from_the_tool_modules() -> None:
    paint = readiness.mcp_probes("joinery-paint")
    assert {probe.capability for probe in paint} >= {"krita", "gimp", "inkscape"}
    only_krita = readiness.mcp_probes("joinery-paint", ["krita"])
    assert [probe.capability for probe in only_krita] == ["krita"]
    assert [p.tool for p in readiness.mcp_probes("claude-collider")] == ["cc_status"]
    assert readiness.mcp_probes("joinery-materialmaker")
    (krita,) = only_krita
    missing = json.dumps(
        {"runtime_available": True, "tools": [{"id": "krita", "application_installed": False}]}
    )
    assert readiness.judge_probe(krita, True, missing)[0] == "needs_image"
    assert readiness.judge_probe(krita, False, "boom")[0] == "broken"
