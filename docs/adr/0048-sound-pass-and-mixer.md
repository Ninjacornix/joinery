# 0048. Sound pass, adaptive music and the Mixer

Status: accepted (2026-11-11)

## Context

Joinery could already make a sound or a song by operating Surge XT, SuperCollider and
FluidSynth (`compose_music`, `process_audio`, SuperCollider NRT patches), and judge its loudness
against the art bible. A game still came out flat: one music file that loops the same way in
a quiet corridor and in a fight, nothing under the player's feet, silent coins and levers, no
room tone, and no place to balance music against effects. Users asked for "depth of music"
and for someone to go through a level and give it its sounds.

## Decision

### 1. A track is layers, sections and states

`compose_track(name, layers, mood, key, tempo_bpm, bars, sections?, stingers?, density?,
seed?)` on `joinery-sound` (`servers/sound/tracks.py`) composes an adaptive track **by
rules, not by a model** (`joinery/tools/compose.py`): the mood picks a mode, two chord
progressions (one per section), General MIDI instruments, a velocity and a density; chords are
voiced with the smallest moves; each role has its own writer — pads hold the chords, bass
plays roots (whole notes, root-fifth halves or an eighth-note pulse with approach notes),
percussion plays General MIDI kit patterns with a fill in the last bar, melody repeats and
answers a seeded one-bar motif with chord tones on strong beats, tension holds a semitone
cluster over a low pulse. One seeded generator makes every choice: the same plan writes the
same notes on every machine; `variation` changes melody and fills and keeps the harmony.

Each section is one Standard MIDI file (`audio/music/<name>/<section>.mid`, one track per
layer) that anyone can open in a sequencer. One recipe renders the whole track in the offline
worker (`joinery/tools/stems.py`): every layer alone through FluidSynth (FluidR3_GM, checked by
hash) or its Surge XT patch; its release tail folded into the start and cut at exactly `bars`,
so the loop has no seam; the section's **mix** measured (EBU R128) and **one gain** applied to
every stem, so the mix meets the bible's music loudness and the stems keep their balance and
sum to the mix; one Ogg Vorbis per stem, every stem of a section the same number of frames,
with Godot import settings that loop and carry BPM, beat count and bar length. Stingers
(victory, checkpoint, fail) are short phrases of their own, normalised alone. The recipe keeps
the plan on one line; `joinery rebuild` replays it byte for byte.

`edit_track(name, changes)` changes the plan (tempo, key, mood, bars, density, variation,
seed, sections, stingers, add/remove layers, re-voice, per-layer density) and renders again; a
render that drops a layer retires the old recipe and its stems, and puts both back if the
render fails. Muting a layer only changes the description.

The **description** (`music/<name>.tres`, `joinery/music_track.py`) is a plain Godot
`Resource` with one metadata dictionary — no script, so stock Godot opens it and the game
needs no Joinery code to read it: tempo, bars, layers (role, fader in dB, mute, solo),
sections and their stems, the level (0–1) of each layer in each intensity state (calm,
explore, danger, combat, victory; combat plays the second section by default), when changes
land (`bar` or `beat`), the fade length and the stingers. It lives outside `audio/` because
everything under `audio/` is an asset with a recipe; the description is data the user edits
in the Mixer. An edit keeps the faders, mutes, solos and state levels the user set.

The bible's loudness rule judges a track by its **section mix** (`section_mixes`,
`mix_measure_script`: ffmpeg `amix` without normalisation, then `ebur128`); each stem only has
to stay under the true-peak ceiling. `/match` does the same and fixes a mix that drifted by
rendering the track again at the target (`edit_track` with `lufs`).

### 2. The game plays it: audio in the game core (MIT)

The core (`blueprints/_core/overlay`) gains six buses in `default_bus_layout.tres` — Master,
Music, SFX, Ambience, UI, Voice — with a compressor on Music keyed to Voice (music ducks under
dialogue) and a room reverb on Ambience. The player's volume sliders now scale on top of the
layout's own bus volume (`Settings.base_volume_db`), so a designer's mix survives them.

`Music` (autoload, `core/audio/music.gd`) plays a description: every layer of a section is an
`AudioStreamPlayer` on the track's bus, all started in the same frame inside
`AudioServer.lock()`, so they stay sample-locked; the bar clock is the playback position plus
the time since the last mix, never frame counting. `set_intensity(state, hold)` waits for the
next bar (or beat, with `change_on = "beat"`) and fades each layer to its new gain over
`fade_beats`; a state that plays another section switches on the next bar. `stinger(event)`
starts on the next beat and ducks the layers 6 dB while it plays. With `auto` on, the game
drives it: `GameEvents.level_started` plays `audio/music/track` at the default state,
`level_completed` plays the victory stinger and holds victory, `level_failed` and
`checkpoint_reached` play theirs, an `enemy` node within `audio/music/combat_radius` (5 m) or
`danger_radius` (12 m) of a `player` gives combat or danger, and a player with `health` at or
under `audio/music/low_health` (30 %) of `max_health` gives at least danger. A missing track or
stem only warns; the game never stops for it. `play_data()` plays a description from memory
with synthesised streams, for tests.

For the sound pass, four silent building blocks: `SoundCue` (plays its stream when its
parent's signal fires — `body_entered`, `switched`, `pressed_changed` — positionally, and keeps
playing if the parent is freed, as a coin is), `Footsteps` (a step every `stride` metres on the
floor collider's `surface` metadata, jump and land), `AmbienceZone` (an `Area3D` that fades its
loop in on the Ambience bus while a player is inside, with Godot's area reverb) and `UiSounds`
(autoload: `audio/ui/click` and `audio/ui/hover` on every button, on the UI bus).

### 3. The sound pass

`/sound-pass [focus]` in the sidebar (or `joinery sound-pass --project P [--json] [--plan]
[--scene res://…]`, no model) listens to a level the way match looks at a bible
(`joinery/sound_pass.py`): the level and player scenes and their scripts tell it what is
missing — footsteps per floor surface (guessed from node, parent and material names; walls,
gates and doors skipped), jump and land, pickups, levers, plates and buttons (by their
signals), hits and damage, UI click and hover, and ambience (cave, forest, room tone or wind by
the level's names). The latest playtest report ranks what the player actually ran into.

The plan (`joinery/sound_plan.py`) groups the work by the file it changes — the player scene,
the level, `project.godot`, props by kind, at most three per task — so tasks never claim the
same file and run in parallel as scripted crew tasks (`bridge/sound_pass.py`, the same machinery
as match, ADR 0045). Each task makes its sounds first with `make_sound` and then writes each
scene once through Godot's `write_file` (`joinery/scene_text.py` adds ext_resources, nodes and
`metadata/surface` to the `.tscn` text so stock Godot loads it). Every change goes through the
submit checks, the reviewer and the glanceable review.

`make_sound(kind, name, surface?, variant?, seed?)` on `joinery-sound` (`joinery/sound_kit.py`,
`servers/sound/make.py`) is a typed kit: footsteps per surface, jump, land, hit, damage, door,
button, lever and plate as seeded SuperCollider NRT patches (`RandSeed`, so noise replays
exactly); pickup, UI click and hover as Surge XT patches without noise; wind, room tone, cave
and forest beds as SuperCollider loops exported to Ogg with a crossfaded loop. Loudness is the
bible's per folder, or the kit's own (footsteps −24, pickups −18, UI hover −28, beds −22 LUFS).
One chained recipe per sound, so `process_audio` and `set_surge_parameters` keep working on it.

The **sound designer** (`sound-designer` in `joinery/roles.py`) is the chat's provider in a
read-only role, like the playtester (ADR 0038): it reads `sound_audit` and writes a short
report of what the level was missing and what is being made. The host runs the audit and the
tasks even if the agent fails. A **Sound pass** card shows the headline ("11 sounds missing in
level.tscn"), one row per change and Stop.

### 4. The Mixer

A **Mixer** tab in the bottom panel (`mixer_tab.gd`, `mixer_strip.gd`, `mixer_music.gd`): one
strip per bus with a fader, mute, solo, a live meter, a reverb and a compressor with one amount
each, and **Duck music under** Off / Voice / SFX (the Music compressor's sidechain); **Add them**
adds missing core buses to an older project. Below, the track picker (`music/*.tres`), one row
per layer with fader, mute and solo, **Play** and an **Intensity** slider (calm → victory) that
auditions the stems live in the editor with the game's gains. Changes are heard at once on the
editor's AudioServer and are never written behind the review: **Save mix** sends `mixer_save`
and the host (`bridge/mixer_save.py`, `joinery/mixer.py`) writes the bus layout and the
description as a scripted crew task — checks, reviewer, glanceable review. **Revert** reloads
what is on disk. Without a connected agent Save is off and says why.

### 5. Review

The A/B audio with its waveform stays. A change that writes a track shows a mini layer view
under it (`review_music.gd`): the track line (tempo, bars, key, mix loudness) and one row per
layer with a small waveform of its stem and its level in the default state.

## Consequences

- A game gets music that follows play — layers in and out on the bar, a combat section, stingers
  — from MIDI anyone can open, rendered by FluidSynth and Surge XT, replayable byte for byte.
- A level gets its sounds in one request, each a small reviewed change in the app that owns it.
- Projects made before this core have no buses beyond Music and SFX and no sound nodes: the Mixer
  adds the buses, the sound pass refuses ("Update the game core") rather than installing them.
- The composer is rules: competent loops in nine moods, not arrangements. Users who want more
  edit the MIDI or re-voice layers with Surge patches.
- One-shots can't reach a bible asking for very loud SFX (−16 LUFS footsteps) without limiting;
  the check says so. 2D levels get footsteps and a plain ambience player; tilemap floors stay
  "default". A node with only `take_damage()` and no signal is listed for the user, not placed.
- The review's mix loudness comes from the change's own measurements; the submit checks measure
  the mix again.
- Two parallel tasks on a project never opened in Godot can clash on the `.uid` files each
  stage's import writes; opening the project once first avoids it.
