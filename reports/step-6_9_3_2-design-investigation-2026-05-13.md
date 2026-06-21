# Step 6.9.3.2 — design-prep investigation findings

Date: 2026-05-13
HEAD: `ddea23c` ("Step 6.9.3.1: scope cascade in has_permission")
Scope: read-only investigation feeding the Step 6.9.3.2 design conversation. No edits, no commits. Test runs limited to F-VERIFY-2 (pytest count) and catalogue SQL queries.

This investigation re-verifies the prior 6.9.3 investigation report (`step-6_9_3-design-investigation-2026-05-13.md`, 32 findings) against the post-cascade codebase at HEAD `ddea23c`, plus adds new findings specific to threading and post-cascade design.

Findings grouped by area: VERIFY → INVENTORY → POST_CASCADE → ANCHOR → THREADING → GATE_REPLACE → DISCIPLINE → CATALOGUE. "Open questions for design conversation" consolidates at the bottom.

Major surfaces (read these first if short on time):

- **F-CATALOGUE-3 (grant-coverage gaps)** — ADMIN.{STORES,ORG_NODES}.{VIEW,CONFIGURE}.TENANT live in catalogue but are held by **SUPER_ADMIN only**. OWNER (the flagship tenant role) has neither. Gating /tenants/{id}/org-tree on ADMIN.ORG_NODES.VIEW.TENANT denies OWNER. Same problem for any STORES/ORG_NODES TENANT-scope gate. Catalogue presence is necessary but not sufficient — role-grant coverage is the load-bearing check.
- **F-GATE-REPLACE-3 (PLATFORM_ADMIN/SUPPORT_ADMIN coverage)** — ADMIN.USERS.VIEW.GLOBAL is held by **SUPER_ADMIN only**. PLATFORM_ADMIN and SUPPORT_ADMIN currently pass `_require_platform_auth` (user_type-only check) but would FAIL `require(ADMIN, USERS, VIEW, GLOBAL)`. The simplest `_require_platform_auth` retirement under cascade still requires grant additions or a different tuple.
- **F-INVENTORY-1 (count correction)** — Prior investigation reported "17 retrofit-eligible" endpoints; actual count is **19** (23 total APIRoutes − 2 public − 2 `/me/*` exempt = 19). Arithmetic error in prior report's F-INVENTORY-MASTER, not a state change.

---

## VERIFY — 6.9.3.1 shipped state at HEAD `ddea23c`

### F-VERIFY-1: 6.9.3.1 deliverables all present

**Question:** Did Step 6.9.3.1 ship exactly what the prompt locked?

**Citation:** `src/admin_backend/auth/permissions.py:102, :114, :150, :155, :246, :303`

**Current code (load-bearing line citations):**

```
:102  _SCOPE_CASCADE_ORDER: tuple[str, ...] = (
:114  def satisfying_scopes(requested: PermissionScope) -> list[str]:
:150  _PERMISSION_SCOPE_ENUM_VALUES: frozenset[str] = frozenset(
:155  def _satisfying_scopes_for_sql(requested: PermissionScope) -> list[str]:
:246  AND p.scope = ANY(CAST(:satisfying_scopes AS permission_scope_enum[]))  -- PLATFORM
:303  AND p.scope = ANY(CAST(:satisfying_scopes AS permission_scope_enum[]))  -- TENANT
```

**Observation:** Exact match. Helper, order tuple, frozenset, SQL-filter companion, and both SQL paths' `ANY()` clauses all present at expected lines. No deviation from the 6.9.3.1 commit's stated scope.

**Confidence:** high.

---

### F-VERIFY-2: pytest count is 308 at HEAD

**Question:** Does the post-6.9.3.1 baseline hold?

**Verification command:** `uv run pytest --tb=no -q`

**Result:** `308 passed, 1 warning in 60.83s`

**Observation:** Matches CLAUDE.md's Current State entry for 6.9.3.1 (294 + 14 = 308). The single warning is the pre-existing python-json-logger deprecation. 6.9.3.2's regression checkpoint is against 308.

**Confidence:** high.

---

### F-VERIFY-3: 6.9.2 deliverables intact at HEAD

**Question:** Did 6.9.3.1 inadvertently change 6.9.2's design surface?

**Citation:** Spot checks against `auth/permissions.py:365`-end (require factory still hardcodes `target_anchor=None` per 6.9.2 locked decision); `errors.py:143-159` (PermissionDeniedError unchanged); `routers/v1/me.py:44-...` (me_router unchanged); `schemas/me.py` (unchanged).

**Observation:** 6.9.2's `require()` factory, `PermissionDeniedError`, `me_router`, and the `/me/*` schemas are untouched by 6.9.3.1. The gate factory's inner function STILL hardcodes `target_anchor=None`; 6.9.3.2 must change that.

**Confidence:** high.

---

### F-VERIFY-4: FN-AB-28 added, FN-AB-26 updated in place

**Question:** Did 6.9.3.1 manage forward-notes as the commit's documentation discipline required?

**Citation:** `CLAUDE.md:975, :981, :985, :1272`

**Current state:**

- `FN-AB-26 — _require_platform_auth retirement decision` (line 975): updated in place with the post-cascade context paragraph. References Step 6.9.3.2 (not 6.9.3) per the split.
- `FN-AB-27 — /me/permissions response shape simplification` (line 981): retrofit-step ref renamed `6.9.3` → `6.9.3.2`.
- `FN-AB-28 — PermissionScope enum expansion (future)` (line 985): new entry.
- `Note on org-hierarchy coupling (Step 6.9.3.1 forward)` (line 1272): new maintenance convention with two in-repo sync points (DDL `org_node_type_enum` + `_SCOPE_CASCADE_ORDER` tuple).

**Observation:** All documentation discipline followed. FN-AB-25 was not modified at 6.9.3.1 because its `DECISION LOCKED` state (per 6.9.2) is unaffected by the cascade change.

**Confidence:** high.

---

## INVENTORY — re-confirmation of retrofit list

### F-INVENTORY-1: 23 APIRoutes at HEAD; 19 retrofit-eligible (prior investigation said 17 — arithmetic correction)

**Question:** Is the route inventory unchanged since the prior investigation?

**Citation:** Live route enumeration at HEAD via `from admin_backend.main import create_app; [r.path for r in create_app().routes if isinstance(r, APIRoute)]`.

**Current state (23 routes, sorted by path):**

```
GET /api/v1/dashboard/fleet-stats
GET /api/v1/dashboard/governance-stats
GET /api/v1/health                                           ← public, exempt
GET /api/v1/lookups
GET /api/v1/me/can-do                                        ← exempt (caller state)
GET /api/v1/me/permissions                                   ← exempt (caller state)
GET /api/v1/module-access/matrix
GET /api/v1/module-access/modules
GET /api/v1/permission-matrix
GET /api/v1/permissions
GET /api/v1/platform-users
GET /api/v1/platform-users/{user_id}
GET /api/v1/ready                                            ← public, exempt
GET /api/v1/role-assignments
GET /api/v1/roles
GET /api/v1/roles/{role_id}/permissions
GET /api/v1/tenant-users
GET /api/v1/tenant-users/{user_id}
GET /api/v1/tenants
GET /api/v1/tenants/stats
GET /api/v1/tenants/{tenant_id}
GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children
GET /api/v1/tenants/{tenant_id}/org-tree
```

**Observation:** 23 APIRoutes (unchanged from prior investigation). Exempt: 4 (`/health`, `/ready`, `/me/permissions`, `/me/can-do`). **Retrofit-eligible: 19**, NOT 17 as the prior investigation's F-INVENTORY-MASTER stated. The prior figure was an arithmetic error (rows 3-21 inclusive is 19 rows, not 17). All 19 retrofit-eligible endpoints are listed in the prior investigation's master table; the count was the error, not the contents.

No new endpoints added between `e0946b8` and `ddea23c` — 6.9.3.1 didn't touch routers.

**Confidence:** high.

---

### F-INVENTORY-2: cascade rule restated and "likely retrofit tuple" guesses re-examined

**Question:** Does scope cascade change the "likely retrofit tuple" picks for each endpoint?

**Cascade rule (restated precisely):** A user's grant at scope N satisfies a check at scope N or any scope BELOW N (per `_SCOPE_CASCADE_ORDER`: GLOBAL > TENANT > BUSINESS_UNIT > HQ > COUNTRY > REGION > STORE > DEPARTMENT). The rule applies to the user's HELD grants vs the endpoint's REQUIRED check — NOT to endpoints accepting lower-scope user grants by widening their gate.

**Implication for gate-tuple choice:** Each endpoint should be gated at the LOWEST scope its semantics warrant. Picking too HIGH a scope (e.g., GLOBAL) denies all users without a GLOBAL grant — including PLATFORM-audience roles that may only hold TENANT-scope grants (see F-GATE-REPLACE-3 for the SUPPORT_ADMIN case). Picking too LOW a scope (e.g., STORE) may admit users with narrowly-scoped grants who shouldn't reach the endpoint.

For tenant-scoped endpoints like `/tenants/{tenant_id}` and `/tenant-users/{user_id}`, the lowest appropriate scope is **TENANT**. A user with TENANT-scope grant passes; cascade lets PLATFORM-audience users with GLOBAL grants pass too. STORE-scope grants don't satisfy a TENANT check (cascade is downward; TENANT > STORE in our hierarchy, so STORE grants don't satisfy TENANT checks).

**Observation:** The prior investigation's "Likely retrofit tuple" guesses already pointed at TENANT-scope for tenant-scoped endpoints. Post-cascade, those guesses are CORRECT — cascade resolves the prior investigation's "multi-user-type access regression" worry (F-CATALOGUE-2 gap class #1) for endpoints whose appropriate scope is TENANT and where TENANT-scope tuples exist or can be added.

**Confidence:** high.

**Open question:** None directly; this finding anchors the rest of CATALOGUE/POST_CASCADE.

---

## POST_CASCADE — impact of cascade on prior 6.9.3 decisions

### F-POST_CASCADE-1: cascade resolves gap class #1 (multi-user-type access) WITH catalogue additions; doesn't help #2 or #3

**Question:** Does cascade let any of the prior investigation's 4 gap classes disappear?

**Prior F-CATALOGUE-2 gap classes (re-examined post-cascade):**

| Gap class | Endpoints | Pre-cascade verdict | Post-cascade verdict |
|---|---|---|---|
| #1 multi-user-type regression | `/tenants/*`, `/dashboard/*` | Gating with `ADMIN.TENANTS.VIEW.GLOBAL` denies TENANT users entirely. | **Cascade makes the GLOBAL pick mechanically safer for PLATFORM users** (PLATFORM_ADMIN has VIEW.GLOBAL → can read). But TENANT users still need a TENANT-scope tuple. **Catalogue addition still required for /tenants/* if TENANT users must keep access**. /dashboard/* needs new ADMIN.DASHBOARD.* tuples regardless. |
| #2 no catalogue tuple | `/module-access/*` | No `ADMIN.MODULES.*` exists. | **Cascade doesn't help** — no tuple to gate against. Catalogue addition required. |
| #3 reference data | `/lookups`, `/permissions`, `/permission-matrix` | Exemption candidate or low-bar perm. | **Cascade doesn't help** — these aren't tenant-scoped resources. Design call: exempt vs gate on reference-data tuple. |
| #4 `/role-assignments` | One endpoint | Proxy via `ADMIN.USERS.VIEW.TENANT` or new tuple. | **Cascade makes the proxy pick (ADMIN.USERS.VIEW.TENANT) mechanically sound** — PLATFORM/TENANT users with this grant both pass. |

**Observation:** Cascade resolves gap class #4 cleanly (proxy via existing TENANT-scope tuple works), partially resolves gap class #1 for PLATFORM users (cascade lets GLOBAL grants pass TENANT checks), but doesn't resolve TENANT user access without catalogue additions for /tenants/*. Gap classes #2 and #3 are catalogue-shape questions independent of cascade.

**Confidence:** high.

---

### F-POST_CASCADE-2: TENANT-scope catalogue additions still required for /tenants/* and /dashboard/*

**Question:** What new tuples does the catalogue need under cascade semantics?

**Observation:** Under cascade, gating at the LOWEST scope appropriate produces the cleanest semantics. For TENANT users to read endpoints scoped to their own tenant's data, TENANT-scope tuples must exist for those resources. Catalogue gaps:

| Endpoint | Proposed tuple | In catalogue? |
|---|---|---|
| `/tenants` (list, multi-tenant aggregate) | `ADMIN.TENANTS.VIEW.TENANT` | **MISSING** — only `.VIEW.GLOBAL` exists. |
| `/tenants/stats` | same | MISSING |
| `/tenants/{tenant_id}` | same | MISSING |
| `/dashboard/fleet-stats` | `ADMIN.DASHBOARD.VIEW.GLOBAL` + `ADMIN.DASHBOARD.VIEW.TENANT` | **BOTH MISSING** — no `ADMIN.DASHBOARD.*` exists. |
| `/dashboard/governance-stats` | same | MISSING |
| `/module-access/modules` | `ADMIN.MODULES.VIEW.GLOBAL` + `ADMIN.MODULES.VIEW.TENANT` | **BOTH MISSING** — no `ADMIN.MODULES.*` exists. |
| `/module-access/matrix` | same | MISSING |

For `/tenants/*`, an alternative path is to **exempt TENANT users from the gate** entirely and rely on RLS — but that breaks discipline (every retrofitted endpoint should have a gate or be in the allowlist). The cleaner path is catalogue addition.

**Confidence:** high.

**Open question:** Are tenant users intended to be able to read `/tenants` (a list) at all? The list returns RLS-filtered rows (always exactly their own tenant for TENANT JWTs). If product intent says "TENANT users should not see a tenants-list endpoint at all", then gating with `ADMIN.TENANTS.VIEW.GLOBAL` (PLATFORM-only) is correct — they get 403. If product intent says "TENANT users should see their own tenant via the same URL", catalogue addition required. **Design conversation territory.**

---

### F-POST_CASCADE-3: Audience-dispatch gate (option C in prior investigation) no longer needed

**Question:** Are all four prior multi-user-type retrofit patterns still on the table?

**Prior patterns (re-examined):**

- **(a) more-permissive scope.** Gate at GLOBAL. Pre-cascade: denied all TENANT users (regression). **Post-cascade: still denies TENANT users** because their grants are TENANT-scope and cascade is downward only (TENANT grants don't satisfy GLOBAL checks). Pattern unchanged.
- **(b) catalogue additions.** Add TENANT-scope counterparts. **Still viable** — and the cleanest path for genuinely multi-user-type endpoints.
- **(c) audience-dispatch gate.** Separate factory `require_audience_dispatch(platform_tuple, tenant_tuple)`. **No longer needed under cascade** — a single TENANT-scope tuple gates both audiences correctly: PLATFORM passes via cascade from GLOBAL, TENANT passes via direct TENANT grant. Cascade subsumes audience dispatch for the common case.
- **(d) exempt.** Add to discipline allowlist. **Still viable for reference data** (`/lookups`, `/permissions`, `/permission-matrix`) where any authenticated user is a legitimate caller.

**Observation:** Cascade reduces 4 patterns to 2: (b) catalogue additions and (d) exemption. The retrofit picks per endpoint based on the resource's nature (tenant data vs reference data) and the design's view on TENANT-user access.

**Confidence:** high.

---

## ANCHOR — per-resource anchor dependency mechanics

### F-ANCHOR-1: per-endpoint anchor lookup chains (re-confirmed)

**Question:** For each retrofit-target endpoint that needs `target_anchor`, what's the lookup chain?

**Endpoints needing an anchor (post-cascade scope picks):**

| Endpoint | Gate tuple (proposed) | target_anchor lookup |
|---|---|---|
| `/tenants/{tenant_id}` | `ADMIN.TENANTS.VIEW.TENANT` (new) | tenant-root org_node path: `org_nodes WHERE tenant_id=:id AND node_type='TENANT' AND parent_id IS NULL → path` |
| `/tenants/{tenant_id}/org-tree` | `ADMIN.ORG_NODES.VIEW.TENANT` | same |
| `/tenants/{tenant_id}/org-nodes/{node_id}/children` | `ADMIN.ORG_NODES.VIEW.TENANT` | `node_id`'s own path: `org_nodes WHERE tenant_id=:tid AND id=:nid → path` (composite key per D-34) |
| `/tenant-users/{user_id}` | `ADMIN.USERS.VIEW.TENANT` | tenant-root path of user's tenant (TenantUser has NO `home_org_node_id` — confirmed at F-ANCHOR-2) |
| `/platform-users/{user_id}` | `ADMIN.USERS.VIEW.GLOBAL` (PLATFORM scope) | None (PLATFORM path ignores anchor) |

**Endpoints that take target_anchor=None (list endpoints + non-row-bound endpoints):**

- `/tenants` (list)
- `/tenants/stats`
- `/tenant-users` (list)
- `/platform-users` (list)
- `/dashboard/*`
- `/module-access/*`
- `/role-assignments`
- `/roles`, `/roles/{role_id}/permissions` (catalogue / global)
- `/permissions`, `/permission-matrix` (catalogue)
- `/lookups` (reference data)

**Observation:** Six of the 19 retrofit endpoints take a real `target_anchor`; the other 13 are list/catalogue/aggregate endpoints where anchor is None and the gate's existing `target_anchor=None` hardcoding stays.

**Confidence:** high.

---

### F-ANCHOR-2: TenantUser has no `home_org_node_id` at HEAD — confirmed unchanged

**Question:** Does TenantUser have a home_org_node FK that an anchor dep could use directly?

**Citation:** `src/admin_backend/models/tenant_user.py` (full file scan at HEAD `ddea23c`) — no `home_org_node_id`, `org_node_id`, or any org_node reference. 17 columns; same shape as the prior investigation reported.

**Observation:** The anchor for `/tenant-users/{user_id}` MUST default to the user's tenant root. Lookup chain: `tenant_user_id → tenant_users.tenant_id → org_nodes (tenant_id, node_type='TENANT', parent_id IS NULL) → path`. Two SELECTs OR one JOIN; no existing Repo method does this in one call.

**Confidence:** high.

**Open question:** None directly (Stage 2 design item: introduce `home_org_node_id` on `tenant_users` for finer-grained per-user anchoring; doesn't affect 6.9.3.2's tenant-root fallback).

---

### F-ANCHOR-3: no existing Repo method returns just `org_node.path`; new methods (or inline SQL) needed

**Question:** Can any existing Repo method be reused for anchor lookups?

**Citation:** Walk through every Repo method:

- `OrgNodesRepo` at `repositories/org_nodes.py:73-228` — methods: `count_active_by_tenant`, `list_active_with_child_counts`, `list_children_paginated`, `node_exists`. None returns a single node's path.
- `TenantsRepo` at `repositories/tenants.py:186-...` — list / detail with aggregates; doesn't return tenant-root path.
- `TenantUsersRepo` at `repositories/tenant_users.py:167-...` — `get_by_id` returns full TenantUser row only.

**Observation:** No reuse. Each anchor dep needs at minimum a new lightweight method. Two design shapes (from the prior investigation's F-ANCHOR-8, restated):

- **(a)** Distribute methods across Repos: `OrgNodesRepo.get_tenant_root_path(tenant_id)`, `OrgNodesRepo.get_path_by_id(tenant_id, node_id)`, `TenantUsersRepo.get_anchor_path_for_user(user_id)`.
- **(b)** Centralise in `auth/anchor_deps.py` (or `routers/v1/_anchor_deps.py`): small async functions issuing their own SQL inline.

**Confidence:** high.

**Open question:** Repo distribution vs centralised helper module. Prior investigation flagged this; design conversation picks.

---

### F-ANCHOR-4: `org_nodes.path::text` is the established transport shape

**Question:** Does any existing code expose `org_node.path` for transport? Is the cast-to-text pattern established?

**Citation:**

- `src/admin_backend/auth/permissions.py:336` — TENANT-side `_get_permissions_tenant` SELECT projection includes `on_.path::text AS anchor_path`.
- `src/admin_backend/repositories/org_nodes.py:172, :198` — `list_children_paginated` returns OrgNode rows with `.path` as `Mapped[str]` (the ORM models it as text-typed; SQLAlchemy reads ltree as Python `str` via the LtreeType absence — the model declares `path: Mapped[str]`).
- Step 5.3's `OrgTreeResponse` schema exposes `path: str` in `OrgNodeTreeItem`.

**Observation:** The codebase consistently transports `org_nodes.path` as a Python `str` (ltree values are bytes-compatible with text encoding in psycopg). No explicit cast is needed at the SQLAlchemy ORM layer; raw SQL paths use `path::text` defensively (as in `_get_permissions_tenant`). Anchor deps returning `str | None` matches the `has_permission` `target_anchor: str | None` parameter type signature.

**Confidence:** high.

---

### F-ANCHOR-5: anchor dep return type — `str | None`

**Question:** What return type should anchor dependencies have?

**Citation:** `src/admin_backend/auth/permissions.py:83` — `target_anchor: str | None = None` (has_permission's parameter).

**Observation:** Anchor deps return `str | None`. `None` signals "no anchor for this request" (used by list endpoints and PLATFORM-scope checks). `str` is an ltree-formatted path string. Anchor deps that need to surface "row not found" should raise 404 (e.g., `OrgNodeNotFoundError`, `TenantUserNotFoundError`) rather than returning `None` — see F-THREADING-4 for the rationale.

**Confidence:** high.

---

## THREADING — `target_anchor` from anchor dep into the gate factory

### F-THREADING-1: FastAPI composes nested Depends transparently inside the gate's inner function

**Question:** Can the gate factory's inner function receive a value from a per-endpoint anchor dep that the endpoint also declares?

**Mechanics (FastAPI standard):** When `Depends(require(M, R, A, S, anchor_dep=X))` resolves, FastAPI inspects the inner `gate` function's signature. If `gate` declares `target_anchor: str | None = Depends(X)` (where `X` is the anchor function), FastAPI resolves `X` BEFORE running `gate` and passes the resolved value as `target_anchor`. This is the canonical "dependency-of-dependency" pattern documented in FastAPI's docs.

**Verification path (mechanics, not via experimental code):** Cross-reference against the existing factory at `auth/permissions.py:397-419`. The current inner `gate` declares `auth: AuthContext = Depends(get_auth_context)` and `session: AsyncSession = Depends(get_tenant_session_dep)` — both ARE resolved before `gate` runs via the exact same mechanism. Adding `target_anchor: str | None = Depends(anchor_dep)` to the same inner function uses the same path; no new framework gymnastics required.

**Confidence:** high.

---

### F-THREADING-2: pattern (a) — factory accepts `anchor_dep` callable — is the clean path

**Question:** Of three candidate threading shapes, which is FastAPI-canonical?

**Pattern (a) — `require(M, R, A, S, *, anchor_dep=None)`:**

```python
def require(
    module: ModuleCode,
    resource: PermissionResource,
    action: PermissionAction,
    scope: PermissionScope,
    *,
    anchor_dep: Callable[..., Awaitable[str | None]] | None = None,
) -> Callable[..., Awaitable[None]]:
    if anchor_dep is None:
        async def gate(
            auth: AuthContext = Depends(get_auth_context),
            session: AsyncSession = Depends(get_tenant_session_dep),
        ) -> None:
            allowed, _, _ = await has_permission(
                session, auth, module, resource, action, scope,
                target_anchor=None,
            )
            if not allowed:
                raise PermissionDeniedError(...)
    else:
        async def gate(
            auth: AuthContext = Depends(get_auth_context),
            session: AsyncSession = Depends(get_tenant_session_dep),
            target_anchor: str | None = Depends(anchor_dep),
        ) -> None:
            allowed, _, _ = await has_permission(
                session, auth, module, resource, action, scope,
                target_anchor=target_anchor,
            )
            if not allowed:
                raise PermissionDeniedError(..., target_anchor=target_anchor)
    return gate
```

Endpoints declare `_: None = Depends(require(MODULE, RESOURCE, ACTION, SCOPE, anchor_dep=get_some_anchor))`. FastAPI resolves both `auth`, `session`, and `target_anchor` (via the anchor dep) before `gate` runs.

**Pattern (b) — parallel Depends at the handler signature:**

```python
@router.get(...)
async def handler(
    _: None = Depends(require(M, R, A, S)),
    target_anchor: str | None = Depends(get_some_anchor),
    ...
):
    ...
```

This makes the anchor available to the handler body but NOT to the gate (`require`'s inner gate doesn't know about the handler's `target_anchor` parameter). The gate would still see `target_anchor=None` — defeating the purpose. **Pattern (b) is structurally broken without additional plumbing.**

**Pattern (c) — configurable wrapper:** vague; no clean FastAPI idiom matches.

**Observation:** **Pattern (a) is the only structurally working option.** It composes cleanly inside the factory's closure; FastAPI resolves both deps before `gate` runs. The two-branch factory shape (anchor_dep=None vs not None) is necessary because FastAPI signature introspection requires a static inner function shape — two distinct inner functions, picked by `anchor_dep` at factory-call time.

**Confidence:** high.

---

### F-THREADING-3: `target_anchor=None` works uniformly; no separate "no anchor" gate variant needed

**Question:** Does the gate need to distinguish "no anchor expected" from "anchor=None"?

**Citation:** `auth/permissions.py:207-209` (TENANT-path SQL):

```sql
AND (
  CAST(:target_anchor AS text) IS NULL
  OR CAST(:target_anchor AS ltree) <@ on_.path
)
```

When `target_anchor IS NULL`, the cascade clause short-circuits to TRUE and any grant matches the tuple. Step 6.9.1's T_C4 test (`test_c4_grant_at_region_with_no_target_anchor_allowed`) verifies this end-to-end.

**Observation:** A single gate factory shape handles both anchored and non-anchored endpoints uniformly. List endpoints declare no `anchor_dep` (factory's `target_anchor=None` branch runs); single-resource endpoints declare an `anchor_dep` (factory's anchored branch runs). The SQL accepts `target_anchor=None` for list endpoints without further branching.

**Confidence:** high.

---

### F-THREADING-4: anchor-lookup failure should raise 404 (RLS-as-404 per D-17)

**Question:** When the path-param row doesn't exist or is RLS-invisible, should the anchor dep return None or raise 404?

**Citation:** Existing not-found pattern at multiple sites:

- `routers/v1/tenant_users.py:83` — `TenantUserNotFoundError`, raised when `Repo.get_by_id` returns None.
- `routers/v1/org_tree.py:93` — `OrgNodeNotFoundError`, same pattern.
- `routers/v1/rbac.py:93` — `RoleNotFoundError`, same.
- D-17 codifies: RLS-filtered / missing-row reads surface as 404.

**Observation:** Anchor deps should **raise the appropriate `*NotFoundError` (404)** when the lookup misses, matching the established not-found pattern. Returning `None` would cause the gate to evaluate with `target_anchor=None` — the cascade clause short-circuits to TRUE, granting access to a non-existent row. That's a security regression. The anchor dep is the single point where "row resolves" is verified; the gate trusts it.

This means anchor lookup failure short-circuits the request entirely BEFORE the gate evaluates `has_permission` — same shape as a `Depends(get_auth_context)` raising `AuthMissingError` short-circuits before handler body runs.

**Confidence:** high.

---

## GATE_REPLACE — `_require_platform_auth` retirement under cascade

### F-GATE-REPLACE-1: 2 call sites, 1 definition, 4 docstring references — unchanged at HEAD

**Question:** Has `_require_platform_auth` usage changed since the prior investigation?

**Citation:** Full grep `grep -rn "_require_platform_auth\|PlatformAccessRequiredError" src/admin_backend/ tests/ --include="*.py"`:

- Definition: `platform_users.py:78-87` (`PlatformAccessRequiredError`), `:102-109` (`_require_platform_auth`)
- Call sites: `platform_users.py:216`, `:258`
- Docstring references: `tenant_users.py:11`, `org_tree.py:20`, `platform_users.py:12`, `test_platform_users_router.py:347`

**Observation:** Unchanged from the prior investigation. Two call sites, one helper definition, one error class. Four documentation references that would become stale on retirement (three router-module docstrings + one test docstring).

**Confidence:** high.

---

### F-GATE-REPLACE-2: `ADMIN.USERS.VIEW.GLOBAL` is the semantically-equivalent tuple — but with coverage caveats

**Question:** What permission tuple captures the `user_type == PLATFORM` check?

**Semantics:** `_require_platform_auth` checks `auth.user_type != "PLATFORM"` and raises 403 if true. The replacement tuple should: (a) be held by all roles whose users currently pass the helper, (b) NOT be held by any role assignable to a TENANT user (the audience-check triggers from Step 6.8.1 ensure TENANT-audience roles can't be granted to PLATFORM users and vice versa).

`ADMIN.USERS.VIEW.GLOBAL` is PLATFORM-only (the GLOBAL scope tuples are PLATFORM-audience-only by design intent). It's the right tuple SEMANTICALLY.

**Confidence:** high.

---

### F-GATE-REPLACE-3 (**critical coverage gap**): `ADMIN.USERS.VIEW.GLOBAL` is held by SUPER_ADMIN ONLY — PLATFORM_ADMIN and SUPPORT_ADMIN don't have it

**Question:** Under `require(ADMIN, USERS, VIEW, GLOBAL)`, do all current PLATFORM-audience roles pass?

**Citation:** Seed-side query at HEAD ddea23c (executed during investigation):

```
ADMIN.USERS.VIEW.GLOBAL  →  held by:  SUPER_ADMIN (only)
ADMIN.USERS.VIEW.TENANT  →  held by:  SUPER_ADMIN, PLATFORM_ADMIN, SUPPORT_ADMIN,
                                      OWNER, ASSOCIATE, COMPLIANCE_OFFICER,
                                      DATA_ANALYST, FINANCE_ADMIN, NIGHT_SHIFT_LEAD,
                                      PERISHABLES_LEAD, PRICING_MANAGER,
                                      PROMOTIONS_MANAGER, STORE_MANAGER (13 roles)
ADMIN.TENANTS.VIEW.GLOBAL →  held by:  SUPER_ADMIN, PLATFORM_ADMIN, SUPPORT_ADMIN
```

PLATFORM_ADMIN grants: `ADMIN.USERS.VIEW.TENANT`, `ADMIN.USERS.CONFIGURE.TENANT`, `ADMIN.ROLES.VIEW.TENANT`, `ADMIN.AUDIT_LOG.VIEW.TENANT`, `ADMIN.TENANTS.VIEW.GLOBAL`, `ADMIN.TENANTS.CONFIGURE.GLOBAL` (6 perms).

SUPPORT_ADMIN grants: `ADMIN.USERS.VIEW.TENANT`, `ADMIN.USERS.OVERRIDE.GLOBAL`, `ADMIN.AUDIT_LOG.VIEW.TENANT`, `ADMIN.TENANTS.VIEW.GLOBAL` (4 perms).

**Observation:** Cascade is DOWNWARD only. PLATFORM_ADMIN has `ADMIN.USERS.VIEW.TENANT`; this does NOT satisfy `ADMIN.USERS.VIEW.GLOBAL` (which is BROADER, not narrower). Same for SUPPORT_ADMIN.

**Regression under naive retirement:** Replacing `_require_platform_auth` with `require(ADMIN, USERS, VIEW, GLOBAL)` would deny PLATFORM_ADMIN and SUPPORT_ADMIN users from `/platform-users/*` — they currently pass via `user_type=PLATFORM`. This is a behavior change at the role level, not just a code change.

Three resolution paths:

- **(a) Grant `ADMIN.USERS.VIEW.GLOBAL` to PLATFORM_ADMIN and SUPPORT_ADMIN** in the catalogue. Semantically defensible (these roles oversee user management at platform scale). Catalogue migration via seed Excel + role_permissions rows.
- **(b) Gate `/platform-users/*` on a different tuple that PLATFORM_ADMIN and SUPPORT_ADMIN already hold.** Candidates: nothing currently held by all three roles that's also TENANT-denied. None obviously fits.
- **(c) Keep `_require_platform_auth` (option (b) of FN-AB-26).** Defer retirement; route `/platform-users/*` continues to use the user-type-only check.

The prior 6.9.3 investigation's F-GATE-REPLACE-3 claimed "No catalogue gap; SUPER_ADMIN holds it." That was true for SUPER_ADMIN but missed PLATFORM_ADMIN and SUPPORT_ADMIN coverage. Surfacing now.

**Confidence:** high.

**Open question:** Pick (a), (b), or (c). Design conversation territory.

---

### F-GATE-REPLACE-4: `PlatformAccessRequiredError` has one raise site (`_require_platform_auth`); retires with it under option (a)

**Question:** If `_require_platform_auth` is retired, what happens to the error class?

**Citation:** Single raise site at `platform_users.py:105` (inside `_require_platform_auth`).

**Observation:** Class becomes dead code on retirement option (a)/(b). Two paths:

- Delete the class. Update the 2 tests that assert `code=PLATFORM_ACCESS_REQUIRED` (`test_platform_users_router.py:340-...` A2; one assertion in the cloud script test set). Cleanest.
- Keep as a forward-defensive artefact. No current consumer.

Under FN-AB-26 retirement option (c) (keep the helper), the class stays.

**Confidence:** high.

---

### F-GATE-REPLACE-5: 4 docstring references would need cleanup on retirement

**Question:** What docstring references would become stale?

**Citation:**

- `routers/v1/tenant_users.py:11` — module docstring describes "RLS-only" pattern by contrast with `_require_platform_auth`. Reword if helper retires; reference is to a coding convention, not a behavioral dependency.
- `routers/v1/org_tree.py:20` — same.
- `routers/v1/platform_users.py:12` — same; module-level prose introducing the file.
- `tests/integration/test_platform_users_router.py:347` — A2 test docstring explains the load-bearing assertion in terms of `_require_platform_auth`. Rewrite to reference `require(ADMIN, USERS, VIEW, GLOBAL)` if option (a) or (b).

**Observation:** All stale references rot in place if not updated. Retirement should bundle docstring cleanup.

**Confidence:** high.

---

## DISCIPLINE — mandatory-gate-discipline test mechanics

### F-DISCIPLINE-1: `app.routes` → `APIRoute.dependant.dependencies` → `.call` path confirmed at HEAD

**Question:** Does FastAPI's route-introspection path still work at HEAD ddea23c?

**Citation:** Live verification (run during this investigation):

```
APIRoute count: 23
GET /api/v1/me/permissions:
  endpoint: admin_backend.routers.v1.me.get_me_permissions
  dependant.dependencies n=2
    - admin_backend.dependencies.get_auth_context
    - admin_backend.dependencies.get_tenant_session_dep
```

**Observation:** Unchanged from the prior investigation. FastAPI's introspection API is stable. The discipline test iterates `app.routes`, filters `APIRoute`, walks `route.dependant.dependencies`, inspects each `Dependant.call` for the gate marker.

**Confidence:** high.

---

### F-DISCIPLINE-2: gate marker still absent at HEAD; 6.9.3.2 must add one

**Question:** Does the require()-returned gate carry a marker attribute today?

**Citation:** `auth/permissions.py:397-419` — the `gate` inner function is defined and returned without any attribute assignment. Grep for `__permission_gate__`, `gate.__`, `gate_marker` in `auth/permissions.py` returns zero hits.

**Observation:** No marker. 6.9.3.2 must add one inside the factory before `return gate`. Marker shape options:

- **(a) Tuple of enum values.** `gate.__permission_gate__ = (module, resource, action, scope)`. The discipline test reads it to assert "this route is gated on tuple X." Simple, immediately useful. Type annotation: `__permission_gate__: tuple[ModuleCode, PermissionResource, PermissionAction, PermissionScope]`.
- **(b) Typed dataclass.** `gate.__permission_gate__ = PermissionGateInfo(module=..., resource=..., action=..., scope=..., anchor_dep=...)`. Richer surface; includes anchor-dep reference for documentation. Type annotation: `__permission_gate__: PermissionGateInfo`. Adds a new schema class.
- **(c) Simple sentinel.** `gate.__permission_gate__ = True` or a module-level singleton. Binary "is a gate" check only; no tuple introspection.

Pattern (a) is the established idiom in FastAPI parameterized gate factories. Pattern (b) is cleaner if 6.9.3.2 wants to surface the gate tuple in error responses or documentation. Pattern (c) is minimal but limits future test assertions.

**Confidence:** high.

**Open question:** Marker shape pick. Design conversation.

---

### F-DISCIPLINE-3: `PUBLIC_PATHS` unchanged at HEAD; gate allowlist = `PUBLIC_PATHS` ∪ `{/api/v1/me/permissions, /api/v1/me/can-do}`

**Question:** Has the auth-skip allowlist changed?

**Citation:** `middleware/auth.py:38-45` (same as prior investigation):

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

**Observation:** Unchanged. Gate allowlist = `PUBLIC_PATHS` ∪ `{/api/v1/me/permissions, /api/v1/me/can-do}` per F-DISCIPLINE-5 from the prior investigation. Note: `/api/v1/openapi.json`, `/api/v1/docs`, `/api/v1/redoc`, and `/metrics` are auth-skipped but NOT user-facing endpoints; the discipline test should also include them in its allowlist (FastAPI's `/docs` and `/redoc` are mounted as `Route` not `APIRoute`, so they may not even be enumerated by the discipline test's filter — verify when implementing).

**Confidence:** high.

---

### F-DISCIPLINE-4: audience-dispatch gate is no longer needed under cascade (F-POST_CASCADE-3); marker logic stays uniform

**Question:** Does the discipline test need to handle multiple gate-factory shapes?

**Observation:** Under F-POST_CASCADE-3, audience-dispatch gates are no longer needed — a single TENANT-scope tuple gates both audiences correctly via cascade. The discipline test only needs to handle the one `require(...)` factory shape. Marker logic uniform across all gated routes.

**Confidence:** high.

---

### F-DISCIPLINE-5: gate allowlist location — design call between dedicated module vs inline-in-test

**Question:** Where does the gate-exempt allowlist live?

**Citation:** Prior investigation's F-DISCIPLINE-4 outlined three options (rejecting extension of `PUBLIC_PATHS` due to different concerns). Re-stated:

- **(a)** `src/admin_backend/auth/gate_allowlist.py` (new module). Importable by both runtime code and the discipline test. Mirrors `auth/permissions.py` neighbour. Pros: shared if any runtime code ever needs the allowlist (none today). Cons: empty-module syndrome for one frozenset.
- **(b)** Inline constant in the discipline test file (`tests/integration/test_gate_discipline.py` or similar). No runtime sharing; test owns the contract. Tightest coupling between test and allowlist. Pros: simplest. Cons: harder to share if future use cases emerge.
- **(c)** Extend `middleware/auth.py:PUBLIC_PATHS`. **Rejected** — conflates auth-skip with gate-skip; `/me/*` IS auth-required.

**Observation:** No new context post-cascade. Design conversation picks between (a) and (b).

**Confidence:** high.

---

## CATALOGUE — permission catalogue under cascade

### F-CATALOGUE-1: 30-row catalogue at HEAD (unchanged since 6.9.2)

**Question:** Has the catalogue changed?

**Citation:** Query at HEAD ddea23c returned 30 rows; identical to the prior investigation's enumeration. No additions or removals.

**Observation:** Catalogue is stable. Cascade was introduced WITHOUT catalogue changes; 6.9.3.2 may need to add tuples (see F-CATALOGUE-5 / F-CATALOGUE-6 below).

**Confidence:** high.

---

### F-CATALOGUE-2: per-endpoint gate-tuple proposals under cascade

**Question:** For each of the 19 retrofit-eligible endpoints, what's the proposed tuple and its catalogue/grant status?

**Master table (cascade-aware picks):**

| # | Endpoint | Proposed gate tuple | In catalogue? | Role coverage notes |
|---|---|---|---|---|
| 1 | GET /tenants | `ADMIN.TENANTS.VIEW.TENANT` (new) OR `ADMIN.TENANTS.VIEW.GLOBAL` | TENANT MISSING / GLOBAL present | GLOBAL: 3 PLATFORM roles (SUPER_ADMIN, PLATFORM_ADMIN, SUPPORT_ADMIN); 0 TENANT roles. If TENANT users should keep multi-user-type access, ADD `.VIEW.TENANT` and grant to OWNER. |
| 2 | GET /tenants/stats | same | same | same |
| 3 | GET /tenants/{tenant_id} | same | same | same |
| 4 | GET /tenant-users (list) | `ADMIN.USERS.VIEW.TENANT` | present | 13 roles (3 PLATFORM + 10 TENANT including OWNER). Wide coverage. |
| 5 | GET /tenant-users/{user_id} | same | present | same |
| 6 | GET /platform-users (list) | `ADMIN.USERS.VIEW.GLOBAL` | present | **SUPER_ADMIN only**. PLATFORM_ADMIN + SUPPORT_ADMIN regression risk — see F-GATE-REPLACE-3. |
| 7 | GET /platform-users/{user_id} | same | same | same |
| 8 | GET /tenants/{id}/org-tree | `ADMIN.ORG_NODES.VIEW.TENANT` | present | **SUPER_ADMIN only**. OWNER doesn't have it — regression for tenant admins on /org-tree. |
| 9 | GET /tenants/{id}/org-nodes/{nid}/children | same | present | same |
| 10 | GET /lookups | exempt (reference data) OR `ADMIN.ROLES.VIEW.TENANT` | n/a / present | If exempt, add to discipline allowlist. If gated on ROLES.VIEW.TENANT: 9 roles (PLATFORM_ADMIN + SUPER_ADMIN + 7 TENANT incl. OWNER). |
| 11 | GET /roles | `ADMIN.ROLES.VIEW.TENANT` | present | same 9 roles |
| 12 | GET /roles/{role_id}/permissions | same | present | same |
| 13 | GET /permissions | `ADMIN.ROLES.VIEW.TENANT` (reuse) | present | same |
| 14 | GET /permission-matrix | same | present | same |
| 15 | GET /dashboard/fleet-stats | `ADMIN.DASHBOARD.VIEW.GLOBAL` (new) + `ADMIN.DASHBOARD.VIEW.TENANT` (new) | **BOTH MISSING** | Add to catalogue; grant GLOBAL to PLATFORM roles, TENANT to OWNER. |
| 16 | GET /dashboard/governance-stats | same | same | same |
| 17 | GET /module-access/modules | `ADMIN.MODULES.VIEW.GLOBAL` (new) + `ADMIN.MODULES.VIEW.TENANT` (new) | **BOTH MISSING** | Add to catalogue; grant accordingly. |
| 18 | GET /module-access/matrix | same | same | same |
| 19 | GET /role-assignments | `ADMIN.USERS.VIEW.TENANT` (proxy) | present | 13 roles (wide coverage). |

**Coverage gap summary:**

- **6 endpoints under existing-tuple gates have a role-coverage regression** vs current RLS-only / `_require_platform_auth` behaviour:
  - `/tenants/*` × 3 if GLOBAL-only: denies all TENANT users (RLS-only access today).
  - `/platform-users/*` × 2 if GLOBAL: denies PLATFORM_ADMIN and SUPPORT_ADMIN (pass today).
  - `/tenants/{id}/org-tree` and `/org-nodes/{nid}/children` × 2: deny OWNER (passes today via RLS).
- **4 endpoints have NO catalogue tuple at all** (`/dashboard/*` × 2, `/module-access/*` × 2). Must add 4 new permission tuples + grants.

**Confidence:** high.

**Open question:** For each coverage gap, design picks: (a) add to catalogue + grant to affected roles, (b) gate at a wider tuple some affected roles hold, (c) exempt, (d) live with the regression as design intent.

---

### F-CATALOGUE-3: critical grant-coverage gaps — STORES/ORG_NODES TENANT-scope tuples are SUPER_ADMIN-only

**Question:** Which roles currently hold which retrofit-candidate tuples?

**Query results at HEAD:**

```
ADMIN.STORES.VIEW.TENANT     →  SUPER_ADMIN only.
ADMIN.STORES.CONFIGURE.TENANT →  SUPER_ADMIN only.
ADMIN.ORG_NODES.VIEW.TENANT   →  SUPER_ADMIN only.
ADMIN.ORG_NODES.CONFIGURE.TENANT  →  SUPER_ADMIN only.
ADMIN.USERS.VIEW.GLOBAL       →  SUPER_ADMIN only.
ADMIN.USERS.OVERRIDE.GLOBAL   →  SUPER_ADMIN + SUPPORT_ADMIN.
ADMIN.AUDIT_LOG.VIEW.TENANT   →  SUPER_ADMIN + PLATFORM_ADMIN + SUPPORT_ADMIN + 8 TENANT roles.
```

**Observation:** Step 6.8.2.1's catalogue additions (ADMIN.STORES/ORG_NODES/USERS at GLOBAL/TENANT scopes) granted ONLY to SUPER_ADMIN. The intent at that step was "Super Admin's own coverage" — not broader role assignment. The retrofit must grant these tuples to OWNER (and possibly PLATFORM_ADMIN, SUPPORT_ADMIN, additional tenant admin roles) if the gates use them.

OWNER's 19 grants today include `ADMIN.USERS.{VIEW,CONFIGURE}.TENANT`, `ADMIN.ROLES.{VIEW,CONFIGURE}.TENANT`, `ADMIN.AUDIT_LOG.{VIEW,AUDIT}.TENANT` — six ADMIN-domain TENANT-scope grants. But NOT `ADMIN.STORES.*` or `ADMIN.ORG_NODES.*`. The tenant owner can't currently configure stores or org-nodes via any catalogue grant.

This is a real catalogue/grants design concern that 6.9.3.2 surfaces. Either:
- The tenant owner SHOULD have ADMIN.{STORES, ORG_NODES}.{VIEW, CONFIGURE}.TENANT — grant additions needed.
- These resources are Ithina-staff-only — and TENANT users (including OWNER) get 403 on `/tenants/{id}/org-tree` after retrofit. Regression vs today's RLS-only access.

**Confidence:** high.

**Open question:** Which roles should hold the TENANT-scope STORES/ORG_NODES tuples? Operator design call.

---

### F-CATALOGUE-4: reference-data endpoints — design call between exempt and gate on existing tuple

**Question:** For /lookups, /permissions, /permission-matrix — gate or exempt?

**Observation:** Re-stated from prior investigation; no new context post-cascade.

- (a) **Exempt.** Reference data inherently public to authenticated users. Discipline allowlist grows by 4 paths.
- (b) **Gate on `ADMIN.ROLES.VIEW.TENANT`.** Held by 9 roles (including OWNER) but NOT by SUPPORT_ADMIN, ASSOCIATE, DATA_ANALYST, NIGHT_SHIFT_LEAD, REGIONAL_DIRECTOR, CATEGORY_MANAGER. Several TENANT roles would lose access to admin-UI dropdowns/labels — minor regression.
- (c) **Add a low-bar permission everyone has.** No such permission exists today; would require catalogue addition.

**Confidence:** high.

**Open question:** Design pick.

---

### F-CATALOGUE-5: exact new catalogue additions for /tenants/* and /dashboard/*

**Question:** If design picks "add TENANT-scope counterparts and grant to OWNER", what exactly gets added?

**Exact tuple additions:**

| New tuple | Module | Resource | Action | Scope | Code | Description suggestion |
|---|---|---|---|---|---|---|
| `ADMIN.TENANTS.VIEW.TENANT` | ADMIN | TENANTS | VIEW | TENANT | ADMIN.TENANTS.VIEW.TENANT | View own tenant's tenant-row data (multi-user-type /tenants/{id}) |
| `ADMIN.DASHBOARD.VIEW.GLOBAL` | ADMIN | DASHBOARD | VIEW | GLOBAL | ADMIN.DASHBOARD.VIEW.GLOBAL | View fleet-scale dashboard aggregates |
| `ADMIN.DASHBOARD.VIEW.TENANT` | ADMIN | DASHBOARD | VIEW | TENANT | ADMIN.DASHBOARD.VIEW.TENANT | View tenant-scoped dashboard aggregates |

**Caveat:** `ADMIN.DASHBOARD.*` requires extending `resource_enum` in the DDL (a Postgres `ALTER TYPE ... ADD VALUE`). Verify the enum at HEAD includes DASHBOARD — if not, the catalogue addition is a 2-step migration (enum first, then catalogue row).

**Citation check on `resource_enum` at HEAD:** `src/admin_backend/models/permission.py:49-63` enumerates `PRICING_RULES, MARKDOWNS, EXPIRING_ITEMS, WASTE_LOG, DONATION_ROUTING, CAMPAIGNS, USERS, ROLES, AUDIT_LOG, TENANTS, STORES, ORG_NODES` (12 values). **No DASHBOARD value.** Catalogue addition for `/dashboard/*` requires `resource_enum` expansion. Same for `MODULES` (next finding).

**Grant assignments (suggested):**

| Tuple | Grant to roles |
|---|---|
| `ADMIN.TENANTS.VIEW.TENANT` | SUPER_ADMIN, OWNER, possibly all tenant admin roles |
| `ADMIN.DASHBOARD.VIEW.GLOBAL` | SUPER_ADMIN, PLATFORM_ADMIN, SUPPORT_ADMIN |
| `ADMIN.DASHBOARD.VIEW.TENANT` | OWNER, possibly REGIONAL_DIRECTOR, STORE_MANAGER (tenant managers who need fleet-level summaries) |

**Confidence:** high.

**Open question:** Catalogue migration shape — Excel-only (matches Step 6.8.2.1) vs Alembic migration (for cloud parity, since cloud seed re-load runs post-deploy). Affects deploy steps.

---

### F-CATALOGUE-6: exact new catalogue additions for /module-access/*

**Question:** Same shape for the module-access endpoints?

**Exact tuple additions:**

| New tuple | Code | Description |
|---|---|---|
| `ADMIN.MODULES.VIEW.GLOBAL` | ADMIN.MODULES.VIEW.GLOBAL | View fleet-wide module enablement (per-module + matrix) |
| `ADMIN.MODULES.VIEW.TENANT` | ADMIN.MODULES.VIEW.TENANT | View own tenant's module enablement |

**Caveat:** Requires `resource_enum` to include `MODULES` — not present at HEAD per F-CATALOGUE-5 enum check. Two-step migration.

**Grant assignments:**

| Tuple | Grant to roles |
|---|---|
| `ADMIN.MODULES.VIEW.GLOBAL` | SUPER_ADMIN, PLATFORM_ADMIN, SUPPORT_ADMIN |
| `ADMIN.MODULES.VIEW.TENANT` | OWNER, possibly REGIONAL_DIRECTOR |

**Confidence:** high.

---

### F-CATALOGUE-7: /role-assignments — proxy via `ADMIN.USERS.VIEW.TENANT` (existing tuple, wide coverage)

**Question:** Does /role-assignments need a new tuple?

**Observation:** Proxy via `ADMIN.USERS.VIEW.TENANT` (held by 13 roles including OWNER and the 3 PLATFORM-audience roles) gates the endpoint reasonably. Role-assignments visibility is fundamentally about "who holds what role" — a USER-related concern. Adding `ADMIN.ROLE_ASSIGNMENTS.VIEW.*` is purer but adds catalogue maintenance for no operational gain in v0.

Recommendation surfaced (not a design decision): proxy. Catalogue addition `ADMIN.ROLE_ASSIGNMENTS.*` is a Stage 2 forward-note candidate if the resource grows distinct semantics.

**Confidence:** high.

**Open question:** Proxy vs dedicated tuple. Design call.

---

## Open questions for design conversation

Consolidated from each finding plus naturally-surfaced observations.

### In-scope (Step 6.9.3.2 design):

1. **F-CATALOGUE-3 / F-INVENTORY-2 / F-POST_CASCADE-2** — How to gate multi-user-type endpoints (`/tenants/*`, `/dashboard/*`)? Pick from: (a) GLOBAL gate, accept TENANT regression; (b) add TENANT-scope counterparts to catalogue + grants to OWNER; (c) gate at TENANT-scope only after catalogue addition; (d) audience-dispatch is no longer needed under cascade — drop it from consideration. Big load-bearing question.

2. **F-CATALOGUE-3 (grant coverage)** — Roles that should hold the existing TENANT-scope STORES/ORG_NODES tuples (currently SUPER_ADMIN only). Adding to OWNER is the obvious next step; should other tenant admin roles get them too?

3. **F-GATE-REPLACE-3 (PLATFORM_ADMIN/SUPPORT_ADMIN coverage gap)** — Replacing `_require_platform_auth` with `require(ADMIN, USERS, VIEW, GLOBAL)` denies PLATFORM_ADMIN and SUPPORT_ADMIN (only SUPER_ADMIN holds `.VIEW.GLOBAL`). Pick from: (a) grant `.VIEW.GLOBAL` to PLATFORM_ADMIN and SUPPORT_ADMIN; (b) different gate tuple; (c) keep `_require_platform_auth` per FN-AB-26 option (c). 

4. **F-CATALOGUE-5 / F-CATALOGUE-6** — Catalogue additions for `/dashboard/*` and `/module-access/*` require expanding `resource_enum` (DDL change + Alembic migration to add DASHBOARD, MODULES values). Affects the migration shape of 6.9.3.2.

5. **F-CATALOGUE-4** — Reference-data endpoints: exempt vs gate. Picks tightly to the discipline-test allowlist's final content.

6. **F-THREADING-2** — Confirm gate factory signature change: `require(M, R, A, S, *, anchor_dep=None)`. Factory returns one of two inner-function shapes depending on `anchor_dep`.

7. **F-DISCIPLINE-2** — Gate marker shape: tuple of enum values (simple) vs typed dataclass (richer) vs sentinel (minimum).

8. **F-DISCIPLINE-5** — Gate allowlist location: dedicated `auth/gate_allowlist.py` module vs inline-in-test.

9. **F-ANCHOR-3** — Anchor lookup methods: distribute across Repos vs centralised `auth/anchor_deps.py` helper module.

10. **F-GATE-REPLACE-4** — Retire `PlatformAccessRequiredError` class on retirement of `_require_platform_auth`?

11. **F-CATALOGUE-7** — `/role-assignments` proxy via existing `ADMIN.USERS.VIEW.TENANT` vs dedicated `ADMIN.ROLE_ASSIGNMENTS.*` tuple.

### Out-of-scope (scope-creep flags for later prompts, NOT investigated here):

- **`tenant_users.home_org_node_id` schema addition.** Stage 2 backlog (per prior investigation F-ANCHOR-2 open question). Not 6.9.3.2.
- **Audit log writes on gate denials.** Deferred to Step 6.16 per Section 6.9 design lock. `PermissionDeniedError.context` already carries the structured fields Step 6.16 will consume.
- **FN-AB-27 `/me/permissions` shape simplification.** Revisit only if frontend integration during 6.9.3.2 surfaces concrete friction.
- **`/role-assignments/{id}` detail endpoint.** Doesn't exist at HEAD; out of 6.9.3.2 scope.
- **`/metrics` endpoint.** Listed in PUBLIC_PATHS forward-looking but no route mounted. Prometheus integration is a separate concern.
- **Stage 3 Auth0 swap (FN-AB-22).** Still flagged for Stage 3 kickoff; AuthContext shape stable.
- **`has_permission()` caching (FN-AB-24).** Sub-millisecond plans; revisit only on measured hot-path indication.
- **`PermissionScope` enum expansion (FN-AB-28).** Future; helper is already forward-compat.
