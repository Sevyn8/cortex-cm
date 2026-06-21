"""Audit log read endpoints (Step 6.16.3).

Two GET handlers under ``/audit/activities``; the parent ``/api/v1``
prefix comes from ``settings.api_prefix`` at ``app.include_router``
time in ``main.py``.

Multi-audience per the v0 auth model: both PLATFORM and TENANT JWTs
accepted. Audience-driven branching happens inside the repo (LD1):

  - TENANT callers see only ``tenant_activity_audit_logs``; RLS
    scopes by tenant_id automatically (D-29 OR-branch policy).
  - PLATFORM callers see a merged UNION ALL stream across both
    audit tables, with the synthesised ``scope`` column distinguishing
    rows from each branch.

Gate: ``ADMIN.AUDIT_LOG.VIEW.TENANT`` on both endpoints. SUPER_ADMIN +
PLATFORM_ADMIN + SUPPORT_ADMIN pass via the GLOBAL→TENANT scope
cascade (they hold ``.GLOBAL`` per the Step 6.16.3 operator catalogue
update); TENANT OWNER (and other tenant roles holding ``.VIEW.TENANT``)
pass via the direct grant. No anchor_dep (LD8): both endpoints are
list/detail reads scoped by caller audience + RLS; no per-row anchor
cascade applies.

Detail endpoint cross-tenant probe by a TENANT JWT surfaces as 404
``AUDIT_EVENT_NOT_FOUND`` (RLS-as-404 per D-17).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.audit.emit import _label_for_resource_type
from admin_backend.auth.context import AuthContext
from admin_backend.auth.permissions import require
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.errors import AuditEventNotFoundError
from admin_backend.models.audit_log import AuditResultType
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.repositories.audit_logs import (
    AuditActivityDetailRow,
    AuditLogsRepo,
)
from admin_backend.schemas.audit_log import (
    AuditActivitiesListResponse,
    AuditActivityDetail,
    AuditActivityListItem,
    CursorPagination,
)


router = APIRouter(prefix="/audit", tags=["audit"])


# Stateless instance reused across requests.
_repo = AuditLogsRepo()


# Conservative cursor shape guard at the router boundary. Cursors are
# base64-encoded JSON; the URL-safe base64 alphabet is
# ``[A-Za-z0-9_-]`` plus optional ``=`` padding. Lengths fall in the
# 50-200 byte range for the encoded payload shape this code produces;
# 1024 chars is a generous cap that still rejects pathologically large
# inputs before the repo tries to decode them. The repo's
# ``InvalidCursorError`` is the authoritative decode failure path; this
# regex is purely a cheap-and-early reject for obviously-malformed
# strings.
_CURSOR_PATTERN = r"^[A-Za-z0-9_=-]+$"


def _compose_what(row: AuditActivityDetailRow) -> str:
    """Compose the ``what`` display string per LD11.

    Format: ``"<Type label>: <resource_label>"``. NULL ``resource_label``
    (failure-path rows without a resource identity) renders as
    ``"<Type label>: -"``. Type label dispatches on
    ``(resource_type, resource_subtype)`` via the LD12 mapping.
    """
    type_label = _label_for_resource_type(
        row.resource_type, row.resource_subtype
    )
    name = row.resource_label if row.resource_label is not None else "-"
    return f"{type_label}: {name}"


def _list_item_from_row(row: AuditActivityDetailRow) -> AuditActivityListItem:
    """Map the repo's 19+scope row to the 14-field wire list item.

    Step 6.16.7 LD10 / LD11 : 6 new fields populated. ``what`` is
    composed at read time from ``resource_type`` + ``resource_subtype``
    + ``resource_label`` via the LD12 helper.
    """
    return AuditActivityListItem(
        id=row.id,
        timestamp=row.timestamp,
        actor_display_name=row.actor_display_name,
        actor_organization_name=row.actor_organization_name,
        actor_roles=row.actor_roles,
        action_label=row.action_label,
        what=_compose_what(row),
        resource_label=row.resource_label,
        resource_type=row.resource_type,
        resource_subtype=row.resource_subtype,
        result_label=row.result_label,
        result_type=row.result_type,
        scope=row.scope,
        tenant_name=row.tenant_name,
    )


def _detail_from_row(row: AuditActivityDetailRow) -> AuditActivityDetail:
    """Map the repo's 19+scope row to the 19-field wire detail shape."""
    return AuditActivityDetail(
        id=row.id,
        timestamp=row.timestamp,
        tenant_id=row.tenant_id,
        tenant_name=row.tenant_name,
        actor_user_id=row.actor_user_id,
        actor_user_type=row.actor_user_type,
        actor_display_name=row.actor_display_name,
        actor_organization_name=row.actor_organization_name,
        actor_roles=row.actor_roles,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        resource_label=row.resource_label,
        resource_subtype=row.resource_subtype,
        action=row.action,
        action_label=row.action_label,
        result_type=row.result_type,
        result_label=row.result_label,
        request_id=row.request_id,
        details=row.details,
    )


@router.get("/activities", response_model=AuditActivitiesListResponse)
async def list_audit_activities(
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.AUDIT_LOG,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
    cursor: str | None = Query(
        None,
        pattern=_CURSOR_PATTERN,
        max_length=1024,
        description=(
            "Opaque pagination cursor (base64-encoded). Pass the "
            "``next_cursor`` from a previous response to fetch the next "
            "page. Omit on the first page. Malformed cursors return "
            "422 ``INVALID_CURSOR``."
        ),
    ),
    limit: int = Query(
        50,
        ge=1,
        le=200,
        description=(
            "Number of rows per page. Default 50, max 200. The repo "
            "fetches ``limit + 1`` to detect ``has_more`` without an "
            "extra count query."
        ),
    ),
    from_ts: datetime | None = Query(
        None,
        alias="from",
        description=(
            "Inclusive lower bound on ``timestamp`` (ISO-8601 with "
            "timezone offset)."
        ),
    ),
    to_ts: datetime | None = Query(
        None,
        alias="to",
        description=(
            "Inclusive upper bound on ``timestamp`` (ISO-8601 with "
            "timezone offset)."
        ),
    ),
    status_: AuditResultType | None = Query(
        None,
        alias="status",
        description=(
            "Filter by ``result_type``. One of: SUCCESS, "
            "PERMISSION_DENIED, VALIDATION_FAILED, CONFLICT, "
            "INTEGRITY_VIOLATION, INTERNAL_ERROR."
        ),
    ),
    tenant_id: UUID | None = Query(
        None,
        description=(
            "Narrow the merged stream to one tenant's rows. PLATFORM "
            "callers only; TENANT callers' filter is silently ignored "
            "(RLS already scopes their visibility to own-tenant)."
        ),
    ),
    scope: Literal["PLATFORM", "TENANT"] | None = Query(
        None,
        description=(
            "Narrow the merged stream to one source branch. PLATFORM "
            "callers only; TENANT callers' filter is silently ignored "
            "(they never see the PLATFORM branch)."
        ),
    ),
    search: str | None = Query(
        None,
        max_length=200,
        description=(
            "Case-insensitive substring match across "
            "``actor_display_name``, ``action_label``, "
            "``resource_label``, and ``tenant_name``. Composed with "
            "other filters via AND."
        ),
    ),
    resource_type: str | None = Query(
        None,
        max_length=64,
        description=(
            "Filter rows by ``resource_type`` (open string vocabulary). "
            "Current values in use: ``TENANT``, ``TENANT_USER``, "
            "``ROLE``, ``MODULE_ACCESS``, ``ORG_NODE``, ``STORE``. "
            "Unknown values return 0 rows (no 422). AND-composed with "
            "other filters; applied to both UNION branches for "
            "PLATFORM callers."
        ),
    ),
    actor_user_id: UUID | None = Query(
        None,
        description=(
            "Filter rows by ``actor_user_id`` (the actor who performed "
            "the audited action). AND-composed with other filters; "
            "applied to both UNION branches for PLATFORM callers. "
            "TENANT callers receive the filter naturally; RLS scoping "
            "ensures they only see audit rows from their own tenant. "
            "Unknown UUIDs return 0 rows (no 422). No companion "
            "``actor_user_type`` parameter: ``platform_users.id`` and "
            "``tenant_users.id`` use the same ``uuidv7()`` DDL default "
            "and are globally unique, so ``actor_user_id`` alone is "
            "fully selective."
        ),
    ),
) -> Any:
    """List audit activities visible to the caller, cursor-paginated.

    PLATFORM sees a merged stream across both audit tables; TENANT
    sees only own-tenant rows from ``tenant_activity_audit_logs``
    (RLS-scoped). Newest-first only.

    Cursor pagination departs from the project's standard offset
    pattern (used by tenants / stores / tenant-users); the audit log
    is the only subsystem with structurally unbounded growth. See
    ``docs/architecture_audit_logs.md`` Read contract > Pagination for
    the rationale.
    """
    if search is not None:
        trimmed = search.strip()
        search = trimmed if trimmed else None

    result = await _repo.list(
        session,
        user_type=auth.user_type,
        cursor=cursor,
        limit=limit,
        from_ts=from_ts,
        to_ts=to_ts,
        status=status_,
        tenant_id=tenant_id,
        scope=scope,
        search=search,
        resource_type=resource_type,
        actor_user_id=actor_user_id,
    )

    items = [_list_item_from_row(r) for r in result.items]
    return AuditActivitiesListResponse(
        items=items,
        pagination=CursorPagination(
            next_cursor=result.next_cursor,
            prev_cursor=result.prev_cursor,
            limit=limit,
            has_more=result.has_more,
        ),
    )


@router.get("/activities/{audit_row_id}", response_model=AuditActivityDetail)
async def get_audit_activity(
    audit_row_id: UUID,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.AUDIT_LOG,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Return the full 16-column detail shape for a single audit row.

    Probes both tables; 404 ``AUDIT_EVENT_NOT_FOUND`` on miss. Per D-17
    the handler does not distinguish "genuinely missing" from
    "RLS-filtered cross-tenant probe"; both produce the same body
    shape.
    """
    row = await _repo.get_by_id(session, audit_row_id=audit_row_id)
    if row is None:
        raise AuditEventNotFoundError(
            f"Audit row {audit_row_id} not visible to this session",
            audit_row_id=str(audit_row_id),
        )
    # TENANT callers' read principle: they never see PLATFORM-scope
    # rows. The repo's probe order is tenant-first, then platform; if
    # we got here with a PLATFORM-scope row under a TENANT caller, the
    # row was found in the platform table (which has no RLS). Surface
    # as 404 to match the cross-tenant tenant-table probe behaviour.
    if row.scope == "PLATFORM" and auth.user_type == "TENANT":
        raise AuditEventNotFoundError(
            f"Audit row {audit_row_id} not visible to this session",
            audit_row_id=str(audit_row_id),
        )
    return _detail_from_row(row)
