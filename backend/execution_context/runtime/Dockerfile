FROM node:22-bookworm-slim
ARG CODEX_VERSION=0.166.0
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global @openai/codex@${CODEX_VERSION} \
    && npm cache clean --force
COPY proxy.js relay.js start.sh /opt/shadow/
RUN chmod 755 /opt/shadow/start.sh && mkdir -p /workspace /run/codex
USER node
ENV CODEX_HOME=/run/codex HTTPS_PROXY=http://127.0.0.1:8080 HTTP_PROXY=http://127.0.0.1:8080 NO_PROXY=
WORKDIR /workspace
ENTRYPOINT ["/opt/shadow/start.sh"]
