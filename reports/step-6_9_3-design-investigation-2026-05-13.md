# Step 6.9.3 — design-prep investigation findings

Date: 2026-05-13
HEAD: `e0946b8` ("Step 6.9.2: gate factory + PermissionDeniedError + /me/* endpoints")
Scope: read-only investigation feeding the Step 6.9.3 design conversation. No edits, no code changes. Test runs limited to F-VERIFY-5 (full pytest count) and the catalogue/SUPER_ADMIN seed queries.

Findings grouped by area: VERIFY → INVENTORY → ANCHOR → GATE_REPLACE → DISCIPLINE → CATALOGUE. "Open questions for design conversation" consolidates at the bottom.

---

## VERIFY — 6.9.2 shipped shape

### F-VERIFY-1: `require()` factory exists with locked signature; `target_anchor=None` hardcoded inside the gate

**Question:** Did Step 6.9.2 ship `require(...)` exactly as the design conversation locked?

**Citation:** `src/admin_backend/auth/permissions.py:165-216`

**Current code (load-bearing):**

```python
def require(
    module: ModuleCode,
    resource: PermissionResource,
    action: PermissionAction,
    scope: PermissionScope,
) -> Callable[..., Awaitable[None]]:
    ...
    async def gate(
        auth: AuthContext = Depends(get_auth_context),
        session: AsyncSession = Depends(get_tenant_session_dep),
    ) -> None:
        allowed, reason_code, detail = await has_permission(
            session,
            auth,
            module,
            resource,
            action,
            scope,
            target_anchor=None,
        )
        if not allowed:
            raise PermissionDeniedError(
                detail,
                module=module.value,
                resource=resource.value,
                action=action.value,
                scope=scope.value,
                target_anchor=None,
                reason_code=reason_code.value,
            )

    return gate
```

**Observation:** Signature matches. `target_anchor=None` hardcoded inside the gate body (line 209). 6.9.3's retrofit must thread `target_anchor` through some new mechanism — most natural shape: factory accepts an optional anchor-dep callable, composes both deps inside `gate`. The `gate` inner function has no marker attribute today; the discipline test (see F-DISCIPLINE-2) will need one, so 6.9.3 adds it.

**Confidence:** high.

**Open question:** What's the threading shape? Three candidates: (a) factory accepts `anchor_dep: Callable | None = None`; (b) factory accepts the anchor function directly and inner gate adds a `target_anchor: str | None = Depends(anchor_dep)` parameter; (c) factory returns a configurable callable that the endpoint can further wire. F-ANCHOR open question consolidates this.

---

### F-VERIFY-2: `PermissionDeniedError` is ClientError, 403, code=PERMISSION_DENIED

**Question:** Does the exception class match the design lock?

**Citation:** `src/admin_backend/errors.py:143-159`

**Current code:**

```python
class PermissionDeniedError(ClientError):
    """Raised by the ``require(...)`` gate when ``has_permission()`` denies.
    ...
    """

    public_message = "Permission denied"
    http_status = 403
    code = "PERMISSION_DENIED"
```

**Observation:** Exact match. Lives in shared `errors.py` (mirrors `InvalidSortKeyClientError`'s promotion). Carries structured fields via the inherited `**context` mechanism on `AdminBackendError`. The response envelope's `details` stays `null` per Q7; structured fields surface only via `exc.context` in error logs.

**Confidence:** high.

---

### F-VERIFY-3: `me_router` mounts at `/me` with both routes

**Question:** Did `/me/permissions` and `/me/can-do` ship at the locked path?

**Citation:** `src/admin_backend/routers/v1/me.py:44, :47, :76`

**Current code (load-bearing):**

```python
router = APIRouter(prefix="/me", tags=["me"])

@router.get("/permissions", response_model=MePermissionsResponse, ...)
async def get_me_permissions(...): ...

@router.get("/can-do", response_model=MeCanDoResponse, ...)
async def get_me_can_do(...): ...
```

`main.py` includes the router under `settings.api_prefix` (`/api/v1`), so the live paths are `/api/v1/me/permissions` and `/api/v1/me/can-do`.

**Observation:** Match. These endpoints describe the caller's own state — they MUST stay un-gated (no `Depends(require(...))`) and MUST appear in the discipline test's allowlist alongside the public-path set.

**Confidence:** high.

---

### F-VERIFY-4: `get_permissions_for_user` lives in `auth/permissions.py`

**Question:** Is the broader query co-located with `has_permission` and `require()` as designed?

**Citation:** `src/admin_backend/auth/permissions.py:239-268` (PLATFORM-path internal); `:271-307` (TENANT-path internal); the public `get_permissions_for_user(session, auth) -> list[PermissionGrant]` at lines 239-268's wrapper.

**Observation:** Yes. Three permission-decision callables (`has_permission`, `get_permissions_for_user`, `require`) share the file. Module docstring rewritten at Step 6.9.2 lists all three. Separate-methods strategy (PLATFORM internal + TENANT internal per function) chosen over shared SQL helper.

**Confidence:** high.

---

### F-VERIFY-5: pytest total at HEAD is 294

**Question:** Does the post-6.9.2 baseline still hold?

**Verification command:** `uv run pytest --tb=no -q`

**Result:** `294 passed, 1 warning in 62.68s`

**Observation:** Matches the post-6.9.2 figure in CLAUDE.md (276 + 18). 6.9.3's regression checkpoint will be against 294. The single warning is the pre-existing python-json-logger deprecation, unrelated.

**Confidence:** high.

---

## INVENTORY — Every endpoint at HEAD

### F-INVENTORY-MASTER: All 23 API routes (21 retrofit-eligible + 2 public + 2 `/me/*` exempt)

**Question:** What's the master retrofit checklist?

**Citation:** All 9 router files at `src/admin_backend/routers/v1/` + `main.py:138-189` for health/ready. Verified via `app.routes` introspection at HEAD: 23 `APIRoute` objects total.

**Master table (all paths under `/api/v1` per `settings.api_prefix`):**

| # | Method+Path | Handler | Current auth | Likely retrofit tuple | `target_anchor` source | Currently `_require_platform_auth`? |
|---|---|---|---|---|---|---|
| 1 | GET /health | `main.health` | none (public) | — exempt — | n/a | no |
| 2 | GET /ready | `main.ready` | none (public) | — exempt — | n/a | no |
| 3 | GET /tenants | `tenants.list_tenants` | RLS-only multi-user-type | `ADMIN.TENANTS.VIEW.GLOBAL` (PLATFORM caller) / scope-based audience dispatch needed for TENANT (see Open) | None (list) | no |
| 4 | GET /tenants/stats | `tenants.tenants_stats` | RLS-only multi-user-type | same as /tenants list (uncertain) | None | no |
| 5 | GET /tenants/{tenant_id} | `tenants.get_tenant` | RLS-only multi-user-type | `ADMIN.TENANTS.VIEW.GLOBAL` (PLATFORM) / TENANT needs scope-relevant (see Open) | tenant-root org_node path for `tenant_id` | no |
| 6 | GET /tenant-users | `tenant_users.list_tenant_users` | RLS-only multi-user-type | `ADMIN.USERS.VIEW.TENANT` (matches both audiences via SUPER_ADMIN/OWNER) | None (list) | no |
| 7 | GET /tenant-users/{user_id} | `tenant_users.get_tenant_user` | RLS-only multi-user-type | `ADMIN.USERS.VIEW.TENANT` | tenant-root path of user's tenant (TenantUser has NO home_org_node — see F-ANCHOR-2) | no |
| 8 | GET /platform-users | `platform_users.list_platform_users` | **`_require_platform_auth`** | `ADMIN.USERS.VIEW.GLOBAL` (in seed; SUPER_ADMIN holds it) | None | **YES** |
| 9 | GET /platform-users/{user_id} | `platform_users.get_platform_user` | **`_require_platform_auth`** | `ADMIN.USERS.VIEW.GLOBAL` | None (PLATFORM grants apply globally) | **YES** |
| 10 | GET /tenants/{tenant_id}/org-tree | `org_tree.get_org_tree` | RLS-only multi-user-type | `ADMIN.ORG_NODES.VIEW.TENANT` | tenant-root path | no |
| 11 | GET /tenants/{tenant_id}/org-nodes/{node_id}/children | `org_tree.get_org_node_children` | RLS-only multi-user-type | `ADMIN.ORG_NODES.VIEW.TENANT` | path of `node_id` (composite-key lookup against `org_nodes`) | no |
| 12 | GET /lookups | `lookups.list_lookups` | RLS-only multi-user-type | none (reference data; design call) | None | no |
| 13 | GET /roles | `rbac.list_roles` | `_audience_filter_for` (Repo-layer filter, not router gate) | `ADMIN.ROLES.VIEW.TENANT` (only TENANT scope in seed; PLATFORM caller has it via SUPER_ADMIN); GLOBAL-scope ROLES.VIEW tuple does NOT exist (see CATALOGUE Open) | None | no |
| 14 | GET /roles/{role_id}/permissions | `rbac.get_role_permissions` | `_audience_filter_for` | `ADMIN.ROLES.VIEW.TENANT` | None | no |
| 15 | GET /permissions | `rbac.list_permissions` | none (catalogue is reference for both audiences) | `ADMIN.ROLES.VIEW.TENANT` (Roles & Permissions page is admin surface) | None | no |
| 16 | GET /permission-matrix | `rbac.get_permission_matrix` | `_audience_filter_for` | `ADMIN.ROLES.VIEW.TENANT` | None | no |
| 17 | GET /dashboard/fleet-stats | `dashboard.get_fleet_stats` | RLS-only multi-user-type | `ADMIN.TENANTS.VIEW.GLOBAL`? (PLATFORM) — dashboard is admin surface; TENANT user sees collapsed values (see CATALOGUE Open) | None | no |
| 18 | GET /dashboard/governance-stats | `dashboard.get_governance_stats` | RLS-only multi-user-type | same | None | no |
| 19 | GET /module-access/modules | `modules_access.list_modules` | RLS-only multi-user-type | catalogue gap — no `ADMIN.MODULES.*` permission seeded (see CATALOGUE) | None | no |
| 20 | GET /module-access/matrix | `modules_access.get_matrix` | RLS-only multi-user-type | same catalogue gap | None | no |
| 21 | GET /role-assignments | `role_assignments.list_role_assignments` | App-layer audience routing (locked decision 12 of Step 6.8.3) | `ADMIN.USERS.VIEW.TENANT`? (or new permission ADMIN.ROLE_ASSIGNMENTS.VIEW.*) | None | no |
| 22 | GET /me/permissions | `me.get_me_permissions` | none (auth required; **NO gate** — caller-state per design lock 6.9.2) | — exempt — | n/a | no |
| 23 | GET /me/can-do | `me.get_me_can_do` | none (same) | — exempt — | n/a | no |

**Retrofit-eligible count:** 17 (rows 3-21).
**Exempt:** 4 (rows 1, 2, 22, 23).
**Currently `_require_platform_auth`:** 2 (rows 8, 9).

**Quirk notes per endpoint:**

- Rows 3-5 (`/tenants`, `/tenants/stats`, `/tenants/{id}`): multi-user-type with RLS. Gating with PLATFORM-scope `ADMIN.TENANTS.VIEW.GLOBAL` would deny TENANT users entirely — regression. The "tenants" resource currently HAS no TENANT-scope VIEW permission in seed; TENANT users see their own tenant only via RLS today.
- Rows 13-16 (`/roles`, `/roles/{id}/permissions`, `/permissions`, `/permission-matrix`): currently use `_audience_filter_for(auth)` to narrow Repo results by `role.audience`. The retrofit gate is orthogonal — it answers "may you call this endpoint at all?". The audience filter stays in the Repo.
- Row 21 (`/role-assignments`): app-layer routing (locked decision 12 of Step 6.8.3) is a separate concern from gating. TENANT JWTs must short-circuit the platform-side query regardless of any gate.
- Rows 17-18 (dashboard) and 19-20 (module-access): aggregate views. RLS does the projection today; gating means picking a permission that both audiences hold (or audience-dispatching gates per endpoint — see Open).

**Confidence:** high (on the route list, current auth, and `_require_platform_auth` usage); medium (on the "likely retrofit tuple" column — many entries are best-guesses pending design conversation).

**Open question:** The single biggest design question is **how do multi-user-type endpoints retrofit?** Three patterns to choose between:

- (a) **Pick the more-permissive scope.** E.g., `/tenants` gated by `ADMIN.TENANTS.VIEW.GLOBAL`; TENANT user has no VIEW.GLOBAL but RLS already scoped them to their own row. This gate would deny all TENANT users — regression.
- (b) **Add new TENANT-scope tuples to catalogue.** E.g., introduce `ADMIN.TENANTS.VIEW.TENANT`; OWNER gets it; TENANT users pass. Requires catalogue migration.
- (c) **Audience-dispatch gate.** New factory variant `require_audience_dispatch(platform_tuple, tenant_tuple)` that picks the tuple based on `auth.user_type`. Routes declare both.
- (d) **Exempt multi-user-type endpoints from the gate.** RLS already does the visibility work; the discipline test allowlist grows.

This is the load-bearing design question for 6.9.3.

---

## ANCHOR — Per-resource `org_node.path` lookup mechanics

### F-ANCHOR-1: Stores anchor depends on Step 4.5 (not yet shipped)

**Question:** How does an anchor dependency reach `org_node.path` for `/stores/{store_id}` endpoints?

**Citation:** No full `Store` ORM model exists; `models/_lightweight_stubs.py` declares `Store` with only `id` and `tenant_id`. No `routers/v1/stores.py` exists.

**Observation:** Not applicable to Step 6.9.3 because no `/stores/*` endpoints exist at HEAD. Step 4.5 (still TODO per CLAUDE.md) ships the full Store model + router; an anchor dep for stores would land then. Retrofitting non-existent endpoints is out of scope for 6.9.3.

**Confidence:** high.

**Open question:** None (deferred until Step 4.5).

---

### F-ANCHOR-2: TenantUser has NO `home_org_node_id` FK; anchor defaults to tenant-root

**Question:** What's the anchor for `/tenant-users/{user_id}`?

**Citation:** `src/admin_backend/models/tenant_user.py` — no `home_org_node_id` column.

**Current code:** The TenantUser model maps the 17 columns of `tenant_users_v1.sql`. The columns are: `id, tenant_id, auth0_sub, email, full_name, status, invited_at, invitation_accepted_at, suspended_at, suspended_reason, suspended_by_user_id, suspended_by_user_type, created_at, created_by_user_id, created_by_user_type, updated_at, updated_by_user_id, updated_by_user_type`. No org_node reference of any kind.

**Observation:** The `target_anchor` for any per-tenant-user action defaults to the **tenant root** (the `org_node` with `node_type='TENANT'` and `parent_id IS NULL` for the user's tenant). Cascade semantics: a user whose role assignment is anchored at the tenant root has permissions covering every descendant org_node in their tenant; for actions on a specific TenantUser, the tenant root is the closest valid anchor.

The lookup chain becomes: `tenant_user_id → tenant_users.tenant_id → org_nodes WHERE tenant_id=... AND node_type='TENANT' AND parent_id IS NULL → path`. Two queries OR one JOIN. No existing Repo method does this; 6.9.3 adds one.

**Confidence:** high.

**Open question:** Should TenantUser get a `home_org_node_id` FK in a future schema change (so per-user anchor is just `tenant_users.home_org_node.path` without the tenant-root indirection)? Out of scope for 6.9.3; surface for Stage 2 design.

---

### F-ANCHOR-3: Tenants anchor = tenant-root org_node

**Question:** What's the anchor for `/tenants/{tenant_id}` and `/tenants/{tenant_id}/org-tree`?

**Citation:** `src/admin_backend/models/org_node.py:63-72` (OrgNodeType.TENANT); `:104` (parent_id nullable).

**Current code (load-bearing):**

```python
class OrgNodeType(str, Enum):
    """Node type. Mirrors ``org_node_type_enum`` in the DDL."""
    TENANT = "TENANT"
    REGION = "REGION"
    ...
```

**Observation:** Tenant root is `node_type='TENANT' AND parent_id IS NULL AND tenant_id=:tenant_id`. Looking it up requires a small new method; no existing OrgNodesRepo method returns the tenant-root row directly. The path string at this row is the cascade root — every other org_node in the tenant has it as an ancestor.

**Confidence:** high.

**Open question:** Same as F-ANCHOR-2 → introduce a `OrgNodesRepo.get_tenant_root_path(tenant_id)` method in 6.9.3, or inline the SQL in each anchor dep. Codification pattern is design choice.

---

### F-ANCHOR-4: Org-nodes anchor = direct `org_nodes.path` lookup by `(tenant_id, node_id)`

**Question:** What's the anchor for `/tenants/{tenant_id}/org-nodes/{node_id}/children`?

**Citation:** `src/admin_backend/repositories/org_nodes.py:73-228` (OrgNodesRepo). The current methods are `count_active_by_tenant`, `list_active_with_child_counts`, `list_children_paginated`, `node_exists`. None return a single node's path directly.

**Observation:** Anchor is `node_id`'s own `path`. The composite-key constraint per D-34 means the lookup is `WHERE id=:node_id AND tenant_id=:tenant_id` so a TENANT-A caller can't probe TENANT-B's node_id and resolve a real path. If the row doesn't match the composite key, anchor resolution must surface as 404 not 403 (per D-17). The anchor dep is thus a small new query — likely a new `OrgNodesRepo.get_path_by_id(tenant_id, node_id) -> str | None` method.

**Confidence:** high.

---

### F-ANCHOR-5: Role assignments — no `assignment_id` path param exists; anchor is N/A for list-only

**Question:** What's the anchor for `/role-assignments`?

**Citation:** `src/admin_backend/routers/v1/role_assignments.py:238` — single route `GET /role-assignments` (no detail endpoint with assignment_id).

**Observation:** The current endpoint is list-only. No `target_anchor` to thread. The role-assignments resource at HEAD has no path-parameter-bearing endpoint. If a future `/role-assignments/{id}` lands, the anchor would be the tenant-side assignment's `org_node_id`'s path (composite-key lookup); platform-side has no anchor. Not relevant to 6.9.3.

**Confidence:** high.

---

### F-ANCHOR-6: Platform users — `target_anchor=None` always (PLATFORM scope)

**Question:** What's the anchor for `/platform-users/{user_id}` and `/platform-users`?

**Citation:** `src/admin_backend/auth/permissions.py:67-84` (`has_permission` PLATFORM branch ignores `target_anchor`).

**Observation:** PLATFORM-scope permissions apply globally; `target_anchor` is accepted but ignored on the PLATFORM path. The anchor dep for `/platform-users/*` simply returns `None`. The require() factory's current `target_anchor=None` hardcoding is exactly right for this endpoint pair.

**Confidence:** high.

---

### F-ANCHOR-7: Catalogue endpoints — `target_anchor=None`

**Question:** What's the anchor for `/lookups`, `/roles`, `/permissions`, `/permission-matrix`?

**Observation:** All are reference / catalogue endpoints. No path parameter ties to an org_node. `target_anchor=None`. The require() gate (if any) is GLOBAL-scoped or TENANT-scoped against the catalogue tuple, with no cascade consideration.

**Confidence:** high.

---

### F-ANCHOR-8: No existing Repo method returns just `org_node.path` for an arbitrary node_id

**Question:** Can any existing Repo method be reused for anchor dependencies?

**Citation:** `repositories/org_nodes.py` (no single-row path lookup); `repositories/tenants.py:226, :343` (list/get with aggregates — doesn't return tenant-root path); `repositories/tenant_users.py:167` (`get_by_id` returns TenantUser row only).

**Observation:** No reuse. Every anchor dep needs at minimum a new lightweight method. Two design shapes:

- (a) **One small method per Repo**: `OrgNodesRepo.get_tenant_root_path(tenant_id)`, `OrgNodesRepo.get_path_by_id(tenant_id, node_id)`, `TenantUsersRepo.get_tenant_root_path_for_user(user_id)` etc. Spread across Repos.
- (b) **One anchor-resolution helper module**: `src/admin_backend/auth/anchor_deps.py` (or `routers/v1/_anchor_deps.py`) containing small `async def get_<resource>_anchor(...)` functions that issue their own SQL inline. Centralised; not necessarily a Repo concern.

**Confidence:** high.

**Open question:** Repo-vs-helper-module location for anchor lookups. Repo distributes the logic by resource (matches existing house style); helper module centralises and is easier to enumerate / test as a unit. Design conversation territory.

---

## GATE_REPLACE — `_require_platform_auth` retirement (FN-AB-26)

### F-GATE-REPLACE-1: Exactly 2 `_require_platform_auth(auth)` call sites at HEAD

**Question:** How many call sites does `_require_platform_auth` have?

**Citation:** Repo-wide grep `grep -rn "_require_platform_auth" src/admin_backend/ tests/ --include="*.py"`:

- **Definition:** `src/admin_backend/routers/v1/platform_users.py:102-109`
- **Call sites:** `platform_users.py:216` (`list_platform_users` handler), `platform_users.py:258` (`get_platform_user` handler)
- **Docstring references (not call sites):** `org_tree.py:20`, `tenant_users.py:11`, `platform_users.py:12` — these are module-level prose pointing AT the helper; not invocations.
- **Test reference:** `tests/integration/test_platform_users_router.py:347` — a docstring mentioning the helper name in the A2 test's prose.

**Observation:** Two call sites at HEAD (unchanged from Step 5.1's introduction). FN-AB-26's retirement scope is two call-site edits plus an optional definition removal.

**Confidence:** high.

---

### F-GATE-REPLACE-2: Replacement permission tuple is `ADMIN.USERS.VIEW.GLOBAL`

**Question:** What permission tuple should replace `_require_platform_auth` on the two `/platform-users` endpoints?

**Citation:** Seed permission catalogue (F-CATALOGUE-1 below) confirms `ADMIN.USERS.VIEW.GLOBAL` exists; SUPER_ADMIN holds it (per F-CATALOGUE-4).

**Observation:** `ADMIN.USERS.VIEW.GLOBAL` is the appropriate replacement. The semantics match: only PLATFORM-audience roles (`SUPER_ADMIN`) hold GLOBAL-scope permissions; TENANT-audience roles cannot via the audience-check triggers. Replacing `_require_platform_auth(auth)` with `Depends(require(ADMIN, USERS, VIEW, GLOBAL))` produces the same `403` envelope shape (different `code`: `PERMISSION_DENIED` vs `PLATFORM_ACCESS_REQUIRED`).

**Code-level change:** the response body's `code` field changes (`PERMISSION_DENIED` after retrofit; `PLATFORM_ACCESS_REQUIRED` today). Frontend consumers reading `code` must accept both during the transition or be updated. Tests asserting `code=PLATFORM_ACCESS_REQUIRED` (test A2 in `test_platform_users_router.py:330+`) need updating.

**Confidence:** high.

---

### F-GATE-REPLACE-3: SUPER_ADMIN holds `ADMIN.USERS.VIEW.GLOBAL` (no catalogue gap)

**Question:** Does the most-privileged platform user role have the replacement permission?

**Citation:** Query against seed `role_permissions` (F-CATALOGUE-4 result):

```
SUPER_ADMIN grants include: ADMIN.USERS.VIEW.GLOBAL  ✓
```

**Observation:** No catalogue gap. SUPER_ADMIN's grant inventory (30 rows) covers `ADMIN.USERS.VIEW.GLOBAL`. The retrofit doesn't lock SUPER_ADMIN out of `/platform-users/*`.

**Confidence:** high.

---

### F-GATE-REPLACE-4: `PlatformAccessRequiredError` has only the 2 call-site raises; retire alongside

**Question:** If `_require_platform_auth` retires, what happens to `PlatformAccessRequiredError`?

**Citation:** Class definition at `routers/v1/platform_users.py:78-87`. Raise sites at `platform_users.py:105` (inside `_require_platform_auth`). Repo-wide grep for `PlatformAccessRequiredError` returns the definition + the raise + test references.

**Observation:** The class has exactly one raise site (`_require_platform_auth` itself). If 6.9.3 retires `_require_platform_auth`, `PlatformAccessRequiredError` becomes dead code. Two options:

- (a) **Retire together.** Delete the class. Tests asserting `code=PLATFORM_ACCESS_REQUIRED` are updated to `code=PERMISSION_DENIED`. Cleaner.
- (b) **Keep the class.** Other endpoints not retrofitted in 6.9.3 might still need a binary user-type gate without a specific permission tuple; class becomes a forward-defensive artefact. Less clean; no current consumer.

Test A2 in `test_platform_users_router.py:340` ("a2_tenant_jwt_returns_403_platform_access_required") asserts `status_code == 403` only — it doesn't assert `code` explicitly. Updating it just means renaming the test and possibly checking `code` to be sharper.

**Confidence:** high.

**Open question:** Retire class together (clean) or keep for forward compatibility (defensive)? Design conversation territory.

---

### F-GATE-REPLACE-5: No callers of `_require_platform_auth` exist outside the two known sites

**Question:** Are there hidden callers, doc references, or tests that construct `_require_platform_auth` for unusual reasons?

**Citation:** Full grep:

```
src/admin_backend/routers/v1/platform_users.py:102: def _require_platform_auth(auth: AuthContext) -> None:
src/admin_backend/routers/v1/platform_users.py:216:     _require_platform_auth(auth)
src/admin_backend/routers/v1/platform_users.py:258:     _require_platform_auth(auth)
# docstring references (no invocation):
src/admin_backend/routers/v1/org_tree.py:20:        ``_require_platform_auth`` gate. RLS scopes visibility:
src/admin_backend/routers/v1/tenant_users.py:11:        ``_require_platform_auth`` gate. Visibility scoping is the DB layer's
src/admin_backend/routers/v1/platform_users.py:12:        handler layer via ``_require_platform_auth(auth)``. ``platform_users``
tests/integration/test_platform_users_router.py:347:    that drops the ``_require_platform_auth`` call would expose Ithina
```

**Observation:** All non-call-site references are documentation prose that becomes stale on retirement. 6.9.3 must update the three router-file docstrings and the test docstring as part of the retirement work; otherwise these references rot in place.

**Confidence:** high.

---

## DISCIPLINE — Mandatory-gate test mechanics

### F-DISCIPLINE-1: `app.routes` → `APIRoute.dependant.dependencies` → `Dependant.call` is the path

**Question:** How does FastAPI expose a route's dependency chain for inspection?

**Citation:** Verified live at HEAD with `app = create_app()` followed by `for r in app.routes:` introspection:

```
APIRoute count: 23
Sample (GET /api/v1/me/permissions):
  endpoint: admin_backend.routers.v1.me.get_me_permissions
  dependant.dependencies n=2
    - admin_backend.dependencies.get_auth_context
    - admin_backend.dependencies.get_tenant_session_dep
```

**Observation:** `app.routes` returns a mix of `Route` (FastAPI's docs/openapi/redoc routes) and `APIRoute` (user-defined). The discipline test filters `APIRoute` instances and walks `route.dependant.dependencies`. Each `Dependant.call` is the callable passed to `Depends(...)`. For nested deps (e.g., `get_tenant_session_dep` itself depends on `get_auth_context`), the dependency tree is flattened-or-nested per FastAPI's internal representation — verify the depth at design time.

**Confidence:** high.

---

### F-DISCIPLINE-2: No marker attribute on `gate` inner function at HEAD; 6.9.3 must add one

**Question:** How does the discipline test identify "this dependency is the require() gate"?

**Citation:** `src/admin_backend/auth/permissions.py:186-216` — the `gate` inner function has NO sentinel attribute set. The function's `__qualname__` resolves to `require.<locals>.gate`.

**Observation:** Two mechanisms:

- (a) **Marker attribute.** 6.9.3 adds `gate.__permission_gate__ = (module, resource, action, scope)` (or a typed sentinel) just before `return gate`. The discipline test then iterates `route.dependant.dependencies` and asserts `any(hasattr(d.call, "__permission_gate__") for d in deps)`. Robust against renames.
- (b) **Qualname matching.** Test asserts `d.call.__qualname__ == "require.<locals>.gate"`. Fragile (renaming `gate` or wrapping it via `functools.wraps` would break it).

(a) is the standard idiom for FastAPI dependency factories per the project's "Note on dependency factories" Step 6.9.2 convention. Marker attribute also enables richer assertions later (e.g., "this route is gated on `ADMIN.USERS.VIEW.GLOBAL`", caught by the discipline test reading `__permission_gate__`).

**Confidence:** high.

**Open question:** Marker attribute shape — tuple of enum values vs typed dataclass vs simple sentinel. Design conversation territory.

---

### F-DISCIPLINE-3: PUBLIC_ROUTES allowlist content for v0

**Question:** What paths should remain un-gated?

**Citation:** Existing PUBLIC_PATHS at `middleware/auth.py:38-45`:

```python
PUBLIC_PATHS = frozenset({
    "/api/v1/health",
    "/api/v1/ready",
    "/api/v1/openapi.json",
    "/api/v1/docs",
    "/api/v1/redoc",
    "/metrics",
})
```

**Observation:** The DISCIPLINE test's allowlist must include:

- Everything in `PUBLIC_PATHS` (no auth, no gate).
- `/api/v1/me/permissions` and `/api/v1/me/can-do` (auth required, no gate — caller-state per design lock 6.9.2).

`PUBLIC_PATHS` (auth-skip) and the gate allowlist (gate-skip) are conceptually different but mostly overlap. The `/me/*` routes are auth-required but gate-exempt — the discipline test's allowlist is therefore PUBLIC_PATHS + {`/api/v1/me/permissions`, `/api/v1/me/can-do`}.

`/metrics` is in PUBLIC_PATHS but is not currently mounted on the FastAPI app — verified via `app.routes` introspection (no `/metrics` route present). The PUBLIC_PATHS entry is forward-looking for a future Prometheus integration; the discipline test's allowlist doesn't need to enumerate routes that aren't actually mounted.

**Confidence:** high.

---

### F-DISCIPLINE-4: PUBLIC_ROUTES allowlist lives in a new constants module (suggested) or alongside the test

**Question:** Where does the gate allowlist live?

**Citation:** No existing v0 location. `PUBLIC_PATHS` lives in `middleware/auth.py` as a module-level frozenset.

**Observation:** Three plausible homes:

- (a) **`src/admin_backend/auth/gate_allowlist.py`** — a new dedicated module with `GATE_EXEMPT_PATHS: frozenset[str]`. Importable by both runtime code (if needed) and the discipline test. Mirrors `auth/permissions.py` neighbour.
- (b) **Inline in the discipline test.** Test reads paths from a local constant. Tightest coupling between allowlist and discipline test; harder to share with runtime code if future use cases need it.
- (c) **Same `PUBLIC_PATHS` frozenset in `middleware/auth.py`, extended.** Conflates auth-skip with gate-skip. `/me/*` would have to be added there even though they DO require auth. Misleads readers about middleware semantics.

(a) reads cleanest; (b) is fine for v0 if no runtime code needs the allowlist.

**Confidence:** high.

**Open question:** Allowlist location — `(a)` new module vs `(b)` inline-in-test. Design conversation territory.

---

### F-DISCIPLINE-5: AuthMiddleware's `PUBLIC_PATHS` is NOT the same set as the gate allowlist

**Question:** Can we reuse `PUBLIC_PATHS` directly?

**Citation:** `middleware/auth.py:38-45` (above).

**Observation:** Cannot reuse directly. `PUBLIC_PATHS` excludes `/me/*` because `/me/*` requires auth. The gate allowlist must include `/me/*` because `/me/*` is gate-exempt (caller-state per design lock). So the gate allowlist is a strict superset of `PUBLIC_PATHS` by exactly 2 entries (`/api/v1/me/permissions` and `/api/v1/me/can-do`).

**Confidence:** high.

---

## CATALOGUE — Permission catalogue gap analysis

### F-CATALOGUE-1: 30-row catalogue at HEAD (full enumeration)

**Question:** What's the master catalogue?

**Citation:** Query against seeded `core.permissions` table (executed at investigation time):

| module | resource | action | scope |
|---|---|---|---|
| ADMIN | AUDIT_LOG | AUDIT | TENANT |
| ADMIN | AUDIT_LOG | VIEW | TENANT |
| ADMIN | ORG_NODES | CONFIGURE | TENANT |
| ADMIN | ORG_NODES | VIEW | TENANT |
| ADMIN | ROLES | CONFIGURE | GLOBAL |
| ADMIN | ROLES | CONFIGURE | TENANT |
| ADMIN | ROLES | VIEW | TENANT |
| ADMIN | STORES | CONFIGURE | TENANT |
| ADMIN | STORES | VIEW | TENANT |
| ADMIN | TENANTS | CONFIGURE | GLOBAL |
| ADMIN | TENANTS | OVERRIDE | GLOBAL |
| ADMIN | TENANTS | VIEW | GLOBAL |
| ADMIN | USERS | CONFIGURE | GLOBAL |
| ADMIN | USERS | CONFIGURE | TENANT |
| ADMIN | USERS | OVERRIDE | GLOBAL |
| ADMIN | USERS | VIEW | GLOBAL |
| ADMIN | USERS | VIEW | TENANT |
| PERISHABLES_ASSISTANT | DONATION_ROUTING | APPROVE | TENANT |
| PERISHABLES_ASSISTANT | DONATION_ROUTING | EXECUTE | STORE |
| PERISHABLES_ASSISTANT | EXPIRING_ITEMS | VIEW | STORE |
| PERISHABLES_ASSISTANT | WASTE_LOG | EXECUTE | STORE |
| PERISHABLES_ASSISTANT | WASTE_LOG | VIEW | STORE |
| PRICING_OS | MARKDOWNS | APPROVE | STORE |
| PRICING_OS | MARKDOWNS | OVERRIDE | STORE |
| PRICING_OS | MARKDOWNS | VIEW | STORE |
| PRICING_OS | PRICING_RULES | CONFIGURE | TENANT |
| PRICING_OS | PRICING_RULES | VIEW | TENANT |
| PROMOTIONS_ASSISTANT | CAMPAIGNS | APPROVE | TENANT |
| PROMOTIONS_ASSISTANT | CAMPAIGNS | CONFIGURE | TENANT |
| PROMOTIONS_ASSISTANT | CAMPAIGNS | VIEW | TENANT |

**Observation:** 30 catalogue rows. ADMIN domain has 17; the three product modules have 13. Notable absences relevant to retrofit:

- No `ADMIN.TENANTS.VIEW.TENANT` (only GLOBAL exists).
- No `ADMIN.ROLES.VIEW.GLOBAL` (only TENANT exists).
- No `ADMIN.MODULES.*` of any shape (module-access endpoints have no catalogue tuple).
- No `ADMIN.DASHBOARD.*` of any shape.
- No `ADMIN.AUDIT_LOG.*.GLOBAL` (only TENANT-scoped audit access exists; relevant to audit endpoints in Step 6.2 / 6.16).

**Confidence:** high.

---

### F-CATALOGUE-2: Retrofit gaps surfaced

**Question:** Which retrofit-eligible endpoints lack a clean catalogue tuple?

**Cross-reference with F-INVENTORY-MASTER:**

| Endpoint | Tuple candidate | Status |
|---|---|---|
| `/tenants`, `/tenants/stats`, `/tenants/{id}` | `ADMIN.TENANTS.VIEW.GLOBAL` (PLATFORM) — no TENANT-scope counterpart | **GAP** — TENANT users would lose multi-user-type access |
| `/tenant-users[/{id}]` | `ADMIN.USERS.VIEW.TENANT` | OK |
| `/platform-users[/{id}]` | `ADMIN.USERS.VIEW.GLOBAL` | OK |
| `/tenants/{id}/org-tree` + `org-nodes/{id}/children` | `ADMIN.ORG_NODES.VIEW.TENANT` | OK |
| `/lookups` | none (reference data) — exemption candidate | Design call |
| `/roles[/{id}/permissions]` + `/permissions` + `/permission-matrix` | `ADMIN.ROLES.VIEW.TENANT` works for both audiences (SUPER_ADMIN holds it) | OK with caveat |
| `/dashboard/fleet-stats` + `/governance-stats` | no obvious tuple; `ADMIN.TENANTS.VIEW.GLOBAL` is closest | **GAP** — same TENANT-access regression as `/tenants` |
| `/module-access/modules` + `/module-access/matrix` | no `ADMIN.MODULES.*` catalogue tuple | **GAP** — no permission to gate against |
| `/role-assignments` | no `ADMIN.ROLE_ASSIGNMENTS.*` tuple; could use `ADMIN.USERS.VIEW.TENANT` as proxy | Design call |

**Observation:** Three distinct gap classes:

1. **Multi-user-type access regression** (`/tenants/*`, `/dashboard/*`): gating with `ADMIN.TENANTS.VIEW.GLOBAL` denies TENANT users entirely. RLS today scopes them to their own row, which is the correct UX. Solutions: (a) add `ADMIN.TENANTS.VIEW.TENANT` to catalogue, grant to OWNER; (b) audience-dispatch gate; (c) exempt these endpoints from gating altogether.
2. **No catalogue tuple for the resource** (`/module-access/*`): no `ADMIN.MODULES.*` exists. Solutions: (a) add catalogue rows (`ADMIN.MODULES.VIEW.GLOBAL` for fleet view, `ADMIN.MODULES.VIEW.TENANT` for own-tenant view); (b) exempt; (c) reuse an unrelated existing tuple.
3. **Reference data** (`/lookups`, `/permissions`, `/permission-matrix`): debate over whether reference data needs a permission gate. `/permissions` is admin-management surface and could use `ADMIN.ROLES.VIEW.TENANT` (SUPER_ADMIN + OWNER both have it).

**Confidence:** high.

**Open question:** Path forward for each gap class — catalogue migration vs audience-dispatch gate vs exempt. Major design conversation territory.

---

### F-CATALOGUE-3: Catalogue addition mechanics

**Question:** If gaps are filled by catalogue additions, what migration shape applies?

**Citation:** Catalogue additions land via the seed Excel (`data/ithina_dev_seed_data.xlsx` — `permissions` sheet) per the established convention. Post Step 6.8.2.1, additions to seed land via:

- Excel edit: append rows under existing `permissions` sheet with `_key=pNN` schema.
- Optional Alembic migration to ALSO insert the rows for already-deployed environments (e.g., Cloud SQL) where re-running the seed loader isn't part of the deploy. Step 6.8.2.1 didn't ship a migration — it just edited Excel and relied on operator-run reseeds.

For cloud deploys, the seed loader re-runs post-deploy, so Excel-edit-only suffices. For prod, a separate strategy (operator-run UPSERT migration) would be needed.

**Observation:** 6.9.3's catalogue additions (if any) follow the same shape as Step 6.8.2.1: Excel edit + optional migration. Not 6.9.3-specific design.

**Confidence:** high.

---

### F-CATALOGUE-4: SUPER_ADMIN role holds all 30 catalogue permissions

**Question:** Does the most-privileged role have full coverage?

**Citation:** Query against `role_permissions` JOIN `permissions` filtered to `role.code='SUPER_ADMIN'` (executed at investigation time):

```
30 rows returned — every permission in the catalogue, no exceptions.
```

**Observation:** SUPER_ADMIN holds all 30 tuples (Step 6.8.2.1 added the 7 missing ADMIN-domain ones). No retrofit-gate will lock SUPER_ADMIN out of any endpoint **as long as the chosen tuple is in the catalogue**. The audience-check trigger means SUPER_ADMIN (a PLATFORM-audience role) only grants PLATFORM-side permissions on the PLATFORM path — but a PLATFORM caller (Anjali, Devon, Kira) sees every PLATFORM grant on her assignment.

If a new catalogue tuple is added (e.g., `ADMIN.MODULES.VIEW.GLOBAL`) without a matching grant to SUPER_ADMIN, the retrofit would lock SUPER_ADMIN out of the gated endpoint. The catalogue addition must include the SUPER_ADMIN grant — same pattern as Step 6.8.2.1.

**Confidence:** high.

---

## Open questions for design conversation

Consolidated from each finding plus naturally-surfaced observations.

### In-scope (Step 6.9.3 design):

1. **F-INVENTORY-MASTER** — Path forward for multi-user-type endpoints. Pick from: (a) more-permissive scope (regression risk), (b) catalogue additions for TENANT-scope counterparts, (c) audience-dispatch gate factory, (d) gate-exempt with the discipline test allowlist. Load-bearing for the entire retrofit. The decision shapes whether 6.9.3 ships a catalogue migration alongside the retrofit code.

2. **F-VERIFY-1 / F-ANCHOR-8** — `target_anchor` threading shape into the gate. Three candidates: (a) factory accepts `anchor_dep: Callable | None`; (b) endpoints declare both gate and anchor as parallel `Depends(...)`; (c) factory returns a configurable wrapper. Affects every retrofitted endpoint that needs cascade-aware checks.

3. **F-ANCHOR-2 / F-ANCHOR-3 / F-ANCHOR-4** — Per-resource anchor dependencies — Repo methods (distributed) vs centralised helper module (`auth/anchor_deps.py` or `routers/v1/_anchor_deps.py`)? Both are house-style-consistent in different ways.

4. **F-ANCHOR-2** — TenantUser has no `home_org_node_id` FK; tenant-root default is fine for 6.9.3 but a future schema change might add the FK for finer-grained per-user anchoring. Out of 6.9.3 scope; flag for Stage 2.

5. **F-DISCIPLINE-2** — Gate marker attribute shape. Tuple of enum values (simple) vs typed dataclass (richer assertion surface) vs sentinel (binary "is a gate" only). The first enables the discipline test to assert "this route is gated on X tuple"; the third is the minimum.

6. **F-DISCIPLINE-4** — Allowlist location: dedicated `auth/gate_allowlist.py` module vs inline-in-test. New module reads cleaner if any runtime path needs the allowlist; inline is fine for v0 if not.

7. **F-GATE-REPLACE-4** — Retire `PlatformAccessRequiredError` together with `_require_platform_auth`, or keep for forward defensiveness? Class has no callers after retirement.

8. **F-CATALOGUE-2** — Per gap class:
   - `/tenants/*` and `/dashboard/*` — multi-user-type regression risk.
   - `/module-access/*` — no `ADMIN.MODULES.*` catalogue tuple.
   - `/lookups` — exempt as pure reference data?
   - `/role-assignments` — proxy tuple (`ADMIN.USERS.VIEW.TENANT`) vs new `ADMIN.ROLE_ASSIGNMENTS.*` tuple?

9. **F-CATALOGUE-3** — If catalogue additions ship in 6.9.3, do they land as Excel-edit-only (matching Step 6.8.2.1) or as an Alembic migration too (for cloud parity)? Affects deploy steps.

### Out-of-scope (scope-creep flags for later prompts, NOT investigated here):

- **`tenant_users.home_org_node_id` schema addition.** Mentioned in F-ANCHOR-2 as a Stage 2 backlog item; not 6.9.3 territory.

- **`/role-assignments/{id}` detail endpoint.** Doesn't exist at HEAD per F-ANCHOR-5; if added later, the anchor dep is the tenant-side assignment's `org_node_id` path. Out of 6.9.3.

- **Audit log writes on gate denials.** Deferred to Step 6.16 per the Section 6.9 design lock. The `**context` on `PermissionDeniedError` already carries the structured fields Step 6.16 will consume.

- **FN-AB-27 (`/me/permissions` shape simplification).** Revisit only if frontend integration during 6.9.3 retrofit surfaces concrete friction; design conversation can defer or trigger based on operator's frontend coordination.

- **`/health`-style endpoints beyond `/health` and `/ready`.** The Prometheus `/metrics` entry in `PUBLIC_PATHS` is forward-looking; no actual `/metrics` route at HEAD.

- **Stage 3 Auth0 swap.** Per FN-AB-22, still flagged for Stage 3 kickoff; AuthContext shape is stable so 6.9.3's retrofit doesn't interact with Auth0 work.

- **Performance / caching.** Per FN-AB-24, `has_permission()` runs sub-millisecond on seeded data; revisit only when monitoring shows it as a measured hot path.
