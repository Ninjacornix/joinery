"""Native Audacity script-pipe/MCP probe in an isolated test-only image."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from joinery.catalog import ROOT, load_catalog


def run(source: Path) -> dict[str, Any]:
    tool = load_catalog().get("audacity")
    assert tool.mcp is not None
    source = source.resolve()
    if (source / ".joinery-test-revision").read_text().strip() != tool.mcp.ref:
        raise ValueError("Audacity test source is not the selected catalog revision.")
    name = "joinery-audacity-test-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="joinery-audacity-native-") as directory:
        scratch = Path(directory).resolve()
        shutil.copy2(ROOT / "tests/upstream/audacity_native.py", scratch / "check.py")
        try:
            got = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    name,
                    "--platform",
                    "linux/amd64",
                    "--network",
                    "none",
                    "--memory",
                    "4g",
                    "--pids-limit",
                    "512",
                    "-e",
                    "PYTHONPATH=/opt/audacity-mcp",
                    "-v",
                    str(source) + ":/opt/audacity-mcp:ro",
                    "-v",
                    str(scratch) + ":/work",
                    "-w",
                    "/work",
                    "joinery-mcp-conformance",
                    "/opt/joinery-mcp-test-python/bin/python",
                    "-u",
                    "/work/check.py",
                ],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            if got.returncode or "AUDACITY_NATIVE_MCP_OK" not in got.stdout:
                raise RuntimeError(
                    "Audacity native probe failed:\n"
                    + got.stdout[-3000:]
                    + got.stderr[-3000:]
                )
            results: dict[str, Any] = json.loads((scratch / "results.json").read_text())
            return {"tool": "audacity", "revision": tool.mcp.ref, **results}
        finally:
            subprocess.run(
                ["docker", "rm", "--force", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().source), indent=2))
