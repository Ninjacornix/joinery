# Program and MCP regression expansion

Date: 2026-11-08. Execution host: macOS ARM64. Native creative applications run
in the existing offline Linux AMD64 Docker workers under emulation.

Added **319 new parameterized cases across eight test files**. The complete
Python suite reports **727 passed, 4 skipped in 10.05s**; the four skips predate
this change. No new program regression case is skipped. Ruff and strict mypy
pass for 76 production/integration source files, including strict checking with
Windows platform stubs. Shared policy
checks pass. These are local execution results; this change has not been run
through the hosted platform matrix.

The tests found a real Blender MCP validation defect: empty/whitespace-only
part names and boolean transform coordinates reached document editing. The
server now rejects those inputs and explicitly requires finite numeric vectors.
Regression cases exercise both the MCP wire and direct local SDK calls, including
NaN/infinity rejection. Valid negative scales and degree-to-radian conversion
remain covered.

Native execution checks:

- Blender's new actual MCP named-part workflow passes. It verifies translation,
  rotation, scale, unchanged unselected geometry, native `.blend`/GLB export,
  clean replay, preview isolation, rejection, acceptance and full revert.
- Existing Krita painted-layer, audible SuperCollider, Inkscape SVG/pixel and
  Material Maker connected-texture/roughness lifecycles pass.
- The expanded combined five-application command passes with all five native
  checks in one run, matching the command used by the existing CI asset job.
- The real upstream Godot MCP create/add/reject/accept/read round trip passes.
- The real Blender → immediate Godot import/scene instance → review/accept/revert
  cross-tool fixture passes. These are MCP/tool operations, not model calls.

Raw local evidence:

- `/tmp/joinery-expanded-final-unit.log`
- `/tmp/joinery-expanded-final-mcp.log`
- `/tmp/joinery-expanded-native-blender-final.log`
- `/tmp/joinery-expanded-native-creative.log`
- `/tmp/joinery-expanded-final-native.log`
- `/tmp/joinery-expanded-godot-mcp.log`
- `/tmp/joinery-expanded-cross-tool.log`

All documents belong to temporary technical fixtures. No application was
installed, no model inference or generative asset service was used, and no user's
game files or art-approval record was modified. Unit fixtures for unsupported or
licensed applications establish only their documented integration boundaries.
See [testing.md](../../docs/testing.md) for scenario coverage and reproduction.

The subsequent [all-program MCP audit](all-mcp-programs.md) extends this work to
every catalogued server and records its separate contract/native outcomes.
