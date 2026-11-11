# 0049. Accessibility check

Status: accepted (2026-11-11)

## Context

The art bible could ask for a contrast ratio, a minimum text size and colour-blind-safe
palette pairs (ADR 0022), and those rules judged UI resource files when they changed. Most
game UI is built in code (the game core's menus are), so nothing measured what a player
actually sees: text on its real background at the size the game draws it, menus a screen
reader can't name, a red and a green that look alike to one player in twelve, a screen
that flashes too fast, a game that can't be paused or remapped. Nothing offered the
settings players look for either.

## Decision

1. **Measured in the running game.** `joinery access --project P [--json] [--area A]
   [--plan] [--no-window]` (`joinery/access.py`) plays the game with the playtest bot
   (ADR 0038) and its probe (`--probe=access`, `joinery/playtest_bot/bot_probe.gd`):
   headless in stock Godot, it reads every visible text and focusable control on the HUD
   during play and on every menu and settings page the game core has (pause, settings and
   each page, main): text, font size and colour, what it sits on (its button, panel or
   ColorRect, blended), outline, accessible name, the label beside it, and where its
   colour and size come from (a node in a scene, a theme resource, or code). A second run
   of the same seed in a background window (`--luma`) records each drawn frame's
   luminance, whole and per quarter. The project's files give the rest: the input map,
   scripts, scenes and features. No model is needed.
2. **Nine areas, each scored** as the share of what it checked that passed:
   - **Contrast**: WCAG AA (4.5:1, 3:1 for text 24 px at 1080p and up), or the bible's
     `min_ui_contrast`; HUD text over the game world needs an outline or a panel. Disabled
     controls are exempt.
   - **Colour**: colours one script switches a property between that are distinct to most
     people but closer than the bible's `colour_blind_min_delta_e` (CIEDE2000, default 10)
     with protanopia, deuteranopia or tritanopia (Machado matrices, as in the bible);
     the bible's `colour_blind_distinct` roles; a colour-blind filter option.
   - **Text size** at `[access] target_height` (1080 by default), scaled by the project's
     stretch mode, against the bible's `min_text_px` (18 px at 1080p by default); a text
     size option.
   - **Subtitles**: voice or dialogue (files, or AudioStreamPlayer nodes, named so) need
     subtitles shown; a subtitles option.
   - **Controls**: remapping (a Controls page, or code that rebinds the input map),
     gamepad bindings, and hold-or-toggle when scripts poll held actions (sprint, crouch,
     aim, …).
   - **Motion**: shake and flash code that ignores the settings; screen shake, reduced
     motion and flash options; more than three flashes in any second of the windowed run
     (WCAG 2.3.1: opposing changes of 10 % relative luminance with the darker side below
     0.8), with its moment and seed.
   - **Pause**: a pause action that works in play (the bot's own pause check).
   - **Assists**: difficulty, assists or a game speed option.
   - **Screen readers**: every focusable control has text, an `accessibility_name` or a
     tooltip.
3. **Fixes are reviewed changes** (`joinery/checks_core.py` plans them, as match does):
   - *typed*, through the Godot MCP in the worker: a recolour of the node or theme the
     colour came from (the nearest bible palette colour that reaches the ratio, else black
     or white, and it says so), a bigger font size, an outline on HUD text, an
     `accessibility_name`. Several edits of one file fold into one write;
   - *feature*: what the **Accessibility** feature adds (below); adding a feature is the
     user's own step, so Fix opens its plan (`/feature add accessibility`) to apply;
     remapping and gamepad bindings come from the controller feature the same way;
   - *agent*: what needs code (a cue besides colour, subtitles for voice lines, honouring
     the shake setting, slowing a flash). The read-only `accessibility` agent role words
     the change and a worker makes it (ADR 0050 describes the runner they share).
4. **The Accessibility feature** (`features/accessibility`, MIT game code) puts one
   Accessibility page in Settings, replacing the core's: text size (menu text and HUD
   text sized in code), high contrast, a colour-blind filter (a full-screen
   daltonization shader for protanopia, deuteranopia or tritanopia), screen shake,
   reduced motion (which also turns shake off until it is turned off), reduce flashing
   (`Accessibility.flashes_reduced()`), subtitles and their size (the `Subtitles`
   autoload: `say(text, seconds, speaker)`, `caption(text)`), toggle instead of hold for
   held actions (any code polling `Input.is_action_pressed` follows), game speed, and
   Remap controls when the Controls page is there. Menu controls take the name screen
   readers say from the label beside them whenever a menu opens.
5. **The card.** `/access`, **Accessibility check** in Settings, or the Review tab's
   **Game checks** row opens the Accessibility card: the Match card (ADR 0045) in this
   check's words — a headline ("8 accessibility problems"), one row per area with its
   score, the top findings in their own words, **Fix N things** and **Fix** per area.

## Consequences

- The menus are read through the game core's `Menus`; a custom game without it is
  checked on what its main scene shows during play.
- Background colour is what sits behind the control in the tree; something drawn over it
  later (a full-screen effect) isn't seen. HUD text over the 3D or 2D world is judged by
  its outline or panel, not by the picture behind it.
- The flash check needs a display (the windowed run); without one the card says the
  flashes weren't measured.
- Colour-only signalling is found where a script switches one property between colour
  values; colours chosen from data or textures are not.
