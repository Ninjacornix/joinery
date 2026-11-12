# 0025: Remove Aseprite, REAPER, MuseScore and Audacity; drop the gimp-mcp pin

Status: accepted, 2026-11-09. Amends the tool table of ADR 0003 and the
user-licensed tool notes in ADR 0008 and ADR 0017. The Audacity part is
reversed by ADR 0082 (2026-11-12): the dedicated image was built, and the
stall turned out to be a plugin check replacing the script pipes.

## Context
Every catalog tool must do its work inside Joinery's offline workers: the
worker is the unit of trust for asset work (ADR 0008), and a tool Joinery
cannot run there cannot be operated, replayed or checked. A per-tool audit
(2026-11-09) ran each program and MCP in the workers:

- **Aseprite** was never run. Its EULA forbids redistribution, so it can never
  ship in a worker image, and asking every user to mount their own Linux build
  is not a path anyone took. Krita already covers pixel art.
- **REAPER** is proprietary and closed, and its MCP needs a live `reapy`
  session in a desktop REAPER. It was never run. SuperCollider and Surge XT
  already cover sound design and synthesis.
- **MuseScore** has no headless mode its MCP can drive (the MCP talks to a GUI
  plugin over a WebSocket), yet the setup screen showed it as ready.
- **Audacity** needs its script pipe, which under the worker's virtual display
  stalled behind startup dialogs and plugin scans; making it dependable would
  need a dedicated image. The user chose not to build one.
- **GIMP**'s community MCP (gimp-mcp) needs a running desktop GIMP. GIMP 3.0.4
  itself runs headless in the creative worker through `gimp-console` and
  Python-Fu.

## Decision
- Aseprite, REAPER, MuseScore and Audacity leave `policy/preferred_tools.toml`
  with their MCP pins. Their adapters, doctor probes, upstream test lanes and
  CI jobs go too. The Aseprite Lua adapter is deleted.
- GIMP stays as a catalog tool owned by Joinery's own `joinery-paint` server;
  the gimp-mcp pin is dropped. Until Joinery's Python-Fu adapter ships, GIMP is
  status-only: readiness reports it present in the worker, and Journeyman says
  it cannot edit GIMP documents yet. (Update: the adapter has shipped in
  `joinery/tools/gimp.py`; GIMP now edits `.xcf` documents offline.)
- Pixel art is made in Krita. Music is written as MIDI sources rendered
  through FluidSynth (General MIDI SoundFont) and Surge XT; sound edits that
  Audacity would have done (trim, fade, normalise, loop points, format) become
  ffmpeg typed operations in `joinery-sound`.
- A project that still enables a removed id keeps working: `selection_ids`
  skips the id, and `catalog.REMOVED_TOOLS` gives the notice shown wherever
  tool status is shown, for example "Aseprite was removed: it can't be
  packaged in Joinery's offline workers (paid licence). Use Krita for pixel
  art, or add Aseprite yourself as a custom tool."
- Users may still add any of these programs as their own custom tool (Rule
  Zero's per-project exception); its assets are `origin: custom-tool`.
- Audacity keeps its desktop "Open in" entry: opening a WAV by hand never
  changed provenance and needs no worker.
- `.aseprite` and `.ase` files keep their Git LFS pattern and human-import
  provenance, so imported files still work.

## Consequences
The catalog lists only tools that run offline in Joinery's workers, so the
readiness core (`joinery/readiness.py`) can check every one of them the same
way. The upstream MCP test lane has no community suite left; it stays for the
next community MCP that clears the bar. Earlier evaluation reports keep their
numbers as history, annotated with this removal.
