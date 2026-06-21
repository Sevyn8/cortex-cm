#!/usr/bin/env bash
# =============================================================================
# deploy-cloud-run.sh
# =============================================================================
# Builds the admin-backend Docker image, pushes it to Artifact Registry, and
# deploys a new revision to Cloud Run. Mirrors the manual sequence we walked
# through; intended to remove the "I forgot to deploy after pushing" hazard.
#
# USAGE:
#   ./deploy-cloud-run.sh                # auto-bumps to the next patch version
#   ./deploy-cloud-run.sh v0.2.0         # use a specific version
#   ./deploy-cloud-run.sh --skip-tests   # skip mypy/pytest (saves ~30s)
#   ./deploy-cloud-run.sh --migrate      # also run alembic migrations
#   ./deploy-cloud-run.sh --dry-run      # plan the work, do nothing
#
# Place this file at:    scripts/deploy-cloud-run.sh
# Make it executable:    chmod +x scripts/deploy-cloud-run.sh
# Run from project root: ./scripts/deploy-cloud-run.sh
# =============================================================================

# Strict mode: fail on any error (-e), unset variable (-u), or pipe failure.
set -euo pipefail

# -----------------------------------------------------------------------------
# Project constants. If any of these move, this is the only place to edit.
# -----------------------------------------------------------------------------
PROJECT_ID="ithina-retail-admin"
REGION="asia-south1"
SERVICE_NAME="admin-backend"
IMAGE_REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/admin-images/${SERVICE_NAME}"
ALEMBIC_JOB="admin-backend-alembic"

# Pretty terminal output (works without ANSI support too).
B=$'\033[1m'; G=$'\033[1;32m'; Y=$'\033[1;33m'; R=$'\033[1;31m'; N=$'\033[0m'
say()  { printf "%s==>%s %s\n" "$B" "$N" "$*"; }
ok()   { printf "%s✓%s  %s\n" "$G" "$N" "$*"; }
warn() { printf "%s!%s  %s\n" "$Y" "$N" "$*"; }
die()  { printf "%s✗%s  %s\n" "$R" "$N" "$*" >&2; exit 1; }

# -----------------------------------------------------------------------------
# Parse command-line flags. Defaults: run tests, don't migrate, do real work.
# -----------------------------------------------------------------------------
VERSION=""
SKIP_TESTS=0
RUN_MIGRATIONS=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --skip-tests) SKIP_TESTS=1 ;;
    --migrate)    RUN_MIGRATIONS=1 ;;
    --dry-run)    DRY_RUN=1 ;;
    -h|--help)    sed -n '2,20p' "$0"; exit 0 ;;
    v[0-9]*)      VERSION="$arg" ;;
    *)            die "unknown arg: $arg (see --help)" ;;
  esac
done

# -----------------------------------------------------------------------------
# Step 0 — Pre-flight. Confirm we're in the right directory, gcloud is logged
# in, and the right binaries are on PATH. Cheap; catches the most common
# "wrong terminal window" errors before we do anything expensive.
# -----------------------------------------------------------------------------
say "Pre-flight checks"
[ -f Dockerfile ]       || die "no Dockerfile here. cd to the admin-backend project root first"
[ -d migrations ]       || die "no migrations/ here. cd to the admin-backend project root first"
command -v gcloud >/dev/null || die "gcloud CLI not installed"
command -v docker >/dev/null || die "docker not installed"
command -v jq    >/dev/null || die "jq not installed (used for smoke check)"

ACCOUNT=$(gcloud config get-value account 2>/dev/null || echo "")
[ -n "$ACCOUNT" ] || die "not logged in. run: gcloud auth login"
ok "gcloud account: $ACCOUNT"

ACTIVE_PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")
if [ "$ACTIVE_PROJECT" != "$PROJECT_ID" ]; then
  warn "active gcloud project is '$ACTIVE_PROJECT', script targets '$PROJECT_ID'"
  warn "this is fine — the script passes --project=$PROJECT_ID everywhere — but worth noticing"
fi

# -----------------------------------------------------------------------------
# Step 1 — Determine the target version. If the user passed an explicit one
# (e.g. ./deploy.sh v0.2.0) we use that. Otherwise we list existing tags in
# Artifact Registry, find the highest v0.X.Y, and bump the patch number.
# -----------------------------------------------------------------------------
say "Resolving target version"
if [ -z "$VERSION" ]; then
  LATEST_VERSION=$(gcloud artifacts docker tags list "$IMAGE_REPO" \
    --project="$PROJECT_ID" --format='value(TAG)' 2>/dev/null \
    | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
    | sort -V | tail -1 || true)

  if [ -z "$LATEST_VERSION" ]; then
    VERSION="v0.1.0"
    warn "no existing semver tags found in registry; starting at $VERSION"
  else
    MAJOR=$(echo "$LATEST_VERSION" | cut -d. -f1)
    MINOR=$(echo "$LATEST_VERSION" | cut -d. -f2)
    PATCH=$(echo "$LATEST_VERSION" | cut -d. -f3)
    VERSION="${MAJOR}.${MINOR}.$((PATCH + 1))"
    ok "highest existing tag is $LATEST_VERSION; bumping to $VERSION"
  fi
else
  ok "user-supplied version: $VERSION"
fi

IMAGE="${IMAGE_REPO}:${VERSION}"

# Refuse to overwrite an existing tag. One tag = one digest = one immutable release.
if gcloud artifacts docker tags list "$IMAGE_REPO" \
     --project="$PROJECT_ID" --format='value(TAG)' 2>/dev/null \
   | grep -qx "$VERSION"; then
  die "tag '$VERSION' already exists in Artifact Registry. bump to a higher version or delete the existing tag manually"
fi

# -----------------------------------------------------------------------------
# Step 2 — Confirmation gate. Show what's currently live and what we'd
# replace it with. Last chance to back out.
# -----------------------------------------------------------------------------
say "Plan"
CURRENT_IMAGE=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" --project="$PROJECT_ID" \
  --format='value(spec.template.spec.containers[0].image)' 2>/dev/null \
  || echo "(service does not exist yet)")
ok "currently live: $CURRENT_IMAGE"
ok "deploying:      $IMAGE"
[ "$RUN_MIGRATIONS" = 1 ] && ok "will run alembic migrations" || true
[ "$SKIP_TESTS"     = 1 ] && warn "will skip mypy + pytest" || true

if [ "$DRY_RUN" = 1 ]; then
  say "DRY RUN complete — exiting before any side effects"
  exit 0
fi

read -r -p "Proceed? [y/N] " yn
[ "$yn" = "y" ] || [ "$yn" = "Y" ] || die "aborted by operator"

# -----------------------------------------------------------------------------
# Step 3 — Local sanity checks. mypy catches type errors, pytest catches
# logic errors. Skip these and you'll find out about regressions in
# production instead of on your laptop.
# -----------------------------------------------------------------------------
if [ "$SKIP_TESTS" = 0 ]; then
  say "Running local checks (mypy + pytest)"
  if [ -x ./scripts/check_setup.sh ]; then
    ./scripts/check_setup.sh || die "check_setup.sh failed — fix before deploying"
  fi
  uv run mypy --strict src/admin_backend || die "mypy failed — fix before deploying"
  uv run pytest -q                       || die "pytest failed — fix before deploying"
  ok "local checks passed"
else
  warn "skipping local tests (--skip-tests)"
fi

# -----------------------------------------------------------------------------
# Step 4 — Build the Docker image. Multi-stage Dockerfile produces a
# ~225 MB image with the venv + src/ baked in.
#
# --build-arg SERVICE_VERSION=$VERSION bakes the tag into the image's
# baseline ENV (per commit 715d298). Resolution order at runtime:
#   1. SERVICE_VERSION env var (set by Cloud Run in step 7) — wins
#   2. Dockerfile ENV (set here by --build-arg) — fallback
#   3. importlib.metadata.version("admin-backend") (pyproject.toml)
#   4. "0.0.0-dev" (last resort)
# Belt-and-suspenders: the image self-describes regardless of how it's deployed.
# -----------------------------------------------------------------------------
say "Building image $IMAGE"
docker build \
  --build-arg "SERVICE_VERSION=${VERSION}" \
  -t "$IMAGE" . \
  || die "docker build failed"
ok "image built locally (SERVICE_VERSION=$VERSION baked in)"

# -----------------------------------------------------------------------------
# Step 5 — Push to Artifact Registry. Cloud Run will pull from here on the
# next deploy. The configure-docker step is idempotent on re-run.
# -----------------------------------------------------------------------------
say "Pushing $IMAGE to Artifact Registry"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet >/dev/null 2>&1 || true
docker push "$IMAGE" || die "docker push failed"
ok "image pushed"

LATEST="${IMAGE_REPO}:latest"
docker tag  "$IMAGE" "$LATEST"
docker push "$LATEST" >/dev/null 2>&1 \
  && ok ":latest tag updated" \
  || warn ":latest tag move failed (non-fatal)"

# -----------------------------------------------------------------------------
# Step 6 — (Optional) Run alembic migrations against Cloud SQL via the
# Cloud Run Job. Only invoked with --migrate.
#
# IMPORTANT: migrations run BEFORE the app deploy. The app code might
# reference new columns/tables; if we deployed first and migrated second,
# requests in the gap window would 500.
# -----------------------------------------------------------------------------
if [ "$RUN_MIGRATIONS" = 1 ]; then
  say "Running alembic migrations via Cloud Run Job '$ALEMBIC_JOB'"
  gcloud run jobs update "$ALEMBIC_JOB" \
    --image="$IMAGE" \
    --region="$REGION" --project="$PROJECT_ID" --quiet \
    || die "couldn't update alembic job's image"

  gcloud run jobs execute "$ALEMBIC_JOB" \
    --region="$REGION" --project="$PROJECT_ID" --wait \
    || die "alembic job failed — schema may be in a half-migrated state. fix before deploying app code."
  ok "migrations applied"
fi

# -----------------------------------------------------------------------------
# Step 7 — Deploy the new revision. New revision gets 100% of traffic by
# default once it passes its startup health check.
#
# --update-env-vars is ADDITIVE: it only changes the named key and leaves
# the other 14 env vars (DATABASE_URL, JWT_ISSUER, AUTH_CLIENT_MODE, etc.)
# untouched.
#
# NEVER replace this with --set-env-vars — that REPLACES the whole env-var
# set, which would strip every other var Settings needs to construct, and
# the service would 500 on every request after first cache miss.
# (Verified empirically before commit 715d298: 14 env vars currently set.)
# -----------------------------------------------------------------------------
say "Deploying $IMAGE to Cloud Run service '$SERVICE_NAME'"
gcloud run deploy "$SERVICE_NAME" \
  --image="$IMAGE" \
  --update-env-vars="SERVICE_VERSION=${VERSION}" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --quiet \
  || die "Cloud Run deploy failed"
ok "new revision deployed and serving 100% traffic"

# -----------------------------------------------------------------------------
# Step 8 — Smoke checks. Confirm the new revision is actually answering
# requests AND reporting the version we just deployed. If health is 200
# and the version it reports matches $VERSION, the deploy worked
# end-to-end (image, env-var injection, and Settings wiring all healthy).
# -----------------------------------------------------------------------------
say "Smoke checks"
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" --project="$PROJECT_ID" \
  --format='value(status.url)')

HEALTH=$(curl -sf "$SERVICE_URL/api/v1/health" 2>/dev/null || echo "")
[ -n "$HEALTH" ] || die "/api/v1/health is not responding 200 — investigate, possibly rollback"
ok "/api/v1/health returns 200"

# Confirm the running container reports the version we just deployed.
# Mismatch means SERVICE_VERSION env var didn't reach the new revision —
# could mean the env-var update silently no-op'd, or Settings is caching.
REPORTED_VERSION=$(echo "$HEALTH" | jq -r '.version' 2>/dev/null || echo "?")
if [ "$REPORTED_VERSION" = "$VERSION" ]; then
  ok "health endpoint reports version: $REPORTED_VERSION (matches deployed tag)"
else
  warn "health endpoint reports version '$REPORTED_VERSION' but we deployed '$VERSION' — investigate"
fi

ROUTE_COUNT=$(curl -sf "$SERVICE_URL/api/v1/openapi.json" 2>/dev/null \
  | jq -r '.paths | keys | length' 2>/dev/null || echo 0)
ok "OpenAPI lists $ROUTE_COUNT routes"

# -----------------------------------------------------------------------------
# Done. Print summary + useful follow-up commands.
# -----------------------------------------------------------------------------
say "Deploy complete"
cat <<EOF

  Live image:      $IMAGE
  Service URL:     $SERVICE_URL
  Reported ver:    $REPORTED_VERSION

  Tail logs:
    gcloud beta run services logs tail $SERVICE_NAME \\
      --region=$REGION --project=$PROJECT_ID

  Roll back to the previous revision (if something is broken):
    gcloud run revisions list --service=$SERVICE_NAME \\
      --region=$REGION --project=$PROJECT_ID --limit=5
    gcloud run services update-traffic $SERVICE_NAME \\
      --to-revisions=<previous-revision-name>=100 \\
      --region=$REGION --project=$PROJECT_ID

EOF
