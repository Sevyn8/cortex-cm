"""Per-resource anchor dependency functions (Step 6.9.3.2).

Anchor deps look up an ``org_node.path`` for a request's target row,
returning a ltree-formatted string suitable for passing to
``has_permission``'s ``target_anchor`` parameter.

CRITICAL — security invariant (per F-THREADING-4): on lookup miss,
these functions RAISE the appropriate ``*NotFoundError`` (404). They do
NOT return ``None`` to signal "not found." Returning ``None`` would
short-circuit the cascade clause in ``has_permission`` to TRUE (no
target_anchor → cascade inactive → grant matches), creating a security
regression. ``None`` is returned ONLY when the request has no specific
target (list endpoints, aggregate stats, PLATFORM-scope checks — those
endpoints declare no ``anchor_dep``).

RLS layering: these queries inherit the request's ``app.tenant_id`` /
``app.user_type`` GUCs via the injected session. Cross-tenant target
ids surface as ``*NotFoundError`` via RLS-invisible reads — matches
D-17's "RLS-as-404" framing.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.config import get_settings
from admin_backend.dependencies import get_tenant_session_dep
from admin_backend.errors import (
    OrgNodeNotFoundError,
    StoreNotFoundError,
    TenantNotFoundError,
    TenantUserNotFoundError,
)


async def get_tenant_anchor(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> str:
    """Return the tenant-root ``org_node.path`` for ``tenant_id``.

    Used by gates on ``/tenants/{tenant_id}`` and
    ``/tenants/{tenant_id}/org-tree``. The tenant-root row is
    ``org_node WHERE tenant_id=:id AND node_type='TENANT' AND parent_id IS NULL``;
    its ``path`` is the cascade root for every other node in the tenant.

    Raises ``TenantNotFoundError`` (404) when the tenant root is
    RLS-invisible to the caller's session or doesn't exist.
    """
    schema = get_settings().db_schema
    sql = text(
        f"""
        SELECT path::text AS anchor_path
        FROM {schema}.org_nodes
        WHERE tenant_id = :tenant_id
          AND node_type = CAST('TENANT' AS {schema}.org_node_type_enum)
          AND parent_id IS NULL
        LIMIT 1
        """
    )
    result = await session.execute(sql, {"tenant_id": tenant_id})
    row = result.first()
    if row is None:
        raise TenantNotFoundError(
            f"tenant_id={tenant_id} not visible or has no tenant-root org_node",
            tenant_id=str(tenant_id),
        )
    return str(row.anchor_path)


async def get_org_node_anchor(
    tenant_id: UUID,
    node_id: UUID,
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> str:
    """Return the ``org_node.path`` for the specific ``(tenant_id, node_id)``.

    Used by gates on ``/tenants/{tenant_id}/org-nodes/{node_id}/children``.
    Composite-key lookup per D-34: a TENANT-A caller cannot probe
    TENANT-B's node_ids — the row is RLS-invisible and the lookup
    raises 404.

    Raises ``OrgNodeNotFoundError`` (404) when the node is
    RLS-invisible to the caller's session or doesn't exist.
    """
    schema = get_settings().db_schema
    sql = text(
        f"""
        SELECT path::text AS anchor_path
        FROM {schema}.org_nodes
        WHERE tenant_id = :tenant_id
          AND id = :node_id
        LIMIT 1
        """
    )
    result = await session.execute(
        sql, {"tenant_id": tenant_id, "node_id": node_id}
    )
    row = result.first()
    if row is None:
        raise OrgNodeNotFoundError(
            f"org_node id={node_id} not visible in tenant_id={tenant_id}",
            tenant_id=str(tenant_id),
            node_id=str(node_id),
        )
    return str(row.anchor_path)


async def get_store_anchor(
    store_id: UUID,
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> str:
    """Return the tenant-root ``org_node.path`` for the store's tenant.

    Single-query JOIN: ``stores → org_nodes`` on the composite
    ``(tenant_id, node_type='TENANT', parent_id IS NULL)`` filter.
    Per-store anchoring (locked decision 8) exposes ``org_node_id``
    as a bare UUID but defers org-node-level scoping to a future step;
    the gate's cascade root is the store's tenant root.

    Used by gates on ``/stores/{store_id}``.

    Raises ``StoreNotFoundError`` (404) when the store is RLS-invisible
    (cross-tenant probe) or doesn't exist. Per F-THREADING-4 the dep
    raises rather than returning ``None`` — returning ``None`` would
    short-circuit ``has_permission``'s cascade to TRUE.
    """
    schema = get_settings().db_schema
    sql = text(
        f"""
        SELECT on_.path::text AS anchor_path
        FROM {schema}.stores AS s
        JOIN {schema}.org_nodes AS on_
          ON on_.tenant_id = s.tenant_id
         AND on_.node_type = CAST('TENANT' AS {schema}.org_node_type_enum)
         AND on_.parent_id IS NULL
        WHERE s.id = :store_id
        LIMIT 1
        """
    )
    result = await session.execute(sql, {"store_id": store_id})
    row = result.first()
    if row is None:
        raise StoreNotFoundError(
            f"store id={store_id} not visible or has no tenant root",
            store_id=str(store_id),
        )
    return str(row.anchor_path)


async def get_tenant_user_anchor(
    user_id: UUID,
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> str:
    """Return the tenant-root path for the tenant_user's tenant.

    TenantUser has no ``home_org_node_id`` FK (per F-ANCHOR-2 of the
    6.9.3 investigation); the per-user anchor defaults to the user's
    tenant root. Single-query JOIN: ``tenant_users → org_nodes`` on the
    composite ``(tenant_id, node_type='TENANT', parent_id IS NULL)``
    filter.

    Used by gates on ``/tenant-users/{user_id}``.

    Raises ``TenantUserNotFoundError`` (404) when the tenant_user is
    RLS-invisible (cross-tenant probe) or doesn't exist.
    """
    schema = get_settings().db_schema
    sql = text(
        f"""
        SELECT on_.path::text AS anchor_path
        FROM {schema}.tenant_users AS tu
        JOIN {schema}.org_nodes AS on_
          ON on_.tenant_id = tu.tenant_id
         AND on_.node_type = CAST('TENANT' AS {schema}.org_node_type_enum)
         AND on_.parent_id IS NULL
        WHERE tu.id = :user_id
        LIMIT 1
        """
    )
    result = await session.execute(sql, {"user_id": user_id})
    row = result.first()
    if row is None:
        raise TenantUserNotFoundError(
            f"tenant_user id={user_id} not visible or has no tenant root",
            user_id=str(user_id),
        )
    return str(row.anchor_path)
