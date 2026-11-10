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

# Every downloaded artifact is x86_64: Blender publishes no Linux arm64 build,
# and neither does Material Maker, so the image is amd64 and so is CI. On Apple
# Silicon it therefore runs emulated, which is fine for reproducible rebuilds
# but slow for interactive work; use host tools (`joinery doctor`) for that.
ARG JOINERY_PLATFORM=linux/amd64
FROM --platform=${JOINERY_PLATFORM} debian:trixie-slim

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8

# Make package fetching survive a slow or emulated network. The image is amd64
# even on an arm64 host, and under emulation a long run of fetches starts
# dropping connections mid-layer. Prefer IPv4, do not pipeline, serialise per
# host, and retry; apt-install wraps the whole step in a retry loop too.
RUN printf 'Acquire::Retries "8";\nAcquire::http::Timeout "60";\nAcquire::http::Pipeline-Depth "0";\nAcquire::Queue-Mode "access";\nAcquire::ForceIPv4 "true";\n' \
      >/etc/apt/apt.conf.d/80-joinery-fetch
COPY apt-install.sh /usr/local/bin/apt-install
RUN chmod +x /usr/local/bin/apt-install

# Which tools to include. Default is the minimum that can run a Godot project
# and render audio and video; the heavy creative tools are opt-in.
ARG WITH_BLENDER=0
ARG WITH_KRITA=0
ARG WITH_SUPERCOLLIDER=0
ARG WITH_MATERIALMAKER=0
ARG WITH_GIMP=0
ARG WITH_INKSCAPE=0

# Core runtime. xvfb because Blender, Krita and Material Maker are GUI programs
# we drive headless; ffmpeg for capture and audio conversion; fluidsynth plus
# the Fluid (R3) GM SoundFont to render MIDI, since fluidsynth alone has no
# instruments to play. That SoundFont is MIT-licensed, which the asset gate
# records for anything built from it.
RUN apt-install \
      ca-certificates \
      curl \
      ffmpeg \
      fluid-soundfont-gm \
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
      xz-utils

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
    apt-install libegl1 libgl1 libglu1-mesa libsm6 libxfixes3 libxi6 \
      libxkbcommon0 libxrender1 libxxf86vm1; \
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
    mkdir -p /opt/blender-extensions/system; \
    cp -r /tmp/mpfb2/src/mpfb /opt/blender-extensions/system/mpfb; \
    rm -rf /tmp/mpfb2.tar.gz /tmp/mpfb2; \
    blender --version; \
    BLENDER_SYSTEM_EXTENSIONS=/opt/blender-extensions blender --background \
      --factory-startup --python-exit-code 1 --python-expr \
      "import addon_utils; assert addon_utils.enable('bl_ext.system.mpfb', default_set=True)"

# MPFB2 2.x is a Blender extension: Blender 4.2+ ignores it under scripts/addons,
# so it lives in the system extension repository and loads as bl_ext.system.mpfb,
# also under --factory-startup (recipe replay).
ENV BLENDER_SYSTEM_EXTENSIONS=/opt/blender-extensions

# --- Krita -------------------------------------------------------------------
# Layered painting. Debian packages it, so apt verifies it.
RUN set -euo pipefail; \
    if [ "${WITH_KRITA}" != "1" ]; then echo "krita: not requested"; exit 0; fi; \
    apt-install krita

# --- SuperCollider -----------------------------------------------------------
# sclang and scsynth, for sound design and synthesis. This is the sandbox's
# synth: it is packaged, drives headlessly from a script, and is what the
# catalog's audio MCP targets.
RUN set -euo pipefail; \
    if [ "${WITH_SUPERCOLLIDER}" != "1" ]; then echo "sc: not requested"; exit 0; fi; \
    apt-install supercollider supercollider-language supercollider-server

# Surge XT is installed in the creative worker layer (creative.Dockerfile),
# from its pinned stable release in surge-synthesizer/releases-xt.

# --- Material Maker ----------------------------------------------------------
# Procedural PBR material graphs, with a CLI exporter.
ARG MATERIALMAKER_VERSION
RUN set -euo pipefail; \
    if [ "${WITH_MATERIALMAKER}" != "1" ]; then echo "mm: not requested"; exit 0; fi; \
    apt-install libegl1 libgl1 libxcursor1 libxi6 libxinerama1 libxrandr2; \
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
    apt-install gimp
RUN set -euo pipefail; \
    if [ "${WITH_INKSCAPE}" != "1" ]; then echo "inkscape: not requested"; exit 0; fi; \
    apt-install inkscape

# Aseprite is deliberately absent: its licence forbids redistribution, so it is
# not a catalog tool (docs/adr/0025-tool-removals.md). Krita covers pixel art.

COPY entrypoint.sh /usr/local/bin/joinery-sandbox
RUN chmod +x /usr/local/bin/joinery-sandbox

# Unprivileged worker: nothing here needs root, and tool scripts are untrusted.
RUN useradd --create-home --shell /bin/bash joiner
USER joiner

# Point Godot at Blender so it can import .blend natively. Godot stores this as
# an editor setting and refuses to configure it in headless mode, so bake it in:
# without this, importing a .blend fails with "Blender path is invalid or not
# set" and no amount of command-line argument fixes it. The setting wants the
# executable, not its directory, which the error message does not say.
RUN set -eu; \
    if [ "${WITH_BLENDER}" != "1" ]; then echo "godot/blender: nothing to link"; exit 0; fi; \
    mkdir -p /tmp/bootstrap; \
    printf 'config_version=5\n\n[application]\nconfig/name="bootstrap"\n' \
      >/tmp/bootstrap/project.godot; \
    godot --headless --path /tmp/bootstrap --editor --quit >/dev/null 2>&1 || true; \
    settings=$(find "${HOME}/.config/godot" -name 'editor_settings-*.tres' | head -1); \
    test -n "${settings}" || { echo "godot wrote no editor settings" >&2; exit 1; }; \
    sed -i 's|filesystem/import/blender/blender_path = .*|filesystem/import/blender/blender_path = "/opt/blender/blender"|' "${settings}"; \
    grep -q '"/opt/blender/blender"' "${settings}" \
      || { echo "failed to set blender_path" >&2; exit 1; }; \
    rm -rf /tmp/bootstrap

WORKDIR /work

ENTRYPOINT ["/usr/local/bin/joinery-sandbox"]
CMD ["bash"]
