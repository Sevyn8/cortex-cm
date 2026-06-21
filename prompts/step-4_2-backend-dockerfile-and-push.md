# Prompt — Steps 4.2 + 4.3: Backend Dockerfile + push to Artifact Registry

> Paste this entire block into a fresh Claude Code session when starting Step 4.2.
> Combines original Steps 4.2 (Dockerfile) and 4.3 (push). They're naturally one
> session; the BUILD_PLAN README anticipates this combination.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report.
2. Read `CLAUDE.md` fully — focus on Python/uv conventions, environment variables, "Stack" section.
3. Read `docs/architecture.md` — focus on "Deployment topology", "Per-region stack", and Appendix A.2 (why Cloud SQL Auth Proxy runs as a sidecar — i.e., NOT in the app image).
4. Read `BUILD_PLAN.md` Steps 4.2 and 4.3 in full.
5. Read this prompt fully and confirm scope.

---

## Step ID and intent

**Steps 4.2 + 4.3** — Production-shaped Docker image for the admin backend, pushed to Artifact Registry.

This is the artifact that the GKE deployment in Step 4.4 will pull, and that the GKE one-shot Job in (re-ordered) Step 4.1 will use to run Alembic against Cloud SQL.

Two deliverables:

1. `Dockerfile` (multi-stage) and `.dockerignore` at repo root.
2. Image pushed to Artifact Registry under the URL output by Terraform.

This is a CLAUDE_CODE step. The user runs `docker push` interactively (it needs their gcloud auth); Claude Code writes the Dockerfile, builds it, smoke-tests it locally, and produces the exact `docker tag` / `docker push` commands.

---

## Scope in

### Prerequisites

- Terraform `envs/dev` has been applied. The image registry URL is available via:
  ```bash
  terraform -chdir=../terraform/envs/dev output -raw artifact_registry_url
  ```
  Expected shape: `asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images`
- `uv.lock` is committed and `pyproject.toml` reflects current deps.

### File 1: `Dockerfile` at repo root

Multi-stage build. Stage 1 builds the venv with uv; stage 2 copies the venv and source. Final image runs uvicorn.

```dockerfile
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
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

# Non-root user. Cloud SQL Auth Proxy sidecar runs as its own UID; this is just for the app.
RUN groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --create-home --shell /usr/sbin/nologin app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src"

WORKDIR /app

# Bring the venv from the builder.
COPY --from=builder --chown=app:app /opt/venv /opt/venv

# Application source. Migrations are bundled because the image is also used
# as the alembic-runner in the schema bring-up step (re-ordered Step 4.1).
COPY --chown=app:app src/ /app/src/
COPY --chown=app:app migrations/ /app/migrations/
COPY --chown=app:app alembic.ini /app/alembic.ini

USER app

EXPOSE 8000

# Default command: run the FastAPI app. The GKE Job overrides CMD with
# `alembic upgrade head` for the schema bring-up.
CMD ["uvicorn", "admin_backend.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--no-server-header", \
     "--log-config", "/app/src/admin_backend/logging.json"]
```

Adjust the `CMD` if the actual main app entry path differs from `admin_backend.main:app` — verify by inspecting `src/admin_backend/main.py`. If `--log-config` file doesn't exist (Step 7.2.1 hasn't landed), drop that flag.

### File 2: `.dockerignore` at repo root

Aggressive — keep the image small and avoid leaking secrets:

```
# Version control
.git
.gitignore

# Python
__pycache__
*.pyc
*.pyo
*.pyd
.pytest_cache
.mypy_cache
.ruff_cache
.coverage
htmlcov
.tox

# Virtual envs (never copied — built in image)
.venv
venv
env

# Tests (not needed at runtime)
tests/

# Local secrets and keys
.env
.env.*
!.env.example
keys/

# Local DB data
data/
db/data/

# Docs and dev tooling
docs/
prompts/
reports/
*.md
!README.md

# Editor
.vscode
.idea
*.swp
.DS_Store

# Docker
Dockerfile
.dockerignore
docker-compose.yml

# CI artefacts
.github/
```

Note: `.env.example` and `README.md` are explicitly kept (the bang-prefix re-includes them).

### Steps to perform

1. Write the Dockerfile and `.dockerignore`.
2. Build locally:
   ```bash
   docker build -t admin-backend:dev .
   ```
   First build: 60-120s. Subsequent builds with cache: <10s.
3. Smoke test locally with the local Postgres container running:
   ```bash
   docker run --rm \
     --network=host \
     -e DATABASE_URL="postgresql+psycopg://user_admin_backend:password_admin_backend@localhost:5432/ithina_platform_db" \
     -e DB_SCHEMA="core" \
     -e AUTH_CLIENT_MODE="STUB" \
     -e JWT_ISSUER="https://stub-issuer.local/" \
     -e JWT_AUDIENCE="https://api.ithina.com" \
     -e JWT_PUBLIC_KEY_PATH="/app/keys/jwt_public.pem" \
     -e APP_REGION="LOCAL" \
     -e ENVIRONMENT="development" \
     -v "$(pwd)/keys:/app/keys:ro" \
     admin-backend:dev
   ```
   In another terminal: `curl http://localhost:8000/v1/health` should return 200.
   Stop the container.
4. Tag for Artifact Registry:
   ```bash
   AR_URL=$(terraform -chdir=../terraform/envs/dev output -raw artifact_registry_url)
   docker tag admin-backend:dev "${AR_URL}/admin-backend:v0.1.0"
   docker tag admin-backend:dev "${AR_URL}/admin-backend:latest"
   ```
5. Configure docker auth (one-time per workstation):
   ```bash
   gcloud auth configure-docker asia-south1-docker.pkg.dev
   ```
6. Push:
   ```bash
   docker push "${AR_URL}/admin-backend:v0.1.0"
   docker push "${AR_URL}/admin-backend:latest"
   ```
7. Verify:
   ```bash
   gcloud artifacts docker images list "${AR_URL}/admin-backend" --project=ithina-retail-admin
   ```

---

## Scope out

- **Cloud SQL Auth Proxy in the image.** It runs as a sidecar in Kubernetes, not in this image. Per architecture Appendix A.2.
- **Multi-arch builds (amd64+arm64).** Single-arch (linux/amd64) is fine for v0; GKE Autopilot nodes are amd64.
- **Image-level secrets baked in.** All secrets come from Secret Manager via env at runtime (Step 4.4).
- **Health-check / probe configuration.** That lives in the Kubernetes manifest (Step 4.4), not the Dockerfile.
- **CI/CD wiring.** Manual `docker push` for v0; Cloud Build / GitHub Actions is post-launch.

---

## Implementation hints

- The Dockerfile's `COPY --from=ghcr.io/astral-sh/uv:0.5.4` pin matters — use a specific uv version for reproducibility. Bump it explicitly when needed.
- `UV_COMPILE_BYTECODE=1` precompiles `.pyc` files at build time so cold start is faster on Cloud Run / GKE.
- The `--mount=type=cache` flags on `RUN` need BuildKit. Modern Docker Desktop / docker-buildx use BuildKit by default. If `docker build` complains, either set `DOCKER_BUILDKIT=1` or remove the cache mounts (slower rebuilds, still works).
- The GKE pod will mount `keys/` from a Kubernetes Secret (Step 4.4) — the image does NOT contain JWT keys.
- If your `pyproject.toml` doesn't have `psycopg[binary]>=3.2`, the image won't have a working psycopg in the venv. Verify against the existing pyproject.toml before building.

---

## Acceptance criteria

- `docker build -t admin-backend:dev .` succeeds with no warnings other than the standard "use BuildKit" notice.
- Final image size is under 350 MB (`docker images admin-backend:dev`).
- Smoke test (step 3 above) returns 200 from `/v1/health`.
- Image pushed to Artifact Registry under both `:v0.1.0` and `:latest` tags.
- `gcloud artifacts docker images list` shows both tags.
- The image runs as non-root user (`docker inspect admin-backend:dev | grep User` shows `app` or `1000`).
- mypy strict still clean (no source changes should affect this).

---

## Stop and ask if

- `pyproject.toml` is missing `uvicorn[standard]` or `psycopg[binary]`.
- The main app entry point isn't `admin_backend.main:app`.
- Terraform outputs aren't accessible (envs/dev hasn't been applied).
- Smoke test fails for reasons other than env-var typos — could be a real app issue.

---

## What to report at end (5-bundle convention)

1. **Code/configs:** Dockerfile (line count), .dockerignore (line count). Image size and digest.
2. **CLAUDE.md updates:** Note that the runtime container is non-root user `app` (uid 1000) — if there's a "Container conventions" section, add this. Otherwise skip.
3. **BUILD_PLAN.md updates:** Steps 4.2 and 4.3 status flipped to DONE.
4. **architecture.md updates:** No change expected. Appendix A.2 already covers sidecar topology.
5. **Prompt file:** `prompts/step-4_2-backend-dockerfile-and-push.md` confirmed in commit set.

Plus: image digest, push output (last line), `docker history` truncated, smoke test output.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
