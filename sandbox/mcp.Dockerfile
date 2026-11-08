# Protocol servers and their child applications share an offline namespace.
ARG JOINERY_PLATFORM=linux/amd64
ARG JOINERY_BASE=joinery-sandbox-runtime
FROM --platform=${JOINERY_PLATFORM} ${JOINERY_BASE}
USER root
RUN apt-install nodejs
USER joiner
WORKDIR /work
