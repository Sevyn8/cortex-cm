# Step 6.16.2 : Audit emission for tenants endpoints

## Plan

Wire synchronous audit emission into the 4 tenant write endpoints (POST, PATCH, suspend, activate) per the design at `docs/architecture_audit_logs.md`. Success rows commit in the same transaction as the data write (Rule 1); failure rows commit in a separate new transaction after the data transaction has rolled back (Rule 2). Bundle the design doc refinement that promotes the original "synchronous, same transaction" single rule to the two-rule structure that this step makes operational.

Authoritative reference: `docs/architecture_audit_logs.md` (Routing principle, Architecture > Two-table split, Emission contract, Failure-row payload shapes). Implementation against the 16 locked decisions (LD1-LD16) in the impl prompt.

Structural precedent: 6.16.1's schema landed the two tables and the new enum. This step adds the application-layer plumbing. Failure-path emission inside the global exception handler is novel in v0; the precedent for "open a fresh connection and write platform-managed metadata" is the seed loader's per-row impersonation pattern (Step 3.5), but the shape here is simpler: no impersonation, no per-row loop.

Deliverables shipped:

1. NEW `src/admin_backend/audit/__init__.py` (package marker).
2. NEW `src/admin_backend/audit/emit.py` (~450 lines): two emission functions, the `AUDITED_ROUTES` dict, seven details-payload builders, four convenience helpers (actor-type mapping, action / result label resolution, request_id extraction, route template extraction).
3. MODIFY `src/admin_backend/repositories/tenants.py`: `create` / `update` / `transition` gain optional `auth: AuthContext | None = None` and `request_id: UUID | None = None` kwargs; emit success rows when both are provided.
4. MODIFY `src/admin_backend/routers/v1/tenants.py`: 4 route handlers gain `request: Request` parameter; pass `auth` and `request.state.request_id` to the repo.
5. MODIFY `src/admin_backend/main.py`: extend `admin_backend_error_handler` with failure-path emission. Two new helpers (`_emit_failure_audit_if_audited`, `_failure_result_and_details`, `_required_permission_from_code`).
6. MODIFY `tests/integration/test_tenants_writes_router.py` `cleanup_tenants_router` fixture: DELETE audit rows before tenants DELETE (FK ON DELETE RESTRICT).
7. NEW `tests/unit/test_audit_emit.py` (6 AE tests).
8. NEW `tests/integration/test_audit_emission_tenants.py` (10 AS tests).
9. NEW `tests/integration/test_audit_emission_failures.py` (11 AF tests; renumbered after dropping the Pydantic-direct 422 case per FN-AB-63).
10. MODIFY `docs/architecture_audit_logs.md`: Emission contract section rewritten with two-rule structure.
11. MODIFY `BUILD_PLAN.md`: Step 6.16.2 status flip TODO -> DONE-LOCAL with as-shipped scope summary.
12. MODIFY `CLAUDE.md`: 1-line pointer above the Step 6.16.1 entry; new FN-AB-63 entry.
13. NEW step doc (this file).
14. NEW prompt file bundled with the commit.

## Mental Model

### Two emission entry points, one routing rule

`emit_audit_event(session, ...)` writes the audit row in the caller's transaction. The caller is a repo method that owns the data write; the session is the same one the data INSERT used. Atomicity is preserved: either both commit, or both roll back.

`emit_audit_event_in_new_transaction(engine, ...)` writes the audit row in a fresh transaction. The caller is the global exception handler; the data session has already rolled back by the time the handler fires. Opening a new connection from the engine pool, setting `app.user_type='PLATFORM'` on it (so the D-29 OR-branch admits the INSERT), and committing the audit row in autocommit context is the cleanest shape that preserves the audit-trail-completeness invariant.

Both functions take the same column-level inputs. The routing decision (tenant table vs platform table) is computed inside each function:

- `route_to_platform=True` -> always platform table (the design-doc-named exception, POST /tenants).
- `route_to_platform=False` AND `tenant_id is not None` -> tenant table.
- `route_to_platform=False` AND `tenant_id is None` -> platform table (platform-scope action).

The `AUDITED_ROUTES` module-level dict carries the `route_to_platform` flag per (method, route template) tuple. Sub-steps 6.16.4 and 6.16.5 extend this dict.

### Why `_actor_type_from_auth` is a 4th local copy

Per LD6, FN-AB-58 stays open. The 3 existing copies live in `routers/v1/rbac.py`, `routers/v1/tenant_users.py`, `routers/v1/stores.py`. Step 6.16.2 adds a 4th copy in `audit/emit.py` rather than promoting to a shared module. Rationale: the function is 3 lines; promoting changes 5 files (the new shared module + 4 import sites); FN-AB-58 already names a future cleanup step. The 4th copy is one more nudge toward consolidation but does not unblock 6.16.2.

### Why repo signatures gain optional `auth` and `request_id`

Existing repo-level integration tests (`test_tenants_repo_writes.py`, 21 tests) call `TenantsRepo.create / update / transition` with `actor_user_id: UUID` and a session: no auth context, no request context. Making `auth` mandatory would force 20+ test updates (the test passes `actor_user_id` directly because the repo-level tests are unit-shaped against the data layer, not the request layer).

Choice: keep `actor_user_id` (the Pattern (a) audit-column FK value) mandatory; add `auth: AuthContext | None = None` and `request_id: UUID | None = None` as optional kwargs. The 4 router callsites pass all three; the 21 repo-level tests skip emission cleanly. Providing one without the other is a developer bug (raises ValueError).

Trade-off accepted: repo-level tests don't exercise audit emission. End-to-end emission is verified through the router-level tests in `test_audit_emission_tenants.py` and `test_audit_emission_failures.py`.

### 404-on-anchor deliberately not audited

For PATCH / suspend / activate, when the path tenant_id doesn't resolve (genuinely missing or RLS-filtered per D-17), the handler raises `TenantNotFoundError` (404). The exception handler sees this; the route matches; `AUDITED_ROUTES` has the entry. Naively, emission would fire and produce a row.

Decision (per the impl prompt's AF6 / now AF5 note): SKIP 404 emission. Rationale: there is no resource to associate the attempt with. The audit row's `resource_id` would either be NULL (no signal beyond "someone probed a non-existent tenant_id", which is application-log territory) or carry the URL path's UUID (which doesn't reference a real row). Neither shape is useful.

Mechanism: `_emit_failure_audit_if_audited` checks `exc.http_status == 404` and returns early. AF5 verifies the audited 404 path produces zero audit rows.

### Why the failure-path tenant_name lookup

On the failure path, the audit row needs `tenant_name` (NOT NULL on the tenant table) and `resource_label` (paired with `resource_id` per the CHECK). The data session is closed; the handler has only the URL path's tenant_id. Two options were considered:

(a) Pass an empty string / placeholder. Fast but loses denormalised-snapshot intent.

(b) Look up the tenant_name in the same new transaction that does the audit INSERT.

Option (b) shipped. The lookup adds one SELECT per audited failure that requires the tenant table (PATCH/suspend/activate failures). Cost is sub-millisecond at v0 scale; reads from the tenants table do not need any GUC setup since the tenant_name lookup happens with `app.user_type='PLATFORM'` already set (which fires the D-29 OR-branch on tenants reads).

The lookup is defensive: if the tenant has been deleted concurrently, fallback to `<unknown>`. The audit row still lands.

### Failure-path emission writes under PLATFORM context regardless of actor

The new connection's GUCs are unset by default. The tenant table's RLS WITH CHECK predicate would reject the INSERT (default-deny on tenant_id mismatch). Setting `app.user_type='PLATFORM'` on the connection fires the D-29 OR-branch and admits the INSERT into either table.

This is purely an INSERT-side mechanism; the actor's true identity (PLATFORM or TENANT) is recorded INSIDE the audit row's `actor_user_type` column. The choice does not blur audit attribution; it makes the writing path mechanically possible.

### Test renumber and the Pydantic-direct 422 deferral

Original impl prompt described 12 AF tests (AF1-AF12). AF4 specifically covered "POST /tenants with invalid body (missing field) -> 422 from Pydantic -> audit emission". Pre-flight surfaced that the codebase's exception handler catches `AdminBackendError` only; Pydantic's `RequestValidationError` is not a subclass and never reaches the handler. Adding a `@app.exception_handler(RequestValidationError)` to catch it would be a wire-contract change spanning every endpoint (not just audit).

Operator decision: drop AF4 from this step; defer the envelope unification to its own scope-decision step; track via FN-AB-63. AF4 renumbered to drop entirely; AF5-AF12 renumbered to AF4-AF11. Test count is 11 in `test_audit_emission_failures.py`, matching the prompt's adjusted catalogue (6 AE + 10 AS + 11 AF = 27 new tests total).

### Why the design doc refinement bundles here

The original Emission contract section read "Every successful write endpoint emits an audit row inside the same database transaction as the user-facing write." This is mechanically true for success but mechanically impossible for failure (the data transaction is rolled back by the time the audit row is needed). Step 6.16.2 forced this clarification; the right place to land it is in the design doc, alongside the implementation that operates the two rules.

Refinement bundle bundled per A6 (architecture doc updates ride with the implementation that operationalises them).

## Retro

### What landed cleanly

- The success-path emission Wiring was clean: 4 router handlers gained `request: Request` parameter and pass `auth` + `request_id` to the repo. Repo methods extend optional kwargs and call `emit_audit_event` at the end of the data-write sequence. All 33 existing router tests pass without modification (the `cleanup_tenants_router` fixture was extended in-place to DELETE audit rows before the tenants DELETE).
- The unit tests for the emit module passed first run. The pure routing-decision tests (`_build_row` returns `TenantActivityAuditLog` vs `PlatformActivityAuditLog` per the rule) are the load-bearing AE1/AE2/AE3.
- mypy strict clean on 79 source files (up 2 from 77 baseline).

### Three iterations on the failure-path emission

Iteration 1: First run of the failure-path tests surfaced 5 failures with `psycopg.errors.InsufficientPrivilege: new row violates row-level security policy for table "tenant_activity_audit_logs"`. Root cause: the new connection from the engine pool had no GUCs set; the RLS WITH CHECK was default-deny. Fix: set `app.user_type='PLATFORM'` on the new connection inside `emit_audit_event_in_new_transaction`'s try block.

Iteration 2: Second run surfaced a different failure mode: `null value in column "tenant_name" of relation "tenant_activity_audit_logs" violates not-null constraint`. Root cause: failure-path emission passed `tenant_name=None` because the handler has only the URL path's tenant_id, not a snapshot. Fix: look up `tenant_name` from the tenants table in the same new transaction.

Iteration 3: Third run surfaced `new row for relation "tenant_activity_audit_logs" violates check constraint "ck_tenant_activity_audit_logs_resource_pair"`. Root cause: I had populated `tenant_name` from the lookup but left `resource_label` as None; the CHECK requires `(resource_id, resource_label)` to be both-NULL or both-NOT-NULL. Fix: when emitting to the tenant table with `resource_type='TENANT'`, populate `resource_label` from the same lookup.

All three iterations landed inside one logical pass: discover failure, trace root cause, single targeted fix. The third fix is the right shape going forward; sub-steps 6.16.4 and 6.16.5 emit for tenant-users, stores, etc., and those resources will need their own resource_label lookup convention. Likely shape: each resource's emission caller passes `resource_label` directly (it has the data already), OR the resource_type's lookup is parameterised. Revisit at 6.16.4 design.

### One scope decision surfaced mid-implementation: 404 not audited

The impl prompt's AF6 note ("404-on-anchor -> 0 audit rows") was clear in intent but didn't prescribe HOW. The cleanest mechanism is to check `exc.http_status == 404` in `_emit_failure_audit_if_audited` and return early. This decision applies uniformly across all current and future audited routes; new sub-steps inherit it.

### FN-AB-63 captures the Pydantic-direct 422 deferral

Operator-authorised Path (b) at the A9 gate: drop AF4 (the Pydantic-direct 422 case) from this step; defer the envelope unification to a separate step. FN-AB-63 carries the full context (current state, why deferred, resolution path, trigger to revisit). The codebase's own 422 paths (EmptyPatchError, InvalidTenantNameForSlugError) flow through the standard envelope and DO emit audit rows; AF4 in the renumbered catalogue verifies the codebase-422 path with EmptyPatchError.

### LD8 location was adjusted: main.py:233, not errors.py

The impl prompt said "the global exception handler at `src/admin_backend/errors.py`". The actual `@app.exception_handler(AdminBackendError)` decorator is in `main.py:233`; `errors.py` holds the class hierarchy and the `build_error_payload` helper. The hook landed in main.py. The intent (single global hook for failure-path emission) is honoured; the file is different from what the prompt said.

### Auth middleware path not hooked

There is also an inline `try/except AdminBackendError` in `middleware/auth.py:71` that catches exceptions raised by the auth middleware itself (mostly `AuthMissingError`, `AuthInvalidError`). Those represent unauthenticated requests; for the 4 tenant endpoints, an unauthenticated request never resolves auth at all and therefore can't populate `actor_user_id`. v0 deferral: unauthenticated attempts not audited. Future step could add audit emission with a SYSTEM-actor convention; out of scope here.

### Cleanup-fixture maintenance: a forward-conventions note

Every test file that creates tenants via the HTTP layer (POST /tenants → emission fires) must DELETE audit rows before deleting tenants (FK ON DELETE RESTRICT). The pattern was applied to `test_tenants_writes_router.py::cleanup_tenants_router`, the new `test_audit_emission_tenants.py::cleanup_tenants_for_audit`, and `test_audit_emission_failures.py::cleanup_tenants_for_audit`. Sub-steps 6.16.4 / 6.16.5 will add analogous cleanup to the tenant-users / module-access / org-tree write-test files when emission lands for those resources. Convention: clear audit rows first, then resource rows, then tenants.

### No FN-AB resolved by this step

Section 6.16.2 lands new audit infrastructure; no existing tech debt closed. FN-AB-58 (`_actor_type_from_auth` consolidation) deliberately stays open (LD6); FN-AB-63 (Pydantic envelope) deliberately opens.
