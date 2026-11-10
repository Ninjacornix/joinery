"""Exercise the native startup against an isolated, local model-list fixture."""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from joinery.distribution import engine_binary

CAPTURES = (
    "startup-agent-connect.png",
    "startup-api-submenu.png",
    "startup-setup-details.png",
    "startup-setup-tools.png",
    "startup-setup-history.png",
    "startup-setup-art.png",
)
#: The new-project wizard's pages, captured by visual_startup_test.gd in each
#: theme: starting point cards, features with Steam and Online play on (the app
#: ID field and the connect choice), the app ID error, brief and art direction
#: with References, and the creating page.
WIZARD_PAGES = (
    "setup-1-details",
    "setup-2-tools",
    "setup-2-steam-error",
    "setup-2-steam-too-big",
    "setup-2-history",
    "setup-3-art",
    "setup-creating",
)
THEMES = ("dark", "light")
#: The fork's light preset, as the Appearance setting writes it.
LIGHT_SETTINGS = (
    '[gd_resource type="EditorSettings" format=3]\n\n'
    "[resource]\n"
    'interface/theme/color_preset = "Joinery Light"\n'
)


def isolated_environment(directory: Path, root: Path, capture: Path) -> dict[str, str]:
    # Retain native process/display discovery, never provider secrets or homes.
    permitted = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "PATHEXT",
        "COMSPEC",
        "LANG",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "GALLIUM_DRIVER",
        "LIBGL_ALWAYS_SOFTWARE",
        "MESA_LOADER_DRIVER_OVERRIDE",
        "VK_DRIVER_FILES",
        "VK_ICD_FILENAMES",
        "JOINERY_RENDERING_DRIVER",
        # Windows CI: print a stack for fail-fast exits (ADR 0032).
        "JOINERY_CRASH_TRACE",
    }
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in permitted
    }
    for key, name in {
        "HOME": "home",
        "USERPROFILE": "home",
        "CODEX_HOME": "codex",
        "APPDATA": "appdata",
        "LOCALAPPDATA": "localappdata",
        "XDG_CONFIG_HOME": "config",
        "XDG_CACHE_HOME": "cache",
        "XDG_DATA_HOME": "data",
        "XDG_STATE_HOME": "state",
        "XDG_RUNTIME_DIR": "runtime",
        "TMP": "temp",
        "TEMP": "temp",
        "TMPDIR": "temp",
    }.items():
        path = directory / name
        path.mkdir(parents=True, exist_ok=True)
        if key == "XDG_RUNTIME_DIR":
            path.chmod(0o700)
        environment[key] = str(path)
    # Windows verifies these known folders under the isolated USERPROFILE.
    # Populate only empty fixture directories; never alter shell-folder registry
    # settings or fall back to the real user's profile.
    for name in ("Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos"):
        (directory / "home" / name).mkdir(exist_ok=True)
    environment.update(
        JOINERY_HOME=str(root),
        JOINERY_SETUP_CHECK_DIR=str(directory / "project"),
        JOINERY_CONNECTIONS_FILE=str(directory / "connections.json"),
        JOINERY_TEST_MCP_COMMAND=str(
            directory / "MCP path with spaces" / "explicitly-selected-mcp.exe"
        ),
        JOINERY_CAPTURE_DIR=str(capture),
        # The fork keeps windowed test editors from taking focus (ADR 0032).
        JOINERY_BACKGROUND="1",
        PYTHONUTF8="1",
        # Reuse the harness's locked dependencies without altering any runtime
        # environment or downloading packages during the local API fixture.
        UV_PROJECT_ENVIRONMENT=sys.prefix,
        UV_NO_SYNC="true",
        UV_FROZEN="true",
        UV_PYTHON_DOWNLOADS="never",
    )
    return environment


def remove_when_released(path: Path, seconds: float = 30.0) -> None:
    """Delete a scratch folder once every process has let go of it.

    Hosts the editor starts (chat, CLI jobs) shut down on their own when their
    pipes close, a moment after the editor exits. Windows refuses to remove a
    folder while such a process still runs in it, so wait for them to finish;
    a host that never exits still fails the run after `seconds`.
    """
    deadline = time.monotonic() + seconds

    def writable(function: Any, name: str, error: BaseException) -> None:
        # Git marks pack files read-only; Windows will not delete those as they are.
        if not isinstance(error, PermissionError) or not os.path.lexists(name):
            raise error
        os.chmod(name, stat.S_IWRITE | stat.S_IREAD)
        function(name)

    while True:
        try:
            shutil.rmtree(path, onexc=writable)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


#: What Godot's own crash handler prints before it aborts.
CRASH_BANNER = "Program crashed with signal"


def crash(returncode: int, output: str = "") -> str:
    """Why an editor run counts as a crash, or "" when the process ended on its own.

    Godot exits with 1 for a failed check, so only a signal (a negative code on
    POSIX, as subprocess reports it), a Windows exception status (0xC0000000 and
    up, such as 0xC0000005 for an access violation) or the crash handler's banner
    counts. A crash while the editor quits comes after the checks have printed
    their result, so every runner tests the exit as well as its marker.
    """
    if returncode < 0:
        try:
            return f"the editor crashed ({signal.Signals(-returncode).name})"
        except ValueError:
            return f"the editor crashed (signal {-returncode})"
    if os.name == "nt" and returncode >= 0xC0000000:
        return f"the editor crashed (0x{returncode:08X})"
    if CRASH_BANNER in output:
        return "the editor crashed (see its log)"
    return ""


@contextmanager
def scratch_folder(prefix: str) -> Iterator[str]:
    """Like tempfile.TemporaryDirectory, but waits for the editor's hosts."""
    directory = tempfile.mkdtemp(prefix=prefix)
    try:
        yield directory
    finally:
        remove_when_released(Path(directory))


def isolated_editor(source: Path, directory: Path) -> Path:
    target = directory / "editor" / source.name
    target.parent.mkdir()
    shutil.copy2(source, target)
    (target.parent / "_sc_").write_text("")
    if os.name == "nt":
        # Never copy arbitrary siblings (configuration, accounts or user data).
        for name in ("D3D12Core.dll", "d3d12SDKLayers.dll", "WinPixEventRuntime.dll"):
            sibling = source.parent / name
            if sibling.is_file():
                shutil.copy2(sibling, target.parent / name)
        mesa = [source.parent / name for name in ("opengl32.dll", "libgallium_wgl.dll")]
        if any(path.is_file() for path in mesa):
            if not all(path.is_file() for path in mesa):
                raise ValueError(
                    "The private Windows software OpenGL DLL pair is incomplete."
                )
            for path in mesa:
                shutil.copy2(path, target.parent / path.name)
    return target


def checked_captures(directory: Path, names: tuple[str, ...] = CAPTURES) -> None:
    for name in names:
        data = (directory / name).read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            raise ValueError("Startup capture is not a PNG: " + name)
        width, height = struct.unpack(">II", data[16:24])
        if width < 100 or height < 100 or len(data) < 1000:
            raise ValueError("Startup capture has no meaningful framebuffer: " + name)


class ModelList(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if (
            self.path != "/v1/models"
            or self.headers.get("Authorization") != "Bearer TEST_ONLY"
        ):
            self.send_error(401)
            return
        body = json.dumps({"data": [{"id": "fixture-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def wizard_pass(root: Path, capture: Path, theme: str) -> list[str]:
    """Walk the new-project wizard in one theme; return what went wrong."""
    names = tuple(f"pm-{page}-{theme}.png" for page in WIZARD_PAGES)
    for name in names:
        (capture / name).unlink(missing_ok=True)
    with scratch_folder("joinery-wizard-") as directory:
        temporary = Path(directory)
        env = isolated_environment(temporary, root, capture)
        env["JOINERY_VISUAL_THEME"] = theme
        binary = isolated_editor(engine_binary(), temporary)
        if theme == "light":
            data = binary.parent / "editor_data"
            data.mkdir(exist_ok=True)
            (data / "editor_settings-4.7.tres").write_text(LIGHT_SETTINGS)
        command = [
            str(binary),
            "--audio-driver",
            "Dummy",
            "--project-manager",
            "--script",
            str(root / "tests/godot/visual_startup_test.gd"),
        ]
        if env.get("JOINERY_RENDERING_DRIVER"):
            command += ["--rendering-driver", env["JOINERY_RENDERING_DRIVER"]]
        try:
            result = subprocess.run(
                command,
                cwd=temporary,
                env=env,
                timeout=300,
                check=False,
                capture_output=True,
                text=True,
            )
            output = result.stdout + result.stderr
            code = result.returncode
        except subprocess.TimeoutExpired:
            output, code = "Timed out.", 1
    (capture / f"wizard-{theme}.log").write_text(output)
    problems: list[str] = []
    if reason := crash(code, output):
        problems.append(f"{theme}: {reason} (wizard-{theme}.log)")
    if code != 0 or "visual startup: ok" not in output:
        problems.append(f"{theme}: the wizard walk failed (wizard-{theme}.log)")
    # ui_checks reports clipped text, contrast, overflow and small targets; the
    # walk adds "primary" when a page shows more than one accent fill.
    problems += [line for line in output.splitlines() if line.startswith("UI-CHECK ")]
    try:
        checked_captures(capture, names)
    except (OSError, ValueError) as exc:
        problems.append(f"{theme}: {exc}")
    return problems


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    if sys.prefix == sys.base_prefix:
        raise ValueError("Run this harness with uv run --frozen python.")
    if shutil.which("codex") is None:
        raise ValueError(
            "Install the Codex CLI for this local API UI fixture; no sign-in is used."
        )
    capture = Path(
        os.environ.get("JOINERY_CAPTURE_DIR", root / "out/startup-captures")
    ).resolve()
    capture.mkdir(parents=True, exist_ok=True)
    # Both parts always run, so one failing never hides the other's result.
    setup = setup_flow(root, capture)
    problems = [p for theme in THEMES for p in wizard_pass(root, capture, theme)]
    for problem in problems:
        print(problem)
    if not problems:
        print(
            f"New-project wizard: {len(WIZARD_PAGES)} pages in {len(THEMES)} themes: PASS"
        )
    return 1 if setup or problems else 0


def setup_flow(root: Path, capture: Path) -> int:
    """Agent connection, API form and project setup, from project_setup_flow_test."""
    for name in CAPTURES:
        (capture / name).unlink(missing_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelList)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with scratch_folder("joinery-api-flow-") as directory:
            temporary = Path(directory)
            env = isolated_environment(temporary, root, capture)
            env["JOINERY_TEST_MODEL_ENDPOINT"] = (
                f"http://127.0.0.1:{server.server_port}/v1"
            )
            binary = isolated_editor(engine_binary(), temporary)
            command = [
                str(binary),
                "--audio-driver",
                "Dummy",
                "--project-manager",
                "--script",
                str(root / "tests/godot/project_setup_flow_test.gd"),
                "--log-file",
                str(capture / "startup.log"),
            ]
            if env.get("JOINERY_RENDERING_DRIVER"):
                command += ["--rendering-driver", env["JOINERY_RENDERING_DRIVER"]]
            try:
                result = subprocess.run(
                    command,
                    cwd=temporary,
                    env=env,
                    timeout=90,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except subprocess.TimeoutExpired as exc:
                output = "\n".join(
                    value.decode(errors="replace")
                    if isinstance(value, bytes)
                    else value or ""
                    for value in (exc.stdout, exc.stderr)
                )
                (capture / "startup-output.log").write_text(output)
                print(output)
                print("Startup harness timed out.")
                return 1
            output = result.stdout + result.stderr
            (capture / "startup-output.log").write_text(output)
            print(output)
            if reason := crash(result.returncode, output):
                print(f"Startup harness: {reason}.")
                return 1
            if (
                result.returncode
                or "project setup flow: ok" not in output
                or any(
                    marker in output
                    for marker in ("SCRIPT ERROR", "Parse Error", "ERROR:")
                )
            ):
                return 1
            checked_captures(capture)
            # The isolated API profile must contain only the test credential.
            profiles = json.loads((temporary / "connections.json").read_text())
            assert len(profiles) == 1
            assert profiles[0]["key"] == "TEST_ONLY"
            assert profiles[0]["model"] == "fixture-model"
            assert profiles[0]["endpoint"] == env["JOINERY_TEST_MODEL_ENDPOINT"]
            assert (binary.parent / "editor_data").is_dir(), (
                "Godot must use self-contained editor settings"
            )
            print(
                "Isolated native startup/API/project setup and five framebuffers: PASS"
            )
            return 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
