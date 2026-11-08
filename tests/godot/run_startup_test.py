"""Exercise the native startup against an isolated, local model-list fixture."""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from joinery.distribution import engine_binary

CAPTURES = (
    "startup-agent-connect.png",
    "startup-api-submenu.png",
    "startup-setup-details.png",
    "startup-setup-tools.png",
    "startup-setup-art.png",
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
        "JOINERY_RENDERING_DRIVER",
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
        "TMP": "temp",
        "TEMP": "temp",
        "TMPDIR": "temp",
    }.items():
        path = directory / name
        path.mkdir(parents=True, exist_ok=True)
        environment[key] = str(path)
    environment.update(
        JOINERY_HOME=str(root),
        JOINERY_SETUP_CHECK_DIR=str(directory / "project"),
        JOINERY_CONNECTIONS_FILE=str(directory / "connections.json"),
        JOINERY_TEST_MCP_COMMAND=str(
            directory / "MCP path with spaces" / "explicitly-selected-mcp.exe"
        ),
        JOINERY_CAPTURE_DIR=str(capture),
        PYTHONUTF8="1",
        # Reuse the harness's locked dependencies without altering any runtime
        # environment or downloading packages during the local API fixture.
        UV_PROJECT_ENVIRONMENT=sys.prefix,
        UV_NO_SYNC="true",
        UV_FROZEN="true",
        UV_PYTHON_DOWNLOADS="never",
    )
    return environment


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


def checked_captures(directory: Path) -> None:
    for name in CAPTURES:
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
    for name in CAPTURES:
        (capture / name).unlink(missing_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelList)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="joinery-api-flow-") as directory:
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
