# 0041. The Godot MCP runs in the offline worker

Status: accepted (2026-11-11)

## Context

The pinned Godot MCP (tugcantopaloglu/godot-mcp) was cloned and built on the user's machine with
Git and npm, and its read-only server ran there under the host's Node.js, driving the Joinery
editor binary. Every staged write and game run already happened in `joinery-mcp-worker`, with
the host-built source mounted into it. So the host needed Node.js for one server, the read side
ran with full network access, and a host build could drift from what the worker ran.

## Decision

1. **Baked into the image.** `sandbox/mcp.Dockerfile` builds the pinned commit in a build stage:
   the commit archive is hash-checked against `checksums.txt` (`godotmcp`, recorded by
   `fetch-checksums.sh` like every direct download), every locked package must name its
   registry tarball and integrity hash, and `npm ci --ignore-scripts` installs from that lock.
   Only the built server and its runtime packages are copied into the final image, with
   `/opt/godot-mcp/joinery-pin.json` naming the commit and the lock's hash. npm and the
   TypeScript compiler stay in the build stage. The commit is written in `versions.env`, the
   Dockerfile's `GODOT_MCP_REF` default and the catalog; a test keeps them equal.
2. **Install checks the image.** `joinery mcp install --tool godot` (and setup) runs one offline
   container that lists the pin, the lock and the in-game inspector script. The pin must match
   the catalog, the lock its recorded hash, and the lock must pass the shared dependency gate.
   The host keeps only a record (`server.json`, kind `docker`) and the inspector script, which
   the gateway compares against to take it out of a project after a game run. A host install
   from an older Joinery is not trusted: readiness asks for setup, which moves it.
3. **Every connection is a worker.** Inspection runs `godot_worker(project, readonly=True)`:
   `--network none`, the project mounted read-only at `/work`, the image's own Godot. A staged
   change runs a writable worker on the stage, as before. No host Node.js or host Godot is used,
   and there is no host fallback: without Docker the tool says Needs Docker, without the image
   it says Rebuild.
4. **Paths.** A worker connection maps absolute host paths under its mounted folder to `/work`
   in every argument, and `/work` paths in every answer back to the host folder
   (`mcp_runtime.worker_root`, `map_paths`, `rewrite_root`). A staged connection's answers are
   then mapped from the stage back to the project, so the agent always reads the project paths
   it asked about. `res://` paths pass through unchanged.

## Consequences

- The user's machine needs no Node.js for Godot; it is still used to build the SuperCollider
  server on the host.
- Inspection uses the worker's stock Godot 4.7.2 rather than the Joinery editor binary; the
  editor binary is still checked, for **Open in**.
- Each project gateway starts one more container for inspection (a second or two, more under
  emulation on Apple Silicon).
- The native CI step can no longer probe the server on runners without worker images; it checks
  that install and status say so. The assets job, which builds the images, probes the server and
  runs a staged edit through it.
- Changing the pin means updating the catalog, `versions.env` and the Dockerfile default,
  re-running `fetch-checksums.sh godotmcp`, and rebuilding the worker (`joinery bootstrap`).
