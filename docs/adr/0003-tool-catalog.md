# 0003: Tool catalog and install menu

Status: accepted, 2026-11-07

## Context
Users choose their creation tools at install, and new tools must be easy to add. Each
outside tool needs an MCP that is official or highly regarded, and popularity alone is
not enough: the most popular Blender MCP (33k stars) bundles generative 3D services.

## Decision
`policy/tools.toml` is the single list, and the menu, allowlist and checks read it. A
new tool is one `[[tool]]` entry. Selection is stored in `joinery.toml`. `joinery setup`
is the first front end; the Godot installer screen will call the same functions.

Admission bar, enforced in `joinery/catalog.py`:
- **Official:** maintained by the tool's own project, or
- **Community:** at least 50 stars, a push within 180 days of vetting, accepted licence.
- All: pinned to a full commit, not on the rejected list, no generative features.

Thresholds live in code so changing them shows up in review as a policy change.

| Tool | MCP | Tier |
|---|---|---|
| Godot | hi-godot/godot-ai | community |
| Blender | lab/blender_mcp (Blender Lab) | official |
| Aseprite | diivi/aseprite-mcp | community |
| SuperCollider | jeremyruppel/claude-collider | community |
| GIMP, Inkscape, REAPER, MuseScore, Audacity | see catalog | community |

Godot uses godot-ai over the more-starred Coding-Solo/godot-mcp, last pushed in April.

No MCP clears the bar for Krita or Surge XT, so both are listed but not selectable.
Material Maker gets a Joinery-built MCP; existing ones are early alphas.

## Limits
Vetting read repo metadata and READMEs, not all source. Stars show popularity, not
safety. Each MCP's real tool list needs checking before it first runs. Install commands
are not automated yet: the menu records choices and drives the allowlist.
