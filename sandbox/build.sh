#!/usr/bin/env bash
# Build the sandbox image, with versions from versions.env so the pins live in
# one place. Tools are opt-in, because all of them together is several
# gigabytes and a project only enables some.
#
#   sandbox/build.sh                                  # core: godot, ffmpeg, xvfb
#   sandbox/build.sh --with blender --with krita      # add tools
#   sandbox/build.sh --all                            # everything packaged here
#
# Run fetch-checksums.sh first: the build verifies every direct download against
# a recorded hash and fails if one is missing.
#
# Written for bash 3.2, which is what macOS ships, so no associative arrays.
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=/dev/null
source "${here}/versions.env"

tag=${JOINERY_SANDBOX_TAG:-joinery-sandbox}
selectable="blender krita supercollider surge materialmaker gimp inkscape"
wanted=""

contains() { # contains <needle> <haystack words>
  local needle=$1
  shift
  local word
  for word in $1; do
    [ "${word}" = "${needle}" ] && return 0
  done
  return 1
}

while [ $# -gt 0 ]; do
  case $1 in
    --with)
      shift
      [ -n "${1:-}" ] || { echo "--with needs a tool name" >&2; exit 2; }
      if ! contains "$1" "${selectable}"; then
        echo "unknown tool: $1" >&2
        echo "available: ${selectable}" >&2
        exit 2
      fi
      contains "$1" "${wanted}" || wanted="${wanted} $1"
      shift
      ;;
    --all)
      wanted="${selectable}"
      shift
      ;;
    --tag)
      shift
      tag=${1:?--tag needs a value}
      shift
      ;;
    -h | --help)
      sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [ ! -s "${here}/checksums.txt" ] || ! grep -qv '^#' "${here}/checksums.txt"; then
  echo "checksums.txt has no entries. Run sandbox/fetch-checksums.sh first," >&2
  echo "which downloads each pinned artifact and records its real hash." >&2
  exit 1
fi

set -- \
  --build-arg "GODOT_VERSION=${GODOT_VERSION}" \
  --build-arg "GODOT_CHANNEL=${GODOT_CHANNEL}" \
  --build-arg "BLENDER_SERIES=${BLENDER_SERIES}" \
  --build-arg "BLENDER_VERSION=${BLENDER_VERSION}" \
  --build-arg "MPFB2_VERSION=${MPFB2_VERSION}" \
  --build-arg "MATERIALMAKER_VERSION=${MATERIALMAKER_VERSION}"

for tool in ${selectable}; do
  flag="WITH_$(printf '%s' "${tool}" | tr '[:lower:]' '[:upper:]')"
  if contains "${tool}" "${wanted}"; then
    set -- "$@" --build-arg "${flag}=1"
  else
    set -- "$@" --build-arg "${flag}=0"
  fi
done

echo "building ${tag} with:${wanted:- (core only)}"
exec docker build -t "${tag}" "$@" "${here}"
