ARG JOINERY_PLATFORM=linux/amd64
FROM --platform=${JOINERY_PLATFORM} joinery-mcp-worker
USER root
RUN apt-install python3-pyqt5 python3-pyqt5.qtsvg libvulkan1 mesa-vulkan-drivers
RUN if [ -f /opt/material-maker/material_maker.x86_64 ]; then cp /usr/local/bin/godot /opt/material-maker/joinery-godot; fi
COPY krita_worker.py /usr/local/bin/joinery-krita
RUN chmod +x /usr/local/bin/joinery-krita
USER joiner
