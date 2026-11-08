# Windows native OpenGL initialization

Accepted: 2026-11-08. Scope: one upstream candidate patch against the pinned
Godot 4.7.2 tree, `platform/windows/gl_manager_windows_native.cpp`.

The native Windows editor compiled successfully but could not create its OpenGL
window on the hosted runner. The graphical gate remained mandatory. A standalone
Win32 probe reproduced the exact WGL initialization stages with the verified,
app-local Mesa runtime and fresh processes for each case.

Evidence: [editor failure](https://github.com/Ninjacornix/joinery/actions/runs/42488962130)
and [native pixel-format probe](https://github.com/Ninjacornix/joinery/actions/runs/42497648444).
The runner can create a real HWND/HDC on WinSta0. Loading Mesa before GDI selects
the pixel format gives a valid format index of 15 and a working OpenGL 3.3 core
context. GDI-first initialization fails legacy context creation even though the
correct private DLL subsequently loads. Setting the same window's pixel format
again returns failure. A `.local` marker does not change these outcomes.

Move the existing OpenGL DLL load and legacy WGL export resolution into the
native manager's initialization, before pixel-format selection. Configure each
window once in `window_create`; remove the redundant configuration inside
`_create_context`. That function's sole call chain already follows successful
window configuration. Keep legacy-context, ARB and OpenGL 3.3 validation intact.

This general renderer fix belongs in its own patch and is suitable for upstream.
The fork-area test permits only this patch's exact Windows implementation file;
the editor-only default, deletion limits and upstream-intent checks remain.
No other platform files or native graphics requirements are admitted by this
exception. App-local software rendering stays CI infrastructure; shipped bundles
retain the original native executable and required SDK runtime notices.

System DLL replacement, registry changes and error suppression do not address
the observed call-order problem. Adding ANGLE alone would leave this native WGL
path incorrect. The narrow fix preserves the existing renderer choices and
avoids changing the renderer configuration to accommodate the test environment.

The native probe establishes causality; release acceptance still requires the
patched Windows editor's graphical review/undo, widgets, isolated startup,
MCP, clean bundle and exported-runtime gates. Removing this patch and its exact
test exception restores the prior fork scope; the other nine patches stay intact.
