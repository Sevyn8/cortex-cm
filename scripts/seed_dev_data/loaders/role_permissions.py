"""Loader for role_permissions (junction table, composite PK).

``role_permissions`` is a pure (role_id, permission_id) mapping
table — no ``id`` column, composite PK. The standard ``_base.
insert_and_register`` adds ``RETURNING id`` which Postgres rejects
on this table; this loader bypasses ``_base`` and does a plain
INSERT.

No other sheet references role_permissions by id, so there's nothing
to register in the UUIDMapper either.
"""
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.seed_dev_data.column_mappings import (
    excel_columns_for_db_insert,
    validate_columns,
)
from scripts.seed_dev_data.loaders._base import build_insert_row
from scripts.seed_dev_data.uuid_mapper import UUIDMapper

SHEET_NAME = "role_permissions"
TABLE_NAME = "role_permissions"


async def load(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    mapper: UUIDMapper,
) -> None:
    if rows:
        validate_columns(SHEET_NAME, list(rows[0].keys()))
    for row in rows:
        insert_row = build_insert_row(SHEET_NAME, row, mapper)
        columns = list(insert_row.keys())
        placeholders = ", ".join(f":{c}" for c in columns)
        column_list = ", ".join(columns)
        sql = (
            f"INSERT INTO {TABLE_NAME} ({column_list}) "
            f"VALUES ({placeholders})"
        )
        await session.execute(text(sql), insert_row)
