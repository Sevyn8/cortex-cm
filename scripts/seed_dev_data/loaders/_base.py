"""Shared loader pattern.

Per-sheet loaders specialise this for their table-specific INSERT
shape, but the column-dispatch logic is uniform.

Self-referential sheets (``platform_users``, ``org_nodes``) bypass or
defer-loop this base because ``mapper.lookup`` raises
``UnresolvedFKError`` when a row references another row that hasn't
been inserted yet.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.seed_dev_data.column_mappings import (
    ColumnRole,
    excel_columns_for_db_insert,
)
from scripts.seed_dev_data.uuid_mapper import UUIDMapper


def build_insert_row(
    sheet_name: str,
    excel_row: dict[str, Any],
    mapper: UUIDMapper,
) -> dict[str, Any]:
    """Translate an Excel row dict into a DB insert row dict.

    - HELPER columns: dropped (already filtered by
      ``excel_columns_for_db_insert``).
    - ``id``: dropped (DB DEFAULT ``uuidv7()`` fires).
    - DB_COLUMN: passed verbatim.
    - FK_REF: looked up via ``mapper[fk_target]``. ``None`` values
      pass through (NULL FK is legitimate for nullable columns and
      the user_role_assignments loader's per-row routing — one
      user-side FK populated, the other NULL — mirroring the
      pre-Step-6.8.1 dual-FK XOR shape from the now-split table).

    Raises ``UnresolvedFKError`` if any FK_REF points to an unmapped
    excel_id; the caller (e.g. org_nodes' multi-pass loader) handles
    by deferring.
    """
    insert_row: dict[str, Any] = {}
    for spec in excel_columns_for_db_insert(sheet_name):
        if spec.name == "id":
            continue  # let DB DEFAULT uuidv7() fire
        excel_value = excel_row.get(spec.name)
        if spec.role is ColumnRole.DB_COLUMN:
            insert_row[spec.name] = excel_value
        elif spec.role is ColumnRole.FK_REF:
            assert spec.fk_target is not None
            insert_row[spec.name] = mapper.lookup(
                spec.fk_target, excel_value
            )
    return insert_row


async def insert_and_register(
    session: AsyncSession,
    sheet_name: str,
    table_name: str,
    excel_row: dict[str, Any],
    mapper: UUIDMapper,
) -> UUID:
    """Build the INSERT row, execute, register the assigned id.

    Uses ``RETURNING id`` so we get the DB-assigned uuidv7 back.
    The Excel's original v4 id is keyed in the mapper to the new v7
    so subsequent sheets can resolve their FK columns.

    Raises ``UnresolvedFKError`` if the row references unmapped FK
    targets; the caller (e.g., org_nodes' multi-pass loader) handles
    by deferring.
    """
    insert_row = build_insert_row(sheet_name, excel_row, mapper)
    columns = list(insert_row.keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    column_list = ", ".join(columns)
    sql = (
        f"INSERT INTO {table_name} ({column_list}) "
        f"VALUES ({placeholders}) RETURNING id"
    )
    result = await session.execute(text(sql), insert_row)
    db_id: UUID = result.scalar_one()
    excel_id = excel_row.get("id")
    if excel_id is not None:
        mapper.register(sheet_name, excel_id, db_id)
    return db_id
