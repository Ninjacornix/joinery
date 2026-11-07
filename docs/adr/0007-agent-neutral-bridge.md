# 0007: Agent-neutral bridge

Status: accepted, 2026-11-07

## Context
The Journeyman sidebar must drive whichever agent the user runs without knowing
which one it is. PLAN.md §3 called for an "agent-agnostic interface" over a
local socket; ADR 0006 made enforcement agent-agnostic, and the bridge is the
matching seam for the sidebar.

## Decision
The sidebar speaks one protocol to a `Bridge`; a per-agent adapter translates to
that agent. Three parts, in `bridge/`:

- `protocol.py`: the stable message contract. Sidebar → agent (`Prompt`,
  `Decision`, `Cancel`) and agent → sidebar (`Step`, `ToolActivity`, `Preview`,
  `Message`, `Done`), carried in an `Envelope` across the socket. `ToolActivity`
  mirrors the gate's verdict so a blocked call shows with its reason instead of
  failing silently.
- `adapters.py`: an `AgentAdapter` per agent, keyed by the same ids as
  `joinery.agents`. Each declares whether it is `ready`.
- `host.py`: the `Bridge` the sidebar uses. It picks the adapter, and refuses
  (fail-closed, `GateUnavailable`) to run any agent whose Rule Zero gate cannot
  hold, consistent with ADR 0006.

## Consequences
The sidebar and the rest of Joinery stay agent-agnostic: adding or dropping an
agent is a registry entry plus an adapter, with no change to callers.

The adapters are declared seams, not finished transports. `ready` is `False` for
all of them, and the bridge will not pretend an agent can run. The socket
transport to the Godot side and the real per-agent transports (Claude via the
Agent SDK, Codex via its app-server protocol, Gemini and opencode via their
CLIs/APIs) are Phase 2; the contract and the fail-closed check exist now so they
hold for every agent from the start.
