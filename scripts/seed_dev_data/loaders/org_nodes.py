"""Loader for org_nodes with multi-pass parent-before-child ordering.

The ``parent_id`` column is a self-FK to ``org_nodes``; children
must be inserted after their parents are registered in the mapper.
The Excel row order does NOT guarantee topological ordering.

Algorithm: loop over the remaining rows, inserting any whose parent
is NULL (TENANT root) or already mapped. Defer the rest to the next
pass. Repeat until either all rows are inserted (success) or no
progress was made in a full pass (cycle / unresolvable parent —
loud error).

Suitable for the ~49-row seed; the small N makes the multi-pass
overhead irrelevant.
"""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from scripts.seed_dev_data.column_mappings import validate_columns
from scripts.seed_dev_data.loaders._base import insert_and_register
from scripts.seed_dev_data.uuid_mapper import UUIDMapper

SHEET_NAME = "org_nodes"
TABLE_NAME = "org_nodes"


async def load(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    mapper: UUIDMapper,
) -> None:
    if rows:
        validate_columns(SHEET_NAME, list(rows[0].keys()))

    remaining = list(rows)
    while remaining:
        progress = False
        deferred: list[dict[str, Any]] = []
        for row in remaining:
            parent_excel_id = row.get("parent_id")
            if parent_excel_id is None:
                # Root node — insert immediately.
                await insert_and_register(
                    session, SHEET_NAME, TABLE_NAME, row, mapper
                )
                progress = True
                continue
            # Check the mapper without raising — defer if parent
            # not yet mapped.
            if not mapper.is_mapped(SHEET_NAME, parent_excel_id):
                deferred.append(row)
                continue
            await insert_and_register(
                session, SHEET_NAME, TABLE_NAME, row, mapper
            )
            progress = True
        if not progress:
            unresolved = [
                r.get("_key", r.get("id")) for r in deferred
            ]
            raise RuntimeError(
                f"org_nodes: cycle or unresolvable parents: "
                f"{unresolved}"
            )
        remaining = deferred
