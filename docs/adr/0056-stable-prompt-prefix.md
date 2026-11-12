# Stable prompt prefix and per-role tool lists

Accepted: 2026-11-12.

## Context

Every provider Joinery runs (Claude Code, Codex, Gemini CLI, opencode) caches
the start of a request: a later request that begins with the same bytes reads
that part from the cache, cheaper and faster. A cache hit needs a
byte-identical prefix, so anything that changes from turn to turn near the
front of a prompt costs the whole rest of it.

Joinery's prompts didn't keep to that:

- Claude Code got Joinery's instructions (about 2.6k tokens) as its system
  prompt *and again* at the top of every turn's text, so each turn paid for
  them anew, uncached, and the conversation grew by that much every turn.
  Gemini CLI and opencode got them in every turn as well.
- The editor context went in as Godot sent it. Its `scene_revision` counter
  (a new number every turn) and `selection_instances` (live instance ids) sit
  near its start, so two turns on the same scene diverged a few bytes into it.
- The roles' prompts put the run's values first and the fixed text after: the
  playtester's minutes, a check's role, a variant's direction, the reviewer's
  diff ahead of the art bible and the game brief.
- Every agent was listed every joinery-registry tool, about 7.4k tokens of
  schemas, including the reviewer, who may call six of the twenty.

## Decision

**One layout for every provider** (`bridge.provider_process.turn_text`):

1. Joinery's fixed instructions: Claude Code's `--append-system-prompt`,
   Codex's developer instructions, the first prompt of an ACP session.
2. The tool schemas (the provider puts them where its API does).
3. The role's rules: a `RolePrompt.rules` (`bridge.instructions`), with the art
   bible and game brief where the role reads them (the reviewer). They reach
   the provider as `Prompt.rules`, the same bytes on every run of the role.
4. The project map: the editor context without its per-turn keys
   (`TURN_KEYS`), keys sorted, one JSON spelling.
5. The turn: the summary a fresh session starts from, the per-turn editor
   keys, the selection, the request and its attachments.

Texts in `bridge.instructions` hold nothing that changes between turns (no
time, counter or id). A `RolePrompt` has two parts, `rules` and `turn`. Its
`rules` may only name what is the same on every run of the role (the role's
name and tool, the bible, the brief). Ways started together put the shared
request before their own direction, so they share all but their last lines.

**Instructions once.** Claude Code's turns no longer repeat the instructions
(they are its system prompt). An ACP session (Gemini CLI, opencode) gets them
with its first prompt, and again when the agent's reported context shrinks
(it compacted its history). An agent that never reports its context gets them
every turn as before, since Joinery can't tell whether they are still there.

**Per-role tool lists.** `joinery.roles.offered_tools(role)` lists, for each
role, the joinery-registry tools it may call (`registry_allowed`) and, for
the read-only roles, `call_tool`, which the gateway holds to audited reads.
Every provider starts the MCP proxy with `--role` (`proxy_config`,
`registry_config` for Codex), and the proxy lists only those tools. A role's
report tool is listed to that role only, so the lead stops carrying the
report schemas. Codex gets approval lines only for the tools it is offered.
An inspection specialist (no role) keeps the full list. For Claude Code, a
read-only role is also offered only `Read, Grep, Glob, Bash` of its built-in
tools (`--tools`). Listing is not the gate. The policy hook and the gateway
still judge every call by role (ADR 0037), so a tool missing from a list is
never what keeps a role from writing.

## What each provider allows

| Provider | Cache | What Joinery does | Verified / assumed |
|---|---|---|---|
| Claude Code | Prefix cache on tools → system → messages; the CLI sets the cache breakpoints itself. Joinery has no per-request cache control and brokers no credentials. | Stable layout. Instructions only in the system prompt. `--exclude-dynamic-system-prompt-sections` when the CLI has it: cwd, environment and repository state move from the system prompt into the first user message, so a role's fresh session shares the system prompt with the last one. `--tools` for read-only roles. Both are probed from `claude --help` (as `--effort` is) and left out on a CLI without them. | Flags verified in `claude --help` of 2.1.321, including the `--system-prompt-snapshot` default (on: the system prompt is recorded once per conversation and resent verbatim on resume). That the cache then hits is assumed from the CLI's own description; no paid run was made. |
| Codex | OpenAI caches the longest matching prefix automatically, from about 1k tokens; no switch to set. | Stable developer instructions (no per-turn content; the read-only tail is last), stable turn layout, role tool list, approval lines only for offered tools. | Automatic prefix caching is assumed from OpenAI's documentation. Whether the app server adds its own per-thread keys is not visible to Joinery. |
| Gemini CLI | Implicit context caching on Gemini 2.5+ models, prefix based. | Instructions once per ACP session (again after the agent compacts), stable turn layout, role tool list through the proxy. | Assumed. Gemini CLI isn't installed here. ACP v1 has no system-prompt field, so the instructions ride on a prompt. Whether Gemini CLI reports `usage_update` over ACP is unverified; without it the instructions go every turn as before. |
| opencode | Depends on the model provider behind it (Anthropic-style or automatic). | Same as Gemini CLI. | Assumed. Not installed here. |

## Effect

Schema tokens listed per agent (`len(json)/4`, from the proxy's own
`list_tools`): every tool ~7,400; lead ~6,100; worker ~5,600; reviewer ~840;
debugger ~930; playtester ~800; accessibility ~750; performance ~1,000;
designer ~1,150; sound-designer ~730. A read-only role carries 85–90% fewer
schema tokens.

An offline simulation of the prefix cache (shared prefix with the previous
request read from cache, the rest uncached), using Joinery's own texts, a 60-node
scene and a 6k/4k-character bible/brief:

| Case | Input before → after | Uncached before → after |
|---|---|---|
| Claude lead, 5 turns | ~115k → ~70k tokens (−39%) | ~31.5k → ~17.4k (−45%) |
| Reviewer, second run on the same project | ~18.2k → ~9.0k (−50%) | ~5.2k → ~0.8k (−84%) |

The remaining uncached lead input is mostly the scene's node list, which
changes when the scene does. These are estimates, not provider numbers.
`joinery metrics` records each turn's reported `cache_read` tokens
(ADR 0054), and the next real runs show the measured effect.

## Consequences

- A new joinery-registry tool must be named in `joinery.roles.REGISTRY_TOOLS`,
  or no role is offered it (a test fails until it is).
- A new role prompt is a `RolePrompt`. Values of the run go in `turn`, never
  in `rules`. Tests check that two runs' rules are byte-identical.
- Prompts are laid out once (`turn_text`). A provider adapter must not add
  per-turn text ahead of it.
