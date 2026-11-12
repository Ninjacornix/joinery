# 0084. Joinery's own icon in the editor and its packages

Status: accepted (2026-11-12)

## Context

The Joinery editor still showed Godot's icon everywhere it showed one: the
window and Dock icon, the boot splash, the Windows executable, and nothing at
all in the packaged macOS app or the Linux archive. Users asked for Joinery's
own icon in its place.

Rule Zero applies to the icon like to any asset: it is drawn, not generated.
And the boundary with Godot stays where the licences put it: Joinery is built
on Godot Engine, games made with Joinery are Godot games.

## Decision

1. **The mark.** A through dovetail seen from the face: a brass tail board
   (`#e6b66b`, the dark theme's brass accent) seated in the socket of a deeper
   brass pin board (`#b07d36`), with a thin shadow line between them, on a
   rounded square of the dark theme's surface (`#202123`). No text, no
   gradients. Three hand-written SVGs in `release/icon/`:
   - `joinery.svg`, the icon (1024 units, flat geometry).
   - `joinery-small.svg`, the same drawing on a 16-unit pixel grid, used at
     16 and 32 px, where the large drawing's joint line falls between pixels.
   - `joinery-mono.svg`, one colour on transparency with a wider joint, for
     menu bars and trays (a template image).
2. **One deterministic build.** `release/icon/build_icons.py` rasterises with
   `rsvg-convert` at a pinned version (2.60.0; any other version refuses to
   run) and assembles the `.ico` (16 to 256, PNG entries) and `.icns` (16 to
   1024, PNG slots, the large sizes on Apple's 824-in-1024 grid) itself, byte
   for byte. Every output and every source is hashed in
   `release/icon/icons.lock.json` (`origin: tool-created`, the recipe is the
   SVGs plus the script). `--check` rebuilds in memory and compares;
   `tests/test_icons.py` checks the containers and sources against the lock.
3. **The fork** (`godot_patches/patches`, one Joinery-only patch, not intended
   for upstream): the editor and project manager use Joinery's window icon
   (`main/app_icon_editor.png`, and the Apple-grid variant for the Dock on
   macOS), boot on Joinery's splash (`main/splash_editor.png`, the
   `no_editor_splash` default flipped, background `#202123`), the Windows
   executable embeds `joinery.ico`, and the project manager's wordmark is led
   by the mark (`editor/icons/JoineryLogo.svg`). The build script installs
   these files with `--godot-src`.
4. **Packages** (`joinery/distribution.py`): `Joinery.app` carries
   `Contents/Resources/Joinery.icns` and `CFBundleIconFile`; the Windows
   folder has `Joinery.ico` for shortcuts; the Linux archive has
   `share/applications/joinery.desktop` and the hicolor PNGs, ready to copy
   under `~/.local` (the entry runs `Joinery`, so the launcher must be on
   `PATH`). `release/verify_bundle.py` checks the macOS icon.
5. **What stays Godot's.** The About dialog keeps Godot's logo, authors,
   donors and licences: it is the engine's credits ("built on Godot Engine").
   Games keep Godot's icon: `main/app_icon.png` and `main/splash.png` are
   untouched, a game run from the editor without an icon of its own still
   shows Godot's, the default project icon is unchanged, and exports use the
   pinned stock export templates, so nothing in an exported game changes.

## Consequences

- Changing the icon means editing an SVG, rerunning the build script (with
  `--godot-src` for the fork), exporting the patch and committing both; the
  lock shows exactly which outputs moved.
- The rasters depend on one librsvg version. Upgrading it is a deliberate
  step that rewrites the lock.
- The Windows `.ico` uses PNG entries, which Windows Vista and later read; the
  resource compiler embeds them as they are.
- There is no web editor build, so there is no favicon to replace.
