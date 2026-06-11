# mix-ingest — bundles Python, ffmpeg, Chromium+xvfb (1001tracklists/Turnstile),
# and deno (yt-dlp's JS runtime). Large image by nature (~2 GB); Chromium dominates.

FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy

WORKDIR /opt/mix
COPY pyproject.toml uv.lock ./
COPY mixingest/ mixingest/
COPY app/ app/
COPY README.md ./
RUN uv sync --frozen --no-dev


FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg xvfb xauth ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

# deno — yt-dlp's preferred JS runtime for YouTube signature solving
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version

WORKDIR /opt/mix
COPY --from=build /opt/mix /opt/mix
ENV PATH=/opt/mix/.venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Chromium version comes from the playwright pinned in uv.lock (use the venv's CLI).
# World-readable so the container also works under a non-root --user.
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /ms-playwright

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD ["python", "-c", "import os,httpx; httpx.get(f\"http://127.0.0.1:{os.getenv('BIND_PORT','8080')}/healthz\").raise_for_status()"]

# Runs as root by default so filed output can be chowned to OWNER_UID/OWNER_GID
# (PUID/PGID accepted as aliases). With --user, the chown is a harmless no-op.
ENTRYPOINT ["docker-entrypoint.sh"]
