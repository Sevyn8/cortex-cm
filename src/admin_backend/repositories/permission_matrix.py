"""PermissionMatrixRepo — load the data backing E6's render-ready grid.

The single ``get_matrix(audience_filter)`` method returns three lists:

  - ``roles``: ordered audience_asc, name_asc, ACTIVE only. The
    audience filter applies here (TENANT JWTs see only TENANT-audience
    roles, PLATFORM JWTs see both audiences).
  - ``permission_rows``: list of dicts, each row carrying the four
    permission enum codes plus four display labels resolved from
    ``lookups`` via four LEFT JOINs (one per slot). Ordered
    module/resource/action/scope ascending.
  - ``grants``: list of ``(role_id, permission_id)`` for every junction
    row referencing a loaded role. The router turns this into the
    boolean ``cells[]`` array per row, position-aligned with the
    column array.

The four-LEFT-JOIN approach is implemented via raw ``text()`` so each
list_name predicate is a constant rather than a parameter. The 4 JOINs
are independent of each other (no co-join concerns); each adds the
``display_name`` of the matching ``lookups`` row, falling back to the
enum code itself if no lookup row matches (defensive — a missing label
should not break matrix render). Per the locked vocabulary, all 25
lookup rows exist post Step 6.1's seed migration, so the COALESCE is
belt-and-suspenders.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.config import get_settings
from admin_backend.models import Role
from admin_backend.models.role_permission import RolePermission


class PermissionMatrixRepo:
    """Read-only repository for the permission matrix (E6)."""

    async def get_matrix(
        self,
        session: AsyncSession,
        *,
        audience_filter: str | None,
    ) -> tuple[list[Role], list[dict[str, Any]], list[tuple[UUID, UUID]]]:
        """Load the three pieces the router assembles into the response.

        Returns:
            (roles, permission_rows, grants)

        - ``roles`` is the column-header dataset, ordered
          audience_asc, name_asc, ACTIVE only.
        - ``permission_rows`` is a list of dicts with the permission
          column values plus four ``*_label`` strings, ordered
          module/resource/action/scope ascending.
        - ``grants`` is a list of ``(role_id, permission_id)`` tuples
          for every junction row referencing one of the loaded roles.
          The router builds the per-row boolean ``cells[]`` array from
          this set.
        """
        # 1. Load roles (audience-filtered + active-only).
        role_stmt = select(Role).where(Role.status == "ACTIVE")
        if audience_filter is not None:
            role_stmt = role_stmt.where(Role.audience == audience_filter)
        role_stmt = role_stmt.order_by(
            Role.audience.asc(),
            Role.name.asc(),
            Role.id.asc(),
        )
        roles = list((await session.execute(role_stmt)).scalars().all())
        role_ids = [r.id for r in roles]

        # 2. Load permissions with display labels via 4 LEFT JOINs.
        permission_rows = await self._load_permissions_with_labels(session)

        # 3. Load grants for the loaded roles.
        if not role_ids:
            grants: list[tuple[UUID, UUID]] = []
        else:
            grant_stmt = select(
                RolePermission.role_id, RolePermission.permission_id
            ).where(RolePermission.role_id.in_(role_ids))
            grants_rows = (await session.execute(grant_stmt)).all()
            grants = [(r[0], r[1]) for r in grants_rows]

        return roles, permission_rows, grants

    async def _load_permissions_with_labels(
        self, session: AsyncSession
    ) -> list[dict[str, Any]]:
        """LEFT JOIN ``permissions`` against ``lookups`` 4 times for the
        four enum slots; return a list of dicts ordered by
        ``lookups.display_order`` (module-side primary), then
        resource/action/scope ascending, then code/id for stable tie-
        breakers.

        Step 6.6 changed the module-side ordering basis: pre-step,
        ``ORDER BY p.module`` sorted by ``module_enum``'s ordinal
        (DDL declaration order); post-step, ``permissions.module`` is
        ``module_code_enum`` whose ordinals differ from the old enum
        for the same four overlapping values (e.g., ADMIN moved from
        ordinal 0 to 5). Sorting by ``lk_module.display_order``
        decouples the sort from enum ordinal and makes the seed data's
        explicit ``display_order`` column the source of truth — robust
        across future enum vocabulary changes (additive ALTER TYPE
        ADD VALUE will append to the enum's ordinal list, but the
        seed's display_order keeps the intended UX ordering stable).
        Resource/action/scope sort still uses enum ordinal because
        their lookups list_names ('resource', 'permission_action',
        'permission_scope') aren't currently joined for the sort and
        haven't shown drift between enum-ordinal and display_order;
        promote them if the same drift surfaces there.

        ``COALESCE(lk_module.display_order, 999)`` defends against a
        permission row whose module value lacks a corresponding
        lookups row — same defensive posture as the
        ``COALESCE(lk_*.display_name, p.<col>::text)`` label fallbacks.
        Such rows sort to the end with stable secondary by
        resource/action/scope/code/id.

        Schema-qualified explicitly because raw text bypasses the
        SQLAlchemy/__table_args__ pathway. ``get_settings().db_schema``
        is the same source the ORM models use.
        """
        schema = get_settings().db_schema
        sql = text(
            f"""
            SELECT
                p.id AS id,
                p.module::text   AS module,
                p.resource::text AS resource,
                p.action::text   AS action,
                p.scope::text    AS scope,
                COALESCE(lk_module.display_name,   p.module::text)   AS module_label,
                COALESCE(lk_resource.display_name, p.resource::text) AS resource_label,
                COALESCE(lk_action.display_name,   p.action::text)   AS action_label,
                COALESCE(lk_scope.display_name,    p.scope::text)    AS scope_label
            FROM {schema}.permissions AS p
            LEFT JOIN {schema}.lookups AS lk_module
                ON lk_module.list_name = 'module_code'
                AND lk_module.code = p.module::text
            LEFT JOIN {schema}.lookups AS lk_resource
                ON lk_resource.list_name = 'resource'
                AND lk_resource.code = p.resource::text
            LEFT JOIN {schema}.lookups AS lk_action
                ON lk_action.list_name = 'permission_action'
                AND lk_action.code = p.action::text
            LEFT JOIN {schema}.lookups AS lk_scope
                ON lk_scope.list_name = 'permission_scope'
                AND lk_scope.code = p.scope::text
            ORDER BY
                COALESCE(lk_module.display_order, 999) ASC,
                p.resource ASC, p.action ASC, p.scope ASC,
                p.code ASC, p.id ASC
            """
        )
        result = await session.execute(sql)
        rows: list[dict[str, Any]] = [dict(row) for row in result.mappings().all()]
        return rows
