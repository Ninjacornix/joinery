# New-project wizard

A new project is where the user decides what game they are making, what it can
do and which apps will make it. Godot's own dialog asks for a name, a folder
and a renderer. The fork's dialog (patch `0017`, Joinery-only, not meant for
upstream) asks for those decisions in three steps and then builds the project
with the same CLI that `joinery new` uses.

## The steps

1. **What are you making?** A grid of cards, one per starting point (ADR 0024).
   Each card shows the starting point's capture (a recorded frame of it
   running, loaded with `Image.load_from_file`), its name and its one-line
   pitch. Without a capture yet, the card draws a greybox preview in code from
   the blueprint's `dimension`, its camera line and its editor `icon`. One card
   is always chosen, and the cards take keyboard focus and arrow keys. Below
   the grid: Name, the folder ("Saved in … Change…") and the optional "Your
   game in one sentence" with the caption "Journeyman reads this before every
   request." The renderer sits under Advanced and defaults from the starting
   point: Forward+ for 3D, Compatibility for 2D and UI games.
2. **Features and apps.** One switch per feature from the chosen blueprint's
   `features` array, on by `default`. A feature with `available = false` is
   disabled and shows its `reason`. Switching one on turns on what it
   `requires`. Switching one off turns off what needed it, and a `conflicts`
   clash turns the other switch off. Every row Joinery changed says why in one
   line. Each row has an "Adds: …" caption from `adds` (file count, autoloads,
   settings).
   With Steam on, the row asks for the Steam app ID inline (digits only, at
   most 10. An empty field is fine and the ID can be added later. Anything
   else shows an error under the field and keeps Next disabled). It also says
   that Joinery downloads GodotSteam, pinned and hash-checked, when the project
   is created. When Online play is on too, it asks "Connect players through:"
   Steam or Direct (IP), with Steam as the default.
   The apps follow as one summary line ("Godot, Blender, …"), with Change…
   opening the grouped app rows and their connector choices. A readiness count
   comes from each tool's `status_label` ("3 ready · 2 download on first
   use"). When an app needs Docker, that tool's `status_reason` shows with a
   Check again button, which reloads only the app list and keeps every choice.
   Above the footer, one line says what Create will do: files, licences and
   network use.
3. **Brief and art direction**, all optional. The game brief's questions
   (pitch and audience) come first. The pitch is filled from step 1's sentence
   and the other fields from the blueprint's `brief` defaults. A field the
   user typed in is never overwritten when they go back and change step 1.
   The art bible's questions follow, with References as its own section (paths
   or notes, files kept in `art/reference/`). The last section is "Starting
   rules", the bible preset, preselected from the blueprint's `bible_preset`.

Only the step's Next or Create button carries the accent fill. A chosen card
gets an accent edge and switches use the addon's switch look (`JoineryCard`,
`JoinerySwitch` in the theme).

## What the wizard reads

Nothing the wizard shows is restated in C++. On a worker thread it runs:

| Command | Used for |
|---|---|
| `joinery blueprints --json` | cards (`id`, `name`, `pitch`, `dimension`, `icon`, `capture`, `hooks`, `brief`, `bible_preset`), each card's `features` (`id`, `name`, `summary`, `default`, `available`, `reason`, `requires`, `conflicts`, `adds`, `external`), and `presets` |
| `joinery tools --json` | app rows, connectors, `status`, `status_label`, `status_reason` |
| `joinery bible --project <home> --questions` | art direction pages; a `references` question is pulled into its own section |
| `joinery brief --project <home> --questions` | brief pages (`pitch`, `audience`) |

The starting points load first, so the cards appear while the rest is still
loading. A failure shows a plain banner with Try again and a "Create plain
project" fallback, with the raw output under Details. An older CLI that sends
no per-blueprint `features` still works: the rows are derived from the
catalogue's `features`, `features_supported` and `default_features`.

## Creating

`project.godot`, the icon and `.editorconfig` are written first, by Godot's own
code, because `joinery game apply` merges the starting point's settings into
`project.godot`. Finishing only registers the project, so the merge is never
overwritten. Then each step runs with its own state and plain failure copy:

1. Copy the addon, with its MIT `LICENSE` (the addon ships inside the game, so
   its licence travels with it). A missing licence fails the step.
2. Git metadata, before `joinery setup` appends its LFS rules.
3. `joinery game apply --project P --blueprint ID --no-addon --features a,b
   [--steam-app-id N] [--connect steam|direct]`, labelled "Copying the
   <Blueprint> starting point", with a "Downloading GodotSteam" line when
   Steam is on. `--features` is always passed, because an absent flag means
   the blueprint's defaults.
4. `joinery setup` with the chosen apps, unchanged.
5. The game brief: `.joinery-brief-answers.json` (`pitch`, `audience`), then
   `joinery brief --project P`.
6. The art bible: `.joinery-bible-answers.json` (the answers, `title` and
   `_preset`), then `joinery bible --project P`.

`game apply` already drafts the brief and the bible itself, reading the
answers files when they exist and refusing to overwrite them afterwards. So
the wizard writes both answers files before step 3, and steps 5 and 6 confirm
`design/brief.md` and `art/bible.md` are there. They run the CLI themselves
only when no starting point wrote them.

`game apply` reports a failed GodotSteam download as a failed step line but
exits 0, because the game still runs without the extension. The wizard shows
that message under the step and waits on "Your project is ready" with Open
project, so the user reads it before the editor opens.

Arguments reach the CLI through `OS::execute`, which runs a shell on Unix with
each argument in double quotes. A folder path containing `"`, `$`, a backtick
or `\` is refused on step 1 with a plain message, and the runner refuses such
a value again before it reaches the shell.

## Stock Godot

Projects stay plain Godot 4.7 projects. Stock Godot never shows this wizard.
The same project comes from

    uv run joinery new <path> --blueprint first-person-3d \
        --features controller,save-load,online-play,steam --steam-app-id 480

and a project made in stock Godot's own dialog opens with the addon offering
setup (`joinery/setup_pending`). A finished project opens in stock Godot with
`--headless --import`.

## Tests

- `tests/test_fork_patches.py`: the series applies, in order, to the pinned
  tag (a throwaway index on the local checkout, no fetch). The wizard patch is
  present and runs the CLI commands above. Each patch says whether it is meant
  for upstream. Deletions stay within their recorded allowance.
- `tests/godot/run_startup_test.py` walks the wizard in dark and light with
  `visual_startup_test.gd`. It captures the cards, features with Steam and
  Online play on (app ID field and connect choice), the app ID error,
  References, and the creating page. It fails on any `ui_checks` finding or
  on more than one primary fill in a capture.
