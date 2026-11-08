# Chatup and preview repair

2026-11-07.

- The earlier Journeyman layout and spacing were restored at the user’s request.
  System logs remain in Output, while slash queries get concise chat replies.
- Upward slash menu supports filtering and arrow keys; Tab/click complete and
  Enter sends in chat, including partial `/hel` and a conversational help reply.
- Dock tab bar removed. Selection is a compact muted context line above the input.
- Object, engine audio, resource and asset-viewer selections populate composer
  chips and are carried in prompt snapshots. Render fallback keeps source IDs.
- Ignored sky sources preview their existing recipe image. Models use Godot's
  empty-editor sky and sun defaults, per the revised user preference.
- Removed GDScript command worker threads after the reported crash. Regression
  checks cover script reload, owner teardown, child cancellation and pipe output.
- 232 Python tests pass, including the six-patch guards, preview linkage and agent menu data. Ruff,
  strict mypy and policy checks pass; changed scripts pass GDScript lint.
- Stock Godot command lifecycle, Chatup and viewer tests pass. Full-editor
  checks and rendered captures cover the source preview and chat command menu.
- No game assets changed. Free-form agent execution is not implemented here.
- Audio playback adds waveform seeking, play/pause, stop, restart, ±5s, loop,
  speed, volume and mute. Runtime tests cover natural completion/repeat,
  keyboard/waveform input, paused seeking and unchanged source bytes/settings.
- Model zoom works through wheel and trackpad input without buttons/tooltips.
  Bottom panels open to a useful height; full editor checks cover this, Enter
  submission, app filtering, hidden tab bar and real model-wheel event routing.
