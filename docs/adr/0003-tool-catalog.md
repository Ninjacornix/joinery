# 0003: Tool catalog and install menu

Status: accepted, 2026-11-07

## Context
Users choose their creation tools per project. Joinery recommends a preferred list, and
users may replace any of it with their own programs and MCPs. Popularity alone cannot
decide the list: the most popular Blender MCP (33k stars) bundles generative 3D services.

## Decision
`policy/preferred_tools.toml` is the preferred list, and a new tool is one `[[tool]]`
entry. Each project stores its choices in its own `joinery.toml`, and custom tools go in
`[[tools.custom]]`.

Bar for preferred MCPs, enforced in `joinery/catalog.py`:
- Non-generative and no telemetry, for every tier, including opt-out telemetry.
- Pinned to a full commit, and not on the rejected list.
- Official, or community with at least 50 stars, a push within 180 days of vetting and
  an accepted licence.

| Tool | MCP | Tier |
|---|---|---|
| Godot | tugcantopaloglu/godot-mcp | community |
| Blender, also materials | lab/blender_mcp (Blender Lab) | official |
| SuperCollider | jeremyruppel/claude-collider | community |
| Inkscape | see list | community |

Aseprite, REAPER, MuseScore and Audacity were later removed, and GIMP's
community pin was dropped for Joinery's own server
([ADR 0025](0025-tool-removals.md)). Audacity returned in its own worker
([ADR 0082](0082-audacity-worker.md)).

Godot's popular options failed the bar: hi-godot/godot-ai sends telemetry by default and
routes vision requests to cloud models, tomyud1/godot-mcp has a `generate_2d_asset` tool,
and Coding-Solo/godot-mcp has had no push since April. The chosen server exposes a generic
`game_http_request` tool, which stays blocked.

Custom tools are the project owner's call and are not vetted. They may be generative or
send telemetry, and the menu says so. Their assets take origin `custom-tool` and can never
be recorded as `tool-created`.

Materials: Blender (shader nodes and bake) is preferred for now. Material Maker's MCPs are
early, so the MIT Tool-MaterialMaker-MCP will be forked and hardened as a Joinery server.
Krita and Surge XT have no MCP that clears the bar, so Joinery builds its own in
`servers/paint` and `servers/sound`. Both are listed but not selectable until then.

## Limits
Vetting scanned each pinned source for telemetry libraries, generative services and
outbound calls. It was not a line-by-line audit, did not run the servers, and did not cover
transitive dependencies or the programs themselves. Offline sandbox workers are what
actually guarantee nothing is sent.
