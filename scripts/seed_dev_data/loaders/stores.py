"""Loader for stores."""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from scripts.seed_dev_data.column_mappings import validate_columns
from scripts.seed_dev_data.loaders._base import insert_and_register
from scripts.seed_dev_data.uuid_mapper import UUIDMapper

SHEET_NAME = "stores"
TABLE_NAME = "stores"


async def load(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    mapper: UUIDMapper,
) -> None:
    if rows:
        validate_columns(SHEET_NAME, list(rows[0].keys()))
    for row in rows:
        await insert_and_register(
            session, SHEET_NAME, TABLE_NAME, row, mapper
        )
