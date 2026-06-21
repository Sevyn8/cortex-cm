# BUILD_PLAN.md — Ithina Admin Backend v0

> Step-by-step build plan for v0 (the initial release / MVP for beta users). The plan organises work into six stages plus a Candidate scope holding-area; v0 is the union of Stages 1–6 (whatever ships across them by Stage 6 cutover). Each step is the unit of work for one Claude Code session (or one Claude AI session, or one human coordination block — see Owner field). Step numbering does not encode timing — steps within a stage land when they're ready, deployed continuously to dev (Stages 1–4), then to staging (Stage 5), then to production (Stage 6).

> Read CLAUDE.md, `docs/architecture.md`, and `docs/api-contract.md` before any step. Read the specific prompt for the step before executing. If the step or its prompt has issues, surface them per the rules in CLAUDE.md.

---

## Owner field convention

Each step has an Owner field. Values:

- **CLAUDE_CODE** — Claude Code executes (writes code, tests, scripts, YAML). User reviews and confirms.
- **CLAUDE_AI** — Claude AI session writes the deliverable (typically polished documents for human readers). User reviews, iterates, then shares with the audience.
- **HUMAN** — Coordination, manual ops, or commands run by a person. Sub-tagged with the actual person/team:
  - `HUMAN (you)`
  - `HUMAN (you + frontend dev)`
  - `HUMAN (GCP-helper)`
  - `HUMAN (you + GCP-helper)`
  - `HUMAN (you + eng head)`
- **HYBRID** — Multiple owners. The step description spells out which parts are which.

---

## Step naming convention

`Step <section-anchor>.<sub>` — e.g., `Step 1.1`, `Step 4.3`. Some steps split into `<section>.<sub>.<part>` (e.g., `Step 1.7.1`, `Step 1.7.2`) when the deliverable has parts with different owners. Section-anchors (1.x, 2.x, 3.x, ...) group related work; they do not encode stage membership directly. Step 8.3 (Auth0) lives in Stage 3 while Steps 8.0, 8.1.x, 8.2 live in Stage 6 — the 8.x cluster splits across stages because Stage assignment is by scope, not by number.

When writing prompts, reference: "This is Step 4.3. Step 4.4 covers X; not in this prompt."

---

## Status legend

- **TODO** — not started.
- **IN PROGRESS** — being worked on.
- **DONE** — acceptance criteria passed; committed.
- **BLOCKED** — waiting on coordination or unresolved issue.

Update status at the end of each step. Keep this document current.

---

## Pre-step status (already completed)

- Local environment installed (Python 3.12 via uv, Docker, Postgres 15, psql, git, Claude Code).
- Repo scaffolded at `/home/zorin/ithina-retail/admin-backend` with src layout, dependencies installed, Postgres running.
- 8 DDL files in `db/raw_ddl/` (the original 7 plus lookups; audit_logs DDL is added at Step 6.2).
- Alembic initialised; `migrations/env.py` reads `DATABASE_URL` from env.
- Git initialised, first commit made.

---

# Stage 1 — Foundation: read-only multi-tenant API (dev)

**Status.** Substantially complete. Step 6.2 still TODO. Section 6.8 (Steps 6.8.1, 6.8.2, 6.8.2.1, 6.8.3) is local-only, pending bundled Cloud Run deploy.

**Stage boundary.** Read-only API across all resources, multi-tenant via RLS, RBAC catalogue readable but not enforced, Auth0 stub still wired.

**Deployment model.** Continuous manual deploy to Cloud Run dev after each step (or small cluster of steps). Frontend integrates against dev in lockstep. Not CI/CD — operator-triggered after step completion. Same model applies to Stages 2, 3, 4.

**Scope contents (in numerical order).** All Step 1.x, 2.x, 3.x, 4.x, 5.x, and 6.1–6.8.3 sit under this stage.

---

## Step 1.1 — Architecture document

**Status.** DONE 
**Owner.** CLAUDE_AI

**Goal.** Produce `docs/architecture.md` covering the system narrative. Get sign-off from engineering head before GCP-helper provisions and before any code is written.

**Scope in.**
- System diagram (FastAPI ↔ Postgres ↔ Frontend ↔ Auth0).
- Per-region topology (EU + US), residency boundary, hostnames.
- Master DB rule (admin backend sole writer to its own tables; other services may own other tables).
- Auth model (Auth0 prod, stub during build).
- Multi-tenancy enforcement (RLS + middleware + dependency).
- Request lifecycle (HTTP → middleware → dependency → handler → response).
- Deployment shape (GKE Autopilot, Cloud SQL, Secret Manager).
- What's deferred for v0 (DR, ArgoCD, Cloudflare, Terraform, write endpoints, rate limits).
- Sent to engineering head for sign-off.

**Scope out.**
- Detailed runbook for DevOps (covered by Steps 1.7.1 and 1.7.2).
- Implementation specifics (covered by individual code steps).

**Acceptance criteria.**
- `docs/architecture.md` exists in repo.
- Engineering head feedback incorporated, sign-off received.
- Doc is part of standing context (loaded by Claude Code at every task start).

**Coordination.**
- Engineering head reviews and signs off.

**Rough effort.** 60-90 min for the doc; depends on eng head turnaround.

---

## Step 1.2 — check_setup.sh script

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** A pre-flight script that runs at the start of every task to catch setup drift before Claude Code starts coding around symptoms.

**Scope in.**
- Write `scripts/check_setup.sh` with tiered checks:
  - **Tier 1 — environment:** required tools on PATH (python, uv, psql, docker, docker compose, alembic), `.python-version` matches uv install, required directories exist.
  - **Tier 2 — services:** Docker daemon up, Postgres container running and healthy.
  - **Tier 3 — connectivity:** Postgres reachable, required env vars set.
  - **Tier 4 — DB state:** `alembic current` runs without error.
  - **Tier 5 — code state:** `uv sync` reports nothing to install, `mypy --strict src/` runs without import errors, `pytest --collect-only` runs without import errors.
- Each check prints PASS / FAIL with an actionable hint on failure.
- Exit code: 0 if all pass, 1 if any fail.

**Scope out.**
- Full test runs (just `--collect-only` to catch import errors).
- Cloud-side checks (local only).

**Acceptance criteria.**
- `./scripts/check_setup.sh` runs and reports per-tier status.
- Deliberate failure (stop Postgres, run script) shows FAIL on Tier 2 with hint.
- Exit code reflects overall pass/fail.

**Coordination.**
- None.

**Rough effort.** 45 min.

---

## Step 1.3 — Stress-test the 8 DDLs

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Surface schema issues before they're encoded into migrations. Cheaper to fix DDLs than migrations.

**Scope in.**
- Read all 8 DDLs in dependency order: shared_utilities → lookups → platform_users → tenants → tenant_users → org_nodes → stores → rbac.
- Cross-file consistency check (enum dependencies, FK targets exist, type referenced before defined).
- Per-file checks (RLS on multi-tenant tables, FORCE RLS, indexes for filter columns, constraint completeness).
- Edge cases not yet covered (NULL handling on composite keys, cascade vs restrict on FKs).
- Output: a list of issues with severity, plus recommended fixes.

**Scope out.**
- Applying anything to the DB.
- Modifying DDL files (only flag, don't fix).
- Adding new tables / columns.

**Acceptance criteria.**
- Issue list (severity-tagged) handed to user.
- Critical issues, if any, get user-approved fixes before Step 1.4.
- Non-critical issues recorded in CLAUDE.md as forward-notes.

**Coordination.**
- None.

**Rough effort.** 60-90 min.

---

## Step 1.4 — Apply DDLs to local Postgres directly via psql

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Verify all 8 DDLs execute cleanly against a real Postgres before wrapping in Alembic.

**Scope in.**
- Apply each DDL via `psql -f` in dependency order.
- Verify schema after each (`\dt`, `\dT`, `\d <table>`).
- If any DDL fails, fix it (with user approval), drop schema, re-apply from start.

**Scope out.**
- Alembic migrations (Step 1.6).
- Smoke test (Step 1.5).
- Audit logs DDL (Step 6.2).

**Acceptance criteria.**
- All 10 tables exist in the `core` schema (lookups + the original 9 from 7 DDL files).
- All enums exist.
- RLS visible on multi-tenant tables (`\d <table>` shows policies).
- All FKs resolve.

**Coordination.**
- None.

**Rough effort.** 30-60 min.

---

## Step 1.5 — Write and run smoke test script

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Verify schema invariants in code (cross-tenant RLS isolation, FK integrity, CHECK constraints).

**Scope in.**
- Python script `scripts/smoke_test.py` using `psycopg` directly.
- Insert test platform_user → tenant → tenant_user → store.
- Set `app.tenant_id` to tenant A; query each tenant-scoped table; expect A's rows.
- Set `app.tenant_id` to a different tenant; query; expect zero rows.
- Test composite FK: try to insert a row with mismatched tenant_id; expect failure.
- Test CHECK constraints: try to insert invalid status transitions; expect failure.
- Print PASS / FAIL per assertion.
- Roll back at the end so DB stays clean.

**Critical: handle FORCE RLS in the test runner.**
With FORCE RLS on multi-tenant tables, the table owner role does NOT bypass RLS. The test runner connection itself must `SET LOCAL app.tenant_id = '<uuid>'` before SELECTs in order to see its own freshly-inserted rows. INSERTs that target a `tenant_id` matching the current `app.tenant_id` are allowed; INSERTs with mismatched `tenant_id` are blocked. The smoke test must explicitly manage `app.tenant_id` across the script's setup, assertion, and teardown phases. Failure to do this will produce confusing "rows missing" errors that look like FK or CHECK issues but are actually RLS filters.

**Scope out.**
- SQLAlchemy ORM (not yet built).
- FastAPI integration tests.
- Comprehensive coverage (just smoke).

**Acceptance criteria.**
- `uv run python scripts/smoke_test.py` prints PASS for every assertion.
- Test runs cleanly twice in a row (no state leftover).

**Coordination.**
- None.

**Rough effort.** 60-90 min.

---

## Step 1.6 — Wrap DDLs as Alembic migrations and verify reversibility

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Migrations track schema state; reversible for safe rollback.

**Scope in.**
- Drop and recreate the configured `DB_SCHEMA` (`core` on local) so Alembic starts from an empty schema. Extensions in `public` are preserved (they are a setup precondition, not a migration concern; see below).
- Create ONE Alembic initial migration via `alembic revision -m "initial schema"`. The migration wraps all 8 DDL files; we do NOT chain 8 separate revisions.
- The migration is self-contained: DDL content is embedded as Python raw triple-quoted string literals at generation time. The migration does NOT read from `db/raw_ddl/` at runtime, so production deployments don't need the DDL source on disk. Generator script lives at `scripts/build_initial_migration.py` so the migration can be regenerated from the DDLs at any time.
- `CREATE EXTENSION` statements are stripped from the embedded DDL content. The application role is NOSUPERUSER NOBYPASSRLS by design (per Step 1.5) and cannot install extensions; extensions (`ltree`, `pgcrypto`) are installed once at database setup by a privileged role.
- The migration's `upgrade()` starts with a precondition check that fails clearly if `ltree` or `pgcrypto` are not installed.
- The migration's `downgrade()` drops 10 tables (reverse dependency order), 18 enums, and the project's 2 functions (`set_updated_at_timestamp`, `uuidv7`). Extensions are NOT dropped.
- `migrations/env.py` reads `DB_SCHEMA` from env (refuses to run if missing); sets `search_path` INSIDE alembic's transaction (a pre-context SET implicitly opens a SQLAlchemy 2.x transaction that silently rolls back the migration); sets `version_table_schema=DB_SCHEMA`, `include_schemas=True`, `target_metadata=None` (TODO: switch to `Base.metadata` when ORM models land at Step 3.1).
- Migration file is schema-agnostic: the literal `'core'` (or any specific schema name) does not appear in the migration. Tables resolve to the configured schema via `search_path`.
- Run `alembic upgrade head`; verify state matches Step 1.4 (10 tables + `alembic_version`, 18 enums, 5/5 RLS+FORCE).
- Run `alembic downgrade base`; verify schema empty (extensions in `public` preserved).
- Round-trip (`upgrade -> downgrade -> upgrade`); verify final state identical to first upgrade.

**Scope out.**
- Audit logs migration (Step 6.2).
- Cloud-side migrations (Step 4.x).
- Multi-revision migrations. Only one revision: the initial wrap.
- Extension installation. Extensions are a database-setup precondition, not a migration concern.
- Smoke test re-run. Step 1.5 already verifies schema invariants and is independent of how the schema was applied; no need to re-run it here.

**Acceptance criteria.**
- One migration in `migrations/versions/` wrapping all 8 DDLs.
- `migrations/env.py` reads `DATABASE_URL` and `DB_SCHEMA`; refuses to run if either is missing; sets `version_table_schema=DB_SCHEMA`; sets `search_path` inside alembic's transaction.
- Migration file contains no hardcoded schema literal.
- Migration's `upgrade()` precondition check fails clearly if `ltree` or `pgcrypto` are missing.
- Migration's `upgrade()` does NOT contain `CREATE EXTENSION` statements.
- Migration's `downgrade()` drops 10 tables, 18 enums, 2 functions, and does NOT drop extensions.
- Fresh-schema upgrade produces 10 application tables + `alembic_version`, 18 enums, 5/5 multi-tenant tables with RLS+FORCE.
- Downgrade to base leaves the schema with 0 application tables, 0 enums, 0 functions; extensions in `public` preserved.
- Round-trip (`upgrade -> downgrade -> upgrade`) yields identical state to first upgrade.
- `alembic current` returns the head revision id when the schema is at head.

**Coordination.**
- None.

**Rough effort.** 90 min.

---

## Step 1.7.1 — Terraform for dev provisioning

**Status.** DONE (2026-05-03)
**Owner.** CLAUDE_AI

**Goal.** Terraform code that provisions the complete dev GCP environment. Supersedes the original "imperative gcloud + kubectl script" shape (D-23 revised 2026-05-03).

**Deliverable.** `terraform/` directory in the separate `ithina-retail-admin-infra` repo, with `bootstrap/`, `modules/` (apis, network, artifact-registry, cloud-sql, secrets, iam-backend, cloud-run-backend, cloud-run-frontend, gke), and `envs/dev/` composing all-but-gke modules. Per D-33, dev uses Cloud Run for the backend (no GKE in dev); the gke module is wired up but only invoked from `envs/prod/` (Step 8.1.1).

**Acceptance criteria (met).**
- `terraform validate` passes in `bootstrap/` and `envs/dev/`.
- `terraform apply` brings up the complete dev environment (project APIs, VPC + private services peering, Cloud SQL with Postgres 15 + ltree, Artifact Registry, Secret Manager, Cloud Run service for the backend, Cloud Run job for Alembic, Cloud Run service for the frontend, IAM bindings).
- `terraform/README.md` (Step 1.7.2) documents the bootstrap → apply → populate-secrets → image-build → deploy flow.

**Coordination.**
- Lives in the infra repo, not this repo. Backend repo references it via the standing-context line at the top of CLAUDE.md.

**Rough effort.** Done in the infra repo on 2026-05-03.

---

## Step 1.7.2 — Terraform README (document)

**Status.** DONE (2026-05-03)
**Owner.** CLAUDE_AI

**Goal.** Narrative README for the Terraform code, audience = DevOps / operator who knows GCP but doesn't know this project.

**Deliverable.** `terraform/README.md` in the `ithina-retail-admin-infra` repo. Covers: prerequisites, expected costs, the bootstrap → apply order, what to verify after each module, where secrets get populated, image-build hand-off, the dev-vs-prod runtime split (D-33), and rollback / destroy guidance.

**Scope out.**
- Production runbook (covered at Step 8.1.1 when prod env lands).
- Cloudflare.

**Acceptance criteria (met).**
- README is reviewable line-by-line; an operator unfamiliar with the project can follow it from clean GCP project to running dev environment.

**Rough effort.** Done in the infra repo on 2026-05-03.

---



---

## Step 2.0 — Frontend API contract sync

**Status.** TODO
**Owner.** HUMAN (you + frontend dev)

**Goal.** Lock API contract with frontend developer before any endpoint code is written. Output: `docs/api-contract.md`.

**Scope in.**
- Pre-meeting prep (~20 min):
  - Compile a list of contract decisions to discuss.
  - Bring 1-2 sample endpoint shapes for discussion.
  - Reference architecture doc + CLAUDE.md.
- Meeting (~45-60 min): walk through each contract decision.
- Decisions to lock:
  1. Response naming: snake_case or camelCase.
  2. Response wrapping: `{ data: [...] }` or raw array/object.
  3. Pagination: offset/limit or cursor.
  4. Date format: ISO 8601 strings.
  5. Error response shape: `{ code, message, details, request_id }`.
  6. Auth: Bearer JWT in Authorization header.
  7. Null fields: always present with null vs omitted.
  8. OpenAPI consumption: TypeScript generation or hand-written types.
  9. Endpoint granularity: any places frontend wants a fat endpoint vs separate calls.
  10. Filter / search query param naming.
- Capture all decisions in `docs/api-contract.md`.

**Scope out.**
- Implementation (covered by Step 3.x onward).

**Acceptance criteria.**
- `docs/api-contract.md` written with locked decisions.
- Frontend dev acknowledges and signs off.
- Doc becomes part of standing context loaded by Claude Code at session start.

**Coordination.**
- Frontend developer available for the meeting.

**Rough effort.** ~1.5 hr including prep.

---

## Step 2.1 — Stub auth: keys, config, AuthContext, StubAuthClient

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Mint and verify RS256 JWTs locally with Auth0-shaped claims.

**Scope in.**
- Generate RSA key pair in `keys/` (gitignored). 2048-bit.
- Implement `src/admin_backend/config.py` using `pydantic-settings`. All env vars from CLAUDE.md.
- Implement `src/admin_backend/auth/context.py` with `AuthContext` Pydantic model (frozen).
- Implement `src/admin_backend/auth/stub.py` with `StubAuthClient.verify(jwt_string) → AuthContext`. RS256 verification, `iss`/`aud`/`exp` validation, custom-claim extraction (`https://ithina.com/tenant_id`, `https://ithina.com/user_type`, `https://ithina.com/user_id`, `https://ithina.com/email`) per D-24.
- Implement `src/admin_backend/auth/testing.py` with `make_test_jwt(...)` helper.
- Implement `src/admin_backend/errors.py` with `AuthMissingError`, `AuthInvalidError`, `InvalidTenantIdError`.
- Unit tests: valid token → AuthContext; expired → raises; wrong audience → raises; wrong signature → raises.

**Scope out.**
- Middleware (Step 2.3).
- Dependency (Step 2.2).
- Endpoints (Step 2.4).
- Real Auth0 client (post-launch).

**Acceptance criteria.**
- `make_test_jwt(...)` round-trips through `StubAuthClient.verify` cleanly.
- All unit tests pass under `pytest -v`.
- mypy strict clean.

**Coordination.**
- None.

**Rough effort.** 90 min.

---

## Step 2.2a — Async DB engine, `get_tenant_session` dependency, runtime privilege check, NULLIF policy migration

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Build the async SQLAlchemy engine, the per-request session-bootstrap dependency, the connect-time `search_path` hook, the runtime privilege check, and the RLS-policy migration that lets the bootstrap actually work on pooled connections.

**Scope in.**
- Implement `src/admin_backend/db/engine.py`. Async SQLAlchemy engine with conservative pool config (pool_size=10, max_overflow=5, pool_pre_ping=True, pool_recycle=1800, `connect_args={"prepare_threshold": None}` per D-14). Connect-time event hook sets `search_path = {db_schema}, public` (D-15 belt-and-suspenders). Exposes `create_session_factory(engine)` and `assert_app_role_no_bypassrls(engine)`.
- Implement `src/admin_backend/db/session.py`. `get_tenant_session(auth, session_factory) -> AsyncIterator[AsyncSession]` async generator. Uses `set_config(name, value, true)` (not `SET LOCAL`) for both `app.tenant_id` and `app.user_type` so NULL handling on the tenant_id branch is clean. Sources are `auth: AuthContext` only — no headers/params/body. AuthContext source-binding supersedes the originally-planned `VerifiedTenantId` newtype (see AI-MT-03 update).
- Add `field_validator` on `Settings.db_schema` rejecting non-identifier values (defence against SQL injection through the connect hook's f-string).
- Migration `e59f62d5037d`: amend all 5 multi-tenant RLS policies (tenants, tenant_users, org_nodes, stores, user_role_assignments) to wrap `current_setting('app.tenant_id', TRUE)` in `NULLIF(..., '')`. Required because Postgres 15 registers placeholder GUCs at session level on first set, and the original policies crash on `''::uuid` for every reused pooled connection past its first transaction. See D-27 for the full reasoning.
- 15 unit tests covering engine init + connect hook (T1, T2), runtime privilege check (T3-T7), db_schema validator (T8), session var setting (T9-T10, T12-T13), default-deny RLS query under PLATFORM-no-impersonation (T11), concurrent session isolation (T14), and reused-connection-after-commit RLS query (T15).

**Scope out.**
- FastAPI middleware that populates AuthContext on the request (Step 2.3).
- Health endpoints and main.py app entrypoint (Step 2.4).
- Lifespan hookup for `assert_app_role_no_bypassrls` (Step 2.4).
- FN-AB-14 OR-clause amendment to `user_role_assignments_tenant_isolation` (Step 2.2b).
- Smoke-test rewrite for the 9-row truth table (Step 2.2b).
- Auth0 client (post-launch).

**Acceptance criteria.**
- All 15 new unit tests pass; existing 21 stub-auth tests still pass; mypy strict clean; check_setup 35/35.
- Live query against `tenants` from a pooled connection that previously held a different transaction does NOT raise `''::uuid` errors.
- Migration `e59f62d5037d` is reversible (verified via downgrade + re-upgrade roundtrip).

**Coordination.**
- None.

**Rough effort.** ~3 hours including the NULLIF investigation, migration, doc updates, and the four-bundle commit gate.

---

## Step 2.2b — `user_role_assignments` permissive OR-clause (FN-AB-14)

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Enable PLATFORM-audience rows on `user_role_assignments` (rows with `tenant_id IS NULL`) to be visible to PLATFORM users via the `app.user_type='PLATFORM'` permissive clause. Closes FN-AB-14.

**Scope in.**
- Alembic migration `4fd3aec6ae0c`: drop and recreate `user_role_assignments_tenant_isolation` only. Other 4 multi-tenant policies are correct as of `e59f62d5037d` and were not touched.
- New policy USING and WITH CHECK:
  ```
  tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
  OR (tenant_id IS NULL AND current_setting('app.user_type', TRUE) = 'PLATFORM')
  ```
- Smoke-test rewrite: old assertions 11/12 (which documented FN-AB-14 as failing) replaced by 9 truth-table assertions covering all (`app.tenant_id`, `app.user_type`) combinations against TENANT-A, TENANT-B, and PLATFORM-audience rows. New meta-assertion: every table with a `tenant_id` column has RLS + FORCE + at-least-one-policy.
- CLAUDE.md FN-AB-14 marked RESOLVED with empirical truth table; rows 7-8 (tenant_id set, user_type unset) corrected from speculative "Invisible" to actual "Visible (1)" with note that this state is unreachable through `get_tenant_session` per AI-MT-03.

**Scope out.**
- Anything beyond the one policy and the smoke test rework. NULLIF on the other 4 policies was already done in 2.2a.

**Acceptance criteria.**
- PLATFORM session (`set_config app.user_type='PLATFORM'`, `app.tenant_id` unset) sees PLATFORM-audience rows from `user_role_assignments`. ✓ (truth table row 6, count=1)
- TENANT session of tenant A sees only tenant A's rows; cannot see B's rows; cannot see PLATFORM-audience rows. ✓ (truth table row 1, count=1)
- Smoke test passes the 9-row truth table. ✓ (24/24 reportable lines PASS)
- Migration reversible: downgrade restores the post-NULLIF (e59f62d5037d) form. ✓ (verified via downgrade + re-upgrade roundtrip)

**Coordination.**
- None.

**Rough effort.** 60-90 min.

---

## Step 2.3 — Middleware + structured errors + tenant-session Depends provider

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Wire the request-handling layer that runs on every request: audit context, auth verification, CORS, structured error responses, and the FastAPI Depends provider that bridges request state to the Step 2.2a tenant-session bootstrap.

**Scope in.**
- `src/admin_backend/middleware/audit_context.py` — outermost middleware. Generates request_id (UUID4), captures `X-Forwarded-For` (first IP) or `request.client.host`, captures `User-Agent`. Adds `X-Request-Id` to response. Emits one INFO log line per request via `admin_backend.requests` with standard fields (request_id, tenant_id, user_id, user_type, method, path, status, latency_ms, ip, user_agent, exception). try/except/finally captures status from response on success or `exc.http_status` on the typed exception path; defaults to 500 for unhandled.
- `src/admin_backend/middleware/auth.py` — middle middleware. Extracts `Authorization: Bearer <jwt>`. Calls `StubAuthClient.verify`. Sets `request.state.auth`. Skips PUBLIC_PATHS (`/v1/health`, `/v1/openapi.json`, `/v1/docs`, `/v1/redoc`, `/metrics`). Converts `AdminBackendError` to JSON response inline via `errors.build_error_payload` because Starlette's `BaseHTTPMiddleware` does NOT route middleware-raised exceptions through FastAPI's `@app.exception_handler`.
- `CORSMiddleware` configured from `Settings.cors_allowed_origins` (comma-separated, parsed in `main.py`). Innermost of the three.
- Middleware ordering: CORS innermost, Auth middle, Audit outermost. Added in REVERSE order in `create_app` so request flow is Audit → Auth → CORS → handler.
- `src/admin_backend/errors.py` refactored to two-tier `ClientError` / `ServerError` hierarchy. `http_status`, `public_message`, `code` are class attributes. ServerError subclasses ALWAYS return generic `INTERNAL_ERROR` to clients (anti-information-disclosure); subclass-specific info goes only to the internal log. `AppRolePrivilegeError` moved here from `db/engine.py` (re-exported from `db/engine.py` for backward compat). New `build_error_payload(exc, request_id)` helper used by both the auth middleware and the FastAPI exception handler.
- `src/admin_backend/dependencies.py` — `get_auth_context`, `get_session_factory`, `get_request_id`, and `get_tenant_session_dep` (the FastAPI-shaped wrapper around `get_tenant_session(auth, session_factory, request_id)`).
- `src/admin_backend/db/session.py` updated: new `request_id: str | None = None` parameter; new `set_config('app.request_id', ..., true)` call. Default of None keeps Step 2.2a tests passing without change.
- `src/admin_backend/logging_config.py` — `configure_logging(level)` sets up stdout JSON via `python-json-logger`, with `rename_fields={"asctime": "timestamp", "levelname": "level"}`.
- `src/admin_backend/main.py` skeleton — `lifespan` constructs engine, runs `assert_app_role_no_bypassrls`, creates session_factory, instantiates `StubAuthClient`, exposes all on `app.state`. `create_app` registers middleware in the right order and the exception handler.
- `src/admin_backend/config.py` — `cors_allowed_origins: str = ""` field added; `@lru_cache get_settings()` accessor added.
- `pyproject.toml` — `python-json-logger` pin tightened to `>=2.0,<4.0`.
- 10 integration tests (`tests/integration/test_middleware.py`) covering: public path no-auth, missing Authorization, empty Bearer, invalid JWT, valid TENANT JWT, X-Request-Id on every response, JSON log shape, request-id consistency between header and log, full-stack injection attempt (X-User-Type header ignored; DB session sees AuthContext.user_type), and ServerError anti-information-disclosure.

**Scope out.**
- Audit log writes to a `audit_logs` table (D-16; population is external in v0; trigger work lands at Step 6.2).
- Health endpoint implementation (Step 2.4).
- Final `main.py` shape and lifespan ordering (Step 2.4 finalises; the current skeleton is functional but the dedicated health endpoint isn't a route yet).
- Auth0 client (Stage 3 work; Step 8.3).
- Domain endpoints (Step 3.x onward).
- 404-vs-403 enforcement on tenant-mismatch (D-17 is handler-layer work; the exception handler carries a TODO comment).

**Acceptance criteria.**
- 10 integration tests pass; existing 36 tests (21 stub auth + 15 engine/session) still pass: 46/46. ✓
- mypy strict clean across all modules. ✓
- check_setup 35/35. ✓
- `uvicorn admin_backend.main:app` boots cleanly; `/v1/openapi.json` returns 200 with X-Request-Id; protected paths return 401 AUTH_MISSING with X-Request-Id; one structured JSON log line per request. ✓
- `request.state.auth.tenant_id` and `request.state.auth.user_type` flow into the DB session via `get_tenant_session_dep`; T9 verifies end-to-end against a header-injected `X-User-Type` (ignored, DB session sees AuthContext value). ✓

**Coordination.**
- None.

**Rough effort.** ~3 hours including the BaseHTTPMiddleware exception-handling investigation and the four-bundle commit gate.

---

## Step 2.4 — Health + readiness endpoints, lifespan finalisation, startup-gate tests

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Close the foundation block. Ship the two probe endpoints Kubernetes will use, finalise the lifespan, and add startup-gate tests that exercise both `Settings()` validation and `assert_app_role_no_bypassrls`.

**Scope in.**
- `GET /v1/health` (liveness, public, no DB access). Returns 200 with `{"status": "ok", "service": "admin-backend", "version": "0.1.0"}`. Tagged `meta` in OpenAPI. Must respond fast even when DB is down or app is misconfigured (kubelet uses this for kill-and-restart).
- `GET /v1/ready` (readiness, public, runs `SELECT 1`). 200 with `{"status": "ready", "db": "ok"}` on success; 503 with `{"status": "not_ready", "db": "error"}` on any failure. Bounded by a 2-second `asyncio.wait_for` so a hung DB does not stall the probe past the readiness window. Tagged `meta`.
- `/v1/ready` added to `PUBLIC_PATHS` in `src/admin_backend/middleware/auth.py`.
- Lifespan refactored to assign each resource onto `app.state` as it is constructed (settings → engine → privilege gate → session_factory → auth_client) so a startup-gate raise leaves partial state inspectable for diagnosis and disposal.
- NotImplementedError message for `AUTH_CLIENT_MODE=AUTH0` rewritten to reflect that Auth0Client is its own pending step (lands when Auth0 tenant configuration arrives).
- Shared integration-test fixtures (`settings`, `app_with_test_routes`, `client`, `valid_tenant_jwt`, `json_log_buffer`, `error_log_buffer`) extracted to `tests/integration/conftest.py`. The stub `/v1/health` route inside the Step 2.3 fixture has been dropped (the real one in `create_app()` now serves).
- 6 health tests (`tests/integration/test_health.py`) covering the expected response shape, the no-DB-access property, healthy readiness, broken-engine readiness, timeout-bounded readiness, and audit-middleware emission.
- 5 lifespan tests (`tests/integration/test_lifespan.py`) covering the happy path, the production+STUB ValidationError, the production-issuer-not-false-positive case, the privilege-gate raise, and the AUTH0-mode pending-Auth0 NotImplementedError.

**Scope out.**
- Auth0Client implementation (separate future step; lands when Auth0 tenant configuration arrives).
- Domain endpoints (Step 3.x onward).
- Router registration (Step 3.x; routes register on the app directly for now).
- `/metrics` (Step 7.2.1, post-launch).
- K8s manifests (Step 4.4); this step ships the endpoints those manifests will probe.

**Acceptance criteria.**
- `uv run uvicorn admin_backend.main:app` starts cleanly. ✓
- `curl http://localhost:8000/v1/health` → 200 with the expected shape. ✓
- `curl http://localhost:8000/v1/ready` → 200 with `{"status": "ready", "db": "ok"}`. ✓
- OpenAPI doc at `/v1/openapi.json` lists both endpoints under the `meta` tag. ✓
- 11 new tests pass (6 health + 5 lifespan); 46 existing (Steps 2.1, 2.2a, 2.3) still pass: 57 total. ✓
- mypy strict clean; check_setup 35/35. ✓

**Coordination.**
- Share OpenAPI spec stub with frontend (`/v1/openapi.json`).
- Confirm GCP-helper progress.

**Rough effort.** 60 min.

---

## Step 3.0 — PLATFORM-visibility OR-clause back-fill on remaining 4 multi-tenant policies

**Status.** DONE
**Owner.** CLAUDE_CODE

**Note on ordering.** Numerically prior to 3.1; chronologically landed after 3.1 + the drift sweep + the convention extension. The need surfaced during Step 3.2 stress-test design (PLATFORM-visible read endpoints planned for Step 3.3 and PLATFORM-INSERT test fixtures planned for Step 3.2 were both blocked by the original single-clause RLS form on these 4 tables). BUILD_PLAN reads in logical order; git log shows actual chronology.

**Goal.** Extend the FN-AB-14 PLATFORM-visibility pattern to the 4 multi-tenant policies that didn't carry it (tenants, tenant_users, org_nodes, stores). Without the OR-branch, a PLATFORM session sees zero rows on these tables AND cannot INSERT into them — the WITH CHECK predicate `tenant_id = NULLIF(NULL, '')::uuid` evaluates to UNKNOWN. The latter blocks `make_tenant` test factories (Step 3.2) and seed scripts (Step 6.3) on the `NOSUPERUSER NOBYPASSRLS` application role (no privileged-role escape hatch by design).

**Scope in.**
- One Alembic migration `21e2ad16303a_step_3_0_platform_visibility_or_clause.py` (down_revision `4fd3aec6ae0c`). Drops and recreates 4 policies in `def upgrade()` with the unconditional PLATFORM OR-branch (`OR current_setting('app.user_type', TRUE) = 'PLATFORM'` — no IS-NULL gate, because the tenant_id/id columns on these tables are NOT NULL). `def downgrade()` reverts to the post-NULLIF, single-clause form (`e59f62d5037d` shape). NULLIF wrapper preserved per D-27. `tenants_self_access` uses column `id` (its own PK); the other 3 use `tenant_id`.
- DDL files NOT edited (per the established convention demonstrated by `e59f62d5037d` and `4fd3aec6ae0c`: DDLs are frozen at as-shipped initial-schema state; subsequent changes encoded in Alembic only). Convention captured as a paragraph in CLAUDE.md "Workflow convention — Per-step commit bundling" section.
- Smoke test (`scripts/smoke_test.py`) grows from 24 to 64 PASS:
  - `test_15_multi_tenant_or_clause_truth_tables`: 4 tables × 9 GUC cells = 36 visibility assertions.
  - `test_16_platform_can_insert_into_multi_tenant_tables`: 4 PLATFORM-INSERT assertions exercising the WITH CHECK path.
- New decision **D-29** — PLATFORM RLS visibility via policy clause, not BYPASSRLS role; documents both shapes (unconditional for NOT NULL tenant_id; IS-NULL-gated for NULLABLE) and the permissive-impersonation property (RLS is the visibility floor; handler-layer scoping in Step 6.1 is where impersonation-tightening lands if needed).
- New forward-note **FN-AB-15** — regenerator-script staleness foot-gun. `scripts/build_initial_migration.py` would silently emit a stale baseline if rerun against frozen DDLs (3 live policy migrations now). Defer guard to post-v0 chain consolidation.
- `docs/architecture.md` Layer 1 RLS prose updated to reflect the OR-branch and the PLATFORM "sees all rows" property; Appendix A flow diagram updated.

**Scope out.**
- Application code changes (none needed; `app.user_type` already set per-transaction by `get_tenant_session` since Step 2.2a).
- `audit_logs` policy (Step 6.2 designs this when the table lands).
- RBAC enforcement (Step 6.1, application-layer concern).
- RLS-enforced impersonation scoping (deferred per D-29 `Reconsider if`).
- DDL edits (frozen per established convention).

**Acceptance criteria.**
- 1 new migration file; alembic upgrade/downgrade/upgrade round-trip clean.
- DDL files NOT edited (convention).
- Smoke test 64/64 post-migration; pre-migration (after downgrade) shows exactly 16 expected failures (12 PLATFORM-visibility cells + 4 PLATFORM-INSERT cells).
- Pytest 70 passed (no regression from 3.1).
- mypy strict clean.
- `check_setup.sh` 35/35.
- CLAUDE.md updated with D-29, FN-AB-15, the DDL-frozen convention paragraph, Schema state line update, and Smoke test count update.
- architecture.md Layer 1 + Appendix A updated.

**Coordination.**
- None.

---

## Step 3.1 — Tenant ORM model + Pydantic Read schema

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Establish the model + schema pattern used by all subsequent resources. `docs/api-contract.md` is still in template state (Step 2.0 not yet held); Step 3.1 locks **provisional defaults** that propagate from this step forward — see D-28.

**Scope in.**
- `src/admin_backend/db/base.py`: project-wide SQLAlchemy `DeclarativeBase` (no global `metadata.schema`; per-table per D-15).
- `src/admin_backend/models/tenant.py`: SQLAlchemy 2.x `mapped_column` style. Maps all 22 columns from `tenants_v3.sql`. Defines four `str`-Enum classes (`TenantStatus`, `TenantTier`, `TenantIndustry`, `TenantRegion`). Enum columns use **`postgresql.ENUM`** with `create_type=False, native_enum=True, values_callable=lambda e: [m.value for m in e]` (the dialect-specific class is required: generic `sqlalchemy.Enum` silently drops `create_type=False`). `__table_args__["schema"]` resolves from `get_settings().db_schema` per D-15. `id` carries no Python or ORM-side default; DB DEFAULT `uuidv7()` is authoritative per D-21. Audit FKs to `platform_users` are not declared at the SA layer (PlatformUser model lands at Step 5.1; DB enforces the FK).
- `src/admin_backend/schemas/tenant.py`: `TenantRead` Pydantic v2 model. `ConfigDict(from_attributes=True)`. Audit-actor IDs hidden from response. `monthly_revenue_usd` (NUMERIC) serialises to JSON as **string** via `field_serializer(when_used="json")` to preserve precision. Provisional defaults applied: snake_case keys (Q1), ISO 8601 with offset (Q4), nulls explicit not omitted (Q7), NUMERIC-as-string (Q11).
- Re-export modules (`models/__init__.py`, `schemas/__init__.py`).
- New tests: `tests/unit/test_tenant_model.py` (T1-T6, 6 cases) and `tests/unit/test_tenant_schemas.py` (S1-S7, 7 cases).
- New CLAUDE.md decision **D-28** capturing the provisional API-response-shape defaults pending Step 2.0.

**Scope out.**
- Repository (Step 3.2).
- Router (Step 3.3).
- List-response wrapping (Step 3.2/3.3 when consumer exists).
- Other resources (Steps 4.5, 5.x, 6.x — same pattern reused).
- Write schemas (post-v0 per FN-AB-12).
- Relationship to `PlatformUser` (Step 5.1+).

**Acceptance criteria.**
- 13 new pytest cases pass; 57 pre-existing pass; full suite at 70 passed (the 14 `scripts/smoke_test.py` collection errors are pre-existing pytest-config drift, unchanged from HEAD).
- mypy strict clean on `src/admin_backend/models src/admin_backend/schemas src/admin_backend/db`.
- `check_setup.sh` 35/35.
- `select(Tenant).compile(dialect=postgresql.dialect())` produces a SELECT qualified with `<DB_SCHEMA>.tenants`.
- `TenantRead.model_dump_json()` shows snake_case keys, NUMERIC-as-string, nulls explicit, ISO-8601 timestamps with offset, audit-actor IDs absent.

**Coordination.**
- None.

**Rough effort.** 45-60 min.

---

## Step 3.2 — TenantsRepo (Tenants Repository class)

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Implement `TenantsRepo` — the Repository class for the `tenants` table. Owns SELECT queries; RLS-bound via session GUCs (D-03 / D-24); the Repo is unaware of multi-tenancy mechanics. Establishes the Repository pattern reused at Steps 4.5, 5.1, 5.2, 5.3, 6.1, 6.2.

**Scope in.**
- `src/admin_backend/repositories/tenants.py` + `repositories/__init__.py`. Class `TenantsRepo` with three async read methods: `get_by_id(session, tenant_id) -> Tenant | None`, `list_all(session) -> list[Tenant]`, `list_by_status(session, status) -> list[Tenant]`. Each accepts an `AsyncSession`; visibility flows from the session's GUCs (no `tenant_id` argument, per D-24). Per D-17, missing/RLS-filtered rows surface as `None`; the router converts to 404.
- Integration tests at `tests/integration/test_tenants_repo.py` (R1-R9, 9 tests): happy-path under PLATFORM, missing-id, list_all under PLATFORM, list_all under TENANT (cross-tenant isolation, **load-bearing R4**), get_by_id under TENANT for other-tenant id (**load-bearing R5**), list_by_status filter under PLATFORM, list_by_status under TENANT, PLATFORM list_all unfiltered across statuses (validates D-29's OR-branch on `tenants` at the Repo layer), orphan TENANT context (defensive).
- Shared repo-test fixtures added to `tests/integration/conftest.py` for downstream reuse: `engine`, `session_factory`, `platform_auth`, `tenant_auth_factory`, `make_tenant` (commits + DELETE-tracked teardown — only works post-3.0), `platform_session`, `tenant_session_factory`.
- **Step 3.1 amendment bundled in the same commit.** `models/tenant.py` adds `server_default=FetchedValue()` to `id`, `status`, `created_at`, `updated_at` so SQLAlchemy correctly omits these columns from INSERT and reads them back via RETURNING. Without it, the ORM sends explicit NULLs that defeat the DDL DEFAULTs (NOT NULL violation on `created_at`). `FetchedValue()` declares the *existence* of a DB-side default without redeclaring the SQL — preserves D-21's intent (DDL is single source of truth for what the default is) without creating an FN-AB-13 maintenance trap. Test T6 in `test_tenant_model.py` tightened: replaces `server_default is None` with `isinstance(col.server_default, FetchedValue)` for the four columns.

**Scope out.**
- Router (Step 3.3).
- List-response wrapping (`TenantListResponse`, `Pagination`) (Step 3.3).
- Write methods (post-v0 per FN-AB-12).
- Other Repos (4.5, 5.x, 6.x — same pattern reused).

**Acceptance criteria.**
- 79 pytest passes (70 prior + 9 new); R4 and R5 cross-tenant isolation tests explicitly green.
- mypy strict clean on `repositories`, `models`, `schemas`, `db`.
- `check_setup.sh` 35/35.
- Smoke command shows `TenantsRepo` with three methods.
- RLS smoke test still 64 PASS — Step 3.2 must not break DB-level isolation.

**Coordination.**
- None.

**Rough effort.** 45-60 min.

---

## Step 3.3 — Tenants router + 3 endpoints + canonical endpoint doc

**Status.** DONE
**Owner.** CLAUDE_CODE

**Note on scope expansion.** The original entry called for "two endpoints, list returns `list[TenantRead]`, references `docs/api-contract.md`" at 60-90 min. None of that matched what shipped. A contract review with the frontend dev surfaced real shape requirements: three endpoints (list, stats, detail), new schemas, new Repo methods, a stub for module entitlements, and two new D-XX entries (D-30, D-31). `docs/api-contract.md` is still in TEMPLATE state; the response shapes were locked directly in `prompts/step-3_3-tenants-router-2026-05-02.md`. Effort closer to a full day. Entry rewritten in this commit.

**Goal.** First domain endpoints. Patterns locked here propagate to every subsequent endpoint step (4.5, 5.x, 6.x): URL prefix wiring, response envelope, error shape, RLS-under-aggregates, per-endpoint documentation.

**Scope in.**
- API URL prefix: new `settings.api_prefix = "/api/v1"` with validator. `/v1/health`, `/v1/ready`, `/v1/openapi.json`, `/v1/docs`, `/v1/redoc` moved to `/api/v1/...`. `AuthMiddleware.PUBLIC_PATHS` updated; `tests/integration/test_health.py` and `test_middleware.py` URL hardcoding updated.
- `src/admin_backend/routers/v1/tenants.py`: `APIRouter(prefix="/tenants", tags=["tenants"])` with three handlers — `list_tenants`, `tenants_stats`, `get_tenant`. `/stats` declared before `/{tenant_id}` (FastAPI is first-match-wins). `app.include_router(tenants.router, prefix=settings.api_prefix)` in `main.py`.
- New schemas (`schemas/tenant.py`): `Module`, `Pagination`, `TenantsListItem`, `TenantsListResponse`, `TenantsStatsResponse`, `TenantDetail`. `TenantRead` from 3.1 untouched. List wraps as `{items, pagination}` per new D-30; field semantics frozen append-only per new D-31.
- New Repo methods (`repositories/tenants.py`): `list_with_aggregates(session, *, tier, search, offset, limit) -> tuple[list[TenantListRow], int]` with correlated scalar subqueries for `num_stores` / `num_users_active`; `get_by_id_with_aggregates(session, tenant_id) -> TenantDetailRow | None`; `count_for_stats(session) -> tuple[int, int]`. The `.correlate(Tenant)` is what scopes the per-row aggregates correctly. Existing 3.2 methods untouched.
- Lightweight ORM stubs (`models/_lightweight_stubs.py`): `Store` and `TenantUser` with the minimal columns needed for the count subqueries. The TenantUser stub uses `postgresql.ENUM(name="tenant_user_status_enum", create_type=False)` so the `status = 'ACTIVE'` comparison generates valid SQL against the live PG enum column. Docstring carries the explicit Alembic-autogenerate warning. Stubs go away at 4.5 / 5.2.
- Module entitlement stub (`repositories/_module_entitlements_stub.py`): hardcoded `{tenant_id -> [module_codes]}` dict with a `get_modules_for_tenant` helper. Tracked by new FN-AB-16.
- Error model: new `TenantNotFoundError(ClientError)` with `code = "TENANT_NOT_FOUND"`, `http_status = 404`, `public_message = "Tenant not found"`. Error envelope grew a `details: None` field on every response (slot reserved for future per-field validation info).
- Conftest fixtures (`tests/integration/conftest.py`): `make_store` and `make_tenant_user` mirroring 3.2's `make_tenant`. Both use raw SQL INSERTs (the lightweight stubs don't declare every NOT NULL column); they honour the audit-actor XOR CHECKs (NULL/NULL pairs) and the tenant_users status-consistency CHECKs (auth0_sub + invitation_accepted_at populated when status='ACTIVE').
- Tests:
  - 21 integration tests at `tests/integration/test_tenants_router.py` (L1-L10, S1-S3, D1-D6, A1-A2). Two are load-bearing: **L9** verifies per-row aggregates scope correctly via `.correlate(Tenant)`, and **D4** verifies cross-tenant detail returns 404 (RLS-blocked surfaces as not-found per D-17).
  - 1 xfail-strict tripwire at `tests/unit/test_module_entitlements.py` for FN-AB-16.
- New CLAUDE.md decisions: D-30 (response envelope is list-only), D-31 (response field semantics are append-only). New forward-note: FN-AB-16 (module stub cleanup).
- `docs/endpoints/tenants.md` rewritten as the canonical 8-section endpoint doc (3 endpoints × 8 sections each); future endpoint docs copy-paste-edit this structure.

**Scope out.**
- Other resources (4.5, 5.x, 6.x).
- Write endpoints (post-v0 per FN-AB-12).
- `tenant_module_access` table (FN-AB-16).
- `legal_name`, live MRR (cut from contract).
- Audit-actor exposure on detail (Step 3.1 hide policy stands).
- RBAC enforcement (Step 6.1).
- `/lookups` endpoint (separate concern).

**Acceptance criteria.**
- 100 pytest passes + 1 XFAIL (79 prior + 21 new).
- mypy strict clean across `src/admin_backend` (28 source files).
- check_setup 35/35.
- RLS smoke test still 64 PASS.
- Cross-tenant isolation test D4 green; aggregate-under-RLS test L9 green.
- Manual curl on `/api/v1/tenants`, `/api/v1/tenants/stats`, `/api/v1/tenants/{ephemeral}` returns the documented shapes.
- OpenAPI generates with all three operations and the new schemas at `/api/v1/openapi.json`.

**Coordination.**
- Share OpenAPI spec with frontend developer.

**Rough effort.** ~one day (vs. the original 60-90 min estimate — see "Note on scope expansion").

---

## Step 3.4 — Confirm GCP dev environment ready

**Status.** TODO
**Owner.** HUMAN (you + GCP-helper)

**Goal.** Hard gate for D#4 cloud deploy.

**Scope in.**
- Verify with GCP-helper:
  - Cloud SQL up, Postgres 15, ltree extension flag set, accessible via Cloud SQL Auth Proxy.
  - GKE Autopilot cluster up, kubectl works.
  - Artifact Registry repo created.
  - Service account with required IAM bindings.
  - Secret Manager accessible.
- Receive credentials/access:
  - Service account JSON for local Cloud SQL Auth Proxy.
  - KUBECONFIG (or `gcloud container clusters get-credentials` instructions).
  - Cloud SQL connection string.
  - Artifact Registry hostname.

**Scope out.**
- Production environment (covered later).

**Acceptance criteria.**
- `cloud_sql_proxy -instances=...` connects and `psql` to the cloud DB works.
- `kubectl get nodes` works.
- Service account can pull from Artifact Registry.

**Coordination.**
- GCP-helper delivers.

**Rough effort.** Varies; gate not effort.

---

## Step 3.4.5 — `tenant_module_access` table + FN-AB-16 cleanup

**Status.** DONE
**Owner.** CLAUDE_CODE

**Note on numbering.** Back-fill that surfaced during Step 3.5 (seed loader) planning. Doing it before the seed loader keeps `tenant_module_access` data in the database (not in a Python dict that the loader would have to keep synchronised). Numerically slotted between 3.4 and 3.5; landed chronologically after Step 3.3 + the PG_ENUM convention note.

**Goal.** Resolve FN-AB-16 (the Step 3.3 module entitlement stub). Add the 11th application table (`tenant_module_access`) so per-tenant module data lives in the database with full lifecycle audit, not in a Python dict.

**Scope in.**
- New frozen DDL `db/raw_ddl/Ithina_postgres_SQL_DDL_tenant_module_access_v1.sql`. Pattern (a) audit-actors per D-13 (typed FK direct to platform_users; no `*_by_user_type`). Two new PG enums (`module_code_enum`, `module_access_status_enum`). Three CHECK constraints: PK, UNIQUE (tenant_id, module), and the disabled-pair-XOR / status-consistency pair. RLS + FORCE; D-29 unconditional OR-clause RLS policy (tenant_id NOT NULL).
- Alembic migration `cd2a02e452ae` (down_revision `21e2ad16303a`). Unqualified names throughout (matching Step 3.0's precedent; env.py sets search_path inside the alembic transaction). Trigger uses `set_updated_at_timestamp()` (the actual shared-utility name; the prompt sketch's `set_updated_at_now()` was a typo, corrected during pre-flight). Seeds six rows into `lookups` for the `module_code` list with display names matching the Step 3.3 stub for cutover stability.
- New ORM models: `TenantModuleAccess` (PG_ENUM per the convention; FetchedValue defaults on id/created_at/updated_at) and `Lookup` (the `lookups` table's first ORM consumer).
- `TenantsRepo.list_with_aggregates` and `get_by_id_with_aggregates` switch from the stub call to a correlated `jsonb_agg` subquery joined to `lookups` for display-name resolution. `aggregate_order_by` controls element order inside the JSON array; COALESCE wraps the empty case to `'[]'::jsonb`. The JOIN casts `tenant_module_access.module` to text to bridge the enum-vs-text mismatch (same gotcha as the PG_ENUM convention reminder; documented inline).
- Stub cleanup pair: `_module_entitlements_stub.py` and `tests/unit/test_module_entitlements.py` both deleted in this commit. The xfail-strict tripwire mechanism worked as designed.
- New conftest fixtures: `make_platform_user` (status='INVITED' default keeps audit-actor + auth0_sub + invitation_accepted_at all NULL — simplest CHECK shape; status='ACTIVE' supported with the companion fields populated) and `make_tenant_module_access` (validates DDL CHECK constraints client-side: status=DISABLED requires both disabled_at AND disabled_by_user_id).
- Test rewrites: L10 → `test_l10_modules_from_table_with_display_name_resolution` (3 ENABLED + 1 DISABLED on TENANT-A; 1 ENABLED on TENANT-B; verifies display name JOIN, DISABLED filter, display_order, cross-tenant isolation). D6 → `test_d6_detail_modules_from_table`. New L10b → `test_l10b_tenant_with_no_modules_returns_empty_array` (guards the COALESCE path).
- Smoke test extends `test_15_multi_tenant_or_clause_truth_tables` to a 5th table (`tenant_module_access`); `test_16_platform_can_insert_into_multi_tenant_tables` gets a 5th INSERT assertion. Total 64 → 74 PASS.
- CLAUDE.md: FN-AB-16 marked **RESOLVED at Step 3.4.5**. Schema state line (10 → 11 application tables, 18 → 20 enums, 5/5 → 6/6 multi-tenant tables). Smoke count 64 → 74. Step 3.4.5 Completed bullet added.
- architecture.md: 10 → 11 tables; new row in the Schema-and-storage table mapping.

**Scope out.**
- Step 3.5 (seed loader) — runs after this; will load `tenant_module_access` rows from the Excel sheet as a normal sheet.
- Audit-trigger writes to `audit_logs` for tenant_module_access changes — Step 6.2 territory.
- API endpoints managing module access (POST/PATCH) — post-v0 per FN-AB-12.
- `/api/v1/lookups/module_code` endpoint for frontend dropdowns — separate concern.

**Acceptance criteria.**
- 1 new migration applied; round-trip clean (upgrade → downgrade → upgrade).
- 101 pytest passes (no XFAIL — tripwire was deleted along with the stub).
- mypy strict clean across `src/admin_backend` (29 source files).
- check_setup 35/35.
- Smoke test 74 PASS.
- FN-AB-16 marked RESOLVED in CLAUDE.md.
- L10b explicitly green (the empty-modules COALESCE guard).

**Coordination.**
- None.

**Rough effort.** Half a day.

---

## Step 3.5 — Dev seed loader from Excel

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Single Python package under `scripts/seed_dev_data/` that reads `data/ithina_dev_seed_data.xlsx` and inserts 11 sheets' worth of data into the dev Postgres, honouring D-21 (UUIDv7) via per-sheet `excel_id → db_id` mapping and FK substitution. After this step, `curl /api/v1/tenants` returns 7 real tenants with their actual store / active-user / module counts — the first time the API surfaces meaningful content end-to-end.

**Scope in.**
- `data/ithina_dev_seed_data.xlsx` committed to the repo (binary blob, source of truth for dev seed data).
- `scripts/seed_dev_data/` package: `__main__.py` (CLI with `--reset`/`--dry-run`/`--sheets` flags + production-refusal guard), `runner.py` (orchestrator), `excel_reader.py` (asymmetric `_is_null_ish` strict + `_is_phantom_cell` broad helpers), `uuid_mapper.py`, `column_mappings.py` (single source of truth for Excel-to-DB column correspondence with drift detection), `truncate.py` (single multi-table TRUNCATE statement, no CASCADE), `loaders/` subpackage (one file per sheet).
- 8 standard loaders (mechanical ~25-line files using `_base.insert_and_register`): `tenants`, `stores`, `tenant_users`, `roles`, `permissions`, `user_role_assignments` (later specialised), `tenant_module_access` (later specialised). Wait — final shape: 5 standard + 5 specialised.
- 5 specialised loaders:
  - `platform_users` — two-phase self-reference (Anjali is `created_by` herself).
  - `org_nodes` — multi-pass parent-first ordering.
  - `role_permissions` — junction table; bypasses `_base.insert_and_register` (no `id` column, composite PK).
  - `user_role_assignments` — per-row tenant impersonation via `set_config('app.tenant_id', ..., true)` for TENANT-side rows under FN-AB-14's IS-NULL-gated policy (D-29).
  - `tenant_module_access` — synthesises the three NOT NULL audit-actor FKs at load time by looking up Anjali by email (the seed's universal "system actor"). New CLAUDE.md "Note on seed Excel shape" convention captures the principle: Excel is a seeding mechanism, not source of truth; system-concern columns may be synthesised.
- AuthContext path (b): synthetic-but-valid-shaped values mirroring `tests/integration/conftest.py:_VALID_AUTH_BASE` (no `model_construct` bypass). Sentinel `user_id` for the loader's PLATFORM session.
- 5 unit tests in `tests/unit/test_seed_column_mappings.py` (drift detection: known/unknown columns, unknown sheet, every-sheet-has-DB-columns, every-FK_REF-has-target).
- 5 integration tests in `tests/integration/test_seed_loader.py`: L1 end-to-end, L2 PLATFORM-visible row counts, L2b URA total across tenants (verifies IS-NULL-gated visibility), L3 sentinel rows + audit-actor synthesis assertion, L4 production-refusal.
- `scripts/__init__.py` added (empty marker) so `scripts.seed_dev_data` resolves consistently under mypy.
- `scripts/seed_dev_data/README.md` covering usage, UUIDv7 substitution mechanism, the five specialised-loader explanations, the audit_logs skip, and the rollback procedure.
- Bundled drift fix on `tests/unit/test_session.py:test_t11`: assertion was stale relative to D-29 (assumed PLATFORM-without-impersonation sees 0 rows on `tenants`), only passed pre-3.5 because the table was empty. Updated to `count >= 0` with a docstring explaining the history.
- CLAUDE.md updates: Step 3.5 Completed bullet, "Note on seed Excel shape" convention paragraph in Code conventions section, line-924 drift fix on the `ENVIRONMENT` env-var documentation row (`local | development | staging | production`).
- Step 7.3.1 entry gets a note that this step is its prototype — with the inversion explicitly called out.

**Scope out.**
- `audit_logs` table and seed loading (Step 6.2 territory; no DDL yet).
- Customer-data converter (Step 7.3.1 — post-v0).
- Per-row error reporting and recovery (Step 7.3.1's customer tool gets the careful surface).
- Idempotency beyond `--reset` (UPSERT semantics are 7.3.1).
- Lookup-table seeding (lookups are seeded by their owning migrations, e.g. Step 3.4.5's `module_code` rows).
- DDL changes in response to data drift (resource_enum stayed at 12 values; user fixed the Excel — no migration).

**Acceptance criteria.**
- 11 of 12 sheets loaded (audit_logs skipped). `--reset` clean.
- 111 pytest passes (was 101; +10 new tests).
- mypy strict clean across `src/admin_backend` AND `scripts/seed_dev_data` (48 source files).
- check_setup 35/35.
- Smoke test 74 PASS post-truncate.
- Manual curl: `/api/v1/tenants/stats` → `{"total_tenants": 7, "total_stores": 25}`; `/api/v1/tenants?search=Buc` returns Buc-ee's with 3 stores, 6 active users, 6 modules.
- Production-refusal verified by `test_l4_seed_refuses_production` (sets `ENVIRONMENT=production` + `AUTH_CLIENT_MODE=AUTH0` + a clean issuer URL so Settings construction succeeds; the seed loader's guard fires; exit 2; no DB writes).

**Coordination.**
- None.

**Rough effort.** ~1.5 days. Multiple seed-data drift cycles (the user pre-cleaned Excel between iterations); one-line reader-asymmetry insight (`_is_phantom_cell` vs `_is_null_ish`) added late in implementation.

---

## Step 3.6 — Lookups batch endpoint + seed data extension

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Single endpoint `GET /api/v1/lookups?lists=...` returns a map of `{list_name: [items]}` so the frontend loads all dropdown values for the tenants list page in one request rather than 5+ sequential. Migration seeds the 4 PG-enum-backed categories that the tenants UI filters on. Frontend integration is gated on this — Amit's tenant-list page needs filter dropdown values.

**Scope in.**
- Migration `0644a4186e48` (down_revision `cd2a02e452ae`) seeds 17 rows into `lookups`: `tenant_tier` (4), `tenant_region` (2), `tenant_status` (5), `tenant_industry` (6). Unqualified table names per Step 3.4.5 precedent. Round-trip clean.
- `LookupsRepo` at `src/admin_backend/repositories/lookups.py` — stateless singleton mirroring `TenantsRepo`'s shape; method `get_lists_batch(session, list_names) -> dict[str, list[Lookup]]` with predictable-empty-shape guarantee (every requested name appears as a key with `[]` for unseeded lists).
- `schemas/lookup.py`: `LookupItem` + `LookupsBatchResponse`. Top-level `{lookups: ...}` envelope (not a bare map) for future metadata extensibility.
- `routers/v1/lookups.py`: `APIRouter(prefix="/lookups", tags=["lookups"])` with `get_tenant_session_dep` for session-getter parity with the tenants router. Comma-separated `lists` query param (whitespace-stripped; empty input returns `{lookups: {}}` with 200). Auth via middleware; no explicit `Depends(require_auth)`.
- main.py wired with `app.include_router(lookups_router.router, prefix=settings.api_prefix)`.
- 4 integration tests in `tests/integration/test_lookups_router.py` mirroring the existing `app_client` + `_platform_jwt(settings)` + sync `TestClient` pattern.
- CLAUDE.md updates: Step 3.6 Completed bullet; new "Note on batch-by-key response envelope" alongside the existing PG_ENUM and seed-Excel notes.
- `docs/openapi.json` regenerated with rich field descriptions for Amit's frontend codegen.

**Scope out.**
- Country lookup design (deferred — see Known follow-ups below).
- Per-list endpoint (`GET /api/v1/lookups/{list_name}`). Batch is the only shape; if a future need emerges, additive surface.
- Cache headers. Defer post-v0.
- Lookup CRUD via API. Step 6.x territory; lookups extensions go via migrations until then.

**Acceptance criteria.**
- 17 rows seeded; `lookups` total goes 6 → 23.
- 115 pytest passes (was 111; +4 new).
- mypy strict clean on 51 source files.
- check_setup 35/35.
- Smoke test 74 PASS post-truncate.
- Migration round-trip clean; downgrade leaves only the 6 `module_code` rows from 3.4.5.
- `docs/openapi.json` includes `/api/v1/lookups` with rich descriptions on summary, query param, and `LookupItem` fields.
- Manual curl returns 5 populated categories + `country: []` (the predictable-empty deferred case).

**Known follow-ups.**
- **Country lookup design deferred.** The dev seed Excel's `tenants.country` carries mixed-case literals (`Canada`, `France`, `Poland`) that violate `lookups.ck_lookups_code_format` (`^[A-Z][A-Z0-9_]*$`). Three viable resolutions: (a) ISO 3166 alpha-3 codes in `lookups` plus a code↔display mapping at the frontend; (b) UPPER-cased literals in `lookups` plus a frontend normalisation step on filter values; (c) a country-aware migration that re-seeds `tenants.country` to UPPER and adds the rows. Each is a real design decision worth its own deliberation; not bundled into this seed migration. Frontend hardcodes the 5 country values for first integration. The endpoint is country-tolerant — `?lists=country` returns `{"country": []}` per the predictable-empty shape; future country lookup data populates without endpoint changes. Tracked here as a deferred design question, not a FN-AB.
- **Comma-separated vs repeated `lists` query-param style.** Locked as comma-separated for v0; reversible to `?lists=a&lists=b` repeated form via a one-line parser change if a frontend HTTP library forces the other shape.

**Coordination.**
- Frontend integration unblocked: Amit's tenants list page loads filter dropdowns via `GET /api/v1/lookups?lists=tenant_tier,tenant_region,tenant_status,tenant_industry,module_code`.

**Rough effort.** ~half day. Pre-flight verified 7 placeholder names against existing code (TenantsRepo singleton pattern, `get_tenant_session_dep` import, sync TestClient fixture); migration's first run hit the `ck_lookups_code_format` CHECK on country codes; deferred per the user's call.

---

## Step 4.1 — Cloud SQL schema bring-up via Cloud Run Job

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Apply Alembic migrations to Cloud SQL dev via a Cloud Run Job using the same image as the service. Verify schema and re-run smoke test against the cloud DB.

**Note on ordering.** Re-ordered: original BUILD_PLAN had this step before 4.2/4.3, but Cloud SQL is private-IP-only, so migrations must run from inside the VPC, which means the image must already be in Artifact Registry. **Run AFTER 4.2/4.3.** The numbering stays 4.1 for build-plan continuity; the runtime ordering is 4.2 → 4.3 → 4.1 → 4.4.

**Scope in.**
- Trigger the `admin-backend-alembic` Cloud Run Job (provisioned by the Terraform module from Step 1.7.1) with the freshly-pushed image tag.
- Job runs `uv run alembic upgrade head` against Cloud SQL via direct VPC egress (no Auth Proxy sidecar — D-33's no-sidecar property).
- Verify schema applied (`\dt`, `\d <table>` via a one-off psql shell from a bastion or via Cloud SQL Studio).
- Re-run smoke test (Step 1.5 shape) against Cloud SQL — same 74-assertion truth-table.

**Scope out.**
- Application deploy (Step 4.4).
- Production schema (Step 8.2 / Step 8.1.1).

**Acceptance criteria.**
- Cloud Run Job execution completes successfully (status `Succeeded`).
- Alembic head matches local head.
- Smoke test prints PASS for every check (74/74).

**Coordination.**
- HUMAN runs `gcloud run jobs execute` against the dev project; CLAUDE_CODE drives the prep + verification.

**Rough effort.** 30-60 min.

---

## Step 4.2 — Dockerfile + image build

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Production-shaped Docker image for the admin backend.

**Scope in.**
- Multi-stage `Dockerfile`:
  - `builder` stage: `python:3.12-slim`. Install uv. Copy `pyproject.toml` + `uv.lock`. Run `uv sync --no-dev --frozen`.
  - `runtime` stage: `python:3.12-slim`. Copy `.venv` from `builder` stage. Copy `src/`. CMD: uvicorn.
- `.dockerignore` excluding `.venv`, `tests/`, `keys/`, `data/`, `.git`, etc. (`migrations/` IS included — image runs Alembic in the bring-up Job).
- Build locally. Test running with local DATABASE_URL.

**Scope out.**
- Push to registry (Step 4.3).
- Cloud Run dev deploy (Step 4.4 per D-33).

**Acceptance criteria.**
- `docker build -t admin-backend:dev .` succeeds.
- `docker run --network=host -e DATABASE_URL=... admin-backend:dev` runs the app.
- `curl localhost:8000/api/v1/health` → 200 from container.

**Coordination.**
- None.

**Rough effort.** 60 min.

---

## Step 4.3 — Push image to Artifact Registry

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Image available for Cloud Run dev deploy (Step 4.4) and future GKE prod deploy (Step 8.2). Same artifact, two runtime shapes per D-33.

**Scope in.**
- `gcloud auth configure-docker asia-south1-docker.pkg.dev`.
- Tag image with Artifact Registry hostname.
- `docker push`.
- Verify with `gcloud artifacts docker images list`.

**Scope out.**
- Cloud Run dev deploy (Step 4.4).

**Acceptance criteria.**
- Image visible in Artifact Registry under both `:v0.1.0` and `:latest`.

**Coordination.**
- None.

**Rough effort.** 15-30 min.

---

## Step 4.3.5 — Dev seed loader against Cloud SQL via Cloud Run Job

**Status.** DONE
**Owner.** CLAUDE_CODE (image build, docs) + HUMAN (GCP-side execute)

**Note on numbering.** Slotted in between Step 4.3 (Artifact Registry push) and Step 4.4 (Cloud Run deploy + cross-tenant test). Same fitted-in pattern as Step 3.4.5. Step 4.4's section 5 (cross-tenant test) was blocked on seed data; this step unblocks it.

**Goal.** Run the Step 3.5 seed loader against the dev Cloud SQL instance so the deployed admin-backend service surfaces real content, and so Step 4.4's cross-tenant test has tenants to test against.

**Scope in.**
- One-off image v0.1.3-seed extending v0.1.2 with `scripts/seed_dev_data/`, `data/ithina_dev_seed_data.xlsx`, `scripts/__init__.py`, and `openpyxl==3.1.5` (uv.lock-resolved version, installed via `uv pip install` against the inherited `/opt/venv` because v0.1.2's venv ships no pip; uv 0.5.4 binary copied from `ghcr.io/astral-sh/uv:0.5.4` for the install — keeps `pyproject.toml` unchanged).
- `Dockerfile.seed` and `scripts/build_seed_image.sh` (with EXIT-trap `.dockerignore` restoration + post-build `git diff` belt-and-suspenders check) added to repo. Reversed at Step 4.4.1.
- Cloud Run Job `admin-backend-seed-dev-data` deployed via `gcloud run jobs describe admin-backend-alembic --format=export` → 4-field sed edit → diff review → `gcloud run jobs replace`. Inherits the alembic Job's wiring exactly: SA, VPC, subnet, VPC egress, secret binding, env vars (already correct ENVIRONMENT=development), resource limits. Only image tag, command, args, and Job name differ.
- FN-AB-18 added to CLAUDE.md naming the temporary deviation.
- CSD-03 added to CLAUDE.md documenting direct-SQL verification paths and limits on private-IP-only Cloud SQL (discovered when both Path 1 `gcloud sql connect` and Path 2 Cloud SQL Studio failed during this step's verification phase).
- Step 4.4.1 added to BUILD_PLAN.md as a hard precondition of Step 4.5.
- Existing `prompts/step-4_4-cloud-run-deploy-dev.md` amended in-place: stale `/v1/` paths corrected to `/api/v1/`; placeholder cross-tenant UUIDs replaced with real seed UUIDs; sections 1-4 marked DONE (deploy/smoke shipped 2026-05-03).
- `platform_users.updated_at` lands at Job execution time, not Excel stamps, due to the loader's two-phase load on that table (self-reference resolution: Phase 1 INSERT with NULL audit-actors, Phase 2 UPDATE to resolve them; the BEFORE-UPDATE trigger refreshes `updated_at = NOW()`). Cosmetic drift between local-seeded and cloud-seeded `platform_users.updated_at` for those 3 rows; INSERT-only tables preserve Excel stamps as-loaded.
- Build script's post-build-diff check had an ordering bug (false-positive failure on success path because the `git diff` ran before the EXIT trap restored `.dockerignore`). Caught and fixed mid-run by adding an explicit `restore_dockerignore` call before the diff check; trap left as SIGKILL safety net.

**Scope out.**
- Step 4.4 cross-tenant test execution. Stays as Step 4.4 work, unblocked by this step.
- Reversal of the temporary discipline deviation — Step 4.4.1.
- Re-deploying the admin-backend service (stays on v0.1.2).
- pyproject.toml changes (openpyxl stays in dev deps).
- BUILD_PLAN.md Step 4.4 GKE→Cloud-Run wording rewrite (pre-existing drift; deferred to whenever Step 4.4 fully flips to DONE).

**Acceptance criteria.**
- v0.1.3-seed builds; in-image verification's four checks all pass (files present, openpyxl import + version, loader package import, Settings construction smoke).
- `gcloud run jobs replace` succeeds; YAML diff vs admin-backend-alembic shows only the four expected field changes.
- `gcloud run jobs execute admin-backend-seed-dev-data --wait` exits 0. **Exit 0 is NOT acceptance on its own** — verification curls are.
- Verification path was end-to-end deployed-service curl + minted PLATFORM JWT (per CSD-03 — direct SQL paths unavailable on private-IP-only Cloud SQL). Four endpoints exercised: `/api/v1/tenants/stats` returned `{total_tenants:7, total_stores:25}`; `/api/v1/platform-users` returned 3 users; `/api/v1/lookups` returned 5 lists totaling 23 rows; `/api/v1/tenant-users` with PLATFORM JWT returned 17 users across 7 tenants (exercises D-29 OR-clause end-to-end). Per-table direct row counts and FORCE-RLS posture not independently re-verified — chain-of-evidence trust on Step 4.1's 74/74 smoke for FORCE-RLS, and on the loader's per-sheet "ok X loaded" commit confirmations for the 6 unverified table counts.
- Build script's post-build `git diff` confirms `.dockerignore`, `Dockerfile`, `pyproject.toml`, `uv.lock` all unchanged.

**Coordination.**
- Frontend integration on the deployed dev URL is unblocked.
- Step 4.4 (cross-tenant test) is unblocked.
- Step 4.4.1 follows as repo cleanup.

**Rough effort.** ~1 hour Claude Code + ~10 min operator GCP commands + ~5 min verification curls.

---

## Step 4.4 — Backend Cloud Run deploy + smoke + cross-tenant test (dev)

**Status.** DONE
**Owner.** HYBRID (CLAUDE_CODE drives + HUMAN runs gcloud)

**Goal.** Real backend image deployed to Cloud Run; cross-tenant isolation verified end-to-end against the cloud DB.

**Note.** Per D-33, dev deploys to Cloud Run, not GKE. The original "k8s manifests" version of this step has been moved to Step 8.2 (production) and rewritten there. The dev image artifact is identical to what prod will eventually run on GKE — same container, two runtime shapes.

**Scope in.**
- HUMAN runs `gcloud run deploy admin-backend --image <Artifact Registry tag> --region asia-south1 --vpc-connector ... --service-account ... --set-secrets=...` (the exact flags are in the deploy commands shipped with the Step 4.4 prompt).
- Verify the service URL responds (`/api/v1/health`, `/api/v1/ready`).
- Mint a PLATFORM JWT and a TENANT JWT against the cloud DB; verify cross-tenant isolation: TENANT-A asking for TENANT-B's user_id returns 404 `TENANT_USER_NOT_FOUND` (the load-bearing T9 assertion shape).
- Verify structured JSON logs surface in Cloud Logging.

**Scope out.**
- Other endpoints (Step 4.5).
- Production deploy (Step 8.2).

**Acceptance criteria.**
- All current endpoints respond via the Cloud Run service URL.
- Cross-tenant isolation verified against Cloud SQL, not just local.
- Logs visible in Cloud Logging with the expected JSON fields (request_id, tenant_id, user_id, latency_ms).

**Coordination.**
- **Hand frontend team: deployed Cloud Run service URL + OpenAPI spec link (`https://<dev-url>/api/v1/openapi.json`).**
- **Frontend begins integrating against GCP dev today, not later.**

**Rough effort.** 60-90 min (Cloud Run is simpler than GKE; first apply may surface a VPC connector / SA-binding hiccup).

**Outcome.** Two-stage delivery: sections 1-4 (Cloud Run deploy v0.1.2, /api/v1/health smoke, OpenAPI fetch, log inspection) shipped 2026-05-03; section 5 (cross-tenant isolation test) blocked on seed data until 2026-05-04, unblocked by Step 4.3.5, ran 2026-05-04. Section 5 verdict: 16/16 substantive matrix cells passed across four sub-matrices — cross-tenant isolation on /tenants (6/6 after re-minting JWTs with cloud-side UUIDs per F1+F2 fix path; see scripts/jwt/generate.sh hazard note in commit body), PLATFORM-only rejection (4/4, includes one cell reclassified as TENANT RLS-aggregate scope verified — /tenants/stats is multi-user-type per Step 3.3, see FN-AB-21), TENANT positive coverage (3/3, /stores deferred to Step 4.5), PLATFORM positive coverage exercising D-29 OR-clause (3/3). Zero isolation breaches, zero 5xx. RLS-as-404 (D-17) confirmed on cross-tenant detail probes via canonical TENANT_NOT_FOUND envelope; PLATFORM_ACCESS_REQUIRED envelope confirmed on /platform-users from TENANT JWT. Service stayed on v0.1.2 throughout. Frontend integration against deployed dev URL unblocked.

---

## Step 4.4.1 — Dockerfile.seed teardown + image discipline restoration

**Status.** DONE
**Owner.** CLAUDE_CODE (repo edits) + HUMAN (registry tag removal)
**Blocked by.** Step 4.3.5 acceptance (seed run verified successful; row counts and FORCE-RLS confirmed).
**Blocks.** Step 4.5 (next image build — Stores resource).

**Goal.** Restore the v0.1.2-era image discipline now that one-off seeding (Step 4.3.5) is done. Close FN-AB-18.

**Scope in.**
- Delete `Dockerfile.seed` from repo.
- Delete `scripts/build_seed_image.sh` from repo.
- Verify `.dockerignore`, `Dockerfile`, `pyproject.toml`, `uv.lock` are byte-identical to their pre-Step-4.3.5 state. Recovery if drift: `git checkout HEAD -- .dockerignore Dockerfile pyproject.toml uv.lock`.
- Operator removes the `v0.1.3-seed` tag from Artifact Registry: `gcloud artifacts docker tags delete asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend:v0.1.3-seed`.
- Operator decides Cloud Run Job fate: delete (default — recommended) or retain as paused artifact. The Dockerfile.seed pattern is reconstructable from the Step 4.3.5 prompt + git history if ever needed again.
- CLAUDE.md FN-AB-18 → status RESOLVED with closure note.

**Scope out.**
- The long-term prod/dev image split. Day 8 concern (pre-prod).
- Re-seeding mechanics. Reconstruct Dockerfile.seed from history if ever needed.

**Acceptance criteria.**
- `git ls-files | grep -E 'Dockerfile.seed|build_seed_image'` empty.
- `gcloud artifacts docker tags list ...` does not include `v0.1.3-seed`.
- `git diff HEAD~1 -- Dockerfile .dockerignore pyproject.toml uv.lock` empty (no drift in production files).
- CLAUDE.md FN-AB-18 has the RESOLVED suffix.
- Step 4.5 unblocked.

**Coordination.**
- Single-commit cleanup. Operator runs the registry/Job commands.

**Rough effort.** 15 minutes.

**Outcome.** Dockerfile.seed and scripts/build_seed_image.sh removed from repo HEAD. `git diff` confirms Dockerfile, .dockerignore, pyproject.toml, uv.lock unchanged from pre-Step-4.3.5 baseline. v0.1.3-seed tag deleted from Artifact Registry. admin-backend-seed-dev-data Cloud Run Job RETAINED as paused artifact (tracked as FN-AB-20). FN-AB-18 RESOLVED. Step 4.5 unblocked.

---

## Step 4.5 — Stores resource (model + schema + Repository class + router + tests)

**Status.** TODO
**Owner.** CLAUDE_CODE
**Blocked by.** Step 4.4.1 (Dockerfile.seed teardown — image discipline must be restored before the next image rebuild).

**Goal.** Second resource. Pattern from tenants applied. Includes `StoresRepo` (Stores Repository class).

**Scope in.**
- Same shape as Steps 3.1, 3.2, 3.3 but for Stores.
- `Store` model maps `stores_v5.sql`. tenant_id FK, org_node_id FK.
- `StoreRead` schema.
- `StoresRepo` (Stores Repository class) with `get_by_id`, `list_all`, `list_by_tenant`, `list_by_status`.
- `GET /v1/stores`, `GET /v1/stores/{store_id}`.
- Tests including cross-tenant isolation.
- Re-deploy to cloud.

**Scope out.**
- Other resources.

**Acceptance criteria.**
- All endpoints work locally and in cloud.
- Cross-tenant isolation test passes for stores.
- `docs/endpoints/stores.md` produced following `docs/endpoints/tenants.md` shape.
- mypy strict clean.

**Coordination.**
- **Frontend integrates new endpoints on GCP dev within 24 hours.**

**Rough effort.** 60-90 min.

---

## Step 4.6 — First frontend integration walkthrough (dev)

**Status.** TODO
**Owner.** HUMAN (you + frontend dev)

**Goal.** Frontend renders tenants/stores screens against deployed dev backend. Surface contract issues immediately.

**Scope in.**
- Frontend points at GCP dev URL.
- Walk through tenant list, tenant detail, stores list, stores detail.
- Capture mismatches: response shape, field names, auth header, CORS.
- Open tickets / Slack threads for each issue.
- Fixes applied in subsequent steps' coordination.

**Scope out.**
- Frontend code changes (frontend team's work).
- Other resources (covered as they're built).

**Acceptance criteria.**
- Frontend renders the screens that current endpoints support.
- Contract issues identified and triaged.

**Coordination.**
- Frontend team available for joint testing.

**Rough effort.** 30-60 min.

---

## Step 5.1 — Platform Users resource

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Staff users readable by PLATFORM JWTs only.

**Scope in (as shipped).**
- `PlatformUser` ORM model + `PlatformUserStatus` enum (`INVITED`,
  `ACTIVE`, `SUSPENDED`). 14 columns from
  `platform_users_v1.sql`; mirrors the `Tenant` model's shape
  (FetchedValue defaults, dialect-specific `postgresql.ENUM`,
  audit-actor columns left as raw UUIDs).
- `PlatformUserRead` + `PlatformUserListItem` (alias for
  `PlatformUserRead`; v0 list/detail share one shape) +
  `PlatformUserListResponse`. Audit-actor IDs and `auth0_sub`
  hidden by deliberate design.
- `PlatformUsersRepo` (stateless singleton): `list(...)`,
  `get_by_id(...)`. Sort key vali
  dation surfaces as
  `InvalidSortKeyError` (a ValueError) caught at the router and
  re-raised as `InvalidSortKeyClientError` (400).
- Router: `GET /api/v1/platform-users`, `GET /api/v1/platform-users/{user_id}`.
  PLATFORM-only access enforced via `_require_platform_auth(auth)` —
  the first concrete instance of v0 router-layer auth-tier
  checking. Three new error classes: `PlatformAccessRequiredError`
  (403), `PlatformUserNotFoundError` (404),
  `InvalidSortKeyClientError` (400).
- 10 integration tests in
  `tests/integration/test_platform_users_router.py`: 6 list
  (envelope/hidden-fields, status filter, search, sort, invalid
  sort -> 400, pagination), 2 detail (happy + 404), 2 auth
  (no-JWT 401, TENANT-JWT 403 — load-bearing).
- `docs/endpoints/platform-users.md` (8-section format;
  mirrors `tenants.md`). OpenAPI snapshot regenerated.
- No conftest changes — the existing `make_platform_user` factory
  (added at Step 3.4.5) already covers test needs.

**Scope out (as shipped).**
- Aggregates (role count, last-login). None for v0.
- Stats endpoint. Pagination total covers v0 needs.
- Write endpoints (post-v0).
- Auth0 sync logic (separate concern).
- Tenant-side visibility (PLATFORM-only by design).
- RBAC per-role distinctions (Step 6.1).

**Acceptance criteria (met).**
- Two endpoints live; 125 pytest passes (was 115; +10);
  mypy strict clean across `src/admin_backend`; check_setup
  35/35; alembic head unchanged at `0644a4186e48`; smoke test
  unchanged at 74 PASS (no RLS surface added).
- `docs/endpoints/platform-users.md` follows `tenants.md`'s
  8-section structure; OpenAPI spec at
  `docs/endpoints/openapi.json` lists both new endpoints with
  rich descriptions.

**Rough effort.** 60 min.

---

## Step 5.2 — Tenant Users resource

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Customer-side users readable by both PLATFORM and TENANT
JWTs, RLS-scoped.

**Scope in (as shipped).**
- `TenantUser` ORM model with all 17 columns + `TenantUserStatus`
  enum (INVITED/ACTIVE/SUSPENDED) + `ActorUserType` enum
  (PLATFORM/TENANT) for the three Pattern (b) audit-actor
  discriminator pairs. `tenant_id` NOT NULL, RLS via the existing
  `tenant_users_tenant_isolation` policy (D-29 unconditional
  OR-branch).
- `TenantUserRead` (= `TenantUserListItem` alias) +
  `TenantUserListResponse`. `auth0_sub` and all six
  Pattern (b) audit-actor columns hidden.
- `TenantUsersRepo` (stateless singleton): `list(...)` (with
  optional `tenant_id` filter for PLATFORM scoping) and
  `get_by_id(...)`. Sort-key validation reuses the shared
  `InvalidSortKeyError` from `repositories/_errors.py`.
- Router: `GET /api/v1/tenant-users`, `GET /api/v1/tenant-users/{user_id}`.
  Both PLATFORM and TENANT JWTs accepted (multi-user-type — no
  PLATFORM-only gate); RLS scopes visibility automatically.
  Optional `?tenant_id=X` for explicit PLATFORM scoping.
- `TenantUserNotFoundError` (404, `TENANT_USER_NOT_FOUND`); fires
  for both genuinely missing rows AND RLS-filtered rows
  (cross-tenant probes from a TENANT JWT) per D-17.
- 13 integration tests including **T9 cross-tenant detail
  returns 404 with TENANT_USER_NOT_FOUND** (LOAD-BEARING — proves
  RLS-as-404 works end-to-end through the API stack) and T10
  cross-tenant `?tenant_id=B` from TENANT-A returns empty.
- `docs/endpoints/tenant-users.md` (8-section format).
- **Lightweight TenantUser stub swap.** `models/_lightweight_stubs.py`
  loses its TenantUser stub (the full ORM model replaces it).
  `repositories/tenants.py` imports updated. Step 3.3's L9 test
  is the load-bearing regression check (22/22 pre and post).
  The Store stub stays until Step 4.5 ships.
- **Shared sort-key error classes.** `InvalidSortKeyError`
  (ValueError) promoted to `repositories/_errors.py`;
  `InvalidSortKeyClientError` (ClientError) promoted to
  `errors.py`. Step 5.1 imports updated; future Repos with a
  sort param reuse these classes rather than duplicating per-Repo.

**Scope out (as shipped).**
- Aggregates (no role count, etc.). Add later if frontend asks.
- Stats endpoint.
- Write endpoints (post-v0 per FN-AB-12).
- Auth0 webhook / sync logic.
- Per-role permission visibility (RBAC, Step 6.1).

**Acceptance criteria (met).**
- Two endpoints live; 138 pytest passes (was 125; +13);
  T9 cross-tenant 404 explicitly green; mypy strict clean
  across `src/admin_backend` (41 source files); check_setup
  35/35; smoke test 74/74 PASS post-truncate; alembic head
  unchanged at `0644a4186e48` (no migration).
- `docs/endpoints/tenant-users.md` follows tenants.md's
  8-section structure; OpenAPI spec at
  `docs/endpoints/openapi.json` lists both new endpoints
  with rich descriptions.
- Lightweight TenantUser stub removed; tenants Repo's tests
  still pass (22/22).

**Rough effort.** 90 min (slightly over 60-min estimate due to
shared-error-class promotion + lightweight-stub swap).

---

## Step 5.3 — Org-tree read surface (lazy-load with smart defaults)

**Status.** DONE
**Owner.** CLAUDE_CODE

**Note on scope narrowing.** Original entry called for four endpoints
(org-nodes flat list, detail, descendants, full org-tree from JWT).
Frontend contract review and design conversation 2026-05-04 narrowed
scope to two endpoints: E2 (org-tree with smart defaults) and E3
(node children for lazy expansion). The originally-planned
`num_nodes` augmentation on `/api/v1/tenants` was also dropped from
this step (parked post-v0; D-31 means it can be added later without
breaking).

**Goal.** Read surface for the Organization Tree page (Frontend
spec 7.3) with scaling for large tenants (3000+ nodes per tenant
supported via lazy-load).

**Scope in (as shipped).**
- `OrgNode` ORM model (full, not lightweight).
- Schemas: `OrgNodeTreeItem` (recursive, with `has_children`,
  `child_count`, `loaded_children`), `OrgTreeStats`,
  `OrgTreeResponse`, `OrgNodeChildrenResponse`.
- `OrgNodesRepo`: `count_active_by_tenant`,
  `list_active_with_child_counts` (full or depth-limited),
  `list_children_paginated`, `node_exists`.
- E2: `GET /api/v1/tenants/{tenant_id}/org-tree` with smart-default
  (full tree if ≤500 nodes; depth=4 otherwise; auto-reduce on
  payload cap with `truncated=true`).
- E3: `GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children`
  paginated lazy expansion.
- Pure-functional `_build_tree` helper (three-pass).
- 21 integration tests covering invariants I1-I13 + smart-default
  behavior + auth + 404 paths + mixed-depth + invalid UUID.
- `docs/endpoints/org-tree.md` (8-section).
- OpenAPI snapshot regenerated at `docs/endpoints/openapi.json`.

**Scope out.**
- `num_nodes` augmentation on `/api/v1/tenants` (parked; D-31 covers).
- `GET /api/v1/org-nodes` flat list / detail / descendants raw-SQL
  endpoint (lazy via E3 covers all UI use cases).
- INACTIVE / ARCHIVED filters.
- Tree mutations (Step 5.4).

**Acceptance criteria (met).**
- 138 prior pytest passes plus 21 new tests, all green (159 total).
- mypy strict clean (45 source files).
- check_setup 35/35.
- Smoke test unchanged.
- Alembic head unchanged at `0644a4186e48`.
- T12 (E2 cross-tenant 404) and T18 (E3 cross-tenant 404)
  load-bearing tests explicitly green.
- T8 (smart-default lazy mode), T9 (payload cap auto-reduce),
  T20 (mixed-depth subtree) explicitly green.
- `docs/endpoints/org-tree.md` follows 8-section format with both
  endpoints.

**DP decisions.**
- DP-1 (SQL strategy): Approach A — split count + LEFT JOIN with CTE.
- DP-2 (count method): Separate `count_active_by_tenant`.
- DP-3 (`node_exists`): Separate method, called from E3 router.
- DP-4 (E2 retry): Bounded loop, max 2 reductions.
- DP-5 (`_build_tree`): Three-pass for clarity.

**Coordination.**
- Frontend integrates against deployed dev within 24 hours.

**Known follow-ups.**
- **Step 5.3.1**: drawer endpoint when mockup is locked (deferred).
- **Step 5.4**: tree mutations + sort_order + status cascade.

---

## Step 6.1 — RBAC read endpoints (Roles + Permissions + Permission Matrix)

**Status.** DONE
**Owner.** CLAUDE_CODE

**Note on scope narrowing.** Original entry called for 5 endpoints
including a list and single-fetch of `user_role_assignments`. A
frontend-locked design review (2026-05-04) narrowed scope to
4 endpoints — the assignments-side is captured as forward notes (see
"Known follow-ups (RBAC)" sub-section below).

**Goal.** Roles & Permissions page (Frontend spec 7.5) becomes
data-driven. Both the Role catalog tab and the Permission matrix tab
render from these endpoints.

**Scope in (as shipped, 2026-05-05).**
- 2 migrations: DDL enum cleanup (narrows `module_enum` to 4 values
  by dropping ROOS/GOAL_CONSOLE; narrows `permission_scope_enum` to 3
  values by dropping REGION; deletes legacy seed rows; forward-only
  via NotImplementedError on downgrade per the project's convention
  for irreversible structural cleanups) + lookups seed (25 rows for
  the four enum display-label categories: 4 module + 12 resource +
  6 permission_action + 3 permission_scope).
- 3 ORM models: `Role`, `Permission`, `RolePermission`. `UserRoleAssignment`
  deferred per FN: E4/E5; a lightweight stub lands in
  `models/_lightweight_stubs.py` (mirrors Step 3.3's TenantUser stub
  pattern; carries id/role_id/tenant_id/status/platform_user_id/
  tenant_user_id only — the columns the user_count subquery needs).
- Schemas covering E1's pre-grouped response, E2's flat list, E3's
  parent-echo, E6's render-ready matrix.
- 3 Repos: `RolesRepo` (list_grouped + get_by_id + list_permissions_for_role),
  `PermissionsRepo` (list), `PermissionMatrixRepo` (get_matrix with
  display labels resolved via four LEFT JOINs against `lookups`).
- 1 router file (`routers/v1/rbac.py`) with 4 endpoints across 3
  APIRouter prefixes (`/roles`, `/permissions`, `/permission-matrix`).
- App-layer audience filter pattern: TENANT JWTs see only
  `audience='TENANT'` rows on E1 (the platform_roles block returns
  empty), E3 (cross-audience id surfaces as 404 ROLE_NOT_FOUND), and
  E6 (only TENANT-audience role columns appear, cells[] arrays
  correspondingly shorter). Distinct from RLS — codified as the new
  "Audience filtering for non-RLS tables" convention note in
  CLAUDE.md.
- 1 new error class: `RoleNotFoundError` (404). `InvalidSortKeyClientError`
  reused from Step 5.2.
- 3 conftest factories (raw-SQL-INSERT pattern): `make_role`,
  `make_permission`, `make_role_permission`.
- 23 integration tests; 5 load-bearing (R2 audience block;
  R4 .correlate scoping; RP3 audience-gated 404; M2 matrix alignment;
  M3 tenant filter on matrix).
- Excel seed update: legacy permission row
  (`PRICING_OS.MARKDOWNS.APPROVE.REGION`, `_key=p4`) removed from the
  permissions sheet AND the 4 role_permissions rows referencing it
  removed from the role_permissions sheet. As a side effect of the
  Excel cleanup, the `=TRUE()` / `=FALSE()` formulas in the
  is_system column on the roles sheet were converted to literal
  Python booleans (openpyxl can't compute formulas, so a save would
  otherwise have wiped the cached values; this is a one-time cleanup
  that makes future Excel edits durable). One stray `=SUBTOTAL`
  formula on role_permissions also cleared.
- `docs/endpoints/rbac.md` (8-section × 4 endpoints in one file).

**Scope out.**
- Permission resolution endpoint (post-v0).
- Write endpoints (FN-AB-12).
- AI-RBAC-01 through AI-RBAC-06 enforcement (write-time concerns).
- RBAC-driven authorisation in handlers (post-v0).
- Custom-role creation flow (FN-AB-06).

**Acceptance criteria (met).**
- 4 endpoints live; pytest 159 -> 182 (159 prior + 23 new RBAC);
  mypy strict clean on 54 source files; check_setup 35/35; smoke
  test unchanged at 74 PASS (no new RLS surface).
- Both migrations applied (90cd038ae618 cleanup, 22ccfb193cff
  lookups). Lookups round-trip clean. DDL cleanup is forward-only
  by design (irreversible row deletion); documented in the migration
  body.
- Cloud SQL dev migration scheduled post-merge (HUMAN-coordinated;
  not blocking step closure).
- All 5 load-bearing tests explicitly green.
- Per-resource regression checkpoint: each previously-shipped
  router test file PASS at exactly its pre-step count
  (`test_tenants_router.py` 22, `test_platform_users_router.py` 10,
  `test_tenant_users_router.py` 13, `test_org_tree_router.py` 21,
  `test_lookups_router.py` 4). No drop in any file.
- `docs/endpoints/rbac.md` follows `tenant-users.md`'s 8-section
  structure across all 4 endpoints; OpenAPI spec at
  `docs/endpoints/openapi.json` regenerated with all 4 new operations.
- Audit-actor columns hidden from response shapes (verified by
  H1/H2 tests).

**Known follow-ups (RBAC).**

The following were considered for Step 6.1 and deliberately deferred.
Each carries its own landing trigger; do not implement until the
trigger fires.

- **A1 / A2: User-resource augmentation.** **RESOLVED at Step 6.8.3 (2026-05-09).**
  Inline `roles[]` array landed on GET /api/v1/tenant-users and
  GET /api/v1/platform-users (list + detail; one Pydantic class
  serves both via `*ListItem = *Read` aliasing). Each item carries
  the 8 locked fields: `assignment_id`, `role_id`, `role_name`,
  `role_code`, `status`, `granted_at`, `org_node_id`, `org_node_name`
  (org_node fields always null for platform users; uniform wire
  shape). All assignments returned regardless of status (ACTIVE +
  INACTIVE both ship). Query posture: jsonb_agg correlated subquery
  mirroring `repositories/tenants.py:list_with_aggregates` exactly.
  The `?role_id=X` / `?org_node_id=X` query parameters originally
  proposed for these endpoints are DEFERRED — frontend can scope
  client-side; the new `/role-assignments` endpoint covers
  cross-resource queries directly.

- **E4: GET /api/v1/role-assignments (list).** **RESOLVED at Step
  6.8.3 (2026-05-09).** URL renamed from `/user-role-assignments` to
  `/role-assignments` (matches plural-resource convention used by
  `/tenants`, `/permissions`, etc.). Multi-user-type with grouped
  envelope `{platform_assignments: {items, pagination},
  tenant_assignments: {items, pagination}}`. Filters: `role_id`,
  `platform_user_id`, `tenant_user_id`, `tenant_id`, `org_node_id`,
  `status`. Sort: `granted_at_asc` / `granted_at_desc`. **Audience
  routing is security-load-bearing** (locked decision 12 of Step
  6.8.3): TENANT JWTs MUST NOT execute the platform-side query
  because `platform_user_role_assignments` has no RLS. Filter-shape
  narrowing: type-specific filters short-circuit the OTHER block
  (e.g., `?platform_user_id=X` skips the tenant-side query).

- **E5: GET /api/v1/role-assignments/{id} (single-fetch).**
  **Retained as forward note.** URL likewise renamed from the
  `/user-role-assignments` proposal. RLS-as-404 for cross-tenant
  probes per D-17. Lands when first of: Step 6.2 audit-log drawer
  needs a live-state panel (vs snapshot-only); user-detail drawer
  adds "click assignment chip → expand" lifecycle panel.

  The split ORM models (`PlatformUserRoleAssignment`,
  `TenantUserRoleAssignment`) and `RoleAssignmentsRepo` shipped at
  Step 6.8.2; consumed by Step 6.8.3. The `models/_lightweight_stubs.py`
  `UserRoleAssignment` stub was already retired at Step 6.8.2 (only
  `Store` stub remains, pending Step 4.5).

- **MODULES-EXT: Module enum extension for ROOS and GOAL_CONSOLE.**
  **RESOLVED at Step 6.6 (2026-05-06) via Path B (unification),
  superseding the additive Path A originally proposed here.** The
  Step 6.6 migration (`cec8fae734e0`) re-pointed `permissions.module`
  from the narrow `module_enum` to the wider `module_code_enum`
  (the same enum that already backed `tenant_module_access.module`
  and includes ROOS + GOAL_CONSOLE among its 6 values), then dropped
  `module_enum` entirely and consolidated the two `lookups` list_names.
  Permission catalogue can now hold tuples targeting ROOS or
  GOAL_CONSOLE without further enum-vocabulary work; the `permissions`
  table is ready for the RBAC catalogue to expand into those modules
  when product is ready. Original additive proposal (`ALTER TYPE
  module_enum ADD VALUE`) is no longer applicable — the enum it
  referenced no longer exists.

- **RESOURCES-EXT: Resource enum extension for MODULE_ACCESS,
  GUARDRAILS, APPROVALS.** Frontend spec 7.6 / 7.7 describes pages
  for these but their RBAC gating is post-v0. v0 ships without
  permissions targeting these resources; the matrix has narrower
  rows, matching v0's narrower enforcement reality. Lands when
  Module Access / Guardrails / Approvals page gating ships. Each
  is one ALTER TYPE ADD VALUE migration plus one lookup row.

**Coordination.**
- Frontend integrates dev within 24 hours.
- Cloud SQL dev migration run post-merge (HUMAN).

---

## Step 6.2 — Audit log DDL + migration + read endpoint

**Status.** TODO
**Owner.** CLAUDE_CODE

**Goal.** Audit log table exists, readable via API.

**Scope in.**
- Write `db/raw_ddl/Ithina_postgres_SQL_DDL_audit_logs_v1.sql`. Columns: id, created_at, actor_user_id, actor_user_type, actor_name_snapshot, actor_role_label_snapshot, tenant_id (nullable), tenant_name_snapshot, action_code, action_label, resource_type, resource_id, resource_label, scope, result (SUCCESS/PENDING/DENIED), ip (INET), user_agent (TEXT), trace_id (UUID), before_jsonb, after_jsonb. Indexes for screen filters. RLS with FORCE.
- Wrap as Alembic migration. Apply locally and to cloud dev.
- Implement `AuditLog` model, `AuditLogRead` schema, `AuditLogsRepo`.
- Implement `GET /v1/audit-logs` with query params: `search`, `result`, `from_date`, `to_date`, `tenant_id` (staff-only), `limit`, `offset`.
- Tests: cross-tenant isolation, filter behaviour, pagination.
- Re-deploy.

**Scope out.**
- Audit log writes from app (D-16: external population).
- Audit log triggers / external writers.

**Acceptance criteria.**
- Endpoint returns expected rows for each filter combination.
- Tenant users see only their tenant's rows.
- `docs/endpoints/audit-logs.md` produced following `docs/endpoints/tenants.md` shape.
- mypy strict clean.

**Coordination.**
- Send updated OpenAPI spec to frontend.
- Frontend integrates on dev within 24 hours.

**Rough effort.** 120-150 min.

---

## Step 6.3 — Seeds: bootstrap, lookups, RBAC static

**Status.** DONE — REDISTRIBUTED. Work folded into:
- Step 3.4.5 — tenant_module_access seed
- Step 3.5 — dev-data seed loader
- Step 3.6 — lookups seed extension
- Step 6.1 — lookups for permissions
- Step 6.7 — module_code reorder
- Step 6.8.2.1 — SUPER_ADMIN permission grants

Development bootstrap user is seeded via the dev-data Excel loader
(non-real user state, sufficient for development). Production
bootstrap user seed for the first real beta tenant is a Stage 6
cutover task, not covered by this step.

**Owner.** CLAUDE_CODE

**Goal.** Foundational seed data for all environments. Files committed to git.

**Scope in.**
- Write `db/seeds/00_bootstrap.sql`:
  - Create one `platform_users` row (Ithina System / system actor) with self-referencing audit FKs.
  - Use a deterministic UUID (e.g., `00000000-0000-0000-0000-000000000001`).
- Write `db/seeds/01_lookups.sql`:
  - All categories: `tier`, `industry`, `region`, `tenant_status`, `store_status`, `tenant_user_status`, `platform_user_status`, `audit_result`, `audit_scope`, `org_node_type`.
  - Each row: list_name, code, display_name, display_order, is_active, audit columns pointing at bootstrap user.
- Write `db/seeds/02_rbac_static.sql`:
  - ~5 platform-defined roles (Super Admin, Support Admin, Owner, Manager, Store Manager).
  - ~10-15 permissions (Module + Resource + Action + Scope tuples).
  - ~5-10 role_permissions linking roles to permissions.
  - 1-3 user_role_assignments for demo (minimal for v0; full RBAC enforcement post-v0).
- Write `scripts/apply_seeds.sh` that runs all seed files in order via psql.
- Test: apply seeds locally, hit endpoints (`GET /v1/lookups/{category}`, `GET /v1/roles`, etc.), verify expected data.
- Sample audit_logs rows can be added separately when audit log endpoint is exercised.

**Scope out.**
- Customer-specific data (Step 7.3.1, 7.3.2).
- Excel-to-SQL conversion (Step 7.3.1).

**Acceptance criteria.**
- `./scripts/apply_seeds.sh` runs cleanly after `alembic upgrade head`.
- Endpoints return seeded data.
- Re-running seeds is idempotent (use `ON CONFLICT DO NOTHING`).

**Coordination.**
- Frontend confirms lookups endpoint returns expected categories.

**Rough effort.** 90-120 min.

---

## Step 6.4 — Tenants list aggregate sort keys

**Status.** DONE
**Owner.** CLAUDE_CODE

**Note on scope.** The original Step 6.4 prompt described this step as
"extending an existing sort vocabulary" with 4 new aggregate keys. In
reality the `/api/v1/tenants` endpoint had no `sort` query parameter at
all and the Repo's `list_with_aggregates` hardcoded `Tenant.name.asc()`
— sort infrastructure that was sketched for Step 3.3 never landed. So
Step 6.4 lands the foundational sort surface (param + SORT_MAP + 6
column-based keys) AND the 4 aggregate-based keys that the dashboard
panel needs, all in one bundle. The forward-looking purpose
(unblocking Step 6.5) is unchanged.

**Goal.** `/api/v1/tenants` accepts a `sort` query parameter with 10
keys. The Step 6.5 dashboard's Top Tenants panel can call
`?sort=num_users_active_desc&limit=5` and get a valid 200 response.

**Scope in (as shipped, 2026-05-06).**
- New `sort` query parameter on `GET /api/v1/tenants`. 10 accepted
  keys: 6 column-based (`created_at_asc`, `created_at_desc`,
  `name_asc`, `name_desc`, `tier_asc`, `tier_desc`) + 4 aggregate-
  based (`num_users_active_asc`, `num_users_active_desc`,
  `num_stores_asc`, `num_stores_desc`).
- New module-level `_BASE_TENANTS_SORT_MAP` (column keys),
  `_AGGREGATE_TENANTS_SORT_KEYS` (aggregate keys), and public
  `TENANTS_SORT_KEYS: frozenset[str]` (full set, used for
  validation) in `repositories/tenants.py`. Aggregate sort clauses
  are built per-call inside `list_with_aggregates` because the
  underlying scalar subqueries are constructed there.
- Stable secondary sort by `Tenant.id ASC` so identical primary-sort
  values page deterministically. Critical for `num_*` keys where
  ties are common (e.g., several tenants with 0 active users).
- Default sort `created_at_desc` (mirrors PlatformUsersRepo /
  TenantUsersRepo precedent). **Behaviour change:** pre-Step-6.4
  callers who didn't pass `sort` got the hardcoded `name ASC`;
  post-Step-6.4 they get `created_at_desc`. Documented in the doc
  + docstring + commit message; acceptable because no v0 frontend
  consumer relied on the previous default (Step 3.3's `name ASC`
  was implicit, not contractual).
- Reuses the Step 5.2 shared error classes: `InvalidSortKeyError`
  (Repo) raises on unknown keys; the router catches and re-raises
  as `InvalidSortKeyClientError` (400, `INVALID_SORT_KEY`).
- 12 new integration tests in `tests/integration/test_tenants_router.py`:
  L4a-L4f for the 6 column keys, L4g for invalid-sort-400, L5a-L5d
  for the 4 aggregate keys, L5e for RLS-on-aggregate-sort. **L5b
  load-bearing** — `?sort=num_users_active_desc&limit=5` is the
  exact query shape Step 6.5's Top Tenants dashboard panel issues.
- Existing L5 (pagination-with-search) updated to pin
  `sort=name_asc` so its alphabetical assertion holds independent
  of the default change.
- `scripts/smoke_curl.sh` extended: 1 new assertion for the
  dashboard panel's call shape; "WHAT'S CHECKED" header count
  15 → 16.
- `docs/endpoints/tenants.md` query-params table gains the `sort`
  row; default-sort behaviour note rewritten; 2 example calls
  added.

**Scope out.**
- No new endpoints. No new schemas. No new Repos. No new error
  classes (reuses Step 5.2's shared classes).
- No migrations. No DDL changes. No seed Excel changes.
- No `num_org_nodes_*` sort keys (org-tree aggregation isn't on
  `/tenants`).
- No sort vocabulary extensions on other resources
  (`platform-users`, `tenant-users`, `roles`).
- No caching (sub-millisecond at v0 fleet scale).

**Acceptance criteria (met).**
- 10 sort keys live; backwards compat preserved (no caller breakage
  beyond the documented default-sort change).
- pytest 182 → 194 (182 prior + 12 new on
  `test_tenants_router.py`); mypy strict clean on 54 source files;
  check_setup 35/35; pytest smoke (`scripts/smoke_test.py`)
  unchanged at 74 PASS post-truncate.
- Per-resource regression checkpoint: tenants 22 → 34 (the +12
  count is the step's deliverable); platform_users 10, tenant_users
  13, org_tree 21, lookups 4, rbac 23 — all unchanged.
- L5b load-bearing test explicitly green; L9 (correlated subquery
  scoping, the underlying subquery semantics this step extends)
  unchanged.
- `scripts/smoke_curl.sh` updated with 1 new assertion; `bash
  scripts/smoke_curl.sh <base>` returns all PASS.
- The other workflow scripts (`scripts/deploy-cloud-run.sh`,
  `scripts/env.sh`, `scripts/jwt/generate_7d.sh`) — unchanged.
- Alembic head unchanged at `22ccfb193cff` (no migration).
- `docs/endpoints/tenants.md` lists all 10 sort keys; OpenAPI spec
  reflects the updated `sort` parameter description.

**Precondition for.** Step 6.5 (Dashboard stats endpoints). The
dashboard's Top Tenants panel calls
`/tenants?sort=num_users_active_desc&limit=5` against this endpoint;
without 6.4 that call would return 400 INVALID_SORT_KEY.

**Coordination.**
- Frontend integrates dev within 24 hours.
- Cloud SQL dev migration N/A (no migration in this step).

**Rough effort.** 30-45 min.

---

## Step 6.5 — Dashboard stats endpoints (fleet-stats + governance-stats)

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Two dedicated dashboard endpoints back the Platform
Dashboard's KPI grid (Frontend spec 7.1). Resolve FN-AB-21 by
confirming Option 2 (multi-user-type, document scope-dependent
semantics) as the platform-wide default for stats endpoints.

**Scope in (as shipped, 2026-05-06).**
- 2 endpoints under ``/api/v1/dashboard/``:
  - ``GET /fleet-stats``       — KPI cards 1-4 (active tenants,
    platform users, stores, aggregated MRR).
  - ``GET /governance-stats``  — KPI cards 5-8 (pending approvals,
    guardrails fired, custom roles, modules deployed). 3 of 4 cards
    stubbed in v0.
- New ``DashboardRepo`` at ``repositories/dashboard.py``. Deliberate
  departure from one-Repo-per-resource — the dashboard is a UI-shaped
  query bundle, not a CRUD resource. Two methods: ``fleet_stats``
  (single CTE producing all 4 cards' aggregates) and
  ``governance_stats`` (single small query for the only real card).
  Decimal-on-the-way-out cast for ``mrr_sum`` to handle driver
  variance on ``SUM(NUMERIC)``.
- New schemas at ``schemas/dashboard.py``: ``DeltaBlock`` + 8
  card models + 2 response models. ``ConfigDict(extra="forbid")`` on
  every model — guards against accidental shape drift.
- New router at ``routers/v1/dashboard.py``. Sub_text helpers (pure
  functions, scope-aware via ``auth.user_type``). Wired into
  ``main.py``.
- Card-shaped responses (deliberate D-30 exception). Both endpoints'
  ``description`` strings call out the exception so OpenAPI
  consumers see it.
- ``available`` + ``unavailable_reason`` stub pattern. v0 vocabulary:
  ``approvals_table_not_built``, ``audit_logs_or_guardrails_not_wired``,
  ``custom_role_creation_not_shipped``. Append-only contract per D-31:
  when a stub flips to real, only ``available``, ``value``, and
  ``unavailable_reason`` change; field set and types stay identical.
- Multi-user-type with RLS-driven persona projection. Same SQL runs
  for both PLATFORM and TENANT JWTs; visible row sets differ by
  session GUC.
- Backend-formatted, scope-aware ``sub_text`` strings. No frontend
  reassembly.
- Explicit ``f"{x:.2f}"`` formatting for ``mrr_aggregated.value`` —
  documented in the schema docstring as different posture from
  ``schemas/tenant.py``'s per-row ``field_serializer`` returning
  ``str(v)``. Different contracts, different posture.
- 16 integration tests at ``tests/integration/test_dashboard_router.py``.
  5 load-bearing: **S2** (TENANT RLS scoping), **S5** (sub_text
  scope-awareness), **S7** (MRR delta permanently stubbed),
  **O2** (modules_deployed real + RLS-scoped while others stubbed),
  **O5** (modules_deployed sub_text scope-awareness). Plus an
  ``X1`` Pydantic-extra-forbid drift guard.
- ``scripts/smoke_curl.sh``: +2 assertions for the new endpoints.
  WHAT'S CHECKED count 16 → 18.
- ``docs/endpoints/dashboard.md`` (8-section × 2 endpoints in one
  file).

**Scope expansion at design-review time (2026-05-06).** The original
prompt's locked sub_text rules covered only TRIAL and SUSPENDED for
``active_tenants.sub_text``. The actual ``TenantStatus`` enum has
**five** values (ONBOARDING, TRIAL, ACTIVE, SUSPENDED, TERMINATED),
not the four the prompt assumed. Stop-and-ask trigger #5 fired;
resolution: extend the sub_text vocabulary to cover ONBOARDING as a
distinct lifecycle segment in lifecycle order
(onboarding → trial → suspended). The CTE adds an explicit
``onboarding`` filter; ``total`` continues to use ``status !=
'TERMINATED'`` (already correct — the prompt's narrative was the
bug, not the SQL). Same shape as Step 6.4's premise-mismatch handling.

**Resolves FN-AB-21.** The original FN-AB-21 had three resolution
options for ``/api/v1/tenants/stats`` posture. Step 6.5 confirms
**Option 2** (multi-user-type, document scope-dependent semantics)
as the platform-wide default for stats endpoints. The existing
``/tenants/stats`` is unchanged; resolution is documentation policy
only. Both new dashboard endpoints follow the same multi-user-type
+ RLS-driven scoping pattern.

**Scope out.**
- Modifications to ``/api/v1/tenants/stats`` — stays at Step 3.3
  contract.
- Caching — at v0 scale every CTE is sub-millisecond; at fleet
  scale of 100+ tenants consider a 60s cache.
- Configurable delta windows — hardcoded 7d/30d/monthly in v0.
- Real values for stubbed cards — each is its own forward note.
- Tenant Owner dashboard frontend — reuses these endpoints; the
  frontend work is tracked separately.
- Ithina commercial health endpoint — deliberately deferred.

**Acceptance criteria (met).**
- 2 endpoints live and routed under ``/api/v1/dashboard/``.
- For seed-loaded data:
  - fleet-stats (PLATFORM): 4 cards real, MRR ``"308100.00"``,
    active 5/7, mrr_aggregated.delta ``available: false``.
  - fleet-stats (TENANT-Buc-ee's): 1/1 active, "in your
    organization" sub_text, MRR ``"48500.00"``, 3 stores in 1
    country.
  - governance-stats (PLATFORM): 3 stub cards with locked
    unavailable_reason; modules_deployed value 27 across 7 tenants.
  - governance-stats (TENANT-Buc-ee's): same 3 stubs;
    modules_deployed value 6, "enabled for your organization".
- 5 load-bearing tests explicitly green.
- pytest 194 → 210 (+16); mypy strict clean on 57 source files;
  check_setup 35/35; smoke test still 74/74 post-truncate.
- Per-resource regression checkpoint: tenants 34, platform_users
  10, tenant_users 13, org_tree 21, lookups 4, rbac 23 — all
  unchanged.
- ``scripts/smoke_curl.sh`` updated: 18/18 PASS (was 16).
- The other workflow scripts (``scripts/deploy-cloud-run.sh``,
  ``scripts/env.sh``, ``scripts/jwt/generate_7d.sh``) — unchanged.
- Alembic head unchanged at ``22ccfb193cff`` (no migration).
- ``docs/endpoints/dashboard.md`` covers both endpoints in
  8-section format; OpenAPI spec at
  ``docs/endpoints/openapi.json`` shows both new operations.

**Known follow-ups (Dashboard).**

The following were considered for Step 6.5 and deliberately deferred.
Each carries its own landing trigger.

- **PENDING-APPROVALS-REAL** — flip
  ``pending_approvals.available`` to ``true``. Lands when the
  ``approvals`` table ships (no current build-plan step). Replace
  the stub with a ``COUNT(*) FILTER (WHERE status = 'PENDING')``
  query against ``approvals``.

- **GUARDRAILS-FIRED-REAL** — flip
  ``guardrails_fired_24h.available`` to ``true``. Lands when audit
  logs ship at Step 6.2 AND guardrail-fire events are emitted into
  ``audit_logs``. Then queries ``audit_logs`` filtered to
  ``action = 'GUARDRAIL_FIRED'`` and ``created_at >= now() -
  interval '24 hours'``; ``escalations`` filters further on the
  fire's ``severity`` or analogue.

- **CUSTOM-ROLES-REAL** — flip ``custom_roles.available`` to
  ``true``. Lands when the create-custom-role write surface ships
  (no current build-plan step). Step 6.1 already shipped read
  endpoints, so the ``roles`` table is reachable. The gap is that
  no v0 path lets users create custom roles, so the count is
  structurally pinned at zero. When the write surface ships,
  replace the stub with ``COUNT(*) FILTER (WHERE is_system =
  false)`` paired with ``COUNT(*)`` against ``roles``.

- **MRR-DELTA-REAL** — flip ``mrr_aggregated.delta.available`` to
  ``true``. Requires a per-period MRR snapshot table (no current
  plan). The shape is preserved (``window: "monthly"``) so when
  this lands the response shape doesn't change.

- **TENANT-OWNER-DASHBOARD** — when a Tenant Owner dashboard ships,
  it reuses these endpoints unchanged. RLS handles persona
  projection. Frontend hides degenerate cards (e.g., ``Active
  tenants 1/1``). No backend work required for the dashboard
  endpoints themselves; lands as a frontend-only step.

- **ITHINA-COMMERCIAL-HEALTH** — future third-concern endpoint
  (``/dashboard/billing-stats``) covering Ithina's own MRR/ARR/
  churn/billing posture. Out of v0 scope; deliberately deferred.

**Coordination.**
- Frontend integrates dev within 24 hours.
- Cloud SQL dev migration N/A (no migration).

**Rough effort.** 2-3 hours.

---

## Step 6.5.1 — Dashboard raw-SQL schema qualification (bugfix + regression guards)

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Fix the dashboard fleet-stats and governance-stats endpoints
returning 500 on Cloud SQL post-Step-6.5 deploy. Lock in a regression
contract that any raw `text()` SQL must schema-qualify table references.

**Symptom.** Cloud Run smoke at v0.1.7 (2026-05-06):
- `/api/v1/dashboard/fleet-stats`      → 500 `relation "tenants" does not exist`
- `/api/v1/dashboard/governance-stats` → 500 `relation "tenant_module_access" does not exist`

Both endpoints worked on local Postgres because the role-default
`search_path` includes `core`, masking the bug. On Cloud SQL the
connect-time `SET search_path` hook (`db/engine.py:72-76`) does not
always mask reliably (connection cycling, pool recycle, and async
event-listener ordering are all plausible reasons; not diagnosed
because the fix removes the dependency).

**Scope in.**
- `src/admin_backend/repositories/dashboard.py`: schema-qualify both
  `text()` queries via `get_settings().db_schema` per-call
  interpolation. Match `repositories/permission_matrix.py:101-128`'s
  pattern exactly. Module-level `_FLEET_STATS_SQL` and
  `_GOVERNANCE_STATS_SQL` constants dropped; SQL builds inside the
  methods.
- `tests/integration/test_dashboard_router.py`: add **X2** regression
  test (clobber search_path, call both Repo methods, assert success).
- `tests/integration/test_rbac_router.py`: add **M6** regression test
  same shape, calling `PermissionMatrixRepo.get_matrix`. The Repo is
  already correct; M6 forward-guards against a future regression.
- `CLAUDE.md`: new convention note "Note on raw `text()` SQL — schema
  qualification is mandatory" with rule + reason + precedent + anti-
  pattern. References Step 6.1's permission_matrix as precedent and
  Step 6.5's dashboard pre-fix as anti-pattern.

**Scope out.**
- No alembic migration. No DDL changes. No seed data changes. No
  endpoint contract changes.
- No diagnosis of why Cloud SQL's session loses `search_path`. The
  fix removes the dependency; investigating connection cycling vs
  cold start vs event-listener ordering would be interesting but
  doesn't change the fix.
- `repositories/tenants.py` `_modules_subq()` builds its SQL via the
  ORM; renders schema-qualified automatically; no change needed.

**Acceptance criteria.**
- pytest 210 → 212 (+2 new regression tests).
- mypy strict clean on 57 source files. check_setup 35/35.
- smoke_curl against the redeployed Cloud Run image returns 200 on
  both `/api/v1/dashboard/*` endpoints.
- Per-resource regression checkpoint: dashboard 16 → 17, rbac 23 →
  24, all other files unchanged.
- Alembic head unchanged at `22ccfb193cff`.

**Coordination.** Re-deploy via `./scripts/deploy-cloud-run.sh`
(no flags); the script auto-bumps the patch version from the highest
existing semver tag in Artifact Registry (e.g., `v0.1.7 → v0.1.8`)
and bakes `SERVICE_VERSION` into the image. The `--migrate` flag is
opt-in and runs the `admin-backend-alembic` Cloud Run Job; not needed
this step (no migration).

**Verification harness override (build-history note).** This step's
7-leg verification was run with `DATABASE_URL` forced to
`postgresql+psycopg://...@127.0.0.1:5432/...` (IPv4 direct) for that
run only. Local `localhost` DNS resolution exhibited a 10-second
timeout per new connection (psql baseline 10.07s on `localhost`,
0.065s on `127.0.0.1`) that would have made the 212-test suite run
~35 minutes. The override bypassed the DNS layer and brought the
suite back to ~31s. Unrelated to Step 6.5.1's substance; committed
`.env` not modified. DNS hygiene flagged as a separate item — out
of scope for this build.

**Rough effort.** 30 min.

---

## Step 6.6 — Module enum unification (Path B)

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Retire `module_enum`; re-point `permissions.module` at
`module_code_enum`; consolidate the two `lookups.list_name` entries
into a single canonical reference. Closes the **MODULES-EXT** forward
note from Step 6.1's "Known follow-ups (RBAC)" via Path B
(unification), superseding the additive Path A originally proposed.

**Why now.** The upcoming Module Access read endpoint (Step 6.7)
backs a UI that displays all 6 modules. Having a unified vocabulary
before 6.7 ships is cleaner than backfilling later. Independently:
the two-enum fork was a Step 3.4.5 oversight (not design intent), and
the Step 6.1 enum-narrowing exposed the drift risk by narrowing only
one of the two enums. Path B fixes the root cause; Path A would have
restored symmetry without fixing the duplication.

**Scope in (as shipped, 2026-05-06).**
- One forward-only Alembic migration (`cec8fae734e0`):
  - `ALTER TABLE permissions ALTER COLUMN module TYPE
    module_code_enum USING module::text::module_code_enum`
  - `DROP TYPE module_enum`
  - `DELETE FROM lookups WHERE list_name = 'module'` with defensive
    row-count assertion (raises if deleted set ≠ the 4 codes Step
    6.1 seeded — catches unexpected hand-edits between Step 6.1's
    seed and this migration's run).
- `downgrade()` raises NotImplementedError per the project's
  irreversible-cleanup convention (mirrors Step 6.1's `90cd038ae618`).
- Python: deleted `PermissionModule` enum class entirely;
  `Permission.module` now references `ModuleCode` from
  `models/tenant_module_access` (the surviving Python enum kept
  under its original name — see "Notes on deviations" below for the
  naming-decision rationale).
- Schema field types in `schemas/permission.py` (`PermissionRead.module`,
  `PermissionMatrixRow.module`) flipped from `PermissionModule` to
  `ModuleCode`. Same wire string values; only the type annotation
  changed.
- Repos: `permission_matrix.py`'s lookups JOIN flipped from
  `list_name='module'` to `'module_code'`; `permissions.py` gained
  a `LEFT JOIN` against `lookups` on the new list_name. Both Repos'
  `module_asc` ORDER BY changed from `Permission.module` enum
  ordinal to `coalesce(lookups.display_order, 999) ASC` per the
  locked sort-stability decision (the new enum's ordinals differ
  from the old enum's for the same overlapping values, so any sort
  by `Permission.module` would re-sequence rows post-migration;
  sorting by `display_order` decouples the contract from enum
  ordinal — robust against future enum vocabulary changes).
- Test/fixture updates: mechanical rename `PermissionModule` →
  `ModuleCode` in `test_rbac_router.py` imports;
  `_permission_sort_tuple` helper rewritten to use a hardcoded
  `_MODULE_DISPLAY_ORDER` map mirroring the Step 3.4.5 seed's
  display_order values for module (resource/action/scope still use
  enum ordinal — those weren't touched). conftest's `make_permission`
  factory's `CAST(:module AS module_enum)` → `CAST(:module AS
  module_code_enum)`.
- CLAUDE.md updates: Step 6.6 Completed bullet; schema state line
  updated (enum count 20 → 19); Step 6.1 cleanup-migration
  description amended to point at Step 6.6 as the resolution. No
  new D-XX entries; no new FN-AB entries.
- BUILD_PLAN updates: Step 6.6 entry; **MODULES-EXT** entry under
  Step 6.1's "Known follow-ups (RBAC)" marked **RESOLVED at Step
  6.6** with the resolution paragraph.

**Scope out.**
- No additive `ALTER TYPE module_enum ADD VALUE`. Path B replaces
  Path A entirely.
- No DDL file edits in `db/raw_ddl/` (per D-21, frozen as-shipped).
- No changes to public endpoint shapes. Wire format identical
  pre/post for per-row JSON.
- No new endpoints. Module Access read endpoint is Step 6.7.
- No label-resolution sweep across older endpoints. Per the team's
  locked policy, old endpoints stay bare-enum; new endpoints (6.7+)
  get server-side label resolution.
- No seed Excel changes. All 23 surviving permissions target values
  present in both enums.
- No rename of `module_code_enum`. The PG enum keeps its current name.
- No rename of `Permission.module` column. It stays named `module`.

**Acceptance criteria (met).**
- 1 new migration file (`cec8fae734e0`); `alembic upgrade head`
  succeeded; head advanced from `22ccfb193cff` to `cec8fae734e0`.
- `module_enum` PG type does NOT exist post-migration (verified via
  `pg_type`).
- `module_code_enum` exists with 6 values unchanged.
- `permissions.module` column type is `module_code_enum` (verified
  via `information_schema.columns`).
- `lookups` table: zero rows where `list_name='module'`; 6 rows
  where `list_name='module_code'`.
- `PermissionModule` Python enum class removed from the codebase.
  `grep -r "PermissionModule" src/ tests/` returns zero hits in
  application code (migrations / docstrings retain historical
  references, expected).
- 212 pytest passes (unchanged from Step 6.5.1 — the same tests run
  under the new schema; no test count delta).
- mypy strict clean on 57 source files.
- check_setup 35/35.
- pytest smoke 74/74 post-truncate.
- Per-resource regression checkpoint: tenants 34, platform_users
  10, tenant_users 13, org_tree 21, lookups 4, rbac 24, dashboard
  17 — all unchanged from Step 6.5.1.
- Manual curl: `/permissions`, `/permission-matrix`,
  `/tenants` modules[], `/dashboard/governance-stats` modules_deployed
  all return wire-format-identical responses pre/post step (per-row
  JSON unchanged; row ordering on `/permissions` and
  `/permission-matrix` reflects the new `lookups.display_order`
  basis — planned change per the locked decision).
- OpenAPI spec regenerated; `module` field's enum vocabulary grows
  from 4 to 6 values (informational broadening, not breaking).

**Notes on deviations from the prompt.**

- **Stop-and-ask trigger #1 — naming the unified Python enum:**
  resolved as **option (a) keep `ModuleCode`**. Three reasons logged:
  (1) the existing `schemas.Module` Pydantic class would shadow a
  renamed `models.Module`; avoiding the namespace conflict is worth
  the slightly less-neutral name. (2) `ModuleCode` is descriptively
  accurate — these values are wire-stable codes shared between two
  consumer columns. (3) The rename diff cost (test fixtures, seed
  loader, multiple integration tests calling `ModuleCode.ADMIN`,
  `ModuleCode.PRICING_OS`) outweighs the cosmetic gain.

**Coordination.** Re-deploy via `./scripts/deploy-cloud-run.sh
--migrate`. The `--migrate` flag is **required** this step — the new
migration `cec8fae734e0` must run on Cloud SQL before the deploy
completes (the live service code expects `permissions.module` to be
`module_code_enum`; without the migration, the cloud DB would still
have `module_enum` and the column-type mismatch would surface as
runtime errors on `/permissions` and `/permission-matrix`).

**Rough effort.** ~3 hours including the sort-stability Repo updates,
test content adjustments, and verification.

---

## Step 6.7 — Module Access read endpoints (Modules + Matrix)

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Two GET endpoints under `/api/v1/module-access/` backing the
Module Access governance console (Frontend spec — sidebar entry under
"ACCESS CONTROL"). First instance of the new label-handling convention
(sibling `<field>_label`, server-side resolution via `lookups`).
Continues the multi-user-type RLS pattern from Step 6.5 (FN-AB-21
resolution).

**Why now.** Step 6.6 just unified `module_enum` and `module_code_enum`
into one PG type. With unification done, Module Access is the cleanest
first consumer of the unified vocabulary, and the page is a real
frontend deliverable Amit's team needs to integrate against.

**Scope in (as shipped, 2026-05-06).**

- 2 endpoints under `/api/v1/module-access/`:
  - **E1** `GET /modules` — 6-card response with `module_code`,
    `module_label`, `enabled_count`, `total_active_trial_tenants`.
    Card ordering anchored on `lookups.display_order`.
  - **E2** `GET /matrix` — paginated tenant × module grid with
    `cells[]` synthesised via tenants × modules CROSS JOIN LEFT JOIN
    tenant_module_access. Sort/filter/q-search; standard
    `{items, pagination}` envelope.
- New `ModulesAccessRepo` at `repositories/modules_access.py`. Two
  methods, both raw `text()` SQL schema-qualified per the raw-SQL
  convention. E2 is a 3-stage query (page → cells → count) assembled
  in Python; mirrors Step 6.1's permission-matrix pattern.
- New schemas at `schemas/modules_access.py`: `ModuleCard`,
  `ModulesResponse`, `MatrixCell`, `MatrixRow`, `MatrixResponse`. All
  use `ConfigDict(extra="forbid")`. Re-exported from `schemas/__init__.py`.
- New router at `routers/v1/modules_access.py` with no
  `_require_platform_auth(...)` gate — both user types accepted, RLS
  does the work. Reuses Step 5.2's `InvalidSortKeyError` /
  `InvalidSortKeyClientError`.
- One additive forward-only Alembic migration `2fdc4bc9f4cb` (down_revision
  `cec8fae734e0`). UPDATEs `lookups` rows under `list_name='module_code'`
  to match the locked screenshot ordering (ROOS=1, GOAL_CONSOLE=2,
  PRICING_OS=3, PERISHABLES_ASSISTANT=4, PROMOTIONS_ASSISTANT=5,
  ADMIN=6); pre-step had GOAL_CONSOLE at 5. Idempotent INSERT for
  `tenant_tier` (4 rows) and `tenant_status` (5 rows) via
  `ON CONFLICT (list_name, code) DO NOTHING` — no-ops since Step 3.6
  already seeded them with matching content. `downgrade()` raises
  NotImplementedError per the project's irreversible-cleanup convention.
- 15 new integration tests in `tests/integration/test_modules_access_router.py`:
  M1-M5 (E1) + X1-X9 (E2) + A1 (auth). Five LOAD-BEARING (M2 TENANT
  aggregate collapse, M3 cross-endpoint position alignment, M4 label
  JOIN end-to-end, X1 TENANT 1-row, X2 cell synthesis under RLS).
- Test-side amendment: `_MODULE_DISPLAY_ORDER` dict in
  `tests/integration/test_rbac_router.py` updated to mirror the new
  live `lookups.display_order` for `module_code`. Necessary maintenance
  per the dict's own commitment-to-stay-in-sync comment; relative
  ordering for the 4 seed-relevant modules is preserved, so rbac sort
  assertions don't break.
- `scripts/smoke_curl.sh` extended with 2 new assertions
  (`module_access_modules`, `module_access_matrix`); WHAT'S CHECKED
  count 18 → 20. Other 3 workflow scripts unchanged.
- `docs/endpoints/module-access.md` (8-section × 2 endpoints).
- `docs/endpoints/openapi.json` regenerated.
- New "Note on label resolution" convention added to CLAUDE.md
  alongside existing convention notes (PG enum, batch-by-key, v0 auth
  model, seed Excel shape, sort-stability, raw text() SQL).
- CLAUDE.md schema-state line updated to record the `module_code`
  display_order re-sequencing.

**Scope out.**

- **MODULE-ACCESS-WRITE.** POST endpoint to enable/disable a module
  for a tenant. The cascade is **structural, not imperative**: the
  `has_permission()` JOIN on `tenant_module_access.status='ENABLED'`
  blocks access on the next request after a disable, with no
  imperative revocation pass; re-enable restores access
  automatically per D-24's identity-only JWT (permissions resolve
  per-request from DB tables). Landed at Step 6.15 (2026-05-16).
- **TIER-INDUSTRY-FILTER-EXTENSION.** `/matrix` filtering by industry,
  multi-tier filter. v0 ships single-value tier and status filters.
- **MODULE-CARD-TIME-SERIES.** "Enabled in 4/7, was 3/7 last month."
  Future.
- **PER-ROLE PERMISSION CASCADE PREVIEW.** Future.
- **Retroactive label resolution on older endpoints.** New convention
  applies forward only.
- **No DDL changes.** No seed Excel changes.

**Acceptance criteria (met).**

- 2 endpoints live and routed under `/api/v1/module-access/`. OpenAPI
  spec includes both with summary, description, query-param
  descriptions, and per-field types.
- `/modules` (PLATFORM): 6 cards in locked ordering (ROOS, GOAL_CONSOLE,
  PRICING_OS, PERISHABLES, PROMOTIONS, ADMIN); `enabled_count` and
  `total_active_trial_tenants` reflect ACTIVE+TRIAL only.
- `/modules` (TENANT-Buc-ee's): same shape; all values RLS-scoped.
  total_active_trial_tenants = 1, enabled_count ∈ {0, 1}.
- `/matrix` (PLATFORM): all non-terminated tenants, each with 6 cells
  in locked ordering, `tier_label` and `status_label` populated.
- `/matrix` (TENANT-Buc-ee's): exactly 1 row.
- 5 LOAD-BEARING tests (M2, M3, M4, X1, X2) green.
- All 15 new integration tests pass.
- Per-resource regression checkpoint clean: tenants 34, platform_users
  10, tenant_users 13, org_tree 21, lookups 4, rbac 24, dashboard 17.
- 227 pytest passes (was 212; +15).
- mypy strict clean on 60 source files.
- check_setup 35/35.
- pytest smoke (`scripts/smoke_test.py`) 74/74 post-truncate.
- `scripts/smoke_curl.sh` 20/20 (was 18; +2).
- Alembic head advanced one revision: `cec8fae734e0` → `2fdc4bc9f4cb`.

**Notes on deviations from the prompt.**

- **Stop-and-ask trigger #3 — lookups already seeded.** The prompt
  expected `tenant_tier` and `tenant_status` lookups to be empty
  pre-step; live state had Step 3.6's seed (`0644a4186e48`) already
  in place with matching vocabulary. The migration's `ON CONFLICT DO
  NOTHING` makes the INSERTs idempotent no-ops; no behavioural
  difference. The load-bearing change in this migration is the
  `module_code` display_order UPDATE.
- **Pre-flight 17a — module_code re-ordering required.** Live state
  had GOAL_CONSOLE at display_order=5; the locked screenshot
  sequence places it at 2. Migration includes the UPDATE block.
- 15 tests instead of the prompt's "~14" estimate — small
  test-count delta, no behavioural change.

**Coordination.**

- **Deploy state.** Steps 6.5, 6.5.1, 6.6 are at `origin/main` but
  the deployed Cloud Run image only includes 6.5 and 6.5.1. Step 6.6's
  migration `cec8fae734e0` has NOT yet run on Cloud SQL.
- **Next deploy MUST use `--migrate`.** This deploy bundles Steps 6.6
  + 6.7. Without `--migrate`, the deployed code (post-6.6) expects
  `module_code_enum` while Cloud SQL still has `module_enum`, and
  `/permissions` + `/permission-matrix` would 500 immediately
  (cascading effect on Step 6.5's governance-stats card too). Step
  6.7's seed migration `2fdc4bc9f4cb` depends on `cec8fae734e0`.
- **Post-deploy verification.** Confirm `module_enum` no longer exists
  in Cloud SQL AND `/api/v1/module-access/modules` and `/matrix`
  return 200.

**Rough effort.** ~4 hours including the new label-handling convention
codification, raw-SQL Repo with 3-stage matrix query, position-
alignment test (M3), RLS aggregate-collapse test (M2), and label JOIN
verification (M4).

---

## Section 6.8 — Split `user_role_assignments` into two physical tables

**Deploy status:** LOCAL-ONLY (pending bundled Cloud Run deploy of 6.8.1 + 6.8.2 + 6.8.2.1 + 6.8.3). Update this line when deployed.

Three sub-steps (6.8.1, 6.8.2, 6.8.3) splitting the dual-FK XOR
`user_role_assignments` table into `platform_user_role_assignments`
(no RLS, platform-global) and `tenant_user_role_assignments`
(RLS+FORCE with the unconditional D-29 OR-branch). The split retires
the FN-AB-14 IS-NULL-gated policy and makes AI-RBAC-06 cross-tenant
injection structurally impossible at the schema layer (composite FKs
on `tenant_user_role_assignments` to `tenant_users (tenant_id, id)`
and `org_nodes (tenant_id, id)`). Cross-references **D-34**
("Mixed-audience tables get split into per-audience physical tables")
in CLAUDE.md.

**Sub-step structure:**

- **6.8.1 — DDL + migration + smoke test (DONE).** Schema split with
  data copy. No application code changes.
- **6.8.2 — ORM models + Repos + schemas + seed loader (DONE).**
  Replaced the `_lightweight_stubs.UserRoleAssignment` stub with
  full `PlatformUserRoleAssignment` and `TenantUserRoleAssignment`
  ORM models; added a new `RoleAssignmentsRepo`; rewrote
  `RolesRepo._user_count_subquery` as the SUM of two correlated
  scalar subqueries (one per physical table; `.correlate(Role)` on
  each branch); restructured `loaders/user_role_assignments.py` to
  route per row by audience and removed the `_set_tenant_guc`
  helper. Resolved the 17 expected URA-stub pytest failures from
  6.8.1; pytest 209 -> 226. New schemas
  (`PlatformAssignmentItem`, `TenantAssignmentItem`,
  `RoleAssignmentsResponse`) defined here so 6.8.3's wire-up is
  mechanical.
- **6.8.3 — `/role-assignments` router + endpoint + tests + docs
  (Ready for prompt; blocked by nothing).** New
  `GET /api/v1/role-assignments` with the pre-grouped envelope
  (`{platform_assignments, tenant_assignments}`) mirroring Step
  6.1's `/roles` shape; new `docs/endpoints/role-assignments.md`;
  OpenAPI regen.

**Cloud deploy** blocks on all three sub-steps landing locally; the
bundle deploys to Cloud SQL only after 6.8.3 is green.

---

## Step 6.8.1 — Split `user_role_assignments` (DDL + migration + smoke)

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Schema-only split of `user_role_assignments` into two
physical tables (`platform_user_role_assignments`,
`tenant_user_role_assignments`) per D-34. First of three sub-steps;
no application code touched.

**Why now.** The IS-NULL-gated FN-AB-14 policy was the only multi-
tenant policy diverging from the D-29 unconditional OR-branch shape.
That divergence had two operational costs: (1) PLATFORM sessions
couldn't see all role assignments in one query (only the 3 PLATFORM-
audience rows; TENANT-side rows hidden under unimpersonated PLATFORM,
blocking Step 6.8 RBAC resolver and any "list all assignments"
view); (2) AI-RBAC-06 cross-tenant injection had to live at the
application layer per the DDL's forward note. The split eliminates
both costs by table-shape rather than policy or trigger.

**Scope in (as shipped, 2026-05-09).**
- New DDL `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql` capturing
  the post-split as-shipped baseline. v2 DDL stays per the frozen-DDL
  convention.
- One Alembic migration `3e05299cb533` (down_revision
  `2fdc4bc9f4cb`):
  - Adds `UNIQUE (tenant_id, id)` on `tenant_users` as a precondition
    for the composite FK on `tenant_user_role_assignments`.
    Constraint name `uq_tenant_users_tenant_id` (mirrors `org_nodes`'
    `uq_org_nodes_tenant_id`). Not reflected in `tenant_users_v1.sql`
    per frozen-DDL; D-34 documents this as live-vs-DDL drift, same
    precedent as the policy-migration drift.
  - Creates `platform_user_role_assignments` (no RLS, 4 indexes,
    audience-check trigger).
  - Creates `tenant_user_role_assignments` (RLS+FORCE, unconditional
    PLATFORM OR-branch policy, 5 indexes, audience-check trigger,
    composite FKs on `(tenant_id, tenant_user_id)` →
    `tenant_users (tenant_id, id)` and `(tenant_id, org_node_id)` →
    `org_nodes (tenant_id, id)`).
  - Two new plpgsql trigger functions: `enforce_platform_role_audience`,
    `enforce_tenant_role_audience`.
  - Data copy via per-row tenant impersonation in a single DO block
    (mirrors `loaders/user_role_assignments.py`'s seed pattern):
    PLATFORM-audience reads admit under PLATFORM session +
    IS-NULL-gate; TENANT-side reads require per-tenant
    `set_config('app.tenant_id', t.id, true)`. Verified 3 PLATFORM
    + 19 TENANT = 22 rows copy cleanly.
  - Post-copy count assertion via DO block.
  - DROP TABLE `user_role_assignments` (zero inbound FKs verified
    pre-flight).
- Reversible: downgrade restores v2 URA shape with FN-AB-14
  IS-NULL-gated policy text byte-equivalent to `4fd3aec6ae0c`'s
  upgrade. Per-row impersonation on the rebuild copy. Verified
  round-trip clean (22 rows preserved).
- `scripts/smoke_test.py` refresh:
  - `test_3` updated to reference `tenant_user_role_assignments`
    instead of dropped URA.
  - `test_7` extended to TWO assertions covering both composite FKs
    (org_node side AND tenant_user side — the structural-impossibility
    guarantee for AI-RBAC-06).
  - `test_11` rewritten from the FN-AB-14 9-row truth table (no
    longer applicable) into 4 structural invariants: 11a no-RLS on
    platform table, 11b RLS+FORCE on tenant table, 11c platform-
    audience trigger rejection, 11d tenant-audience trigger rejection.
  - `test_15` 6-table truth table extended to include
    `tenant_user_role_assignments` (54 cells = 6×9; was 5×9=45).
  - `test_16` PLATFORM-INSERT extended with assertions for both
    new tables.
  - Smoke count: 74 → 81 PASS post-truncate (+7).
- `scripts/verify_cloud_schema.py` module docstring updated:
  expected table count 12 → 13 (12 application + alembic_version);
  RLS list updated (tenant_user_role_assignments replaces URA).
- CLAUDE.md updates per the per-step bundling convention:
  schema state line (11 → 12); D-29 amended (uniform unconditional
  shape post-split, IS-NULL-gated form retired); FN-AB-14 deepened
  resolution; new D-34 (mixed-audience tables get split).
- architecture.md updates: schema and storage table inventory
  (11 → 12; URA replaced by 2 entries); multi-tenancy section's
  policy-form prose (uniform unconditional shape).
- BUILD_PLAN.md updates: Section 6.8 introduction; Step 6.8.1 entry
  status DONE; Step 6.8.2 + 6.8.3 TODO placeholders.

**Scope out.**
- Application code: ORM models, Repos, schemas, router, seed loader,
  endpoint integration tests. All Step 6.8.2 territory (the
  `_lightweight_stubs.UserRoleAssignment` stub still references the
  dropped table; `RolesRepo._user_count_subquery` queries it).
- New `/role-assignments` endpoint: Step 6.8.3 territory.
- Cloud deploy: blocks on 6.8.3 landing locally.
- DDL edits to `tenant_users_v1.sql`: forbidden by frozen-DDL
  convention; the new UNIQUE is captured in this step's migration
  and documented in D-34 as live-vs-DDL drift.

**Acceptance criteria (met).**
- New file `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql` created.
- v2 DDL file unchanged.
- New Alembic migration created with `down_revision=2fdc4bc9f4cb`.
- Migration body uses `op.execute()` with raw SQL throughout; no
  hardcoded schema literals.
- `alembic upgrade head` runs cleanly with seeded data: 22 URA rows
  → 3 PLATFORM + 19 TENANT in new tables.
- Round-trip clean: upgrade → downgrade → upgrade restores
  byte-equivalent state (verified 22 rows preserved).
- Schema verification: 12 application tables; RLS+FORCE on
  `tenant_user_role_assignments` (rls=t, force=t); no RLS on
  `platform_user_role_assignments` (rls=f, force=f).
- `tenant_user_role_assignments` policy uses unconditional OR-branch
  matching the other 5 tables.
- Downgrade restores URA's FN-AB-14 IS-NULL-gated form
  byte-equivalent to `4fd3aec6ae0c`.
- Smoke test 81 PASS post-truncate (was 74; +7).
- mypy strict clean (60 source files; no application code changed).
- check_setup 35/35.
- pytest 209 passed (was 227; -18) + 17 expected failures, all
  `relation "core.user_role_assignments" does not exist` localised
  to URA-stub references; defines Step 6.8.2's targeted-fix surface.
- `verify_cloud_schema.py` docstring updated.
- CLAUDE.md / BUILD_PLAN.md / architecture.md updated per the
  per-step bundling convention.

**Notes on deviations from the prompt.**

- **Stop-and-ask trigger #4 (composite UNIQUE pre-check) fired:**
  `tenant_users` had only `PRIMARY KEY (id)`, lacking the composite
  UNIQUE on `(tenant_id, id)` required for the new composite FK.
  Resolved per operator decision (Q1=A, Q2=b): folded the
  `ALTER TABLE tenant_users ADD CONSTRAINT uq_tenant_users_tenant_id`
  into this step's migration as upgrade operation 0; documented as
  live-vs-DDL drift under D-34 (no edit to `tenant_users_v1.sql`).
- **Migration's data-copy required RLS-aware iteration.** The OLD URA's
  IS-NULL-gated policy doesn't admit TENANT-side reads to the
  application role without per-tenant `app.tenant_id` impersonation.
  The migration body sets `app.user_type='PLATFORM'` and iterates
  tenants, mirroring the seed loader's pattern. A naive
  `INSERT ... SELECT` would have copied 0 TENANT-side rows under the
  unimpersonated migration session.
- **Smoke count delta +7** (was prompt's "approximately +2 to +4"):
  the test_15 6-table extension contributes +9 cells (9 truth-table
  rows × 1 new table); test_16 contributes +2 (one per new table);
  test_7 contributes +1 (1 → 2 assertions); test_11 contributes -5
  (9 → 4 assertions). Net +7. Test_3 stays at 1 cell (one entry
  swapped for another).

**Coordination.** Local-only this step. Cloud deploy blocks on
6.8.3 landing locally; the three sub-steps deploy together as a
single bundle.

**Rough effort.** ~5 hours including the precondition UNIQUE on
`tenant_users` (Stop-and-ask resolution), the per-row-impersonation
data copy required by the OLD URA's IS-NULL-gated policy,
test_11 rewrite (4 invariants from the retired truth table),
test_15 6-table extension, test_16 + test_7 extensions, and the
documentation bundle.

---

## Step 6.8.2 — ORM models + repositories + seed loader cutover

**Status.** DONE
**Owner.** CLAUDE_CODE

**Goal.** Application-layer cutover to the post-split shape. The
codebase is now runnable again — the 17 expected URA-stub-failing
pytests turned green; new schemas and a `RoleAssignmentsRepo` are in
place so Step 6.8.3's router wire-up is mechanical.

**Blocked by.** Step 6.8.1 (DONE).

**Scope landed.**
- Deleted `models/_lightweight_stubs.UserRoleAssignment`; only `Store`
  stub remains.
- New full ORM models at `models/platform_user_role_assignment.py` and
  `models/tenant_user_role_assignment.py` per D-13 Pattern (b)
  audit-actor shape; PG_ENUM declarations per the convention;
  `FetchedValue()` defaults; composite FKs left at the DB layer (no
  SA-layer `ForeignKeyConstraint`). `UserRoleAssignmentStatus` Python
  enum declared in the platform module; `ActorUserType` reused from
  `tenant_user`. Models re-exported from `models/__init__.py`.
- New `RoleAssignmentsRepo` at `repositories/role_assignments.py`
  with `list_platform_assignments` and `list_tenant_assignments`;
  default sort `granted_at_desc`; reuses `InvalidSortKeyError`.
- Rewrote `RolesRepo._user_count_subquery` as the SUM of two
  correlated scalar subqueries (one per physical table) at the
  column-expression layer — `.correlate(Role)` on EACH branch
  (third occurrence of the L9/L11/R4 trap). Imports flipped from
  `_lightweight_stubs.UserRoleAssignment` to the two new models.
  Caller call sites unchanged (still `.label("user_count")`).
- New schemas at `schemas/role_assignment.py`:
  `PlatformAssignmentItem`, `TenantAssignmentItem`,
  `RoleAssignmentsResponse` (all `ConfigDict(extra="forbid")`;
  hidden audit-actor fields per H1 convention).
- Restructured `loaders/user_role_assignments.py` as a routing
  loader: per-row inspection of `platform_user_id` / `tenant_user_id`,
  pop the columns that don't exist on the target table, INSERT into
  `platform_user_role_assignments` or
  `tenant_user_role_assignments`. Per-row tenant impersonation
  removed; `_set_tenant_guc` helper deleted (no other callers
  per repo-wide grep).
- `column_mappings.USER_ROLE_ASSIGNMENTS` shape unchanged; comment
  rewritten. `truncate.SEED_TABLES` now lists both new tables in
  place of the old URA. `_base.py` and `README.md` docstrings
  rewritten to reflect routing.
- `test_rbac_router.py`: `_insert_active_platform_ura` ->
  `_insert_active_platform_assignment` (writes to
  `platform_user_role_assignments`); `_delete_uras_by_id` ->
  `_delete_assignments_by_id`. R4 call sites updated.
- `test_seed_loader.py`: `EXPECTED_VISIBLE_COUNTS_PLATFORM` updated
  (both new tables; PLATFORM session sees full counts);
  `EXPECTED_URA_TOTAL` -> `EXPECTED_ASSIGNMENTS_TOTAL`;
  `test_l2b_*` rewritten as `test_l2b_role_assignments_total_split_correctly`
  (no per-tenant impersonation; sum the two tables directly);
  `test_l3` PLATFORM-audience sentinel updated to count the new
  platform table directly.
- CLAUDE.md: new Completed bullet for Step 6.8.2; "Canonical write
  pattern under PLATFORM session" amendment marking the per-row
  impersonation pattern retired for v0; lightweight-stub state
  updated. `docs/endpoints/rbac.md`: `user_count` field description
  + Behaviour notes RLS-scoping bullet rewritten for the
  SUM-of-two-correlated-subqueries shape.

**Acceptance result.** All 17 URA-stub pytest failures from Step 6.8.1
green; pytest 209 -> 226 (+17 exactly; no other tests changed
state); mypy strict clean (87 source files); check_setup 35/35;
smoke test 81/81 PASS post-truncate (unchanged from 6.8.1 — no
schema changes); alembic head unchanged at `3e05299cb533` (no
migration). Cross-tenant integrity verification query returns 0
rows post-reseed; composite FKs guarantee structurally.

**Local-only.** Cloud deploy still blocks on Step 6.8.3.

---

## Step 6.8.2.1 — SUPER_ADMIN supplementary ADMIN-domain permissions

**Status.** DONE (2026-05-09).
**Owner.** OPERATOR (manual seed-Excel + test edits) → CLAUDE_CODE (verification, doc updates, commit hygiene only).

**Goal.** Close the supplementary-permissions gap surfaced during
Step 6.8.3's design conversation. The Step 3.5 seed Excel shipped
with one ADMIN-domain permission targeting SUPER_ADMIN's actual
operational scope (ADMIN.USERS.VIEW.TENANT). The seven other
ADMIN-domain operations a Super Admin needs (cross-tenant store and
org-node visibility/configuration; global-scope user/role
configuration) had no rows in the catalogue. **Hard precondition for
Step 6.8 (RBAC enforcement layer)** — without these grants the
resolver becomes a structural-deadlock once it gates ADMIN-domain
writes (the most-privileged role wouldn't have permissions to do
its job).

**Resolves FN-AB-19** (created and marked RESOLVED in this same
commit per the FN-AB-21 precedent — never had a TODO period).

**Operator-driven implementation.**

- `data/ithina_dev_seed_data.xlsx` `permissions` sheet: appended
  rows `_key=p28..p34` for codes:
  - **ADMIN.STORES.VIEW.TENANT**
  - **ADMIN.STORES.CONFIGURE.TENANT**
  - **ADMIN.ORG_NODES.VIEW.TENANT**
  - **ADMIN.ORG_NODES.CONFIGURE.TENANT**
  - **ADMIN.USERS.VIEW.GLOBAL**
  - **ADMIN.ROLES.CONFIGURE.GLOBAL**
  - **ADMIN.USERS.CONFIGURE.GLOBAL**
- `data/ithina_dev_seed_data.xlsx` `role_permissions` sheet:
  appended seven rows linking SUPER_ADMIN
  (`role_id=f10c718b-1eb0-438a-a75d-d5af3c365296`) to each new
  permission.
- `tests/integration/test_seed_loader.py`
  `EXPECTED_VISIBLE_COUNTS_PLATFORM`:
  - `permissions`: 23 → 30
  - `role_permissions`: 113 → 120
- `tests/integration/test_rbac_router.py` two fixture repairs for
  unique-constraint collisions with the new catalogue rows:
  - P4 (`test_p4_tenant_jwt_sees_full_catalogue`): unseeded tuple
    repointed from `(ADMIN, STORES, CONFIGURE, TENANT)` to
    `(ADMIN, STORES, EXECUTE, STORE)`.
  - RP1 (`test_rp1_returns_role_permissions_with_parent_echo`):
    unseeded tuples for `perm1` / `perm2` repointed from
    `(ADMIN, ORG_NODES, VIEW, TENANT)` and
    `(ADMIN, ORG_NODES, CONFIGURE, TENANT)` to
    `(ADMIN, ORG_NODES, EXECUTE, STORE)` and
    `(ADMIN, ORG_NODES, AUDIT, STORE)`. Test comment also updated.

**Claude Code's contribution.** Verification harness, CLAUDE.md and
BUILD_PLAN.md doc bumps (this entry + the Step 6.8.2.1 Completed
bullet + the FN-AB-19 forward-note slot), commit hygiene.

**Counts.**
- pytest: 263 → 263 (unchanged; no new tests).
- Smoke: 248 passed (unchanged from post-6.8.3 baseline).
- mypy strict: clean on 65 source files.
- check_setup: 35/35.
- alembic head: unchanged at `3e05299cb533` (no migration).

**No code changes** to `src/`. **No new tests.** Cloud-SQL re-deploy
bundles 6.8.1 + 6.8.2 + 6.8.3 + 6.8.2.1 — the seed re-load against
cloud uses the post-6.8.2.1 Excel.

**Numbering note.** Sequenced as 6.8.2.1 (between 6.8.2 and 6.8.3)
because it's seed-data work conceptually pairing with Step 6.8.2's
seed-loader cutover, but executed and committed AFTER 6.8.3 lands.
The four-segment number reflects the chronological-vs-logical
ordering rather than blocking 6.8.3 on it.

**Rough effort.** ~30 minutes operator + verification.

---

## Step 6.8.3 — Bundled inline `roles[]` augmentation + standalone `/role-assignments` endpoint

**Status.** DONE (2026-05-09).
**Owner.** CLAUDE_CODE

**Goal.** Ship two surfaces in one commit, each mutually
load-bearing for the other (shared factories, shared tests, shared
docs reconciliation):

- **Half 1 (A1/A2):** inline `roles: list[UserRoleAssignmentItem]`
  field on `GET /api/v1/tenant-users` and `GET /api/v1/platform-users`
  (list + detail). Each item: `{assignment_id, role_id, role_name,
  role_code, status, granted_at, org_node_id, org_node_name}`.
  Append-only per D-31; URL unchanged.

- **Half 2 (E4):** new `GET /api/v1/role-assignments` returning
  `{platform_assignments: {items, pagination},
  tenant_assignments: {items, pagination}}`. Filters: role_id,
  platform_user_id, tenant_user_id, tenant_id, org_node_id, status.
  Sort: granted_at_asc / granted_at_desc.

**Bundled scope rationale.** Both halves share the same conftest
factories (`make_platform_user_role_assignment`,
`make_tenant_user_role_assignment` — neither existed pre-6.8.3),
the same models from 6.8.2, the same RLS plumbing, and the same
BUILD_PLAN reconciliation. Splitting would mean writing factories
and tests twice and reconciling BUILD_PLAN twice.

**Locked decisions** (from operator/Claude design conversation
2026-05-09):

1. Bundled scope under step number 6.8.3.
2. Half 1 query posture: jsonb_agg correlated subquery, mirroring
   `repositories/tenants.py:list_with_aggregates` exactly.
3. Schema home for `UserRoleAssignmentItem`: `schemas/tenant_user.py`
   (re-exported from `schemas/platform_user.py`). Distinct from
   `schemas/role_assignment.py` shapes.
4. `UserRoleAssignmentItem.model_config = ConfigDict(from_attributes=True)`
   (no `extra="forbid"`) — match user-schema neighbours.
5. 8 fields exactly per item; not `revoked_at`, not `updated_at`,
   not any audit-actor field.
6. ALL assignments returned regardless of status (ACTIVE + INACTIVE).
7. Uniform shape across user types (platform users get
   `org_node_id: null`, `org_node_name: null`).
8. Empty list (not null) when user has no assignments
   (`COALESCE(jsonb_agg(...), '[]'::jsonb)`).
9. URL: `/api/v1/role-assignments` (not `/user-role-assignments`).
10. Conftest factory naming: `make_platform_user_role_assignment`,
    `make_tenant_user_role_assignment`. Local
    `_insert_active_platform_assignment` retired in this step.
11. Factories take a `role_id` argument and trust caller for
    audience matching (audience-check trigger rejects mismatches).
12. Audience routing on `/role-assignments` is a CALL-SITE
    DECISION, not a column filter. **Security-load-bearing:** TENANT
    JWTs MUST NOT execute the platform-side query because
    `platform_user_role_assignments` has no RLS.
13. `RoleAssignmentsRepo.list_tenant_assignments` extended this
    step to accept a `tenant_id: UUID | None = None` filter.
14. Sort vocabulary: `frozenset({"granted_at_asc", "granted_at_desc"})`,
    `InvalidSortKeyError` reuse.

**Trigger #8 resolution (path-(a)).** `schemas/role_assignment.py`'s
`RoleAssignmentsResponse` rewritten from flat `list[Item]` per block
to nested `{items, pagination}` per block. Per-block envelope
wrappers `PlatformAssignmentsBlock` / `TenantAssignmentsBlock`
added. Per-row item types (`PlatformAssignmentItem`,
`TenantAssignmentItem`, `_Assigned*` mini-objects) preserved.

**Filter-shape narrowing.** `platform_user_id` → tenant block
short-circuits (a platform user has no tenant-side assignments
by definition). `tenant_user_id` / `org_node_id` → platform block
short-circuits. `tenant_id` → narrows tenant block only (platform
side has no tenant_id column).

**Pre-flight discrepancies surfaced and resolved.** The prompt's
pre-flight item 9 stated `perms=27, rp=117` as expected; reality
is `perms=23, rp=113` (per `tests/integration/test_seed_loader.py`
and CLAUDE.md Step 6.1). Operator confirmed seed-loader values
are ground truth. Same for `pu=7` (prompt) vs `pu=3` (reality).

**Tests.** Half 1: 18 new test functions across
`test_tenant_users_router.py` (11 functions, U7 parametrized over
4 endpoint kinds = 14 collected items) and
`test_platform_users_router.py` (7 functions). Five LOAD-BEARING:
U5_tu_list (cross-tenant RLS isolation), U5_tu_detail (RLS-as-404
regression), U6_tu_list / U6_pu_list (pagination not broken),
U7 (negative-key assertion: no audit-actor leakage).
Half 2: 15 new tests in NEW `test_role_assignments_router.py`
(R1-R15). Five LOAD-BEARING: R2 (TENANT JWT short-circuits
platform-side), R3 (RLS scoping), R7 (new tenant_id filter), R8
(composite-FK injection rejection at DB), R12 (PLATFORM
no-impersonation regression). 4 broken exact-set assertions
updated.

**Smoke test extension.** 4 new `req` calls inside
`run_matrix_for_caller` in `scripts/test_endpoints.sh`: list,
list?limit=5, status filter, invalid_sort 400. 4 entries × 4
callers = 16 new smoke checks (deviates from the prompt's "+1"
projection but matches existing per-caller pattern for
multi-user-type endpoints).

**Counts.**
- pytest: 227 → 263 (+36).
- mypy strict: 65 source files (was 60).
- check_setup: 35/35.
- alembic head: unchanged at `3e05299cb533` (no migration).

**Coordination.** Cloud deploy unblocked. Bundle
6.8.1 + 6.8.2 + 6.8.3 ships via `--migrate`.

**Resolves.** Step 6.1 forward notes A1, A2, E4. E5 retained as
forward note.

**FN-AB-06 (Tenant-custom roles).** Estimated 2-month landing
trigger per operator's projection 2026-05-09. Schema additions:
`roles.tenant_id NULLABLE` (NULL = platform-shipped, NOT NULL =
tenant-owned), `roles.is_system` distinguishes Ithina-shipped from
tenant-owned, RLS on `roles` with OR-branch policy
`tenant_id IS NULL OR tenant_id = current_setting(...)`,
audience-trigger update from 6.8.1 to handle the tenant-owned
case. Per-tenant cap: default 50 custom roles, overridable via
new `tenants.custom_role_limit INT NOT NULL DEFAULT 50` column.
Permission catalogue stays platform-global; tenants compose
existing permissions into custom role bundles. Lands as its own
step, likely 6.8.7 or 6.9.x.

**Rough effort.** ~6 hours actual (vs ~4 estimated; 4 ADMIN-domain
permissions deferred per operator).

---

### Carryover from Stage 1 (must complete before Stage 6)

(none currently — all planned Stage 1 items shipped or in flight.)

---

# Stage 2 — RBAC enforcement + write surface (dev)

**Status.** Not started.

**Stage boundary.** Full read+write API for the resources in scope, RBAC enforced on every gated endpoint, audit log populated from app for write actions. Auth0 still stubbed.

**Deployment model.** Continuous manual deploy to Cloud Run dev as steps land (same as Stage 1).

---

## Section 6.9 — RBAC enforcement layer

**Status.** COMPLETE (6.9.1, 6.9.2, 6.9.3.1, 6.9.3.2, 6.9.3.2 cleanup all DONE).

Four sub-steps (6.9.3 split into 6.9.3.1 + 6.9.3.2 at Step 6.9.3.1), plus a follow-on cleanup commit:

- **Step 6.9.1 — `has_permission()` core + `PermissionGrant` + `ReasonCode`.** Pure-SQL single-tuple permission check at `src/admin_backend/auth/permissions.py`. Signature: `has_permission(session, auth, module, resource, action, scope, target_anchor=None) → tuple[bool, ReasonCode, str]`. Dispatches on `auth.user_type`. PLATFORM reads `platform_user_role_assignments` JOIN `role_permissions` JOIN `permissions`, filtered by `platform_user_id` and `status='ACTIVE'`; no anchor cascade, no `tenant_module_access` JOIN (Ithina staff administer modules regardless of enablement). TENANT reads `tenant_user_role_assignments` JOIN `role_permissions` JOIN `permissions` JOIN `org_nodes` (composite key per D-34 / AI-RBAC-06) JOIN `tenant_module_access` (filter `status='ENABLED'`). Cascade via Postgres `ltree <@` operator. LIMIT 1 on both paths. `PermissionGrant` `@dataclass(frozen=True)` at `src/admin_backend/auth/permission_grant.py` shipped for Step 6.9.2's `/me/permissions` to consume. `ReasonCode` `StrEnum` at `src/admin_backend/auth/reason_code.py` binary in v0 (`GRANT_MATCHED`, `NO_MATCHING_GRANT_OR_OUT_OF_SCOPE`); granular codes deferred to Step 6.16 audit log writes. Per-request DB read; no caching (FN-AB-24 forward-notes future revisit). EXPLAIN ANALYZE measured 0.169 ms PLATFORM, 0.196 ms TENANT on seeded data. 13 integration tests at `tests/integration/test_has_permission.py`; 5 LOAD-BEARING (T_C1 cascade, T_C3 sibling-region segment-boundary, T_M1 module-DISABLED denial, T_T3 inactive-assignment denial, T_X1 cross-tenant injection guard). Three forward-notes in CLAUDE.md: FN-AB-23 impersonation enforcement, FN-AB-24 caching, FN-AB-25 target_anchor resolution. **Type-drift fix originally in scope was dropped**: the investigation report (F-REPO-4) was stale at write-time; `RoleAssignmentsRepo.list_tenant_assignments` already declared the correct `tuple[list[TenantUserRoleAssignment], int]` annotation since Step 6.8.2 (commit `de9a39cd`). Operator confirmed (a) at pre-flight on 2026-05-13. Status: DONE.

- **Step 6.9.2 — Gate factory + `PermissionDeniedError` + `/me/*` endpoints.** `require(module, resource, action, scope)` factory at `src/admin_backend/auth/permissions.py` returns a FastAPI dependency callable; novel dependency-factory pattern in v0. The gate calls `has_permission()` and raises `PermissionDeniedError` on denial. `target_anchor=None` hardcoded inside the gate for 6.9.2; threading from per-endpoint anchor dependencies ships at 6.9.3. `PermissionDeniedError` at `src/admin_backend/errors.py` — shared/system-wide; `ClientError` subclass; `http_status=403`; `code='PERMISSION_DENIED'`; structured fields via `**context` reach error logs only, response envelope `details=null` per Q7. `get_permissions_for_user(session, auth) -> list[PermissionGrant]` companion at `auth/permissions.py` — same JOIN structure as `has_permission` per audience, per-tuple WHERE clauses dropped, projection widened. `me_router` at `routers/v1/me.py` with `/me` prefix; `GET /me/permissions` returns `{"permissions": [PermissionGrantRead, ...]}` (always array, empty if no grants), `GET /me/can-do?module=...&resource=...&action=...&scope=...&target_anchor=...` returns `{"allowed": bool, "reason_code": str}`. Both endpoints multi-user-type; no `require(...)` gate applies (caller-state endpoints). New `schemas/me.py` ships `PermissionGrantRead`, `MePermissionsResponse`, `MeCanDoResponse`; `extra="forbid"` everywhere; enum fields typed `str` so StrEnum serialises clean. 18 integration tests at `tests/integration/test_me_router.py` (6 MP, 7 MC, 4 GF, 1 XT); 4 LOAD-BEARING (T_GF1 factory mounts as Depends, T_GF2 denial envelope contract, T_GF3 allow runs handler, T_GF4 denial never fires handler Repo via `patch.object` + `AsyncMock` mirror of Step 6.8.3 R2). EXPLAIN ANALYZE (`/me/permissions` on seed): PLATFORM 0.170 ms / 30 rows, TENANT 0.314 ms / 19 rows. 294 pytest passes (276 + 18); mypy strict clean on 70 source files. `scripts/smoke_curl.sh` 20→22; `scripts/test_endpoints.sh` 248→256 (matrix +2 entries × 4 callers); `scripts/test_endpoints_cloud.sh` mirrors. `docs/endpoints/me.md` 8-section; `docs/endpoints/openapi.json` regenerated. Two new forward-notes: FN-AB-26 (`_require_platform_auth` retirement decision deferred to 6.9.3), FN-AB-27 (`/me/permissions` shape simplification revisit at 6.9.3). FN-AB-25 DECISION LOCKED to pattern (b) per-endpoint anchor dependencies; per-resource functions ship at 6.9.3. Status: DONE.

- **Step 6.9.3.1 — Scope cascade in `has_permission`.** Downward cascade (a grant at level N satisfies checks at every level below N) per the locked design. New `satisfying_scopes(scope) -> list[str]` helper at `src/admin_backend/auth/permissions.py`; new `_SCOPE_CASCADE_ORDER` tuple encoding all 8 hierarchy levels (GLOBAL plus the 7 `org_node_type_enum` values). Both `has_permission` SQL paths replace `AND p.scope = CAST(:scope AS permission_scope_enum)` with `AND p.scope = ANY(CAST(:satisfying_scopes AS permission_scope_enum[]))` — first `ANY()` array bind in the codebase. Private `_satisfying_scopes_for_sql` filters the helper's full list down to current `PermissionScope` enum values before binding (Postgres rejects out-of-enum strings at `CAST` time). `get_permissions_for_user` NOT modified — returns raw grants; cascade is the gate's concern. 8 cascade integration tests (T_SC1-T_SC8); 2 LOAD-BEARING (T_SC6 STORE grant fails TENANT check, T_SC8 cross-tenant cascade still denied). 6 helper unit tests at NEW `tests/unit/test_permissions_helpers.py`. EXPLAIN ANALYZE: PLATFORM 0.139 ms / TENANT 0.146 ms; both still hit `pk_permissions`. 308 pytest passes (294 + 14); mypy strict clean. New "Note on org-hierarchy coupling" maintenance convention. FN-AB-28 added (PermissionScope enum expansion); FN-AB-26 updated in place. Status: DONE.

- **Step 6.9.3.2 — Retrofit existing GET endpoints with permission gates.** **14 endpoints retrofitted** across 7 routers (`tenants`, `platform_users`, `tenant_users`, `org_tree`, `dashboard`, `modules_access`, `role_assignments`) with `Depends(require(M, R, A, S, *, anchor_dep=None))`. **3 new modules** at `src/admin_backend/auth/`: `gate_info.py` (`PermissionGateInfo` frozen dataclass marker carried as `__permission_gate__` on every gate function), `anchor_deps.py` (3 per-resource functions: `get_tenant_anchor`, `get_org_node_anchor`, `get_tenant_user_anchor` — each raises the appropriate `*NotFoundError` on miss per F-THREADING-4), `gate_allowlist.py` (`GATE_EXEMPT_PATHS: frozenset[str]` with 7 paths: `/me/permissions`, `/me/can-do`, `/lookups`, `/permissions`, `/permission-matrix`, `/roles`, `/roles/{role_id}/permissions`). `require(...)` extended with `anchor_dep` keyword-only param; two inner-function shapes (no-anchor + with-anchor) keep FastAPI's static-signature requirement satisfied. **FN-AB-26 RESOLVED via option (a)**: `_require_platform_auth` removed; its 2 call sites use `Depends(require(ADMIN, USERS, VIEW, GLOBAL))`. `PlatformAccessRequiredError` left as documented dead code (FN-AB-33 — removal at next cleanup pass). **2 errors promoted** to shared `errors.py` (`TenantUserNotFoundError`, `OrgNodeNotFoundError`). **Wire-contract change**: TENANT-JWT denial at `/platform-users` returns `code=PERMISSION_DENIED` (was `PLATFORM_ACCESS_REQUIRED`); still 403. **Gate-discipline meta-test** at `tests/integration/test_gate_discipline.py` enumerates every `APIRoute` and asserts disjoint trichotomy (gated OR in `GATE_EXEMPT_PATHS` OR in `PUBLIC_PATHS`); single LOAD-BEARING test. **10 retrofit tests** at NEW `tests/integration/test_gate_retrofit.py` (T_RET_1-T_RET_8 with 5a/5b/8a/8b split); 3 LOAD-BEARING (T_RET_3 anchor-miss security regression, T_RET_5b retirement equivalence, T_RET_6 marker positive verification). New `tenant_owner_jwt_factory(tenant_id, with_grants=...)` conftest fixture creates synthetic user + role + grants + assignment + tenant_module_access + org_node; function-scoped, DELETE-tracked teardown in FK-aware parameter order; inline `strict=False` rationale comment in docstring. New `super_admin_jwt` fixture (queries Anjali by email, read-only). **15 existing tests marked xfail** across 5 router test files (`test_dashboard_router.py` × 7, `test_modules_access_router.py` × 3, `test_org_tree_router.py` × 1, `test_tenants_router.py` × 1, `test_gate_retrofit.py` × 1, 2 dashboard already-xfailed from 6.9.3) — all `strict=False` so seed-update auto-flips to pass. **6 tests renamed** to reflect post-retrofit gate-denial semantics. **319 collected, 306 passed, 13 xfailed** (target was 317 ± 5; actual is 319; xfail count 13 within ±5 of target 15). mypy strict clean on 73 source files (was 70; +3 new modules); check_setup 35/35; smoke_test 81/81 post-truncate (no schema changes); smoke_curl 22/22; alembic head unchanged at `3e05299cb533` (no migration). 5 new forward notes added (FN-AB-29 dashboard+module-access dedicated tuples, FN-AB-30 reference-data+roles gating, FN-AB-31 /role-assignments dedicated tuple, FN-AB-32 PLATFORM_ADMIN/SUPPORT_ADMIN ADMIN.USERS.VIEW.GLOBAL coverage, FN-AB-33 `PlatformAccessRequiredError` final removal); FN-AB-26 marked RESOLVED. New "Note on gate allowlist coupling" maintenance convention codifies the GATE_EXEMPT_PATHS sync discipline. `scripts/test_endpoints*.sh` comment lines updated PLATFORM_ACCESS_REQUIRED → PERMISSION_DENIED. EXPLAIN ANALYZE on anchor-dep endpoint (`GET /tenants/{tenant_id}` via PLATFORM JWT): seeded data, sub-millisecond — index lookup on `org_nodes (tenant_id, node_type, parent_id IS NULL)` for the anchor probe, then `pk_tenants` for the row read; no degradation vs pre-retrofit. Status: DONE.

- **Step 6.9.3.2 cleanup — Phase 3 seed update + test infra reconciliation.** Follow-on commit to `80911fa`. Operator applied 3 Phase 3 seed updates post-commit (permissions 30→31, role_permissions 120→122; new permission row + 2 OWNER grants). Pre-cleanup state: 305 passed + 1 failed (`test_l2_seed_row_counts` stale counts) + 13 xfailed. **Cleanup audit surfaced a 6.9.3.2 misclassification**: only 2 of the 13 xfails (D3, T_RET_2) actually used `tenant_owner_jwt_factory`; the other 11 used `_tenant_jwt` (random-UUID JWT) which can't pass the retrofitted gate regardless of seed updates. Marker removal alone was therefore insufficient; 11 test bodies needed migration. **Factory edits at `tests/integration/conftest.py`**: (a) lookup switched from code-string to structural-tuple identity (`module`, `resource`, `action`, `scope`) per the `uq_permissions_tuple` UNIQUE — closes the vulnerability to display-string drift via seed-Excel typos (the Phase 3 update shipped one such typo: code column `ADMIN.TENANTS.VIEW.TENANTS` plural vs tuple scope `TENANT` singular; runtime uses tuple, factory now matches); (b) default grants extended from 1 tuple to 3 (added ADMIN.TENANTS.VIEW.TENANT + ADMIN.ORG_NODES.VIEW.TENANT to ADMIN.USERS.VIEW.TENANT); (c) tenant_module_access insert switched to SELECT-then-conditional-insert via `make_tenant_module_access` (preserves teardown) — caller contract: factory ENSURES presence not status; pre-existing DISABLED row defeats the factory's gate (test must use a different module for the DISABLED case); (d) tenant-root org_node reused if one exists for the tenant — creating a second TENANT-root would race `get_tenant_anchor`'s `LIMIT 1` lookup and break cascade when the LIMIT picks the test's root while the assignment is anchored at the factory's; docstring updated with the explicit caller contract. **11 test bodies migrated** `_tenant_jwt(settings, tid)` → `await tenant_owner_jwt_factory(tid)`: 7 in `test_dashboard_router.py` (S2, S5, S6, O2, O4, O5, O6), 3 in `test_modules_access_router.py` (M2, M5, X1), 1 in `test_org_tree_router.py` (T11). **One assertion bumped**: S2's `platform_users.value == 2` → `== 3` (factory adds 1 ACTIVE tenant_user). **One module swap**: M5's DISABLED ADMIN → DISABLED GOAL_CONSOLE (preserves "DISABLED rows excluded from count" intent; module choice incidental). **13 xfail markers removed** (the 11 migrated + D3 + T_RET_2). **`test_l2_seed_row_counts` updated**: permissions 30→31, role_permissions 120→122. **CLAUDE.md**: appended cleanup sub-bullet to 6.9.3.2 current-state entry; added **FN-AB-34** (seed-loader validation forward note — assert `excel.code == f"{module}.{resource}.{action}.{scope}"` for each permission row at load time; catches display-string typos at load time). **319 passed, 0 failed, 0 xfailed** (was 305 passed + 1 failed + 13 xfailed). mypy strict clean on 73 source files (unchanged); check_setup 35/35; smoke_test 81/81 post-truncate (unchanged); alembic head unchanged at `3e05299cb533` (no migration); no DDL changes; no seed Excel changes in this commit. Status: DONE.

---

## Step 6.10 — Write endpoints: Platform users + Tenant users (umbrella; split into sub-steps)

**Status.** Split (2026-05-14) into 6.10.1 / 6.10.2 / 6.10.3 below. Original umbrella scope retained here for historical context: POST/PATCH endpoints for `platform_users` and `tenant_users`, including stub `auth0_sub` generation (`stub|<uuid7>`) pending Stage 3 Auth0 integration.

### Step 6.10.1 — Tenant users write endpoints

**Status.** DONE-LOCAL (single commit per the new WORKFLOW.md default). Cloud deploy deferred per Phase 5.5 operator pause; batched verification with Step 6.12 et seq.

**Goal (delivered).** Four write endpoints on `/api/v1/tenant-users`:
- `POST /tenant-users` — create with bundled role assignments
- `PATCH /tenant-users/{user_id}` — full_name / email / roles replace-set
- `POST /tenant-users/{user_id}/suspend` — ACTIVE → SUSPENDED
- `POST /tenant-users/{user_id}/activate` — SUSPENDED → ACTIVE

All four multi-audience (`audience=None`) gated on `ADMIN.USERS.CONFIGURE.TENANT` (SUPER_ADMIN + PLATFORM_ADMIN + OWNER). TENANT-audience callers cannot target their own `user_id` (handler-side self-edit guard on the 3 path-bound endpoints). Server-forces `status='INVITED'` on create; INVITED → ACTIVE is the Auth0 invite-accept callback flow (Stage 3 territory; the explicit `/activate` refuses that path with 409). Bundled role assignments anchor at the tenant root org_node (locked decision 4); handler-side Option X audience pre-check converts the trigger reject into clean 422 INVALID_ROLE / INVALID_ROLE_AUDIENCE.

**Lifecycle locked.**
```
[create] ──► INVITED ──[Auth0 invite-accept; Stage 3]──► ACTIVE
                                                         ▲ │
                                                    activate │ suspend
                                                         │ ▼
                                                       SUSPENDED
```

| From      | suspend                          | activate                         |
|-----------|----------------------------------|----------------------------------|
| INVITED   | 409 `INVALID_STATE_TRANSITION`   | 409 `INVALID_STATE_TRANSITION`   |
| ACTIVE    | -> SUSPENDED                     | 409 `INVALID_STATE_TRANSITION`   |
| SUSPENDED | 409 `INVALID_STATE_TRANSITION`   | -> ACTIVE                        |

**Reuse.** `TenantUserNotFoundError` (already in `errors.py` since Step 6.9.3.2), `get_tenant_user_anchor` (already in `anchor_deps.py` returning ltree path), `TransitionResult` enum from `tenants` repo, `InvalidStateTransitionError` + `EmptyPatchError` from Step 6.11.1.

**New artifacts.** 4 new ClientError subclasses (`SelfEditForbiddenError`, `DuplicateTenantUserEmailError`, `InvalidRoleAudienceError`, `InvalidRoleError`); 2 new request schemas (`TenantUserCreateRequest`, `TenantUserPatchRequest`); 3 new TenantUsersRepo methods (`create`, `update`, `transition`); 4 new handlers; 31 new router tests (5 LOAD-BEARING: C3, C7, P3, P5, S4).

**Pytest.** 385 → 416. 0 xfail. mypy strict clean on 73 source files. check_setup 35/35. No DDL changes; no migrations; no seed Excel changes.

**Out of scope (deferred).**
- Hard delete (no DELETE endpoints in v0).
- Cancel invitation (INVITED users with no clean off-ramp) → Step 6.10.3; FN-AB-38 tracks.
- Anchored role bundling (role + non-root org_node) → Step 6.14; FN-AB-41 tracks.
- Platform users writes → Step 6.10.2 (sibling).
- INVITED → ACTIVE (Auth0 invite-accept callback) → Stage 3; FN-AB-39 tracks.
- Email-change Auth0 reconciliation → Stage 3; FN-AB-40 tracks.

### Step 6.10.2 — Platform users write endpoints

**Status.** TODO

**Goal.** POST/PATCH/suspend/activate on `/api/v1/platform-users`. Same shape as Step 6.10.1 but PLATFORM-audience-only (Pattern (a) self-FK on `platform_users.created_by_user_id`; no TENANT-side path). Audit-actor columns are typed FKs to `platform_users.id` per D-13 Pattern (a), so the handler signs writes with the JWT's user_id directly (no `actor_user_type` discriminator needed).

(Detail elaborated when work begins.)

### Step 6.10.3 — Cancel invitation for tenant_users (deferred from 6.10.1)

**Status.** TODO; deferred. Tracked by FN-AB-38.

**Why deferred.** An INVITED tenant_user (`auth0_sub IS NULL`, `invitation_accepted_at IS NULL`) currently has no clean path off the row: suspend is structurally rejected by `ck_tenant_users_auth0_sub_consistency`; PATCH can rename / re-role but not retire; hard delete is not in v0. Two implementation options, both rejected at 6.10.1 design time:

- **(a) Column-based cancellation.** Additive Alembic migration adding `cancelled_at`, `cancelled_by_user_id`, `cancelled_by_user_type` columns + a partial-unique-index rewrite on email so a cancelled invitation can be re-issued. Most surgical fix; requires schema work product hasn't asked for yet.
- **(b) Email-mangling workaround.** Update cancelled row's email to a unique sentinel and rely on PATCH to free the original for re-invite. No schema change; pollutes the row's email history irreversibly.

**Resolution criterion.** Either:
- A v0 deferred-cleanup pass bundles the column-based DDL migration with similar soft-delete additions on other tables, OR
- Product / UX surfaces a hard requirement to cancel an invitation not accepted within N days.

**Scope when picked up.** Single endpoint `POST /api/v1/tenant-users/{user_id}/cancel-invitation`. Allowed source: INVITED only. Gated on `ADMIN.USERS.CONFIGURE.TENANT` (same tuple as 6.10.1, no catalogue update needed).

---

## Step 6.11 — Write endpoints: Tenants

**Status.** DONE-LOCAL (Step 6.11.1 commit `f280f8a`; Step 6.11.2 this commit). Cloud deploy via standard 12-step workflow.

**Goal (delivered).** Four platform-only write endpoints for `tenants`: POST `/api/v1/tenants` (provision); PATCH `/api/v1/tenants/{id}` (partial update); POST `/api/v1/tenants/{id}/suspend`; POST `/api/v1/tenants/{id}/activate`. TERMINATE transition deferred (out of scope per locked decision).

**Two-commit shape.**

- **Step 6.11.1** — foundations + internal API. 4 new ClientError subclasses; `audience` kwarg on `require()`; `TenantCreateRequest` + `TenantPatchRequest`; `TransitionResult` enum + `TenantsRepo.create / update / transition`. 32 new tests (4 error envelope + 13 schema + 16 repo integration). Pytest 319 → 352.

- **Step 6.11.2** — endpoints + tests + smoke + docs. 4 new handlers in `routers/v1/tenants.py`; 31 router tests + extended gate-discipline meta-test (audience assertion on the 4 platform-only routes); smoke scripts +5 entries; `architecture_RBAC.md` Appendix A (audience-kwarg subsection + 3 worked examples) — order-of-checks block D3-corrected to reflect live FastAPI Depends → gate-body ordering; `docs/endpoints/tenants.md` extended with 4 new operations in 8-section format; OpenAPI regenerated; CLAUDE.md gains 3 FN-AB (35-37). Pytest 352 → 385. 0 xfail.

**Lifecycle locked.** Create → TRIAL. TRIAL/ACTIVE → SUSPENDED via /suspend (action OVERRIDE.GLOBAL). TRIAL/SUSPENDED → ACTIVE via /activate (action OVERRIDE.GLOBAL). SUSPENDED → ACTIVE never re-enters TRIAL. Invalid transitions → 409 INVALID_STATE_TRANSITION. Audit columns populated/cleared atomically with status (DDL CHECK enforces).

**Permissions.** POST + PATCH gated on `ADMIN.TENANTS.CONFIGURE.GLOBAL` (SUPER_ADMIN + PLATFORM_ADMIN). /suspend + /activate gated on `ADMIN.TENANTS.OVERRIDE.GLOBAL` (SUPER_ADMIN only per Phase 3 seed). Layer 1 audience="PLATFORM" rejects TENANT JWTs ahead of the permission check.

**Out of scope.**
- DELETE / TERMINATE transition.
- Module enable/disable on existing tenants (Step 6.15 — see re-scope below).
- Audit-log emission to `core.audit_logs` (Step 6.16).
- Multi-audience PATCH (TENANT OWNER edits) — deferred post-6.16; FN-AB-37 tracks. Pattern (a) FKs on audit columns block TENANT-side UPDATE at the schema layer.
- UNIQUE constraint on `tenants.name` — app-layer enforced; FN-AB-35 + FN-AB-36 track the additive migration.

---

## Step 6.12 — Write endpoints: Stores

**Status.** TODO

**Goal.** POST/PATCH endpoints for `stores`. Create store, update store attributes, archive.

(Detail to be elaborated when work begins.)

---

## Step 6.13 — Write endpoints: Org-tree mutations

**Status.** DONE-LOCAL (2026-05-16; cloud deploy deferred per Phase 5.5 batching).
**Owner.** CLAUDE_CODE

**Goal (shipped).** Two endpoints — `POST /api/v1/tenants/{tenant_id}/org-tree` (Add Node under existing parent) and `PATCH /api/v1/tenants/{tenant_id}/org-tree/{node_id}` (Edit Node: rename / recode / reparent atomically). Both multi-audience, gated on `ADMIN.ORG_NODES.CONFIGURE.TENANT` with `anchor_dep=get_tenant_anchor`. node_type is immutable. Archive and delete are out of scope. Reparent rewrites the moved node's ltree path AND every descendant's path in one transaction using ltree's `subpath` + `||` primitives. Role assignments anchored at moved nodes remain intact (D-11 stable id reference). Detail in `docs/implementation-steps/step-6_13-org-tree-writes-2026-05-16.md`.

**Closes.** FN-AB-47 catalogue gap at local DB (cloud deferred). +2 permissions, +5 role_permissions to seed.

---

## Step 6.14 — Write endpoints: Role assignments (per-anchor `roles[]` + diff-replace)

**Status.** DONE-LOCAL (2026-05-16; cloud deploy deferred per Phase 5.5 batching).
**Owner.** CLAUDE_CODE

**Goal (shipped).** No new endpoints. Existing `POST /api/v1/tenant-users` and `PATCH /api/v1/tenant-users/{user_id}` body's `roles` field changes shape from `list[UUID]` (tenant-root-only anchoring per Step 6.10.1) to `list[RoleAssignmentItem]` where each item carries `{role_id, org_node_id}`. The repo's whole-set-replace path is retired in favor of diff-replace: unchanged `(role_id, org_node_id)` tuples in the (current ∩ desired) set retain their original `granted_at` and `granted_by_*` audit columns; only added or removed tuples produce DB writes.

**Resolves.** FN-AB-41 (anchored role bundling at create). Opens FN-AB-45 (cross-step behavioral shift documentation for Step 6.16 audit-log emission).

**Scope in (shipped).**

- `schemas/tenant_user.py`: new `RoleAssignmentItem` Pydantic model; `roles` field retyped on `TenantUserCreateRequest` and `TenantUserPatchRequest`. POST `min_length=1`; PATCH allows `[]` (revoke-all) and `None` (no change).
- `repositories/tenant_users.py`: `_resolve_role_audience` → `_validate_roles` (adds ARCHIVED check; aggregates missing+archived under INVALID_ROLE; audience-mismatch keeps INVALID_ROLE_AUDIENCE distinct). New `_validate_org_nodes` (missing/archived/cross-tenant aggregated under INVALID_ORG_NODE). New `_select_current_active_assignments_for_update` (SELECT FOR UPDATE on current ACTIVE rows). New `_apply_role_assignments_diff` (computes set differences; per-row UPDATE for revoke, per-row INSERT for new ACTIVE; IntegrityError catch scoped to `uq_tenant_user_role_assignments_active` constraint name).
- `routers/v1/tenant_users.py`: new `_flatten_role_assignments` handler-side helper (converts Pydantic items to tuples AND raises `DuplicateRoleAssignmentInRequestError` 422 on within-request `(role_id, org_node_id)` dupes per LD5). Signatures unchanged; one line per endpoint threads the new shape into the repo.
- `errors.py`: 3 new ClientError subclasses (`InvalidOrgNodeError` 422, `DuplicateRoleAssignmentInRequestError` 422, `RoleAssignmentConflictError` 409). Q7 posture: structured context in `exc.context`; response envelope `details` stays `null`.
- Tests: 31 existing router tests mechanically retrofitted to new body shape; 13 new router tests (R1-R5, V1-V7, P1); 6 new repo writes tests (RT1-RT6); 4 new schema unit tests (S1-S4); 3 new errors unit tests (E1-E3). Total +26 tests; pytest 437 → 463.
- Smoke scripts: 4 new entries on `smoke_curl.sh` (WHAT'S CHECKED 38 → 42). Mirror Phase 4c on `test_endpoints.sh` + `test_endpoints_cloud.sh` with 3 new outside-matrix entries under PLATFORM and 1 under TENANT (ADMIN role denied).
- Docs: `docs/endpoints/tenant-users.md` POST + PATCH sections updated (body shape, error codes, diff-replace semantics note); `docs/endpoints/openapi.json` regenerated; `docs/architecture_RBAC.md` cookbook entry gains a "Note on diff-replace (Step 6.14)" paragraph; new step doc `docs/implementation-steps/step-6_14-role-assignment-writes-2026-05-16.md`.

**Scope out.**

- No new endpoints. The standalone `/role-assignments` write surface (POST, PATCH, REVOKE) stays out of scope; the read endpoint at `/role-assignments` (Step 6.8.3) is unchanged.
- No DDL changes, no Alembic migration, no seed Excel changes.
- No catalogue tuple changes. Gate stays `ADMIN.USERS.CONFIGURE.TENANT` multi-audience.
- No audit-log emission (Step 6.16).

---

## Step 6.15 — Write endpoints: Tenant module access (toggle on existing tenants)

**Status.** DONE-LOCAL (2026-05-16; cloud deploy deferred per Phase 5.5 batching).
**Owner.** CLAUDE_CODE

**Re-scoped at Step 6.11 (2026-05-14).** Original scope was "module-access writes" (broad). The create-with-modules-bundle slice landed at Step 6.11.2 inside `POST /api/v1/tenants` — modules requested at tenant-creation time get bundled `tenant_module_access` rows with `status=ENABLED` in the same transaction. Step 6.15's remaining scope was therefore narrower: enable/disable modules on EXISTING tenants via a dedicated endpoint pair.

**Goal (shipped).** Two PLATFORM-only POST endpoints toggle `tenant_module_access.status` between `ENABLED` and `DISABLED` on existing rows, with one upsert seam on the enable path. URL shape `POST /api/v1/module-access/{tenant_id}/{module_code}/enable` and `.../disable` (writes follow the reads' prefix per the URL-convention precondition set by Step 6.7's reads at `/module-access/`).

**Scope in (shipped).**

- 2 endpoints: enable (upsert), disable.
- Gated on `ADMIN.TENANTS.OVERRIDE.GLOBAL` with `audience="PLATFORM"` and `anchor_dep=get_tenant_anchor`. Same privilege boundary as tenant suspend/activate (SUPER_ADMIN only per Phase 3 seed).
- Idempotent-200 on no-op cells (LD4): enable on ENABLED is 200 with no row mutation; disable on DISABLED is 200 with no row mutation; disable on missing is 404 `MODULE_ACCESS_NOT_FOUND`.
- LD5 overwrite semantics: `enabled_at` + `enabled_by_user_id` overwritten on every `DISABLED -> ENABLED` flip and set on initial INSERT; preserved on the disable flip (historical record of when the just-ended ENABLED stint began).
- Upsert race control (LD8): SELECT FOR UPDATE on `(tenant_id, module)` plus `IntegrityError` retry on the INSERT branch of the enable path; the retry sees the committed row from the racing writer and takes the UPDATE branch.
- New `ModuleAccessRead` schema (Pydantic v2, `from_attributes=True`, `extra="forbid"`; audit-actor IDs hidden per H1).
- New `ModuleAccessNotFoundError` (404, `MODULE_ACCESS_NOT_FOUND`); structured `tenant_id` + `module_code` live in `exc.context` for log paths only (Q7 envelope).
- `ModulesAccessRepo` extended with `enable`, `disable`, `_select_for_update`, `_insert_enabled`, `_apply_enable_transition`, `_apply_disable_transition`, `_refetch` private helpers; module-local `TransitionResult` enum (`OK` / `NOT_FOUND`).
- Gate-discipline meta-test extended from 4 to 6 platform-only-write tuples.
- 21 new tests (14 router, 4 repo, 2 schema, 1 error). 437 pytest passes (was 416).
- Smoke + endpoint scripts extended by 6 calls each.

**Scope out.**

- No DDL changes, no Alembic migration, no seed Excel changes.
- No multi-audience writes; PLATFORM-only is locked.
- No imperative cascade to `tenant_user_role_assignments`. The access cascade is structural via the `has_permission()` JOIN on `tenant_module_access.status='ENABLED'`; disabling a module blocks access on the next request without touching the assignment table. Re-enable restores access automatically per D-24.
- No new `D-XX` decision for the URL-convention pattern. Codify when role-assignment writes at Step 6.14 give the second worked example.

**Acceptance criteria (met).**

- 6 transition matrix cells covered end-to-end (router + repo): enable on missing / DISABLED / ENABLED; disable on missing / ENABLED / DISABLED.
- LD8 retry verified: pre-INSERT a row via a separate session, run `enable()`, observe UPDATE branch returns the existing row's id.
- LD5 overwrite verified across two transactions.
- TENANT JWT refused at Layer 1 with 403 `PLATFORM_AUDIENCE_REQUIRED`; PLATFORM_ADMIN refused at Layer 2 with 403 `PERMISSION_DENIED` (catches OVERRIDE-vs-CONFIGURE catalogue regression).
- Anchor dep precedes the gate body: unknown `tenant_id` returns 404 `TENANT_NOT_FOUND` before either Layer 1 or Layer 2 runs.
- 437 pytest passes; mypy strict clean on 73 source files; check_setup 36/36; smoke_curl 38/38 local; gate-discipline meta-test green on 6 tuples.
- Documentation: `docs/architecture_RBAC.md` worked example slotted between tenant suspend/activate and POST `/tenant-users`; `docs/endpoints/module-access.md` extended with the 2 new operations in the canonical 8-section format; `docs/endpoints/openapi.json` regenerated.

**Coordination.** Re-deploy via `./scripts/deploy-cloud-run.sh` (no `--migrate` needed — no DDL changes).

**Rough effort.** ~3 hours including the router + repo writes, tests, smoke updates, docs, and verification.

---

## Step 6.16 - Audit log subsystem

**Status.** DONE-LOCAL 2026-05-21 (core series complete); 6.16.6 followed up 2026-05-23 with the `actor_user_id` filter on the GET endpoint for frontend drawer integration; 6.16.7 followed up 2026-05-23 with the audit row schema additions (3 new columns) + emission retrofit + GET response shape extension (6 new fields) for the audit list-view redesign. All 16 v0 write endpoints across 6 resource families emit synchronous audit rows on success and failure paths; GET endpoint reads the merged stream with `resource_type` + `actor_user_id` filters and the redesigned response shape; per-route extractor mapping ships (FN-AB-66 closure).

**Owner.** CLAUDE_CODE (impl across sub-steps) + HUMAN (catalogue Excel + Cloud SQL UPSERT at 6.16.3).

**Blocked by.** None.

**Goal.** Ship the audit log subsystem: two physically separate tables (tenant + platform) with symmetric column shape and mixed RLS posture, synchronous emission from all v0 write endpoints (16 endpoints across 6 resource families: tenants, tenant-users, module-access, org-tree, stores, roles PATCH), GET endpoint backing the frontend audit timeline, and permission tuples gating audit-log access. Architectural pattern mirrors Step 6.8.1's split of user_role_assignments per D-29. The 6.16.0 design originally framed 12 endpoints / 4 resource families; stores writes (6.17.3 + 6.17.4) and roles PATCH (6.18.3) shipped post-6.16.0 with explicit audit deferrals. Step 6.16.4 closes the roles PATCH deferral; Step 6.16.5 closes the stores deferrals. Step 6.16.6 followed up post-closure with the actor filter required for frontend drawer integration (PlatformUserDetailDrawer.Activity, TenantUserDetailDrawer.Activity, RecentActivityPanel).

**Design reference.** Full subsystem design captured at `docs/architecture_audit_logs.md` (landed at Step 6.16.0). All sub-steps implement against this design document; the design doc is the authoritative reference.

**Sub-steps.** 6.16.0 (design doc + sub-step plan) -> 6.16.1 (schema) -> 6.16.2 (tenants emission) -> 6.16.3 (GET endpoint + permission catalogue) -> 6.16.4 (tenant-users + role-assignments emission) -> 6.16.5 (module-access + org-tree emission + GET resource_type filter) -> 6.16.6 (GET actor_user_id filter; closes FN-AB-69).

------

### Step 6.16.0 - Audit log subsystem design document (pre-step)

**Status.** DONE-LOCAL (this commit).
**Owner.** CHAT (design) + CLAUDE_CODE (commit shape).
**Blocks.** 6.16.1.

**Goal.** Land the subsystem design document at `docs/architecture_audit_logs.md` and the sub-step plan in BUILD_PLAN.md. No code, no schema, no test changes.

**Scope in.**

- `docs/architecture_audit_logs.md` NEW: 391-line design document covering purpose, routing principle, read-access principle, schema (symmetric two-table design), emission contract, read contract, scale considerations, open deferred items, sub-step plan.
- `BUILD_PLAN.md`: Step 6.16 entry expanded from TODO stub to sub-step structure with 6 sub-step blocks (6.16.0 through 6.16.5).
- `CLAUDE.md`: 1-line pointer to the new design doc in the Completed section per post-485d123 lean convention.

**Scope out.** No schema, no application code, no tests.

**Acceptance.** Design document lands; BUILD_PLAN.md reflects sub-step plan; check_setup remains 36/36; pytest baseline unchanged at 689.

**Coordination.** No cloud deploy needed (docs-only). Frontend can read the new design document for context on the audit subsystem.

**Rough effort.** ~30 minutes.

------

### Step 6.16.1 - Audit log schema (DDL + ORM + RLS + indexes)

**Status.** DONE-LOCAL (2026-05-20). Migration `c530346032dd` lands the 2 tables, `audit_result_type_enum` (6 values), 1 CHECK on the tenant table (resource pair), 2 CHECKs on the platform table (resource pair + tenant pair), 5 indexes total, RLS+FORCE+D-29 OR-branch policy on the tenant table, FK to `tenants(id)` on both. ORM models in `src/admin_backend/models/audit_log.py` (`TenantActivityAuditLog`, `PlatformActivityAuditLog`, `AuditResultType`). 13 new integration tests across 2 files. `current_schema.sql` regenerated; `seed_dev_data/truncate.py` `SEED_TABLES` extended with the 2 audit tables for FK-graph resolution under `--reset`. Detail: `docs/implementation-steps/step-6_16_1-audit-log-schema-2026-05-20.md`.
**Owner.** CLAUDE_CODE.
**Blocked by.** 6.16.0.

**Goal.** Land the database schema for both audit log tables per the design at `docs/architecture_audit_logs.md`. Schema only: 2 tables, 1 new enum, RLS posture, indexes, ORM models, schema tests. No application code, no emission.

**Scope in.**

- New Alembic migration creating: `core.audit_result_type_enum` (6 values), `core.tenant_activity_audit_logs` table (16 columns + FK + 2 CHECK constraints + RLS+FORCE+D-29 OR-branch + 3 indexes), `core.platform_activity_audit_logs` table (same 16 columns with tenant_id/tenant_name NULLABLE + FK + 2 CHECK constraints + 2 indexes; no RLS).
- ORM models: `TenantActivityAuditLog`, `PlatformActivityAuditLog` in `src/admin_backend/models/audit_log.py`. Python `AuditResultType` enum mirroring the SQL enum. Reuse existing `ActorUserType` enum for `actor_user_type` column.
- Tests: ~12-15 new tests covering migration roundtrip, ORM persistence, NULL-pair CHECK constraints, RLS posture, FK behavior.
- Regenerate `docs/schema/current_schema.sql`.

**Scope out.** No application code; no emission; no GET endpoint; no permission catalogue change.

**Acceptance.** Migration applies and downgrades cleanly. Schema matches design doc. RLS policy enforces D-29 OR-branch on tenant table. Both tables UNION-compatible at the column level. Tests green. mypy strict clean. check_setup 36/36.

**Coordination.** No cloud deploy at sub-step end; batched with subsequent sub-steps at next Phase 6 cycle.

**Rough effort.** ~3 hours.

------

### Step 6.16.2 - Audit emission: tenants endpoints

**Status.** DONE-LOCAL (2026-05-20). New `src/admin_backend/audit/` package with `emit_audit_event` (same-transaction success path) and `emit_audit_event_in_new_transaction` (separate-transaction failure path). Wired into `TenantsRepo.create` / `update` / `transition` for the 4 tenant write endpoints; failure-path emission hooks into the global exception handler at `main.py:233` with the `AUDITED_ROUTES` dict mapping (method, route template) to (action, resource_type, route_to_platform). Tenant-creation routes to `platform_activity_audit_logs` per the design-doc-named exception; everything else routes by tenant_id presence. 404-on-anchor deliberately NOT audited (no resource to log). Pydantic-direct 422 deferred per FN-AB-63. Bundled design doc refinement: `docs/architecture_audit_logs.md` Emission contract section now states the two-rule transaction semantics explicitly. 27 new tests (6 AE + 10 AS + 11 AF; 13 LOAD-BEARING). pytest 702 -> 729. mypy strict clean on 79 source files. Detail: `docs/implementation-steps/step-6_16_2-audit-emission-tenants-2026-05-20.md`.
**Owner.** CLAUDE_CODE.
**Blocked by.** 6.16.1.

**Goal.** Wire synchronous audit emission into 4 write endpoints on `/tenants`: POST (create), PATCH, suspend, activate. All emission inside the same transaction as the data write per design doc emission contract.

**Scope in.**

- Shared audit-emission helper in a new module under `src/admin_backend/audit/`. Helper takes auth context, action, resource details, result_type, details payload; routes to correct table per routing principle.
- Updates to `TenantsRepo.create`, `update`, `transition` methods to call the audit helper.
- Tests: emission unit tests + integration tests verifying audit rows produced on each endpoint outcome (success + failure paths per `result_type` taxonomy).
- The tenant-creation exception: success row goes to platform_activity_audit_logs (per routing principle).

**Scope out.** Other endpoints (later sub-steps); GET endpoint (6.16.3).

**Acceptance.** All 4 tenants endpoints emit audit rows on success and failure. Atomicity verified (rollback on emission failure rolls back data write). Tests green.

**Coordination.** Batched cloud deploy.

**Rough effort.** ~3-4 hours.

------

### Step 6.16.3 - GET endpoint + permission catalogue

**Status.** DONE-LOCAL (2026-05-20).
**Owner.** CLAUDE_CODE (endpoint) + HUMAN (Excel + Cloud SQL UPSERT).
**Blocked by.** 6.16.2.

**As-shipped.** Two new read endpoints on `/api/v1/audit/activities` (list + detail). Multi-audience gate on `ADMIN.AUDIT_LOG.VIEW.TENANT` with audience-driven repo dispatch (LD1): PLATFORM callers see merged UNION ALL across both audit tables; TENANT callers see only `tenant_activity_audit_logs` (RLS-scoped). Cursor pagination via opaque base64-encoded `(timestamp DESC, id DESC)` (LD3); deviates from project's offset pattern per the audit log's unbounded growth (design doc Read contract > Pagination updated). Detail probes both tables, returns 404 `AUDIT_EVENT_NOT_FOUND` on miss or cross-tenant/cross-audience (D-17). New error classes: `AuditEventNotFoundError` (404), `InvalidCursorError` (422). New `AuditLogsRepo` + 4 new schemas (`CursorPagination`, `AuditActivityListItem`, `AuditActivitiesListResponse`, `AuditActivityDetail`). Operator pre-prompt applied catalogue UPSERT: +1 permission `ADMIN.AUDIT_LOG.VIEW.GLOBAL`; revoked `.VIEW.TENANT` from SUPER_ADMIN / PLATFORM_ADMIN / SUPPORT_ADMIN; granted `.VIEW.GLOBAL` to same 3 platform roles. Live DB: permissions 36 -> 37; role_permissions 132 -> 131. 37 new tests (25 router + 8 repo + 4 schema; 12 LOAD-BEARING). Pytest 729 -> 766. mypy strict clean on 82 source files. smoke_curl 64 -> 67. OpenAPI: +2 paths, +4 schemas. New FN-AB-64 captures uniform 4-column search rationale + over-granted `.VIEW.TENANT` tenant-role observation (deferred to v0 staging cleanup). Detail: `docs/implementation-steps/step-6_16_3-audit-read-endpoints-2026-05-20.md`.

**Goal.** Ship the GET endpoint backing the frontend audit timeline. Filters, search, cursor pagination per the design doc. Permission tuples added to catalogue (Excel + local + Cloud SQL in lockstep, matching the Step 6.13 / 6.17.1 / 6.18.1 precedent).

**Scope in.**

- Permission catalogue addition: 2 new permission rows for tenant + platform audit view, role grants for SUPER_ADMIN / PLATFORM_ADMIN / OWNER per the read-access principle. Excel + local DB + Cloud SQL UPSERT in the standard catalogue-update workflow.
- New router endpoint(s) at `/api/v1/audit/activities` (final URL TBD at impl design). UNION ALL across both tables for platform users; tenant-table-only for tenant users (RLS enforces).
- Cursor-paginated, accepts `from` / `to` / `status` / `tenant_id` / `scope` filters and `search` parameter per design doc.
- Tests covering filter combinations, RLS scoping, cursor stability, search semantics.

**Scope out.** Other emission endpoints (later sub-steps).

**Acceptance.** Frontend can query the audit log via the GET endpoint. Filtering and pagination work end-to-end. Tenant scoping verified via RLS. Tests green.

**Coordination.** Cloud SQL catalogue update applied in lockstep with local. Frontend integrates within 24 hours of deploy. Per the operator-communicated plan: audit log will show only tenants-endpoint events until 6.16.4 and 6.16.5 ship the remaining emission.

**Rough effort.** ~4 hours.

------

### Step 6.16.4 - Audit emission: tenant-users (4) + roles PATCH (1) endpoints

**Status.** DONE-LOCAL 2026-05-21.
**Owner.** CLAUDE_CODE.
**Blocked by.** 6.16.3.

**Goal.** Wire synchronous audit emission into 5 v0 write endpoints — 4 on `/tenant-users` (POST, PATCH, suspend, activate) and PATCH `/roles/{role_id}` (closes the 6.18.3 audit deferral). PATCH /tenant-users includes the diff-replace on `roles[]`; PATCH /roles includes the diff-replace on `permission_ids`. Both produce one audit row per HTTP request.

**Scope in.**

- AUDITED_ROUTES extended with 5 new entries (TENANT_USER × 4 with route_to_platform=False; ROLE × 1 with route_to_platform=True).
- `TenantUsersRepo.create / update / transition` and `RolesRepo.update` extended with `auth: AuthContext | None` + `request_id: UUID | None` optional kwargs; same-transaction emission.
- Frozen-label resolution at write time: role+org_node names for tenant-users CREATE/UPDATE; permission codes for roles UPDATE.
- Two new optional sub-keys on the standard `details` payload shapes per the optional-sub-key convention codified in `docs/architecture_audit_logs.md`: `denial_reason` on PERMISSION_DENIED (handler-side guard); `invariant` on INTERNAL_ERROR (Layer 2 tripwire).
- Failure-path handler extended to dispatch path-param extraction across `tenant_id` / `user_id` / `role_id`; resource_label lookup dispatches on `resource_type` (TENANT / TENANT_USER / ROLE).
- 33 new tests (3 AE unit + 10 AS + 12 AF + 8 RS/RF; 16 LOAD-BEARING).
- `make_tenant` conftest fixture extension: audit-row DELETE precedes tenant DELETE (FK ON DELETE RESTRICT).

**Scope out.** Module-access + org-tree + stores emission (6.16.5). Promotion of `_actor_type_from_auth` to shared module (FN-AB-58 stays open). New endpoints; permission catalogue change.

**Acceptance.** 791 -> 824 pytest passes; mypy strict clean; check_setup 36/36; design doc + BUILD_PLAN.md endpoint-count correction applied.

**Coordination.** No DDL; no migration; no smoke / test_endpoint script changes; no permission catalogue change. Cloud deploy batched with 6.16.5 at next deploy cycle.

**Rough effort.** ~3 hours.

------

### Step 6.16.5 - Audit emission: module-access (2) + org-tree (2) + stores (3) + GET resource_type filter

**Status.** DONE-LOCAL 2026-05-21.
**Owner.** CLAUDE_CODE.
**Blocked by.** 6.16.4.

**Goal.** Wire synchronous audit emission into the remaining 7 v0 write endpoints (module-access enable/disable, org-tree POST add-node + PATCH edit-node, stores POST + PATCH + set-status); extend `GET /api/v1/audit/activities` with a `resource_type` filter query parameter; promote the failure-path resource extraction to a per-route extractor mapping (FN-AB-66 closure). Closes the 6.16 series.

**As-shipped scope.**

- `AUDITED_ROUTES` extended with 7 new entries. Per LD3, stores set-status's success path emits one of 4 per-target action codes (OPEN_SOFT / ACTIVATE / CLOSE / DEACTIVATE) dispatched on `target_status`; failure-path uses the single fallback `SET_STATUS`.
- Per LD2, module-access ENABLE / DISABLE emit one action code with before/after status distinguishing first-time INSERT (`before=null`) from re-enable. No-op idempotent paths (enable-on-ENABLED, disable-on-DISABLED) emit ZERO audit rows — closes FN-AB-42.
- Per LD6, atomic-pair stores POST emits 1 audit row (LD14 invariant) with `org_node_created_atomically: true` in `details.snapshot` (always true in v0 per FN-AB-68; reserved for forward variants).
- Org-tree `add_node` / `edit_node`, modules-access `enable` / `disable`, stores `create` / `update` / `transition` repo methods gain optional `request_id: UUID | None = None` kwargs (auth stays mandatory — load-bearing for the actor-pair INSERTs on these resources). When `request_id` is None, emission skips cleanly for repo-level unit tests.
- LD12 per-route extractor mapping rewrite at `src/admin_backend/main.py` (FN-AB-66 closure): sibling dict `RESOURCE_EXTRACTORS` keyed by `resource_type`. Six extractors: `TENANT`, `TENANT_USER`, `ROLE`, `MODULE_ACCESS`, `ORG_NODE`, `STORE`. The 6.16.4 `_failure_result_and_details(... auth=...)` extension preserved for caller_audience fallback.
- LD17: `GET /api/v1/audit/activities` gains `resource_type: str | None = None` query param (open string vocabulary; AND-composed with existing filters; applied to both UNION branches; unknown values return 0 rows naturally). No schema-file change (query params declared inline at the router).
- LD9 module-access label resolution introduces a 3rd lookup pattern (label from `core.lookups` keyed by `(list_name='module_code', code=:mc)`); ORG_NODE and STORE follow the established direct-table lookup pattern.

**Test catalogue.** 4 new files + 2 unit-test additions:

- `tests/unit/test_audit_emit.py` +2: AE10 (CREATE snapshot carries new optional sub-keys `org_node_created_atomically`, `parent_org_node_name`); AE11 (LOAD-BEARING; stores set-status action label dispatch covers OPEN_SOFT / CLOSE / DEACTIVATE; LD3 contract).
- `tests/integration/test_audit_emission_module_access.py` (12 tests): MS1-MS7 success-path; MF1-MF5 failure-path. LOAD-BEARING: MS1, MS2, MS3 (no-op-not-audited), MS5 (no-op-not-audited), MS6, MF1, MF3 (anchor-404 not audited).
- `tests/integration/test_audit_emission_org_tree.py` (12 tests): OS1-OS7 success; OF1-OF5 failure. LOAD-BEARING: OS1 (CREATE snapshot with parent_org_node_name frozen), OS3 (reparent before/after parent_org_node_name), OS4 (multi-field diff stays action=UPDATE per LD4), OF1 (anchor-404 not audited), OF2 (duplicate-code CONFLICT).
- `tests/integration/test_audit_emission_stores.py` (14 tests, SS5 dropped per FN-AB-68 + SS1/SS2 consolidated per LD6 always-true): SS atomic-pair, rename, no-change, 3 transition action codes, CREATE row count, request_id correlation; SF dup-code, empty-PATCH, anchor-404 PATCH, invalid-transition, anchor-404 set-status, TENANT PERMISSION_DENIED. LOAD-BEARING: SS-atomic, SS-close, SS-single-row (LD14), SF-dupcode (failure routes to platform table per LD10), SF-invalid-transition, SF-anchor-404.
- `tests/integration/test_audit_router_resource_type_filter.py` (5 tests): RTF1 (filter by TENANT_USER), RTF2 (STORE filter), RTF3 (unknown value -> 0 rows no 422), RTF4 (AND-compose with status), RTF5 (TENANT caller still RLS-scoped). LOAD-BEARING: RTF1, RTF3, RTF4.

**Outcome.**

- pytest 824 -> 869 (+45 net).
- mypy strict clean on 82 source files (unchanged).
- check_setup 36/36 (unchanged).
- alembic head unchanged (no migration).
- No DDL changes; no permission catalogue change; no Excel change.
- 4 design doc edits at `docs/architecture_audit_logs.md`: per-route extractor paragraph in Emission contract (FN-AB-66 closure); resource_type vocabulary table in Schema; resource_type filter mention in Read contract; sub-step plan table closure + "6.16 series complete" note.
- OpenAPI regenerated (`docs/endpoints/openapi.json`): `GET /audit/activities` carries the new `resource_type` query param.

**FN-AB closures.** FN-AB-65 closes (series acceptance criterion met). FN-AB-66 closes (per-route extractor mapping shipped; sibling dict shape b). FN-AB-42 closes (no-op idempotent module-access flips no longer emit; refines 6.16.4 LD14 from "one row per request" to "at most one row per request"). FN-AB-68 NEW: OPEN_SOFT action code reserved for `target=OPENING` set-status but no TRANSITION_MATRIX cell allows `*->OPENING` today (OPENING is entry-only via POST per 6.17.4 LD1); label stays in vocabulary for D-31 append-only and forward matrix relaxation; AE11 unit covers the label dispatch; integration coverage gated on matrix relaxation.

**Coordination.** Cloud deploy batched per Phase 5.5 (no DDL; bundles with other DONE-LOCAL steps at next deploy cycle).

------

### Step 6.16.6 - GET `/audit/activities` actor_user_id filter

**Status.** DONE-LOCAL 2026-05-23. Single optional query parameter `actor_user_id: UUID | None = None` added to `GET /api/v1/audit/activities`. AND-composed with existing filters per the 6.16.5 LD17 / 6.16.3 LD5 precedent; SQL clause `AND (CAST(:actor_user_id AS uuid) IS NULL OR actor_user_id = CAST(:actor_user_id AS uuid))` added inline at two sites in `src/admin_backend/repositories/audit_logs.py` (the TENANT-only builder at `_build_tenant_only_sql` and the shared `common_where` block in `_build_union_sql`). No `_apply_common_filters` shared helper exists in the live repo (LD5 Adjusted-trivial vs the prompt's single-helper framing; behaviour identical). No actor_user_type companion parameter (LD4: `platform_users.id` and `tenant_users.id` use the same `uuidv7()` DDL default, globally unique). LD3 open-vocabulary posture mirrors 6.16.5's resource_type filter: unknown UUIDs return 0 rows naturally, no 422. 3 new tests in `tests/integration/test_audit_router.py` (AUF1 happy path, AUF2 AND-composition with status, AUF3 unknown UUID returns empty). 2 LOAD-BEARING (AUF1, AUF3). pytest 869 -> 872 (+3 exact). mypy strict clean on 82 source files (unchanged). check_setup 36/36 (unchanged). One design doc edit at `docs/architecture_audit_logs.md`: new `actor_user_id` row in the Read contract > Filter parameters table; "Currently deferred" wording in Scale considerations option 6 + Open deferred items > Actor filter parameter rewritten to reflect shipped state. OpenAPI regenerated. Detail: `docs/implementation-steps/step-6_16_6-actor-filter-on-audit-activities-2026-05-23.md`.
**Owner.** CLAUDE_CODE.
**Blocked by.** 6.16.5.

**Goal.** Close FN-AB-69 (frontend drawer activity tabs need an actor filter on `/audit/activities` to consume the audit endpoint instead of the dead `/audit-logs` endpoint). Add a single optional query parameter; no companion type parameter; no new permission tuples; no DDL.

**Scope in.**

- `routers/v1/audit.py`: `actor_user_id: UUID | None = Query(...)` added to `list_audit_activities` adjacent to the existing `resource_type` parameter.
- `repositories/audit_logs.py`: `AuditLogsRepo.list` gains `actor_user_id: UUID | None = None` kwarg; param bound at the params dict; SQL clause added inline at two sites (LD5 Adjusted-trivial).
- 3 new tests in `tests/integration/test_audit_router.py`: AUF1 (happy path), AUF2 (AND with status), AUF3 (unknown UUID -> empty no 422).
- One design doc edit at `docs/architecture_audit_logs.md` (Read contract > Filter parameters table + Scale + Open deferred items + Sub-step plan).
- OpenAPI regen.
- New step doc + this prompt bundled.

**Scope out.** No new endpoints; no actor_user_type companion; no actor BTREE index (deferred per design doc Scale option 6); no permission catalogue change; no Alembic migration; no DDL; no smoke / test_endpoint script changes; no frontend integration (separate frontend work).

**Acceptance.** pytest 869 -> 872 (+3 exact). New parameter visible in OpenAPI. FN-AB-69 RESOLVED in CLAUDE.md. Behaviour identical for existing 25 tests in `test_audit_router.py` (the `IS NULL` branch of the new clause is exercised implicitly by every call that omits the filter).

**Coordination.** Cloud deploy batched per Phase 5.5; bundles with 6.16.4 + 6.16.5 at next deploy cycle.

**Rough effort.** ~1 hour.

**FN-AB closures.** FN-AB-69 RESOLVED (born-resolved: created and flipped in the same commit; mirrors FN-AB-19 / FN-AB-21 precedent — the pre-existing acknowledgement of the gap lived in `docs/architecture_audit_logs.md:399` (Scale option 6) and `:432` (Open deferred items) prior to 6.16.6 without a FN-AB number).

------

### Step 6.16.7 — Audit list-view redesign (backend wire shape + display vocabulary)

**Status.** DONE-LOCAL 2026-05-23. Single-commit step. 3 new columns added to both audit tables (`actor_organization_name` NOT NULL, `actor_roles` NOT NULL, `resource_subtype` NULL) via migration `7a3c8e9d2f5b`. Path A backfill: pre-6.16.7 rows backfilled per LD3 (`actor_organization_name` via CASE on `actor_user_type` for tenant table, literal `'Platform-Ithina'` for platform table; `actor_roles = '-'` for all historical rows; `resource_subtype` stays NULL on historical rows). NOT NULL applied post-backfill on the two non-NULL columns. Migration upgrade + downgrade + round-trip clean. Emission retrofit at all 16 v0 audit emission sites populates the 3 new columns; new helpers in `audit/emit.py` resolve actor_organization_name (JOIN tenants for TENANT actors; literal `Platform-Ithina` for PLATFORM) and actor_roles (JOIN appropriate role-assignments table + `core.roles`; aggregate `roles.name` display values for ACTIVE assignments; comma-separated). Org-tree paths (2 sites) populate `resource_subtype` from `org_nodes.node_type`; all other paths leave it None. Action vocabulary: UPDATE label flipped "Updated" -> "Edited"; SET_STATUS label flipped "Status change" -> "Set status" (LD8). CONFLICT `result_label` composes `"Blocked - <qualifier>"` via per-class dispatch covering 9 ClientError subclasses (LD9). GET endpoint response schema (`AuditActivityListItem`) grows from 8 to 14 fields, additive only; detail (`AuditActivityDetail`) grows from 16 to 19 fields. Backend composes `what` field via `_label_for_resource_type` helper using `resource_type` + `resource_subtype` + `resource_label` (LD11 + LD12). pytest 872 -> 892 (+20). FN-AB-67 RESOLVED. FN-AB-70 NEW (INTEGRITY_VIOLATION reserved vocabulary; no callers; revisit when use case surfaces). Frontend rendering of the redesigned wire shape is out of project scope; handled separately by the frontend team against the deployed wire shape on their own schedule. Detail: `docs/implementation-steps/step-6_16_7-audit-row-schema-and-emission-retrofit-2026-05-23.md`.

**Owner.** CLAUDE_CODE.

**Blocked by.** 6.16.6.

**Goal.** Extend the audit row schema with 3 additive columns supporting the audit list-view redesign (Phase 1 + Phase 2 of 6.16.7). Surface the new columns through the GET endpoint response shape (6 new additive fields). Vocabulary changes for action labels + CONFLICT qualifier dispatch + ORG_NODE subtype labels.

**Scope in.**

- Alembic migration `7a3c8e9d2f5b` (Path A: ADD COLUMN NULL -> UPDATE backfill -> SET NOT NULL on the 2 non-NULL columns); both audit tables symmetric per LD1.
- ORM models extension (3 new columns on both `TenantActivityAuditLog` and `PlatformActivityAuditLog`).
- `src/admin_backend/audit/emit.py` retrofit: 2 new resolvers (actor_organization_name, actor_roles) called inside both emission entry points per LD13 centralisation; 1 new helper (`_label_for_resource_type`) for the LD12 type-label mapping; 1 new dispatch (`_CONFLICT_QUALIFIERS` + `compose_conflict_result_label`) for LD9 composition; action label updates per LD8; dual-mechanism INSERT retrofit (ORM constructor + raw `text()` INSERT statement extended from 14 to 17 explicit columns).
- `src/admin_backend/repositories/org_nodes.py` only: pass `resource_subtype=row.node_type.value` kwarg on both emission sites (2 sites). Other 5 emission-site repos unchanged.
- `src/admin_backend/schemas/audit_log.py`: `AuditActivityListItem` 8 -> 14 fields; `AuditActivityDetail` 16 -> 19 fields (additive).
- `src/admin_backend/repositories/audit_logs.py`: SELECT projection on all 4 SQL builders extended; `AuditActivityDetailRow` dataclass extended.
- `src/admin_backend/routers/v1/audit.py`: `_compose_what` helper + extended `_list_item_from_row` + `_detail_from_row` mappers.
- 3 new unit tests (`_label_for_resource_type` × 3 cases, CONFLICT qualifier dispatch). Existing AE1-AE3 updated for the new `_build_row` signature; AE5/AE11 updated for the new labels.
- 3 new repo unit tests for `what` composition (covers 13 resource_type x subtype combinations).
- 2 new router tests for list-response 14-field shape (one TENANT_USER, one ORG_NODE/REGION).
- 1 new emission integration test per file: AS_N1 (tenants), OS_N1 (org-tree, resource_subtype populated), AF_N1 (failures, CONFLICT qualifier composition + actor enrichment on failure path).
- `tests/integration/test_audit_migration.py` NEW: 8 tests covering the migration's invariants (head revision, column nullability, INSERT round-trip, backfill expression, NOT NULL enforcement, NULL semantics).
- Conftest fixture extension: both audit factories gain 3 optional kwargs (`actor_organization_name`, `actor_roles`, `resource_subtype`) with sensible defaults.
- `docs/architecture_audit_logs.md` 3 sections updated (Schema, Read contract response shape, Display vocabulary subsection).
- `docs/schema/current_schema.sql` + `docs/schema/migration_log.md` regenerated.
- `docs/endpoints/openapi.json` regenerated.
- BUILD_PLAN.md 6.16 root amendment + this 6.16.7 entry.
- CLAUDE.md capsule + FN-AB-67 closure + FN-AB-70 new entry.
- New step doc + this prompt bundled.

**Scope out.** Frontend rendering of the redesigned wire shape (separate team scope); detail panel JSON view + permission gate (future step); old-field retirement from `AuditActivityListItem` (additive only); actor BTREE index (deferred per design doc Scale considerations); async outbox emission (deferred); `_actor_type_from_auth` promotion (FN-AB-58 stays open); FN-AB-63 Pydantic 422 envelope.

**Acceptance.** pytest 872 -> 892 (+20). mypy strict clean (82 source files). check_setup 36/36. Migration round-trip clean. New columns surface in OpenAPI. FN-AB-67 RESOLVED. FN-AB-70 NEW.

**Coordination.** Cloud deploy batched per Phase 5.5; bundles with 6.16.6 at next deploy cycle. DDL migration applies at Phase 6 deploy.

**Rough effort.** ~1 day.

**FN-AB closures.** FN-AB-67 RESOLVED (actor enrichment shipped via role + organisation; full_name was a candidate the operator chose not to pursue per Phase 1 lock).

**FN-AB additions.** FN-AB-70 NEW (INTEGRITY_VIOLATION reserved vocabulary; no production callers; revisit when an emission path needs to distinguish DB-layer integrity violations from app-layer CONFLICTs).

------

---

### Step 6.17 — Stores resource (read + write surface)

**Status.** DONE-LOCAL (2026-05-18). All 4 sub-steps (6.17.1 seed deltas, 6.17.2 GET, 6.17.3 POST + PATCH, 6.17.4 set-status) shipped locally; cloud deploy batched per Phase 6 cycle when operator chooses. **Owner.** CLAUDE_CODE (impl) + HUMAN (Excel) **Blocked by.** None.

**Goal.** Stores becomes a first-class resource: list + detail reads, create + edit + status-change writes. Follows the tenants resource shape with three deviations: cross-table `tenant_name` on list (JOIN), single `set-status` endpoint replacing tenants-style suspend/activate verbs, and CLOSED-state audit-triplet state machine.

**RBAC posture.** Every endpoint shipped in this step is gated via `Depends(require(...))` per the mandatory-gate-discipline contract (Step 6.9.3.2). No endpoint in 6.17 is allowlisted; all carry a gate. Catalogue grants for SUPER_ADMIN / PLATFORM_ADMIN / OWNER land in 6.17.1 before any endpoint ships. Gate tuples and anchor deps are settled at each sub-step's design conversation.

**Sub-steps.** 6.17.1 (seed deltas) → 6.17.2 (GET) → 6.17.3 (POST + PATCH) → 6.17.4 (set-status).

**Outcome.** `/api/v1/stores` mounted with full surface (list, detail, create, edit, lifecycle). Frontend integrates incrementally after each sub-step.

------

### Step 6.17.1 — Seed deltas (catalogue + lookups)

**Status.** DONE **Owner.** HUMAN (Excel) + CLAUDE_CODE (SQL script + tests) **Blocks.** 6.17.2.

**Goal.** Land permission grants for SUPER_ADMIN / PLATFORM_ADMIN / OWNER on stores, and seed `store_status` + `tax_treatment` lookups for frontend dropdowns.

**Scope in. Shipped**

- Excel: `permissions` +2 rows (`ADMIN.STORES.VIEW.GLOBAL`, `ADMIN.STORES.CONFIGURE.GLOBAL`); `role_permissions` +6 rows (SUPER_ADMIN + PLATFORM_ADMIN on the 2 new GLOBAL tuples; OWNER on the 2 existing TENANT tuples).
- SQL UPSERT script at `scripts/sql/step-6_17_1-seed-delta.sql`, idempotent, covers the catalogue delta plus 6 `lookups` rows (`store_status` × 4, `tax_treatment` × 2). Lookups are SQL-managed, not in Excel, because `seed_dev_data --reset` does not truncate `core.lookups`.
- Test count updates in `test_seed_loader.py::EXPECTED_VISIBLE_COUNTS_PLATFORM`.

- Inline SQL (run in Cloud SQL Studio for cloud parity, and in DBeaver for local lookups): catalogue UPDATE+INSERT (mutate p28/p29 to GLOBAL, insert p38/p39 TENANT, insert 4 role_permissions), plus 6 `lookups` rows (`store_status` × 4, `tax_treatment` × 2). Lookups SQL-managed, not in Excel, because `seed_dev_data --reset` does not truncate `core.lookups`.

**Scope out.** No application code, no Alembic migration.

**Acceptance.** `--reset` produces expected catalogue counts; `/api/v1/lookups?lists=store_status,tax_treatment` returns the 6 seeded values; SQL script idempotent across local + Cloud SQL; test suite green.

**Coordination.** Frontend can consume the new lookups immediately after deploy.
**Outcome**
Local via Excel reseed; Cloud SQL via inline SQL. Lookups SQL-managed on both envs (not in Excel). `test_seed_loader.py::EXPECTED_VISIBLE_COUNTS_PLATFORM` updated; pytest 505 passed.
**Rough effort.** 30 to 45 min.

------

### Step 6.17.2 — Stores GET endpoints

**Status.** DONE-LOCAL (2026-05-18) **Owner.** CLAUDE_CODE **Blocked by.** 6.17.1.

**Goal.** `GET /stores` (list with optional `?tenant_id=` plus standard filters and sort) and `GET /stores/{store_id}` (detail). Replaces the 2-column ORM stub at `models/_lightweight_stubs.py` with a full Store model.

**RBAC.** Both endpoints gated via `require(...)`; mandatory-gate-discipline test runs in CI.

**Scope in.**

- `models/store.py` (full 22-column ORM model + `StoreStatus` / `TaxTreatment` Python enums); stub retired; sole importer at `repositories/tenants.py:53` updated.
- `schemas/store.py` with `StoreListItem` (carries `tenant_name` via tenants JOIN), `StoreListResponse`, `StoreDetail`.
- `repositories/stores.py` with `StoresRepo.list` + `get_by_id`; SORT_MAP constant; `InvalidSortKeyError` raise.
- `auth/anchor_deps.py::get_store_anchor`; `StoreNotFoundError` in `errors.py`.
- `routers/v1/stores.py` mounted at `/stores`.
- `make_store` fixture upgraded from raw SQL to ORM-native.
- Repo + router tests; smoke + curl additions; `docs/endpoints/stores.md`; OpenAPI regen.

**Scope out.** Writes (6.17.3, 6.17.4).

**Acceptance.** Both endpoints work locally + cloud; cross-tenant isolation verified; mandatory-gate-discipline test green; mypy strict clean; check_setup clean.

**Coordination.** Frontend integrates within 24 hours of dev deploy.

**Rough effort.** 2 to 3 hours.

------

### Step 6.17.3 — Stores POST + PATCH

**Status.** DONE-LOCAL (2026-05-18). See `docs/implementation-steps/step-6_17_3-stores-writes-2026-05-18.md`. **Owner.** CLAUDE_CODE **Blocked by.** 6.17.2.

**Goal.** `POST /stores` (multi-audience create; `org_node_id` optional in body to support all three product workflows for the store ↔ org_node link) and `PATCH /stores/{store_id}` (edit; `status` and `tenant_id` immutable on PATCH).

**RBAC.** Both endpoints gated via `require(...)`; mandatory-gate-discipline test runs in CI.

**Scope in.**

- `StoreCreateRequest`, `StorePatchRequest` schemas; Pydantic validators mirroring DDL CHECKs (currency `^[A-Z]{3}$`, country length + regex, lat/lng ranges, name length).
- `StoresRepo.create` + `StoresRepo.update`; audit-actor pair (`created_by_*`, `updated_by_*`) populated from `auth` context.
- Error classes: `DuplicateStoreCodeError` (409, `DUPLICATE_STORE_CODE`), `OrgNodeNotForStoreError` (4xx, exact code TBD at impl design), `EmptyPatchError` reused.
- Router endpoints.
- C-series + P-series tests; smoke + curl; docs; OpenAPI regen.

**Scope out.** Lifecycle transitions (6.17.4).

**Acceptance.** Both endpoints work; FK + UNIQUE + CHECK error mapping verified; both null and populated `org_node_id` paths on POST covered; mandatory-gate-discipline test green; mypy clean.

**Coordination.** Frontend integrates within 24 hours of dev deploy.

**Rough effort.** ~3 hours.

------

### Step 6.17.4 — Stores POST set-status

**Status.** DONE-LOCAL (2026-05-18). Shipped as `POST /api/v1/stores/{store_id}/set-status` (URL revised from the original draft `PATCH .../change_status` — POST verb + hyphenated set-status name match the project-wide convention per the openapi.json enumeration; see LD6). See `docs/implementation-steps/step-6_17_4-stores-set-status-2026-05-18.md`. **Owner.** CLAUDE_CODE **Blocked by.** 6.17.3.

**Goal.** `POST /stores/{store_id}/set-status` with `target_status` in body. Single endpoint covers all transitions across the 4 states (OPENING / ACTIVE / INACTIVE / CLOSED). Handles `ck_stores_closed_consistency` audit-triplet via repo logic.

**RBAC.** Endpoint gated via `require(...)`; mandatory-gate-discipline test runs in CI.

**Scope in.**

- `StoreChangeStatusRequest` schema.
- `StoresRepo.transition(store_id, target_status, actor) -> TransitionResult`; state matrix cells locked at impl design; reuses `TransitionResult` enum from tenants Repo.
- Audit-triplet handling: into-CLOSED populates `closed_at` + `closed_by_*`; out-of-CLOSED nulls them per the CHECK constraint; between non-CLOSED states no triplet movement.
- Router endpoint; `InvalidStateTransitionError` reused (409 `INVALID_STATE_TRANSITION`).
- Matrix-complete tests; smoke + curl; docs; OpenAPI regen.

**Scope out.** Suspend/activate verb-per-URL endpoints (consolidated into change_status by deliberate divergence from tenants).

**Acceptance.** Allowed transitions return 200 + new state; rejected transitions return 409; audit-triplet correctness on into-CLOSED and out-of-CLOSED; mandatory-gate-discipline test green.

**Coordination.** Frontend integrates within 24 hours of dev deploy.

**Rough effort.** ~2 hours.

---

### Step 6.18 - Role edit (PATCH /api/v1/roles/{role_id})

**Status.** DONE-LOCAL (2026-05-19). All three sub-steps shipped.
**Owner.** CLAUDE_CODE (impl) + HUMAN (Excel + Cloud SQL).
**Blocked by.** None.

**Goal.** Ship the role-edit feature: SUPER_ADMIN can edit a role's name,
description, and permission set via PATCH /api/v1/roles/{role_id}. Also
adds GET /api/v1/roles/{role_id} (detail) for the edit screen UX.

**RBAC posture.** PATCH gated by ADMIN.ROLES.OVERRIDE.GLOBAL (new
permission introduced in 6.18.1). GET stays GATE_EXEMPT per FN-AB-30
deferral. Only SUPER_ADMIN holds OVERRIDE.GLOBAL initially.

**Sub-steps.** 6.18.1 (catalogue seed) -> 6.18.2 (GET detail) -> 6.18.3 (PATCH).

------

### Step 6.18.1 - Catalogue seed delta (ADMIN.ROLES.OVERRIDE.GLOBAL)

**Status.** DONE-LOCAL + CLOUD SQL APPLIED (2026-05-19).
**Owner.** HUMAN (Excel + Cloud SQL inline UPSERT) + CHAT (SQL drafting).
**Blocks.** 6.18.2.

**Goal.** Add ADMIN.ROLES.OVERRIDE.GLOBAL permission and grant to SUPER_ADMIN.

**Scope in.** Excel +1 permission row (p40); +1 role_permissions row
(r_super_admin holds p40); test count updates (permissions 35 -> 36,
role_permissions 131 -> 132). Cloud SQL inline UPSERT applied via Cloud
SQL Studio (operator-driven; uses anjali@ithina.ai as the
created_by_user_id since bootstrap@ithina.ai does not exist in cloud).

**Scope out.** No application code.

**Acceptance.** Local + Cloud SQL hold the new permission and grant.
Test suite green.

------

### Step 6.18.2 - GET /api/v1/roles/{role_id} detail endpoint

**Status.** DONE-LOCAL (2026-05-19).
**Owner.** CLAUDE_CODE.
**Blocks.** 6.18.3 (PATCH endpoint).

**Goal.** Ship the self-contained role-detail endpoint backing the role-edit
screen UX. Frontend renders the edit form from one URL: role metadata + held
permissions + grantable permissions (catalogue minus held; TENANT-audience
roles exclude `scope='GLOBAL'` per audience-scope coherence).

**Scope in.**

- `schemas/permission.py`: +`PermissionDetail` (4 enum slots + 4 display labels).
- `schemas/role.py`: +`RoleDetail` (role metadata + `permissions` + `available_permissions`).
- `schemas/__init__.py`: re-export both.
- `repositories/roles.py`: +`get_detail_by_id` method + module-level
  `_select_permissions_with_labels` helper (used twice, once for held,
  once for available; raw `text()` with 4 LEFT JOINs on `core.lookups`,
  schema-qualified per CSD-03).
- `routers/v1/rbac.py`: +`get_role` handler at `GET /roles/{role_id}`.
  Reuses `_audience_filter_for` + `RoleNotFoundError`.
- `auth/gate_allowlist.py`: append `/api/v1/roles/{role_id}` to
  `GATE_EXEMPT_PATHS` (GET stays exempt per FN-AB-30 deferral; PATCH
  in 6.18.3 will gate on ADMIN.ROLES.OVERRIDE.GLOBAL).
- `tests/integration/test_rbac_router.py`: +8 D-series tests (6 load-bearing:
  D1 envelope contract; D2 same-audience read; D3 cross-audience 404
  via audience-as-app-layer-RLS; D5 server-side label resolution; D6
  PLATFORM available_permissions CAN include GLOBAL; D7 TENANT
  available_permissions EXCLUDES GLOBAL per LD2).
- `scripts/smoke_curl.sh`: +3 assertions (PJWT role detail; TJWT
  TENANT-role detail; TJWT PLATFORM-role -> 404). Counter 55 -> 58.
- `scripts/test_endpoints.sh` + `test_endpoints_cloud.sh`: +3 matrix
  entries per caller for the new endpoint, mirroring the existing E3
  audience-gate pattern.
- `docs/endpoints/rbac.md`: append E7 section in 8-section format.
- `docs/endpoints/openapi.json`: regen (+1 path, +2 schemas).

**Scope out (deferred with triggers).**

- PATCH endpoint: Step 6.18.3.
- Read-gate hardening (FN-AB-30 revisit): defer with Stage 2 close.
- Label promotion onto `PermissionRead` (E2/E3): defer; `PermissionDetail`
  is separate per LD3 so existing wire shapes stay frozen per D-31.
- Pagination on `available_permissions`: not needed at v0 catalogue size
  (36 permissions today; ~50 at scale).

**Acceptance.** All 8 D-series tests pass; load-bearing 6 verified by id.
Full suite 627 -> 635 (+8). mypy strict clean (76 src files). check_setup
36/36. Smoke 55 -> 58 PASS with fresh JWTs. OpenAPI regenerated.
Per-resource regression checkpoint clean (only `test_rbac_router.py`
delta).

**Coordination.** Cloud deploy batched with 6.18.3 at Phase 6 deploy
(no DDL, no migration, pure code addition).

**Rough effort.** ~3 hours including pre-flight + verification + docs.

------

### Step 6.18.3 - PATCH /api/v1/roles/{role_id} role-edit endpoint

**Status.** DONE-LOCAL (2026-05-19).
**Owner.** CLAUDE_CODE.
**Blocks.** Step 6.18 root (closes the sub-series).

**Goal.** Ship the role-edit write endpoint. PLATFORM-only by gate-tuple
construction (`ADMIN.ROLES.OVERRIDE.GLOBAL` + `audience="PLATFORM"`).
Two-layer OVERRIDE.GLOBAL invariant guards the platform-admin bootstrap
against zeroing out active holders. SUPER_ADMIN locked from PATCH in v0
(LD12 + LD20). Diff-replace on role_permissions preserves audit history
on unchanged rows (LD5).

**Scope in.**

- `schemas/role.py`: +`RoleUpdateRequest` (extra='forbid'; name +
  description + permission_ids only).
- `schemas/__init__.py`: re-export.
- `errors.py`: +5 ClientError (`RoleArchivedError`,
  `InvalidPermissionError`, `AudienceScopeMismatchError`,
  `LastOverrideHolderError`, `SuperAdminProtectedError`) +
  1 ServerError (`InternalInvariantViolationError`).
- `repositories/roles.py`: +`OVERRIDE_GLOBAL_CODE` constant,
  +`_count_override_global_active_holders` helper,
  +`_resolve_override_global_permission_id` helper, +`RolesRepo.update`
  method (LD17 order-of-operations).
- `routers/v1/rbac.py`: +`patch_role` handler with
  `Depends(require(ADMIN, ROLES, OVERRIDE, GLOBAL, audience="PLATFORM"))`
  gate; +local `_actor_type_from_auth` copy (third in the codebase;
  FN-AB tracks promotion).
- `tests/integration/test_gate_discipline.py`: +1 entry in
  `_PLATFORM_ONLY_WRITE_ROUTES` (`("PATCH", "/api/v1/roles/{role_id}")`).
- `tests/integration/test_rbac_writes_router.py`: NEW +30 W-series tests
  (23 LOAD-BEARING: W1, W3-W5, W7-W11, W13-W16, W18-W26, W29-W30 +
  W7-W10 parametrized).
- `tests/integration/test_rbac_writes_repo.py`: NEW +6 RW-series
  repo-direct tests (invariant edge cases + diff preservation +
  rollback).
- `scripts/smoke_curl.sh`: +5 probes (PATCH happy, forbidden field 422,
  TENANT audience-deny 403, unknown 404, SUPER_ADMIN_PROTECTED 409).
  Counter 58 -> 63.
- `scripts/test_endpoints.sh` + `test_endpoints_cloud.sh`: +5 entries in
  a new Phase 4h block mirroring the smoke probes.
- `docs/endpoints/rbac.md`: append E8 section in 8-section format.
- `docs/endpoints/openapi.json`: regen (+0 paths — the
  `/api/v1/roles/{role_id}` path already exists from Step 6.18.2; the
  PATCH method is added; +1 schema RoleUpdateRequest).

**Scope out (deferred with triggers).**

- Status transitions on roles (activate/deactivate API): defer to a
  separate step. ARCHIVED roles refuse PATCH (LD3).
- Custom-role creation: deferred (BUILD_PLAN scope-out).
- SUPER_ADMIN editability via API: v1 promotion deferred per FN-AB.
- FN-AB-30 read-gate hardening: still deferred. GET stays GATE_EXEMPT.
- Concurrent-edit race-condition mitigation (SERIALIZABLE / SELECT FOR
  UPDATE): accepted at v0 per FN-AB-CRITICAL. Layer 2 tripwire is the
  only mitigation; race acknowledged as acceptable at v0 scale.
- Runtime permission catalogue API (Direction C): deferred per
  FN-AB-CRITICAL-2.
- Audit log integration: Step 6.2 (deferred). PATCH operations not yet
  audit-logged; will surface when 6.2 ships.

**Acceptance.** All 30 W-series + 6 RW-series tests pass; 23 load-bearing
W-tests verified by id. Full suite 635 -> 671 (+36). mypy strict clean
(76 src files). check_setup 36/36. Smoke 58 -> 63 PASS with fresh JWTs.
OpenAPI regenerated. Per-resource regression checkpoint clean
(test_rbac_router.py unchanged at 32; only the 2 new files add tests).

**Coordination.** Cloud deploy of 6.18.2 + 6.18.3 batched at next Phase 6
deploy (no DDL, no migration, no Cloud SQL catalogue update — 6.18.1
already applied via inline UPSERT). Standard Cloud Run image deploy.

**Rough effort.** ~4 hours including pre-flight + verification + docs +
test-cleanup discipline.

---

## 6.20 Bug Fixes


### Step 6.20.1 — TenantsRepo.create provisions tenant-root org_node

**Status.** DONE-LOCAL (2026-05-18). Shipped as a single commit on `main`; extends `TenantsRepo.create` to insert the tenant-root `org_nodes` row in the same transaction as the existing `tenants` + `tenant_module_access` writes. New pure-function helper `slug_for_tenant_root` derives `(code, path)` from `display_code` or `name`. Empty-slug input raises 422 `INVALID_TENANT_NAME_FOR_SLUG`. See `docs/implementation-steps/step-6_20_1-tenants-create-org-node-root-2026-05-18.md`. **Owner.** CLAUDE_CODE. **Blocked by.** None (post-6.17.4 bug fix).

**Goal.** Restore the invariant "every tenant has exactly one `(node_type='TENANT', parent_id IS NULL)` org_node" at the `POST /api/v1/tenants` seam. Pre-fix, POST succeeded but every downstream endpoint gated on `get_tenant_anchor` (GET detail, PATCH, suspend, activate, org-tree, module-access enable/disable) 404'd POST-created tenants because the anchor lookup missed.

**Scope in.**

- `errors.py`: +`InvalidTenantNameForSlugError` (422 `INVALID_TENANT_NAME_FOR_SLUG`).
- `repositories/tenants.py`: +`slug_for_tenant_root` pure helper; extend `create()` with org_node INSERT between tenants INSERT and module loop. Refined LD2: slug call BEFORE tenants INSERT so a 422 leaves no partial state.
- Unit tests: `tests/unit/test_tenant_root_slug.py` NEW (10 tests covering LD3 rule).
- Repo invariant tests: `test_tenants_repo_writes.py` +5 tests including the load-bearing `test_create_inserts_tenant_root_org_node`.
- Router roundtrip: `test_tenants_writes_router.py` +1 test (`test_post_then_get_roundtrip`) locking the end-to-end fix.
- Smoke + endpoint scripts: +1 assertion each (POST then GET same tenant returns 200).
- Docs: architecture.md Appendix A.3 (tenant create transaction shape + slug rule), tenants.md POST section side-effect note, openapi.json regen (no-op — no new endpoints).
- BUILD_PLAN.md: this entry. CLAUDE.md: pointer + FN-AB-NN (slug-truncation collision risk).
- Pre-fix orphan cleanup: 10 cloud + 1 local tenants without org_nodes deleted in single transactions each (2026-05-18 pre-step).

**Scope out (deferred with triggers).**

- Backfill org_nodes for production tenants — done (cleanup pre-fix).
- Slug-conflict resolution beyond truncation — structurally unreachable at tenant-root insert (FN-AB tracks).
- Editorial short-codes for POST-created tenants (seed-curated form like `BUC-EES` vs mechanical `BUC-EE-S`) — defer to future PATCH-on-tenant-root surface.
- Tightening regex/validation on `code` — current slug rule produces DDL-compliant codes.

**Acceptance.** All 16 prior + 21 new tests in `test_tenants_repo_writes.py` pass; all 32 prior + 1 new in `test_tenants_writes_router.py`; all 10 new slug unit tests pass. Per-resource regression checkpoint clean. Smoke `smoke_curl.sh` POST then GET roundtrip returns 200. mypy strict clean. No DDL changes; no migration.

**Coordination.** Frontend unblocked on tenant management within minutes of dev deploy. Cloud deploy via standard `./scripts/deploy-cloud-run.sh` (no `--migrate` needed).

**Rough effort.** ~3 hours including pre-flight + docs + retro.

---


### Step 6.20.2 — /me/can-do ltree input validation

**Status.** DONE-LOCAL (2026-05-19). Shipped as a single commit on `main`; adds a Pydantic `pattern=` + `max_length=` validator to the `target_anchor` Query param on `GET /api/v1/me/can-do`. Pre-fix, a caller-supplied non-ltree value (e.g., UUID with hyphens) reached `CAST(:target_anchor AS ltree)` in `_has_permission_tenant`, raised `psycopg.errors.SyntaxError`, and bubbled to the generic 500 `INTERNAL_ERROR` envelope. Post-fix, FastAPI returns 422 BEFORE the gate dependency runs. See `docs/implementation-steps/step-6_20_2-can-do-ltree-validation-2026-05-19.md`. **Owner.** CLAUDE_CODE. **Blocked by.** None (post-6.20.1 bug fix; resolves FN-AB-61).

**Goal.** Surface malformed `target_anchor` input as a clean 422 from Pydantic instead of a 500 from psycopg's ltree CAST. Mirrors the existing pattern validator at `schemas/org_node.py:271` for org_node codes.

**Scope in.**

- `routers/v1/me.py`: extend the `target_anchor` Query declaration with `pattern=r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$"` (multi-label ltree path grammar per LD1) + `max_length=1024` (LD3) + expanded description.
- `schemas/org_node.py`: correct the backwards docstring claim at line 275 ("No underscores (ltree label restriction)" was the wrong direction; underscores are the ltree-label form, hyphens are the org_node-code form, `_path_label` is the bridge). Pattern itself unchanged.
- `tests/integration/test_me_router.py`: +1 new test function `test_mc8_malformed_target_anchor_returns_422` with 6 assertion blocks (MC8a UUID-with-hyphens, MC8b leading-dot, MC8c trailing-dot, MC8d consecutive-dots, MC8e whitespace, MC8f empty-string). LOAD-BEARING: MC8a-MC8e. MC8f correctness-only. Test shape mirrors `test_v7_invalid_code_format_pydantic_422` at `tests/integration/test_org_tree_writes_router.py:485-521` (LD6).
- Smoke + endpoint scripts: +1 assertion each (63 -> 64 in `smoke_curl.sh`; new Phase 4i block in `test_endpoints.sh` + `test_endpoints_cloud.sh`).
- Docs: `docs/endpoints/openapi.json` regen (Query param gains `pattern` + `maxLength`). No new schemas; no new paths.
- CLAUDE.md: pointer to step doc + FN-AB-61 marked RESOLVED.

**Scope out (deferred with triggers).**

- Shared `LtreePath` Pydantic type promotion: deferred per FN-AB-61 option (c). Single call site today (investigation Bucket 2 confirmed). Revisit trigger documented when a second user-supplied-ltree endpoint surfaces.
- Other endpoint hardening: not needed. `/me/can-do` is the sole caller-supplied-ltree surface; the other 6 ltree CAST sites consume `_path_label`-derived or DB-read strings.
- Generic 500-envelope SQL-error refactor: out of scope. ServerError's anti-information-disclosure posture is correct per the CLAUDE.md error model; Pydantic-layer validation is the right layer.
- Cloud SQL changes: none (pure code fix; no DDL, no migration, no catalogue update).

**Acceptance.** Pre-fix repro: 500 INTERNAL_ERROR on malformed target_anchor under TENANT JWT. Post-fix: 422 with Pydantic detail envelope identifying `target_anchor` as the failing field. Full suite 671 -> 672 (+1: test_mc8). Smoke 63 -> 64. mypy strict clean. No DDL changes; no migration.

**Coordination.** Cloud deploy batched with 6.18.2 + 6.18.3 + 6.20.2 at next Phase 6 deploy. Cloud incident at v0.1.17 (revision admin-backend-00018-46f) is the trigger.

**Rough effort.** ~2 hours including pre-flight + docs + retro.

---


### Step 6.20.3 — RBAC structural enforcement triggers

**Status.** DONE-LOCAL (2026-05-20). Shipped as a single commit on `main`; adds three Postgres triggers via Alembic migration `5e22b2ca13cc` closing structural enforcement gaps surfaced by the 2026-05-19 investigation. (1) `tg_role_permissions_audience_scope_coherence` rejects (TENANT-audience role x GLOBAL-scope permission) on INSERT or UPDATE OF role_id/permission_id; backstops Step 6.18.3 LD17. (2) `tg_role_permissions_protect_super_admin_override` pins the (SUPER_ADMIN x ADMIN.ROLES.OVERRIDE.GLOBAL) grant from DELETE. (3) `tg_roles_protect_super_admin` pins SUPER_ADMIN role status/code/audience and blocks DELETE; name and description remain editable. See `docs/implementation-steps/step-6_20_3-role-audience-scope-trigger-2026-05-19.md`. **Owner.** CLAUDE_CODE (impl) + HUMAN (manual pre-check, Cloud SQL migration). **Blocked by.** None.

**Goal.** Close 3 structural enforcement gaps surfaced by 2026-05-19 investigation. App-layer checks give clean 422 envelopes for API callers; triggers backstop direct-SQL, seed-loader, and any future-endpoint bypass paths.

**Scope in.**

- New Alembic migration `migrations/versions/5e22b2ca13cc_step_6_20_3_rbac_structural_triggers.py`: 3 `CREATE OR REPLACE FUNCTION` + 3 `CREATE TRIGGER` blocks. Schema-qualified per `current_schema()` at migration time, mirroring `a0982a86985b` (CSD-03 fix posture). Reversible downgrade: 3 `DROP TRIGGER IF EXISTS` + 3 `DROP FUNCTION IF EXISTS` in reverse order.
- `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql`: 3 trigger DDL blocks appended adjacent to the relevant tables. Trigger 3 sits after `tg_roles_set_updated_at` (the `roles` table). Triggers 1+2 sit after the `role_permissions` table (after `ix_role_permissions_permission`). Unqualified identifiers, matching surrounding precedent.
- `tests/integration/test_rbac_audience_scope_triggers.py` NEW: 17 DB-direct tests (12 LOAD-BEARING). T1-T7 cover Trigger 1 (audience-scope coherence on INSERT + UPDATE OF role_id / permission_id, with negative-case verification for valid combinations and audit-column-only UPDATEs); T8-T10 cover Trigger 2 (DELETE pin); T11-T17 cover Trigger 3 (UPDATE of status/code/audience rejected; name/description allowed; DELETE rejected; non-SUPER_ADMIN UPDATEs untouched).
- `BUILD_PLAN.md`: this sub-step block.
- `CLAUDE.md`: one-line pointer to step doc + cross-reference note on Step 6.18.3 LD17.
- `docs/implementation-steps/step-6_20_3-role-audience-scope-trigger-2026-05-19.md` NEW: Mental Model + Implementation Plan + Retro.

**Scope out (deferred with triggers).**

- App-layer changes: NONE. Step 6.18.3 LD17 audience-scope check stays untouched (defense in depth).
- App-layer catch of `ProgrammingError` from Trigger 1: NOT added. Pure tripwire pattern; if a trigger fires from an API path, that's an LD17 bug (mirrors LD8 Layer 2 tripwire behavior at 6.18.3).
- Cross-table cascade invariants (full LAST_OVERRIDE_HOLDER enforcement; last-active-platform-user pinning): deferred. App-layer Step 6.18.3 two-layer invariant handles OVERRIDE.GLOBAL last-holder.
- Catalogue-extension trigger for runtime permission additions (FN-AB-60): deferred.
- Data migration: NONE. Operator manually verifies zero pre-existing violations in seed Excel + local DB + Cloud SQL before applying migration. If any violation found, operator cleans first.
- AI-RBAC-01 comment in `rbac_v3.sql` amended to reference the new DDL backstop: deferred (Block 2a adjacent improvement; left optional unless operator opts in. Captured as FN-AB-62.)

**Acceptance.** Migration applies cleanly; round-trip (`upgrade head` -> `downgrade -1` -> `upgrade head`) verified clean. 17 new trigger tests pass. Full suite 672 -> 689 (+17). raw_ddl + migration in sync. mypy strict clean (76 source files; counted, was 73 in CLAUDE.md but the count had drifted pre-step). check_setup 36/36.

**Coordination.** DDL change. Migration runs via the standard `--migrate` path on next Cloud SQL deploy. Cloud deploy batched with 6.18.2 + 6.18.3 + 6.20.2 + 6.20.3 at next Phase 6 deploy.

**Rough effort.** ~3 hours including pre-flight + migration + tests + docs + retro.

---

### Step 6.21 — Write-surface coupling fixes (org-tree + stores)

**Status.** PARTIAL (6.21.1 DONE-LOCAL; 6.21.2 TODO). Family block; see sub-steps below.

**Goal.** Close two frontend-vs-backend coupling gaps surfaced by the 2026-05-20 write-surface coupling investigation (`docs/investigations/2026-05-20-write-surface-coupling.md`):

- **Gap A (Step 6.21.1, DONE-LOCAL).** GET /org-tree did not expose the tenant-root `org_nodes.id`. Frontend synthesised a TENANT row using `data.tenant_id` (the `tenants.id` UUID) and POSTed it as `parent_id` on Add Org Node; backend correctly rejected (two independent UUIDs). Fix: surface `tenant_root_id` / `tenant_root_code` / `tenant_root_path` as additive top-level fields on `OrgTreeResponse`.

- **Gap B (Step 6.21.2, TODO).** Store ↔ STORE-type org_node coupling. Out of scope for 6.21.1; larger work parked for a separate sub-step.

**Coordination.** 6.21.1 + 6.21.2 can ship independently. Frontend's coordinated Add Org Node release benefits from 6.21.1 landing in cloud first.

### Step 6.21.1 — Expose tenant_root_id / tenant_root_code / tenant_root_path on GET /org-tree

**Status.** DONE-LOCAL (2026-05-20). Shipped as a single commit on `main`. Adds three additive top-level fields on `OrgTreeResponse`. Pure code addition: no new SQL, no DDL, no migration, no seed update, no permission catalogue change. Detail: `docs/implementation-steps/step-6_21_1-org-tree-expose-tenant-root-id-2026-05-20.md`. **Owner.** CLAUDE_CODE (impl). **Blocked by.** None.

**Goal.** Surface the tenant-root org_node's id/code/path on the GET /org-tree response so the frontend has the correct UUID for use as `parent_id` on POST /org-tree when the synthesized TENANT row is selected.

**Scope in.**

- `src/admin_backend/schemas/org_node.py`: +3 required fields on `OrgTreeResponse` (`tenant_root_id: UUID`, `tenant_root_code: str`, `tenant_root_path: str`); class docstring updated; `tree` field-level description updated to reflect that the tenant-root id/code/path now appear as separate top-level fields.
- `src/admin_backend/routers/v1/org_tree.py`: handler extracts the TENANT-typed row from the existing `OrgNodesRepo.list_active_with_child_counts` result and populates the 3 new fields on `OrgTreeResponse`. No new SQL. No new repo method.
- `tests/integration/test_org_tree_router.py`: +3 router tests (T22 PLATFORM happy path; T23 TENANT OWNER own-tenant; T24 empty-descendants tenant); T1's exact-set keys assertion updated.
- `scripts/smoke_curl.sh`, `scripts/test_endpoints.sh`, `scripts/test_endpoints_cloud.sh`: +1 assertion each that the response carries `tenant_root_id` / `tenant_root_code` / `tenant_root_path`. Cloud assertion is strict against Buc-ee's (`tenant_root_code = BUC-EES`, `tenant_root_path = buc_ees`).
- `docs/endpoints/org-tree.md`: GET section + Response 200 sample envelopes updated.
- `docs/endpoints/openapi.json`: regen produces 3 new field blocks and 2 description updates on `OrgTreeResponse`.

**Scope out.**

- Gap B (store ↔ STORE-type org_node coupling). Deferred to Step 6.21.2.
- POST /org-tree, PATCH /org-tree, or any other endpoint contract changes.
- DDL, migration, seed update, permission catalogue change.

**Acceptance.** All new tests pass; existing test suite untouched (766 -> 769; +3). mypy strict clean (82 source files). check_setup 36/36. Local smoke 67 -> 68 (+1). OpenAPI regen produces the 3 new fields with no unrelated description drift.

**Coordination.** Cloud deploy required for the frontend fix to land; batchable with 6.21.2 at next Phase 6 deploy. No DDL, no migration, no env-var, no IAM, no secret, no seed change.

**Rough effort.** ~2 hours including pre-flight + impl + tests + smoke + docs + retro.

### Step 6.21.2 — Store ↔ org_node atomic-pair write surface

**Status.** DONE-LOCAL (2026-05-21). Shipped as a single commit on `main`. Closes Gap B from the 2026-05-20 write-surface coupling investigation. POST `/api/v1/stores` becomes the atomic-pair entry point: server creates both the `stores` row and the paired STORE-type `org_nodes` row in one transaction. PATCH cascades `name`/`store_code`/`parent_org_node_id` to the paired org_node. set-status cascades store status to the paired org_node's status + `archived_*` triplet. POST `/org-tree` rejects `node_type='STORE'`; PATCH `/org-tree` on STORE-type targets rejects shared fields `name` and `code` (reparent stays allowed). DDL migration `34f515cbc63a` tightens `core.stores.org_node_id` to NOT NULL. Detail: `docs/implementation-steps/step-6_21_2-stores-org-node-atomic-paired-write-2026-05-21.md`. **Owner.** CLAUDE_CODE (impl).

**Goal.** Close Gap B from the 2026-05-20 write-surface coupling investigation. Establish "two tables = one entity, atomic API at one endpoint" as a codified pattern (architecture.md § A.4) with this seam as the second concrete instance (tenant + tenant-root org_node from Step 6.20.1 is the first).

**Scope in.**

- `src/admin_backend/schemas/store.py`: `StoreCreateRequest` removes `org_node_id`, adds required `parent_org_node_id`. `StorePatchRequest` adds optional `parent_org_node_id`.
- `src/admin_backend/schemas/org_node.py`: `OrgNodeCreateRequest._reject_forbidden_node_types` rejects STORE in addition to TENANT.
- `src/admin_backend/errors.py`: adds `OrgNodeFieldNotAllowedForTypeError` (422); removes `OrgNodeNotForStoreError`.
- `src/admin_backend/repositories/stores.py`: module-level `_org_nodes_repo` singleton + `STORE_STATUS_TO_ORG_NODE_STATUS` map; `_check_parent_node_for_store` replaces `_check_org_node_for_store`; `create` performs atomic paired write; `update` cascades shared fields; `transition` cascades status via `OrgNodesRepo.set_status`. Method signatures take `auth: AuthContext` (replacing `actor_user_*` pair).
- `src/admin_backend/repositories/org_nodes.py`: new `set_status` method (archived_* triplet symmetric to stores closed_* triplet).
- `src/admin_backend/routers/v1/stores.py`: handlers updated to pass `auth=auth` to repo methods.
- `src/admin_backend/routers/v1/org_tree.py`: `edit_org_node` adds pre-fetch + field-allowlist check on STORE-type targets.
- `migrations/versions/34f515cbc63a_step_6_21_2_stores_org_node_id_not_null.py`: ALTER TABLE NOT NULL migration, reversible.
- Test churn per Deviation #6: rename `_base_create_kwargs` parameter; 17+ test sites get `parent_org_node_id`; C5/C6 assert `ParentNodeNotFoundError`; C7 deleted; RC10 deleted; 11 new PW tests; 5 new W tests; 2 new SS tests; 4 new V8/E13/E14/E16 tests; 2 new RT7/RT8 tests on OrgNodesRepo.set_status.
- Smoke scripts (smoke_curl.sh, test_endpoints.sh, test_endpoints_cloud.sh) updated for the new body shape and Step 6.21.2 ot_flow shape (DEPARTMENT-typed add instead of STORE).
- `docs/endpoints/stores.md`, `docs/endpoints/org-tree.md`, `docs/endpoints/openapi.json`: contract-change banner + per-section updates.
- `docs/architecture.md`: § A.4 (two-table-one-entity general principle) and § A.5 (store seam specifics) inserted after § A.3.
- CLAUDE.md: Completed entry; D-36 codifying the two-table-one-entity pattern.

**Scope out.**

- Pre-deploy SQL cleanup of dev orphans (operator workflow at Phase 6).
- Frontend changes (breaking wire-contract change; coordinated at Phase 6 deploy timing).
- New permission tuples (existing `ADMIN.STORES.CONFIGURE.TENANT` covers the whole atomic write per architecture.md § A.4 RBAC rule).
- Retroactive backfill of dev orphan stores or STORE-type org_nodes.

**Acceptance.** All new tests pass; existing test suite passes (regressions resolved per Deviation #6). Migration round-trip clean. mypy strict clean. check_setup 36/36. Local smoke 69/69 (was 68; +1 for org_node_id-in-response). Local DB had zero pre-existing NULL `org_node_id` rows (pre-flight Check #12).

**Coordination.** Cloud deploy via `--migrate` (depends on the new `34f515cbc63a` migration). Pre-deploy cleanup of Cloud SQL orphans (Appendix A of the impl prompt) is mandatory before the migration runs.

**Rough effort.** ~6 hours including pre-flight + design reframings + impl + tests + scripts + docs + retro.

---


### Carryover from Stage 2 (must complete before Stage 6)

(empty for now; populates if items get deferred mid-stage.)

---

# Stage 3 — Auth0 integration (dev)

**Status.** Not started.

**Stage boundary.** Auth0 integration complete in dev; admin-backend authenticates real Auth0-issued tokens; stub auth retired or feature-flagged off.

**Deployment model.** Continuous manual deploy to Cloud Run dev as steps land.

**Note on numbering.** Step 8.3 (Auth0 swap) belongs to Stage 3 even though its number sits in the 8.x cluster. The other 8.x steps (8.0, 8.1.1, 8.1.2, 8.1.3, 8.2) are Stage 6 (production GCP provisioning + GKE prod deploy). The 8.x numbering split is a real consequence of "keep numbering as-is" + "regroup by stage."

---

> Note: Stage 3 scope is currently the vanilla Auth0 swap (Step 8.3).
> An expansion under consideration — admin-backend as the
> platform-wide auth gate — is flagged as FN-AB-22 in CLAUDE.md, to
> be settled at Stage 3 kickoff.
## Step 8.3 — Auth0 swap

**Status.** TODO
**Owner.** CLAUDE_CODE

**Goal.** Replace stub auth with Auth0 in dev environment. (Previously labeled "(conditional)" — under the new Stage model, Auth0 is in v0 so the qualifier no longer applies.)

**Scope in.**
- Implement `src/admin_backend/auth/auth0.py`. JWKS-based RS256 verification with caching.
- Toggle via env var: `AUTH_CLIENT_MODE=AUTH0` vs `STUB`.
- Update Secret Manager with Auth0 JWKS URL, issuer, audience.
- Re-deploy to Cloud Run dev.
- Verify Auth0-issued JWT verifies. Stub disabled.

**Scope out.**
- Production deployment (Stage 6 territory).

**Acceptance criteria.**
- Auth0-issued JWT works against dev backend.
- Stub mode disabled in dev env vars.

**Coordination.**
- Auth0 owner provides JWKS URL, iss, aud.

**Rough effort.** 60-90 min for the swap itself.

**Additional sub-steps may emerge.** When Stage 3 work begins, expect new sub-steps for JWKS rotation handling, Auth0-tenant configuration (custom claims, callback URLs, social connections), integration tests against real Auth0, error-mapping verification, cookie/session domain configuration. Not enumerated upfront per the "minimum cascading changes" principle.

---

### Carryover from Stage 3 (must complete before Stage 6)

(empty for now.)

---

# Stage 4 — Late scope additions from business (dev)

**Status.** Not started. May remain empty.

**Stage boundary.** Any late-scope items from business or customer requests that surface between Stage 3 completion and Stage 5 start. Closed empty if none surface.

**Deployment model.** Continuous manual deploy to Cloud Run dev as items land.

(No items currently planned. This stage exists as a container for scope that emerges late. Items added here get step numbers in the next available slot — likely 6.17+ if RBAC/permissions-related, or appropriate for the resource.)

---

### Carryover from Stage 4 (must complete before Stage 6)

(empty.)

---

# Stage 5 — Staging / UAT (cross-system integration with other Ithina platforms)

**Status.** Not started.

**Stage boundary.** Cross-system integration validated against other Ithina platforms (Pricing OS, DIS, etc.), UAT signed off by stakeholders, rework from staging-discovered issues either completed or formally moved to Candidate scope.

**Deployment model.** Deployed to staging environment (separate from Cloud Run dev).

**Additional sub-steps may emerge.** When Stage 5 work begins, expect new sub-steps for cross-system integration tests, UAT validation steps with internal stakeholders, rework / fixes captured ad-hoc as discovered.

---

## Step 7.1 — Critical-path test suite

**Status.** TODO
**Owner.** CLAUDE_CODE

**Goal.** All critical-path tests defined in CLAUDE.md present and passing.

**Scope in.**
- `tests/integration/test_critical_path.py`:
  - Cross-tenant read returns zero rows on every endpoint that filters by tenant.
  - Tenant mismatch (JWT vs path) returns 400 with `code=TENANT_CONTEXT_MISMATCH`.
  - Suspended user JWT returns 401.
  - Permission cascade: assignment at Region grants access to descendant Stores' data; sibling Region's data not accessible.
  - Org tree descendants returns correct subtree.
  - Audit log filters work as expected.
- Fix anything these tests surface.

**Scope out.**
- Comprehensive coverage (post-v0).
- Performance tests.

**Acceptance criteria.**
- All critical-path tests pass.
- mypy strict clean.

**Coordination.**
- None.

**Rough effort.** 90-120 min.

---

## Step 7.2.1 — Structured JSON logs + app /metrics endpoint

**Status.** TODO
**Owner.** CLAUDE_CODE

**Goal.** App outputs JSON logs and exposes Prometheus metrics endpoint.

**Scope in.**
- Configure `python-json-logger` in `main.py` startup.
- Each log line includes timestamp, level, request_id, tenant_id, user_id, route, method, status, latency_ms, message.
- Apply logging discipline rules from CLAUDE.md (one INFO per request, no DEBUG in committed code, no payload dumps).
- Add `prometheus-fastapi-instrumentator`. Default metrics. `/metrics` endpoint, no auth.
- Verify locally: `curl localhost:8000/metrics` returns Prometheus-format text.

**Scope out.**
- Local Prometheus server (not needed; just verify the endpoint).
- Kubernetes annotations (Step 7.2.2).
- Cloud verification (Step 7.2.3).

**Acceptance criteria.**
- All logs are valid JSON.
- `/metrics` returns Prometheus-format output.
- Per-request INFO logs include all standard fields.

**Coordination.**
- None.

**Rough effort.** 45-60 min.

---

## Step 7.2.2 — Kubernetes annotations for managed Prometheus

**Status.** TODO
**Owner.** CLAUDE_CODE

**Goal.** GCP managed Prometheus discovers and scrapes the deployed app's `/metrics` endpoint.

**Scope in.**
- Update `k8s/dev/deployment.yaml` (and `k8s/prod/` later) with annotations:
  ```yaml
  monitoring.googleapis.com/scrape: "true"
  monitoring.googleapis.com/path: "/metrics"
  monitoring.googleapis.com/port: "8000"
  ```
  Or a `PodMonitoring` resource if that's the cluster's pattern.
- Re-deploy.

**Scope out.**
- GCP-side enabling of managed Prometheus (GCP-helper handles).
- Verification (Step 7.2.3).

**Acceptance criteria.**
- Manifests applied without error.
- Pods Running with scrape annotations visible.

**Coordination.**
- Confirm with GCP-helper that managed Prometheus is enabled on the cluster.

**Rough effort.** 30 min.

---

## Step 7.2.3 — Verify metrics in GCP Console

**Status.** TODO
**Owner.** HUMAN (you)

**Goal.** Confirm metrics flow from app → managed Prometheus → Cloud Monitoring.

**Scope in.**
- GCP Console → Monitoring → Metrics Explorer.
- Search for admin-backend metrics (e.g., `prometheus.googleapis.com/http_requests_total/counter`).
- Confirm metrics scraped within last 5 minutes.
- Optionally: build a basic dashboard for request rate + p95 latency.
- Verify Cloud Logging shows JSON logs from cloud env.

**Scope out.**
- Alerting setup (post-v0).
- OpenTelemetry tracing.

**Acceptance criteria.**
- Metrics visible in Cloud Monitoring.
- Logs visible in Cloud Logging with parsed JSON fields.

**Coordination.**
- None.

**Rough effort.** 30 min.

---

### Carryover from Stage 5 (must complete before Stage 6)

(empty for now; this is where staging-discovered bugs that block prod cutover would live.)

---

# Stage 6 — Production cutover (v0 / MVP for beta users)

**Status.** Not started.

**Stage boundary.** Production live with customers using it, frontend connected to prod, handover complete, post-launch backlog formally established. End of v0.

**Deployment model.** Deployed to production environment.

**Stage 6 entry condition.**
- All Stage 1, 2, 3, 4, 5 planned items shipped
- All carryover from earlier stages either shipped OR formally moved to Candidate scope via decision record
- Staging validation complete

---

## Step 7.3.1 — Excel-to-SQL converter script

**Status.** TODO (may be deprioritized — see Stage 2 note below)
**Owner.** CLAUDE_CODE

**Stage 2 fallback note.** With the Stage 2 write surface (Steps 6.10–6.15), customer data can be loaded via API endpoints rather than via this tool. Step 7.3.1 may be deprioritized or skipped if the API-based load path proves sufficient. Kept in scope as a fallback for bulk-load scenarios where scripting against the API is slower than a one-shot tool.

**Goal.** Reusable script that reads filled Excel customer-data templates and generates SQL INSERTs.

**Note on prototype.** Step 3.5's `scripts/seed_dev_data/` package is the prototype for this work — it shares shape (column-mapping table, UUIDv7 substitution via `excel_id → db_id` mapper, per-sheet loaders in FK dependency order). The principles invert at this layer per the "Note on seed Excel shape" convention captured at Step 3.5: the customer's data IS the source of truth (no synthesis), error handling is per-row (not "fail the sheet"), and the tool is idempotent (UPSERT with `ON CONFLICT (id) DO NOTHING`, not TRUNCATE-then-load).

**Scope in.**
- Write `scripts/excel_to_seed_sql.py`:
  - Reads filled Excel (path passed as argument).
  - For each sheet (tenants, stores, tenant_users), generates INSERT statements.
  - Uses deterministic UUIDs where possible (so re-runs are idempotent).
  - Hardcodes `created_by_user_id` to the bootstrap user.
  - Outputs to `db/seeds/03_customer_data_dev.sql` (or path specified).
  - Uses `ON CONFLICT (id) DO NOTHING` so re-runs don't duplicate.

**Scope out.**
- Running the converter with real data (Step 7.3.2).
- Excel template editing.

**Acceptance criteria.**
- Script runs against the existing template, produces a valid SQL file.
- Generated SQL applies cleanly to the local DB.
- Re-running with same Excel produces identical output (idempotent).

**Coordination.**
- None.

**Rough effort.** 60-90 min.

---

## Step 7.3.2 — Run customer data load

**Status.** TODO (may be deprioritized — see Stage 2 note below)
**Owner.** HUMAN (you)

**Stage 2 fallback note.** Same as Step 7.3.1. With Stage 2's write surface, this run may not be needed. Kept as fallback.

**Goal.** Customer Excel data loaded into local + cloud dev DB.

**Scope in.**
- Verify Excel template is filled with customer data.
- Run `python scripts/excel_to_seed_sql.py <excel_path>` → generates `03_customer_data_dev.sql`.
- Apply to local: `psql $DATABASE_URL_LOCAL -f db/seeds/03_customer_data_dev.sql`.
- Apply to cloud dev: `psql $DATABASE_URL_DEV -f db/seeds/03_customer_data_dev.sql`.
- Verify endpoints return expected customer data (curl + frontend).

**Scope out.**
- Production load (Step 9.2).

**Acceptance criteria.**
- Frontend rendering on cloud dev shows real customer data.

**Coordination.**
- Confirm data is loaded with whoever's preparing it.
- Frontend deeper integration test.

**Rough effort.** 30 min.

---

## Step 8.0 — Automate Cloud SQL extension creation in admin-infra Terraform

**Status.** TODO
**Owner.** HUMAN (operator decides approach) + CLAUDE_CODE (writes the Terraform once approach is chosen)

**Goal.** Replace the Cloud SQL Studio manual `CREATE EXTENSION IF NOT EXISTS ltree; CREATE EXTENSION IF NOT EXISTS pgcrypto;` step (CSD-02 in CLAUDE.md, currently manual for dev as of Step 4.1, 2026-05-04) with Terraform-managed extension creation in the admin-infra repo. Production and any future env (staging, second region) must self-heal on a single `terraform apply` with no operator console action.

**Why this is on the critical path, not a v1 wish.** Manual extension creation works when you remember it exists. It fails silently on a fresh prod cutover when an operator forgets — the Cloud Run alembic Job blows up with the same `ltree extension is required but not installed` error we hit in dev, but at 2 AM in prod with paying customers waiting. Tracked as **FN-AB-17**.

**Hard precondition.** Step 8.0 must ship before Step 8.1.1. Step 8.1.1 cannot start until Step 8.0 is DONE.

**Scope in.**
- Decide between three viable approaches (operator decision, then CLAUDE_CODE implements):
  1. `cyrilgdn/postgresql` provider with `postgresql_extension` resources. Provider needs network reachability to private-IP Cloud SQL: Cloud Build with a private worker pool, or a serverless VPC connector, or a Cloud SQL Auth Proxy sidecar invoked from a `null_resource` `local-exec`.
  2. One-time Cloud Run Job (Terraform-managed) that runs `psql` (or a small Python wrapper) authenticated as the `postgres` BUILT_IN user with the cloudsqlsuperuser role, sourcing the password from a Terraform-generated secret. Triggered as a post-apply `null_resource` with `local-exec` calling `gcloud run jobs execute`.
  3. Cloud Build trigger with a private worker pool that runs the SQL via Auth Proxy.
- Implement the chosen approach in `terraform/modules/cloud-sql/` (or a new `terraform/modules/cloud-sql-extensions/` if the chosen mechanism is shaped differently).
- Wire it into `envs/dev` and `envs/prod` (when prod env composition lands at 8.1.1, this module is invoked from there too).
- Test on the existing dev Cloud SQL instance: `terraform plan` should be a no-op (extensions already exist from the manual setup); a destroy-and-recreate of the dev DB should produce extensions automatically.

**Scope out.**
- Other extensions beyond `ltree` and `pgcrypto`. If a future migration needs `pg_trgm` or similar, the same Terraform mechanism extends to it.
- The application role's privilege model (stays `NOSUPERUSER NOBYPASSRLS` per Step 1.5; this step does not change that).

**Acceptance criteria.**
- `terraform apply` against a clean GCP project (no manual Cloud SQL Studio actions) produces a Cloud SQL instance with `ltree` and `pgcrypto` already created in the application database, ready for the alembic bring-up Job to run successfully on first attempt.
- The dev env's existing Cloud SQL instance, after this step lands, shows `terraform plan` as no-op (idempotent against the manually-created extensions).
- CLAUDE.md CSD-02 amended (not deleted) to record the historical manual-until-Step-8.0 period; FN-AB-17 marked RESOLVED.

**Coordination.**
- Lives in the infra repo. This admin-backend BUILD_PLAN entry is the planning anchor; the actual Terraform code lands in `ithina-retail-admin-infra`.
- Operator (HUMAN) picks one of the three approaches based on Cloud Build availability, willingness to manage proxy sidecars, etc. CLAUDE_CODE implements once the approach is chosen.

**Rough effort.** 2-4 hours (depending on chosen approach). Approach 1 (`cyrilgdn/postgresql` provider) is the most "right" but has the highest provider-config complexity. Approach 2 (Cloud Run Job + null_resource) reuses our existing Cloud Run Job patterns and is likely the lowest-friction.

---

## Step 8.1.1 — Production GCP provisioning (Terraform envs/prod)

**Status.** TODO
**Owner.** CLAUDE_CODE
**Blocked by.** Step 8.0 (FN-AB-17). Production cannot depend on a manual Cloud SQL Studio step.

**Goal.** Add `terraform/envs/prod/` reusing the modules from `terraform/modules/`. Lives in the `ithina-retail-admin-infra` repo alongside the dev env.

**Scope in.**
- Compose the modules already used by `envs/dev/` plus the gke module (which dev does not invoke per D-33).
- Differences from dev:
  - Separate state bucket (per-env state isolation).
  - Larger Cloud SQL tier (`db-custom-2-7680` or per ops review).
  - `availability_type = REGIONAL` on Cloud SQL (HA).
  - `deletion_protection = true` on Cloud SQL.
  - `master_authorized_networks` set on Cloud SQL.
  - Frontend `min_instances >= 1` (no scale-to-zero in prod).
  - Invokes the gke module + iam-backend with `enable_workload_identity = true` (no `cloud_run_backend` invocation in prod — backend runs on GKE per D-33).

**Scope out.**
- DR site, Cloudflare (post-launch).
- The actual GKE deploy of the backend (Step 8.2 covers the manifests + first deploy).

**Acceptance criteria.**
- `terraform validate` passes in `envs/prod/`.
- `terraform plan` against a clean prod project shows only the expected creates.
- `terraform apply` brings up the prod environment.

**Coordination.**
- Lives in the infra repo. Backend repo references via the standing-context line in CLAUDE.md.

**Rough effort.** 60-90 min (most modules already exist from Step 1.7.1; envs/prod composition + tier differences).

---

## Step 8.1.2 — Production GCP provisioning runbook

**Status.** TODO
**Owner.** CLAUDE_AI

**Goal.** Narrative runbook for production provisioning.

**Scope in.**
- Write `docs/gcp-provisioning-runbook-prod.md`.
- Differences from dev: tier sizes, IAM separation, secret values, network config.
- Verification commands.
- Credentials handover.

**Scope out.**
- Cloudflare in front (post-launch).
- DR site (post-launch).

**Acceptance criteria.**
- Runbook sent to GCP-helper.

**Coordination.**
- GCP-helper accepts and provisions.

**Rough effort.** 45 min.

---

## Step 8.1.3 — Confirm prod environment ready

**Status.** TODO
**Owner.** HUMAN (you + GCP-helper)

**Goal.** Hard gate for prod deploy.

**Scope in.**
- Verify all prod resources provisioned.
- Receive credentials.

**Acceptance criteria.**
- `kubectl get nodes` works against prod cluster.
- Cloud SQL reachable.

**Rough effort.** Varies.

---

## Step 8.2 — Deploy backend to GKE prod (NEW SHAPE)

**Status.** TODO
**Owner.** HYBRID (CLAUDE_CODE writes manifests + HUMAN runs kubectl)

**Goal.** Backend live in production on GKE Autopilot.

**Note on D-33.** This is where the GKE stack first runs in anger. Dev runs on Cloud Run (per D-33); prod is the first time the sidecar / Workload Identity / GCE Ingress / BackendConfig pattern executes against real traffic. Treat as new ground; do NOT assume parity with dev gives a free pass. The prod cutover is where the K8s-specific runtime patterns first get exercised.

**Scope in.**
- Apply schema to Cloud SQL prod via a Cloud Run Job (same shape as Step 4.1; reuses the alembic Job module from Terraform — or alternatively a one-off K8s Job). Cloud Run Job is simpler; pick that unless there's a reason not to.
- Push production-tagged image to prod Artifact Registry.
- `k8s/prod/` manifests:
  - `Deployment` with the backend container + Cloud SQL Auth Proxy sidecar.
  - `Service` (ClusterIP).
  - `Ingress` (GCE LB) with managed cert.
  - `BackendConfig` configuring health checks against `/api/v1/health`.
  - `ConfigMap` for non-secret env vars.
  - `ServiceAccount` with Workload Identity annotation binding to the prod GCP SA (provisioned by Terraform's iam-backend module with `enable_workload_identity = true` per Step 8.1.1).
- HUMAN runs `kubectl apply -f k8s/prod/`.
- Verify pods Running, ingress accessible.
- Run smoke test (Step 1.5 shape) against Cloud SQL prod.
- Run cross-tenant isolation check end-to-end in prod (TENANT-A asking for TENANT-B's user_id returns 404 — load-bearing T9 assertion shape, this time against the real prod stack).

**Scope out.**
- Auth0 swap (Step 8.3 if ready).
- Cloudflare.
- ArgoCD, DR (post-launch).

**Acceptance criteria.**
- All endpoints respond at the prod URL.
- Smoke test 74/74 PASS against prod DB.
- Cross-tenant isolation verified in prod.
- Logs in Cloud Logging.

**Coordination.**
- HUMAN runs the kubectl applies; CLAUDE_CODE drives the manifest authoring + verification.

**Rough effort.** 120-180 min (first-time GKE work in prod; expect Workload Identity / Ingress / BackendConfig debugging).

---

## Step 9.1 — Frontend cutover to prod

**Status.** TODO
**Owner.** HUMAN (you + frontend dev)

**Goal.** Frontend points at prod URL. Verification, not new integration (integration happened on dev from D#4 onward).

**Scope in.**
- Confirm CORS works on prod hostname.
- Confirm auth header format (Auth0 swap if happened may need frontend update).
- Walk through every screen against prod data.
- Fix any prod-specific issues.

**Scope out.**
- New contract negotiations (those happened on dev).

**Acceptance criteria.**
- Frontend renders all screens correctly against prod.

**Coordination.**
- Frontend team available.

**Rough effort.** 60 min.

---

## Step 9.2 — Production customer data load

**Status.** TODO (may be deprioritized — see Stage 2 note below)
**Owner.** HUMAN (you + ops)

**Stage 2 fallback note.** Same as Steps 7.3.1 / 7.3.2. With the Stage 2 write surface, prod customer data may load via API rather than via SQL files generated by Step 7.3.1. Kept as fallback for bulk-load scenarios.

**Goal.** Real first-customer data in prod.

**Scope in.**
- Use Excel-to-SQL converter from Step 7.3.1 with prod customer data.
- Apply to prod via SQL.
- Verify data via API.
- Verify audit_logs populated as expected (likely by external triggers/scripts; flag if not).

**Scope out.**
- Automated data load (post-v0).

**Acceptance criteria.**
- First customer's data visible in prod via the frontend.

**Coordination.**
- Customer data prepared by ops.

**Rough effort.** 60 min.

---

## Step 9.3 — Handover documentation

**Status.** TODO
**Owner.** CLAUDE_AI

**Goal.** Operational docs for whoever runs the system after launch.

**Scope in.**
- `docs/data-load.md`: SQL templates for inserting customer data manually.
- `docs/runbook.md`: deploy / rollback / restart / common errors / log-reading commands.
- `docs/auth.md`: how to mint test JWTs, how stub vs Auth0 toggle works, how to swap.

**Scope out.**
- Comprehensive ops manual (post-launch).

**Acceptance criteria.**
- All three docs written, reviewed.

**Coordination.**
- Ops team to read and confirm.

**Rough effort.** 90 min.

---

## Step 10.1 — Buffer / fix-anything

**Status.** TODO
**Owner.** HYBRID (CLAUDE_CODE + HUMAN)

**Goal.** Whatever surfaced through integration testing, performance gaps, missing edge cases, doc clarifications.

**Scope.** Variable. Whatever D#9 leaves outstanding plus anything new the customer onboarding flow reveals.

**Acceptance criteria.** Done is "first customer can be onboarded without a known blocker."

**Coordination.** Continuous with frontend, ops, and customer onboarding.

**Rough effort.** 4-6 hours.

---

## Step 10.2 — Engineering head sign-off + post-launch backlog

**Status.** TODO
**Owner.** CLAUDE_AI (writes backlog) + HUMAN (you + eng head, sign-off)

**Goal.** Production posture approved. Backlog captured for week 2+.

**Scope in.**
- Demo to engineering head.
- Post-launch backlog written: Auth0 (if not done), second region, audit log writers, ArgoCD GitOps, DR site, Cloudflare, comprehensive test coverage, write endpoints (v1).
- Documented as `docs/post-launch-backlog.md`.

**Scope out.**
- Doing any of the backlog items.

**Acceptance criteria.**
- Sign-off received.
- Backlog captured.

**Coordination.**
- Engineering head review.

**Rough effort.** 60 min.

---

# Coordination dependencies summary

| Person | Deliverable | Step where needed |
|---|---|---|
| Engineering head | Architecture sign-off | After Step 1.1 |
| GCP-helper / DevOps | Dev environment provisioned | Before Step 4.1 (Stage 1, before first cloud deploy) |
| Frontend developer | API contract sync | Step 2.0 |
| Frontend team | Daily integration on dev | Step 4.4 onward |
| GCP-helper / DevOps | Prod environment provisioned | Before Step 8.2 (Stage 6, before GKE prod deploy) |
| Auth0 owner (if assigned) | JWKS URL, iss, aud | Step 8.3 |
| Frontend team | Cutover to prod | Step 9.1 |
| Ops / data-prep | Excel/SQL customer data | Step 9.2 |
| Engineering head | Final prod sign-off | Step 10.2 |

---

### Carryover from Stage 6

(empty; this is the final stage. Items not shipped by end of Stage 6 either move to Candidate scope or get explicitly closed as won't-do.)

---

# Candidate scope (eligible for v0 promotion)

**Status.** Not currently placed in a Stage.

**Stage boundary.** Items here are eligible for promotion to v0 if a customer or business ask materializes. Promotion moves an item out of this section into a Stage (1–6) with an assigned step number. v0 scope evolves; this section is where evolution lives before it's committed to a Stage.

When an item moves out of Candidate scope into a Stage, log a corresponding decision-record entry in CLAUDE.md (D-XX) capturing the trigger and the destination stage.

### Items

- RBAC role management writes — editing permissions on existing roles, creating new roles. Related to FN-AB-06 tenant-custom-roles work.

- Tenant-custom roles (FN-AB-06)

- Cross-application SSO mechanics (per arch.md appendices)

- Permission-resolution endpoint for cross-user lookup (`GET /api/v1/users/{id}/effective-permissions` — per arch.md "Mental model — What v0 defers, with rationale")

- Rate limiting (per arch.md "Mental model — What v0 defers, with rationale")

- DR site (per arch.md "Deployment topology — Deferred deployment concerns")

- Cloudflare in front (per arch.md "Deployment topology — Deferred deployment concerns")

- ArgoCD GitOps (per arch.md "Deployment topology — Deferred deployment concerns")

- OpenTelemetry tracing (per arch.md "Observability — Tracing")

- Memorystore Redis (per arch.md "Deployment topology — Resource sizing")

- Service accounts (per Step 6.8 design context doc)

- AuthZ JWT format for cross-application use (per Step 6.8 design context doc)

- JWKS endpoint exposure (per Step 6.8 design context doc)

- Cookie domain `.ithina.com` (per Step 6.8 design context doc)

- Permission token validation library for other Ithina services (per Step 6.8 design context doc)

- Tenant onboarding workflows (per arch.md "Mental model — What v0 defers, with rationale")

(Future items get appended here.)

---

# Risks and mitigations

| Risk | Mitigation |
|---|---|
| Engineering head delayed on architecture sign-off | Don't block code work entirely; proceed with Steps 1.2-1.6 (DDL work) which is reversible if architecture changes. |
| GCP-helper delayed past Step 3.4 | Cloud deploy slips into later Stage 1 work. Cascade tightens. Fall back: stay local longer, deploy late in Stage 1. |
| Frontend dev unavailable for Step 2.0 | Can't lock contract. Default to internal best guesses, flag to revisit; risk of rework when first cloud-deployed endpoints land at Step 4.4. |
| Auth0 not ready in time for Stage 3 | Stage 3 slips; Stages 4–6 push out by the same amount. Stay on stub auth in dev until ready. |
| DDL stress-test surfaces issues | Stage 1 buffer (Step 1.3). Fix before encoding migrations. |
| First Cloud SQL deploy hits gotchas | Stage 1 buffer (Step 4.1 / 4.4). Common: ltree extension flag. |
| Real data has unexpected shapes | Stage 2 write surface (or fallback Step 7.3.1 Excel-to-SQL converter) gives iteration paths; load incrementally during Stage 5 and Stage 6. |
| Vibe-coded bugs at scale | Critical-path tests at Step 7.1 (Stage 5) catch the worst. |

---

# How to use this with Claude Code

For each step:

1. Open a focused Claude Code session.
2. Use the prompt for that step (from PROMPTS.md, to be written next).
3. Frame the prompt with: step ID, scope, technical hints, acceptance criteria, what's NOT in scope.
4. Run the acceptance criteria yourself before moving on.
5. Update this BUILD_PLAN.md (status field) at the end of each step.
6. Commit at the end of each step (see CLAUDE.md "After completing a task" — Pattern A).

If a step takes more than ~2-3 hours of Claude Code session time, stop and re-prompt with sharper scope. Likely the step needs splitting.

---

# End of BUILD_PLAN.md
