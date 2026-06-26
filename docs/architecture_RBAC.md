# Role-Based Access Control (RBAC)

> Shipped to HEAD `f3826a8` as of 2026-05-13.

## Overview

The admin-backend uses a permission-based RBAC model for authorising 
request access. Authorisation is enforced at the gate layer 
(immediately before request handlers execute) via FastAPI 
dependencies. A single SQL query per gate check determines whether 
the requesting user holds the required permission tuple, with two 
orthogonal cascade dimensions (scope and org-tree anchor) handled 
inside that query.

The model has three structural pieces:

1. **Resolver layer** — pure-SQL permission check 
   (`has_permission`); takes auth context, tuple, optional 
   target anchor; returns allowed/reason/detail.

2. **Gate layer** — FastAPI dependency factory (`require()`) that 
   calls the resolver. Endpoints declare gates via 
   `Depends(require(...))`. Two inner-function shapes are produced 
   by the factory based on whether the gate needs an anchor 
   dependency.

3. **Error layer** — system-wide `PermissionDeniedError` 
   (subclass of `ClientError`). 403 response. Structured context 
   fields available via `exc.context` for audit logs; 
   `details=null` in the response envelope.

A mandatory-gate-discipline meta-test (
`tests/integration/test_gate_discipline.py`) iterates `app.routes` 
on app start and asserts every `APIRoute` is either gated (carries 
the `__permission_gate__` marker attribute) or in an explicit 
allowlist. This is the deploy-time structural guarantee that 
prevents endpoints shipping without authorisation.

## System model: module, resource, action, scope

The Ithina admin system authorises actions using a 4-dimensional 
permission identity. Every gated endpoint maps to exactly one 
4-tuple:

```
(MODULE, RESOURCE, ACTION, SCOPE)
```

Each dimension answers a different question about the requested 
action:

```
MODULE    — which product surface of the system?
RESOURCE  — what kind of entity is being acted on?
ACTION    — what operation is being performed?
SCOPE     — at what level of organisational authority?
```

A grant is the same shape. A user holds grants (via roles); a 
request requires a grant. The gate succeeds when the user's 
grants include a tuple that satisfies the requested tuple.

### Module: the product surface

Modules are top-level product areas. Each tenant subscribes to 
zero or more modules; per-tenant module-enablement is tracked in 
`tenant_module_access`. A user's grant in a module is only 
effective if the tenant has that module enabled.

```
ADMIN                  Cross-cutting platform administration 
                       (tenant management, user management, role 
                       management, audit log)
PRICING_OS             Pricing engine (rules, markdowns, 
                       overrides)
PERISHABLES_ASSISTANT  Perishables management (expiring items, 
                       waste logging, donation routing)
PROMOTIONS_ASSISTANT   Promotions and campaign management
... (others as the product expands)
```

Module enablement is a separate concern from authorisation. A 
TENANT user with grants in `PRICING_OS` cannot exercise them if 
the tenant has disabled `PRICING_OS`. PLATFORM users (Ithina 
staff) bypass module-enablement checks — they administer the 
system regardless of which modules a tenant has chosen.

### Resource: the entity type

Resources are the addressable entity types within a module:

```
TENANTS         Tenant organisations
USERS           Platform and tenant users
ROLES           Role catalogue (which permissions a role grants)
STORES          Physical store locations
ORG_NODES       Internal organisation tree nodes (HQ, regions, etc.)
AUDIT_LOG       Compliance audit entries
PRICING_RULES   Pricing rule definitions (PRICING_OS module)
MARKDOWNS       Markdown approvals and overrides (PRICING_OS)
... (others per module)
```

Resources are NOT the same as URL paths. One resource can have 
multiple endpoints (list, detail, create, update, delete). The 
permission identity is the resource type; the HTTP route is just 
the addressing mechanism.

### Action: the semantic operation

Actions describe what's being done, semantically — not which HTTP 
verb is used:

```
VIEW         Read operation (list or detail)
CONFIGURE    Mutating operation (create, update, delete shape)
APPROVE      Workflow gate (approve a markdown, approve a campaign)
EXECUTE      Operational action (log waste, route donation)
OVERRIDE     Escalated authority (override a system decision)
AUDIT        Compliance review (read audit log with sensitive 
             context)
```

Multiple HTTP routes can map to the same action — a POST and a 
PUT that both modify a resource typically use CONFIGURE. The 
"Action semantics" table in this document maps HTTP verbs to 
typical actions; see that section for the full grid.

The action enum exists at the system level (not per-resource), 
so the catalogue keeps consistent semantics across modules. 
`PRICING_OS.MARKDOWNS.APPROVE.STORE` and `ADMIN.TENANTS.CONFIGURE.TENANT` 
share the same action enum even though they're in different 
modules.

### Scope: the level of authority

Scope encodes the organisational level at which a grant applies. 
The full org hierarchy has 8 levels (top to bottom):

```
GLOBAL           Platform-wide (Ithina staff)
TENANT           Whole tenant organisation
BUSINESS_UNIT    Tenant's business unit
HQ               Tenant's HQ
COUNTRY          Country within tenant
REGION           Region within country
STORE            Individual store
DEPARTMENT       Department within store
```

`PermissionScope` enum at HEAD has 3 values (GLOBAL, TENANT, STORE). 
The 5 intermediate levels are valid org-tree node types but are NOT 
yet in the scope enum. They're encoded in the cascade-order tuple 
for forward-compatibility; the enum expands as product needs surface.

Scope cascades DOWNWARD only: a grant at GLOBAL satisfies a check 
at TENANT or STORE. A grant at TENANT does NOT satisfy a check at 
GLOBAL. This matches the intuition that broader authority subsumes 
narrower authority, not vice versa.

### The system identity, summarised

A permission tuple is a 4-tuple that answers: 
**"In WHICH product surface, on WHICH entity type, performing 
WHICH semantic operation, at WHICH level of authority?"**

Examples reading the tuple right-to-left:

```
ADMIN.TENANTS.VIEW.GLOBAL
  → "Across the platform (GLOBAL), 
     view (VIEW) tenant organisations (TENANTS), 
     in the admin module (ADMIN)."
  → Ithina staff read tenants across all customers.

ADMIN.TENANTS.VIEW.TENANT
  → "Within a tenant (TENANT), 
     view (VIEW) tenant organisations (TENANTS), 
     in the admin module (ADMIN)."
  → Tenant admins view their own tenant's details.

PRICING_OS.MARKDOWNS.APPROVE.STORE
  → "At the store level (STORE), 
     approve (APPROVE) markdowns (MARKDOWNS), 
     in the pricing module (PRICING_OS)."
  → Store managers approve markdown proposals for their store.
```

### Two-dimensional cascade summary

Two independent cascade dimensions apply inside `has_permission`. 
Both must pass for a grant to satisfy a request:

```
Scope cascade   — does the user's grant cover the requested level?
                  (GLOBAL ↓ TENANT ↓ STORE)
                  
Anchor cascade  — does the user's grant cover the specific tree 
                  position of the request's target?
                  (tenant root ↓ region ↓ store)
```

The full mechanism (Postgres ltree, ANY clauses, the satisfying-
scopes helper) is in the "Resolver" section below. For the 
mental model: scope answers "level of authority"; anchor answers 
"position in your tenant's org tree." A grant succeeds when both 
dimensions are satisfied.

## Mental model: full request flow

```
Request comes in
      │
      ▼
AuthMiddleware
      │  Verifies JWT, builds request.state.auth (AuthContext)
      │  Allows PUBLIC_PATHS through without auth
      │  (/health, /ready, /openapi.json, /docs, /redoc, /metrics)
      ▼
FastAPI dependency resolution begins
      │
      ├─► get_auth_context dependency → AuthContext from request.state
      │       Raises AuthMissingError (401) if state.auth not set
      │
      ├─► get_tenant_session_dep → AsyncSession (async generator)
      │       Opens DB connection
      │       Sets RLS GUCs: app.tenant_id, app.user_type, app.user_id
      │       Yields session to dependent code
      │       Session stays open through handler; closes on completion
      │
      ├─► [for endpoints with target_anchor:]
      │   get_<resource>_anchor → ltree path string
      │       Looks up org_node.path for the request's target row
      │       (or parent context for CREATE operations)
      │       Composite-key query (tenant_id + id) per D-34
      │       Returns None for endpoints with no specific target
      │       (list, aggregate, top-level CREATE, PLATFORM-scope)
      │       Raises *NotFoundError (404) on miss — NEVER returns 
      │       None to signal not-found (would short-circuit cascade)
      │
      └─► Depends(require(MODULE, RESOURCE, ACTION, SCOPE)) factory
              │  Inner gate function receives:
              │    auth (via Depends(get_auth_context))
              │    session (via Depends(get_tenant_session_dep))
              │    target_anchor (via Depends(anchor_dep) for 
              │      endpoints with a specific target; None for 
              │      list/aggregate/top-level CREATE)
              │  
              │  Calls has_permission(session, auth, M, R, A, S, target_anchor)
              │    Internal: dispatches on auth.user_type
              │    PLATFORM path → platform_user_role_assignments
              │    TENANT path → tenant_user_role_assignments + 
              │                    org_nodes + tenant_module_access
              │  
              │  Both paths apply scope cascade (Python helper + 
              │  SQL ANY clause) AND anchor cascade (Postgres ltree 
              │  `<@` operator)
              │  
              │  Returns (allowed: bool, reason_code, detail)
              │  
              │  If not allowed:
              │    raise PermissionDeniedError(detail, **context)
              │    → FastAPI exception handler at main.py
              │    → 403 {code:"PERMISSION_DENIED", 
              │           message:"Permission denied",
              │           details:null, request_id}
              │    Handler body NEVER executes
              │
              ▼
Handler body executes
      │  Receives resolved Depends (auth, session, target_anchor, 
      │  path params, query params, request body)
      │  Calls Repo methods (which use the shared session)
      │  RLS scopes queries per session GUCs
      │  Constructs response Pydantic model
      │
      ▼
FastAPI response serialization
      │  Pydantic → JSON envelope
      │  List endpoints: {items, pagination}
      │  Singleton endpoints: bare resource
      │  /me/permissions: batch-by-key shape
      │
      ▼
Middleware exit
      │  Session close, connection pool return
      │  Logging middleware records request
      │  request_id surfaces in response headers
      │
      ▼
HTTP response leaves the app
```

Error trajectory:

```
AuthMissing       → 401 (from get_auth_context, before gate runs)
AuthInvalid       → 401 (from middleware, before dependency resolution)
TargetNotFound    → 404 (from anchor dep for retrofitted endpoints,
                          before gate runs; RLS-as-404 invariant)
PermissionDenied  → 403 (from gate, before handler body)
HandlerError      → 500 or domain-specific 4xx (from handler body)
```

## The permission model: catalogue and roles

The system-model dimensions (module/resource/action/scope, above) 
map to concrete database tables. This section covers the catalogue 
mechanics — the schema, role indirection, and assignment shape.

### Catalogue: `core.permissions`

The 4-tuple identity is stored in `core.permissions` with a 
unique constraint over all four columns:

```
core.permissions
  id           uuid (DEFAULT uuidv7())
  module       enum module_enum             ← ModuleCode
  resource     enum permission_resource_enum ← PermissionResource
  action       enum permission_action_enum   ← PermissionAction
  scope        enum permission_scope_enum    ← PermissionScope
  code         text (denormalised: M.R.A.S dotted form)
  description  text
  created_at, updated_at  timestamptz
  
  UNIQUE (module, resource, action, scope)   ← uq_permissions_tuple
```

The tuple IS the structural identity. The `code` column is a 
denormalised display string (`ADMIN.TENANTS.VIEW.TENANT`); useful 
for logs and human-readable references, but NOT what the 
application uses for identity lookups.

Catalogue size at HEAD: 31 rows. Resource enum has 12 values 
(not yet DASHBOARD or MODULES — see FN-AB-29 for the deferred 
DDL expansion).

### Action semantics

The action is the SEMANTIC operation, not the HTTP verb. Multiple 
POST endpoints can map to different actions depending on intent.

| HTTP verb | Typical action          | Example tuple                       |
|-----------|-------------------------|-------------------------------------|
| GET       | VIEW                    | `ADMIN.TENANTS.VIEW.TENANT`         |
| POST      | CONFIGURE (create)      | `ADMIN.STORES.CONFIGURE.TENANT`     |
| PUT       | CONFIGURE (update)      | `ADMIN.STORES.CONFIGURE.TENANT`     |
| PATCH     | CONFIGURE (partial)     | `ADMIN.STORES.CONFIGURE.TENANT`     |
| DELETE    | CONFIGURE (or EXECUTE,  | `ADMIN.STORES.CONFIGURE.TENANT`     |
|           |  depending on semantics) |                                    |
| POST      | APPROVE (workflow)      | `PRICING_OS.MARKDOWNS.APPROVE.STORE`|
| POST      | EXECUTE (action)        | `PERISHABLES.WASTE_LOG.EXECUTE.STORE`|
| POST      | OVERRIDE (escalation)   | `PRICING_OS.MARKDOWNS.OVERRIDE.STORE`|
| POST      | AUDIT (compliance op)   | `ADMIN.AUDIT_LOG.AUDIT.TENANT`      |

Notes:

- **CONFIGURE** covers most data-shape changes (create, update, 
  partial update, delete). The same tuple gates create + update + 
  delete on the same resource by default; if finer separation is 
  needed (e.g., delete requires escalated authority), use distinct 
  tuples or add a new action enum value.

- **APPROVE / EXECUTE / OVERRIDE** are workflow actions, not 
  data-shape changes. They typically apply to operational 
  endpoints (markdown approval, waste-log entry, etc.), not to 
  CRUD on administrative resources.

- Write endpoints use the SAME `require()` factory and anchor 
  dependency mechanism as reads. No special write-only 
  authorisation path exists; this is a deliberate uniformity 
  choice.

### Roles

Roles are the indirection between users and permissions. A user 
doesn't directly hold permissions; a user has roles, and roles 
hold permissions via `role_permissions`.

Two role audiences:

```
PLATFORM roles (Ithina staff): SUPER_ADMIN, PLATFORM_ADMIN, 
                                SUPPORT_ADMIN
TENANT roles (tenant members): OWNER, FINANCE_ADMIN, REGIONAL_DIRECTOR, 
                                STORE_MANAGER, ASSOCIATE, ... 
                                (12 roles at HEAD)
```

Audience-check triggers at the DB level enforce that PLATFORM 
roles cannot be assigned to TENANT users (and vice versa).

### Assignments and anchors

A role-permission grant becomes a user's actual authority via 
role assignments:

```
platform_user_role_assignments (platform_user_id, role_id, status)
  - No anchor; PLATFORM grants apply cross-tenant by audience

tenant_user_role_assignments (tenant_user_id, role_id, tenant_id, 
                              org_node_id, status)
  - Anchored at an org_node within the tenant
  - Anchor cascade applies via ltree paths
```

### Two-dimensional cascade (mechanism)

The system-model "two-dimensional cascade" above (scope + anchor) 
is implemented inside `has_permission` via two independent SQL 
mechanisms. Both must pass for the resolver to return allowed:

```
Dimension          Mechanism                Direction
─────────────      ──────────────────       ──────────────
Scope cascade      Python helper +          Downward only
                   SQL ANY clause           (GLOBAL → TENANT 
                                            → STORE, etc.)
                   
Anchor cascade     Postgres ltree <@        Downward through 
                   operator in SQL          org tree
                                            (tenant root → region 
                                            → store)
```

The detailed query shapes (and how each mechanism fits into the 
final SQL) are in the "Resolver" section below.

## Resolver: `has_permission`

The resolver is a single targeted SQL query per check — NOT a 
permission-set enumeration. The query asks: "Does THIS user have 
THIS specific permission tuple, optionally with this target 
anchor?" The WHERE clause narrows to one (M, R, A, S); LIMIT 1 
short-circuits as soon as one matching role assignment is found.

This is a deliberate design choice. The alternative (build a full 
PermissionSet for the user, then check membership) was considered 
and rejected. Reasons:

- The hot path is gated requests, which always ask about one 
  specific tuple. Caching a full set adds memory and complexity 
  without buying anything.
- Each gate is one fast indexed query (sub-millisecond per 
  EXPLAIN ANALYZE).
- The single-tuple query benefits from `uq_permissions_tuple` 
  index lookup; full-set queries cannot.

### Query shape — PLATFORM path

```sql
SELECT 1
FROM core.platform_user_role_assignments pura
JOIN core.role_permissions rp 
  ON rp.role_id = pura.role_id
JOIN core.permissions p 
  ON p.id = rp.permission_id
WHERE pura.platform_user_id = :user_id
  AND pura.status = 'ACTIVE'
  AND p.module = :module
  AND p.resource = :resource
  AND p.action = :action
  AND p.scope = ANY(CAST(:satisfying_scopes AS permission_scope_enum[]))
LIMIT 1
```

The JOIN chain walks user → role → role_permissions → permissions. 
No anchor or module-access filtering for PLATFORM (Ithina staff 
administer modules across all tenants; their access is not gated 
by per-tenant module-enablement status).

### Query shape — TENANT path

```sql
SELECT 1
FROM core.tenant_user_role_assignments tura
JOIN core.role_permissions rp 
  ON rp.role_id = tura.role_id
JOIN core.permissions p 
  ON p.id = rp.permission_id
JOIN core.org_nodes on_
  ON on_.tenant_id = tura.tenant_id
  AND on_.id = tura.org_node_id
JOIN core.tenant_module_access tma
  ON tma.tenant_id = tura.tenant_id
  AND tma.module = p.module
WHERE tura.tenant_user_id = :user_id
  AND tura.status = 'ACTIVE'
  AND tma.status = 'ENABLED'
  AND p.module = :module
  AND p.resource = :resource
  AND p.action = :action
  AND p.scope = ANY(CAST(:satisfying_scopes AS permission_scope_enum[]))
  AND (
    :target_anchor IS NULL
    OR :target_anchor::ltree <@ on_.path
  )
LIMIT 1
```

Two additional JOINs vs PLATFORM:

- **org_nodes JOIN**: retrieves the assignment's anchor path 
  (`on_.path`). The composite key (`tenant_id + id`) enforces 
  tenant isolation per D-34.
- **tenant_module_access JOIN**: filters out grants in modules 
  the tenant has disabled. Prevents suspended-module permissions 
  from leaking through.

The anchor cascade clause uses Postgres's ltree `<@` operator 
("is descendant of, or equal to"). A grant anchored at tenant 
root covers any store under that tenant; a grant anchored at a 
specific region covers stores within that region only.

### Scope cascade implementation

The Python helper translates a requested scope into the list of 
scopes whose grants satisfy that check:

```python
_SCOPE_CASCADE_ORDER: tuple[str, ...] = (
    "GLOBAL",        # Platform — highest
    "TENANT",
    "BUSINESS_UNIT",
    "HQ",
    "COUNTRY",
    "REGION",
    "STORE",
    "DEPARTMENT",    # lowest
)

def satisfying_scopes(requested: PermissionScope) -> list[str]:
    """Return scopes whose grants satisfy a check at the requested 
    scope, via downward cascade. A grant at level N satisfies 
    checks at every level below N.
    """
    idx = _SCOPE_CASCADE_ORDER.index(requested.value)
    return list(_SCOPE_CASCADE_ORDER[: idx + 1])
```

Examples:

```
satisfying_scopes(GLOBAL) → ['GLOBAL']
satisfying_scopes(TENANT) → ['GLOBAL', 'TENANT']
satisfying_scopes(STORE)  → ['GLOBAL', 'TENANT', 'BUSINESS_UNIT', 
                             'HQ', 'COUNTRY', 'REGION', 'STORE']
```

The tuple lists all 8 hierarchy levels even though the v0 
`PermissionScope` enum has 3 values (GLOBAL, TENANT, STORE). The 
5 intermediate levels exist in the cascade order for 
forward-compatibility; they're inert in queries because no 
catalogue rows reference them.

A private companion (`_satisfying_scopes_for_sql`) intersects 
the helper output with the current enum's values before SQL 
binding. Postgres rejects out-of-enum strings at CAST time; the 
companion filters them out.

`get_permissions_for_user` (the broader query feeding 
`/me/permissions`) does NOT filter on scope — it returns raw 
grants. Cascade is the gate's concern; the broader query is the 
UI gating hint. The frontend applies cascade when rendering; 
`/me/can-do` is the server-authoritative cascade-aware check.

## RLS interplay

The gate enforces "is this user authorised for this action." 
Postgres RLS is the second line of defense: "are these rows in 
the user's visible scope." Both must pass for a request to land.

For reads:

- The gate allows or denies the request as a whole
- If allowed, RLS scopes the SELECT to visible rows
- Empty result is a legitimate outcome (caller authorised, no 
  matching rows visible to them)

For writes:

- **INSERT**: `tenant_id` must match the session GUC (or be in 
  the user's permission scope for PLATFORM-side inserts). The 
  RLS policy uses REJECT semantics in v0; mismatched tenant_id 
  raises at INSERT time.

- **UPDATE / DELETE**: Rows hidden by RLS are not affected by 
  the operation. The handler sees 0 rows affected when targeting 
  an RLS-invisible row. Handlers MUST check the affected-row 
  count and raise `*NotFoundError` (404) when 0 rows changed, to 
  surface the RLS-as-404 contract.

Implementation guidance for write handlers:

```python
# Use the session from get_tenant_session_dep — already has 
# RLS GUCs set
session: AsyncSession = Depends(get_tenant_session_dep)

# For UPDATE/DELETE, check rowcount; raise 404 if 0
result = await session.execute(update_stmt)
if result.rowcount == 0:
    raise TenantNotFoundError(tenant_id)

# For INSERT, let psycopg surface the constraint violation if 
# tenant_id mismatches; convert via FastAPI exception handler 
# to a clean error response
```

The two-layer model (gate + RLS) is deliberate redundancy:

- The gate ensures only authorised users reach the handler
- RLS ensures even authorised users can only affect rows in 
  their visible scope (defense in depth)
- A bug in either layer is caught by the other

## Gate: `require()` factory

The gate factory produces a FastAPI dependency that wraps 
`has_permission` and raises `PermissionDeniedError` on denial. 
Endpoints declare gates at the handler signature:

```python
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

### Factory signature

```python
def require(
    module: ModuleCode,
    resource: PermissionResource,
    action: PermissionAction,
    scope: PermissionScope,
    *,
    anchor_dep: Callable[..., Awaitable[str]] | None = None,
) -> Callable[..., Awaitable[None]]:
```

`anchor_dep` is keyword-only — at the endpoint declaration, the 
caller writes `anchor_dep=get_tenant_anchor` explicitly. The 
keyword-only constraint protects against positional confusion 
(scope_value vs anchor_dep_callable).

### Two inner-function shapes

The factory produces two distinct inner gate functions based on 
whether `anchor_dep` was supplied. FastAPI introspects each 
dependency's signature at app startup; the signature must be 
static (cannot conditionally include a `Depends()` parameter).

```python
# Shape 1: no anchor dep
async def gate(
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> None:
    # passes target_anchor=None to has_permission

# Shape 2: with anchor dep
async def gate(
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
    target_anchor: str = Depends(anchor_dep),
) -> None:
    # passes resolved target_anchor to has_permission
```

The factory selects which inner function to return based on the 
`anchor_dep` parameter.

### Gate marker

Each gate function carries a `__permission_gate__` attribute 
attached after the factory builds the inner function. The marker 
is a frozen dataclass:

```python
@dataclass(frozen=True)
class PermissionGateInfo:
    module: ModuleCode
    resource: PermissionResource
    action: PermissionAction
    scope: PermissionScope
    anchor_dep: Callable[..., Awaitable[str]] | None
```

The marker exists for the mandatory-gate-discipline meta-test 
(below). It captures the full tuple plus the anchor dep reference 
so that test introspection can verify exact gate semantics, not 
just gate presence.

### Two-layer gate: optional `audience` parameter

The `require()` factory accepts an optional `audience` keyword
(Step 6.11.1):

```python
def require(
    module: ModuleCode,
    resource: PermissionResource,
    action: PermissionAction,
    scope: PermissionScope,
    *,
    anchor_dep: Callable | None = None,
    audience: Literal["PLATFORM", "TENANT"] | None = None,
) -> Callable[..., Awaitable[None]]:
```

When `audience` is set, the gate body asserts `auth.user_type == audience`
BEFORE running `has_permission()`. On audience mismatch the gate raises
`PlatformAudienceRequiredError` (code `PLATFORM_AUDIENCE_REQUIRED`, 403),
ahead of the permission check.

This is defense-in-depth, not a replacement for the permission
check. A typical seeded catalogue already aligns grant audience
with scope. The audience parameter catches the catalogue-drift
case: if a future seed change ever leaks a `.GLOBAL`-scoped grant
to a TENANT-audience role, write endpoints with
`audience="PLATFORM"` continue to refuse at Layer 1.

`audience=None` (default) preserves all existing call sites
unchanged.

Endpoints exclusively administering platform-wide resources (e.g.,
the tenants write surface at Step 6.11) declare `audience="PLATFORM"`
on every route. The mandatory-gate-discipline meta-test enforces this
on the four tenants-write routes via the marker's `audience` field.

#### Worked example: writing the two-layer gate

```python
Depends(require(
    ModuleCode.ADMIN,
    PermissionResource.TENANTS,
    PermissionAction.CONFIGURE,
    PermissionScope.GLOBAL,
    audience="PLATFORM",
))
```

Order of checks at request time (live runtime order — FastAPI Depends
resolution happens BEFORE the gate body runs, so an anchor_dep miss
raises 404 ahead of either gate-body check):

```
1. JWT verified by auth middleware           → 401 NOT_AUTHENTICATED
2. FastAPI resolves Depends() parameters on the gate function:
     a. get_auth_context        (auth)
     b. get_tenant_session_dep  (session)
     c. anchor_dep (if set)     → 404 *NotFoundError on miss
3. Gate body runs:
     a. audience check (Layer 1; if set)     → 403 PLATFORM_AUDIENCE_REQUIRED
     b. has_permission(...)     (Layer 2)    → 403 PERMISSION_DENIED
4. Handler runs.
```

The audience check fires only inside the gate body — never before
Depends resolution — so a malformed anchor lookup still surfaces as
404 even if the audience would also have rejected the caller.

#### `audience=None` — multi-audience endpoints

When `audience` is omitted (or explicitly `None`), the gate skips
Layer 1 entirely; both PLATFORM and TENANT JWTs are eligible to pass
Layer 2. This is the right shape for endpoints where the underlying
resource is per-tenant but both audiences may legitimately operate
on it — for example, `/api/v1/tenant-users` writes (Step 6.10.1),
where Ithina staff (PLATFORM) and tenant OWNER (TENANT) both manage
a tenant's users. The TENANT-side tenant-isolation guarantee is
provided by RLS on the underlying table; Layer 2 still narrows
authority to those with the gate permission tuple
(`ADMIN.USERS.CONFIGURE.TENANT`).

Self-edit semantics are handler-side, not gate-side. When a
multi-audience endpoint must forbid a TENANT caller from operating
on themselves (e.g., suspending one's own account), the handler
inserts a guard after `Depends(require(...))` resolves but before
the repo call. See the "Worked example: PATCH /tenant-users/{id}"
section below for the canonical multi-audience-with-self-edit-guard
shape.

## Anchor dependencies

Per-resource anchor lookup functions live at 
`src/admin_backend/auth/anchor_deps.py`. Three functions at HEAD:

```python
get_tenant_anchor(tenant_id, session) -> str
  Returns the tenant's root org_node.path.
  Used by /tenants/{id}, /tenants/{id}/org-tree.
  Raises TenantNotFoundError (404) on miss.

get_org_node_anchor(tenant_id, node_id, session) -> str
  Returns a specific node's path within a tenant.
  Used by /tenants/{id}/org-nodes/{nid}/children.
  Raises OrgNodeNotFoundError (404) on miss.

get_tenant_user_anchor(user_id, session) -> str
  Returns the user's tenant-root path (TenantUser has no 
  home_org_node_id at HEAD; defaults to tenant root).
  Used by /tenant-users/{user_id}.
  Raises TenantUserNotFoundError (404) on miss.
```

The 404-on-miss invariant is security-critical. If anchor deps 
returned `None` to signal "not found," the gate's cascade clause 
would short-circuit to TRUE (no target_anchor → no anchor 
constraint → grant matches). The miss-returns-404 contract 
ensures the gate never runs with `target_anchor=None` for an 
endpoint that requires anchor scoping.

The 404 also surfaces RLS-as-404 cleanly: when a cross-tenant 
request asks for a row in another tenant, RLS hides the row, the 
anchor dep returns nothing, and the user sees 404 — they cannot 
distinguish "row doesn't exist" from "row exists but you can't 
see it."

### Anchor dependencies for write operations

The anchor pattern adapts cleanly for writes, with one important 
asymmetry: CREATE operations anchor on the PARENT context, not 
on the resource being created (which doesn't exist yet).

```
UPDATE on /resource/{id} (PUT, PATCH):
  Anchor dep looks up the id's existing row's path.
  404 if id doesn't exist or RLS-invisible.
  Pattern: anchor_dep=get_<resource>_anchor
  
  Example: PUT /tenants/{tenant_id}
    anchor_dep=get_tenant_anchor

DELETE on /resource/{id}:
  Same as UPDATE — anchor is the resource being deleted.

CREATE on /parent/{parent_id}/resource (POST):
  Anchor dep looks up the PARENT's path.
  The new resource will be created under that anchor.
  404 if parent_id doesn't exist or RLS-invisible.
  Pattern: anchor_dep=get_<parent_resource>_anchor
  
  Example: POST /tenants/{tenant_id}/stores
    anchor_dep=get_tenant_anchor    (not get_store_anchor)

Top-level CREATE (e.g., POST /tenants — Ithina staff creates a 
new tenant):
  No anchor — operation is at GLOBAL scope.
  Gate: require(ADMIN, TENANTS, CONFIGURE, GLOBAL)
  Anchor cascade clause in has_permission becomes TRUE when 
  target_anchor is None.
```

A new resource type that doesn't exist yet (e.g., a new STORES 
endpoint group in Stage 2) may need a new anchor dep function. 
Add it to `auth/anchor_deps.py` following the existing pattern: 
single-row indexed lookup, raises `*NotFoundError` (404) on miss, 
never returns None.

## Error contract

`PermissionDeniedError` (in `errors.py`) is the system-wide gate 
denial error. Subclass of `ClientError`.

```python
class PermissionDeniedError(ClientError):
    http_status = 403
    code = "PERMISSION_DENIED"
    
    def __init__(self, detail: str, **context):
        # context includes module, resource, action, scope, 
        # target_anchor, reason_code — passed as kwargs
        super().__init__(detail, **context)
```

Response envelope:

```json
{
  "code": "PERMISSION_DENIED",
  "message": "Permission denied",
  "details": null,
  "request_id": "..."
}
```

The structured context fields (module, resource, action, scope, 
target_anchor, reason_code) are NOT surfaced to the client via 
`details`. They reach application logs via `exc.context`. Two 
reasons:

- Defensive posture: less information about what was attempted is 
  better when denying access.
- Frontend knows its own request context locally; doesn't need 
  server-supplied details to render appropriate UI.

`reason_code` is a `ReasonCode` enum:

```
GRANT_MATCHED                     (used by /me/can-do for allow)
NO_MATCHING_GRANT_OR_OUT_OF_SCOPE (used for deny — does not 
                                    distinguish which specific 
                                    constraint failed, to avoid 
                                    leaking information)
```

### Server errors

5xx errors (database errors, unexpected exceptions, etc.) follow 
the same `{code, message, details, request_id}` envelope. Standard 
exception handlers in `main.py` convert exceptions to the envelope. 
Stage 2 write handlers should:

- Let `psycopg`/SQLAlchemy errors propagate to the default handler 
  (returns 500 with generic message; details logged server-side)
- Raise domain-specific errors for known conditions:
  - Unique constraint violation → 409 Conflict
  - Foreign-key violation → 422 Unprocessable Entity or 409
  - Resource not found / RLS-invisible → 404 NotFound subclass
- NEVER catch+swallow without surfacing — partial writes that 
  silently fail are harder to debug than clean 500s

The `request_id` in every error envelope correlates client-side 
errors with server-side logs for debugging.

## Mandatory-gate-discipline meta-test

The structural guarantee that prevents endpoints from shipping 
ungated by accident:

```python
def test_gate_discipline_every_route_is_gated_or_allowlisted() -> None:
    app = create_app()
    allowed_paths = GATE_EXEMPT_PATHS | PUBLIC_PATHS
    ungated_routes = []
    
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in allowed_paths:
            continue
        has_gate = any(
            hasattr(dep.call, "__permission_gate__")
            for dep in route.dependant.dependencies
        )
        if not has_gate:
            ungated_routes.append(route.path)
    
    assert not ungated_routes, (
        f"Routes neither gated nor allowlisted: {ungated_routes}"
    )
```

Test passes if every `APIRoute` is either:

1. **Gated** — one of its dependencies' `.call` carries the 
   `__permission_gate__` marker
2. **Allowlisted** — path appears in `GATE_EXEMPT_PATHS` 
   (`auth/gate_allowlist.py`) or `PUBLIC_PATHS` 
   (`middleware/auth.py`)

A new endpoint added without either fails this test, blocking 
the build.

### Allowlist categories

```
PUBLIC_PATHS (no auth, no gate):
  /api/v1/health, /api/v1/ready, /api/v1/openapi.json,
  /api/v1/docs, /api/v1/redoc, /metrics

GATE_EXEMPT_PATHS (auth required, no gate):
  /api/v1/me/permissions          caller-state endpoint
  /api/v1/me/can-do               server-authoritative check
  /api/v1/lookups                 reference data
  /api/v1/permissions             permission catalogue
  /api/v1/permission-matrix       role × permission matrix
  /api/v1/roles                   role catalogue
  /api/v1/roles/{role_id}/permissions
```

5 endpoints exempt for product reasons (read-only catalogue data, 
caller-state queries). 2 from `/me/*` (gate-bypass by design — 
the endpoints' authorization is "is this YOUR data," not 
"are you authorized for permission X").

### Allowlist coupling

The mandatory-gate-discipline test depends on three frozensets 
that must stay in sync:

- `PUBLIC_PATHS` at `middleware/auth.py` (auth-skip layer)
- `GATE_EXEMPT_PATHS` at `auth/gate_allowlist.py` (gate-skip layer)
- Implicit: every new endpoint must land in one of these OR carry 
  a gate

Adding an endpoint without matching any of these is a deploy-time 
error by design. The discipline test catches it; CI blocks the 
deploy.

## Adding a new endpoint (cookbook)

Step-by-step recipe for shipping a new endpoint (read OR write):

```
1. PICK THE PERMISSION TUPLE
   - Module: which area of the system (ADMIN, PRICING_OS, ...)
   - Resource: which entity (TENANTS, STORES, USERS, ...)
   - Action: GET → VIEW; write → CONFIGURE/APPROVE/EXECUTE/OVERRIDE
     (see "Action semantics" table above)
   - Scope: lowest scope appropriate for the endpoint's audience
     - TENANT-scope for endpoints accessed by tenant admins
     - GLOBAL-scope ONLY for endpoints accessed by Ithina staff 
       exclusively
   - Cross-check the tuple exists in core.permissions

2. PICK THE ANCHOR DEPENDENCY (if applicable)
   - Endpoint operates on a specific resource id (singleton 
     read, UPDATE, DELETE)? → anchor dep for the resource id
   - CREATE operation on /parent/{parent_id}/resource? → anchor 
     dep for the PARENT id (not the resource being created)
   - List, aggregate, or top-level CREATE? → no anchor dep
   - New resource type? → add a new function to 
     auth/anchor_deps.py (single-row indexed lookup, 
     raises *NotFoundError on miss, never returns None)

3. WRITE THE HANDLER
   - Add Depends(require(...)) with the tuple + anchor_dep
   - Standard dependencies: auth (get_auth_context), session 
     (get_tenant_session_dep)
   - For writes: request body via Pydantic model
   - Return appropriate status:
     - 200 for read or successful UPDATE
     - 201 for CREATE
     - 204 for DELETE
   - RLS scopes queries via session GUCs; for writes, check 
     rowcount and raise 404 on RLS-invisible targets

4. CATALOGUE COVERAGE CHECK
   - Does the chosen tuple already exist in seed?
   - Do the appropriate roles already hold it?
   - If yes: proceed
   - If no: operator Phase X seed update (Excel + local DB + 
     Cloud SQL UPSERT). Test fixtures may need xfail markers 
     until the seed update lands; remove markers in a small 
     follow-up commit (see Step 6.9.3.2 cleanup precedent)

5. UPDATE SMOKE SCRIPTS
   - scripts/smoke_curl.sh (quick local smoke)
   - scripts/test_endpoints.sh (full local integration)
   - scripts/test_endpoints_cloud.sh (cloud verification 
     post-deploy)

6. VERIFY DISCIPLINE
   - Run pytest — mandatory-gate-discipline catches the new 
     route automatically
   - If the new route is intentionally ungated (e.g., new 
     caller-state endpoint similar to /me/*), add path to 
     GATE_EXEMPT_PATHS in auth/gate_allowlist.py
```

### Response envelope conventions

| Operation             | Status | Body shape                          |
|-----------------------|--------|-------------------------------------|
| GET list              | 200    | `{items: [...], pagination: {...}}` |
| GET singleton         | 200    | `{resource fields...}`              |
| POST create           | 201    | `{resource fields with new id}`     |
| POST action           | 200    | `{result fields}` OR 204            |
| PUT update            | 200    | `{updated resource fields}`         |
| PATCH partial update  | 200    | `{updated resource fields}`         |
| DELETE                | 204    | (no body)                           |
| `/me/permissions`     | 200    | `{permissions: [...]}`              |
| `/me/can-do`          | 200    | `{allowed: bool, reason_code: str}` |

Error responses (4xx/5xx) always carry:

```json
{"code": "...", "message": "...", "details": null, "request_id": "..."}
```

### Worked example: PUT /tenants/{tenant_id}

A complete write endpoint, showing all the pieces together:

```python
from uuid import UUID
from fastapi import Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.permissions import require
from admin_backend.auth.anchor_deps import get_tenant_anchor
from admin_backend.auth.context import AuthContext, get_auth_context
from admin_backend.dependencies import get_tenant_session_dep
from admin_backend.errors import TenantNotFoundError
from admin_backend.models.enums import (
    ModuleCode, 
    PermissionResource, 
    PermissionAction, 
    PermissionScope,
)
from admin_backend.schemas.tenants import (
    TenantUpdateRequest, 
    TenantDetailResponse,
)
from admin_backend.repositories.tenants import TenantsRepo


@router.put("/{tenant_id}")
async def update_tenant(
    tenant_id: UUID,
    body: TenantUpdateRequest,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        anchor_dep=get_tenant_anchor,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> TenantDetailResponse:
    """Update a tenant's mutable fields.
    
    Gated on ADMIN.TENANTS.CONFIGURE.TENANT. PLATFORM users pass 
    via cascade from .CONFIGURE.GLOBAL. TENANT users need direct 
    .CONFIGURE.TENANT grant (OWNER role).
    """
    repo = TenantsRepo(session)
    updated = await repo.update(tenant_id, body)
    
    if updated is None:
        # Either tenant_id doesn't exist OR RLS-invisible.
        # Surface as 404 (RLS-as-404 invariant).
        raise TenantNotFoundError(tenant_id)
    
    return TenantDetailResponse.from_orm(updated)
```

What this example demonstrates:

- Gate placement: handler signature, before all other dependencies
- Anchor dep for the resource being updated
- Standard error pattern: 404 when RLS hides or row missing
- Standard response shape: 200 with the updated resource
- Reuses the same `require()` factory as reads — no write-specific 
  authorisation surface

### Worked example: POST /tenants/{tenant_id}/stores

A CREATE operation showing the anchor-on-parent pattern:

```python
@router.post(
    "/{tenant_id}/stores",
    status_code=status.HTTP_201_CREATED,
)
async def create_store(
    tenant_id: UUID,
    body: StoreCreateRequest,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.STORES,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        anchor_dep=get_tenant_anchor,  # parent's anchor, not store's
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> StoreDetailResponse:
    """Create a new store under the given tenant.
    
    Anchor is the tenant root (the parent context), not the store 
    being created (which doesn't exist yet). The gate verifies the 
    user can administer stores under THIS tenant.
    """
    repo = StoresRepo(session)
    created = await repo.create(tenant_id, body)
    return StoreDetailResponse.from_orm(created)
```

### Worked example: POST /tenants (create + bundled module enablement)

Platform-only CREATE on a top-level resource with bundled writes
into a related table in the same transaction (Step 6.11.2):

```python
@router.post(
    "",
    response_model=TenantDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant(
    body: TenantCreateRequest,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.CONFIGURE,
        PermissionScope.GLOBAL,
        audience="PLATFORM",
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Provision a new tenant. Platform-only.

    Forces status=TRIAL on insert. ADMIN module force-merged into
    modules_enabled (schema validator). tenant_module_access rows
    inserted in the same transaction.

    Gated on ADMIN.TENANTS.CONFIGURE.GLOBAL (SUPER_ADMIN,
    PLATFORM_ADMIN). audience="PLATFORM" rejects TENANT JWTs at
    Layer 1 with 403 PLATFORM_AUDIENCE_REQUIRED before the
    permission check.
    """
    row = await _repo.create(session, **body.model_dump(), actor_user_id=auth.user_id)
    return _detail_from_row(row)
```

What this demonstrates: `audience="PLATFORM"` + `scope=GLOBAL`; no
anchor_dep; top-level CREATE on empty-path route; cross-table writes
in one transaction; status server-forced; 409
`DUPLICATE_TENANT_NAME` emerges from a domain-level uniqueness check
(app-layer; no DB UNIQUE on `tenants.name` in v0 — see CLAUDE.md
FN-AB).

### Worked example: PATCH /tenants/{tenant_id} (platform-only attributes update)

Platform-only partial update. PATCH on this resource is
platform-only in v0; multi-audience PATCH (TENANT OWNER editing
own tenant) is deferred — `tenants` uses Pattern (a) typed
FKs to `platform_users` for audit columns (per D-13), which blocks
TENANT-side UPDATE at the FK layer. Future post-6.16 step migrates
those columns to Pattern (b) and adds the multi-audience surface.

```python
@router.patch("/{tenant_id}", response_model=TenantDetail)
async def patch_tenant(
    tenant_id: UUID,
    body: TenantPatchRequest,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.CONFIGURE,
        PermissionScope.GLOBAL,
        audience="PLATFORM",
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Partial update of a tenant. Allowed in any non-TERMINATED
    state including SUSPENDED.

    Same gate as POST /tenants. Status transitions go through
    /suspend and /activate; extra="forbid" rejects status in body.

    Empty body -> 422 EMPTY_PATCH. Name-rename runs the same
    uniqueness pre-check the create path uses (excludes self).
    """
    sent_fields = body.model_dump(exclude_unset=True)
    if not sent_fields:
        raise EmptyPatchError(...)

    updated = await _repo.update(
        session, tenant_id, fields=sent_fields, actor_user_id=auth.user_id,
    )
    if updated is None:
        raise TenantNotFoundError(...)
    return _detail_from_row(updated)
```

What this demonstrates: same gate tuple as POST; `exclude_unset=True`
on Pydantic dump (only sent fields hit UPDATE SQL); empty body ->
422; name-rename uniqueness in repo (excludes self by id);
RLS-as-404 per D-17; "PATCH allowed on SUSPENDED" is a domain
invariant, not a gate concern.

### Worked example: POST /tenants/{tenant_id}/suspend and /activate

Named transition endpoints sharing the same gate tuple
(`OVERRIDE.GLOBAL`); differ only in target state and allowed
sources. SUPER_ADMIN holds `ADMIN.TENANTS.OVERRIDE.GLOBAL`;
PLATFORM_ADMIN holds `ADMIN.TENANTS.CONFIGURE.GLOBAL` (create/edit
only) and is refused on these transitions by Layer 2.

```python
@router.post("/{tenant_id}/suspend", response_model=TenantDetail)
async def suspend_tenant(
    tenant_id: UUID,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.OVERRIDE,
        PermissionScope.GLOBAL,
        audience="PLATFORM",
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Transition TRIAL|ACTIVE -> SUSPENDED.

    Gated on ADMIN.TENANTS.OVERRIDE.GLOBAL — held only by
    SUPER_ADMIN. PLATFORM_ADMIN can create/edit (CONFIGURE.GLOBAL)
    but cannot suspend/activate (OVERRIDE.GLOBAL). The catalogue
    encodes the privilege distinction.
    """
    row, result = await _repo.transition(
        session, tenant_id, target_status="SUSPENDED",
        actor_user_id=auth.user_id,
    )
    if result is TransitionResult.NOT_FOUND:
        raise TenantNotFoundError(...)
    if result is TransitionResult.INVALID_STATE:
        raise InvalidStateTransitionError(...)
    return _detail_from_row(row)


@router.post("/{tenant_id}/activate", response_model=TenantDetail)
async def activate_tenant(
    tenant_id: UUID,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.OVERRIDE,
        PermissionScope.GLOBAL,
        audience="PLATFORM",
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Transition TRIAL|SUSPENDED -> ACTIVE.

    Activate from SUSPENDED clears suspended_at and
    suspended_by_user_id atomically with the status flip. From
    TRIAL is forward-only progression.

    SUSPENDED -> ACTIVE never lands back in TRIAL.
    """
    row, result = await _repo.transition(
        session, tenant_id, target_status="ACTIVE",
        actor_user_id=auth.user_id,
    )
    if result is TransitionResult.NOT_FOUND:
        raise TenantNotFoundError(...)
    if result is TransitionResult.INVALID_STATE:
        raise InvalidStateTransitionError(...)
    return _detail_from_row(row)
```

Lifecycle:

```
              suspend                          activate
            ┌──────────────► SUSPENDED ──────────────┐
            │                                         ▼
       TRIAL ─────────────── activate ───────────► ACTIVE
            │                                         │
            └─────────────── suspend  ────────────────┘
```

| From      | suspend                          | activate                         |
|-----------|----------------------------------|----------------------------------|
| TRIAL     | -> SUSPENDED                     | -> ACTIVE                        |
| ACTIVE    | -> SUSPENDED                     | 409 `INVALID_STATE_TRANSITION`   |
| SUSPENDED | 409 `INVALID_STATE_TRANSITION`   | -> ACTIVE                        |

What this demonstrates: named transition endpoints (not
status-in-PATCH); `OVERRIDE` action per catalogue privilege
topology; one activate endpoint covers undo-of-suspend and
forward-progression; repo returns `(row, TransitionResult)` to
distinguish NOT_FOUND from INVALID_STATE; audit-actor columns
populated/cleared atomically with status.

### Worked example: POST /module-access/{tenant_id}/{module_code}/enable and /disable (platform-only with upsert seam)

Two named transition endpoints sharing the same gate tuple as
tenant suspend/activate (`ADMIN.TENANTS.OVERRIDE.GLOBAL`,
SUPER_ADMIN only). Differ from tenant suspend/activate on three
axes: the enable endpoint has an upsert seam (creates a row when
no `tenant_module_access(tenant_id, module)` exists), no-op cases
return 200 idempotently (no 409 INVALID_STATE_TRANSITION), and
the resource lives under the reads' URL prefix
(`/module-access/`) rather than under the affecting parent
(`/tenants/{id}/...`).

The URL convention divergence is intentional: when reads of a
resource live at a non-parent prefix, writes follow the reads so
the resource has one URL prefix end to end. When reads live at
the parent prefix (`/tenants/{id}` for tenant suspend/activate),
writes nest under the parent. The 6.7 reads at
`/module-access/...` set the precondition that drove this
choice; do not extrapolate without checking the read posture.

```python
@router.post(
    "/{tenant_id}/{module_code}/enable",
    response_model=ModuleAccessRead,
)
async def enable_module_for_tenant(
    tenant_id: UUID,
    module_code: ModuleCode,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.OVERRIDE,
        PermissionScope.GLOBAL,
        audience="PLATFORM",
        anchor_dep=get_tenant_anchor,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Enable module for tenant; upserts if no row exists.

    Idempotent: enable on already-ENABLED is 200 with no row
    mutation. DISABLED to ENABLED overwrites enabled_at and
    clears disabled_at atomically per the DDL CHECK pair.
    """
    row = await _repo.enable(
        session, tenant_id, module_code,
        actor_user_id=auth.user_id,
    )
    return ModuleAccessRead.model_validate(row)


@router.post(
    "/{tenant_id}/{module_code}/disable",
    response_model=ModuleAccessRead,
)
async def disable_module_for_tenant(
    tenant_id: UUID,
    module_code: ModuleCode,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.OVERRIDE,
        PermissionScope.GLOBAL,
        audience="PLATFORM",
        anchor_dep=get_tenant_anchor,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Disable module for tenant; 404 if no row exists.

    Idempotent: disable on already-DISABLED is 200 with no row
    mutation. ENABLED to DISABLED sets disabled_at and
    disabled_by_user_id atomically per the DDL CHECK pair;
    enabled_at carries forward as the historical record of when
    the just-ended ENABLED stint began.
    """
    row, result = await _repo.disable(
        session, tenant_id, module_code,
        actor_user_id=auth.user_id,
    )
    if result is TransitionResult.NOT_FOUND:
        raise ModuleAccessNotFoundError(
            "module access not found",
            tenant_id=str(tenant_id),
            module_code=module_code.value,
        )
    assert row is not None
    return ModuleAccessRead.model_validate(row)
```

Transition matrix:

| Current row state | enable                                                    | disable                                                      |
|-------------------|-----------------------------------------------------------|--------------------------------------------------------------|
| missing           | INSERT, 200                                               | 404 `MODULE_ACCESS_NOT_FOUND`                                |
| DISABLED          | UPDATE to ENABLED (overwrites enabled_at, clears disabled_*), 200 | 200 no-op                                                    |
| ENABLED           | 200 no-op                                                 | UPDATE to DISABLED (sets disabled_*, preserves enabled_at), 200 |

Access cascade is structural, not imperative.
`has_permission()`'s TENANT path JOINs `tenant_module_access`
and filters `status='ENABLED'`. A DISABLED row fails the JOIN;
every TENANT-side permission check against that module returns
false on the next request. The role assignment table is never
touched by these endpoints. Re-enable restores access
automatically without re-granting roles (D-24 per-request
resolution makes this safe).

What this demonstrates: same gate tuple can drive different
matrices (suspend/activate strict-409; module-access
idempotent-200) when the resource's audit-trail and operational
profile differ; URL convention follows the reads when reads do
not live under an affecting parent; structural cascade via JOIN
filter beats imperative cascade writes for permission
revocation.

### Worked example: POST /tenant-users (multi-audience create with bundled role assignments)

Step 6.10.1. Multi-audience: both PLATFORM (Ithina staff) and TENANT
(tenant OWNER) JWTs may call this endpoint, subject to the
`ADMIN.USERS.CONFIGURE.TENANT` gate.

```python
@router.post(
    "",
    response_model=TenantUserRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant_user(
    body: TenantUserCreateRequest,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.USERS,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        # No audience kwarg — multi-audience.
        # No anchor_dep — tenant_id is in the body, not the path.
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    row = await _repo.create(
        session,
        tenant_id=body.tenant_id,
        email=body.email,
        full_name=body.full_name,
        role_ids=list(body.roles),
        actor_user_id=auth.user_id,
        actor_user_type=_actor_type_from_auth(auth),
    )
    if row is None:
        raise TenantNotFoundError(...)
    return _detail_from_row(row)
```

Order of checks at request time (the tenant_users contract is the
canonical multi-audience write surface; see Step 6.11.2 worked
example for the platform-only variant):

```
1. ASGI middleware           →  auth context attached (or 401)
2. FastAPI dependency resolution graph:
   a. Body parsed + Pydantic →  TenantUserCreateRequest
                                 - email lowercased (validator)
                                 - full_name length 1-200
                                 - roles non-empty + deduped
                                 422 surfaces here if body invalid.
   b. AuthContext             →  via Depends(get_auth_context)
   c. AsyncSession            →  via Depends(get_tenant_session_dep)
                                  (app.tenant_id, app.user_type set)
   d. Gate body               →  Depends(require(...))
                                  Layer 1 skipped (no audience)
                                  Layer 2 has_permission() check
3. Handler body runs          →  repository call
                                  - role audience pre-check (Option X)
                                    surfaces as 422 INVALID_ROLE or
                                    INVALID_ROLE_AUDIENCE on mismatch.
                                  - tenant-root anchor lookup is
                                    RLS-scoped; cross-tenant target
                                    from a TENANT JWT returns 404
                                    TENANT_NOT_FOUND (RLS-as-404).
                                  - INSERT tenant_users + N
                                    INSERTs into
                                    tenant_user_role_assignments
                                    anchored at the tenant root.
```

The role-audience pre-check fires app-side (locked decision: Option X,
handler-shape (b)) — the DB trigger `enforce_tenant_role_audience`
otherwise raises a plpgsql exception that would surface as 500. The
pre-check converts the rejection into a domain-shaped 422 with the
invalid role IDs carried in `exc.context` per the Q7 envelope lock
(structured detail in logs only; response `details` field stays
`null`).

Repository behavior (single transaction):

```
1. Pre-check role audience:
     SELECT id, audience::text FROM core.roles
      WHERE id = ANY(:role_ids)
   - Missing id → InvalidRoleError (422)
   - audience != 'TENANT' → InvalidRoleAudienceError (422)
2. Lookup tenant root org_node:
     SELECT id FROM core.org_nodes
      WHERE tenant_id = :t AND node_type = 'TENANT'
        AND parent_id IS NULL
   - 0 rows → TenantNotFoundError (404)  (RLS-as-404)
3. Pre-check email uniqueness within tenant.
4. INSERT core.tenant_users with status='INVITED',
   auth0_sub=NULL, invitation_accepted_at=NULL, Pattern (b)
   audit-actor pair populated.
5. INSERT N core.tenant_user_role_assignments rows, all anchored at
   tenant_root_id, status='ACTIVE', Pattern (b) granted_* pair
   populated.
```

### Worked example: PATCH /tenant-users/{user_id} (multi-audience update with self-edit guard)

Step 6.10.1. The canonical multi-audience-with-self-edit-guard
shape: PLATFORM and TENANT both pass the gate; a TENANT caller
cannot operate on themselves.

```python
@router.patch(
    "/{user_id}",
    response_model=TenantUserRead,
)
async def patch_tenant_user(
    user_id: UUID,
    body: TenantUserPatchRequest,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.USERS,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        anchor_dep=get_tenant_user_anchor,
        # No audience kwarg — multi-audience.
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    _raise_if_self_edit(auth, user_id)

    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise EmptyPatchError(...)

    row = await _repo.update(
        session, user_id,
        fields=fields,
        actor_user_id=auth.user_id,
        actor_user_type=_actor_type_from_auth(auth),
    )
    if row is None:
        raise TenantUserNotFoundError(...)
    return _detail_from_row(row)
```

**Self-edit guard placement.** The guard runs **after** Layer 2
`has_permission` passes (the user has `CONFIGURE.TENANT`) but
**before** the repo call. It is handler-layer logic, not gate logic
— the gate factory has no knowledge of "subject of action vs actor
of action." The guard fires for TENANT callers only; a PLATFORM
SUPER_ADMIN editing a tenant user cannot be self-editing (PLATFORM
users live in `platform_users`, a different table).

```python
def _raise_if_self_edit(auth: AuthContext, user_id: UUID) -> None:
    if auth.user_type == "TENANT" and auth.user_id == user_id:
        raise SelfEditForbiddenError(...)
```

**Role replace-set semantics (Step 6.10.1; superseded at Step 6.14).** Step 6.10.1 originally implemented a whole-set replace: every existing ACTIVE assignment went INACTIVE on PATCH and the desired set INSERTed as new ACTIVE rows. Step 6.14 retires this in favor of diff-replace; see "Note on diff-replace (Step 6.14)" below.

Both old (now INACTIVE) and new (now ACTIVE) rows continue to appear in `/tenant-users/{id}.roles[]` — the inline list returns all assignments regardless of status, in `granted_at DESC, id ASC` order. Frontend filters as needed.

**Note on diff-replace (Step 6.14).** The `roles` field on POST and PATCH `/tenant-users` uses diff-replace semantics. Each request body item carries `(role_id, org_node_id)` rather than a bare role UUID (Step 6.10.1 retired). The submitted list is the desired complete set of ACTIVE assignments after the operation. Tuples present in both the current and desired sets are preserved verbatim, including their `granted_at` and `granted_by_*` audit columns. Only tuples in `(current − desired)` flip to INACTIVE; only tuples in `(desired − current)` INSERT as new ACTIVE rows. PATCH `roles: []` revokes every current ACTIVE assignment; PATCH with `roles` omitted leaves them untouched.

Tenant-root-only anchoring is retired with this step: any non-archived org_node in the same tenant is acceptable as an anchor. Step 6.14's `_validate_org_nodes` pre-check converts missing / archived / cross-tenant org_nodes into a 422 `INVALID_ORG_NODE` ahead of the composite-FK reject at INSERT time; the composite FK remains the structural guard. Within-request duplicates `(role_id, org_node_id)` reject as 422 `DUPLICATE_ROLE_ASSIGNMENT_IN_REQUEST` ahead of the partial-UNIQUE index `uq_tenant_user_role_assignments_active`. Concurrent edits that race past `SELECT FOR UPDATE` and hit the partial-UNIQUE on INSERT surface as 409 `ROLE_ASSIGNMENT_CONFLICT`; the catch is scoped to the constraint name so other IntegrityErrors (cross-tenant FK reject, NOT NULL violation, audience-trigger reject) propagate and surface as 500.

### Worked example: POST /tenant-users/{user_id}/suspend and /activate (multi-audience transitions)

Step 6.10.1. Same multi-audience + self-edit-guard shape as PATCH.

```python
@router.post("/{user_id}/suspend", response_model=TenantUserRead)
async def suspend_tenant_user(
    user_id: UUID,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.USERS,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        anchor_dep=get_tenant_user_anchor,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    _raise_if_self_edit(auth, user_id)

    row, result = await _repo.transition(
        session, user_id,
        target_status="SUSPENDED",
        actor_user_id=auth.user_id,
        actor_user_type=_actor_type_from_auth(auth),
    )
    if result is TransitionResult.NOT_FOUND:
        raise TenantUserNotFoundError(...)
    if result is TransitionResult.INVALID_STATE:
        raise InvalidStateTransitionError(...)
    return _detail_from_row(row)
```

**Transition lifecycle for `core.tenant_users`:**

```
[create] ──► INVITED ──[Auth0 invite-accept; out of scope]──► ACTIVE
                                                              ▲ │
                                                         activate │ suspend
                                                              │ ▼
                                                           SUSPENDED
```

| From      | suspend                          | activate                         |
|-----------|----------------------------------|----------------------------------|
| INVITED   | 409 `INVALID_STATE_TRANSITION`   | 409 `INVALID_STATE_TRANSITION`   |
| ACTIVE    | -> SUSPENDED                     | 409 `INVALID_STATE_TRANSITION`   |
| SUSPENDED | 409 `INVALID_STATE_TRANSITION`   | -> ACTIVE                        |

Key differences from the tenants (Step 6.11) transition matrix:

- INVITED is a real source state for tenant_users (created INVITED
  via POST, becomes ACTIVE via Auth0 invite-accept callback, which is
  Stage 3 territory). The app layer maps INVITED ->
  {SUSPENDED, ACTIVE} to 409 `INVALID_STATE_TRANSITION` so the client
  never sees a 500 from `ck_tenant_users_auth0_sub_consistency`.

  Auth0 provisioning (Step CI-4b): after the INVITED row commits, a
  post-commit FastAPI background task provisions the Auth0 user (with
  app_metadata `{tenant_id, user_type, user_id}`) and issues an
  invitation ticket via the Auth0 Management client. The task runs
  AFTER the response and after the session commit-in-teardown, so the
  row is durably committed first. It is fail-safe: an Auth0 failure
  leaves the row a valid re-provisionable INVITED user (logged, not
  rolled back, not surfaced to the client; 201-before-provisioning is
  the accepted observable behavior). It does NOT write `auth0_sub`
  (forbidden while INVITED); recording `auth0_sub` and INVITED->ACTIVE
  is the invite-accept callback, Step 4c. Platform-user provisioning is
  a separate future question (no platform_users create endpoint exists).
- Only ACTIVE <-> SUSPENDED is a valid transition pair. TRIAL exists
  on tenants but not on tenant_users; ONBOARDING / TERMINATED are
  not tenant_user states.
- SUSPENDED -> ACTIVE clears `suspended_at`, `suspended_by_user_id`,
  AND `suspended_by_user_type` atomically with the status flip
  (Pattern (b) pair invariant per
  `ck_tenant_users_suspended_consistency`).

### Pattern (b) audit-actor population — repo-side requirement

Tables using Pattern (b) audit-actor columns (per D-13:
`tenant_users`, `org_nodes`, `stores`, `roles`, `role_permissions`,
both `*_role_assignments` tables) require that repository
INSERT/UPDATE statements populate BOTH columns of each
`*_by_user_*` pair simultaneously. Each table carries a
`ck_*_actor_pair` CHECK constraint enforcing either both-NULL or
both-NOT-NULL.

Example: writing to `core.tenant_users` requires:

```sql
INSERT INTO core.tenant_users (
    ...,
    created_by_user_id,     -- UUID
    created_by_user_type,   -- actor_user_type_enum
    updated_by_user_id,     -- UUID
    updated_by_user_type    -- actor_user_type_enum
) VALUES (
    ...,
    :actor_user_id,
    CAST(:actor_user_type AS actor_user_type_enum),
    :actor_user_id,
    CAST(:actor_user_type AS actor_user_type_enum)
);
```

Common mistake: writing only `*_by_user_id` and leaving
`*_by_user_type` NULL violates the pair-CHECK. The repo's bound
parameter dict must include the `actor_user_type` alongside
`actor_user_id` on every Pattern (b) write.

Helper convention (per Step 6.10.1 router): handlers map
`AuthContext.user_type` (a `Literal["PLATFORM", "TENANT"]`) to
`ActorUserType` (the typed enum) via a small helper, then pass both
columns through the kwargs:

```python
def _actor_type_from_auth(auth: AuthContext) -> ActorUserType:
    return (
        ActorUserType.PLATFORM
        if auth.user_type == "PLATFORM"
        else ActorUserType.TENANT
    )

# Handler:
row = await _repo.create(
    session,
    ...,
    actor_user_id=auth.user_id,
    actor_user_type=_actor_type_from_auth(auth),
)
```

This is distinct from Pattern (a) tables (`tenants`,
`platform_users`) where the audit-actor column is a single typed FK
to `platform_users` and no `*_user_type` column exists. The Step
6.11 tenants write surface uses Pattern (a); Step 6.10.1's
tenant_users write surface uses Pattern (b).

## Coupling and conventions

### Org hierarchy is hardcoded in three places

The org-tree hierarchy ordering is encoded in three independent 
sources that must stay in sync:

```
1. DDL org_node_type_enum in db/raw_ddl/shared_utilities_v1.sql
2. Frontend product spec (frontend repo: 
   Ithina_Admin_Frontend.md section 5.5)
3. _SCOPE_CASCADE_ORDER tuple in auth/permissions.py
```

When the hierarchy changes (add/remove/reorder levels), update 
all three together. Unit test 
(`test_scope_cascade_order_matches_canonical`) catches local 
drift in the Python tuple but does NOT catch cross-source drift. 
Manual sync required.

Levels in `_SCOPE_CASCADE_ORDER` that aren't yet in 
`PermissionScope` enum (BUSINESS_UNIT, HQ, COUNTRY, REGION, 
DEPARTMENT in v0) are inert in queries; no catalogue rows have 
those scope values. They're present in the tuple for 
forward-compatibility when the enum expands.

### PermissionScope enum has 3 values in v0

```
GLOBAL, TENANT, STORE
```

The 5 intermediate org-hierarchy levels (BUSINESS_UNIT, HQ, 
COUNTRY, REGION, DEPARTMENT) are valid `org_node_type_enum` 
values but are NOT in `PermissionScope` in v0. Expanding 
`PermissionScope` requires:

- Add value to `PermissionScope` enum (Python + DDL enum, 
  via Alembic migration for DDL change)
- Add catalogue rows for resources that should support the new 
  scope
- Grant rows to appropriate roles
- No change to `_SCOPE_CASCADE_ORDER` or `satisfying_scopes()` — 
  they already encode all 8 levels

## Data-seeding posture

Permissions, roles, and role-permissions live in `core.*` tables 
and are seeded from `data/ithina_dev_seed_data.xlsx`. Catalogue 
changes do NOT use Alembic migrations; the seed Excel is the 
source of truth and gets re-applied on schema bring-up.

**Reasons for the no-Alembic posture:**

- Catalogue rows are not schema; they're data. Alembic migrations 
  on data rows create deployment coupling without buying anything.
- Cloud SQL updates can be applied via operator-run UPSERT SQL 
  (faster than a deploy cycle for a row addition).
- Excel is auditable by non-engineers; SQL migrations require 
  developer involvement.

**Lifecycle for catalogue/role-grant changes:**

```
1. Operator edits the Excel file
2. Local DB: seed loader applies the Excel
3. Cloud SQL: operator runs targeted UPSERT SQL
4. Backend tests verify the new state
```

Alembic migrations remain the canonical mechanism for DDL changes 
(table additions, enum value additions, constraint changes).

### A known gotcha: code-column drift

The seed loader honours the Excel `code` column verbatim (it 
doesn't regenerate from M.R.A.S). Excel typos in derived columns 
therefore drift from the M.R.A.S enum columns and propagate to 
the database. Section 6.9 caught one such case during Phase 3 
apply (the Excel had `ADMIN.TENANTS.VIEW.TENANTS` plural while 
the enum columns correctly said `TENANT` singular). FN-AB-34 
tracks the planned load-time validation that catches such drift 
before DB insertion (assertion that 
`excel.code == f"{module}.{resource}.{action}.{scope}"` for each 
row). Until that lands, Excel edits to the `code` column need 
manual double-check.

## Forward-compatibility seams

Several design choices anticipate near-term expansion:

### Scope enum expansion (REGION is the likely next addition)

The cascade order tuple already includes REGION. Adding REGION 
to `PermissionScope` is a focused change:

```
Files to edit:
  - models/enums.py (Python enum)
  - DDL permission_scope_enum (Alembic migration; DDL not data)
  - Seed Excel (add new catalogue rows at REGION scope)
  - Cloud SQL (UPSERT for catalogue rows)

Files NOT to edit:
  - auth/permissions.py (helper already supports all 8 levels)
  - has_permission SQL (uses the helper's output)
  - require() factory (parameterised on scope; works with any 
    enum value)
```

### Resource enum expansion (DASHBOARD, MODULES are deferred)

`/dashboard/*` and `/module-access/*` endpoints currently proxy 
their gate through `ADMIN.TENANTS.VIEW.TENANT` (per FN-AB-29). 
The principled design adds dedicated tuples:

- `ADMIN.DASHBOARD.VIEW.{GLOBAL,TENANT}`
- `ADMIN.MODULES.VIEW.{GLOBAL,TENANT}`

Both require `resource_enum` DDL expansion (Alembic migration). 
Deferred until product need surfaces — currently the proxy works 
correctly.

### Write endpoints (Stage 2+)

Read endpoints (Section 6.9) establish the pattern. Write 
endpoints (Stage 2 onward) re-use:

- The same `require()` factory (with `CONFIGURE`, `APPROVE`, 
  `EXECUTE` action enum values)
- The same anchor dependency mechanism
- The same `PermissionDeniedError` envelope
- The mandatory-gate-discipline meta-test (continues to enforce 
  coverage)

No new mechanism is needed for writes; the read enforcement 
generalises.

### `_require_platform_auth` retirement

The pre-Section-6.9 `_require_platform_auth` helper 
(user-type-only fast path) was retired during Step 6.9.3.2. 
Replaced uniformly with `require(ADMIN, USERS, VIEW, GLOBAL)`. 
PLATFORM users pass via cascade from their GLOBAL grant. 
`PlatformAccessRequiredError` class kept as dead-code with an 
inline comment marking for potential later removal (FN-AB-33).

## Performance characteristics

EXPLAIN ANALYZE captured at multiple points across Section 6.9:

```
has_permission PLATFORM path:  0.139 ms 
has_permission TENANT path:    0.146 ms
Anchor dep query:              < 1 ms (single indexed SELECT)
```

Both `has_permission` paths use the `pk_permissions` index for 
the tuple match; the `ANY(...)` scope-cascade predicate is a 
Filter clause on the index-scan side, not a query-plan 
degradation. The TENANT path's additional JOINs all use indexed 
columns (composite `(tenant_id, id)` on org_nodes, 
`(tenant_id, module)` on tenant_module_access).

Per-request gate overhead is bounded by the SQL roundtrip plus 
the inner-function execution; both are sub-millisecond at v0 
scale. The architecture scales with the catalogue (30 rows of 
permissions, ~120 rows of role_permissions today) and with the 
user count via standard B-tree index lookup.

## Audit-trail attributes

Every gate denial raises `PermissionDeniedError` with structured 
context. The `exc.context` dict includes:

```
module           the gated permission's module
resource         the gated permission's resource
action           the gated permission's action
scope            the gated permission's scope
target_anchor    the request's anchor (None for list endpoints)
reason_code      one of NO_MATCHING_GRANT_OR_OUT_OF_SCOPE
```

These flow into application logs at error time. They're 
intentionally NOT in the response envelope — the gate denial 
doesn't tell the user what they would have needed to access the 
resource.

For SUCCESSFUL gate passes (handler executes):

- The gate function returns None silently; no current logging at 
  the gate layer for allowed requests
- The standard request-logging middleware records each request 
  (method, path, status, request_id, latency) regardless of auth 
  outcome

### Audit logging guidance for Stage 2 write handlers

Until structured audit-log writes are formalised (planned Step 
6.16+), write handlers should log meaningful state changes via 
the standard logger at INFO level. At minimum include:

```python
logger.info(
    "store.created",
    extra={
        "request_id": request_id,
        "user_id": str(auth.user_id),
        "user_type": auth.user_type,
        "tenant_id": str(tenant_id),
        "resource_id": str(created.id),
        # avoid sensitive request body fields (passwords, PII, etc.)
    },
)
```

The structured-event-name pattern (`<resource>.<action>`, e.g., 
`store.created`, `tenant.updated`, `user.role_assigned`) makes 
post-hoc audit reconstruction practical without a dedicated 
audit_log table. Step 6.16+ will migrate this to a structured 
table; the in-handler event names from Stage 2 will inform that 
schema.

## References

- Resolver implementation: `src/admin_backend/auth/permissions.py`
- Gate factory: same file
- Gate marker dataclass: `src/admin_backend/auth/gate_info.py`
- Anchor dependencies: `src/admin_backend/auth/anchor_deps.py`
- Gate allowlist: `src/admin_backend/auth/gate_allowlist.py`
- PermissionDeniedError: `src/admin_backend/errors.py`
- Mandatory-gate-discipline meta-test: 
  `tests/integration/test_gate_discipline.py`
- /me/* endpoints: `src/admin_backend/routers/v1/me.py`
- Frontend product spec: maintained in the admin-frontend repo, 
  `Ithina_Admin_Frontend.md` (cascade rules section 5.5)
