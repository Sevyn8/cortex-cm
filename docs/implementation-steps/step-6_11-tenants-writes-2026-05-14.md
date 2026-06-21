# Step 6.11 — Tenants write endpoints

**Shipped.** 2026-05-14 across two commits.

- **6.11.1** `f280f8a` — foundations: 4 ClientError subclasses, `audience` kwarg on `require()`, `TenantCreateRequest` + `TenantPatchRequest`, `TenantsRepo.create / update / transition`, `TransitionResult` enum.
- **6.11.2** (this commit) — 4 endpoints, 31 router tests + extended gate-discipline meta-test, smoke scripts +5 each, `architecture_RBAC.md` Appendix A, `docs/endpoints/tenants.md` extended, OpenAPI regenerated, CLAUDE.md + BUILD_PLAN.md updates.

**Result.** Pytest 319 → 385 (+66). 0 xfail. mypy strict clean (73 src files). check_setup 35/35. Per-resource regression checkpoint clean.

---

## Endpoints

| Method | Path | Gate | Action |
|---|---|---|---|
| POST | `/api/v1/tenants` | `audience="PLATFORM"` + `ADMIN.TENANTS.CONFIGURE.GLOBAL` | Provision tenant; force `status=TRIAL`; bundle `tenant_module_access` rows in same tx. |
| PATCH | `/api/v1/tenants/{tenant_id}` | same as POST | Partial update; `status`/`region` rejected; empty body → 422 `EMPTY_PATCH`; rename uniqueness pre-check excludes self. |
| POST | `/api/v1/tenants/{tenant_id}/suspend` | `audience="PLATFORM"` + `ADMIN.TENANTS.OVERRIDE.GLOBAL` | `TRIAL`/`ACTIVE` → `SUSPENDED`; populates `suspended_at` + `suspended_by_user_id`. |
| POST | `/api/v1/tenants/{tenant_id}/activate` | same as /suspend | `TRIAL`/`SUSPENDED` → `ACTIVE`; clears `suspended_*` on revert from SUSPENDED. |

OVERRIDE.GLOBAL held by SUPER_ADMIN only per the Phase 3 seed; PLATFORM_ADMIN holds CONFIGURE.GLOBAL but cannot suspend/activate. The catalogue encodes the privilege distinction; test S6 is the load-bearing regression.

---

## Retro

- **CONFIGURE vs OVERRIDE split caught the privilege model.** The catalogue Phase 3 ships SUPER_ADMIN with OVERRIDE.GLOBAL and PLATFORM_ADMIN without — the natural read was "let PLATFORM_ADMIN do everything platform-side." The session's locked decision 8 deliberately mapped POST/PATCH to CONFIGURE and suspend/activate to OVERRIDE so the catalogue can express "trusted operator vs full admin" without a new permission tuple. S6 (PLATFORM_ADMIN on /suspend → 403 PERMISSION_DENIED) makes this future-proof: a seed change that re-grants OVERRIDE to PLATFORM_ADMIN now fails CI.

- **Multi-audience PATCH explored and reverted at pre-flight.** Initial reading wanted TENANT OWNER to PATCH their own tenant. Pre-flight item 8 surfaced Pattern (a) FKs on `*_by_user_id` audit columns referencing `platform_users(id)` — a TENANT OWNER's user_id is in `tenant_users`, not `platform_users`, so UPDATE would fail the FK. The deferral (FN-AB-37) is over-cautious only if Pattern (b) is already in place; pre-flight confirmed Pattern (a) is the live shape. Step 6.16 (audit-log emission) bundles the same Pattern (b) migration concern for other write tables; deferring 6.11's multi-audience PATCH to that step is the natural bundle point.

- **D3 order-of-checks correction surfaced at pre-flight.** The prompt's Appendix A order block put `anchor_dep` resolution AFTER the gate body. Reading `auth/permissions.py:558-562` showed `anchor_dep` is declared as a FastAPI `Depends(...)` parameter on the inner gate function — so FastAPI resolves it BEFORE the gate body runs (anchor_dep 404 fires earliest, ahead of the audience or permission check). The prompt's "NOTE: confirm the live runtime order" explicitly invited the correction; the audience kwarg was sliced into the gate body's first action (before `has_permission()`), and Appendix A's order block was rewritten to match before landing in `architecture_RBAC.md`.

- **`session.expire_all()` after raw UPDATE.** SA's identity-map cached the Tenant ORM instance loaded during `create()`'s `get_by_id_with_aggregates`. Subsequent `update()`/`transition()` ran raw SQL UPDATE — bypassing ORM — which left the cached instance with stale `.status`, `.suspended_*`, `.updated_by_user_id`. The post-UPDATE `get_by_id_with_aggregates` returned the cached object verbatim. The fix is one line per method (`session.expire_all()`) and surfaces in test_rt5 (SUSPENDED → ACTIVE clears suspended_*) as the load-bearing assertion that catches the regression class.

- **Schema deviation: `number_of_stores_as_of_date` REQUIRED on create.** Prompt sketched it `date | None = None`, but `number_of_stores` is required + >=1 and DDL `ck_tenants_number_of_stores_as_of_consistency` mandates both-or-neither — the optional shape would have driven users into a CHECK violation surfacing as 500. Promoted to required at the schema layer; documented in the schemas-file section header.

---

## What's next

Cloud deploy via the standard 12-step workflow. After deploy:

- Step 6.12 (Stores writes) inherits the audience-kwarg pattern; the codification-as-convention question (when to require `audience="PLATFORM"`) gets confirmed by the second example.
- Multi-audience PATCH on tenants returns post-6.16 as its own step (FN-AB-37 owns the spec).
- FN-AB-35 (tenant name UNIQUE) is a 30-min additive migration when the schema-hardening pass runs.
