# Responsive assistant and native UI

Local execution: 2026-11-08 on macOS ARM64 / M2. Creative applications run in
offline Linux AMD64 workers under emulation. No hosted CI run or remote push is
claimed for these changes.

Implemented Auto/Fast/Thorough routing, provider-reported model/effort controls,
project-local preferences, bounded read-only specialist sessions, scoped native
container reuse, parallel/cached discovery, local phase metrics and native UI
progress/cancellation. The UI has a wider padded chat surface, actual model/effort
status, light/dark styling, larger review images and scrollable themed setup.

## Measured results

- Codex connected through the actual CLI and returned **8 visible account models**
  and their reasoning levels. No inference request was made. Custom API profiles
  are tested to retain their verified model rather than use this subscription list.
- The Python suite reports **790 passed, 4 skipped**;
  37 cases specifically cover routing, provider config, specialist access/lifecycle,
  local timing privacy/bounds and worker identity/cancellation. The existing four
  platform-dependent skips remain unchanged.
- Ruff, strict mypy (including Windows platform stubs), shared policy checks and
  workflow validation pass locally.
- All five native workflows pass through reused containers: Blender named-part
  editing/GLB, Krita layers/exact pixels, SuperCollider audible NRT, Inkscape SVG/PNG
  and Material Maker graph/texture/roughness. Each verifies source preservation,
  clean replay, rejection, acceptance and revert.
- A real native cancellation fixture starts an owned application and descendant,
  cancels the request and checks that its container is gone. It passes.
- Native Godot widget tests pass, including model/effort, progress, unavailable
  preferences, message bubbles, assistant copy controls, multiline/Shift+Enter,
  safe text formatting and scroll retention during streaming. Eight graphical
  captures cover dark/light themes, active/completed states and 480px/1000px
  widths. The completed sidebar was visually checked against the VS Code reference.
- Isolated project-manager connection/API/project setup passes with five captures.
- The full graphical editor scene preview/review/undo tests pass for both an
  existing scene and an initially empty editor. The first run caught a Window vs
  Control theme parameter mismatch; the theme now handles both native owners.
  Direct SceneTree termination in the fixture intermittently raced outstanding
  asset-preview viewport cleanup. The fixture now clears its owned preview and
  drains frames before quitting. Normal deferred plugin cleanup is retained;
  repeated existing/empty editor runs contain no error messages.

Blender factory-startup/inspection benchmark, three samples per mode:

| Execution | Samples (seconds) | Median |
| --- | --- | --- |
| Fresh container | 2.632, 2.660, 2.582 | 2.632 s |
| Reused scoped container | 2.498, 2.382, 2.382 | 2.382 s |

This is about **9.5% lower median native startup/inspection time** in this local
sample. It is not an end-to-end model response measurement or a guarantee on
other machines. The initial Python wrapper measured 2.864s reused vs 2.662s fresh
and was replaced before completion. Application startup still accounts for much
of the remaining time; reasoning, network, exports and imports have separate costs.

## Evidence

Follow-up chat polish: selected resources now use a compact outlined badge with
a smaller remove action. Composer dropdowns have matching rounded surfaces and
height, and Send has a blue fill with contrasting text/icon and keyboard focus.
Conversation content has consistent side gutters and top spacing. Native widget
and graphical checks pass, including an actual mouse click that submits Send
exactly once with the selected resource. Eight renewed captures cover both
themes, both widths and working/completed states. Logs:
`/tmp/joinery-chat-polish-widgets.log`, `/tmp/joinery-chat-polish-ui.log`.

- `/tmp/joinery-responsive-live-models.log`: actual non-inference model discovery.
- `/tmp/joinery-responsive-new-tests.log`: focused regression cases.
- `/tmp/joinery-responsive-final-unit.log`: full Python suite.
- `out/assistant-latency.json` and `/tmp/joinery-responsive-all-native.log`: measured
  worker timings, all native lifecycles and cancellation proof.
- `/tmp/joinery-vscode-widgets-final.log`: native widget execution.
- `out/assistant-captures/`: four UI framebuffers.
- `/tmp/joinery-responsive-startup.log`: isolated startup/project setup.
- `/tmp/joinery-vscode-editor-final.log`: corrected existing/empty editor flow.
- `/tmp/joinery-responsive-live-specialist.log`: actual child-session handshake,
  including verification that native nested agents are disabled; no inference.
- `/tmp/joinery-vscode-ui-final.log`: graphical input/layout/formatting/scroll checks.

No game project, model subscription configuration or art approval was changed.
The existing tool readiness failures recorded in [all-mcp-programs.md](all-mcp-programs.md)
are separate: this work does not turn an upstream SuperCollider boot timeout or
Audacity script-pipe failure into a passing native integration. Claude's print
transport still lacks a model catalog, and unsupported model/effort controls are
not fabricated. See [assistant-performance.md](../../docs/assistant-performance.md)
for behavior, boundaries and reproduction.
