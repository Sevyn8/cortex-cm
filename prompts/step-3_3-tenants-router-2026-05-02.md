# Prompt — Step 3.3: Tenants router + endpoints + endpoint doc

> Generated 2026-05-02, 09:30 PM. Revised 2026-05-02, 10:30 PM (stress-test fixes: 11 issues addressed across skeletons, fixtures, tripwire heuristic, Alembic regression note, and acceptance criteria).
> Paste this entire block into a fresh Claude Code session to start Step 3.3.
> First domain endpoints. Patterns locked here propagate to every subsequent endpoint step (4.5, 5.x, 6.x): URL prefix wiring, response envelope, error shape, RLS-under-aggregates, per-endpoint documentation. Get the shapes right.

---

## Context: substantial scope expansion vs. BUILD_PLAN's original Step 3.3

BUILD_PLAN.md currently says Step 3.3 is "60-90 min, two endpoints, list returning `list[TenantRead]`." That estimate predates a contract review with the frontend dev that surfaced real shape requirements. The actual work is **three endpoints**, **new schemas**, **new Repo methods**, **a stub for module entitlements**, and **two new D-XX entries**. Estimated effort is closer to a full day. Rewrite Step 3.3's scope-in/acceptance to match what ships; don't try to compress the work to fit the old estimate.

The original prompt at `prompts/step-3_3-tenants-router.md` references `docs/api-contract.md` decisions that were never finalised (the file is still TEMPLATE) — ignore those references. This prompt locks the actual response shapes directly.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -10` — confirm Step 3.2 (`9abb109` or thereabouts) at HEAD. The chain back to Step 3.0 must all be present.
3. Read `CLAUDE.md` fully. Focus on:
   - **D-03** — RLS via `app.tenant_id` and `app.user_type`.
   - **D-15** — `__table_args__["schema"]` parameterisation.
   - **D-17** — RLS-blocked → 404. Load-bearing for the detail endpoint's missing-row handling.
   - **D-21** — UUIDv7 via project `uuidv7()` PL/pgSQL function.
   - **D-24** — AuthContext flows tenant context; never as method argument.
   - **D-27** — NULLIF wrapper on RLS policies.
   - **D-28** — provisional API response defaults from Step 3.1; this step's response shapes confirm them.
   - **D-29** — PLATFORM RLS visibility via OR-clause; required for the count subqueries to behave correctly under both PLATFORM and TENANT contexts.
   - "Per-endpoint documentation" section — the 8-section format that `docs/endpoints/tenants.md` follows. Locks the canonical example.
   - "Workflow convention — Per-step commit bundling" — five-item bundle now includes architecture.md when system shape moves.
4. Read `docs/architecture.md` "Schema and storage", "Code structure", and the request-flow / Layer 1 RLS sections.
5. Read `BUILD_PLAN.md` Step 3.3 in full:
   ```bash
   grep -A30 "## Step 3.3" BUILD_PLAN.md
   ```
   Note: scope-in says two endpoints, returns `list[TenantRead]`, references `docs/api-contract.md`. None of that matches what this prompt specifies. Step 3.3's BUILD_PLAN entry needs rewriting in this commit.
6. Read `src/admin_backend/repositories/tenants.py` — Step 3.2's `TenantsRepo`. Existing methods (`get_by_id`, `list_all`, `list_by_status`) stay untouched; this step adds new aggregate-shaped methods alongside.
7. Read `src/admin_backend/schemas/tenant.py` — Step 3.1's `TenantRead`. Stays as-is for direct projection use; new schemas in this step compose on top of it or alongside it.
8. Read `src/admin_backend/models/tenant.py` — Step 3.1 + 3.2's amendment (FetchedValue on the four DB-defaulted columns).
9. Read `src/admin_backend/errors.py` — Step 2.3's `ClientError`/`ServerError` hierarchy. The detail endpoint's 404 needs a new `TenantNotFoundError` subclass following the same shape.
10. Read `src/admin_backend/main.py` — Step 2.4's lifespan + create_app. The router-include line goes here.
11. Read `src/admin_backend/dependencies.py` — Step 2.3's `get_tenant_session_dep`. Handlers depend on this.
12. Read `tests/integration/conftest.py` — Step 3.2's fixtures (`make_tenant`, `platform_session`, `tenant_session_factory`). Reuse for this step's tests.
13. Read this prompt fully.

---

## Step ID and intent

**Step 3.3** — Three GET endpoints for the tenants resource: list, stats, detail.

Seven concrete deliverables:

1. **Router** at `src/admin_backend/routers/v1/tenants.py` with three handlers.
2. **New Pydantic schemas** for list/detail/stats responses, pagination, error envelope, and query-param validation.
3. **New Repo methods** for aggregate-shaped queries (list with counts + pagination, detail with counts, stats scalars).
4. **Lightweight ORM stubs** for `Store` and `TenantUser` so subqueries are typed.
5. **Module entitlement stub file** with an xfail tripwire test for FN-AB-XX cleanup.
6. **Conftest fixtures** for `make_store` and `make_tenant_user` mirroring Step 3.2's `make_tenant` pattern; foundation for 4.5 and 5.2's tests.
7. **`docs/endpoints/tenants.md`** — canonical 8-section template every subsequent endpoint doc follows.

Plus the conventional bundle: BUILD_PLAN.md status flip + scope rewrite, CLAUDE.md updates including two new D-XX entries, architecture.md edits if system shape moved, prompt file in commit set.

CLAUDE_CODE step. No DDL changes, no migrations, no application-side state changes outside the new code.

---

## Endpoint specifications

The three endpoints return shapes locked through a contract review with the frontend dev. The shapes below are *the* source of truth for this step. If the contract document at `tenants-api-contract-v0.md` differs, the shapes here win — the contract has padding cuts and shape simplifications applied that aren't in the original document.

### Endpoint 1: `GET /api/v1/tenants` — list

**Query parameters** (all optional):

| Param | Type | Default | Validation |
|---|---|---|---|
| `tier` | string | None | One of `tenant_tier_enum` values; 422 otherwise |
| `search` | string | None | Trimmed; if length < 1 after trim, treat as None (no filter) |
| `offset` | int | 0 | >= 0 |
| `limit` | int | 20 | >= 1, <= 100 |

**Response 200** — `TenantsListResponse`:

```json
{
  "items": [
    {
      "id": "972a8469-1641-4f82-8b9d-2434e465e150",
      "name": "Buc-ee's",
      "display_code": "buc-ees",
      "country": "USA",
      "region": "US",
      "industry": "CONVENIENCE_FUEL",
      "tier": "ENTERPRISE",
      "status": "ACTIVE",
      "monthly_revenue_usd": "48500.00",
      "num_stores": 47,
      "num_users_active": 312,
      "modules": [
        { "code": "ROOS", "name": "ROOS" },
        { "code": "PRICING_OS", "name": "Pricing OS" }
      ],
      "created_at": "2026-04-19T15:00:00+00:00",
      "updated_at": "2026-04-19T15:00:00+00:00"
    }
  ],
  "pagination": {
    "total": 7,
    "offset": 0,
    "limit": 20
  }
}
```

13 fields per item. Default sort: `name ASC` (no `sort` query param).

**Behaviour notes:**
- `num_stores` is the live count from `stores` (not `tenants.number_of_stores`); subquery filtered by `s.tenant_id = t.id`.
- `num_users_active` is the live count from `tenant_users WHERE status='ACTIVE'`; subquery filtered by `u.tenant_id = t.id AND u.status = 'ACTIVE'`.
- `modules` from `_module_entitlements_stub.py` (per-tenant Python dict lookup; `tenant_module_access` table doesn't exist yet, tracked as FN-AB-XX). Each module is `{code, name}`; no `enabled_at` on list.
- `pagination.total` is the RLS-filtered total — what the caller can see, not the platform total. PLATFORM gets full count; TENANT-A would see 1.
- The list response intentionally **omits**: `monthly_revenue_as_of_date`, `number_of_stores`, `number_of_stores_as_of_date`, `primary_contact_name`, `contact_email`, `suspended_at`, `terminated_at`. Available on detail.

**Errors:** 400/422 `VALIDATION_ERROR`, 401, 403.

**Cache header:** none.

### Endpoint 2: `GET /api/v1/tenants/stats` — header summary

**Query parameters:** none.

**Response 200** — `TenantsStatsResponse`:

```json
{
  "total_tenants": 7,
  "total_stores": 10084
}
```

Two scalars, both RLS-filtered.

**Behaviour notes:**
- `total_tenants` = `SELECT count(*) FROM tenants` under the caller's RLS context.
- `total_stores` = `SELECT count(*) FROM stores` under the caller's RLS context.
- For PLATFORM callers (the only realistic consumer), both scalars reflect the platform's full state.

**Errors:** 401, 403.

**Cache header:** `Cache-Control: private, max-age=60`. First (and only) endpoint setting Cache-Control in v0.

### Endpoint 3: `GET /api/v1/tenants/{tenant_id}` — detail

**Path parameter:** `{tenant_id}` — UUID, validated by FastAPI (`tenant_id: UUID`); malformed → 422.

**Response 200** — `TenantDetail`:

```json
{
  "id": "...",
  "name": "Żabka Group",
  "display_code": "zabka-group",
  "country": "Poland",
  "region": "EU",
  "tier": "ENTERPRISE",
  "industry": "CONVENIENCE",
  "monthly_revenue_usd": "142000.00",
  "monthly_revenue_as_of_date": "2026-04-01",
  "number_of_stores": 9842,
  "number_of_stores_as_of_date": "2026-04-01",
  "primary_contact_name": "Tomasz Nowak",
  "contact_email": "tomasz.nowak@zabka.pl",
  "status": "ACTIVE",
  "created_at": "...",
  "updated_at": "...",
  "suspended_at": null,
  "terminated_at": null,
  "num_stores": 9842,
  "num_users_active": 1240,
  "modules": [
    { "code": "ROOS", "name": "ROOS" }
  ]
}
```

Fully flat. 21 fields. No `live_counts` nesting, no `lifecycle` nesting, no `legal_name`, no `*_by_user_id` exposed (Step 3.1's hide policy stands), no `enabled_at` on modules.

**Behaviour notes:**
- Same `num_stores` and `num_users_active` semantics as list endpoint.
- `monthly_revenue_usd` is the self-reported value from the tenants row (live revenue tracking deferred; field stays as stored value for now).
- 404 returned when the row doesn't exist OR is filtered out by RLS — per D-17, the handler can't and shouldn't distinguish. Body shape is the standard error envelope; `code: "TENANT_NOT_FOUND"`.

**Errors:** 401, 403, 404 `TENANT_NOT_FOUND`, 422 (malformed UUID).

**Cache header:** none.

### Cross-cutting conventions (all three endpoints)

- **URL prefix:** `/api/v1/...`. Implementation: a single `api_prefix` setting in config, applied at `app.include_router(..., prefix=settings.api_prefix)` in main.py. Routers themselves declare `prefix="/tenants"` only.
- **Auth:** `Authorization: Bearer <jwt>` required. Step 2.3's middleware wires this; handlers depend on `Depends(get_tenant_session_dep)` which transitively depends on AuthContext.
- **`X-Request-Id` response header:** already wired by Step 2.3's audit middleware.
- **Response shape conventions** (D-28): snake_case keys, ISO 8601 with offset for timestamps, NUMERIC as JSON string with two decimals, nulls explicit, enum values as raw DDL strings.
- **Wrapping rule** (new D-XX, captured this step): list endpoints wrap as `{items, pagination}`; single-object endpoints return the object directly with no envelope.
- **Field-meaning lock** (new D-XX, captured this step): `num_stores` and `num_users_active` semantics are frozen on first ship; v0.1 cannot reinterpret them. New variants get new field names.
- **Error envelope:** `{code, message, details, request_id}`. Step 2.3's existing handler emits `{code, message, request_id}` — needs a `details` field added (object or null). Small handler edit; see Files section.
- **RBAC:** not enforced in 3.3. Any valid PLATFORM JWT can call all three endpoints; any valid TENANT JWT can call them too (TENANT calls are useless for list/stats but technically work). Permission check (`ADMIN.TENANTS.VIEW`) lands at Step 6.1.

---

## Scope in

### File 1: `src/admin_backend/config.py` — modify

Add the API prefix setting:

```python
class Settings(BaseSettings):
    # ... existing fields ...
    api_prefix: str = "/api/v1"
```

Validators: ensure starts with `/`, no trailing `/`, matches `^/[a-z0-9/_-]+$`. The choice to make this configurable (vs. hardcoding) is forward-compatibility for a v2 cutover — when v2 ships and replaces v1, the setting flips, no router file edits needed.

### File 2: `src/admin_backend/routers/__init__.py` — new

Empty. Marker file.

### File 3: `src/admin_backend/routers/v1/__init__.py` — new

Empty.

### File 4: `src/admin_backend/routers/v1/tenants.py` — new

`APIRouter(prefix="/tenants", tags=["tenants"])`. Three handlers.

Critical detail on route order: `/stats` MUST be declared before `/{tenant_id}`. FastAPI matches routes top-to-bottom; if `/{tenant_id}` is declared first, `GET /tenants/stats` would be interpreted as "stats is a UUID path param" and fail validation with a 422.

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from admin_backend.dependencies import get_tenant_session_dep
from admin_backend.repositories.tenants import TenantsRepo
from admin_backend.schemas.tenant import (
    TenantsListResponse, TenantsStatsResponse, TenantDetail,
)
from admin_backend.errors import TenantNotFoundError
from admin_backend.models.tenant import TenantTier  # for query param validation


router = APIRouter(prefix="/tenants", tags=["tenants"])
_repo = TenantsRepo()  # stateless instance, reused across requests


@router.get("", response_model=TenantsListResponse)
async def list_tenants(
    session: AsyncSession = Depends(get_tenant_session_dep),
    tier: TenantTier | None = Query(None),
    search: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> TenantsListResponse:
    # Trim search; treat empty as None
    search = search.strip() if search else None
    if search == "":
        search = None
    rows, total = await _repo.list_with_aggregates(
        session, tier=tier, search=search, offset=offset, limit=limit,
    )
    items = [TenantsListResponse.item_from_row(r) for r in rows]  # or similar mapping
    return TenantsListResponse(
        items=items,
        pagination=Pagination(total=total, offset=offset, limit=limit),
    )


@router.get("/stats", response_model=TenantsStatsResponse)
async def tenants_stats(
    response: Response,
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> TenantsStatsResponse:
    # FastAPI injects the outgoing Response object via dependency injection
    # when typed in the handler signature. Setting headers on it modifies
    # the same response that will carry the Pydantic-serialized body.
    # Using JSONResponse(...) directly here would lose response_model
    # auto-validation, so this idiom is preferred.
    response.headers["Cache-Control"] = "private, max-age=60"
    total_tenants, total_stores = await _repo.count_for_stats(session)
    return TenantsStatsResponse(
        total_tenants=total_tenants,
        total_stores=total_stores,
    )


@router.get("/{tenant_id}", response_model=TenantDetail)
async def get_tenant(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> TenantDetail:
    row = await _repo.get_by_id_with_aggregates(session, tenant_id)
    if row is None:
        raise TenantNotFoundError(
            f"Tenant {tenant_id} not visible to this session",
            tenant_id=str(tenant_id),
        )
    return TenantDetail.from_row(row)
```

Skeleton above is illustrative; adapt naming and method shapes to fit existing patterns.

### File 5: `src/admin_backend/main.py` — modify

Wire the router with the configured prefix:

```python
from admin_backend.routers.v1 import tenants as tenants_router

# In create_app():
app.include_router(tenants_router.router, prefix=settings.api_prefix)
```

Verify health/ready endpoints still work — they likely currently mount on `/v1/health` etc. via direct registration; they should also move to `/api/v1/health` for consistency. Check Step 2.4's lifespan/health work and verify the prefix is applied uniformly. If health/ready paths change, update the test expectations in `tests/integration/test_health.py`.

### File 6: `src/admin_backend/schemas/tenant.py` — modify

Add new schemas alongside existing `TenantRead` (which stays):

```python
class Module(BaseModel):
    code: str
    name: str


class Pagination(BaseModel):
    total: int
    offset: int
    limit: int


class TenantsListItem(BaseModel):
    """Per-card response shape. 13 fields, fully flat."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    display_code: str | None
    country: str | None
    region: TenantRegion
    industry: TenantIndustry | None
    tier: TenantTier | None
    status: TenantStatus
    monthly_revenue_usd: Decimal | None
    num_stores: int
    num_users_active: int
    modules: list[Module]
    created_at: datetime
    updated_at: datetime

    @field_serializer("monthly_revenue_usd", when_used="json")
    def _serialise_money(self, v: Decimal | None) -> str | None:
        return str(v) if v is not None else None


class TenantsListResponse(BaseModel):
    items: list[TenantsListItem]
    pagination: Pagination


class TenantsStatsResponse(BaseModel):
    total_tenants: int
    total_stores: int


class TenantDetail(BaseModel):
    """Detail response shape. 21 fields, fully flat. Step 3.1's TenantRead
    plus three live aggregates and the modules array."""
    model_config = ConfigDict(from_attributes=True)

    # All TenantRead fields
    id: UUID
    name: str
    display_code: str | None
    country: str | None
    region: TenantRegion
    tier: TenantTier | None
    industry: TenantIndustry | None
    monthly_revenue_usd: Decimal | None
    monthly_revenue_as_of_date: date | None
    number_of_stores: int | None
    number_of_stores_as_of_date: date | None
    primary_contact_name: str | None
    contact_email: str | None
    status: TenantStatus
    created_at: datetime
    updated_at: datetime
    suspended_at: datetime | None
    terminated_at: datetime | None
    # Aggregates
    num_stores: int
    num_users_active: int
    modules: list[Module]

    @field_serializer("monthly_revenue_usd", when_used="json")
    def _serialise_money(self, v: Decimal | None) -> str | None:
        return str(v) if v is not None else None
```

Decisions reflected:
- `Module` is a small reusable class; both list and detail use it.
- `TenantDetail` does NOT subclass `TenantRead`. Inheritance creates surprises (mypy treats one as a subtype of the other in unintended places); copy-paste the field set. Six months from now if the lists drift, that's expected; one is the detail, one is direct projection.
- `monthly_revenue_usd` serializer is duplicated across list and detail. Extract to a shared helper if the duplication grows; for two classes it's fine.
- `*_by_user_id` fields stay hidden per Step 3.1's design.

### File 7: `src/admin_backend/repositories/tenants.py` — modify

Add new methods alongside existing ones (existing untouched):

```python
class TenantsRepo:
    # ... existing get_by_id, list_all, list_by_status from 3.2 ...

    async def list_with_aggregates(
        self,
        session: AsyncSession,
        *,
        tier: TenantTier | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[TenantListRow], int]:
        """Returns (rows, total_count).

        Both queries inherit RLS via the session's GUCs (app.tenant_id,
        app.user_type), set per-transaction by get_tenant_session. No
        special handling needed here: a TENANT-A session sees only
        TENANT-A's tenants row in both the main query and the count;
        a PLATFORM session sees all rows in both.
        """
        # Build filter conditions (user-supplied, applied to both queries).
        conditions = []
        if tier is not None:
            conditions.append(Tenant.tier == tier)
        if search is not None:
            pat = f"%{search}%"
            conditions.append(or_(
                Tenant.name.ilike(pat),
                Tenant.display_code.ilike(pat),
                Tenant.contact_email.ilike(pat),
            ))

        # Count query: same WHERE, no LIMIT/OFFSET. Inherits RLS.
        count_stmt = select(func.count()).select_from(Tenant)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total: int = (await session.execute(count_stmt)).scalar_one()

        # Aggregate subqueries for the main query.
        # Each subquery inherits RLS independently, filtered to the
        # outer row's tenant_id via .correlate(Tenant).
        num_stores_subq = (
            select(func.count(Store.id))
            .where(Store.tenant_id == Tenant.id)
            .correlate(Tenant)
            .scalar_subquery()
        )
        num_users_active_subq = (
            select(func.count(TenantUser.id))
            .where(
                TenantUser.tenant_id == Tenant.id,
                TenantUser.status == "ACTIVE",
            )
            .correlate(Tenant)
            .scalar_subquery()
        )

        stmt = (
            select(
                Tenant,
                num_stores_subq.label("num_stores"),
                num_users_active_subq.label("num_users_active"),
            )
            .order_by(Tenant.name.asc())
            .limit(limit)
            .offset(offset)
        )
        if conditions:
            stmt = stmt.where(*conditions)

        result = await session.execute(stmt)
        rows = [
            TenantListRow(tenant=t, num_stores=ns, num_users_active=nua)
            for (t, ns, nua) in result.all()
        ]
        return rows, total

    async def get_by_id_with_aggregates(
        self,
        session: AsyncSession,
        tenant_id: UUID,
    ) -> TenantDetailRow | None:
        """Single-row variant. Same subquery pattern as list, with
        WHERE Tenant.id == tenant_id and no LIMIT/OFFSET. RLS-filtered
        rows or genuinely-missing rows both surface as None per D-17.
        """
        # ... mirror list_with_aggregates' subquery construction,
        # but with .where(Tenant.id == tenant_id) on the outer select
        # and .one_or_none() on the result.

    async def count_for_stats(
        self,
        session: AsyncSession,
    ) -> tuple[int, int]:
        """Returns (total_tenants, total_stores). Both RLS-filtered.

        For PLATFORM callers the counts reflect platform totals; for
        TENANT callers they reflect what that tenant can see (typically
        1 tenant and that tenant's store count).
        """
        total_tenants: int = (
            await session.execute(select(func.count()).select_from(Tenant))
        ).scalar_one()
        total_stores: int = (
            await session.execute(select(func.count()).select_from(Store))
        ).scalar_one()
        return total_tenants, total_stores
```

`TenantListRow` and `TenantDetailRow` are small dataclasses (or Pydantic models) declared in the repo file or a sibling `_rows.py`. They hold the ORM tenant + aggregates. Handlers map from `*Row` to the response schema.

The skeletons above are intended as starting points — mypy strict may require additional type annotations or `typing.cast` calls (see regression risks below).

### File 8: `src/admin_backend/models/_lightweight_stubs.py` — new

Lightweight ORM stubs for Store and TenantUser. Minimal columns needed for the count subqueries:

```python
"""Lightweight ORM stubs.

Full models for `Store` and `TenantUser` land at Steps 4.5 and 5.2 with all
columns. This file declares the minimal column set (id, tenant_id, plus
status for TenantUser) so that subqueries in TenantsRepo can be written as
typed SQLAlchemy expressions instead of raw SQL strings.

When 4.5/5.2 land, this file is deleted and the real models in
`models/store.py` / `models/tenant_user.py` take over. The full models will
extend the same `__table_args__["schema"]` pattern Step 3.1 set for Tenant.

CRITICAL: These stubs are deliberately INCOMPLETE — they declare a strict
subset of the actual table columns (only what's needed for count subqueries).
Pointing Alembic autogenerate at `Base.metadata` while these stubs exist
would propose ALTER TABLE DROP statements for every column on the real
`stores` and `tenant_users` tables that isn't declared here. Until 4.5/5.2
ship, `migrations/env.py` MUST keep `target_metadata = None`. Do not
"complete" these stubs to make autogenerate happy; the right fix is the
full ORM models in 4.5/5.2.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import get_settings
from admin_backend.db.base import Base


_DB_SCHEMA = get_settings().db_schema


class Store(Base):
    __tablename__ = "stores"
    __table_args__ = {"schema": _DB_SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column()
    # Other columns omitted; full model in 4.5.


class TenantUser(Base):
    __tablename__ = "tenant_users"
    __table_args__ = {"schema": _DB_SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(Text)  # PG enum; string is fine for filter
    # Other columns omitted; full model in 5.2.
```

If any other code paths in this step need richer fields, add them — but stay minimal. The stubs go away at 4.5/5.2.

### File 9: `src/admin_backend/repositories/_module_entitlements_stub.py` — new

```python
"""STUB. Module entitlements per tenant, hardcoded.

Replaced when `tenant_module_access` table ships per FN-AB-XX. The xfailed
test in `tests/unit/test_module_entitlements.py` will start xpassing when
this file is deleted, which forces the cleanup at table-landing time.
"""
from uuid import UUID

# Module code → display name. Module set is platform-fixed per design.
_MODULE_NAMES: dict[str, str] = {
    "ROOS": "ROOS",
    "PRICING_OS": "Pricing OS",
    "PERISHABLES_ASSISTANT": "Perishables Assistant",
    "PROMOTIONS_ASSISTANT": "Promotions Assistant",
    "GOAL_CONSOLE": "Goal Console",
    "ADMIN": "Admin",
}

# Tenant ID → module codes. UUIDs match seed Excel; expand as seeds grow.
_TENANT_MODULES: dict[UUID, list[str]] = {
    # Buc-ee's
    UUID("972a8469-1641-4f82-8b9d-2434e465e150"): [
        "ROOS", "PRICING_OS", "PERISHABLES_ASSISTANT", "PROMOTIONS_ASSISTANT", "ADMIN",
    ],
    # Żabka Group, Infomil, etc. — fill in remaining 6 from seed Excel.
    # ...
}


def get_modules_for_tenant(tenant_id: UUID) -> list[dict[str, str]]:
    """Return list of {code, name} dicts. Empty list if tenant unknown."""
    codes = _TENANT_MODULES.get(tenant_id, [])
    return [{"code": c, "name": _MODULE_NAMES[c]} for c in codes]
```

### File 10: `src/admin_backend/errors.py` — modify

Add `TenantNotFoundError` subclass of `ClientError`:

```python
class TenantNotFoundError(ClientError):
    public_message = "Tenant not found"
    http_status = 404
    code = "TENANT_NOT_FOUND"
```

Also: amend the exception handler in `main.py` (added at Step 2.3) to include a `details` field in the response body. Currently emits `{code, message, request_id}`; should emit `{code, message, details, request_id}` where `details` is `None` for now. Optional field, not required by frontend — the contract specs it as a slot for future per-field validation errors.

Small handler edit, ~3 lines.

### File 11: `tests/unit/test_module_entitlements.py` — new

Xfail tripwire test for FN-AB-XX. The cleanup action being tracked is **deletion of the stub file**; the test asserts file-existence, which is the most robust check.

```python
"""Tripwire for FN-AB-XX (module entitlement stub cleanup).

The current implementation reads from a hardcoded Python dict in
`_module_entitlements_stub.py`. When the `tenant_module_access` table
ships, this file is deleted and the implementation moves into a real
Repo method querying the table.

The xfail below asserts that the stub file does not exist. It currently
fails (xfail) because the file does exist. The day someone deletes the
file as part of the FN-AB-XX cleanup, this test starts passing — under
strict=True, an xpassed test is reported as a failure, forcing the
test itself to be deleted as part of the cleanup commit.

Why file-existence and not source-inspection: source inspection is
fragile (the future implementation might import the stub from a
different module, alias it, etc., causing the heuristic to silently
miss the cleanup). File-existence is unambiguous: the cleanup
commit deletes the file, the test starts passing, the test gets
deleted in the same commit.
"""
import os
from pathlib import Path

import pytest


@pytest.mark.xfail(
    reason="FN-AB-XX: _module_entitlements_stub.py exists; will be "
           "deleted when tenant_module_access table ships. xpass under "
           "strict=True forces this test's deletion alongside the stub.",
    strict=True,
)
def test_module_entitlements_stub_does_not_exist():
    """When the stub file is deleted (as part of FN-AB-XX cleanup),
    this test starts passing. Under strict=True, an xpassed test is a
    test failure, which forces the cleanup PR to also delete this test.
    """
    import admin_backend
    pkg_root = Path(admin_backend.__file__).parent
    stub_path = pkg_root / "repositories" / "_module_entitlements_stub.py"
    assert not stub_path.exists(), (
        f"Stub file still exists at {stub_path}. "
        "FN-AB-XX cleanup is incomplete: delete this stub file and "
        "this test, and replace the stub call sites in TenantsRepo "
        "with real queries against tenant_module_access."
    )
```

The `strict=True` flag is what makes xpass-as-failure work. Without it, xpass is silent; with it, xpass aborts the test run. Test will currently xfail (the file exists); when the file is deleted, the test xpasses, pytest reports failure, the PR author deletes the test as part of the same commit.

### File 11a: `tests/integration/conftest.py` — modify (new fixtures)

Step 3.2's conftest has `make_tenant` (a fixture that inserts + tracks + DELETEs tenants). The new tests in this step need analogous factories for stores and tenant_users so that `num_stores` and `num_users_active` aggregates can be exercised.

Add `make_store` and `make_tenant_user` fixtures, mirroring `make_tenant`'s pattern: async factory that inserts via PLATFORM session, commits, tracks created IDs, DELETEs at teardown.

Sketch (adapt to actual ORM stub fields):

```python
@pytest_asyncio.fixture
async def make_store(
    session_factory,
    platform_auth,
) -> AsyncIterator[Callable[..., Awaitable[Store]]]:
    """Async factory: insert + commit a Store via PLATFORM session,
    return persisted ORM object. Tracks IDs and DELETEs at teardown.

    Caller must pass tenant_id explicitly; the factory does not
    auto-associate with a tenant created in the same test.
    """
    created_ids: list[UUID] = []

    async def _make(*, tenant_id: UUID, **overrides) -> Store:
        store = Store(tenant_id=tenant_id, **overrides)
        async for session in get_tenant_session(platform_auth, session_factory):
            session.add(store)
            await session.flush()
            await session.refresh(store)
            created_ids.append(store.id)
        return store

    yield _make

    if created_ids:
        async for session in get_tenant_session(platform_auth, session_factory):
            await session.execute(
                delete(Store).where(Store.id.in_(created_ids))
            )


@pytest_asyncio.fixture
async def make_tenant_user(
    session_factory,
    platform_auth,
) -> AsyncIterator[Callable[..., Awaitable[TenantUser]]]:
    """Async factory: insert + commit a TenantUser via PLATFORM session.
    Tracks IDs and DELETEs at teardown.

    `status` defaults to 'ACTIVE' (the value most relevant to the
    num_users_active subquery). Override via kwargs for tests
    exercising other statuses.
    """
    created_ids: list[UUID] = []

    async def _make(
        *,
        tenant_id: UUID,
        status: str = "ACTIVE",
        **overrides,
    ) -> TenantUser:
        user = TenantUser(tenant_id=tenant_id, status=status, **overrides)
        async for session in get_tenant_session(platform_auth, session_factory):
            session.add(user)
            await session.flush()
            await session.refresh(user)
            created_ids.append(user.id)
        return user

    yield _make

    if created_ids:
        async for session in get_tenant_session(platform_auth, session_factory):
            await session.execute(
                delete(TenantUser).where(TenantUser.id.in_(created_ids))
            )
```

These fixtures use the lightweight Store/TenantUser ORM stubs from File 8. The `**overrides` pattern allows callers to set any field that's been declared on the stub; the stubs only declare the minimal column set, so test-side overrides are limited to those columns. If a test needs to set a non-stubbed column (unlikely in 3.3), surface and we'll widen the stub.

Note: these fixtures become the foundation for Steps 4.5 (Stores) and 5.2 (TenantUsers) integration tests. They're additive — Step 4.5/5.2 will widen them to use the full ORM models, but the basic shape (commit + DELETE-tracked teardown) carries forward.

### File 12: `tests/integration/test_tenants_router.py` — new

Integration tests exercising the three endpoints through FastAPI's TestClient, against real Postgres. Reuse `make_tenant`, `platform_session`, `tenant_session_factory` fixtures from Step 3.2's conftest.

Tests by endpoint:

**List endpoint:**
- L1. PLATFORM, no params → returns all visible tenants, paginated. `pagination.total` matches.
- L2. PLATFORM, `tier=ENTERPRISE` → returns only enterprise tenants.
- L3. PLATFORM, `search=acme` → ILIKE match across name/display_code/contact_email.
- L4. PLATFORM, `search=` (empty after trim) → behaves as no filter.
- L5. **Deterministic pagination test.** Create 5 tenants with unique-prefix names: `L5-Alpha`, `L5-Bravo`, `L5-Charlie`, `L5-Delta`, `L5-Echo`. Then call `GET /api/v1/tenants?search=L5-&limit=2&offset=2`. Assert `items[0].name == "L5-Charlie"`, `items[1].name == "L5-Delta"`, `pagination.total == 5`, `pagination.offset == 2`, `pagination.limit == 2`. The `search=L5-` filter scopes to this test's data, isolating from other tests' tenants. This is also a load-bearing test for the search-AND-pagination interaction (both filters applied to the count query).
- L6. PLATFORM, `limit=200` → 422 (validation cap).
- L7. PLATFORM, `tier=invalid` → 422 (enum validation).
- L8. TENANT-A → returns exactly 1 row (tenant A); `pagination.total = 1`.
- L9. RLS-under-aggregates: insert 3 stores under TENANT-A (`make_store(tenant_id=tenant_a.id)` × 3) and 2 stores under TENANT-B. Insert 4 active and 1 suspended tenant_users under TENANT-A. Call list under PLATFORM session. Assert TENANT-A's row in the response has `num_stores=3` and `num_users_active=4`. Assert TENANT-B's row has `num_stores=2`. **This validates that the per-row subqueries correctly scope to each tenant via `.correlate(Tenant)`.**
- L10. Modules: each returned item has the module list expected from the stub. Assert that for a tenant in the stub, `len(item.modules) > 0` and that `modules[0].name` matches `_MODULE_NAMES[modules[0].code]`. For an unknown tenant ID (one not in `_TENANT_MODULES`), `modules == []`.

**Stats endpoint:**
- S1. PLATFORM (no extra setup beyond what other tests created) → `total_tenants` and `total_stores` are positive ints. Don't assert exact counts; the platform-wide count includes data from concurrent or earlier tests. **Subset assertion:** total_tenants ≥ 1 and total_stores ≥ 0.
- S2. TENANT-A → `total_tenants == 1` (only the caller's own tenant) and `total_stores ==` the count of stores owned by tenant A (set up in this test via `make_store`).
- S3. Cache-Control header is set to `private, max-age=60`.

**Detail endpoint:**
- D1. PLATFORM, valid tenant id → 200 with all 21 fields populated; `num_stores` and `num_users_active` match the stores/users created in test setup.
- D2. PLATFORM, non-existent UUID (use `uuid.uuid4()` for ephemeral test ID) → 404. **Body shape assertion:** body has exactly 4 keys `{code, message, details, request_id}`; `code == "TENANT_NOT_FOUND"`; `message == "Tenant not found"`; `details is None`; `request_id` is a valid UUID. This is the canonical error-envelope assertion; future endpoint tests reuse the pattern.
- D3. TENANT-A, own id → 200.
- D4. TENANT-A, TENANT-B's id → 404 (RLS-blocked → not-found per D-17). **Load-bearing security regression test.** Body shape same as D2.
- D5. Malformed UUID in path (`/api/v1/tenants/not-a-uuid`) → 422. Body shape: `{code: "VALIDATION_ERROR" or similar, message, details, request_id}`. The `details` field MAY contain per-field error info from FastAPI's validation; assert body has at least the four canonical keys.
- D6. Modules: detail's modules array matches stub for that tenant.

Plus auth:
- A1. No `Authorization` header → 401 from middleware (covers all three endpoints).
- A2. Invalid JWT → 401 (covers all three).

About 18-19 tests total.

Test infrastructure: use FastAPI's `TestClient` per CLAUDE.md "Module" tier — FastAPI + Postgres + stub auth. Mint test JWTs via `make_test_jwt` from Step 2.1's helpers. The `app_with_test_routes` pattern from Step 2.3's middleware tests is the closest precedent; adapt for "real app, real router, no test-only routes."

### File 13: `docs/endpoints/tenants.md` — new

Canonical 8-section template per CLAUDE.md "Per-endpoint documentation." Three endpoints, each with all 8 sections:

1. Endpoint summary (method, path, description, who can call)
2. Request (auth, path/query params, body — none for these)
3. Response 200 (full shape with sample, field-by-field reference)
4. Response codes (error table with sample bodies)
5. Behaviour notes (RLS scope, sort, pagination, edge cases)
6. Example calls (curl)
7. Sample integration code (TypeScript snippet — keep light for v0)
8. Implementation reference (file pointers: router, repo method, schema class)

This is the template every subsequent endpoint doc inherits. Get the structure right; future endpoints copy-paste-edit.

### File 14: `BUILD_PLAN.md` — modify

- **Step 3.3 status:** TODO → DONE.
- **Scope-in:** rewrite. Original says "two endpoints, list returns `list[TenantRead]`, references `docs/api-contract.md`." New entry names three endpoints, the contract-cut shape decisions (no sort, no status filter, single-value tier, `num_stores` live, `num_users_active`, modules from stub, flat detail), the stub file with FN-AB tripwire, the new D-XX entries.
- **Acceptance:** rewrite. Drop the "matches `docs/api-contract.md`" line (stale; api-contract.md is template). Replace with the actual response shapes and test expectations.
- **Effort:** rewrite from "60-90 min" to roughly a full day.

### File 15: `CLAUDE.md` — modify

- **Current state → Completed:** add Step 3.3 bullet covering the three endpoints, the new schemas, the new Repo methods, the stub file with FN-AB tripwire, the api-prefix wiring, the two new D-XX entries.
- **Current state → Not yet completed:** advance "Steps 3.3 onward" to "Steps 4.x onward."
- **New D-XX entry: Response wrapping shape.** Suggested D-30:
  > **D-30 — Response envelope is list-only.**
  >
  > *What.* Endpoints returning a collection wrap as `{items, pagination}`. Endpoints returning a single object return the object directly with no wrapper. No top-level `data` key, no `result` key — single-object responses *are* the object.
  >
  > *Why.* Wrapping single objects adds a layer the frontend has to peel for no benefit. The list wrapper exists because pagination metadata has nowhere else to live; a single-object response has no metadata to carry.
  >
  > *Reconsider if.* Cross-cutting metadata (rate-limit body indicators, partial-result flags) becomes a recurring need across all endpoint types. Until then, the simpler shape wins.
- **New D-XX entry: Field-meaning lock.** Suggested D-31:
  > **D-31 — Response field semantics are append-only.**
  >
  > *What.* Once a field ships with defined meaning, that meaning is frozen for the lifetime of the API version. New variants are added as new fields with distinct names, never as semantic reinterpretations of existing fields.
  >
  > *Why.* Renaming-while-keeping-the-name is invisible to frontend code that's already shipped; it breaks consumers without compile-time signal. Adding a new field is visible: the frontend opts into using it.
  >
  > *Reconsider if.* Never; this is a compatibility invariant. The escape hatch is bumping the API version (v2), not redefining v1 fields.
- **New FN-AB entry: Module stub cleanup.** Suggested FN-AB-16 (verify next-free):
  > **FN-AB-16 — `tenant_module_access` table not yet shipped; module entitlements served from a stub.**
  >
  > Step 3.3 ships module data from a hardcoded Python dict in `src/admin_backend/repositories/_module_entitlements_stub.py`. The xfail-strict test in `tests/unit/test_module_entitlements.py` is a tripwire: when a future step lands the `tenant_module_access` table and rewrites the Repo to query it, the test xpasses (test failure under strict=True), forcing deletion of the stub file and the test.
  >
  > Resolution: a future step adding the DDL, migration, RLS policy following D-29, model, and replacing the stub call with a real query in `TenantsRepo.list_with_aggregates` and `TenantsRepo.get_by_id_with_aggregates`.

### File 16: `docs/architecture.md` — likely yes-edit

The "Code structure" section may need to mention the routers layer (router → repo → model flow). Read first; if the doc already describes the layer abstractly, no edit needed (3.3 implements an already-named layer). If the doc is silent on it, add a short paragraph. Per the convention extension: don't hunt for an edit. If nothing changed at the system-shape level, skip.

The Layer 1 RLS section probably doesn't need edits — RLS behaviour didn't change in this step.

### File 17: `prompts/step-3_3-tenants-router-2026-05-02.md` — new

This prompt file. Bundled per the per-step convention.

---

## Testing and regression discipline

### New tests added by this step

- **18-19 integration tests** in `tests/integration/test_tenants_router.py` (specified in File 12).
- **1 unit test** (the xfail tripwire) in `tests/unit/test_module_entitlements.py`.

Design discipline:
- Each test must fail against an empty router (handler returns 500 / route 404). Verify by running tests against the new test file *before* implementing the handlers; expect mass failure.
- The cross-tenant 404 test (D4) is the load-bearing security assertion — call it out by name in the report.

### Regression risk surface introduced by this step

1. **Router include order in main.py.** `/stats` MUST come before `/{tenant_id}` in the router file (FastAPI's first-match-wins routing means a parameterised path swallows static paths declared after it). Verify by curling `/api/v1/tenants/stats` after the work and confirming it returns the stats response, not a 422 "stats is not a valid UUID."
2. **API prefix change affects health/ready endpoints.** Step 2.4's health/ready endpoints likely currently mount at `/v1/health` / `/v1/ready`. After the prefix setting lands, they should mount at `/api/v1/health` / `/api/v1/ready`. Existing `tests/integration/test_health.py` may have hardcoded URLs that need updating. Check and fix in this step's commit.
3. **Error envelope shape change breaks Step 2.3 tests.** Step 2.3's exception handler currently emits `{code, message, request_id}`. Adding `details: None` changes the body shape. Specifically check:
   - `tests/integration/test_middleware.py` — any test that asserts exact-shape on error response bodies needs updating from `{code, message, request_id}` to `{code, message, details, request_id}` with `details: None`.
   - The handler itself in `main.py` (or wherever Step 2.3 placed it) — add `"details": None` to both the ServerError and ClientError JSON response paths.
   - Existing tests that don't assert exact shape (e.g., `assert response.json()["code"] == "AUTH_INVALID"`) keep working without change.

   Run the existing test suite *first* with no other changes; identify which specific assertions break; update them; only then add D2's new assertion (which expects 4 keys).
4. **The lightweight ORM stubs interact dangerously with Alembic autogenerate.** The mechanism: stubs declare `__tablename__` and `__table_args__["schema"]` for tables (`stores`, `tenant_users`) that already exist in the DB with full column sets. If `migrations/env.py` ever sets `target_metadata = Base.metadata` (currently `None`), Alembic autogenerate would compare the stubs (id + tenant_id + status only) against the live tables (~20 columns each) and propose ALTER TABLE DROP statements for every undeclared column. **Do not change `migrations/env.py`'s `target_metadata = None` setting in this step.** When 4.5/5.2 land the full models, Alembic wiring is reconsidered then. The CRITICAL note in the stub file's docstring (File 8) reinforces this rule for future readers.
5. **Pagination `total` query is a second roundtrip.** Two queries per list call. For 7 tenants this is fine. Verified by integration test L5 that asserts the count matches.
6. **`make_tenant`, `make_store`, `make_tenant_user` fixtures and `Base.metadata`.** This step adds two new ORM stubs to `Base.metadata`. The existing `make_tenant` fixture from Step 3.2 inserts only into `tenants` and is unaffected. The new `make_store` and `make_tenant_user` fixtures hit the stubbed tables, which exist as full tables in the DB (from Step 1.4 DDL load) — inserts work because the DB has all columns and the stub-declared subset matches. Verify by running Step 3.2's existing 9 Repo tests and confirming they still pass after the new fixtures are added (they should — additive only).
7. **mypy strict on multi-column SQLAlchemy selects.** The new Repo methods use selects of the form `select(Tenant, subquery1.label(...), subquery2.label(...))` and unpack results as `(tenant, ns, nua) = row`. SQLAlchemy 2.x's typing for multi-column selects has improved but isn't always frictionless under mypy strict. Specific hot spots: `result.scalar_one()` returns `Any` in some stub versions; `result.all()` returns `Sequence[Row[...]]` where the row tuple's element types may need explicit hinting. Plan for ~30 minutes of mypy-clean-up work even if the logic is right; `typing.cast` and explicit `Mapped[T]` annotations may be needed. Don't paper over with `# type: ignore` — fix properly.
8. **`get_tenant_session_dep` async-iteration in fixtures.** Step 3.2's conftest pattern uses `async for session in get_tenant_session(...)` to drive the dependency-as-generator. The new fixtures (`make_store`, `make_tenant_user`) follow the same pattern. Verify the iteration semantics are right — the loop body should run exactly once per call (the generator yields once). If a future implementation makes `get_tenant_session` yield multiple times, these fixtures would behave incorrectly. Not a current risk; flagging in case the pattern is examined.

### Verification harness (run all five; all must be green)

```bash
# 1. Full pytest suite — new + regression
uv run pytest -v

# 2. mypy strict on the new and surrounding modules
uv run mypy --strict src/admin_backend

# 3. Pre-flight checker
./scripts/check_setup.sh

# 4. RLS smoke test still 64 PASS
python scripts/smoke_test.py

# 5. Manual smoke: each endpoint returns 200 with expected shape
# (use curl or httpie against a running local server; document one example per endpoint in the report)
uv run uvicorn admin_backend.main:app --port 8000 &
sleep 2
JWT=$(uv run python -c "from admin_backend.auth.testing import make_test_jwt; print(make_test_jwt(user_type='PLATFORM'))")
curl -H "Authorization: Bearer $JWT" http://localhost:8000/api/v1/tenants/stats
curl -H "Authorization: Bearer $JWT" "http://localhost:8000/api/v1/tenants?limit=2"
# detail requires a real tenant ID; fetch from the list response above
kill %1
```

Expected: ~89 pytest passes (70 prior + 18-19 new); mypy clean; check_setup 35/35; smoke 64 PASS; manual curl returns the expected JSON shapes.

If any of the five is not green, **report the failure rather than the step.** Don't ship a step with one leg of the harness dropped.

---

## Scope out

- **Other resources** (stores, users, RBAC). Steps 4.5, 5.x, 6.x.
- **Write endpoints** (POST, PATCH, suspend, terminate). Post-v0 per FN-AB-12.
- **`tenant_module_access` table.** Deferred via stub; tracked as FN-AB-16.
- **`legal_name` column.** Cut from contract; v0.1 if frontend asks.
- **Live MRR.** `monthly_revenue_usd` stays self-reported until live revenue tracking ships.
- **Audit-actor exposure on detail.** `*_by_user_id` fields stay hidden per Step 3.1.
- **`docs/endpoints/_example_tenants.md`** referenced in CLAUDE.md "Per-endpoint documentation." That file becomes `docs/endpoints/tenants.md` in this step (the canonical example *is* the tenants doc; no separate example file).
- **RBAC enforcement** (`ADMIN.TENANTS.VIEW` permission check). Step 6.1.
- **`/lookups` endpoint** for dropdown options. Separate concern; not in 3.3 scope.

---

## Stop and ask if

- The existing `docs/endpoints/` directory doesn't exist or has unexpected files. The convention is to create the directory at this step (3.3 is the first endpoint doc); verify before writing.
- The `app_with_test_routes` pattern from Step 2.3 doesn't fit "real app, real router" testing. The `client` fixture in conftest may need extension or a new fixture pattern. Surface what you find.
- The error envelope `details` field addition breaks an existing test by changing exact-shape expectations. Surface and we'll decide whether to update tests or skip the `details` addition until needed.
- Health/ready endpoints don't migrate cleanly to the new `/api/v1/...` prefix (e.g., they use a different mounting pattern that doesn't honour the prefix setting). Surface; we'll either standardise or document the asymmetry.
- The lightweight Store/TenantUser stubs cause unexpected mypy errors or SQLAlchemy registry conflicts (e.g., the registry already had these classes from another path). Surface concrete error.
- The xfail-strict tripwire test runs but xpasses immediately (i.e., the heuristic in the test asserts something that's already false against the stub-based implementation). The test design should fail under the stub and pass under a real query. If it passes under the stub, the heuristic is wrong; surface and we'll redesign.
- A test reveals an actual RLS gap (cross-tenant data visible). Stop, surface, do not commit.
- Performance of the list endpoint with 7 tenants and per-row subqueries is unexpectedly bad (>500ms). Should be <100ms; if it's slower, the subqueries may not be hitting indexes correctly. Surface query plans.

---

## Acceptance criteria

- 17-19 files created/modified per the bundle (range accommodates whether `architecture.md` and `tests/integration/test_health.py` need edits — both are conditional).
- Three endpoints reachable at `/api/v1/tenants`, `/api/v1/tenants/stats`, `/api/v1/tenants/{id}`.
- All new tests pass: 18-19 integration tests + 1 xfail unit test (xfail still failing-as-expected pre-cleanup).
- All existing 79 tests still pass — no regressions. Step 2.3's middleware tests may need updates to error envelope assertions per regression risk #3; updates are part of this step's scope, not a regression.
- mypy strict clean across `src/admin_backend`. The Repo's multi-column selects may require `typing.cast` or explicit annotations per regression risk #7.
- `check_setup.sh` 35/35.
- RLS smoke test still 64 PASS.
- Manual curl smoke (3 endpoints) returns expected shapes; sample outputs in the report.
- **Cross-tenant isolation test D4 passes — load-bearing security assertion. Report calls it out by name.**
- **Aggregate-under-RLS test L9 passes — verifies per-row subqueries scope correctly via `.correlate(Tenant)`. Report calls it out by name.**
- `docs/endpoints/tenants.md` written, all 8 sections per endpoint, three endpoints documented.
- Two new D-XX entries in CLAUDE.md (D-30 wrapping, D-31 field-meaning lock); one new FN-AB entry (FN-AB-16 module stub cleanup).
- BUILD_PLAN.md Step 3.3 entry rewritten and flipped to DONE.
- OpenAPI generation verified: curl `/api/v1/openapi.json` returns valid JSON with three operation IDs (`list_tenants`, `tenants_stats`, `get_tenant` or whatever names FastAPI assigns based on handler function names) and the request/response schemas matching the Pydantic classes. No special "make it correct" step beyond writing the routes and schemas correctly — but verify the spec actually generates without errors.

---

## Report (BEFORE proposing commit)

Five bundles per the convention:

1. **Code/tests:** files created/modified with line counts; the three endpoint shapes verified via manual curl (sample outputs); test count delta; xfail tripwire test confirmed failing-as-expected; D4 cross-tenant 404 result called out by name.
2. **CLAUDE.md updates:** Current state Completed/Not-yet-completed; D-30, D-31, FN-AB-16 added with the wording above (or improvements thereof).
3. **BUILD_PLAN.md updates:** Step 3.3 status DONE; scope-in/acceptance/effort rewritten.
4. **architecture.md updates:** specific edits made, or "no change" with one-line reason.
5. **Prompt file:** `prompts/step-3_3-tenants-router-2026-05-02.md` confirmed in commit set.

Plus: pytest counts, mypy status, check_setup status, smoke test status, manual curl results.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
