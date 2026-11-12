#!/usr/bin/env python3
"""Audacity, headless in the offline worker, driven through mod-script-pipe.

  joinery-audacity RECIPE.json   replay a typed operation log (a recipe script)
  joinery-audacity --check       start Audacity, answer one command, print its version
  joinery-audacity --commands    print Audacity's scripting commands as JSON
  joinery-audacity --serve-mcp   start Audacity, then the pinned Audacity MCP on stdio

Every run starts a fresh Audacity with a fresh settings folder, so nothing a
previous run left behind can change the result: no dither on export (it adds
random noise), no first-run dialogs, mod-script-pipe enabled. A recipe lists
typed operations; this script turns each into Audacity's own scripting
commands, logs every command it sends, and writes the outputs the recipe names.
Raw scripting commands are accepted only as a `macro` step, which the log marks
as raw.

Standard library only: it runs on the worker's system Python.
"""

from __future__ import annotations

import json
import math
import os
import pwd
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

WORK = Path("/work")
START_TIMEOUT = float(os.environ.get("JOINERY_AUDACITY_START_TIMEOUT", "300"))
#: One command; a modal dialog never answers, so this is what a run waits
#: before it reports the command that stuck.
COMMAND_TIMEOUT = float(os.environ.get("JOINERY_AUDACITY_COMMAND_TIMEOUT", "180"))
RECIPE_FORMAT = 1
#: The plugin registry recorded at image build (see write_settings).
PLUGIN_TEMPLATE = Path(os.environ.get("JOINERY_AUDACITY_PLUGINS", "/opt/joinery/audacity-plugins"))
PLUGIN_FILES = ("pluginregistry.cfg", "pluginsettings.cfg")

#: Scripting commands a `macro` step may not send: files, the application,
#: preferences (dither or formats would make a replay differ), transport and
#: Nyquist code. Files are written only by the recipe's own outputs.
REFUSED_COMMANDS = frozenset(
    {
        "Import2",
        "Export2",
        "ExportMp3",
        "ExportWav",
        "ExportOgg",
        "ExportMultiple",
        "ExportLabels",
        "ExportMIDI",
        "ImportLabels",
        "ImportMIDI",
        "ImportRaw",
        "OpenProject2",
        "SaveProject2",
        "SaveCopy",
        "Save",
        "SaveAs",
        "Open",
        "New",
        "Close",
        "Exit",
        "Screenshot",
        "SetPreference",
        "Preferences",
        "PlayStop",
        "Play",
        "Record1stChoice",
        "Record2ndChoice",
        "TimerRecord",
        "PunchAndRoll",
        "NyquistPrompt",
        "Nyquist Prompt",
        "NyquistPlugInInstaller",
        "ManageMacros",
        "ApplyMacro",
        "PluginManager",
        "ResetConfig",
    }
)
#: Text Audacity takes inside a quoted parameter: no quotes, backslashes or
#: control characters, which would end the parameter or the command.
_TEXT = re.compile(r"^[^\"\\\x00-\x1f]{0,200}$")


class AudacityError(RuntimeError):
    """Audacity refused or failed a command."""


# --- Starting Audacity -------------------------------------------------------------


def _version() -> str:
    got = subprocess.run(
        ["dpkg-query", "-W", "-f=${Version}", "audacity"],
        capture_output=True,
        text=True,
        check=True,
    )
    return got.stdout.strip()


def _script_pipe_module() -> Path:
    for pattern in ("/usr/lib/*/audacity/modules", "/usr/lib/audacity/modules"):
        for folder in sorted(Path("/").glob(pattern.lstrip("/"))):
            found = folder / "mod-script-pipe.so"
            if found.is_file():
                return found
    raise AudacityError("mod-script-pipe is missing from the worker")


def noise_settings(steps: list[Any]) -> str:
    """Settings-file lines for the recipe's noise_reduction step, if any.

    Audacity's Noise Reduction takes no scripting parameters; it reads its
    amounts from the settings file once, when Audacity starts.
    """
    found = [s for s in steps if isinstance(s, dict) and s.get("op") == "noise_reduction"]
    if not found:
        return ""
    if len(found) > 1:
        raise ValueError(
            "noise_reduction: one noise profile per recipe; Audacity keeps "
            "only the last profile it captured"
        )
    step = found[0]
    reduction = _number(step, "reduction_db", 0, 48, 12)
    sensitivity = _number(step, "sensitivity", 0, 24, 6)
    smoothing = _number(step, "smoothing", 0, 12, 3)
    return (
        "[Effects/NoiseReduction]\n"
        f"Gain={reduction:g}\nSensitivity={sensitivity:g}\n"
        f"FreqSmoothing={int(smoothing)}\nReductionChoice=0\n"
    )


def write_settings(home: Path, extra: str = "") -> None:
    """A fresh Audacity settings file: pipe enabled, no dialogs, no dither."""
    module = _script_pipe_module()
    stamp = datetime.fromtimestamp(module.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
    major, minor, micro = _version().split("+")[0].split("-")[0].split(".")[:3]
    text = (
        "PrefsVersion=1.1.1r1\n"
        # Without a [Version] matching the installed release, Audacity treats
        # the run as a fresh install: it resets settings and opens a modal
        # "What's new" dialog that blocks every command.
        f"[Version]\nMajor={major}\nMinor={minor}\nMicro={micro}\n"
        "[GUI]\nShowSplashScreen=0\nBeepOnCompletion=0\n"
        # A module is reset to "new" (never loaded) unless its recorded path
        # and file date match the file on disk.
        "[Module]\nmod-script-pipe=1\n"
        f"[ModulePath]\nmod-script-pipe={module}\n"
        f"[ModuleDateTime]\nmod-script-pipe={stamp}\n"
        # Dither adds random noise to every export that reduces bit depth, so
        # two replays would never match. Exports stay exact without it.
        "[Quality]\nDitherAlgorithmChoice=None\nHQDitherAlgorithmChoice=None\n"
        "DitherAlgorithm=0\nHQDitherAlgorithm=0\n"
        "[Warnings]\nFirstProjectSave=0\nMixMono=0\nMixStereo=0\n"
        "MixUnknownChannels=0\nEmptyProject=0\n"
        "[Update]\nDefaultUpdatesChecking=0\nUpdateNoticeShown=1\n"
        "[SendAnonymousUsageInfo]\nAllowed=0\n" + extra
    )
    for folder in (home / ".audacity-data", home / ".config/audacity"):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "audacity.cfg").write_text(text)
        # Audacity checks every plugin missing from its registry in a child
        # `audacity --host` process. That child loads mod-script-pipe too,
        # recreates both pipes and exits, leaving the real Audacity waiting on
        # pipes no client can reach. The image records the registry once
        # (--prime) and every run starts from it, so no check runs.
        for name in PLUGIN_FILES:
            if (PLUGIN_TEMPLATE / name).is_file():
                shutil.copyfile(PLUGIN_TEMPLATE / name, folder / name)


def prime(target: Path) -> None:
    """Let Audacity scan its plugins once and keep the registry it writes."""
    with tempfile.TemporaryDirectory(prefix="joinery-audacity-") as directory:
        home = Path(directory) / "home"
        write_settings(home)
        audacity = Audacity(home, Path(directory) / "audacity.log")
        audacity.launch()
        deadline = time.monotonic() + START_TIMEOUT
        audacity._settle(deadline)
        while _children(audacity.process.pid if audacity.process else 0):
            audacity._alive(deadline, "its plugin scan never finished")
            time.sleep(0.5)
        audacity._settle(deadline)
        audacity.stop()
        found = [home / ".audacity-data" / name for name in PLUGIN_FILES]
        if not all(path.is_file() and path.stat().st_size for path in found):
            raise AudacityError(f"Audacity wrote no plugin registry. {audacity.tail()}")
        target.mkdir(parents=True, exist_ok=True)
        for path in found:
            shutil.copyfile(path, target / path.name)


def _children(pid: int) -> list[int]:
    found = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                parent = int((entry / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue
            if parent == pid:
                found.append(int(entry.name))
    return found


def _wait_for_display(seconds: float = 60) -> None:
    """Wait until the virtual display accepts a connection.

    The image's entrypoint starts Xvfb and waits only briefly for its socket
    file. Without a display Audacity exits at once with status 255 and prints
    nothing, so say what is missing instead.
    """
    display = os.environ.get("DISPLAY", ":99")
    path = f"/tmp/.X11-unix/X{display.lstrip(':').split('.')[0]}"
    deadline = time.monotonic() + seconds
    while True:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            try:
                probe.connect(path)
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise AudacityError(
                        f"the virtual display {display} never accepted a connection"
                    ) from None
        time.sleep(0.2)


def _pipes() -> tuple[Path, Path]:
    uid = os.getuid()
    return (
        Path(f"/tmp/audacity_script_pipe.to.{uid}"),
        Path(f"/tmp/audacity_script_pipe.from.{uid}"),
    )


def _cpu_ticks(pid: int) -> int:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])


class Audacity:
    """One disposable Audacity and its script pipe."""

    def __init__(
        self, home: Path, log: Path, settings: str = "", *, connect: bool = True
    ) -> None:
        self.home = home
        self.settings = settings
        #: False leaves the script pipe for another client (the MCP): Audacity
        #: serves one connection, and ends its pipe server when it closes.
        self.connect = connect
        self.log_path = log
        self.process: subprocess.Popen[bytes] | None = None
        self._to: Any = None
        self._from: Any = None
        self.sent: list[str] = []
        self.started_seconds = 0.0

    def start(self) -> None:
        if not all((PLUGIN_TEMPLATE / name).is_file() for name in PLUGIN_FILES):
            raise AudacityError(
                f"no plugin registry in {PLUGIN_TEMPLATE}; the image runs "
                "joinery-audacity --prime when it is built"
            )
        began = time.monotonic()
        write_settings(self.home, self.settings)
        self.launch()
        self._connect(began)

    def launch(self) -> None:
        _wait_for_display()
        for pipe in _pipes():
            pipe.unlink(missing_ok=True)
        # Audacity's own temporary projects; one left behind would open a
        # crash-recovery dialog that blocks every command.
        shutil.rmtree(f"/var/tmp/audacity-{pwd.getpwuid(os.getuid()).pw_name}", ignore_errors=True)
        env = dict(
            os.environ,
            HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / ".config"),
            XDG_DATA_HOME=str(self.home / ".local/share"),
            XDG_CACHE_HOME=str(self.home / ".cache"),
            LANG="C.UTF-8",
            LC_ALL="C.UTF-8",
        )
        # `docker exec` into a warm worker does not inherit the entrypoint's
        # DISPLAY; its Xvfb is always :99.
        env.setdefault("DISPLAY", ":99")
        self._log = self.log_path.open("wb")
        self.process = subprocess.Popen(
            ["audacity"],
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            env=env,
        )

    def _connect(self, began: float) -> None:
        deadline = time.monotonic() + START_TIMEOUT
        to_pipe, from_pipe = _pipes()
        while not (to_pipe.exists() and from_pipe.exists()):
            self._alive(deadline, "its script pipe never opened")
            time.sleep(0.1)
        first = to_pipe.stat().st_ino
        # The pipe opens long before Audacity can run commands: a command sent
        # too early gets an empty reply, and dropping that connection crashes
        # it. Its CPU use settles once the main window is up.
        self._settle(deadline)
        if not to_pipe.exists() or to_pipe.stat().st_ino != first:
            # A plugin check in a child `audacity --host` replaced the pipes;
            # Audacity itself waits on the old ones and can never answer.
            raise AudacityError(
                "Audacity checked a plugin at startup and its script pipe was "
                "replaced; rebuild the worker so --prime records every plugin"
            )
        # mod-script-pipe opens its command pipe for reading first and its
        # reply pipe for writing only after that, so a client must open them
        # in the same order (as Audacity's own pipe_test.py does): opening
        # the reply pipe first blocks both sides for ever.
        if not self.connect:
            self.started_seconds = time.monotonic() - began
            return
        # Held for the whole run; Audacity serves one connection per launch.
        self._to = self._bounded(
            lambda: open(to_pipe, "w"),  # noqa: SIM115 - lives as long as Audacity
            60,
            "open its command pipe",
        )
        self._from = self._bounded(
            lambda: open(from_pipe),  # noqa: SIM115 - lives as long as Audacity
            60,
            "open its reply pipe",
        )
        self.command("GetInfo: Type=Tracks Format=JSON", log=False)
        self.started_seconds = time.monotonic() - began

    def _alive(self, deadline: float, what: str) -> None:
        assert self.process is not None
        if self.process.poll() is not None:
            raise AudacityError(
                f"Audacity exited while starting (status {self.process.returncode}): {self.tail()}"
            )
        if time.monotonic() > deadline:
            raise AudacityError(f"Audacity did not start: {what}. {self.tail()}")

    def _settle(self, deadline: float) -> None:
        assert self.process is not None
        hertz = os.sysconf("SC_CLK_TCK")
        quiet = 0
        time.sleep(2)
        while quiet < 3:
            self._alive(deadline, "it never finished starting")
            before = _cpu_ticks(self.process.pid)
            time.sleep(1)
            busy = (_cpu_ticks(self.process.pid) - before) / hertz
            quiet = quiet + 1 if busy < 0.2 else 0

    @staticmethod
    def _bounded(action: Any, seconds: float, what: str) -> Any:
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["value"] = action()
            except BaseException as error:  # noqa: BLE001 - reported below
                box["error"] = error

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(seconds)
        if thread.is_alive():
            raise AudacityError(f"Audacity did not {what} within {seconds:.0f} s")
        if "error" in box:
            raise box["error"]
        return box["value"]

    def command(self, text: str, *, log: bool = True, timeout: float = COMMAND_TIMEOUT) -> str:
        """Send one scripting command; its reply without the status line."""
        if log:
            self.sent.append(text)

        def exchange() -> tuple[str, str]:
            self._to.write(text + "\n")
            self._to.flush()
            lines: list[str] = []
            status = ""
            while True:
                line = self._from.readline()
                if not line:
                    raise AudacityError("Audacity closed its script pipe")
                if status and line == "\n":
                    return "".join(lines), status
                if line.startswith("BatchCommand finished:"):
                    status = line.strip()
                    continue
                lines.append(line)

        reply, status = self._bounded(exchange, timeout, f"answer {text.split(':')[0]}")
        if not status.endswith("OK"):
            raise AudacityError(f"{text.split(':')[0]} failed: {reply.strip()[-600:]}")
        return str(reply)

    def info(self, kind: str) -> Any:
        reply = self.command(f"GetInfo: Type={kind} Format=JSON", log=False)
        return json.loads(reply)

    def tail(self) -> str:
        try:
            return self.log_path.read_text(errors="replace")[-1500:]
        except OSError:
            return ""

    def stop(self) -> None:
        # Outputs are complete files by now; nothing Audacity does on the way
        # out may touch them. Kill it rather than wait on exit dialogs.
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait()


# --- Typed operations --------------------------------------------------------------


def _number(step: dict[str, Any], key: str, low: float, high: float, default: float | None = None) -> float:
    value = step.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{step['op']}: {key} must be a number")
    if not low <= float(value) <= high:
        raise ValueError(f"{step['op']}: {key} must be {low:g} to {high:g}")
    return float(value)


def _span(step: dict[str, Any], key: str) -> tuple[float, float]:
    value = step.get(key)
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
        or not 0 <= value[0] < value[1] <= 86_400
    ):
        raise ValueError(f"{step['op']}: {key} is [start, end] in seconds, start before end")
    return float(value[0]), float(value[1])


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _TEXT.match(value):
        raise ValueError(f"{what} is plain text up to 200 characters, without quotes or backslashes")
    return value


def _work_path(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{what} must be a project path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not _TEXT.match(value):
        raise ValueError(f"{what} must be a plain path inside the project")
    return (WORK / path).as_posix()


def _seconds(value: float) -> str:
    return f"{value:.6f}"


class Session:
    """A recipe being replayed: Audacity plus what the operations have made."""

    def __init__(self, audacity: Audacity) -> None:
        self.app = audacity
        self.raw: list[str] = []
        self.label_track: int | None = None
        self.profiled = False

    def tracks(self) -> list[dict[str, Any]]:
        found = self.app.info("Tracks")
        return found if isinstance(found, list) else []

    def wave_track(self, step: dict[str, Any]) -> int:
        index = step.get("track")
        tracks = self.tracks()
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(tracks)
            or tracks[index].get("kind") != "wave"
        ):
            raise ValueError(f"{step['op']}: track {index!r} is not an audio track")
        return index

    def select(self, track: int, start: float, end: float) -> None:
        self.app.command(
            f"Select: Start={_seconds(start)} End={_seconds(end)} "
            f"Track={track} TrackCount=1 Mode=Set"
        )

    # Each operation below is one typed step of a recipe.

    def op_import(self, step: dict[str, Any]) -> None:
        path = _work_path(step.get("path"), "import: path")
        if not Path(path).is_file():
            raise ValueError(f"import: {step.get('path')} is missing")
        before = len(self.tracks())
        self.app.command(f'Import2: Filename="{path}"')
        after = self.tracks()
        if len(after) != before + 1 or after[before].get("kind") != "wave":
            raise AudacityError(f"import: {step.get('path')} did not become one audio track")
        if "name" in step:
            name = _text(step["name"], "import: name")
            self.app.command(f'SetTrack: Track={before} Name="{name}"')

    def op_noise_reduction(self, step: dict[str, Any]) -> None:
        if self.profiled:
            raise ValueError(
                "noise_reduction: one noise profile per recipe; Audacity keeps "
                "only the last profile it captured"
            )
        track = self.wave_track(step)
        profile = _span(step, "profile")
        # Audacity's two passes: the first run on a selection of pure noise
        # captures the profile, the next one reduces that noise. The effect
        # takes no scripting parameters; its amounts come from the settings
        # file written before Audacity started (noise_settings).
        self.select(track, *profile)
        self.app.command("NoiseReduction:")
        self.profiled = True
        start, end = _span(step, "region") if "region" in step else (0.0, self._end(track))
        self.select(track, start, end)
        self.app.command("NoiseReduction:")

    def op_spectral_edit(self, step: dict[str, Any]) -> None:
        track = self.wave_track(step)
        start, end = _span(step, "time")
        low = _number(step, "low_hz", 1, 96_000)
        high = _number(step, "high_hz", 1, 96_000)
        if not low < high:
            raise ValueError("spectral_edit: low_hz must be below high_hz")
        # Spectral effects refuse (with a modal dialog) a track that is not
        # shown as a spectrogram; the view goes back afterwards.
        self.app.command(f"SetTrack: Track={track} Display=Spectrogram")
        self.select(track, start, end)
        self.app.command(f"SelectFrequencies: Low={low:g} High={high:g}")
        if "gain_db" in step:
            gain = _number(step, "gain_db", -24, 24)
            # Parametric EQ centred on the selected band; its width is the
            # band itself.
            self.app.command(f"SpectralEditParametricEq: CONTROL-GAIN={gain:g}")
        else:
            self.app.command("SpectralDelete:")
        self.app.command(f"SetTrack: Track={track} Display=Waveform")

    def op_label(self, step: dict[str, Any]) -> None:
        start, end = _span(step, "time") if isinstance(step.get("time"), list) else (
            _number(step, "at", 0, 86_400),
        ) * 2
        text = _text(step.get("text", ""), "label: text")
        if self.label_track is None:
            self.app.command("NewLabelTrack:")
            self.label_track = len(self.tracks()) - 1
        before = self._labels()
        self.select(self.label_track, start, end)
        self.app.command("AddLabel:")
        after = self._labels()
        new = [i for i, label in enumerate(after) if label not in before or after.count(label) > before.count(label)]
        if len(after) != len(before) + 1 or not new:
            raise AudacityError("label: Audacity did not add the label")
        self.app.command(
            f'SetLabel: Label={new[0]} Text="{text}" Start={_seconds(start)} End={_seconds(end)}'
        )

    def op_track(self, step: dict[str, Any]) -> None:
        index = step.get("track")
        tracks = self.tracks()
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(tracks):
            raise ValueError(f"track: no track {index!r}")
        parts = [f"Track={index}"]
        if "name" in step:
            parts.append(f'Name="{_text(step["name"], "track: name")}"')
        if "gain_db" in step:
            parts.append(f"Volume={_number(step, 'gain_db', -36, 36):g}")
        if "pan" in step:
            # Audacity's scripting pan runs from -100 (left) to 100 (right).
            parts.append(f"Pan={_number(step, 'pan', -1, 1) * 100:g}")
        if "mute" in step:
            if not isinstance(step["mute"], bool):
                raise ValueError("track: mute is true or false")
            parts.append(f"Mute={int(step['mute'])}")
        if len(parts) == 1:
            raise ValueError("track: give name, gain_db, pan or mute")
        self.app.command("SetTrack: " + " ".join(parts))

    def op_move(self, step: dict[str, Any]) -> None:
        track = self.wave_track(step)
        start = _number(step, "start", 0, 86_400)
        clips = [c for c in self.app.info("Clips") if c.get("track") == track]
        if len(clips) != 1:
            raise ValueError("move: the track must hold exactly one clip")
        at = (float(clips[0]["start"]) + float(clips[0]["end"])) / 2
        self.app.command(f"SetClip: Track={track} At={_seconds(at)} Start={_seconds(start)}")

    def op_macro(self, step: dict[str, Any]) -> None:
        commands = step.get("commands")
        why = step.get("why")
        if not isinstance(why, str) or not why.strip():
            raise ValueError("macro: say why no typed operation fits (why)")
        if not isinstance(commands, list) or not commands:
            raise ValueError("macro: commands is a list of Audacity scripting commands")
        for command in commands:
            if not isinstance(command, str) or "\n" in command or len(command) > 500:
                raise ValueError("macro: each command is one line")
            name = command.split(":", 1)[0].strip()
            if name in REFUSED_COMMANDS or not re.fullmatch(r"[A-Za-z0-9-]+", name):
                raise ValueError(f"macro: {name} is not allowed in a raw step")
        for command in commands:
            self.raw.append(command)
            self.app.command(command)

    def _end(self, track: int) -> float:
        return float(self.tracks()[track].get("end", 0.0))

    def _labels(self) -> list[tuple[float, float, str]]:
        found: list[tuple[float, float, str]] = []
        for _track, labels in self.app.info("Labels"):
            for start, end, text in labels:
                found.append((round(float(start), 6), round(float(end), 6), str(text)))
        return found

    # Outputs.

    def write_labels(self, relative: str) -> None:
        lines = [f"{start:.6f}\t{end:.6f}\t{text}\n" for start, end, text in self._labels()]
        target = Path(_work_path(relative, "labels"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("".join(lines))

    def export_audio(self, output: dict[str, Any]) -> None:
        path = _work_path(output.get("path"), "audio export")
        if Path(path).suffix.lower() != ".wav":
            raise ValueError("audio exports are WAV; make Ogg files with process_audio")
        channels = output.get("channels", 2)
        if channels not in (1, 2):
            raise ValueError("audio export: channels is 1 or 2")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.app.command("SelectAll:")
        # Export2 mixes every unmuted track, with each track's gain and pan.
        self.app.command(f'Export2: Filename="{path}" NumChannels={channels}')

    def save_project(self, relative: str) -> None:
        path = Path(_work_path(relative, "project"))
        if path.suffix != ".aup3":
            raise ValueError("the project is an .aup3 file")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        self.app.command(f'SaveProject2: Filename="{path}" AddToHistory=0')


def close_project(path: Path) -> None:
    """Fold the project's write-ahead log into the .aup3, as closing it would.

    Audacity is killed rather than closed (closing can ask questions), which
    leaves the newest pages in `-wal` beside the project. SQLite's last
    connection checkpoints the log and removes `-wal` and `-shm`, leaving one
    self-contained project file.
    """
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise AudacityError(f"{path.name} failed SQLite's integrity check")
    finally:
        connection.close()
    for suffix in ("-wal", "-shm"):
        leftover = path.with_name(path.name + suffix)
        if leftover.exists() and leftover.stat().st_size:
            raise AudacityError(f"{leftover.name} was not folded into the project")
        leftover.unlink(missing_ok=True)


OPERATIONS = {
    "import": Session.op_import,
    "noise_reduction": Session.op_noise_reduction,
    "spectral_edit": Session.op_spectral_edit,
    "label": Session.op_label,
    "track": Session.op_track,
    "move": Session.op_move,
    "macro": Session.op_macro,
}


def replay(recipe_path: Path) -> dict[str, Any]:
    recipe = json.loads(recipe_path.read_text())
    if recipe.get("joinery_audacity") != RECIPE_FORMAT:
        raise ValueError("not a Joinery Audacity recipe")
    steps = recipe.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("a recipe needs steps")
    for step in steps:
        if not isinstance(step, dict) or step.get("op") not in OPERATIONS:
            raise ValueError(f"unknown operation {step!r}; use one of {sorted(OPERATIONS)}")
    with tempfile.TemporaryDirectory(prefix="joinery-audacity-") as directory:
        root = Path(directory)
        audacity = Audacity(root / "home", root / "audacity.log", noise_settings(steps))
        try:
            audacity.start()
            session = Session(audacity)
            for step in steps:
                OPERATIONS[step["op"]](session, step)
            for output in recipe.get("audio", []):
                session.export_audio(output)
            if recipe.get("labels"):
                session.write_labels(recipe["labels"])
            session.save_project(recipe["project"])
            tracks = session.tracks()
        finally:
            audacity.stop()
    close_project(Path(_work_path(recipe["project"], "project")))
    return {
        "audacity": _version(),
        "seconds_to_ready": round(audacity.started_seconds, 1),
        "commands": audacity.sent,
        "raw_commands": session.raw,
        "tracks": [
            {k: t.get(k) for k in ("name", "kind", "start", "end", "volume", "pan", "mute")}
            for t in tracks
        ],
    }


def _run_app(action: Any) -> Any:
    with tempfile.TemporaryDirectory(prefix="joinery-audacity-") as directory:
        root = Path(directory)
        audacity = Audacity(root / "home", root / "audacity.log")
        try:
            audacity.start()
            return action(audacity)
        finally:
            audacity.stop()


def serve_mcp() -> None:
    """Start Audacity, then hand this process to the pinned MCP on stdio."""
    root = Path(tempfile.mkdtemp(prefix="joinery-audacity-"))
    # Wait until Audacity is ready, but leave its one pipe connection to the
    # MCP, which opens both ends itself.
    audacity = Audacity(root / "home", root / "audacity.log", connect=False)
    audacity.start()
    python = "/opt/audacity-python/bin/python"
    os.execv(python, [python, "-m", "audacity_mcp.main"])


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    if argv[0] == "--serve-mcp":
        serve_mcp()
        return 0
    try:
        if argv[0] == "--prime":
            prime(PLUGIN_TEMPLATE)
            print(f"JOINERY_AUDACITY_PRIMED {PLUGIN_TEMPLATE}")
        elif argv[0] == "--check":
            seconds = _run_app(lambda app: app.started_seconds)
            print(f"JOINERY_AUDACITY_READY {_version()} {seconds:.1f}")
        elif argv[0] == "--commands":
            print(json.dumps(_run_app(lambda app: app.info("Commands")), indent=1))
        else:
            report = replay(Path(argv[0]))
            print("JOINERY_AUDACITY_REPORT " + json.dumps(report, sort_keys=True))
            print("JOINERY_AUDACITY_OK")
    except (AudacityError, ValueError, OSError, KeyError) as error:
        print(f"JOINERY_AUDACITY_FAILED {error}", file=sys.stderr)
        sys.stdout.flush()
        os._exit(1)
    sys.stdout.flush()
    # Pipe reader threads may still wait on an Audacity that is gone.
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
