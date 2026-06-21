# Step 6.14 — Role-assignment writes (per-anchor `roles[]` + diff-replace)

**Shipped.** 2026-05-16 in a single commit per the WORKFLOW.md default.

- No new endpoints. Existing `POST /api/v1/tenant-users` and `PATCH /api/v1/tenant-users/{user_id}` change shape: `roles` field flips from `list[UUID]` to `list[RoleAssignmentItem]` (`{role_id, org_node_id}`). Tenant-root-only anchoring retired; any non-archived org_node in the same tenant is acceptable.
- Repo's whole-set-replace path retired in favor of diff-replace (LD3). Unchanged `(role_id, org_node_id)` tuples in the desired set retain their original `granted_at`, `granted_by_*`, and `updated_at`; only added or removed tuples produce DB writes.
- 3 new ClientError subclasses: `InvalidOrgNodeError` (422), `DuplicateRoleAssignmentInRequestError` (422), `RoleAssignmentConflictError` (409). `RoleAssignmentConflictError` catches `IntegrityError` ONLY when the constraint name matches `uq_tenant_user_role_assignments_active`; other IntegrityErrors propagate.
- LD4 validation order: roles → tenant visibility → org_nodes → email. Deterministic 422 envelope for tests.
- 26 new tests (13 router, 6 repo, 4 schema, 3 errors). 9 LOAD-BEARING. Resolves FN-AB-41 (anchored role bundling). Opens FN-AB-45 (cross-step behavioral shift documentation).
- Cloud deploy deferred per Phase 5.5 operator pause.

**Result.** Pytest 437 → 463 (+26). 0 xfail. mypy strict clean on 73 src files (per check_setup scope). check_setup 36/36. smoke_curl 42/42 local. Per-resource regression checkpoint clean. No DDL changes; no migrations; no seed Excel changes.

---

## Implementation plan (single commit)

Public-surface change is shape-only (URL + gate + audience unchanged):
- `POST /api/v1/tenant-users` — body `roles` shape `list[RoleAssignmentItem]`; `min_length=1`; within-request `(role_id, org_node_id)` dupes -> 422 at handler.
- `PATCH /api/v1/tenant-users/{user_id}` — body `roles` shape `list[RoleAssignmentItem] | None`; `None` means "no change", `[]` means "revoke all current ACTIVE", non-empty means diff-replace.

Code touchpoints:
- `src/admin_backend/errors.py` — 3 new ClientError subclasses.
- `src/admin_backend/schemas/tenant_user.py` — new `RoleAssignmentItem`, retype `roles` field, retire `_dedupe_role_ids` in favor of dedupe-by-tuple inside the model.
- `src/admin_backend/schemas/__init__.py` — re-exports.
- `src/admin_backend/repositories/tenant_users.py`:
  - Module-level alias `RoleAssignmentTuple = tuple[UUID, UUID]`.
  - `_resolve_role_audience` → `_validate_roles` (adds ARCHIVED check; aggregates missing+archived under INVALID_ROLE; audience-mismatch keeps its INVALID_ROLE_AUDIENCE code).
  - New `_validate_org_nodes` (aggregates missing/archived/cross-tenant under INVALID_ORG_NODE).
  - `_lookup_tenant_root` retired; replaced with simpler `_tenant_exists` (anchor is in the body now).
  - `_insert_role_assignments` retired in favor of `_apply_role_assignments_diff` (handles all three diff branches; per-pair INSERT loop because psycopg can't bind a list-of-tuples to `(role_id, org_node_id) = ANY(...)`).
  - New `_select_current_active_assignments_for_update` returning `set[RoleAssignmentTuple]`.
  - `create` and `update` rewritten to use the diff helper. Validation order per LD4.
  - IntegrityError caught only when constraint name matches `uq_tenant_user_role_assignments_active`; otherwise re-raise.
- `src/admin_backend/routers/v1/tenant_users.py`:
  - New `_flatten_role_assignments` handler-side helper: converts `list[RoleAssignmentItem]` to repo tuples AND raises `DuplicateRoleAssignmentInRequestError` (LD5).
  - `create_tenant_user` + `patch_tenant_user` call the helper before the repo call. Signatures unchanged.

Tests (4 files touched):
- `tests/integration/test_tenant_users_writes_router.py` — 31 existing tests mechanically retrofitted to new body shape (`role_ids=[X]` → `role_assignments=[(X, root_id)]`); P6 test name + docstring updated to call out the no-overlap corner. 13 new tests appended: R1-R5 (diff shape + Pattern B + empty list), V1-V7 (validation matrix), P1 (self-edit regression).
- `tests/integration/test_tenant_users_repo_writes.py` — NEW. 6 tests RT1-RT6 covering invariants the router can't reliably simulate (concurrent UNIQUE conflict via direct call to `_apply_role_assignments_diff`).
- `tests/unit/test_tenant_users_writes_schemas.py` — NEW. 4 tests S1-S4.
- `tests/unit/test_tenant_users_errors.py` — NEW. 3 tests E1-E3 (envelope shape).

Scripts:
- `scripts/smoke_curl.sh` — 4 new entries (38 → 42). Multi-anchor POST, diff-replace PATCH, no-op PATCH, invalid_org_node POST. Existing POST body retrofitted to new shape.
- `scripts/test_endpoints.sh` + `scripts/test_endpoints_cloud.sh` — Phase 4c block extended: 3 new entries (multi-anchor POST, diff-replace PATCH, invalid_org_node POST) under PLATFORM-1; 1 new entry (ADMIN role denied) under TENANT-2.

Docs:
- `docs/endpoints/tenant-users.md` — POST + PATCH sections updated (body shape, error codes, diff-replace semantics note).
- `docs/endpoints/openapi.json` — regenerated; `RoleAssignmentItem` schema appears; `TenantUserCreateRequest.roles` and `TenantUserPatchRequest.roles` reference it.
- `docs/architecture_RBAC.md` — cookbook entry for POST `/tenant-users` augmented with a "Note on diff-replace (Step 6.14)" paragraph at the end (per Appendix-A-framing-only convention).
- `CLAUDE.md` — 1-2 sentence step pointer + FN-AB-41 RESOLVED + FN-AB-45 NEW.
- `BUILD_PLAN.md` — Step 6.14 status flip TODO → DONE-LOCAL.

---

## Mental model

The 6.10.1 anchored-at-root pattern is structurally simple but produces wrong product: a user granted a role gets it across the entire tenant tree, no matter the role's intended scope. Step 6.14 unbundles the role-to-anchor pairing and lets the caller name the anchor explicitly. The repo already had RLS-bound visibility on `org_nodes` and a composite FK on `tenant_user_role_assignments(tenant_id, org_node_id) → org_nodes(tenant_id, id)`; the missing piece was wire-level support for naming the anchor on the request.

The whole-set replace path was a 6.10.1 stopgap. Logically equivalent to diff-replace when no overlap exists; the rebellion was audit noise — every PATCH produced N revoke + N insert rows even if the user's assignments hadn't actually changed. The diff-replace path eliminates the audit-trail noise and preserves `granted_at` as a meaningful "when this grant first happened" signal.

LD4 validation order matters. The repo's `_validate_roles` runs first because the role catalogue is platform-global (visible to every session); `_validate_org_nodes` runs after the tenant_exists check because org_nodes are tenant-scoped and we want to surface cross-tenant probes from a TENANT JWT as 404 TENANT_NOT_FOUND (RLS-as-404) rather than 422 INVALID_ORG_NODE (which would leak the existence of org_nodes in other tenants).

The `RoleAssignmentConflictError` catch path is narrow by design. Other constraint violations on the same INSERT — composite FK reject on `org_node_id` mismatched with `tenant_id`, NOT NULL violation, the audience-check trigger — are all real bugs that should surface as 500 so they're loud in logs. Misclassifying any of them as 409 conflicts would mask regressions.

---

## Surface-and-stop findings (resolved at pre-flight)

| # | Finding | Resolution |
|---|---------|------------|
| F1 | Three test files listed as `EXTEND` in the prompt's file change list don't exist | Treated as `NEW`; operator confirmed. |
| F2 | Prompt cited `_actor_type_from_auth` as repo-scoped; actually lives in `routers/v1/tenant_users.py:310` | Reuse the existing router-scoped helper; no relocation. |
| F3 | Router signature stays unchanged, but the handler-body wire-up changes one line per endpoint (Pydantic items -> repo tuples) | Documented. Handler line at `router.py:392` (POST) and the post-`_raise_if_self_edit` block (PATCH) edited; rest of handler unchanged. |
| F5 | Existing 31 tests use whole-set-replace assertions (P6 in particular) | P6 retrofitted in place — under no-overlap (the original P6 scenario) the diff-replace behavior is wire-equivalent to whole-set replace; docstring updated to call out the no-overlap corner; R3 added for the actual overlap case. |
| F6 | Prompt's "WHAT'S CHECKED 38 to 42" assumes 38 baseline | Verified at pre-flight: smoke_curl.sh's WHAT'S CHECKED header was 38. Now 42. |

LD5 implementation: the prompt's "Pydantic validator" wording aspirationally placed the duplicate check on the schema layer, but raising a domain-shaped error from inside Pydantic validation conflicts with FastAPI's default 422 envelope path. Operator-authorised: handler-side check in `_flatten_role_assignments` raising `DuplicateRoleAssignmentInRequestError` directly.

---

## Locked-decision honour record

| # | Decision | Status |
|---|----------|--------|
| LD1 | `roles` body shape `list[{role_id, org_node_id}]`; bare-UUID rejected as 422. POST `min_length=1`; PATCH allows `[]`. | Honoured. V7 LOAD-BEARING regression. |
| LD2 | Tenant root is an ordinary org_node UUID; no null/sentinel/default. | Honoured. |
| LD3 | Diff-replace uniformly on POST + PATCH; computed against `frozenset[(role_id, org_node_id)]`; unchanged tuples preserved. | Honoured. R3, R4, RT1 LOAD-BEARING. |
| LD4 | Validation order: Pydantic shape → within-request dupe → roles existence/audience → org_nodes existence/status → current set lock → diff. | Honoured. RT5 explicit ordering test. |
| LD5 | Within-request `(role_id, org_node_id)` dupes → 422 `DUPLICATE_ROLE_ASSIGNMENT_IN_REQUEST`. | Adjusted: handler-side check (not Pydantic validator) per operator-confirmed amendment. V5 LOAD-BEARING. |
| LD6 | `INVALID_ROLE` aggregates missing + ARCHIVED; `INVALID_ORG_NODE` aggregates missing + ARCHIVED + cross-tenant. | Honoured. `INVALID_ROLE_AUDIENCE` kept distinct per operator note. |
| LD7 | Concurrent UNIQUE conflict on `uq_tenant_user_role_assignments_active` → 409 `ROLE_ASSIGNMENT_CONFLICT`. Other IntegrityErrors propagate. | Honoured via constraint-name match in the catch block. RT4 LOAD-BEARING. |
| LD8 | SELF_EDIT_FORBIDDEN guard from 6.10.1 fires before diff computation; PLATFORM never self-edits by construction. | Honoured. P1 LOAD-BEARING. |
| LD9 | Gate unchanged: `ADMIN.USERS.CONFIGURE.TENANT` multi-audience. | Honoured. No catalogue changes. |

---

## Cross-step behavioral shift

Pre-Step-6.14 PATCH was whole-set replace: every existing ACTIVE assignment went INACTIVE; every desired assignment INSERTed. Post-Step-6.14 PATCH is diff-replace: only `(current − desired)` flips INACTIVE; only `(desired − current)` INSERTs.

Implications:
- **Audit trail volume.** Pre-6.14, a "rename user, keep same roles" PATCH produced N revoke + N insert rows (even though no logical change). Post-6.14, the same PATCH produces zero role-assignment rows. When Step 6.16 lands audit-log emission, the row counts for equivalent logical edits will differ across the 6.14 cutover. Captured as FN-AB-45.
- **`updated_at` semantics.** Unchanged assignment rows now keep their original `updated_at`. RT1 LOAD-BEARING regression test guards this.
- **Concurrent edits.** Two concurrent PATCHes editing the same user that both want to grant `(role_x, anchor_y)` could collide on the partial-UNIQUE index. The 409 path is the contract: caller retries after reading the current state.

---

## Per-resource regression checkpoint

| File | Pre | Post | Delta |
|---|---|---|---|
| test_tenants_router.py | 34 | 34 | 0 |
| test_tenant_users_router.py | 27 | 27 | 0 |
| test_tenant_users_writes_router.py | 31 | 44 | +13 |
| test_tenant_users_repo_writes.py | — | 6 | NEW |
| tests/unit/test_tenant_users_writes_schemas.py | — | 4 | NEW |
| tests/unit/test_tenant_users_errors.py | — | 3 | NEW |
| (all other files) | n | n | 0 |
| **Total** | **437** | **463** | **+26** |

---

## Cloud deploy

Deferred per Phase 5.5 operator pause. Bundled with the next deploy cycle. No migration; no env-var; no IAM change. Frontend coordination required (breaking body shape) at deploy time.
