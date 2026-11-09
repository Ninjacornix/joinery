# Test-only application image. Production creative worker images are unchanged.
FROM joinery-creative-worker
USER root
ARG AUDACITY_VERSION=3.7.3+dfsg-1
RUN apt-install "audacity=${AUDACITY_VERSION}"
# Audacity scans plugins at startup and blocks every script command behind an
# "Incompatible plugins" dialog when it meets the worker's Surge XT plugins.
# This image only tests Audacity, so it drops them; real workers keep Surge.
RUN rm -rf /usr/lib/vst3/Surge* /usr/lib/lv2/Surge* /usr/lib/clap/Surge*
COPY sdk1/requirements.txt /opt/joinery-mcp-test-requirements.txt
RUN python3 -m venv /opt/joinery-mcp-test-python && \
    /opt/joinery-mcp-test-python/bin/pip install --no-cache-dir --require-hashes \
      -r /opt/joinery-mcp-test-requirements.txt
USER joiner
WORKDIR /work
