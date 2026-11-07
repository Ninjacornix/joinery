# Joinery sandbox: the creative tools, offline.
#
# This image is where Rule Zero is actually guaranteed. The per-agent hooks are
# a guard rail that a determined script can talk its way around; a worker with
# no network cannot reach a generative service at all. So run it with no
# network, which is what sandbox/build.sh and the documented run command do.
#
# Build through sandbox/build.sh, which sources versions.env so the pins live in
# exactly one place:
#
#   sandbox/fetch-checksums.sh          # once, records real hashes (needs network)
#   sandbox/build.sh --with blender     # then build, verified and offline-ready
#
# Tools are selectable, because installing all of them is several gigabytes and
# a project only enables some. Each is its own layer, so its cost is visible and
# cached independently.
#
# Everything Debian packages comes from apt, which pins and GPG-verifies it.
# Direct downloads are hash-checked against checksums.txt, which only
# fetch-checksums.sh writes, from the bytes it actually downloaded. A missing or
# mismatched hash fails the build rather than installing an unverified binary.

FROM debian:trixie-slim

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8

# Which tools to include. Default is the minimum that can run a Godot project
# and render audio and video; the heavy creative tools are opt-in.
ARG WITH_BLENDER=0
ARG WITH_KRITA=0
ARG WITH_SUPERCOLLIDER=0
ARG WITH_SURGE=0
ARG WITH_MATERIALMAKER=0
ARG WITH_GIMP=0
ARG WITH_INKSCAPE=0

# Core runtime. xvfb because Blender, Krita and Material Maker are GUI programs
# we drive headless; ffmpeg for capture and audio conversion; fluidsynth to
# render MIDI through SoundFonts.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      ffmpeg \
      fluidsynth \
      fontconfig \
      fonts-dejavu-core \
      git \
      git-lfs \
      python3 \
      python3-venv \
      unzip \
      xauth \
      xvfb \
      xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Hashes for every direct download, checked before anything is unpacked.
COPY checksums.txt /opt/joinery/checksums.txt
COPY verify.sh /usr/local/bin/joinery-verify-download
RUN chmod +x /usr/local/bin/joinery-verify-download

# --- Godot -------------------------------------------------------------------
# The engine itself, headless-capable. Must match godot_patches/UPSTREAM, which
# is the base of the patch series.
ARG GODOT_VERSION
ARG GODOT_CHANNEL
RUN set -euo pipefail; \
    if [ -z "${GODOT_VERSION:-}" ] || [ -z "${GODOT_CHANNEL:-}" ]; then \
      echo "GODOT_VERSION/GODOT_CHANNEL unset: build via sandbox/build.sh" >&2; \
      exit 1; \
    fi; \
    name="Godot_v${GODOT_VERSION}-${GODOT_CHANNEL}_linux.x86_64"; \
    curl -fsSL -o /tmp/godot.zip \
      "https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}-${GODOT_CHANNEL}/${name}.zip"; \
    joinery-verify-download /tmp/godot.zip godot; \
    unzip -q /tmp/godot.zip -d /tmp/godot; \
    install -m 0755 "/tmp/godot/${name}" /usr/local/bin/godot; \
    rm -rf /tmp/godot.zip /tmp/godot

# --- Blender (+ MPFB2) -------------------------------------------------------
# Modelling, geometry nodes, shader nodes and bake, plus parametric humans.
ARG BLENDER_SERIES
ARG BLENDER_VERSION
ARG MPFB2_VERSION
RUN set -euo pipefail; \
    if [ "${WITH_BLENDER}" != "1" ]; then echo "blender: not requested"; exit 0; fi; \
    if [ -z "${BLENDER_VERSION:-}" ]; then \
      echo "BLENDER_VERSION unset: build via sandbox/build.sh" >&2; exit 1; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
      libegl1 libgl1 libglu1-mesa libsm6 libxfixes3 libxi6 libxkbcommon0 \
      libxrender1 libxxf86vm1 \
    && rm -rf /var/lib/apt/lists/*; \
    curl -fsSL -o /tmp/blender.tar.xz \
      "https://download.blender.org/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz"; \
    joinery-verify-download /tmp/blender.tar.xz blender; \
    mkdir -p /opt/blender; \
    tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1; \
    ln -s /opt/blender/blender /usr/local/bin/blender; \
    rm /tmp/blender.tar.xz; \
    curl -fsSL -o /tmp/mpfb2.tar.gz \
      "https://github.com/makehumancommunity/mpfb2/archive/refs/tags/v${MPFB2_VERSION}.tar.gz"; \
    joinery-verify-download /tmp/mpfb2.tar.gz mpfb2; \
    mkdir -p /tmp/mpfb2; \
    tar -xzf /tmp/mpfb2.tar.gz -C /tmp/mpfb2 --strip-components=1; \
    test -d /tmp/mpfb2/src/mpfb || { echo "mpfb2: src/mpfb missing" >&2; exit 1; }; \
    addons="/opt/blender/${BLENDER_SERIES}/scripts/addons"; \
    mkdir -p "${addons}"; \
    cp -r /tmp/mpfb2/src/mpfb "${addons}/mpfb"; \
    rm -rf /tmp/mpfb2.tar.gz /tmp/mpfb2; \
    blender --version

# --- Krita -------------------------------------------------------------------
# Layered painting. Debian packages it, so apt verifies it.
RUN set -euo pipefail; \
    if [ "${WITH_KRITA}" != "1" ]; then echo "krita: not requested"; exit 0; fi; \
    apt-get update && apt-get install -y --no-install-recommends krita \
    && rm -rf /var/lib/apt/lists/*

# --- SuperCollider -----------------------------------------------------------
# sclang and scsynth, for sound design and synthesis.
RUN set -euo pipefail; \
    if [ "${WITH_SUPERCOLLIDER}" != "1" ]; then echo "sc: not requested"; exit 0; fi; \
    apt-get update && apt-get install -y --no-install-recommends \
      supercollider supercollider-language supercollider-server \
    && rm -rf /var/lib/apt/lists/*

# --- Surge XT ----------------------------------------------------------------
# Synth patches for SFX and instruments. Packaged name needs confirming on the
# first build with --with surge; if apt has no surge-xt, this fails loudly
# rather than quietly producing an image without it.
RUN set -euo pipefail; \
    if [ "${WITH_SURGE}" != "1" ]; then echo "surge: not requested"; exit 0; fi; \
    apt-get update && apt-get install -y --no-install-recommends surge-xt \
    && rm -rf /var/lib/apt/lists/*

# --- Material Maker ----------------------------------------------------------
# Procedural PBR material graphs, with a CLI exporter.
ARG MATERIALMAKER_VERSION
RUN set -euo pipefail; \
    if [ "${WITH_MATERIALMAKER}" != "1" ]; then echo "mm: not requested"; exit 0; fi; \
    apt-get update && apt-get install -y --no-install-recommends \
      libegl1 libgl1 libxcursor1 libxi6 libxinerama1 libxrandr2 \
    && rm -rf /var/lib/apt/lists/*; \
    under=$(printf '%s' "${MATERIALMAKER_VERSION}" | tr '.' '_'); \
    curl -fsSL -o /tmp/mm.tar.gz \
      "https://github.com/RodZill4/material-maker/releases/download/${MATERIALMAKER_VERSION}/material_maker_${under}_linux.tar.gz"; \
    joinery-verify-download /tmp/mm.tar.gz materialmaker; \
    mkdir -p /opt/material-maker; \
    tar -xzf /tmp/mm.tar.gz -C /opt/material-maker --strip-components=1; \
    binary=$(find /opt/material-maker -maxdepth 2 -type f -name '*.x86_64' | head -1); \
    test -n "${binary}" || { echo "material-maker: no binary in archive" >&2; exit 1; }; \
    chmod +x "${binary}"; \
    ln -s "${binary}" /usr/local/bin/material-maker; \
    rm /tmp/mm.tar.gz

# --- GIMP / Inkscape ---------------------------------------------------------
RUN set -euo pipefail; \
    if [ "${WITH_GIMP}" != "1" ]; then echo "gimp: not requested"; exit 0; fi; \
    apt-get update && apt-get install -y --no-install-recommends gimp \
    && rm -rf /var/lib/apt/lists/*
RUN set -euo pipefail; \
    if [ "${WITH_INKSCAPE}" != "1" ]; then echo "inkscape: not requested"; exit 0; fi; \
    apt-get update && apt-get install -y --no-install-recommends inkscape \
    && rm -rf /var/lib/apt/lists/*

# Aseprite is deliberately absent: it is paid and source-available, so the user
# mounts their own licensed build. Krita covers the free pixel path.

COPY entrypoint.sh /usr/local/bin/joinery-sandbox
RUN chmod +x /usr/local/bin/joinery-sandbox

# Unprivileged worker: nothing here needs root, and tool scripts are untrusted.
RUN useradd --create-home --shell /bin/bash joiner
USER joiner
WORKDIR /work

ENTRYPOINT ["/usr/local/bin/joinery-sandbox"]
CMD ["bash"]
