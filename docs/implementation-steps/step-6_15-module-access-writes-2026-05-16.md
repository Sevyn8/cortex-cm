# Step 6.15 — Tenant module access write endpoints

**Shipped.** 2026-05-16 in a single commit per the WORKFLOW.md default.

- 2 endpoints under `/api/v1/module-access/{tenant_id}/{module_code}/` — POST enable (upsert), POST disable.
- PLATFORM-only (`audience="PLATFORM"`); gated on `ADMIN.TENANTS.OVERRIDE.GLOBAL` (SUPER_ADMIN per Phase 3 seed). Same privilege boundary as tenant suspend/activate.
- Idempotent-200 on no-op cells (LD4): enable on ENABLED, disable on DISABLED both return 200 with no row mutation. Disable on missing returns 404 `MODULE_ACCESS_NOT_FOUND`. No 409 `INVALID_STATE_TRANSITION` on this surface; the asymmetry vs tenants suspend/activate is captured as FN-AB-42.
- `ModulesAccessRepo.enable / .disable` use `SELECT FOR UPDATE` on `(tenant_id, module)` with one `IntegrityError` retry on the INSERT branch (LD8). LD5 overwrite semantics on `enabled_at` + `enabled_by_user_id`.
- 1 new error class (`ModuleAccessNotFoundError`), 1 new schema (`ModuleAccessRead`), 1 new local enum (`TransitionResult` in `modules_access.py`).
- 21 new tests (14 router, 4 repo, 2 schema, 1 error envelope). 6 LOAD-BEARING.
- Cloud deploy deferred per Phase 5.5 operator pause.

**Result.** Pytest 416 → 437 (+21). 0 xfail. mypy strict clean (73 src files). check_setup 36/36. smoke_curl 38/38 local. Per-resource regression checkpoint clean. No DDL changes; no migrations; no seed Excel changes.

---

## Implementation Plan

Single commit per WORKFLOW.md default. 18 files touched: 4 new test
files, 14 modify, 1 regen (openapi.json), plus 2 new doc files (step
doc + prompt).

Public surface:
  - POST   /api/v1/module-access/{tenant_id}/{module_code}/enable
  - POST   /api/v1/module-access/{tenant_id}/{module_code}/disable

Both PLATFORM-only (`audience="PLATFORM"`); gated on
`ADMIN.TENANTS.OVERRIDE.GLOBAL` + `anchor_dep=get_tenant_anchor`.

Code:
  - src/admin_backend/errors.py — `ModuleAccessNotFoundError` (404,
    `MODULE_ACCESS_NOT_FOUND`).
  - src/admin_backend/schemas/modules_access.py — `ModuleAccessRead`
    (8 fields; audit-actor IDs hidden per H1).
  - src/admin_backend/schemas/__init__.py — re-export.
  - src/admin_backend/repositories/modules_access.py — `enable`,
    `disable` methods; private helpers `_select_for_update`,
    `_insert_enabled`, `_apply_enable_transition`,
    `_apply_disable_transition`, `_refetch`; module-local
    `TransitionResult` enum.
  - src/admin_backend/routers/v1/modules_access.py — 2 handlers;
    reuses `get_tenant_anchor` from `auth/anchor_deps.py` and the
    `audience="PLATFORM"` kwarg from `auth/permissions.py:require`.

Tests:
  - tests/integration/test_module_access_writes_router.py — NEW.
    14 tests: C1-C6 transition matrix cells, P1-P4 permission
    boundary, V1 path validation, AUD-1 layer ordering, R1-R2
    regression flows. Local helper `_make_tenant_with_root` pairs
    `make_tenant` with `make_org_node(node_type='TENANT')` so the
    gate's anchor_dep resolves.
  - tests/integration/test_module_access_repo_writes.py — NEW.
    4 tests: RT1 (FOR UPDATE in-transaction), RT2 (LD8 retry post-
    race), RT3 (LD5 overwrite across transactions), RT4 (no-op
    leaves `updated_at` unchanged).
  - tests/unit/test_module_access_schemas.py — NEW. 2 tests.
  - tests/unit/test_module_access_errors.py — NEW. 1 test.
  - tests/integration/test_gate_discipline.py — `_PLATFORM_ONLY_WRITE_ROUTES`
    extended from 4 to 6 tuples.

Smoke + cloud:
  - scripts/smoke_curl.sh — WHAT'S CHECKED 32 → 38. 6 new entries
    selecting a seeded tenant with anchor + 2 unused modules; TENANT
    audience-deny uses the TJWT's own tenant_id (Layer 1 must run
    after the anchor dep resolves under PLATFORM impersonation).
  - scripts/test_endpoints.sh — Phase 4d block mirroring smoke flow.
  - scripts/test_endpoints_cloud.sh — Phase 4d block mirroring.
  - docs/endpoints/openapi.json — regenerated.

Docs:
  - docs/architecture_RBAC.md — 1 worked example slotted between
    tenants suspend/activate and POST `/tenant-users`.
  - docs/endpoints/module-access.md — 2 new operation sections.
  - CLAUDE.md — Current-state entry; FN-AB-42, FN-AB-43.
  - BUILD_PLAN.md — Step 6.15 flip TODO → DONE-LOCAL; Step 6.7
    Scope-out wording correction (cascade is structural, not
    imperative).
  - prompts/step-6_15-impl-2026-05-15.md — bundled per per-step
    convention.

---

## Mental model

Module access is a per-tenant per-module entitlement bit with audit
columns. v0 supports ENABLED / DISABLED on existing rows plus an
upsert seam (enable on a missing row inserts it). The transition
endpoints are PLATFORM-only because module enablement is a
commercial concern (Ithina staff territory), not a tenant
self-service one.

The cascade is structural, not imperative. The TENANT path of
`has_permission()` JOINs `tenant_module_access` filtered to
`status='ENABLED'`. Disabling a module makes the JOIN miss; every
TENANT-side permission check against that module returns false on
the next request. No imperative revocation pass is required; re-
enable restores access automatically per D-24 (identity-only JWT,
per-request resolution).

The transition matrix is intentionally idempotent. Module flips
happen in commercial / operational workflows (upsell, downgrade,
trial extension) where re-asserting the current state should not
fail. The 6 cells are:

| Current      | enable                          | disable                          |
|--------------|---------------------------------|----------------------------------|
| missing      | INSERT new ENABLED, 200         | 404 `MODULE_ACCESS_NOT_FOUND`    |
| DISABLED     | UPDATE to ENABLED (LD5), 200    | 200 no-op                        |
| ENABLED      | 200 no-op                       | UPDATE to DISABLED (LD5), 200    |

Same gate tuple as tenant suspend/activate (OVERRIDE.GLOBAL), same
audience pattern, different no-op semantics — captured as FN-AB-42.

---

## DDL facts (verified live)

`core.tenant_module_access`:

- PK `id UUID DEFAULT core.uuidv7()`.
- UNIQUE `(tenant_id, module)` — arbiter for the upsert race.
- `module module_code_enum NOT NULL` (6 PG enum values; the Python
  `ModuleCode` enum carries 5 — ROOS retired Python-side 2026-05-12).
- `status module_access_status_enum NOT NULL` (ENABLED / DISABLED).
- `enabled_at TIMESTAMPTZ NOT NULL`, `enabled_by_user_id UUID NOT NULL`.
- `disabled_at`, `disabled_by_user_id` NULL or both-non-NULL
  (`ck_tenant_module_access_disabled_pair`); status-consistency
  pairs `status='ENABLED'` ↔ `disabled_at IS NULL`.
- Pattern (a) audit-actors: typed FKs to `core.platform_users(id)`,
  ON UPDATE/DELETE RESTRICT. PLATFORM-only audience makes this
  structurally satisfiable.
- BEFORE-UPDATE trigger `tg_tenant_module_access_set_updated_at`
  bumps `updated_at` only.
- RLS ENABLED + FORCED; policy uses D-29 unconditional OR-branch
  plus D-27 NULLIF wrapper.

---

## Retro

### What shipped

Two write endpoints landed clean. pytest 416 → 437 (+21, all
passing, no xfails). mypy strict clean (73 source files).
check_setup 36/36. smoke_curl 38/38. Per-resource regression
checkpoint held across 15 pre-existing files.

All 8 locked decisions (LD1-LD8) honored end-to-end:
  - LD1 URL shape under `/api/v1/module-access/` (writes follow reads).
  - LD2 audience=PLATFORM (Layer 1 refusal on TENANT JWTs).
  - LD3 `ADMIN.TENANTS.OVERRIDE.GLOBAL` (SUPER_ADMIN only).
  - LD4 idempotent-200 on no-op cells; 404 only on disable-on-missing.
  - LD5 `enabled_at` + `enabled_by_user_id` overwrite on every
    DISABLED → ENABLED flip; preserved on disable.
  - LD6 200 OK on every successful response (no 201).
  - LD7 path-param `module_code: ModuleCode` (FastAPI 422 on invalid).
  - LD8 SELECT FOR UPDATE + IntegrityError retry on the INSERT branch.

### What worked

**Pre-flight DDL check (3a + 3b + 3c).** All three passes confirmed
the prompt's DDL facts. No drift between snapshot and live DB. The
migration history sanity check (3c) is new-ish discipline since
WORKFLOW.md A4; the 30-second cost catches a real foot-gun (a
schema migration shipping without a snapshot refresh would have
left this step's prompt off-spec).

**Per-resource transition enum (TransitionResult local to the
module).** Mirrors `TenantsRepo`'s pattern (separate enum, same
shape). Prompt explicitly locked this so cross-resource transition
semantics stay decoupled — easier to extend per resource. The two
enums happen to share `OK` / `NOT_FOUND` values today but the
locked separation leaves room for `INVALID_STATE` on tenants vs
absence on modules.

**Repo-test multi-session pattern for time-bearing assertions.**
RT3 (LD5 overwrite verification) initially failed under a single
`platform_session` because Postgres `now()` is fixed within a
transaction. Switched to separate `get_tenant_session` invocations
per phase so each `now()` returns a fresh timestamp. The fix isn't
mechanical — it's the production-shape mirror (each enable / disable
happens in a separate request transaction in real traffic).

### What did not

**Initial router-test assumption that `make_tenant` produces a
fully-routable tenant.** It doesn't — `make_tenant` inserts only
the `tenants` row; `get_tenant_anchor` needs a paired tenant-root
`org_node`. The first router-test pass failed all 13 routed tests
on 404 `TENANT_NOT_FOUND` from the anchor dep. Resolution: a local
helper `_make_tenant_with_root` that pairs `make_tenant` with
`make_org_node(node_type='TENANT')`. Caught at first pytest run;
fixed in one round.

The org_node `code` column has a CHECK constraint
`^[A-Za-z0-9][A-Za-z0-9-]{0,62}[A-Za-z0-9]$` — underscores aren't
allowed. First attempt used `f"test_{tenant.id.hex[:8]}"` which
violates; fixed to hyphen-separated `f"t-{tenant.id.hex[:8]}"`.

Both findings are factory-pairing concerns that the existing
conftest fixtures don't explicitly call out; worth a forward note
if a future write step uses `get_tenant_anchor` and creates
synthetic tenants for testing. Captured in this step's CLAUDE.md
Current-state entry.

**Layer-1-vs-anchor ordering in router tests P1/P2/AUD-1.** Initial
tests used a random `tenant_id` in the URL, expecting Layer 1
(audience) to fire on the TENANT JWT. Reality: FastAPI resolves
the anchor dep BEFORE the gate body, so an unreachable tenant_id
404s at the anchor lookup ahead of either Layer 1 or Layer 2. Fixed
by using a real tenant + tenant-root that the test creates, then
asserting Layer 1 fires after the anchor resolves. Documented in
the test docstrings.

**Smoke tenant selection.** Initial smoke implementation picked the
first visible tenant from `/tenants?limit=50`, which on a freshly
re-seeded local DB picks up smoke-created tenants from prior runs
(those have no org_node root). Fixed: probe `/tenants/{id}/org-tree`
returning 200 as the anchor-reachability check before considering
a tenant. The smoke ended up structurally tolerant to the leaked
tenants the previous step's flows leave behind.

**Smoke TENANT audience-deny.** Same FastAPI Depends-order issue
at the smoke level: the TENANT JWT (Marcus T, Buc-ee's) probing a
tenant the smoke chose (different tenant) 404s at the anchor
because RLS filters Buc-ee's-only visibility. Fixed: extract the
TJWT's `tenant_id` claim and use that for the audience-deny call
so the anchor dep resolves.

### Forward notes

Two FN-AB entries landed in CLAUDE.md at commit time:
  - FN-AB-42 — Cross-resource transition-matrix asymmetry (modules
    idempotent-200 vs tenants 409). Revisit at Step 6.16 when
    audit-log emission surfaces the billing-mutation audit-trail
    differences concretely.
  - FN-AB-43 — Module-access schema evolution under billing/payments.
    Stage 3 or post-v0 billing-integration design step.

### Metrics

  pytest                 416 → 437  (+21)
  mypy strict            73 source files (unchanged)
  check_setup            36/36 PASS
  smoke_curl             38/38 PASS (local)
  test_endpoints local   272 calls total (266 prior + 6 new); 20
                         pre-existing stale-expectation failures
                         unrelated to this step (per the WORKFLOW.md
                         gate-retrofit discipline note); all 6 new
                         Step 6.15 entries pass.
  alembic head           unchanged at a0982a86985b (no migration).

  EXPLAIN ANALYZE:
    SELECT FOR UPDATE on (tenant_id, module)   Index Scan, < 1 ms
    INSERT new row                              one round-trip, < 1 ms
    UPDATE existing                             one round-trip, < 1 ms

  Lines of code:
    src/  ~ +220 net (errors +18, schemas +75, repo +180, router +85,
                      offset by 0 deletions)
    tests/ + ~ 850 across 4 new files
    docs/ + ~ 320 across architecture_RBAC + module-access endpoint doc
