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
from joinery.worker_identity import docker_owner_args

EVIDENCE = ROOT / "out/audacity-native"


def _keep_evidence(scratch: Path) -> None:
    """Copy the failure screenshot and Audacity's log out of the scratch folder."""
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    for name in ("failure.png", "audacity.log", "processes.txt"):
        if (scratch / name).is_file():
            shutil.copy2(scratch / name, EVIDENCE / name)


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
            try:
                got = _run(name, source, scratch)
            except subprocess.TimeoutExpired as error:
                # The probe can hang too hard to report; capture from outside.
                _capture(name)
                _keep_evidence(scratch)
                raise RuntimeError(
                    "Audacity native probe hung; screen and log kept in "
                    f"{EVIDENCE}.\n" + _text(error.stdout)[-3000:]
                ) from error
            if got.returncode or "AUDACITY_NATIVE_MCP_OK" not in got.stdout:
                _keep_evidence(scratch)
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


def _text(value: str | bytes | None) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else value or ""


def _capture(name: str) -> None:
    """Screenshot the container's display into /work, which is the scratch folder."""
    subprocess.run(
        [
            "docker",
            "exec",
            name,
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "x11grab",
            "-i",
            ":99",
            "-frames:v",
            "1",
            "/work/failure.png",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    subprocess.run(
        [
            "docker",
            "exec",
            name,
            "sh",
            "-c",
            "ps -eo pid,stat,etime,wchan:24,args > /work/processes.txt",
        ],
        timeout=15,
        check=False,
    )


def _run(name: str, source: Path, scratch: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            # Native Linux: run as the caller so the private scratch
            # folder is readable; Docker Desktop maps ownership itself.
            *docker_owner_args(),
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
        timeout=240,
        check=False,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().source), indent=2))
