"""Lookups batch endpoint (Step 3.6).

``GET /api/v1/lookups?lists=...`` returns a map of
``{list_name: [item, ...]}``. Single batch endpoint so the frontend
loads all dropdown values for a page in one request rather than one
request per dropdown.

Auth: standard JWT-required (any user_type accepted; ``lookups`` is
platform-global reference data). Same session-dep as the tenants
router (``get_tenant_session_dep``) for parity — even though
``lookups`` has no RLS, using a different session-getter would
diverge the router patterns.

Query-param style: comma-separated (``?lists=a,b,c``), not repeated
(``?lists=a&lists=b&lists=c``). Both are defensible REST styles;
choosing one consistently is what matters. Reversible to the
repeated form via a one-line parser change if a frontend HTTP
library forces the other shape.

Empty input: ``?lists=`` or ``?lists=  ,  ,  `` returns
``{lookups: {}}`` (200, not 422). Whitespace-stripping happens in
the handler; the underlying repo handles the empty list cleanly.

Unknown list_names: requesting a ``list_name`` not seeded in the DB
returns an empty array for that key (predictable shape — frontend
iterates without nullchecks). The endpoint is country-tolerant in
this way: ``?lists=country`` returns ``{"lookups": {"country": []}}``
until the country lookup design lands (deferred per Step 3.6's
known follow-up).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.dependencies import get_tenant_session_dep
from admin_backend.repositories.lookups import LookupsRepo
from admin_backend.schemas.lookup import LookupItem, LookupsBatchResponse


router = APIRouter(prefix="/lookups", tags=["lookups"])

# Stateless instance reused across requests (mirrors TenantsRepo pattern).
_repo = LookupsRepo()


@router.get(
    "",
    response_model=LookupsBatchResponse,
    summary="Batch lookup values for dropdowns",
)
async def get_lookups_batch(
    lists: str = Query(
        ...,
        description=(
            "Comma-separated list_names. Each list_name maps to a "
            "category of dropdown values (e.g., ``tenant_tier``, "
            "``tenant_region``, ``tenant_status``, ``tenant_industry``, "
            "``module_code``). list_names not seeded in the database "
            "return an empty array for that key (predictable shape). "
            "Whitespace-only or empty input returns an empty lookups "
            "map with status 200."
        ),
        examples=[
            "tenant_tier,tenant_region,tenant_status,"
            "tenant_industry,module_code"
        ],
    ),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Return ``{lookups: {list_name: [items], ...}}`` for the requested lists.

    Items per list are sorted by ``display_order`` ascending. Each
    requested ``list_name`` appears as a key in the response, even
    if the DB has no rows for it (empty-array shape — the frontend
    can iterate without null checks).
    """
    list_names = [n.strip() for n in lists.split(",") if n.strip()]
    grouped = await _repo.get_lists_batch(session, list_names)
    return LookupsBatchResponse(
        lookups={
            name: [
                LookupItem.model_validate(row) for row in rows
            ]
            for name, rows in grouped.items()
        }
    )
