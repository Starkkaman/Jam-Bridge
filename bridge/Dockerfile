# Home Assistant base image with Python (Alpine). Pinned; current tags are listed in
# https://github.com/home-assistant/docker-base (README). The Supervisor no longer passes BUILD_FROM by default
# (since 2026.04), so it has a default here and build.yaml is not used.
ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.14-alpine3.24
FROM ${BUILD_FROM}

# Python stdlib plus one pinned package: aiohttp (server, SSE, n8n client, crew_hub websocket client). PyPI has
# cp314 musllinux wheels for amd64 and aarch64. No runtime downloads. tzdata: local time for the nightly prune.
RUN apk add --no-cache tzdata \
    && pip install --no-cache-dir aiohttp==3.14.5

COPY app/ /app/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
