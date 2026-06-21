"""AuditLogsRepo : read-only access to the two audit tables (Step 6.16.3).

Two methods:

  - ``list(...)``: cursor-paginated list, audience-dispatched. PLATFORM
    callers see a UNION ALL across both tables; TENANT callers see only
    ``tenant_activity_audit_logs`` (RLS handles the tenant_id scoping
    automatically via the D-29 OR-branch policy).

  - ``get_by_id(...)``: probes both tables. ``tenant_activity_audit_logs``
    first (RLS filters by caller's tenant_id), then
    ``platform_activity_audit_logs`` (no RLS, accessible to PLATFORM
    only via the API gate). Returns None on miss; the router converts
    to 404 ``AUDIT_EVENT_NOT_FOUND`` per D-17.

Raw ``text()`` SQL with schema-qualified table names per CSD-03; the
``module_code_enum`` / ``audit_result_type_enum`` / ``actor_user_type_enum``
casts are wrapped in ``CAST(:x AS {schema}.<enum>)`` so the SQL works
regardless of session ``search_path``.

Cursor encoding (LD13): ``base64(json({"ts": <iso8601>, "id": <uuid>}))``.
The encoded payload is the LAST row of the current page; decoding it
re-anchors the next page at "rows strictly older than that anchor."

Default LIMIT 50, max 200 (LD5). The implementation queries ``limit + 1``
rows to detect ``has_more`` cheaply: if ``limit + 1`` rows come back,
drop the extra and set ``has_more=True``.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.config import get_settings
from admin_backend.errors import InvalidCursorError
from admin_backend.models.audit_log import AuditResultType
from admin_backend.models.tenant_user import ActorUserType


# ---------------------------------------------------------------------------
# Result row + list-call return shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditActivityDetailRow:
    """One audit row, fully projected.

    Carries all 19 stored columns (post-6.16.7) plus the synthesised
    ``scope`` string (``'TENANT'`` or ``'PLATFORM'``). The router-side
    mapper projects this to the 14-field ``AuditActivityListItem`` or
    the 19-field ``AuditActivityDetail`` for the wire response.

    Step 6.16.7 LD10 added 3 stored columns:
    ``actor_organization_name``, ``actor_roles``, ``resource_subtype``.
    """

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
    scope: str  # synthesised: 'PLATFORM' | 'TENANT'


@dataclass(frozen=True)
class ListResult:
    """List call return shape: page items + pagination metadata.

    ``items`` carries the (already-clipped to ``limit``) page rows.
    ``has_more`` reflects whether a ``limit + 1`` probe found an extra
    row beyond the page. ``next_cursor`` / ``prev_cursor`` are the
    encoded anchors for the surrounding pages (or None on edges).
    """

    items: list[AuditActivityDetailRow]
    next_cursor: str | None
    prev_cursor: str | None
    has_more: bool


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------


def _encode_cursor(timestamp: datetime, row_id: UUID) -> str:
    """Encode a ``(timestamp, id)`` anchor as an opaque base64 string.

    Format: ``base64(json({"ts": <iso8601>, "id": <uuid>}))``. Caller
    treats as opaque; decoder is in ``_decode_cursor``.
    """
    payload = json.dumps(
        {"ts": timestamp.isoformat(), "id": str(row_id)},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode an opaque cursor back into a ``(timestamp, id)`` anchor.

    Failure modes collapsed under ``InvalidCursorError`` (422
    ``INVALID_CURSOR``):
      - Malformed base64 / padding error.
      - Decoded payload is not valid JSON.
      - JSON payload lacks the required ``ts`` / ``id`` keys.
      - ``ts`` value cannot be parsed as ISO-8601 datetime.
      - ``id`` value cannot be parsed as UUID.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
    except Exception as exc:
        raise InvalidCursorError(
            f"base64 decode failed: {exc}",
            reason="base64_decode_failed",
        ) from exc

    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise InvalidCursorError(
            f"JSON decode failed: {exc}",
            reason="json_decode_failed",
        ) from exc

    if not isinstance(payload, dict) or "ts" not in payload or "id" not in payload:
        raise InvalidCursorError(
            "cursor payload missing 'ts' or 'id'",
            reason="payload_shape_invalid",
        )

    try:
        ts = datetime.fromisoformat(str(payload["ts"]))
    except Exception as exc:
        raise InvalidCursorError(
            f"ts field not valid ISO-8601: {exc}",
            reason="ts_format_invalid",
        ) from exc

    try:
        row_id = UUID(str(payload["id"]))
    except Exception as exc:
        raise InvalidCursorError(
            f"id field not valid UUID: {exc}",
            reason="id_format_invalid",
        ) from exc

    return ts, row_id


# ---------------------------------------------------------------------------
# Repo
# ---------------------------------------------------------------------------


class AuditLogsRepo:
    """Read-only repository for the two audit tables.

    Stateless singleton; mirrors the existing repo pattern (``LookupsRepo``,
    ``DashboardRepo``, ``PermissionMatrixRepo``).
    """

    async def list(
        self,
        session: AsyncSession,
        *,
        user_type: Literal["PLATFORM", "TENANT"],
        cursor: str | None = None,
        limit: int = 50,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        status: AuditResultType | None = None,
        tenant_id: UUID | None = None,
        scope: Literal["PLATFORM", "TENANT"] | None = None,
        search: str | None = None,
        resource_type: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> ListResult:
        """Cursor-paginated list of audit rows visible to the caller.

        Dispatches on ``user_type``:

          - TENANT: SELECT from ``tenant_activity_audit_logs`` only.
            RLS scopes by tenant_id automatically. ``tenant_id`` and
            ``scope`` filter parameters are silently ignored (LD14,
            LD15).

          - PLATFORM: UNION ALL across both tables with a synthesised
            ``scope`` column. Filters: ``tenant_id`` narrows the
            tenant branch (no rows on platform branch when set; the
            platform branch's tenant_id only populates on
            tenant-creation success rows, and the merged-view design
            does not promise to include those when scoping to a
            specific tenant); ``scope`` narrows the UNION to one
            branch.

        Common filters apply across both branches:
          - ``from_ts`` / ``to_ts`` : timestamp range.
          - ``status`` : ``result_type`` filter.
          - ``search`` : ``ILIKE %term%`` across 4 columns
            (``actor_display_name``, ``action_label``,
            ``resource_label``, ``tenant_name``) joined with OR per
            LD6.

        Cursor: when set, restricts to rows strictly OLDER than the
        anchor (``timestamp DESC, id DESC`` is the canonical order).
        Decoded via ``_decode_cursor``; malformed cursor surfaces as
        422 ``INVALID_CURSOR``.

        ``limit + 1`` rows fetched to detect ``has_more`` cheaply.
        """
        if cursor is not None:
            cursor_ts, cursor_id = _decode_cursor(cursor)
        else:
            cursor_ts, cursor_id = None, None

        schema = get_settings().db_schema
        params: dict[str, Any] = {
            "limit_plus_one": limit + 1,
            "cursor_ts": cursor_ts,
            "cursor_id": cursor_id,
            "from_ts": from_ts,
            "to_ts": to_ts,
            "status": status.value if status is not None else None,
            "tenant_id": tenant_id,
            "search": f"%{search}%" if search else None,
            "resource_type": resource_type,
            "actor_user_id": actor_user_id,
        }

        if user_type == "TENANT":
            # Tenant callers: tenant table only. RLS does the scoping.
            sql = self._build_tenant_only_sql(schema)
        else:
            # PLATFORM callers: UNION ALL across both tables, with the
            # ``scope`` filter optionally pruning one branch.
            sql = self._build_union_sql(schema, scope)

        result = await session.execute(sql, params)
        rows = list(result.mappings())

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = [self._row_to_dataclass(r) for r in page_rows]

        next_cursor: str | None = None
        if has_more and items:
            last = items[-1]
            next_cursor = _encode_cursor(last.timestamp, last.id)

        # First page has no prev_cursor; subsequent pages encode the
        # FIRST row of the current page as the prev_cursor so the
        # client could (in a future iteration) navigate backwards.
        # Sequential next-only is the v0 affordance per the design doc.
        prev_cursor: str | None = None
        if cursor is not None and items:
            first = items[0]
            prev_cursor = _encode_cursor(first.timestamp, first.id)

        return ListResult(
            items=items,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
        )

    async def get_by_id(
        self,
        session: AsyncSession,
        *,
        audit_row_id: UUID,
    ) -> AuditActivityDetailRow | None:
        """Probe both tables for a single audit row.

        Order: ``tenant_activity_audit_logs`` first, then
        ``platform_activity_audit_logs``. Returns None on miss; the
        router converts to 404 ``AUDIT_EVENT_NOT_FOUND``.

        RLS does the right thing on both probes:
          - Tenant caller probing a row in their own tenant: tenant
            table SELECT succeeds.
          - Tenant caller probing a row in another tenant: tenant
            table SELECT returns 0 rows (RLS); platform table SELECT
            returns the row if it exists, but the API gate prevents
            tenant callers from reaching this method at all (the gate
            is multi-audience but the read intent is "your audit
            events"). The 404 still fires for cross-tenant probes
            because the row's audience does not match the caller's
            access posture under the read principle.
          - Platform caller: both branches reachable; first match wins.
        """
        schema = get_settings().db_schema

        sql_tenant = text(
            f"""
            SELECT
                id, timestamp,
                tenant_id, tenant_name,
                actor_user_id, actor_user_type::text AS actor_user_type,
                actor_display_name,
                actor_organization_name, actor_roles,
                resource_type, resource_id, resource_label,
                resource_subtype,
                action, action_label,
                result_type::text AS result_type, result_label,
                request_id, details,
                'TENANT'::text AS scope
            FROM {schema}.tenant_activity_audit_logs
            WHERE id = :id
            LIMIT 1
            """
        )
        result = await session.execute(sql_tenant, {"id": audit_row_id})
        row = result.mappings().first()
        if row is not None:
            return self._row_to_dataclass(row)

        sql_platform = text(
            f"""
            SELECT
                id, timestamp,
                tenant_id, tenant_name,
                actor_user_id, actor_user_type::text AS actor_user_type,
                actor_display_name,
                actor_organization_name, actor_roles,
                resource_type, resource_id, resource_label,
                resource_subtype,
                action, action_label,
                result_type::text AS result_type, result_label,
                request_id, details,
                'PLATFORM'::text AS scope
            FROM {schema}.platform_activity_audit_logs
            WHERE id = :id
            LIMIT 1
            """
        )
        result = await session.execute(sql_platform, {"id": audit_row_id})
        row = result.mappings().first()
        if row is not None:
            return self._row_to_dataclass(row)

        return None

    # ------------------------------------------------------------------
    # Internal SQL builders
    # ------------------------------------------------------------------

    def _build_tenant_only_sql(self, schema: str) -> Any:
        """Compose the TENANT-callers SELECT against tenant_activity_audit_logs."""
        return text(
            f"""
            SELECT
                id, timestamp,
                tenant_id, tenant_name,
                actor_user_id, actor_user_type::text AS actor_user_type,
                actor_display_name,
                actor_organization_name, actor_roles,
                resource_type, resource_id, resource_label,
                resource_subtype,
                action, action_label,
                result_type::text AS result_type, result_label,
                request_id, details,
                'TENANT'::text AS scope
            FROM {schema}.tenant_activity_audit_logs
            WHERE
                (CAST(:from_ts AS timestamptz) IS NULL
                    OR timestamp >= CAST(:from_ts AS timestamptz))
                AND (CAST(:to_ts AS timestamptz) IS NULL
                    OR timestamp <= CAST(:to_ts AS timestamptz))
                AND (CAST(:status AS {schema}.audit_result_type_enum) IS NULL
                    OR result_type = CAST(:status AS {schema}.audit_result_type_enum))
                AND (CAST(:cursor_ts AS timestamptz) IS NULL
                    OR (timestamp, id) < (
                        CAST(:cursor_ts AS timestamptz),
                        CAST(:cursor_id AS uuid)
                    ))
                AND (
                    CAST(:search AS text) IS NULL
                    OR actor_display_name ILIKE :search
                    OR action_label ILIKE :search
                    OR resource_label ILIKE :search
                    OR tenant_name ILIKE :search
                )
                AND (CAST(:resource_type AS text) IS NULL
                    OR resource_type = CAST(:resource_type AS text))
                AND (CAST(:actor_user_id AS uuid) IS NULL
                    OR actor_user_id = CAST(:actor_user_id AS uuid))
            ORDER BY timestamp DESC, id DESC
            LIMIT :limit_plus_one
            """
        )

    def _build_union_sql(
        self,
        schema: str,
        scope: Literal["PLATFORM", "TENANT"] | None,
    ) -> Any:
        """Compose the PLATFORM-callers UNION ALL across both tables.

        ``scope`` filter prunes the UNION at SQL-shape time: when set,
        only the matching branch is emitted, so PG doesn't read rows
        from the other table at all.

        ``tenant_id`` filter applies only to the tenant branch (the
        platform branch's ``tenant_id`` populates only on
        tenant-creation success rows; the merged-view design does not
        promise to include those when scoping to a specific tenant).
        """
        emit_tenant_branch = scope is None or scope == "TENANT"
        emit_platform_branch = scope is None or scope == "PLATFORM"

        common_where = (
            "(CAST(:from_ts AS timestamptz) IS NULL "
            "    OR timestamp >= CAST(:from_ts AS timestamptz)) "
            "AND (CAST(:to_ts AS timestamptz) IS NULL "
            "    OR timestamp <= CAST(:to_ts AS timestamptz)) "
            f"AND (CAST(:status AS {schema}.audit_result_type_enum) IS NULL "
            f"    OR result_type = CAST(:status AS {schema}.audit_result_type_enum)) "
            "AND ("
            "    CAST(:search AS text) IS NULL "
            "    OR actor_display_name ILIKE :search "
            "    OR action_label ILIKE :search "
            "    OR resource_label ILIKE :search "
            "    OR tenant_name ILIKE :search"
            ") "
            "AND (CAST(:resource_type AS text) IS NULL "
            "    OR resource_type = CAST(:resource_type AS text)) "
            "AND (CAST(:actor_user_id AS uuid) IS NULL "
            "    OR actor_user_id = CAST(:actor_user_id AS uuid)) "
        )

        # Cursor predicate is applied AFTER the UNION (against the
        # merged stream's ordering), so the merge-then-filter shape
        # below pulls it into a CTE.
        tenant_branch_sql = f"""
            SELECT
                id, timestamp,
                tenant_id, tenant_name,
                actor_user_id, actor_user_type::text AS actor_user_type,
                actor_display_name,
                actor_organization_name, actor_roles,
                resource_type, resource_id, resource_label,
                resource_subtype,
                action, action_label,
                result_type::text AS result_type, result_label,
                request_id, details,
                'TENANT'::text AS scope
            FROM {schema}.tenant_activity_audit_logs
            WHERE {common_where}
              AND (CAST(:tenant_id AS uuid) IS NULL
                  OR tenant_id = CAST(:tenant_id AS uuid))
        """

        platform_branch_sql = f"""
            SELECT
                id, timestamp,
                tenant_id, tenant_name,
                actor_user_id, actor_user_type::text AS actor_user_type,
                actor_display_name,
                actor_organization_name, actor_roles,
                resource_type, resource_id, resource_label,
                resource_subtype,
                action, action_label,
                result_type::text AS result_type, result_label,
                request_id, details,
                'PLATFORM'::text AS scope
            FROM {schema}.platform_activity_audit_logs
            WHERE {common_where}
              AND (CAST(:tenant_id AS uuid) IS NULL
                  OR tenant_id = CAST(:tenant_id AS uuid))
        """

        if emit_tenant_branch and emit_platform_branch:
            inner = f"{tenant_branch_sql} UNION ALL {platform_branch_sql}"
        elif emit_tenant_branch:
            inner = tenant_branch_sql
        else:
            inner = platform_branch_sql

        # Cursor + ORDER BY + LIMIT applied at the outer level so the
        # merged stream is ordered consistently across both branches.
        return text(
            f"""
            WITH merged AS (
                {inner}
            )
            SELECT *
            FROM merged
            WHERE
                (CAST(:cursor_ts AS timestamptz) IS NULL
                    OR (timestamp, id) < (
                        CAST(:cursor_ts AS timestamptz),
                        CAST(:cursor_id AS uuid)
                    ))
            ORDER BY timestamp DESC, id DESC
            LIMIT :limit_plus_one
            """
        )

    def _row_to_dataclass(self, row: Any) -> AuditActivityDetailRow:
        """Project a SQLAlchemy row-mapping to the frozen dataclass.

        The SQL emits ``actor_user_type::text`` and ``result_type::text``
        so the values come back as strings; cast to the typed enums at
        the dataclass boundary so consumers (router, tests) get a
        consistent typed shape.
        """
        return AuditActivityDetailRow(
            id=row["id"],
            timestamp=row["timestamp"],
            tenant_id=row["tenant_id"],
            tenant_name=row["tenant_name"],
            actor_user_id=row["actor_user_id"],
            actor_user_type=ActorUserType(row["actor_user_type"]),
            actor_display_name=row["actor_display_name"],
            actor_organization_name=row["actor_organization_name"],
            actor_roles=row["actor_roles"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            resource_label=row["resource_label"],
            resource_subtype=row["resource_subtype"],
            action=row["action"],
            action_label=row["action_label"],
            result_type=AuditResultType(row["result_type"]),
            result_label=row["result_label"],
            request_id=row["request_id"],
            details=row["details"],
            scope=row["scope"],
        )
