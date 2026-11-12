"""Typed Blender operations with a warm Blender against a fresh one per call.

The same small model (as tests/integration/blender_batch.py) is made in two
stages; each gets the same six typed operations through joinery-blender-ops,
one call each, exactly as an agent calls them. Both stages reuse one worker
container, as the gateway does for an agent:

* cold: ``JOINERY_WARM_BLENDER=0``, a fresh Blender per operation and a fresh
  container per fingerprint (today's behaviour);
* warm: one Blender kept running in the container (joinery.warm_blender) for
  the operations and their fingerprints.

Then:

1. both stages hold the same recipe log (scripts, notes, outputs, sources and
   the recorded fingerprints; preimage names masked, since Blender writes a
   different .blend byte for byte each run) and byte-identical GLB and
   texture exports;
2. every recipe of the warm stage replays (`joinery rebuild`, a fresh
   container) to its recorded fingerprints;
3. a failing operation fails the same way, and the next one still works;
4. a warm Blender killed between two operations is replaced and the next
   operation works; a warm Blender that cannot start falls back to a cold run.

Per-operation wall-clock seconds are reported for both. Last, a review's
before/after renders are drawn once and a second review of the same documents
takes them from the render cache. Technical fixtures
only. Run: `uv run python tests/integration/warm_blender.py`.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import statistics
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from joinery import warm_blender, warm_workers
from joinery.authoring import edit_document
from joinery.recipe import HISTORY_DIR, recipe_paths
from tests.integration.blender_batch import (
    MAKE,
    MODEL,
    SOURCE,
    STEPS,
    call,
    log,
    outputs,
    replay,
    stage_of,
)


async def ops(stage: Path) -> list[float]:
    times = []
    for step in STEPS:
        start = time.monotonic()
        result, text = await call(
            stage,
            step["op"],
            {"source": SOURCE, "exports": [MODEL], **step["arguments"]},
        )
        assert not result.is_error, f"{step['op']}: {text}"
        times.append(round(time.monotonic() - start, 2))
    return times


def warm_mode(on: bool) -> None:
    os.environ["JOINERY_WARM_BLENDER"] = "1" if on else "0"


async def main() -> dict[str, Any]:
    os.environ.setdefault("JOINERY_WORKER_SCOPE", uuid.uuid4().hex)
    facts: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="joinery-warm-blender-") as directory:
        root = Path(directory)
        cold_tx, cold = stage_of(root, "cold")
        warm_tx, warm = stage_of(root, "warm")
        odd_tx, odd = stage_of(root, "odd")
        warm_mode(False)
        await edit_document(cold, "blender", SOURCE, MAKE, [MODEL], "Make the box")
        for other in (warm, odd):
            for folder in ("art", "recipes"):
                shutil.copytree(cold / folder, other / folder)
            shutil.copy2(cold / "joinery.lock", other / "joinery.lock")

        # The first call in each stage also starts its container (and, warm,
        # its Blender); both are reported apart from the steady state.
        facts["cold_s"] = await ops(cold)
        warm_mode(True)
        facts["warm_s"] = await ops(warm)
        facts["cold_median_s"] = statistics.median(facts["cold_s"][1:])
        facts["warm_median_s"] = statistics.median(facts["warm_s"][1:])
        blenders = list(warm_workers._blenders.values())
        facts["warm_processes_started"] = sum(b.starts for b in blenders)
        facts["warm_requests_served"] = sum(b.served for b in blenders)
        facts["warm_restarts_for_changed_state"] = sum(b.unclean for b in blenders)

        assert log(warm) == log(cold), "the recipe logs differ"
        assert outputs(warm) == outputs(cold), "the exports differ"
        history = sorted((warm / HISTORY_DIR).glob("*.toml"))
        failed = await replay(warm, [*recipe_paths(warm), *history])
        assert not failed, failed
        facts["replayed"] = len(history) + len(recipe_paths(warm))

        # A failing operation, warm and cold, says the same thing.
        bad = {"source": SOURCE, "exports": [MODEL], "objects": ["Nope"]}
        result, warm_text = await call(odd, "apply_scale", bad)
        assert result.is_error and "Nope" in warm_text, warm_text
        warm_mode(False)
        result, cold_text = await call(odd, "apply_scale", bad)
        assert result.is_error and "Nope" in cold_text, cold_text
        warm_mode(True)

        # Killed between two operations: a new one starts.
        first, second = STEPS[0], STEPS[4]
        result, text = await call(
            odd,
            first["op"],
            {"source": SOURCE, "exports": [MODEL], **first["arguments"]},
        )
        assert not result.is_error, text
        for blender in warm_workers._blenders.values():
            if blender.process is not None:
                blender.process.kill()
        result, text = await call(
            odd,
            second["op"],
            {"source": SOURCE, "exports": [MODEL], **second["arguments"]},
        )
        assert not result.is_error, text

        # Cannot start: the operation runs cold.
        async def broken(self: warm_blender.WarmBlender) -> None:
            raise RuntimeError("no Blender")

        original = warm_blender.WarmBlender.start
        warm_blender.WarmBlender.start = broken  # type: ignore[method-assign]
        try:
            for blender in list(warm_workers._blenders.values()):
                await blender.close()
            result, text = await call(
                odd,
                STEPS[1]["op"],
                {"source": SOURCE, "exports": [MODEL], **STEPS[1]["arguments"]},
            )
            assert not result.is_error, text
        finally:
            warm_blender.WarmBlender.start = original  # type: ignore[method-assign]
        failed = await replay(odd, recipe_paths(odd))
        assert not failed, failed
        for tx in (cold_tx, warm_tx, odd_tx):
            tx.reject()
    await warm_workers.close_blenders()
    return facts


async def render_cache() -> dict[str, Any]:
    """A review's renders drawn once, then taken from the cache: the second
    review of the same documents draws nothing."""
    from joinery import review
    from joinery.transactions import FileTransaction

    os.environ.pop("JOINERY_WORKER_SCOPE", None)  # the gateway renders cold
    facts: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="joinery-render-cache-") as directory:
        tx, stage = stage_of(Path(directory), "project")
        await edit_document(stage, "blender", SOURCE, MAKE, [MODEL], "Make the box")
        tx.review()
        tx.accept()
        second = FileTransaction(tx.project)
        second.begin("Unchanged")
        changes = [{"path": SOURCE, "change": "modified"}]
        pictures = []
        for name in ("first_review_s", "second_review_s"):
            media, renders = review.plan(second, changes)
            start = time.monotonic()
            await review.render(renders, strict=True)
            facts[name] = round(time.monotonic() - start, 2)
            pictures.append(
                {Path(media[0][side]).read_bytes() for side in ("before", "after")}
            )
        # Both sides show the same document, so the first review drew it twice
        # at once (Workbench pictures can differ in a few bytes between runs);
        # the second shows a drawn picture without drawing.
        assert pictures[1] <= pictures[0], "the second review drew again"
        assert facts["second_review_s"] < 1.0, facts
        second.reject()
    return facts


if __name__ == "__main__":
    found = asyncio.run(main())
    found["render_cache"] = asyncio.run(render_cache())
    print(json.dumps(found, indent=1))
    print("WARM_BLENDER_OK")
