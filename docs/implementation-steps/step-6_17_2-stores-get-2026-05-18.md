# Step 6.17.2 — Stores GET endpoints

**Shipped.** 2026-05-18 in a single commit on `main`.

## Mental Model

Reads-only on the Stores resource, plus the cleanup that retires the
2-column lightweight ``Store`` stub carried since Step 3.3. Two GET
endpoints under ``/api/v1/stores`` — list (with filters, search, 8 sort
keys, pagination) and detail (17 fields including the joined
``tenant_name`` label). Multi-user-type per the v0 auth model:
``ADMIN.STORES.VIEW.TENANT`` gates both endpoints, with PLATFORM
passing via the ``.GLOBAL``→``.TENANT`` scope cascade (SUPER_ADMIN,
PLATFORM_ADMIN) and TENANT OWNER passing via the direct ``.TENANT``
grant added at Step 6.17.1.

The detail route binds ``anchor_dep=get_store_anchor`` so cross-tenant
probes surface as 404 ``STORE_NOT_FOUND`` ahead of the gate body
(F-THREADING-4 / RLS-as-404 per D-17). The new anchor dep resolves the
store's tenant root via a single ``stores → org_nodes`` JOIN; on miss
it raises ``StoreNotFoundError`` rather than returning ``None`` (which
would short-circuit ``has_permission``'s cascade clause to TRUE — the
prompt's surface-and-stop scenario #5 motivation).

Locked decision 2 — ``tenant_name`` via LEFT JOIN to ``core.tenants``
rather than a correlated subquery — keeps the query plan a simple
nested-loop join (0.31 ms total time at v0 scale) and is the right
shape for a sibling-table label (not an aggregate). The other locked
decisions (URL shape, 8 sort keys, 4-filter vocabulary, 50/100
pagination, audit-actor columns hidden, NUMERIC-as-string serialisation
on lat/long, ``org_node_id`` as bare UUID without a name JOIN) all
honoured verbatim.

Deferred to follow-on steps: POST (6.17.3), PATCH (6.17.3),
change_status (6.17.4); ``org_node_name`` JOIN on detail and a sort key
for ``org_node_id`` (additive when frontend demands them).

## Implementation Plan

Single commit per the WORKFLOW.md default.

Public surface:
  - GET /api/v1/stores
  - GET /api/v1/stores/{store_id}

Code (NEW):
  - src/admin_backend/models/store.py — full ``Store`` ORM model + ``StoreStatus`` / ``TaxTreatment`` enums.
  - src/admin_backend/schemas/store.py — ``StoreListItem``, ``StoreListResponse``, ``StoreDetail``.
  - src/admin_backend/repositories/stores.py — ``StoresRepo``, ``SORT_MAP``, ``StoresListRow`` / ``StoreDetailRow``, ``DEFAULT_STORES_SORT``.
  - src/admin_backend/routers/v1/stores.py — 2 handlers, ``_list_item_from_row`` / ``_detail_from_row``.

Code (MODIFY):
  - src/admin_backend/auth/anchor_deps.py — ``+get_store_anchor``.
  - src/admin_backend/errors.py — ``+StoreNotFoundError``.
  - src/admin_backend/models/__init__.py — re-export Store + enums.
  - src/admin_backend/schemas/__init__.py — re-export 3 schemas.
  - src/admin_backend/repositories/tenants.py:53 — import swap (stub -> full model).
  - src/admin_backend/main.py — ``+include_router(stores_router)``.
  - src/admin_backend/models/_lightweight_stubs.py — DELETED (only ``Store`` was left).
  - tests/integration/conftest.py — ``Store`` import swap; ``make_store`` upgraded to ORM-native with new ``country`` / ``store_code`` / ``status`` / ``tax_treatment`` parameters.
  - tests/integration/test_dashboard_router.py — S6 country-override sites use the new ``country`` kwarg (3 lines simplified).

Tests (NEW):
  - tests/integration/test_stores_repo.py — 13 tests + parametrized R8 expands to 8 sub-tests; effective 20 collected.
  - tests/integration/test_stores_router.py — 14 tests + MG1 gate marker; 15 collected.

Smoke / endpoint scripts (MODIFY):
  - scripts/smoke_curl.sh — +2 (stores_list + stores_detail with first_id capture); WHAT'S CHECKED 47 -> 49.
  - scripts/test_endpoints.sh — +2 inside ``run_matrix_for_caller`` (stores_list + stores_detail_unknown); × 4 callers = +8 calls.
  - scripts/test_endpoints_cloud.sh — mirrors local.

Docs:
  - docs/endpoints/stores.md — NEW (8-section format).
  - docs/endpoints/openapi.json — REGEN; both new paths present.
  - CLAUDE.md — one-line pointer + 2 FN-AB additions deferred from 6.17.1.
  - BUILD_PLAN.md — 6.17.2 TODO -> DONE-LOCAL.
  - docs/implementation-steps/step-6_17_2-stores-get-2026-05-18.md — this file.

No DDL changes; no migrations; no seed Excel changes (the 6.17.1
catalogue is reused).

## Retro

- **Locked-decision honour record.** All 8 locked decisions honoured verbatim. The one surface-and-stop item (LD9 main.py routing claim of "alphabetical" — actual file is not alphabetical) was a documentation-vs-reality mismatch in the prompt; resolved by inserting ``stores_router`` between ``org_tree_router`` and ``rbac_router`` to preserve the file's de-facto grouping (tenant-data routers cluster).
- **Tests.** 35/35 new tests passed first run; pytest 505 -> 540 (+35); mypy strict clean on 76 src files (was 73, +3 — store.py model, store.py schema, store.py repo); smoke_curl 47/47 -> 49/49 local (after JWT refresh — pre-existing stale-token regen unrelated to the step). EXPLAIN ANALYZE on the LEFT JOIN list query: 0.312 ms total, nested-loop plan at seed scale (25 stores × 7 tenants).
- **Pre-existing condition surfaced (not blocking).** ``scripts/smoke_test.py`` (DB-level RLS truth-table smoke) requires the DB to be truncated and re-seeded; current dev DB state produces 21/81 pass independent of this step (verified via ``git stash``). The relevant CLAUDE.md historical note is "smoke test 81/81 PASS post-truncate". This step does not regress that floor.
- **Cloud deploy.** Deferred per Phase 5.5 operator pause; batched verification next cycle.
- **Workflow lesson — A7 (Phase 5 commit discipline).** Gap: the impl prompt enumerated source / test / docs / scripts / openapi-regen buckets and the report-shape template, but did NOT spec the commit itself. Symptom: CC closed at "tests green, 540 passing, working tree dirty" and reported back; the operator then wrote a separate "Stage and commit" prompt to land the commit. Cost: one extra operator-CC round trip. Fix: WORKFLOW.md A7 codifies that every impl prompt MUST spec the Phase 5 commit as its own work bucket, including the A6 message template inline, a `Commit:` line in the report shape, and the `<this-commit>` canonical placeholder for the CLAUDE.md current-state entry. Applied in the same retro commit as this section.
