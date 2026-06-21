# Prompt — Step 6.9.3.2: endpoint retrofit + anchor deps + mandatory-gate discipline

> Generated 2026-05-13. Calibrated against codebase HEAD at commit
> `ddea23c` ("Step 6.9.3.1: scope cascade in has_permission").
> Pytest baseline: 308 passes, 1 warning (pre-existing
> python-json-logger deprecation).
>
> Paste this entire block into a fresh Claude Code session to start
> Step 6.9.3.2.

---

## Standing discipline (read first)

### On the code sketches in this prompt

Code blocks below are STARTING POINTS, not the answer. The operator
drafts prompts without live access to the codebase. You have live
access. Use it.

Where you have a better implementation than what's sketched,
implement the better version. Surface deviations in your report with
a one-line reason.

**Specifically for anchor_deps.py and the require() factory edits:**
the sketches show shape and behavioral contract only. Look at
existing single-row Repo lookup patterns in `repositories/tenants.py`
and `repositories/org_nodes.py` BEFORE implementing — those reflect
the codebase's actual SQLAlchemy 2.x async style, schema
qualification convention (per CLAUDE.md), and error-raising
patterns. Do not copy SQL fragments or function bodies from the
sketches; mirror existing patterns instead.

Locked decisions in the "Locked decisions" section remain locked.
Everything else is calibrated guidance.

### Documentation writing

Updates to CLAUDE.md, BUILD_PLAN.md must be technical, sharp,
concise. Rules: state facts, active voice present tense, one sentence
per fact, specific over general, cite by reference (e.g., "per D-34"),
no meta-commentary, no adjectives that don't add information.

Bad: "This step introduces a comprehensive retrofit of the gating
layer that should make permission enforcement much more robust."

Good: "14 retrofit-eligible GET endpoints gain `Depends(require(...))`
per the gate-assignment table in this commit. 5 endpoints remain
exempt; entries in `auth/gate_allowlist.py:GATE_EXEMPT_PATHS`.
Mandatory-gate-discipline test asserts every APIRoute either has the
gate marker or appears in the allowlist."

### Definition of done

Before reporting complete:
1. All tests pass (existing + new).
2. mypy strict clean on every file touched.
3. EXPLAIN ANALYZE for one representative anchor-dep-using endpoint
   captured; query plan still uses indexed paths.
4. CLAUDE.md updates sharp per the documentation-writing rules above.
5. Pre-commit checks (check_setup.sh, pytest, mypy, alembic check) all
   pass.
6. The 3 seed data updates (1 permission row + 2 role_permissions rows)
   are NOT applied in this commit — operator applies post-commit per
   data-seeding posture P-1/P-2/P-3.

---

## Context: why this step exists

Section 6.9 sub-step status:
- Step 6.9.1 (SHIPPED at `63dd565`): `has_permission()` pure-SQL
  permission check + `PermissionGrant` + `ReasonCode`
- Step 6.9.2 (SHIPPED at `e0946b8`): `require()` factory +
  `PermissionDeniedError` + `/me/*` endpoints
- Step 6.9.3.1 (SHIPPED at `ddea23c`): scope cascade in
  `has_permission` (satisfying_scopes helper + `_SCOPE_CASCADE_ORDER`
  tuple + `ANY` clause in SQL)
- **Step 6.9.3.2 (THIS STEP):** endpoint retrofit + per-resource
  anchor dependencies + mandatory-gate-discipline test

### Gap this step closes

At HEAD `ddea23c`, the `require()` factory exists but is not used by
any production endpoint. `_require_platform_auth` (user-type-only
check) gates 2 endpoints in `routers/v1/platform_users.py`. All other
GET endpoints rely on RLS-only scoping for tenant data and bare auth
for cross-tenant data.

6.9.3.2 retrofits 14 GET endpoints with `Depends(require(...))` using
the per-endpoint gate assignments locked during design, adds the
per-resource anchor dependency mechanism, retires
`_require_platform_auth`, and adds a meta-test that prevents future
endpoints from shipping without either a gate or an explicit
allowlist entry.

### Design intent (locked during design conversation 2026-05-13)

Three phases of decisions, all locked:

**Phase 1 — Endpoint gate assignments** (per cluster):

```
1a /tenants/*        /tenants                            ADMIN.TENANTS.VIEW.GLOBAL
                     /tenants/stats                      ADMIN.TENANTS.VIEW.GLOBAL
                     /tenants/{tenant_id}                ADMIN.TENANTS.VIEW.TENANT (NEW tuple)
                     
1b /platform-users/* /platform-users                     ADMIN.USERS.VIEW.GLOBAL
                     /platform-users/{user_id}           ADMIN.USERS.VIEW.GLOBAL
                     RETIRE _require_platform_auth + PlatformAccessRequiredError 
                     (keep error class as dead-code with comment)
                     
1c /tenant-users/*   /tenant-users                       ADMIN.USERS.VIEW.TENANT
                     /tenant-users/{user_id}             ADMIN.USERS.VIEW.TENANT
                     
1d org-tree          /tenants/{tenant_id}/org-tree       ADMIN.ORG_NODES.VIEW.TENANT
                     /tenants/{tenant_id}/org-nodes/{nid}/children
                                                         ADMIN.ORG_NODES.VIEW.TENANT
                     
1e /dashboard/*      /dashboard/fleet-stats              ADMIN.TENANTS.VIEW.TENANT (proxy)
                     /dashboard/governance-stats         ADMIN.TENANTS.VIEW.TENANT (proxy)
                     
1f /module-access/*  /module-access/modules              ADMIN.TENANTS.VIEW.TENANT (proxy)
                     /module-access/matrix               ADMIN.TENANTS.VIEW.TENANT (proxy)
                     
1g reference         /lookups                            EXEMPT
                     /permissions                        EXEMPT
                     /permission-matrix                  EXEMPT
                     
1h /roles            /roles                              EXEMPT
                     /roles/{role_id}/permissions        EXEMPT
                     
1i role-assignments  /role-assignments                   ADMIN.USERS.VIEW.TENANT (proxy)
```

**Phase 2 — Mechanics:**

1. **Gate factory signature:** `require(M, R, A, S, *, anchor_dep=None)`
   - Two inner-function shapes picked at factory-call time by
     `anchor_dep` presence (FastAPI requires static inner signatures)
2. **Gate marker:** `PermissionGateInfo` frozen dataclass at
   `src/admin_backend/auth/gate_info.py` (new file). Fields:
   `module, resource, action, scope, anchor_dep`
3. **Anchor deps:** `src/admin_backend/auth/anchor_deps.py` (new
   file). Three functions: `get_tenant_anchor`,
   `get_org_node_anchor`, `get_tenant_user_anchor`. Raise 404 on
   lookup miss (NOT return None — would short-circuit cascade clause
   to TRUE, security regression per F-THREADING-4).
4. **Gate allowlist:** `src/admin_backend/auth/gate_allowlist.py`
   (new file). `GATE_EXEMPT_PATHS: frozenset[str]` containing the 7
   paths exempt from gating (`/me/*` × 2, reference data × 3, roles
   × 2).
5. **PlatformAccessRequiredError:** keep at `platform_users.py:78-87`
   as dead-code with inline comment marking for potential later
   removal.

**Phase 3 — Catalogue + grants (operator-driven post-commit):**

- Catalogue: +1 row (`ADMIN.TENANTS.VIEW.TENANT`).
- Grants: +2 rows (OWNER → `ADMIN.TENANTS.VIEW.TENANT`, OWNER →
  `ADMIN.ORG_NODES.VIEW.TENANT`).
- NOT applied in this commit. Operator applies via Excel edit + seed
  loader (local) + UPSERT SQL (cloud) post-commit per P-1/P-2/P-3.
- Tests in this commit USE seed data as-is (without the additions).
  T_RET_* tests that exercise OWNER cascade must work around the
  missing grants (see "Test fixture strategy" below).

### Out of scope for 6.9.3.2

- DDL migrations (no resource_enum expansion; proxies cover
  dashboard/module-access).
- `_require_platform_auth` re-introduction or alternative
  fast-path. Single replacement strategy locked: `require(ADMIN,
  USERS, VIEW, GLOBAL)`.
- Write endpoint gates (Stage 2 onward).
- Audit log writes on gate denials (Step 6.16).
- FN-AB-27 `/me/permissions` shape simplification.
- Stage 3 Auth0 swap.
- Custom roles (FN-AB-06).
- Architecture.md RBAC section — written separately from the
  rbac-design-digest after this step commits.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -3`. Confirm HEAD is `ddea23c`.
3. `git status`. Note any pre-existing items in the working tree;
   surface anything unexpected.
4. `uv run alembic heads`. Expect `3e05299cb533` (no migration in
   this step).
5. `uv run pytest --tb=no -q | tail -5`. Expect 308 passes, 0
   failures, 1 warning (pre-existing python-json-logger). **If
   anything other than 308 passes, stop and report.**
6. Re-confirm live route enumeration matches the F-INVENTORY-1
   master table (23 APIRoutes). Run:
   ```python
   from admin_backend.main import create_app
   from fastapi.routing import APIRoute
   app = create_app()
   routes = sorted([r.path for r in app.routes if isinstance(r, APIRoute)])
   for p in routes: print(p)
   ```
   Expect 23 paths. Compare against the prompt's gate-assignment
   table. **If any route is missing or new, surface and stop.**
7. Read `src/admin_backend/auth/permissions.py` fully. Focus on:
   - `_SCOPE_CASCADE_ORDER` and `satisfying_scopes` (6.9.3.1
     deliverables; unchanged here)
   - `has_permission()` (PLATFORM and TENANT paths; unchanged here)
   - `require()` factory (LINES TO MODIFY — both inner-function
     shapes to be implemented; current code hardcodes
     `target_anchor=None`)
8. Read `src/admin_backend/routers/v1/platform_users.py` fully.
   Focus on:
   - `PlatformAccessRequiredError` class (lines 78-87) — keep with
     dead-code comment
   - `_require_platform_auth` helper (lines 102-109) — RETIRE
   - 2 call sites (lines 216, 258) — replace with
     `Depends(require(...))`
9. Read each of the 19 retrofit-target router files at HEAD:
   - `routers/v1/tenants.py`
   - `routers/v1/platform_users.py`
   - `routers/v1/tenant_users.py`
   - `routers/v1/org_tree.py`
   - `routers/v1/dashboard.py`
   - `routers/v1/modules_access.py`
   - `routers/v1/rbac.py` (roles, permission_matrix, permissions,
     lookups, role_assignments)
   Identify the exact handler functions to retrofit and their
   existing dependency signatures.
10. Read `src/admin_backend/auth/permission_grant.py`,
    `reason_code.py`, `errors.py` (PermissionDeniedError). Confirm
    shape unchanged since 6.9.2.
11. Read `src/admin_backend/middleware/auth.py:38-45` (PUBLIC_PATHS
    frozenset). Confirm contents unchanged.
12. Read `src/admin_backend/routers/v1/me.py` (me_router). Confirm
    `/me/permissions` and `/me/can-do` paths. These go into
    GATE_EXEMPT_PATHS.
13. Read `tests/integration/test_has_permission.py` (21 tests at
    HEAD: 13 prior + 8 cascade from 6.9.3.1). Don't modify;
    regression baseline.
14. Read `tests/unit/test_permissions_helpers.py` (6 tests). Don't
    modify.
15. Read `tests/integration/test_me_router.py` (18 tests). These
    test endpoints that go into GATE_EXEMPT_PATHS; should continue
    passing unchanged.
16. Read each existing per-router integration test file at HEAD.
    Note the patterns:
    - Existing tests use JWT helpers, conftest fixtures, seed data
      assumptions
    - The retrofit will fail some existing tests (e.g., tests that
      use a JWT without sufficient grants will now get 403)
    
    **Required output BEFORE any code edits:** produce a test 
    audit table in the report. For each test that touches a 
    retrofitted endpoint:
    
    ```
    Test file               | Test name    | JWT used     | Strategy
    ────────────────────────|-------------|--------------|─────────────────
    test_tenants_router.py  | test_get_X  | SUPER_ADMIN  | unchanged
    test_tenants_router.py  | test_get_Y  | OWNER        | xfail-seed-update
    test_platform_users... | test_403_*  | NO_PLATFORM  | update assertion
    ```
    
    Strategy categories:
    - **unchanged** — JWT passes the new gate via cascade or direct 
      grant; test behavior preserved
    - **xfail-seed-update** — `pytest.mark.xfail(reason="Needs 
      operator seed grant update: OWNER → X")`; operator un-skips 
      after Phase 3b applied
    - **update assertion** — error code or response shape changed 
      (e.g., `code=PLATFORM_ACCESS_REQUIRED` → `code=PERMISSION_DENIED`)
    - **JWT swap** — test fixture switched from one role to another 
      to preserve test intent
    
    Surface this table in the report BEFORE proceeding with edits. 
    Catches surprises.
17. Read `CLAUDE.md` fully. Focus on:
    - Existing D-XX entries
    - FN-AB-26 (require_platform_auth retirement) — update during
      this step to mark RESOLVED
    - FN-AB-27 (`/me/permissions` shape) — unchanged; remains
      deferred
    - "Note on org-hierarchy coupling" (6.9.3.1) — model for the
      new "Note on gate allowlist coupling" added in this step
18. Read `BUILD_PLAN.md` Section 6.9 entry. Confirm structure:
    6.9.1 DONE, 6.9.2 DONE, 6.9.3.1 DONE, 6.9.3.2 TODO.

---

## Step ID and intent

**Step 6.9.3.2** — retrofit 14 GET endpoints with permission gates,
add per-resource anchor dependency mechanism, retire
`_require_platform_auth`, add gate marker + allowlist module, add
mandatory-gate-discipline meta-test, prepare seed data updates for
operator post-commit application.

### Scope in

- **3 NEW files in `src/admin_backend/auth/`:**
  - `gate_info.py` — `PermissionGateInfo` frozen dataclass
  - `anchor_deps.py` — 3 anchor lookup functions
  - `gate_allowlist.py` — `GATE_EXEMPT_PATHS` frozenset
- **1 MODIFIED file in `src/admin_backend/auth/`:**
  - `permissions.py` — `require()` factory: two inner-function
    shapes, marker attribute attachment
- **6+ MODIFIED router files in `src/admin_backend/routers/v1/`:**
  - Add `Depends(require(...))` to 14 endpoints
  - Retire `_require_platform_auth` from `platform_users.py`
  - Cleanup 4 docstring references to `_require_platform_auth`
- **2 NEW integration test files:**
  - `tests/integration/test_gate_discipline.py` — mandatory-gate
    meta-test (1 test, LOAD-BEARING)
  - `tests/integration/test_gate_retrofit.py` — gate behavioral 
    tests (T_RET_1 through T_RET_8; 3 LOAD-BEARING)
- **Per-router test updates** — existing tests that break due to
  retrofitted gates get updated JWT fixtures (use SUPER_ADMIN JWT or
  other appropriately-privileged role for the retrofitted endpoint)
- **CLAUDE.md** — Current state entry for 6.9.3.2, "Note on gate
  allowlist coupling," FN-AB-26 resolved, retire
  `PlatformAccessRequiredError` comment, 3-5 new forward notes for
  deferred reviews
- **BUILD_PLAN.md** — Section 6.9 status flipped to COMPLETE
- **prompt file bundled into commit:**
  `prompts/step-6_9_3_2-endpoint-retrofit-2026-05-13.md`
- **investigation report bundled into commit:**
  `reports/step-6_9_3_2-design-investigation-2026-05-13.md`

### Scope out

- DDL migrations / Alembic.
- Catalogue rows or role_permissions inserts (operator does these
  post-commit per data-seeding posture).
- Write endpoints.
- New endpoint additions.
- Architecture.md (separate task).
- The rbac-design-digest is reference material; no need to update
  it.

### Acceptance criteria

- Every retrofit-target endpoint (14 endpoints) has
  `Depends(require(M, R, A, S, anchor_dep=...))` declared at the
  handler.
- Every exempt endpoint (5 endpoints + /me/* × 2 + 6 PUBLIC_PATHS)
  is listed in `GATE_EXEMPT_PATHS` or `PUBLIC_PATHS`.
- `require()` factory produces a gate with `__permission_gate__`
  attribute set to a `PermissionGateInfo` instance.
- Gate marker is readable: `route.dependant.dependencies` includes
  the gate function whose `.call` has the `__permission_gate__`
  attribute.
- Mandatory-gate-discipline test passes: every APIRoute is either
  gated (has marker) OR is in the gate allowlist OR is in
  PUBLIC_PATHS.
- `_require_platform_auth` retired at both call sites; helper
  function deleted; `PlatformAccessRequiredError` class kept with
  dead-code comment.
- 4 docstring references to `_require_platform_auth` cleaned up.
- All 308 pre-step tests still pass (some may need JWT fixture
  updates; behavioral baseline unchanged).
- Mandatory-gate-discipline meta-test passes.
- All 8 gate-retrofit behavioral tests pass (or are xfail-marked 
  with explicit seed-update reason for OWNER on 3 endpoints).
- Anchor deps raise the appropriate `*NotFoundError` (404) on
  lookup miss; never return None to signal not-found.
- mypy strict clean on every file touched (~74 source files
  post-step; verify against `find src/admin_backend -name '*.py' | 
  wc -l` and surface actual count).
- `scripts/check_setup.sh` 35/35.
- `scripts/smoke_test.py` PASS count unchanged.
- `scripts/smoke_curl.sh` and `scripts/test_endpoints.sh` PASS
  counts unchanged (test scripts use SUPER_ADMIN JWT; retrofitted
  endpoints still pass via cascade).
- 1-2 representative endpoints captured via EXPLAIN ANALYZE for
  performance verification.
- BUILD_PLAN.md Section 6.9 flipped to ALL DONE.

### Locked decisions (do not deviate)

1. **Endpoint gate assignments per Phase 1 table.** Each of the 19
   endpoints' gate decision is locked. Do not propose alternatives.
2. **Factory signature: `require(M, R, A, S, *, anchor_dep=None)`.**
   Keyword-only `anchor_dep`. Two inner-function shapes by
   `anchor_dep` presence.
3. **Gate marker: `PermissionGateInfo` frozen dataclass.** Fields:
   `module, resource, action, scope, anchor_dep`. At new file
   `auth/gate_info.py`.
4. **Anchor deps: 3 functions at `auth/anchor_deps.py`.** Raise
   404 (`*NotFoundError`) on lookup miss; NEVER return None to
   signal not-found.
5. **Allowlist: `GATE_EXEMPT_PATHS` at `auth/gate_allowlist.py`.**
   Documented coupling note in CLAUDE.md.
6. **PlatformAccessRequiredError kept as dead code.** Inline
   comment marks for potential later removal.
7. **Operator-driven seed updates post-commit.** No Alembic, no
   programmatic seed insertion in this step.

---

## Implementation outline

### File 1: `src/admin_backend/auth/gate_info.py` (NEW)

```python
"""Permission gate marker dataclass.

A PermissionGateInfo instance is attached to each gate function 
returned by require() via the __permission_gate__ attribute. The 
mandatory-gate-discipline meta-test reads this attribute to verify 
that every retrofittable endpoint either has a gate or is in the 
explicit allowlist.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Awaitable, Callable

from admin_backend.models.enums import (
    ModuleCode,
    PermissionAction,
    PermissionResource,
    PermissionScope,
)


@dataclass(frozen=True)
class PermissionGateInfo:
    """Marker carried by every gate function. Read by the discipline 
    test for assertions about which endpoint is gated on what tuple.
    
    `anchor_dep` references the per-resource anchor dependency 
    function if the gate has one; None for list/aggregate endpoints.
    """
    module: ModuleCode
    resource: PermissionResource
    action: PermissionAction
    scope: PermissionScope
    anchor_dep: Callable[..., Awaitable[str | None]] | None
```

### File 2: `src/admin_backend/auth/anchor_deps.py` (NEW)

Three async functions, each returns `str` (the target's ltree path);
raises 404 on lookup miss.

**Signatures + behavioral contracts only.** Read existing single-row
Repo lookup patterns in `repositories/tenants.py` (e.g.,
`get_tenant_by_id` style) and `repositories/org_nodes.py` (composite-
key queries per D-34) BEFORE implementing. Mirror those patterns.

```python
# Module docstring captures the security-critical invariant:
"""Per-resource anchor dependency functions.

Anchor deps look up an org_node.path for a request's target row, 
returning a ltree-formatted string suitable for passing to 
has_permission's target_anchor parameter.

CRITICAL: On lookup miss, these functions RAISE the appropriate 
*NotFoundError (404) — they do NOT return None to signal 
"not found." Returning None would short-circuit the cascade 
clause in has_permission to TRUE (no target_anchor → cascade 
inactive → grant matches), creating a security regression per 
F-THREADING-4.

None is returned ONLY when the request has no specific target 
(list endpoints, aggregate stats, PLATFORM-scope checks).
"""
```

Function signatures (return-type narrowed to `str` since miss → raise):

```python
async def get_tenant_anchor(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> str: ...

async def get_org_node_anchor(
    tenant_id: UUID,
    node_id: UUID,
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> str: ...

async def get_tenant_user_anchor(
    user_id: UUID,
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> str: ...
```

**Behavioral contracts:**

- `get_tenant_anchor` — returns the tenant's root org_node path
  (`node_type='TENANT' AND parent_id IS NULL` filter). Raise
  TenantNotFoundError on miss. Used by `/tenants/{tenant_id}` and
  `/tenants/{tenant_id}/org-tree`.

- `get_org_node_anchor` — returns a specific node's path
  (composite-key query per D-34). Raise OrgNodeNotFoundError on
  miss. Used by `/tenants/{tenant_id}/org-nodes/{node_id}/children`.

- `get_tenant_user_anchor` — returns the user's tenant-root path
  (TenantUser has no `home_org_node_id` per F-ANCHOR-2; defaults to
  tenant root). Raise TenantUserNotFoundError on miss. Used by
  `/tenant-users/{user_id}`.

**Implementation choices to surface in the report:**

- For `get_tenant_user_anchor`: JOIN approach (single query
  `tenant_users → tenant_id → org_nodes filter`) vs two-step
  (lookup `tenant_id`, then `org_nodes`). Pick what matches existing
  Repo patterns. Surface choice.
- Error class location: see Caution #3.
- SQL style: raw text() vs ORM expression — mirror what's used
  elsewhere in the codebase for similar single-row lookups.

Read existing Repo lookup methods first; do not improvise.

### File 3: `src/admin_backend/auth/gate_allowlist.py` (NEW)

```python
"""Mandatory-gate-discipline allowlist.

Endpoints listed here are explicitly exempt from RBAC gating. They 
require authentication (via AuthMiddleware) but have no permission 
gate. The mandatory-gate-discipline test asserts every APIRoute is 
either gated (has __permission_gate__ marker) OR is in this set 
OR is in PUBLIC_PATHS.

COUPLING NOTE: This frozenset must stay in sync with:
  - PUBLIC_PATHS in middleware/auth.py (the auth-skip layer)
  - Mandatory-gate-discipline test (consumes both sets)
  - Any new API addition: if intentionally ungated, MUST be added 
    here or the discipline test fails the deploy
  
Adding an endpoint without either a gate OR an allowlist entry is 
a deploy-time error by design — the structural guarantee.

Maintenance: 5 paths exempt for legitimate reasons:
  - /me/* (caller-state endpoints; auth required but data scoped 
    to the caller by design)
  - /lookups, /permissions, /permission-matrix (reference data)
  - /roles, /roles/{role_id}/permissions (role catalogue view; 
    arguably should be gated — see CLAUDE.md forward note)
"""
from __future__ import annotations


GATE_EXEMPT_PATHS: frozenset[str] = frozenset({
    "/api/v1/me/permissions",
    "/api/v1/me/can-do",
    "/api/v1/lookups",
    "/api/v1/permissions",
    "/api/v1/permission-matrix",
    "/api/v1/roles",
    "/api/v1/roles/{role_id}/permissions",
})
```

Verify the exact path strings against the live FastAPI registration
(path params in braces match FastAPI's routing convention).

### File 4: `src/admin_backend/auth/permissions.py` (MODIFIED)

Modify the existing `require()` factory at lines ~365-... 

**The current factory** (verified at HEAD ddea23c) hardcodes
`target_anchor=None` in its inner gate function and does NOT attach
a marker. Two changes:

1. **Add keyword-only `anchor_dep` parameter** to the factory signature.
2. **Two inner-function shapes** based on `anchor_dep` presence
   (FastAPI requires static signatures; cannot conditionally include
   a `Depends()` parameter in one function — needs two distinct inner
   functions).
3. **Attach `PermissionGateInfo` marker** to whichever inner function
   is returned, before returning.

**Behavioral contract:**

```
require(module, resource, action, scope, *, anchor_dep=None) → 
    FastAPI dependency callable
    
Inner gate function behavior:
  - Resolves auth via Depends(get_auth_context)
  - Resolves session via Depends(get_tenant_session_dep)
  - If anchor_dep set: resolves target_anchor via Depends(anchor_dep)
  - Calls has_permission(session, auth, M, R, A, S, target_anchor)
  - On (allowed=False): raises PermissionDeniedError with structured 
    context (module, resource, action, scope, target_anchor, reason_code)
  - On (allowed=True): returns None (FastAPI dep completes)
  
Marker attachment (after defining the inner function, before return):
  gate.__permission_gate__ = PermissionGateInfo(
      module=module,
      resource=resource,
      action=action,
      scope=scope,
      anchor_dep=anchor_dep,
  )
```

**Shape sketch (refine against actual factory at HEAD):**

```python
def require(
    module: ModuleCode,
    resource: PermissionResource,
    action: PermissionAction,
    scope: PermissionScope,
    *,
    anchor_dep: Callable[..., Awaitable[str]] | None = None,
) -> Callable[..., Awaitable[None]]:
    """FastAPI gate dependency factory.
    
    Returns a dependency callable that checks the requesting user 
    has the required permission tuple. If anchor_dep is provided, 
    it resolves the target_anchor passed to has_permission for 
    cascade-aware checks.
    """
    if anchor_dep is None:
        # Inner function: no target_anchor parameter
        # Resolves auth + session via Depends
        # Passes target_anchor=None to has_permission
        ...
    else:
        # Inner function: takes target_anchor via Depends(anchor_dep)
        # Resolves auth + session + target_anchor via Depends
        # Passes resolved target_anchor to has_permission
        ...
    
    # Attach marker (same on either inner function)
    gate.__permission_gate__ = PermissionGateInfo(...)
    return gate
```

**mypy strict consideration:** `Callable[..., Awaitable[str]] | None`
on `anchor_dep` may surface typing complications. Consider a type
alias if helpful:

```python
AnchorDep = Callable[..., Awaitable[str]]
```

Then `anchor_dep: AnchorDep | None = None`. Surface choice in report.

Add import for `PermissionGateInfo` from `auth/gate_info.py` at the
top of the file.

### File 5+: Router files (MODIFIED)

For each retrofit target, add `Depends(require(...))` with the
locked gate per the Phase 1 table. Add per-resource anchor deps
where applicable.

Example shape (per `/tenants/{tenant_id}`):

```python
from admin_backend.auth.permissions import require
from admin_backend.auth.anchor_deps import get_tenant_anchor
from admin_backend.models.enums import (
    ModuleCode, PermissionResource, PermissionAction, PermissionScope,
)

@router.get("/{tenant_id}")
async def get_tenant_by_id(
    tenant_id: UUID,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
        anchor_dep=get_tenant_anchor,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> TenantDetailResponse:
    ...
```

Surface deviations from the locked Phase 1 table only if a router's
handler has a signature surprise (e.g., uses different path
parameter names than expected) — surface, don't silently work
around.

For `/platform-users/*`:
- Delete `_require_platform_auth` helper (lines 102-109).
- Replace 2 call sites (lines 216, 258) with
  `_: None = Depends(require(ADMIN, USERS, VIEW, GLOBAL))`.
- Keep `PlatformAccessRequiredError` class with inline comment:
  ```python
  # DEAD CODE candidate (post-6.9.3.2 retrofit).
  # _require_platform_auth retired during Step 6.9.3.2; this class 
  # was its sole raise site. Kept as a forward-defensive artefact 
  # in case a future PLATFORM-only check needs a distinct error 
  # code from PERMISSION_DENIED. Safe to remove if no consumer 
  # emerges by Stage 3.
  class PlatformAccessRequiredError(ClientError):
      ...
  ```
- Cleanup 4 docstring references in:
  - `routers/v1/tenant_users.py:11`
  - `routers/v1/org_tree.py:20`
  - `routers/v1/platform_users.py:12`
  - `tests/integration/test_platform_users_router.py:347`

### File for test: `tests/integration/test_gate_discipline.py` (NEW)

```python
"""Mandatory-gate-discipline meta-test.

Asserts every APIRoute in the FastAPI app is either:
  (a) Gated — has the __permission_gate__ marker attribute on at 
      least one of its dependencies' .call, OR
  (b) Exempt — path appears in GATE_EXEMPT_PATHS or PUBLIC_PATHS

Fails the build if any APIRoute is neither gated nor allowlisted.
Run as part of every CI pytest invocation. LOAD-BEARING test.
"""
from __future__ import annotations
import pytest
from fastapi.routing import APIRoute

from admin_backend.auth.gate_allowlist import GATE_EXEMPT_PATHS
from admin_backend.main import create_app
from admin_backend.middleware.auth import PUBLIC_PATHS


def test_gate_discipline_every_route_is_gated_or_allowlisted() -> None:
    """LOAD-BEARING — Mandatory-gate-discipline meta-assertion.
    
    Every APIRoute must be either:
      (a) Gated — at least one dependency in route.dependant.dependencies 
          has __permission_gate__ marker on its .call
      (b) Allowlisted — route.path in GATE_EXEMPT_PATHS or PUBLIC_PATHS
    
    A new endpoint that lacks BOTH a gate AND an allowlist entry 
    fails this test, preventing it from shipping ungated by 
    accident. Removing a gate without adding to the allowlist also 
    fails.
    """
    app = create_app()
    allowed_paths = GATE_EXEMPT_PATHS | PUBLIC_PATHS
    ungated_routes = []
    
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue  # Skip non-APIRoute (e.g., /docs, /redoc)
        
        if route.path in allowed_paths:
            continue  # Explicitly exempt
        
        has_gate = any(
            hasattr(dep.call, "__permission_gate__")
            for dep in route.dependant.dependencies
        )
        if not has_gate:
            ungated_routes.append(route.path)
    
    assert not ungated_routes, (
        f"Routes neither gated nor allowlisted: {ungated_routes}. "
        f"Either add Depends(require(...)) to the handler or add the "
        f"path to GATE_EXEMPT_PATHS in auth/gate_allowlist.py."
    )
```

Positive verification of marker contents (assert each gate's marker 
captures the right tuple) lives in `tests/integration/test_gate_retrofit.py` 
as T_RET_6, not here. This file contains the structural meta-test 
only.

### File for new behavioral tests: `tests/integration/test_gate_retrofit.py` (NEW)

The discipline meta-test verifies STRUCTURAL coverage (every route 
has a gate or is allowlisted). It does NOT verify BEHAVIORAL 
coverage (the gate actually does the right thing end-to-end).

**8 new behavioral tests; 3 LOAD-BEARING.** Mirror the 6.9.2 
test_me_router.py pattern (test naming, fixture usage, assertion 
style).

```
T_RET_1 — SUPER_ADMIN passes /tenants/{id} via GLOBAL→TENANT cascade
          Verifies: cascade end-to-end through the gate (not just 
          inside has_permission). SUPER_ADMIN holds .VIEW.GLOBAL; 
          gate requires .VIEW.TENANT; cascade satisfies.
          
T_RET_2 — OWNER on /tenants/{their_id} — DEFERRED
          xfail with reason: needs operator seed grant update 
          (OWNER → ADMIN.TENANTS.VIEW.TENANT). Un-skip when 
          operator applies Phase 3a/3b updates.
          
T_RET_3 — Cross-tenant request returns 404 via anchor dep
          PLATFORM user requests /tenants/{other_tenant_id} where 
          other tenant is RLS-invisible OR doesn't exist. Anchor 
          dep raises TenantNotFoundError (404) BEFORE the gate 
          evaluates has_permission. Verifies F-THREADING-4 
          security invariant: anchor-lookup-miss raises 404, not 
          returns None.
          LOAD-BEARING — verifies the security-critical invariant 
          that anchor-miss does not short-circuit the gate.
          
T_RET_4 — Endpoint with no auth → 401 (regression baseline)
          Pick one retrofitted endpoint. Confirm gate runs AFTER 
          auth middleware; missing auth returns 401, not 403.
          
T_RET_5 — _require_platform_auth retirement: behavioral equivalence
          PLATFORM user (SUPER_ADMIN) passes /platform-users/* via 
          new require(ADMIN, USERS, VIEW, GLOBAL) gate.
          TENANT user (any) denied with code=PERMISSION_DENIED.
          Replaces the prior test that asserted 
          code=PLATFORM_ACCESS_REQUIRED.
          LOAD-BEARING — confirms retirement doesn't regress 
          the access-control surface.
          
T_RET_6 — Gate marker introspection (positive verification)
          For 3 sample retrofitted routes, read the marker from 
          route.dependant.dependencies and assert it captures 
          the expected (module, resource, action, scope) tuple. 
          Pairs with the discipline test (which only verifies 
          marker EXISTENCE).
          LOAD-BEARING — without this, the discipline test could 
          be satisfied by a stub marker that doesn't match the 
          gate's actual semantics.
          
T_RET_7 — Anchor dep injection: target_anchor flows through
          /tenants/{id}/org-nodes/{nid}/children with SUPER_ADMIN. 
          Verify (via DB-side inspection of EXPLAIN-style query 
          plans, or test fixture inspection) that the 
          target_anchor was actually passed to has_permission's 
          query — not silently dropped. One approach: log/capture 
          the SQL parameters in a test wrapper. Surface chosen 
          verification mechanism.
          
T_RET_8 — Multi-user-type endpoint behavior under cascade
          /tenant-users gated on ADMIN.USERS.VIEW.TENANT. 
          PLATFORM SUPER_ADMIN passes (cascade from .VIEW.GLOBAL). 
          TENANT OWNER passes (direct .VIEW.TENANT grant).
          Verifies the audience-dispatch-via-cascade design.
```

**Test fixture conventions** (mirror 6.9.2 me_router tests):
- JWT helpers from conftest
- Test data uses seeded users (no mocks unless explicitly noted)
- Assertions on response code + structured fields (where applicable)

### Tests requiring fixture updates (existing tests)

After identifying the retrofit's surface, audit affected tests 
BEFORE editing. Produce a table in the report:

```
Test file                          | Test name | JWT used    | Status
───────────────────────────────────|-----------|-------------|─────────────────
test_tenants_router.py             | test_X    | SUPER_ADMIN | unchanged
test_tenants_router.py             | test_Y    | OWNER       | xfail-seed-update
test_platform_users_router.py      | A2        | NO_PLATFORM | update assertion 
                                                              (code=PERMISSION_DENIED)
...
```

Strategy categories:
- **unchanged** — JWT is SUPER_ADMIN or has appropriate grants; 
  cascade or direct grant carries them through the new gate
- **xfail-seed-update** — needs `pytest.mark.xfail(reason="...")` 
  with explicit reason; operator un-skips post-seed-update
- **update assertion** — error code or response shape changed (e.g., 
  PLATFORM_ACCESS_REQUIRED → PERMISSION_DENIED)
- **JWT swap** — test fixture switched from one role to another to 
  preserve test intent

Surface the full table in the report before proceeding with edits.

### CLAUDE.md modifications

**Current state — Completed (add):**

```
- Section 6.9.3.2 — endpoint retrofit + per-resource anchor deps + 
  mandatory-gate-discipline test. 14 retrofitted endpoints (out of 
  19 retrofit-eligible) with Depends(require(...)); 5 exempt 
  endpoints in auth/gate_allowlist.py. PermissionGateInfo frozen 
  dataclass at auth/gate_info.py marks every gate. Anchor 
  dependencies at auth/anchor_deps.py (3 functions; raise 404 on 
  miss). _require_platform_auth retired (2 call sites → 
  require(ADMIN, USERS, VIEW, GLOBAL)); PlatformAccessRequiredError 
  kept as dead-code with inline comment. Mandatory-gate-discipline 
  meta-test (LOAD-BEARING) at tests/integration/test_gate_discipline.py. 
  Catalogue/role-grant updates (1 permission + 2 role_permissions) 
  applied operator-side post-commit per data-seeding posture P-1/P-2/P-3. 
  Total pytest 308 → 308 + N (N depends on retrofit-test additions; 
  surface actual count).
```

**Add new "Note on gate allowlist coupling" maintenance convention:**

```
### Note on gate allowlist coupling

The mandatory-gate-discipline test depends on two frozensets that 
must stay in sync:
  - PUBLIC_PATHS at middleware/auth.py:38-45 (auth-skip layer)
  - GATE_EXEMPT_PATHS at auth/gate_allowlist.py (gate-skip layer)

When adding a new endpoint to the API:
  - If the endpoint should be RBAC-gated: add Depends(require(...)) 
    to the handler. No allowlist edit needed.
  - If the endpoint should be auth-required but ungated: add path 
    to GATE_EXEMPT_PATHS.
  - If the endpoint should be public (no auth at all): add path to 
    PUBLIC_PATHS.

The discipline test fails the build if a new APIRoute appears without 
matching any of these three. This is the structural guarantee that 
prevents endpoints from shipping ungated by accident.
```

**Forward-note actions** (5 new + 1 update to existing):

1. **NEW forward-notes** (assign next 5 available FN-AB numbers):

```
### FN-AB-NN — Dashboard + module-access dedicated tuples review

/dashboard/* and /module-access/* are currently gated on 
ADMIN.TENANTS.VIEW.TENANT as a proxy (Step 6.9.3.2 design). The 
semantically purer design adds dedicated tuples:
  - ADMIN.DASHBOARD.VIEW.{GLOBAL,TENANT}
  - ADMIN.MODULES.VIEW.{GLOBAL,TENANT}

Both require:
  - DDL: extend resource_enum to add DASHBOARD + MODULES values 
    (Alembic migration)
  - Catalogue: 4 new permission rows
  - Grants: assign GLOBAL to PLATFORM-audience roles, TENANT to OWNER

Revisit when:
  - Authorization for these endpoints needs to diverge from tenant-row 
    authorization
  - Frontend UI gating needs distinct on/off for dashboard or modules
  - These endpoints grow actions beyond VIEW (CONFIGURE, EXECUTE)
```

```
### FN-AB-NN — Reference-data + roles gating review

Currently exempt from RBAC gating (auth required but ungated), 
accessible to any authenticated user:
  - /lookups
  - /permissions
  - /permission-matrix
  - /roles
  - /roles/{role_id}/permissions

Revisit gating these on ADMIN.ROLES.VIEW.TENANT when:
  - Custom roles arrive (FN-AB-06)
  - Stage 2 write endpoints for roles introduce stricter visibility 
    semantics
  - Frontend role-management UI surfaces and requires gated reads 
    for consistency with writes

/lookups stays exempt regardless.
```

```
### FN-AB-NN — /role-assignments dedicated tuple

/role-assignments currently gated on ADMIN.USERS.VIEW.TENANT as a 
proxy. Dedicated ADMIN.ROLE_ASSIGNMENTS.* tuples are purer but 
unnecessary in v0.

Revisit when:
  - Stage 2 writes introduce role-assignment-specific actions 
    (assign, revoke, transfer) needing distinct permissions
  - A role should view users but NOT role-assignments
```

```
### FN-AB-NN — PLATFORM_ADMIN / SUPPORT_ADMIN ADMIN.USERS.VIEW.GLOBAL coverage

ADMIN.USERS.VIEW.GLOBAL is held by SUPER_ADMIN only at HEAD. 
PLATFORM_ADMIN and SUPPORT_ADMIN currently pass /platform-users/* 
gates (via SUPER_ADMIN equivalence) but NOT explicitly via grant.

Revisit when:
  - PLATFORM_ADMIN or SUPPORT_ADMIN users surface a real product 
    need to view platform-user records and discover the gap
  - Stage 2 platform-user write endpoints land (CONFIGURE / EXECUTE 
    will surface the same coverage question)

Resolution: grant ADMIN.USERS.VIEW.GLOBAL to PLATFORM_ADMIN and 
SUPPORT_ADMIN via role_permissions seed update. No code change.
```

```
### FN-AB-NN — PlatformAccessRequiredError removal

Class kept at platform_users.py:78-87 as dead-code post-6.9.3.2 
retrofit. _require_platform_auth retired; class has no current 
raise site.

Resolution: delete the class once Stage 3 (or earlier) confirms no 
future PLATFORM-only error code is needed. Test assertion updates 
required (2 tests assert code=PLATFORM_ACCESS_REQUIRED; convert to 
code=PERMISSION_DENIED).
```

2. **UPDATE to existing forward-note** (no new FN-AB number; modify 
   in place):

**FN-AB-26 — _require_platform_auth retirement: mark RESOLVED:**

Update the entry to indicate the retirement happened during Step
6.9.3.2 with the locked replacement.

### BUILD_PLAN.md modifications

Section 6.9 status flipped to "ALL DONE" — 6.9.1, 6.9.2, 6.9.3.1,
6.9.3.2 all complete. Section 6.9 marked COMPLETE.

---

## Caution-first risks

1. **Existing tests break.** Some per-router tests use JWTs that
   today pass via RLS-only but fail with the new gates. Audit
   every test that hits a retrofitted endpoint; either update the
   JWT fixture to SUPER_ADMIN (passes everything via cascade) or
   mark `pytest.mark.skip(reason="Needs seed grant update
   post-Step-6.9.3.2")` with explicit reason.

2. **OWNER cannot exercise 3 retrofitted endpoints until seed
   updates are applied.** `/tenants/{id}`, `/tenants/{id}/org-tree`,
   `/tenants/{id}/org-nodes/{nid}/children`. Tests using OWNER JWT
   for these will fail until operator applies the +2 grants. Use
   skip-with-reason for these specific tests.

3. **Anchor dep error classes — concrete audit needed.** Per the 
   6.9.3.2 investigation report F-THREADING-4, these classes exist 
   at HEAD as per-router classes:
   - `OrgNodeNotFoundError` at `routers/v1/org_tree.py:93`
   - `TenantUserNotFoundError` at `routers/v1/tenant_users.py:83`
   - `RoleNotFoundError` at `routers/v1/rbac.py:93`
   
   `TenantNotFoundError` is NOT mentioned in the investigation; 
   verify at HEAD. Likely needs to be CREATED.
   
   **Layering concern:** anchor_deps.py lives in `auth/`. The 
   error classes currently live in `routers/v1/`. Importing 
   `auth/` → `routers/v1/` creates a backward-pointing dependency 
   that violates layering (auth is a lower-level concern; routers 
   are higher).
   
   **Two paths to resolve, both surface in the report:**
   
   - **(a) Move existing error classes to `errors.py`.** Make 
     OrgNodeNotFoundError, TenantUserNotFoundError shared across 
     auth/ and routers/v1/. Update existing call sites. Define 
     TenantNotFoundError in `errors.py`. Cleanest layering; 
     mechanically modest.
   
   - **(b) Define new mirror classes in `errors.py` (or `auth/errors.py`) 
     for anchor deps only.** Keep the existing per-router classes 
     where they are. Two parallel hierarchies. Worse layering but 
     no churn on existing call sites.
   
   **Recommendation: (a).** Cleaner; matches `PermissionDeniedError` 
   precedent (lives in `errors.py`, shared across auth/ and 
   routers/v1/). Mechanically: move 2 classes, define 1 new, 
   update existing call-site imports.
   
   Surface the chosen path with rationale in the report.

4. **TenantUser has no home_org_node_id at HEAD.** Per F-ANCHOR-2.
   `get_tenant_user_anchor` does a 2-step lookup (user_id →
   tenant_id, then tenant_id → tenant-root path). Single JOIN
   query OR two SELECTs; surface chosen approach in the report.

5. **PlatformAccessRequiredError tests — 3 assertion sites total.** 
   After `_require_platform_auth` retirement, all 3 must update 
   from `code=PLATFORM_ACCESS_REQUIRED` to `code=PERMISSION_DENIED`:
   
   - `tests/integration/test_platform_users_router.py` — 2 test 
     methods (investigation cited line :340-...; look up exact 
     names at HEAD)
   - `scripts/test_endpoints_cloud.sh` — 1 assertion (cloud-only; 
     update via inspection but cannot verify locally — operator 
     verifies post-deploy)
   
   The test BEHAVIOR (denial for non-PLATFORM users) is unchanged; 
   only the asserted error code changes because the gate now uses 
   PermissionDeniedError uniformly.

6. **The mandatory-gate-discipline test fails on first run** if
   anything is missed. This is by design — that's the point of the
   meta-test. Use its failure message to identify any missed
   retrofit and add it. Don't silence the test to make it pass.

7. **FastAPI route iteration may include framework routes.** Add a
   filter for `isinstance(route, APIRoute)` to skip /docs, /redoc,
   /openapi.json. These are not APIRoute (they're Route or
   Mount), so the filter naturally excludes them. Verify at HEAD.

8. **`require()`'s closure captures (M, R, A, S, anchor_dep).**
   Python late-binding pitfalls don't apply here because the
   factory is called once per gate; each gate gets its own closure.
   Sanity-check by mentally tracing one gate creation.

9. **Some retrofitted endpoints have multiple existing `Depends()`
   parameters.** Adding `Depends(require(...))` to a handler with
   existing `Depends(get_auth_context)` and `Depends(...)` works
   normally; FastAPI resolves them in dependency-graph order.
   Don't worry about positioning; just add the gate dep at the
   handler signature.

10. **mypy strict on the anchor_dep parameter type.** The annotation
    `Callable[..., Awaitable[str]] | None` may surface mypy
    issues with the FastAPI `Depends()` wrapping. If mypy
    complains, an explicit type alias may help:
    ```python
    AnchorDep = Callable[..., Awaitable[str]]
    ```
    Then `anchor_dep: AnchorDep | None = None`. Surface choice if
    mypy push back.

11. **Smoke/endpoint scripts may break in subtle ways.** 
    `scripts/smoke_curl.sh` and `scripts/test_endpoints.sh` exercise 
    endpoints under various auth scenarios. After retrofit:
    - 200 cases with SUPER_ADMIN should still pass via cascade
    - 401 cases (no auth) still get 401 (gate runs AFTER auth 
      middleware)
    - Cases asserting specific behavior for non-SUPER_ADMIN users 
      may have changed shape (e.g., previously RLS-empty-result vs 
      now 403-gate-denial)
    
    Audit each script's curl invocations during pre-flight. 
    Surface any that need updating. Do NOT silently change 
    assertions; surface the change with rationale.

---

## Testing and regression discipline

### New tests

1 mandatory-gate-discipline meta-test at 
`tests/integration/test_gate_discipline.py` (LOAD-BEARING).

8 gate-behavior tests at `tests/integration/test_gate_retrofit.py` 
(T_RET_1 through T_RET_8). 3 LOAD-BEARING: T_RET_3 (anchor-miss 
security invariant), T_RET_5 (retirement behavioral equivalence), 
T_RET_6 (marker positive verification).

Total new tests: 9 (1 discipline + 8 retrofit).

### Pytest count delta

Pre-step: 308 passes.

Adds:
  +1 discipline test
  +8 retrofit tests
  
Subtracts (xfail-seed-update; do not count as failing):
  -3 OWNER on /tenants/{id}, /tenants/{id}/org-tree, 
     /tenants/{id}/org-nodes/{nid}/children (audit existing tests 
     during pre-flight to confirm count)

Net expected: **308 → 314 passes + 3 xfail** (or similar, depending 
on what the per-router test audit surfaces). Surface actual count 
in the report.

### Tests deliberately not added

- New per-endpoint retrofit tests for EVERY gated endpoint. The 8 
  retrofit tests sample the patterns (cascade, anchor, 
  multi-user-type, retirement); per-endpoint coverage is provided by 
  the discipline meta-test (structural) + existing per-router tests 
  (behavioral, unchanged).
- Performance/load tests. EXPLAIN ANALYZE for 1-2 representative 
  endpoints is sufficient.
- Test coverage of every PermissionDeniedError raise path. 6.9.2 
  tests cover the error-raising behavior; the retrofit uses the 
  same path.

### Regression risk surface

1. **All 308 pre-step tests.** Most should pass unchanged
   (SUPER_ADMIN JWT, passes everything via cascade). Some require
   skip-with-reason (OWNER on 3 endpoints awaiting seed grant
   updates).

2. **Existing test patterns using "regular user" JWTs.** Audit per
   test; update JWT to one with sufficient grants OR mark
   skip-with-reason.

3. **Smoke and endpoint scripts.** Use SUPER_ADMIN by default;
   should pass unchanged.

4. **mypy on the new files and factory changes.** Strict mode may
   surface issues with `Callable[..., Awaitable[str | None]]`
   typing.

5. **EXPLAIN ANALYZE for one anchor-dep-using endpoint.** Verify
   the anchor lookup query is fast (single indexed SELECT) and the
   has_permission query is unchanged.

---

## Verification harness

Run in order. All must be green before reporting.

```bash
# 0. Pre-verification reseed (if needed).
# Verify the actual seed loader invocation against scripts/ at HEAD; 
# the seed loader's CLI may have evolved since 6.9.3.1. The 
# invocation below is illustrative — confirm before running.
uv run python -m scripts.seed_dev_data --reset

# 0a. Confirm seed counts (sanity).
psql "$DATABASE_URL" -c "
SET search_path TO core, public;
SELECT
  (SELECT COUNT(*) FROM tenant_users) AS tu,
  (SELECT COUNT(*) FROM platform_users) AS pu,
  (SELECT COUNT(*) FROM permissions) AS perm,
  (SELECT COUNT(*) FROM role_permissions) AS rp;
"
# Expected: tu=17, pu=3, perm=30, rp=120 (PRE-step baseline;
# operator adds +1 perm + 2 rp POST-commit, not in this step).

# 1. Type checking.
uv run mypy src/admin_backend/

# 2. Pytest, all tests.
uv run pytest --tb=no -q

# 2a. Mandatory-gate-discipline meta-test (LOAD-BEARING).
uv run pytest tests/integration/test_gate_discipline.py -v

# 2b. Gate retrofit behavioral tests.
uv run pytest tests/integration/test_gate_retrofit.py -v
# Expected: 8 tests; 3 marked LOAD-BEARING; 3 marked xfail-seed-update 
# (T_RET_2 for OWNER cases).

# 2c. Per-router regression checkpoint.
# Existing per-file counts should be unchanged or have explicit
# xfail-with-reason markers. Surface any drop with reason.

# 3. Smoke test.
uv run python -m scripts.smoke_test

# 3a. Smoke curl + local endpoint tests.
bash scripts/smoke_curl.sh
# Expected: PASS count unchanged (still 22).

bash scripts/test_endpoints.sh
# Expected: clean run; counts unchanged.

# 4. Alembic heads.
uv run alembic heads
# Expected: 3e05299cb533 (unchanged; no migration in this step).

# 5. Import smoke.
uv run python -c "
from admin_backend.auth.gate_info import PermissionGateInfo
from admin_backend.auth.anchor_deps import (
    get_tenant_anchor, get_org_node_anchor, get_tenant_user_anchor,
)
from admin_backend.auth.gate_allowlist import GATE_EXEMPT_PATHS
from admin_backend.auth.permissions import require
print('All imports OK')
print(f'GATE_EXEMPT_PATHS: {len(GATE_EXEMPT_PATHS)} paths')
"

# 6. Live route enumeration with marker introspection.
uv run python -c "
from fastapi.routing import APIRoute
from admin_backend.main import create_app
from admin_backend.auth.gate_allowlist import GATE_EXEMPT_PATHS
from admin_backend.middleware.auth import PUBLIC_PATHS

app = create_app()
gated = []
exempt = []
public = []
unknown = []

for route in app.routes:
    if not isinstance(route, APIRoute):
        continue
    if route.path in PUBLIC_PATHS:
        public.append(route.path)
        continue
    if route.path in GATE_EXEMPT_PATHS:
        exempt.append(route.path)
        continue
    has_gate = any(
        hasattr(d.call, '__permission_gate__')
        for d in route.dependant.dependencies
    )
    if has_gate:
        gated.append(route.path)
    else:
        unknown.append(route.path)

print(f'Gated: {len(gated)}, Exempt: {len(exempt)}, Public: {len(public)}, Unknown: {len(unknown)}')
if unknown:
    print(f'WARNING: ungated, unlisted routes: {unknown}')
"

# 7. EXPLAIN ANALYZE — anchor dep + has_permission queries for 
# /tenants/{bucees_id} as SUPER_ADMIN.

# 7a. Anchor dep query (single-row indexed lookup, should be < 1ms):
psql "$DATABASE_URL" -c "
SET search_path TO core, public;
EXPLAIN ANALYZE
SELECT path FROM core.org_nodes 
WHERE tenant_id = '<bucees_tenant_id>' 
  AND node_type = 'TENANT' 
  AND parent_id IS NULL;
"
# Expected: Index Scan on org_nodes; execution time < 1ms.
# Substitute actual tenant_id from seed (Buc-ee's row).

# 7b. has_permission TENANT path query (already verified in 6.9.3.1):
# Re-run the 6.9.3.1 harness step 8 query. Plan should be unchanged 
# now that the gate calls it; ANY clause still uses pk_permissions 
# index; execution time comparable to 6.9.3.1 baselines (0.139 ms 
# PLATFORM, 0.146 ms TENANT).

# If either plan degrades (seq scan, > 5x baseline), surface and 
# pause before proceeding.
```

---

## Report (BEFORE proposing commit)

1. Pre-flight outputs (items 1-18 explicit results).
2. Resolution of implementation choices:
   - Anchor dep error classes (which exist; where defined)
   - TenantUser anchor lookup approach (JOIN vs 2 SELECTs)
   - mypy type alias for AnchorDep (used or not)
   - Test fixture updates (which tests updated; which skipped with
     reason)
3. Diffs:
   - New: `auth/gate_info.py`, `auth/anchor_deps.py`,
     `auth/gate_allowlist.py`, 
     `tests/integration/test_gate_discipline.py`,
     `tests/integration/test_gate_retrofit.py`
   - Modified: `auth/permissions.py` (factory updates), 
     `errors.py` (centralized not-found error classes per Caution 
     #3 resolution path), 6+ router files (gates added),
     `platform_users.py` (helper retired), CLAUDE.md, BUILD_PLAN.md
   - Bundled: `prompts/step-6_9_3_2-endpoint-retrofit-2026-05-13.md`,
     `reports/step-6_9_3_2-design-investigation-2026-05-13.md`
4. Verification harness output (steps 0 - 7 all green).
5. Pre/post pytest counts. Expected: 308 → 314 passes + 3 xfail 
   (1 discipline + 8 retrofit new tests; 3 OWNER tests marked 
   xfail-seed-update). Surface actual counts; if delta differs 
   from expected, explain.
6. Per-endpoint retrofit summary table:
   - 14 retrofitted endpoints with confirmed gate tuple + anchor 
     dep (where applicable)
   - 5 allowlisted endpoints
   - 6 PUBLIC_PATHS (unchanged)
7. Test audit table (from pre-flight 16) including final 
   disposition of each affected test (unchanged / xfail-seed-update 
   / update assertion / JWT swap).
8. EXPLAIN ANALYZE output for anchor dep query + has_permission 
   query (per verification harness step 7).
9. Any deviation from the locked design decisions (should be 
   none).
10. Forward-notes: list the 5 new FN-AB numbers assigned with 
    one-line summary each (dashboard+module-access dedicated 
    tuples, reference-data+roles gating, /role-assignments 
    dedicated tuple, PLATFORM_ADMIN/SUPPORT_ADMIN coverage, 
    PlatformAccessRequiredError removal).
11. FN-AB-26 resolution: quote the updated wording marking 
    retirement complete.

Wait for explicit operator authorisation before staging or
committing.

---

## Surface-and-stop scenarios

Stop and report (do not work around silently) if:

1. Pytest baseline is not 308 passes at pre-flight.
2. Live route enumeration shows fewer or more than 23 APIRoutes;
   surface the actual list against the prompt's gate-assignment
   table.
3. Any retrofit-target endpoint has a handler signature that
   doesn't match expectations (e.g., expected path param doesn't
   exist).
4. Anchor dep error classes are not findable at HEAD; surface
   what's available and propose definition location.
5. mypy strict surfaces non-trivially-fixable errors.
6. More than ~5 existing tests break in ways NOT resolvable by
   JWT-fixture update or skip-with-reason.
7. EXPLAIN ANALYZE shows the anchor dep query is unexpectedly
   slow (full table scan, missing index, etc.).
8. The mandatory-gate-discipline test reveals an APIRoute that
   shouldn't be retrofitted but also isn't in any allowlist.

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task"
Pattern A:

```bash
git status
git add src/admin_backend/auth/gate_info.py \
        src/admin_backend/auth/anchor_deps.py \
        src/admin_backend/auth/gate_allowlist.py \
        src/admin_backend/auth/permissions.py \
        src/admin_backend/errors.py \
        src/admin_backend/routers/v1/tenants.py \
        src/admin_backend/routers/v1/platform_users.py \
        src/admin_backend/routers/v1/tenant_users.py \
        src/admin_backend/routers/v1/org_tree.py \
        src/admin_backend/routers/v1/dashboard.py \
        src/admin_backend/routers/v1/modules_access.py \
        src/admin_backend/routers/v1/rbac.py \
        tests/integration/test_gate_discipline.py \
        tests/integration/test_gate_retrofit.py \
        tests/integration/test_platform_users_router.py \
        scripts/test_endpoints_cloud.sh \
        CLAUDE.md BUILD_PLAN.md \
        prompts/step-6_9_3_2-endpoint-retrofit-2026-05-13.md \
        reports/step-6_9_3_2-design-investigation-2026-05-13.md
# Add any other modified test files (per audit table in pre-flight 16)
git commit -m "$(cat <<'EOF'
Step 6.9.3.2: endpoint retrofit + anchor deps + gate discipline

- NEW: src/admin_backend/auth/gate_info.py. PermissionGateInfo 
  frozen dataclass (module, resource, action, scope, anchor_dep). 
  Marker attached by require() factory to every gate function 
  via gate.__permission_gate__.
- NEW: src/admin_backend/auth/anchor_deps.py. 3 anchor lookup 
  functions: get_tenant_anchor (tenant root), get_org_node_anchor 
  (specific node, composite-key per D-34), get_tenant_user_anchor 
  (user → tenant root). Raise 404 (*NotFoundError) on miss; never 
  return None to signal not-found (per F-THREADING-4 security 
  consideration).
- NEW: src/admin_backend/auth/gate_allowlist.py. GATE_EXEMPT_PATHS 
  frozenset (7 paths: /me/* × 2, /lookups, /permissions, 
  /permission-matrix, /roles, /roles/{role_id}/permissions).
- NEW: tests/integration/test_gate_discipline.py. LOAD-BEARING 
  meta-test. Asserts every APIRoute is either gated (has marker) 
  OR in GATE_EXEMPT_PATHS OR in PUBLIC_PATHS. Deploy-time guarantee.
- NEW: tests/integration/test_gate_retrofit.py. 8 behavioral 
  tests (T_RET_1 through T_RET_8); 3 LOAD-BEARING: T_RET_3 
  (anchor-miss raises 404, never returns None — F-THREADING-4 
  security invariant), T_RET_5 (_require_platform_auth retirement 
  behavioral equivalence), T_RET_6 (gate marker positive 
  verification — paired with structural discipline test).
- MODIFIED: src/admin_backend/errors.py. Centralized 
  TenantNotFoundError (new), OrgNodeNotFoundError (moved from 
  routers/v1/org_tree.py), TenantUserNotFoundError (moved from 
  routers/v1/tenant_users.py). Anchor deps in auth/ now import 
  from errors.py without backward layering violation.
- MODIFIED: src/admin_backend/auth/permissions.py. require() factory 
  gets keyword-only anchor_dep parameter; two inner-function shapes 
  picked by anchor_dep presence (FastAPI requires static signatures). 
  Gate marker attached before returning.
- MODIFIED: 14 router endpoints across 6 router files retrofitted 
  with Depends(require(...)) per Phase 1 gate-assignment table. 
  5 endpoints exempt via gate_allowlist.
- MODIFIED: routers/v1/platform_users.py. _require_platform_auth 
  retired; 2 call sites replaced with require(ADMIN, USERS, VIEW, 
  GLOBAL). PlatformAccessRequiredError kept as dead-code with 
  inline comment marking for potential later removal (FN-AB-NN).
- MODIFIED: 4 router-module docstring references to 
  _require_platform_auth cleaned up.
- MODIFIED: tests/integration/test_platform_users_router.py. 2 
  test assertions updated from code=PLATFORM_ACCESS_REQUIRED to 
  code=PERMISSION_DENIED (test behavior unchanged; only error 
  code).
- MODIFIED: CLAUDE.md. Current state entry for 6.9.3.2. "Note on 
  gate allowlist coupling" maintenance convention. FN-AB-26 marked 
  RESOLVED. New FN-AB entries: dashboard+module-access dedicated 
  tuples review, reference-data+roles gating review, 
  /role-assignments dedicated tuple, PLATFORM_ADMIN/SUPPORT_ADMIN 
  ADMIN.USERS.VIEW.GLOBAL coverage, PlatformAccessRequiredError 
  removal.
- MODIFIED: BUILD_PLAN.md. Section 6.9 status flipped to COMPLETE.
- prompts/step-6_9_3_2-endpoint-retrofit-2026-05-13.md bundled.
- reports/step-6_9_3_2-design-investigation-2026-05-13.md bundled.

- pytest 308 → 314 passes + 3 xfail (9 new tests: 1 discipline + 
  8 retrofit; 3 OWNER tests marked xfail-seed-update). Actual 
  counts verified in the report. mypy strict clean.
- 14 retrofitted endpoints; 5 exempt. Mandatory-gate-discipline 
  meta-test passes — every APIRoute is gated or allowlisted.
- No DDL changes. No Alembic migration.

Catalogue / role-grant updates applied post-commit per data-seeding 
posture P-1/P-2/P-3:
  - permissions: +1 row (ADMIN.TENANTS.VIEW.TENANT)
  - role_permissions: +2 rows (OWNER → ADMIN.TENANTS.VIEW.TENANT, 
    OWNER → ADMIN.ORG_NODES.VIEW.TENANT)

Tests temporarily skipped (with reason) for OWNER on 3 endpoints 
awaiting seed grant updates:
  - /tenants/{id}
  - /tenants/{id}/org-tree
  - /tenants/{id}/org-nodes/{nid}/children

Operator un-skips affected tests once seed updates are applied.

Section 6.9 COMPLETE. Unblocks Stage 2 write endpoints (Steps 6.10+).
EOF
)" && git status
```

Run? yes / no / edit message — awaiting authorisation.

---

## Coordination

- **Unblocks Stage 2 writes.** Steps 6.10+ (write endpoints) can now
  declare `Depends(require(M, R, A, S, anchor_dep=...))` with the
  same pattern.
- **Operator post-commit task:** apply 3 seed data updates (Excel
  + local DB + cloud SQL). Un-skip the affected tests.
- **No deploy required.** All retrofit changes are code-side; cloud
  smoke tests should pass with new SUPER_ADMIN JWT (cascade passes).
- **Architecture.md RBAC section is a separate task** post-commit;
  draft from rbac-design-digest polished against the shipped reality.

---

## End of prompt
