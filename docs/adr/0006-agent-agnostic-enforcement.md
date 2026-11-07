# 0006: Agent-agnostic creation-only enforcement

Status: accepted, 2026-11-07

## Context
Rule Zero (creation, not generation) was enforced only through a Claude Code
`PreToolUse` hook. Users want to run other agents — Codex, Gemini CLI,
opencode — with their own subscriptions or API keys. An agent Joinery does not
wire would bypass the gate entirely, which is the worst failure: it looks
supported but enforces nothing.

The agents agree on MCP (one server works in all of them) but differ on hooks:
config location and format, the name of the pre-tool event, how MCP tool names
are spelled, and how the hook learns the project directory. Codex and opencode
also have documented gaps where some tool calls do not reach a hook.

## Decision
One policy brain, four mouths. The violation logic stays in `joinery/hook.py`,
`policy.py` and `catalog.py`, all agent-neutral. A normalisation layer
(`hook.normalise_event`) folds each agent's payload into one canonical shape, so
the single `joinery hook` command works for every agent:

| Agent | Config | Pre-tool event | MCP naming | Project dir | Shell tool |
|---|---|---|---|---|---|
| Claude Code | `.claude/settings.json` | `PreToolUse` | `mcp__s__t` | `CLAUDE_PROJECT_DIR` | `Bash` |
| Codex CLI | `.codex/hooks.json` | `PreToolUse` | `mcp__s__t` | session cwd → git root | `Bash` |
| Gemini CLI | `.gemini/settings.json` | `BeforeTool` | `mcp_s_t` | stdin `cwd` | `run_shell_command` |
| opencode | `.opencode/plugin/joinery-gate.js` | `tool.execute.before` | server field | plugin payload | `bash` |

`joinery/agents.py` is the single registry of these facts. `joinery/wiring.py`
generates each agent's config; `joinery new` wires all of them into every
project, and the engine repo carries them too. The hook resolves the project
directory itself (agent env var → stdin cwd → git root → repo), so it does not
depend on any one agent's variable.

**Fail-closed.** An agent without a reliable blocking hook is never wired
silently; `wiring.unwireable()` lists it and the bridge refuses to run it. All
four agents in scope currently qualify.

**Authentication** stays each vendor's concern. `joinery/auth.py` and
`joinery auth` document the subscription and API-key path for each agent so a
user can run or switch any of them; Joinery never brokers credentials.

Repo config is portable: Claude uses `${CLAUDE_PROJECT_DIR}`, the others drop
`--project` and let `uv` discover the env from the working directory, so no
machine-specific path is committed. Scaffolded game projects pin the concrete
engine install, since they live elsewhere and must still find `joinery`.

## Limits
No tool is disabled or removed in any agent's config. Every tool stays callable
and is judged per call, with two outcomes:

- **stop** — a Rule Zero violation (anything on `policy/denylist.toml`: a
  generative service, a generative package, an asset site, or an MCP server
  named after one). Where the agent supports it (`stop_mode == "json"`: Claude
  Code, Gemini CLI) the hook exits 0 with `continue: false`, halting the turn so
  the user is told, instead of the agent quietly probing another route. The same
  payload also carries `decision: deny` / `permissionDecision: deny`, so an
  agent that ignores `continue` still refuses the call: it cannot fail open.
  Where there is no verified JSON stop path (Codex, opencode) the hook exits 2,
  which reliably refuses the call, and names Rule Zero in stderr.
- **deny** — not available here, a configuration state rather than a violation:
  an MCP server the project did not enable, or a narrow escape-hatch tool on an
  otherwise fine server. Exit 2 refuses that one call and the turn continues.

Each agent's config passes `--agent <id>` so the hook knows which signalling to
use. Without the flag it falls back to exit 2, which refuses the call.

Remaining honest residue, surfaced in `joinery agents`:
- Codex: hosted WebSearch does not reach a hook, and stays allowed — it reads
  the web, it does not create assets. A hook timeout (not an error) could let a
  call run; the hook is pure-Python, fast, and fails closed on any exception.
- opencode: `tool.execute.before` does not intercept subagent tool calls
  (issue #5894). Nothing is disabled to compensate.

The real guarantee is unchanged and agent-independent: offline sandbox workers,
plus CI (`joinery check deps` and `joinery check assets`). Those hold no matter
which agent ran, or whether its hook fired. See ADR 0002.
