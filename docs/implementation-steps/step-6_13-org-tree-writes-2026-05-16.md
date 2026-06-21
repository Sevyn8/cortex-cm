# Step 6.13 — Org-tree write endpoints (Add Node + Edit Node)

**Shipped.** 2026-05-16 in a single commit per the WORKFLOW.md default.

- 2 endpoints on the existing org-tree router: POST add (under existing parent), PATCH edit (rename / recode / reparent).
- Multi-audience write (`audience=None`); gated on `ADMIN.ORG_NODES.CONFIGURE.TENANT`. SUPER_ADMIN and PLATFORM_ADMIN pass via GLOBAL->TENANT cascade (closed at Phase 3b — FN-AB-47). OWNER passes via direct .TENANT grant.
- Cascade-order rule enforced app-side: parent.node_type ordinal must be strictly less than child's. Skipping allowed.
- Reparent rewrites the moved node's path plus every descendant's path in one atomic SQL UPDATE using ltree's `subpath` and `||` operators.
- Role assignments anchored at moved nodes remain intact (D-11: assignment references stable id, not path). Verified by E10.
- 6 new error classes (`InvalidParentNodeTypeError`, `InvalidParentStateError`, `TenantRootNotReparentableError`, `CycleDetectedError`, `DuplicateOrgNodeCodeError`, `ParentNodeNotFoundError`).
- 3 new schemas: `OrgNodeCreateRequest`, `OrgNodePatchRequest`, `OrgNodeRead`.
- 42 new tests (29 router, 6 repo, 4 schema, 3 errors). 10 LOAD-BEARING including 2 PLATFORM_ADMIN cascade tests closing FN-AB-47's coverage gap.

**Catalogue gap closure (FN-AB-47).** Pre-flight item 5 fired: OWNER lacked `ADMIN.ORG_NODES.CONFIGURE.TENANT`, and PLATFORM_ADMIN had no `ADMIN.ORG_NODES.*` grants of any kind (so GLOBAL->TENANT cascade had nothing to resolve through). Operator applied a Phase 3b seed update before implementation started:

- +2 permission rows: `ADMIN.ORG_NODES.CONFIGURE.GLOBAL`, `ADMIN.ORG_NODES.VIEW.GLOBAL`.
- +5 role_permissions: SUPER_ADMIN -> both new GLOBAL tuples, PLATFORM_ADMIN -> both new GLOBAL tuples, OWNER -> `ADMIN.ORG_NODES.CONFIGURE.TENANT`.

Local DB: 31 permissions -> 33; 122 role_permissions -> 127. `EXPECTED_VISIBLE_COUNTS_PLATFORM` in `tests/integration/test_seed_loader.py` updated accordingly.

Cloud SQL update is DEFERRED to the next Phase 6 deploy cycle. Cloud test_endpoints.sh entries for org-tree write flow may produce 403 PERMISSION_DENIED on OWNER and PLATFORM_ADMIN paths until cloud catches up; SUPER_ADMIN happy paths and TENANT-no-grant denies are reliable in the interim.

**Result.** Pytest 463 -> 505 (+42). 0 xfail. mypy strict clean (73 src files). check_setup 36/36. smoke_curl 47/47 local. Per-resource regression: `test_org_tree_router.py` (Step 5.3 read-side) stays at 21. `test_seed_loader.py` 5/5 (counts updated for the catalogue addition). No DDL changes; no migrations.

---

## Implementation Plan

Single commit. 18 files touched.

Public surface:
  - POST   /api/v1/tenants/{tenant_id}/org-tree                  (Add Node)
  - PATCH  /api/v1/tenants/{tenant_id}/org-tree/{node_id}        (Edit Node)

Both multi-audience (`audience=None`); gated on
`ADMIN.ORG_NODES.CONFIGURE.TENANT` + `anchor_dep=get_tenant_anchor`.

Code:
  - src/admin_backend/errors.py — 6 new ClientError subclasses.
  - src/admin_backend/schemas/org_node.py — Create/Patch/Read schemas.
  - src/admin_backend/repositories/org_nodes.py — `add_node`, `edit_node`;
    helpers `_check_cascade_order`, `_select_for_update_node`,
    `_refetch_by_id`, `_map_code_uniqueness_violation`,
    `_is_descendant`, `_path_label`; module-level `_ORDINAL_MAP`.
  - src/admin_backend/routers/v1/org_tree.py — 2 new handlers
    (`add_org_node`, `edit_org_node`). Tenant-root reparent guard at
    the router (reads target node_type via raw SQL before delegating
    to repo).

Tests:
  - tests/integration/test_org_tree_writes_router.py — NEW. 29 tests.
  - tests/integration/test_org_tree_repo_writes.py — NEW. 6 tests.
  - tests/unit/test_org_tree_writes_schemas.py — NEW. 4 tests.
  - tests/unit/test_org_tree_writes_errors.py — NEW. 3 tests.

Smoke + cloud:
  - scripts/smoke_curl.sh — WHAT'S CHECKED 42 -> 47. 5 new entries:
    add STORE, rename, reparent, cascade reject, duplicate-code reject.
    (Tenant-root reparent reject verified by integration E7; can't be
    exercised from smoke because the read endpoint excludes the root
    from its response by design.)
  - scripts/test_endpoints.sh — Phase 4e block mirroring smoke + 1
    TENANT-no-grant deny under T1 caller.
  - scripts/test_endpoints_cloud.sh — Phase 4e mirror. Note: pre-cloud
    Phase 6 deploy, some entries may produce 403 until the catalogue
    update lands.
  - docs/endpoints/openapi.json — regenerated.

Docs:
  - docs/endpoints/org-tree.md — POST + PATCH operation sections in the
    canonical 8-section format.
  - CLAUDE.md — Current-state pointer to this step doc; FN-AB-47.
  - BUILD_PLAN.md — Step 6.13 flip TODO -> DONE-LOCAL with 1-2 sentence
    scope summary.
  - prompts/step-6_13-impl-2026-05-16.md — bundled per per-step
    convention.

---

## Mental Model

The Add and Edit endpoints sit on top of an existing read-only router
and a read-only Repo. The natural extension preserves the read API
unchanged; the write surface gets new methods on `OrgNodesRepo` and
new handlers on `org_tree.py`.

The novel piece is ltree path maintenance. ltree paths are stable
materialisations of the parent chain; if the chain changes (reparent),
every descendant's path must move with the node. The single SQL UPDATE
that handles subtree re-pathing uses `subpath(path, nlevel(old_prefix))`
to extract everything below the old prefix and prepends the new prefix
— atomic, one statement, descendant count irrelevant.

The cascade-order rule keeps the hierarchy semantically valid (a Store
can't have an HQ under it). It's a pure-function check; no DB query.

The tenant-root protection is structural — the DDL CHECK
`ck_org_nodes_root_parent_consistency` rejects any TENANT-type row
with non-NULL parent_id. The app-layer guard (router-side, reading
node_type before delegating to the repo) catches the attempt earlier
and returns a clean 422 `TENANT_ROOT_NOT_REPARENTABLE` instead of a
DB-level CHECK violation.

Role assignment stability falls out for free. Assignments reference
the org_node by its `id` UUID; `path` is incidental to RBAC. Moving a
node updates the path; the assignment's `org_node_id` stays the same;
`has_permission()`'s anchor JOIN traverses via the new path
automatically. No imperative cascade.

---

## Retrospective

**What landed cleanly.**

- Repo writes mirror the established shape (raw SQL with schema
  qualification, `SELECT FOR UPDATE` before mutate,
  `session.expire_all()` after) so the patterns from Step 6.11
  (TenantsRepo writes) and Step 6.15 (ModulesAccessRepo writes) carried
  forward without surprise.
- Pydantic-level validation (`extra="forbid"`, format regex, the
  `_at_least_one_field` model_validator on PATCH) caught most of the
  edge cases before the handler ran; the router and repo only deal with
  business-logic errors.
- The subtree re-path SQL works in one statement using ltree primitives.
  No multi-row loop, no application-side path manipulation past the
  first segment derivation.

**What surprised.**

- The prompt's V3 catalogue entry ("DEPARTMENT under REGION rejected as
  reversal") is incorrect under the cascade-order rule as documented:
  DEPARTMENT (ord 6) > REGION (ord 4), so this is allowed level
  skipping. Repurposed V3 to test STORE-under-STORE (same-ord reject),
  which is the canonical equal-ordinal reject case. The cascade-order
  rule is consistent; the prompt's example was off.
- Fixture-order discipline matters here too. `make_tenant(with_root=True)`
  + `tenant_owner_jwt_factory` + test-direct `make_org_node` calls all
  reference the same tenant root. `make_tenant`'s teardown DELETEs the
  root and the tenant; if `tenant_owner_jwt_factory`'s sub-fixtures
  (especially `make_tenant_user_role_assignment` with composite FK to
  the root) tear down LATER than `make_tenant`, the FK violation
  surfaces. Fix: list `make_tenant` BEFORE `tenant_owner_jwt_factory`
  in test signatures so `make_tenant` tears down LAST. Mirrors existing
  pattern in `test_tenant_users_writes_router.py`.

**What the gate retrofit gap looked like at pre-flight.**

OWNER seed had `ORG_NODES.VIEW.TENANT` but no `CONFIGURE.TENANT`.
PLATFORM_ADMIN had no `ORG_NODES.*` grants at all. The gap was
documented in FN-AB-32 as a deferred housekeeping note; this step
forced the issue because the locked LD2 design requires both audiences
to pass. Operator pre-applied the Phase 3b seed update (Excel + reseed
local DB) before implementation began. Two new PLATFORM_ADMIN tests
(PA1 POST, PA2 PATCH) lock the cascade resolution end-to-end and prevent
a future seed regression from going undetected.

**Operator notes for cloud deploy.**

Cloud catalogue is still at 31/122. Cloud test_endpoints.sh Phase 4e
entries:

- `ot_flow__add_store`, `ot_flow__rename`, `ot_flow__reparent`,
  `ot_flow__cascade_reject`, `ot_flow__duplicate_code` (SUPER_ADMIN
  caller): reliable.
- `ot_flow__tenant_no_grant_deny` (T1 caller): reliable.

The OWNER and PLATFORM_ADMIN paths are not exercised by the cloud
endpoint script (smoke uses SUPER_ADMIN only); integration tests cover
them locally. When the next Phase 6 deploy lands, the catalogue update
should be bundled with it.

---

## Open follow-ups

- **FN-AB-47 cloud catalogue sync** — track in CLAUDE.md as new entry.
  Closes when cloud DB carries the +2 permissions and +5 role_permissions
  rows.
- **Archive / delete on org_nodes** — out of scope for 6.13; defer to a
  future step when business explicitly asks. The cascade-order rule and
  cycle prevention shape the design; archiving an interior node would
  need a story for descendants (cascade vs orphan vs reject).
