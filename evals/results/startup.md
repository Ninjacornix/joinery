# Agent connection and project startup

2026-11-08.

- The rebuilt Project Manager opens at agent connection. Existing Claude and
  Codex sign-ins were detected locally; no login, model request or credential
  replacement was performed during verification.
- Four vertical agent buttons use existing marks from local apps/extensions.
  No selection, missing CLI/sign-in or an unactivated key blocks advancement.
- Successful sign-in advances automatically; no Sign in again or Continue.
  Change agent still opens settings without immediately redirecting back.
- API submenu saves endpoint/model/masked-key profiles privately outside projects.
  The helper receives secrets on stdin; status and argv contain no key. Native
  UI verification saves a fixture profile and advances automatically, then tests
  an account transition and the complete project creation flow.
- Setup renderer choices are horizontal. All five steps retain their rectangle;
  Back preserves answers. Preferred MCP defaults and unchecked candidate defaults
  are verified. Non-preferred choices save as explicit custom registrations.
- The reported crash was a test process at final Create: the upstream temporary
  file API dereferenced a default null error pointer. Supplying that pointer
  fixes the path. The full UI regression creates a temporary project and verifies
  its MCP executable/argv, without registering the fixture or opening an editor.
- 278 Python tests, Ruff, strict mypy and policy checks pass. Changed scripts
  pass GDScript lint. Native startup flow and rendered captures were inspected.
- Sign-in is provider-owned. Chat transports are still unimplemented; account
  connection does not pretend to enable free-form editing.
- Command lifecycle checks also send 40 KB of UTF-8 over chunked stdin, in
  addition to reload, cancellation, teardown and separate stdout/stderr checks.
- No newer crash report exists beyond the known 00:01:38 incident, PID 27726.

- New API connections must pass model-list verification before saving or advancing.
  The complete native test uses a local HTTP fixture, not a real provider or key.
