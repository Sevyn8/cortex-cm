"""Pydantic v2 read schema for the Tenant resource.

Applies the provisional API-contract defaults locked at Step 3.1
(``docs/api-contract.md`` is still in template state, pending the
Step 2.0 sync with the frontend developer):

  - Q1 (response naming): snake_case.
  - Q4 (datetimes): ISO 8601 with timezone offset (Pydantic v2 default
    for ``datetime`` with timezone).
  - Q7 (nulls): nullable fields are emitted explicitly as JSON ``null``,
    not omitted.
  - Q11 (NUMERIC): monetary fields serialise to JSON as strings, to
    preserve decimal precision in JS clients.

Audit-actor IDs (``*_by_user_id``) are deliberately NOT exposed: they
are internal lineage. The frontend renders lifecycle state from
``suspended_at`` / ``terminated_at`` alone.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from admin_backend.models.tenant import (
    TenantIndustry,
    TenantRegion,
    TenantStatus,
    TenantTier,
)
from admin_backend.models.tenant_module_access import ModuleCode


class TenantRead(BaseModel):
    """Tenant entity as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    display_code: str | None
    country: str | None
    region: TenantRegion
    tier: TenantTier | None
    industry: TenantIndustry | None
    monthly_revenue_usd: Decimal | None
    monthly_revenue_as_of_date: date | None
    number_of_stores: int | None
    number_of_stores_as_of_date: date | None
    primary_contact_name: str | None
    contact_email: str | None
    status: TenantStatus
    created_at: datetime
    updated_at: datetime
    suspended_at: datetime | None
    terminated_at: datetime | None

    @field_serializer("monthly_revenue_usd", when_used="json")
    def _serialise_money(self, v: Decimal | None) -> str | None:
        # NUMERIC -> string in JSON output (Q11): preserves trailing
        # zeros and avoids JS Number-precision loss. ``when_used="json"``
        # leaves ``model_dump()`` (Python mode) returning a Decimal.
        return str(v) if v is not None else None


# =============================================================================
# Step 3.3 schemas: list / stats / detail responses + Module + Pagination.
#
# Wrapping convention (D-30): list endpoints wrap as `{items, pagination}`;
# single-object endpoints return the object directly with no envelope. Field
# semantics are append-only (D-31): once a field's meaning ships, that
# meaning is frozen for the lifetime of the API version.
# =============================================================================


class Module(BaseModel):
    """Module entitlement entry. Reused across list and detail responses."""

    code: str
    name: str


class Pagination(BaseModel):
    """Pagination metadata block. Carried only by list responses."""

    total: int
    offset: int
    limit: int


class TenantsListItem(BaseModel):
    """Per-card response shape for the tenants list endpoint.

    13 fields, fully flat. Deliberately omits monthly_revenue_as_of_date,
    number_of_stores (the self-reported snapshot), number_of_stores_as_of_date,
    primary_contact_name, contact_email, suspended_at, terminated_at — all
    available on the detail endpoint.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    display_code: str | None
    country: str | None
    region: TenantRegion
    industry: TenantIndustry | None
    tier: TenantTier | None
    status: TenantStatus
    monthly_revenue_usd: Decimal | None
    num_stores: int
    num_users_active: int
    modules: list[Module]
    created_at: datetime
    updated_at: datetime

    @field_serializer("monthly_revenue_usd", when_used="json")
    def _serialise_money(self, v: Decimal | None) -> str | None:
        # Same NUMERIC-as-string shape as TenantRead per D-28 / Q11.
        return str(v) if v is not None else None


# =============================================================================
# Step 6.11.1 write schemas: TenantCreateRequest, TenantPatchRequest.
#
# Both ``extra="forbid"``. Server-side fields (``id``, ``status``,
# ``created_at``, ``updated_at``, ``suspended_*``, ``terminated_*``) are
# never accepted via the wire. ``status`` rejection on create + patch is
# load-bearing: status transitions go through the dedicated
# ``/suspend`` and ``/activate`` endpoints.
#
# Validators below honour the DDL CHECK constraints documented in
# ``db/raw_ddl/Ithina_postgres_SQL_DDL_tenants_v3.sql``:
#   - contact_email must be lowercase (ck_tenants_contact_email_lowercase)
#   - monthly_revenue_usd / monthly_revenue_as_of_date are both-or-neither
#     (ck_tenants_monthly_revenue_as_of_consistency)
#   - number_of_stores / number_of_stores_as_of_date are both-or-neither
#     (ck_tenants_number_of_stores_as_of_consistency)
#
# Deviation from the prompt sketch: ``number_of_stores_as_of_date`` is
# REQUIRED on create (not ``date | None = None``). ``number_of_stores``
# is required + ``ge=1``; the DB CHECK then mandates the as-of date.
# Promoting to required at the schema level surfaces the contract
# cleanly as 422 instead of relying on DB-CHECK -> 500.
# =============================================================================


class TenantCreateRequest(BaseModel):
    """Request shape for ``POST /api/v1/tenants``.

    ``status`` is server-forced to ``TRIAL`` (locked decision 3); not
    accepted on the wire. ``id`` is server-generated via DB
    ``DEFAULT uuidv7()``. Audit-actor columns are populated from
    ``auth.user_id`` in the repo.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    region: TenantRegion
    tier: TenantTier
    industry: TenantIndustry
    country: str = Field(min_length=2, max_length=100)
    primary_contact_name: str = Field(min_length=1, max_length=200)
    contact_email: EmailStr
    number_of_stores: int = Field(ge=1)
    number_of_stores_as_of_date: date
    display_code: str | None = Field(default=None, max_length=64)
    monthly_revenue_usd: Decimal | None = None
    monthly_revenue_as_of_date: date | None = None
    modules_enabled: list[ModuleCode] = Field(
        default_factory=list, validate_default=True
    )

    @field_validator("contact_email", mode="after")
    @classmethod
    def _lowercase_email(cls, v: str) -> str:
        """Lowercase email to satisfy ``ck_tenants_contact_email_lowercase``.

        Pydantic's ``EmailStr`` normalises the domain part but not the
        local part. The DB CHECK rejects mixed-case emails; lowering
        here keeps the rejection path off the DB.
        """
        return v.lower()

    @field_validator("modules_enabled", mode="after")
    @classmethod
    def _force_include_admin(cls, v: list[ModuleCode]) -> list[ModuleCode]:
        """Dedupe preserving order; force-include ADMIN (locked decision 4).

        Every tenant gets ADMIN; the frontend can request other modules.
        Dedupe runs first so a caller passing ``[ADMIN, ADMIN]`` doesn't
        bypass the unique-(tenant, module) constraint at INSERT time.
        """
        seen: set[ModuleCode] = set()
        result: list[ModuleCode] = []
        for m in v:
            if m not in seen:
                seen.add(m)
                result.append(m)
        if ModuleCode.ADMIN not in seen:
            result.append(ModuleCode.ADMIN)
        return result

    @model_validator(mode="after")
    def _monthly_revenue_pair_consistency(self) -> Self:
        """Enforce monthly_revenue_usd / monthly_revenue_as_of_date both-or-neither.

        Mirrors DDL ``ck_tenants_monthly_revenue_as_of_consistency``.
        """
        usd = self.monthly_revenue_usd
        as_of = self.monthly_revenue_as_of_date
        if (usd is None) != (as_of is None):
            raise ValueError(
                "monthly_revenue_usd and monthly_revenue_as_of_date "
                "must be both set or both omitted"
            )
        if usd is not None and usd < 0:
            raise ValueError("monthly_revenue_usd must be >= 0")
        return self


class TenantPatchRequest(BaseModel):
    """Request shape for ``PATCH /api/v1/tenants/{tenant_id}``.

    All fields optional; the handler raises ``EmptyPatchError`` (422) if
    ``model_dump(exclude_unset=True)`` produces an empty dict.

    Excluded fields (rejected at schema layer via ``extra="forbid"``):
      - ``region``: immutable post-create (region pin per D-05).
      - ``status``: transitions go through ``/suspend`` and ``/activate``.
      - ``id``, ``created_at``, ``updated_at``, ``*_by_user_id``,
        ``suspended_*``, ``terminated_*``: server-managed.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    display_code: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=100)
    tier: TenantTier | None = None
    industry: TenantIndustry | None = None
    primary_contact_name: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    contact_email: EmailStr | None = None
    monthly_revenue_usd: Decimal | None = None
    monthly_revenue_as_of_date: date | None = None
    number_of_stores: int | None = Field(default=None, ge=1)
    number_of_stores_as_of_date: date | None = None

    @field_validator("contact_email", mode="after")
    @classmethod
    def _lowercase_email(cls, v: str | None) -> str | None:
        """Lowercase email to satisfy ``ck_tenants_contact_email_lowercase``.

        Same rationale as ``TenantCreateRequest._lowercase_email``.
        """
        return v.lower() if v is not None else None


class TenantsListResponse(BaseModel):
    """List endpoint response envelope: {items, pagination} per D-30."""

    items: list[TenantsListItem]
    pagination: Pagination


class TenantsStatsResponse(BaseModel):
    """Header summary scalars. Both RLS-filtered to caller's visibility."""

    total_tenants: int
    total_stores: int


class TenantDetail(BaseModel):
    """Detail response shape: TenantRead's projection plus three live
    aggregates and the modules list. 21 fields, fully flat.

    No inheritance from ``TenantRead`` — duplicating the field set keeps
    the two response shapes structurally independent so a future change
    to one doesn't surprise the other (e.g., adding a TenantRead-only
    field shouldn't auto-leak into the detail response).
    """

    model_config = ConfigDict(from_attributes=True)

    # ---- TenantRead fields, copied verbatim ----
    id: UUID
    name: str
    display_code: str | None
    country: str | None
    region: TenantRegion
    tier: TenantTier | None
    industry: TenantIndustry | None
    monthly_revenue_usd: Decimal | None
    monthly_revenue_as_of_date: date | None
    number_of_stores: int | None
    number_of_stores_as_of_date: date | None
    primary_contact_name: str | None
    contact_email: str | None
    status: TenantStatus
    created_at: datetime
    updated_at: datetime
    suspended_at: datetime | None
    terminated_at: datetime | None

    # ---- Aggregates added at Step 3.3 ----
    num_stores: int
    num_users_active: int
    modules: list[Module]

    @field_serializer("monthly_revenue_usd", when_used="json")
    def _serialise_money(self, v: Decimal | None) -> str | None:
        # Same NUMERIC-as-string shape as TenantRead per D-28 / Q11.
        return str(v) if v is not None else None
