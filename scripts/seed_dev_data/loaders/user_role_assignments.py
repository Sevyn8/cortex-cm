"""Loader for the user_role_assignments sheet — routes per row.

Post Step 6.8.1 split (D-34): the dual-FK XOR is gone at the DB layer.
Each Excel row has exactly one user-side FK (``platform_user_id`` XOR
``tenant_user_id``); the loader inspects which one is populated and
writes to the matching physical table:

  - ``platform_user_id`` set, ``tenant_user_id`` NULL ->
    ``platform_user_role_assignments`` (no RLS, no tenant_id columns).
  - ``tenant_user_id`` set, ``platform_user_id`` NULL ->
    ``tenant_user_role_assignments`` (RLS+FORCE, NOT NULL tenant_id).

Per-row tenant impersonation (the pre-split pattern under FN-AB-14's
IS-NULL-gated policy) is NO LONGER NEEDED here.
``tenant_user_role_assignments`` uses the unconditional D-29
OR-branch; the runner's PLATFORM session writes any TENANT-side row
without setting ``app.tenant_id``. The ``_set_tenant_guc`` helper has
been retired.

Audience-check triggers (``enforce_platform_role_audience``,
``enforce_tenant_role_audience``) fire per row and would abort the
load if a row's ``role.audience`` doesn't match the user-side column.
The seed has been verified consistent at multiple prior steps; the
trigger is the schema-level guarantee.

Composite FKs on ``tenant_user_role_assignments`` —
``(tenant_id, tenant_user_id) -> tenant_users(tenant_id, id)`` and
``(tenant_id, org_node_id) -> org_nodes(tenant_id, id)`` — make
cross-tenant injection structurally impossible at the schema layer.
The loader does not need to enforce that invariant in code.
"""
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.seed_dev_data.column_mappings import validate_columns
from scripts.seed_dev_data.loaders._base import build_insert_row
from scripts.seed_dev_data.uuid_mapper import UUIDMapper

SHEET_NAME = "user_role_assignments"
PLATFORM_TABLE = "platform_user_role_assignments"
TENANT_TABLE = "tenant_user_role_assignments"

# Columns to drop from the platform-side INSERT row (they don't exist
# on ``platform_user_role_assignments``).
_PLATFORM_DROP_COLUMNS: tuple[str, ...] = (
    "tenant_id",
    "tenant_user_id",
    "org_node_id",
)

# Column to drop from the tenant-side INSERT row.
_TENANT_DROP_COLUMNS: tuple[str, ...] = ("platform_user_id",)


async def load(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    mapper: UUIDMapper,
) -> None:
    """Route each Excel row to the correct physical table and INSERT.

    Each row must have exactly one of ``platform_user_id`` /
    ``tenant_user_id`` populated. Both populated or both NULL is a
    seed-data error and raises ``ValueError`` (loud failure rather
    than silent routing).
    """
    if rows:
        validate_columns(SHEET_NAME, list(rows[0].keys()))

    for row in rows:
        platform_user_xid = row.get("platform_user_id")
        tenant_user_xid = row.get("tenant_user_id")
        platform_set = platform_user_xid is not None
        tenant_set = tenant_user_xid is not None

        if platform_set and not tenant_set:
            target_table = PLATFORM_TABLE
            drop_columns = _PLATFORM_DROP_COLUMNS
        elif tenant_set and not platform_set:
            target_table = TENANT_TABLE
            drop_columns = _TENANT_DROP_COLUMNS
        else:
            raise ValueError(
                f"user_role_assignments seed row with _key="
                f"{row.get('_role_key', '?')}/"
                f"{row.get('_org_node_key', '?')}: exactly one of "
                f"platform_user_id / tenant_user_id must be populated; "
                f"got platform_user_id={platform_user_xid}, "
                f"tenant_user_id={tenant_user_xid}"
            )

        # Build the row dict via the shared helper, then strip the
        # columns that don't exist on the target table. ``build_insert_row``
        # constructs and returns a fresh dict per call (no shared state),
        # so popping from it is safe.
        insert_row = build_insert_row(SHEET_NAME, row, mapper)
        for col in drop_columns:
            insert_row.pop(col, None)

        columns = list(insert_row.keys())
        placeholders = ", ".join(f":{c}" for c in columns)
        column_list = ", ".join(columns)
        sql = (
            f"INSERT INTO {target_table} ({column_list}) "
            f"VALUES ({placeholders}) RETURNING id"
        )
        result = await session.execute(text(sql), insert_row)
        db_id = result.scalar_one()
        excel_id = row.get("id")
        if excel_id is not None:
            mapper.register(SHEET_NAME, excel_id, db_id)
    # Caller's get_tenant_session handles commit/rollback on yield exit
    # (see src/admin_backend/db/session.py).
