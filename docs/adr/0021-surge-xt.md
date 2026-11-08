# 0021: Surge XT through Joinery's sound server

Status: accepted, 2026-11-08. Supersedes the Surge XT part of ADR 0008.

## Context
ADR 0008 left Surge XT out of the sandbox because upstream seemed to publish
only nightly Linux builds under a moving tag. That was wrong: stable releases
live in `surge-synthesizer/releases-xt`, and 1.3.4 ships a Linux `.deb` and
plugin archives. No preferred MCP for Surge clears the catalog bar.

## Decision
- The creative worker installs Surge XT 1.3.4 from its pinned `.deb`, checked
  against `sandbox/checksums.txt` like every direct download, plus a hash-locked
  Python environment with Pedalboard (GPL-3.0, sandbox only, never in a game).
- `joinery-sound` gains `surge-xt` documents. The native source is JSON under
  `audio/`: Surge parameters by name in the plugin's display units, and notes.
  `joinery-surge` renders it offline through the real VST3 to 16-bit WAV.
- Recipes replay exactly. Oscillators retrigger by default, since free-running
  phase changed every render. The renderer renders twice and refuses a patch
  whose two renders differ, rather than recording an asset that cannot replay.
- A number given for a stepped parameter (such as pitch in semitones) snaps to
  the nearest step; an out-of-range value or unknown name fails by name.
- Inspecting a Surge source also lists all parameters with their defaults.

## Measured (2026-11-08, Apple Silicon under x86 emulation)
Create 17 s and edit 12 s for a 1.2 s sound; lowering the cutoff lowers RMS
(0.057 to 0.045); replay matches byte for byte. CI runs the same check natively.

## Limits
Factory `.fxp` patches are not loaded yet; sounds start from Surge's init patch
and named parameters. Patches that need randomness (noise, random LFOs) are
refused until a seeded approach exists.
