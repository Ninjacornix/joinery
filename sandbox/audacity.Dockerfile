# The Audacity worker: Audacity driven headless through mod-script-pipe.
#
# Built on the MCP worker, not the creative worker: Audacity scans audio
# plugins at startup and stops every script command behind an "Incompatible
# plugins" dialog when it meets the creative worker's Surge XT plugins. This
# image has no third-party plugins at all.
#
# Audacity is Debian trixie's own build (it ships mod-script-pipe). The exact
# .deb pair is downloaded and hash-checked against checksums.txt like every
# direct download; apt then installs its dependencies from the GPG-verified
# archive. Nothing reaches the network at run time (--network none).
#
# The pinned Audacity MCP runs from its pinned source (mounted read-only, like
# the Inkscape MCP) on the packages in audacity-requirements.txt, installed
# with --no-deps --require-hashes. Its manifest also names faster-whisper,
# which is never installed: the build fails if any speech or model package is
# importable, and policy blocks the transcription tools
# (docs/adr/0082-audacity-worker.md).
ARG JOINERY_PLATFORM=linux/amd64
FROM --platform=${JOINERY_PLATFORM} joinery-mcp-worker
USER root

# Must match versions.env (a test checks).
ARG AUDACITY_DEB_VERSION=3.7.3+dfsg-1
COPY checksums.txt /opt/joinery/checksums.txt
RUN set -euo pipefail; \
    base=https://deb.debian.org/debian/pool/main/a/audacity; \
    curl -fsSL --retry 3 -o /tmp/audacity.deb \
      "${base}/audacity_${AUDACITY_DEB_VERSION}_amd64.deb"; \
    joinery-verify-download /tmp/audacity.deb audacity; \
    curl -fsSL --retry 3 -o /tmp/audacity-data.deb \
      "${base}/audacity-data_${AUDACITY_DEB_VERSION}_all.deb"; \
    joinery-verify-download /tmp/audacity-data.deb audacitydata; \
    apt-install /tmp/audacity.deb /tmp/audacity-data.deb; \
    rm /tmp/audacity.deb /tmp/audacity-data.deb; \
    test "$(dpkg-query -W -f='${Version}' audacity)" = "${AUDACITY_DEB_VERSION}"; \
    find / -xdev -name 'mod-script-pipe.so' -path '*audacity*' | grep -q .

# Measure RMS (rms.ny) never registers in this build: Audacity re-checks it
# in a child `audacity --host` process on every start, and that child loads
# mod-script-pipe too and replaces the script pipes under the running
# Audacity. Divert it so dpkg still knows where it went. The level it
# measures is in Joinery's sound tools (loudness).
RUN mkdir -p /usr/share/audacity/plug-ins-diverted && \
    dpkg-divert --local --rename \
      --divert /usr/share/audacity/plug-ins-diverted/rms.ny \
      --add /usr/share/audacity/plug-ins/rms.ny && \
    mkdir -p /opt/joinery/audacity-plugins && \
    chown joiner:joiner /opt/joinery/audacity-plugins

# No third-party audio plugins: any found here would be scanned at startup.
RUN set -eu; \
    for d in /usr/lib/vst3 /usr/lib/lv2 /usr/lib/clap /usr/lib/vst /usr/lib/ladspa; do \
      if [ -d "$d" ] && [ -n "$(ls -A "$d")" ]; then echo "unexpected plugins in $d" >&2; exit 1; fi; \
    done

COPY audacity-requirements.txt /opt/joinery/audacity-requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip python3 -m venv /opt/audacity-python && \
    /opt/audacity-python/bin/pip install --no-deps --timeout 120 --retries 8 \
      --require-hashes -r /opt/joinery/audacity-requirements.txt
# Rule Zero: no speech or model package may be importable in this worker.
RUN set -eu; \
    for module in faster_whisper whisper ctranslate2 onnxruntime torch transformers huggingface_hub tokenizers; do \
      if /opt/audacity-python/bin/python -c "import ${module}" 2>/dev/null; then \
        echo "model package ${module} is installed in the Audacity worker" >&2; exit 1; \
      fi; \
    done; \
    /opt/audacity-python/bin/python -c "import mcp.server.fastmcp"

COPY audacity_worker.py /usr/local/bin/joinery-audacity
RUN chmod +x /usr/local/bin/joinery-audacity
USER joiner
# Record the plugin registry every run starts from (a fresh settings folder
# would otherwise trigger the plugin check above), then prove a run answers.
# Both runs start Xvfb; its lock file names a process ID, and a lock left in
# the image stops Xvfb in any container where that ID is in use, so nothing
# the runs leave in /tmp may stay in the layer.
RUN joinery-sandbox sh -c 'joinery-audacity --prime && joinery-audacity --check' && \
    find /tmp /var/tmp -mindepth 1 -maxdepth 1 -user joiner -exec rm -rf {} + && \
    test ! -e /tmp/.X99-lock
WORKDIR /work
ENV PYTHONPATH=/opt/audacity-mcp DO_NOT_TRACK=1
