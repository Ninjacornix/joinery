# Change metrics

Accepted: 2026-11-12.

## Context

Making the harness cheaper and faster (prompt order, batched ops, model
routing, fewer send-backs) can only be judged with numbers taken the same way
before and after. Joinery already kept a per-turn latency line
(`.joinery/latency.jsonl`, `joinery.timing`) with a few phase totals, and the
sidebar's usage meter showed each provider's session token totals, but nothing
said what one change cost: how long each agent turn, tool call, check, review
render and reviewer pass took, how many tokens each agent turn used, or how
many rounds it took to reach review.

## Decision

Every user request records its spans in one plain JSONL file under
`<project>/.joinery/metrics/`, named by the transaction it put in review (or
by the turn's id when nothing was staged). `joinery metrics --project P
[--last N] [--json]` prints the breakdown. The shared turn runner and gateway
record it, so it is the same for Claude Code, Codex, Gemini CLI and opencode,
for the chat and for parallel tasks.

- **OpenTelemetry names, local file.** Each line is a span (`trace_id`,
  `span_id`, `parent_span_id`, `name`, `start_time_unix_nano`,
  `end_time_unix_nano`, `attributes`, `status`), with attributes from the
  OpenTelemetry GenAI semantic conventions: `gen_ai.operation.name`
  (`invoke_agent`, `execute_tool`), `gen_ai.provider.name`,
  `gen_ai.agent.name`, `gen_ai.request.model`, `gen_ai.response.model`,
  `gen_ai.tool.name`, `gen_ai.usage.input_tokens` (including cached, as the
  conventions define it), `gen_ai.usage.output_tokens`,
  `gen_ai.usage.cache_read.input_tokens`, `error.type`. Joinery's own facts use
  `joinery.*` (`joinery.span.kind`, `joinery.round`, `joinery.transaction.id`,
  …). Attributes are a flat object, not OTLP key/value lists. There is no
  exporter, no network and no new dependency.
- **Spans.** One root `change` span per request (outcome, transaction,
  agent, submits, reviewer send-backs, continuations) and, under it:
  `agent_turn` for each provider prompt (the request and each continuation,
  plus each reviewer or other read-only role turn), `tool` for each gateway
  tool call, `worker_start` for starting a tool's MCP worker, `checks` for the
  submit checks, `reviewer` for the reviewer pass, `review_render` for the
  before/after renders (appended when they finish after the turn), and
  `send_back` from a change being sent back (by the checks or the reviewer) to
  its next submit. The user's Accept or Reject is appended as a `decision`
  line. Spans nest, so times by kind overlap and the report says so.
- **Tokens from what the agent already reports.** Providers report session
  totals; a turn's tokens are the difference from the totals before it (a
  total that went down means a new session, whose totals are all this turn's).
  A role agent runs in its own session, so its totals are its turn's. An agent
  that reports nothing gets `joinery.usage.reported = false` and the report
  says "not reported", never zero.
- **Worker start vs work.** The gateway times starting a tool's worker
  (`worker_start`) apart from the call. A worker that knows its own split can
  put `joinery/worker_start_ms` and `joinery/work_ms` in its tool result's
  `_meta`; the call's span carries them as `joinery.worker.*`. No first-party
  worker reports them yet, so a warm worker's container start inside a call is
  still part of that call.
- **Never content.** Fixed labels, durations and counts only: no prompt,
  reply, tool argument or payload. Files stay in `.joinery/` (git-ignored);
  the newest 500 are kept. Measuring never fails the change it measures.
- **Benchmark.** `evals/benchmark.toml` holds ten typical requests on the
  testbed (model edit, new prop, texture recolour, sound, animation tweak,
  script fix, blockout, kit batch, scene layout, UI text), with seed files for
  the ones the testbed lacks. `evals/run.py --suite benchmark --live --agent A
  --model M` runs each on a fresh copy through the real chat host
  (`joinery.benchmark.LiveDriver`, accepting the change it opens) and reports
  median time, tokens and rounds per case from the same metrics files.
  Without `--live` it only lists the cases. Tests drive it with fake agents.
- **History.** `joinery transactions list` gives each change a `cost`
  (total time, tokens, agent turns) when it was measured, for the editor to
  show later. No editor UI shows it yet.

## Consequences

- Any harness change can be compared on the same ten requests, and any real
  session can be read back change by change.
- Token numbers are only as good as each provider's report: Claude Code's
  stream-json `result` usage and Codex's `thread/tokenUsage/updated` totals are
  read today; Gemini CLI and opencode report over ACP only if they send a
  prompt response `usage` or a `usage_update`, otherwise their changes show
  time and rounds without tokens. Resuming a provider thread whose totals
  predate this chat counts them once in the first turn after.
- `.joinery/latency.jsonl` stays as it is; the metrics files are the detailed
  record.
