# Prompt — Step 4.3.5: Dev seed loader against Cloud SQL via Cloud Run Job

> Paste this entire block into a fresh Claude Code session when starting Step 4.3.5.
>
> **Numbering rationale.** Step 4.3 (image push) is DONE. Step 4.4 (Cloud
> Run deploy + smoke + cross-tenant test) is partially DONE — sections 1-4
> shipped 2026-05-03 (deploy v0.1.2, /api/v1/health smoke, log inspection);
> section 5 (cross-tenant test) is blocked on seed data. This step
> populates that seed data so Step 4.4's section 5 can proceed. Slotted in
> as 4.3.5 per the Step 3.4.5 precedent (work fitted between numbered
> steps).
>
> **Approach.** One-off image `v0.1.3-seed` extending v0.1.2 with the seed
> loader bits (`scripts/seed_dev_data/`, `data/ithina_dev_seed_data.xlsx`,
> `openpyxl` pinned to the uv.lock-resolved version). Cloud Run Job
> `admin-backend-seed-dev-data` deployed by exporting
> `admin-backend-alembic`'s YAML (Step 4.1 precedent), editing four fields,
> and running `gcloud run jobs replace`. Execute, verify row counts and
> RLS posture, then reverse the temporary discipline deviation in
> Step 4.4.1 (separate prompt, hard precondition of Step 4.5). The
> existing Step 4.4 prompt (`step-4_4-cloud-run-deploy-dev.md`) gets a
> small in-place amendment in this same commit: stale `/v1/` paths fixed
> to `/api/v1/`, placeholder cross-tenant UUIDs replaced with real seed
> UUIDs from this run.
>
> The deployed `admin-backend` Cloud Run service stays on `v0.1.2`
> throughout. No service redeploy. No Cloud SQL state mutations. No
> public-IP toggle. No laptop proxy.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report.
2. Read `CLAUDE.md` fully — focus on D-29 (PLATFORM RLS via OR-clause),
   FN-AB-14 (IS-NULL gate on `user_role_assignments`), CSD-01 / CSD-02
   (Cloud SQL specifics), Step 3.5 completion notes (`scripts/__init__.py`
   marker, synthetic PLATFORM AuthContext via `model_construct`,
   production-refusal guard on `ENVIRONMENT=production`, audit_logs skip).
3. Read `docs/architecture.md` "Schema and storage" — confirms the
   11-table count, the seed-vs-DDL distinction, the `cloudsqlsuperuser`
   privilege boundary that keeps the application role at
   `NOSUPERUSER NOBYPASSRLS`.
4. Read `BUILD_PLAN.md` Step 3.5 (loader contract), Step 4.1 (Cloud Run
   Job precedent), Step 4.4 (next step in sequence — partially DONE;
   section 5 cross-tenant test pending). Note: BUILD_PLAN.md Step 4.4
   may still be in its original GKE/manifests wording — that drift is
   pre-existing and NOT in scope for this commit (deferred to whenever
   Step 4.4 fully flips to DONE).
5. Read `prompts/step-3_5-dev-seed-loader-2026-05-03.md` (loader spec).
6. Read `prompts/step-4_1-cloud-sql-schema-bringup.md` (Cloud Run Job
   pattern). The shape of `admin-backend-seed-dev-data` mirrors
   `admin-backend-alembic` modulo image tag, command, and Job name.
7. Read `prompts/step-4_4-cloud-run-deploy-dev.md` (the canonical Step
   4.4 prompt). Its sections 1-4 (Cloud Run deploy, /v1/health smoke,
   OpenAPI, log inspection) shipped 2026-05-03 — the deployed service
   is at v0.1.2 and `/api/v1/health` returns 200. Its section 5
   (cross-tenant test) is what unlocks once this step (Step 4.3.5)
   completes. Phase 4 of THIS prompt amends that file in-place to fix
   stale `/v1/` paths and replace placeholder UUIDs with real seed
   UUIDs.
8. Read `scripts/seed_dev_data/__main__.py`. Specifically confirm:
   - The order in which the loader (a) constructs `Settings` via
     `get_settings()`, (b) checks the production-refusal guard,
     (c) calls `create_engine`, (d) calls
     `assert_app_role_no_bypassrls` (if at all — Step 2.2a defined
     the gate but it lives in `db/engine.py`; the loader may or may
     not invoke it), (e) opens the first session, (f) reads the
     workbook, (g) starts inserting.
   - Whether the loader sets `app.request_id` (Step 2.3 added it as a
     third session var; the loader's synthetic AuthContext path may
     pass `None`, which the dependency translates to a NULL GUC).
   - The exit-code contract: when does `__main__.py` return 0? Does
     it return 0 if `--sheets` filters all sheets out (no-op success)?
     This matters for Phase 3's failure-branch logic.
9. Read `scripts/seed_dev_data/README.md` and
   `scripts/seed_dev_data/column_mappings.py`. Understand the synthetic
   PLATFORM AuthContext path, the per-sheet loader contract, the
   `--reset` / `--dry-run` / `--sheets` flag semantics.
10. **Trigger scan.** Read
    `db/raw_ddl/Ithina_postgres_SQL_DDL_shared_utilities_v1.sql` and
    any other DDL file matched by
    `grep -l 'CREATE TRIGGER\|CREATE OR REPLACE TRIGGER' db/raw_ddl/*.sql`.
    For each trigger that fires on INSERT/UPDATE on a seeded table, check:
    - Does it read `current_setting('app.tenant_id', ...)` with a
      `missing_ok=true` second argument or a NULLIF wrapper that
      handles unset GUCs? (Loader sets `app.tenant_id=NULL` for
      PLATFORM rows.)
    - Does it read `current_setting('app.user_type', ...)`? Loader
      sets this to `'PLATFORM'` always (or `'TENANT'` per-row inside
      the `user_role_assignments` loader's TENANT-side rows).
    - Does it read `current_setting('app.request_id', ...)`? If it
      casts to UUID without a NULL check, INSERT fails on the loader's
      synthetic NULL request_id.
    - Does it write to `audit_logs`? `audit_logs` table doesn't exist
      until Step 6.2; if a trigger writes to it, INSERTs blow up.

    **If any concerning trigger is found, STOP.** Do not proceed to
    Phase 2. The fix lives in a separate step (either amend the
    trigger to handle the loader's GUC pattern, or amend the loader
    to set the GUCs the trigger requires). Surface to the operator
    with the specific trigger name and the specific concern; wait
    for direction.
11. Read the current `Dockerfile`, `.dockerignore`, `pyproject.toml`,
    and `uv.lock`. Confirm:
    - openpyxl is in `[dependency-groups].dev` (NOT runtime
      `dependencies`).
    - `scripts/seed_dev_data/` is excluded by `.dockerignore`.
    - `data/` is excluded by `.dockerignore`.
    - Only `scripts/smoke_test.py` and `scripts/verify_cloud_schema.py`
      are selectively COPYed into the image.
    - The exact openpyxl version resolved in `uv.lock`. Capture this
      for Phase 2's Dockerfile.seed pin.
      ```
      grep -A1 '^name = "openpyxl"' uv.lock | grep '^version'
      # Example output: version = "3.1.5"
      ```
12. Read this prompt fully and confirm scope.

---

## Step ID and intent

**Step 4.3.5** — Run the dev seed loader (Step 3.5 deliverable) against
the dev Cloud SQL instance `admin-master-dev` via a Cloud Run Job using
a one-off image extending v0.1.2.

After this step: `curl https://admin-backend-f2qhpcdeba-el.a.run.app/api/v1/tenants/stats`
returns `{"total_tenants": 7, "total_stores": 25}`. The deployed Cloud
Run service surfaces meaningful content for the first time. Step 4.4's
section 5 (cross-tenant test) is unblocked. Frontend integration on the
deployed dev URL is unblocked.

The image discipline established at Step 4.2/4.3 (`scripts/seed_dev_data/`
and `data/` excluded from runtime image; openpyxl in dev deps only) is
deliberately deviated from for this run. The deviation is named in
**FN-AB-XX** (drafted in Phase 4) and reversed in **Step 4.4.1**
(separate prompt, hard precondition of Step 4.5).

This is a CLAUDE_CODE step with operator-driven GCP mutations. The
operator runs `docker push`, the YAML diff review, `gcloud run jobs
replace`, `gcloud run jobs execute`, and the verification queries via
`gcloud sql connect`. Claude Code does the local image build, the
in-image verification, the YAML edit, the post-run report synthesis,
and the documentation edits.

---

## Scope in

### Phase 1 — pre-flight reads (Claude Code)

Verify the preconditions enumerated above. Specifically report:

- `scripts/__init__.py` exists in repo HEAD.
- `scripts/seed_dev_data/__main__.py` exists; its startup sequence
  documented (per Pre-flight item 8).
- `data/ithina_dev_seed_data.xlsx` exists.
- openpyxl is dev-group only; the uv.lock-resolved version captured.
- Trigger scan output: each trigger that fires on INSERT into seeded
  tables, with a one-line "OK because…" rationale. If any trigger is
  concerning, STOP per Pre-flight item 10.

STOP. Wait for operator OK before Phase 2.

### Phase 2 — build the temporary image (Claude Code)

#### File 1: `Dockerfile.seed` — new, root of repo

Substitute the actual openpyxl version captured in Phase 1 into the
`==X.Y.Z` pin below. Don't use `>=`.

```dockerfile
# syntax=docker/dockerfile:1.7
#
# Dockerfile.seed — one-off image for the admin-backend-seed-dev-data
# Cloud Run Job. Extends v0.1.2 with the dev seed loader, its Excel
# fixture, and openpyxl (which is in dependency-groups.dev only).
#
# openpyxl is pinned to the exact uv.lock-resolved version so the image
# matches the locally-tested loader behaviour. uv.lock drift after this
# image ships does NOT update Dockerfile.seed; that's intentional — this
# is a one-off image deleted at Step 4.4.1.
#
# Tag: v0.1.3-seed. NOT promoted to :latest. NOT used by the live
# admin-backend Cloud Run service.
#
# Reversed at Step 4.4.1. See FN-AB-XX in CLAUDE.md.

FROM asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend:v0.1.2

USER root

# openpyxl: dev-group dep in pyproject.toml. The runtime venv was built
# with `uv sync --frozen --no-dev`, so install directly into /opt/venv.
# Version pin matches uv.lock — substitute the captured version below.
RUN /opt/venv/bin/pip install --no-cache-dir 'openpyxl==<UV_LOCK_VERSION>'

# scripts/__init__.py is the package marker (Step 3.5) that lets
# `python -m scripts.seed_dev_data` resolve from WORKDIR=/app.
# v0.1.2's selective scripts/ COPYs (smoke_test.py, verify_cloud_schema.py)
# do NOT include __init__.py, so add it explicitly here.
COPY --chown=app:app scripts/__init__.py /app/scripts/__init__.py

# Seed loader package and its fixture. Both are excluded by the regular
# .dockerignore; this build uses a permissive ignorefile installed by
# scripts/build_seed_image.sh.
COPY --chown=app:app scripts/seed_dev_data/ /app/scripts/seed_dev_data/
COPY --chown=app:app data/ithina_dev_seed_data.xlsx /app/data/ithina_dev_seed_data.xlsx

USER app

# Default CMD is inherited from v0.1.2 (uvicorn). The Cloud Run Job
# overrides command/args to: ["python", "-m", "scripts.seed_dev_data"].
```

#### File 2: `scripts/build_seed_image.sh` — new, executable

```bash
#!/usr/bin/env bash
# Build v0.1.3-seed locally with a temporarily-permissive .dockerignore.
# Restores .dockerignore on EXIT (success, failure, SIGINT, SIGTERM).
# SIGKILL bypasses traps; the post-build `git diff` check at the end
# of this script is the belt-and-suspenders catch for that case.
#
# After successful build, runs in-image verification:
#   1. Files present (scripts/seed_dev_data/, data/Excel, scripts/__init__.py)
#   2. openpyxl import + version
#   3. Loader package import
#   4. Settings construction smoke (instantiates Settings via get_settings();
#      does NOT open a DB connection — catches Pydantic validator failures
#      before we burn Cloud Run cycles)
#
# Reversed at Step 4.4.1. See FN-AB-XX.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

IMAGE="asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend:v0.1.3-seed"
BACKUP=".dockerignore.bak.$$"

# Sanity: the source files we need must exist locally.
test -f scripts/__init__.py             || { echo "FAIL: scripts/__init__.py missing"; exit 2; }
test -f scripts/seed_dev_data/__main__.py || { echo "FAIL: scripts/seed_dev_data/__main__.py missing"; exit 2; }
test -f data/ithina_dev_seed_data.xlsx  || { echo "FAIL: data/ithina_dev_seed_data.xlsx missing"; exit 2; }
test -f Dockerfile.seed                  || { echo "FAIL: Dockerfile.seed missing"; exit 2; }

# Restore .dockerignore on any exit. Trap fires before `set -e` aborts.
restore_dockerignore() {
    if [[ -f "$BACKUP" ]]; then
        mv "$BACKUP" .dockerignore
        echo "[build_seed_image] .dockerignore restored from $BACKUP"
    fi
}
trap restore_dockerignore EXIT

# Backup the production .dockerignore.
cp .dockerignore "$BACKUP"
echo "[build_seed_image] .dockerignore backed up to $BACKUP"

# Install a permissive .dockerignore. Excludes only the obviously-unwanted.
cat > .dockerignore <<'EOF'
# Permissive context for Dockerfile.seed (v0.1.3-seed build only).
# Production .dockerignore is restored on EXIT.
__pycache__/
*.py[cod]
*$py.class
.Python
*.egg-info/
.eggs/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
.venv/
venv/
env/
.git/
.gitignore
.gitattributes
keys/
*.pem
*.key
.env
.env.local
.env.*.local
.envrc
tests/
docs/
*.md
!README.md
db/raw_ddl/
db/seeds/
.vscode/
.idea/
*.swp
*.swo
.DS_Store
Thumbs.db
k8s/
*.log
logs/
tmp/
*.tmp
*.bak
Dockerfile
docker-compose.yml
.dockerignore
postgres-data/
EOF
echo "[build_seed_image] permissive .dockerignore installed"

# Build the image.
echo "[build_seed_image] docker build -f Dockerfile.seed -t $IMAGE ."
docker build -f Dockerfile.seed -t "$IMAGE" .

# In-image verification. Each check is independently diagnostic.
echo ""
echo "=========================================="
echo "v0.1.3-seed verification"
echo "=========================================="

docker run --rm --entrypoint sh "$IMAGE" -c '
set -e
echo "--- Check 1: files present ---"
ls -la /app/scripts/seed_dev_data/ | head -20
echo ""
ls -la /app/data/
echo ""
ls -la /app/scripts/__init__.py
echo ""

echo "--- Check 2: openpyxl import ---"
python -c "import openpyxl; print(\"openpyxl\", openpyxl.__version__)"
echo ""

echo "--- Check 3: seed loader package import ---"
python -c "import scripts.seed_dev_data; print(\"seed loader importable\")"
echo ""

echo "--- Check 4: Settings construction smoke (no DB connection) ---"
cd /app
APP_REGION=US \
  AUTH_CLIENT_MODE=STUB \
  GCP_PROJECT_ID=ithina-retail-admin \
  DB_SCHEMA=core \
  JWT_ISSUER=https://stub-issuer.local/ \
  JWT_AUDIENCE=https://api.ithina.com \
  JWT_PUBLIC_KEY_PATH=/dev/null \
  JWT_PRIVATE_KEY_PATH=/dev/null \
  LOG_LEVEL=INFO \
  CLOUD_SQL_INSTANCE=ithina-retail-admin:asia-south1:admin-master-dev \
  SERVICE_NAME=admin-backend \
  ENVIRONMENT=development \
  DATABASE_URL=postgresql+psycopg://x:x@127.0.0.1:5432/x \
  python -c "
from admin_backend.config import get_settings
s = get_settings()
print(f\"Settings OK: environment={s.environment}, db_schema={s.db_schema}, auth_mode={s.auth_client_mode}\")
"
'

echo ""
echo "=========================================="
echo "build complete: $IMAGE"
echo "=========================================="
echo ""

# Belt-and-suspenders: confirm production files are unchanged after the
# build. The EXIT trap above should have restored .dockerignore; this
# verifies it AND catches SIGKILL-induced trap miss.
echo "[build_seed_image] post-build verification: production files unchanged?"
DRIFT=$(git diff --name-only -- .dockerignore Dockerfile pyproject.toml uv.lock)
if [[ -n "$DRIFT" ]]; then
    echo ""
    echo "FAIL: production files have unexpected drift:"
    echo "$DRIFT"
    echo ""
    echo "Recover with: git checkout HEAD -- .dockerignore Dockerfile pyproject.toml uv.lock"
    exit 3
fi
echo "[build_seed_image] OK: .dockerignore, Dockerfile, pyproject.toml, uv.lock unchanged"
echo ""
echo "Next steps (operator runs):"
echo "  docker push $IMAGE"
echo "  (then: see Phase 3 of step-4_3_5 prompt for Job deploy via YAML replace)"
```

`chmod +x scripts/build_seed_image.sh`.

STOP. Show operator both files. Wait for OK to run the build.

Run `./scripts/build_seed_image.sh`. Report build success, image size,
and the verification block output (all four checks). The post-build
`git diff` check is part of the script's own output — confirm it
printed the OK line.

STOP. Wait for OK.

### Phase 3 — operator-driven GCP execution (Claude Code drafts, operator runs)

The pattern: export the precedent Job's spec via `--format=export`,
edit four fields, show diff for review, `gcloud run jobs replace`. This
guarantees the new Job's wiring matches `admin-backend-alembic`'s
exactly (same SA, VPC, subnet, VPC egress, secret binding, env shape,
resource limits) — no flag-syntax risk, no env-var-substitution
mistakes, the diff is fully auditable before deploy.

#### Command (a) — push the image

```bash
docker push asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend:v0.1.3-seed
```

#### Command (b) — export precedent Job spec, edit, review, replace

```bash
# Export admin-backend-alembic's full spec
gcloud run jobs describe admin-backend-alembic \
  --region=asia-south1 --project=ithina-retail-admin \
  --format=export > /tmp/seed-job.yaml

# Edit four fields. Use sed for transparency (no YAML-structural changes).
# Verify each edit by inspecting the diff after.
sed -i \
  -e 's|name: admin-backend-alembic|name: admin-backend-seed-dev-data|' \
  -e 's|admin-backend:v0.1.2|admin-backend:v0.1.3-seed|' \
  -e 's|^            - alembic$|            - python|' \
  /tmp/seed-job.yaml

# The args list is more delicate — sed it in two steps because YAML
# list items live on separate lines. The original alembic Job has:
#   args:
#   - upgrade
#   - head
# Replace with:
#   args:
#   - -m
#   - scripts.seed_dev_data
sed -i \
  -e 's|^            - upgrade$|            - -m|' \
  -e 's|^            - head$|            - scripts.seed_dev_data|' \
  /tmp/seed-job.yaml

# Show the diff for human review BEFORE replace.
gcloud run jobs describe admin-backend-alembic \
  --region=asia-south1 --project=ithina-retail-admin \
  --format=export > /tmp/alembic-job.yaml

diff /tmp/alembic-job.yaml /tmp/seed-job.yaml
```

The diff should show exactly four-or-five line changes:

- `name:` (one line, top of metadata)
- image tag (one line)
- `command:` value (`alembic` → `python`)
- two args lines (`upgrade` → `-m`, `head` → `scripts.seed_dev_data`)

If the diff shows anything else, **STOP**. The sed pattern matched
something it shouldn't have, or the precedent Job's YAML drifted from
the assumed shape. Investigate before applying.

When the diff is clean:

```bash
# Apply
gcloud run jobs replace /tmp/seed-job.yaml \
  --region=asia-south1 --project=ithina-retail-admin
```

`gcloud run jobs replace` creates the Job if it doesn't exist, updates
it if it does. First run creates `admin-backend-seed-dev-data`.

#### Command (c) — execute and watch

```bash
gcloud run jobs execute admin-backend-seed-dev-data \
  --region=asia-south1 \
  --project=ithina-retail-admin \
  --wait
```

`--wait` blocks until execution finishes; gcloud's exit code reflects
the loader's exit code.

#### Failure-branch handling — STOP rules

**The Job's exit-0 status is NOT acceptance.** Only the row-count and
FORCE-RLS verification queries below are acceptance.

Three branches after `execute --wait`:

- **Branch A: exit 0 AND row counts match expected.** Proceed to Phase 4.
- **Branch B: exit non-zero.** STOP. Do not proceed. Read the execution
  logs:
  ```bash
  gcloud run jobs executions describe \
    $(gcloud run jobs executions list \
        --job=admin-backend-seed-dev-data \
        --region=asia-south1 --project=ithina-retail-admin \
        --limit=1 --format='value(metadata.name)') \
    --region=asia-south1 --project=ithina-retail-admin \
    --format='value(status.logUri)'
  # Open the logUri in browser, OR:
  gcloud logging read \
    "resource.type=cloud_run_job AND resource.labels.job_name=admin-backend-seed-dev-data" \
    --project=ithina-retail-admin --limit=200 --format='value(textPayload)'
  ```
  Report the failure mode in Phase 3 report. Step 4.3.5 is NOT done in
  this branch. Common causes: trigger interaction missed in Phase 1
  scan; Excel data drift; openpyxl version mismatch despite the pin;
  Settings validator failing on something the local smoke didn't catch.
- **Branch C: exit 0 BUT row counts don't match expected.** STOP. Do
  not proceed. Likely cause: a per-sheet loader silently no-op'd
  (e.g., empty sheet detection wrong) or a sheet was skipped. Step
  4.3.5 is NOT done. Investigate. **Cloud SQL now contains partial
  state.** Do not retry blindly with `--reset` until the cause is
  understood — a partially-loaded DB plus `--reset` plus a re-run is
  fine in principle but the diagnostic value of the partial state is
  destroyed by --reset.

#### Verification queries (operator runs via gcloud sql connect)

The application role `user_admin_backend` is `NOSUPERUSER NOBYPASSRLS`;
RLS hides rows from it without an `app.user_type='PLATFORM'` GUC. This
is the exact RLS posture the loader operates under, so verifying via
this role + GUC also exercises the D-29 OR-clause path end-to-end —
stronger signal than connecting as `postgres` (which would bypass RLS
via superuser and merely count rows without testing the policy).

```bash
gcloud sql connect admin-master-dev \
  --user=user_admin_backend \
  --database=ithina_platform_db \
  --project=ithina-retail-admin
```

(`gcloud sql connect` proxies via Cloud SQL's public path with
just-in-time IP whitelisting of the operator's source IP for ~5 minutes;
no Cloud SQL state mutation, fully reversible. The Cloud SQL instance's
authorized-networks state is not changed permanently.)

At the psql prompt:

```sql
-- Verification 1: row counts (PLATFORM-impersonated)
BEGIN;
SET LOCAL app.user_type = 'PLATFORM';
SELECT 'platform_users'        AS t, count(*) FROM core.platform_users
UNION ALL SELECT 'tenants',                count(*) FROM core.tenants
UNION ALL SELECT 'org_nodes',              count(*) FROM core.org_nodes
UNION ALL SELECT 'stores',                 count(*) FROM core.stores
UNION ALL SELECT 'tenant_users',           count(*) FROM core.tenant_users
UNION ALL SELECT 'roles',                  count(*) FROM core.roles
UNION ALL SELECT 'permissions',            count(*) FROM core.permissions
UNION ALL SELECT 'role_permissions',       count(*) FROM core.role_permissions
UNION ALL SELECT 'user_role_assignments',  count(*) FROM core.user_role_assignments
UNION ALL SELECT 'tenant_module_access',   count(*) FROM core.tenant_module_access
ORDER BY t;
COMMIT;
```

Expected:

| table | count |
|---|---|
| platform_users | 3 |
| tenants | 7 |
| org_nodes | 49 |
| stores | 25 |
| tenant_users | 17 |
| roles | 15 |
| permissions | 24 |
| role_permissions | 117 |
| user_role_assignments | 22 |
| tenant_module_access | 27 |

```sql
-- Verification 2: FORCE RLS still intact on all 6 multi-tenant tables
SELECT relname, relrowsecurity, relforcerowsecurity
FROM pg_class
WHERE relname IN (
    'tenants', 'org_nodes', 'stores',
    'tenant_users', 'user_role_assignments', 'tenant_module_access'
)
ORDER BY relname;
```

Expected: all 6 rows show `t|t`. The loader does not touch RLS posture;
this query is a sanity belt-and-suspenders check.

```sql
\q
```

Then from terminal (deployed-service smoke):

```bash
curl -sS https://admin-backend-f2qhpcdeba-el.a.run.app/api/v1/tenants/stats
# Expected: {"total_tenants": 7, "total_stores": 25}

curl -sS -o /dev/null -w "health: %{http_code}\nready: %{http_code}\n" \
  https://admin-backend-f2qhpcdeba-el.a.run.app/api/v1/health \
  https://admin-backend-f2qhpcdeba-el.a.run.app/api/v1/ready
# Expected: both 200
```

After the operator pastes back execute result + the three verification
outputs (counts, FORCE-RLS, stats curl), Claude Code writes the Phase 3
report (5-bundle shape — see Report section). STOP. No commits yet.

### Phase 4 — documentation + Step 4.3.5 closure (Claude Code)

Five edits, one commit.

#### Edit 1: `CLAUDE.md` — add FN-AB-XX

Pick the next free FN-AB number after FN-AB-17. Place in Forward-notes,
matching the existing numeric ordering.

```markdown
### FN-AB-XX — v0.1.3-seed image is a temporary discipline deviation; reverse at Step 4.4.1

Step 4.3.5 dev seeding required `scripts/seed_dev_data/`, the Excel
fixture `data/ithina_dev_seed_data.xlsx`, and `openpyxl` (a
dependency-groups.dev member, pinned to the uv.lock-resolved version
in Dockerfile.seed) inside the Cloud Run Job's image. All three are
excluded from v0.1.2 by deliberate Step 4.2 / 4.3 discipline: dev
tooling and fake-company fixture data have no business in a runtime
image. The temporary v0.1.3-seed image (extending v0.1.2 via
Dockerfile.seed) shipped them in for one Cloud Run Job execution.

**Why acceptable for one run.** The image is tagged `v0.1.3-seed`,
not promoted to `:latest`, and is referenced only by the
`admin-backend-seed-dev-data` Cloud Run Job — never by the live
`admin-backend` service (which stays on `v0.1.2` throughout). The
synthetic-PLATFORM AuthContext code path (loader's `model_construct`
bypass of validation) is gated by the production-refusal guard
(refuses when `ENVIRONMENT=production`); the data fixture is
fake-company seed data, no real PII.

**Why this still has to be reversed.** Defense-in-depth says don't
ship dev tooling in any deployable image, even a Job image. The
Dockerfile.seed, the build script, and the v0.1.3-seed tag in
Artifact Registry all need to come out once seeding is verified.
Otherwise the next person needing a one-off Job image inherits a
"just extend the seed image" pattern that drifts further from
discipline.

**Hard precondition.** Step 4.4.1 (Dockerfile.seed teardown) is a
hard precondition of Step 4.5 (Stores resource — next image build).
Step 4.5 will not start until Step 4.4.1 ships. FN-AB-XX closes
when Step 4.4.1 ships.

**Resolution criterion.** Repo no longer contains `Dockerfile.seed`
or `scripts/build_seed_image.sh`. Artifact Registry no longer has
the `v0.1.3-seed` tag (the underlying digest may remain GC-eligible;
tag removal is the load-bearing part — prevents accidental
redeployment). The `admin-backend-seed-dev-data` Cloud Run Job is
either deleted (default) or kept paused as a re-seeding artifact
(operator's call at Step 4.4.1 time).

**The deeper thing this defers.** A proper prod-vs-dev image split
(separate Dockerfile targets, separate registry paths, separate
.dockerignore variants) is the long-term answer. That conversation
happens before the first prod deploy (Step 8.x), not now. This
FN-AB names the gap so it doesn't get forgotten in the run-up to
prod.
```

#### Edit 2: `BUILD_PLAN.md` — add Step 4.3.5 entry

Slot in between Step 4.3 and Step 4.4. Status DONE in this commit.

```markdown
## Step 4.3.5 — Dev seed loader against Cloud SQL via Cloud Run Job

**Status.** DONE
**Owner.** CLAUDE_CODE (image build, docs) + HUMAN (GCP-side execute)

**Note on numbering.** Slotted in between Step 4.3 (Artifact Registry
push) and Step 4.4 (Cloud Run deploy + cross-tenant test). Same
fitted-in pattern as Step 3.4.5. Step 4.4's section 5 (cross-tenant
test) was blocked on seed data; this step unblocks it.

**Goal.** Run the Step 3.5 seed loader against the dev Cloud SQL
instance so the deployed admin-backend service surfaces real content,
and so Step 4.4's cross-tenant test has tenants to test against.

**Scope in.**
- One-off image v0.1.3-seed extending v0.1.2 with `scripts/seed_dev_data/`,
  `data/ithina_dev_seed_data.xlsx`, `scripts/__init__.py`, and `openpyxl`
  pinned to the uv.lock-resolved version (pip-installed into /opt/venv,
  not via uv sync — keeps pyproject.toml unchanged).
- `Dockerfile.seed` and `scripts/build_seed_image.sh` (with EXIT-trap
  .dockerignore restoration + post-build `git diff` belt-and-suspenders
  check) added to repo. Reversed at Step 4.4.1.
- Cloud Run Job `admin-backend-seed-dev-data` deployed via
  `gcloud run jobs describe admin-backend-alembic --format=export` →
  4-field sed edit → diff review → `gcloud run jobs replace`. Inherits
  the alembic Job's wiring exactly: SA, VPC, subnet, VPC egress, secret
  binding, env vars (already correct ENVIRONMENT=development), resource
  limits. Only image tag, command, args, and Job name differ.
- FN-AB-XX added to CLAUDE.md naming the temporary deviation.
- Step 4.4.1 added to BUILD_PLAN.md as a hard precondition of Step 4.5.
- Existing `prompts/step-4_4-cloud-run-deploy-dev.md` amended in-place:
  stale `/v1/` paths corrected to `/api/v1/`; placeholder cross-tenant
  UUIDs replaced with real seed UUIDs; sections 1-4 marked DONE
  (deploy/smoke shipped 2026-05-03).

**Scope out.**
- Step 4.4 cross-tenant test execution. Stays as Step 4.4 work,
  unblocked by this step.
- Reversal of the temporary discipline deviation — Step 4.4.1.
- Re-deploying the admin-backend service (stays on v0.1.2).
- pyproject.toml changes (openpyxl stays in dev deps).
- BUILD_PLAN.md Step 4.4 GKE→Cloud-Run wording rewrite (pre-existing
  drift; deferred to whenever Step 4.4 fully flips to DONE).

**Acceptance criteria.**
- v0.1.3-seed builds; in-image verification's four checks all pass
  (files present, openpyxl import + version, loader package import,
  Settings construction smoke).
- `gcloud run jobs replace` succeeds; YAML diff vs admin-backend-alembic
  shows only the four expected field changes.
- `gcloud run jobs execute admin-backend-seed-dev-data --wait` exits 0.
  **Exit 0 is NOT acceptance on its own** — the row-count query is.
- Row counts (PLATFORM-impersonated, run as user_admin_backend via
  gcloud sql connect) match expected: platform_users=3, tenants=7,
  org_nodes=49, stores=25, tenant_users=17, roles=15, permissions=24,
  role_permissions=117, user_role_assignments=22, tenant_module_access=27.
- All 6 multi-tenant tables show `t|t` for relrowsecurity /
  relforcerowsecurity (FORCE RLS intact post-seed).
- `curl /api/v1/tenants/stats` → `{"total_tenants": 7, "total_stores": 25}`.
- Build script's post-build `git diff` confirms .dockerignore,
  Dockerfile, pyproject.toml, uv.lock all unchanged.

**Coordination.**
- Frontend integration on the deployed dev URL is unblocked.
- Step 4.4 (cross-tenant test) is unblocked.
- Step 4.4.1 follows as repo cleanup.

**Rough effort.** ~1 hour Claude Code + ~10 min operator GCP commands
+ ~5 min verification queries.
```

#### Edit 3: `BUILD_PLAN.md` — add Step 4.4.1 entry

Place after Step 4.4 (which stays TODO).

```markdown
## Step 4.4.1 — Dockerfile.seed teardown + image discipline restoration

**Status.** TODO
**Owner.** CLAUDE_CODE (repo edits) + HUMAN (registry tag removal)
**Blocked by.** Step 4.3.5 acceptance (seed run verified successful;
row counts and FORCE-RLS confirmed).
**Blocks.** Step 4.5 (next image build — Stores resource).

**Goal.** Restore the v0.1.2-era image discipline now that one-off
seeding (Step 4.3.5) is done. Close FN-AB-XX.

**Scope in.**
- Delete `Dockerfile.seed` from repo.
- Delete `scripts/build_seed_image.sh` from repo.
- Verify `.dockerignore`, `Dockerfile`, `pyproject.toml`, `uv.lock`
  are byte-identical to their pre-Step-4.3.5 state. Recovery if drift:
  `git checkout HEAD -- .dockerignore Dockerfile pyproject.toml uv.lock`.
- Operator removes the `v0.1.3-seed` tag from Artifact Registry:
  `gcloud artifacts docker tags delete \
    asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend:v0.1.3-seed`.
- Operator decides Cloud Run Job fate: delete (default — recommended)
  or retain as paused artifact. The Dockerfile.seed pattern is
  reconstructable from the Step 4.3.5 prompt + git history if ever
  needed again.
- CLAUDE.md FN-AB-XX → status RESOLVED with closure note.

**Scope out.**
- The long-term prod/dev image split. Day 8 concern (pre-prod).
- Re-seeding mechanics. Reconstruct Dockerfile.seed from history if
  ever needed.

**Acceptance criteria.**
- `git ls-files | grep -E 'Dockerfile.seed|build_seed_image'` empty.
- `gcloud artifacts docker tags list ...` does not include `v0.1.3-seed`.
- `git diff HEAD~1 -- Dockerfile .dockerignore pyproject.toml uv.lock`
  empty (no drift in production files).
- CLAUDE.md FN-AB-XX has the RESOLVED suffix.
- Step 4.5 unblocked.

**Coordination.**
- Single-commit cleanup. Operator runs the registry/Job commands.

**Rough effort.** 15 minutes.
```

#### Edit 4: `BUILD_PLAN.md` — amend Step 4.5 with Blocked-by

Add this line just below Step 4.5's `**Owner.**`:

```markdown
**Blocked by.** Step 4.4.1 (Dockerfile.seed teardown — image discipline
must be restored before the next image rebuild).
```

#### Edit 5: `prompts/step-4_4-cloud-run-deploy-dev.md` — amend in place

Five small changes inside the existing file. Do NOT rename the file.
Do NOT rewrite the structure. Do NOT touch sections that are correct
as-shipped.

**Change 1.** Replace stale `/v1/` paths with `/api/v1/`:

- `/v1/health` → `/api/v1/health` (sections 3, "Acceptance criteria",
  "Stop and ask if")
- `/v1/openapi.json` → `/api/v1/openapi.json` (section 3, "Acceptance
  criteria")

Verify by `grep '/v1/' prompts/step-4_4-cloud-run-deploy-dev.md` after
the edit — should be empty.

**Change 2.** Add a status line at the top of the prompt (just below
the `> Run AFTER ...` callout block), recording partial completion:

```markdown
> **Status as of 2026-05-04.** Sections 1-4 (Cloud Run deploy v0.1.2,
> /api/v1/health smoke, OpenAPI fetch, log inspection) shipped
> 2026-05-03. Section 5 (cross-tenant isolation test) blocked on seed
> data until 2026-05-04; unblocked by Step 4.3.5 run. This step flips
> to DONE when section 5 passes.
```

**Change 3.** Replace placeholder cross-tenant UUIDs in section 5
with real seed UUIDs from the Step 4.3.5 run. Replace the JWT mint
block:

```python
uv run python -c "
from admin_backend.auth.testing import make_test_jwt
import uuid
t1 = uuid.UUID('00000000-0000-0000-0000-000000000001')
t2 = uuid.UUID('00000000-0000-0000-0000-000000000002')
print('TENANT-1:', make_test_jwt(tenant_id=t1, user_type='TENANT'))
print('TENANT-2:', make_test_jwt(tenant_id=t2, user_type='TENANT'))
"
```

with:

```python
# Real seed UUIDs from Step 4.3.5 (Buc-ee's, Żabka). The seed loader
# captured these via UUIDv7 substitution at load time; identical UUIDs
# in local DB and Cloud SQL because the Excel's ID column is the
# Excel-side key, mapped to DB-generated UUIDs via UUIDMapper.
# CAVEAT: local UUIDs differ from cloud UUIDs because each --reset
# regenerates them. Use the cloud UUIDs (below) when minting JWTs for
# the cloud test; mint locally first against local DB UUIDs to sanity-
# confirm the JWT chain works before hitting cloud.
uv run python -c "
from admin_backend.auth.testing import make_test_jwt
import uuid

# Cloud-side UUIDs (post-Step-4.3.5):
BUCEES_TENANT     = uuid.UUID('972a8469-1641-4f82-8b9d-2434e465e150')
BUCEES_USER       = uuid.UUID('93829b43-922f-415a-a1e3-db63ef7ddc76')  # marcus.t@bucees.com
ZABKA_TENANT      = uuid.UUID('17fc695a-07a0-4a6e-8822-e8f36c031199')
ZABKA_USER        = uuid.UUID('310c0c00-3fa7-4104-9bf9-0e27dc96925e')  # a.kowalski@zabka.pl

print('MARCUS:  ', make_test_jwt(user_id=BUCEES_USER, tenant_id=BUCEES_TENANT, user_type='TENANT'))
print('KOWALSKI:', make_test_jwt(user_id=ZABKA_USER,  tenant_id=ZABKA_TENANT,  user_type='TENANT'))
"
```

Update the curl block correspondingly:

```bash
curl -H "Authorization: Bearer $MARCUS_JWT"   "${BACKEND_URL}/api/v1/tenants"
curl -H "Authorization: Bearer $KOWALSKI_JWT" "${BACKEND_URL}/api/v1/tenants"
```

Add the 6-call cross-tenant matrix (own / cross / list, two tenants)
that was in the operator's earlier handoff. Replace the brief
"Expected: each call returns only the tenant's own row(s)..." paragraph
with an explicit table:

```markdown
| Call | Token | Expected |
|---|---|---|
| GET /api/v1/tenants                                  | MARCUS   | 200, items length 1, only Buc-ee's |
| GET /api/v1/tenants                                  | KOWALSKI | 200, items length 1, only Żabka     |
| GET /api/v1/tenants/972a8469-1641-4f82-8b9d-2434e465e150 | MARCUS   | 200, Buc-ee's data |
| GET /api/v1/tenants/972a8469-1641-4f82-8b9d-2434e465e150 | KOWALSKI | **404** (RLS-as-404 per D-25) |
| GET /api/v1/tenants/17fc695a-07a0-4a6e-8822-e8f36c031199 | KOWALSKI | 200, Żabka data |
| GET /api/v1/tenants/17fc695a-07a0-4a6e-8822-e8f36c031199 | MARCUS   | **404** (RLS-as-404 per D-25) |

Optional richer matrix (only if time permits): repeat for
`/api/v1/tenant-users` and `/api/v1/stores`. Same isolation expected.
```

Also drop the line "Test tenants must exist in the cloud DB. If Step
4.1 didn't insert them as part of the smoke test, do so now via the
same Cloud Run Job pattern..." — that fallback is no longer needed
because Step 4.3.5 has populated the cloud DB.

**Change 4.** Update the "What to report at end" Bundle 3 line:

> **BUILD_PLAN.md updates:** Step 4.4 status flipped to DONE.

…stays as the END-OF-STEP-4.4 acceptance (when section 5 passes), but
add a parenthetical noting Step 4.3.5 dependency:

> **BUILD_PLAN.md updates:** Step 4.4 status flipped to DONE (sections
> 1-4 shipped 2026-05-03; section 5 unblocked by Step 4.3.5 run on
> 2026-05-04). Note the Cloud Run path; reference D-31. Coordination
> block updated: frontend team integrating against the deployed dev URL.

**Change 5.** Update the "Implementation hints" line about test data:

> Test tenants must exist in the cloud DB. If Step 4.1 didn't insert
> them as part of the smoke test, do so now via the same Cloud Run
> Job pattern...

→ remove this line (it's implicitly addressed by the Step 4.3.5
dependency stated in the new status callout block).

#### Stage and commit (after operator OK)

```bash
git status
# Expected new/modified:
#   new:      Dockerfile.seed
#   new:      scripts/build_seed_image.sh
#   modified: CLAUDE.md
#   modified: BUILD_PLAN.md
#   modified: prompts/step-4_4-cloud-run-deploy-dev.md
#   new:      prompts/step-4_3_5-cloud-sql-seed-loader-2026-05-04.md
#   new:      prompts/step-4_4_1-image-discipline-restoration-2026-05-04.md

git add Dockerfile.seed scripts/build_seed_image.sh \
        CLAUDE.md BUILD_PLAN.md \
        prompts/step-4_4-cloud-run-deploy-dev.md \
        prompts/step-4_3_5-cloud-sql-seed-loader-2026-05-04.md \
        prompts/step-4_4_1-image-discipline-restoration-2026-05-04.md
```

Propose this commit message:

```
Step 4.3.5: dev seed loader against Cloud SQL via Cloud Run Job

- One-off image v0.1.3-seed extending v0.1.2 with seed loader, Excel
  fixture, scripts/__init__.py, and openpyxl pinned to the uv.lock-
  resolved version (pip-installed into /opt/venv to keep pyproject.toml
  unchanged). Tagged v0.1.3-seed only; not promoted to :latest;
  referenced only by the admin-backend-seed-dev-data Job.
- Cloud Run Job admin-backend-seed-dev-data deployed via YAML-replace
  pattern: export admin-backend-alembic spec, sed-edit four fields
  (name, image, command, args), diff for review, gcloud run jobs replace.
  Inherits the alembic Job's wiring exactly.
- Execute --wait exit 0; loader processed 11/12 sheets (audit_logs
  skipped per Step 6.2 territory). Row counts verified via
  `gcloud sql connect --user=user_admin_backend` with
  `SET LOCAL app.user_type='PLATFORM'` (exercises the D-29 OR-clause
  end-to-end, stronger signal than connecting as superuser): 7 tenants,
  25 stores, 17 tenant_users, 22 user_role_assignments, 27
  tenant_module_access, etc. All 6 multi-tenant tables show t|t for
  relrowsecurity / relforcerowsecurity.
- /api/v1/tenants/stats now returns {"total_tenants": 7, "total_stores": 25}
  through the deployed Cloud Run service. Step 4.4 cross-tenant test
  unblocked. Frontend integration on dev URL unblocked.
- FN-AB-XX (CLAUDE.md): names the temporary discipline deviation
  (Dockerfile.seed, openpyxl in image, fixture data in image) as
  acceptable for one run; hard precondition tied to Step 4.4.1
  reversal; deeper prod/dev image split deferred to Day 8.
- BUILD_PLAN.md Step 4.3.5 added (DONE; slotted between 4.3 and 4.4
  per Step 3.4.5 precedent); Step 4.4.1 added (TODO; blocks Step 4.5);
  Step 4.5 amended with "Blocked by Step 4.4.1". Pre-existing
  BUILD_PLAN.md Step 4.4 GKE→Cloud-Run wording drift left untouched
  (defer to Step 4.4 DONE commit).
- prompts/step-4_3_5-cloud-sql-seed-loader-2026-05-04.md (this prompt).
- prompts/step-4_4_1-image-discipline-restoration-2026-05-04.md
  (the reversal prompt).
- prompts/step-4_4-cloud-run-deploy-dev.md amended in place: stale
  /v1/ paths corrected to /api/v1/; placeholder cross-tenant UUIDs
  (00000000-...0001/0002) replaced with real seed UUIDs from this
  run (Buc-ee's 972a8469-..., Żabka 17fc695a-...); status callout
  notes sections 1-4 shipped 2026-05-03 / section 5 unblocked by
  Step 4.3.5 / DONE flip pending section-5 pass.
```

Ask operator: "Run? yes / no / edit message".

---

## Scope out

- **Step 4.4 cross-tenant test execution.** Stays as Step 4.4 work
  per the existing prompt; unblocked by this step but not run here.
- **Step 4.4.1 cleanup execution.** Lives in its own prompt
  (`prompts/step-4_4_1-image-discipline-restoration-2026-05-04.md`),
  triggered after Step 4.3.5 commit lands.
- **BUILD_PLAN.md Step 4.4 GKE→Cloud-Run wording rewrite.** Pre-existing
  drift; not introduced or addressed by this commit. Defer to whenever
  Step 4.4 flips to DONE.
- **Reseeding mechanics, idempotency improvements, UPSERT semantics.**
  Step 7.3.1 territory.
- **Modifications to the deployed admin-backend service.** Service
  stays on v0.1.2 throughout. No revisions touched.
- **Cloud SQL state mutations.** No public-IP toggle, no
  authorized-networks changes, no role privilege changes. The Job
  reaches Cloud SQL via the same VPC private-IP path the deployed
  service uses. `gcloud sql connect` for verification uses Cloud
  SQL's just-in-time IP whitelisting which is automatically reverted.
- **pyproject.toml / uv.lock changes.** openpyxl stays in dev deps;
  the seed image installs it ad-hoc via pinned `pip install`.

---

## Testing and regression discipline

### Pre-execution

- In-image verification block (run by `scripts/build_seed_image.sh`):
  four independent checks (files present, openpyxl import + version,
  loader package import, Settings construction smoke without DB).
- `git diff` post-build proves `.dockerignore`, `Dockerfile`,
  `pyproject.toml`, `uv.lock` unchanged.
- Phase 1 trigger scan flags any DDL trigger that would fail on the
  loader's synthetic GUC pattern (NULL request_id, PLATFORM user_type,
  NULL or per-row tenant_id).
- YAML diff (Phase 3) confirms the new Job's spec differs from the
  precedent Job's only in four expected fields.

### Post-execution

- 10 row-count assertions match expected (Verification 1 query).
- 6 FORCE-RLS rows show `t|t` (Verification 2).
- `/api/v1/health` and `/api/v1/ready` return 200 + `db:ok` (proves
  no schema corruption from the seed run).
- `/api/v1/tenants/stats` returns `{"total_tenants": 7, "total_stores": 25}`.

### Tests deliberately not added

- New unit/integration tests for the loader. Step 3.5 already shipped
  10 of these (4 integration + 5 unit + 1 unit drift detector); the
  Cloud Run Job execution exercises the loader in a new environment,
  not new code.
- A "seeded data round-trip" test against deployed backend. Step 4.4
  section 5 covers that; this step stops at row counts + FORCE-RLS.

### Regression risk surface introduced

1. v0.1.3-seed image existing in Artifact Registry is an attack
   surface (dev tooling in deployable image). Mitigated by tag-only
   (no `:latest` promotion), single-Job consumer, time-bounded by
   Step 4.4.1.
2. `Dockerfile.seed` and `scripts/build_seed_image.sh` in repo HEAD
   are legible "do this for next one-off" patterns. Mitigated by
   removal at Step 4.4.1 + the FN-AB-XX explanation that recreating
   from history is preferred over keeping them.
3. `admin-backend-seed-dev-data` Cloud Run Job lingering after Step
   4.4.1 (if operator chooses to retain) holds a frozen reference to
   v0.1.3-seed digest. Operator-decision-time concern; default is delete.

---

## Acceptance criteria

- v0.1.3-seed image built; in-image verification's four checks pass;
  build script's post-build `git diff` reports no production-file drift.
- `docker push` succeeds.
- `gcloud run jobs replace` YAML diff shows only the four expected
  field changes.
- `gcloud run jobs execute --wait` exits 0. **Exit 0 is not acceptance
  on its own** — see below.
- Row counts: 10/10 match expected (run as user_admin_backend with
  PLATFORM GUC, exercising D-29 OR-clause).
- FORCE-RLS posture: 6/6 tables show `t|t`.
- `/api/v1/tenants/stats` returns `{"total_tenants": 7, "total_stores": 25}`
  through the deployed service.
- One commit lands containing: Dockerfile.seed,
  scripts/build_seed_image.sh, CLAUDE.md (FN-AB-XX),
  BUILD_PLAN.md (Step 4.3.5 DONE + Step 4.4.1 TODO + Step 4.5
  Blocked-by), the existing Step 4.4 prompt amended in place,
  this prompt file, the Step 4.4.1 prompt file.

---

## Report (BEFORE proposing commit)

Five bundles per the convention:

1. **Local image work.** Dockerfile.seed and scripts/build_seed_image.sh
   created with line counts; uv.lock-resolved openpyxl version;
   build success; image size; verification block output (each of the
   four checks); post-build `git diff` clean.

2. **Cloud-side run.** `docker push` digest; YAML diff against
   admin-backend-alembic (paste verbatim — should be the four expected
   field changes); `gcloud run jobs replace` result; Job execute exit
   code; pasted log highlights (per-sheet "Loading X (N rows)..." +
   "✓ X loaded." lines + final "Seed complete." line). Time-to-completion.

3. **Verification queries.** Both psql results pasted verbatim
   (10-row count table + 6-row FORCE-RLS table). Pass/fail per row.
   `/api/v1/tenants/stats` curl output. `/api/v1/health` and
   `/api/v1/ready` outputs.

4. **Documentation edits.** FN-AB-XX text (the actual block landing
   in CLAUDE.md). Step 4.3.5 entry. Step 4.4.1 entry. Step 4.5
   "Blocked by" line. The five amendments to the existing Step 4.4
   prompt with a final `grep '/v1/'` showing zero matches.

5. **Anything that needed adjustment.** Trigger findings from the
   shared_utilities_v1.sql scan; any drift between this prompt's
   expectations and the actual loader / Settings / .dockerignore;
   any operator-side surprise (gcloud command, YAML edit, Job
   execute, `gcloud sql connect`).

Wait for explicit authorisation before staging or committing.

---

## After completing

When operator authorises (after reviewing the report), propose the
git commands above. Ask: "Run? yes / no / edit message".

After commit lands: hand control back. Step 4.4.1 lives in its own
prompt + own commit, triggered when operator is ready (typically same
session, optionally later). Step 4.4 cross-tenant test (section 5 of
the existing prompt) is now unblocked and can run independently of
4.4.1 timing.

---

## Guardrails throughout

- **Do NOT modify** the committed `Dockerfile`, `.dockerignore`,
  `pyproject.toml`, or `uv.lock`. The build script's swap pattern
  handles temporary changes via backup + EXIT-trap restore + post-build
  `git diff` belt-and-suspenders. If the build script's post-build
  check reports drift, recover with
  `git checkout HEAD -- .dockerignore Dockerfile pyproject.toml uv.lock`
  and STOP — investigate before re-running.
- **Do NOT modify** `pyproject.toml`. openpyxl gets pip-installed inside
  Dockerfile.seed only.
- **Do NOT touch** any Cloud Run service, Cloud SQL instance, Secret
  Manager, IAM, or VPC state in Phases 1-2 or 4. Local-only work in
  those phases. GCP mutations live in Phase 3, all operator-driven.
  `gcloud sql connect` for verification queries uses Cloud SQL's
  just-in-time IP whitelisting which is automatically reverted —
  this is NOT a permanent state mutation.
- **Do NOT commit between phases.** One commit at end of Phase 4.
  Step 4.4.1 ships in its own commit later.
- **Do NOT rename** `prompts/step-4_4-cloud-run-deploy-dev.md`. The
  Phase 4 amendment is in-place: five small edits, no structural
  rewrite.
- **Do NOT rewrite** the BUILD_PLAN.md Step 4.4 entry. Pre-existing
  GKE→Cloud-Run wording drift is out of scope.
- **Job exit 0 is NOT acceptance.** Row counts + FORCE-RLS + the
  stats curl are acceptance.
- **STOP at every STOP gate.** Phase 1 → Phase 2 (mid-phase, after
  files drafted) → Phase 2 (post-build) → Phase 3 hand-off → post-execute
  report → Phase 4 commit proposal. Six STOP gates total.
- **STOP if Phase 1 trigger scan finds a problematic trigger.** Do
  not build the image until the trigger interaction is resolved
  (separate step's worth of work, not in scope here).
- **STOP if Phase 3 execute exits non-zero or row counts don't match.**
  Step 4.3.5 is NOT done; Phase 4 does NOT proceed. Diagnose; report;
  wait for operator direction. Do not blind-retry with `--reset`
  (destroys partial-state diagnostic value).
- The deployed `admin-backend` Cloud Run service is read-only-from-
  our-perspective throughout. Do NOT redeploy it. Do NOT touch
  revisions. Do NOT bump its image tag.

---

## End of prompt
