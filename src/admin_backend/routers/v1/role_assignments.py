"""Role assignments router (Step 6.8.3 — Half 2 / E4).

One GET endpoint at ``/api/v1/role-assignments`` returning a grouped
envelope ``{platform_assignments, tenant_assignments}`` with each
block carrying its own ``{items, pagination}``.

Auth posture (multi-user-type, but with a security-load-bearing
twist — see CLAUDE.md "v0 auth model" note for the standard
patterns).

  - PLATFORM JWTs see BOTH blocks populated. The platform-side block
    is a direct read against ``platform_user_role_assignments`` (no
    RLS — platform-global). The tenant-side block is RLS-scoped via
    D-29's unconditional OR-branch on
    ``tenant_user_role_assignments_tenant_isolation``; PLATFORM
    sessions see all tenants' rows.

  - TENANT JWTs see ONLY their own tenant's tenant_assignments. The
    platform-side query is **short-circuited at the router** — NOT
    issued — and the platform_assignments block returns
    ``{items: [], pagination.total: 0}``.

    **Security-load-bearing (locked decision 12 of Step 6.8.3):**
    ``platform_user_role_assignments`` has NO RLS. The audience check
    happens at the application layer here. If the platform-side
    query were issued under a TENANT session, every row on the
    table would be returned (no RLS to filter, no tenant_id column
    to compare against). The R2 integration test asserts the
    short-circuit behaviour empirically.

Per D-30: the response is two ``{items, pagination}`` blocks under a
named-pair envelope. Per D-31: response field semantics frozen
append-only.

Sort key validation: an unknown ``sort`` raises ``InvalidSortKeyError``
in the Repo (a ValueError subclass shared via ``repositories._errors``).
The handler catches it and re-raises as ``InvalidSortKeyClientError``
from ``admin_backend.errors`` so the response surfaces as 400
``INVALID_SORT_KEY`` instead of 500. Mirrors the pattern from Step
6.4 / 5.2 / 5.1 / 3.3.
"""
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.context import AuthContext
from admin_backend.auth.permissions import require
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.errors import InvalidSortKeyClientError
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.models import (
    PlatformUserRoleAssignment,
    TenantUserRoleAssignment,
    UserRoleAssignmentStatus,
)
from admin_backend.repositories._errors import InvalidSortKeyError
from admin_backend.repositories.role_assignments import RoleAssignmentsRepo
from admin_backend.schemas.role_assignment import (
    PlatformAssignmentItem,
    PlatformAssignmentsBlock,
    RoleAssignmentsResponse,
    TenantAssignmentItem,
    TenantAssignmentsBlock,
    _AssignedOrgNode,
    _AssignedPlatformUser,
    _AssignedRole,
    _AssignedTenant,
    _AssignedTenantUser,
)
from admin_backend.schemas.tenant import Pagination


router = APIRouter(prefix="/role-assignments", tags=["role-assignments"])

# Stateless instance reused across requests (mirrors other Repos).
_repo = RoleAssignmentsRepo()


# ---- Mappers ---------------------------------------------------------------
#
# The pre-emptive nested shapes from Step 6.8.2 (in
# schemas/role_assignment.py) include inline mini-objects for role,
# tenant_user, tenant, org_node, platform_user. Their data lives on
# the related ORM rows rather than on the assignment row itself; we
# fetch via additional SELECTs against the same RLS-bound session.
#
# At v0 fleet scale (3 platform + 19 tenant assignments after seed)
# the per-row JOIN + index lookup overhead is negligible. Two passes
# (collect ids; batch fetch related) is the cleaner pattern; one
# could later inline this into JOINed Repo queries if the dataset
# grows.


async def _hydrate_platform_items(
    session: AsyncSession,
    raw_items: list[PlatformUserRoleAssignment],
) -> list[PlatformAssignmentItem]:
    """Hydrate raw platform-side assignment rows into PlatformAssignmentItem.

    Fetches related ``platform_users`` and ``roles`` rows via batched
    queries on the same RLS-bound session (no new connection). For the
    platform-side path there's no RLS to inherit; the additional
    queries simply read from no-RLS tables.
    """
    if not raw_items:
        return []

    from admin_backend.models.platform_user import PlatformUser
    from admin_backend.models.role import Role
    from sqlalchemy import select

    user_ids = {a.platform_user_id for a in raw_items}
    role_ids = {a.role_id for a in raw_items}

    user_rows = (
        await session.execute(
            select(PlatformUser).where(PlatformUser.id.in_(user_ids))
        )
    ).scalars().all()
    role_rows = (
        await session.execute(
            select(Role).where(Role.id.in_(role_ids))
        )
    ).scalars().all()

    users_by_id = {u.id: u for u in user_rows}
    roles_by_id = {r.id: r for r in role_rows}

    out: list[PlatformAssignmentItem] = []
    for a in raw_items:
        u = users_by_id[a.platform_user_id]
        r = roles_by_id[a.role_id]
        out.append(
            PlatformAssignmentItem(
                id=a.id,
                platform_user=_AssignedPlatformUser(
                    id=u.id, email=u.email, full_name=u.full_name
                ),
                role=_AssignedRole(
                    id=r.id, code=r.code, name=r.name, audience=r.audience.value
                ),
                status=a.status.value,
                granted_at=a.granted_at,
                revoked_at=a.revoked_at,
                updated_at=a.updated_at,
            )
        )
    return out


async def _hydrate_tenant_items(
    session: AsyncSession,
    raw_items: list[TenantUserRoleAssignment],
) -> list[TenantAssignmentItem]:
    """Hydrate raw tenant-side assignment rows into TenantAssignmentItem.

    Related fetches inherit RLS from the session GUCs. For TENANT
    JWTs, RLS already restricted the raw_items to the caller's tenant;
    the related fetches against tenant_users, tenants, and org_nodes
    similarly RLS-scope, so cross-tenant relations cannot bleed in.
    For PLATFORM JWTs, all rows are visible (D-29 OR-branch) — so the
    related fetches return everything needed.
    """
    if not raw_items:
        return []

    from admin_backend.models.org_node import OrgNode
    from admin_backend.models.role import Role
    from admin_backend.models.tenant import Tenant
    from admin_backend.models.tenant_user import TenantUser
    from sqlalchemy import select

    tenant_user_ids = {a.tenant_user_id for a in raw_items}
    tenant_ids = {a.tenant_id for a in raw_items}
    org_node_ids = {a.org_node_id for a in raw_items}
    role_ids = {a.role_id for a in raw_items}

    tu_rows = (
        await session.execute(
            select(TenantUser).where(TenantUser.id.in_(tenant_user_ids))
        )
    ).scalars().all()
    t_rows = (
        await session.execute(
            select(Tenant).where(Tenant.id.in_(tenant_ids))
        )
    ).scalars().all()
    on_rows = (
        await session.execute(
            select(OrgNode).where(OrgNode.id.in_(org_node_ids))
        )
    ).scalars().all()
    role_rows = (
        await session.execute(
            select(Role).where(Role.id.in_(role_ids))
        )
    ).scalars().all()

    tu_by_id = {x.id: x for x in tu_rows}
    t_by_id = {x.id: x for x in t_rows}
    on_by_id = {x.id: x for x in on_rows}
    role_by_id = {x.id: x for x in role_rows}

    out: list[TenantAssignmentItem] = []
    for a in raw_items:
        tu = tu_by_id[a.tenant_user_id]
        t = t_by_id[a.tenant_id]
        on = on_by_id[a.org_node_id]
        r = role_by_id[a.role_id]
        out.append(
            TenantAssignmentItem(
                id=a.id,
                tenant_user=_AssignedTenantUser(
                    id=tu.id, email=tu.email, full_name=tu.full_name
                ),
                tenant=_AssignedTenant(id=t.id, name=t.name),
                org_node=_AssignedOrgNode(
                    id=on.id,
                    name=on.name,
                    code=on.code,
                    node_type=on.node_type.value,
                ),
                role=_AssignedRole(
                    id=r.id, code=r.code, name=r.name, audience=r.audience.value
                ),
                status=a.status.value,
                granted_at=a.granted_at,
                revoked_at=a.revoked_at,
                updated_at=a.updated_at,
            )
        )
    return out


# ---- Endpoint --------------------------------------------------------------


@router.get(
    "",
    response_model=RoleAssignmentsResponse,
    summary="List role assignments (grouped by audience)",
    description=(
        "Grouped envelope: `{platform_assignments: {items, pagination}, "
        "tenant_assignments: {items, pagination}}`. PLATFORM JWTs see "
        "both blocks; TENANT JWTs see only their own tenant's "
        "tenant_assignments — the platform-side query is "
        "short-circuited at the router (security-load-bearing: "
        "platform_user_role_assignments has no RLS). Filters apply "
        "to whichever block(s) the filter targets; pagination is "
        "per-block."
    ),
)
async def list_role_assignments(
    role_id: UUID | None = Query(
        None,
        description="Filter both blocks by role id.",
    ),
    platform_user_id: UUID | None = Query(
        None,
        description="Filter platform_assignments by platform user id.",
    ),
    tenant_user_id: UUID | None = Query(
        None,
        description="Filter tenant_assignments by tenant user id.",
    ),
    tenant_id: UUID | None = Query(
        None,
        description=(
            "Filter tenant_assignments to a single tenant. Useful for "
            "PLATFORM callers scoping to one tenant; for TENANT callers "
            "the filter is redundant (RLS already scopes)."
        ),
    ),
    org_node_id: UUID | None = Query(
        None,
        description="Filter tenant_assignments by org_node anchor id.",
    ),
    status_filter: UserRoleAssignmentStatus | None = Query(
        None,
        alias="status",
        description="Filter both blocks by status: ACTIVE or INACTIVE.",
    ),
    sort: str = Query(
        "granted_at_desc",
        description="Sort key for both blocks: granted_at_asc, granted_at_desc.",
    ),
    offset: int = Query(0, ge=0, description="Pagination offset (per block)."),
    limit: int = Query(
        50, ge=1, le=200, description="Pagination limit per block (max 200)."
    ),
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.USERS,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    # Filter-shape narrowing: per-block queries are short-circuited
    # when a filter targets a column that block's table doesn't have.
    #
    #   - ``platform_user_id`` set: skip tenant-side (a platform
    #     user has no tenant-side assignments by definition).
    #   - ``tenant_user_id`` set: skip platform-side (symmetric).
    #   - ``org_node_id`` set: skip platform-side (no org_node
    #     column on platform-side).
    #   - ``tenant_id`` set: narrow tenant-side; leave platform
    #     unaffected (platform-side has no tenant_id; the filter is
    #     not applicable there, but the platform block can still
    #     legitimately be populated).
    #
    # Plus the security-load-bearing TENANT-JWT short-circuit on
    # platform-side per locked decision 12.
    skip_tenant_block = platform_user_id is not None
    skip_platform_block = (
        tenant_user_id is not None
        or org_node_id is not None
        or auth.user_type == "TENANT"
    )

    try:
        if skip_platform_block:
            raw_platform: list[PlatformUserRoleAssignment] = []
            platform_total = 0
        else:
            raw_platform, platform_total = await _repo.list_platform_assignments(
                session,
                role_id=role_id,
                platform_user_id=platform_user_id,
                status=status_filter,
                sort=sort,
                offset=offset,
                limit=limit,
            )

        if skip_tenant_block:
            raw_tenant: list[TenantUserRoleAssignment] = []
            tenant_total = 0
        else:
            raw_tenant, tenant_total = await _repo.list_tenant_assignments(
                session,
                role_id=role_id,
                tenant_user_id=tenant_user_id,
                tenant_id=tenant_id,
                org_node_id=org_node_id,
                status=status_filter,
                sort=sort,
                offset=offset,
                limit=limit,
            )
    except InvalidSortKeyError as exc:
        raise InvalidSortKeyClientError(
            str(exc), sort=sort
        ) from exc

    platform_items = await _hydrate_platform_items(session, raw_platform)
    tenant_items = await _hydrate_tenant_items(session, raw_tenant)

    return RoleAssignmentsResponse(
        platform_assignments=PlatformAssignmentsBlock(
            items=platform_items,
            pagination=Pagination(
                total=platform_total, offset=offset, limit=limit
            ),
        ),
        tenant_assignments=TenantAssignmentsBlock(
            items=tenant_items,
            pagination=Pagination(
                total=tenant_total, offset=offset, limit=limit
            ),
        ),
    )
