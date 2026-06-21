"""Tenants router: list / stats / detail endpoints (Step 3.3).

Three GET handlers under the ``/tenants`` sub-prefix; the parent
``/api/v1`` prefix comes from ``settings.api_prefix`` at
``app.include_router`` time in ``main.py``.

Route ordering note. ``/stats`` is declared BEFORE ``/{tenant_id}``.
FastAPI matches routes top-to-bottom; if ``/{tenant_id}`` were
declared first, ``GET /tenants/stats`` would be parsed as ``stats``
being a malformed UUID, returning 422.

Tenant context flows through ``Depends(get_tenant_session_dep)`` —
the session arrives with ``app.tenant_id`` and ``app.user_type``
already set from AuthContext per D-24. Handlers never look at
``request.state.auth`` directly.

The detail endpoint surfaces missing or RLS-filtered rows as 404 via
``TenantNotFoundError`` per D-17. The list and stats endpoints simply
return whatever RLS lets through (zero rows is a valid response, not
an error).
"""
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.anchor_deps import get_tenant_anchor
from admin_backend.auth.context import AuthContext
from admin_backend.auth.permissions import require
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.errors import (
    EmptyPatchError,
    InvalidSortKeyClientError,
    InvalidStateTransitionError,
    TenantNotFoundError,
)
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant import TenantTier
from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.repositories._errors import InvalidSortKeyError
from admin_backend.repositories.tenants import (
    DEFAULT_TENANTS_SORT,
    TenantDetailRow,
    TenantListRow,
    TenantsRepo,
    TransitionResult,
)
from admin_backend.schemas.tenant import (
    Module,
    Pagination,
    TenantCreateRequest,
    TenantDetail,
    TenantPatchRequest,
    TenantsListItem,
    TenantsListResponse,
    TenantsStatsResponse,
)


router = APIRouter(prefix="/tenants", tags=["tenants"])

# Stateless instance reused across requests. The Repo holds no
# session, no settings, no config — it's a method bag.
_repo = TenantsRepo()


def _list_item_from_row(row: TenantListRow) -> TenantsListItem:
    """Map a TenantListRow (Tenant + aggregates) -> TenantsListItem.

    Modules arrive on the row as ``list[dict[str, str]]`` from the
    Repo's ``jsonb_agg`` subquery; the COALESCE in the subquery
    guarantees the list is empty rather than None when no modules are
    enabled.
    """
    t = row.tenant
    modules = [Module(code=m["code"], name=m["name"]) for m in row.modules]
    return TenantsListItem(
        id=t.id,
        name=t.name,
        display_code=t.display_code,
        country=t.country,
        region=t.region,
        industry=t.industry,
        tier=t.tier,
        status=t.status,
        monthly_revenue_usd=t.monthly_revenue_usd,
        num_stores=row.num_stores,
        num_users_active=row.num_users_active,
        modules=modules,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _detail_from_row(row: TenantDetailRow) -> TenantDetail:
    """Map a TenantDetailRow -> TenantDetail. Mirrors _list_item_from_row
    for the additional fields exposed only on detail."""
    t = row.tenant
    modules = [Module(code=m["code"], name=m["name"]) for m in row.modules]
    return TenantDetail(
        id=t.id,
        name=t.name,
        display_code=t.display_code,
        country=t.country,
        region=t.region,
        tier=t.tier,
        industry=t.industry,
        monthly_revenue_usd=t.monthly_revenue_usd,
        monthly_revenue_as_of_date=t.monthly_revenue_as_of_date,
        number_of_stores=t.number_of_stores,
        number_of_stores_as_of_date=t.number_of_stores_as_of_date,
        primary_contact_name=t.primary_contact_name,
        contact_email=t.contact_email,
        status=t.status,
        created_at=t.created_at,
        updated_at=t.updated_at,
        suspended_at=t.suspended_at,
        terminated_at=t.terminated_at,
        num_stores=row.num_stores,
        num_users_active=row.num_users_active,
        modules=modules,
    )


@router.post(
    "",
    response_model=TenantDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant(
    body: TenantCreateRequest,
    request: Request,
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

    Server-forces ``status=TRIAL`` per locked decision 3; the DDL
    default ``ONBOARDING`` is never hit through this path. The ADMIN
    module is force-merged into ``modules_enabled`` (schema validator)
    and bundled ``tenant_module_access`` rows land in the same
    transaction as the ``tenants`` row.

    Gate: ``audience="PLATFORM"`` + ``ADMIN.TENANTS.CONFIGURE.GLOBAL``
    (held by SUPER_ADMIN and PLATFORM_ADMIN per Phase 3 seed).
    TENANT JWTs are refused at Layer 1 with 403
    ``PLATFORM_AUDIENCE_REQUIRED``, ahead of the permission check.

    Errors:
      - 409 ``DUPLICATE_TENANT_NAME`` if a tenant with that name exists
        (app-layer SELECT-then-INSERT; FN-AB tracks the missing UNIQUE
        constraint).
    """
    row = await _repo.create(
        session,
        name=body.name,
        region=body.region.value,
        tier=body.tier.value,
        industry=body.industry.value,
        country=body.country,
        primary_contact_name=body.primary_contact_name,
        contact_email=body.contact_email,
        number_of_stores=body.number_of_stores,
        number_of_stores_as_of_date=body.number_of_stores_as_of_date,
        display_code=body.display_code,
        monthly_revenue_usd=body.monthly_revenue_usd,
        monthly_revenue_as_of_date=body.monthly_revenue_as_of_date,
        modules_enabled=body.modules_enabled,
        actor_user_id=auth.user_id,
        auth=auth,
        request_id=request.state.request_id,
    )
    return _detail_from_row(row)


@router.get("", response_model=TenantsListResponse)
async def list_tenants(
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.VIEW,
        PermissionScope.GLOBAL,
    )),
    session: AsyncSession = Depends(get_tenant_session_dep),
    tier: TenantTier | None = Query(None),
    search: str | None = Query(None),
    sort: str = Query(
        DEFAULT_TENANTS_SORT,
        description=(
            "Sort key. Column-based: created_at_asc, created_at_desc "
            "(default), name_asc, name_desc, tier_asc, tier_desc. "
            "Aggregate-based (correlated subqueries, RLS-correct): "
            "num_users_active_asc, num_users_active_desc, "
            "num_stores_asc, num_stores_desc. Stable secondary sort by "
            "id ASC for deterministic pagination on tied primary keys."
        ),
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> Any:
    """List tenants visible to the caller, paginated.

    Behaviour:
    - PLATFORM session sees all rows (D-29 OR-branch on tenants).
    - TENANT session sees only the row matching ``app.tenant_id``.
    - ``pagination.total`` is RLS-filtered: it reflects what the caller
      can see, not the platform total.
    - ``search`` is trimmed; empty after trim is treated as no filter.
    - ``sort`` defaults to ``created_at_desc``. **Pre-Step-6.4 the
      endpoint had no sort param and ordering was hardcoded ``name
      ASC``; callers who don't pass ``sort`` now receive
      ``created_at_desc`` (newest first).**
    """
    if search is not None:
        trimmed = search.strip()
        search = trimmed if trimmed else None

    try:
        rows, total = await _repo.list_with_aggregates(
            session,
            tier=tier,
            search=search,
            sort=sort,
            offset=offset,
            limit=limit,
        )
    except InvalidSortKeyError as exc:
        raise InvalidSortKeyClientError(str(exc), sort=sort) from exc

    items = [_list_item_from_row(r) for r in rows]
    return TenantsListResponse(
        items=items,
        pagination=Pagination(total=total, offset=offset, limit=limit),
    )


@router.get("/stats", response_model=TenantsStatsResponse)
async def tenants_stats(
    response: Response,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.VIEW,
        PermissionScope.GLOBAL,
    )),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Header summary scalars: total_tenants and total_stores.

    Both RLS-filtered. For PLATFORM callers (the realistic consumer)
    these reflect platform totals. ``Cache-Control: private,
    max-age=60`` is set per the contract — the only endpoint setting
    a cache header in v0.

    Note on the Response injection: setting headers on the injected
    Response object lets us keep ``response_model=TenantsStatsResponse``
    auto-validation. Constructing a JSONResponse manually here would
    bypass that validation.
    """
    response.headers["Cache-Control"] = "private, max-age=60"
    total_tenants, total_stores = await _repo.count_for_stats(session)
    return TenantsStatsResponse(
        total_tenants=total_tenants,
        total_stores=total_stores,
    )


@router.get("/{tenant_id}", response_model=TenantDetail)
async def get_tenant(
    tenant_id: UUID,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
        anchor_dep=get_tenant_anchor,
    )),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Return the full detail shape for a single tenant.

    404 if the row doesn't exist OR is RLS-filtered. Per D-17 the
    handler can't and shouldn't distinguish — both produce the same
    body shape (``code: TENANT_NOT_FOUND``). Malformed UUID in the
    path is rejected by FastAPI's path-param validation as 422
    before this handler runs.
    """
    row = await _repo.get_by_id_with_aggregates(session, tenant_id)
    if row is None:
        raise TenantNotFoundError(
            f"Tenant {tenant_id} not visible to this session",
            tenant_id=str(tenant_id),
        )
    return _detail_from_row(row)


@router.patch("/{tenant_id}", response_model=TenantDetail)
async def patch_tenant(
    tenant_id: UUID,
    body: TenantPatchRequest,
    request: Request,
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
    """Partial update of a tenant. Platform-only.

    Same gate as POST /tenants. ``status``, ``region``, and audit-column
    fields are rejected at the schema layer via ``extra="forbid"``.
    Status transitions go through ``/suspend`` and ``/activate``.

    Behaviour:
      - 422 ``EMPTY_PATCH`` when the body has no fields set.
      - 409 ``DUPLICATE_TENANT_NAME`` when renaming to a name another
        tenant already holds (rename-to-self is a 200 no-op).
      - 404 ``TENANT_NOT_FOUND`` when the row is missing or RLS-filtered
        (RLS-as-404 per D-17).
      - Allowed on rows in any non-TERMINATED state including SUSPENDED.

    Multi-audience PATCH (TENANT OWNER editing own tenant's
    operational fields) is deferred post-6.16 — the tenants table uses
    Pattern (a) typed FKs to ``platform_users`` for audit columns per
    D-13, which would reject a TENANT-side UPDATE at the FK layer.
    """
    sent_fields = body.model_dump(exclude_unset=True)
    if not sent_fields:
        raise EmptyPatchError(
            f"PATCH on tenant {tenant_id} had no set fields",
            tenant_id=str(tenant_id),
        )

    updated = await _repo.update(
        session,
        tenant_id,
        fields=sent_fields,
        actor_user_id=auth.user_id,
        auth=auth,
        request_id=request.state.request_id,
    )
    if updated is None:
        raise TenantNotFoundError(
            f"Tenant {tenant_id} not visible to this session",
            tenant_id=str(tenant_id),
        )
    return _detail_from_row(updated)


@router.post("/{tenant_id}/suspend", response_model=TenantDetail)
async def suspend_tenant(
    tenant_id: UUID,
    request: Request,
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
    """Suspend a tenant. Platform-only. Held by SUPER_ADMIN only per
    the catalogue (Phase 3 seed; PLATFORM_ADMIN holds CONFIGURE.GLOBAL
    for create/edit but NOT OVERRIDE.GLOBAL).

    Allowed sources: TRIAL or ACTIVE. SUSPENDED -> SUSPENDED returns
    409 ``INVALID_STATE_TRANSITION``. Successful transitions populate
    ``suspended_at`` and ``suspended_by_user_id`` atomically with the
    ``status`` flip (required by ``ck_tenants_suspended_consistency``).
    """
    row, result = await _repo.transition(
        session,
        tenant_id,
        target_status="SUSPENDED",
        actor_user_id=auth.user_id,
        auth=auth,
        request_id=request.state.request_id,
    )
    if result is TransitionResult.NOT_FOUND:
        raise TenantNotFoundError(
            f"Tenant {tenant_id} not visible to this session",
            tenant_id=str(tenant_id),
        )
    if result is TransitionResult.INVALID_STATE:
        raise InvalidStateTransitionError(
            (
                f"tenant {tenant_id} cannot transition to SUSPENDED "
                "from its current status"
            ),
            tenant_id=str(tenant_id),
            target_status="SUSPENDED",
        )
    assert row is not None  # TransitionResult.OK guarantees a row
    return _detail_from_row(row)


@router.post("/{tenant_id}/activate", response_model=TenantDetail)
async def activate_tenant(
    tenant_id: UUID,
    request: Request,
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
    """Activate a tenant. Platform-only; same OVERRIDE.GLOBAL gate as
    /suspend.

    Allowed sources: TRIAL or SUSPENDED. ACTIVE -> ACTIVE returns 409
    ``INVALID_STATE_TRANSITION``. SUSPENDED -> ACTIVE clears
    ``suspended_at`` and ``suspended_by_user_id`` atomically with the
    ``status`` flip (required by ``ck_tenants_suspended_consistency``).
    A SUSPENDED tenant activated never lands back in TRIAL.
    """
    row, result = await _repo.transition(
        session,
        tenant_id,
        target_status="ACTIVE",
        actor_user_id=auth.user_id,
        auth=auth,
        request_id=request.state.request_id,
    )
    if result is TransitionResult.NOT_FOUND:
        raise TenantNotFoundError(
            f"Tenant {tenant_id} not visible to this session",
            tenant_id=str(tenant_id),
        )
    if result is TransitionResult.INVALID_STATE:
        raise InvalidStateTransitionError(
            (
                f"tenant {tenant_id} cannot transition to ACTIVE "
                "from its current status"
            ),
            tenant_id=str(tenant_id),
            target_status="ACTIVE",
        )
    assert row is not None
    return _detail_from_row(row)
