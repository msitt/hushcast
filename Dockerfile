# check=skip=JSONArgsRecommended
# Shell-form CMD is deliberate (${HUSHCAST_PORT} expansion; exec handles signals)

# --- stage 1: build the SPA ---
FROM node:24-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# vite.config.ts reads the app version from ../backend/hushcast/__init__.py
COPY backend/hushcast/__init__.py /backend/hushcast/__init__.py
RUN npm run build

# --- stage 2: install python deps ---
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS deps
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY backend/ ./backend/
RUN uv sync --frozen --no-dev

# --- stage 3: runtime ---
FROM python:3.14-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 hushcast \
    && useradd -u 1000 -g 1000 -M -s /usr/sbin/nologin hushcast \
    && mkdir -p /config /data \
    && chown hushcast:hushcast /config /data
WORKDIR /app
COPY --from=deps --chown=hushcast:hushcast /app/.venv ./.venv
COPY --chown=hushcast:hushcast backend/ ./backend/
COPY --from=frontend --chown=hushcast:hushcast /build/dist ./frontend/dist
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" \
    HUSHCAST_CONFIG_DIR=/config \
    HUSHCAST_DATA_DIR=/data \
    HUSHCAST_PORT=4874 \
    PUID=1000 \
    PGID=1000
# /config: SQLite DB + settings
# /data: audio, transcripts, scratch
# Runs as root until the entrypoint drops to PUID:PGID (default 1000:1000).
VOLUME /config /data
EXPOSE 4874
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD curl -fsS "http://127.0.0.1:${HUSHCAST_PORT:-4874}/healthz" || exit 1
ENTRYPOINT ["docker-entrypoint.sh"]
CMD exec uvicorn hushcast.main:app --host 0.0.0.0 --port "${HUSHCAST_PORT:-4874}"
