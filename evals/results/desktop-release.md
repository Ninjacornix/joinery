# Desktop alpha verification

Release target: `v0.1.0-alpha.1`. Publication remains gated on actual native
Windows, Linux and macOS jobs; this report is not a claim that pending jobs pass.

## Local evidence

- Updated Python suite: 384 passed, 3 native-Windows tests skipped on macOS.
- Ruff and strict mypy pass for 72 production/release source files, including
  strict checking against native Windows platform stubs.
- Native macOS existing-scene and empty-scene review/undo suites pass, retaining
  checked command-menu, scrollable-review and accepted-model PNG captures.
- A fresh macOS desktop extraction passes measured manifest, version, CLI,
  addon-delivery and launcher checks in a path containing spaces.
- All three official-runtime game exports were produced. The macOS export runs
  natively; the Linux export runs in an offline Linux AMD64 container. Both prove
  the technical GLB geometry loads. Windows native execution remains pending.
- Actual tool and real model workflow evidence is in [alpha-tools.md](alpha-tools.md).

## Hosted failures found and corrected

Initial candidate: `002390b`, [run 42487334081](https://github.com/Ninjacornix/joinery/actions/runs/42487334081).

| Gate | Observed failure | Correction and verification |
| --- | --- | --- |
| Windows provider cleanup | The Node wrapper exited before its native Codex descendant released the temporary home | Snapshot owned descendants and wait on stable native handles after cancellation; require pipe EOF and immediate child exit. The real Windows Codex startup/EOF/cancellation check passes in the next validation run. |
| Windows editor build | Missing upstream AccessKit and D3D12 build dependencies | Download exact SDK archives, verify measured/published hashes, extract verified bytes, retain accessibility and D3D12 support. Package only matching original runtime DLLs and their upstream notices. Actual native build is pending. |
| Linux recipe replay | All ten outputs were absent: image UID 1000 could not write host UID 1001 files, although Blender returned zero | Run Linux workers under the invoking non-root UID:GID with a private disposable home. All six original recipes pass unchanged fingerprints, all four creative lifecycles pass, and all four MCP workers connect on Linux-owned storage; saved Godot output belongs to UID 1001. Hosted retry passed all six recipes in 43 seconds and all four rich lifecycles in 91 seconds. |
| macOS graphical editor | Compilation succeeded; native OpenGL was explicitly rejected in the hosted virtual Mac | Enable the upstream-supported ANGLE backend with verified static dependencies. Native VM retry is pending. |

The original macOS/Linux unit and real Codex startup checks passed. Native
Windows typing also revealed POSIX-only stub assumptions; correction and the
remaining Windows unit, ACL, hook and graphical gates are still pending.

Focused retry: `37e2679`, [run 42488962130](https://github.com/Ninjacornix/joinery/actions/runs/42488962130).
It exercises the Windows editor and all three Python/creative checks without
duplicating the original Linux/macOS compiler jobs. It cannot publish a release.

Final release validation, native screenshots, archive hashes and publication
verification will be recorded here after those gates actually complete.
