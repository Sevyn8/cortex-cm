"""Module Access read endpoints (Step 6.7).

Two GET endpoints under ``/module-access``:

  - ``GET /api/v1/module-access/modules`` E1 — 6 module cards with
    per-module aggregates.
  - ``GET /api/v1/module-access/matrix``  E2 — paginated tenant ×
    module grid.

Multi-user-type per the v0 auth model: both PLATFORM and TENANT JWTs
accepted; visibility scoping is RLS's job (D-29 OR-clause on
``tenants`` and ``tenant_module_access``).

  - PLATFORM JWT: fleet-wide aggregates / matrix rows.
  - TENANT JWT:   own-tenant aggregates / 1-row matrix.

Same response shape for both user types — RLS does the persona
projection at the data layer; the application layer adds no
``user_type``-based string formatting (unlike ``/dashboard/*``).

Label-handling convention (locked at this step). Every enum-coded
field carries a sibling ``<field>_label`` resolved server-side via
LEFT JOIN against ``lookups`` with COALESCE fallback. Applied here for
``module_code`` (E1 + E2), ``tier``, and ``status`` (E2). Codified in
CLAUDE.md as the rule for new endpoints from Step 6.7 forward.
"""
from __future__ import annotations

from typing import Any, Literal

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.anchor_deps import get_tenant_anchor
from admin_backend.auth.context import AuthContext
from admin_backend.auth.permissions import require
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.errors import (
    InvalidSortKeyClientError,
    ModuleAccessNotFoundError,
)
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.repositories._errors import InvalidSortKeyError
from admin_backend.repositories.modules_access import (
    DEFAULT_MATRIX_SORT,
    MatrixCellRow,
    MatrixTenantRow,
    ModuleCardRow,
    ModulesAccessRepo,
    TransitionResult,
)
from admin_backend.schemas.modules_access import (
    MatrixCell,
    MatrixResponse,
    MatrixRow,
    ModuleAccessRead,
    ModuleCard,
    ModulesResponse,
)
from admin_backend.schemas.tenant import Pagination


router = APIRouter(prefix="/module-access", tags=["module-access"])

# Stateless instance reused across requests (mirrors TenantsRepo etc.).
_repo = ModulesAccessRepo()


# Locked tier / status filter vocabularies — narrower than the underlying
# enums in two ways: tier is fully reflected; status excludes TERMINATED
# (the row-set filter excludes it structurally, so allowing it as a
# filter would always match zero rows — confusing rather than useful).
TierFilterLiteral = Literal["ENTERPRISE", "MID_MARKET", "SMB", "SINGLE_STORE"]
StatusFilterLiteral = Literal["ONBOARDING", "TRIAL", "ACTIVE", "SUSPENDED"]


# =============================================================================
# E1: GET /module-access/modules
# =============================================================================


@router.get(
    "/modules",
    response_model=ModulesResponse,
    summary="Module catalogue with per-module aggregates",
    description=(
        "Returns all 6 modules with per-module aggregate counts: "
        "``enabled_count`` (visible tenants with this module ENABLED) "
        "over ``total_active_trial_tenants`` (visible tenants with "
        "status IN ACTIVE / TRIAL). Both fields RLS-scoped — under "
        "TENANT JWT, ``enabled_count`` collapses to 0 or 1 and "
        "``total_active_trial_tenants`` collapses to 0 or 1 (own "
        "tenant only). Items ordered by ``lookups.display_order`` "
        "(decoupled from enum ordinal per Step 6.6's sort-stability "
        "decision). Server-side label resolution: each item carries "
        "``module_label`` resolved via JOIN against "
        "``lookups.list_name='module_code'`` with COALESCE fallback "
        "to the raw enum code."
    ),
)
async def list_modules(
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
    )),
    auth: AuthContext = Depends(get_auth_context),  # noqa: ARG001
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    rows: list[ModuleCardRow] = await _repo.list_modules_with_aggregates(
        session
    )
    return ModulesResponse(
        items=[
            ModuleCard.model_validate(
                {
                    "module_code": r.module_code,
                    "module_label": r.module_label,
                    "enabled_count": r.enabled_count,
                    "total_active_trial_tenants": (
                        r.total_active_trial_tenants
                    ),
                }
            )
            for r in rows
        ]
    )


# =============================================================================
# E2: GET /module-access/matrix
# =============================================================================


@router.get(
    "/matrix",
    response_model=MatrixResponse,
    summary="Tenant × module enablement grid",
    description=(
        "Returns the tenant × module grid for the Module Access "
        "governance console. Row set: all visible non-TERMINATED "
        "tenants (ACTIVE + TRIAL + SUSPENDED + ONBOARDING). Each row "
        "carries 6 ``cells`` — one per module — position-aligned "
        "across rows AND with ``/modules.items[]``: "
        "``cells[i].module_code`` is the same for every row in this "
        "response. Cells render as ``ENABLED`` only when an ENABLED "
        "``tenant_module_access`` row exists; absent rows AND rows "
        "with ``status='DISABLED'`` both render as ``DISABLED`` (the "
        "frontend doesn't distinguish). Multi-user-type: PLATFORM "
        "sees fleet rows; TENANT sees exactly own-tenant. Sibling "
        "``tier_label`` and ``status_label`` resolved server-side "
        "via ``lookups``."
    ),
)
async def list_matrix(
    sort: str = Query(
        DEFAULT_MATRIX_SORT,
        description=(
            "Sort key. One of: ``name_asc``, ``name_desc``, "
            "``created_at_asc``, ``created_at_desc``, ``tier_asc``, "
            "``tier_desc``. Default ``tier_asc``. Stable secondary "
            "sort by name then id. Unknown -> 400 ``INVALID_SORT_KEY``."
        ),
    ),
    tier: TierFilterLiteral | None = Query(
        None,
        description=(
            "Exact-match filter on tenant tier. One of: ENTERPRISE, "
            "MID_MARKET, SMB, SINGLE_STORE."
        ),
    ),
    status_filter: StatusFilterLiteral | None = Query(
        None,
        alias="status",
        description=(
            "Exact-match filter on tenant status. One of: ONBOARDING, "
            "TRIAL, ACTIVE, SUSPENDED. ``TERMINATED`` is implicitly "
            "excluded from the matrix row set, so it isn't a valid "
            "filter value."
        ),
    ),
    q: str | None = Query(
        None,
        description=(
            "Case-insensitive ILIKE substring match against "
            "``tenants.name``. Empty / whitespace ignored."
        ),
    ),
    limit: int = Query(
        25,
        ge=1,
        le=200,
        description="Pagination limit. 1-200; default 25.",
    ),
    offset: int = Query(0, ge=0, description="Pagination offset."),
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
    )),
    auth: AuthContext = Depends(get_auth_context),  # noqa: ARG001
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    if q is not None:
        trimmed = q.strip()
        q = trimmed if trimmed else None

    try:
        tenant_rows, cells_by_tenant, total = await _repo.list_matrix(
            session,
            sort=sort,
            tier=tier,
            status=status_filter,
            q=q,
            limit=limit,
            offset=offset,
        )
    except InvalidSortKeyError as exc:
        raise InvalidSortKeyClientError(
            str(exc), sort=sort
        ) from exc

    items: list[MatrixRow] = []
    for trow in tenant_rows:
        cells = _cells_for(trow, cells_by_tenant)
        items.append(
            MatrixRow.model_validate(
                {
                    "tenant_id": trow.tenant_id,
                    "name": trow.name,
                    "tier": trow.tier,
                    "tier_label": trow.tier_label,
                    "status": trow.status,
                    "status_label": trow.status_label,
                    "cells": cells,
                }
            )
        )

    return MatrixResponse(
        items=items,
        pagination=Pagination(total=total, offset=offset, limit=limit),
    )


def _cells_for(
    trow: MatrixTenantRow,
    cells_by_tenant: dict[Any, list[MatrixCellRow]],
) -> list[MatrixCell]:
    """Map the Repo's per-tenant cell rows to MatrixCell schema models.

    The Repo guarantees one entry per tenant_id key (initialised even
    for tenants with zero ``tenant_module_access`` rows — the CROSS
    JOIN against the modules list always synthesises 6 cells per
    tenant). A defensive ``.get(..., [])`` would be dead code; we use
    direct subscripting and let a KeyError surface (it would indicate
    a Repo contract bug, not a data issue).
    """
    rows = cells_by_tenant[trow.tenant_id]
    return [
        MatrixCell.model_validate(
            {"module_code": r.module_code, "status": r.status}
        )
        for r in rows
    ]


# =============================================================================
# Step 6.15: write surface (PLATFORM-only transitions)
# =============================================================================
#
# Two named transition endpoints under the reads' URL prefix:
#
#   POST /api/v1/module-access/{tenant_id}/{module_code}/enable
#   POST /api/v1/module-access/{tenant_id}/{module_code}/disable
#
# Same gate tuple as tenant suspend/activate
# (ADMIN.TENANTS.OVERRIDE.GLOBAL, SUPER_ADMIN-only post Phase 3 seed)
# with audience="PLATFORM" + anchor_dep=get_tenant_anchor (RLS-as-404 on
# cross-tenant probe via the anchor lookup).
#
# Idempotent-200 on no-op cells per LD4: enable on ENABLED is 200 with
# no row mutation; disable on DISABLED is 200 with no row mutation;
# disable on missing is 404 MODULE_ACCESS_NOT_FOUND. No 409
# INVALID_STATE_TRANSITION on this pair — module access flips are
# legitimate at any point and the operational profile differs from
# tenant lifecycle (see the FN-AB on the cross-resource transition-
# matrix asymmetry).


@router.post(
    "/{tenant_id}/{module_code}/enable",
    response_model=ModuleAccessRead,
    summary="Enable a module for a tenant (upserts if no row exists)",
    description=(
        "Enables ``module_code`` for ``tenant_id``. Upserts: creates a "
        "row when none exists; flips ``DISABLED`` to ``ENABLED`` "
        "otherwise. Idempotent: enable on already-``ENABLED`` returns "
        "200 with no row mutation. Per LD5, ``enabled_at`` and "
        "``enabled_by_user_id`` mark the start of the current "
        "ENABLED stint and are overwritten on every "
        "``DISABLED -> ENABLED`` flip; the ``disabled_*`` pair clears "
        "atomically. Gated on ``ADMIN.TENANTS.OVERRIDE.GLOBAL`` "
        "(SUPER_ADMIN-only) with ``audience='PLATFORM'`` — TENANT JWTs "
        "are refused at Layer 1 with 403 ``PLATFORM_AUDIENCE_REQUIRED``."
    ),
)
async def enable_module_for_tenant(
    tenant_id: UUID,
    module_code: ModuleCode,
    request: Request,
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
    row = await _repo.enable(
        session,
        tenant_id,
        module_code,
        actor_user_id=auth.user_id,
        auth=auth,
        request_id=request.state.request_id,
    )
    return ModuleAccessRead.model_validate(row)


@router.post(
    "/{tenant_id}/{module_code}/disable",
    response_model=ModuleAccessRead,
    summary="Disable a module for a tenant",
    description=(
        "Disables ``module_code`` for ``tenant_id``. 404 "
        "``MODULE_ACCESS_NOT_FOUND`` when no row exists for the "
        "supplied pair (only the disable path produces this code; "
        "enable upserts). Idempotent: disable on already-``DISABLED`` "
        "returns 200 with no row mutation. Per LD5, ``enabled_at`` is "
        "preserved through the disable flip (carries forward as the "
        "historical record of when the just-ended ENABLED stint "
        "began); only ``disabled_at`` and ``disabled_by_user_id`` are "
        "written. Access cascade is structural via the "
        "``has_permission`` JOIN on ``tma.status='ENABLED'`` — disabling "
        "blocks every TENANT-side permission check against the module "
        "on the next request without touching the role-assignment "
        "table. Gated on ``ADMIN.TENANTS.OVERRIDE.GLOBAL`` "
        "(SUPER_ADMIN-only) with ``audience='PLATFORM'``."
    ),
)
async def disable_module_for_tenant(
    tenant_id: UUID,
    module_code: ModuleCode,
    request: Request,
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
    row, result = await _repo.disable(
        session,
        tenant_id,
        module_code,
        actor_user_id=auth.user_id,
        auth=auth,
        request_id=request.state.request_id,
    )
    if result is TransitionResult.NOT_FOUND:
        raise ModuleAccessNotFoundError(
            (
                f"no tenant_module_access row for tenant_id={tenant_id} "
                f"module={module_code.value}"
            ),
            tenant_id=str(tenant_id),
            module_code=module_code.value,
        )
    # ``row`` is non-None on the OK branch (returned by either the
    # ENABLED -> DISABLED UPDATE path or the DISABLED no-op refetch).
    assert row is not None
    return ModuleAccessRead.model_validate(row)
