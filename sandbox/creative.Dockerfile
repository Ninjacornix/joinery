ARG JOINERY_PLATFORM=linux/amd64
FROM --platform=${JOINERY_PLATFORM} joinery-mcp-worker
USER root
RUN apt-install python3-pyqt5 python3-pyqt5.qtsvg libvulkan1 mesa-vulkan-drivers
RUN if [ -f /opt/material-maker/material_maker.x86_64 ]; then cp /usr/local/bin/godot /opt/material-maker/joinery-godot; fi
COPY krita_worker.py /usr/local/bin/joinery-krita
RUN chmod +x /usr/local/bin/joinery-krita

# --- Surge XT ----------------------------------------------------------------
# Stable release from surge-synthesizer/releases-xt, hash-checked like every
# direct download. Rendered offline through its VST3 by Pedalboard; nothing is
# played and nothing reaches the network at run time.
ARG SURGE_VERSION=1.3.4
COPY checksums.txt /opt/joinery/checksums.txt
COPY surge-requirements.txt /opt/joinery/surge-requirements.txt
RUN set -euo pipefail; \
    curl -fsSL -o /tmp/surge.deb \
      "https://github.com/surge-synthesizer/releases-xt/releases/download/${SURGE_VERSION}/surge-xt-linux-x64-${SURGE_VERSION}.deb"; \
    joinery-verify-download /tmp/surge.deb surge; \
    apt-install /tmp/surge.deb; \
    rm /tmp/surge.deb; \
    test -d "/usr/lib/vst3/Surge XT.vst3"
RUN --mount=type=cache,target=/root/.cache/pip python3 -m venv /opt/surge-python && \
    /opt/surge-python/bin/pip install --no-deps --timeout 120 --retries 8 \
      --require-hashes -r /opt/joinery/surge-requirements.txt
COPY surge_worker.py /usr/local/bin/joinery-surge
RUN chmod +x /usr/local/bin/joinery-surge
USER joiner
