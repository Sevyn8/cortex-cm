# Step 6.9.1 — RBAC resolver core: read-only investigation findings

Date: 2026-05-11
Scope: facts and observations to feed the Step 6.9.1 design conversation.
Read-only pass — no edits, no test runs.

The resolver under design has the signature
`(auth, action, resource, scope, target_anchor, permission_set) -> (allowed, reason)`
and is pure (no DB calls inside it). `PermissionSet` is built per request.
The four design assumptions from the prompt that this investigation checks
against the codebase are:

1. Resolver is a pure function (no DB calls inside resolver itself).
2. PermissionSet is built per request (no caching in v0).
3. `RoleAssignmentsRepo.for_auth(auth)` provides unified-shape read.
4. Source-binding rule extends from `tenant_id` to `user_id` and `user_type`.

Findings are grouped below by area (AUTH, SESSION, REPO, CATALOG, TRAP).
Scope-creep observations (GATE / DEPEND / ERR / TEST / RETROFIT) are
deliberately NOT investigated; anything that surfaced naturally is listed
in the final "Open questions" section as a flag for later prompts.

---

## AUTH — AuthContext shape, as it feeds the resolver

### F-AUTH-1: AuthContext model is a frozen Pydantic v2 model with 8 fields

Question: what does the resolver receive as the `auth` argument?

Citation: `src/admin_backend/auth/context.py:42-90`

Current code (load-bearing portion):

```python
class AuthContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Standard JWT claims
    sub: str
    iss: str
    aud: str | list[str]
    exp: int

    # Custom claims (Auth0 namespaced; per D-24)
    user_id: UUID
    tenant_id: UUID | None
    user_type: Literal["PLATFORM", "TENANT"]
    email: str
```

Plus a cross-field validator (lines 79-90): `TENANT` user_type requires
non-NULL `tenant_id`; `PLATFORM` is permissive on `tenant_id` (NULL is
the standard case; non-NULL is the impersonation pattern, deferred).

Observation: the four resolver-relevant fields (`user_id`, `tenant_id`,
`user_type`, `email`) are already present and typed. `user_id` is `UUID`
unconditionally (non-optional). `tenant_id` is `UUID | None`. `user_type`
is a `Literal["PLATFORM", "TENANT"]`. Frozen — mutation raises
`ValidationError`. mypy strict statically rejects any flow of a raw string
into these fields.

Confidence: high.

Open question: None directly; the model already supports the
source-binding extension (`user_id` and `user_type` are typed fields on
a frozen model — same posture as `tenant_id`).

---

### F-AUTH-2: AuthContext is identity-only — no RBAC-relevant fields

Question: does AuthContext already carry roles/permissions, or is it
identity-only as D-24 specifies?

Citation: `src/admin_backend/auth/context.py:1-90` (the entire model);
docstring lines 1-12.

Current code (load-bearing portion):

```python
"""AuthContext: the verified identity context derived from a valid JWT.

Per D-24 (CLAUDE.md), the JWT carries identity claims only. No roles,
no permissions. Permission resolution happens in-app per request from
the DB tables (roles, permissions, role_permissions,
user_role_assignments).
"""
```

The class has no `roles` field, no `permissions` field, no `permission_set`
field. Repo-wide grep for `PermissionSet`, `permission_set`, `class
Resolver` returns zero hits in `src/`.

Observation: AuthContext as-is is sufficient input for the resolver's
source-binding requirement (user_id, user_type, tenant_id, email). No
RBAC data on it today; if the resolver design wants the caller to pass
a separately-constructed `PermissionSet`, that aligns with the existing
"identity-only" posture. If a future design wanted to fold PermissionSet
into AuthContext (anti-pattern per D-24), it would require an explicit
D-24 amendment.

Confidence: high.

Open question: confirm at design time whether PermissionSet flows as a
separate resolver argument (matches D-24) or is attached to AuthContext
(would require D-24 amendment — not flagged as proposed direction; just
a fact about the design space).

---

### F-AUTH-3: AuthContext is built once per request by `StubAuthClient.verify`; reaches handlers via `request.state.auth`

Question: where does AuthContext come from, and how does the resolver get
hold of it?

Citation:

- Build site: `src/admin_backend/auth/stub.py:131-141`
- Middleware populates request state: `src/admin_backend/middleware/auth.py:67-69`
- FastAPI dependency reads it back: `src/admin_backend/dependencies.py:25-37`

Current code (load-bearing portions):

```python
# stub.py:131-141
return AuthContext(
    sub=payload["sub"],
    iss=payload["iss"],
    aud=payload["aud"],
    exp=payload["exp"],
    user_id=user_id,
    tenant_id=tenant_id,
    user_type=user_type,
    email=email,
)
```

```python
# middleware/auth.py:67-69
auth_client: StubAuthClient = request.app.state.auth_client
auth_context = auth_client.verify(jwt_string)
request.state.auth = auth_context
```

```python
# dependencies.py:25-37
def get_auth_context(request: Request) -> AuthContext:
    auth = getattr(request.state, "auth", None)
    if auth is None:
        raise AuthMissingError(
            "AuthContext missing from request.state; "
            "auth middleware did not run"
        )
    return auth
```

Observation: `AuthContext` is constructed exactly once per request, at
middleware time, before any handler runs. It is available to any code in
the handler call chain via `Depends(get_auth_context)` (already imported
from `dependencies.py`). Production swap to Auth0 only changes the
`verify` implementation; the resolver's input shape is invariant.

Confidence: high.

Open question: None.

---

## SESSION — only as it relates to PermissionSet build

### F-SESSION-1: `get_tenant_session` sets three GUCs inside a single transaction; the session is transaction-scoped

Question: when in the request lifecycle is the session/GUCs available
for a PermissionSet read?

Citation: `src/admin_backend/db/session.py:37-82`

Current code (load-bearing portion):

```python
async def get_tenant_session(
    auth: AuthContext,
    session_factory: async_sessionmaker[AsyncSession],
    request_id: str | None = None,
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        async with session.begin():
            tenant_id_value = (
                str(auth.tenant_id) if auth.tenant_id is not None else None
            )
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": tenant_id_value},
            )
            await session.execute(
                text("SELECT set_config('app.user_type', :ut, true)"),
                {"ut": auth.user_type},
            )
            await session.execute(
                text("SELECT set_config('app.request_id', :rid, true)"),
                {"rid": request_id},
            )

            yield session
```

Observation: the session is yielded with all three GUCs (`app.tenant_id`,
`app.user_type`, `app.request_id`) already set on the same transaction
that the handler will run its queries against. `set_config(..., true)`
means LOCAL — vars only persist within the transaction; on commit/rollback
they reset. There is no leakage between requests. Any PermissionSet read
issued by the resolver build path against this same `session` inherits
the GUCs automatically.

Confidence: high.

Open question: None.

---

### F-SESSION-2: PermissionSet build via DB read can run anywhere inside the request's `get_tenant_session` lifetime; GUCs are already set when the dependency yields

Question: does PermissionSet build need any special session lifecycle
(early read before GUCs, or special-case transaction)?

Citation: `src/admin_backend/dependencies.py:56-72`, `src/admin_backend/db/session.py:63-81`

Current code (load-bearing portion):

```python
# dependencies.py:56-72
async def get_tenant_session_dep(
    auth: AuthContext = Depends(get_auth_context),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory
    ),
    request_id: str | None = Depends(get_request_id),
) -> AsyncIterator[AsyncSession]:
    async for session in get_tenant_session(
        auth, session_factory, request_id=request_id
    ):
        yield session
```

Observation: by the time `get_tenant_session_dep` yields, every GUC is
set. A PermissionSet build issued before the handler body (e.g., via a
nested `Depends` that itself depends on `get_tenant_session_dep`) sees
the same RLS view a handler-body query would. For PLATFORM users the
GUC `app.user_type='PLATFORM'` is set; the unconditional D-29 OR-branch
on `tenant_user_role_assignments` is fully active. For TENANT users
`app.tenant_id` filters to their own tenant's rows on the tenant table.
`platform_user_role_assignments` has no RLS — every session sees all
rows; the resolver build must therefore filter by `platform_user_id`
in app code for PLATFORM users (DB does no filtering there).

Confidence: high.

Open question: None directly. (Aside: D-29 + audience-check triggers
mean a TENANT user's `platform_user_role_assignments` read would
naturally return zero rows because PLATFORM roles can only be granted
to PLATFORM users — but only if the read is filtered by `auth.user_id`
in app code, since the table has no `tenant_id`.)

---

## REPO — RoleAssignmentsRepo internals

### F-REPO-1: RoleAssignmentsRepo has two physically separated list methods, returning ORM rows + total

Question: what is the existing surface the resolver-build path can call
to read role assignments?

Citation: `src/admin_backend/repositories/role_assignments.py:64-178`

Current code (load-bearing portion):

```python
class RoleAssignmentsRepo:
    """Read-only repository for the two post-split assignment tables."""

    async def list_platform_assignments(
        self,
        session: AsyncSession,
        *,
        role_id: UUID | None = None,
        platform_user_id: UUID | None = None,
        status: UserRoleAssignmentStatus | None = None,
        sort: str = "granted_at_desc",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[PlatformUserRoleAssignment], int]:
        ...

    async def list_tenant_assignments(
        self,
        session: AsyncSession,
        *,
        role_id: UUID | None = None,
        tenant_user_id: UUID | None = None,
        tenant_id: UUID | None = None,
        org_node_id: UUID | None = None,
        status: UserRoleAssignmentStatus | None = None,
        sort: str = "granted_at_desc",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[PlatformUserRoleAssignment], int]:  # actually TenantUserRoleAssignment
        ...
```

Observation: filter args overlap on `role_id`, `status`, `sort`,
`offset`, `limit`. The tenant-side adds `tenant_user_id`, `tenant_id`,
`org_node_id`; the platform-side has only `platform_user_id`. Default
sort `granted_at_desc` on both. Limit defaults to 50. Returns
`(items, total)` for pagination. Both methods raise `InvalidSortKeyError`
on unknown sort keys.

Confidence: high.

Open question: the resolver doesn't need pagination or sort; it needs a
complete read of the caller's ACTIVE assignments. Whether the resolver
build path calls the existing methods with a high `limit` (e.g. 1000),
or a new method is added without offset/limit/sort, is a design choice.

---

### F-REPO-2: No `for_auth(auth)` facade or "all assignments for a user" helper exists today

Question: does the assumed `RoleAssignmentsRepo.for_auth(auth)` facade
exist in the codebase?

Citation: searched `src/` for `for_auth`, `get_all_assignments_for_user`,
`all_assignments_for` — zero hits.

Current code: not present.

Observation: the prompt's design assumption (3) — that
`RoleAssignmentsRepo.for_auth(auth)` provides the unified-shape read —
describes a future addition, not an existing surface. The resolver
design needs to decide whether 6.9.1 lands this facade or whether the
resolver build path calls the two existing methods directly and merges
in-app.

Confidence: high.

Open question: confirm at design time whether `for_auth(auth)` lands as
part of 6.9.1 or is a separately-scoped Repo change.

---

### F-REPO-3: Post-split is handled at call site — callers dispatch on `auth.user_type`; no unified entry point exists

Question: how is the platform/tenant split currently navigated by code
that needs to look at "this user's assignments"?

Citation:

- The `/role-assignments` router dispatches on `auth.user_type` (per the
  CLAUDE.md Step 6.8.3 narrative: "audience routing on `/role-assignments`
  is a CALL-SITE DECISION, not a column filter ... TENANT JWTs MUST NOT
  execute the platform-side query because `platform_user_role_assignments`
  has no RLS"). Confirmed in `src/admin_backend/routers/v1/rbac.py` (line
  112 contains the AI-MT-03 source-binding comment).
- Per-user-row roles aggregation uses two parallel `_roles_subq()`
  helpers, one per Repo:
  - `src/admin_backend/repositories/platform_users.py:91-141` joins
    `platform_user_role_assignments` only.
  - `src/admin_backend/repositories/tenant_users.py:96-161` joins
    `tenant_user_role_assignments` only (via composite-key joins).

Observation: there is no existing single function or method that takes
an `AuthContext` and returns "all of this user's assignments" by
dispatching on `user_type`. The dispatch is done by the caller every
time. Two distinct correlated-subquery shapes (`_roles_subq`) already
exist for the user-detail endpoints — they each cover one side of the
split.

Confidence: high.

Open question: the `for_auth(auth)` facade design needs to pick one of
three shapes (return ORM rows from one table; return a merged
list-of-rows; return a pre-computed `PermissionSet`). Existing precedent
covers shape (1) and (2) but nothing pre-computes the permission set.

---

### F-REPO-4: Return-row shape is ORM model objects (not dataclasses or tuples)

Question: what does the resolver build path receive when it calls the
existing Repo methods?

Citation: `src/admin_backend/repositories/role_assignments.py:77,129`

Current code (load-bearing portion):

```python
) -> tuple[list[PlatformUserRoleAssignment], int]:
...
) -> tuple[list[PlatformUserRoleAssignment], int]:  # tenant side, per the file
```

(The tenant-side method's return type, line 129, declares
`tuple[list[PlatformUserRoleAssignment], int]` — this is the literal
declared type; the actual returned objects are `TenantUserRoleAssignment`.
Pure type-declaration drift, not a runtime bug. Flagged for design's
awareness; mypy strict does pass because at the call site the list
elements are accessed without type narrowing.)

Observation: rows come back as fully-instantiated ORM models. Each has
`role_id`, `status`, `granted_at`, and the various audit-actor columns.
The role's `audience`, `name`, `code` are NOT auto-loaded — that
requires a JOIN or a separate read against `roles`. The permission
tuples (module, resource, action, scope) are TWO joins away
(`role_permissions` and `permissions`).

Confidence: high (on return shape); medium (on the noted return-type
declaration drift — needs a separate sanity-pass eventually).

Open question: the resolver PermissionSet construction needs the four
permission tuple fields per row. The existing Repo surface does not
provide them; it provides assignment rows only. Design must decide
whether `for_auth(auth)` joins all the way through (assignment → role
→ role_permissions → permissions) and returns flattened tuples, OR
returns assignment rows and a second Repo call resolves role_id →
permissions, OR a different shape entirely.

---

### F-REPO-5: Existing Repo methods do NOT compose `role → role_permissions → permissions` for the resolver's needs

Question: can the resolver call the existing Repo methods and get back
the permission tuples it needs to populate PermissionSet?

Citation:

- `src/admin_backend/repositories/role_assignments.py:67-178` — no JOIN
  to role_permissions or permissions in either method.
- `src/admin_backend/repositories/roles.py:230-255` — `RolesRepo.list_permissions_for_role(role_id)` exists
  and DOES join `role_permissions → permissions`, but takes a single
  `role_id`, not an `AuthContext` / user identity.

Current code (load-bearing portion):

```python
# repositories/roles.py:230-255
async def list_permissions_for_role(
    self,
    session: AsyncSession,
    role_id: UUID,
) -> list[Permission]:
    stmt = (
        select(Permission)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
        .order_by(...)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
```

Observation: to build a PermissionSet for an `AuthContext`, the resolver
build path either:

(a) calls `RoleAssignmentsRepo.list_*_assignments` to get role_ids,
    then loops `RolesRepo.list_permissions_for_role(role_id)` for each
    (N+1 queries, but bounded — most users have 1–3 roles);

(b) issues a single JOIN query (assignment ⋈ role_permissions ⋈
    permissions, filtered by user_id/user_type and `status='ACTIVE'`,
    audience-aware via the table choice);

(c) builds a new dedicated `for_auth(auth)` method on
    RoleAssignmentsRepo (or a new Repo entirely) that returns the
    flattened tuple set.

None of (a)/(b)/(c) exists yet.

Confidence: high.

Open question: the choice between (a)/(b)/(c) is a design decision for
6.9.1 — surfaced here as a fact rather than a recommendation.

---

## CATALOG — permission catalogue current state

### F-CATALOG-1: 30 permissions, 120 role_permissions, 15 roles after Step 6.8.2.1

Question: what does the resolver's PermissionSet domain look like in
size at this moment?

Citation: `tests/integration/test_seed_loader.py:39-55`

Current code (load-bearing portion):

```python
EXPECTED_VISIBLE_COUNTS_PLATFORM = {
    "platform_users": 3,
    "tenants": 7,
    "org_nodes": 49,
    "stores": 25,
    "tenant_users": 17,
    "roles": 15,
    "permissions": 30,
    "role_permissions": 120,
    "tenant_module_access": 27,
    "platform_user_role_assignments": 3,
    "tenant_user_role_assignments": 19,
}
```

Observation: small enough that every operation the resolver might
contemplate (membership test, intersection, full enumeration) is cheap
even with a naive `set[tuple[...]]` representation. The catalogue is
not expected to grow by orders of magnitude during v0 (the seed Excel
is the canonical source, and growth would happen via additive
migrations).

Confidence: high.

Open question: None.

---

### F-CATALOG-2: Permission tuple shape is `(module, resource, action, scope)` plus a derived `code` TEXT field; UNIQUE on both

Question: what is the canonical key for a permission?

Citation:

- `src/admin_backend/models/permission.py:82-153`
- `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql:130-163`

Current code (load-bearing portion, DDL):

```sql
CREATE TABLE permissions (
    id              UUID                    NOT NULL DEFAULT uuidv7(),

    module          module_enum             NOT NULL,  -- actually module_code_enum post Step 6.6
    resource        resource_enum           NOT NULL,
    action          action_enum             NOT NULL,
    scope           permission_scope_enum   NOT NULL,

    code            TEXT                    NOT NULL,

    CONSTRAINT uq_permissions_tuple
        UNIQUE (module, resource, action, scope),
    CONSTRAINT uq_permissions_code
        UNIQUE (code),
    CONSTRAINT ck_permissions_code_format
        CHECK (code ~ '^[A-Z_]+\.[A-Z_]+\.[A-Z_]+\.[A-Z_]+$')
);
```

(`module_enum` in the v3 DDL text is the as-shipped name; live schema
references `module_code_enum` after Step 6.6's `cec8fae734e0` migration
re-pointed the column. ORM model declares the live name.)

Python enums:

```python
# models/permission.py
class PermissionResource(str, Enum):
    PRICING_RULES, MARKDOWNS, EXPIRING_ITEMS, WASTE_LOG,
    DONATION_ROUTING, CAMPAIGNS, USERS, ROLES, AUDIT_LOG,
    TENANTS, STORES, ORG_NODES = ...

class PermissionAction(str, Enum):
    VIEW, CONFIGURE, EXECUTE, APPROVE, OVERRIDE, AUDIT = ...

class PermissionScope(str, Enum):
    GLOBAL, TENANT, STORE = ...

# ModuleCode imported from models/tenant_module_access:
# ROOS, GOAL_CONSOLE, PRICING_OS, PERISHABLES_ASSISTANT,
# PROMOTIONS_ASSISTANT, ADMIN
```

Observation: the resolver's permission key is a 4-tuple of typed Python
enums (`ModuleCode`, `PermissionResource`, `PermissionAction`,
`PermissionScope`). DB enforces uniqueness on the tuple AND on the
derived `code` string. The resolver can use either as the
PermissionSet's hash key — the tuple is "more native" to typed Python,
the string is "more native" to logs/audit references.

Confidence: high.

Open question: PermissionSet representation — `set[tuple[Module,
Resource, Action, Scope]]` vs `set[str]` (the code) vs a frozen
dataclass with `(module, resource, action, scope, anchor_path)`
elements. Surfaced as a design fact, not a recommendation.

---

### F-CATALOG-3: PLATFORM/TENANT audience distinction lives on `roles`, NOT on `permissions`

Question: how does the resolver decide if a permission tuple is a
PLATFORM-audience grant vs a TENANT-audience grant?

Citation:

- `src/admin_backend/models/role.py:61-103` (Role.audience column)
- `src/admin_backend/models/permission.py:82-153` (no audience column)
- `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql:130-163` (permissions
  table — no audience column)

Current code (load-bearing portion):

```python
# models/role.py:94-103
audience: Mapped[RoleAudience] = mapped_column(
    PG_ENUM(
        RoleAudience,
        name="role_audience_enum",
        create_type=False,
        ...
    ),
    nullable=False,
)
```

Observation: permissions are platform-global reference data. A given
permission tuple (e.g., `ADMIN.USERS.VIEW.TENANT`) is structurally
identical regardless of which audience-flavour role grants it. The
audience distinction is enforced by the audience-check triggers on the
two assignment tables (Step 6.8.1, migration `3e05299cb533`):

- `platform_user_role_assignments` rows can only reference
  `role.audience='PLATFORM'`.
- `tenant_user_role_assignments` rows can only reference
  `role.audience='TENANT'`.

So once the resolver knows it's reading `platform_user_role_assignments`
(for a PLATFORM caller) vs `tenant_user_role_assignments` (for a TENANT
caller), the audience-check is implicit in the table choice — no
in-Python audience filter is needed on the resulting permission tuples.

Confidence: high.

Open question: the cross-audience case (a PLATFORM user impersonating a
tenant — `auth.user_type='PLATFORM'` with non-NULL `auth.tenant_id`,
per the AuthContext validator's "permissive PLATFORM" case) might want
to OR-combine permissions from both tables. The audience-check triggers
say a PLATFORM user CAN'T have rows in the tenant table, so this case
collapses to "PLATFORM user reading only `platform_user_role_assignments`"
— but worth confirming at design time.

---

### F-CATALOG-4: No precedent in the codebase for a "computed permission set" representation; closest analogues are `frozenset` for sort-key vocabularies and `@dataclass(frozen=True)` for stats row carriers

Question: is there established style for representing a per-request
computed set of permissions?

Citation:

- `frozenset` vocabularies:
  - `src/admin_backend/middleware/auth.py:38` — `PUBLIC_PATHS = frozenset({...})`
  - `src/admin_backend/repositories/role_assignments.py:48` — `ROLE_ASSIGNMENTS_SORT_KEYS: frozenset[str]`
  - `src/admin_backend/repositories/tenants.py:90` — `TENANTS_SORT_KEYS: frozenset[str]`
- `@dataclass(frozen=True)` rows: `src/admin_backend/repositories/dashboard.py:59,87`
  (`FleetStatsRow`, `GovernanceStatsRow`); `src/admin_backend/repositories/modules_access.py:49,59,71`.
- Non-frozen dataclass row carriers: `TenantListRow`, `PlatformUserListRow`,
  `TenantUserListRow` (each tied to one Repo method's return shape).

Current code: no `PermissionSet` class anywhere in `src/`. No
`set[tuple[...]]` of permission tuples constructed per request anywhere
in `src/`.

Observation: the closest house style is `@dataclass(frozen=True)` for
row-shape carriers (used in dashboard / modules-access Repos) and
`frozenset[<scalar>]` for compile-time-known vocabularies. A
PermissionSet representation has no direct precedent and is a fresh
naming/shape decision for 6.9.1.

Confidence: high.

Open question: shape of PermissionSet — `frozenset[tuple[Module, ...]]`
vs `@dataclass(frozen=True)` containing the set vs a richer object with
both the tuple set and the anchor-path set (relevant for the AI-RBAC-05
ltree cascade — see TRAP F-TRAP-3 / catalogue notes below). Surfaced
as a design fact.

---

## TRAP — known traps the resolver might hit

### F-TRAP-1: `.correlate(<OuterModel>)` trap; multiple precedents

Question: if the resolver build path uses a SQLAlchemy correlated
subquery (to attach assignments to an outer user row), where does this
trap live and will the resolver hit it?

Citation (every current occurrence in `src/`):

| File | Line | Outer correlated to | Step that introduced it |
|---|---|---|---|
| `repositories/tenants.py:290` | num_stores_subq | `.correlate(Tenant)` | Step 3.3 L9 |
| `repositories/tenants.py:299` | num_users_active_subq | `.correlate(Tenant)` | Step 3.3 L9 |
| `repositories/tenants.py:355` | num_stores_subq (detail) | `.correlate(Tenant)` | Step 3.3 |
| `repositories/tenants.py:364` | num_users_active_subq (detail) | `.correlate(Tenant)` | Step 3.3 |
| `repositories/tenants.py:158` | modules_subq | `.correlate(Tenant)` | Step 3.4.5 |
| `repositories/roles.py:100` | platform_count_subq | `.correlate(Role)` | Step 6.1 R4 / Step 6.8.2 |
| `repositories/roles.py:107` | tenant_count_subq | `.correlate(Role)` | Step 6.8.2 |
| `repositories/platform_users.py:139` | _roles_subq | `.correlate(PlatformUser)` | Step 6.8.3 |
| `repositories/tenant_users.py:159` | _roles_subq | `.correlate(TenantUser)` | Step 6.8.3 |

Current code (one representative example):

```python
# repositories/roles.py:96-114
platform_count_subq = (
    select(func.count(PlatformUserRoleAssignment.id))
    .where(PlatformUserRoleAssignment.role_id == Role.id)
    .where(PlatformUserRoleAssignment.status == "ACTIVE")
    .correlate(Role)
    .scalar_subquery()
)
tenant_count_subq = (
    select(func.count(TenantUserRoleAssignment.id))
    .where(TenantUserRoleAssignment.role_id == Role.id)
    .where(TenantUserRoleAssignment.status == "ACTIVE")
    .correlate(Role)
    .scalar_subquery()
)
```

Observation: if the resolver build path is a single SQL pass that
attaches a `permission_set` aggregate to some outer row (e.g., to the
caller's row in `platform_users`/`tenant_users`), the
`.correlate(<OuterModel>)` call is mandatory — without it the subquery
emits a global rather than per-row aggregate. If the build path is a
flat SELECT keyed by `user_id`/`user_type` with no outer correlation
(simpler), the trap does not apply.

Confidence: high.

Open question: the resolver-build query shape (correlated-aggregate vs
flat-keyed) is a design choice. Either shape works; just flag the trap
if shape (a) is chosen.

---

### F-TRAP-2: Schema-qualification mandatory for raw `text()` SQL; existing `RoleAssignmentsRepo` is pure-ORM and avoids it

Question: if the resolver build path uses raw `text()` SQL, what
convention applies; if it stays ORM, does the trap apply at all?

Citation:

- Precedent that DOES schema-qualify: `repositories/permission_matrix.py:127-158`
  (uses `schema = get_settings().db_schema` and interpolates as
  `f"FROM {schema}.permissions AS p"` etc.).
- Anti-pattern + fix: `repositories/dashboard.py` (Step 6.5.1 bugfix —
  module-level `text()` constants with unqualified names worked locally
  but failed on Cloud SQL with `relation "tenants" does not exist`).
- `RoleAssignmentsRepo` (`repositories/role_assignments.py:31-178`) and
  `TenantUsersRepo._roles_subq` (`repositories/tenant_users.py:96-161`)
  use pure ORM — `__table_args__["schema"]` injects schema at SQL render
  time, no manual interpolation needed.

Current code (load-bearing portion from permission_matrix.py:127):

```python
schema = get_settings().db_schema
sql = text(
    f"""
    SELECT ...
    FROM {schema}.permissions AS p
    LEFT JOIN {schema}.lookups AS lk_module ...
    """
)
```

Observation: the established RoleAssignmentsRepo posture is pure ORM.
If 6.9.1's `for_auth(auth)` facade or PermissionSet build query uses
ORM, no raw-SQL schema-qualification work needed. If it switches to
raw SQL (e.g., to express a UNION across the two assignment tables
plus the role_permissions ⋈ permissions JOIN in a single query), the
schema-qualification convention applies — see permission_matrix.py for
the canonical pattern.

Confidence: high.

Open question: query-shape choice (ORM vs raw SQL) is a design
decision; either is consistent with house style.

---

### F-TRAP-3: Cross-tenant injection guarantees on `tenant_user_role_assignments` are STRUCTURAL via composite-FK invariants (D-34 / AI-RBAC-06); the resolver READS rows only, so the trap applies asymmetrically

Question: what does the resolver need to be aware of about the
post-split tenant-side assignment table?

Citation:

- DDL composite FKs are declared on `tenant_user_role_assignments` per
  D-34 / Step 6.8.1 (migration `3e05299cb533`): composite FK to
  `tenant_users(tenant_id, id)` and to `org_nodes(tenant_id, id)`.
- App-layer composite-key JOIN is already used at read time:
  `repositories/tenant_users.py:148-153` joins via the composite
  `(tenant_id, org_node_id)`:

```python
.join(
    OrgNode,
    and_(
        OrgNode.tenant_id == TenantUserRoleAssignment.tenant_id,
        OrgNode.id == TenantUserRoleAssignment.org_node_id,
    ),
)
.where(
    TenantUserRoleAssignment.tenant_id == TenantUser.tenant_id,
    TenantUserRoleAssignment.tenant_user_id == TenantUser.id,
)
.correlate(TenantUser)
```

Observation: cross-tenant injection structurally cannot occur on writes
to `tenant_user_role_assignments` — the composite FKs reject any insert
where the row's `tenant_id` mismatches the referenced user's or
org_node's. The resolver READS, not writes, so it cannot create such a
row. But the resolver's read should still use the composite key when
joining to `tenant_users` or `org_nodes` to stay consistent with the
established pattern (`tenant_users.py:148-157` is the canonical
example). Joining via single-column `tenant_user_id` alone is
structurally safe-for-reads (the row IDs match the FK guarantee) but
diverges from the house convention.

Confidence: high.

Open question: org_node anchoring + ltree cascade — the resolver's
`target_anchor` argument is a hint that it may need each assignment's
`org_node.path` so the permission "applies to this anchor and all
descendants" cascade (AI-RBAC-05) can resolve. Existing
`list_tenant_assignments` returns only the assignment row, not the
org_node row. Joining-through to fetch `org_node.path` is a Repo design
decision for 6.9.1.

---

## Open questions for design conversation

Consolidated from the findings above plus naturally-surfaced
observations. The first nine sit inside the AUTH / SESSION / REPO /
CATALOG / TRAP scope; the trailing bullets flag scope-creep
observations for later prompts (do NOT investigate them in 6.9.1).

In-scope (design discussion for 6.9.1):

1. F-AUTH-2 — PermissionSet flows as a separate resolver argument
   (matches D-24 identity-only AuthContext) or attaches to AuthContext
   (would require explicit D-24 amendment).
2. F-REPO-1 / F-REPO-2 — The `for_auth(auth)` facade does not yet
   exist. Does 6.9.1 land it on `RoleAssignmentsRepo`, or somewhere
   else (new `ResolverRepo`, or build inside the resolver-wiring layer
   directly)?
3. F-REPO-5 — Query shape for the PermissionSet build: (a) call
   existing `list_*_assignments` + loop `RolesRepo.list_permissions_for_role`
   per role (N+1, bounded); (b) single JOIN query across
   assignment ⋈ role_permissions ⋈ permissions filtered by user; (c) a
   new dedicated method/Repo returning flattened tuples. None of (a)-(c)
   exists today.
4. F-CATALOG-2 / F-CATALOG-4 — PermissionSet element representation:
   tuple-of-typed-enums vs `code` string vs frozen dataclass that also
   carries the org_node anchor / path. No house precedent for a
   "computed permission set" type.
5. F-CATALOG-3 — Cross-audience case (PLATFORM impersonating a tenant,
   per the AuthContext validator's permissive PLATFORM branch). The
   audience-check triggers say a PLATFORM user has no rows in
   `tenant_user_role_assignments`, so this likely collapses to "read
   only platform-side"; worth confirming the resolver's behaviour for
   this case explicitly.
6. F-TRAP-1 — Query-shape choice (ORM correlated aggregate vs flat
   keyed SELECT vs raw SQL) determines whether `.correlate()` is
   needed. Mention at design time so the precedent gets followed if
   shape (a) is chosen.
7. F-TRAP-3 — Whether the resolver build path needs `org_node.path`
   per tenant-side assignment (for AI-RBAC-05 ltree cascade). Affects
   whether the build query joins to `org_nodes`.
8. F-REPO-4 (sanity-check item) — Tenant-side list method declares
   `tuple[list[PlatformUserRoleAssignment], int]` as its return type
   (`role_assignments.py:129`); should be
   `tuple[list[TenantUserRoleAssignment], int]`. Pure type-declaration
   drift; doesn't break the existing code path (mypy strict still
   passes because callers iterate without narrowing). Worth fixing as
   a same-commit cleanup whenever 6.9.1 touches this Repo, but not
   gating.
9. The prompt's design assumption (2) — PermissionSet build per request
   with no caching — is straightforwardly compatible with the request
   lifecycle (F-SESSION-1, F-SESSION-2). No findings counter this.

Out-of-scope (scope-creep flags for later prompts, NOT investigated
here):

- GATE (gate-pattern shape, where `_require_*` helpers land, when
  `allowed=False` raises vs returns a structured result) — 6.9.2 territory.
- DEPEND (FastAPI dependency wiring beyond what the resolver itself
  needs; how `Depends(get_permission_set)` flows into handlers) — 6.9.2.
- ERR (new error class hierarchy entries for "permission denied" vs
  existing `PlatformAccessRequiredError` parallel) — 6.9.2.
- TEST (unit-test surface for the resolver function beyond its purity
  contract; integration tests for gate behaviour) — 6.9.2.
- RETROFIT (which Step-3.x-onward endpoints get retrofitted with gates,
  precedence between existing PLATFORM-only gates and the new resolver)
  — 6.9.3.
