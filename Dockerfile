FROM node:24-bookworm-slim AS frontend
WORKDIR /build/web/frontend
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci
COPY web/frontend/ ./
RUN npm run build

# Pin the official standalone Linux x86_64 Codex binary.  This deliberately
# avoids the npm launcher, whose optional @openai/codex-linux-x64 package may
# be omitted in Docker/npm installs and then fail at runtime.
FROM debian:bookworm-slim AS codex-cli
ARG CODEX_CLI_VERSION=0.154.0
ARG CODEX_TARGET=x86_64-unknown-linux-musl
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl tar \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/codex /tmp/codex \
    && curl -fL --retry 4 --retry-delay 2 \
      "https://github.com/openai/codex/releases/download/rust-v${CODEX_CLI_VERSION}/codex-${CODEX_TARGET}.tar.gz" \
      -o /tmp/codex/codex.tar.gz \
    && tar -xzf /tmp/codex/codex.tar.gz -C /tmp/codex \
    && install -m 0755 "/tmp/codex/codex-${CODEX_TARGET}" /opt/codex/codex \
    && /opt/codex/codex --version \
    && /opt/codex/codex app-server --help >/dev/null \
    && rm -rf /tmp/codex

FROM python:3.11-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app/web/backend \
    NEXIS_DATA_ROOT=/data \
    CODEX_APP_SERVER_BIN=/usr/local/bin/codex \
    COPILOT_CLI_EXTRACT_DIR=/opt/github-copilot-sdk

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates libgl1 libglu1-mesa libx11-6 libxext6 libxrender1 \
      libxcb1 libxkbcommon-x11-0 libfontconfig1 tini prusa-slicer \
    && command -v prusa-slicer >/dev/null \
    && dpkg-query -W prusa-slicer >/dev/null \
    && rm -rf /var/lib/apt/lists/*

COPY --from=codex-cli /opt/codex/codex /usr/local/bin/codex

WORKDIR /app
COPY requirements-web.txt requirements_accelerator.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt -r requirements_accelerator.txt \
    && python -m copilot download-runtime \
    && chmod -R a+rX /opt/github-copilot-sdk

COPY src/ ./src/
COPY vendor/ ./vendor/
COPY tools/ ./tools/
COPY scripts/ ./scripts/
COPY web/backend/ ./web/backend/
COPY --from=frontend /build/web/frontend/dist ./web/frontend/dist

# Stable numeric UID/GID prevents Docker-volume ownership changes after package updates.
ARG CADIA_UID=10001
ARG CADIA_GID=10001
RUN addgroup --gid ${CADIA_GID} nexis \
    && adduser --uid ${CADIA_UID} --gid ${CADIA_GID} --disabled-password --gecos "" --home /home/nexis nexis \
    && mkdir -p /data /data/projects /data/codex-users \
    && chown -R ${CADIA_UID}:${CADIA_GID} /app /data /home/nexis

USER nexis
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "standalonecad_web.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
