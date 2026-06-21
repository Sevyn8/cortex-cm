"""LookupsRepo — read-only access to the ``lookups`` table.

Returns dropdown data for frontend forms. ``lookups`` is platform-
global reference data (no RLS); all sessions read the same rows
regardless of tenant context.

Mirrors ``TenantsRepo``'s shape: stateless singleton (``_repo =
LookupsRepo()`` at module level in the router), methods take
``session: AsyncSession`` as their first argument. The Repo holds
no session, no settings, no config — it's a method bag.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.models.lookup import Lookup


class LookupsRepo:
    """Read-only repository for lookup categories. Stateless."""

    async def get_lists_batch(
        self,
        session: AsyncSession,
        list_names: list[str],
    ) -> dict[str, list[Lookup]]:
        """Return a map of ``list_name -> list[Lookup]``.

        Each list is filtered to ``is_active=True`` and sorted by
        ``display_order`` ascending. Every requested ``list_name`` is
        present in the returned dict; lists with no rows in the DB
        come back as empty lists (predictable shape — frontend can
        iterate without nullchecks).

        Empty input returns an empty dict.
        """
        if not list_names:
            return {}

        stmt = (
            select(Lookup)
            .where(
                Lookup.list_name.in_(list_names),
                Lookup.is_active.is_(True),
            )
            .order_by(Lookup.list_name, Lookup.display_order)
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())

        # Initialise every requested list_name with an empty list so
        # the response shape is predictable even when a list has no
        # rows in the DB. Then populate from query results.
        grouped: dict[str, list[Lookup]] = {
            name: [] for name in list_names
        }
        for row in rows:
            grouped[row.list_name].append(row)
        return grouped
