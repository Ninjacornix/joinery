"""Actual pinned GIMP plug-in command handlers and pixel/file verification."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from joinery.catalog import ROOT, load_catalog


def run(source: Path) -> dict[str, object]:
    tool = load_catalog().get("gimp")
    assert tool.mcp is not None
    if (source / ".joinery-test-revision").read_text().strip() != tool.mcp.ref:
        raise ValueError("GIMP test source is not the selected catalog revision.")
    name = "joinery-gimp-test-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="joinery-gimp-native-") as directory:
        scratch = Path(directory).resolve()
        shutil.copy2(source / "gimp-mcp-plugin.py", scratch / "gimp-mcp-plugin.py")
        shutil.copy2(ROOT / "tests/upstream/gimp_batch.py", scratch / "check.py")
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
                    "-v",
                    str(scratch) + ":/work",
                    "-w",
                    "/work",
                    "joinery-creative-worker",
                    "gimp",
                    "--new-instance",
                    "--no-data",
                    "--no-fonts",
                    "--no-splash",
                    "--batch-interpreter=python-fu-eval",
                    "-b",
                    'import runpy; runpy.run_path("/work/check.py")',
                    "--quit",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if got.returncode or "GIMP_NATIVE_PIXELS_OK" not in got.stdout:
                raise RuntimeError(
                    "GIMP native command fixture failed:\n"
                    + got.stdout[-3000:]
                    + got.stderr[-3000:]
                )
            results = json.loads((scratch / "results.json").read_text())
            return {
                "tool": "gimp",
                "revision": tool.mcp.ref,
                "native_commands": len(results),
                "source_format": "xcf",
                "red_blue_pixels": "passed",
            }
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
