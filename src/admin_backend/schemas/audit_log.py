"""Pydantic v2 schemas for the audit log read endpoints (Step 6.16.3).

Three response shapes plus the cursor pagination envelope:

- ``CursorPagination``: companion to the project's offset-based
  ``Pagination`` (lives in ``schemas/tenant.py``). The audit log
  subsystem departs from offset because audit rows grow unbounded;
  offset pagination degrades visibly past ~100k rows. See
  ``docs/architecture_audit_logs.md`` Read contract > Pagination for
  the rationale.
- ``AuditActivityListItem``: 8 summary columns rendered in the
  frontend feed (Layer 1).
- ``AuditActivitiesListResponse``: ``{items, pagination}`` per D-30
  envelope convention; pagination is the cursor flavour above.
- ``AuditActivityDetail``: 16 columns including the ``details`` JSONB
  payload; rendered on click in the drilldown (Layer 2).

All four models use ``ConfigDict(extra="forbid")`` as the drift
guard, same posture as the dashboard / module-access shapes
established at Steps 6.5 / 6.7.

Wire-format conventions follow D-28:
  - snake_case field names.
  - ISO-8601 datetimes with timezone offset (Pydantic v2 default
    when the source value is timezone-aware).
  - JSON ``null`` for nullable fields (no exclude_none).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from admin_backend.models.audit_log import AuditResultType
from admin_backend.models.tenant_user import ActorUserType


class CursorPagination(BaseModel):
    """Cursor pagination metadata block.

    Local to the audit endpoints at v0. If a second cursor-paginated
    endpoint ships in the future, promote to ``schemas/_common.py`` (or
    co-locate beside ``Pagination`` in ``schemas/tenant.py``).

    Encoding (LD13): ``base64(json({"ts": <iso8601>, "id": <uuid>}))``.
    The string is opaque to the client; treat as a token.

    ``has_more`` is the affordance the frontend uses to grey out the
    Next button on the last page. ``prev_cursor`` is ``None`` on the
    first page and on any page sequence that did not retain its prior
    cursor (v0 is sequential next-only; ``prev_cursor`` is populated as
    a future affordance).
    """

    model_config = ConfigDict(extra="forbid")

    next_cursor: str | None
    prev_cursor: str | None
    limit: int
    has_more: bool


class AuditActivityListItem(BaseModel):
    """One row in the Layer 1 feed (14 summary columns post-6.16.7).

    Step 6.16.7 LD10 added 6 additive fields for the audit list-view
    redesign: ``actor_organization_name``, ``actor_roles``, ``what``,
    ``resource_type``, ``resource_subtype``, ``result_type``. All
    existing 8 fields kept unchanged in shape.

    Deliberately omits ``details`` (JSONB) and ``request_id``. Those
    live on ``AuditActivityDetail`` for the drilldown view.

    The ``scope`` field is synthesised at query time, not stored:
    rows from ``platform_activity_audit_logs`` project ``'PLATFORM'``,
    rows from ``tenant_activity_audit_logs`` project ``'TENANT'``.
    Frontend uses this for visual disambiguation in the merged stream
    that PLATFORM callers see.

    ``tenant_name`` is the denormalised snapshot at write time per the
    design doc Schema section; NULL only on platform-table rows that
    do not carry tenant context (the non-tenant-creation rows).

    ``actor_organization_name`` is the frozen snapshot of the actor's
    organisation (tenant name for tenant actors, literal
    ``"Platform-Ithina"`` for platform actors) per LD6.

    ``actor_roles`` is the frozen snapshot of the actor's active role
    display names at write time, comma-separated (e.g.,
    ``"Owner, Promotions Assistant"``) per LD5. Rendered directly by
    the UI without further transformation. ``"-"`` on rows where the
    actor had no active roles or where resolution failed.

    ``what`` is the composed ``"<Type label>: <resource_label>"`` display
    string for the resource the action affected; backend composes at
    read time from ``resource_type`` + ``resource_subtype`` +
    ``resource_label`` via the LD12 type-label mapping. NULL
    ``resource_label`` (failure-path rows without a resource identity)
    renders as ``"<Type label>: -"``.

    ``resource_type``, ``resource_subtype``, and ``result_type`` are
    raw enum-shaped values surfaced to support frontend filtering and
    visual styling without round-tripping through the detail endpoint.
    ``resource_subtype`` is non-NULL only on ORG_NODE rows; NULL on
    every other resource_type and on pre-6.16.7 historical ORG_NODE
    rows.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    timestamp: datetime
    actor_display_name: str
    actor_organization_name: str
    actor_roles: str
    action_label: str
    what: str
    resource_label: str | None
    resource_type: str
    resource_subtype: str | None
    result_label: str
    result_type: AuditResultType
    scope: str
    tenant_name: str | None


class AuditActivitiesListResponse(BaseModel):
    """List endpoint response envelope: {items, pagination} per D-30.

    The pagination block is ``CursorPagination`` (not the project's
    standard offset ``Pagination``). See class docstring on
    ``CursorPagination`` for the rationale.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[AuditActivityListItem]
    pagination: CursorPagination


class AuditActivityDetail(BaseModel):
    """Detail response shape: the full 19-column audit row (post-6.16.7).

    Step 6.16.7 LD10 added 3 stored columns visible here:
    ``actor_organization_name``, ``actor_roles``, ``resource_subtype``.
    All other fields keep their pre-6.16.7 shape.

    Includes the ``details`` JSONB payload, the ``request_id``, and
    structured codes (``action`` / ``resource_type`` / ``result_type``)
    in addition to everything on ``AuditActivityListItem``. Rendered
    in the Layer 2 drilldown panel.

    The ``details`` payload shape varies per ``result_type`` (see the
    design doc Emission contract > Failure-row payload shapes table).
    The schema accepts any JSON object; the frontend renders per its
    own per-result_type templates.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    timestamp: datetime
    tenant_id: UUID | None
    tenant_name: str | None
    actor_user_id: UUID
    actor_user_type: ActorUserType
    actor_display_name: str
    actor_organization_name: str
    actor_roles: str
    resource_type: str
    resource_id: UUID | None
    resource_label: str | None
    resource_subtype: str | None
    action: str
    action_label: str
    result_type: AuditResultType
    result_label: str
    request_id: UUID
    details: dict[str, Any]
