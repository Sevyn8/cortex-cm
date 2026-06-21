# Prompt — Step 4.1 (re-ordered, Cloud Run Job): Cloud SQL schema bring-up

> Paste this entire block into a fresh Claude Code session when starting Step 4.1.
> **Re-ordered:** original BUILD_PLAN had this before 4.2/4.3, but Cloud SQL is
> private-IP-only, so Alembic must run from inside the VPC, which means the
> backend image must already be in Artifact Registry. Run AFTER 4.2/4.3.
>
> **Cloud Run Job approach:** dev runs the backend on Cloud Run (not GKE per
> D-31). Schema bring-up uses a Cloud Run Job — same image, alembic command,
> via VPC egress to private Cloud SQL.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report.
2. Read `CLAUDE.md` fully — focus on Alembic conventions, migration round-trip discipline, "Schema and storage".
3. Read `docs/architecture.md` "Schema and storage", "Multi-tenancy and data isolation", and the new D-31 entry (dev on Cloud Run; prod on GKE).
4. Read `BUILD_PLAN.md` Step 4.1.
5. Read `BUILD_PLAN.md` Step 1.5 (smoke test) — script is re-run here against Cloud SQL.
6. Read this prompt fully and confirm scope.

---

## Step ID and intent

**Step 4.1** — Apply Alembic migrations to Cloud SQL dev instance and verify schema integrity end-to-end against managed Postgres.

Why this exists separately from local Alembic (Step 1.6): Cloud SQL has subtle differences from local Docker Postgres (extension availability, default `search_path`, role permissions, RLS behaviour under managed Postgres). We verify migrations apply identically and the cross-tenant RLS smoke test passes against cloud, not just local.

This is a CLAUDE_CODE step. The user runs `gcloud` interactively where their auth is needed; Claude Code orchestrates the verification and runs the smoke test.

---

## Scope in

### Prerequisites

- Terraform `envs/dev` applied. Resources exist:
  - Cloud SQL with private IP
  - Secret Manager containers populated (especially `admin-backend-database-url`)
  - Artifact Registry with `admin-backend:v0.1.0` pushed (Steps 4.2/4.3)
  - Cloud Run Job `admin-backend-alembic` exists (placeholder image)
- Terraform outputs available:
  ```bash
  PROJECT=ithina-retail-admin
  REGION=asia-south1
  DEV=../terraform/envs/dev   # adjust to your repo layout

  AR_URL=$(terraform -chdir=$DEV output -raw artifact_registry_url)
  JOB=$(terraform -chdir=$DEV output -raw backend_alembic_job_name)
  ```

### Steps to perform

1. **Confirm the DATABASE_URL secret has a real value** (not just an empty container):
   ```bash
   gcloud secrets versions list admin-backend-database-url --project=$PROJECT
   ```
   At least one ENABLED version must exist. If not, populate now from Terraform's assembled URL:
   ```bash
   terraform -chdir=$DEV output -raw backend_database_url | \
     gcloud secrets versions add admin-backend-database-url \
       --data-file=- --project=$PROJECT
   ```

2. **Update the Cloud Run Job to point at the real image** (Terraform leaves it on the placeholder):
   ```bash
   gcloud run jobs update $JOB \
     --image="${AR_URL}/admin-backend:v0.1.0" \
     --region=$REGION --project=$PROJECT
   ```
   This patches the Job spec; subsequent executions use the new image.

3. **Execute the Job and wait** for it to complete:
   ```bash
   gcloud run jobs execute $JOB \
     --wait --region=$REGION --project=$PROJECT
   ```
   The `--wait` flag streams the execution status; Cloud Run Job logs go to Cloud Logging. Expected wall-clock: 30-90 seconds for a fresh apply.

4. **Inspect the execution log**:
   ```bash
   EXEC=$(gcloud run jobs executions list --job=$JOB \
     --region=$REGION --project=$PROJECT \
     --limit=1 --format='value(name)')
   gcloud beta run jobs executions logs read $EXEC \
     --region=$REGION --project=$PROJECT
   ```
   Expected ending: `INFO  [alembic.runtime.migration] Running upgrade ...` lines for each migration, finishing at the head revision. No errors.

5. **Verify schema** by triggering a one-shot read using `gcloud sql connect` or by running a verification job. Cleanest: a second one-shot Cloud Run Job execution that runs an inline Python script. For v0 simplicity, use `gcloud sql connect` with operator credentials by temporarily authorizing:

   **Option A** (preferred for repeatability — write a verification script):
   Create `scripts/verify_cloud_schema.py` that connects via `DATABASE_URL` env, prints:
   - All tables in `core` schema
   - Tables with `forcerowsecurity = true`
   - Alembic head revision

   Run it as a one-shot Cloud Run Job execution by overriding the command:
   ```bash
   gcloud run jobs update $JOB \
     --command="python" \
     --args="/app/scripts/verify_cloud_schema.py" \
     --region=$REGION --project=$PROJECT
   gcloud run jobs execute $JOB --wait --region=$REGION --project=$PROJECT
   gcloud beta run jobs executions logs read $(gcloud run jobs executions list --job=$JOB --region=$REGION --project=$PROJECT --limit=1 --format='value(name)') --region=$REGION --project=$PROJECT
   ```

   Then revert the Job back to alembic command for future re-use:
   ```bash
   gcloud run jobs update $JOB \
     --command="alembic" --args="upgrade,head" \
     --region=$REGION --project=$PROJECT
   ```

   **Option B** (faster, less repeatable — operator workstation):
   Use Cloud SQL Studio in the GCP Console to run verification SQL directly. Acceptable for one-time bring-up; not for CI/recurring use.

   Pick Option A. It's the right shape and reusable later.

6. **Run the smoke test** (`scripts/smoke_test.py` from Step 1.5) against Cloud SQL via the same Job pattern. Override command to `python /app/scripts/smoke_test.py` if the script is included in the image (verify in the Dockerfile from Step 4.2 — `COPY scripts/ /app/scripts/` should be there; if not, add it as a tweak to the Dockerfile and rebuild).

   Expected: all smoke-test PASS lines as in local. If any fail, stop and surface — cloud-specific behaviour discovered.

---

## Scope out

- **Application deploy** (the Cloud Run service for the long-running backend) — Step 4.4.
- **HPA-equivalent (concurrency tuning, max instances)** — set in Terraform; tune later if needed.
- **Production Cloud SQL** — Step 8.x.
- **Alembic downgrade verification in cloud** — local round-trip (Step 1.6) covers it; cloud-side downgrade isn't useful for v0.

---

## Implementation hints

- Cloud Run Jobs use the same image as the service. The image is bundled with `migrations/` and `alembic.ini` per the Step 4.2 Dockerfile (`COPY` lines must be present).
- Cloud Run Jobs reach Cloud SQL via the VPC egress configured on the Job (set in Terraform `cloud-run-backend` module). Direct VPC egress with `PRIVATE_RANGES_ONLY` routes the private-IP traffic correctly.
- The Job's runtime SA already has `roles/cloudsql.client` — but with private-IP egress, that role isn't strictly needed (it's for the Cloud SQL connector). It's harmless.
- If `CREATE EXTENSION ltree` fails: Cloud SQL's `cloudsqlsuperuser` role can create extensions. The application user (`user_admin_backend`) was created by Terraform without superuser privileges. You may need to either grant `CREATE` on schema or run extension creation from a privileged session. **Surface this if it happens** — fix is documented in Cloud SQL release notes; small DDL change.
- `gcloud beta run jobs executions logs read` requires `gcloud components install beta` first if not already installed.
- Cloud Run Jobs executions persist in Cloud Logging — easy to audit later.

---

## Acceptance criteria

- Cloud Run Job execution completes with success status (`gcloud run jobs executions describe` shows `condition.type=Completed, status=True`).
- Execution logs show all migrations applied to head with no errors.
- Schema verification (Option A): 10 application tables in `core` schema, 5 multi-tenant tables with `forcerowsecurity=true`, alembic_version row at head.
- Smoke test: all PASS lines (or, if smoke-test step is deferred, deferral documented in the report with a follow-up step ID).
- mypy strict still clean (no source changes here aside from the verify script).

---

## Stop and ask if

- Job execution fails with image-pull error (IAM issue — surface; default Cloud Run Job runtime should be able to pull from Artifact Registry in the same project, but if not, may need explicit `roles/artifactregistry.reader` on the runtime SA).
- `CREATE EXTENSION ltree` fails (privilege issue — see hints).
- Migration applies locally but errors against Cloud SQL (cloud-specific behaviour — needs investigation, possibly a CLAUDE.md "Cloud-specific differences" entry).
- Smoke test reveals cross-tenant isolation issues — disaster-class per architecture; stop and surface immediately.

---

## What to report at end (5-bundle convention)

1. **Code/configs:** `scripts/verify_cloud_schema.py` if Option A taken (line count). Any Dockerfile tweak (e.g., adding `COPY scripts/`). The exact `gcloud run jobs update` and `execute` commands used (paste in report so they're a record).
2. **CLAUDE.md updates:** "Current state" entry — Cloud SQL dev schema at head revision <hash>. Any cloud-specific differences discovered.
3. **BUILD_PLAN.md updates:** Step 4.1 status flipped to DONE. Note the re-ordering (4.2/4.3 → 4.1 → 4.4) and the Cloud Run Job approach in the step description.
4. **architecture.md updates:** No change expected unless a cloud-specific behaviour was discovered worth documenting.
5. **Prompt file:** `prompts/step-4_1-cloud-sql-schema-bringup.md` confirmed in commit set.

Plus: full alembic execution log (truncated), schema verification output, smoke test output.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
