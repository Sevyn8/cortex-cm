# Step 6.17.3 — Stores POST + PATCH endpoints

**Shipped.** 2026-05-18 in a single commit on `main`.

## Mental Model

Write surface for the Stores resource on top of Step 6.17.2's read
foundation. Two new endpoints — `POST /api/v1/stores` (create) and
`PATCH /api/v1/stores/{store_id}` (edit) — both multi-audience per
LD1 (diverges from tenants POST which is platform-only): PLATFORM
callers via the GLOBAL→TENANT cascade, TENANT OWNER via the
`.TENANT` grant added by Step 6.17.1's catalogue update. `tenant_id`
in the POST body is verified against the caller's RLS-bound session
via a `_tenant_exists` pre-check — cross-tenant ids by TENANT
callers surface as 404 `TENANT_NOT_FOUND` (RLS-as-404 per D-17)
instead of letting the stores RLS `WITH CHECK` predicate reject as
a 500 server error.

Status transitions are out of scope here (Step 6.17.4 owns the
`/change_status` endpoint with the lifecycle matrix and CLOSED-state
audit-triplet logic); the PATCH explicitly rejects `status`. The
PATCH also rejects `org_node_id` per LD3 — store ↔ org_node linkage
mutability is deferred pending a product workflow decision; future
loosening is additive per D-31 and so non-breaking.

`store_code` uniqueness is enforced case-insensitively per tenant.
Surface-and-stop finding: the prompt's LD5 stated "DDL has no UNIQUE
constraint" — actually the DDL ships a partial unique index
`uq_stores_tenant_store_code_lower` on `(tenant_id, lower(store_code))
WHERE store_code IS NOT NULL`. The repo's app-layer pre-check uses
`lower()` to align with the index; the DB index closes the race
window so the typed 409 path is reliable.

Audit-actor columns follow Pattern (b) per D-13: both halves of each
`*_by_user_id` / `*_by_user_type` pair populate on every write, with
the `actor_user_type_enum` cast supplied explicitly in the SQL per
the architecture_RBAC reference example. `_actor_type_from_auth` is
declared in the router (local 4-liner) mirroring the
`routers/v1/tenant_users.py` precedent rather than importing across
routers.

## Implementation Plan

Single commit per the WORKFLOW.md default.

Public surface:
  - POST /api/v1/stores
  - PATCH /api/v1/stores/{store_id}

Code (MODIFY only — no NEW files; 6.17.2 shipped the foundation):
  - src/admin_backend/schemas/store.py — +StoreCreateRequest, +StorePatchRequest.
  - src/admin_backend/errors.py — +DuplicateStoreCodeError, +OrgNodeNotForStoreError.
  - src/admin_backend/schemas/__init__.py — re-export 2 new schemas.
  - src/admin_backend/repositories/stores.py — +_tenant_exists, +_raise_if_store_code_taken, +_check_org_node_for_store, +create, +update.
  - src/admin_backend/routers/v1/stores.py — +_actor_type_from_auth, +create_store handler, +patch_store handler.

Tests (NEW):
  - tests/integration/test_stores_repo_writes.py — 17 tests (C1-C9 + U1-U8).
  - tests/integration/test_stores_writes_router.py — 24 tests (RC1-RC12 + RP1-RP11 + MG).

Smoke / endpoint scripts (MODIFY):
  - scripts/smoke_curl.sh — +3 entries (POST happy, PATCH happy, TENANT OWNER multi-audience happy). WHAT'S CHECKED 49 → 52.
  - scripts/test_endpoints.sh — Phase 4f stores write flow (3 entries).
  - scripts/test_endpoints_cloud.sh — mirrors local.

Docs:
  - docs/endpoints/stores.md — POST + PATCH sections appended in canonical 8-section format.
  - docs/endpoints/openapi.json — REGEN; both new operations present.
  - CLAUDE.md — one-line pointer + 1 FN-AB candidate (NOT NULL migration on store_code / tax_treatment) + 1 inline note on the LD5/LD8 prompt-vs-DDL contradictions.
  - BUILD_PLAN.md — 6.17.3 TODO → DONE-LOCAL.
  - docs/implementation-steps/step-6_17_3-stores-writes-2026-05-18.md — this file.

No DDL changes; no migrations; no seed Excel changes (the 6.17.1
catalogue is reused).

## Retro

- **Surface-and-stop findings (3, all resolved with documented context).** (a) LD5 said "DDL has no UNIQUE constraint" — DDL actually has `uq_stores_tenant_store_code_lower` partial unique index; the app-layer pre-check was switched to case-insensitive `lower()` comparison to align. (b) LD8 said "Status server-forced to OPENING via DDL default" — DDL default is actually `ACTIVE`; code honoured LD8's intent ("omit status from INSERT; DDL default fires") and the docs note the v0 default value with the product-intent OPENING deferred to a future migration. (c) LD1's "tenant_id verified against RLS-bound session" needed an app-layer pre-check (mirrors `TenantUsersRepo._tenant_exists`) to convert the RLS WITH CHECK rejection into a clean 404 TENANT_NOT_FOUND instead of a 500.
- **Tests.** 41/41 new tests passed; pytest 540 → 581 (+41 = 17 repo + 24 router); mypy strict clean on 76 src files (no count change — modifications only); check_setup 36/36; smoke_curl 49/49 → 52/52 after JWT refresh (pre-existing stale-token regen, unrelated to the step). The cleanup ordering of `cleanup_stores` (listed AFTER upstream factories, BEFORE `platform_session`) is load-bearing per the existing 6.11.2 fixture-order discipline note.
- **Cross-resource consistency note.** Smoke's third entry started as "TENANT no-grants → 403" per the prompt sketch, then shifted to "TENANT OWNER happy path → 201" once it surfaced that the seeded TENANT JWT (Marcus, Buc-ee's OWNER) holds the grant — so 403 wasn't reachable from smoke without a separate ungranted-TENANT user. Integration test RC7 covers the deny path with a synthetic random-UUID TENANT JWT; smoke's swap to the happy path validates the multi-audience contract end-to-end.
- **Cloud deploy.** Deferred per Phase 5.5 operator pause; batched verification with Step 6.17.4 at end of the 6.17 series.
