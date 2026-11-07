#!/bin/sh
# Install packages, surviving a flaky or emulated network.
#
#   apt-install krita
#
# The image is amd64 even on an arm64 host, because Blender and Material Maker
# publish no Linux arm64 build. Under emulation a long run of package fetches
# starts dropping connections part way through a layer, which fails the whole
# build. Each attempt keeps whatever apt already downloaded in its cache, so
# retrying makes progress rather than starting over.
set -eu

for attempt in 1 2 3 4 5; do
  if apt-get update && apt-get install -y --no-install-recommends "$@"; then
    rm -rf /var/lib/apt/lists/*
    exit 0
  fi
  echo "apt-install: attempt ${attempt} failed, retrying" >&2
  sleep 5
done

echo "apt-install: gave up after 5 attempts: $*" >&2
exit 1
