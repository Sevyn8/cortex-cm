# Prompt — Step 6.8.3: inline `roles[]` on user endpoints + standalone `/role-assignments` endpoint

> Generated 2026-05-09. Revision v2 (post-stress-test): blocking issues 1–3 fixed (Repo return-type contract pinned to TenantsRepo precedent; jsonb-deserialization pattern made explicit; RoleAssignmentsRepo extension scoped in); high-severity issues 4–6 fixed (audience-filter posture made decisive; security-load-bearing test elevated; reseed gate before verification); medium/low issues 7–11 fixed (test counts pinned to specific numbers; smoke test entry mandated; OpenAPI regen via correct script; sort vocabulary infrastructure declared; audit-actor leakage test made explicit).
>
> Bundled scope: A1/A2 inline `roles[]` augmentation on existing user endpoints AND the standalone `GET /api/v1/role-assignments` endpoint that consumes 6.8.2's prepared `RoleAssignmentsRepo`.
>
> Operative word: **caution.** Two distinct surfaces in one commit. The investigation (read-only) confirmed every load-bearing piece is in place; this prompt is calibrated against the actual codebase shape, not assumptions.
>
> Paste this entire block into a fresh Claude Code session.

---

## Context: why this step exists and why now

### The two halves

**Half 1 — A1/A2 augmentation.** The frontend Users page renders one or more role chips per user row, plus user details. Existing `/tenant-users` and `/platform-users` endpoints don't carry assignment data; this step adds an inline `roles[]` array to both response shapes.

This was deferred at Step 6.1 as forward notes A1 and A2 with the landing trigger "before the Users page integration goes live in dev." That trigger has now fired.

**Half 2 — Standalone `/role-assignments` endpoint.** Step 6.8.2 deliberately built `RoleAssignmentsRepo` with two list methods (`list_platform_assignments`, `list_tenant_assignments`) and pre-emptive Pydantic shapes in `schemas/role_assignment.py` to consume in this step. Without this endpoint, that infrastructure is orphan code. The endpoint also unblocks future frontend use cases (role drill-down, audit-log drawer in Step 6.2, user detail drawer expansion).

### Why bundle into one step

The two halves share:
- Conftest factories (`make_platform_user_role_assignment`, `make_tenant_user_role_assignment` — neither exists yet).
- Load-bearing tests (RLS isolation on `tenant_user_role_assignments`; composite-FK invariant from 6.8.1; audience-check triggers).
- Models from 6.8.2 (`PlatformUserRoleAssignment`, `TenantUserRoleAssignment`).
- RLS plumbing.
- One BUILD_PLAN reconciliation (the existing 6.8.3 entry must be rewritten regardless).

Splitting them into two steps would mean writing those factories and tests twice, and reconciling BUILD_PLAN twice. Bundling: one commit, one verification harness, one deploy bundle.

### Caution-first risks the prompt explicitly guards against

1. **Row multiplication breaking pagination.** A naïve `LEFT JOIN tenant_user_role_assignments` on the parent query would multiply rows N×M; offset/limit would slice on multiplied rows. Locked posture: jsonb_agg correlated subquery (no row multiplication). Verified by U6.
2. **Cross-tenant RLS leak via the join.** If the augmentation's join uses a different session than the user query, RLS can misbehave. Same RLS-bound session must serve both. Verified by U5.
3. **Composite-FK skew.** Tenant-side joins must use `(tenant_id, tenant_user_id)` and `(tenant_id, org_node_id)` per 6.8.1's D-34 — not just the singular ids. Joining on just `tenant_user_id` works against current data because INSERTs are guarded, but it would silently disagree with the storage invariant. Verified by R8.
4. **Pattern (b) audit-actor leakage.** The four `*_by_user_*` columns on each assignment table must NOT appear in the `roles[]` envelope. Verified by U7 (negative-key assertions).
5. **Platform-side RLS hole.** `platform_user_role_assignments` has NO RLS (Section 2.4 of investigation). The app-layer audience filter is the ONLY thing keeping a TENANT JWT from seeing all platform-side assignments via `/role-assignments`. Verified by R2 (load-bearing security test).
6. **Four exact-set assertions in existing tests will break.** Updating those is non-optional and must be in the same commit.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect `35/35`.
2. `git log --oneline -5` — confirm Step 6.8.2.1 at HEAD.
3. `git status` — note pre-existing in-progress items in the working tree; do NOT stage them. Operator's expected pre-existing items: `docs/build-step-workflow.md`, `scripts/test_endpoints.sh`, `db/scripts/`, `reports/step-6_8_1-report.md`, `reports/step-6_8_2-readonly-investigation.md`, `scripts/test_endpoints_max_view.sh`. Anything else: surface and ask.
4. `uv run alembic heads` → expect `3e05299cb533` (no migration in this step).
5. `uv run pytest --tb=no -q | tail -5` → expect 227 PASS baseline. **If anything other than 227, stop and report.**
6. Read `CLAUDE.md` fully. Focus on:
   - **D-13** — Pattern (b) audit-actor pairs; hidden from response shapes.
   - **D-15** — `DB_SCHEMA` from environment.
   - **D-21** — UUIDv7 default.
   - **D-27** — `NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid` in RLS policies.
   - **D-29** — PLATFORM RLS visibility via OR-branch (no BYPASSRLS).
   - **D-30** — Response envelope is list-only `{items, pagination}` for list endpoints.
   - **D-31** — Append-only response shapes.
   - **D-34** — Split `user_role_assignments` into `platform_user_role_assignments` + `tenant_user_role_assignments`.
   - The Step 6.1, 6.4, 6.8.1, 6.8.2, 6.8.2.1 entries.
7. Read `BUILD_PLAN.md`:
   - Step 6.1 "Known follow-ups (RBAC)" sub-section (line ~1460) — A1/A2 forward note.
   - Step 6.1 E4/E5 forward note (line ~1469) — uses old URL `/user-role-assignments`; this step renames to `/role-assignments`.
   - The CURRENT Step 6.8.3 entry (line ~2492-2528) — describes ONLY the standalone endpoint. **Rewritten as part of this step's commit** to reflect bundled scope.
   - The stale paragraph (line ~1484-1488) about "UserRoleAssignment ORM model lands with E4/E5" — predates 6.8.1's split. Reconciled in this commit.
8. Read these files in full BEFORE writing any code:
   - `src/admin_backend/routers/v1/tenant_users.py`
   - `src/admin_backend/routers/v1/platform_users.py`
   - `src/admin_backend/repositories/tenant_users.py` — note lines 120-134 vanilla `select(TenantUser).order_by(...)` shape.
   - `src/admin_backend/repositories/platform_users.py` — same.
   - **`src/admin_backend/repositories/tenants.py`** — specifically `list_with_aggregates` and how it returns `TenantListRow`. **Investigate and document the return-row shape (dataclass? namedtuple? `Row` from `result.mappings()`? typed dict?). The Half 1 augmentation must mirror this exactly.** Pre-flight item 8a below pins this down.
   - `src/admin_backend/repositories/role_assignments.py` — `RoleAssignmentsRepo` from 6.8.2. Note: `list_tenant_assignments` does NOT currently accept a `tenant_id` filter (per investigation Section 2.1). This step extends it to add one — see deliverable #5.
   - `src/admin_backend/schemas/tenant.py` — read how `TenantsListResponse`/`TenantsListItem` deserialize the `enabled_modules` jsonb_agg result into `list[Module]`. **The Half 1 augmentation MUST replicate this exact deserialization path** for `roles: list[UserRoleAssignmentItem]`.
   - `src/admin_backend/models/tenant_user_role_assignment.py` — composite-FK model. `tenant_id` AND `org_node_id` both NOT NULL.
   - `src/admin_backend/models/platform_user_role_assignment.py` — NO `tenant_id`, NO `org_node_id`.
   - `src/admin_backend/models/role.py` — `Role.name`, `Role.code` are the fields.
   - `src/admin_backend/models/org_node.py` — `OrgNode.name` is the field.
   - `src/admin_backend/schemas/tenant_user.py` — note `TenantUserListItem = TenantUserRead` alias at line ~60.
   - `src/admin_backend/schemas/platform_user.py` — same alias pattern.
   - `src/admin_backend/schemas/role_assignment.py` — pre-emptive shapes from 6.8.2; used directly by Half 2.
   - `tests/integration/test_tenant_users_router.py` — note the four `assert set(item.keys()) == {...}` exact-set assertions (lines 118-129, 383-394).
   - `tests/integration/test_platform_users_router.py` — same (lines 118-128, 297-307).
   - `tests/integration/conftest.py` — confirm no existing `make_*_role_assignment` factories.
   - `tests/integration/test_rbac_router.py` lines 138-175 — local `_insert_active_platform_assignment` helper to be retired in this step.
   - `scripts/test_endpoints.sh` — note that this script runs the smoke harness AND regenerates `docs/endpoints/openapi.json` as a side-effect of Phase 1 (per investigation Section 7.5).

9. Confirm DB state with full counts:

```bash
psql "$DATABASE_URL" -c "
SET search_path TO core, public;
SELECT
  (SELECT COUNT(*) FROM tenant_users) AS tu,
  (SELECT COUNT(*) FROM platform_users) AS pu,
  (SELECT COUNT(*) FROM tenant_user_role_assignments) AS tura,
  (SELECT COUNT(*) FROM platform_user_role_assignments) AS pura,
  (SELECT COUNT(*) FROM permissions) AS perms,
  (SELECT COUNT(*) FROM role_permissions) AS rp,
  (SELECT COUNT(*) FROM roles) AS roles_total,
  (SELECT COUNT(*) FROM org_nodes) AS org_nodes;
"
```

**Expected post-6.8.2.1 fresh-reseed values:**
- `permissions = 27`
- `role_permissions = 117`
- `tenant_user_role_assignments = 19`
- `platform_user_role_assignments = 3`
- `tenant_users = 17`
- `platform_users = 7`
- `roles_total = 15`

If `tura = 0` OR `pura = 0`: pytest fixture cleanup left the DB empty. Run `uv run python -m scripts.seed_dev_data --reset` BEFORE proceeding. Re-run the count query to confirm.

If any other count is unexpectedly different (perms ≠ 27, role_permissions ≠ 117, etc.): surface and stop.

**Pre-flight item 8a (do this BEFORE writing Half 1 code):**

Investigate `repositories/tenants.py:list_with_aggregates` and `schemas/tenant.py` end-to-end and produce a 1-paragraph written summary covering:

- What concrete row type does `list_with_aggregates` return? (dataclass, NamedTuple, `sqlalchemy.engine.Row` mapping, typed dict, etc.)
- How is the `enabled_modules` jsonb_agg result represented in that row type?
- How does `TenantsListItem.model_validate(...)` (or whatever path is used) convert that representation into `list[Module]`?
- Specifically: is there a `field_validator`, a `model_validator(mode="before")`, a custom `model_validate` override, an alias, or does Pydantic v2 + `from_attributes=True` Just Work because the SQLAlchemy adapter returns `list[dict]` and Pydantic auto-validates each dict?

Do NOT skip this item. The Half 1 augmentation depends on replicating this pattern exactly. Surface the summary in your pre-step report; if any of the four sub-questions cannot be answered from the code, STOP and ask.

10. Read this prompt fully before starting any task.

---

## Step ID and intent

**Step 6.8.3** — bundled commit covering:

- **Half 1 (A1/A2):** inline `roles[]` augmentation on existing user endpoints. URLs unchanged; response body grows.
- **Half 2 (E4):** new `GET /api/v1/role-assignments` endpoint returning grouped `{platform_assignments, tenant_assignments}` envelope.

**Out of scope (forward-noted):**
- E5 single-fetch `/role-assignments/{id}` (audit-drawer use case dormant in v0).
- POST/PUT/DELETE on assignments (post-v0 write surface).
- RBAC enforcement layer (Step 6.8 proper).
- Tenant-custom roles (FN-AB-06; ~2-month estimated landing).

### Locked decisions (do not deviate)

These were resolved in the operator/Claude design conversation 2026-05-09. Do NOT re-litigate.

1. **Bundled scope under step number 6.8.3.** Existing BUILD_PLAN 6.8.3 entry rewritten in this commit.
2. **Query posture: jsonb_agg correlated subquery, mirroring `tenants.py:list_with_aggregates`.** Not selectinload (would introduce first `relationship()` declaration in the codebase, a convention shift outside this step's scope). Not two-pass at the Repo (works but precedent is jsonb_agg).
3. **Schema home for `UserRoleAssignmentItem`: `schemas/tenant_user.py` with re-export from `schemas/platform_user.py`.** Not `schemas/role_assignment.py` (already used for the richer nested shape consumed by Half 2; mixing the two shapes in one file invites confusion).
4. **`UserRoleAssignmentItem.model_config = ConfigDict(from_attributes=True)`** (no `extra="forbid"`) — match the existing convention on user schemas.
5. **Rich envelope on `roles[]` — 8 fields exactly:** `assignment_id`, `role_id`, `role_name`, `role_code`, `status`, `granted_at`, `org_node_id`, `org_node_name`. NOT `revoked_at`, NOT `updated_at`, NOT any audit-actor field (D-31 means we can append later if needed).
6. **All assignments returned regardless of `status`.** ACTIVE and INACTIVE both ship; frontend filters as needed.
7. **Uniform shape across user types.** Platform users get `org_node_id: null` and `org_node_name: null` keys present (not omitted).
8. **Empty list (not null) when user has no assignments.** Field always present; uses `COALESCE(jsonb_agg(...), '[]'::jsonb)`.
9. **URL for the standalone endpoint: `/api/v1/role-assignments`** (not `/user-role-assignments`). Matches `/tenants`, `/tenant-users`, `/platform-users`, `/permissions` plural-resource convention.
10. **Conftest factory naming:** `make_platform_user_role_assignment` and `make_tenant_user_role_assignment`. The local `_insert_active_platform_assignment` in `test_rbac_router.py` is retired in this step.
11. **Conftest factories take a `role_id` argument and trust the caller** to pass an audience-matching role. Audience-check triggers reject mismatches at INSERT.
12. **Audience routing for `/role-assignments` is a CALL-SITE DECISION, not a column filter.** TENANT JWTs MUST NOT execute the platform-side query at all (short-circuit before calling `RoleAssignmentsRepo.list_platform_assignments`). This is a SECURITY-LOAD-BEARING decision: `platform_user_role_assignments` has no RLS, so the app-layer routing is the only barrier. Distinct from Step 6.1's `_audience_filter_for(auth)` pattern (which filtered a column on the queried table); here the "filter" is "skip the query entirely."
13. **`RoleAssignmentsRepo.list_tenant_assignments` is extended this step** to accept a `tenant_id: UUID | None = None` filter, in addition to its existing `role_id`, `tenant_user_id`, `org_node_id`, `status` filters. Required by the endpoint's `?tenant_id=X` query parameter (PLATFORM-side audit use case). For TENANT JWTs, the param is redundant (RLS handles scoping) but accepted (handler may pass through; RLS produces the same result).
14. **Sort vocabulary on `/role-assignments`: explicit `frozenset({"granted_at_asc", "granted_at_desc"})`** plus `InvalidSortKeyError` reuse from Step 5.2's shared error class. `InvalidSortKeyClientError` (400, `INVALID_SORT_KEY`) at the router level. Matches the pattern Step 6.4 / 5.2 / 5.1 / 3.3 established.

---

## Concrete deliverables

### Half 1: A1/A2 augmentation

**1. New Pydantic class `UserRoleAssignmentItem`** in `src/admin_backend/schemas/tenant_user.py`. Re-exported from `src/admin_backend/schemas/platform_user.py` via `from admin_backend.schemas.tenant_user import UserRoleAssignmentItem`.

```python
class UserRoleAssignmentItem(BaseModel):
    """Inline role-assignment item for user response augmentation.

    Used by both tenant_users and platform_users responses. For platform
    users, org_node_id and org_node_name are always None (the underlying
    platform_user_role_assignments table has no org-node anchoring).

    All assignments are returned regardless of status (ACTIVE + INACTIVE
    both ship); frontend filters as needed.

    Schema home: tenant_user.py (re-exported from platform_user.py).
    Distinct from schemas/role_assignment.py shapes which serve the
    standalone /role-assignments endpoint with a richer nested envelope.
    """

    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    role_id: UUID
    role_name: str
    role_code: str
    status: UserRoleAssignmentStatus
    granted_at: datetime
    org_node_id: UUID | None
    org_node_name: str | None
```

Augment existing schemas:
- `TenantUserRead` — append `roles: list[UserRoleAssignmentItem]` field at the END (D-31).
- `PlatformUserRead` — same.

The `*ListItem = *Read` aliases mean both list and detail responses pick up the new field automatically.

**2. Repo augmentation** for the user-side queries — pinned to the `tenants.py:list_with_aggregates` precedent.

**Critical: this is a BREAKING CHANGE to the existing public methods' return types.**

The current `TenantUsersRepo.list(...)` returns `tuple[list[TenantUser], int]` — bare ORM rows. The augmented version returns `tuple[list[<row>], int]` where `<row>` is **the same row type used by `tenants.py:list_with_aggregates`** (whatever pre-flight item 8a determined that to be).

Possible shapes Claude Code may find in `tenants.py`:
- A `TenantListRow` dataclass with explicit fields including `enabled_modules: list[Module]`.
- A `NamedTuple` with similar shape.
- Direct use of `result.mappings().all()` returning `Sequence[RowMapping]`.

**Whatever pattern `tenants.py` uses, mirror exactly.** Define `TenantUserListRow` and `PlatformUserListRow` as parallel types. Each carries the existing user fields PLUS a `roles` field of the appropriate shape (typically `list[UserRoleAssignmentItem]` or its dict-equivalent depending on whether Pydantic validation happens at row-construction time or at `model_validate` time).

For `TenantUsersRepo.list(...)` and `TenantUsersRepo.get_by_id(...)`:
- Augment the SELECT with a correlated subquery using `jsonb_agg(...)` returning a JSON array of role-assignment objects.
- The correlated subquery joins `tenant_user_role_assignments` → `roles` → `org_nodes` on the COMPOSITE keys: `(tenant_user_role_assignments.tenant_id, tenant_user_role_assignments.tenant_user_id) → (tenant_users.tenant_id, tenant_users.id)` and `(tenant_user_role_assignments.tenant_id, tenant_user_role_assignments.org_node_id) → (org_nodes.tenant_id, org_nodes.id)`.
- The subquery returns a JSON array of objects with EXACT field names matching `UserRoleAssignmentItem`: `{"assignment_id": ..., "role_id": ..., "role_name": ..., "role_code": ..., "status": ..., "granted_at": ..., "org_node_id": ..., "org_node_name": ...}`. Use `jsonb_build_object('assignment_id', tura.id, 'role_id', r.id, ...)` per the `tenants.py` precedent.
- Wrap with `COALESCE(jsonb_agg(...), '[]'::jsonb)` so users with zero assignments get `[]`, never null.
- Order assignments deterministically inside the jsonb_agg via `ORDER BY tura.granted_at DESC, tura.id ASC`.
- `.correlate(TenantUser).scalar_subquery()` per the precedent, with `.label("roles")`.

For `PlatformUsersRepo.list(...)` and `PlatformUsersRepo.get_by_id(...)`:
- Same pattern but joins `platform_user_role_assignments` → `roles`. NO org_node join.
- The `jsonb_build_object` sets `'org_node_id', NULL` and `'org_node_name', NULL` literally so the wire shape is uniform with tenant-side.
- Same `COALESCE` empty-array safety.
- Same `ORDER BY granted_at DESC, id ASC`.

**Critical design discipline:**
- Use the **same RLS-bound session** the existing query uses. The correlated subquery automatically inherits the session's RLS context — verify by inspection that you are NOT opening any new session/connection. If you find yourself reaching for `engine.connect()` or a separate session, STOP and ask.
- Use the **composite key joins** on tenant side (locked decision per 6.8.1's D-34).
- Pattern (b) audit-actor columns (`granted_by_user_id`, `granted_by_user_type`, `revoked_by_user_id`, `revoked_by_user_type`) MUST NOT appear in the `jsonb_build_object`. Inspect the SELECT list of the correlated subquery and confirm only the 8 locked fields are present.
- The sub-query's `granted_at` column must be the assignment's `granted_at`, not the user's `created_at` or any other timestamp.

**Reference precedent:** `repositories/tenants.py:list_with_aggregates` — its `enabled_modules` jsonb_agg is the closest existing pattern. Pre-flight item 8a's investigation produces the exact pattern to mirror. **Do not invent.**

**3. Router updates** to `routers/v1/tenant_users.py` and `routers/v1/platform_users.py`:
- Pass through the augmented Repo output to the response model, using whatever validation path pre-flight item 8a's investigation identified.
- No new error paths.
- No change to dependencies, auth gates, query params, or status codes.
- `_require_platform_auth` on `/platform-users` stays untouched.
- `response_model` declarations on FastAPI route decorators automatically pick up the new field once `TenantUserRead` and `PlatformUserRead` are augmented.

**4. Test additions** for Half 1 — pinned counts.

The four affected endpoints are: `/tenant-users` list, `/tenant-users/{id}`, `/platform-users` list, `/platform-users/{id}`. Naming convention: `Un_<endpoint_short>_...` where short ∈ {tu_list, tu_detail, pu_list, pu_detail}.

**Per-endpoint tests (12 total — 3 per endpoint × 4 endpoints):**

- **U1_<short>: roles array present and populated.** User with ≥1 ACTIVE assignment returns the role(s) in the array; all 8 fields populated correctly. For tenant-side endpoints, also assert `org_node_id` and `org_node_name` are non-null and resolve correctly (use `make_org_node` for the test fixture). For platform-side endpoints, assert `org_node_id` and `org_node_name` are explicitly `null`.
- **U2_<short>: roles array empty for unassigned user.** User with no assignments returns `roles: []`. Field present, not omitted, not null.
- **U3_<short>: INACTIVE assignments included.** Create one ACTIVE + one INACTIVE assignment for the same user. Both appear in `roles[]`. Order respects `granted_at DESC`.

**Cross-cutting (5 total):**

- **U4_tu_list (PLATFORM JWT visibility, tenant-users only):** PLATFORM JWT calling `/tenant-users` sees rows for all tenants per D-29's OR-branch; each user's `roles[]` is correctly attributed to that user only (no cross-user contamination). 1 test.
- **U5_tu_list (cross-tenant RLS isolation, LOAD-BEARING):** Two tenants (A and B), each with users and assignments. TENANT JWT for tenant A. Calling `/tenant-users` returns only tenant A's users; the `roles[]` for each user contains only tenant A's assignments. Cross-tenant assignment leak attempt produces zero rows in tenant A's response. **Use composite-FK invariant in the test setup — explicitly assert that all returned `roles[]` items have anchor-tenant matching tenant A.** 1 test.
- **U5_tu_detail (cross-tenant 404, LOAD-BEARING):** TENANT JWT for tenant A requests `/tenant-users/{tenant_b_user_id}` — returns 404 (RLS-as-404 per D-17). Augmentation must not regress this. 1 test.
- **U6_tu_list (regression — pagination not broken by jsonb_agg):** Seed 7 tenant users in tenant A, each with 2-3 assignments. Request `/tenant-users?tenant_id=A&limit=3`. Response has exactly 3 users (parent rows not multiplied), each with their full `roles[]`. `pagination.total = 7`. 1 test.
- **U6_pu_list (same regression for platform-users):** 1 test.

**Audit-actor leakage check (1 test):**

- **U7 (negative-key assertion across all 4 endpoints, parametrized):** For each of the 4 endpoints, assert that for every item in `roles[]`, none of these keys are present: `granted_by_user_id`, `granted_by_user_type`, `revoked_by_user_id`, `revoked_by_user_type`, `revoked_at`, `updated_at`, `tenant_id`. (The 8 locked fields are the ONLY keys.) Use exact-set assertion: `assert set(role.keys()) == {"assignment_id", "role_id", "role_name", "role_code", "status", "granted_at", "org_node_id", "org_node_name"}`. 1 parametrized test = 4 actual test cases.

**Half 1 total: 12 + 5 + 1 = 18 new test functions** (the parametrized U7 expands to 4 cases at runtime, but counts as 1 function for collection purposes).

**Update existing tests with broken exact-sets — 4 edits:**
- `tests/integration/test_tenant_users_router.py` line ~118-129 (L1 list): add `"roles"` to the asserted set.
- `tests/integration/test_tenant_users_router.py` line ~383-394 (D1 detail): same.
- `tests/integration/test_platform_users_router.py` line ~118-128 (L1 list): same.
- `tests/integration/test_platform_users_router.py` line ~297-307 (D1 detail): same.

Verify the hidden-fields loop assertions immediately below each stay valid (no audit-actor leakage from the augmentation).

**Pre-step search to confirm line numbers:** `grep -n "assert set(item.keys())" tests/integration/test_tenant_users_router.py tests/integration/test_platform_users_router.py`. If the grep returns more or fewer than 4 matches, STOP and report — codebase has shifted from investigation snapshot.

### Half 2: standalone `/role-assignments` endpoint

**5. Extend `RoleAssignmentsRepo.list_tenant_assignments`** to accept a new optional filter:

```python
async def list_tenant_assignments(
    self,
    session: AsyncSession,
    *,
    role_id: UUID | None = None,
    tenant_user_id: UUID | None = None,
    tenant_id: UUID | None = None,        # NEW
    org_node_id: UUID | None = None,
    status: UserRoleAssignmentStatus | None = None,
    sort: str = "granted_at_desc",
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[TenantUserRoleAssignment], int]:
```

When `tenant_id` is non-null, add a WHERE clause: `TenantUserRoleAssignment.tenant_id == tenant_id`. Composes with RLS — no conflict.

`list_platform_assignments` does NOT need extension (no `tenant_id` column on platform side).

Also extend the sort vocabulary handling: introduce module-level constants in `repositories/role_assignments.py`:

```python
ROLE_ASSIGNMENTS_SORT_KEYS: frozenset[str] = frozenset({
    "granted_at_asc",
    "granted_at_desc",
})

# Internal map; lambdas/expressions per the existing pattern in tenants.py / tenant_users.py
_ROLE_ASSIGNMENTS_SORT_MAP = {
    "granted_at_asc": [<column>.asc(), ...stable secondary sort by id...],
    "granted_at_desc": [<column>.desc(), ...stable secondary sort by id...],
}
```

If the existing 6.8.2 implementation already has sort handling, mirror the existing structure rather than introduce a parallel one. If it doesn't, this step adds the sort infrastructure following the pattern from Step 6.4 / 5.2 / 5.1 / 3.3 (frozenset for validation, internal map for clauses, raises `InvalidSortKeyError` on unknown keys).

**6. Router** at `src/admin_backend/routers/v1/role_assignments.py`:
- Single endpoint: `GET /api/v1/role-assignments`.
- Multi-user-type. Uses `get_tenant_session_dep`.
- Query parameters (all optional unless noted):
  - `role_id: UUID | None`
  - `platform_user_id: UUID | None`
  - `tenant_user_id: UUID | None`
  - `tenant_id: UUID | None`
  - `org_node_id: UUID | None`
  - `status: UserRoleAssignmentStatus | None`
  - `sort: str = "granted_at_desc"` (validated against `ROLE_ASSIGNMENTS_SORT_KEYS`; raises 400 `INVALID_SORT_KEY` on unknown)
  - `offset: int = 0` (`Query(0, ge=0)`)
  - `limit: int = 50` (`Query(50, ge=1, le=200)` — matches existing endpoint pattern)
- Catches `InvalidSortKeyError` from the Repo and re-raises as `InvalidSortKeyClientError` (reuse from Step 5.2).

**Audience routing (security-load-bearing per locked decision 12):**

```python
auth = ...  # from dependency
if auth.user_type == "PLATFORM":
    # Run BOTH queries.
    platform_items, platform_total = await repo.list_platform_assignments(...)
    tenant_items, tenant_total = await repo.list_tenant_assignments(...)
elif auth.user_type == "TENANT":
    # SECURITY: TENANT JWT MUST NOT execute the platform-side query.
    # platform_user_role_assignments has no RLS; app-layer routing is the only barrier.
    platform_items, platform_total = [], 0
    tenant_items, tenant_total = await repo.list_tenant_assignments(...)
else:
    # Unknown user_type — defensive deny.
    raise PlatformAccessRequiredError()
```

The check is **before** the platform-side query, not a post-query filter. There is no `audience` column on either physical table; the routing decision is the audience filter for this endpoint.

**7. Wire the router** in `src/admin_backend/main.py` (or wherever the v1 router prefix is assembled). Mirror existing wiring patterns. Confirm by reading `main.py` BEFORE editing.

**8. Response shape** uses the existing `schemas/role_assignment.py` types from 6.8.2. Read the file BEFORE writing the router; if the types match the prompt's intended shape (`{platform_assignments: {items, pagination}, tenant_assignments: {items, pagination}}`), use them directly. If they differ materially, surface and ask before deviating.

**9. Test additions** for Half 2 — pinned counts.

File: `tests/integration/test_role_assignments_router.py` (NEW).

- **R1: PLATFORM JWT returns both blocks populated.** Seeded user_role_assignments rows (post-fresh-reseed: 3 PLATFORM-side + 19 TENANT-side). Response has both blocks with non-empty items. `platform_assignments.pagination.total ≥ 3`; `tenant_assignments.pagination.total ≥ 19`.
- **R2 (LOAD-BEARING SECURITY): TENANT JWT does NOT see platform_user_role_assignments rows.** Even though the platform table has no RLS, the app-layer routing must short-circuit the query. Response has `platform_assignments.items = []` and `platform_assignments.pagination.total = 0`. Verify by inspecting the test's query log (or by counting actual SELECTs against `platform_user_role_assignments` if observable) that NO query was issued against `platform_user_role_assignments`. At minimum: assert response body. Stronger: assert via test instrumentation that `repo.list_platform_assignments` was not invoked (e.g., spy/patch the method and assert call count = 0).
- **R3: TENANT JWT sees own-tenant tenant_assignments only.** Two tenants seeded; TENANT JWT for tenant A. `tenant_assignments.items` contains only tenant A's rows; `total` matches tenant A's count.
- **R4: filter by `role_id`.** Pick a role with multiple assignments; `?role_id=<id>`; only matching assignments returned.
- **R5: filter by `platform_user_id`** — only that platform user's rows returned in `platform_assignments`; `tenant_assignments.items = []`.
- **R6: filter by `tenant_user_id`** — symmetric; only that tenant user's rows in `tenant_assignments`; `platform_assignments.items = []`.
- **R7: filter by `tenant_id`** (PLATFORM JWT). PLATFORM JWT requesting `?tenant_id=<tenant_A>`: returns only tenant A's tenant_assignments rows. Verifies the new filter added in deliverable #5.
- **R8 (LOAD-BEARING): cross-tenant injection rejection at DB layer.** Direct DB-layer fixture setup attempting to insert a `tenant_user_role_assignment` where `tenant_id` does NOT match the parent `tenant_user`'s tenant. The composite FK `fk_tenant_user_role_assignments_tenant_user_same_tenant` rejects with FK violation. Assert the SQL exception is raised at INSERT time. (This is the structural-impossibility test from 6.8.1 D-34 / AI-RBAC-06.)
- **R9: filter by `org_node_id`.** Tenant-scoped node anchor filter.
- **R10: filter by `status`.** ACTIVE-only and INACTIVE-only filters return correct subsets.
- **R11: pagination per block.** Total counts correct independently for each block. Offset/limit applies per block.
- **R12 (LOAD-BEARING): PLATFORM no-impersonation regression.** PLATFORM JWT calling `/role-assignments` without setting `app.tenant_id` to any specific value: sees PLATFORM-audience rows from `platform_user_role_assignments` (no RLS) AND TENANT-audience rows from `tenant_user_role_assignments` via D-29's OR-branch (`current_setting('app.user_type', TRUE) = 'PLATFORM'`). No per-row impersonation needed (FN-AB-14 anti-pattern retired in 6.8.1).
- **R13: audience-check trigger regression.** Fixture setup attempts to INSERT a TENANT-audience role into `platform_user_role_assignments` (and a PLATFORM-audience role into `tenant_user_role_assignments`). Both rejected by the audience-check triggers from 6.8.1.
- **R14: invalid sort key returns 400.** `?sort=garbage_desc` returns 400 with `code=INVALID_SORT_KEY`. Mirrors Step 6.4's L4g pattern.
- **R15: 401 without JWT.** Standard auth gate test.

**Half 2 total: 15 new tests.**

### Shared (across both halves)

**10. Conftest factories** — add to `tests/integration/conftest.py`:

```python
@pytest_asyncio.fixture
async def make_platform_user_role_assignment(...):
    """Raw-SQL-INSERT factory for platform_user_role_assignments.
    
    Caller's responsibility to pass a PLATFORM-audience role_id;
    audience-check trigger from 6.8.1 rejects mismatches at INSERT.
    Pattern (b) audit-actor pairs default to NULL.
    """
    async def _make(
        *,
        platform_user_id: UUID,
        role_id: UUID,
        status: UserRoleAssignmentStatus = "ACTIVE",
        granted_at: datetime | None = None,
        granted_by_user_id: UUID | None = None,
        granted_by_user_type: ActorUserType | None = None,
    ) -> PlatformUserRoleAssignment:
        ...
    yield _make
    # Teardown: DELETE rows by tracked id.

@pytest_asyncio.fixture
async def make_tenant_user_role_assignment(...):
    """Raw-SQL-INSERT factory for tenant_user_role_assignments.
    
    tenant_id and org_node_id are NOT NULL on tenant side.
    Caller's responsibility:
    - Pass a TENANT-audience role_id (audience-check trigger rejects mismatches).
    - Ensure tenant_id matches both tenant_user_id's parent tenant AND
      org_node_id's parent tenant (composite FK rejects mismatches).
    """
    async def _make(
        *,
        tenant_id: UUID,
        tenant_user_id: UUID,
        org_node_id: UUID,
        role_id: UUID,
        status: UserRoleAssignmentStatus = "ACTIVE",
        granted_at: datetime | None = None,
        granted_by_user_id: UUID | None = None,
        granted_by_user_type: ActorUserType | None = None,
    ) -> TenantUserRoleAssignment:
        ...
    yield _make
    # Teardown: DELETE rows by tracked id.
```

Mirror the raw-SQL pattern of the existing factories (`make_tenant_user`, `make_org_node`, `make_role_permission`) — explicit INSERT statements, teardown by tracked id, no use of the ORM session's transaction state.

**Retire the local helper:** `_insert_active_platform_assignment` in `test_rbac_router.py` lines 138-175. Replace its callers (within `test_rbac_router.py`) with `make_platform_user_role_assignment`. Verify `test_rbac_router.py` still passes at exactly its pre-step count after the swap.

**11. Documentation:**

- `docs/endpoints/tenant-users.md` — update response shape section. Add `roles[]` field with all 8 sub-fields documented. Note ALL assignments returned (ACTIVE + INACTIVE). Note ordering (`granted_at DESC, assignment_id ASC`).
- `docs/endpoints/platform-users.md` — same. Note `org_node_id` / `org_node_name` always null for platform users.
- `docs/endpoints/role-assignments.md` — NEW file. Mirror the 8-section structure used by `tenant-users.md` and `rbac.md`. Sections:
  1. Synopsis
  2. Request shape (URL, query params, headers)
  3. Response shape (both blocks; per-block `items` and `pagination`)
  4. Visibility / multi-user-type behaviour (PLATFORM sees both; TENANT sees own tenant_assignments only)
  5. Filters (each query param documented)
  6. Sort vocabulary
  7. Error responses (401, 400 INVALID_SORT_KEY)
  8. Examples (PLATFORM JWT, TENANT JWT, with curl)

- Regenerate `docs/endpoints/openapi.json` by running `./scripts/test_endpoints.sh` (per investigation Section 7.5 — Phase 1 of the script regenerates openapi.json as a side-effect). Do NOT regenerate by manually running uvicorn + curl unless the script fails.

**12. Smoke test entry** in `scripts/test_endpoints.sh`:

Add ONE new assertion for the new endpoint, matching the precedent set by Step 6.4 / 5.3:

```bash
# /role-assignments — PLATFORM JWT sees both blocks populated
curl -fsS -H "Authorization: Bearer $PJWT" \
  "$BASE/api/v1/role-assignments?limit=5" \
  | jq -e '.platform_assignments.pagination.total >= 0 and .tenant_assignments.pagination.total >= 0' >/dev/null \
  && echo "PASS: role-assignments PLATFORM both blocks" \
  || echo "FAIL: role-assignments PLATFORM both blocks"
```

Update the "WHAT'S CHECKED" header count at the top of the script (current count → +1).

Optionally add a second assertion for TENANT JWT showing `platform_assignments.items == []` (security-load-bearing). If you decide against (smoke is a sanity check, not a security audit; R2 covers the security side), surface and explain.

**13. CLAUDE.md / BUILD_PLAN.md / architecture.md updates:**

**CLAUDE.md:**
- Add Step 6.8.3 "Completed" bullet describing scope (both halves), key contract decisions (rich envelope, all-statuses, uniform shape, jsonb_agg query posture, schema home, factory naming, retired local helper, audience routing as security-load-bearing), pytest count delta, smoke count delta.

**BUILD_PLAN.md — multiple reconciliations:**

- **Rewrite the existing Step 6.8.3 entry** (line ~2492-2528) to reflect the bundled scope: A1/A2 augmentation + standalone /role-assignments endpoint. Include locked decisions and test counts.
- **In Step 6.1's "Known follow-ups (RBAC)":**
  - **A1 / A2 marked RESOLVED at Step 6.8.3 (2026-05-09).**
  - **E4 marked RESOLVED at Step 6.8.3 (2026-05-09)** with URL `/api/v1/role-assignments`.
  - **E5 stays forward-noted** with explicit clarification: "single-fetch `/api/v1/role-assignments/{id}`. Lands when first of: Step 6.2 audit-log drawer needs live-state panel; user detail drawer adds 'click assignment chip → expand' lifecycle panel."
- **Update the stale paragraph (line ~1484-1488)** to: "The split ORM models (`PlatformUserRoleAssignment`, `TenantUserRoleAssignment`) and `RoleAssignmentsRepo` shipped at Step 6.8.2; consumed by Step 6.8.3."
- **URL drift cleanup:** any reference to `/user-role-assignments` in BUILD_PLAN.md gets updated to `/role-assignments`. Search: `grep -n "user-role-assignments" BUILD_PLAN.md`.

**Add FN-AB-06 forward note** under the appropriate section (likely Step 6.8 root or "Future RBAC work"):

> **FN-AB-06: Tenant-custom roles.** Schema additions: `roles.tenant_id NULLABLE` (NULL = platform-shipped, NOT NULL = tenant-owned), `roles.is_system` distinguishes Ithina-shipped from tenant-owned, RLS on `roles` with OR-branch policy `tenant_id IS NULL OR tenant_id = current_setting(...)`, audience-trigger update from 6.8.1 to handle the tenant-owned case. Permission catalog stays platform-global; tenants compose existing permissions into custom role bundles. **Per-tenant cap: default 50 custom roles, overridable via new `tenants.custom_role_limit INT NOT NULL DEFAULT 50` column.** Estimated 2-month landing trigger (per operator's projection 2026-05-09: 100 tenants, 5,000 stores, 50-100 users/tenant within 12 months; tenant-custom roles expected at ~2 months from now). Lands as its own step, likely 6.8.7 or 6.9.x.

**architecture.md:**
- Likely no change. Verify by `grep -n "tenant_users\|platform_users\|user_role_assignments\|role_assignments" docs/architecture.md`. If any documented response example exists, update it. If the file enumerates v1 endpoints, add `GET /api/v1/role-assignments`.
- Report "no change" or describe the edit explicitly.

---

## Verification harness

Run in order. All must be green before reporting.

```bash
# 0. PRE-VERIFICATION RESEED — pytest fixture cleanup may have emptied DB.
uv run python -m scripts.seed_dev_data --reset

# 0a. Confirm seed counts back to expected values
psql "$DATABASE_URL" -c "
SET search_path TO core, public;
SELECT
  (SELECT COUNT(*) FROM tenant_users) AS tu,
  (SELECT COUNT(*) FROM platform_users) AS pu,
  (SELECT COUNT(*) FROM tenant_user_role_assignments) AS tura,
  (SELECT COUNT(*) FROM platform_user_role_assignments) AS pura;
"
# Expected: tu=17, pu=7, tura=19, pura=3

# 1. Migrations no-op
uv run alembic upgrade head
uv run alembic check
# Expected: head unchanged (3e05299cb533); autogenerate-clean.

# 2. mypy
uv run mypy src/admin_backend/
# Expected: clean. Source-file count grows by 1-2 (new router).

# 3. Per-resource regression
uv run pytest tests/integration/test_tenant_users_router.py -v
# Pre-step: 13. Post-step: 13 + 9 (U1_tu_list, U1_tu_detail, U2_tu_list, U2_tu_detail, U3_tu_list, U3_tu_detail, U4_tu_list, U5_tu_list, U5_tu_detail, U6_tu_list, plus U7's 2 tenant cases = 12 — but two of these are detail-level so check naming maps) — verify by running and comparing.
uv run pytest tests/integration/test_platform_users_router.py -v
# Pre-step: 10. Post-step: 10 + similar count for platform side.
uv run pytest tests/integration/test_role_assignments_router.py -v
# NEW file. Expected: 15 tests (R1-R15).
uv run pytest tests/integration/test_rbac_router.py -v
# Pre-step count unchanged; only the local helper swap.

# 4. Full pytest
uv run pytest --tb=no -q | tail -5
# Expected: 227 -> 227 + N. N is the sum of new tests across all files; document explicit count in commit msg. Half 1: 18, Half 2: 15. Total target: 227 + 33 = 260.

# 5. check_setup
./scripts/check_setup.sh
# Expected: 35/35.

# 6. Smoke test (also regenerates openapi.json as side-effect of Phase 1)
./scripts/test_endpoints.sh
# Expected: 81 + 1 (new /role-assignments PASS line) = 82 PASS.
# Confirm openapi.json mtime is fresh.

# 7. Manual curl verification

# Refresh JWTs
PJWT=$(uv run python -c "from admin_backend.auth.testing import make_test_jwt; print(make_test_jwt(user_type='PLATFORM'))")
TENANT_ID=$(psql "$DATABASE_URL" -tAc "SET search_path TO core, public; SELECT id FROM tenants ORDER BY created_at LIMIT 1")
TJWT=$(uv run python -c "from admin_backend.auth.testing import make_test_jwt; print(make_test_jwt(user_type='TENANT', tenant_id='${TENANT_ID}'))")

# 7a. Half 1 — augmentation on /tenant-users (TENANT JWT)
curl -s -H "Authorization: Bearer $TJWT" "http://localhost:8000/api/v1/tenant-users?limit=5" \
  | jq '.items[0] | {id, email, roles}'
# Expected: roles array populated; each item has 8-field rich envelope; org_node fields resolved.

# 7b. Half 1 — augmentation on /platform-users (PLATFORM JWT only — TENANT JWT is 403)
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/platform-users?limit=5" \
  | jq '.items[0] | {id, email, roles}'
# Expected: roles array; each item has org_node_id=null, org_node_name=null.

# 7c. Half 1 — assert TENANT JWT cannot reach /platform-users
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TJWT" "http://localhost:8000/api/v1/platform-users"
# Expected: 403

# 7d. Half 2 — standalone endpoint, PLATFORM JWT
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/role-assignments?limit=5" \
  | jq '{platform_count: .platform_assignments.pagination.total, tenant_count: .tenant_assignments.pagination.total}'
# Expected: platform_count >= 3, tenant_count >= 19.

# 7e. Half 2 — standalone endpoint, TENANT JWT — SECURITY-LOAD-BEARING
curl -s -H "Authorization: Bearer $TJWT" "http://localhost:8000/api/v1/role-assignments?limit=5" \
  | jq '{platform_count: .platform_assignments.pagination.total, platform_items_len: (.platform_assignments.items | length), tenant_count: .tenant_assignments.pagination.total}'
# Expected: platform_count = 0 AND platform_items_len = 0 AND tenant_count > 0.
# If platform_count > 0 OR platform_items_len > 0: SECURITY REGRESSION. Stop.

# 7f. Half 2 — filter by role_id
SUPER_ADMIN_ID=$(psql "$DATABASE_URL" -tAc "SET search_path TO core, public; SELECT id FROM roles WHERE code='SUPER_ADMIN'")
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/role-assignments?role_id=$SUPER_ADMIN_ID" \
  | jq '{platform_n: (.platform_assignments.items | length), tenant_n: (.tenant_assignments.items | length)}'
# Expected: platform_n >= 1, tenant_n = 0 (SUPER_ADMIN is PLATFORM audience).

# 7g. Half 2 — filter by tenant_id (the new filter from deliverable #5)
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/role-assignments?tenant_id=$TENANT_ID" \
  | jq '.tenant_assignments.items | length, .platform_assignments.items | length'
# Expected: tenant count > 0 (subset of full tenant_assignments), platform count >= 0 (filter only applies to tenant table).

# 7h. Half 2 — invalid sort key
curl -s -o /tmp/resp.json -w "%{http_code}\n" -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/role-assignments?sort=garbage_desc"
cat /tmp/resp.json | jq '.code'
# Expected: 400; .code = "INVALID_SORT_KEY".

# 8. OpenAPI verification
ls -la docs/endpoints/openapi.json
# mtime should be fresh (within last few minutes).
jq '.paths | keys | map(select(contains("role-assignments")))' docs/endpoints/openapi.json
# Expected: ["/api/v1/role-assignments"].
jq '.components.schemas.UserRoleAssignmentItem' docs/endpoints/openapi.json
# Expected: schema present with the 8 locked fields.
jq '.components.schemas.TenantUserRead.properties.roles' docs/endpoints/openapi.json
# Expected: array reference to UserRoleAssignmentItem.
```

If ANY step is not green, report the failure rather than the step. The per-resource regression checkpoint (#3) is especially load-bearing — a failure there means an existing endpoint's behaviour was inadvertently changed.

---

## Stop and ask if

1. Pre-flight pytest count is anything other than 227.
2. Pre-flight DB row counts diverge from expected after reseed (`tura ≠ 19` or `pura ≠ 3` after a fresh seed run).
3. Pre-flight item 8a (the `tenants.py:list_with_aggregates` deserialization investigation) cannot fully answer all four sub-questions — surface what's missing.
4. The `tenants.py:list_with_aggregates` precedent is materially different from what the augmentation needs (e.g., it uses ORM relationship loading, not jsonb_agg) — surface; we may need to refine the posture.
5. The investigation said `org_node_id` is NOT NULL on tenant side; if you find any existing assignment row with `org_node_id IS NULL`, surface immediately.
6. Audience-check triggers from 6.8.1 fire during fixture setup with an unexpected error.
7. `RoleAssignmentsRepo.list_tenant_assignments` already accepts a `tenant_id` filter (i.e., the investigation was wrong) — confirm and skip the extension; otherwise add it.
8. The pre-emptive `schemas/role_assignment.py` shapes from 6.8.2 don't match the prompt's intended response shape `{platform_assignments: {items, pagination}, tenant_assignments: {items, pagination}}`. Surface and ask before deviating.
9. Adding `roles: list[UserRoleAssignmentItem]` to `TenantUserRead` and `PlatformUserRead` causes a Pydantic ForwardRef issue (cross-import between `tenant_user.py` and `platform_user.py`) — surface; small refactor to a shared module may be needed.
10. The 4 broken exact-set assertions don't all live at the line numbers the investigation reported (codebase moved between investigation and prompt). Find them yourself by `grep -n "assert set(item.keys())" tests/integration/test_tenant_users_router.py tests/integration/test_platform_users_router.py`. If the count differs from 4, surface.
11. The local helper `_insert_active_platform_assignment` has callers that expect specific return types or fixtures that the new factory doesn't match — surface; we'll either match the factory shape or update the callers.
12. BUILD_PLAN.md sections have shifted line numbers from what's listed in this prompt — re-find by content (search for "A1 / A2", "Step 6.8.3", "user-role-assignments" etc.) before rewriting.
13. The composite-FK joins produce unexpected query plans (e.g., a sequential scan instead of index seek) — surface for investigation before committing.
14. Any place in the codebase opens a new `engine.connect()` or separate session for the augmentation — STOP. The augmentation must use the existing RLS-bound session.
15. The verification step 7e returns `platform_count > 0` or `platform_items_len > 0` for a TENANT JWT — STOP. This is a SECURITY REGRESSION. Diagnose before any further work.

---

## Report (BEFORE proposing commit)

1. Pre-flight outputs (items 2, 4, 5, 9, 8a written summary).
2. Diffs for the schema files: `schemas/tenant_user.py`, `schemas/platform_user.py`. Show the new `UserRoleAssignmentItem` class and the `roles` field addition on `*Read`.
3. Diffs for the repos: `repositories/tenant_users.py`, `repositories/platform_users.py`, `repositories/role_assignments.py`. Show the jsonb_agg correlated subquery and the `tenant_id` filter extension.
4. Diff for the new router: `routers/v1/role_assignments.py` (full file).
5. Diffs for routers: `routers/v1/tenant_users.py`, `routers/v1/platform_users.py`.
6. Diff for `main.py` showing the new router wiring.
7. Diffs for tests: `test_tenant_users_router.py`, `test_platform_users_router.py`, `test_role_assignments_router.py` (NEW), `test_rbac_router.py` (helper retirement).
8. Diff for `conftest.py` (factory additions).
9. Diff for `scripts/test_endpoints.sh` (new smoke entry + count bump).
10. Diffs for `docs/endpoints/tenant-users.md`, `docs/endpoints/platform-users.md`, `docs/endpoints/role-assignments.md` (NEW), `docs/endpoints/openapi.json`.
11. Diffs for `CLAUDE.md`, `BUILD_PLAN.md`. BUILD_PLAN diff is large — spell out each reconciliation explicitly.
12. `architecture.md` status — "no change" or describe edit.
13. Verification harness output for ALL sections (0, 0a, 1-8 with all sub-steps).
14. Pre/post pytest count: 227 → 227+N (N = total new tests). State explicit count.
15. Smoke test result (expect 81 → 82).
16. mypy / check_setup status.
17. Manual curl outputs (7a–7h) with expected vs actual side-by-side. **Specifically call out 7e's result** — security-load-bearing assertion.

Wait for explicit operator authorisation before staging or committing.

---

## Commit message template

```
Step 6.8.3: inline roles[] augmentation + standalone /role-assignments endpoint

Bundled commit covering both A1/A2 (forward note from Step 6.1) and E4
(standalone endpoint that consumes 6.8.2's RoleAssignmentsRepo).

Half 1 — A1/A2 augmentation:
- Augment GET /api/v1/tenant-users (list + single) and
  GET /api/v1/platform-users (list + single) responses with inline
  roles[] array carrying 8 fields per item: assignment_id, role_id,
  role_name, role_code, status, granted_at, org_node_id, org_node_name.
- Append-only per D-31; URL unchanged; no new endpoint URL on user side.
- Query posture: jsonb_agg correlated subquery, mirroring
  repositories/tenants.py:list_with_aggregates exactly. No row
  multiplication; pagination preserved.
- Composite-FK joins on tenant side per 6.8.1 D-34.
- Uniform shape across user types (platform users get org_node_id=null
  and org_node_name=null).
- All assignments returned regardless of status (ACTIVE + INACTIVE both
  ship); frontend filters as needed.
- BREAKING return-type change on TenantUsersRepo.list / get_by_id and
  PlatformUsersRepo.list / get_by_id — return rows now include the
  roles JSON column. Routers updated accordingly.

Half 2 — Standalone endpoint:
- New GET /api/v1/role-assignments endpoint returning grouped envelope
  {platform_assignments: {items, pagination}, tenant_assignments: {items, pagination}}.
- Filters: role_id, platform_user_id, tenant_user_id, tenant_id,
  org_node_id, status. Sort vocabulary: granted_at_asc, granted_at_desc.
- Multi-user-type. PLATFORM JWT sees both blocks; TENANT JWT sees own
  tenant_assignments only — platform_assignments short-circuited at
  router level (security-load-bearing: platform_user_role_assignments
  has no RLS, app-layer routing is the only barrier).
- RoleAssignmentsRepo.list_tenant_assignments extended to accept
  tenant_id filter.
- Sort vocabulary infrastructure: ROLE_ASSIGNMENTS_SORT_KEYS frozenset;
  reuses Step 5.2's InvalidSortKeyError / InvalidSortKeyClientError.

Schema home for UserRoleAssignmentItem: schemas/tenant_user.py
(re-exported from schemas/platform_user.py). Distinct from the richer
nested shapes in schemas/role_assignment.py used by Half 2.

Conftest factories added: make_platform_user_role_assignment,
make_tenant_user_role_assignment. The local helper
_insert_active_platform_assignment in test_rbac_router.py is retired;
its callers updated to use the conftest factory.

Resolves Step 6.1 forward notes A1, A2, E4.
E5 (single-fetch /role-assignments/{id}) retained as forward note.

pytest: 227 -> 227+<N> (<N> new tests; Half 1: ~18, Half 2: 15;
target total ~260).
Smoke: 81 -> 82 (1 new /role-assignments PASS).
alembic: 3e05299cb533 (unchanged; no migration).
mypy: clean on <X> source files.

CLAUDE.md / BUILD_PLAN.md: 6.8.3 entry rewritten to reflect bundled
scope; A1/A2/E4 marked RESOLVED; E5 retained as forward note;
URL drift (/user-role-assignments → /role-assignments) reconciled
across BUILD_PLAN; FN-AB-06 forward note added (tenant-custom roles
with per-tenant cap of 50, overridable via tenants.custom_role_limit
column; estimated 2-month landing trigger).
docs/endpoints/{tenant-users,platform-users}.md: response shape
section updated; docs/endpoints/role-assignments.md added; OpenAPI
snapshot regenerated via test_endpoints.sh.
architecture.md: <no change | describe edit>.
```

Use explicit `git add`:

```bash
git add \
  src/admin_backend/schemas/tenant_user.py \
  src/admin_backend/schemas/platform_user.py \
  src/admin_backend/repositories/tenant_users.py \
  src/admin_backend/repositories/platform_users.py \
  src/admin_backend/repositories/role_assignments.py \
  src/admin_backend/routers/v1/tenant_users.py \
  src/admin_backend/routers/v1/platform_users.py \
  src/admin_backend/routers/v1/role_assignments.py \
  src/admin_backend/main.py \
  tests/integration/test_tenant_users_router.py \
  tests/integration/test_platform_users_router.py \
  tests/integration/test_role_assignments_router.py \
  tests/integration/test_rbac_router.py \
  tests/integration/conftest.py \
  scripts/test_endpoints.sh \
  docs/endpoints/tenant-users.md \
  docs/endpoints/platform-users.md \
  docs/endpoints/role-assignments.md \
  docs/endpoints/openapi.json \
  CLAUDE.md \
  BUILD_PLAN.md
```

Add `architecture.md` only if it actually changed. Do NOT use `git add -A` — preserve pre-existing in-progress items: `docs/build-step-workflow.md`, `db/scripts/`, `reports/...`, `scripts/test_endpoints_max_view.sh`. Note: `scripts/test_endpoints.sh` IS being modified by this step (smoke entry addition) and is included in the explicit add list above; the operator's pre-existing modifications to it from earlier sessions need to be reconciled — surface if you find conflicting changes.
