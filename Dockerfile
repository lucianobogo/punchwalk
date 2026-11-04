# syntax=docker/dockerfile:1.7
# Punchwalk: one image, one process, one URL (API + media + the built PWA).
#
#   docker build -t punchwalk .                      # make docker (the runtime image, the last stage)
#   docker build --target test -t punchwalk:test .   # runs the ingest / PDF / API tests inside the image
#   docker run --rm -p 8010:8000 -v punchwalk-local:/data punchwalk
#
# Stages: web (node: pnpm build of web/ -> punchwalk/static) -> base (python + ffmpeg + WeasyPrint libs +
# locked deps + code) -> test (base + dev deps + tests, runs pytest) and runtime (base + non-root user).

# --------------------------------------------------------------------------- web build
FROM node:22-bookworm-slim AS web
WORKDIR /src
RUN corepack enable && corepack prepare pnpm@9.15.0 --activate
COPY web/package.json web/pnpm-lock.yaml web/
RUN pnpm -C web install --frozen-lockfile
COPY web/ web/
# The deployed app talks to the API: build with VITE_DATA=live explicitly (vite's own default is still
# mock until PLAN 5.8, and a mock build copies fixtures/mock_api into static/mock-api). The fixtures
# are only copied in so `--build-arg VITE_DATA=mock` (a fixtures-only demo build) still works.
COPY fixtures/mock_api/ fixtures/mock_api/
ARG VITE_DATA=live
RUN VITE_DATA=${VITE_DATA} pnpm -C web build \
 && test -f punchwalk/static/index.html \
 && if [ "${VITE_DATA}" = "live" ] && [ -e punchwalk/static/mock-api ]; then \
      echo "live build must not ship mock data (punchwalk/static/mock-api)" >&2; exit 1; fi

# --------------------------------------------------------------------------- optional folders
# demo/ (720p demo copies, PLAN 8.1) may not exist yet; this stage makes COPY of it always succeed.
FROM busybox:1.36 AS context
COPY . /ctx
RUN mkdir -p /ctx/demo /ctx/eval /ctx/fixtures/cache /ctx/fixtures/pipeline

# --------------------------------------------------------------------------- python base
# Debian 13 (trixie): ffmpeg 7.1, which has -display_rotation and the same audio/HDR behaviour the
# ingest tests expect (bookworm's ffmpeg 5.1 fails them).
FROM python:3.11-slim-trixie AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.11 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_NO_SYNC=1 \
    PATH=/app/.venv/bin:$PATH

# ffmpeg/ffprobe (Debian's build has libzimg for zscale HDR tonemapping); Pango/HarfBuzz(+subset)/Cairo + fonts
# for WeasyPrint; util-linux for setpriv in the entrypoint; tzdata for the America/New_York daily cap and re-seed.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 libffi8 libcairo2 \
      fonts-dejavu-core fontconfig \
      util-linux ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /usr/local/bin/

WORKDIR /app
# Dependencies first (cached unless the lockfile changes), then the project itself. uv's download
# cache lives in a BuildKit cache mount, so it isn't baked into the image (~475 MB).
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY punchwalk/ punchwalk/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
COPY --from=web /src/punchwalk/static/ punchwalk/static/
COPY scripts/ scripts/
COPY --from=context /ctx/fixtures/cache/ fixtures/cache/
COPY --from=context /ctx/fixtures/pipeline/ fixtures/pipeline/
COPY --from=context /ctx/eval/ eval/
COPY --from=context /ctx/demo/ demo/

ENV DATA_DIR=/data \
    PUNCHWALK_CACHE=readwrite \
    FIXTURE_CACHE_DIR=/app/fixtures/cache \
    PUNCHWALK_WORKER=1

# --------------------------------------------------------------------------- tests (docker build --target test)
FROM base AS test
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY tests/ tests/
COPY --from=context /ctx/fixtures/ fixtures/
RUN PUNCHWALK_CACHE=readonly python -m pytest -q -p no:cacheprovider \
      tests/test_ingest.py tests/test_color.py tests/test_report.py tests/test_api_core.py -rs

# --------------------------------------------------------------------------- runtime (default)
FROM base AS runtime
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin punch \
 && mkdir -p /data && chown punch:punch /data \
 && chmod +x /app/scripts/entrypoint.sh
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
# ONE process: the job cap (2 concurrent walks) and the DEMO_MODE upload limiter are per process.
# --no-proxy-headers: uvicorn would otherwise rewrite the client IP from X-Forwarded-For for loopback
# peers before guards.client_ip() runs (spoofable); Fly-Client-IP / TRUST_PROXY are handled there.
CMD ["uvicorn", "punchwalk.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "75", "--no-proxy-headers"]
