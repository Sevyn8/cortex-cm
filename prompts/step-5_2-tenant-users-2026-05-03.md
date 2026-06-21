# Prompt — Step 5.2: Tenant Users resource (model + schema + repo + router + endpoint doc)

> Generated 2026-05-03. Revised 2026-05-03 (v2: hardened auth/session-dependency naming via explicit pre-flight verification; tightened cross-tenant test docstrings; lightened lightweight-stub-swap risk via verification step; corrected OpenAPI path; added re-seed + cross-tenant fixture coordination notes). Revised 2026-05-03 (v3: added Ground rules and v0 auth model preamble; tightened File 9 CLAUDE.md update to extend Step 5.1's auth-model convention note with the multi-user-type pattern + cross-tenant-404 framing).
> Paste this entire block into a fresh Claude Code session to start Step 5.2.
> Two GET endpoints (list + detail) for tenant_users — customer-side users. Mirrors Step 5.1's shape but adds RLS-bound tenant isolation, multi-user-type access (PLATFORM and TENANT JWTs both accepted), and cross-tenant isolation tests. **Step 5.1 must land before this**; the patterns established there (PLATFORM auth gate, schema layout, repo singleton) propagate.

---

## Ground rules and v0 auth model

The four ground rules this step participates in:

1. **PLATFORM users can view (and post-v0 configure) tenant users across all tenants.** Step 5.2 implements the VIEW side. Configuration is post-v0 per FN-AB-12. Mechanism: D-29 permissive impersonation — PLATFORM JWTs see all rows on tenant-owned tables regardless of `app.tenant_id`.

2. **TENANT users can view (and post-v0 configure) tenant users belonging to the same tenant only.** Step 5.2 implements the VIEW side. Configuration is post-v0. Mechanism: RLS policy `tenant_users_tenant_isolation` filters by `tenant_id = NULLIF(current_setting('app.tenant_id'), '')::uuid` for TENANT sessions.

3. **RBAC refines further — scope, module access, actions.** RBAC will distinguish e.g. "Owner can list all tenant users; Pricing Manager can list only those in their region." **Not in scope for this step.** RBAC enforcement is Step 6.1; seed data already exists per Step 3.5 (roles, permissions, role_permissions, user_role_assignments tables all populated).

4. **Cross-tenant access by TENANT users is forbidden and surfaces as 404, not 403.** Per D-17 (RLS-as-404): a TENANT-A user requesting TENANT-B's user_id receives 404 because the row is invisible to their session via RLS. Returning 403 would disclose that the user_id exists; 404 is information-equivalent for "not visible to you" regardless of the reason.

**v0 auth model (until Step 6.1 lands):**

The router-layer auth check is **binary user_type-based** (PLATFORM vs TENANT), not role-based.

- This step's `/api/v1/tenant-users` endpoint accepts BOTH user_types — there is no `_require_platform_auth` gate. RLS handles visibility scoping automatically.
- Step 5.1's `/api/v1/platform-users` endpoint is PLATFORM-only via `_require_platform_auth(auth)` gate.
- No router-layer permission check (e.g., "does this user have ADMIN.TENANT_USERS.VIEW") in v0. That's Step 6.1.

The cross-tenant 404 (test T9) is the **load-bearing assertion** that the v0 model works end-to-end. Without it, regressions in RLS or session-handling could silently expose tenant data across boundaries.

**This framing must be reflected in CLAUDE.md when this step ships** — see File 9 below. Step 5.1 introduced the same auth-model note; this step extends it (Step 5.2 is the multi-user-type pattern Step 5.1's note refers to).

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 5.1 at HEAD (most recent commit).
3. `uv run alembic heads` — confirm output is `0644a4186e48` (Step 3.6's revision is the current head; Step 5.1 added no migration).
4. Read `CLAUDE.md` fully. Focus on:
   - **D-03** — RLS enforcement; `app.tenant_id` and `app.user_type` set per-transaction by `get_tenant_session`. **Critical for this step:** `tenant_users` HAS RLS+FORCE; visibility flows automatically from session GUCs.
   - **D-13** — Audit-actor patterns. `tenant_users` uses Pattern (b): paired `*_by_user_id UUID` + `*_by_user_type actor_user_type_enum` columns. No FK on the actor.
   - **D-15** — `__table_args__["schema"]` from environment.
   - **D-17** — Missing or RLS-filtered rows surface as None from the repo; router converts to 404. **For tenant_users this is load-bearing:** a TENANT-A user requesting TENANT-B's user_id sees 404 because RLS filters the row out, not because it doesn't exist.
   - **D-21** — UUIDv7 default; `id` carries no Python or ORM-side default; DB DEFAULT `uuidv7()` is authoritative.
   - **D-24** — AuthContext is the only path for tenant context. Repo MUST NOT accept a `tenant_id` argument for visibility purposes — RLS handles it via session GUCs.
   - **D-29** — PLATFORM RLS visibility. PLATFORM JWTs see all tenant_users across all tenants by RLS policy. TENANT JWTs see only their own tenant's users. The OR-clause on `tenant_users_tenant_isolation` policy makes this work without an application-layer filter.
   - **D-30** — List-only response envelope (`{items, pagination}` for collections; single resource returned directly).
   - **D-31** — Response field semantics are append-only.
   - "Note on PG enum columns" subsection.
   - "Note on Repository pattern".
5. Read `db/raw_ddl/Ithina_postgres_SQL_DDL_tenant_users_v1.sql` — full column list, `tenant_user_status_enum`, `actor_user_type_enum` (Pattern b), CHECK constraints (auth0_sub_consistency, invitation_accepted_consistency, suspended_consistency), unique constraints `(tenant_id, email)` and `(tenant_id, auth0_sub) WHERE auth0_sub IS NOT NULL`, RLS policy.
6. Read `migrations/versions/21e2ad16303a_*.py` (Step 3.0). The OR-clause shape on `tenant_users_tenant_isolation` is what makes PLATFORM-visibility work for this step. Verify the migration is applied (alembic current).
7. Read `src/admin_backend/models/tenant.py` and the just-shipped `src/admin_backend/models/platform_user.py`. The `TenantUser` model mirrors both: tenant.py for the multi-tenant table conventions (tenant_id, FK to tenants, RLS-aware modeling); platform_user.py for the user-shape (status, invitation_at, suspended_at). **Also read `src/admin_backend/models/_lightweight_stubs.py` if it exists** — it likely contains a `TenantUser` stub used by tenants Repo's correlated subqueries (Step 3.3). Confirm: (a) what columns the stub declares, (b) where the stub class is imported (likely `repositories/tenants.py`), (c) whether the stub class name is `TenantUser` (collision with the new full model) or something distinguishing. The full model in this step **replaces** the stub. See File 1 below for the swap procedure.
8. Read `src/admin_backend/schemas/platform_user.py` (just shipped). The `TenantUserRead` schema mirrors `PlatformUserRead`'s shape but adds `tenant_id` and the Pattern (b) audit-actor fields stay hidden from response.
9. Read `src/admin_backend/repositories/platform_users.py` (just shipped). The `TenantUsersRepo` mirrors this exactly: stateless singleton, list+get_by_id, sort/search/pagination. Add a `tenant_id` filter for the list endpoint (PLATFORM users can scope to a single tenant if they want). **Confirm exactly: the constructor pattern, method signatures, sort-key validation approach, search ILIKE pattern. Mirror identically.**
10. Read `src/admin_backend/routers/v1/platform_users.py` (just shipped). **Confirm exactly:**
    - (a) The session dependency import (likely `from admin_backend.dependencies import get_tenant_session_dep`).
    - (b) The auth-context retrieval pattern (the `_require_platform_auth` from Step 5.1, OR whatever pattern was actually adopted there). Step 5.2 does NOT use that gate (both PLATFORM and TENANT accepted), but the import still needs to know what was adopted.
    - (c) URL prefix style on the APIRouter.
    - (d) The error-class shape (`TenantUserNotFoundError(ClientError)` mirrors `PlatformUserNotFoundError`).
    - (e) Whether `Pagination` is imported from `schemas.tenant`, `schemas._common`, or wherever Step 5.1 placed it.
    - **Do not assume the names in this prompt's File 4 skeleton are correct without verifying.**
11. Read `tests/integration/test_platform_users_router.py` (just shipped) and `tests/integration/test_tenants_router.py`. **Confirm exactly:**
    - (a) The `app_client` fixture name and sync `TestClient` usage.
    - (b) Both `_platform_jwt(settings)` AND `_tenant_jwt(settings, *, tenant_id)` helpers — Step 5.1 should have added the latter; if it didn't, this step adds it (see File 6 below).
    - (c) How cross-tenant assertion patterns are structured in the tenants tests (D4 from Step 3.3 is the canonical RLS-as-404 test; mirror its shape).
12. Read `tests/integration/conftest.py`. **Critical for File 6:** confirm:
    - (a) Whether `make_tenant_user` factory exists. If yes, use it; if no, plan whether to add it or work against seed data.
    - (b) Whether fixtures for `sample_tenant_id`, `tenant_a_id`, `tenant_b_id` exist or need to be added.
    - (c) The async/sync conventions and teardown patterns of existing factories — any new fixtures MUST mirror exactly.
13. Read `docs/endpoints/tenants.md` and the just-shipped `docs/endpoints/platform-users.md`. Mirror the 8-section structure.
14. Read `BUILD_PLAN.md` Step 5.2 entry — the existing entry's scope-in/scope-out is the starting point.
15. **Verify the OpenAPI spec canonical location.** Run `ls -la docs/openapi.json docs/endpoints/openapi.json 2>/dev/null` — Step 3.6's pre-push hygiene relocated the canonical spec to `docs/endpoints/openapi.json`. References in this prompt all use that path.
16. Read this prompt fully.

---

## Step ID and intent

**Step 5.2** — Tenant Users resource. Two GET endpoints + ORM model + schemas + repo + tests + endpoint doc.

The shape is **almost identical to Step 5.1** except:

1. **`tenant_id` column.** TenantUser has a tenant_id; PlatformUser does not.
2. **RLS-bound.** Visibility flows automatically from session GUCs via the existing `tenant_users_tenant_isolation` policy. No application-layer scoping.
3. **Both user types accepted.** PLATFORM JWT sees all tenant_users (cross-tenant). TENANT JWT sees only their own. No `_require_platform_auth` gate.
4. **Pattern (b) audit-actors.** `*_by_user_id` is a raw UUID; `*_by_user_type` is an enum. Both hidden from response.
5. **Cross-tenant isolation tests.** Load-bearing: tenant A asking for tenant B's user → 404 (RLS-as-404 per D-17).
6. **`tenant_id` filter for PLATFORM list.** PLATFORM users can scope the list to a single tenant via `?tenant_id=...`. RLS doesn't filter for PLATFORM (per D-29's permissive impersonation), so application-layer filter is needed when scoping is desired.

Six concrete deliverables:

1. **`TenantUser` ORM model** with `TenantUserStatus` enum.
2. **`TenantUserRead`, `TenantUserListItem`, `TenantUserListResponse` schemas.**
3. **`TenantUsersRepo`** with `list(session, *, tenant_id, status, search, sort, offset, limit)` and `get_by_id(session, user_id)`.
4. **Router** with `GET /api/v1/tenant-users` and `GET /api/v1/tenant-users/{user_id}`. Plus `TenantUserNotFoundError`.
5. **Integration tests** (~13 tests) including cross-tenant isolation (LOAD-BEARING).
6. **`docs/endpoints/tenant-users.md`** following the 8-section pattern.

CLAUDE_CODE step. **No DDL changes.** No migration. No schema impact.

---

## Source-of-truth specification

### File 1: `src/admin_backend/models/tenant_user.py` — new

Maps `tenant_users_v1.sql`. Single `TenantUserStatus` enum (`INVITED`, `ACTIVE`, `SUSPENDED`). Columns: `id`, `tenant_id`, `auth0_sub`, `email`, `full_name`, `status`, `invited_at`, `invitation_accepted_at`, `suspended_at`, `suspended_by_user_id`, `suspended_by_user_type`, `created_at`, `created_by_user_id`, `created_by_user_type`, `updated_at`, `updated_by_user_id`, `updated_by_user_type`.

Mirror the `Tenant` model + just-shipped `PlatformUser` model:

- `__tablename__ = "tenant_users"`
- `__table_args__["schema"]` from `get_settings().db_schema`
- `id`: `server_default=FetchedValue()`, no Python default
- `tenant_id`: NOT NULL, FK to `tenants.id`. Type UUID. **No SQLAlchemy `relationship("Tenant", ...)` declared** — keep the ORM minimal; the relationship isn't used for joins in this Repo's queries.
- `created_by_user_id`, `updated_by_user_id`, `suspended_by_user_id`: raw UUID columns, **no FK declared at SA layer** per D-13 Pattern (b). The DDL has no FK either (Pattern b's whole point).
- `created_by_user_type`, `updated_by_user_type`, `suspended_by_user_type`: `actor_user_type_enum` columns. Use `postgresql.ENUM("PLATFORM", "TENANT", name="actor_user_type_enum", create_type=False, native_enum=True, values_callable=...)`.
- `status`: `tenant_user_status_enum` via `postgresql.ENUM(..., create_type=False, native_enum=True)`.
- `created_at`, `updated_at`: `server_default=FetchedValue()`.

The `(tenant_id, email)` UNIQUE constraint and `(tenant_id, auth0_sub) WHERE auth0_sub IS NOT NULL` partial unique are DB-side; the model doesn't redeclare them.

**Lightweight stub swap procedure (load-bearing for Step 3.3 regression):**

If `models/_lightweight_stubs.py` exists with a `TenantUser` stub (per Step 3.3 memory), this step's full ORM model **replaces** it. The swap is delicate because Step 3.3's tenants endpoints rely on the stub for the `num_users_active` per-row aggregate (the L9 test in `test_tenants_router.py` is load-bearing).

Procedure:

1. **Before writing anything,** run the existing test suite with `uv run pytest tests/integration/test_tenants_router.py -v -k "L9 or aggregates"` and capture the pass output. This is the baseline — these tests must still pass after the swap.
2. Write `models/tenant_user.py` with the full `TenantUser` model (this File 1).
3. Update `repositories/tenants.py` to import from the new path: `from admin_backend.models.tenant_user import TenantUser` instead of `from admin_backend.models._lightweight_stubs import TenantUser`. The stub's columns (`id`, `tenant_id`, `status`) are a subset of the full model's columns — the SELECT subqueries should produce identical SQL output.
4. Verify SQL equivalence: the tenants Repo's `num_users_active` subquery references `TenantUser.id` (count target), `TenantUser.tenant_id` (correlation predicate), and `TenantUser.status == TenantUserStatus.ACTIVE` (filter). All three columns exist identically in the full model. **The status enum comparison is the riskiest part:** the stub may have used a string-typed enum or a different Python enum class; the full model uses `TenantUserStatus`. If both classes use `values_callable=lambda e: [m.value for m in e]`, the SQL output is identical strings (e.g., `WHERE status = 'ACTIVE'`).
5. Remove the `TenantUser` class from `_lightweight_stubs.py`. **Keep the `Store` stub** — Step 4.5 (Stores) hasn't shipped, that stub is still in use.
6. Run `uv run pytest tests/integration/test_tenants_router.py -v -k "L9 or aggregates"` again. Confirm same pass count and the L9 test specifically passes.
7. If anything breaks, **roll back the swap** — keep the stub for now, ship Step 5.2's other deliverables, and surface the swap difficulty for separate handling.

The stub's docstring carries an explicit warning about Alembic autogenerate — do not point Alembic at `Base.metadata` while the stub exists. With the full model replacing it, this restriction lifts (the full model declares all columns); but Step 4.5's `Store` stub is still in place, so the warning still applies overall until 4.5 ships. Update the docstring in `_lightweight_stubs.py` if necessary to reflect "Store stub only" after the TenantUser removal.

### File 2: `src/admin_backend/schemas/tenant_user.py` — new

Three classes:

- `TenantUserRead` — full single-resource response. Fields: `id`, `tenant_id`, `email`, `full_name`, `status`, `invited_at`, `invitation_accepted_at`, `suspended_at`, `created_at`, `updated_at`. **Hidden:** `auth0_sub` (Auth0-specific), `suspended_by_user_id`, `suspended_by_user_type`, `created_by_user_id`, `created_by_user_type`, `updated_by_user_id`, `updated_by_user_type`.
- `TenantUserListItem` — same shape as `TenantUserRead` for v0 (single class can serve both).
- `TenantUserListResponse` — `{items, pagination}` per D-30. Reuse `Pagination`.

All Pydantic v2 with `ConfigDict(from_attributes=True)`. ISO 8601 timestamps with offset.

### File 3: `src/admin_backend/repositories/tenant_users.py` — new

**The skeleton below mirrors `repositories/platform_users.py` (Step 5.1). Before writing, verify the singleton + method-takes-session pattern is identical to what Step 5.1 just shipped.**

```python
"""TenantUsersRepo — read-only data access for the tenant_users table.

Owns SELECT queries on tenant_users. Visibility flows from session GUCs:
- PLATFORM JWT: sees all tenant_users across all tenants (per D-29 permissive)
- TENANT JWT: RLS scopes to only the matching tenant_id

The Repo is unaware of multi-tenancy mechanics — RLS handles it. Per
D-24, no tenant_id argument on visibility-bearing methods.

The list() method optionally accepts a tenant_id filter for PLATFORM
users who want to scope the list to a single tenant (e.g., the admin
console showing tenant detail with the tenant's users). For TENANT
JWTs, the filter is functionally redundant — RLS already scopes to the
caller's tenant — but harmless.
"""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.models.tenant_user import TenantUser, TenantUserStatus


SORT_MAP = {
    "created_at_asc": TenantUser.created_at.asc(),
    "created_at_desc": TenantUser.created_at.desc(),
    "full_name_asc": TenantUser.full_name.asc(),
    "full_name_desc": TenantUser.full_name.desc(),
    "email_asc": TenantUser.email.asc(),
    "email_desc": TenantUser.email.desc(),
}


class TenantUsersRepo:
    """Read-only repository for tenant_users."""

    async def list(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID | None = None,
        status: TenantUserStatus | None = None,
        search: str | None = None,
        sort: str = "created_at_desc",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[TenantUser], int]:
        """Return tenant_users matching filters, plus total count.

        Visibility:
        - PLATFORM JWT (no tenant_id filter): all tenant_users
        - PLATFORM JWT + tenant_id filter: scoped to that tenant
        - TENANT JWT: RLS scopes to caller's tenant; tenant_id filter
          is redundant (and if non-matching, returns empty)
        """
        if sort not in SORT_MAP:
            raise ValueError(f"unknown sort key: {sort}")

        stmt = select(TenantUser)
        count_stmt = select(func.count()).select_from(TenantUser)

        if tenant_id is not None:
            stmt = stmt.where(TenantUser.tenant_id == tenant_id)
            count_stmt = count_stmt.where(TenantUser.tenant_id == tenant_id)

        if status is not None:
            stmt = stmt.where(TenantUser.status == status)
            count_stmt = count_stmt.where(TenantUser.status == status)

        if search:
            pattern = f"%{search}%"
            search_clause = TenantUser.email.ilike(pattern) | TenantUser.full_name.ilike(pattern)
            stmt = stmt.where(search_clause)
            count_stmt = count_stmt.where(search_clause)

        # Stable secondary sort by id
        stmt = stmt.order_by(SORT_MAP[sort], TenantUser.id.asc())
        stmt = stmt.offset(offset).limit(limit)

        items_result = await session.execute(stmt)
        items = list(items_result.scalars().all())

        count_result = await session.execute(count_stmt)
        total = count_result.scalar_one()

        return items, total

    async def get_by_id(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> TenantUser | None:
        """Return the tenant_user with this id, or None if not visible.

        "Not visible" includes both genuinely missing rows AND rows
        filtered by RLS (per D-17). Router converts None to 404.
        """
        stmt = select(TenantUser).where(TenantUser.id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


_repo = TenantUsersRepo()
```

### File 4: `src/admin_backend/routers/v1/tenant_users.py` — new

**Mirrors the just-shipped `routers/v1/platform_users.py` EXCEPT:**
- No `_require_platform_auth` call. Both PLATFORM and TENANT JWTs are accepted.
- The list handler has an additional `tenant_id` query param (UUID type, optional).
- Error class is `TenantUserNotFoundError` with `code: TENANT_USER_NOT_FOUND`.

```python
"""Router for GET /api/v1/tenant-users (list) and /{user_id} (detail).

Auth: both PLATFORM and TENANT JWTs accepted. Visibility scoped by RLS:
- PLATFORM JWT sees all tenant_users across all tenants
- TENANT JWT sees only own tenant's users

Per D-30: list returns {items, pagination}. Per D-31: field semantics
frozen append-only.

Per D-17: missing OR RLS-filtered rows surface as 404 from the detail
endpoint. A TENANT-A user asking for TENANT-B's user_id receives 404,
NOT 403 — the row is invisible to them, no information disclosure.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.dependencies import get_tenant_session_dep
from admin_backend.errors import ClientError
from admin_backend.models.tenant_user import TenantUserStatus
from admin_backend.repositories.tenant_users import _repo as tenant_users_repo
from admin_backend.schemas.tenant import Pagination
from admin_backend.schemas.tenant_user import (
    TenantUserListItem,
    TenantUserListResponse,
    TenantUserRead,
)


router = APIRouter(prefix="/tenant-users", tags=["tenant-users"])


class TenantUserNotFoundError(ClientError):
    """Raised when a tenant_user lookup by id finds nothing.

    Per D-17, this fires for both genuinely missing rows and rows
    filtered by RLS. Frontend treats both cases identically.
    """
    code = "TENANT_USER_NOT_FOUND"
    http_status = 404
    public_message = "Tenant user not found"


@router.get(
    "",
    response_model=TenantUserListResponse,
    summary="List tenant users",
    description=(
        "List customer-side users. PLATFORM JWTs see all tenant_users "
        "across all tenants; TENANT JWTs see only their own tenant's "
        "users (RLS-scoped). Optional tenant_id filter for PLATFORM "
        "users to scope to a single tenant. Supports filter by status, "
        "search across email/full_name, sort, and pagination."
    ),
)
async def list_tenant_users(
    tenant_id: Annotated[
        UUID | None,
        Query(description="Filter to a single tenant. PLATFORM users use this for scoped views; TENANT users see only their own tenant regardless."),
    ] = None,
    status_filter: Annotated[
        TenantUserStatus | None,
        Query(alias="status", description="Filter by status: INVITED, ACTIVE, or SUSPENDED."),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Case-insensitive substring match on email and full_name."),
    ] = None,
    sort: Annotated[
        str,
        Query(description="Sort key: created_at_asc, created_at_desc, full_name_asc, full_name_desc, email_asc, email_desc."),
    ] = "created_at_desc",
    offset: Annotated[int, Query(ge=0, description="Pagination offset.")] = 0,
    limit: Annotated[int, Query(ge=1, le=200, description="Pagination limit (max 200).")] = 50,
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> TenantUserListResponse:
    items, total = await tenant_users_repo.list(
        session,
        tenant_id=tenant_id,
        status=status_filter,
        search=search,
        sort=sort,
        offset=offset,
        limit=limit,
    )

    return TenantUserListResponse(
        items=[TenantUserListItem.model_validate(u) for u in items],
        pagination=Pagination(
            limit=limit,
            offset=offset,
            total=total,
            has_more=(offset + limit) < total,
        ),
    )


@router.get(
    "/{user_id}",
    response_model=TenantUserRead,
    summary="Get tenant user by ID",
    description=(
        "Get a single tenant user by their UUID. PLATFORM JWTs can read "
        "any tenant user. TENANT JWTs can only read their own tenant's "
        "users — requesting another tenant's user_id returns 404 "
        "(RLS-as-404 per D-17, not 403, to avoid information disclosure)."
    ),
)
async def get_tenant_user(
    user_id: UUID,
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> TenantUserRead:
    user = await tenant_users_repo.get_by_id(session, user_id)
    if user is None:
        raise TenantUserNotFoundError()

    return TenantUserRead.model_validate(user)
```

### File 5: `src/admin_backend/main.py` — modify

Wire the new router. Mirror Step 5.1's pattern:

```python
from admin_backend.routers.v1 import tenant_users
app.include_router(tenant_users.router, prefix=settings.api_prefix)
```

### File 6: `tests/integration/test_tenant_users_router.py` — new

**Mirror Step 5.1's tests structurally. The cross-tenant isolation tests are LOAD-BEARING — they prove RLS-as-404 works end-to-end at the API layer.**

**Required prerequisite fixtures (build before writing tests):**

The cross-tenant tests reference `sample_tenant_id`, `tenant_a_id`, `tenant_b_id`, `tenant_b_user_id`. Per Pre-flight item 12, check whether any exist already. Three cases:

- **(a) All exist in conftest** → reuse, no work needed.
- **(b) None exist, easy to add** → add as session-scoped or function-scoped fixtures that look up real seed data via SQL. Mirror the existing fixture conventions (sync vs async, scope, return type). Skeleton:
  ```python
  @pytest.fixture
  def tenant_a_id(session_factory, platform_auth) -> UUID:
      """First tenant alphabetically (Buc-ee's per seed data)."""
      # Implementation: open a PLATFORM session, SELECT id FROM tenants ORDER BY name LIMIT 1
      # Mirror existing fixture pattern in conftest exactly
  ```
  Same shape for `tenant_b_id` (second tenant), `tenant_b_user_id` (any tenant_user belonging to tenant_b).

- **(c) Adding fixtures is non-trivial** (e.g., the existing pattern uses complex async-context-manager wrappers that don't compose well) → **STOP AND ASK**. Either:
  - We simplify the test design (look up IDs inline in each test instead of using fixtures), or
  - We add fixtures with a slightly different shape and document the divergence, or
  - We write a smaller scope of tests this step and add cross-tenant tests later.

**Required prerequisite — `_tenant_jwt(settings, *, tenant_id)` helper:** if Step 5.1 added it (per Pre-flight item 11(b)), reuse. If not, add it parallel to `_platform_jwt`:
```python
def _tenant_jwt(settings, *, tenant_id: UUID) -> str:
    return make_test_jwt(
        settings,
        user_type="TENANT",
        user_id=UUID("00000000-0000-0000-0000-000000000002"),
        tenant_id=tenant_id,
    )
```

**Do not skip the cross-tenant 404 test under any circumstance.** It's the load-bearing assertion proving RLS works end-to-end at the API layer. If fixture work is genuinely awkward, simplify by using inline lookups within the test, but ship the test.

Aim for ~13 tests:

```python
"""Integration tests for /api/v1/tenant-users endpoints.

The cross-tenant isolation tests (T9, T10) are load-bearing. They
verify that RLS scoping works through the full middleware → session →
repo → router stack, not just at the raw SQL layer.
"""
import pytest


# ─── List endpoint, PLATFORM context ─────────────────────────────

def test_list_tenant_users_as_platform(app_client, settings):
    """PLATFORM JWT sees all tenant_users (D-29 permissive)."""
    response = app_client.get(
        "/api/v1/tenant-users",
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 200
    body = response.json()
    # Seed has 17 tenant_users across 7 tenants
    assert body["pagination"]["total"] >= 17


def test_list_tenant_users_platform_with_tenant_filter(app_client, settings, sample_tenant_id):
    """PLATFORM with ?tenant_id=X scopes to that tenant only."""
    response = app_client.get(
        "/api/v1/tenant-users",
        params={"tenant_id": str(sample_tenant_id)},
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    # Every returned item should have the requested tenant_id
    assert all(u["tenant_id"] == str(sample_tenant_id) for u in items)


def test_list_tenant_users_filter_by_status(app_client, settings):
    """status=ACTIVE returns only ACTIVE users."""
    response = app_client.get(
        "/api/v1/tenant-users",
        params={"status": "ACTIVE"},
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert all(u["status"] == "ACTIVE" for u in items)


def test_list_tenant_users_search(app_client, settings):
    """Search matches on email or full_name."""
    response = app_client.get(
        "/api/v1/tenant-users",
        params={"search": "marcus"},  # adjust to a real seed user
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    if items:
        assert any("marcus" in u["email"].lower() or "marcus" in u["full_name"].lower() for u in items)


def test_list_tenant_users_sort(app_client, settings):
    """sort=email_asc returns alphabetical email order."""
    response = app_client.get(
        "/api/v1/tenant-users",
        params={"sort": "email_asc", "limit": 10},
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 200
    emails = [u["email"] for u in response.json()["items"]]
    assert emails == sorted(emails)


def test_list_tenant_users_invalid_sort(app_client, settings):
    """Unknown sort key returns 400."""
    response = app_client.get(
        "/api/v1/tenant-users",
        params={"sort": "not_a_real_sort"},
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 400


def test_list_tenant_users_pagination(app_client, settings):
    """limit=2 returns 2 items, has_more=true."""
    response = app_client.get(
        "/api/v1/tenant-users",
        params={"limit": 2, "offset": 0},
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) <= 2
    assert body["pagination"]["has_more"] is True


# ─── List endpoint, TENANT context (RLS-scoped) ──────────────────

def test_list_tenant_users_as_tenant_a(app_client, settings, tenant_a_id):
    """TENANT-A JWT sees only tenant A's users (RLS scoping)."""
    response = app_client.get(
        "/api/v1/tenant-users",
        headers={"Authorization": f"Bearer {_tenant_jwt(settings, tenant_id=tenant_a_id)}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    # All visible items should belong to tenant A
    assert all(u["tenant_id"] == str(tenant_a_id) for u in items)
    # And there should be at least one (Buc-ee's has 6 active users per seed)
    assert len(items) > 0


# ─── Detail endpoint ─────────────────────────────────────────────

def test_get_tenant_user_by_id_as_platform(app_client, settings):
    """PLATFORM can read any tenant_user by id."""
    list_resp = app_client.get(
        "/api/v1/tenant-users",
        params={"limit": 1},
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    user_id = list_resp.json()["items"][0]["id"]

    response = app_client.get(
        f"/api/v1/tenant-users/{user_id}",
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == user_id
    # Hidden fields absent
    assert "auth0_sub" not in body
    assert "created_by_user_id" not in body
    assert "created_by_user_type" not in body
    assert "suspended_by_user_id" not in body


def test_get_tenant_user_not_found(app_client, settings):
    """Unknown user_id returns 404 with TENANT_USER_NOT_FOUND code."""
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = app_client.get(
        f"/api/v1/tenant-users/{fake_id}",
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "TENANT_USER_NOT_FOUND"


# ─── Cross-tenant isolation (LOAD-BEARING) ───────────────────────

def test_get_tenant_user_cross_tenant_returns_404(
    app_client, settings, tenant_a_id, tenant_b_user_id,
):
    """LOAD-BEARING: TENANT-A user requesting TENANT-B's user_id returns 404.

    Verifies RLS-as-404 per D-17. The row is invisible to tenant A's
    session due to RLS; repo returns None; router converts to 404.
    NOT 403 — no information disclosure about whether the user_id exists.
    """
    response = app_client.get(
        f"/api/v1/tenant-users/{tenant_b_user_id}",
        headers={"Authorization": f"Bearer {_tenant_jwt(settings, tenant_id=tenant_a_id)}"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "TENANT_USER_NOT_FOUND"


def test_list_tenant_users_cross_tenant_filter_returns_empty(
    app_client, settings, tenant_a_id, tenant_b_id,
):
    """TENANT-A querying ?tenant_id=B returns empty list.

    Mechanism: RLS adds `tenant_id = A` to the WHERE clause for the
    TENANT-A session; the application filter adds `tenant_id = B`.
    Combined as AND, no row satisfies both, so the result is empty.
    """
    response = app_client.get(
        "/api/v1/tenant-users",
        params={"tenant_id": str(tenant_b_id)},
        headers={"Authorization": f"Bearer {_tenant_jwt(settings, tenant_id=tenant_a_id)}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert items == []


# ─── Auth ────────────────────────────────────────────────────────

def test_list_tenant_users_no_jwt(app_client):
    """No JWT returns 401."""
    response = app_client.get("/api/v1/tenant-users")
    assert response.status_code == 401
```

The fixtures `sample_tenant_id`, `tenant_a_id`, `tenant_b_id`, `tenant_b_user_id` need to be available in conftest. They can either:

- (a) Be added as fixtures that look up real IDs from seed data via SQL queries
- (b) Be replaced by inline lookups within each test

Option (a) is cleaner. Per pre-flight item 12, check what's already in conftest. If `make_tenant_user` exists, the fixtures can build on it. If not, fixtures query seed data directly:

```python
@pytest.fixture
def tenant_a_id(session_factory, platform_auth) -> UUID:
    """Return the UUID of the first tenant in seed (alphabetically)."""
    async def _lookup():
        async for session in get_tenant_session(platform_auth, session_factory):
            r = await session.execute(text("SELECT id FROM tenants ORDER BY name LIMIT 1"))
            return r.scalar_one()
    return asyncio.run(_lookup())
```

Build the helpers however the existing pattern dictates. **Stop and ask** if the fixture machinery is awkward to extend.

The `_tenant_jwt(settings, tenant_id=...)` helper definitely needs to exist. If Step 5.1 added one, reuse. If not, add it parallel to `_platform_jwt`:

```python
def _tenant_jwt(settings, *, tenant_id: UUID) -> str:
    return make_test_jwt(
        settings,
        user_type="TENANT",
        user_id=UUID("00000000-0000-0000-0000-000000000002"),  # synthetic
        tenant_id=tenant_id,
    )
```

### File 7: `docs/endpoints/tenant-users.md` — new

Mirror `docs/endpoints/tenants.md`. 8 sections per endpoint. Two endpoints documented: `GET /api/v1/tenant-users` and `GET /api/v1/tenant-users/{user_id}`.

Behaviour notes specific to tenant-users:

- **RLS scope.** PLATFORM JWT sees all tenant_users across all tenants (D-29 permissive). TENANT JWT sees only own tenant.
- **Cross-tenant detail returns 404.** TENANT-A asking for TENANT-B's user_id receives 404, not 403. Per D-17 (RLS-as-404). Frontend should treat 404 as "user not found" without distinguishing access reasons.
- **`tenant_id` filter for PLATFORM.** Use `?tenant_id=X` to scope a list query to a single tenant. For TENANT JWTs the filter is functionally redundant (RLS already scopes); using it just makes the intent explicit.
- **Hidden fields.** `auth0_sub`, audit-actor `*_user_id` and `*_user_type` columns hidden from response.

### File 8: `BUILD_PLAN.md` — modify

Update Step 5.2 status TODO → DONE in same commit.

```markdown
## Step 5.2 — Tenant Users resource

**Status:** DONE
**Owner:** CLAUDE_CODE

**Goal.** Customer-side users readable by both PLATFORM and TENANT JWTs, RLS-scoped.

**Scope in.**
- `TenantUser` model + `TenantUserStatus` enum.
- `TenantUserRead`, `TenantUserListItem`, `TenantUserListResponse` schemas. Audit-actor columns and auth0_sub hidden.
- `TenantUsersRepo` with `list(session, *, tenant_id, status, search, sort, offset, limit)` and `get_by_id(session, user_id)`. Stateless-singleton pattern.
- Router: `GET /api/v1/tenant-users`, `GET /api/v1/tenant-users/{user_id}`. Both PLATFORM and TENANT JWTs accepted; RLS scopes visibility automatically. Optional `?tenant_id=X` filter for PLATFORM to scope to a single tenant.
- `TenantUserNotFoundError` (404, code TENANT_USER_NOT_FOUND).
- ~13 integration tests including 2 LOAD-BEARING cross-tenant isolation tests (T9, T10) that prove RLS-as-404 works end-to-end.
- `docs/endpoints/tenant-users.md` following the 8-section structure.
- Lightweight TenantUser stub at `models/_lightweight_stubs.py` (from Step 3.3) replaced by the full ORM model. Imports updated.

**Scope out.**
- Aggregates (no role count, etc.). Add later if frontend needs.
- Stats endpoint.
- Write endpoints (post-v0).
- Tenant-side roles (those land at Step 6.1, RBAC).
```

### File 9: `CLAUDE.md` — modify

- **Current state → Completed:** Step 5.2 bullet covering the model + schemas + repo + router + tests + endpoint doc, AND the replacement of the lightweight TenantUser stub from Step 3.3 (FN-AB-16-adjacent cleanup; not the FN-AB-16 itself which was tenant_module_access — but a related stub-cleanup). Note the load-bearing cross-tenant 404 test (T9) as the assertion that v0's auth model works end-to-end.
- **Schema state line:** no change.
- **Extend the "v0 auth model" convention note** added by Step 5.1 (in the Code conventions section). Step 5.1 introduced the note; this step adds the multi-user-type pattern as a concrete instance:
  > Step 5.2's `/api/v1/tenant-users` is the canonical multi-user-type endpoint pattern: no `_require_platform_auth` gate, RLS scopes data visibility per session GUCs, optional application-layer filter (`?tenant_id=X`) for explicit scoping by PLATFORM users. Future endpoints accepting both user_types follow this shape; future PLATFORM-only endpoints follow Step 5.1's gated pattern.
  >
  > Cross-tenant access by TENANT users surfaces as 404 (RLS-as-404 per D-17), not 403, to avoid information disclosure. Test T9 in `test_tenant_users_router.py` is the load-bearing assertion that this works end-to-end.
- **No new D-XX entries** unless implementation surfaces something genuine. The "RLS-as-404 over 403" is already captured by D-17; this step verifies the property at the API layer rather than introducing a new decision.
- **No new FN-AB entries.** If the lightweight TenantUser stub was tracked anywhere (likely informally, not as a numbered FN-AB), mark resolved in CLAUDE.md's relevant section.

### File 10: `prompts/step-5_2-tenant-users-2026-05-03.md` — new

This prompt file. Bundled per the per-step convention.

### File 11: `docs/endpoints/openapi.json` — re-export

After all code is in and tests pass:

```bash
uv run uvicorn admin_backend.main:app --reload &
sleep 2
curl -s http://localhost:8000/api/v1/openapi.json | jq '.' > docs/endpoints/openapi.json
```

The new `/api/v1/tenant-users` and `/api/v1/tenant-users/{user_id}` endpoints with their full schemas appear in the spec.

---

## Implementation hints

### The `_lightweight_stubs.py` cleanup

Per memory of Step 3.3, `models/_lightweight_stubs.py` contains a TenantUser stub used by the tenants Repo's correlated subqueries (counting active users per tenant for the `num_users_active` aggregate). Once `TenantUser` is a full ORM model, the stub becomes redundant.

**Important:** the stub was deliberately minimal — declaring only `id`, `tenant_id`, and the `status` enum column. The full `TenantUser` model adds many more columns. The tenants Repo's subquery references `TenantUser.id`, `TenantUser.tenant_id`, `TenantUser.status` — the full model has all three identically named, so the subquery should keep working.

What changes:

- The stub class and its declaration in `_lightweight_stubs.py` can be removed.
- The import in `repositories/tenants.py` switches from the stub to the new full model.
- The `Store` stub (also in `_lightweight_stubs.py`) is unaffected — Step 4.5 (Stores) hasn't shipped yet.

Verify the tenants Repo tests still pass after the swap. Specifically, **L9 (per-row aggregates scope correctly via .correlate(Tenant))** is the load-bearing test that proves the swap didn't break the tenant-list endpoint.

### `actor_user_type_enum` declaration

The Pattern (b) audit-actor type columns use `actor_user_type_enum`, defined in the shared utilities migration. ORM declaration:

```python
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM

class TenantUser(Base):
    # ...
    created_by_user_type: Mapped[ActorUserType | None] = mapped_column(
        PG_ENUM(
            "PLATFORM", "TENANT",
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
```

If Step 5.1 didn't introduce an `ActorUserType` Python enum (because platform_users uses Pattern (a), no actor-type column), this step adds it. **Place inside `models/tenant_user.py` alongside the model class**, following the pattern established in `models/tenant.py` (where TenantStatus, TenantTier, etc. live alongside Tenant). No separate enums module needed.

### TENANT JWT in tests — prerequisite

Step 5.1's tests probably skipped the TENANT-JWT-rejection test (per the Stop-and-ask in Step 5.1's prompt). This step **must** have a working `_tenant_jwt(settings, tenant_id=...)` helper. If it doesn't exist yet:

- Build it parallel to `_platform_jwt`. The shape is just `make_test_jwt(settings, user_type="TENANT", user_id=..., tenant_id=...)`.
- Place where `_platform_jwt` lives (likely a test helpers module).
- Step 5.1's commit may be amended retroactively to add the helper if convenient — or this step adds it fresh.

### FastAPI route ordering — none needed

Unlike Step 3.3 (where `/stats` had to come before `/{tenant_id}` because FastAPI is first-match-wins), this step has no static routes that could collide with `/{user_id}`. Order doesn't matter; declare in the natural order.

---

## Testing and regression discipline

### New tests added by this step

~13 integration tests, including the 2 load-bearing cross-tenant isolation tests.

### Tests deliberately not added

- TENANT-creating-platform-user attempts (write endpoint not in scope).
- Per-role visibility distinctions (RBAC-level, Step 6.1).
- Multi-tenant aggregates (no aggregates in this step).

### Regression risk surface introduced by this step

1. **The cross-tenant 404 must work end-to-end.** If `test_get_tenant_user_cross_tenant_returns_404` fails or is weakened, RLS isn't actually enforcing isolation through the API, and any tenant could potentially probe other tenants' user_ids. **This test is non-negotiable.**

2. **The lightweight stub swap mustn't break tenants endpoints.** After replacing the TenantUser stub with the full model, Step 3.3's test L9 must still pass. Run the full suite, not just the new tests.

3. **Hidden fields stay hidden** (per D-31). Tests assert that the seven hidden fields (`auth0_sub`, three `*_user_id` columns, three `*_user_type` columns) are absent from response bodies.

4. **`tenant_id` filter doesn't break TENANT-scoped sessions.** A TENANT JWT querying `?tenant_id=other_tenant_id` should return empty, not 500 or 403. The RLS policy filters at the SQL level; the additional WHERE clause just intersects to empty.

5. **No regressions in existing tests.** Step 5.1's pytest count was ~125; this step should land around 138 (125 + ~13 new). All prior tests must still pass.

### Verification harness (run all six; all must be green)

```bash
# 1. Re-seed the DB before pytest. Tests will truncate and re-create
# their own state, but starting from a known seed makes test failures
# easier to diagnose than starting from an arbitrary partial state.
uv run python -m scripts.seed_dev_data --reset

# 2. Full pytest suite — new + regression. The cross-tenant tests are
#    load-bearing — explicitly verify they pass.
uv run pytest -v
uv run pytest -v -k "cross_tenant"   # Confirms isolation tests run

# Re-seed again after pytest (which may have truncated) before manual curl
uv run python -m scripts.seed_dev_data --reset

# 3. mypy strict
uv run mypy --strict src/admin_backend

# 4. Pre-flight checker
./scripts/check_setup.sh

# 5. Migration round-trip — no new migration but verify state hasn't drifted
uv run alembic current   # should still show 0644a4186e48

# 6. Manual curl verification (server must be running; seed must be present)
PLATFORM_JWT=$(...)  # use scripts/jwt/generate.sh anjali@ithina.ai
TENANT_JWT=$(...)    # use scripts/jwt/generate.sh <tenant-user-email>

# PLATFORM sees all
curl -s -H "Authorization: Bearer $PLATFORM_JWT" \
  "http://localhost:8000/api/v1/tenant-users" | jq '.pagination.total'
# Expected: 17 (or current seed count)

# TENANT sees own only
curl -s -H "Authorization: Bearer $TENANT_JWT" \
  "http://localhost:8000/api/v1/tenant-users" | jq '.pagination.total'
# Expected: matches the count of users in that tenant

# Cross-tenant 404
TENANT_B_USER_ID="..."  # a user_id from a tenant other than the JWT's
curl -s -H "Authorization: Bearer $TENANT_JWT" \
  "http://localhost:8000/api/v1/tenant-users/${TENANT_B_USER_ID}" | jq
# Expected: {"code": "TENANT_USER_NOT_FOUND", ...}
```

Expected pytest count: ~138 passes (125 prior + ~13 new). Smoke test unchanged at 74 PASS.

If any leg is not green, **report rather than commit**.

---

## Scope out

- **Aggregates on tenant_users.** Role assignment count per user; last-login. None for v0; add when frontend asks.
- **Stats endpoint.** List endpoint pagination total covers it.
- **Write endpoints.** Invite / accept / suspend (post-v0 per FN-AB-12).
- **Auth0 webhook handler.** Separate concern.
- **Per-role permission visibility.** RBAC, Step 6.1.
- **Tenant-creating-tenant-users flow.** Tenant admin invites a tenant user. Post-v0 self-serve flow.
- **Step 5.3 (org_nodes).** Separate prompt, separate commit. Lands after this.

---

## Stop and ask if

- The lightweight TenantUser stub from Step 3.3 has unexpected dependencies — surface and we'll plan the swap carefully.
- The `ActorUserType` Python enum placement — in this step's prompt placed alongside `TenantUser` in `models/tenant_user.py`. If existing convention dictates a different location (e.g., a shared enums module), surface and align.
- The conftest fixtures for `tenant_a_id`, `tenant_b_id`, `tenant_b_user_id` are awkward to construct — surface and we'll simplify (e.g., inline lookups, or simpler test data setup).
- The `_tenant_jwt(settings, tenant_id=...)` helper doesn't exist and adding it would require widespread test-helper changes — surface; we may need a smaller scope or a parallel helper file.
- `make_tenant_user` factory is missing from conftest — surface; we'll either add it or write tests against seed data only.
- Running the cross-tenant isolation tests requires real seed data and the seed has been truncated by some previous test — surface and we'll add an explicit re-seed in a fixture or document the requirement.

---

## Acceptance criteria

- 11 files created/modified.
- 2 endpoints live: `GET /api/v1/tenant-users` and `GET /api/v1/tenant-users/{user_id}`.
- ~13 new integration tests pass; 2 cross-tenant isolation tests are explicitly green.
- All prior tests still pass (Step 5.1's ~125 pytest count holds).
- mypy strict clean.
- check_setup 35/35.
- Smoke test unchanged at 74 PASS.
- BUILD_PLAN.md Step 5.2 entry updated to DONE.
- `docs/endpoints/tenant-users.md` follows tenants.md's 8-section structure.
- `docs/endpoints/openapi.json` regenerated with new endpoints visible.
- **OpenAPI spec quality:** `/api/v1/tenant-users` shows clear summary, description, parameter descriptions and examples; response schema with per-field descriptions; the cross-tenant-404 behaviour mentioned in the description.
- Manual curl verification: PLATFORM sees all 17 tenant_users; TENANT sees own only; cross-tenant detail returns 404 with TENANT_USER_NOT_FOUND code.
- Lightweight TenantUser stub at `models/_lightweight_stubs.py` removed; tenants Repo's tests still pass.

---

## Report (BEFORE proposing commit)

Five bundles per the convention:

1. **Code:** files created/modified with line counts; sample curl outputs (PLATFORM list, TENANT list, cross-tenant 404, detail success) confirming behaviour. Confirmation that the lightweight TenantUser stub was removed and tenants Repo tests still pass.
2. **CLAUDE.md updates:** Step 5.2 Completed bullet; Step 3.3's stub-removal note; no new D-XX or FN-AB unless implementation surfaced something genuine.
3. **BUILD_PLAN.md updates:** Step 5.2 entry updated to DONE; scope-in/acceptance match what shipped.
4. **architecture.md updates:** likely "no change" — endpoint addition. Confirm by reading the relevant section before declaring no edit.
5. **OpenAPI spec snapshot:** `docs/endpoints/openapi.json` regenerated with the two new endpoints visible.
6. **Prompt file:** `prompts/step-5_2-tenant-users-2026-05-03.md` confirmed in commit set.

Plus: pytest count delta (was ~125, now ~138); cross-tenant isolation tests explicitly listed as green; mypy status; check_setup; alembic current; smoke test count.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
