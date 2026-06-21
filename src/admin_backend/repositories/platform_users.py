"""PlatformUsersRepo — read-only data access for ``platform_users``.

Owns SELECT queries on ``platform_users``. The Repo does NOT set
session GUCs, NOT begin transactions, NOT handle commits/rollbacks —
those are the session/middleware layer's job.

``platform_users`` has no RLS (per the DDL's "No Row-Level Security"
section). Access is gated at the router layer: only PLATFORM JWTs
reach handlers that call this Repo. The session passed in still
carries ``app.tenant_id`` / ``app.user_type`` GUCs (set by
``get_tenant_session`` for session-flow consistency); they simply
don't filter anything here.

Per D-17, missing rows surface as ``None`` from ``get_by_id``; the
router converts to 404.

Mirrors ``TenantsRepo``: stateless singleton (constructed once at
module import), each method takes ``session`` as the first positional
argument, no instance state.

Step 6.8.3 — A1/A2 augmentation. ``list(...)`` and ``get_by_id(...)``
now return row carriers (``PlatformUserListRow`` /
``PlatformUserDetailRow``) carrying the ORM row plus a per-row
``roles`` aggregate produced by a correlated jsonb_agg subquery
against ``platform_user_role_assignments``. Mirrors ``tenants.py``'s
``list_with_aggregates`` precedent. NO org_node join — platform-side
assignments have no org-node anchoring; the jsonb_build_object
literally sets ``org_node_id`` and ``org_node_name`` to NULL so the
wire shape stays uniform with tenant-side.
"""
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.models.platform_user import PlatformUser, PlatformUserStatus
from admin_backend.models.platform_user_role_assignment import (
    PlatformUserRoleAssignment,
)
from admin_backend.models.role import Role
from admin_backend.repositories._errors import InvalidSortKeyError


# Sort keys map to (column, direction) tuples. Uniform secondary sort
# by ``id ASC`` is appended in the query so identical primary-sort
# values (e.g., two users created in the same millisecond) page
# deterministically.
# Annotated `dict[str, Any]` rather than the inferred `dict[str, object]`:
# mypy strict otherwise rejects the values at the `select(...).order_by(...)`
# call site because ORM `UnaryExpression`s erase to `object` once stored
# in a heterogeneous mapping. `Any` here is locally scoped (only flows
# into the `select` call) and the keys are the public contract.
SORT_MAP: dict[str, Any] = {
    "created_at_asc": PlatformUser.created_at.asc(),
    "created_at_desc": PlatformUser.created_at.desc(),
    "full_name_asc": PlatformUser.full_name.asc(),
    "full_name_desc": PlatformUser.full_name.desc(),
    "email_asc": PlatformUser.email.asc(),
    "email_desc": PlatformUser.email.desc(),
}


@dataclass
class PlatformUserListRow:
    """Row carrier for ``list(...)``: ORM PlatformUser + roles aggregate.

    ``roles`` is the JSONB-decoded list[dict] from psycopg's automatic
    JSONB conversion; the router maps each dict to a
    ``UserRoleAssignmentItem`` Pydantic model. For platform users
    every dict has ``org_node_id=None`` and ``org_node_name=None``.
    """

    user: PlatformUser
    roles: list[dict[str, Any]]


@dataclass
class PlatformUserDetailRow:
    """Row carrier for ``get_by_id(...)``: same shape as
    PlatformUserListRow; kept distinct so list-vs-detail mappers stay
    typed independently.
    """

    user: PlatformUser
    roles: list[dict[str, Any]]


def _roles_subq() -> Any:
    """Per-platform-user roles as ``jsonb_agg`` of the 8-field item.

    Returns a scalar subquery correlated to the outer ``PlatformUser``
    row. Yields a JSONB array (decoded by psycopg as ``list[dict]``)
    where each element is the 8-field ``UserRoleAssignmentItem`` shape.

    No org_node join — ``platform_user_role_assignments`` has no
    tenant_id and no org_node_id (per D-34 / Step 6.8.1's split).
    The jsonb_build_object literally sets ``org_node_id`` and
    ``org_node_name`` to NULL so the wire shape stays uniform with
    tenant-side.

    All assignments returned regardless of status (locked decision 6
    of Step 6.8.3); ``ORDER BY granted_at DESC, id ASC`` inside the
    aggregate keeps the wire shape deterministic.

    COALESCE-to-``'[]'::jsonb`` so users with zero assignments get an
    empty list rather than NULL.
    """
    status_as_text = cast(PlatformUserRoleAssignment.status, String)
    item_object = func.jsonb_build_object(
        "assignment_id", PlatformUserRoleAssignment.id,
        "role_id", Role.id,
        "role_name", Role.name,
        "role_code", Role.code,
        "status", status_as_text,
        "granted_at", PlatformUserRoleAssignment.granted_at,
        "org_node_id", text("NULL::uuid"),
        "org_node_name", text("NULL::text"),
    )
    ordered_item = aggregate_order_by(
        item_object,
        PlatformUserRoleAssignment.granted_at.desc(),
        PlatformUserRoleAssignment.id.asc(),
    )
    return (
        select(
            func.coalesce(
                func.jsonb_agg(ordered_item),
                text("'[]'::jsonb"),
            )
        )
        .select_from(PlatformUserRoleAssignment)
        .join(Role, Role.id == PlatformUserRoleAssignment.role_id)
        .where(
            PlatformUserRoleAssignment.platform_user_id == PlatformUser.id,
        )
        .correlate(PlatformUser)
        .scalar_subquery()
    )


class PlatformUsersRepo:
    """Read-only repository for ``platform_users``."""

    async def get_by_id(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> PlatformUserDetailRow | None:
        """Return the platform user with this id (with roles aggregate)
        or ``None`` if not found."""
        stmt = select(
            PlatformUser,
            _roles_subq().label("roles"),
        ).where(PlatformUser.id == user_id)
        result = await session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        user_obj, roles = row
        return PlatformUserDetailRow(user=user_obj, roles=roles)

    async def list(
        self,
        session: AsyncSession,
        *,
        status: PlatformUserStatus | None = None,
        search: str | None = None,
        sort: str = "created_at_desc",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[PlatformUserListRow], int]:
        """Return ``(rows, total)`` matching filters.

        ``rows`` carries each ``PlatformUser`` with its roles
        aggregate; ``total`` counts rows matching the same filters but
        ignoring offset/limit so pagination metadata is correct.

        - ``status``: filter to a single status (typically ``ACTIVE``).
        - ``search``: case-insensitive ILIKE across ``email`` and
          ``full_name``.
        - ``sort``: one of ``SORT_MAP`` keys; raises
          ``InvalidSortKeyError`` for unknown keys.
        - ``offset`` / ``limit``: pagination.

        The roles correlated subquery is independent per outer row;
        no row multiplication, so ``limit``/``offset`` slice the
        intended PlatformUser rows.
        """
        if sort not in SORT_MAP:
            raise InvalidSortKeyError(f"unknown sort key: {sort}")

        conditions = []
        if status is not None:
            conditions.append(PlatformUser.status == status)
        if search:
            pat = f"%{search}%"
            conditions.append(
                or_(
                    PlatformUser.email.ilike(pat),
                    PlatformUser.full_name.ilike(pat),
                )
            )

        count_stmt = select(func.count()).select_from(PlatformUser)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        count_result = await session.execute(count_stmt)
        total: int = count_result.scalar_one()

        stmt = (
            select(
                PlatformUser,
                _roles_subq().label("roles"),
            )
            .order_by(SORT_MAP[sort], PlatformUser.id.asc())
        )
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.offset(offset).limit(limit)

        items_result = await session.execute(stmt)
        rows = [
            PlatformUserListRow(user=u, roles=r)
            for (u, r) in items_result.all()
        ]
        return rows, total
