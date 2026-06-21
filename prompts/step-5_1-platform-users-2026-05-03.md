# Prompt — Step 5.1: Platform Users resource (model + schema + repo + router + endpoint doc)

> Generated 2026-05-03. Revised 2026-05-03 (v2: hardened auth-dependency naming via explicit pre-flight verification; clarified `_require_platform_auth` is provisional pending pattern check; replaced async/sync-mixed factory skeleton with explicit "mirror existing" instruction; corrected OpenAPI path to `docs/endpoints/openapi.json` throughout; made `_tenant_jwt` helper an explicit deliverable; added re-seed step to verification harness). Revised 2026-05-03 (v3: added Ground rules and v0 auth model preamble; tightened File 10 CLAUDE.md update to codify the v0 binary-user_type-vs-RBAC framing).
> Paste this entire block into a fresh Claude Code session to start Step 5.1.
> Two GET endpoints (list + detail) for platform_users — Ithina staff users. Mirrors the Step 3.3 pattern; lighter than 3.3 because no aggregates, no stats endpoint, no RLS surface. Frontend integration follow-on after the tenants endpoints land.

---

## Ground rules and v0 auth model

The four ground rules this step participates in:

1. **PLATFORM users can view (and post-v0 configure) tenant users across all tenants.** Step 5.1 doesn't implement this — it ships in Step 5.2.

2. **TENANT users can view (and post-v0 configure) tenant users belonging to the same tenant only.** Step 5.1 doesn't implement this — it ships in Step 5.2 via RLS.

3. **RBAC refines further — scope, module access, actions.** RBAC will distinguish e.g. "Module Admin can list platform users but not see suspension details" from "Super Admin sees everything." **Not in scope for this step.** RBAC enforcement is Step 6.1; seed data already exists per Step 3.5.

4. **Platform users are visible only to PLATFORM JWTs.** Tenant JWTs receive 403 PLATFORM_ACCESS_REQUIRED. This is Step 5.1's primary deliverable.

**v0 auth model (until Step 6.1 lands):**

The router-layer auth check is **binary user_type-based** (PLATFORM vs TENANT), not role-based. This is the *coarse* boundary; RBAC adds *fine* distinctions.

- Platform-only endpoint (this step's `/api/v1/platform-users`): explicit `_require_platform_auth(auth)` gate → 403 if `user_type != PLATFORM`.
- Multi-user-type endpoint (Step 5.2's `/api/v1/tenant-users`, plus existing `/api/v1/tenants`): no explicit gate; both PLATFORM and TENANT JWTs accepted; RLS scopes data visibility per session GUCs.
- No router-layer permission check (e.g., "does this user have ADMIN.USERS.VIEW") in v0. That's Step 6.1.

The PLATFORM-only gate this step introduces is the first concrete instance of v0 router-layer auth tier checking. Step 5.2's tenant_users router does NOT use this gate (both user_types accepted). Future PLATFORM-only endpoints inherit Step 5.1's pattern.

**This framing must be reflected in CLAUDE.md when this step ships** — see File 10 below.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 3.6 at HEAD (most recent commit). The Pre-push hygiene commit may sit on top; that's fine.
3. `uv run alembic heads` — confirm output is `0644a4186e48` (Step 3.6's revision). No new migration in this step; verifying no drift.
4. Read `CLAUDE.md` fully. Focus on:
   - **D-13** — Audit-actor patterns. `platform_users` uses Pattern (a) self-FK (only platform_users create other platform_users; no actor-type column needed).
   - **D-15** — `__table_args__["schema"]` from environment.
   - **D-17** — Missing or RLS-filtered rows surface as None from the repo; router converts to 404.
   - **D-21** — UUIDv7 default; `id` carries no Python or ORM-side default; DB DEFAULT `uuidv7()` is authoritative.
   - **D-24** — AuthContext is the only path for tenant context; never accept a `tenant_id` argument on visibility-bearing repo methods (not directly relevant here since platform_users has no tenant_id, but reinforces "session GUCs flow visibility, not method args").
   - **D-29** — PLATFORM RLS visibility. `platform_users` has NO RLS — it's platform-global reference data. Access is controlled by application-layer authorisation: only PLATFORM JWTs reach this endpoint.
   - **D-30** — List-only response envelope (`{items, pagination}` for collections; single resource returned directly).
   - **D-31** — Response field semantics are append-only.
   - "Note on PG enum columns" subsection.
   - "Note on Repository pattern".
5. Read `db/raw_ddl/Ithina_postgres_SQL_DDL_platform_users_v1.sql` — full column list, enum, CHECK constraints, indexes, and the "No Row-Level Security" section confirming application-layer auth is the boundary.
6. Read `src/admin_backend/models/tenant.py`. The `Tenant` model is the canonical reference for: `__tablename__`, schema parameterisation via `get_settings().db_schema`, `id` with no Python default (DB DEFAULT fires), `server_default=FetchedValue()` declarations on DB-default columns, enum column declaration via `postgresql.ENUM(create_type=False, native_enum=True)`. Mirror this shape exactly for `PlatformUser`.
7. Read `src/admin_backend/schemas/tenant.py`. Pydantic v2 patterns: `ConfigDict(from_attributes=True)`, audit-actor IDs hidden from response, NUMERIC-as-string serialisation, ISO 8601 timestamps with offset. Mirror for `PlatformUserRead` (the audit-actor hide is especially important — `created_by_user_id`, `updated_by_user_id`, `suspended_by_user_id` all stay out of the response).
8. Read `src/admin_backend/repositories/tenants.py`. **Confirm exactly:** (a) the stateless-singleton constructor pattern (`_repo = TenantsRepo()` at module level), (b) methods take `session` as first positional argument, (c) import paths for SQLAlchemy primitives. The skeletons below use placeholders; replace before writing.
9. Read `src/admin_backend/routers/v1/tenants.py`. **Confirm exactly the following BEFORE writing File 4 below:**
   - (a) The exact import for the session dependency. The skeleton's `from admin_backend.dependencies import get_tenant_session_dep` was correct at Step 3.6; verify it still is.
   - (b) **How AuthContext is retrieved inside handlers.** The skeleton in File 4 imports `get_auth_context_dep` — this name is a guess. The actual pattern may be: a `Request`-typed parameter that reads `request.state.auth`, a different `Depends(...)` name (e.g., `get_current_auth`, `auth_dep`), or implicit via middleware (no Depends needed). **Do not assume; copy the exact pattern from tenants router.**
   - (c) The status query param uses `alias="status"` (FastAPI's `status` collision avoidance). Mirror exactly.
   - (d) The `responses` dict for OpenAPI error documentation.
   - (e) Whether `Pagination` is imported from `schemas.tenant` or has been relocated to a common module. If still in `schemas.tenant`, the prompt's import is correct; if relocated (e.g., to `schemas._common`), update the import.
   - Auth happens via middleware; there is no separate `Depends(require_auth)` (middleware populates `request.state.auth`).
10. Read `tests/integration/test_tenants_router.py`. **Confirm exactly the following BEFORE writing Files 6 and 7 below:**
    - (a) The `app_client` fixture name and the sync `TestClient` usage.
    - (b) The `_platform_jwt(settings)` helper signature and usage. **Where is this defined?** (Likely a top-level helper in the same file or in a shared test utilities module.)
    - (c) **Does a `_tenant_jwt(...)` helper already exist?** Check both this file and any test-helpers module. Step 5.1 needs one for the TENANT-JWT-rejection test (load-bearing); see File 7 below for the explicit deliverable.
    - (d) The headers pattern for authorised requests.
    - (e) How `make_tenant` and `make_tenant_user` are constructed in conftest — whether they're sync or async fixtures, how teardown is handled, what session pattern they use.
11. Read `tests/integration/conftest.py`. **Critical for File 6:** confirm whether the existing factories (`make_tenant`, `make_tenant_user`) are sync or async, how they handle commits and teardown, and what session-dependency pattern they use. The skeleton in File 6 below shows a generic shape; **the actual implementation MUST mirror the existing factories' shape exactly** — including async/sync, fixture scope, and whether they use `session_factory()` directly vs `get_tenant_session(...)`.
12. Read `docs/endpoints/tenants.md`. This is the canonical 8-section endpoint doc. `docs/endpoints/platform-users.md` mirrors this structure.
13. Read `BUILD_PLAN.md` Step 5.1 entry — the existing entry's scope-in/scope-out is the starting point for what ships here.
14. **Verify the OpenAPI spec canonical location.** Run `ls -la docs/openapi.json docs/endpoints/openapi.json 2>/dev/null` — Step 3.6's pre-push hygiene commit relocated the canonical spec to `docs/endpoints/openapi.json`. The references in this prompt all use that path. If for some reason the location has moved again, surface and we'll align.
15. Read this prompt fully.

---

## Step ID and intent

**Step 5.1** — Platform Users resource. Two GET endpoints + ORM model + schemas + repo + tests + endpoint doc. Pattern mirrored from Step 3.3 with three deliberate simplifications (no aggregates, no stats endpoint, no RLS surface).

Five concrete deliverables:

1. **`PlatformUser` ORM model** with `PlatformUserStatus` enum, mapping the 14 columns of `platform_users_v1.sql`.
2. **`PlatformUserRead` Pydantic schema** + `PlatformUserListItem` + `PlatformUserListResponse`. Audit-actor IDs hidden.
3. **`PlatformUsersRepo`** with `list(session, *, status, search, sort, offset, limit) -> tuple[list[PlatformUser], int]` and `get_by_id(session, user_id) -> PlatformUser | None`.
4. **Router** with `GET /api/v1/platform-users` (list with filter/search/sort/pagination) and `GET /api/v1/platform-users/{user_id}` (detail). Plus `PlatformUserNotFoundError` for 404 on missing/unknown id.
5. **Integration tests** (~10 tests) covering happy paths under PLATFORM JWT, auth requirements (TENANT JWT rejected — see scope discussion), filter/search/sort/pagination, and 404 path.
6. **`docs/endpoints/platform-users.md`** following `tenants.md`'s 8-section pattern.
7. **`make_platform_user` factory** added to `tests/integration/conftest.py` — **conditional, only if tests genuinely need a user shape the seed doesn't provide.** See File 6 below for the decision procedure.

CLAUDE_CODE step. **No DDL changes.** No migration. No schema impact. Lighter than Step 3.3 because of the three simplifications above.

---

## Source-of-truth specification

### File 1: `src/admin_backend/models/platform_user.py` — new

Maps `platform_users_v1.sql`. Single `PlatformUserStatus` enum (`INVITED`, `ACTIVE`, `SUSPENDED`). 14 columns: `id`, `auth0_sub`, `email`, `full_name`, `status`, `invited_at`, `invitation_accepted_at`, `suspended_at`, `suspended_by_user_id`, `created_at`, `created_by_user_id`, `updated_at`, `updated_by_user_id`.

Mirror `Tenant`'s shape:

- `__tablename__ = "platform_users"`
- `__table_args__["schema"]` from `get_settings().db_schema` per D-15
- `id`: no Python default; `server_default=FetchedValue()` so SQLAlchemy omits it from INSERT and reads back via RETURNING
- Audit-actor FKs (`created_by_user_id`, `updated_by_user_id`, `suspended_by_user_id`): self-referential FK to `platform_users.id` with `ON DELETE RESTRICT, ON UPDATE RESTRICT`. Per D-13 Pattern (a), no `*_by_user_type` companion column. Whether to declare a SQLAlchemy `relationship("PlatformUser", remote_side=[...])` or leave them as raw UUID columns: **leave raw**. Self-referential relationships add complexity for no gain at v0; the test factory and the response schema both treat the actor IDs as opaque UUIDs (and the response *hides* them anyway).
- `status` column declared via `postgresql.ENUM("INVITED", "ACTIVE", "SUSPENDED", name="platform_user_status_enum", create_type=False, native_enum=True, values_callable=lambda e: [m.value for m in e])`. The `create_type=False` is essential — the enum already exists in the DB from the initial migration; ORM must not try to create it.
- `created_at`, `updated_at`: `server_default=FetchedValue()` so SQLAlchemy reads them back via RETURNING after INSERT.
- The `auth0_sub` UNIQUE constraint and the `(email)` UNIQUE constraint are DB-side; the model doesn't need to redeclare them.

### File 2: `src/admin_backend/schemas/platform_user.py` — new

Three classes:

- `PlatformUserRead` — full single-resource response. Fields: `id`, `email`, `full_name`, `status`, `invited_at`, `invitation_accepted_at`, `suspended_at`, `created_at`, `updated_at`. **Hidden:** `auth0_sub` (Auth0-specific, not for UI), `suspended_by_user_id`, `created_by_user_id`, `updated_by_user_id` (audit-actor IDs hidden per Step 3.1 convention).
- `PlatformUserListItem` — same shape as `PlatformUserRead` for v0 (small dataset; no need for a slimmer list shape). Single class can serve both purposes if convenient.
- `PlatformUserListResponse` — `{items: list[PlatformUserListItem], pagination: Pagination}` per D-30. Reuse the `Pagination` class from `schemas/tenant.py` (or wherever it currently lives — check during pre-flight).

All Pydantic v2 with `ConfigDict(from_attributes=True)`. ISO 8601 timestamps with offset.

### File 3: `src/admin_backend/repositories/platform_users.py` — new

**The skeleton below uses placeholder import paths and constructor patterns. Before writing, complete Pre-flight item 8 (read `repositories/tenants.py`) and replace with the actual stateless-singleton conventions used by `TenantsRepo`. Do not assume the skeleton's exact form is correct.**

```python
"""PlatformUsersRepo — read-only data access for the platform_users table.

Owns SELECT queries on platform_users. The Repo does NOT set tenant
context, NOT begin transactions, NOT handle commits/rollbacks. The
session passed in already carries app.user_type GUC set by the request
middleware; platform_users has no RLS so the GUCs don't actually filter
anything here, but the session-flow consistency is preserved.

Application-layer authorisation: this Repo's endpoints are only reachable
via PLATFORM JWTs (enforced at router layer). Without that gate, any
authenticated TENANT JWT would be able to read all staff users.

Per D-17, missing rows surface as None from get_by_id. The router
converts None to 404.
"""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.models.platform_user import PlatformUser, PlatformUserStatus


SORT_MAP = {
    "created_at_asc": PlatformUser.created_at.asc(),
    "created_at_desc": PlatformUser.created_at.desc(),
    "full_name_asc": PlatformUser.full_name.asc(),
    "full_name_desc": PlatformUser.full_name.desc(),
    "email_asc": PlatformUser.email.asc(),
    "email_desc": PlatformUser.email.desc(),
}


class PlatformUsersRepo:
    """Read-only repository for platform_users."""

    async def list(
        self,
        session: AsyncSession,
        *,
        status: PlatformUserStatus | None = None,
        search: str | None = None,
        sort: str = "created_at_desc",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[PlatformUser], int]:
        """Return platform_users matching filters, plus the total count
        for pagination.

        - status: filter to a single status (typically ACTIVE).
        - search: case-insensitive ILIKE across email and full_name.
        - sort: one of SORT_MAP keys; raises ValueError if unknown.
        - offset / limit: pagination.

        Returns (items, total_count). total_count counts rows matching
        filters but ignoring offset/limit, for has_more computation.
        """
        if sort not in SORT_MAP:
            raise ValueError(f"unknown sort key: {sort}")

        stmt = select(PlatformUser)
        count_stmt = select(func.count()).select_from(PlatformUser)

        if status is not None:
            stmt = stmt.where(PlatformUser.status == status)
            count_stmt = count_stmt.where(PlatformUser.status == status)

        if search:
            pattern = f"%{search}%"
            search_clause = PlatformUser.email.ilike(pattern) | PlatformUser.full_name.ilike(pattern)
            stmt = stmt.where(search_clause)
            count_stmt = count_stmt.where(search_clause)

        # Stable secondary sort by id for deterministic pagination
        stmt = stmt.order_by(SORT_MAP[sort], PlatformUser.id.asc())
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
    ) -> PlatformUser | None:
        """Return the platform_user with this id, or None if not found."""
        stmt = select(PlatformUser).where(PlatformUser.id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


# Module-level singleton, mirroring TenantsRepo's pattern.
_repo = PlatformUsersRepo()
```

### File 4: `src/admin_backend/routers/v1/platform_users.py` — new

**The skeleton below uses placeholder import paths AND a provisional `_require_platform_auth` pattern. Before writing, complete Pre-flight item 9 (read `routers/v1/tenants.py`) and replace ALL imports + dependency names with the actual conventions. Do not assume any name in this skeleton is correct without verifying.**

**Critical Stop-and-ask:** The `_require_platform_auth` + `PlatformAccessRequiredError` pattern below is NEW — Step 3.3's tenants router doesn't have an auth-tier gate (RLS scopes there). If the codebase already has a different pattern for "this endpoint requires PLATFORM" (e.g., a router-level dependency, a decorator, an existing error class), surface it and use the existing pattern; do NOT introduce a new one. If genuinely no PLATFORM-gate exists yet, the pattern below becomes the convention — this warrants surfacing as a CLAUDE.md note (see File 10 below).

The skeleton:

```python
"""Router for GET /api/v1/platform-users (list) and /{user_id} (detail).

Auth: PLATFORM JWTs only. The middleware authenticates the request and
populates AuthContext; this router additionally enforces user_type ==
PLATFORM at the handler layer (defence in depth — the middleware doesn't
distinguish PLATFORM from TENANT for routing purposes).

Why PLATFORM-only: platform_users contains Ithina staff identities. A
tenant user has no business reading the staff directory. RLS doesn't
enforce this (platform_users has no RLS), so application-layer auth
must.

Per D-30: list returns {items, pagination}; detail returns the resource
directly. Per D-31: field semantics frozen append-only.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.context import AuthContext
from admin_backend.dependencies import get_tenant_session_dep, get_auth_context_dep
from admin_backend.errors import ClientError
from admin_backend.models.platform_user import PlatformUserStatus
from admin_backend.repositories.platform_users import _repo as platform_users_repo
from admin_backend.schemas.platform_user import (
    PlatformUserListItem,
    PlatformUserListResponse,
    PlatformUserRead,
)
from admin_backend.schemas.tenant import Pagination  # reuse


router = APIRouter(prefix="/platform-users", tags=["platform-users"])


class PlatformUserNotFoundError(ClientError):
    """Raised when a platform_user lookup by id finds nothing."""
    code = "PLATFORM_USER_NOT_FOUND"
    http_status = 404
    public_message = "Platform user not found"


class PlatformAccessRequiredError(ClientError):
    """Raised when a non-PLATFORM JWT calls a PLATFORM-only endpoint."""
    code = "PLATFORM_ACCESS_REQUIRED"
    http_status = 403
    public_message = "This endpoint requires platform access"


def _require_platform_auth(auth: AuthContext) -> None:
    """Raise if the calling user_type is not PLATFORM."""
    if auth.user_type != "PLATFORM":
        raise PlatformAccessRequiredError()


@router.get(
    "",
    response_model=PlatformUserListResponse,
    summary="List platform users",
    description=(
        "List Ithina staff users. PLATFORM JWTs only. "
        "Supports filter by status, search across email/full_name, "
        "sort, and pagination."
    ),
)
async def list_platform_users(
    status_filter: Annotated[
        PlatformUserStatus | None,
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
    auth: AuthContext = Depends(get_auth_context_dep),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> PlatformUserListResponse:
    _require_platform_auth(auth)

    items, total = await platform_users_repo.list(
        session,
        status=status_filter,
        search=search,
        sort=sort,
        offset=offset,
        limit=limit,
    )

    return PlatformUserListResponse(
        items=[PlatformUserListItem.model_validate(u) for u in items],
        pagination=Pagination(
            limit=limit,
            offset=offset,
            total=total,
            has_more=(offset + limit) < total,
        ),
    )


@router.get(
    "/{user_id}",
    response_model=PlatformUserRead,
    summary="Get platform user by ID",
    description="Get a single platform user by their UUID. PLATFORM JWTs only.",
)
async def get_platform_user(
    user_id: UUID,
    auth: AuthContext = Depends(get_auth_context_dep),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> PlatformUserRead:
    _require_platform_auth(auth)

    user = await platform_users_repo.get_by_id(session, user_id)
    if user is None:
        raise PlatformUserNotFoundError()

    return PlatformUserRead.model_validate(user)
```

The exact name of the AuthContext dependency (`get_auth_context_dep`, `get_current_auth`, etc.) MUST be verified against the existing tenants router. If `get_auth_context_dep` doesn't exist, the alternatives are: (a) extract auth from `request.state.auth` directly via a `Request` dependency, (b) use whatever pattern the tenants router uses to read AuthContext. **Verify before writing.**

### File 5: `src/admin_backend/main.py` — modify

Wire the new router into the app. Mirror the existing tenants/lookups include pattern:

```python
from admin_backend.routers.v1 import platform_users
app.include_router(platform_users.router, prefix=settings.api_prefix)
```

If a different inclusion pattern is in use (e.g., a v1_router that aggregates), follow that. Pre-flight item 9 should clarify.

### File 6: `tests/integration/conftest.py` — modify (CONDITIONAL)

**Decide first: is this factory actually needed?**

The seed loader provides 3 platform_users (per Step 3.5). Most tests in this step can read them. A factory is only needed if a test specifically requires a user shape the seed doesn't provide (e.g., a SUSPENDED platform_user to test the status filter).

Evaluation order:

1. Write the tests in File 7 below FIRST.
2. If every test can be expressed against seed data alone, **skip File 6 entirely**. Do not add an unneeded factory.
3. If one or more tests genuinely need a specific shape not in the seed, ADD `make_platform_user` to conftest — and **mirror the existing `make_tenant` factory pattern exactly**, including async vs sync, fixture scope, teardown handling, and session pattern. Do not invent a new shape.

If you do add the factory:

- The bootstrap challenge is small: `platform_users.created_by_user_id` is nullable per the DDL, so a row can insert with NULL audit-actors. The existing seed loader's `platform_users` two-phase insert (Phase 1 NULL, Phase 2 UPDATE) is the canonical pattern — but for a single-row test factory, single-phase NULL-on-insert is fine.
- CHECK constraints (`ck_platform_users_auth0_sub_consistency`, `ck_platform_users_invitation_accepted_consistency`, `ck_platform_users_suspended_consistency`) constrain auth0_sub and lifecycle timestamps based on status. The factory must populate `auth0_sub` and `invitation_accepted_at` when status='ACTIVE' or 'SUSPENDED', and must populate `suspended_at` + `suspended_by_user_id` (nullable; can be NULL on bootstrap) when status='SUSPENDED'.

**Do not write the factory skeleton in this prompt.** Mirror `make_tenant` from existing conftest exactly. The async/sync mix in the original draft was incorrect; the existing factory is the source of truth.

### File 7: `tests/integration/test_platform_users_router.py` — new

**The skeleton below uses placeholder fixture names. Before writing, complete Pre-flight item 10 (read `tests/integration/test_tenants_router.py`) and replace fixture names with whatever the existing tests use. The assertion patterns stay; the fixture machinery may need renaming.**

**Required prerequisite — `_tenant_jwt(settings, *, tenant_id)` helper:** the load-bearing TENANT-JWT-rejection test (`test_list_platform_users_tenant_jwt_rejected`) requires this helper. Per Pre-flight item 10(c), check whether it already exists in any test-helpers module. Three cases:

- **(a) Already exists** → reuse, no work needed.
- **(b) Doesn't exist, easy to add** → add it parallel to `_platform_jwt`. Same shape:
  ```python
  def _tenant_jwt(settings, *, tenant_id: UUID) -> str:
      return make_test_jwt(
          settings,
          user_type="TENANT",
          user_id=UUID("00000000-0000-0000-0000-000000000002"),
          tenant_id=tenant_id,
      )
  ```
  Place where `_platform_jwt` lives (likely the test file itself or a shared helpers module). The synthetic user_id passes Pydantic validation; `make_test_jwt` doesn't validate against the DB.
- **(c) Doesn't exist, awkward to add** (e.g., `_platform_jwt` is in a complex helper module with many moving parts) → surface as Stop-and-ask. Either skip the TENANT-JWT-rejection test temporarily, or add the helper at Step 5.2 where it's used more heavily and the cost is amortised.

**Do not skip the TENANT-JWT-rejection test silently.** It's the load-bearing assertion for the PLATFORM-only auth gate; without it, future regressions could expose staff identities to tenant users undetected. Either ship it now (case a/b) or explicitly defer to Step 5.2 with a TODO comment in the file (case c).

Tests cover three categories: happy-path reads, auth requirements, edge cases. Aim for ~10 tests.

```python
"""Integration tests for /api/v1/platform-users endpoints."""
import pytest


# ─── List endpoint ───────────────────────────────────────────────

def test_list_platform_users_as_platform(app_client, settings):
    """PLATFORM JWT returns all platform_users with pagination."""
    response = app_client.get(
        "/api/v1/platform-users",
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "pagination" in body
    # Seed loader provides 3 platform_users (Anjali, Devon, Kira)
    assert body["pagination"]["total"] >= 3
    assert len(body["items"]) >= 3


def test_list_platform_users_filter_by_status(app_client, settings):
    """status=ACTIVE filter returns only ACTIVE users."""
    response = app_client.get(
        "/api/v1/platform-users",
        params={"status": "ACTIVE"},
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert all(u["status"] == "ACTIVE" for u in items)


def test_list_platform_users_search(app_client, settings):
    """search=anjali returns the user whose email or full_name matches."""
    response = app_client.get(
        "/api/v1/platform-users",
        params={"search": "anjali"},
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) >= 1
    assert any("anjali" in u["email"].lower() or "anjali" in u["full_name"].lower() for u in items)


def test_list_platform_users_sort(app_client, settings):
    """sort=email_asc returns users alphabetically by email."""
    response = app_client.get(
        "/api/v1/platform-users",
        params={"sort": "email_asc"},
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 200
    emails = [u["email"] for u in response.json()["items"]]
    assert emails == sorted(emails)


def test_list_platform_users_invalid_sort(app_client, settings):
    """Unknown sort key returns 400."""
    response = app_client.get(
        "/api/v1/platform-users",
        params={"sort": "not_a_real_sort"},
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 400


def test_list_platform_users_pagination(app_client, settings):
    """limit=2 returns 2 items with has_more=true."""
    response = app_client.get(
        "/api/v1/platform-users",
        params={"limit": 2, "offset": 0},
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) <= 2
    if body["pagination"]["total"] > 2:
        assert body["pagination"]["has_more"] is True


# ─── Detail endpoint ─────────────────────────────────────────────

def test_get_platform_user_by_id(app_client, settings):
    """Get a known seeded platform_user by id returns 200."""
    # First, list to find a real id
    list_resp = app_client.get(
        "/api/v1/platform-users",
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert list_resp.status_code == 200
    user_id = list_resp.json()["items"][0]["id"]

    response = app_client.get(
        f"/api/v1/platform-users/{user_id}",
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == user_id
    # Audit-actor IDs hidden
    assert "created_by_user_id" not in body
    assert "updated_by_user_id" not in body
    assert "suspended_by_user_id" not in body
    # auth0_sub hidden
    assert "auth0_sub" not in body


def test_get_platform_user_not_found(app_client, settings):
    """Unknown user_id returns 404 with PLATFORM_USER_NOT_FOUND code."""
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = app_client.get(
        f"/api/v1/platform-users/{fake_id}",
        headers={"Authorization": f"Bearer {_platform_jwt(settings)}"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "PLATFORM_USER_NOT_FOUND"


# ─── Auth ────────────────────────────────────────────────────────

def test_list_platform_users_no_jwt(app_client):
    """No JWT returns 401."""
    response = app_client.get("/api/v1/platform-users")
    assert response.status_code == 401


def test_list_platform_users_tenant_jwt_rejected(app_client, settings):
    """TENANT JWT returns 403 with PLATFORM_ACCESS_REQUIRED."""
    # Construct a TENANT JWT with a real tenant_id from seed data.
    response = app_client.get(
        "/api/v1/platform-users",
        headers={"Authorization": f"Bearer {_tenant_jwt(settings, tenant_id=...)}"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "PLATFORM_ACCESS_REQUIRED"
```

The `_tenant_jwt(settings, tenant_id=...)` helper may not exist; check existing tests. If only `_platform_jwt` exists, add a `_tenant_jwt` helper to the test file (or to a shared test utility), parallel in shape to `_platform_jwt`.

### File 8: `docs/endpoints/platform-users.md` — new

Mirror `docs/endpoints/tenants.md` exactly. 8 sections per endpoint: Endpoint summary, Request, Response 200, Response codes, Behaviour notes, Example calls, Sample integration code (TypeScript), Implementation reference. Two endpoints documented: `GET /api/v1/platform-users` and `GET /api/v1/platform-users/{user_id}`.

Sample data: use realistic but fictional data (Anjali Mehta, Devon Park, Kira Singh) with proper UUIDv7 strings, ISO 8601 timestamps. Behaviour notes should call out:

- PLATFORM-only access (TENANT JWTs return 403 PLATFORM_ACCESS_REQUIRED)
- No RLS (the application-layer auth gate is the boundary)
- Sort default `created_at_desc`
- Search ILIKE across email + full_name
- Audit-actor IDs and auth0_sub hidden in response

### File 9: `BUILD_PLAN.md` — modify

Update Step 5.1 status TODO → DONE in same commit. Refresh scope-in/scope-out to match what shipped.

```markdown
## Step 5.1 — Platform Users resource

**Status:** DONE
**Owner:** CLAUDE_CODE

**Goal.** Staff users readable by PLATFORM JWTs.

**Scope in.**
- `PlatformUser` model + `PlatformUserStatus` enum.
- `PlatformUserRead`, `PlatformUserListItem`, `PlatformUserListResponse` schemas. Audit-actor IDs and auth0_sub hidden.
- `PlatformUsersRepo` with `list(...)` and `get_by_id(...)`. Stateless-singleton pattern per Step 3.2.
- Router: `GET /api/v1/platform-users`, `GET /api/v1/platform-users/{user_id}`. PLATFORM-only access enforced via `_require_platform_auth(auth)`.
- `PlatformUserNotFoundError` (404, code PLATFORM_USER_NOT_FOUND).
- `PlatformAccessRequiredError` (403, code PLATFORM_ACCESS_REQUIRED).
- `make_platform_user` factory in conftest (only if tests required a non-seed-provided shape; may be skipped per implementation decision).
- ~10 integration tests covering list filter/search/sort/pagination, detail success/404, no-JWT 401, TENANT-JWT 403.
- `docs/endpoints/platform-users.md` following tenants.md's 8-section structure.

**Scope out.**
- Aggregates (no role count, no permission summary). Add later if frontend needs.
- Stats endpoint. List endpoint's pagination total covers most stats needs.
- Write endpoints (post-v0).
- Auth0 sync logic (separate concern).
- Tenant-side visibility (PLATFORM-only by design).
```

### File 10: `CLAUDE.md` — modify

- **Current state → Completed:** Step 5.1 bullet covering the model + schemas + repo + router + tests + endpoint doc. Specifically note: this step introduces the first PLATFORM-only router endpoint and establishes the `_require_platform_auth` pattern (or whatever pattern was actually adopted per Pre-flight item 9(b)).
- **Schema state line:** no change (DDL is unchanged; this step adds ORM + API surface for an already-existing table).
- **Append a "v0 auth model" convention note** in the Code conventions section, alongside the existing "Note on PG enum columns" and the Step 3.6 "Note on batch-by-key response envelope":
  > Until Step 6.1 (RBAC) lands, the v0 auth model is binary user_type-based at the router layer + RLS at the DB layer:
  > - PLATFORM-only endpoints (e.g., `/api/v1/platform-users`) gate via `_require_platform_auth(auth)` → 403 PLATFORM_ACCESS_REQUIRED for TENANT JWTs.
  > - Multi-user-type endpoints (e.g., `/api/v1/tenants`, `/api/v1/tenant-users`) accept both user_types; RLS scopes visibility per session GUCs (D-29 permissive impersonation for PLATFORM).
  > - No router-layer permission check (e.g., "does this user have ADMIN.USERS.VIEW") in v0; RBAC seed data exists (per Step 3.5) but enforcement is Step 6.1.
  >
  > Future PLATFORM-only endpoints inherit Step 5.1's gate pattern. Future endpoints accepting both user_types follow Step 5.2's RLS-only pattern.
- **No new D-XX entries.** The auth model note above is a *convention* note (alongside PG enum and batch-envelope notes), not a decision-of-record. If during implementation the gate pattern is judged load-bearing enough to warrant a D-32, surface and we'll decide.
- **No new FN-AB entries** unless implementation surfaces something genuinely new.

### File 11: `prompts/step-5_1-platform-users-2026-05-03.md` — new

This prompt file. Bundled per the per-step convention.

### File 12: `docs/endpoints/openapi.json` — re-export

After all code is in and tests pass, regenerate the OpenAPI spec snapshot:

```bash
# Server must be running for this
uv run uvicorn admin_backend.main:app --reload &
sleep 2
curl -s http://localhost:8000/api/v1/openapi.json | jq '.' > docs/endpoints/openapi.json
```

This is the deliverable to Amit. The new `/api/v1/platform-users` and `/api/v1/platform-users/{user_id}` endpoints with their full schemas appear in the spec.

---

## Implementation hints

### The PLATFORM-only auth gate

The two endpoints are PLATFORM-only. There are two reasonable shapes:

1. **Function-level check at handler top.** What the skeleton uses: `_require_platform_auth(auth)` raises `PlatformAccessRequiredError` if `auth.user_type != "PLATFORM"`. Pros: explicit, obvious in the handler code, easy to test. Cons: needs to be remembered on every handler in this router.

2. **Router-level dependency.** A `Depends(require_platform_auth)` injected at the router level. Pros: declarative, can't forget. Cons: depends on FastAPI dependency-injection ergonomics; adds a layer.

**Recommend (1) for v0.** The pattern is more transparent and the volume is small (2 handlers). When Step 5.2 lands and we have multiple PLATFORM-only routers, consider promoting to (2) as a refactor.

### `auth0_sub` hidden from response — why

The Auth0 `sub` claim is an internal identifier mapping our local user row to the Auth0 identity provider. It has no UI use. Including it in the response would be unnecessary information disclosure. Step 3.1's pattern is to hide audit-actor IDs from the response shape; `auth0_sub` follows the same logic.

### `make_platform_user` and the bootstrap chicken-and-egg

`platform_users.created_by_user_id` is **nullable** in the DDL. The first row can insert with NULL audit-actors; subsequent rows can reference an earlier id. The seed loader at Step 3.5 already navigates this via two-phase insert (Phase 1 NULL, Phase 2 UPDATE to set actor IDs). The conftest factory can use the same NULL-on-bootstrap pattern for tests that need to insert ad-hoc users.

If the test only needs to *read* the seeded users (Anjali, Devon, Kira), the factory may not be needed at all. Decide during implementation:

- If most tests just read seed data → skip the factory, save the complexity
- If multiple tests need specific shapes (SUSPENDED user, INVITED user) → add the factory

Lean toward skipping unless a specific test demands it. Seed data has 3 ACTIVE users; that's enough for most cases.

### Sort key validation

The `SORT_MAP` raises `ValueError` on unknown keys. The router doesn't currently catch this — it'd surface as a 500 InternalError via the global handler. **Better:** convert to a 400 ValidationError. Either:

1. The router catches `ValueError` and re-raises as `ValidationError` (existing class).
2. The repo raises a typed `InvalidSortKeyError(ClientError)` that the global handler converts to 400.

Pick whichever fits the existing error shape; verify against the tenants router behaviour at pre-flight.

### TENANT JWT in tests

The skeleton test for "TENANT JWT returns 403" needs a `_tenant_jwt(settings, tenant_id=...)` helper. If one doesn't exist, build it parallel to `_platform_jwt(settings)`. The tenant_id can be looked up from seed data (any of the 7 seeded tenants). If the test fixtures don't make this trivial, **simplify** to: skip the TENANT-JWT test in this step, add it later in Step 5.2 where it fits naturally (Step 5.2 has tenant-scoped routing, so the TENANT JWT machinery lands there anyway). Surface during implementation if this saves significant effort.

---

## Testing and regression discipline

### New tests added by this step

~10 integration tests in `tests/integration/test_platform_users_router.py`.

### Tests deliberately not added

- Cross-tenant isolation tests. platform_users has no tenant boundary; no cross-tenant concept applies.
- RLS truth-table tests. platform_users has no RLS; smoke test doesn't grow.
- Performance/scale tests. ~3 platform users at v0; not a concern.

### Regression risk surface introduced by this step

1. **The PLATFORM-only gate must work.** Test "TENANT JWT returns 403" is the load-bearing assertion. If it's skipped or weakened, a future bug could expose staff identities to tenant users.

2. **Hidden fields stay hidden.** Tests assert that `auth0_sub`, `created_by_user_id`, `updated_by_user_id`, `suspended_by_user_id` are absent from response bodies. Per D-31 these become permanent contract; renaming or exposing later requires a v2 API.

3. **Sort validation.** Unknown sort key returns 400, not 500. Tests confirm.

4. **Stable secondary sort by id.** Pagination determinism depends on this. Without the secondary sort, two rows with identical `created_at` could swap positions across pages.

5. **`status` enum value validation.** Pydantic's `PlatformUserStatus | None` automatically validates the query param against enum values; FastAPI returns 422 on bad input. A test for `?status=BOGUS` returning 422 is worthwhile but optional.

6. **No regressions in existing tests.** Step 3.6's pytest count was ~115; Step 5.1 should land around 125 (115 + ~10 new). All 115 prior tests must still pass — `make_platform_user` (if added) must not interfere with `make_tenant_user` or `make_tenant`.

### Verification harness (run all six; all must be green)

```bash
# 1. Re-seed before manual curl verification — the test suite truncates
# multi-tenant tables; without re-seeding, the curl checks below see
# empty tables. Tests run against a self-managing DB state; manual curl
# checks need the seed to be intact.
uv run python -m scripts.seed_dev_data --reset

# 2. Full pytest suite — new + regression
uv run pytest -v

# Note: pytest may have re-truncated the seed; re-seed again before manual curl
uv run python -m scripts.seed_dev_data --reset

# 3. mypy strict
uv run mypy --strict src/admin_backend

# 4. Pre-flight checker
./scripts/check_setup.sh

# 5. Migration round-trip — no new migration but verify state hasn't drifted
uv run alembic current   # should show 0644a4186e48

# 6. Manual curl verification (server must be running; seed must be present)
JWT=$(uv run python -c "
from admin_backend.config import get_settings
from admin_backend.auth.testing import make_test_jwt
from uuid import UUID
print(make_test_jwt(get_settings(), user_type='PLATFORM', user_id=UUID('00000000-0000-0000-0000-000000000001')))
")

curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:8000/api/v1/platform-users" \
  | jq

curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:8000/api/v1/platform-users?status=ACTIVE&sort=email_asc" \
  | jq

# Pick an id from the list output above:
USER_ID="<paste-id-here>"
curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:8000/api/v1/platform-users/${USER_ID}" \
  | jq
```

Expected pytest count: ~125 passes (115 prior + ~10 new). Smoke test unchanged at 74 PASS (no RLS surface added).

If any leg is not green, **report rather than commit**.

---

## Scope out

- **Step 5.2 (tenant_users)** — separate prompt, separate commit. Lands next.
- **Aggregates on platform_users.** Role assignment count, last-login timestamp, etc. None for v0; add when frontend requests.
- **Write endpoints.** Invite, suspend, etc. Post-v0 per FN-AB-12.
- **Auth0 webhook handler.** Separate concern; lands when Auth0 tenant is configured.
- **Stats endpoint.** Tenants has `/stats` for the dashboard summary; platform_users count is small enough that the list pagination total covers it.
- **Permission visibility.** Frontend may want "what permissions does this platform user have" on the detail page. That's RBAC territory (Step 6.1); this step doesn't expose it.
- **Cross-cutting permissions check.** PLATFORM gate is a binary check. Per-role distinctions ("Module Admin can list but not see suspension details") live in Step 6.1.

---

## Stop and ask if

- The `Pagination` schema isn't easily importable from `schemas/tenant.py`. May need to relocate to `schemas/_common.py` or similar — surface and confirm before reorganising.
- The `_require_platform_auth` pattern conflicts with an existing convention (e.g., a different auth-gate pattern is already used elsewhere). Surface the existing pattern and we'll align.
- The `make_platform_user` factory turns out to be unnecessary — all tests pass against seed data alone. Surface and we'll skip the factory addition; less code to maintain.
- The TENANT-JWT test in `test_list_platform_users_tenant_jwt_rejected` is awkward to construct (no existing `_tenant_jwt` helper, fixture work involved). Surface and we'll either build the helper or skip the test for now.
- Auth dependency name (`get_auth_context_dep`) doesn't exist in the codebase — surface what the actual name is and the skeleton's imports change.

---

## Acceptance criteria

- 12 files created/modified (range slightly wider if `Pagination` needs relocation or `make_platform_user` is skipped).
- 2 endpoints live: `GET /api/v1/platform-users` and `GET /api/v1/platform-users/{user_id}`.
- ~10 new integration tests pass; all prior tests still pass.
- mypy strict clean across `src/admin_backend`.
- check_setup 35/35.
- Smoke test unchanged at 74 PASS.
- BUILD_PLAN.md Step 5.1 entry updated to DONE.
- `docs/endpoints/platform-users.md` follows tenants.md's 8-section structure.
- `docs/endpoints/openapi.json` regenerated with new endpoints visible.
- **OpenAPI spec quality:** `/api/v1/platform-users` shows clear summary, description, parameter descriptions and examples; response schema with per-field descriptions on `email`, `full_name`, `status`, `invited_at`, `invitation_accepted_at`, `suspended_at`, `created_at`, `updated_at`. Verify by `cat docs/endpoints/openapi.json | jq '.paths."/api/v1/platform-users"'` showing rich content.
- Manual curl verification: list returns 3+ items; detail returns single object with hidden fields absent; TENANT JWT returns 403.

---

## Report (BEFORE proposing commit)

Five bundles per the convention:

1. **Code:** files created/modified with line counts; sample curl outputs (list response, detail response, 403 response from TENANT JWT) confirming behaviour.
2. **CLAUDE.md updates:** Step 5.1 Completed bullet; no new D-XX or FN-AB unless implementation surfaced something genuine.
3. **BUILD_PLAN.md updates:** Step 5.1 entry updated to DONE; scope-in/acceptance match what shipped.
4. **architecture.md updates:** likely "no change" — endpoint addition; no system-shape movement. Confirm by reading the relevant section before declaring no edit.
5. **OpenAPI spec snapshot:** `docs/endpoints/openapi.json` regenerated with the two new endpoints visible.
6. **Prompt file:** `prompts/step-5_1-platform-users-2026-05-03.md` confirmed in commit set.

Plus: pytest count delta (was ~115, now ~125); mypy status; check_setup; alembic current.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
