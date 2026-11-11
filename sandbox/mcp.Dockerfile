# Protocol servers and their child applications share an offline namespace.
#
# The pinned Godot MCP server is baked in here, so it runs with no network next
# to this image's headless Godot and the user's machine needs no Node for it.
# Its commit archive is hash-checked against checksums.txt, its packages come
# from its own lockfile (npm ci checks every integrity hash and runs no install
# scripts), and only the built server and its runtime packages reach the image:
# the compiler and npm stay in the build stage.
ARG JOINERY_PLATFORM=linux/amd64
ARG JOINERY_BASE=joinery-sandbox-runtime

FROM --platform=${JOINERY_PLATFORM} ${JOINERY_BASE} AS godot-mcp
USER root
# Must match policy/preferred_tools.toml and versions.env (a test checks).
ARG GODOT_MCP_REF=88f0fbc69a08225d4fdec32f921209e127f52395
RUN apt-install nodejs npm
COPY checksums.txt /opt/joinery/checksums.txt
COPY verify.sh /usr/local/bin/joinery-verify-download
RUN set -euo pipefail; \
    chmod +x /usr/local/bin/joinery-verify-download; \
    curl -fsSL -o /tmp/godot-mcp.tar.gz \
      "https://github.com/tugcantopaloglu/godot-mcp/archive/${GODOT_MCP_REF}.tar.gz"; \
    joinery-verify-download /tmp/godot-mcp.tar.gz godotmcp; \
    mkdir -p /opt/godot-mcp; \
    tar -xzf /tmp/godot-mcp.tar.gz -C /opt/godot-mcp --strip-components=1; \
    rm /tmp/godot-mcp.tar.gz
WORKDIR /opt/godot-mcp
# Every locked package must name its registry tarball and its integrity hash,
# so npm ci can install nothing the lockfile does not pin.
RUN node -e ' \
      const lock = require("./package-lock.json"); \
      const bad = Object.entries(lock.packages || {}).filter(([path, entry]) => \
        path && !entry.link && !(entry.integrity && /^https:\/\/registry\.npmjs\.org\//.test(entry.resolved || ""))); \
      if (lock.lockfileVersion < 2 || bad.length) { \
        console.error("unpinned packages:", bad.map(([path]) => path).join(", ") || "old lockfile"); \
        process.exit(1); \
      }'
RUN set -euo pipefail; \
    npm ci --ignore-scripts --no-audit --no-fund; \
    npm run build; \
    npm prune --omit=dev --ignore-scripts --no-audit --no-fund; \
    test -f build/index.js; \
    test -f build/scripts/mcp_interaction_server.gd; \
    sha256sum package-lock.json | cut -d' ' -f1 >/tmp/lock; \
    printf '{"repo": "https://github.com/tugcantopaloglu/godot-mcp", "ref": "%s", "lock_sha256": "%s"}\n' \
      "${GODOT_MCP_REF}" "$(cat /tmp/lock)" >/opt/godot-mcp/joinery-pin.json; \
    rm -rf src tests scripts .github /root/.npm /tmp/lock

FROM --platform=${JOINERY_PLATFORM} ${JOINERY_BASE}
USER root
RUN apt-install nodejs
COPY --from=godot-mcp /opt/godot-mcp /opt/godot-mcp
RUN node --version && test -f /opt/godot-mcp/build/index.js
USER joiner
WORKDIR /work
