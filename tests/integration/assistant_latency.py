"""Measure fresh/reused native worker latency and prove native edit isolation.

No inference or user's project is involved. This is not an AI response benchmark.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import tempfile
import time
import uuid
from pathlib import Path

from joinery.authoring import run_offline
from joinery.warm_workers import _cleanup, _containers
from tests.integration.creative_updates import (
    blender,
    inkscape,
    krita,
    material_maker,
    supercollider,
)


async def run(output: Path, all_native: bool = False) -> None:
    if os.environ.get("JOINERY_WORKER_SCOPE"):
        raise ValueError("Run this benchmark outside an existing worker scope.")
    command = [
        "blender",
        "--background",
        "--factory-startup",
        "--python-expr",
        "import bpy; assert len(bpy.data.objects)==3; print('JOINERY_LATENCY_OK')",
    ]
    results: dict[str, list[float]] = {"fresh": [], "reused": []}
    try:
        with tempfile.TemporaryDirectory(prefix="joinery-worker-latency-") as directory:
            project = Path(directory)
            for mode, measurements in results.items():
                if mode == "reused":
                    os.environ["JOINERY_WORKER_SCOPE"] = uuid.uuid4().hex
                for _ in range(3):
                    started = time.monotonic()
                    result = await run_offline(command, project, 60, readonly=True)
                    elapsed = time.monotonic() - started
                    if not result.ok or "JOINERY_LATENCY_OK" not in result.output:
                        raise RuntimeError(result.output[-3000:])
                    measurements.append(round(elapsed, 3))
            print(json.dumps(results), flush=True)
        # Exercise actual SDK edits/source/GLB/replay/review/revert through the
        # reused-container path as well as the existing fresh-worker path.
        _cleanup()
        operations = [("blender", blender)]
        if all_native:
            operations += [
                ("krita", krita),
                ("supercollider", supercollider),
                ("inkscape", inkscape),
                ("material-maker", material_maker),
            ]
        for name, operation in operations:
            os.environ["JOINERY_WORKER_SCOPE"] = uuid.uuid4().hex
            try:
                await operation()
            finally:
                _cleanup()
        with tempfile.TemporaryDirectory(
            prefix="joinery-native-cancellation-"
        ) as directory:
            project = Path(directory)
            os.environ["JOINERY_WORKER_SCOPE"] = uuid.uuid4().hex
            job = asyncio.create_task(
                run_offline(
                    [
                        "python3",
                        "-c",
                        "import subprocess,pathlib; p=subprocess.Popen(['sleep','60']); pathlib.Path('/work/ready').write_text('ready'); p.wait()",
                    ],
                    project,
                    60,
                )
            )
            try:
                async with asyncio.timeout(30):
                    while not (project / "ready").exists():
                        if job.done():
                            raise RuntimeError((await job).output)
                        await asyncio.sleep(0.05)
                names = tuple(_containers.values())
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
                for name in names:
                    process = await asyncio.create_subprocess_exec(
                        "docker",
                        "container",
                        "inspect",
                        name,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    assert await process.wait() != 0, (
                        "Cancelled native worker still exists"
                    )
                print(
                    "Native worker cancellation and descendant cleanup: PASS",
                    flush=True,
                )
            finally:
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
                _cleanup()
        report = {
            "scope": "native Blender startup/inspection; no model inference",
            "seconds": results,
            "median_seconds": {
                name: round(statistics.median(values), 3)
                for name, values in results.items()
            },
            "reused_native_blender_lifecycle": "passed",
            "native_lifecycles": {name: "passed" for name, _ in operations},
            "native_cancellation": "passed",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
    finally:
        os.environ.pop("JOINERY_WORKER_SCOPE", None)
        _cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=Path("out/assistant-latency.json")
    )
    parser.add_argument("--all-native", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.output, args.all_native))
