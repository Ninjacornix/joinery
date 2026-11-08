# Test-only application image. Production creative worker images are unchanged.
FROM joinery-creative-worker
USER root
ARG AUDACITY_VERSION=3.7.3+dfsg-1
RUN apt-install "audacity=${AUDACITY_VERSION}"
COPY sdk1/requirements.txt /opt/joinery-mcp-test-requirements.txt
RUN python3 -m venv /opt/joinery-mcp-test-python && \
    /opt/joinery-mcp-test-python/bin/pip install --no-cache-dir --require-hashes \
      -r /opt/joinery-mcp-test-requirements.txt
USER joiner
WORKDIR /work
