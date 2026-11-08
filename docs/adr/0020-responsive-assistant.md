# Responsive assistant configuration and native worker reuse

Accepted: 2026-11-08.

The sidebar previously used the provider's default model for every request,
showed connection and work in one status line, and had no per-turn latency
breakdown. Native document operations started a new container each time.

Use an agent-neutral model/effort catalog and per-turn selection. Auto chooses
only from reported models; explicit choices are validated, and unsupported
providers retain their configured model. Codex uses model discovery and per-turn
overrides, ACP uses model/thought configuration options with a legacy fallback,
and Claude's print transport retains its configured model. Endpoint API profiles
remain bound to their verified model. No global provider settings are rewritten.

Expose bounded read-only specialist inspection through the project MCP proxy.
Child sessions retain the shared hook and use an endpoint that rejects changes
independently of the model's instructions. Only the parent owns review and
mutations. Keep the existing mutation lock: parallel authoring of one native
document is not introduced. Cancellation closes child providers and endpoints
before restoring pending changes.

Measure monotonic durations locally and keep request/response bodies out of the
metrics. Phase spans are explicitly inclusive where nested. A small project-local
history and native timing controls make regressions visible without adding logs
to chat. Native controls and shared theme tokens provide model selectors,
progress/cancellation and wider padded chat; review remains a separate panel.

Reuse scoped offline containers, keyed by project, image and mount mode, while
starting a new native application per command. Preserve label-based ownership,
process-group cleanup, network isolation, source replay and transaction staging.
Unscoped runners remain one-shot. Cache successful discovery briefly and bound
concurrent startup. Never cache a readiness failure as a healthy connection.

Validation covers account-reported Codex discovery without inference, provider
protocol fixtures, read-only gateway denial, native worker cancellation and all
five creative lifecycles. A Python execution wrapper initially regressed startup
latency under emulation; a lightweight process-group wrapper measured better.
The exact measurements and native UI checks are in
[responsive-assistant.md](../../evals/results/responsive-assistant.md).
