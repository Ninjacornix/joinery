# Accepted recipes as skills

Accepted: 2026-11-12.

## Context

Every asset in a project is made by a recipe (`recipes/*.toml` and the op log
it runs) that the user accepted with the change that made it. Those recipes are
the project's own worked examples: the door-creak patch is most of a gate
creak, the stone-tile material most of a cobble path. Agents started each new
asset from nothing, spending calls and tokens to rediscover tool operations and
numbers the project already settled on. Voyager's skill library and Agent
Workflow Memory both reuse past successful procedures the same way.

## Decision

`joinery.skills` indexes a project's recipes by what they made, and a
read-only `find_recipe(request, limit)` tool returns the closest ones.

- **What is indexed.** Every recipe in the project's `recipes/` (a recipe is
  there only once its change was accepted; staged changes live in their own
  folder). For each: its tool, op log, outputs, note, the operations its op log
  calls, named numbers in it (constants, assignments, keyword arguments; a kit
  recipe's recorded parameters), and the summaries of the accepted changes
  that touched the recipe, its op log or its outputs (from the change
  journals).
- **Where.** `.joinery/skills/index.json`, per project. An entry is rebuilt
  only when its recipe, op log or summaries changed (a content digest). The
  summaries are kept in the index, so they outlive the journals history
  retention removes. Accepting a change refreshes the index (bookkeeping: it
  never fails the decision); `find_recipe` and `joinery skills` refresh it too.
- **Scoring.** BM25 over words, no model and no network. Words are split from
  snake_case and CamelCase, folded (plurals, -ing, -ed, a trailing -y or -e:
  "creaky", "creaked" and "creaks" are all "creak") and stripped of filler and
  code words. A word counts by where it was found: change summary ×3, note and
  output names ×2, tool ×1.5, op log ×1.
- **What the agent gets.** Up to five recipes with score and matched words,
  tool, op log path, outputs, note, the requests it was made for, its
  operations and parameters, and a next step: read the op log, make the new
  asset with the same tool's operations and changed parameters (recorded as
  its own recipe), or edit the existing asset when the user asked to change
  that one. Nothing is copied or run by `find_recipe`.
- **Who.** The lead and workers (`*` in their registry lists). Read-only roles
  are not offered it.
- **Per project.** Recipes reach another project only inside a kit, whose
  recipes land in that project's `recipes/` and are indexed there. Nothing reads
  another project's index.
- **CLI.** `joinery skills --project P [--find REQUEST] [--json]`.

## Consequences

- Rule Zero is unchanged: a recipe found is the project's own tool-made
  recipe; the new asset is still made by operating the tool and gets its own
  recipe and lock entry.
- Lexical scoring misses synonyms ("gate" does not find "door"); the change
  summaries, notes and output names carry most of the matches. A better scorer
  can replace `score` without changing the tool.
- On the testbed: "a creaky gate sound" finds the door SFX patch, "stone floor
  texture" the stone-tile material, "wooden chest with brass hinges" the door
  model, "calm exploration music" the music patch, "evening sky" the sky
  (`tests/test_skills.py`).
