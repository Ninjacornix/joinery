# 0062. A warm Blender per worker, and review renders kept by content

Status: accepted (2026-11-12)

## Context

A typed Blender operation (`apply_scale`, `set_pivot`, …) runs its recipe
script in the agent's reused worker container (`joinery.warm_workers`): a new
Blender process per call, then a second Blender in a new container
(`SandboxRunner`) to fingerprint the saved `.blend` for the recipe. Starting
Blender, under amd64 emulation on an Apple-silicon Mac, is most of the call.
`blender_batch` (ADR 0057) already saves this for several operations sent
together; single calls still pay it every time.

A review's before/after renders (`joinery.review`) draw both sides in Blender
every time the review opens, also when a send-back or a second round leaves
one side exactly as it was.

## Decision

1. **One Blender per warm container** (`joinery.warm_blender`). The first
   Blender command in a container starts
   `blender --background --factory-startup --python-expr <loop>` through
   `docker exec -i`; the loop reads one JSON request per line on stdin and
   answers one line on a duplicate of the original stdout. The loop is passed
   as an argument, so nothing is written into the project.
2. **Only two command shapes run warm**, the ones typed operations and
   fingerprints use: `blender --background --factory-startup --python
   recipes/x.py` (the recipe script) and `blender --background <doc>.blend
   --python-expr <code>` (the fingerprint and other reads). A batch driver
   (own arguments, `os._exit` on failure), renders and every other program
   run as before.
3. **Each request runs as a fresh Blender would.** Before the script:
   `read_factory_settings` (what `--factory-startup` gives) or
   `open_mainfile(doc)`; fresh globals with `__name__ == "__main__"` and the
   script's `__file__`; `os.chdir("/work")`. File descriptors 1 and 2 are
   redirected to files for the request, so Blender's own C-level messages are
   in the output too, exactly what the cold command printed for the parsers
   (`_export_facts`, signatures). An error at the top of a recipe gives exit
   code 1 when the recipe installed its fail-on-error hook
   (`FAIL_ON_ERROR`) and 0 otherwise, as Blender does; `sys.exit(n)` gives n.
   `sys.excepthook` and `sys.argv` are put back after every request.
4. **On any doubt, a fresh process.** The process is restarted after 25
   requests, after 300 s idle (a timer stops it), after a failed request, and
   after a request that left a new handler function (`bpy.app.handlers`) or a
   new registered type (`bpy.types`) behind. Handler *functions* are compared,
   not counts: Blender's own pose library adds its load handlers again on
   every factory reset, in a cold process too. A process that dies, does not
   start, or answers out of protocol makes the request run cold in the same
   container (`docker exec`, as before). A request that runs past its timeout
   raises as the cold run does, and the container (with the hung process) is
   removed. A failed request also removes the container, as a failed cold
   command already did. `JOINERY_WARM_BLENDER=0` turns all of this off.
5. **Fingerprints use the warm worker too.** `authoring._record` measures the
   new recipe's outputs through `warm_workers.WarmRunner` (the recipe code is
   synchronous and runs in a thread; each command is sent to the warm
   container on the server's event loop) instead of a new container per
   fingerprint, when the server has a warm worker and warm Blender is on.
6. **Review renders are kept by content** (`review.Render.key`): sha256 of the
   document's bytes, the render script (which names the part or action) and
   a render version. A finished picture, its far side and its clip facts are
   copied into `<project>/.joinery/render_cache/`; a render whose key is there
   is copied back instead of drawn. An unchanged side, before or after, is
   never drawn again. The cache keeps the 400 most recently used pictures
   (front and back); a cache that cannot be read or written only costs the
   render.

## Measurements

`tests/integration/warm_blender.py`: the small textured model of ADR 0057,
six typed operations one call each through joinery-blender-ops, both stages
in one reused worker container, Apple-silicon Mac (amd64 worker under
emulation). Seconds per call:

| run | | first call | then (median of 5) |
|---|---|---|---|
| A | fresh Blender per call (`JOINERY_WARM_BLENDER=0`) | 5.0 | 4.9 |
| A | warm Blender | 4.5 | 0.26 |
| B (host load ~15) | fresh Blender per call | 12.2 | 11.2 |
| B (host load ~15) | warm Blender | 6.3 | 0.69 |

The host was shared with other work, so absolute numbers vary between runs;
the ratio held (15–19× per call once warm). One warm process served all
twelve requests (six scripts, six fingerprints) with no restart. Before
handler counts were relaxed to handler functions every operation restarted
the process, and a warm call cost 2.1 s against 3.8 s cold. With the warm
Blender, `tests/integration/blender_batch.py`'s six calls one by one took
2.6 s in all, less than the batch (4.6 s, which still starts its own
Blender).

Both stages end with the same recipe log (scripts, notes, outputs, sources
and recorded fingerprints; preimage names masked, since Blender writes a
different `.blend` byte for byte each run, cold as well) and byte-identical
GLB and texture exports; every recipe of the warm stage, history included,
replays in a fresh container to its fingerprints. A failing operation fails
with the same message warm and cold; a killed warm process is replaced, and
one that cannot start falls back to a cold run, both with the next operation
succeeding.

Review renders (same script, a fresh container per render as the gateway
runs them): a review of the model drew both sides in 10.9–12.2 s; a second
review of the same documents took 0.0 s, both sides from the cache.
Workbench pictures of one document can differ in a few bytes between runs,
so the cache shows a picture drawn earlier, not a byte-identical redraw.

## Consequences

- A single typed Blender operation costs about a quarter of a second of
  Blender time once the worker is warm, instead of two Blender starts.
- The warm path adds state that a cold run never had; the restart rules and
  the cold fall-back bound it, and the integration test compares the two on
  every output a recipe records.
- Other apps (Krita, Material Maker, the sound tools) still start per call;
  the same loop would suit any app with an embedded interpreter.
- `.joinery/render_cache` grows to a few hundred small PNGs at most.
