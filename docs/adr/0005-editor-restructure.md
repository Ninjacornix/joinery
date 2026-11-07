# 0005: Restructure the editor around the Journeyman sidebar

Status: accepted, 2026-11-07

## Context
Users will control most things through the sidebar, so Godot's full editor layout is more
than they need. ADR 0001 assumed a thin series of small, upstreamable patches.

## Decision
Partially restructure and simplify the Godot editor around the sidebar. UI stays plugin
first, and anything the plugin API cannot do goes in Joinery-only patches. General fixes,
such as the 3D context-menu slot, are still kept small and offered upstream. Which panels
change will be decided with the sidebar design.

## Consequences
The series grows beyond thin, so each new Godot release costs more to rebase. Keep Joinery-
only patches isolated from general ones, and prefer hiding or rearranging existing UI over
rewriting it. Projects stay plain Godot projects, and the addon still works on stock Godot
without the restructure.
