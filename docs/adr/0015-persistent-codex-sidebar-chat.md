# Persistent Codex sidebar chat

The startup screen could detect Codex sign-in, but ordinary sidebar prompts had
no transport subscriber. Authentication alone did not establish a conversation.

Use the locally installed Codex `app-server` over JSON-RPC stdio. The sidebar
starts the selected agent/profile automatically, streams assistant deltas, and
preserves a single thread across prompts. Each prompt carries an independent
snapshot of the engine selection. Cancellation interrupts the active turn;
closing the addon closes pipes so the Python host terminates its owned process
group. Native nonblocking pipes keep all GDScript editor work on the main thread.
No credential contents appear in arguments or UI events. Private API profiles
must have passed startup verification before use.

Codex project-local hook layers may be untrusted and omitted. Validate project
wiring and supply the exact shared gate as session configuration. Inspect all
enabled hooks before any model turn; only the known gate and already trusted
hooks qualify. Register only the shared gate’s exact hash through Codex’s configuration API,
as reviewing that known hook in `/hooks` would do. Other hook trust is untouched;
no bypass flag is used. Restart and verify the trusted hash before accepting a
prompt. Reject unknown unreviewed sources or changed inventories.

The conversation uses the read-only filesystem sandbox. Chat and inspection are
implemented; editor/file changes require the future transaction workflow. Other
agents retain their existing authentication support but report unimplemented
chat transports honestly. The wheel now includes the bridge package so launch
from a separate game project resolves correctly.

Give the sidebar a font-scaled minimum width of 24 characters, including when a
saved layout previously made it narrow. Keep its earlier Journeyman layout and
show connection status plus a recovery action for failed connections.
