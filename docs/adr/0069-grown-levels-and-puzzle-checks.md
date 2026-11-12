# 0069. Grown levels and puzzle checks

Status: accepted (2026-11-12)

## Context

Two requests came from users making many small levels. First: "here is a level I
drew; make me ten more like it". Writing each layout by hand (ADR 0043) is slow, and
an agent asked for "another one like this" drifts. Rule Zero rules out a generative
model, but not a deterministic algorithm working from the user's own example: Wave
Function Collapse (Gumin, 2016) grows a new grid whose every small window also occurs
in the example. Second: grid puzzle games (push the boxes, find the key, hold the
switch) and the memory-cards starting point ship levels nobody has proved can be
won, and a level that can't be won reaches players as a bug. Both are code, not
asset generation.

## Decision

### Levels grown from the user's example

1. **Only the user's own example.** `joinery blockout grow --sample <png|toml> --size
   WxH --seed N` and the `grow_level(sample, size, seed, …)` tool read a local file:
   a picture the user painted (`joinery/blockout/png.py` reads PNGs with no image
   library; one pixel or one block of pixels per cell, block size found from the run
   lengths), a grid drawn in text (`grid = '''…'''`), or one of their blockout
   layouts (rasterised in cells of one metre or one tile). Links are refused and
   nothing is fetched; the tool only takes a file inside the project (an attached
   picture counts). Colours: dark or transparent wall, light floor, blue spawn, green
   goal, yellow coin, cyan pickup, magenta checkpoint, red hazard.
2. **Overlapping WFC, our own code** (`joinery/blockout/wfc.py`, MIT). 3 x 3 patterns
   with their rotations and mirror images (side views mirror only, gravity stays
   down), weighted by count; least-entropy observation; propagation over
   compatibility masks. A side of the sample that is one tile all along (an outer wall,
   the ground) is kept on the same side of the output, and a solid block seen only
   against such a side stays there, so a thick border can't flood the middle. Only
   `random.Random(seed * 1009 + try).random()` is drawn, so a seed always grows the
   same grid.
3. **A plain blockout layout** (`joinery/blockout/grow.py`). The largest open region
   is kept (pockets become wall); the spawn goes where the sample had it (scaled),
   goals as far from it as the floor allows (side views: standing places within the
   player's jump), the sample's other markers scaled by area. One room spans the open
   part; wall cells merge into rectangles that become platforms (wall-high blocks in
   3D, obstacles top-down, ground side-on). Cells are 2 m in 3D, 2 tiles top-down, 1
   tile side-on (1 m or 1 tile for a layout sample). The result is an ordinary
   `design/blockouts/<name>.toml`; check, build and walk treat it like any layout.
4. **Reachability is enforced.** Each try is checked by the blockout check
   (`check.py`, `reach.py`) with the project's player; a try where the spawn misses a
   goal, or WFC contradicts itself, is dropped and the next try at that seed is grown,
   up to 12. If none passes, the answer says why the last tries failed.
5. **The recipe.** The layout's new `[grown]` table records the sample path, its
   SHA-256, the seed, size, cell, pattern size and which try passed.
   `joinery blockout grow design/blockouts/<name>.toml` grows it again and reports
   "Same bytes as before"; a changed sample is refused rather than silently growing
   something else. Keep the sample in `art/reference/` to grow again elsewhere.
6. **Reviewed like a blockout.** `grow_level` stages the layout and scene through the
   blockout tool's own staging (`stage.stage`): the playtest bot walks it in Godot,
   the facts are "Walkable: …" and "Grown from <sample>: WxH cells, seed N", and the
   review shows the grown plan beside the sample as Joinery read it (an item with a
   `reference`). The lead and workers may call it; read-only roles may not, in every
   agent (roles, the policy hook, Codex approvals).

### Puzzle solvability and difficulty

1. **A level format** (`joinery/puzzle/grid.py`): `design/puzzles/<name>.toml` holds
   one `map` or several `[[levels]]`, drawn in text: `#` wall, space floor, `@`
   player, `$` box, `.` goal, `>` exit, `a`-`z` keys, `A`-`Z` doors, `_` switches,
   `=` gates (open while every switch is held), with `move_limit` and `keys =
   "consumed" | "kept"`. Won when every goal has a box and, if there are exits, the
   player is on one.
2. **A bounded solver** (`joinery/puzzle/solve.py`): breadth first over states
   (player, boxes, keys picked, doors opened) up to 200 000, so the first win is a
   shortest one; boxes pushed onto squares that can never reach a goal end that line.
   With the whole space searched it also counts dead ends (reachable states from
   which no win is reachable) and moves per state. Difficulty (easy, medium, hard)
   is a rough score from the shortest win, the dead-end share and the branching.
   Unsolvable levels say why: too few boxes, a walled-off exit or goal, a box stuck
   against a wall, the move limit, else that every reachable state was tried and how
   close the best came. Too large to decide is a warning, never a send-back.
3. **Memory-card deals** (`joinery/puzzle/cards.py`): the deck's shuffle is repeated
   exactly (Godot's PCG32 seeding and bounded draws; a Godot test compares 160 deals),
   and seeds 1-200 are played by a perfect-memory player. Unsolvable when `pair_count`
   exceeds the deck's symbols or `move_limit` is below `pair_count`; a warning when
   most deals need luck to fit the limit.
4. **Where it runs.** `submission.validate` checks staged puzzle files and, in a
   memory-cards game, a change to `game/level.tscn`, `level.gd` or `deck.gd`; an
   unsolvable one comes back as a `puzzles` problem with `puzzles.unsolvable`,
   `puzzles.move_limit` or `puzzles.cards_unsolvable` items naming the file, the
   level and the reason. Precheck shows the same. `joinery puzzle check [files]
   [--json]` runs it on a project.

## Consequences

- A grown level is a recipe twice over: sample + seed grow the layout, the layout
  builds the scene. Both are byte-stable.
- Small samples grow levels that share the sample's local shapes, not its rooms:
  WFC copies 3 x 3 neighbourhoods, it doesn't understand the plan. A larger example
  gives more structure. One floor height per cell; grown 3D levels are flat.
- The grid level format is Joinery's own; a game reads it however it likes. The
  solver judges the rules written here; a game whose rules differ (ice, pulling
  boxes) needs its own rules in the solver first.
- The difficulty label is an estimate from search numbers, not a judgement of fun.
