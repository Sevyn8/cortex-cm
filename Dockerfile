# syntax=docker/dockerfile:1.7

# ---------- Stage 1: builder ----------
FROM python:3.12-slim AS builder

# uv installs deps fast and respects uv.lock for reproducibility.
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

# Copy only dep manifests first to maximise layer cache.
COPY pyproject.toml uv.lock ./

# Install runtime deps only (no dev group).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Copy source and install the project itself (puts the package in /opt/venv).
COPY src/ ./src/
COPY README.md ./README.md
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

# Non-root user. Cloud SQL Auth Proxy sidecar (prod only) runs as its own UID;
# this is just for the app.
RUN groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --create-home --shell /usr/sbin/nologin app

# SERVICE_VERSION is baked into the image at build time so /api/v1/health
# reports the image tag without any deploy-time env-var setup. Pass via
# `docker build --build-arg SERVICE_VERSION=v0.1.4 ...`. Default `dev`
# only fires when the build script forgets to pass it (and is loud enough
# in /api/v1/health to be noticed). Cloud Run / GKE can still override
# at deploy time by setting the SERVICE_VERSION env var, which takes
# precedence over the baked default.
ARG SERVICE_VERSION=dev

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src" \
    SERVICE_VERSION=${SERVICE_VERSION}

WORKDIR /app

# Bring the venv from the builder.
COPY --from=builder --chown=app:app /opt/venv /opt/venv

# Application source. Migrations are bundled because the image is also used
# as the alembic-runner in the schema bring-up step (Step 4.1).
COPY --chown=app:app src/ /app/src/
COPY --chown=app:app migrations/ /app/migrations/
COPY --chown=app:app alembic.ini /app/alembic.ini

# Cloud Run Job verification scripts (Step 4.1). Selective copy: only the
# scripts intended to run inside the image. Dev-only tooling (check_setup.sh,
# excel_to_seed_sql.py, apply_seeds.sh) stays excluded via .dockerignore.
COPY --chown=app:app scripts/verify_cloud_schema.py /app/scripts/verify_cloud_schema.py
COPY --chown=app:app scripts/smoke_test.py /app/scripts/smoke_test.py

USER app

EXPOSE 8000

# Default command: run the FastAPI app. Cloud Run / GKE Job overrides CMD with
# `alembic upgrade head` for schema bring-up.
# --log-config will be added at Step 7.2.1 when the JSON log config file lands.
CMD ["uvicorn", "admin_backend.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--no-server-header"]
