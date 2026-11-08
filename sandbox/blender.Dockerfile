ARG JOINERY_PLATFORM=linux/amd64
FROM --platform=${JOINERY_PLATFORM} joinery-mcp-worker
USER root
COPY blender-requirements.txt /opt/joinery/blender-requirements.txt
RUN python3 -m venv /opt/blender-python && /opt/blender-python/bin/pip install --require-hashes -r /opt/joinery/blender-requirements.txt
COPY blender_worker.py blender_bootstrap.py /opt/joinery/
USER joiner
ENTRYPOINT ["/usr/local/bin/joinery-sandbox", "python3", "/opt/joinery/blender_worker.py"]
