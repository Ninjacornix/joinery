ARG JOINERY_PLATFORM=linux/amd64
FROM --platform=${JOINERY_PLATFORM} joinery-creative-worker
USER root
# The analysis server drives the creative worker's Inkscape. A creative worker
# built without it (WITH_INKSCAPE=0) would make a worker whose every call fails,
# so the build stops here instead.
RUN test -x /usr/bin/inkscape || { echo "inkscape worker: the creative worker has no /usr/bin/inkscape; rebuild it with Inkscape" >&2; exit 1; }
RUN apt-install build-essential python3-dev pkg-config libcairo2-dev libgirepository-2.0-dev gir1.2-gtk-3.0
COPY inkscape-requirements.txt /opt/joinery/inkscape-requirements.txt
# The pinned upstream uv.lock deliberately overrides inkex's old lxml ceiling.
# Install that complete hashed resolution, preserving the upstream override.
RUN --mount=type=cache,target=/root/.cache/pip python3 -m venv /opt/inkscape-python && /opt/inkscape-python/bin/pip install --no-deps --timeout 120 --retries 8 --resume-retries 8 --require-hashes -r /opt/joinery/inkscape-requirements.txt
USER joiner
ENV PYTHONPATH=/opt/inkscape-mcp/src DO_NOT_TRACK=1 FASTMCP_TELEMETRY_ENABLED=false
