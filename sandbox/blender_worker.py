"""Two upstream processes, one offline container; stdout belongs to MCP only."""

import os
import signal
import socket
import subprocess
import sys
import time


def main() -> int:
    with open("/tmp/joinery-blender.log", "w") as log:
        blender = subprocess.Popen(
            ["blender", "--background", "--factory-startup", "--python", "/opt/joinery/blender_bootstrap.py"],
            stdout=log, stderr=log,
        )
        mcp = None
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if blender.poll() is not None:
                    raise RuntimeError("Blender worker exited before connecting.")
                try:
                    with socket.create_connection(("127.0.0.1", 9876), timeout=1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise TimeoutError("Blender addon did not start within 90 seconds.")
            env = dict(os.environ, PYTHONPATH="/opt/blender-mcp/mcp", BLENDER_MCP_HOST="127.0.0.1")
            mcp = subprocess.Popen(["/opt/blender-python/bin/python", "-m", "blmcp"], env=env)
            def stop(_signum: int, _frame: object) -> None:
                if mcp is not None:
                    mcp.terminate()
            signal.signal(signal.SIGTERM, stop)
            return mcp.wait()
        finally:
            for process in (mcp, blender):
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, TimeoutError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
