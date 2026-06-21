# Prompt — Step 4.4: Backend Cloud Run deploy + smoke + cross-tenant test (dev)

> Paste this entire block into a fresh Claude Code session when starting Step 4.4.
> Run AFTER Steps 4.2/4.3 (image pushed) and Step 4.1 (schema applied).
>
> **Replaces** the GKE/manifest-based Step 4.4 prompt. Per D-31, dev runs the
> backend on Cloud Run; prod will use GKE (separate prompt when prod time comes).

> **Status as of 2026-05-04.** Sections 1-4 (Cloud Run deploy v0.1.2,
> /api/v1/health smoke, OpenAPI fetch, log inspection) shipped
> 2026-05-03. Section 5 (cross-tenant isolation test) was blocked on
> seed data until 2026-05-04; unblocked by Step 4.3.5 run. This step
> flips to DONE when section 5 passes.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report.
2. Read `CLAUDE.md` fully — focus on environment variables required at startup, "Stack" section.
3. Read `docs/architecture.md` — focus on D-31 (dev on Cloud Run; prod on GKE), the dev-shape deployment topology, and "Network and security".
4. Read `docs/api-contract.md` — endpoint paths and `/api/v1/health` shape.
5. Read `BUILD_PLAN.md` Step 4.4 (Cloud Run version).
6. Read this prompt fully and confirm scope.

---

## Step ID and intent

**Step 4.4** — Roll the real backend image into the Cloud Run service, verify it serves traffic, and verify cross-tenant isolation works against the cloud database (not just local Docker).

This is a HYBRID step. Claude Code drives the deploy commands and tests; the user runs `gcloud` interactively where their auth is needed.

Deliverables:

1. Backend Cloud Run service running the real image.
2. `/api/v1/health` returns 200 via the public service URL.
3. Cross-tenant isolation verified end-to-end with two test JWTs.
4. Frontend team handed the deployed backend URL.

---

## Scope in

### Prerequisites

- Steps 4.2 and 4.3 done (image pushed to Artifact Registry).
- Step 4.1 done (Cloud SQL schema applied).
- Terraform `envs/dev` applied:
  ```bash
  PROJECT=ithina-retail-admin
  REGION=asia-south1
  DEV=../terraform/envs/dev

  AR_URL=$(terraform -chdir=$DEV output -raw artifact_registry_url)
  BACKEND_SVC=$(terraform -chdir=$DEV output -raw backend_service_name)
  ```
- JWT keys exist in Secret Manager (populated in Step 3 of the README).

### Steps to perform

1. **Deploy the real image to the backend Cloud Run service**. Terraform created the service with a placeholder; this rolls the real image in:
   ```bash
   gcloud run deploy $BACKEND_SVC \
     --image="${AR_URL}/admin-backend:v0.1.0" \
     --region=$REGION --project=$PROJECT
   ```
   Cloud Run will:
   - Pull the image
   - Spin up an instance
   - Run the startup probe against `/api/v1/health`
   - Mark traffic at 100% to the new revision once probe passes
   - Garbage-collect the placeholder revision

   Expected wall-clock: 30-90 seconds. Watch the streaming output.

2. **Get the service URL** (Terraform also outputs it; both should match):
   ```bash
   BACKEND_URL=$(gcloud run services describe $BACKEND_SVC \
     --region=$REGION --project=$PROJECT \
     --format='value(status.url)')
   echo "Backend URL: $BACKEND_URL"
   ```

3. **Smoke**:
   ```bash
   curl -i "${BACKEND_URL}/api/v1/health"
   ```
   Expected: 200 OK with the health response shape from `docs/api-contract.md`.

   ```bash
   curl -i "${BACKEND_URL}/api/v1/openapi.json" | head -20
   ```
   Expected: OpenAPI JSON document.

4. **Inspect logs** to confirm the app started cleanly and is connected to Cloud SQL:
   ```bash
   gcloud run services logs read $BACKEND_SVC \
     --region=$REGION --project=$PROJECT --limit=50
   ```
   Look for:
   - JSON-formatted startup log lines (per D-20)
   - No DB connection errors
   - No "secret not found" errors (means JWT keys mounted correctly)

5. **Cross-tenant isolation check**. Mint two JWTs locally (one per tenant). The keys mounted in Cloud Run are the same keypair as `backend/keys/`:
   ```bash
   # Real seed UUIDs from Step 4.3.5 (Buc-ee's, Żabka). The seed loader
   # uses UUIDv7 substitution at load time — local DB UUIDs differ from
   # cloud DB UUIDs because each --reset regenerates them. Use the
   # CLOUD-side UUIDs below when minting JWTs for the deployed-service
   # matrix; sanity-check locally first against local DB UUIDs to
   # confirm the JWT chain works before hitting cloud.
   uv run python -c "
   from admin_backend.auth.testing import make_test_jwt
   import uuid

   # Cloud-side UUIDs (post-Step-4.3.5):
   BUCEES_TENANT     = uuid.UUID('019df261-b878-7c78-ad1c-da36f80aa17c')
   BUCEES_USER       = uuid.UUID('019df261-b90e-784e-9d97-bc7ee2ed70be')  # marcus.t@bucees.com
   ZABKA_TENANT      = uuid.UUID('019df261-b87c-7d3e-ab9e-dcf26259cec6')
   ZABKA_USER        = uuid.UUID('019df261-b914-75c3-becd-75345876279b')  # a.kowalski@zabka.pl

   print('MARCUS:  ', make_test_jwt(user_id=BUCEES_USER, tenant_id=BUCEES_TENANT, user_type='TENANT'))
   print('KOWALSKI:', make_test_jwt(user_id=ZABKA_USER,  tenant_id=ZABKA_TENANT,  user_type='TENANT'))
   "
   ```

   Then `curl` both JWTs against tenant-scoped endpoints:
   ```bash
   curl -H "Authorization: Bearer $MARCUS_JWT"   "${BACKEND_URL}/api/v1/tenants"
   curl -H "Authorization: Bearer $KOWALSKI_JWT" "${BACKEND_URL}/api/v1/tenants"
   ```

   Cross-tenant matrix (6 calls, two tenants × own/cross/list shapes):

   | Call | Token | Expected |
   |---|---|---|
   | GET /api/v1/tenants                                            | MARCUS   | 200, items length 1, only Buc-ee's |
   | GET /api/v1/tenants                                            | KOWALSKI | 200, items length 1, only Żabka     |
   | GET /api/v1/tenants/019df261-b878-7c78-ad1c-da36f80aa17c       | MARCUS   | 200, Buc-ee's data |
   | GET /api/v1/tenants/019df261-b878-7c78-ad1c-da36f80aa17c       | KOWALSKI | **404** (RLS-as-404 per D-17) |
   | GET /api/v1/tenants/019df261-b87c-7d3e-ab9e-dcf26259cec6       | KOWALSKI | 200, Żabka data |
   | GET /api/v1/tenants/019df261-b87c-7d3e-ab9e-dcf26259cec6       | MARCUS   | **404** (RLS-as-404 per D-17) |

   Optional richer matrix (only if time permits): repeat for `/api/v1/tenant-users` and `/api/v1/stores`. Same isolation expected.

6. **Hand backend URL to frontend team**:
   ```
   Backend dev URL: <BACKEND_URL>
   OpenAPI: <BACKEND_URL>/api/v1/openapi.json
   CORS already includes the frontend Cloud Run URL (wired by Terraform).
   ```

   Frontend builds with `--build-arg NEXT_PUBLIC_API_BASE_URL=<BACKEND_URL>` and integrates daily from now on.

---

## Scope out

- **Frontend deploy** (separate prompt — `step-frontend-cloudrun-deploy.md` if needed).
- **Other endpoints (stores, etc.)** — Steps 4.5, 5.x.
- **TLS / managed cert / custom domain** — default `*.run.app` cert is fine for dev.
- **Concurrency tuning, min_instances** — set in Terraform; tune in a follow-up if cold starts hurt.
- **Production deploy** — separate prompt for GKE/manifests; Step 8.x.

---

## Implementation hints

- The Cloud Run service has `lifecycle.ignore_changes` on `image` in Terraform — `gcloud run deploy` won't churn Terraform state.
- Cold start for first request: 5-15s if scaled to zero. Subsequent requests under the keep-warm window (~15 min) are <100ms.
- If startup probe fails, the deploy fails and traffic stays on the placeholder revision. Read the failure log in the streaming `gcloud run deploy` output.
- VPC egress is set to `PRIVATE_RANGES_ONLY` — Cloud SQL traffic goes through the VPC, internet (Auth0 callbacks etc.) goes default route. If the app needs egress to a non-RFC1918 destination via the VPC (rare), switch to `ALL_TRAFFIC` in Terraform.
- The `CORS_ALLOWED_ORIGINS` env var was set by Terraform at apply time to include the frontend Cloud Run URL. If the frontend URL changes for any reason, re-apply Terraform to refresh.
- Cloud Run does not allow port 8000 directly — it sets `$PORT` (default 8080) and the container must listen there. **Check the Dockerfile from Step 4.2**: if `CMD` hardcodes port 8000, the container will fail Cloud Run's health check. Fix: either change CMD to `--port=$PORT` or set `--port=8000` on the Cloud Run service. Terraform sets `container_port = 8000` so the second route works — verify it matches your Dockerfile's CMD.

---

## Acceptance criteria

- `gcloud run deploy` completes with `Service [admin-backend] revision ...has been deployed and is serving 100 percent of traffic.`
- `/api/v1/health` returns 200 from the service URL.
- `/api/v1/openapi.json` returns valid JSON.
- Logs show clean startup, JSON format, no errors.
- Cross-tenant test: TENANT-1 JWT sees tenant-1 rows only; TENANT-2 JWT sees tenant-2 rows only. Cross-tenant access returns 404/4xx.
- Frontend team has been handed the URL.
- mypy strict still clean.

---

## Stop and ask if

- Service won't start because a required env var the app expects at startup isn't set in the Terraform config (something the Terraform run-env didn't capture).
- VPC egress is blocking something the app needs at runtime (rare; `PRIVATE_RANGES_ONLY` should be safe for our shape).
- Cross-tenant isolation FAILS — disaster-class per architecture; stop everything, surface immediately, do not proceed.
- Frontend team can't reach the backend due to CORS — verify `CORS_ALLOWED_ORIGINS` in `gcloud run services describe` output matches the frontend's actual Cloud Run URL.
- Container fails the startup probe with `connection refused on port 8000` — the Dockerfile-vs-Terraform port mismatch (see hints).

---

## What to report at end (5-bundle convention)

1. **Code/configs:** No source changes expected. If a Dockerfile tweak was needed (port mismatch fix), note it. Any one-shot scripts created for cross-tenant test data setup.
2. **CLAUDE.md updates:** "Current state" — backend dev URL, image tag deployed, cross-tenant test status. Any environment-specific findings.
3. **BUILD_PLAN.md updates:** Step 4.4 status flipped to DONE (sections 1-4 shipped 2026-05-03; section 5 unblocked by Step 4.3.5 run on 2026-05-04). Note the Cloud Run path; reference D-31. Coordination block updated: frontend team integrating against the deployed dev URL.
4. **architecture.md updates:** No change expected unless something new about Cloud Run behaviour was learned that's worth documenting.
5. **Prompt file:** `prompts/step-4_4-cloud-run-deploy-dev.md` confirmed in commit set.

Plus: deployed backend URL, revision name, cross-tenant test result table (TENANT-1 / TENANT-2 × tenants/stores), Cloud Logging query that returned the test traffic.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
