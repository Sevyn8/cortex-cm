"""Pydantic v2 schemas for the lookups batch endpoint (Step 3.6).

Response shape: ``{lookups: {list_name: [item, ...], ...}}``. The
top-level wrapper leaves room for cross-cutting metadata
(``cached_at``, ``version``, etc.) without breaking the contract —
the convention captured in CLAUDE.md is "future batch-by-key
endpoints follow this same envelope pattern; do not return a bare
map at top level."
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LookupItem(BaseModel):
    """Single row from the ``lookups`` table.

    The DB row carries more (id, description, created_at, updated_at)
    but the API surface exposes only the three fields the frontend
    needs to render a dropdown option.
    """

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(
        ...,
        description=(
            "Wire-stable enum code; matches the value stored in other "
            "tables' enum columns (e.g., a ``tenants.tier`` value of "
            "``ENTERPRISE`` aligns with this code in the ``tenant_tier`` "
            "list)."
        ),
    )
    display_name: str = Field(
        ...,
        description=(
            "Human-readable label for UI rendering (title case for "
            "natural words; ALL CAPS only when the original is an "
            "acronym)."
        ),
    )
    display_order: int = Field(
        ...,
        description=(
            "Frontend ordering hint; lower numbers shown first within "
            "a list."
        ),
    )


class LookupsBatchResponse(BaseModel):
    """Response shape for ``GET /api/v1/lookups``.

    Top-level envelope wraps the map (rather than returning a bare
    map) so future cross-cutting metadata can land at top level
    without breaking the contract.
    """

    lookups: dict[str, list[LookupItem]] = Field(
        default_factory=dict,
        description=(
            "Map of list_name -> sorted list of "
            "{code, display_name, display_order}. Every list_name "
            "in the request appears as a key (with an empty list as "
            "value if no rows are seeded for that list)."
        ),
    )
