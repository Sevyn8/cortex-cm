"""Pydantic v2 schemas for the Module Access read endpoints (Step 6.7).

Two endpoints back the Module Access governance console
(Frontend spec — sidebar entry "Module Access" under "ACCESS CONTROL"):

  - E1 ``GET /api/v1/module-access/modules`` — 6 module cards with
    per-module aggregates.
  - E2 ``GET /api/v1/module-access/matrix``  — paginated tenant ×
    module grid.

**Label-handling convention (locked at this step).** Every enum-coded
field carries a sibling ``<field>_label`` resolved server-side via
LEFT JOIN against ``lookups`` with COALESCE(display_name, code) fallback.
Always present, never null. Applies to **new endpoints from Step 6.7
forward**; older endpoints (`/tenants`, `/tenant-users`, `/platform-users`,
`/roles`, `/org-tree`, `/dashboard/*`) stay bare-enum.

Both responses use ``ConfigDict(extra="forbid")`` so undocumented fields
fail Pydantic validation immediately rather than silently shipping.

D-30 posture: ``/modules`` uses a list envelope without pagination
(fixed cardinality of 6 — no pagination metadata to carry).
``/matrix`` uses the standard ``{items, pagination}`` envelope.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from admin_backend.models.tenant_module_access import (
    ModuleAccessStatus,
    ModuleCode,
)
from admin_backend.schemas.tenant import Pagination

# Locked enum vocabularies. Mirroring the live ORM enums:
#   ModuleCode   (models/tenant_module_access.py)
#   ModuleAccessStatus (models/tenant_module_access.py)
#   TenantTier   (models/tenant.py)
#   TenantStatus (models/tenant.py) minus TERMINATED on the matrix row
#                                    set (TERMINATED tenants are filtered out).
ModuleCodeLiteral = Literal[
    "GOAL_CONSOLE",
    "PRICING_OS",
    "PERISHABLES_ASSISTANT",
    "PROMOTIONS_ASSISTANT",
    "ADMIN",
]


# =============================================================================
# E1: GET /module-access/modules
# =============================================================================


class ModuleCard(BaseModel):
    """One module card. Six per response, ordered by lookups.display_order.

    ``enabled_count`` is RLS-scoped; under TENANT JWT it collapses to
    0 or 1 (own tenant only). ``total_active_trial_tenants`` is the
    same row-set property on every card (cardinality of the active +
    trial slice of visible tenants).
    """

    model_config = ConfigDict(extra="forbid")

    module_code: ModuleCodeLiteral = Field(
        description=(
            "Stable wire code matching ``module_code_enum``. Use this "
            "for any frontend dispatch logic — labels can change, "
            "codes are append-only."
        ),
    )
    module_label: str = Field(
        description=(
            "Display name resolved from ``lookups`` "
            "(``list_name='module_code'``). COALESCE-fallback to the "
            "raw enum code if a lookup row is missing. Always present."
        ),
    )
    enabled_count: int = Field(
        description=(
            "Number of visible tenants (status IN (ACTIVE, TRIAL)) "
            "with this module ENABLED. RLS-scoped: TENANT JWTs see "
            "0 or 1."
        ),
    )
    total_active_trial_tenants: int = Field(
        description=(
            "Denominator: total visible tenants with status IN "
            "(ACTIVE, TRIAL). Identical on every card in a single "
            "response (it's a row-set property, not per-module)."
        ),
    )


class ModulesResponse(BaseModel):
    """E1 response envelope.

    No pagination — fixed cardinality of 6 (one card per module in
    ``module_code_enum``). The wrapping envelope reserves room for
    future cross-cutting metadata (e.g., ``cached_at``) without
    breaking the contract.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[ModuleCard] = Field(
        description=(
            "Always 6 entries, ordered by "
            "``lookups.display_order ASC, module_code ASC``."
        ),
    )


# =============================================================================
# E2: GET /module-access/matrix
# =============================================================================


class MatrixCell(BaseModel):
    """Per-tenant per-module enablement state.

    ``status='ENABLED'`` reflects an ENABLED ``tenant_module_access``
    row; ``'DISABLED'`` reflects either an absent row OR a row with
    ``status='DISABLED'``. Frontend doesn't need to distinguish the
    two — the rendered behaviour is identical.
    """

    model_config = ConfigDict(extra="forbid")

    module_code: ModuleCodeLiteral
    status: Literal["ENABLED", "DISABLED"]


class MatrixRow(BaseModel):
    """One tenant row in the matrix. ``cells[]`` is position-aligned
    with E1's ``items[]`` ordering: ``cells[i].module_code`` is the
    same for every row in the response.

    ``status`` excludes ``TERMINATED`` because the matrix row set
    filters those out at the row-set level — they never appear here.
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    name: str
    tier: Literal["ENTERPRISE", "MID_MARKET", "SMB", "SINGLE_STORE"] | None = (
        Field(
            description=(
                "Commercial-tier classification. Nullable because the "
                "DDL allows tenants without a tier set yet (early "
                "onboarding state)."
            ),
        )
    )
    tier_label: str | None = Field(
        description=(
            "Display name resolved from ``lookups`` "
            "(``list_name='tenant_tier'``). ``null`` only when "
            "``tier`` itself is null. COALESCE-fallback to raw code "
            "otherwise."
        ),
    )
    status: Literal["ONBOARDING", "TRIAL", "ACTIVE", "SUSPENDED"] = Field(
        description=(
            "Lifecycle state. ``TERMINATED`` is structurally absent "
            "(filtered at the row-set level), so the literal vocabulary "
            "is narrower than the underlying ``tenant_status_enum``."
        ),
    )
    status_label: str = Field(
        description=(
            "Display name resolved from ``lookups`` "
            "(``list_name='tenant_status'``). Always present "
            "(COALESCE-fallback to raw code)."
        ),
    )
    cells: list[MatrixCell] = Field(
        description=(
            "Always 6 entries. Position-aligned with E1's "
            "``items[]`` and across rows: ``cells[i].module_code`` is "
            "the same for every row in this response."
        ),
    )


class MatrixResponse(BaseModel):
    """E2 response envelope: ``{items, pagination}`` per D-30."""

    model_config = ConfigDict(extra="forbid")

    items: list[MatrixRow]
    pagination: Pagination


# =============================================================================
# Step 6.15 write surface: ModuleAccessRead
# =============================================================================
#
# Returned by the enable / disable transition endpoints. Reflects the
# row state AFTER the transition (or AS-IS on idempotent no-op cells).
# Audit-actor IDs (``enabled_by_user_id``, ``disabled_by_user_id``,
# ``created_by_user_id``, ``updated_by_user_id``) are hidden per the
# H1 convention; they live in the DB row but never reach the wire.


class ModuleAccessRead(BaseModel):
    """Module access row state, returned by enable / disable endpoints.

    Maps from a ``TenantModuleAccess`` ORM row (``from_attributes=True``).
    ``extra="forbid"`` guards against accidental audit-actor leakage if
    the ORM model grows new columns.

    Field semantics (Step 6.15):

    - ``status='ENABLED'`` requires ``disabled_at is None`` (DDL
      ``ck_tenant_module_access_status_consistency``).
    - ``enabled_at`` is the start of the current ENABLED stint. The
      enable handler overwrites it on every DISABLED -> ENABLED flip
      per LD5; the disable handler preserves it as historical record.
    - ``disabled_at`` is the most-recent DISABLE event timestamp; NULL
      when the row is currently ENABLED.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(description="Row primary key.")
    tenant_id: UUID = Field(
        description="Tenant that owns this access row."
    )
    module: ModuleCode = Field(
        description="Module code (mirrors ``module_code_enum``)."
    )
    status: ModuleAccessStatus = Field(
        description="Current access state (``ENABLED`` or ``DISABLED``)."
    )
    enabled_at: datetime = Field(
        description=(
            "Start of the current ENABLED stint. Overwritten on every "
            "DISABLED -> ENABLED flip; preserved on ENABLED -> DISABLED "
            "as historical record."
        ),
    )
    disabled_at: datetime | None = Field(
        description=(
            "Most-recent DISABLE event timestamp. ``null`` whenever "
            "``status='ENABLED'`` (DDL ck constraint)."
        ),
    )
    created_at: datetime = Field(description="Row creation timestamp.")
    updated_at: datetime = Field(
        description="Row last-update timestamp (auto by BEFORE-UPDATE trigger)."
    )
