"""Loader for tenant_module_access (specialised: audit-actor synthesis).

The seed Excel for ``tenant_module_access`` predates Step 3.4.5 and
does not carry audit-actor columns. The DDL requires three NOT NULL
FKs to ``platform_users``:

  - ``enabled_by_user_id``  (NOT NULL)
  - ``created_by_user_id``  (NOT NULL)
  - ``updated_by_user_id``  (NOT NULL)
  - ``disabled_by_user_id`` (nullable)

This loader synthesises the three NOT NULL audit-actors at load time
by looking up Anjali (the seed's "system actor", per the seed
convention used elsewhere in the workbook) by email in the live
``platform_users`` table — which has been populated by the time
``tenant_module_access`` runs in the SHEETS_IN_ORDER list.

Why synthesise rather than edit the Excel: the seed Excel is a
*seeding mechanism*, not a *source of truth*. Audit-actor identity
for tenant_module_access is a system concern (the platform admin
managed the entitlement); it isn't tenant-author data. Carrying it
in the Excel would conflate two concerns and require a per-row
edit that adds no information. See CLAUDE.md "Note on seed Excel
shape" for the captured convention.

``disabled_by_user_id`` stays NULL because every seed row has
``status='ENABLED'``; the DDL's ``ck_tenant_module_access_disabled_pair``
CHECK requires both ``disabled_at`` and ``disabled_by_user_id`` NULL
when the module is currently enabled.

The ``column_mappings.py`` entry for tenant_module_access stays at
7 columns (id, tenant_id, module, status, enabled_at + 2 helpers)
intentionally — adding the audit-actor columns to the mapping would
imply Excel-sourced data, which they aren't.
"""
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.seed_dev_data.column_mappings import (
    excel_columns_for_db_insert,
    validate_columns,
)
from scripts.seed_dev_data.uuid_mapper import UUIDMapper

SHEET_NAME = "tenant_module_access"
TABLE_NAME = "tenant_module_access"

# The seed's universal "system actor" — by convention every audit-
# actor in the seed Excel points at Anjali (the platform admin).
# Looking up by email rather than by mapper-iteration-order gives
# a stable anchor that doesn't depend on insert sequencing.
_SYSTEM_ACTOR_EMAIL = "anjali@ithina.ai"


async def _resolve_system_actor_id(session: AsyncSession) -> UUID:
    """Look up Anjali's platform_users.id by email.

    Runs once at the start of this sheet's load. Raises if no
    platform_user with that email exists — that would mean
    platform_users didn't load, which is a sequencing bug worth
    surfacing immediately.
    """
    result = await session.execute(
        text("SELECT id FROM platform_users WHERE email = :email"),
        {"email": _SYSTEM_ACTOR_EMAIL},
    )
    row = result.one_or_none()
    if row is None:
        raise RuntimeError(
            f"tenant_module_access loader: no platform_user with email "
            f"{_SYSTEM_ACTOR_EMAIL!r} found. Has platform_users loaded "
            "first?"
        )
    return row.id  # type: ignore[no-any-return]


async def load(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    mapper: UUIDMapper,
) -> None:
    if rows:
        validate_columns(SHEET_NAME, list(rows[0].keys()))
    if not rows:
        return

    # Resolve the system actor once; reuse across all rows.
    system_actor_id = await _resolve_system_actor_id(session)

    for row in rows:
        # Build the Excel-sourced portion via the standard mapping
        # (id stripped; tenant_id resolved via UUIDMapper; the rest
        # passed verbatim).
        insert_row: dict[str, Any] = {}
        for spec in excel_columns_for_db_insert(SHEET_NAME):
            if spec.name == "id":
                continue  # let DB DEFAULT uuidv7() fire
            v = row.get(spec.name)
            if spec.fk_target is not None:
                insert_row[spec.name] = mapper.lookup(spec.fk_target, v)
            else:
                insert_row[spec.name] = v

        # Synthesise the three NOT NULL audit-actor columns. The
        # Excel does not carry them; Anjali is the universal system
        # actor by seed convention. disabled_by_user_id stays NULL
        # because every seed row is ENABLED (DDL CHECK enforces
        # the pairing).
        insert_row["enabled_by_user_id"] = system_actor_id
        insert_row["created_by_user_id"] = system_actor_id
        insert_row["updated_by_user_id"] = system_actor_id

        columns = list(insert_row.keys())
        placeholders = ", ".join(f":{c}" for c in columns)
        column_list = ", ".join(columns)
        sql = (
            f"INSERT INTO {TABLE_NAME} ({column_list}) "
            f"VALUES ({placeholders}) RETURNING id"
        )
        result = await session.execute(text(sql), insert_row)
        db_id = result.scalar_one()
        excel_id = row.get("id")
        if excel_id is not None:
            mapper.register(SHEET_NAME, excel_id, db_id)
