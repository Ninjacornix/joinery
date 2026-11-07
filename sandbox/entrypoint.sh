#!/usr/bin/env bash
# Start a virtual display, then run the command. Blender, Krita and Material
# Maker are GUI programs driven headless, so they need an X server even when
# nothing is watching.
#
# Nothing here reaches the network. Run the container with --network none and
# this still works, which is the point: a worker that cannot reach a generative
# service cannot use one.
set -euo pipefail

export DISPLAY=${DISPLAY:-:99}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/tmp/runtime-joiner}
mkdir -p "${XDG_RUNTIME_DIR}"
chmod 700 "${XDG_RUNTIME_DIR}"

Xvfb "${DISPLAY}" -screen 0 1920x1080x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
xvfb_pid=$!
trap 'kill "${xvfb_pid}" 2>/dev/null || true' EXIT

# Wait for the display rather than sleeping a guessed interval.
for _ in $(seq 1 50); do
  if xauth list >/dev/null 2>&1 || [[ -e /tmp/.X11-unix/X${DISPLAY#:} ]]; then
    break
  fi
  sleep 0.1
done

exec "$@"
