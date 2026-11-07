# 0004: Joinery replaces Godot's create-project dialog

Status: accepted, 2026-11-07

## Decision
Creating a project goes through Joinery's flow, where the user names the project and picks
tools. In the fork, `ProjectManager::_new_project` opens the Joinery screen instead of
`ProjectDialog` in `MODE_NEW`. That screen calls the same code as `joinery new`:
`joinery/installer.py` and `joinery/project.py`. Godot's import and install modes stay.

## Consequences
This patch is Joinery-specific and will not go upstream, unlike the others in the series.
Stock Godot keeps its own dialog, so `joinery new` is the path there. Projects stay plain
Godot projects either way.

The native screen is not built yet.
