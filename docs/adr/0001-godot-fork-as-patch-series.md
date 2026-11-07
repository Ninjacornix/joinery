# 0001: Godot fork as a patch series

Status: accepted, 2026-11-07

## Context
Engine changes stay small and upstreamable, and live in `godot_patches/`. Godot's repo is large, and this repo must stay light.

## Decision
- Pin `4.7.2-stable` (latest 4.7 tag at setup) by tag and commit in `godot_patches/UPSTREAM`.
- Track patches here; check out Godot shallowly into gitignored `godot_patches/godot-src/`,
  branch `joinery-4.7`. `fork.sh` handles sync, export, rebase and build.
- No hosted GitHub fork for now. Add one as a remote if upstream PRs or CI need it.

## Consequences
Sync verifies the pinned commit, so a moved tag fails loudly. The checkout is
disposable, so unexported commits are lost on `sync --force`. Moving to a new tag is
one command, plus conflict resolution.
