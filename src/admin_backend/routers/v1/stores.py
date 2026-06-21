"""Stores router: list / detail endpoints (Step 6.17.2).

Two GET handlers under the ``/stores`` sub-prefix; the parent
``/api/v1`` prefix comes from ``settings.api_prefix`` at
``app.include_router`` time in ``main.py``.

Multi-user-type per the v0 auth model: both PLATFORM and TENANT JWTs
accepted; visibility is scoped by RLS via the session GUCs set by
``get_tenant_session``. PLATFORM sees all rows via D-29's OR-branch;
TENANT sees only own-tenant rows. Cross-tenant probes by a TENANT JWT
surface as 404 ``STORE_NOT_FOUND`` (RLS-as-404 per D-17).

Gate: ``ADMIN.STORES.VIEW.TENANT`` on both endpoints. SUPER_ADMIN +
PLATFORM_ADMIN pass via the GLOBAL→TENANT scope cascade; TENANT OWNER
passes via the direct ``.TENANT`` grant (Step 6.17.1 seed update).
Store Manager has only ``.STORE`` scope and is denied by the cascade
direction (a STORE grant doesn't satisfy a TENANT-scoped check).
"""
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.anchor_deps import get_store_anchor
from admin_backend.auth.context import AuthContext
from admin_backend.auth.permissions import require
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.errors import (
    EmptyPatchError,
    InvalidSortKeyClientError,
    InvalidStateTransitionError,
    StoreNotFoundError,
    TenantNotFoundError,
)
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.store import StoreStatus
from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.repositories._errors import InvalidSortKeyError
from admin_backend.repositories.stores import (
    DEFAULT_STORES_SORT,
    StoreDetailRow,
    StoresListRow,
    StoresRepo,
)
from admin_backend.repositories.tenants import TransitionResult
from admin_backend.schemas.store import (
    StoreCreateRequest,
    StoreDetail,
    StoreListItem,
    StoreListResponse,
    StorePatchRequest,
    StoreSetStatusRequest,
)
from admin_backend.schemas.tenant import Pagination


router = APIRouter(prefix="/stores", tags=["stores"])

# Stateless instance reused across requests.
_repo = StoresRepo()


def _list_item_from_row(row: StoresListRow) -> StoreListItem:
    """Map a StoresListRow (Store + joined tenant_name) -> StoreListItem."""
    s = row.store
    return StoreListItem(
        id=s.id,
        tenant_id=s.tenant_id,
        tenant_name=row.tenant_name,
        name=s.name,
        store_code=s.store_code,
        country=s.country,
        status=s.status,
        created_at=s.created_at,
    )


def _detail_from_row(row: StoreDetailRow) -> StoreDetail:
    """Map a StoreDetailRow -> StoreDetail. Audit-actor columns hidden."""
    s = row.store
    return StoreDetail(
        id=s.id,
        tenant_id=s.tenant_id,
        tenant_name=row.tenant_name,
        org_node_id=s.org_node_id,
        name=s.name,
        store_code=s.store_code,
        country=s.country,
        timezone=s.timezone,
        address=s.address,
        latitude=s.latitude,
        longitude=s.longitude,
        currency=s.currency,
        tax_treatment=s.tax_treatment,
        status=s.status,
        created_at=s.created_at,
        updated_at=s.updated_at,
        closed_at=s.closed_at,
    )


@router.get("", response_model=StoreListResponse)
async def list_stores(
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.STORES,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
    )),
    session: AsyncSession = Depends(get_tenant_session_dep),
    tenant_id: UUID | None = Query(
        None,
        description=(
            "Filter to a single tenant. Useful for PLATFORM callers "
            "scoping a list view; TENANT callers see only their own "
            "tenant regardless (RLS handles it)."
        ),
    ),
    status_: StoreStatus | None = Query(
        None,
        alias="status",
        description="Filter by status: OPENING, ACTIVE, INACTIVE, or CLOSED.",
    ),
    country: str | None = Query(
        None,
        description="Exact-match filter on country (case-sensitive).",
    ),
    search: str | None = Query(
        None,
        description=(
            "Case-insensitive substring match across name and store_code."
        ),
    ),
    sort: str = Query(
        DEFAULT_STORES_SORT,
        description=(
            "Sort key. Field-based: name_asc, name_desc, created_at_asc, "
            "created_at_desc, status_asc, country_asc. Cross-table: "
            "tenant_name_asc (default), tenant_name_desc; both apply a "
            "stable secondary sort by stores.name ASC for deterministic "
            "pagination within a tenant. Unknown values -> 400 "
            "INVALID_SORT_KEY."
        ),
    ),
    offset: int = Query(0, ge=0, description="Standard pagination."),
    limit: int = Query(
        50, ge=1, le=100, description="Standard pagination."
    ),
) -> Any:
    """List stores visible to the caller, paginated.

    PLATFORM sees all rows (D-29 OR-branch); TENANT sees own-tenant
    only. ``pagination.total`` is RLS-filtered (reflects what the
    caller can see, not the platform total).

    Search is trimmed; empty after trim is treated as no filter.
    """
    if search is not None:
        trimmed = search.strip()
        search = trimmed if trimmed else None

    try:
        rows, total = await _repo.list(
            session,
            tenant_id=tenant_id,
            status=status_,
            country=country,
            search=search,
            sort=sort,
            offset=offset,
            limit=limit,
        )
    except InvalidSortKeyError as exc:
        raise InvalidSortKeyClientError(str(exc), sort=sort) from exc

    items = [_list_item_from_row(r) for r in rows]
    return StoreListResponse(
        items=items,
        pagination=Pagination(total=total, offset=offset, limit=limit),
    )


@router.get("/{store_id}", response_model=StoreDetail)
async def get_store(
    store_id: UUID,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.STORES,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
        anchor_dep=get_store_anchor,
    )),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Return the full detail shape for a single store.

    404 if the row doesn't exist OR is RLS-filtered. Per D-17 the
    handler can't and shouldn't distinguish — both produce the same
    body shape (``code: STORE_NOT_FOUND``). The anchor dep
    ``get_store_anchor`` also raises ``StoreNotFoundError`` on the
    same conditions, so cross-tenant probes hit 404 ahead of the gate
    body fire path.
    """
    row = await _repo.get_by_id(session, store_id)
    if row is None:
        raise StoreNotFoundError(
            f"Store {store_id} not visible to this session",
            store_id=str(store_id),
        )
    return _detail_from_row(row)


@router.post(
    "",
    response_model=StoreDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_store(
    body: StoreCreateRequest,
    request: Request,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.STORES,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        # No audience kwarg — multi-audience per LD1.
        # No anchor_dep — tenant_id lives in the body, not the path.
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Create a new store. Multi-audience.

    PLATFORM tier (Super Admin, Platform Admin) creates for any tenant
    via the GLOBAL->TENANT cascade. TENANT tier (Owner) creates for own
    tenant via direct ``.TENANT`` grant; ``tenant_id`` in body verified
    against the caller's RLS-bound session — a cross-tenant id is
    invisible to TENANT callers and surfaces as 404
    ``ORG_NODE_NOT_FOR_STORE`` (when ``org_node_id`` provided) or as a
    DB FK violation otherwise.

    Server omits ``status`` so the DDL default fires (LD8). The DDL
    default in v0 is ``ACTIVE`` (see ``stores_v5.sql``); the prompt's
    intent ("via DDL default") is honoured by omitting the column
    from the INSERT — if a future migration changes the default to
    ``OPENING`` (the product-intended initial state implied by the
    lifecycle enum ordering), no app code change is required.
    Audit-actor pairs populate from ``auth.user_id`` and the
    audience-bridged ``ActorUserType``.

    ``store_code`` uniqueness checked per-tenant (case-insensitive,
    aligns with DDL partial unique index) before INSERT — pre-check
    surfaces as 409 ``DUPLICATE_STORE_CODE``. Future NOT NULL migration
    on ``store_code`` / ``tax_treatment`` will tighten DDL alongside
    these app-layer schema constraints.

    ``parent_org_node_id`` is REQUIRED (Step 6.21.2). It must reference
    a non-STORE org_node in the same tenant. The server creates the
    paired STORE-type org_node under it inside the same transaction as
    the ``stores`` row. 404 ``PARENT_NODE_NOT_FOUND`` for missing or
    RLS-filtered parents; 422 ``INVALID_PARENT_NODE_TYPE`` for
    STORE-type parents; 409 ``DUPLICATE_ORG_NODE_CODE`` when the
    store_code collides with any other org_node code in the tenant.
    """
    row = await _repo.create(
        session,
        tenant_id=body.tenant_id,
        name=body.name,
        country=body.country,
        timezone=body.timezone,
        currency=body.currency,
        store_code=body.store_code,
        tax_treatment=body.tax_treatment,
        parent_org_node_id=body.parent_org_node_id,
        address=body.address,
        latitude=body.latitude,
        longitude=body.longitude,
        auth=auth,
        request_id=request.state.request_id,
    )
    if row is None:
        raise TenantNotFoundError(
            f"Tenant {body.tenant_id} not visible to this session",
            tenant_id=str(body.tenant_id),
        )
    return _detail_from_row(row)


@router.patch("/{store_id}", response_model=StoreDetail)
async def patch_store(
    store_id: UUID,
    body: StorePatchRequest,
    request: Request,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.STORES,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        anchor_dep=get_store_anchor,
        # No audience kwarg — multi-audience.
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Partial update of a store. Multi-audience.

    Mutable fields: ``name``, ``store_code``, ``country``, ``timezone``,
    ``currency``, ``tax_treatment``, ``address``, ``latitude``,
    ``longitude``, and ``parent_org_node_id`` (Step 6.21.2). Other
    fields rejected at the schema layer via ``extra="forbid"`` —
    including ``status`` (lifecycle is in Step 6.17.4's
    ``/change_status``), ``tenant_id``, and ``org_node_id`` (the
    store's slot id is immutable; reparent uses
    ``parent_org_node_id``).

    Step 6.21.2 cascade: ``name``, ``store_code``, and
    ``parent_org_node_id`` changes propagate to the paired STORE-type
    org_node atomically inside one transaction. Org_node-side
    UNIQUE collisions on ``store_code`` change surface as 409
    ``DUPLICATE_ORG_NODE_CODE`` (broader scope than the
    stores-only ``DUPLICATE_STORE_CODE`` pre-check). Parent
    validation produces 404 ``PARENT_NODE_NOT_FOUND`` or 422
    ``INVALID_PARENT_NODE_TYPE`` for invalid
    ``parent_org_node_id`` values.

    Behaviour:
      - 422 ``EMPTY_PATCH`` when no fields are set.
      - 409 ``DUPLICATE_STORE_CODE`` on rename to a value held by
        another store in the same tenant (case-insensitive). Rename to
        the same value (after case-fold) is a 200 no-op.
      - 404 ``STORE_NOT_FOUND`` when the row is missing or RLS-filtered
        (RLS-as-404 per D-17). Anchor dep ``get_store_anchor`` fires
        the same code via the gate path for cross-tenant probes.
      - ``updated_at`` bumps on every non-empty PATCH (LD4); a
        non-empty same-as-current PATCH still bumps.
    """
    sent_fields = body.model_dump(exclude_unset=True)
    if not sent_fields:
        raise EmptyPatchError(
            f"PATCH on store {store_id} had no set fields",
            store_id=str(store_id),
        )

    updated = await _repo.update(
        session,
        store_id,
        fields=sent_fields,
        auth=auth,
        request_id=request.state.request_id,
    )
    if updated is None:
        raise StoreNotFoundError(
            f"Store {store_id} not visible to this session",
            store_id=str(store_id),
        )
    return _detail_from_row(updated)


@router.post("/{store_id}/set-status", response_model=StoreDetail)
async def set_store_status(
    store_id: UUID,
    body: StoreSetStatusRequest,
    request: Request,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.STORES,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        anchor_dep=get_store_anchor,
        # No audience kwarg — multi-audience per LD9 (same gate as PATCH).
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """State-transition endpoint for a store. Multi-audience.

    Gate: same as PATCH ``/stores/{store_id}`` —
    ``ADMIN.STORES.CONFIGURE.TENANT`` with ``anchor_dep=get_store_anchor``.
    SUPER_ADMIN + PLATFORM_ADMIN pass via the GLOBAL->TENANT cascade;
    TENANT OWNER passes via the direct ``.TENANT`` grant.

    9-cell liberal transition matrix per LD1: all transitions allowed
    EXCEPT ``*->OPENING``. CLOSED is reversible. Same-state returns
    409 ``INVALID_STATE_TRANSITION`` (LD5; mirrors tenants
    ``allowed_sources`` convention).

    Behaviour:
      - 200 + full ``StoreDetail`` on successful transition. Class 1
        (into-CLOSED) populates ``closed_*``; Class 2 (out-of-CLOSED)
        nulls it; Class 3 (between non-CLOSED) leaves it untouched.
      - 404 ``STORE_NOT_FOUND`` when the store id is missing or
        RLS-filtered. Anchor dep ``get_store_anchor`` fires first on
        cross-tenant probes by TENANT callers.
      - 409 ``INVALID_STATE_TRANSITION`` on rejected transitions
        (including same-state). Context kwargs ``store_id`` +
        ``target_status`` reach ``exc.context`` for logs; response
        envelope ``details`` stays ``null`` per the Q7 lock.

    ``reason`` is forward-compatible (LD3). It is consumed by Pydantic
    validation but NOT passed to the repo in this step. When Step 6.2
    audit_log ships, the handler gains an
    ``audit_log_repo.write(...reason=body.reason)`` call after the
    repo call succeeds; no API change required.
    """
    row, result = await _repo.transition(
        session,
        store_id,
        target_status=body.target_status,
        auth=auth,
        request_id=request.state.request_id,
    )
    if result is TransitionResult.NOT_FOUND:
        raise StoreNotFoundError(
            f"Store {store_id} not visible to this session",
            store_id=str(store_id),
        )
    if result is TransitionResult.INVALID_STATE:
        raise InvalidStateTransitionError(
            (
                f"store {store_id} cannot transition to "
                f"{body.target_status.value} from its current status"
            ),
            store_id=str(store_id),
            target_status=body.target_status.value,
        )
    assert row is not None  # TransitionResult.OK guarantees a row
    return _detail_from_row(row)
