# One readiness source

Whether an app can do its work shows up in many places: the new-project
wizard, Project apps, the app rail, the asset workspace, `/tools`, `joinery
doctor`, `joinery status` and every agent's `discover_tools`. Each used to
compute it on its own (the setup menu asked Docker itself, the workspace asked
nothing), so they could disagree, and a screen that polled every two seconds
could start Docker commands every two seconds.

## Decision

`joinery/readiness.py` is the only code that decides readiness. Every surface
reads it:

| Surface | How it reads |
|---|---|
| `joinery status [--json]` | `readiness.main` |
| `joinery tools --json` (wizard, setup menu) | `installer.tool_menu` → `readiness.current(catalog=True)` |
| Project apps dialog | `joinery status --catalog --background --json`, as a job |
| App rail and its card | `joinery status --catalog --background --json`, as a job |
| Asset workspace (`joinery workspace`) | `readiness.cached_tools()` only |
| `joinery doctor` | `readiness.status`, then the checks the cache lacks |
| MCP inspection (`joinery mcp status --probe`) | the tool modules' `McpProbe` checks, judged by `readiness.judge_probe` |

Tool modules (`joinery/tools/*`) declare their checks as data; readiness runs
them. Nothing else decides a state.

## States

Inside, a tool's state is the worst of its required checks: `ready`,
`downloading`, `needs_image`, `needs_licence`, `broken`, `needs_docker`.
Features (MPFB2, Surge XT's MIDI music and audio edits) carry their own state
and never change the tool's.

Screens show five states (`SURFACE`), always with the same words
(`STATUS_LABELS`, mirrored by `ui_components.READINESS` in the addon):

| Internal | Shown | Words | Dot |
|---|---|---|---|
| `ready` | `ready` | Ready | green |
| `downloading` | `preparing` | Downloading… | accent |
| `needs_docker` | `needs_docker` | Needs Docker | red |
| `needs_licence` | `needs_licence` | Needs your licence | red |
| `needs_image`, `broken` | `unavailable` | Not available yet | ring |

Readiness never blocks choosing an app: only the catalog can refuse one (no
reviewed connector), and a paid app needs a licence. Each entry carries:

- `status`, `status_label`;
- `summary`, one plain sentence, and `status_reason` (the summary when the
  tool is not ready, else empty);
- `action`, at most one: `{id, label}` with id `start_docker` ("Start
  Docker"), `rebuild` ("Rebuild"), `check_again` ("Check again") or `get`
  ("Get it"), or null;
- `checked_at`, so a stalled check never looks current ("Last checked 14:02");
- `checking`, true while a check has no answer yet.

`joinery status --agents` adds each agent's connection summary and its
`verified` flag, so one report can feed both the setup step and `/tools`.

## Cache rules

- Tier 0 (Docker, images, setup progress, installed pins) is asked live.
- Tiers 1 to 3 (commands in the offline worker, MCP probes) are cached in
  `~/.cache/joinery/readiness.json`, keyed by the image id (and the installed
  source tree for MCP probes). A rebuilt image or reinstalled server is checked
  again. MCP answers expire after a day; failed MCP calls and timeouts are never
  cached.
- Every report is kept in `status.json`. `current()` answers from it for 30
  seconds without starting any process; past that, it asks the cheap checks
  again. It never answers from the snapshot while setup is building.
- A cache miss is reported as `checking`; `--background` then starts one
  detached `--fill` run, which runs only the missing checks. A lock keeps it to
  one at a time. `--refresh` (Check again) runs every check of a tool now.
- The workspace reads `cached_tools()` only: no Docker call on its two-second
  refresh. An unknown tool simply shows no dot.
- Setup (`joinery bootstrap`) writes `setup.json` (`phase`, `image`,
  `started`, `pid`) while it builds, so every surface says "Downloading…"
  instead of "Not available yet". It labels each image
  `org.joinery.sandbox-rev` with a hash of `sandbox/`, sets
  `JOINERY_SANDBOX_REV`, and starts a background refresh when it is done. An
  image whose label differs is out of date ("Rebuild"); an image without the
  label counts as current. Setup never removes images or build cache.

## UI rules

- Every read runs off the UI thread, through `command_job.gd`.
- Project apps re-asks every two seconds while anything is `checking` or
  `preparing`. Start Docker shows how to start it, with Check again beside it;
  Rebuild runs `joinery bootstrap --project` as a job; Check again runs
  `joinery status --tool <id> --refresh`.
- The rail shows a mark only when an app needs something. Hover or focus shows
  a card with the sentence, the action, Check again and the last-checked time.
  When an app turns Ready its glyph pulses once and the mark fades.

## Consequences

One fake host in `tests/test_readiness.py` drives the setup menu, the
workspace and doctor, and the test fails if any of them shows something else.
`installer.DockerProbe` and `installer.readiness` are gone. A new surface reads
`joinery status` or `readiness.current()`; it does not ask Docker itself.
