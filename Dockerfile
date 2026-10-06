# syntax=docker/dockerfile:1.7
# =============================================================================
# The Hestia gateway image: dashboard + API + engine in one process.
#
#   docker build -t hestia-gateway .
#
# Stage 1 builds the dashboard (React) with Node; stage 2 is a slim Python
# runtime with the locked dependencies only (uv.lock, hashes checked), running
# as an unprivileged user. Node, compilers and dev tools never reach the
# final image.
# =============================================================================

# ── 1. Dashboard ──────────────────────────────────────────────────────────────
FROM node:22-alpine AS dashboard
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# vite.config.ts writes the build to ../backend/src/hestia/static
RUN npm run build

# ── 2. Runtime ────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Dependencies first (cached until uv.lock changes), exactly as locked.
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# The application itself, then the dashboard built in stage 1.
COPY backend/src ./src
COPY --from=dashboard /src/backend/src/hestia/static ./src/hestia/static

# Unprivileged user; /data holds the database, journal and caches (a volume).
RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin hestia \
    && mkdir -p /data && chown hestia:hestia /data \
    && rm /usr/local/bin/uv
USER hestia

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src \
    HESTIA_DATA_DIR=/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status != 200)"]

# Behind the reverse proxy (Caddy) on a private network: trust its
# X-Forwarded-For so login throttling sees real client addresses.
CMD ["uvicorn", "--factory", "hestia.main:create_app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--no-server-header"]
