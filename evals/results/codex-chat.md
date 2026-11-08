# Codex sidebar chat

2026-11-08. The narrow initial sidebar and disconnected free-form prompts were
reproduced: startup auth existed, but no transport was wired to prompt_submitted.

- 285 Python tests pass, including seven new deterministic fake-app-server tests.
  Covers persistent thread reuse, selected-target payload, UTF-8 deltas, completed
  message fallback, missing policy, unreviewed hooks, sign-out, cancellation,
  disconnection, secret redaction, malformed input, and CLI stdin/EOF cleanup.
- Ruff, strict mypy (35 files), and `joinery check all` pass.
- GDScript lint passes. Stock Godot runtime checks pass for chat UI, native pipe
  lifecycle (reload/cancel/large payload), and framed chat transport with split
  UTF-8 input, queued targets, streaming, and useful failure/reconnect state.
- Native full-editor layout check passes, including the font-scaled width,
  actual prompt wiring, and automatic Codex account connection. Captured UI shows
  the wider sidebar with “Codex connected”. Existing shell/preview checks pass.
- A live Codex prompt returned JOINERY_CONNECTION_OK. A second live shell probe
  returned the project cwd and completed without changing project content. The
  final probe observed `hook/started` and `hook/completed` from the actual shared
  gate. Codex’s automation trust-bypass flag did not activate hooks in this
  app-server flow; it was removed in favor of registering and rechecking only
  the known gate’s exact hash.

During verification, hooks/list exposed an empty project hook inventory in an
untrusted game config layer. Session wiring and vetted hook review were added;
unknown untrusted hooks now fail closed. Python-only validation would have missed
both that and the earlier missing editor subscription.

Scope: connected chat and project inspection; model-driven edit transactions and
other providers' chat transports remain unimplemented. No game assets changed.
