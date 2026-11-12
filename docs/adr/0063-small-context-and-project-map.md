# Small context and a project map

Accepted: 2026-11-12.

## Context

ADR 0056 made the start of every prompt the same bytes turn after turn, so a
provider's prefix cache can hit. What is *in* the prompt still cost a lot:

- The editor context the addon sends holds the scene on screen as up to 1,000
  nodes, each with its transform and up to 32 stored properties (up to 500 kB).
  It went into every turn whole: about 13.9k tokens for a 60-node scene, 46k
  for a 200-node one. ADR 0056 already named it the bulk of the lead's uncached
  input.
- Tool results went back to the agent whole: a scene tree from
  `inspect_editor`, a 1,500-line run log from a Godot call, every finding of a
  check. In a session they stay in the conversation, so a result read once is
  sent again with every later request.
- An agent changing three lines of a script through Godot's `write_file` sent
  the whole file back, paid as output tokens.
- An agent started each turn knowing only the scene on screen, so it spent
  calls (and their results) finding out what the project has.

## Decision

### Short results, full output on request (`joinery.compact`)

- **Summaries in the gateway.** The gateway answers the agent-facing methods
  that can be long (`call`, `inventory`, `submit`, `precheck`, `delegate`,
  `reproduce`, `blockout`, `playtest`, `access_check`, `perf_check`,
  `sound_audit`, `task_status`) as they are when their JSON is at most 6,000
  characters (about 1.5k tokens). A longer one becomes a summary: lists keep
  their first items, texts their first and last lines, each long line its first
  400 characters, and every cut says what it left out ("… 1480 more lines: ask
  for details"). Passes keep less until it fits. Scalars (status,
  attempts_left, counts, paths) are never cut, so the summary answers the same
  yes/no questions. A JSON document carried as a string is cut as JSON. Images
  and other media parts are kept and not counted.
- **The full result on request.** The full result is kept under
  `.joinery/results/<ref>.json` (the newest 200), and the summary carries
  `details: {ref, left_out, how}`. A new read-only tool,
  `details(ref, path, offset)`, pages through it (150 lines or 12k characters a
  page) or reads one part (`problems/12`, `content/0/text`). Every role is
  offered it (`REGISTRY_READS`). `call_tool` also adds the note as a text part,
  since an MCP client may show only the content.
- **The scene as an outline.** In the prompt's editor context and in
  `inspect_editor`, the node list is one line per node: path, type, position
  (3D), script and the scene it comes from, without properties; 80 nodes in the
  prompt, 120 from `inspect_editor`, in the addon's breadth-first order, with a
  note of how many more there are and how ids are formed
  (`<scene>::<path>`). `inspect_editor(node=…)` (an id, path or name) gives a
  node's full row, properties included. The outline is deterministic, so the
  prefix stays one spelling.
- **Patches, not whole files.** Where Joinery controls the write, a Godot
  `write_file` may carry `patch: [{find, replace}, …]` instead of `content`
  (`joinery.text_patch`). The gateway applies the pairs to the staged file
  (each `find` must match exactly once at its step), judges the result with
  the same denylist as any argument, and sends it on as `content`, so the
  Godot MCP and the review see an ordinary write. The Godot tool module notes
  this on `write_file`'s description. Joinery's typed ops (`edit_scene`,
  Blender/Krita/sound ops) were already edits. Joinery's own check fixes
  already fold their edits into one write.
- **The instructions** say once that long results arrive as summaries, that
  `find_recipe` exists (ADR 0064) and that patches are preferred.

### The project map (`joinery.project_map`, `joinery map --project P`)

A short ranked map of the project, like Aider's repository map: the game
(name, blueprint, features, main scene), autoloads, input actions, scenes
(root, type, node count, script, children, instanced scenes, users), scripts
(class, base, signals, exported variables, public functions, users),
resources, assets counted by folder and type, recipes and their outputs, and a
count of test scripts.

- **Ranking.** PageRank over references: `ext_resource` lines of scenes and
  resources, `res://` strings and used `class_name`s in scripts. The walk
  restarts at the main scene, the autoloads and `[game]` hooks ten times as
  often as anywhere else. Sections list their best-ranked entries first.
- **Budget.** At most 7,000 characters (about 1.75k tokens). Over budget, the
  lowest-ranked entry of the section that takes the most room goes first, and
  each section says how many it left out. The 2D platformer blueprint maps to
  about 1.1k tokens.
- **Cached by content.** The key hashes the content of every file the map
  reads (scenes, resources, scripts, `project.godot`, `joinery.toml`) and the
  path and size of every other file; Joinery's state, `.godot`, `addons` and
  hidden folders are left out. The text is kept in `.joinery/map/<key>.txt`
  (the newest three). In-process, a stat-only key skips the hashing while no
  file's size or time changed.
- **In the stable prefix.** `bridge.provider_process.project_map` puts the map
  ahead of the editor context, in the part ADR 0056 calls the project map, so
  all four providers get it in the same place. It enriches that part; the
  editor context stays after it, as an outline. A folder that isn't a Godot
  project (and every existing test prompt) gets no map, and the bytes are as
  before.

### What each provider allows for stale tool output

| Provider | Clearing old tool results | What Joinery does |
|---|---|---|
| Claude Code | The Messages API has context editing that clears old tool results (a beta `clear_tool_uses` strategy), but the CLI owns its requests; Joinery can't turn it on. The CLI compacts long conversations on its own. | Short results (this ADR). |
| Codex | The app server compacts the thread itself; no per-result clearing exposed. | Same. |
| Gemini CLI | Compresses chat history past a threshold; not exposed over ACP. | Same. |
| opencode | Prunes old tool outputs when it compacts (assumed from its docs; not installed here). | Same. |

None of these is something Joinery can set per call, so the answer stays
agent-agnostic: results are short when they enter the conversation.

## Effect

Offline, with fake agents, in the stable-prefix test style (four characters a
token; a 60-node scene with the addon's 32 properties per node;
`tests/test_small_context.py` checks the scene part):

| Part | Before | After |
|---|---|---|
| Editor context in each turn, 20 / 60 / 200 nodes | 4.6k / 13.9k / 46.3k | 0.3k / 0.7k / 1.0k |
| Project map in each turn (platformer blueprint) | — | 1.1k |
| `inspect_editor`, 60 nodes | 13.9k | 0.75k |
| A 1,500-line Godot run log | 20.5k | 0.6k |
| `precheck` with 40 problems | 1.7k | 0.9k |
| Schemas listed to the lead / a reviewer | — | +0.46k / +0.19k (`details`, `find_recipe`, `inspect_editor(node)`) |

A simulated Claude lead, five turns (prefix cache as in ADR 0056: the
conversation grows by appending, so each request's new bytes are uncached; one
`inspect_editor`, one `precheck` and one run log in each of the first two
turns, a `precheck` in the others):

| Scene | Input before → after | Uncached before → after |
|---|---|---|
| 20 nodes | ~820k → ~228k (−72%) | ~92k → ~24k (−74%) |
| 60 nodes | ~1.36M → ~255k (−81%) | ~157k → ~27k (−83%) |
| 200 nodes | ~3.24M → ~281k (−91%) | ~384k → ~30k (−92%) |

These are estimates from Joinery's own texts, not provider numbers; `joinery
metrics` (ADR 0054) records each turn's reported tokens, and the next real
runs show the measured effect. A turn on a tiny scene can cost slightly more
than before: the map's 1.1k tokens outweigh what the outline saves.

## Consequences

- An agent sees properties only for the nodes it asks about. The selection
  (the turn's targets) is unchanged.
- The map goes with every turn, like the editor context. In a long session that
  is about 1.1k uncached tokens a turn; sending it only when it changed would
  need per-session state in each provider adapter and is left for later.
- A new long-answer RPC method belongs in `COMPACTED`; a method whose answer
  must stay whole (a schema, a report) does not.
- `.joinery/results/` and `.joinery/map/` are Joinery's state, git-ignored with
  the rest of `.joinery/`.
