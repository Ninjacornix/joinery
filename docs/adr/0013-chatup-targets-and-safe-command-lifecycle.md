# 0013: Chatup targets and safe command lifecycle

Status: accepted, 2026-11-07

## Decision

The user preferred the earlier Journeyman sidebar layout after trying a compact
Chatup treatment. Restore the earlier name, heading, spacing and inset transcript.
Retain command completion and explicit targets. Diagnostics stay in Output.
Slash queries produce concise chat replies instead of raw CLI output. The agent
menu reads registry-owned names through `joinery agents --json`.

Typing `/` opens an upward command menu that filters with the input and supports
clicks, arrow keys, Tab, Enter and Escape. Tab and clicks complete; Enter posts
the typed message in chat. Unique command prefixes resolve on submit, and
`/help` replies in the conversation. The docking tab bar is hidden, while
selection context uses a compact muted line above the input with a clear action.
Editor object selections use scene-relative node
paths; filesystem, inspector and asset-viewer selections use resource identities.
Audio nodes include their stream resource. Submitted prompts carry an independent
snapshot of these targets. Removing a chip is explicit, and automatic preview
refresh does not repopulate it. No edit is pretended: the agent bridge is still
unconnected.

Render-only native sources may preview an existing image from the same recipe.
The selected source identity and its Open action remain unchanged. Model views
use Godot's empty-editor procedural sky and sun defaults, independently of game
environments, following the user's revised background preference.

Model interaction uses wheel and trackpad zoom without buttons or instruction
tooltips. Audio previews add a waveform decoded in bounded main-thread chunks,
click/drag seeking, transport controls, full-stream looping, volume, mute and
speed (with pitch changes). Preview streams are duplicated before disabling
imported loop flags; no resource settings or source bytes are changed.

The fork opens bottom utility docks to at least 45% of the central split area.
Larger saved offsets remain respected, and users can drag the divider while open.
This is a Joinery-only shell preference, exported as patch 6.

## Crash and worker lifecycle

The reported macOS crash occurred on the workspace's GDScript `_execute` thread.
The editor log recorded tool-script parse/reload errors and a bad address index
before the crash. This is consistent with a worker retaining script state while
tool scripts reload; the report alone does not establish every native frame's
cause.

Remove that execution path. Workspace and chat commands now share native
nonblocking subprocess pipes, polled on the main thread. There is no GDScript
worker executing while an owner reloads or closes. Stdout and stderr stay
separate, UTF-8 is decoded after complete collection, and teardown cancels the
owned child and closes its pipes.

## Validation

The command lifecycle test reloads the command script while a subprocess runs,
checks large UTF-8 output and separate stderr, verifies cancellation, and reloads
then closes a workspace with its CLI in flight. Chat tests cover menu placement,
completion, selection snapshots, conversational agent results and log isolation.
Full-editor tests verify engine object/audio selection, native sky preview,
selection chips and rendered command-menu placement.
Playback tests cover waveform decoding, seeking while stopped and paused,
keyboard play, loop completion, end positions, gain, speed and source immutability.
