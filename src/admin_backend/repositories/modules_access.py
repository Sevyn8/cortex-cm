"""ModulesAccessRepo — read-only data access for the Module Access endpoints (Step 6.7).

Two methods, both RLS-bound via session GUCs (no ``tenant_id``
parameter — D-24 source-binding):

  - ``list_modules_with_aggregates(session)`` runs the locked
    ``/modules`` SQL: lookups-driven row set CROSS JOINed with a
    single-row total, LEFT JOINed against a per-module enabled-count
    aggregate from ``tenant_module_access`` joined to ``tenants``
    (status IN ACTIVE / TRIAL).
  - ``list_matrix(session, *, sort, tier, status, q, limit, offset)``
    runs the ``/matrix`` query in three stages: (1) page of tenants
    with sort/filter/pagination + label JOINs, (2) cells grid for the
    page via tenants × modules CROSS JOIN LEFT JOIN
    ``tenant_module_access``, (3) total count for pagination.

Schema-qualified ``text()`` SQL per the "Note on raw text() SQL"
convention (Step 6.5.1). The two consumers of `lookups` here mirror
``permission_matrix.py``'s posture: per-call interpolation of
``get_settings().db_schema`` into f-strings; injection-safe because the
Settings layer field-validates ``db_schema`` as a Postgres identifier.

Sort key validation reuses the shared ``InvalidSortKeyError`` from
``repositories/_errors.py`` (Step 5.2). The router catches and re-raises
as the shared ``InvalidSortKeyClientError`` (400).

Step 6.6 sort-stability decision applies: module ordering is anchored
on ``lookups.display_order`` (decoupled from enum ordinal), so adding
or reordering enum values doesn't perturb the rendered sequence.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.audit.emit import (
    build_success_details_for_update,
    emit_audit_event,
)
from admin_backend.auth.context import AuthContext
from admin_backend.config import get_settings
from admin_backend.models.audit_log import AuditResultType
from admin_backend.models.tenant_module_access import (
    ModuleAccessStatus,
    ModuleCode,
    TenantModuleAccess,
)
from admin_backend.repositories._errors import InvalidSortKeyError


# =============================================================================
# Carrier dataclasses (framework-agnostic — router maps to schema models)
# =============================================================================


@dataclass(frozen=True)
class ModuleCardRow:
    """One ``/modules`` card. Six per response, ordered by display_order."""

    module_code: str
    module_label: str
    enabled_count: int
    total_active_trial_tenants: int


@dataclass(frozen=True)
class MatrixTenantRow:
    """One ``/matrix`` tenant row, sans cells."""

    tenant_id: UUID
    name: str
    tier: str | None
    tier_label: str | None
    status: str
    status_label: str


@dataclass(frozen=True)
class MatrixCellRow:
    """One ``/matrix`` cell, grouped under a tenant_id by the Repo."""

    module_code: str
    status: str  # 'ENABLED' or 'DISABLED'


# =============================================================================
# Sort-key vocabulary (column-only — no aggregate keys for /matrix)
# =============================================================================
#
# Mirrors ``/tenants``'s base column-key set (Step 6.4). The aggregate
# keys (num_users_active_*, num_stores_*) are deliberately absent —
# the matrix doesn't expose those aggregates per row.

# Logical key -> (SQL ORDER BY column expression, ASC/DESC).
# Stored as a list of pairs so the Repo can build the ORDER BY clause
# inline within the f-string interpolation; we never write user input
# into the ORDER BY directly, only the validated key.
_MATRIX_SORT_CLAUSES: dict[str, str] = {
    "name_asc":         "t.name ASC",
    "name_desc":        "t.name DESC",
    "created_at_asc":   "t.created_at ASC",
    "created_at_desc":  "t.created_at DESC",
    "tier_asc":         "t.tier ASC",
    "tier_desc":        "t.tier DESC",
}

MATRIX_SORT_KEYS: frozenset[str] = frozenset(_MATRIX_SORT_CLAUSES.keys())

DEFAULT_MATRIX_SORT: str = "tier_asc"


# =============================================================================
# Repo
# =============================================================================


class ModulesAccessRepo:
    """Read-only repository for the Module Access endpoints."""

    async def list_modules_with_aggregates(
        self, session: AsyncSession
    ) -> list[ModuleCardRow]:
        """Return 6 ModuleCardRow rows in display_order.

        ``enabled_count`` reflects ENABLED ``tenant_module_access``
        rows joined to ``tenants WHERE status IN (ACTIVE, TRIAL)``.
        ``total_active_trial_tenants`` is the same scalar on every row.

        Schema-qualified per the raw-SQL convention; RLS is applied
        via the session GUCs the caller has set.
        """
        schema = get_settings().db_schema
        sql = text(
            f"""
            WITH visible_tenants AS (
                SELECT id
                FROM {schema}.tenants
                WHERE status IN ('ACTIVE', 'TRIAL')
            ),
            enabled_per_module AS (
                SELECT
                    tma.module::text                  AS module_code,
                    COUNT(DISTINCT tma.tenant_id)     AS enabled_count
                FROM {schema}.tenant_module_access tma
                JOIN visible_tenants vt ON tma.tenant_id = vt.id
                WHERE tma.status = 'ENABLED'
                GROUP BY tma.module
            ),
            total_count AS (
                SELECT COUNT(*) AS total FROM visible_tenants
            )
            SELECT
                lk.code                                  AS module_code,
                COALESCE(lk.display_name, lk.code)       AS module_label,
                COALESCE(epm.enabled_count, 0)           AS enabled_count,
                tc.total                                 AS total_active_trial_tenants
            FROM {schema}.lookups lk
            LEFT JOIN enabled_per_module epm
                ON epm.module_code = lk.code
            CROSS JOIN total_count tc
            WHERE lk.list_name = 'module_code'
            ORDER BY lk.display_order ASC, lk.code ASC
            """
        )
        result = await session.execute(sql)
        return [
            ModuleCardRow(
                module_code=row.module_code,
                module_label=row.module_label,
                enabled_count=int(row.enabled_count),
                total_active_trial_tenants=int(
                    row.total_active_trial_tenants
                ),
            )
            for row in result.all()
        ]

    async def list_matrix(
        self,
        session: AsyncSession,
        *,
        sort: str = DEFAULT_MATRIX_SORT,
        tier: str | None = None,
        status: str | None = None,
        q: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[
        list[MatrixTenantRow],
        dict[UUID, list[MatrixCellRow]],
        int,
    ]:
        """Three-stage query backing ``/matrix``.

        Returns ``(tenant_rows, cells_by_tenant_id, total)``. The
        router iterates ``tenant_rows`` in order and reads cells from
        the dict — preserving the sort applied at stage 1.

        Pagination applies at the *tenant* level: the tenant page is
        materialised first, then the cells query joins exactly that
        page's tenants against the modules list. ``total`` counts
        pre-pagination row matches under the same RLS + filter set.

        ``q`` is a case-insensitive ILIKE substring match on
        ``tenants.name`` only.

        Sort is validated against ``MATRIX_SORT_KEYS``; an invalid
        value raises ``InvalidSortKeyError`` (router re-raises as 400).
        """
        if sort not in MATRIX_SORT_KEYS:
            raise InvalidSortKeyError(f"unknown sort key: {sort}")

        schema = get_settings().db_schema
        sort_clause = _MATRIX_SORT_CLAUSES[sort]

        # Build the WHERE filters fragment + parameter dict shared by
        # the page query and the count query so they can't drift apart.
        where_parts: list[str] = ["t.status != 'TERMINATED'"]
        params: dict[str, Any] = {}
        if tier is not None:
            where_parts.append("t.tier::text = :tier")
            params["tier"] = tier
        if status is not None:
            where_parts.append("t.status::text = :status")
            params["status"] = status
        if q is not None:
            where_parts.append("t.name ILIKE :q")
            params["q"] = f"%{q}%"
        where_clause = " AND ".join(where_parts)

        # Stage 1: tenant page with label JOINs.
        page_sql = text(
            f"""
            SELECT
                t.id                                AS id,
                t.name                              AS name,
                t.tier::text                        AS tier,
                t.status::text                      AS status,
                COALESCE(t_tier_lk.display_name, t.tier::text)
                                                    AS tier_label,
                COALESCE(t_status_lk.display_name, t.status::text)
                                                    AS status_label
            FROM {schema}.tenants t
            LEFT JOIN {schema}.lookups t_tier_lk
                ON t_tier_lk.list_name = 'tenant_tier'
                AND t_tier_lk.code = t.tier::text
            LEFT JOIN {schema}.lookups t_status_lk
                ON t_status_lk.list_name = 'tenant_status'
                AND t_status_lk.code = t.status::text
            WHERE {where_clause}
            ORDER BY {sort_clause}, t.name ASC, t.id ASC
            LIMIT :limit OFFSET :offset
            """
        )
        page_params = {**params, "limit": limit, "offset": offset}
        page_result = await session.execute(page_sql, page_params)
        tenant_rows = [
            MatrixTenantRow(
                tenant_id=row.id,
                name=row.name,
                tier=row.tier,
                tier_label=row.tier_label if row.tier is not None else None,
                status=row.status,
                status_label=row.status_label,
            )
            for row in page_result.all()
        ]

        # Stage 3 (run before stage 2 so empty page short-circuits).
        # Same WHERE filter as stage 1; no JOINs needed.
        count_sql = text(
            f"""
            SELECT COUNT(*) AS total
            FROM {schema}.tenants t
            WHERE {where_clause}
            """
        )
        count_result = await session.execute(count_sql, params)
        total = int(count_result.scalar_one())

        if not tenant_rows:
            return [], {}, total

        # Stage 2: cells for the page. ``modules_ordered`` is the
        # canonical 6-row module list ordered by display_order; the
        # CROSS JOIN with the page's tenant ids then LEFT JOIN to
        # ``tenant_module_access`` produces the synthesized grid.
        # The CASE expression collapses 'absent' AND 'DISABLED' to
        # 'DISABLED' on the wire (frontend doesn't distinguish).
        tenant_ids = [tr.tenant_id for tr in tenant_rows]
        cells_sql = text(
            f"""
            WITH page_tenants AS (
                SELECT UNNEST(CAST(:tenant_ids AS uuid[])) AS id
            ),
            modules_ordered AS (
                SELECT
                    code AS module_code,
                    display_order
                FROM {schema}.lookups
                WHERE list_name = 'module_code'
            )
            SELECT
                pt.id           AS tenant_id,
                mo.module_code  AS module_code,
                CASE
                    WHEN tma.status = 'ENABLED' THEN 'ENABLED'
                    ELSE 'DISABLED'
                END             AS cell_status
            FROM page_tenants pt
            CROSS JOIN modules_ordered mo
            LEFT JOIN {schema}.tenant_module_access tma
                ON tma.tenant_id = pt.id
                AND tma.module::text = mo.module_code
            ORDER BY mo.display_order ASC, mo.module_code ASC
            """
        )
        cells_result = await session.execute(
            cells_sql, {"tenant_ids": tenant_ids}
        )

        cells_by_tenant: dict[UUID, list[MatrixCellRow]] = {
            tid: [] for tid in tenant_ids
        }
        for row in cells_result.all():
            cells_by_tenant[row.tenant_id].append(
                MatrixCellRow(
                    module_code=row.module_code,
                    status=row.cell_status,
                )
            )

        return tenant_rows, cells_by_tenant, total

    # ------------------------------------------------------------------
    # Step 6.15 write surface: enable / disable transitions
    # ------------------------------------------------------------------
    #
    # Both methods are PLATFORM-only at the route layer (audience="PLATFORM"
    # on the gate). The session carries app.user_type='PLATFORM' so the
    # D-29 OR-branch on tenant_module_access admits the writes regardless
    # of app.tenant_id.
    #
    # ``enable`` is upsert-shaped: INSERT when no row exists for
    # (tenant_id, module); UPDATE to ENABLED when a DISABLED row exists;
    # no-op when an ENABLED row exists. ``disable`` is UPDATE-only;
    # 404-shaped on missing rows.
    #
    # Race control via SELECT FOR UPDATE on (tenant_id, module) inside
    # the request transaction. The enable path also catches
    # IntegrityError on the INSERT branch (concurrent enable-on-missing
    # race) and retries with SELECT FOR UPDATE; the second pass sees
    # the committed row from the concurrent writer and takes the
    # UPDATE branch (LD8).
    #
    # session.expire_all() after every UPDATE / INSERT so a subsequent
    # ORM read returns fresh column values (the raw SQL bypasses the
    # SA ORM identity-map invalidation).

    async def enable(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        module: ModuleCode,
        *,
        actor_user_id: UUID,
        auth: AuthContext | None = None,
        request_id: UUID | None = None,
    ) -> TenantModuleAccess:
        """Enable ``module`` for ``tenant_id``; upserts if no row exists.

        Idempotent: returns the existing row unchanged when current state
        is already ENABLED (LD4 no-op cell). DISABLED -> ENABLED
        overwrites ``enabled_at`` + ``enabled_by_user_id`` per LD5; clears
        the disabled pair atomically per the DDL CHECK constraint.

        Race control: SELECT FOR UPDATE inside the request transaction.
        On the missing-row branch, catches IntegrityError from a
        concurrent enable-on-missing (UNIQUE violation on
        ``uq_tenant_module_access_tenant_module``), re-runs SELECT FOR
        UPDATE, and takes the UPDATE branch (LD8). One retry is
        sufficient because the second SELECT sees the committed row.

        Step 6.16.5 audit emission (LD2): the no-op ENABLED-already
        branch produces ZERO audit rows (closes FN-AB-42). First-time
        INSERT emits with ``before.status=None``; DISABLED -> ENABLED
        emits with ``before.status='DISABLED'``. ``auth`` + ``request_id``
        must be provided together for emission; both omitted skips
        emission cleanly for repo-level unit tests.
        """
        if (auth is None) != (request_id is None):
            raise ValueError(
                "auth and request_id must be provided together for audit "
                "emission, or both omitted for repo-level test paths"
            )

        # First attempt.
        existing = await self._select_for_update(session, tenant_id, module)
        before_status: str | None
        if existing is None:
            before_status = None
            try:
                row = await self._insert_enabled(
                    session, tenant_id, module, actor_user_id=actor_user_id
                )
            except IntegrityError:
                # Concurrent enable-on-missing won the INSERT race. Retry.
                await session.rollback()
                # Re-take the FOR UPDATE lock; the row is committed now.
                existing_after_race = await self._select_for_update(
                    session, tenant_id, module
                )
                if existing_after_race is None:
                    # Pathological: UNIQUE violated but row gone before
                    # retry. Surface as IntegrityError again.
                    raise
                before_status = existing_after_race.status
                if before_status == ModuleAccessStatus.ENABLED.value:
                    # Race resolved to already-ENABLED; idempotent no-op.
                    return await self._refetch(
                        session, existing_after_race.id
                    )
                row = await self._apply_enable_transition(
                    session,
                    existing_after_race,
                    actor_user_id=actor_user_id,
                )
        elif existing.status == ModuleAccessStatus.DISABLED.value:
            before_status = ModuleAccessStatus.DISABLED.value
            row = await self._apply_enable_transition(
                session, existing, actor_user_id=actor_user_id
            )
        else:
            # status == 'ENABLED'; idempotent no-op. Per LD2, no audit row.
            return await self._refetch(session, existing.id)

        # Successful state-change path : emit one audit row.
        if auth is not None and request_id is not None:
            tenant_name, module_label = await self._lookup_tenant_and_module_labels(
                session, tenant_id, module
            )
            await emit_audit_event(
                session,
                auth=auth,
                action="ENABLE",
                resource_type="MODULE_ACCESS",
                resource_id=row.id,
                resource_label=module_label,
                result_type=AuditResultType.SUCCESS,
                details=build_success_details_for_update(
                    before={"status": before_status},
                    after={"status": ModuleAccessStatus.ENABLED.value},
                ),
                tenant_id=tenant_id,
                tenant_name=tenant_name,
                request_id=request_id,
                route_to_platform=False,
            )
        return row

    async def disable(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        module: ModuleCode,
        *,
        actor_user_id: UUID,
        auth: AuthContext | None = None,
        request_id: UUID | None = None,
    ) -> tuple[TenantModuleAccess | None, "TransitionResult"]:
        """Disable ``module`` for ``tenant_id``.

        Returns ``(row | None, result)``:

        - ``(None, NOT_FOUND)`` when no row exists for (tenant_id, module).
          Caller (router) maps this to 404 ``MODULE_ACCESS_NOT_FOUND``.
          Per LD13, anchor-404 paths are NOT audited; the row stays
          un-emitted.
        - ``(row, OK)`` on any successful path:
          - ENABLED -> DISABLED with ``disabled_at`` + ``disabled_by_user_id``
            populated; ``enabled_at`` preserved (LD5). Emits one audit
            row.
          - DISABLED -> DISABLED is idempotent no-op (LD4); returns
            existing row unchanged. Per LD2, no audit row.
        """
        if (auth is None) != (request_id is None):
            raise ValueError(
                "auth and request_id must be provided together for audit "
                "emission, or both omitted for repo-level test paths"
            )

        existing = await self._select_for_update(session, tenant_id, module)
        if existing is None:
            return None, TransitionResult.NOT_FOUND

        if existing.status == ModuleAccessStatus.ENABLED.value:
            row = await self._apply_disable_transition(
                session, existing, actor_user_id=actor_user_id
            )
            if auth is not None and request_id is not None:
                tenant_name, module_label = (
                    await self._lookup_tenant_and_module_labels(
                        session, tenant_id, module
                    )
                )
                await emit_audit_event(
                    session,
                    auth=auth,
                    action="DISABLE",
                    resource_type="MODULE_ACCESS",
                    resource_id=row.id,
                    resource_label=module_label,
                    result_type=AuditResultType.SUCCESS,
                    details=build_success_details_for_update(
                        before={"status": ModuleAccessStatus.ENABLED.value},
                        after={"status": ModuleAccessStatus.DISABLED.value},
                    ),
                    tenant_id=tenant_id,
                    tenant_name=tenant_name,
                    request_id=request_id,
                    route_to_platform=False,
                )
            return row, TransitionResult.OK

        # status == 'DISABLED'; idempotent no-op. Per LD2, no audit row.
        return (
            await self._refetch(session, existing.id),
            TransitionResult.OK,
        )

    async def _lookup_tenant_and_module_labels(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        module: ModuleCode,
    ) -> tuple[str, str]:
        """Resolve (tenant_name, module_display_label) for the audit row.

        One SELECT joining ``tenants`` (for ``tenant_name``, NOT NULL on
        the tenant audit table) and ``core.lookups`` keyed by
        ``(list_name='module_code', code=module)`` for the module's
        display label (LD9). Both reads happen inside the caller's
        transaction.

        Defensive fallback to ``<unknown>`` for either field if the
        lookup row is missing (very unlikely for tenant; the lookups
        rows for the 6 modules were seeded at Step 3.4.5 / 6.7).
        """
        schema = get_settings().db_schema
        result = await session.execute(
            text(
                f"""
                SELECT
                    (SELECT name FROM {schema}.tenants WHERE id = :tenant_id)
                        AS tenant_name,
                    (SELECT COALESCE(display_name, code)
                       FROM {schema}.lookups
                      WHERE list_name = 'module_code'
                        AND code = :module_code)
                        AS module_label
                """
            ),
            {"tenant_id": tenant_id, "module_code": module.value},
        )
        row = result.first()
        tenant_name = (
            str(row.tenant_name) if row and row.tenant_name else "<unknown>"
        )
        module_label = (
            str(row.module_label) if row and row.module_label else module.value
        )
        return tenant_name, module_label

    # ------------------------------------------------------------------
    # Step 6.15 private transition helpers
    # ------------------------------------------------------------------

    async def _select_for_update(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        module: ModuleCode,
    ) -> Any:
        """SELECT FOR UPDATE on (tenant_id, module); returns row or None.

        Race-control arbiter. The arbiter unique index is
        ``uq_tenant_module_access_tenant_module(tenant_id, module)``;
        we project the columns the transition logic needs.
        """
        schema = get_settings().db_schema
        sql = text(
            f"""
            SELECT id, status::text AS status,
                   enabled_at, disabled_at
              FROM {schema}.tenant_module_access
             WHERE tenant_id = :tenant_id
               AND module = CAST(:module AS {schema}.module_code_enum)
             FOR UPDATE
            """
        )
        result = await session.execute(
            sql, {"tenant_id": tenant_id, "module": module.value}
        )
        return result.first()

    async def _insert_enabled(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        module: ModuleCode,
        *,
        actor_user_id: UUID,
    ) -> TenantModuleAccess:
        """INSERT a new ENABLED row; returns the materialised ORM model."""
        schema = get_settings().db_schema
        result = await session.execute(
            text(
                f"""
                INSERT INTO {schema}.tenant_module_access (
                    tenant_id, module, status,
                    enabled_at, enabled_by_user_id,
                    created_by_user_id, updated_by_user_id
                ) VALUES (
                    :tenant_id,
                    CAST(:module AS {schema}.module_code_enum),
                    CAST('ENABLED' AS {schema}.module_access_status_enum),
                    now(), :actor,
                    :actor, :actor
                )
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "module": module.value,
                "actor": actor_user_id,
            },
        )
        new_id: UUID = result.scalar_one()
        return await self._refetch(session, new_id)

    async def _apply_enable_transition(
        self,
        session: AsyncSession,
        existing: Any,
        *,
        actor_user_id: UUID,
    ) -> TenantModuleAccess:
        """UPDATE existing DISABLED row to ENABLED with overwrite semantics.

        LD5: ``enabled_at`` + ``enabled_by_user_id`` overwritten (treated
        as "current ENABLED stint began at" markers). The disabled pair
        clears atomically per ``ck_tenant_module_access_disabled_pair``
        + ``ck_tenant_module_access_status_consistency``.
        """
        schema = get_settings().db_schema
        await session.execute(
            text(
                f"""
                UPDATE {schema}.tenant_module_access
                   SET status = CAST('ENABLED' AS {schema}.module_access_status_enum),
                       enabled_at = now(),
                       enabled_by_user_id = :actor,
                       disabled_at = NULL,
                       disabled_by_user_id = NULL,
                       updated_by_user_id = :actor
                 WHERE id = :id
                """
            ),
            {"actor": actor_user_id, "id": existing.id},
        )
        session.expire_all()
        return await self._refetch(session, existing.id)

    async def _apply_disable_transition(
        self,
        session: AsyncSession,
        existing: Any,
        *,
        actor_user_id: UUID,
    ) -> TenantModuleAccess:
        """UPDATE existing ENABLED row to DISABLED; preserves ``enabled_at``.

        Per LD5 ``enabled_at`` is the historical record of when the
        just-ended ENABLED stint began; the disable does not touch it.
        Only the disabled pair + updated audit columns change.
        """
        schema = get_settings().db_schema
        await session.execute(
            text(
                f"""
                UPDATE {schema}.tenant_module_access
                   SET status = CAST('DISABLED' AS {schema}.module_access_status_enum),
                       disabled_at = now(),
                       disabled_by_user_id = :actor,
                       updated_by_user_id = :actor
                 WHERE id = :id
                """
            ),
            {"actor": actor_user_id, "id": existing.id},
        )
        session.expire_all()
        return await self._refetch(session, existing.id)

    async def _refetch(
        self, session: AsyncSession, row_id: UUID
    ) -> TenantModuleAccess:
        """Read the row back as a fully-populated ORM model.

        Raw SQL bypasses the ORM identity map; a previous read against
        the same id would otherwise return stale attribute values. The
        caller of every write path runs through ``session.expire_all()``
        before this method so the identity-map miss forces a fresh
        SELECT.
        """
        from sqlalchemy import select

        result = await session.execute(
            select(TenantModuleAccess).where(TenantModuleAccess.id == row_id)
        )
        return result.scalar_one()


class TransitionResult(StrEnum):
    """Outcome enum for ``ModulesAccessRepo.disable`` (Step 6.15).

    Two values: ``OK`` (a row exists, transition or no-op applied) and
    ``NOT_FOUND`` (no row for the supplied (tenant_id, module) pair —
    only the disable path can produce this; enable upserts).

    Local to this module mirroring ``TenantsRepo``'s ``TransitionResult``
    (Step 6.11.1). Per locked decisions, the two enums stay separate
    even though both happen to carry ``OK`` and ``NOT_FOUND``: cross-
    resource transition semantics differ (tenants raise 409
    INVALID_STATE_TRANSITION; modules are idempotent-200 on no-op).
    """

    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
