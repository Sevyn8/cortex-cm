# Step 6.16.4 : Audit emission for tenant-users + roles PATCH endpoints (2026-05-21)

> Status: DONE-LOCAL. Cloud deploy batched with 6.16.5.

## Mental model

The 6.16.0 design landed with a count of 12 endpoints across 4 resource families. Between 6.16.0 design and 6.16.4 implementation, two steps shipped additional write endpoints with explicit "audit deferred" annotations: Step 6.17.3 / 6.17.4 added 3 stores writes; Step 6.18.3 added roles PATCH. v0's live write surface is 16 endpoints across 6 resource families. Step 6.16.4 closes the roles PATCH deferral (4 tenant-users + 1 roles PATCH = 5 endpoints); Step 6.16.5 closes the stores + module-access + org-tree (2 + 2 + 3 = 7 endpoints).

The implementation mirrors 6.16.2's tenants emission. Two structural extensions are new this step:

1. **Failure-path handler dispatches on resource_type.** 6.16.2 hardcoded path-param extraction as `tenant_id` and the tenant_name lookup as `core.tenants`. 6.16.4 extends both to fall through across `tenant_id` / `user_id` / `role_id` and dispatches the lookup table on `resource_type` (TENANT_USER -> `core.tenant_users` with a JOIN to `core.tenants` to back-fill tenant_id / tenant_name; ROLE -> `core.roles`).

2. **Optional sub-keys on standard `details` shapes.** PERMISSION_DENIED gains an optional `denial_reason` sub-key (LD11 — self-edit guard); INTERNAL_ERROR gains an optional `invariant` sub-key (LD12 — Layer 2 tripwire naming). The standard sub-keys stay; the optional ones augment. Codified as a convention in `docs/architecture_audit_logs.md` Failure-row payload shapes section.

`actor_display_name` continues to use `auth.email` per the 6.16.2 posture (Deviation 1 / Option B at pre-flight). The JWT email claim IS the snapshot per Phase 1 Q2; no DB lookup added.

## Implementation plan (as shipped)

### Bucket 1 : `src/admin_backend/audit/emit.py`

- AUDITED_ROUTES gains 5 entries:
  - `("POST", "/api/v1/tenant-users")` -> `("CREATE", "TENANT_USER", False)`
  - `("PATCH", "/api/v1/tenant-users/{user_id}")` -> `("UPDATE", "TENANT_USER", False)`
  - `("POST", "/api/v1/tenant-users/{user_id}/suspend")` -> `("SUSPEND", "TENANT_USER", False)`
  - `("POST", "/api/v1/tenant-users/{user_id}/activate")` -> `("ACTIVATE", "TENANT_USER", False)`
  - `("PATCH", "/api/v1/roles/{role_id}")` -> `("UPDATE", "ROLE", True)`
- `build_success_details_for_create(snapshot, *, roles=None)` : optional `roles` list of frozen-label dicts (each `{role_id, role_name, org_node_id, org_node_name}` per LD9). Backwards-compatible with 6.16.2 callers.
- `build_success_details_for_update(before, after, *, before_roles=None, after_roles=None, before_permissions=None, after_permissions=None)` : full lists on both sides when role/perm diffs fired (Phase 1 Q1). Backwards-compatible.
- `build_permission_denied_details(..., *, denial_reason=None)` : optional sub-key per LD11.
- `build_internal_error_details(..., *, invariant=None)` : optional sub-key per LD12.
- `emit_audit_event_in_new_transaction` lookup section dispatches on `resource_type`: TENANT (existing), TENANT_USER (JOIN tenant_users + tenants for full_name + tenant_id + tenant_name), ROLE (just roles.name; tenant_id stays NULL). Routes the row to the tenant table when the JOIN populates tenant_id for TENANT_USER routes; ROLE routes always go to platform table.

### Bucket 2 : `src/admin_backend/main.py`

- Imports `SelfEditForbiddenError`.
- `_emit_failure_audit_if_audited` extracts resource_id by falling through path keys `tenant_id` -> `user_id` -> `role_id`; `tenant_id_for_row` is set only when the path key was literally `tenant_id`.
- `_failure_result_and_details(exc, route_template, *, auth=None)` : new `auth` kwarg; falls back `caller_audience` to `auth.user_type` when the raise site didn't set it (gate raises don't; handler-side guards don't). Dispatches `isinstance(exc, SelfEditForbiddenError)` -> `denial_reason="SELF_EDIT_FORBIDDEN"`. ServerError path reads `exc.context.get("invariant")` and threads it.

### Bucket 3 : `src/admin_backend/repositories/tenant_users.py`

- New imports: `AuthContext`, `AuditResultType`, `emit_audit_event` + three success builders.
- New module-level aliases: `RoleLabelDict = dict[str, Any]`, `RoleLabelList = list[RoleLabelDict]` (the `list` shadow trap inside the class body forces a module-level alias for annotations).
- Two new private methods: `_tenant_name_for(session, tenant_id)`, `_resolve_role_labels(session, pairs)`.
- `create / update / transition` each gain optional kwargs `auth: AuthContext | None = None, request_id: UUID | None = None`; emit audit row when both provided; raise ValueError when one supplied without the other; skip emission cleanly when both omitted (repo-level tests).
- `update`'s tenant_id lookup SELECT extended to also return `full_name, email` so before-values can be captured; `transition`'s SELECT FOR UPDATE extended to also return `tenant_id, full_name` for the same purpose.
- `update`'s role-diff block captures `before_roles_set = current_set` + `after_roles_set = desired_set` before applying the diff; both pass to `build_success_details_for_update`.

### Bucket 4 : `src/admin_backend/repositories/roles.py`

- New imports: `AuthContext`, `AuditResultType`, `emit_audit_event`, `build_success_details_for_update`.
- `RolesRepo.update` gains `auth`/`request_id` optional kwargs. Captures `before_name + before_description` immediately after the Step 1 lookup (before `expire_all()`). Emits success row between Step 9 (Layer 2 tripwire) and Step 10 (re-fetch); routes to platform table (`tenant_id=None`, `route_to_platform=True`).
- Permission diff resolution: when `new_perm_ids is not None`, the emission reads `code` for `union = current_perm_ids | new_perm_ids` in one SELECT, then builds the before+after `permissions[]` lists with `{permission_id, permission_code}` items.
- Two `InternalInvariantViolationError` raise sites updated to pass `invariant="OVERRIDE_GLOBAL_HOLDER_PRESERVATION"` (post-Layer-1 tripwire) and `invariant="OVERRIDE_GLOBAL_CATALOGUE_PRESENCE"` (missing OVERRIDE.GLOBAL permission row). The handler reads `exc.context.get("invariant")` and threads into `build_internal_error_details`.

### Bucket 5 : `src/admin_backend/routers/v1/tenant_users.py` + `rbac.py`

- Each of the 4 tenant-users endpoints (`create_tenant_user`, `patch_tenant_user`, `suspend_tenant_user`, `activate_tenant_user`) gains a `request: Request` parameter and threads `auth=auth, request_id=request.state.request_id` into the repo call. Same shape as 6.16.2's `tenants.py` precedent.
- `rbac.py::patch_role` gets the same treatment.

### Bucket 6 : `tests/unit/test_audit_emit.py` (+3)

- AE7 (LOAD-BEARING): `build_success_details_for_create` includes `roles[]` with the 4 frozen-label fields; omitting `roles` produces the bare snapshot shape.
- AE8: `build_success_details_for_update` carries `before_roles + after_roles` or `before_permissions + after_permissions` as full lists; omitting both preserves 6.16.2 shape.
- AE9 (LOAD-BEARING): PERMISSION_DENIED with optional `denial_reason`; INTERNAL_ERROR with optional `invariant`. Both default-omitted (back-compat).

### Bucket 7 : `tests/integration/test_audit_emission_tenant_users.py` (NEW, +10)

AS1-AS10 mirror 6.16.2's AS-series. LOAD-BEARING: AS1 (per-endpoint contract), AS4 (role-diff full-lists), AS6 (suspend transition), AS8 (CREATE-snapshot roles with frozen labels).

Helpers: `_seed_tenant_with_root`, `_roles_payload`, `_valid_create_body`, `_promote_to_active` (raw UPDATE INVITED -> ACTIVE since the Auth0 invite-accept callback is Stage 3).

Fixture `cleanup_tu_audit_users` tracks user_ids; teardown DELETEs audit rows referencing those user_ids in BOTH tables (defensive — Pattern (b) `actor_user_id` / `resource_id` columns have no FK to user tables but the rows are still test-noise).

### Bucket 8 : `tests/integration/test_audit_emission_failures_tenant_users.py` (NEW, +12)

AF1-AF12 mirror 6.16.2's AF-series. LOAD-BEARING: AF1 (TENANT JWT denial), AF2 (self-edit `denial_reason`), AF3 (DUPLICATE_TENANT_USER_EMAIL conflict), AF7 (EMPTY_PATCH validation), AF9 (state-transition conflict carries CURRENT state in details.value), AF12 (self-edit row carries full Phase 1 Q8 contract).

Helper `cleanup_audit_by_request_ids` tracks request_ids for tests where the failure doesn't pin to a tracked user_id (e.g., AF1 POST /tenant-users routing-nuance fallback).

AF5 reshape: original spec named ARCHIVED org_node but `make_org_node` rejects ARCHIVED at insert time (`ck_org_nodes_archived_consistency` requires archived triplet); test now uses a nonexistent org_node_id, which exercises the same `InvalidOrgNodeError` 422 path with cleaner setup.

### Bucket 9 : `tests/integration/test_audit_emission_roles.py` (NEW, +8)

- RS1-RS3 : success-path emission. RS2 (LOAD-BEARING) covers permission-list frozen labels.
- RF1 (LOAD-BEARING) : TENANT JWT -> 403 PLATFORM_AUDIENCE_REQUIRED routes to platform table.
- RF2 (LOAD-BEARING) : PATCH on SUPER_ADMIN -> 409 SUPER_ADMIN_PROTECTED.
- RF3 : TENANT-audience role + GLOBAL-scope permission -> 422 AUDIENCE_SCOPE_MISMATCH.
- RF4 : unknown permission UUID -> 422 INVALID_PERMISSION_ID.
- RF5 (LOAD-BEARING) : Layer 2 tripwire via `monkeypatch` of `_count_override_global_active_holders` (Layer 1 returns 1, Layer 2 returns 0); audit row carries `invariant="OVERRIDE_GLOBAL_HOLDER_PRESERVATION"` per LD12.

RS2 and RF3 use uncommon `(PRICING_OS, WASTE_LOG, AUDIT, ...)` tuples to avoid `uq_permissions_code` collision with the seed catalogue.

### Bucket 10 : `tests/integration/conftest.py` + `test_tenant_users_writes_router.py`

- `make_tenant` teardown extended with audit-row DELETE for both audit tables, scoped by `tenant_id` — mirrors `cleanup_tenants_router`'s 6.16.2 extension and lifts the pattern to the shared fixture so any future audit-emitting endpoint test inherits the cleanup.
- `cleanup_tenant_users_router` in `test_tenant_users_writes_router.py` (NOT in conftest.py per Deviation 3) extended to DELETE audit rows referencing tracked user_ids ahead of the assignments + users DELETE. Defensive — Pattern (b) actor / resource columns have no FK back to user tables so the user DELETE itself isn't blocked; the cleanup keeps cross-test noise out of audit queries.

## Verification

- pytest: **791 -> 824 passed** (+33 = 3 AE + 10 AS + 12 AF + 8 RS/RF). 0 failed. 0 xfail.
- mypy --strict: clean on 82 src files.
- check_setup: 36 / 36.
- Per-resource regression: tenants_writes 33, tenant_users_writes 44, rbac_writes_router 30, rbac_writes_repo 6, audit_router 25, audit_logs_repo 8, audit_emission_tenants 10, audit_emission_failures 11, audit_emit unit 9 (was 6; +3 new) — all baseline counts preserved for existing files.

## Locked-decision honour record

LD1 honoured. LD2 honoured. LD3 honoured (with the substantive handler extension noted in retro). LD4 honoured. LD5 honoured. LD6 honoured. LD7 honoured. LD8 honoured. LD9 honoured. LD10 honoured. LD11 honoured (handler-side class-shape dispatch since `SelfEditForbiddenError` carries no structured kwargs). LD12 honoured (raise sites pass `invariant=` via `**context`; handler reads `exc.context.get("invariant")` and threads into builder). LD13 amended at pre-flight to Option B (codebase wins): `actor_display_name` = `auth.email` (no DB lookup). LD14 honoured. LD15 honoured. LD16 honoured. LD17 honoured. LD18 adjusted at pre-flight (Deviation 3): fixture extension lands in `test_tenant_users_writes_router.py` rather than `conftest.py`. LD19 honoured.

## Retro

### Iteration 1 : pre-flight surfaced LD3 was mechanically false

The prompt's LD3 said "no handler change needed" because the global handler at main.py:233 was already wired. Pre-flight Check #5 + the operator's Deviation 2 (Option A) amended this: the handler's path-param extraction was hardcoded to `tenant_id`, and the tenant_name SELECT was hardcoded to `core.tenants`. Both needed extension for the 5 new routes. The extension is small (one path-key fallthrough loop, one resource_type dispatch in the lookup) and reuses the existing failure-path structure; no architectural change. Captured in the prompt's amended LD3 wording.

### Iteration 2 : `caller_audience` was empty on PERMISSION_DENIED audit rows

First test run of AF1 and AF12 surfaced `details.caller_audience == ""` instead of `"TENANT"`. Cause: gate raises and handler-side guards both raise `PermissionDeniedError` / `SelfEditForbiddenError` without populating `caller_audience` in `**context`. The handler's `context.get("caller_audience", "")` returned the empty default.

Resolution: extended `_failure_result_and_details` to take `auth: AuthContext` and fall back `caller_audience` to `auth.user_type` when context-provided value is empty. Trade-off: the JWT identity IS the authoritative source for audience; relying on the raise site to set it would mean updating every raise site. The fall-back centralises the rule.

### Iteration 3 : `make_tenant` teardown blocked by FK to audit rows

After wiring emission into TenantUsersRepo, the existing 44 router tests in `test_tenant_users_writes_router.py` started failing with `FK violation: tenant_activity_audit_logs.tenant_id`. Cause: tests that use `make_tenant` + trigger an audit-emitting endpoint left audit rows pinned to the tenant; `make_tenant`'s teardown DELETEs the tenant; FK ON DELETE RESTRICT blocked.

Resolution: extended `make_tenant`'s teardown to DELETE audit rows scoped by `tenant_id` from both audit tables before the tenant DELETE. Mirrors the 6.16.2 cleanup_tenants_router extension, promoted to the shared fixture so future audit-emitting endpoint tests inherit it without further wiring.

### Iteration 4 : AF5 ARCHIVED org_node fixture friction

The original AF5 spec named "archived org_node in roles[]". `make_org_node` rejects status=ARCHIVED at insert time per `ck_org_nodes_archived_consistency` (DDL requires archived_at + archived_by_user_id + archived_by_user_type co-set when status=ARCHIVED). Resolution: AF5 was reshaped to use a nonexistent org_node_id, which exercises the same `InvalidOrgNodeError` 422 path with cleaner setup. The validator's docstring states it aggregates three failure modes (missing globally, cross-tenant, archived) under one error class; the audit row's `result_type=VALIDATION_FAILED` shape is identical across all three.

### Iteration 5 : permission code collision in RS2 / RF3

Initial test code used `make_permission(module="ADMIN", resource="USERS", action="VIEW", scope="TENANT")` which collides with the seed catalogue. Resolution: switched to `(PRICING_OS, WASTE_LOG, AUDIT, ...)` for the test-only permission tuples; PRICING_OS module doesn't operationally grant USERS perms in seed, so the combo is structurally available.

### Pre-existing observations (not addressed)

- FN-AB-58 (`_actor_type_from_auth` duplication) stays open. Step 6.16.4 reuses the existing copy in `audit/emit.py` per LD16 — no fifth copy.
- `actor_user_id` arg redundancy with `auth.user_id` in repo signatures (pre-flight observation 1) is pre-existing convention. Not changed in this step.
- The failure-path now does a small `core.tenant_users` JOIN to back-fill tenant_id for failure rows on TENANT_USER routes; the JOIN cost is sub-millisecond per audit emission at v0 scale.

## Forward notes

- **FN-AB-65** opens: post-6.16.0 gap-closure record (4 endpoints shipped with explicit audit deferrals; 6.16.4 closes 1, 6.16.5 closes the remaining 3).
- **FN-AB-66** opens: per-route extractor mapping in AUDITED_ROUTES. The 6.16.4 fix is a minimal fallthrough loop; if 6.16.5 surfaces a third path-param shape or third resource-label lookup table, promote to a per-route extractor declaration.
- **FN-AB-67** opens: audit row actor enrichment. Product intent for `actor_full_name` + `actor_roles` snapshot at write time exists; deferred pending operator decision (cost: ~half day to one day; ripples backward into 6.16.2 emission code; existing rows carry NULL in new columns).

## Cloud deploy

Batched with 6.16.5. No DDL changes; no migration; no smoke / test_endpoint script changes; no permission catalogue change.
