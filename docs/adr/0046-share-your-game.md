# 0046. Share your game

Status: accepted (2026-11-11)

## Context

A game made in Joinery could be played in the editor, but getting it to players
meant learning Godot's export dialog, finding export templates, zipping the
right files with the right licences, taking screenshots by hand and writing a
store page. itch.io is where most small games start; it has an official upload
tool, butler. Every part of this touches the network or third-party binaries,
so the rules for when Joinery may download, and who may publish, need to be
written down.

## Decision

1. **`joinery share build`** exports the web, Windows, macOS and Linux builds
   with stock Godot 4.7. Joinery keeps one export preset per target in the
   project's ordinary `export_presets.cfg`: it adds a missing preset and fills
   in a setting the build needs when it is missing, and never changes a value
   the user set. The web build has threads off (itch.io serves it without
   special headers) and uses the Compatibility renderer; macOS builds are
   universal with Godot's ad-hoc signature, so `import_etc2_astc` is switched
   on for them. Joinery's own files (`addons/joinery`, `share/`, `recipes/`,
   `design/`, `tests/`) stay out of every export. Each build is zipped with a
   README (how to start it, the controls) and licence notes: the game's own
   licence, the MIT notice of the starting-point code, Godot's licence and
   third-party notices, ANGLE's for Windows and macOS, pinned extensions' and
   imported assets' licences. Sizes are checked against itch.io's browser
   limits. The build for the host system is started once headless, and the
   playtester (ADR 0038) plays the pack it ships (`--main-pack`), because
   release templates ignore `--script`.
2. **Export templates are pinned** in `policy/export_templates.toml` (the
   official 4.7.2 archive's SHA-512 and each needed file's SHA-256), shared with
   the release checks. Joinery keeps them in a private Godot data folder in its
   cache and runs the exporter with HOME pointing there, so no absolute path is
   written into the project and the user's Godot settings are not touched. A
   file is taken from that folder, from templates the user installed with
   Godot's manager, or from the archive already cached, each hash-checked. The
   1.2 GB download happens only on `--download-templates`, which the Share
   page's "Download and build" button passes; nothing downloads by itself.
3. **`joinery share media`** makes 4 to 8 screenshots, a 20 to 40 s trailer and
   a 630x500 cover, all from the game itself. A seeded headless playtest finds
   moments; a windowed replay of the same seed (`JOINERY_BACKGROUND=1`) saves
   frames; the worker's ffmpeg shrinks them to grey grids and the most
   different are kept. The trailer is a Movie Maker recording of a seeded bot
   run with a title and end card in the game's name, encoded in the offline
   worker to WebM, MP4 and an Ogg preview the editor plays. It keeps the
   game's own sound only when every audio file is tool-made; there is never
   added music. Krita in the worker composes the cover (saved as `.kra`). The
   worker's ffmpeg and Krita must match `policy/share.toml`. Rule Zero holds:
   nothing is generated, no stock media is fetched.
4. **`joinery share page`** drafts `share/page.md` from the brief, the input
   map and `joinery.lock`, leaving marked gaps rather than inventing, and runs
   the brief's filler-word check. It never overwrites the user's page unless
   asked for a new draft.
5. **`joinery share itch` is the user's act only.** It runs butler, itch.io's
   tool, once per channel (`user/game:web|windows|mac|linux`). The shared
   policy hook refuses `joinery share itch` and `butler push|login|logout|upgrade`
   in every agent's shell, so no agent can publish or even dry-run a publish;
   the command also refuses inside an agent role. The Share page's dialog
   names the itch.io page, the channels and each file before Publish is
   enabled. `--dry-run` shows exactly what would be pushed and needs neither
   butler nor a login.
6. **butler follows the GodotSteam model** (ADR 0027): one version pinned in
   `policy/butler.toml` with each platform's URL on itch.io's official broth
   server and its SHA-256; installed only with `--install-butler` (a checkbox
   in the dialog that says what is downloaded and from where), downloaded over
   https with redirects held to the same rules, size-capped, hash-checked
   before the archive is opened, unpacked into Joinery's cache, never a
   project. A platform without a recorded hash is refused, not downloaded.
7. **Credentials stay the user's.** butler uses their `BUTLER_API_KEY` or the
   login they made with `butler login` in a terminal. Joinery never asks for,
   stores or passes a key; `share/itch.json` keeps only the page name and
   channels.
8. **What is kept.** `share/` has a `.gdignore` (Godot never imports it) and a
   `.gitignore` for builds, `share/.work/` and rendered media. The page text,
   `share/media/cover.kra` and `share/recipes/` (seeds, frames, the ffmpeg
   graph, the Krita script, tool versions, output hashes) are kept so media
   can be made again. Store media is not a game asset, so it has no
   `joinery.lock` entry; the recipe is its provenance.

## Consequences

- One click builds every target on any desktop; only the host's build is
  started and playtested. Windows and Linux builds made on a Mac are checked
  for size and contents only.
- The first build needs the export templates; offline users without them see
  how large the download is and decide.
- Store media needs a display and Docker; without them the commands say so.
- butler 15.24.0 is pinned for macOS, Linux and Windows (x86-64 only:
  itch.io publishes no ARM builds). On Apple Silicon it runs through Rosetta;
  without Rosetta, Publish says how to install it. A user can still install
  butler themselves and point `JOINERY_BUTLER` at it.
- Joinery never uploads anything on its own: every network use in this
  feature is an explicit user action.
