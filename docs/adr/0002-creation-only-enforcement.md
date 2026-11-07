# 0002: Creation-only enforcement

Status: accepted, 2026-11-07

## Decision
Enforce the rules in code and CI, with data in `policy/`:

- **MCP allowlist:** a PreToolUse hook denies every MCP server outside the built-in
  servers, the project's selected tools and its custom tools. It is deny by default, so account connectors and
  plugin MCPs are blocked in this repo too. The hook is agent-agnostic: one
  `joinery hook` command is wired into every supported agent (ADR 0006).
- **Commands and URLs:** the hook blocks denied hosts and installs of denied packages.
- **Dependencies:** `joinery check deps` scans manifests and lockfiles, including
  transitive packages in `uv.lock`.
- **Assets:** `joinery check assets` requires a `joinery.lock` entry for every file under
  `art/` and `audio/`, with matching sha256, plus a recipe or import record. Output from
  user-added tools uses origin `custom-tool`, so a project stays honest about its assets.

The hook fails closed: internal errors exit 2, and each agent's config blocks on
hook failure where it can (Claude Code's `onFailure: block` needs 2.1.295+).

## Limits
- The hook is a guard rail, not a sandbox. A determined script can get around text
  matching; offline sandbox workers are the real boundary. Per-agent hooks also
  have gaps (hosted tools, subagent calls, hook-error-passes); see ADR 0006. The
  guarantee that holds regardless of agent is the sandbox plus the CI gate.
- The asset gate checks that a recipe exists. Replay comes with the rebuild command.
- Hook matching cannot inspect what an allowed MCP does internally, which is why the
  catalog vets each MCP and pins it to a commit.
