#!/usr/bin/env bash
# Manage the thin Godot fork: a pinned upstream tag plus patches/*.patch.
# The checkout in godot-src/ is disposable; patches/ and UPSTREAM are the truth.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/godot-src"
# shellcheck source=UPSTREAM
source "$HERE/UPSTREAM"

g() { git -C "$SRC" "$@"; }
die() { echo "fork.sh: $*" >&2; exit 1; }

fetch_tag() {
  g fetch --quiet --depth 1 --no-tags upstream "refs/tags/$1:refs/tags/$1"
}

cmd_sync() {
  if [[ -d "$SRC" ]]; then
    [[ "${1:-}" == "--force" ]] ||
      die "godot-src/ exists. 'sync --force' rebuilds it from patches/ and drops unexported commits."
    rm -rf "$SRC"
  fi
  mkdir -p "$SRC"
  g init --quiet --initial-branch "$JOINERY_BRANCH"
  g remote add upstream "$GODOT_REMOTE"
  fetch_tag "$GODOT_TAG"
  local actual
  actual="$(g rev-parse "$GODOT_TAG^{commit}")"
  [[ "$actual" == "$GODOT_COMMIT" ]] ||
    die "tag $GODOT_TAG is $actual, pinned $GODOT_COMMIT. Refusing to continue."
  g reset --quiet --hard "$actual"
  shopt -s nullglob
  local patches=("$HERE"/patches/*.patch)
  if ((${#patches[@]})); then
    g -c user.name="Joinery Build" -c user.email="build@joinery.invalid" am --quiet --3way "${patches[@]}"
  fi
  echo "$JOINERY_BRANCH = $GODOT_TAG + ${#patches[@]} patch(es) in $SRC"
}

cmd_export() {
  [[ -d "$SRC" ]] || die "no godot-src/. Run 'sync' first."
  find "$HERE/patches" -name '*.patch' -delete
  g format-patch --quiet --zero-commit --no-signature --no-numbered \
    -o "$HERE/patches" "$GODOT_TAG..$JOINERY_BRANCH"
  echo "exported $(find "$HERE/patches" -name '*.patch' | wc -l | tr -d ' ') patch(es)"
}

cmd_rebase() {
  local new="${1:-}"
  [[ -n "$new" ]] || die "usage: fork.sh rebase <tag>"
  [[ -d "$SRC" ]] || die "no godot-src/. Run 'sync' first."
  [[ "$new" != "$GODOT_TAG" ]] || die "already on $new"
  fetch_tag "$new"
  # Re-running after resolving conflicts lands here with the rebase already done.
  if ! g merge-base --is-ancestor "$new" "$JOINERY_BRANCH"; then
    g rebase --onto "$new" "$GODOT_TAG" "$JOINERY_BRANCH" || die \
      "conflicts. Resolve in godot-src/, run 'git rebase --continue', then re-run this command."
  fi
  local commit
  commit="$(g rev-parse "$new^{commit}")"
  sed -i.bak -e "s/^GODOT_TAG=.*/GODOT_TAG=$new/" -e "s/^GODOT_COMMIT=.*/GODOT_COMMIT=$commit/" \
    "$HERE/UPSTREAM"
  rm "$HERE/UPSTREAM.bak"
  GODOT_TAG="$new"
  cmd_export
  echo "rebased onto $new. Review godot_patches/ and commit it."
}

cmd_build() {
  [[ -d "$SRC" ]] || die "no godot-src/. Run 'sync' first."
  local jobs
  jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"

  # Cache aggressively: patching the engine means rebuilding it repeatedly, and
  # almost nothing changes between those builds. Two independent caches:
  #
  #  ccache     skips recompiling a translation unit whose input is unchanged.
  #             This is the one that matters after editing a single file, and it
  #             survives `sync --force` because it is keyed on content, not path.
  #  SCons cache keyed on build signature, so it also helps across a fresh
  #             checkout or a switched branch.
  #
  # Both are opt-out via JOINERY_NO_CCACHE / JOINERY_NO_SCONS_CACHE, since a
  # cache is the first thing to suspect when a build goes strange.
  local -a cache_args=()
  if [[ -z "${JOINERY_NO_CCACHE:-}" ]] && command -v ccache >/dev/null; then
    cache_args+=(
      "c_compiler_launcher=ccache"
      "cpp_compiler_launcher=ccache"
    )
  fi
  if [[ -z "${JOINERY_NO_SCONS_CACHE:-}" ]]; then
    local scons_cache="${JOINERY_SCONS_CACHE:-$HOME/.cache/joinery/scons}"
    mkdir -p "$scons_cache"
    cache_args+=("cache_path=$scons_cache" "cache_limit=${JOINERY_SCONS_CACHE_GIB:-8}")
  fi

  (cd "$SRC" && uv run --frozen --project "$HERE/.." scons target=editor -j"$jobs" "${cache_args[@]}" "$@")
}

cmd_cachestats() {
  command -v ccache >/dev/null || die "ccache is not installed."
  ccache --show-stats
}

case "${1:-}" in
  sync | export | rebase | build | cachestats)
    cmd="$1"
    shift
    "cmd_$cmd" "$@"
    ;;
  *)
    echo "usage: fork.sh sync [--force] | export | rebase <tag> | build [scons args] | cachestats" >&2
    exit 2
    ;;
esac
