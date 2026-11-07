# 0004: Joinery replaces Godot's create-project dialog

Status: accepted, 2026-11-07. Built 2026-11-07.

## Decision
Creating a project goes through Joinery's flow, where the user names the project
and picks the tools that will make it. The dialog has two pages: Godot's own
fields, then Joinery's questionnaire. **Next** moves between them and **Create**
finishes, so the whole decision happens in the Project Manager rather than being
deferred to the editor. Godot's import and install modes stay as they are.

This has to be a patch, not a plugin. The Project Manager runs before any
project is open, so no addon is loaded and there is nothing to hook; the
questionnaire cannot live in GDScript here, which is why it is C++ in
`editor/project_manager/project_dialog.cpp`.

The questionnaire is built at dialog-open time from `joinery tools --json`, so
the catalog and its admission rules stay the single source of truth. The C++
cannot invent a tool, cannot offer one whose server is not built, and shows the
catalog's own reason as each tooltip. Creating then installs the addon into the
new project and runs `joinery setup` with the chosen tools, which is the same
path `joinery new` takes.

## Consequences
Joinery-specific, and will not go upstream: it reroutes a stock flow into
Joinery's own. See ADR 0005.

Everything degrades. With no Joinery install, or no `uv`, the page is skipped,
the title and button read exactly as stock Godot, and the user gets a plain
valid project with a warning in the log. Failing project creation over a missing
sidebar would be the wrong trade. Only in that skipped case is
`joinery/setup_pending` written, so the addon asks in the editor instead; when
the questionnaire has already run it does not ask again.

Stock Godot keeps its own dialog, so `joinery new` remains the path there, and
projects are plain Godot projects either way.

Defaults for a brand-new project come from the engine repo's own selection,
since the project has no `joinery.toml` yet. That is a sensible starting set
rather than a considered decision, and is worth revisiting when the bible
interview lands.
