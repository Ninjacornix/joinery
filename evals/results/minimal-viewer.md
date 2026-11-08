# Minimal viewer

2026-11-07. Refinement from the user's five screenshots.

- 227 Python tests pass, including the five-patch guard checks. Ruff, strict
  mypy, policy checks and changed GDScript lint pass.

- Removed the metadata block and manual Refresh button. A selected model now
  fills the viewer using perspective framing based on bounds and aspect ratio.
- App-only rail; app buttons and the toolbar toggle use one sizing/alignment
  function. Bottom version/status chrome and assistant docking popup removed.
- Automatic refresh detects source/config changes, skips unchanged hashes and
  preserves idle previews. It also responds to editor reimport signals.
- Python tests cover change stamps, additions/deletions, explicit sources and
  unchanged polling without index/hash work. Full editor tests verify automatic
  refresh after a change in a temporary project copy, hidden native selectors,
  absent version info and docking popup, and consistent rail button sizing.
- Stock viewer test verifies image/model/audio loading and camera preservation
  on an unchanged poll. Full rendered editor captures inspected.
- No game asset content changed. Provenance enforcement stays in the shared
  Python core; metadata remains available through the CLI.
