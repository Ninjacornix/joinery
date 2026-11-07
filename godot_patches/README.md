# Godot fork

Joinery's fork is a patch series, not a long-lived repo: `UPSTREAM` pins a Godot
tag and `patches/` holds each change as a small, upstreamable patch. `godot-src/`
is a disposable, gitignored shallow checkout on branch `joinery-4.7`.

```bash
godot_patches/fork.sh sync            # checkout pinned tag + apply patches
godot_patches/fork.sh export          # write joinery-4.7 commits to patches/
godot_patches/fork.sh rebase 4.7.3-stable   # move series to a new tag, update UPSTREAM
godot_patches/fork.sh build [args]    # scons target=editor via uv
```

To change a patch: commit (or amend) on `joinery-4.7` in `godot-src/`, run
`export`, then commit `patches/` here. `sync --force` discards unexported commits.

Intended series: 3D editor context-menu slot and viewport pick hook, meant for
upstream, plus Joinery-only patches that restructure the editor (ADR 0005). Projects
must stay openable in stock Godot; the addon degrades without these.
