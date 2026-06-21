"""Dashboard stats endpoints (Step 6.5).

Two GET endpoints under ``/dashboard``:

  - ``GET /api/v1/dashboard/fleet-stats``      — KPI cards 1-4
  - ``GET /api/v1/dashboard/governance-stats`` — KPI cards 5-8

Both endpoints return **card-shaped** response objects rather than
the standard list envelope (deliberate D-30 exception — the
dashboard is a UI-shaped query bundle, not a paginatable collection).
The ``description`` strings on each route call out the exception so
the OpenAPI spec is self-documenting.

Auth posture (multi-user-type — see CLAUDE.md "v0 auth model" note).
Both PLATFORM and TENANT JWTs accepted; visibility scoping is the
DB layer's job via RLS:

  - PLATFORM JWT: fleet-wide aggregates via D-29 OR-clause on each
    underlying table (tenants, tenant_users, stores,
    tenant_module_access).
  - TENANT JWT: own-tenant aggregates via the equality clause.

Per-card stub posture in v0:

  - Fleet-stats: all 4 cards real; ``mrr_aggregated.delta`` is the
    only stub field (no MRR snapshot table). Tracked as
    MRR-DELTA-REAL forward note.
  - Governance-stats: 3 of 4 cards stubbed (``available: false`` with
    locked ``unavailable_reason``); ``modules_deployed`` is real.

Sub_text strings are backend-formatted, scope-aware via
``auth.user_type``. Helpers below are pure functions; if they grow
beyond ~5 they should move to a sibling ``_helpers.py`` module.

Step 6.5 amendment (2026-05-06): the active-tenants ``sub_text``
vocabulary covers all five tenant_status values. ONBOARDING is
broken out as a distinct lifecycle segment alongside TRIAL and
SUSPENDED. Original prompt's locked rules covered only TRIAL +
SUSPENDED; ONBOARDING was added at design-review time because
silent rollup under-served the Super Admin reader.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.context import AuthContext
from admin_backend.auth.permissions import require
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.repositories.dashboard import (
    DashboardRepo,
    FleetStatsRow,
    GovernanceStatsRow,
)
from admin_backend.schemas.dashboard import (
    ActiveTenantsCard,
    CustomRolesCard,
    DeltaBlock,
    FleetStatsResponse,
    GovernanceStatsResponse,
    GuardrailsFired24hCard,
    ModulesDeployedCard,
    MrrAggregatedCard,
    PendingApprovalsCard,
    PlatformUsersCard,
    StoresCard,
)


router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Stateless instance reused across requests (mirrors TenantsRepo etc.).
_repo = DashboardRepo()


# =============================================================================
# Sub_text helpers — backend-formatted, scope-aware, pure functions
# =============================================================================


def _direction_for(value: int) -> str:
    """Derive direction from a delta value. Values: up / down / flat."""
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def _active_tenants_sub_text(
    onboarding: int, trial: int, suspended: int
) -> str:
    """Format the active-tenants breakdown segment.

    Includes each non-zero segment from {ONBOARDING, TRIAL, SUSPENDED}
    in lifecycle order. Empty string when all three are zero.

    Examples (from Step 6.5 design review, 2026-05-06):
        (1, 2, 1) -> "1 onboarding · 2 trial · 1 suspended"
        (0, 2, 1) -> "2 trial · 1 suspended"
        (1, 0, 0) -> "1 onboarding"
        (0, 0, 0) -> ""

    Same rule for both user types — under TENANT JWT the values are
    typically all zero (the caller's own tenant is rarely in trial /
    suspended / onboarding while it's making the request) so the
    string collapses to empty naturally.
    """
    segments: list[str] = []
    if onboarding > 0:
        segments.append(f"{onboarding} onboarding")
    if trial > 0:
        segments.append(f"{trial} trial")
    if suspended > 0:
        segments.append(f"{suspended} suspended")
    return " · ".join(segments)


def _platform_users_sub_text(user_type: str) -> str:
    return (
        "in your organization"
        if user_type == "TENANT"
        else "across all tenants"
    )


def _stores_sub_text(distinct_countries: int) -> str:
    """Singular/plural country count. Same rule for both user types."""
    if distinct_countries == 1:
        return "1 country"
    return f"{distinct_countries} countries"


def _pending_approvals_sub_text(user_type: str) -> str:
    return (
        "across your organization"
        if user_type == "TENANT"
        else "across guardrails"
    )


def _modules_deployed_sub_text(user_type: str, visible_tenants: int) -> str:
    """Scope-aware. PLATFORM: 'across N tenant(s)'. TENANT: org-anchored."""
    if user_type == "TENANT":
        return "enabled for your organization"
    if visible_tenants == 1:
        return "across 1 tenant"
    return f"across {visible_tenants} tenants"


# =============================================================================
# E1: GET /api/v1/dashboard/fleet-stats
# =============================================================================


def _build_fleet_stats_response(
    row: FleetStatsRow, user_type: str
) -> FleetStatsResponse:
    """Map the CTE row + auth context to the FleetStatsResponse shape."""
    new_7d = row.tenants_new_7d
    active_tenants = ActiveTenantsCard(
        value=row.tenants_active,
        total=row.tenants_total,
        sub_text=_active_tenants_sub_text(
            onboarding=row.tenants_onboarding,
            trial=row.tenants_trial,
            suspended=row.tenants_suspended,
        ),
        delta=DeltaBlock(
            value=new_7d,
            direction=_direction_for(new_7d),  # type: ignore[arg-type]
            window="7d",
            available=True,
        ),
        available=True,
    )

    new_30d = row.users_new_30d
    platform_users = PlatformUsersCard(
        value=row.users_active,
        sub_text=_platform_users_sub_text(user_type),
        delta=DeltaBlock(
            value=new_30d,
            direction=_direction_for(new_30d),  # type: ignore[arg-type]
            window="30d",
            available=True,
        ),
        available=True,
    )

    stores = StoresCard(
        value=row.stores_total,
        distinct_countries=row.stores_distinct_countries,
        sub_text=_stores_sub_text(row.stores_distinct_countries),
        # ``delta`` defaults to None on this card.
        available=True,
    )

    # Always-2dp string format for the dashboard contract. See
    # MrrAggregatedCard docstring for rationale (Q2 confirmed at the
    # Step 6.5 design review). Decimal('0E-2') etc. would otherwise
    # surface in str(...).
    mrr_decimal: Decimal = row.mrr_sum
    mrr_value_str = f"{mrr_decimal:.2f}"
    mrr_aggregated = MrrAggregatedCard(
        value=mrr_value_str,
        currency="USD",
        sub_text="recurring",
        delta=DeltaBlock(
            value=None,
            direction=None,
            window="monthly",
            available=False,
        ),
        available=True,
    )

    return FleetStatsResponse(
        active_tenants=active_tenants,
        platform_users=platform_users,
        stores=stores,
        mrr_aggregated=mrr_aggregated,
    )


@router.get(
    "/fleet-stats",
    response_model=FleetStatsResponse,
    summary="Dashboard fleet-scale KPIs (cards 1-4)",
    description=(
        "Returns the four fleet-scale KPI cards for the Platform "
        "Dashboard (Frontend spec 7.1): active tenants, platform "
        "users, stores under management, aggregated tenant MRR. "
        "**Card-shaped response (deliberate D-30 exception)** — not "
        "the standard `{items, pagination}` envelope; the dashboard "
        "is a UI-shaped query bundle, not a paginatable collection. "
        "Multi-user-type: PLATFORM JWTs see fleet-wide aggregates; "
        "TENANT JWTs see own-tenant aggregates (RLS handles persona "
        "projection). All four cards have `available: true` in v0; "
        "the only stub field is `mrr_aggregated.delta` (no MRR "
        "snapshot table — tracked as MRR-DELTA-REAL forward note)."
    ),
)
async def get_fleet_stats(
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    row = await _repo.fleet_stats(session)
    return _build_fleet_stats_response(row, auth.user_type)


# =============================================================================
# E2: GET /api/v1/dashboard/governance-stats
# =============================================================================


def _build_governance_stats_response(
    row: GovernanceStatsRow, user_type: str
) -> GovernanceStatsResponse:
    """Map the governance-stats row + auth context to the response shape."""
    pending_approvals = PendingApprovalsCard(
        value=0,
        sub_text=_pending_approvals_sub_text(user_type),
        # ``delta`` defaults to None.
        available=False,
        unavailable_reason="approvals_table_not_built",
    )

    guardrails_fired = GuardrailsFired24hCard(
        value=0,
        escalations=0,
        sub_text="0 escalations",
        available=False,
        unavailable_reason="audit_logs_or_guardrails_not_wired",
    )

    custom_roles = CustomRolesCard(
        value=0,
        total=0,
        sub_text="of 0 total",
        available=False,
        unavailable_reason="custom_role_creation_not_shipped",
    )

    modules_deployed = ModulesDeployedCard(
        value=row.modules_enabled,
        sub_text=_modules_deployed_sub_text(
            user_type=user_type,
            visible_tenants=row.modules_visible_tenant_count,
        ),
        available=True,
    )

    return GovernanceStatsResponse(
        pending_approvals=pending_approvals,
        guardrails_fired_24h=guardrails_fired,
        custom_roles=custom_roles,
        modules_deployed=modules_deployed,
    )


@router.get(
    "/governance-stats",
    response_model=GovernanceStatsResponse,
    summary="Dashboard governance KPIs (cards 5-8)",
    description=(
        "Returns the four governance-posture KPI cards for the "
        "Platform Dashboard: pending approvals, guardrails fired in "
        "24h, custom roles, modules deployed. **Card-shaped response "
        "(deliberate D-30 exception).** Multi-user-type with RLS-"
        "driven persona projection. **Three of four cards are "
        "stubbed in v0** (`available: false` with a fixed-vocabulary "
        "`unavailable_reason` — `approvals_table_not_built`, "
        "`audit_logs_or_guardrails_not_wired`, "
        "`custom_role_creation_not_shipped`). `modules_deployed` is "
        "real and RLS-scoped. Append-only contract per D-31: when a "
        "stub flips to real, only `available`, `value`, and "
        "`unavailable_reason` change; field set and types stay "
        "the same."
    ),
)
async def get_governance_stats(
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.TENANTS,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    row = await _repo.governance_stats(session)
    return _build_governance_stats_response(row, auth.user_type)
