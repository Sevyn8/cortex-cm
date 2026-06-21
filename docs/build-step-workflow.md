# Build-Step Workflow

> Authored 2026-05-05 after walking through Step 6.1's full post-commit cycle
> end-to-end (local validation through cloud verification). This doc captures
> what we actually did, the decisions we hit, and the gotchas worth remembering.
> It is NOT speculative — every step here was practiced. Future build steps
> follow the same shape.

---

## Purpose

Each build step in this project ends with Claude Code reaching "proposing a git commit." Everything after that — commit review, push, local validation, cloud migration, deploy, smoke check, closure — is human-driven. This doc is the canonical sequence.

A team member with this doc + the Claude Code report should be able to walk a build step from commit-ready to fully-deployed-and-verified without reconstructing the workflow from memory.

---

## The 12 steps

1. **Validate proposed commit** — review staged files, message, scope.
2. **Local commit + state snapshot** — `git add` (explicit), `git commit`, capture pre-state to `build-history.md`.
3. **Push to origin** — `git push origin main`.
4. **Deploy local app** — start uvicorn against local Postgres.
5. **Validate local DB state** — alembic head + canonical row counts match Claude Code's report.
6. **Re-seed if drifted** — only when step 5 surfaces drift.
7. **Local smoke** — `scripts/test_endpoints.sh` (full matrix) or `scripts/smoke_curl.sh http://localhost:8000` (light check).
8. **Cloud SQL migration** — alembic Cloud Run Job execution.
9. **Build + push image to Artifact Registry** — Docker build with `SERVICE_VERSION` baked in.
10. **Deploy to Cloud Run** — new revision serves 100% traffic.
11. **Cloud smoke** — `scripts/smoke_curl.sh <cloud-url>` against new revision.
12. **Mark step DONE** — append cloud-verified entry to `build-history.md`.

Each step is self-contained: prior step's output enables the next, but rollback is possible at any point with the snapshots captured along the way.

---

## Detailed step walkthrough

### Step 1 — Validate proposed commit

Claude Code surfaces a 6-bundle report ending with a proposed `git commit -m "..."` block. Before approving:

**Three things to verify:**

- **Staged file list matches Bundle 1 of the report.** Cross-reference what Claude Code says it changed against what `git status` shows is staged. Mismatches mean Claude Code touched files outside its declared scope, or missed files it intended to ship.
- **Commit message accurately summarises the work.** Skim the proposed message; check for outdated facts (e.g., "23 tests" when the report shows 24), missing context, or generic boilerplate.
- **No extraneous files in staging.** If your repo has unrelated untracked files (other in-flight work, helper scripts), they should NOT sweep into this commit. Use explicit `git add <paths>` rather than `git add -A` when this is a concern.

**Decision:**

- **Approve** → say "yes" to Claude Code, it executes.
- **Edit** → ask Claude Code to revise the commit message. Common reasons: shorter summary, different wording, additional context.
- **Abort** → tell Claude Code to back out. Restart from earlier in the step (re-prompt, re-execute).

### Step 2 — Local commit + state snapshot

Once approved, the commit lands locally. Capture the snapshot **before pushing**:

```bash
# Commit (per Claude Code's proposal)
git status                # confirm staging
git add <explicit paths>  # avoid -A unless certain no extraneous files
git commit -m "..."
git log --oneline -1      # confirm SHA

# Capture state snapshot
echo "Step X.Y commit: $(git log --format=%h -1)"
uv run alembic current
gcloud run revisions list --service=admin-backend \
    --region=asia-south1 --project=ithina-retail-admin --limit=1
```

Three pieces to record into `docs/build-history.md`:
- **Commit SHA** (the `6178546`-shaped hash).
- **Local alembic head** (the `22ccfb193cff`-shaped revision after Claude Code's migrations applied).
- **Current Cloud Run revision** (the `admin-backend-00006-jzt`-shaped name; this is the rollback target if something later breaks).

The snapshot is the recovery reference. Without it, "what was running before this step" becomes archaeology.

### Step 3 — Push to origin

```bash
git push origin main
```

Expected: clean fast-forward, no force, no merge required. If `git push` reports "branch is behind origin," someone else committed. Investigate before proceeding.

### Step 4 — Deploy local app

Stop any running uvicorn from a previous session, then start fresh:

```bash
# In a dedicated terminal
cd ~/ithina-retail/admin-backend/
uv run uvicorn admin_backend.main:app --reload --port 8000
```

***Use "Ctrl + C" for closing the application***

Watch the startup log. Expect:

- "Application startup complete." (no errors)
- DB connection established.
- No alembic-mismatch warnings (means local is at the new head from Claude Code's migration).

If startup fails, surface the error before proceeding. Common causes:
- `.env` missing or mismatched with what the new code expects.
- A new migration the app code references hasn't applied yet (rare; Claude Code already runs `alembic upgrade head` as part of step closure).

### Step 5 — Validate local DB state

**Pre-condition: source `.env` in this terminal first.** Each new shell needs:

```bash
source scripts/env.sh
```

This loads `.env` AND derives `$PSQL_URL` (libpq form, sans SQLAlchemy `+psycopg` suffix). Without it, `alembic` fails with `RuntimeError: DB_SCHEMA env var is required` and `psql` fails to connect.

Confirm head + canonical row counts match Claude Code's report:

```bash
uv run alembic current
# Expected: <head from Claude Code's report> (e.g., 22ccfb193cff)

psql "$PSQL_URL" -c "SELECT list_name, COUNT(*) FROM core.lookups GROUP BY list_name ORDER BY list_name;"
# Expected: per-category counts match what the migration seeded.

# <step-specific row count query, e.g., for 6.1:>
psql "$PSQL_URL" -c "
  SELECT 'roles' as t, COUNT(*) FROM core.roles
  UNION ALL SELECT 'permissions', COUNT(*) FROM core.permissions
  UNION ALL SELECT 'role_permissions', COUNT(*) FROM core.role_permissions;"
# Expected: numbers from the Claude Code report.
```

If everything matches → skip step 6. If anything drifts → step 6.

### Step 6 — Re-seed if drifted (optional)

If step 5's row counts don't match the report, the local DB has drifted. Re-seed to a known-clean state:

```bash
python -m scripts.seed_dev_data --reset
```

The `--reset` flag truncates seed-loaded tables before re-inserting. Migrations remain applied — only seed data is replaced.

After re-seed, re-run step 5 queries. They should now match.

### Step 7 — Local smoke

Two options depending on confidence level needed.

**Option A — Full integration matrix (`test_endpoints.sh`):**

```bash
./scripts/test_endpoints.sh
```

Runs ~150 calls across 4 callers (2 PLATFORM, 2 TENANT in different tenants). Mints fresh JWTs from local DB, discovers fixture IDs, exercises every endpoint with sensible variations, saves all response bodies under `scripts/test_endpoints/results/<timestamp>/`. Exit 0 if all calls returned the expected status code.

This is the heavy regression check. Run after every build step before committing to cloud deploy.

**Option B — Light smoke (`smoke_curl.sh`):**

```bash
./scripts/smoke_curl.sh http://localhost:8000
```

Runs ~15 canonical curls. Consumes pre-minted JWT files. Faster (~5 seconds vs ~30 seconds for test_endpoints). Use when you've already run the full matrix recently and want quick re-verification.

Either passes or surfaces failures inline with response body excerpts. Failures fall into 3 buckets:

- **Code bug** → abort workflow. File a fix as a *new commit* (do NOT amend pushed history). Restart from step 1 of the next commit.
- **Env/config drift** → fix in place, re-run smoke. No need to re-commit unless config files in the repo changed.
- **Data issue** → back to step 6 (re-seed).

### Step 8 — Cloud SQL migration

The alembic Cloud Run Job executes the migration against Cloud SQL. Three sub-steps.

**8.0 — Read cloud alembic head (verify migration is needed)**

Before mutating any cloud state, read the current alembic head in Cloud SQL and compare against the local head captured in Step 2. Three outcomes are possible; only one of them proceeds to Step 8.

This check exists because:

- Most build steps don't ship a migration (e.g., Step 6.4 was alembic-head-unchanged). Running the alembic Job blindly wastes 30-90 seconds and pollutes the audit trail by logging an "applied" event that didn't apply anything.
- If a prior workflow run partially succeeded (Step 8 ran, Step 10 didn't), cloud is already at the target. Re-running Step 8 is a no-op but the build-history loses fidelity if "applied" and "already there" both look the same.
- If cloud is ahead of local (rare: a hotfix applied directly via Cloud SQL Studio, or a separate workflow ran in parallel), running `alembic upgrade head` from the new image is undefined behaviour. You want to know about this **before** kicking off the Job, not after.

**8.0.1 — Read cloud head and local head **

Override the alembic Job's args temporarily, execute, capture the head, revert. Same override-execute-revert pattern as Step 4.1's verify-schema flow:

```bash
# 1. What's in cloud:
gcloud beta run jobs executions logs read admin-backend-alembic-pxpzb \
    --region=asia-south1 --project=ithina-retail-admin

# 2. What's in local:
source scripts/env.sh
uv run alembic current
```

**8.0.2 — Compare and decide.**

Three cases, three actions:

| Cloud head vs Local head                                     | Action                                                       |
| ------------------------------------------------------------ | ------------------------------------------------------------ |
| `CLOUD_HEAD_BEFORE == LOCAL_HEAD`                            | **Skip Step 8 entirely.** No migration is needed. Proceed to Step 9 (the image still needs to build, push, and deploy because the **code** may have changed even when the **schema** didn't). Record in build-history as `Migration: skipped (cloud already at $LOCAL_HEAD)`. |
| `CLOUD_HEAD_BEFORE` is an ancestor of `LOCAL_HEAD` in local alembic history | **Normal case.** Proceed to Step 8.1. The Job will apply N migrations from `CLOUD_HEAD_BEFORE` up to `LOCAL_HEAD`. Record in build-history as `Migration: applied $CLOUD_HEAD_BEFORE -> $LOCAL_HEAD`. |
| `CLOUD_HEAD_BEFORE` is NOT in local history (or local head is an ancestor of cloud head) | **STOP.** This is divergence. Cloud has revisions local doesn't know about, or local is behind cloud. Do not run the Job. Surface both heads to whoever owns the schema and resolve out-of-band. Re-running this step's workflow on top of divergence will not fix it. |

To check ancestry, run locally:

```bash
uv run alembic history | grep -E "$CLOUD_HEAD_BEFORE|$LOCAL_HEAD"
```

If both appear and `CLOUD_HEAD_BEFORE` is older in the printed order, it's an ancestor. If `CLOUD_HEAD_BEFORE` doesn't appear at all, that's the divergence case.

**8.0.3 — Handle the skip case in the build-history.**

When Step 8 is skipped, the build-history entry's "Alembic head (Cloud SQL dev)" field still reflects the head — it just didn't change this step. Add a `Migration` line to the build-history template (see "State snapshot format" section) that captures whether this step applied or skipped a migration. This makes the audit trail honest about what actually ran.

**8.1 — Sanity-check the alembic Cloud Run Job exists and is configured correctly:**

```bash
gcloud run jobs describe admin-backend-alembic \
    --region=asia-south1 --project=ithina-retail-admin
```

Verify these fields in the output:
- `Image:` — present (will be updated in step 9).
- `Command: alembic` and `Args: upgrade head` — confirms job runs the migration command.
- `Env vars:` includes `DB_SCHEMA: core`.
- `Secrets:` includes `DATABASE_URL` (linked to a Secret Manager entry).
- `Service account:` matches the project's admin-backend service account.
- `VPC access:` configured (job needs VPC route to reach Cloud SQL).

If any are missing, surface and fix before proceeding. The job was originally created in Step 4.1 (Cloud SQL bring-up); modifying it from scratch is an outside-this-doc concern.



**8.1.1 - Migration Checks** 

**A. The cloud's current head, as a single line.** Don't use the log-read approach at all. Run `alembic current` directly against Cloud SQL via the Auth Proxy, or as a one-shot Job invocation:

```bash
# Option 1 — one-shot Job override (slower, ~30s, but no proxy needed)
gcloud run jobs execute admin-backend-alembic \
    --region=asia-south1 --project=ithina-retail-admin \
    --args=current --wait

# Then read its logs
gcloud beta run jobs executions logs read <execution-name-from-output> \
    --region=asia-south1 --project=ithina-retail-admin
```

That gives you exactly one line: `2fdc4bc9f4cb (head)` (or whatever cloud is at). Much cleaner than the history dump.

**B. Recent migration application history (what was applied when).** 

Cloud Logging has every execution's logs going back 30 days. Use:

```bash
# List the last 5 executions
gcloud run jobs executions list \
    --job=admin-backend-alembic \
    --region=asia-south1 --project=ithina-retail-admin \
    --limit=5

# Read the logs of one specific execution (e.g., the most recent)
gcloud beta run jobs executions logs read <execution-name> \
    --region=asia-south1 --project=ithina-retail-admin
```

Each execution's log shows exactly what alembic did during that run. The execution from your earlier `pxpzb` log shows just the head:

```
2026-05-06 07:43:25 22ccfb193cff (head)
```

That was an `alembic current` invocation. The `xhn2x` log shows the full history because that one was an `alembic history` invocation.

**C. Just the alembic_version table value.** Most direct:

```bash
# Via Cloud SQL Auth Proxy (if running on your machine):
psql "$CLOUD_PSQL_URL" -c "SELECT version_num FROM core.alembic_version;"
```

Returns one row with the current head.

Skip the psql verification, use the one-shot Job approach

This works without any local network setup. Run alembic current as a one-shot Job override:

```bash
gcloud run jobs execute admin-backend-alembic \
    --region=asia-south1 --project=ithina-retail-admin \
    --args=current --wait
```

Capture the execution name from the output (last line, looks like `admin-backend-alembic-XXXXX`). Then: Note placeholder in below command <execution-name-from-above>, it has to be replaced by whatever came in previous command -  `admin-backend-alembic-XXXXX`

```bash
gcloud beta run jobs executions logs read <execution-name-from-above> \
    --region=asia-south1 --project=ithina-retail-admin \
    | grep -E '^[0-9-]+ [0-9:]+ [a-f0-9]{12}'
```

That returns one line — the cloud head. Takes ~30s for the Job to spin up.

**8.1.2 — Update the Job's image to the new version**

First, decide the new version tag. If last deployed service was `v0.1.8`. **Bump to `v0.1.9`.**

Set the version in your shell so subsequent commands can reuse it:

bash

```bash
VERSION=v0.1.9
echo "VERSION=$VERSION"
```

My mistake. The doc's note about lazy-pull (line 319) was wrong, or `gcloud` validates the image at update time. Either way, the workflow has to be: build and push **first**, then update the Job.

This is the alternative ordering the doc itself mentions at line 321: *"If you want to be cautious and avoid this implicit ordering, you can re-order: build + push first (steps 9.1, 9.2), then update the alembic job's image (step 8.2), then execute (step 8.3). The deploy script (`scripts/deploy-cloud-run.sh`) follows this order. Either is correct."*

So we jump to Step 9, then come back to 8.2.



**8.2 — Update the job's image to the version we're about to deploy:**

```bash
gcloud run jobs update admin-backend-alembic \
    --image=asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend:vX.Y.Z \
    --region=asia-south1 --project=ithina-retail-admin --quiet
```

This is a config change only. The job is now "ready to run migrations from vX.Y.Z" but hasn't actually run yet.

**8.3 — Execute the job:**

```bash
gcloud run jobs execute admin-backend-alembic \
    --region=asia-south1 --project=ithina-retail-admin --wait
```

The `--wait` flag is critical — without it, the command returns before the migration finishes. The migration typically takes 30-90 seconds (most of that is container startup; actual SQL is sub-second).

**8.4 — Verify the migration log:**

```bash
gcloud beta run jobs executions logs read <execution-name-from-step-3-output> \
    --region=asia-south1 --project=ithina-retail-admin
```

Expect lines like:
```
INFO  [alembic.runtime.migration] Running upgrade <prev> -> <new>, <step_name>
```

One line per migration applied. No tracebacks, no partial-application warnings.

**On failure:** STOP. Do not retry. Inspect Cloud SQL state directly (via Cloud SQL Auth Proxy or by running `verify_cloud_schema.py` as a one-off Cloud Run Job invocation) to see exactly what's applied. Half-migrated states require manual SQL recovery.

### Step 9 — Build + push image to Artifact Registry

The image bundles the new code AND the migration files. Both travel together.

**What was the last version ?** 

```bash
gcloud run services describe admin-backend \
    --region=asia-south1 --project=ithina-retail-admin \
    --format='table(
        status.latestReadyRevisionName,
        spec.template.spec.containers[0].image,
        status.conditions[0].lastTransitionTime
    )'
```

**Build image and update artifact registry**

```bash
docker build \
    --build-arg SERVICE_VERSION=vX.Y.Z \
    -t asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend:vX.Y.Z \
    .
```

```bash
gcloud auth configure-docker asia-south1-docker.pkg.dev --quiet  # idempotent
```

```bash
docker push asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend:vX.Y.Z
```

The `SERVICE_VERSION` build-arg bakes the version into the image's environment so `/api/v1/health` can report it without depending on env var injection.

The push transfers only new layers (most are cached from prior versions). Final line of `docker push` reports the image digest — capture it for the build-history snapshot.

**Note:** the alembic job in step 8 was updated to use this same image tag (vX.Y.Z) BEFORE we pushed it. That works because `gcloud run jobs update` only changes the spec; the job doesn't actually pull the image until step 8.3 executes it. By that point, the push has completed.

If you want to be cautious and avoid this implicit ordering, you can re-order: build + push first (steps 9.1, 9.2), then update the alembic job's image (step 8.2), then execute (step 8.3). The deploy script (`scripts/deploy-cloud-run.sh`) follows this order. Either is correct.

### Step 10 — Deploy to Cloud Run

```bash
gcloud run deploy admin-backend \
    --image=asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend:vX.Y.Z \
    --update-env-vars=SERVICE_VERSION=vX.Y.Z \
    --region=asia-south1 --project=ithina-retail-admin --quiet
```

Cloud Run creates a new revision (`admin-backend-00007-XXX`-shaped name), runs the startup health check on it, and shifts 100% of traffic when it passes.

**`--update-env-vars` is additive.** It only changes the named env var and leaves the other ~13 env vars (DATABASE_URL, JWT_AUDIENCE, etc.) untouched. **Never use `--set-env-vars`** — that REPLACES the entire env-var set and would strip every other var the app needs.

Capture the new revision name (in the deploy output) for the build-history snapshot.

### Step 11 — Cloud smoke

```bash
./scripts/smoke_curl.sh https://admin-backend-315143921819.asia-south1.run.app
```

Same script as local smoke (step 7), different target URL. Verifies:
- Health responds, version reports the deployed tag.
- All RBAC + regression endpoints return expected status codes.
- TENANT-side checks run if a TENANT JWT is available (see "JWT sourcing" below).

If smoke fails, the rollback is one command:

```bash
gcloud run services update-traffic admin-backend \
    --to-revisions=<previous-revision-name>=100 \
    --region=asia-south1 --project=ithina-retail-admin
```

The previous revision name is the rollback target captured in step 2.

### Step 12 — Mark step DONE

Append the cloud-verified entry to `docs/build-history.md`:

```
## Step X.Y — <title> — <date>

- Commit SHA: <sha>
- Alembic head (local): <rev>
- Alembic head (Cloud SQL dev): <rev>
- Cloud Run revision (pre-deploy): <revision>   ← rollback target
- Cloud Run revision (post-deploy): <revision>  ← currently serving
- Image deployed: vX.Y.Z (digest sha256:...)
- Step closure timestamp: <utc-iso>

Rollback command:
  gcloud run services update-traffic admin-backend \
    --to-revisions=<pre-deploy-revision>=100 \
    --region=asia-south1 --project=ithina-retail-admin
```

This is the audit trail. Six weeks from now when something breaks, this file tells you exactly what was deployed when.

`BUILD_PLAN.md`'s step entry should already be marked DONE by Claude Code's commit. Double-check:

```bash
grep -A 2 "Step X.Y" BUILD_PLAN.md
# Expected: **Status.** DONE
```

---

## Decision rules

Branch points in the workflow where operator judgment is exercised.

### Step 1 — Approve / Edit / Abort

- Approve when staged files match report and message is accurate.
- Edit when message needs tweaking but file scope is correct.
- Abort when scope is wrong or report doesn't match staging — re-prompt Claude Code.

### Step 5 → 6 — Re-seed or skip

- Skip if all DB checks match Claude Code's report.
- Re-seed if drift detected, or if you want a known-clean baseline before manual exploration.

### Step 7 / 11 — Smoke triage

Three buckets:
- **Code bug** → abort, file fix as new commit, restart workflow at step 1 of next commit. Do NOT amend pushed history (avoid force-push on shared branch).
- **Env/config drift** → fix in place, re-run smoke. Document if config files in repo changed.
- **Data issue** → back to step 6 (re-seed local) or investigate cloud DB drift.

### Demo-imminent rule

**Never deploy to cloud within 2 hours of a scheduled demo against the target environment.** Cloud deploys go either before a demo with full verification time, or well after. Single exception: rollback to a known-good revision when something is actively broken in the demo environment.

### Schema-change shape and migration-deploy ordering

For most build steps (additions, narrowings of unused values), the canonical order is:
1. Migrate first (step 8 before step 10).
2. Deploy app code that depends on the migrated schema.

For breaking schema changes (dropping a column or table that production code currently uses), the order flips:
1. Deploy app code that stops using the column.
2. Migrate the schema to drop it.
3. (Optional cleanup commit.)

v0 should not need this — but knowing the principle exists prevents a surprise when it does.

---

## Pre-conditions per step

Things that must be true at the start of each step. Each one corresponds to a real failure mode observed.

### Step 5 — `.env` MUST be sourced in the active terminal

**Why:** uvicorn loads `.env` at startup. Parallel terminals don't inherit those vars.

**Symptoms when missed:**
- `RuntimeError: DB_SCHEMA env var is required` from alembic (D-15).
- `connection failed: socket /var/run/postgresql/.s.PGSQL.5432` from psql (DATABASE_URL not set, falls back to default socket).

**Fix:** `source scripts/env.sh` at the start of any new terminal.

### Step 5 — `DATABASE_URL` is SQLAlchemy-formatted; psql needs the prefix stripped

**Why:** SQLAlchemy uses `postgresql+psycopg://...` to specify the driver; psql/libpq don't understand the `+psycopg` suffix.

**Fix:** `scripts/env.sh` derives `$PSQL_URL` from `$DATABASE_URL` automatically. Use `psql "$PSQL_URL"` for direct queries; use `$DATABASE_URL` for alembic and the app.

### Step 5 — `psql -tAc` may have whitespace

**Why:** psql's tuple-only mode sometimes leaves trailing whitespace.

**Fix:** pipe through `xargs` when capturing into a shell variable:

```bash
OWNER_ID=$(psql "$PSQL_URL" -tAc "SELECT id FROM core.roles WHERE code='OWNER';" | xargs)
```

### Step 7 — JWT must be pre-generated against a real seeded user

**Canonical script:** `./scripts/jwt/generate_7d.sh <email>`. Examples:
- `./scripts/jwt/generate_7d.sh anjali@ithina.ai` (PLATFORM user)
- `./scripts/jwt/generate_7d.sh marcus.t@bucees.com` (TENANT user)

JWTs land at `scripts/jwt/tokens/<email-prefix>-7d.jwt`.

**Do NOT** call `make_test_jwt()` directly with kwargs only — its signature is `make_test_jwt(settings, user_type=..., ...)` with `settings` as the first positional argument. Bare kwargs raise `TypeError`.

### Step 7 / 11 — API prefix is `/api/v1/`, NOT `/v1/`

Every endpoint lives under `/api/v1/...`. The OpenAPI spec is at `/api/v1/openapi.json`.

If you see `{"detail": "Not Found"}` from a request, check the path — that's FastAPI's default 404 envelope (vs the structured `AUTH_MISSING` envelope you'd get from a real route returning 401).

### Step 8 — alembic Cloud Run Job sanity check

Always run `gcloud run jobs describe admin-backend-alembic ...` before invoking the job. Confirms:
- Image is set (will be updated to the new version).
- Command is `alembic`, args are `upgrade head`.
- DB_SCHEMA env var is `core`.
- DATABASE_URL secret is mounted.
- Service account + VPC config are present.

A missing or misconfigured job will fail in confusing ways. The describe takes 5 seconds and surfaces issues before they cost you a half-deployed state.

### Step 11 — Cloud smoke needs JWTs that work against the deployed environment

PLATFORM JWT works universally (no `tenant_id` claim, valid against any DB).

TENANT JWT minted from local DB has the LOCAL tenant_id, which doesn't exist in cloud. Cross-tenant tests in cloud need a tenant JWT minted with cloud-side IDs.

**For now, smoke against cloud uses PLATFORM-only.** TENANT cloud tests require manual JWT minting against the cloud DB — see "Future improvements" below.

---

## Tooling

### Scripts in `scripts/`

| Script | Purpose | When to run |
|---|---|---|
| `env.sh` | Source `.env` + export `$PSQL_URL` | At the start of every new terminal |
| `check_setup.sh` | 35-check pre-flight (DB up, deps in sync, etc.) | Before starting any session |
| `jwt/generate.sh` | Mint 1-hour JWT (canonical for testing) | When test_endpoints.sh needs fresh JWTs |
| `jwt/generate_7d.sh` | Mint 7-day JWT (canonical for frontend integration) | Before smoke_curl.sh; for dev integration |
| `test_endpoints.sh` | Comprehensive local integration matrix (~150 calls) | Step 7 — local smoke after every build step |
| `smoke_curl.sh` | Light smoke (~15 endpoints) for any base URL | Steps 7 + 11 — fast verification |
| `deploy-cloud-run.sh` | Single-command build + push + migrate + deploy | Steps 8-10 if you want one-shot automation |
| `verify_cloud_schema.py` | Inspect cloud DB schema + alembic head | Step 8 verification when needed |

### `scripts/env.sh` usage

```bash
source scripts/env.sh    # load .env into current shell
echo $DB_SCHEMA          # → core
echo $DATABASE_URL       # → postgresql+psycopg://...   (SQLAlchemy form)
echo $PSQL_URL           # → postgresql://...           (libpq form)
```

After sourcing once, `psql`, `alembic`, `pytest` all work without further configuration in this terminal.

### `scripts/smoke_curl.sh` usage

```bash
# Local
./scripts/smoke_curl.sh http://localhost:8000

# Cloud
./scripts/smoke_curl.sh https://admin-backend-315143921819.asia-south1.run.app

# Override JWT files (advanced)
PJWT_FILE=path/to/p.jwt TJWT_FILE=path/to/t.jwt ./scripts/smoke_curl.sh <url>
```

Default JWT paths: `scripts/jwt/tokens/anjali-7d.jwt` (PLATFORM, required) + `scripts/jwt/tokens/marcus-t-7d.jwt` (TENANT, optional).

If TENANT JWT is missing, smoke skips the tenant-specific assertions and runs PLATFORM-only.

### `scripts/test_endpoints.sh` usage

```bash
./scripts/test_endpoints.sh
```

No arguments needed for default seed users. Override emails via positional args if you've seeded different users:

```bash
./scripts/test_endpoints.sh anjali@ithina.ai devon@ithina.ai \
    marcus.t@bucees.com a.kowalski@zabka.pl
```

Override base URL via env:
```bash
BASE_URL=http://localhost:9000 ./scripts/test_endpoints.sh
```

**Note:** `test_endpoints.sh` queries LOCAL Postgres for fixture IDs and JWT minting. Running it against `BASE_URL=https://...run.app` works for the HTTP calls but mints JWTs from local DB, which won't see cloud rows. Use `smoke_curl.sh` for cloud, `test_endpoints.sh` for local.

---

## Pitfalls log

Numbered list of real failures observed during workflow execution. Reference these when something breaks.

1. **`make_test_jwt()` raises `missing required argument 'settings'`** when called as `make_test_jwt(user_type='PLATFORM')`. Cause: signature is `make_test_jwt(settings, user_type=..., ...)`. Fix: use `./scripts/jwt/generate_7d.sh <email>` instead of inline Python.

2. **jq shorthand `{module, resource, action}` raises syntax error.** Older jq versions don't support shorthand object construction. Fix: use explicit `{module: .module, resource: .resource, ...}`, or just `.items[0]` for full object render.

3. **Curl returns `{detail: "Not Found"}` on a path that should exist.** Cause: API prefix is `/api/v1/`, not `/v1/`. Fix: hardcode the prefix in every curl command and lock it in `smoke_curl.sh`.

4. **Curl returns `{code: AUTH_MISSING}` despite passing `-H "Authorization: Bearer $PJWT"`.** Cause: `$PJWT` was empty (set via `||` chain that masked failure). Fix: explicit assignment + sanity check (`echo ${PJWT:0:20}` to verify non-empty).

5. **`num_nodes: null` in `/api/v1/tenants` response.** This was a Step 5.3 forward-looking spec that didn't ship in the actual commit. Not a regression. Fix: smoke assertions match what actually shipped, not what was proposed.

6. **`docker images | grep admin-backend | head -5` truncated v0.1.5 from output.** Tags are sorted by underlying layer creation time, not tag name. Fix: don't use `head -5` when you need to find a specific tag — use `docker images <full-image-ref>` to filter.

7. **Pre-flight item "Step 5.3 at HEAD" is over-strict.** Small follow-up commits land between steps; phrasing should be "alembic head matches expected, no migration drift" rather than "at HEAD."

8. **`psql -tAc` whitespace in captured shell variable.** Pipe through `xargs` when assigning to a shell var that will be interpolated into URLs.

9. **Demo-imminent deploy.** Rule: never deploy to cloud within 2 hours of a scheduled demo against the target environment. Single exception: rollback to a known-good revision when actively broken.

---

## State snapshot format

Every step appends one entry to `docs/build-history.md`. Format:

```
## Step X.Y — <title> — <date>

- Commit SHA: <sha>
- Alembic head (local): <rev>
- Alembic head (Cloud SQL dev): <rev>
- Cloud Run revision (pre-deploy): <revision-name>   ← rollback target
- Cloud Run revision (post-deploy): <revision-name>  ← currently serving
- Image deployed: vX.Y.Z (digest sha256:<digest>)
- Step closure timestamp: <utc-iso>

Rollback command:
  gcloud run services update-traffic admin-backend \
    --to-revisions=<pre-deploy-revision>=100 \
    --region=asia-south1 --project=ithina-retail-admin
```

The pre-deploy revision is the rollback target; the post-deploy revision is what the next step will treat as ITS rollback target.

---

## Step 6.1 worked example

A concrete walkthrough of the workflow as executed for Step 6.1 (RBAC read endpoints).

**Input:** Claude Code's report ending with proposed commit message for Step 6.1.

**Step 1 — Validate.** Reviewed Bundle 1's file list (13 new + 10 modified files); cross-referenced against `git status`; commit message matched scope. Approved.

**Step 2 — Local commit + snapshot.**
```
Commit SHA: 6178546
Local alembic head: 22ccfb193cff
Cloud Run revision (pre-deploy): admin-backend-00006-jzt
```

**Step 3 — Push.** `git push origin main`. Clean fast-forward: `715d298..6178546`.

**Step 4 — Local app.** `uv run uvicorn admin_backend.main:app --reload --port 8000`. Started cleanly.

**Step 5 — DB state.** `source scripts/env.sh`. Then:
- `alembic current` → `22ccfb193cff` ✓
- Lookups counts: module=4, resource=12, permission_action=6, permission_scope=3 ✓
- Legacy permission rows deleted: 0 ✓
- roles=15, permissions=23, role_permissions=113 ✓

All matched Claude Code's report. Skipped step 6.

**Step 6 — Skipped.** No drift.

**Step 7 — Local smoke.** Manual curls (smoke_curl.sh hadn't been written yet at this point in the workflow's first run). All 4 RBAC endpoints + tenants regression + lookups regression returned 200 with expected shapes. One false alarm: `num_nodes: null` on tenants response — investigated, turned out to be a phantom expectation (see pitfall #5).

**Step 8 — Cloud SQL migration.**
- 8.1 sanity check: `gcloud run jobs describe admin-backend-alembic` → image v0.1.2, args `alembic upgrade head`, DB_SCHEMA=core, DATABASE_URL via secret, VPC configured. ✓
- 8.2 update job image: v0.1.2 → v0.1.5 ✓
- 8.3 execute: `--wait` returned in ~30 seconds, exit 0.
- 8.4 verify logs: both migrations applied (`0644a4186e48 -> 90cd038ae618` then `90cd038ae618 -> 22ccfb193cff`). No tracebacks.

**Step 9 — Build + push image.**
- Docker build: 1.5 sec (most layers cached).
- `docker push`: image digest captured: `sha256:c848ed44cb13532024ae0967050f7aec899313ab20971a066f7ed7dd833c6ba5`.

**Step 10 — Deploy to Cloud Run.** New revision `admin-backend-00007-6kb` deployed at `2026-05-05T14:25:58Z`. Serving 100% traffic.

**Step 11 — Cloud smoke.** Manual curls (since smoke_curl.sh didn't exist yet). All checks green:
- Health version: `v0.1.5` ✓
- Roles PLATFORM: 3 + 12 ✓
- Permissions: 23 ✓
- Matrix: 15×23×15 ✓ (all labels populated)
- Tenants regression: 7 tenants ✓
- Lookups regression: ✓

**Step 12 — Mark DONE.** BUILD_PLAN.md already marked DONE by Claude Code's commit. Snapshot appended to `docs/build-history.md` (this file's first entry).

Total elapsed time: ~90 minutes (including discussion of unfamiliar mechanics on first run; expect 30-45 min for subsequent steps).

---

## Future improvements

Things this doc identifies as worth doing but doesn't currently cover.

### Cloud-tenant JWT minting

Currently TENANT JWTs are minted against local Postgres (`scripts/jwt/generate_7d.sh` queries `tenant_users` from local DB). The minted JWT contains the local `tenant_id`, which doesn't match cloud's `tenant_id` for the same email.

For cloud cross-tenant testing, JWTs need to be minted with cloud-side UUIDs. Two paths:
- **(a) New tooling step:** write `scripts/jwt/generate_cloud.sh` that uses Cloud SQL Auth Proxy or a dedicated Cloud Run Job to query cloud DB and mint a JWT with the right tenant_id. Lands as its own build step when tenant cross-environment testing becomes a real need.
- **(b) Manual inline minting:** the pattern from `prompts/step-4_4-cloud-run-deploy-dev.md` section 5 — use Python REPL with hardcoded cloud tenant UUIDs. Acceptable for one-off testing but doesn't scale.

For now, `smoke_curl.sh` against cloud uses PLATFORM-only assertions (universal across environments).

### Per-step regression checkpoint script

`test_endpoints.sh` already provides full regression coverage. A leaner per-resource regression checkpoint (each existing pytest file's count compared pre/post step) would be useful for catching subtle integration regressions that don't surface in HTTP smoke.

This is partially captured in Claude Code's report (`per-file pytest count` is in the workflow's Step 6.1 report), but a standalone script that runs the same checks would make it accessible outside of Claude Code's flow.

### Schema diff before migration

`pg_dump --schema-only` before and after migration, with `diff` between, gives high-confidence assurance that migration touched ONLY the tables/types it claimed to touch. Could be added as step 8.5 (post-migration verify) for breaking-change steps.

### Build-history automation

Currently `docs/build-history.md` is appended manually after each step. Worth scripting: a `scripts/append_build_history.sh` that takes a commit SHA + step name and produces the entry from `git log` + `gcloud` queries.

### Cloud Run revision pinning

Each step's snapshot captures the revision-name; a future hardening: tag the Artifact Registry image with the step name (e.g., `:step-6.1`) so the rollback target is greppable by step rather than by revision SHA.

---

## Out of scope

This doc deliberately does NOT cover:

- **Production deploy workflow.** No production environment yet; doc covers cloud-dev only. When prod splits off, this doc gets a section 13.
- **Multi-developer coordination** (locking, branch protection, PR review). Current flow is solo-commit-to-main; team coordination joins when ≥2 developers push concurrently.
- **Database backup / restore procedures.** Out-of-band ops concern.
- **Cloud Run scaling configuration.** Workflow assumes existing service config is correct.
- **Auth0 integration.** Current flow uses local JWT signing.
- **Frontend deployment.** `admin-frontend` has its own deploy path; if that becomes coupled to backend deploys, joint workflow joins here.

These get their own docs when the need arises.

---

## Summary

The workflow is 12 steps. Two existing scripts (`test_endpoints.sh`, `deploy-cloud-run.sh`) automate large chunks. Two new helpers (`env.sh`, `smoke_curl.sh`) close the per-terminal-state and cloud-smoke gaps respectively. State snapshots in `build-history.md` make rollback feasible at any step.

Run through it for one build step (Step 6.1 above is the worked example). The second build step you do should take half the time of the first.
