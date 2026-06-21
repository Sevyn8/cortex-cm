"""Pydantic v2 schemas for the Store resource.

Read schemas (Step 6.17.2):

- ``StoreListItem`` — slim per-row shape for the list endpoint. Wire
  size is roughly half ``StoreDetail``; deliberately omits address,
  lat/long, currency, tax_treatment, timezone, org_node_id, updated_at,
  closed_at. All available on detail.

- ``StoreListResponse`` — list envelope ``{items, pagination}`` per
  D-30.

- ``StoreDetail`` — 17 fields: all 22 DDL columns minus the 6 Pattern
  (b) audit-actor columns (``*_by_user_id``, ``*_by_user_type``), plus
  the joined ``tenant_name`` label.

Write schemas (Step 6.17.3):

- ``StoreCreateRequest`` — POST body. 7 required fields + 4 optional.
  ``extra="forbid"`` rejects ``status``, ``id``, audit columns,
  ``closed_*``. Server forces ``status='OPENING'`` via DDL default
  (locked decision 8).

- ``StorePatchRequest`` — PATCH body. 9 mutable fields; all optional.
  ``extra="forbid"`` rejects ``status``, ``tenant_id``, ``org_node_id``
  (immutability per locked decision 3), audit columns, ``closed_*``.

NUMERIC fields (``latitude``, ``longitude``) serialise to JSON as
strings via ``field_serializer(when_used="json")`` to preserve
precision in JS clients (Q11 / D-28). ``model_dump()`` in Python mode
keeps the ``Decimal``. Request-side string-to-Decimal coercion is
Pydantic's default; no custom validator needed.
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from admin_backend.models.store import StoreStatus, TaxTreatment
from admin_backend.schemas.tenant import Pagination


class StoreListItem(BaseModel):
    """Slim per-row shape for ``GET /api/v1/stores``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    tenant_name: str
    name: str
    store_code: str | None
    country: str
    status: StoreStatus
    created_at: datetime


class StoreListResponse(BaseModel):
    """List endpoint response envelope: ``{items, pagination}`` per D-30."""

    model_config = ConfigDict(extra="forbid")

    items: list[StoreListItem]
    pagination: Pagination


class StoreDetail(BaseModel):
    """Detail response shape for ``GET /api/v1/stores/{store_id}``.

    17 fields. Audit-actor IDs (Pattern (b) per D-13) deliberately
    hidden; ``org_node_id`` exposed as a bare UUID column (locked
    decision 8) — frontend resolves the name via ``/org-tree`` if
    needed.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    tenant_name: str
    org_node_id: UUID | None
    name: str
    store_code: str | None
    country: str
    timezone: str
    address: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    currency: str
    tax_treatment: TaxTreatment
    status: StoreStatus
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    @field_serializer("latitude", "longitude", when_used="json")
    def _serialise_coord(self, v: Decimal | None) -> str | None:
        """Serialise lat/long to JSON as strings (D-28 / Q11).

        Stored as NUMERIC(9, 6); a JS Number would round at the 7th
        decimal place, defeating storage precision. ``when_used="json"``
        keeps ``model_dump()`` (Python mode) returning a ``Decimal``.
        """
        return str(v) if v is not None else None


# =============================================================================
# Step 6.17.3 write schemas: StoreCreateRequest, StorePatchRequest.
#
# Both ``extra="forbid"``. Server-managed fields (``id``, ``status``,
# ``created_at``, ``updated_at``, ``closed_*``, all audit-actor
# columns) are never accepted on the wire.
#
# Locked decisions honoured here:
#   - LD2: ``store_code`` and ``tax_treatment`` required at schema layer
#     despite DDL nullability. NOT NULL migration deferred (FN-AB).
#   - LD3: ``org_node_id`` rejected on PATCH (immutable). POST allows it
#     as optional.
#   - LD8: ``status`` rejected on POST (server-forced to DDL default
#     OPENING).
#
# Schema validators honour DDL CHECK constraints on ``core.stores``:
#   - ``ck_stores_name_length`` (1..200)
#   - ``ck_stores_country_format`` (length 2..100; DDL doesn't restrict
#     character set, so neither do we — operator decision 2026-05-18)
#   - ``ck_stores_currency_format`` (regex ``^[A-Z]{3}$``)
#   - ``ck_stores_latitude_range`` (-90..90)
#   - ``ck_stores_longitude_range`` (-180..180)
# =============================================================================


class StoreCreateRequest(BaseModel):
    """Request shape for ``POST /api/v1/stores``.

    Multi-audience (PLATFORM and TENANT OWNER per locked decision 1).
    ``tenant_id`` in body is verified against the caller's RLS-bound
    session: a TENANT JWT supplying another tenant's id finds the
    cross-tenant target invisible and surfaces as 404
    ``TENANT_NOT_FOUND`` from the create path's preconditions.

    ``status`` is not accepted on the wire; the DDL default (currently
    ``ACTIVE``; product intent is ``OPENING``, deferred to a future
    migration) fires server-side (locked decision 8 — "via DDL default").

    Step 6.21.2: ``parent_org_node_id`` (REQUIRED) names the parent
    under which the server creates the paired STORE-type org_node. The
    server provisions the org_node + the store row atomically (one
    transaction). The pre-6.21.2 ``org_node_id`` field is gone; sending
    it produces 422 ``extra_forbidden`` via ``extra="forbid"``.
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    name: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=2, max_length=100)
    timezone: str = Field(min_length=1, max_length=50)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    store_code: str = Field(min_length=1, max_length=50)
    tax_treatment: TaxTreatment

    parent_org_node_id: UUID = Field(
        description=(
            "UUID of the org_node under which this store should be "
            "anchored in the org tree. Must be a non-STORE node in the "
            "same tenant. Use ``tenant_root_id`` from GET /org-tree to "
            "anchor directly under the tenant root."
        ),
    )
    address: str | None = None
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)


class StorePatchRequest(BaseModel):
    """Request shape for ``PATCH /api/v1/stores/{store_id}``.

    All fields optional; the handler raises ``EmptyPatchError`` (422) if
    ``model_dump(exclude_unset=True)`` produces an empty dict (locked
    decision 4).

    Step 6.21.2: ``parent_org_node_id`` (optional; omitted = no change)
    reparents the paired STORE-type org_node. The store's own
    ``org_node_id`` (its slot) is unaffected; only the slot's
    ``parent_id`` is updated. Cascade behaviour for ``name`` and
    ``store_code`` also lands at this step: changes propagate to the
    paired org_node atomically.

    Excluded fields (rejected at schema layer via ``extra="forbid"``):
      - ``tenant_id``: a store cannot change tenancy.
      - ``org_node_id``: immutable. The store's link to its paired
        org_node is set once at create-time and never changes; the
        paired org_node's ``parent_id`` is the mutable concept (via
        ``parent_org_node_id`` here).
      - ``status``: lifecycle transitions land at
        ``POST /api/v1/stores/{store_id}/set-status`` (Step 6.17.4);
        not via PATCH.
      - ``id``, ``created_at``, ``updated_at``, ``*_by_user_id``,
        ``*_by_user_type``, ``closed_*``: server-managed.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    store_code: str | None = Field(default=None, min_length=1, max_length=50)
    country: str | None = Field(default=None, min_length=2, max_length=100)
    timezone: str | None = Field(default=None, min_length=1, max_length=50)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    tax_treatment: TaxTreatment | None = None
    address: str | None = None
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)
    parent_org_node_id: UUID | None = Field(
        default=None,
        description=(
            "If set, move the store under a new parent org_node. Must "
            "be a non-STORE node in the same tenant. The store's own "
            "org_node_id (its slot) is unaffected; only the slot's "
            "parent_id changes."
        ),
    )


# =============================================================================
# Step 6.17.4 write schema: StoreSetStatusRequest.
#
# Body for ``POST /api/v1/stores/{store_id}/set-status``. State-transition
# endpoint with 9-cell liberal matrix (per the locked decision in Step
# 6.17.4): all transitions allowed except ``*->OPENING`` (3 rejected
# cells). Same-state returns 409 ``INVALID_STATE_TRANSITION``
# (mirrors tenants' ``allowed_sources`` convention: target NOT in own
# allowed-sources set).
#
# ``reason`` is forward-compatible with Step 6.2's ``audit_log`` write
# integration: accepted at the schema layer here and silently dropped
# at the repo layer until audit_log ships, at which point the handler
# gains an ``audit_log_repo.write(...reason=...)`` call. No API change
# required when audit_log lands.
# =============================================================================


class StoreSetStatusRequest(BaseModel):
    """Request shape for ``POST /api/v1/stores/{store_id}/set-status``.

    Multi-audience (LD9 — same gate as ``StorePatchRequest``):
    PLATFORM via the GLOBAL->TENANT cascade; TENANT OWNER via the
    direct ``.TENANT`` grant.

    ``reason`` is forward-compatible: accepted on the wire now,
    consumed by Pydantic validation, then silently dropped at the
    repo layer until Step 6.2's ``audit_log`` integration lands.
    The API contract does not change when audit_log ships.
    """

    model_config = ConfigDict(extra="forbid")

    target_status: StoreStatus
    reason: str | None = None
