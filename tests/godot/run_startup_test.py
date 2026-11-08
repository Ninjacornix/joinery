"""Exercise the native startup against an isolated, local model-list fixture."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


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
    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelList)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="joinery-api-flow-") as directory:
            temporary = Path(directory)
            env = dict(
                os.environ,
                JOINERY_HOME=str(root),
                JOINERY_SETUP_CHECK_DIR=str(temporary / "project"),
                JOINERY_CONNECTIONS_FILE=str(temporary / "connections.json"),
                JOINERY_TEST_MODEL_ENDPOINT=f"http://127.0.0.1:{server.server_port}/v1",
            )
            result = subprocess.run(
                [
                    str(root / "godot_patches/godot-src/bin/godot.macos.editor.arm64"),
                    "--project-manager",
                    "--script",
                    str(root / "tests/godot/project_setup_flow_test.gd"),
                    "--log-file",
                    "/tmp/joinery-final-auto-api-flow-test.log",
                ],
                env=env,
                timeout=35,
                check=False,
            )
            return result.returncode
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
