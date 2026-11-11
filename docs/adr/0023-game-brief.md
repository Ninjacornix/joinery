# Game brief

The art bible says how a game looks and sounds. Nothing said what the game is:
its pitch, how it plays, which devices it supports, whether it goes online,
saves, or ships in more than one language. Journeyman had to guess, or ask the
same questions in every session.

Each project now has `design/brief.md`, rendered from `templates/game_brief.md`
by `joinery/brief.py`. Nineteen sections run from the pitch to scope and open
questions, and end with a small TOML **facts** block (genre, dimension,
platforms, stores, input, online, players, host model, session length,
languages, saving, monetisation, rating).

**The draft has three sources, and no others.** The wizard asks one line (the
pitch) and, optionally, the audience. The starting point (blueprint) supplies
genre, camera, core loop, progression and player verbs; these sections end with
"_From the <name> starting point; change freely._". The chosen features decide
facts: online play means LAN with four players, Steam plus online play means
online, controller adds gamepad input, save-load saves, translations add an open
question about languages. Everything else is `_Not decided yet._`. Monetisation
is never suggested unless the user raises it.

**Controls come from the applied input map.** When the project already has an
`[input]` section, the controls table is read from `project.godot` itself, so
it shows exactly what the starting point and features bound, gamepad included.
Otherwise it comes from the blueprint description, with pad bindings only when
the controller feature is chosen. The reader handles Godot's own multi-line
`Object(InputEvent…)` text; a test writes an input map with stock Godot and
checks the key codes and names it reads back.

**The brief directs; it does not block.** It sits outside `art/` and `audio/`,
so it is not an asset, needs no lock entry and has no approval gate. Changing it
is a reviewed edit like any other file. Wizard answers arrive in
`.joinery-brief-answers.json`, which is consumed and deleted on write, as with
the bible. An existing brief is never overwritten by setup.

**Suggestions, never actions.** `consistency()` compares the facts with the
project's `[game]` record in both directions: the brief says online but online
play is not added, gamepad without controller support, several languages
without translations, Steam without the Steam feature, saving without
save-load, and the reverse cases where a feature is added but the brief says
otherwise. `summary()` orders suggestions as broken facts, then setup
disagreements, then the brief's open questions, then at most three open
sections. Adding a feature is a user action (`/feature add`, CLI); the agent may
only suggest it.

**Registry.** The read-only `joinery-registry` server gains `game_brief`
(the summary plus an edit hint) and `game_setup` (the `[game]` record, the
`game/*` settings from `project.godot`, what features can still be added and
why others cannot, and the game core's extension points so the agent builds on
`Menus`, `GameEvents`, `Game` and the saveable group instead of editing
`core/`). `art_direction` gains `lint`, the bible lint summary. The catalogue
and lint modules are optional imports, so the registry serves every tool even
when one of them is absent; a failing lint is reported beside the bible, never
instead of it.

## Addendum: design documents (2026-11)
The user's own design documents (Markdown, text or PDF, at most 10 at a time)
are copied into `design/docs/` from the wizard, `--design-doc PATH` or
`joinery references add`, and listed in a **Design documents** section at the
top of the brief. `joinery/gdd.py` drafts open brief sections from Markdown and
text only by deterministic extraction: ATX and setext headings outside code
fences are matched to brief sections through a fixed phrase table, and the
section's own text (subheadings turned into bold lines, cut at 1,500
characters with a pointer to the file) replaces the open marker, ending with
the file and heading it came from. A section the user wrote is never changed;
a heading the table doesn't know stays only in the document. PDFs are not read:
text pulled from a PDF loses its order in columns and tables, a PDF library
would be a new dependency, and every supported agent reads PDFs itself, so the
brief says the document is there and `game_brief` returns it with `text:
null`. `game_brief` returns every document in `design/docs/`, with the text of
Markdown and text ones (20,000 characters each, 60,000 in all). The brief and
the bible win where a document disagrees.

