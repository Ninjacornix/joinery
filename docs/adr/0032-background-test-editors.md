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

## Crash traces for fail-fast exits on Windows

A second test-support patch, in `platform/windows/godot_windows.cpp` only,
adds `JOINERY_CRASH_TRACE=1`. On Windows, `abort()`, an invalid C runtime
parameter, `std::terminate` and a pure virtual call end the process with
`0xC0000409` through `__fastfail`, which bypasses Godot's crash handler and
often Windows Error Reporting, so such a crash left no stack. With the
variable set, handlers in the editor's static runtime and in `ucrtbase.dll`
(shared by the private Mesa DLLs) print the failing thread's stack, resolved
with the editor's `.pdb`, to stderr and exit with `0xE04A0001`–`0xE04A0004`.
`JOINERY_CRASH_TRACE=selftest` aborts at startup to prove the trace in CI.
Only the Windows CI editor steps set it; `isolated_environment()` passes it
through. Without it nothing changes.

On macOS the test editor's windows also stay at desktop level (`GodotWindow` clamps
`setLevel`), behind every app the user works in, so they never draw over it. They
keep drawing for captures, because the window delegate ignores occlusion in this mode.
