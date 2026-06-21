"""RoleAssignmentsRepo — read-only data access for the post-split URA tables.

Two list methods, one per physical table. The 6.8.3
``/role-assignments`` router calls both (PLATFORM JWT) or only the
tenant-side one (TENANT JWT, per locked decision 12 of Step 6.8.3 —
security-load-bearing) and assembles the grouped response shape with
two ``{items, pagination}`` blocks.

Per D-24, this Repo does NOT accept ``tenant_id`` for visibility
purposes. For the tenant table, RLS scopes via session GUCs (D-29
unconditional OR-branch on ``tenant_user_role_assignments``). For the
platform table, no scoping — platform-global; the audience-check
trigger ensures only PLATFORM-audience role rows live there.

Step 6.8.3 — ``list_tenant_assignments`` extended with a new
optional ``tenant_id`` filter. This is *application-layer narrowing*
for PLATFORM callers who want to scope a listing to a single tenant;
it composes with RLS without conflict (RLS already restricts TENANT
JWTs to their own tenant; this filter further narrows for PLATFORM
JWTs). For TENANT JWTs the filter is functionally redundant (RLS
handles scoping) but accepted; a non-matching value just intersects
to empty.

Mirrors ``PlatformUsersRepo`` shape: stateless singleton, each method
takes ``session`` as the first positional argument, sort-key validation
raises ``InvalidSortKeyError``.
"""
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.models import (
    PlatformUserRoleAssignment,
    TenantUserRoleAssignment,
    UserRoleAssignmentStatus,
)
from admin_backend.repositories._errors import InvalidSortKeyError


# Sort vocabulary for /role-assignments (Step 6.8.3, locked decision 14).
# Public frozenset for validation; internal maps for SQL clauses.
# Stable secondary sort by ``id ASC`` is appended at query time so
# identical primary-sort values page deterministically. ``dict[str, Any]``
# rather than the inferred ``dict[str, object]`` per the same mypy nuance
# documented in ``platform_users.SORT_MAP``.
ROLE_ASSIGNMENTS_SORT_KEYS: frozenset[str] = frozenset({
    "granted_at_asc",
    "granted_at_desc",
})

PLATFORM_ASSIGNMENTS_SORT_MAP: dict[str, Any] = {
    "granted_at_desc": PlatformUserRoleAssignment.granted_at.desc(),
    "granted_at_asc": PlatformUserRoleAssignment.granted_at.asc(),
}

TENANT_ASSIGNMENTS_SORT_MAP: dict[str, Any] = {
    "granted_at_desc": TenantUserRoleAssignment.granted_at.desc(),
    "granted_at_asc": TenantUserRoleAssignment.granted_at.asc(),
}


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
        """List PLATFORM-side assignments.

        No RLS — visible to every session. Audience-check trigger
        ensures every row here references a PLATFORM-audience role.

        Returns ``(items, total)`` where ``total`` counts rows matching
        the same filters but ignoring ``offset`` / ``limit``.
        """
        if sort not in PLATFORM_ASSIGNMENTS_SORT_MAP:
            raise InvalidSortKeyError(f"unknown sort key: {sort}")

        conditions = []
        if role_id is not None:
            conditions.append(PlatformUserRoleAssignment.role_id == role_id)
        if platform_user_id is not None:
            conditions.append(
                PlatformUserRoleAssignment.platform_user_id == platform_user_id
            )
        if status is not None:
            conditions.append(PlatformUserRoleAssignment.status == status)

        count_stmt = select(func.count()).select_from(PlatformUserRoleAssignment)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        count_result = await session.execute(count_stmt)
        total: int = count_result.scalar_one()

        stmt = select(PlatformUserRoleAssignment).order_by(
            PLATFORM_ASSIGNMENTS_SORT_MAP[sort],
            PlatformUserRoleAssignment.id.asc(),
        )
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.offset(offset).limit(limit)

        items_result = await session.execute(stmt)
        items = list(items_result.scalars().all())
        return items, total

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
    ) -> tuple[list[TenantUserRoleAssignment], int]:
        """List TENANT-side assignments.

        RLS-scoped via session GUCs: PLATFORM JWTs see all rows
        (D-29 unconditional OR-branch); TENANT JWTs see only rows
        whose ``tenant_id`` matches ``app.tenant_id``.

        ``tenant_id`` filter (Step 6.8.3): application-layer narrowing
        for PLATFORM callers wanting a single-tenant view. Composes
        with RLS without conflict.

        Returns ``(items, total)`` where ``total`` counts rows matching
        the same filters but ignoring ``offset`` / ``limit``.
        """
        if sort not in TENANT_ASSIGNMENTS_SORT_MAP:
            raise InvalidSortKeyError(f"unknown sort key: {sort}")

        conditions = []
        if role_id is not None:
            conditions.append(TenantUserRoleAssignment.role_id == role_id)
        if tenant_user_id is not None:
            conditions.append(
                TenantUserRoleAssignment.tenant_user_id == tenant_user_id
            )
        if tenant_id is not None:
            conditions.append(TenantUserRoleAssignment.tenant_id == tenant_id)
        if org_node_id is not None:
            conditions.append(
                TenantUserRoleAssignment.org_node_id == org_node_id
            )
        if status is not None:
            conditions.append(TenantUserRoleAssignment.status == status)

        count_stmt = select(func.count()).select_from(TenantUserRoleAssignment)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        count_result = await session.execute(count_stmt)
        total: int = count_result.scalar_one()

        stmt = select(TenantUserRoleAssignment).order_by(
            TENANT_ASSIGNMENTS_SORT_MAP[sort],
            TenantUserRoleAssignment.id.asc(),
        )
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.offset(offset).limit(limit)

        items_result = await session.execute(stmt)
        items = list(items_result.scalars().all())
        return items, total
