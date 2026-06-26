# Architecture — Ithina Admin Backend

> System architecture for the Admin Backend service (v0). Read the Summary section first; it is sufficient for sign-off and high-level review. Detailed sections below cover specific concerns for engineering team and Claude Code at build time.

---

## Document map

| Section | Audience |
|---|---|
| Summary | Engineering head (sign-off), PM |
| Mental model | Engineering team, Claude Code |
| Stack | Engineering team, Claude Code |
| Request lifecycle | Engineering team, Claude Code |
| Multi-tenancy and data isolation | Engineering team, Claude Code (load-bearing) |
| Authentication and authorisation | Engineering team, Claude Code |
| Schema and storage | Engineering team |
| Deployment topology | Engineering team, GCP-helper, PM |
| Observability | Engineering team |
| What v0 defers | Engineering head, PM |
| Risks and mitigations | Engineering head, engineering team |
| Appendix A — pod and connection internals | Engineering team, new engineers, Claude Code |

---

# Summary

## What we are building

A Python FastAPI backend service for the Ithina platform's Admin Console. Provides safe, authenticated, multi-tenant-isolated access to the platform's master database for the web frontend. v0 ships in stages — read-only foundation first (Stage 1), then RBAC enforcement and write surface (Stage 2), then Auth0 integration (Stage 3), then production cutover (Stage 6). See BUILD_PLAN.md for the complete stage map.

The service is one of multiple components in the broader Ithina platform. Other services (DIS, Pricing OS, etc.) operate independently and own their own databases or schemas; admin backend owns the master DB tables for tenants, stores, users, RBAC, and audit logs.

## Why it matters

- Real product going to a select set of paying B2B customers in Phase 1.
- Cross-tenant data leak is treated as an unacceptable risk class. Multi-tenancy isolation is the primary architectural constraint.

## How it works at a glance

```
                         +--------------------------------+
                         |  Frontend (Admin Console)      |
                         |  (out of scope for this doc)   |
                         +---------------+----------------+
                                         |
                                  HTTPS  | Bearer JWT
                                         |
                         +---------------v----------------+
                         |  Cloudflare (post-MVP/v0)      |
                         +---------------+----------------+
                                         |
                         +---------------v----------------+
                         |  GKE Autopilot (per region)    |
                         |  +--------------------------+  |
                         |  |  FastAPI pods (HPA)      |  |
                         |  |  + cloudflared sidecar   |  |
                         |  |  + Cloud SQL Auth Proxy  |  |
                         |  +-----------+--------------+  |
                         +---------------+----------------+
                                         |
                                         | psycopg3 (async)
                                         |
                         +---------------v----------------+
                         |  Cloud SQL PostgreSQL 15       |
                         |  (master DB per region)        |
                         |  + RLS enforced on all tables  |
                         +--------------------------------+
```

The flow per request: HTTPS request lands at the regional ingress (GCE Ingress in prod GKE, the Cloud Run service URL in dev per D-33), the FastAPI process's middleware verifies JWT and resolves AuthContext, a tenant-scoped DB session opens with `set_config('app.tenant_id', ..., true)` and `set_config('app.user_type', ..., true)`, the handler queries via SQLAlchemy through a Repository class, RLS on Postgres (using `NULLIF`-wrapped policies per D-27) enforces tenant isolation as a last-resort filter, response goes back through middleware to the client.

For pod / Cloud Run service internals showing SQLAlchemy and psycopg3 layering, see Appendix A.1. For why the Cloud SQL Auth Proxy runs as a sidecar in prod (and why dev's Cloud Run uses direct VPC egress instead, no sidecar), see Appendix A.2.

## Key architectural decisions

| Decision | Why |
|---|---|
| FastAPI + SQLAlchemy 2.x async + psycopg3 | Modern Python async stack, OpenAPI auto-generated, industry standard |
| PostgreSQL 15 on Cloud SQL | Cross-team consistency with DIS; RLS support; managed reliability |
| Multi-tenancy via shared schema + RLS with FORCE | Last-resort filter; even if every layer above fails, RLS returns zero rows instead of wrong rows |
| Pattern 2 user split (`platform_users` vs `tenant_users`) | Physical separation makes cross-tenant leakage structurally impossible, not policy-based |
| Auth0 (production) with RS256 stub during build | Auth0 ownership not yet assigned at Stage 1; production-shaped stub keeps interface contracts stable through Stage 3 swap |
| Per-region deployment (EU + US) | Data residency boundary; no cross-region routing in backend |
| Managed compute (Cloud Run + GKE Autopilot) + Cloud SQL + Secret Manager | Managed services, less ops burden, GCP-native. Per D-33, dev backend runs on Cloud Run, prod backend on GKE Autopilot; frontend on Cloud Run in both per D-32 |
| App-level connection pool (no PgBouncer for v0) | MVP scale; `prepare_threshold=None` from day one keeps PgBouncer addable later |

## What v0 defers

Items deferred from v0 are listed in BUILD_PLAN.md's "Candidate scope (eligible for v0 promotion)" section. That list is the canonical source; this document does not duplicate it. Items move from Candidate scope into a Stage if a customer or business ask materializes.

## Sign-off ask

Engineering head review covers:

- Multi-tenancy enforcement strategy (RLS + FORCE + middleware + dependency).
- Pattern 2 user table split (platform vs tenant).
- Per-region deployment with hard residency boundary.
- v0 scope across six stages — read-only foundation (Stage 1), RBAC enforcement and writes (Stage 2), Auth0 integration (Stage 3), production cutover (Stage 6). See BUILD_PLAN.md for the complete stage map.
- Stub auth pattern (production-shaped; Auth0 integration in Stage 3).
- Observability floor (JSON logs, basic Prometheus).
- Deferred items (see BUILD_PLAN.md Candidate scope). Terraform now in scope from day one per D-23 (revised 2026-05-03); lives in the separate infra repo.

If any of these warrant a different posture, flag now. Build starts immediately on sign-off.

---

# Mental model

The admin backend is a REST API service that gives the frontend safe, authenticated, multi-tenant-isolated access to the platform's master database.

Most endpoints are CRUD-shaped (one table, one operation). A few involve derived shapes (org tree descendants via ltree, permission resolution across joined tables) that are still READ but require non-trivial query logic. Write endpoints (Stage 2) involve multi-table operations, validation, and side effects (audit, notifications, cache invalidation).

**Auth and multi-tenancy are not "standard concerns" but the load-bearing mechanics of this service.** Every request is filtered by tenant context; every connection is RLS-bound. Get auth right and data isolation is guaranteed. Get it wrong and there is a leak.

Standard REST API concerns apply on top: input validation (Pydantic), structured error responses, observability, URI versioning, OpenAPI specification. v0 includes all of these.

## What v0 defers, with rationale

Selected items from BUILD_PLAN.md's Candidate scope, with the reasoning preserved here for architectural context. The canonical list lives in BUILD_PLAN.md.

- **Rate limiting.** Multi-tenant rate limits need state (Redis), middleware ordering decisions, and per-tenant tuning. Cross-team decision needed.
- **Permission-resolution endpoint for cross-user lookup.** `GET /api/v1/users/{id}/effective-permissions` (lookup of one user's permissions by another user) sits in Candidate scope. The user's own permission inquiry endpoints (`/me/permissions` and `/me/can-do`) ship in Stage 2 as part of RBAC enforcement.
- **Tenant onboarding workflows.** Phase 1 onboarding is staff-driven (manual SQL); endpoint-driven onboarding is in Candidate scope.

---

# Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Framework | FastAPI (async) |
| DB engine | PostgreSQL 15 |
| DB driver | psycopg3 (async) |
| ORM | SQLAlchemy 2.x async with `prepare_threshold=None` |
| Migrations | Alembic |
| Auth | Auth0 (production) / RS256 stub (build phase) |
| Package manager | uv |
| Type checking | mypy strict in CI |
| Container | Multi-stage `python:3.12-slim` |
| Cloud | Google Cloud Platform |
| Compute | Cloud Run (dev backend, frontend in both envs) + GKE Autopilot (prod backend); per D-32 / D-33 |
| DB hosting | Cloud SQL for PostgreSQL |
| Secrets | GCP Secret Manager (Workload Identity binding) |
| Edge | Cloudflare (post-MVP/v0) |
| Logs | Structured JSON to stdout → Cloud Logging |
| Metrics | Prometheus `/metrics` → managed Prometheus on GKE → Cloud Monitoring |

`prepare_threshold=None` is set from day one. This disables prepared statements, which is required for PgBouncer compatibility in transaction-pooling mode if added later. No PgBouncer in v0.

---

# Request lifecycle

Detailed walk-through of a single authenticated read request, from HTTPS ingress to response.

```
+----------------------------------------------------------------------+
| 1. HTTPS Request lands at the regional ingress                       |
|    PROD: GCE Ingress -> GKE Service -> backend Pod                   |
|    DEV : Cloud Run service URL -> backend container (per D-33)       |
|    GET /api/v1/tenants                                               |
|    Authorization: Bearer <RS256 JWT>                                 |
+----------------------+-----------------------------------------------+
                       |
                       v
+----------------------------------------------------------------------+
| 2. Routed to a FastAPI process                                       |
|    PROD: one of N GKE pods (HPA-scaled, default 3 replicas)          |
|    DEV : a Cloud Run service instance (scale-to-zero, on-demand)     |
+----------------------+-----------------------------------------------+
                       |
                       v
+----------------------------------------------------------------------+
| 3. ASGI middleware: auth.py                                          |
|    - Extracts Authorization header                                   |
|    - Calls StubAuthClient.verify(jwt) (or Auth0Client in prod)       |
|    - Validates signature (RS256), iss, aud, exp                      |
|    - Extracts custom claims:                                         |
|        https://sevyn8.com/tenant_id                                  |
|        https://sevyn8.com/user_type                                  |
|    - Builds AuthContext, sets request.state.auth                     |
|    - On failure: returns 401 with structured error                   |
+----------------------+-----------------------------------------------+
                       |
                       v
+----------------------------------------------------------------------+
| 4. ASGI middleware: audit_context.py                                 |
|    - Generates UUID4 request_id                                      |
|    - Captures IP (X-Forwarded-For or peer)                           |
|    - Captures User-Agent                                             |
|    - Sets on request.state                                           |
|    - Logs INFO line on completion (one per request)                  |
+----------------------+-----------------------------------------------+
                       |
                       v
+----------------------------------------------------------------------+
| 5. Handler: routers/v1/tenants.py                                    |
|    - Receives session via Depends(get_tenant_session)                |
|                                                                      |
|    get_tenant_session dependency:                                    |
|    - Reads request.state.auth                                        |
|    - Opens AsyncSession                                              |
|    - Begins transaction                                              |
|    - set_config('app.tenant_id', <uuid-or-null>, true)               |
|    - set_config('app.user_type', 'PLATFORM' | 'TENANT', true)        |
|    - Yields session to handler                                       |
+----------------------+-----------------------------------------------+
                       |
                       v
+----------------------------------------------------------------------+
| 6. Handler calls TenantsRepo (Repository class)                      |
|    - tenants_repo.list_all(session)                                  |
|    - Repo runs SELECT via SQLAlchemy                                 |
+----------------------+-----------------------------------------------+
                       |
                       v
+----------------------------------------------------------------------+
| 7. Postgres receives query                                           |
|    - app.tenant_id and app.user_type session vars set per request    |
|    - RLS policy: tenant_id = NULLIF(...)::uuid OR user_type=PLATFORM |
|    - Returns tenant-isolated rows (TENANT) or all rows (PLATFORM)    |
+----------------------+-----------------------------------------------+
                       |
                       v
+----------------------------------------------------------------------+
| 8. Repo returns ORM objects                                          |
|    - Handler converts via Pydantic schemas (TenantRead)              |
|    - FastAPI serialises to JSON                                      |
+----------------------+-----------------------------------------------+
                       |
                       v
+----------------------------------------------------------------------+
| 9. Response goes back through middleware                             |
|    - Audit context middleware adds X-Request-Id header               |
|    - Logs INFO with request_id, tenant_id, route, status, latency_ms |
+----------------------+-----------------------------------------------+
                       |
                       v
+----------------------------------------------------------------------+
| 10. HTTPS response to client                                         |
+----------------------------------------------------------------------+
```

Key invariants enforced along the way:

- **Tenant_id reaches the backend only from verified JWT** (or verified path parameter for staff cross-tenant operations). Never from request body, query string, or custom headers.
- **`app.tenant_id` is sourced exclusively from `AuthContext.tenant_id`** (a `UUID | None` field on a frozen Pydantic model; mypy strict enforces, no raw-string path exists). See AI-MT-03 / Layer 4.
- **Every connection acquired by app code goes through `get_tenant_session()`.** No direct `engine.connect()` paths.
- **Cross-tenant tenant_id mismatch (JWT vs path) returns 400 with quarantine code, not the requested data.**

---

# Multi-tenancy and data isolation

The most load-bearing concern in the system. Defence-in-depth across five layers.

## Layer 1 — Postgres Row-Level Security (RLS) with FORCE

Every table that holds tenant-owned data has:

- `tenant_id UUID NOT NULL` (or its primary key acts as tenant_id, in the case of `tenants` itself).
- `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`.
- `ALTER TABLE ... FORCE ROW LEVEL SECURITY` (so the table owner role does not bypass).
- `CREATE POLICY ... USING (tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid OR current_setting('app.user_type', TRUE) = 'PLATFORM')` — see D-27 for why the NULLIF wrapper is mandatory; D-29 for the PLATFORM OR-branch. The branch is policy-enforced (no BYPASSRLS role).

Two session vars carry the tenant context per transaction:

- `app.tenant_id`: the tenant UUID for TENANT users, or for PLATFORM users impersonating a tenant. NULL semantics for PLATFORM not impersonating; in practice the GUC ends up at empty string after the first set on a connection (Postgres 15 quirk), and the NULLIF wrapper turns that back into NULL inside the policy.
- `app.user_type`: `'PLATFORM'` or `'TENANT'`. Drives the OR-branch on every multi-tenant policy. When set to `'PLATFORM'`, the branch is unconditionally TRUE on every multi-tenant table — uniform unconditional shape across all 6 multi-tenant tables (tenants, tenant_users, org_nodes, stores, tenant_module_access, tenant_user_role_assignments) post-Step-6.8.1. The IS-NULL-gated form that originally lived on `user_role_assignments` (FN-AB-14) was retired by the table split per D-34: `user_role_assignments` no longer exists; PLATFORM-audience rows live in `platform_user_role_assignments` (no RLS — platform-global; visibility at app layer mirrors `platform_users`' posture per D-12). Net effect: PLATFORM sessions see all rows on multi-tenant tables; per-role RBAC (application layer) constrains what they can do with that visibility.

Default-deny for TENANT users: when `app.tenant_id` resolves to NULL on a TENANT-typed session (pristine connection or empty-string-after-NULLIF), `tenant_id = NULL` is unknown (false in WHERE), the OR-branch's `app.user_type = 'PLATFORM'` is also false, and the policy returns zero rows. **A misconfigured TENANT caller cannot read data; it just sees nothing.** A misconfigured PLATFORM caller (`app.user_type` = 'PLATFORM' set unintentionally) sees everything — which is why `app.user_type` is sourced exclusively from `AuthContext.user_type` (a `Literal["PLATFORM", "TENANT"]` on a frozen Pydantic model, mypy-strict-enforced; per AI-MT-03).

## Layer 2 — Centralised tenant binding

A single FastAPI dependency `get_tenant_session()` is the only path that opens a DB connection in app code. It:

1. Reads AuthContext from `request.state` (set by auth middleware; wiring lands at Step 2.3).
2. Opens an AsyncSession with a fresh transaction.
3. Runs `SELECT set_config('app.tenant_id', <uuid-or-null>, true)`.
4. Runs `SELECT set_config('app.user_type', <'PLATFORM'|'TENANT'>, true)`.
5. Yields the session to the handler.
6. Commits or rolls back at handler completion.

`set_config(..., is_local=true)` (not `SET LOCAL`) is used because `SET LOCAL` cannot represent NULL cleanly; `set_config` accepts NULL as a value. (The Postgres 15 quirk that makes `current_setting` return `''` after the first set is handled in Layer 1 via NULLIF.) A linter rule forbids direct `engine.connect()` in handlers. Structurally, no handler can query without the correct tenant set.

## Layer 3 — Trusted source for tenant_id

`tenant_id` reaches the backend only from:

- Verified JWT claims (for tenant users; the JWT contains `https://sevyn8.com/tenant_id`).
- Verified path parameters (for staff cross-tenant operations like `/api/v1/admin/tenants/{tenant_id}/users`).

Never from request body, query string, custom headers, or any caller-supplied untrusted source.

## Layer 4 — Source-binding via AuthContext

`get_tenant_session()` accepts `auth: AuthContext` only. `auth.tenant_id` is `UUID | None` on a frozen Pydantic v2 model (D-24). mypy strict statically rejects any attempt to flow a raw string into the dependency, and there is no runtime path that constructs an AuthContext from an unvalidated source. A separate `VerifiedTenantId` newtype was previously planned but is redundant ceremony given the AuthContext field type already enforces the same guarantee. See AI-MT-03 for the canonical statement.

## Layer 5 — Cross-check on multi-source tenant context

If a request carries both a JWT tenant_id and a path tenant_id, they MUST match. Mismatch returns 400 with `code=TENANT_CONTEXT_MISMATCH` and is logged as a potential attack signal. Quarantine, not silent acceptance.

## Why these five layers

Cross-tenant leak is treated as a disaster-class risk. Any single layer can fail (developer error in a query, middleware misorder, misconfigured RLS policy). Five independent layers means a leak requires simultaneous failure across all five. This is what justifies the additional design weight relative to a typical CRUD service.

---

# Authentication and authorisation

## Authentication

Auth0 in production. Stub during build phase.

JWT shape (Auth0-compatible custom claim namespace):

```json
{
  "iss": "https://ithina.auth0.com/",
  "aud": "https://api.ithina.com",
  "sub": "auth0|abc123...",
  "iat": 1714492800,
  "exp": 1714579200,
  "https://sevyn8.com/tenant_id": "<uuid>",        // null for staff
  "https://sevyn8.com/user_type": "TENANT",        // or "PLATFORM"
  "https://sevyn8.com/user_id": "<uuid>"
}
```

StubAuthClient and Auth0Client implement the same interface: `verify(jwt_string) -> AuthContext`. Toggle via env var `AUTH_CLIENT_MODE=AUTH0|STUB`. Production uses `AUTH0`. Build phase uses `STUB`.

`AuthContext` is a frozen Pydantic model:

```python
class AuthContext(BaseModel):
    user_id: UUID
    user_type: Literal["PLATFORM", "TENANT"]
    tenant_id: UUID | None  # None for staff
    is_staff: bool
    roles: list[str]
    auth_subject: str  # raw "sub" claim
```

## Authorisation

This section describes authorisation in two phases:
(a) pre-Section-6.9 (early Stage 1 — read-only API, tenant isolation via RLS, no fine-grained permission gates), now historical, and
(b) post-Section-6.9 (current shipped state — RBAC enforcement layer with mandatory-gate-discipline; full reference in `architecture_RBAC.md`).

Both subsections below are retained: (a) for historical context; (b) describes the live system.

### Authorisation — pre-Section-6.9 (historical, retained for context)

Before Section 6.9, v0 Stage 1 enforced authorisation at two levels:

- **Endpoint-level:** auth middleware blocks unauthenticated requests at 401.
- **Tenant-level:** RLS filters reads to caller's tenant. Cross-tenant access by TENANT users is structurally impossible; PLATFORM users see all rows (Layer 1 unconditional OR-clause per D-29).

Permission catalogue (roles, permissions, role_permissions, platform_user_role_assignments, tenant_user_role_assignments) is readable via Step 6.1 endpoints but not consumed for enforcement. Reads are gated by tenant context (RLS) and JWT validity only.

Permission cascade rules (referenced for completeness; consumed in post-Stage-2):

- Assignments anchor at a position in the org tree (org_node).
- Permissions cascade downward via ltree path `<@` operator.
- Example: a user with role "Pricing Manager" assigned at `Region: Texas` sees data for all stores under Texas, but not stores under another region.

### Authorisation — RBAC enforcement (shipped at Section 6.9)

Stage 2's RBAC enforcement layer shipped end-to-end across Section 6.9 (commits 63dd565 through f3826a8). The system authorises every request using a 4-tuple permission identity `(module, resource, action, scope)`. Each gated endpoint maps to exactly one tuple; a user holds grants of the same shape via roles; the gate succeeds when the user's grants include a tuple that satisfies the request's tuple.

Key mechanisms:

- **Resolver (`has_permission`)** — a single targeted SQL query per gate check; not enumeration. Two cascade dimensions inside the query: scope cascade (Python helper + SQL `ANY` clause; GLOBAL → TENANT → STORE downward) and anchor cascade (Postgres ltree `<@` operator; tenant root → region → store downward).
- **Gate factory (`require()`)** — FastAPI dependency. Endpoints declare `Depends(require(M, R, A, S, anchor_dep=...))`. Two inner-function shapes depending on whether the gate needs an anchor dependency.
- **Anchor dependencies** — per-resource lookup functions in `auth/anchor_deps.py`. Raise 404 on miss (NEVER return None — would short-circuit the cascade clause to TRUE; security-critical invariant).
- **Mandatory-gate-discipline meta-test** — `tests/integration/test_gate_discipline.py`. Iterates `app.routes`, asserts every `APIRoute` is either gated (carries the `__permission_gate__` marker) or in an explicit allowlist (`GATE_EXEMPT_PATHS` or `PUBLIC_PATHS`). Deploy-time structural guarantee.
- **Error contract** — `PermissionDeniedError` (403, code `PERMISSION_DENIED`, structured context via `exc.context` for audit logs).
- **RLS as defense-in-depth** — the gate authorises the request; RLS scopes visible rows. For writes: handlers check rowcount and raise 404 on RLS-invisible targets (the RLS-as-404 contract).

Catalogue and seed data:

- 31 permission rows; 122 role-permission grants at HEAD `f3826a8`.
- Seeded via `data/ithina_dev_seed_data.xlsx`; catalogue changes apply via operator workflow (Excel + local seed loader + Cloud SQL UPSERT), not Alembic.

**See `architecture_RBAC.md` for the full reference** — system model, request flow, query shapes, gate factory internals, anchor dependency patterns for both reads and writes, error contract details, mandatory-gate-discipline mechanics, the "Adding a new endpoint" cookbook with worked PUT and POST examples, coupling and conventions, forward-compatibility seams, performance characteristics, and audit-trail guidance. Authoritative for Stage 2+ write-endpoint design.

---

# Schema and storage

## Master database

A single Cloud SQL Postgres 15 instance per region, shared platform-wide. Admin backend is the sole writer to its own tables. Other services may own and write other tables in the same instance (e.g., DIS canonical+bronze are in a separate Cloud SQL instance, but other shared platform tables may live alongside).

Schema separation: admin-backend tables live in a Postgres schema whose name is supplied at runtime via the `DB_SCHEMA` env var per D-15. Local dev is `core`; dev / staging / prod each pick their own. The application code, DDLs, and migration files are identical across environments; only the env-var value differs. Renaming a schema later is a 30-second `ALTER SCHEMA ... RENAME` because no schema name is hardcoded in code or migrations.

The application schema (`core` and per-env equivalents) is created by Alembic at first upgrade, not by Terraform — `migrations/env.py` issues `CREATE SCHEMA IF NOT EXISTS` before the first migration runs, so a fresh Cloud SQL instance whose database and role are provisioned by Terraform self-heals on first bring-up.

Cloud SQL extensions (`ltree`, `pgcrypto`) are infra-owned, not Alembic-owned. The application role is `NOSUPERUSER NOBYPASSRLS` by deliberate design (Step 1.5 hardening) and cannot `CREATE EXTENSION`; only `cloudsqlsuperuser` can. In dev this is currently a one-time manual step in Cloud SQL Studio (run as the `postgres` BUILT_IN user) per CLAUDE.md CSD-02. **Step 8.0 in BUILD_PLAN.md automates this in admin-infra Terraform and is a hard precondition of Step 8.1.1 (production provisioning).** Production must never depend on a manual Cloud SQL Studio step.

## Tables

12 tables across 9 DDL files (rbac_v3 supersedes rbac_v2 conceptually post-Step-6.8.1; v2 stays as historical record per the frozen-DDL convention). A 10th file (audit_logs) is added during the build at Step 6.2.

| Order | File | Tables | Tenant-scoped? | RLS? |
|---|---|---|---|---|
| 1 | shared_utilities_v1 | (extensions, functions, shared enums) | n/a | n/a |
| 2 | lookups_v1 | lookups | No (platform-global) | No |
| 3 | platform_users_v1 | platform_users | No (platform-global) | No |
| 4 | tenants_v3 | tenants | Self (id IS the tenant_id) | Yes |
| 5 | tenant_users_v1 | tenant_users | Yes | Yes |
| 6 | org_nodes_v2 | org_nodes | Yes | Yes |
| 7 | stores_v5 | stores | Yes | Yes |
| 8 | rbac_v3 (Step 6.8.1) | permissions, roles, role_permissions, platform_user_role_assignments, tenant_user_role_assignments | Mixed (assignments split per audience: platform-* no RLS; tenant-* RLS+FORCE) | platform-* No; tenant-* Yes |
| 9 | tenant_module_access_v1 | tenant_module_access | Yes | Yes |
| 10 | audit_logs_v1 (added during build) | audit_logs | Yes (nullable for GLOBAL scope) | Yes |

## Schema conventions

- snake_case identifiers.
- Plural table names, singular column names.
- Surrogate UUID PK (`id UUID NOT NULL DEFAULT uuidv7()`). Per D-21, UUIDv7 (not v4) for insert-locality and WAL-pattern benefits at the canonical layer; `uuidv7()` is the project's PL/pgSQL function in `db/raw_ddl/Ithina_postgres_SQL_DDL_shared_utilities_v1.sql`. FN-AB-13 tracks the eventual swap to Postgres 18's native `uuidv7()` once Cloud SQL ships it.
- Timestamps `_at` suffix, TIMESTAMPTZ, UTC.
- Booleans `is_<state>` or `has_<thing>`.
- Constraint prefixes: `pk_`, `fk_`, `uq_`, `ix_`, `ck_`.
- Enums named `<column>_enum`.
- TEXT over varchar(n); JSONB over JSON.

## Audit columns pattern

Every business table has audit columns:

- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `created_by_user_id UUID NOT NULL`
- `created_by_user_type actor_user_type_enum NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `updated_by_user_id UUID NOT NULL`
- `updated_by_user_type actor_user_type_enum NOT NULL`

The `*_user_id + *_user_type` pair is Pattern (b) — UUID + enum, no FK to either user table. App-layer validation ensures the UUID exists in the table indicated by `user_type`. Tech debt: no DB-level referential integrity on actors. Migration to FK-bearing pattern is ~2-4 days when needed.

---

# Deployment topology

## Per-region stack

Per D-33, the dev and prod runtime shapes diverge: dev runs the backend on Cloud Run (no Kubernetes); prod runs the backend on GKE Autopilot. Frontend is on Cloud Run in both envs per D-32. The container image artifact is identical across both shapes — the same image that's tested in dev is shipped to prod.

### Per-region stack — DEV (asia-south1, ithina-retail-admin)

```
+--------------------------------------------------+
|  Region: asia-south1 (DEV)                       |
|                                                  |
|  +--------------------------------------------+  |
|  |  Cloud Run                                 |  |
|  |    + admin-frontend (Service, Next.js)     |  |
|  |    + admin-backend  (Service, FastAPI)     |  |
|  |    + admin-backend-alembic (Job, one-shot) |  |
|  |  All scale-to-zero. Public invoker.        |  |
|  +-----+--------------------------------------+  |
|        | direct VPC egress (private ranges)      |
|        v                                         |
|  +--------------------------------------------+  |
|  |  VPC + private services peering            |  |
|  +-----+--------------------------------------+  |
|        v                                         |
|  +--------------------------------------------+  |
|  |  Cloud SQL Postgres 15 (private IP)        |  |
|  +--------------------------------------------+  |
|                                                  |
|  +--------------------------------------------+  |
|  |  Secret Manager  +  Artifact Registry      |  |
|  +--------------------------------------------+  |
+--------------------------------------------------+
```

Cloud Run's runtime SA holds `roles/cloudsql.client` and `roles/secretmanager.secretAccessor`. No sidecar — direct VPC egress reaches Cloud SQL on its private IP.

### Per-region stack — PROD (asia-south1, ithina-admin-prod)

```
+--------------------------------------------------+
|  Region: asia-south1 (PROD)                      |
|                                                  |
|  +--------------------------------------------+  |
|  |  GKE Autopilot                             |  |
|  |  +--------------------------------------+  |  |
|  |  |  admin-backend Deployment (HPA 3-10) |  |  |
|  |  |    + uvicorn FastAPI                 |  |  |
|  |  |    + Cloud SQL Auth Proxy sidecar    |  |  |
|  |  +--------------------------------------+  |  |
|  |  Service: ClusterIP   Ingress: GCE LB     |  |
|  +-----+--------------------------------------+  |
|        |                                         |
|  +--------------------------------------------+  |
|  |  Cloud Run                                 |  |
|  |    + admin-frontend (Service, Next.js)     |  |
|  +--------------------------------------------+  |
|                                                  |
|  +--------------------------------------------+  |
|  |  Cloud SQL Postgres 15 (private IP)        |  |
|  |    + read replica                          |  |
|  +--------------------------------------------+  |
|                                                  |
|  +--------------------------------------------+  |
|  |  Secret Manager  +  Artifact Registry      |  |
|  |  + Workload Identity binding (GKE backend) |  |
|  +--------------------------------------------+  |
+--------------------------------------------------+

[Same prod shape replicated for europe-west1 (PROD)]
[admin-eu.ithina.com / admin-us.ithina.com]
```

## Per-region isolation

- EU and US are independent stacks. No cross-region routing in backend.
- Tenants are pinned to a region at onboarding.
- Frontend handles staff cross-region access by talking to both regional backends from the same UI.
- Per-region hostnames: `admin-eu.ithina.com` and `admin-us.ithina.com`.

## Network and security

- Inbound: HTTPS only, via GCE Ingress (managed cert) for v0.
- Outbound: only to Cloud SQL (via Auth Proxy) and GCP Secret Manager.
- Service account: per-deployment, with minimum-needed roles (`roles/cloudsql.client`, `roles/secretmanager.secretAccessor`). No broader IAM.
- Workload Identity binds the GKE service account to the GCP service account. No service account keys checked in.

## Resource sizing (v0 starting point)

| Component | Sizing |
|---|---|
| Cloud SQL | `db-g1-small` for dev, `db-custom-2-7680` for prod (revisit based on load) |
| GKE pod | request 250m CPU / 256Mi RAM, limit 500m / 512Mi |
| GKE replicas | 2 in dev, 3 in prod, HPA up to 10 |
| Memorystore | none in v0 (see BUILD_PLAN.md Candidate scope) |

## Two GCP projects

- `ithina-retail-admin`: development and integration testing.
- `ithina-retail-admin-prod` (TBD): paying customers.

Cross-project IAM isolation. Same region for now (asia-south1, Mumbai). Add other regions later as customer geography demands.

## Deferred deployment concerns

Deployment-related items deferred from v0 (DR site, ArgoCD GitOps, Cloudflare WAF/DDoS/CDN, auto-scaling tuning beyond defaults) are listed in BUILD_PLAN.md's "Candidate scope (eligible for v0 promotion)" section.

Terraform infra-as-code is no longer deferred: per D-23 (revised 2026-05-03) the GCP infrastructure is provisioned via Terraform from day one. The Terraform code lives in the separate `ithina-retail-admin-infra` repo.

---

# Observability

## Logs

Structured JSON to stdout. Cloud Logging captures stdout from container automatically and indexes JSON fields.

Per-request log line includes: timestamp, level, request_id, tenant_id, user_id, route, method, status, latency_ms, message.

Logging discipline:

- One INFO log per request (audit context middleware handles).
- One ERROR log per failure with full context.
- No DEBUG in committed code unless feature-flagged.
- No payload dumps; summary statistics only.
- No logs in tight loops or hot paths.

Filtering example: `jsonPayload.tenant_id="<uuid>" AND jsonPayload.status>=500`.

## Metrics

Prometheus-format `/metrics` endpoint exposed by the FastAPI app via `prometheus-fastapi-instrumentator`. Default metrics: request count, latency histogram, in-flight requests.

GCP managed Prometheus on GKE scrapes the endpoint via Pod annotations:

```yaml
monitoring.googleapis.com/scrape: "true"
monitoring.googleapis.com/path: "/metrics"
monitoring.googleapis.com/port: "8000"
```

Metrics flow to Cloud Monitoring; Metrics Explorer for ad-hoc queries; Dashboards for ops review.

## Tracing

Deferred to BUILD_PLAN.md Candidate scope. OpenTelemetry tracing lands when cross-service debugging becomes frequent. v0 uses request_id propagation (header `X-Request-Id`) for correlation across logs.

## Alerting

Cloud Monitoring alerts on:

- 5xx rate above threshold.
- p95 latency above threshold.
- Pod crash-loop / unavailability.

Specific thresholds tuned post-deploy.

---

# What v0 defers

Items deferred from v0 are listed in BUILD_PLAN.md's "Candidate scope (eligible for v0 promotion)" section, which is the canonical source. Rationale for the most architecturally significant deferrals (rate limiting, permission-resolution endpoint, tenant onboarding workflows) is preserved in the Mental model section above. Items move from Candidate scope into a Stage if a customer or business ask materializes.

---

# Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Cross-tenant data leak | Low (defence-in-depth) | Disaster | Five-layer multi-tenancy; critical-path tests verify in cloud |
| Auth0 ownership delayed | Medium | Medium | Stub auth ships in Stage 1; Auth0 integration is Stage 3 (before staging/UAT). Delay past Stage 3 entry slips Stages 4–6 by the same amount. |
| GCP-helper provisioning delayed | Medium | High | Cloud deploy slips within Stage 1; cascade tightens but v0 still ships if local dev complete |
| Schema bug discovered post-deploy | Medium | Medium | Stress-test in Stage 1 (Step 1.3); smoke test validates RLS in cloud at Stage 1 (Step 4.4) |
| Cloud SQL `ltree` extension config issue | Low | Medium | Flag set at provisioning; verified in Step 4.1 |
| Frontend integration discovers contract mismatches | High | Low | Frontend integrates daily on dev from Stage 1 (Step 4.6 onward); contract sync in Step 2.0 |
| Cloud-specific behaviour differs from local | Medium | Medium | Smoke test runs against Cloud SQL at Stage 1 (Step 4.4) |
| RLS doesn't work as expected in cloud Postgres | Low | Disaster | Explicit test in Step 4.4 verifies cross-tenant isolation in cloud |
| Vibe-coded bugs at scale | Medium | Medium | Critical-path tests at Stage 5 (Step 7.1) catch worst; mypy strict gates obvious errors |

---

# Appendix A — Detailed pod and connection internals

The "How it works at a glance" diagram earlier in this document gives the high-level system view. This appendix expands two areas that come up frequently when working in the codebase or onboarding to it.

## A.1 — Application data access path

What runs inside the FastAPI pod, layer by layer, when a request reaches a database.

```
+-----------------------------------------------+
|  FastAPI Pod                                  |
|                                               |
|  +-----------------------------------------+  |
|  |  FastAPI handler code                   |  |
|  |  (e.g., GET /api/v1/tenants endpoint)       |  |
|  +-----------------+-----------------------+  |
|                    |                          |
|                    | calls repo.list_all()    |
|                    |                          |
|  +-----------------v-----------------------+  |
|  |  Repository class (e.g., TenantsRepo)   |  |
|  |  builds query, calls session.execute    |  |
|  +-----------------+-----------------------+  |
|                    |                          |
|                    | session.execute(...)     |
|                    |                          |
|  +-----------------v-----------------------+  |
|  |  SQLAlchemy 2.x async ORM               |  |
|  |  - builds SQL from Python code          |  |
|  |  - manages connection pool              |  |
|  |  - handles transactions and set_config()|  |
|  +-----------------+-----------------------+  |
|                    |                          |
|                    | sends SQL via async      |
|                    |                          |
|  +-----------------v-----------------------+  |
|  |  psycopg3 (async DB driver)             |  |
|  |  speaks Postgres wire protocol          |  |
|  +-----------------+-----------------------+  |
|                    |                          |
+--------------------+--------------------------+
                     |
                     | localhost:5432 inside pod
                     |
+--------------------v--------------------------+
|  Cloud SQL Auth Proxy sidecar                 |
|  (separate container in same pod)             |
+--------------------+--------------------------+
                     |
                     | encrypted, IAM-authenticated
                     |
+--------------------v--------------------------+
|  Cloud SQL Postgres 15 (managed instance)     |
+-----------------------------------------------+
```

What each layer is responsible for:

| Layer | Purpose |
|---|---|
| FastAPI handler | Defines the endpoint, receives request, returns response. Stays free of SQL. |
| Repository class | Owns SELECT queries for one resource (one Repo per table). Hides SQLAlchemy from handlers. |
| SQLAlchemy 2.x async ORM | Translates Python objects and queries to SQL. Manages the connection pool. Handles transactions, including the per-request `set_config('app.tenant_id', ..., true)` and `set_config('app.user_type', ..., true)`. |
| psycopg3 async driver | Sends raw SQL over the wire to Postgres. Implements the Postgres protocol. |
| Cloud SQL Auth Proxy sidecar | Secure tunnel to Cloud SQL (see A.2). |
| Cloud SQL Postgres 15 | Actual database. Stores rows, enforces RLS, executes queries. |

The high-level diagram in "How it works at a glance" labels the wire-level component (psycopg3) for brevity. In practice, FastAPI handlers call into Repository classes, Repositories call into SQLAlchemy, and SQLAlchemy uses psycopg3 internally as the wire-level driver. New engineers should hold this layered picture, not the simplified one.

## A.2 — Why the Cloud SQL Auth Proxy is a sidecar

Connecting to Cloud SQL securely requires three things the FastAPI app shouldn't have to handle directly: IAM authentication, TLS encryption, and reachability to the managed instance. The Cloud SQL Auth Proxy is a Google-provided binary that handles all three. Running it as a sidecar (its own container alongside FastAPI in the same pod) is the recommended pattern.

```
+------------------------------------------------+
|  Pod                                           |
|                                                |
|  +------------------------+                    |
|  |  FastAPI container     |                    |
|  |                        |                    |
|  |  Connects to:          |                    |
|  |  localhost:5432        |                    |
|  +-----------+------------+                    |
|              |                                 |
|              | (loopback, no encryption        |
|              |  needed inside the pod)         |
|              |                                 |
|  +-----------v---------------+                 |
|  |  Cloud SQL Auth Proxy     |                 |
|  |  sidecar container        |                 |
|  |                           |                 |
|  |  - Listens on :5432       |                 |
|  |  - Authenticates via IAM  |                 |
|  |    (Workload Identity)    |                 |
|  |  - Adds TLS upstream      |                 |
|  +-----------+---------------+                 |
|              |                                 |
+--------------|---------------------------------+
               |
               | encrypted + IAM-authenticated
               |
+--------------v---------------------------------+
|  Cloud SQL Postgres instance                   |
|  (private IP, only reachable from this VPC)    |
+------------------------------------------------+
```

What this gets us:

| Concern | Without proxy | With proxy |
|---|---|---|
| Authentication | Postgres username + password stored in app config | IAM-authenticated via Workload Identity. No password to manage. |
| TLS encryption | App must manage cert verification, rotation, trust chain | Proxy handles TLS to Cloud SQL upstream; app talks plaintext to localhost |
| Network reachability | App must know Cloud SQL hostname, handle private-IP routing | App connects to localhost:5432; proxy figures out how to reach the instance |
| Credential rotation | Rotate DB password across every pod and config | Revoke IAM role on the service account; takes effect immediately |
| Local dev parity | Connection string differs from prod | Connection string is identical (`localhost:5432`); local dev points at Docker Postgres, prod points at the proxy |

Trade-off accepted: one extra container per pod. The benefits (no DB password in config, IAM-driven access, no TLS plumbing in the app) outweigh the slightly higher pod resource usage at our scale.

The Cloud SQL Auth Proxy is only present in deployed environments. Locally, the FastAPI app connects directly to the Docker Postgres container at `localhost:5432`. The Dockerfile produces only the app image; the sidecar is added by the Kubernetes deployment manifest, not the app image.

---

## A.3 — Tenant create transaction shape (Step 6.20.1)

`POST /api/v1/tenants` (PLATFORM-audience, gated `ADMIN.TENANTS.CONFIGURE.GLOBAL`) writes three tables in a single request transaction:

1. `core.tenants` — the tenant row itself. `status='TRIAL'`, audit-actor pair populated from JWT.
2. `core.org_nodes` — the tenant-root row. `node_type='TENANT'`, `parent_id IS NULL`, `path` is an ltree label derived from `display_code` (if provided) or `name` (fallback). DDL CHECK `ck_org_nodes_root_parent_consistency` enforces the `node_type='TENANT'` ↔ `parent_id IS NULL` invariant.
3. `core.tenant_module_access` — one row per module in `modules_enabled` (ADMIN force-included by the request schema).

All three writes are atomic. A failure at any step rolls back the entire transaction; no partial tenants survive.

The tenant-root org_node row is the anchor for every tenant-scoped endpoint gated with `anchor_dep=get_tenant_anchor`. Its existence is a load-bearing invariant: omitting it causes every detail / write endpoint to 404 the tenant. Step 6.20.1 fixed the original Step 6.11.2 implementation which omitted this write; cleanup of orphan rows (POST-created tenants without org_nodes) was completed pre-fix.

### Slug derivation for tenant-root code / path

Pure-function helper `slug_for_tenant_root` in `repositories/tenants.py`. Input is `display_code` (if non-None) else `name`. Output is `(code, path)`:

- ASCII normalisation via `unicodedata.normalize('NFKD', ...)` + diacritic strip
- lowercase, non-alphanumerics collapsed to single `-`, leading/trailing `-` trimmed
- truncated to 64 chars (DDL CHECK `ck_org_nodes_code_format` max length)
- empty result → 422 `INVALID_TENANT_NAME_FOR_SLUG`
- `code` = result uppercased; `path` = result with `-` → `_` (ltree label requirement)

Examples:

- `Buc-ee's` → code `BUC-EE-S`, path `buc_ee_s` (mechanical slug; differs from seed's editorial `BUC-EES`)
- `Żabka Group` → code `ZABKA-GROUP`, path `zabka_group`
- `!!!` → 422

POST-created tenants use the mechanical slug. Seed-loaded tenants retain their curated codes (e.g. `BUC-EES`) because the seed loader inserts org_nodes directly with the Excel-specified code. A future PATCH-on-tenant-root surface would allow editorial override post-create.

---

## A.4 — Two-table-one-entity coupling

A small number of resources in the master DB are represented across two tables that together form one logical entity. When this is the case, every write that touches either table flows through one API endpoint, which performs all writes atomically inside a single request transaction. No endpoint mutates one half of the pair without the other.

This pattern applies when all three conditions hold:

1. The two tables share a 1:1 relationship enforced by schema (FK plus a partial or full UNIQUE constraint on the linking column).
2. The entity has no meaningful existence without both rows. A row in either table without its counterpart is, by domain definition, malformed.
3. Field-uniqueness or ID-assignment for the entity requires both rows to be in place at the same time (e.g., tenant-uniqueness checks that the schema enforces on the second table).

The pattern is narrow. It does NOT generalize to "every multi-table write is atomic." Most cross-table operations in the master DB are between distinct entities (tenants vs users, users vs role-assignments) and the writes stay separate; a failure in the second one does not corrupt the first one.

Two seams currently use this pattern:

- **Tenant + tenant-root org_node.** See § A.3.
- **Store + STORE-type org_node.** See § A.5.

A third candidate exists in principle (tenant_users and their FK back to tenants) but does not meet condition (2): a tenant can exist without any tenant_users, and a tenant_user without a tenant is structurally impossible at the FK layer. That relationship is normal parent/child, not one-entity-two-tables.

### Implementation rules

When this pattern applies:

- **RBAC** gates the caller's semantic intent, not the storage layer. One permission tuple covers the whole atomic write. Example: `POST /api/v1/stores` is gated by `ADMIN.STORES.CONFIGURE.TENANT` alone, even though it writes both `core.stores` and `core.org_nodes`. The org_node write is an implementation consequence of the "create a store" intent, not a separate authorization concern.

- **Field ownership** is one-directional. When two tables share a field (e.g., a store's `name` and its paired org_node's `name`), the field has one owner endpoint. Writes to the other endpoint reject attempts to modify the shared field with a clear 422. The shared-field rule is enforced in the router (which can see the target row's type from a pre-fetch), not in the request schema (which can't).

- **Cascade semantics** are spelled out per shared field. The owner endpoint, when modifying a shared field, propagates the change to the other table in the same transaction. The cascade is not a database trigger; it is application code, because the value transformation may be non-trivial (see the store-status to org_node-status mapping in § A.5).

- **Lifecycle separation** is preserved where the two tables hold semantically distinct lifecycles. Where the lifecycles diverge (different enum value sets, different transitions), a mapping function in the repo layer projects one to the other. The mapping is named, tested, and documented at the seam.

### Forward-notes

Each instance of this pattern carries deferred questions about whether the projection between the two tables should be tightened, untangled, or made the schema's responsibility instead of the application's. See the per-instance sections.

---

## A.5 — Store create / update transaction shape (Step 6.21.2)

`POST /api/v1/stores` (TENANT-audience, gated `ADMIN.STORES.CONFIGURE.TENANT`) writes two tables in a single request transaction:

1. `core.org_nodes`: the STORE-type tree slot. `node_type='STORE'`, `parent_id` = the caller-supplied `parent_org_node_id`, `path` derived from `parent.path || own_code`, `name` and `code` copied verbatim from the store request (`stores.name` to `org_nodes.name`; `stores.store_code` to `org_nodes.code`), audit-actor pair populated from JWT. DDL CHECK `ck_org_nodes_root_parent_consistency` is satisfied because `node_type='STORE'` requires `parent_id IS NOT NULL`.
2. `core.stores`: the store row itself. `org_node_id` set to the org_node id from step 1 (creating the 1:1 link), audit-actor pair populated from JWT.

Both writes are atomic. A failure at either step rolls back the entire transaction.

`PATCH /api/v1/stores/{store_id}` and `POST /api/v1/stores/{store_id}/set-status` follow the same shape: both endpoints write to both tables under a single permission gate. PATCH cascades `name` / `store_code` / `parent_org_node_id` changes to the paired org_node; set-status cascades `status` changes per the mapping below.

### Field ownership and shared-field rule

Three fields are present on both `stores` and the paired `org_nodes` row:

| Concept | `stores` column | `org_nodes` column |
|---|---|---|
| Display name | `name` | `name` |
| Short code | `store_code` | `code` |
| Lifecycle status | `status` (4 states) | `status` (3 states) |

All three are owned by the `/stores` endpoints. `PATCH /api/v1/tenants/{tenant_id}/org-tree/{node_id}` on a STORE-type target rejects any attempt to modify these fields with 422 `ORG_NODE_FIELD_NOT_ALLOWED_FOR_TYPE`. The handler pre-fetches the node, checks its type, and applies a field allowlist before delegating to the repo. Non-STORE nodes are unaffected: same fields mutable as before.

The reverse direction is symmetric for entity creation: `POST /api/v1/tenants/{tenant_id}/org-tree` rejects `node_type='STORE'` with 422. Stores can only be created via `POST /api/v1/stores`.

### Parent ownership: dual-endpoint write

Of the four mutable concepts on a store's tree slot, three (name, code, status) are single-endpoint. The fourth, the tree parent (`org_nodes.parent_id`), is writable from either endpoint:

- `PATCH /api/v1/stores/{store_id}` with `parent_org_node_id` set, OR
- `PATCH /api/v1/tenants/{tenant_id}/org-tree/{node_id}` (where the node is STORE-type) with `parent_id` set.

Both produce the same UPDATE on `core.org_nodes.parent_id` and the same ltree path rewrite. The frontend picks whichever endpoint fits its UX (drag-and-drop in tree view to /org-tree; "change parent" dropdown in store editor to /stores).

`core.stores.org_node_id` is NEVER modified after the initial INSERT. The store's "tree position" concept is expressed entirely by the paired org_node's `parent_id`, not by changing the store's link.

### Status mapping

The two status enums do not line up 1:1:

- `stores.status`: `OPENING / ACTIVE / INACTIVE / CLOSED`
- `org_nodes.status`: `ACTIVE / INACTIVE / ARCHIVED`

The projection from store status to org_node status:

| `stores.status` | becomes | `org_nodes.status` |
|---|---|---|
| OPENING | -> | ACTIVE |
| ACTIVE | -> | ACTIVE |
| INACTIVE | -> | INACTIVE |
| CLOSED | -> | ARCHIVED |

The mapping is implemented as a module-level constant in `repositories/stores.py` (`STORE_STATUS_TO_ORG_NODE_STATUS`). Both `StoresRepo.create` and `StoresRepo.transition` read from the same map.

The mapping loses information: OPENING and ACTIVE both project to ACTIVE on the org_node side. An observer of the org_node alone cannot distinguish a pre-opening store from an operational one. **Forward note:** this is acceptable for v0 because no current consumer reads the org_node status independently of the store status. The collapse is reconsidered when (a) a dashboard or external service needs to see "this store is being prepared" from the org_node side, or (b) the next two-table-one-entity seam introduces a similar mismatch and a generalized lifecycle-extraction pattern becomes warranted.

### Closed-state triplets

`stores.status='CLOSED'` populates the `closed_*` audit triplet on the store row. The matching `org_nodes.status='ARCHIVED'` populates the `archived_*` audit triplet on the org_node row. Both triplets are written in the same transaction with the same actor and timestamp. Reverting out of CLOSED nulls both triplets symmetrically.

### Schema constraint tightening

Step 6.21.2 includes a DDL change: `ALTER TABLE core.stores ALTER COLUMN org_node_id SET NOT NULL`. Pre-Step-6.21.2, `stores.org_node_id` was nullable because the original POST endpoint accepted NULL. After Step 6.21.2, every write produces a non-NULL value by construction, and the schema enforces this at the storage layer. Existing NULL rows in dev (7 in Buc-ee's, smoke-test debris) are deleted before the ALTER runs. No production tenant has any NULL rows.

The pre-existing partial UNIQUE index `uq_stores_org_node_id ... WHERE org_node_id IS NOT NULL` becomes equivalent to a total UNIQUE constraint once the column is NOT NULL. The partial form is preserved (no DDL change to the index) for migration simplicity.

### Historical context

The original Step 6.17.3 (Stores writes) accepted `org_node_id` directly on the POST body and made no attempt to create or link an org_node automatically. This allowed two failure modes that surfaced in dev after Step 6.13 (Add Org Node) shipped:

- Stores with `org_node_id=NULL` (created via POST /stores without the optional field).
- STORE-type org_nodes with no matching stores row (created via POST /org-tree).

Step 6.21.2 closes both gaps. The catalogue cleanup (delete the 7 + 8 orphan rows) precedes the ALTER COLUMN migration in the deploy sequence.

### Cleanup operations (one-time, pre-deploy)

Operator-run SQL on Cloud SQL (queries in the Step 6.21.2 impl prompt's Appendix A; run during Phase 6 before the migration runs):

- Delete the 7 NULL-`org_node_id` rows in `core.stores` belonging to Buc-ee's.
- Delete the 8 orphan STORE-type rows in `core.org_nodes` belonging to Buc-ee's (those with no matching `stores` row).

No production tenant is on Cloud SQL yet (this is dev-only debris from smoke testing).

---

# Cross-references

- **CLAUDE.md** for decisions with reasoning, working rules, code conventions, environment variables, testing discipline.
- **BUILD_PLAN.md** for the step-by-step sequence to build this architecture.
- **docs/api-contract.md** (when produced at Step 2.0) for the frontend-locked API contract.
- **docs/gcp-provisioning-runbook.md** (Step 1.7.2) for DevOps provisioning details.

---

# End of architecture document
