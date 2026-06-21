"""Loader for platform_users with two-phase self-reference handling.

Phase 1: INSERT each row with NULL audit-actors. The columns are
nullable in the DDL and the table's CHECK constraints accept the
INVITED/ACTIVE/SUSPENDED status with appropriate companion fields
(auth0_sub, invitation_accepted_at, suspended_*); the loader passes
those Excel-supplied values verbatim.

Phase 2: walk the rows again and UPDATE each one to set the
audit-actor IDs. By this point Phase 1 has registered every row's
``excel_id -> db_id`` in the mapper, so ``mapper.lookup("platform_users",
...)`` resolves cleanly for every audit-actor reference (including
self-references like Anjali's ``created_by_user_id`` pointing at
herself).

Both phases share the runner's outer transaction (``get_tenant_session``
opens an ``async with session.begin()``); the loader does not commit
itself. The transaction commits cleanly when the runner's
``async for session in get_tenant_session(...)`` body exits.
"""
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.seed_dev_data.column_mappings import (
    excel_columns_for_db_insert,
    validate_columns,
)
from scripts.seed_dev_data.uuid_mapper import UUIDMapper

SHEET_NAME = "platform_users"
TABLE_NAME = "platform_users"


async def load(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    mapper: UUIDMapper,
) -> None:
    if rows:
        validate_columns(SHEET_NAME, list(rows[0].keys()))

    # Phase 1: INSERT with NULL self-FK audit-actors. The
    # platform_users DDL declares those columns as nullable; the
    # CHECK constraints (auth0_sub_consistency,
    # invitation_accepted_consistency, suspended_consistency) only
    # constrain those companion fields, not the audit-actor FKs.
    for row in rows:
        insert_row: dict[str, Any] = {}
        for spec in excel_columns_for_db_insert(SHEET_NAME):
            if spec.name == "id":
                continue  # let DB DEFAULT uuidv7() fire
            if spec.fk_target == "platform_users":
                # Phase 2 will populate; Phase 1 inserts NULL.
                insert_row[spec.name] = None
            else:
                insert_row[spec.name] = row.get(spec.name)
        columns = list(insert_row.keys())
        placeholders = ", ".join(f":{c}" for c in columns)
        column_list = ", ".join(columns)
        sql = (
            f"INSERT INTO {TABLE_NAME} ({column_list}) "
            f"VALUES ({placeholders}) RETURNING id"
        )
        result = await session.execute(text(sql), insert_row)
        db_id = result.scalar_one()
        mapper.register(SHEET_NAME, row["id"], db_id)

    # Phase 2: UPDATE each row's audit-actor columns now that all
    # rows are registered in the mapper.
    for row in rows:
        db_id = mapper.lookup(SHEET_NAME, row["id"])
        created_by = mapper.lookup(
            "platform_users", row.get("created_by_user_id")
        )
        updated_by = mapper.lookup(
            "platform_users", row.get("updated_by_user_id")
        )
        suspended_by = mapper.lookup(
            "platform_users", row.get("suspended_by_user_id")
        )
        await session.execute(
            text(
                """
                UPDATE platform_users
                SET created_by_user_id = :created_by,
                    updated_by_user_id = :updated_by,
                    suspended_by_user_id = :suspended_by
                WHERE id = :id
                """
            ),
            {
                "id": db_id,
                "created_by": created_by,
                "updated_by": updated_by,
                "suspended_by": suspended_by,
            },
        )
