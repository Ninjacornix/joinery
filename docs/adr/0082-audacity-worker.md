# 0082. Audacity returns, in its own offline worker

Status: accepted (2026-11-12). Reverses the Audacity part of ADR 0025.

## Context

ADR 0025 removed Audacity because its script pipe stalled in the worker, and
said making it dependable would need a dedicated image. The user approved
building that image (2026-11-12). Audacity is worth having for what the
`joinery-sound` ffmpeg operations do not cover: noise reduction from a noise
profile taken from the user's own recording, spectral edits, label tracks and
multi-track mixdowns.

Finding why it stalled took three causes, each confirmed in the worker:

1. **A plugin check replaced the script pipes.** With a fresh settings folder,
   Audacity checks every plugin missing from its registry in a child
   `audacity --host` process. That child loads mod-script-pipe too, unlinks
   and recreates both FIFOs, and exits. Audacity itself stays blocked opening
   the old, now unreachable FIFO, so no client can ever connect. Debian's
   Measure RMS (`rms.ny`) never registers in this build, so even a recorded
   registry triggered the check on every start.
2. **The client opened the pipes in the wrong order.** mod-script-pipe opens
   its command pipe for reading, then its reply pipe for writing; a client
   that opens the reply pipe first blocks both sides.
3. **A stale X lock in the image.** Running Audacity during the build left
   `/tmp/.X99-lock` (holding a process ID) in the layer. Xvfb then refused to
   start in any container where that ID was in use, and Audacity exited with
   status 255 and no output: about one start in eight.

## Decision

1. **Image.** `sandbox/audacity.Dockerfile` builds `joinery-audacity-worker` on
   the MCP worker, not the creative worker (the creative worker's Surge XT
   plugins would be scanned and stop every command behind an "Incompatible
   plugins" dialog). Audacity is Debian trixie's own 3.7.3 build
   (`AUDACITY_DEB_VERSION` in `versions.env`), downloaded as the exact
   `.deb` pair and checked against `checksums.txt` like every direct download;
   apt installs its dependencies from the GPG-verified archive. The build
   fails if any third-party audio plugin is present.
2. **Startup.** The build diverts `rms.ny` (with `dpkg-divert`, so dpkg knows),
   runs `joinery-audacity --prime` once to record the plugin registry, proves
   a run answers (`--check`), and removes everything those runs left in `/tmp`.
   Every run starts from a fresh settings folder: the recorded registry,
   mod-script-pipe enabled with its path and date (or Audacity resets it),
   the installed version (or a "What's new" dialog blocks every command),
   dither off (it adds random noise), and no first-run dialogs. The launcher
   waits for the display to accept connections, waits for Audacity's CPU use
   to settle, refuses to continue if the pipes were replaced, and opens them
   in mod-script-pipe's order.
3. **Typed operations, recorded and replayed.** An Audacity project (`.aup3`
   under `audio/`) is edited with `joinery-sound operate_document`, whose code
   is a step list: `import` (a recording in the project), `noise_reduction`
   (profile span, region, amounts; one per project, as Audacity keeps one
   profile), `spectral_edit` (time and frequency box; delete, or parametric
   EQ gain), `label`, `track` (name, gain, pan, mute), `move` (a clip). The one
   fallback is `macro`: raw scripting commands with a reason, refused for
   file, preference, transport and Nyquist commands, and listed separately in
   the replay report. The recipe is the whole list; every replay rebuilds the
   project from the recordings, which are the recipe's sources (so an edit
   keeps no copy of the old project, and an agent sends the whole list back
   with its change; `inspect_document` returns it). Exports are WAV mixdowns
   and one label file; Ogg is made from the WAV by the sound tools.
4. **Exact replay.** Noise Reduction takes no scripting parameters, so its
   amounts go into the settings file before Audacity starts. Spectral effects
   need the track shown as a spectrogram (else a modal dialog), so the step
   switches the view and back. Audacity is killed rather than closed (closing
   can ask questions), and the project's SQLite write-ahead log is then
   checkpointed into the `.aup3`, checked for integrity, and its `-wal`/`-shm`
   files removed. With these, the WAV, the label file and the `.aup3` replay
   byte for byte; no fingerprint is needed.
5. **Own worker for the tool's recipes.** A tool module may name a
   `replay_image`; `operate_document` and `joinery rebuild` run its recipes
   there instead of the creative worker.
6. **The pinned Audacity MCP**, for inspection, runs beside its own Audacity
   (`joinery-audacity --serve-mcp` waits until Audacity is ready, leaves the
   one pipe connection to the MCP, and becomes it). Its packages come from
   `sandbox/audacity-requirements.txt` (hashed, `--no-deps`); its source is
   mounted read-only from the pinned install like the Inkscape MCP. Its
   manifest also names faster-whisper, a speech model: Joinery never installs
   it, the build fails if faster-whisper, whisper, ctranslate2, onnxruntime,
   torch, transformers, huggingface_hub or tokenizers can be imported, and its
   seven transcription tools are `generative_tools` in the catalog. They are
   hidden from discovery, and a call to one is a Rule Zero violation that
   stops the turn for every agent, in the shared hook and in the gateway
   before a worker starts.
   Its own writes have no recipe, so review refuses them; agents are told to
   keep edits with `operate_document`.

## Consequences

- Setup offers Audacity again; readiness reports `needs_image` until the worker
  is built, then checks Audacity's version and that it answers its pipe.
- The image adds about 37 MB (compressed content) to the MCP worker it builds
  on; a rebuild of its own layers takes about 40 s on a warm base. Audacity is
  ready about 6 s after launch under amd64 emulation on Apple silicon.
- Measured only under emulation (macOS arm64, Docker Desktop with Rosetta). The
  native amd64 CI run is still the deciding result.
- Not typed: effects beyond noise reduction and spectral edits (use the sound
  tools, or a logged `macro`), MIDI, and recording (the worker has no audio
  device). Audacity's own Nyquist plug-ins run only as named commands inside
  a `macro`; Nyquist code is refused.
- `tests/integration/audacity_native.py` covers the real worker; the unit
  tests cover the module, the recipe, the per-tool image and the policy.
