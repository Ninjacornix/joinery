# Background test editors

Accepted: 2026-11-10. Scope: one Joinery-only test-support patch against the
pinned Godot 4.7.2 tree, in `platform/joinery_background.h`,
`platform/macos/` and `platform/windows/display_server_windows.cpp`.

The native gates (`tests/godot/run_*.py`) open real, windowed editors to
check layouts and capture screenshots. On a developer's Mac each launch
activated the app, made its window key and put it in the Dock, so every run
pulled the keyboard and mouse away from whatever the user was doing.

`JOINERY_BACKGROUND=1` keeps such an editor in the background. On macOS the
app becomes an accessory app (no Dock icon, no activation), skips
`activateIgnoringOtherApps:` and the unbundled activation workaround, and
shows its windows with `orderFrontRegardless` instead of
`makeKeyAndOrderFront:`, so they are on screen and draw but are never key.
While the switch is on, a window another app covers still counts as visible,
so rendering (and every capture) continues; stock Godot stops drawing a fully
occluded window. On Windows, windows open with `SW_SHOWNOACTIVATE` and never
call `SetForegroundWindow`/`SetFocus`. Linux runs in CI under a virtual
display, where focus does not matter, and is unchanged.

`isolated_environment()` in `tests/godot/run_startup_test.py` sets the
variable, so every runner gets it. The editor the user launches never has it
set, and without it the code paths are stock.

This is test support, not intended for upstream. The fork-area guard permits
only these exact files with this ADR.
