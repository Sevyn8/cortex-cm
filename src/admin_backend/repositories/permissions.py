"""PermissionsRepo — read-only data access for the ``permissions`` catalogue.

Backs E2 (``GET /api/v1/permissions``).

The permissions table is platform-global, no RLS. Catalogue is
reference data — both user types see all rows. No ``audience_filter``
parameter (deliberate scope-out per BUILD_PLAN's Step 6.1: catalogue
is reference data).

Stateless singleton; mirrors the rest of the v0 repos.

Step 6.6 amendment (2026-05-06): the ``module_asc`` sort key sorts by
``lookups.display_order`` (joined via ``list_name='module_code'``)
rather than by ``Permission.module`` enum ordinal. Two reasons:

  - The migration that re-pointed ``permissions.module`` from
    ``module_enum`` to ``module_code_enum`` changed enum ordinals for
    the four overlapping values (e.g., ADMIN moved from ordinal 0
    in the narrow enum to ordinal 5 in the wider enum). Sort by enum
    ordinal would produce a different sequence post-migration than
    pre-migration on identical data.
  - ``lookups.display_order`` is the seed-data-defined, UX-correct
    ordering. Sorting by it makes the seed the source of truth for
    "how should this be displayed," decoupling the sort from any
    future enum vocabulary changes (additive ALTER TYPE ADD VALUE
    appends to the enum's ordinal list; the seed's display_order
    keeps the intended ordering stable).

The LEFT JOIN against ``lookups`` is added unconditionally — the
catalogue is small (44 rows post Step 6.6) and the JOIN cost is an
index seek on ``(list_name, code)``, sub-millisecond. The other sort
keys (``code_asc``, ``code_desc``) don't reference module so they
ignore the JOIN entirely.

``COALESCE(Lookup.display_order, 999)`` defends against a permission
row whose module value lacks a lookups row — same defensive posture
the permission-matrix Repo uses for label fallbacks.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import String, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.models import Permission
from admin_backend.models.lookup import Lookup
from admin_backend.repositories._errors import InvalidSortKeyError


# ``module_asc`` sorts by lookups.display_order to be enum-ordinal-
# independent (see module docstring). Other keys ignore the JOIN.
# Each value is a sequence of clauses passed to ``order_by(*clauses)``;
# stable tiebreaker by ``id ASC`` is appended at query time.
SORT_MAP: dict[str, list[Any]] = {
    "module_asc": [
        func.coalesce(Lookup.display_order, 999).asc(),
        Permission.resource.asc(),
        Permission.action.asc(),
        Permission.scope.asc(),
    ],
    "code_asc": [Permission.code.asc()],
    "code_desc": [Permission.code.desc()],
}


class PermissionsRepo:
    """Read-only repository for the ``permissions`` catalogue."""

    async def list(
        self,
        session: AsyncSession,
        *,
        module: str | None = None,
        scope: str | None = None,
        sort: str = "module_asc",
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[Permission], int]:
        """Return ``(items, total)``.

        ``total`` counts rows matching the same filters but ignoring
        offset/limit, so pagination metadata is correct.

        Filters:
          - ``module``: filter by one of the locked module values.
          - ``scope``:  filter by one of the locked scope values.
          - ``sort``:   one of SORT_MAP keys.
          - ``offset``/``limit``: pagination (never paginates in v0;
            present for consistency).

        The page query LEFT JOINs ``lookups`` on
        ``(list_name='module_code', code=Permission.module::text)``
        so the ``module_asc`` sort can use ``display_order``. Cast
        is needed because ``Permission.module`` is the
        ``module_code_enum`` PG type and ``Lookup.code`` is text;
        Postgres rejects implicit text-vs-enum equality.
        """
        if sort not in SORT_MAP:
            raise InvalidSortKeyError(f"unknown sort key: {sort}")

        conditions = []
        if module is not None:
            conditions.append(Permission.module == module)
        if scope is not None:
            conditions.append(Permission.scope == scope)

        count_stmt = select(func.count()).select_from(Permission)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        count_result = await session.execute(count_stmt)
        total: int = count_result.scalar_one()

        stmt = select(Permission).outerjoin(
            Lookup,
            and_(
                Lookup.list_name == "module_code",
                Lookup.code == cast(Permission.module, String),
            ),
        )
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.order_by(*SORT_MAP[sort], Permission.id.asc())
        stmt = stmt.offset(offset).limit(limit)

        result = await session.execute(stmt)
        return list(result.scalars().all()), total
