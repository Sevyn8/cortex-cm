"""TenantUsersRepo — read-only data access for ``tenant_users``.

Owns SELECT queries on ``tenant_users``. The Repo does NOT set session
GUCs, NOT begin transactions, NOT handle commits/rollbacks — those are
the session/middleware layer's job.

Visibility flows from session GUCs (RLS-bound, unlike platform_users):

  - PLATFORM JWT: sees all tenant_users across all tenants via D-29's
    unconditional OR-branch on ``tenant_users_tenant_isolation``.
  - TENANT JWT: RLS scopes to the matching ``app.tenant_id`` row set.

Per D-24, no ``tenant_id`` argument on visibility-bearing methods —
visibility is the session's job. The optional ``tenant_id`` filter
on ``list(...)`` is an *application-layer narrowing* for PLATFORM
users who want to scope the response to a single tenant
(e.g., the admin console showing tenant detail with its users).
For TENANT JWTs the filter is functionally redundant (RLS already
scopes to their tenant); a non-matching value just intersects to
empty rather than disclosing other-tenant rows.

Per D-17, missing OR RLS-filtered rows surface as ``None`` from
``get_by_id``; the router converts to 404 (TENANT_USER_NOT_FOUND).

Mirrors ``PlatformUsersRepo``: stateless singleton at module level,
each method takes ``session`` as the first positional argument, no
instance state. Sort-key validation reuses the shared
``InvalidSortKeyError`` from ``repositories._errors`` (introduced at
Step 5.1; promoted to a shared module at Step 5.2 so this Repo and
future ones import the same class).

Step 6.8.3 — A1/A2 augmentation. ``list(...)`` and ``get_by_id(...)``
now return row carriers (``TenantUserListRow`` /
``TenantUserDetailRow``) carrying both the ORM row and a per-row
``roles: list[dict[str, Any]]`` produced by a correlated jsonb_agg
subquery. Mirrors ``repositories/tenants.py:list_with_aggregates``
exactly. The router maps each dict in ``roles`` to a
``UserRoleAssignmentItem`` via a hand-written mapper (the same
pattern ``routers/v1/tenants.py:_list_item_from_row`` uses for
``Module``).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import String, and_, cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.audit.emit import (
    build_success_details_for_create,
    build_success_details_for_transition,
    build_success_details_for_update,
    emit_audit_event,
)
from admin_backend.auth.context import AuthContext
from admin_backend.config import get_settings
from admin_backend.errors import (
    DuplicateTenantUserEmailError,
    InvalidOrgNodeError,
    InvalidRoleAudienceError,
    InvalidRoleError,
    RoleAssignmentConflictError,
)
from admin_backend.models.audit_log import AuditResultType
from admin_backend.models.org_node import OrgNode
from admin_backend.models.role import Role
from admin_backend.models.tenant_user import (
    ActorUserType,
    TenantUser,
    TenantUserStatus,
)
from admin_backend.models.tenant_user_role_assignment import (
    TenantUserRoleAssignment,
)
from admin_backend.repositories._errors import InvalidSortKeyError
from admin_backend.repositories.tenants import TransitionResult


class AcceptInvitationResult(StrEnum):
    """Outcome of ``TenantUsersRepo.accept_invitation`` (Step CI-4c).

    Distinct from ``TransitionResult`` because invite-accept has idempotent and
    race outcomes the admin suspend/activate transition does not:

      - ``ACTIVATED``: the guarded UPDATE flipped INVITED -> ACTIVE (a real change).
      - ``ALREADY_ACTIVE``: the row is already ACTIVE (idempotent repeat call).
      - ``NOT_INVITED``: the row exists but is SUSPENDED (cannot self-accept; 409).
      - ``CONFLICT``: the row is still INVITED after a 0-row UPDATE (a concurrent
        accept race; transient, retryable). Kept distinct so it is never
        misclassified as ALREADY_ACTIVE or NOT_INVITED.
      - ``NOT_FOUND``: no row for this (user_id, tenant_id), or RLS-invisible.
    """

    ACTIVATED = "ACTIVATED"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    NOT_INVITED = "NOT_INVITED"
    CONFLICT = "CONFLICT"
    NOT_FOUND = "NOT_FOUND"


# Module-level type alias: inside ``TenantUsersRepo``'s class scope,
# the bare name ``list`` resolves to the bound method ``.list(...)``
# even under ``from __future__ import annotations``. Use the alias on
# method parameters to keep the annotations resolvable.
RoleIdList = list[UUID]

# Step 6.14: a single role-anchor assignment as a (role_id, org_node_id)
# tuple. The Pydantic ``RoleAssignmentItem`` carries the same two
# fields; the repo accepts pre-flattened tuples so it stays a pure
# data-access layer (no Pydantic import).
RoleAssignmentTuple = tuple[UUID, UUID]
RoleAssignmentList = list[RoleAssignmentTuple]

# Step 6.16.4: shape of an audit-row role-item with frozen labels per
# LD9. Module-level alias because inside the class body ``list``
# resolves to the bound method ``.list()`` even under ``from __future__
# import annotations``; same trick as ``RoleAssignmentList``.
RoleLabelDict = dict[str, Any]
RoleLabelList = list[RoleLabelDict]

# Constraint name of the partial-UNIQUE index that licenses Pattern B
# (same role at distinct anchors) while blocking duplicate ACTIVE rows.
# Matched against ``IntegrityError`` content to scope the catch to the
# concurrent-edit race; other IntegrityErrors propagate (per the Step
# 6.14 LD7 operator note).
_UQ_ACTIVE_INDEX_NAME = "uq_tenant_user_role_assignments_active"


# Sort keys map to ORM column expressions. Stable secondary sort by
# ``id ASC`` is appended in the query so identical primary-sort
# values page deterministically.
SORT_MAP: dict[str, Any] = {
    "created_at_asc": TenantUser.created_at.asc(),
    "created_at_desc": TenantUser.created_at.desc(),
    "full_name_asc": TenantUser.full_name.asc(),
    "full_name_desc": TenantUser.full_name.desc(),
    "email_asc": TenantUser.email.asc(),
    "email_desc": TenantUser.email.desc(),
}


@dataclass
class TenantUserListRow:
    """Row carrier for ``list(...)``: ORM TenantUser + roles aggregate.

    ``roles`` is the JSONB-decoded list[dict] from psycopg's automatic
    JSONB conversion; the router maps each dict to a
    ``UserRoleAssignmentItem`` Pydantic model.
    """

    user: TenantUser
    roles: list[dict[str, Any]]


@dataclass
class TenantUserDetailRow:
    """Row carrier for ``get_by_id(...)``: same shape as
    TenantUserListRow; kept distinct so list-vs-detail mappers stay
    typed independently.
    """

    user: TenantUser
    roles: list[dict[str, Any]]


def _roles_subq() -> Any:
    """Per-tenant-user roles as ``jsonb_agg`` of the 8-field item, ordered.

    Returns a scalar subquery correlated to the outer ``TenantUser``
    row. Yields a JSONB array (decoded by psycopg as ``list[dict]``)
    where each element is the 8-field ``UserRoleAssignmentItem`` shape.

    Composite-key joins (per Step 6.8.1 D-34, AI-RBAC-06):
    ``tenant_user_role_assignments`` is joined to ``tenant_users`` via
    the composite ``(tenant_id, tenant_user_id)`` (correlated to the
    outer ``TenantUser`` row), and to ``org_nodes`` via the composite
    ``(tenant_id, org_node_id)``. Both composites are guaranteed by
    DDL FKs; using the composite at the read path keeps the read shape
    consistent with the storage invariant.

    All assignments are returned regardless of status (locked decision
    6 of Step 6.8.3); ``ORDER BY granted_at DESC, id ASC`` inside the
    aggregate keeps the wire shape deterministic.

    COALESCE-to-``'[]'::jsonb`` so users with zero assignments get an
    empty list rather than NULL.
    """
    # Cast the assignment status enum to text so jsonb_build_object
    # emits a clean string ("ACTIVE" / "INACTIVE") that Pydantic's
    # str-Enum coerces. Same gotcha as the module enum cast in
    # tenants.py:_modules_subq (per "Note on PG enum columns" in
    # CLAUDE.md).
    status_as_text = cast(TenantUserRoleAssignment.status, String)
    item_object = func.jsonb_build_object(
        "assignment_id", TenantUserRoleAssignment.id,
        "role_id", Role.id,
        "role_name", Role.name,
        "role_code", Role.code,
        "status", status_as_text,
        "granted_at", TenantUserRoleAssignment.granted_at,
        "org_node_id", OrgNode.id,
        "org_node_name", OrgNode.name,
    )
    ordered_item = aggregate_order_by(
        item_object,
        TenantUserRoleAssignment.granted_at.desc(),
        TenantUserRoleAssignment.id.asc(),
    )
    return (
        select(
            func.coalesce(
                func.jsonb_agg(ordered_item),
                text("'[]'::jsonb"),
            )
        )
        .select_from(TenantUserRoleAssignment)
        .join(Role, Role.id == TenantUserRoleAssignment.role_id)
        .join(
            OrgNode,
            and_(
                OrgNode.tenant_id == TenantUserRoleAssignment.tenant_id,
                OrgNode.id == TenantUserRoleAssignment.org_node_id,
            ),
        )
        .where(
            TenantUserRoleAssignment.tenant_id == TenantUser.tenant_id,
            TenantUserRoleAssignment.tenant_user_id == TenantUser.id,
        )
        .correlate(TenantUser)
        .scalar_subquery()
    )


class TenantUsersRepo:
    """Read-only repository for ``tenant_users``."""

    async def get_by_id(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> TenantUserDetailRow | None:
        """Return the tenant user with this id (with roles aggregate)
        or ``None`` if not visible.

        "Not visible" includes both genuinely missing rows AND rows
        filtered by RLS (per D-17). The router converts ``None`` to
        404 ``TENANT_USER_NOT_FOUND`` — load-bearing for cross-tenant
        access not disclosing existence.

        The roles correlated subquery inherits the same RLS context
        as the outer query (D-29 OR-branch on
        ``tenant_user_role_assignments_tenant_isolation``).
        """
        stmt = select(
            TenantUser,
            _roles_subq().label("roles"),
        ).where(TenantUser.id == user_id)
        result = await session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        user_obj, roles = row
        return TenantUserDetailRow(user=user_obj, roles=roles)

    async def list(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID | None = None,
        status: TenantUserStatus | None = None,
        search: str | None = None,
        sort: str = "created_at_desc",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[TenantUserListRow], int]:
        """Return ``(rows, total)`` matching filters under RLS.

        ``rows`` carries each visible ``TenantUser`` with its roles
        aggregate; ``total`` counts rows matching the same filters but
        ignoring offset/limit so pagination metadata is correct.

        Visibility is session-driven via RLS. Filters layered on:

          - ``tenant_id``: optional application-layer scoping. Useful
            for PLATFORM callers narrowing to one tenant; redundant
            (but harmless) for TENANT callers.
          - ``status``: filter to a single status (typically ACTIVE).
          - ``search``: case-insensitive ILIKE across ``email`` and
            ``full_name``.
          - ``sort``: one of ``SORT_MAP`` keys; raises
            ``InvalidSortKeyError`` for unknown keys (router catches
            and re-raises as ``InvalidSortKeyClientError`` -> 400).
          - ``offset`` / ``limit``: pagination.

        The roles correlated subquery is independent per outer row;
        no row multiplication, so ``limit``/``offset`` slice the
        intended TenantUser rows.
        """
        if sort not in SORT_MAP:
            raise InvalidSortKeyError(f"unknown sort key: {sort}")

        conditions = []
        if tenant_id is not None:
            conditions.append(TenantUser.tenant_id == tenant_id)
        if status is not None:
            conditions.append(TenantUser.status == status)
        if search:
            pat = f"%{search}%"
            conditions.append(
                or_(
                    TenantUser.email.ilike(pat),
                    TenantUser.full_name.ilike(pat),
                )
            )

        count_stmt = select(func.count()).select_from(TenantUser)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        count_result = await session.execute(count_stmt)
        total: int = count_result.scalar_one()

        stmt = (
            select(
                TenantUser,
                _roles_subq().label("roles"),
            )
            .order_by(SORT_MAP[sort], TenantUser.id.asc())
        )
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.offset(offset).limit(limit)

        items_result = await session.execute(stmt)
        rows = [
            TenantUserListRow(user=u, roles=r)
            for (u, r) in items_result.all()
        ]
        return rows, total

    # ------------------------------------------------------------------
    # Step 6.10.1 write methods
    # ------------------------------------------------------------------
    #
    # All three methods use raw ``text()`` SQL with explicit schema
    # qualification per the convention. The handler owns transaction
    # scope: ``get_tenant_session_dep`` opens one transaction per
    # request and commits on clean exit. Method bodies issue
    # statements but never COMMIT or ROLLBACK.
    #
    # Pattern (b) audit-actor pair population (per D-13): every
    # tenant_users / tenant_user_role_assignments INSERT or UPDATE that
    # touches a ``*_by_user_id`` column populates the matching
    # ``*_by_user_type`` column simultaneously. The ``ck_*_actor_pair``
    # CHECK constraints enforce both-NULL or both-NOT-NULL.

    async def _validate_roles(
        self,
        session: AsyncSession,
        role_ids: RoleIdList,
    ) -> None:
        """Validate every ``role_id`` exists, is non-ARCHIVED, and is
        TENANT-audience.

        Step 6.14 extends 6.10.1's ``_resolve_role_audience`` with an
        ARCHIVED check, aggregated with missing into ``INVALID_ROLE``.
        The audience mismatch keeps its existing
        ``INVALID_ROLE_AUDIENCE`` code (distinct from
        ``INVALID_ROLE``) per the locked decision.

        Order of failure modes (deterministic for testability):
          1. ``InvalidRoleError`` (422) if any role_id is missing OR
             archived. ``exc.context['unknown_role_ids']`` carries
             missing + archived together (the wire response is the
             same 422 regardless).
          2. ``InvalidRoleAudienceError`` (422) if any role exists,
             is non-ARCHIVED, but has audience != 'TENANT'.
             ``exc.context['invalid_role_ids']`` carries the full set.

        Pattern (Q7 lock): structured detail in ``exc.context``;
        response envelope ``details`` stays ``null``.
        """
        if not role_ids:
            return

        schema = get_settings().db_schema
        result = await session.execute(
            text(
                f"""
                SELECT id,
                       audience::text AS audience,
                       status::text AS status
                FROM {schema}.roles
                WHERE id = ANY(:ids)
                """
            ),
            {"ids": role_ids},
        )
        found: dict[UUID, tuple[str, str]] = {
            UUID(str(r.id)): (str(r.audience), str(r.status))
            for r in result.all()
        }

        # Missing + archived aggregated under INVALID_ROLE.
        invalid_role_ids: list[UUID] = [
            rid
            for rid in role_ids
            if rid not in found or found[rid][1] == "ARCHIVED"
        ]
        if invalid_role_ids:
            raise InvalidRoleError(
                f"unknown or archived role ids: {invalid_role_ids!r}",
                unknown_role_ids=[str(r) for r in invalid_role_ids],
            )

        # Audience mismatch keeps its distinct code.
        invalid_audience: list[UUID] = [
            rid for rid in role_ids if found[rid][0] != "TENANT"
        ]
        if invalid_audience:
            raise InvalidRoleAudienceError(
                f"non-TENANT audience role ids: {invalid_audience!r}",
                invalid_role_ids=[str(r) for r in invalid_audience],
            )

    async def _validate_org_nodes(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        org_node_ids: RoleIdList,
    ) -> None:
        """Validate every ``org_node_id`` is in the supplied
        ``tenant_id`` and is non-ARCHIVED.

        Step 6.14 pre-check. The composite FK
        ``fk_tenant_user_role_assignments_org_node_same_tenant`` would
        reject cross-tenant ``org_node_id`` at INSERT time; the
        pre-check surfaces it as a clean 422 ahead of the write.

        Three failure modes aggregated under a single
        ``InvalidOrgNodeError`` (422):
          - org_node_id not present in the catalogue (missing globally),
          - org_node_id present but in a different tenant (RLS or
            cross-tenant probe — surfaces as missing-from-tenant),
          - org_node_id present in the correct tenant but archived.

        Identical wire response per LD6. Structured detail in
        ``exc.context['invalid_org_node_ids']``.
        """
        if not org_node_ids:
            return

        schema = get_settings().db_schema
        # Visible-and-non-archived org_nodes scoped to this tenant.
        # The RLS policy on org_nodes plus the WHERE tenant_id =
        # :tenant_id clause double-scope; the explicit tenant_id check
        # catches the cross-tenant case under a PLATFORM session
        # (where RLS doesn't filter).
        result = await session.execute(
            text(
                f"""
                SELECT id, status::text AS status
                FROM {schema}.org_nodes
                WHERE id = ANY(:ids)
                  AND tenant_id = :tenant_id
                """
            ),
            {"ids": org_node_ids, "tenant_id": tenant_id},
        )
        found: dict[UUID, str] = {
            UUID(str(r.id)): str(r.status) for r in result.all()
        }

        invalid: list[UUID] = [
            oid
            for oid in org_node_ids
            if oid not in found or found[oid] == "ARCHIVED"
        ]
        if invalid:
            raise InvalidOrgNodeError(
                f"unknown or archived org_node ids: {invalid!r}",
                invalid_org_node_ids=[str(o) for o in invalid],
            )

    async def _raise_if_email_taken(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        email: str,
        exclude_user_id: UUID | None,
    ) -> None:
        """Per-tenant uniqueness check on ``tenant_users.email``.

        Schema-level ``uq_tenant_users_tenant_email`` enforces the
        constraint at the DB layer; the app-layer pre-check (same
        transaction) surfaces the conflict as a domain-shaped 409
        rather than letting the unique-index violation surface as 500.

        ``exclude_user_id`` lets ``update`` skip the rename-to-self
        case: a PATCH that keeps the email unchanged would otherwise
        always reject.
        """
        schema = get_settings().db_schema
        if exclude_user_id is None:
            row = await session.execute(
                text(
                    f"SELECT 1 FROM {schema}.tenant_users "
                    "WHERE tenant_id = :tenant_id AND email = :email "
                    "LIMIT 1"
                ),
                {"tenant_id": tenant_id, "email": email},
            )
        else:
            row = await session.execute(
                text(
                    f"SELECT 1 FROM {schema}.tenant_users "
                    "WHERE tenant_id = :tenant_id "
                    "AND email = :email AND id != :exclude_id LIMIT 1"
                ),
                {
                    "tenant_id": tenant_id,
                    "email": email,
                    "exclude_id": exclude_user_id,
                },
            )
        if row.first() is not None:
            raise DuplicateTenantUserEmailError(
                f"tenant_user email already taken in tenant {tenant_id}: "
                f"{email!r}",
                tenant_id=str(tenant_id),
                email=email,
            )

    async def _tenant_exists(
        self,
        session: AsyncSession,
        tenant_id: UUID,
    ) -> bool:
        """Return True iff ``tenant_id`` is visible to this session.

        Step 6.14 replaces ``_lookup_tenant_root`` (retired with the
        tenant-root-only anchor pattern of 6.10.1). The create path
        now needs to know whether the tenant is visible at all (so
        cross-tenant probes from a TENANT JWT still surface as 404);
        anchor validity is the
        ``_validate_org_nodes`` pre-check's concern.

        RLS on ``tenants`` filters cross-tenant probes for TENANT
        sessions; PLATFORM sessions see all rows via D-29.
        """
        schema = get_settings().db_schema
        row = await session.execute(
            text(
                f"SELECT 1 FROM {schema}.tenants "
                "WHERE id = :tenant_id LIMIT 1"
            ),
            {"tenant_id": tenant_id},
        )
        return row.first() is not None

    async def _tenant_name_for(
        self,
        session: AsyncSession,
        tenant_id: UUID,
    ) -> str:
        """Snapshot ``tenants.name`` for the audit row's ``tenant_name``
        column (NOT NULL on the tenant table; LD7).

        Falls back to ``"<unknown>"`` if the tenant row is no longer
        visible (defensive; the caller has already validated existence
        in the same transaction so this branch should not fire).
        """
        schema = get_settings().db_schema
        row = await session.execute(
            text(
                f"SELECT name FROM {schema}.tenants "
                "WHERE id = :tenant_id"
            ),
            {"tenant_id": tenant_id},
        )
        return str(row.scalar_one_or_none() or "<unknown>")

    async def _resolve_role_labels(
        self,
        session: AsyncSession,
        pairs: RoleAssignmentList,
    ) -> RoleLabelList:
        """Eager-resolve ``role_name`` + ``org_node_name`` for a list of
        ``(role_id, org_node_id)`` tuples per LD9.

        Returns a list of dicts each carrying ``role_id``,
        ``role_name``, ``org_node_id``, ``org_node_name``. Frozen
        labels at write time; subsequent renames do not rewrite
        historical audit rows.

        Two scalar SELECTs against ``roles`` and ``org_nodes``
        keyed by UUID array. Per-call cost at v0 scale is sub-
        millisecond per query; deferred to Scale Considerations
        triggers per the design doc.
        """
        if not pairs:
            return []
        role_ids = list({rid for (rid, _oid) in pairs})
        org_node_ids = list({oid for (_rid, oid) in pairs})
        schema = get_settings().db_schema
        role_rows = await session.execute(
            text(
                f"SELECT id, name FROM {schema}.roles "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": role_ids},
        )
        role_name_by_id: dict[UUID, str] = {
            UUID(str(r.id)): str(r.name) for r in role_rows
        }
        on_rows = await session.execute(
            text(
                f"SELECT id, name FROM {schema}.org_nodes "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": org_node_ids},
        )
        on_name_by_id: dict[UUID, str] = {
            UUID(str(r.id)): str(r.name) for r in on_rows
        }
        return [
            {
                "role_id": str(rid),
                "role_name": role_name_by_id.get(rid),
                "org_node_id": str(oid),
                "org_node_name": on_name_by_id.get(oid),
            }
            for (rid, oid) in pairs
        ]

    async def _select_current_active_assignments_for_update(
        self,
        session: AsyncSession,
        tenant_user_id: UUID,
    ) -> set[RoleAssignmentTuple]:
        """Return the current ACTIVE (role_id, org_node_id) tuples for
        ``tenant_user_id`` under ``SELECT ... FOR UPDATE``.

        Step 6.14 diff-replace foundation. Locking the current set
        inside the request transaction blocks parallel transactions
        from racing the same user's assignments between our SELECT
        and our INSERT/UPDATE writes.

        Empty set when the user has no current ACTIVE rows (or when
        RLS filters all of them out — the row-visibility check happens
        upstream).
        """
        schema = get_settings().db_schema
        result = await session.execute(
            text(
                f"""
                SELECT role_id, org_node_id
                FROM {schema}.tenant_user_role_assignments
                WHERE tenant_user_id = :tu_id
                  AND status = CAST('ACTIVE'
                               AS {schema}.user_role_assignment_status_enum)
                FOR UPDATE
                """
            ),
            {"tu_id": tenant_user_id},
        )
        return {
            (UUID(str(r.role_id)), UUID(str(r.org_node_id)))
            for r in result.all()
        }

    async def _apply_role_assignments_diff(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        tenant_user_id: UUID,
        current_set: set[RoleAssignmentTuple],
        desired_set: set[RoleAssignmentTuple],
        actor_user_id: UUID,
        actor_user_type: ActorUserType,
    ) -> None:
        """Apply the diff between ``current_set`` and ``desired_set``
        (Step 6.14 LD3):

          - (current ∩ desired): NO WRITE; rows retain original
            ``granted_at`` / ``granted_by_*`` / ``updated_at``.
          - (current − desired): UPDATE row to INACTIVE; populate
            ``revoked_at`` + ``revoked_by_*`` per Pattern (b).
          - (desired − current): INSERT new ACTIVE row; populate
            ``granted_by_*`` per Pattern (b).

        Composite anchor at ``(tenant_id, org_node_id)`` on INSERT
        per D-34 (the FK ``fk_..._org_node_same_tenant`` enforces the
        same-tenant invariant structurally).

        Concurrent-edit race (LD7): an in-flight parallel INSERT can
        collide with our INSERT on
        ``uq_tenant_user_role_assignments_active``. Caught as
        ``IntegrityError`` and surfaced as
        ``RoleAssignmentConflictError`` (409) — but ONLY when the
        constraint name matches; other ``IntegrityError`` instances
        propagate so real-bug cases (cross-tenant FK reject, audience
        trigger reject, NOT NULL violation) surface as 500.
        """
        schema = get_settings().db_schema

        to_revoke = current_set - desired_set
        to_insert = desired_set - current_set
        # Unchanged = current_set & desired_set; no work.

        # Revokes first so a subsequent INSERT on the same
        # (role, anchor) inside this same request body never
        # collides with itself. Within a single request, the desired
        # set is deduped by the schema layer, so we won't issue both
        # a revoke AND an insert for the same tuple — but the order
        # is principled regardless.
        if to_revoke:
            # Per-row UPDATE rather than a composite ANY() bind:
            # psycopg can't send a Python list-of-tuples as a Postgres
            # record array on the wire. Per-user assignment counts are
            # small (low single digits in practice); the loop cost is
            # immaterial vs the composite-array alternative (unnest +
            # CTE) which would be harder to read.
            for (role_id, org_node_id) in to_revoke:
                await session.execute(
                    text(
                        f"""
                        UPDATE {schema}.tenant_user_role_assignments
                           SET status = CAST('INACTIVE'
                                        AS {schema}.user_role_assignment_status_enum),
                               revoked_at = now(),
                               revoked_by_user_id = :actor,
                               revoked_by_user_type = CAST(:actor_type
                                                      AS {schema}.actor_user_type_enum)
                         WHERE tenant_user_id = :tu_id
                           AND role_id = :role_id
                           AND org_node_id = :on_id
                           AND status = CAST('ACTIVE'
                                        AS {schema}.user_role_assignment_status_enum)
                        """
                    ),
                    {
                        "tu_id": tenant_user_id,
                        "role_id": role_id,
                        "on_id": org_node_id,
                        "actor": actor_user_id,
                        "actor_type": actor_user_type.value,
                    },
                )

        if to_insert:
            for (role_id, org_node_id) in to_insert:
                try:
                    await session.execute(
                        text(
                            f"""
                            INSERT INTO {schema}.tenant_user_role_assignments (
                                tenant_id, tenant_user_id, org_node_id,
                                role_id, status,
                                granted_by_user_id, granted_by_user_type
                            ) VALUES (
                                :tenant_id, :tu_id, :on_id,
                                :role_id,
                                CAST('ACTIVE'
                                     AS {schema}.user_role_assignment_status_enum),
                                :actor,
                                CAST(:actor_type
                                     AS {schema}.actor_user_type_enum)
                            )
                            """
                        ),
                        {
                            "tenant_id": tenant_id,
                            "tu_id": tenant_user_id,
                            "on_id": org_node_id,
                            "role_id": role_id,
                            "actor": actor_user_id,
                            "actor_type": actor_user_type.value,
                        },
                    )
                except IntegrityError as exc:
                    if _UQ_ACTIVE_INDEX_NAME in str(exc.orig):
                        raise RoleAssignmentConflictError(
                            (
                                "concurrent edit produced a duplicate "
                                f"ACTIVE (user={tenant_user_id}, "
                                f"role={role_id}, org_node={org_node_id})"
                            ),
                            conflicting_triple={
                                "tenant_user_id": str(tenant_user_id),
                                "role_id": str(role_id),
                                "org_node_id": str(org_node_id),
                            },
                        ) from exc
                    raise

    async def create(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        email: str,
        full_name: str,
        role_assignments: RoleAssignmentList,
        actor_user_id: UUID,
        actor_user_type: ActorUserType,
        auth: AuthContext | None = None,
        request_id: UUID | None = None,
    ) -> TenantUserDetailRow | None:
        """Insert one ``tenant_users`` row + N
        ``tenant_user_role_assignments`` rows in a single transaction.

        Server-forces ``status='INVITED'`` (the only valid initial
        state per ``ck_tenant_users_auth0_sub_consistency``;
        ``auth0_sub`` and ``invitation_accepted_at`` stay NULL until
        Stage 3's Auth0 invite-accept callback).

        Step 6.14 (vs 6.10.1): ``role_assignments`` is a list of
        ``(role_id, org_node_id)`` tuples. Tenant-root-only anchoring
        is retired; any non-archived org_node in the same tenant is
        acceptable. The repo runs the diff-replace helper against an
        empty current-set, which collapses to "INSERT each desired
        pair as ACTIVE."

        ``email`` is already lowercased by the Pydantic validator.
        Caller has already deduped ``(role_id, org_node_id)`` pairs
        (schema layer) and pre-checked within-request duplicates
        (router layer).

        Returns ``None`` if the tenant is invisible (RLS-as-404:
        cross-tenant ``tenant_id`` from a TENANT JWT). Caller raises
        ``TenantNotFoundError``.

        Validation order (LD4):
          1. ``InvalidRoleError`` (422) on missing/archived role.
          2. ``InvalidRoleAudienceError`` (422) on non-TENANT role.
          3. ``InvalidOrgNodeError`` (422) on missing/archived/
             cross-tenant org_node.
          4. ``TenantNotFoundError`` (caller-side) on invisible tenant.
          5. ``DuplicateTenantUserEmailError`` (409) on email collision.
        """
        schema = get_settings().db_schema

        role_ids = [rid for (rid, _oid) in role_assignments]
        org_node_ids = [oid for (_rid, oid) in role_assignments]

        # Validation in deterministic order (LD4). Each raises a 422
        # without writing.
        await self._validate_roles(session, role_ids)

        if not await self._tenant_exists(session, tenant_id):
            return None

        await self._validate_org_nodes(session, tenant_id, org_node_ids)

        await self._raise_if_email_taken(
            session,
            tenant_id=tenant_id,
            email=email,
            exclude_user_id=None,
        )

        insert_user = await session.execute(
            text(
                f"""
                INSERT INTO {schema}.tenant_users (
                    tenant_id, email, full_name, status,
                    auth0_sub, invited_at, invitation_accepted_at,
                    created_by_user_id, created_by_user_type,
                    updated_by_user_id, updated_by_user_type
                ) VALUES (
                    :tenant_id, :email, :full_name,
                    CAST('INVITED' AS {schema}.tenant_user_status_enum),
                    NULL, now(), NULL,
                    :actor, CAST(:actor_type AS {schema}.actor_user_type_enum),
                    :actor, CAST(:actor_type AS {schema}.actor_user_type_enum)
                )
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "email": email,
                "full_name": full_name,
                "actor": actor_user_id,
                "actor_type": actor_user_type.value,
            },
        )
        new_user_id: UUID = insert_user.scalar_one()

        # Diff against empty current_set collapses to all-INSERT.
        await self._apply_role_assignments_diff(
            session,
            tenant_id=tenant_id,
            tenant_user_id=new_user_id,
            current_set=set(),
            desired_set=set(role_assignments),
            actor_user_id=actor_user_id,
            actor_user_type=actor_user_type,
        )

        # Flush so the aggregate-shaped read sees the writes (the
        # request-scope session has not committed yet).
        await session.flush()

        # Step 6.16.4 audit emission. Success row goes to
        # tenant_activity_audit_logs (route_to_platform=False).
        # Same-transaction with the data write per LD2. Both `auth`
        # and `request_id` are required together: providing only one
        # is a developer bug; repo-level tests that pass neither skip
        # emission cleanly.
        if auth is not None and request_id is not None:
            role_items = await self._resolve_role_labels(
                session, role_assignments
            )
            tenant_name = await self._tenant_name_for(session, tenant_id)
            snapshot = {
                "id": new_user_id,
                "tenant_id": tenant_id,
                "email": email,
                "full_name": full_name,
                "status": "INVITED",
            }
            await emit_audit_event(
                session,
                auth=auth,
                action="CREATE",
                resource_type="TENANT_USER",
                resource_id=new_user_id,
                resource_label=full_name,
                result_type=AuditResultType.SUCCESS,
                details=build_success_details_for_create(
                    snapshot, roles=role_items
                ),
                tenant_id=tenant_id,
                tenant_name=tenant_name,
                request_id=request_id,
                route_to_platform=False,
            )
        elif auth is not None or request_id is not None:
            raise ValueError(
                "auth and request_id must be provided together for audit "
                "emission, or both omitted for repo-level test paths"
            )

        return await self.get_by_id(session, new_user_id)

    async def update(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        fields: dict[str, Any],
        actor_user_id: UUID,
        actor_user_type: ActorUserType,
        auth: AuthContext | None = None,
        request_id: UUID | None = None,
    ) -> TenantUserDetailRow | None:
        """Partial update of one ``tenant_users`` row, with optional
        role diff-replace semantics (Step 6.14 LD3).

        ``fields`` is the caller's ``exclude_unset=True`` dump of the
        Pydantic patch body. Allowed keys: ``full_name``, ``email``,
        ``roles``. ``roles`` is a list of ``(role_id, org_node_id)``
        tuples (caller has converted from the Pydantic
        ``RoleAssignmentItem`` shape).

        Returns ``None`` when the row is missing or RLS-filtered
        (RLS-as-404 per D-17). Caller raises
        ``TenantUserNotFoundError`` on ``None``.

        ``email`` change: pre-checks per-tenant uniqueness excluding
        self via ``uq_tenant_users_tenant_email``.

        ``roles`` change (diff-replace):
          - unchanged (role_id, org_node_id) tuples are NOT touched
            (preserves ``granted_at`` + ``granted_by_*``);
          - tuples in (current − desired) flip to INACTIVE with
            revoked_* populated;
          - tuples in (desired − current) INSERT as new ACTIVE rows.

        ``roles=[]`` (empty desired set) revokes ALL current ACTIVE
        assignments and leaves none.

        Always bumps ``updated_by_user_id`` + ``updated_by_user_type``;
        the BEFORE-UPDATE trigger ``tg_tenant_users_set_updated_at``
        refreshes ``updated_at``.
        """
        schema = get_settings().db_schema

        allowed_keys: frozenset[str] = frozenset({"full_name", "email", "roles"})
        invalid = set(fields.keys()) - allowed_keys
        if invalid:
            raise ValueError(
                f"unexpected update fields: {sorted(invalid)!r}"
            )

        # Pre-check role audience BEFORE any write so we never land a
        # partial update. The shared transaction guarantees atomicity
        # but the early failure keeps the error path clean.
        new_role_assignments: RoleAssignmentList | None = None
        if "roles" in fields:
            new_role_assignments = [
                (UUID(str(p[0])), UUID(str(p[1])))
                for p in fields["roles"]
            ]
            await self._validate_roles(
                session, [rid for (rid, _oid) in new_role_assignments]
            )

        # Look up the row's tenant_id (needed for the email-uniqueness
        # pre-check scope, the org_node validation tenant scope, and
        # the assignment tenant_id column) and full_name + email
        # (needed to capture before-values for the audit row's
        # ``before`` field-level diff per LD8). RLS-as-404: invisible
        # row returns None.
        row = await session.execute(
            text(
                f"SELECT tenant_id, full_name, email "
                f"FROM {schema}.tenant_users WHERE id = :user_id"
            ),
            {"user_id": user_id},
        )
        rec = row.first()
        if rec is None:
            return None
        tenant_id = UUID(str(rec.tenant_id))
        before_full_name = str(rec.full_name)
        before_email = str(rec.email)

        if new_role_assignments is not None:
            await self._validate_org_nodes(
                session,
                tenant_id,
                [oid for (_rid, oid) in new_role_assignments],
            )

        if "email" in fields:
            await self._raise_if_email_taken(
                session,
                tenant_id=tenant_id,
                email=fields["email"],
                exclude_user_id=user_id,
            )

        # Build UPDATE clause for ``tenant_users`` field changes.
        # ``roles`` is not a ``tenant_users`` column; it's handled
        # separately on the role-assignments table.
        col_fields = {
            k: v for k, v in fields.items() if k in ("full_name", "email")
        }
        if col_fields:
            set_parts = [f"{k} = :{k}" for k in col_fields]
            set_parts.append("updated_by_user_id = :actor")
            set_parts.append(
                f"updated_by_user_type = CAST(:actor_type "
                f"AS {schema}.actor_user_type_enum)"
            )
            params: dict[str, Any] = {
                "user_id": user_id,
                "actor": actor_user_id,
                "actor_type": actor_user_type.value,
                **col_fields,
            }
            await session.execute(
                text(
                    f"UPDATE {schema}.tenant_users "
                    f"SET {', '.join(set_parts)} "
                    "WHERE id = :user_id"
                ),
                params,
            )
        elif new_role_assignments is not None:
            # Roles-only PATCH still needs to bump the audit-actor pair
            # on tenant_users so the user's updated_by_* reflects this
            # change. Without this, a roles-only PATCH would leave
            # tenant_users.updated_* unchanged.
            await session.execute(
                text(
                    f"""
                    UPDATE {schema}.tenant_users
                       SET updated_by_user_id = :actor,
                           updated_by_user_type = CAST(:actor_type
                                                AS {schema}.actor_user_type_enum)
                     WHERE id = :user_id
                    """
                ),
                {
                    "user_id": user_id,
                    "actor": actor_user_id,
                    "actor_type": actor_user_type.value,
                },
            )

        # Roles diff-replace: lock current ACTIVE, compute diff, apply.
        before_roles_set: set[RoleAssignmentTuple] | None = None
        after_roles_set: set[RoleAssignmentTuple] | None = None
        if new_role_assignments is not None:
            current_set = (
                await self._select_current_active_assignments_for_update(
                    session, user_id
                )
            )
            desired_set = set(new_role_assignments)
            before_roles_set = current_set
            after_roles_set = desired_set
            await self._apply_role_assignments_diff(
                session,
                tenant_id=tenant_id,
                tenant_user_id=user_id,
                current_set=current_set,
                desired_set=desired_set,
                actor_user_id=actor_user_id,
                actor_user_type=actor_user_type,
            )

        # Raw UPDATE bypasses SA ORM; expire so the in-session
        # TenantUser identity-map entry doesn't return stale fields.
        session.expire_all()
        result_row = await self.get_by_id(session, user_id)

        # Step 6.16.4 audit emission. Normal routing (tenant_id set;
        # route_to_platform=False). Same-transaction success row.
        # ``before`` / ``after`` carry only the changed field-level
        # columns (full_name / email) plus, when a roles diff fired,
        # the full before+after role lists per Phase 1 Q1.
        if (
            auth is not None
            and request_id is not None
            and result_row is not None
        ):
            after_full_name = str(result_row.user.full_name)
            before_payload: dict[str, Any] = {}
            after_payload: dict[str, Any] = {}
            if "full_name" in fields:
                before_payload["full_name"] = before_full_name
                after_payload["full_name"] = after_full_name
            if "email" in fields:
                before_payload["email"] = before_email
                after_payload["email"] = str(result_row.user.email)
            before_roles_items: list[dict[str, Any]] | None = None
            after_roles_items: list[dict[str, Any]] | None = None
            if before_roles_set is not None and after_roles_set is not None:
                # Resolve names for the UNION; partition into before
                # and after preserving each pair (frozen labels at
                # write time per LD9). Calling _resolve_role_labels
                # twice keeps each list ordered by the pair sequence
                # that the auditor most naturally reads.
                before_roles_items = await self._resolve_role_labels(
                    session, sorted(before_roles_set)
                )
                after_roles_items = await self._resolve_role_labels(
                    session, sorted(after_roles_set)
                )
            tenant_name = await self._tenant_name_for(session, tenant_id)
            await emit_audit_event(
                session,
                auth=auth,
                action="UPDATE",
                resource_type="TENANT_USER",
                resource_id=user_id,
                resource_label=after_full_name,
                result_type=AuditResultType.SUCCESS,
                details=build_success_details_for_update(
                    before_payload,
                    after_payload,
                    before_roles=before_roles_items,
                    after_roles=after_roles_items,
                ),
                tenant_id=tenant_id,
                tenant_name=tenant_name,
                request_id=request_id,
                route_to_platform=False,
            )
        elif auth is not None or request_id is not None:
            raise ValueError(
                "auth and request_id must be provided together for audit "
                "emission, or both omitted for repo-level test paths"
            )

        return result_row

    async def transition(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        target_status: Literal["SUSPENDED", "ACTIVE"],
        actor_user_id: UUID,
        actor_user_type: ActorUserType,
        auth: AuthContext | None = None,
        request_id: UUID | None = None,
    ) -> tuple[TenantUserDetailRow | None, TransitionResult]:
        """Atomic status transition for one ``tenant_users`` row.

        Returns ``(row | None, result)``:
          - ``(None, NOT_FOUND)`` when the row is missing or RLS-filtered.
          - ``(None, INVALID_STATE)`` when the current status doesn't
            permit the requested transition.
          - ``(row, OK)`` after a successful UPDATE.

        Allowed sources (locked decision 7):
          - SUSPENDED <= ACTIVE only.
          - ACTIVE    <= SUSPENDED only.

        INVITED is intentionally not a source for either direction:
        INVITED -> SUSPENDED is structurally rejected by
        ``ck_tenant_users_auth0_sub_consistency`` (SUSPENDED requires
        ``auth0_sub`` non-NULL; INVITED requires NULL). INVITED ->
        ACTIVE is the Auth0 invite-accept callback flow (out of scope
        v0). Both map to ``INVALID_STATE`` at this layer so the
        client never sees a 500.

        Pattern (b) audit-actor: ``updated_by_user_id`` +
        ``updated_by_user_type`` always; ``suspended_by_user_id`` +
        ``suspended_by_user_type`` set on ACTIVE->SUSPENDED, cleared
        atomically on SUSPENDED->ACTIVE per
        ``ck_tenant_users_suspended_consistency``.

        SELECT FOR UPDATE locks the row inside the request transaction
        so a concurrent suspend / activate doesn't race.
        """
        schema = get_settings().db_schema

        row = await session.execute(
            text(
                f"SELECT status, tenant_id, full_name "
                f"FROM {schema}.tenant_users "
                "WHERE id = :user_id FOR UPDATE"
            ),
            {"user_id": user_id},
        )
        current = row.first()
        if current is None:
            return None, TransitionResult.NOT_FOUND

        allowed_sources: dict[str, frozenset[str]] = {
            "SUSPENDED": frozenset({"ACTIVE"}),
            "ACTIVE": frozenset({"SUSPENDED"}),
        }
        if current.status not in allowed_sources[target_status]:
            return None, TransitionResult.INVALID_STATE
        before_status = str(current.status)
        tenant_id = UUID(str(current.tenant_id))
        full_name_snapshot = str(current.full_name)

        if target_status == "SUSPENDED":
            await session.execute(
                text(
                    f"""
                    UPDATE {schema}.tenant_users
                       SET status = CAST('SUSPENDED'
                                    AS {schema}.tenant_user_status_enum),
                           suspended_at = now(),
                           suspended_by_user_id = :actor,
                           suspended_by_user_type = CAST(:actor_type
                                                    AS {schema}.actor_user_type_enum),
                           updated_by_user_id = :actor,
                           updated_by_user_type = CAST(:actor_type
                                                  AS {schema}.actor_user_type_enum)
                     WHERE id = :user_id
                    """
                ),
                {
                    "actor": actor_user_id,
                    "actor_type": actor_user_type.value,
                    "user_id": user_id,
                },
            )
        else:  # target_status == "ACTIVE"
            await session.execute(
                text(
                    f"""
                    UPDATE {schema}.tenant_users
                       SET status = CAST('ACTIVE'
                                    AS {schema}.tenant_user_status_enum),
                           suspended_at = NULL,
                           suspended_by_user_id = NULL,
                           suspended_by_user_type = NULL,
                           updated_by_user_id = :actor,
                           updated_by_user_type = CAST(:actor_type
                                                  AS {schema}.actor_user_type_enum)
                     WHERE id = :user_id
                    """
                ),
                {
                    "actor": actor_user_id,
                    "actor_type": actor_user_type.value,
                    "user_id": user_id,
                },
            )

        # Raw UPDATE bypasses SA ORM; expire so the in-session
        # TenantUser identity-map entry doesn't return stale
        # status / suspended_*.
        session.expire_all()
        result_row = await self.get_by_id(session, user_id)

        # Step 6.16.4 audit emission. Normal routing (tenant_id set;
        # route_to_platform=False). Same-transaction success row.
        # ``action`` is SUSPEND or ACTIVATE per ``target_status``.
        if (
            auth is not None
            and request_id is not None
            and result_row is not None
        ):
            action_code = (
                "SUSPEND" if target_status == "SUSPENDED" else "ACTIVATE"
            )
            tenant_name = await self._tenant_name_for(session, tenant_id)
            await emit_audit_event(
                session,
                auth=auth,
                action=action_code,
                resource_type="TENANT_USER",
                resource_id=user_id,
                resource_label=full_name_snapshot,
                result_type=AuditResultType.SUCCESS,
                details=build_success_details_for_transition(
                    before_status=before_status,
                    after_status=target_status,
                ),
                tenant_id=tenant_id,
                tenant_name=tenant_name,
                request_id=request_id,
                route_to_platform=False,
            )
        elif auth is not None or request_id is not None:
            raise ValueError(
                "auth and request_id must be provided together for audit "
                "emission, or both omitted for repo-level test paths"
            )

        return result_row, TransitionResult.OK

    async def accept_invitation(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        tenant_id: UUID,
        auth0_sub: str,
        auth: AuthContext | None = None,
        request_id: UUID | None = None,
    ) -> tuple[TenantUserDetailRow | None, AcceptInvitationResult]:
        """Self-service invite-accept (Step CI-4c): flip the caller's own row
        INVITED -> ACTIVE and record ``auth0_sub`` in ONE atomic UPDATE.

        ``auth0_sub`` is the caller's VERIFIED token sub (the endpoint passes
        ``auth.sub``); ``user_id`` / ``tenant_id`` are the verified claims. None of
        these come from request input. The single guarded UPDATE (``WHERE status =
        'INVITED'`` and the status flip + ``auth0_sub`` write together) is
        constraint-safe (``ck_tenant_users_auth0_sub_consistency`` is satisfied at
        both ends) and idempotent (a repeat call matches no INVITED row).

        This does NOT use ``transition()`` (which excludes INVITED and never writes
        ``auth0_sub``). Audit (action ``ACCEPT_INVITATION``, actor = the user
        themselves, pattern (b)) is emitted same-transaction ONLY on the activated
        case. Returns ``(row, ACTIVATED)`` on the flip; otherwise ``(None, ...)``
        with the classified outcome.
        """
        schema = get_settings().db_schema

        result = await session.execute(
            text(
                f"""
                UPDATE {schema}.tenant_users
                   SET status = CAST('ACTIVE'
                                AS {schema}.tenant_user_status_enum),
                       auth0_sub = :auth0_sub,
                       invitation_accepted_at = now(),
                       updated_by_user_id = :user_id,
                       updated_by_user_type = CAST('TENANT'
                                              AS {schema}.actor_user_type_enum)
                 WHERE id = :user_id
                   AND status = CAST('INVITED'
                                AS {schema}.tenant_user_status_enum)
                   AND tenant_id = :tenant_id
                """
            ),
            {"auth0_sub": auth0_sub, "user_id": user_id, "tenant_id": tenant_id},
        )

        # A DML statement's session.execute returns a CursorResult whose
        # ``rowcount`` is the matched-row count; the async execute() return is typed
        # as the base Result (no rowcount), so this access is type-ignored narrowly.
        if result.rowcount == 1:  # type: ignore[attr-defined]
            # Raw UPDATE bypasses the ORM identity map; expire so a re-read is fresh.
            session.expire_all()
            result_row = await self.get_by_id(session, user_id)

            # Same-transaction audit (LD2), activated case only. Actor = the user
            # (self-activation, pattern (b) updated_by). Mirrors transition().
            if (
                auth is not None
                and request_id is not None
                and result_row is not None
            ):
                tenant_name = await self._tenant_name_for(session, tenant_id)
                await emit_audit_event(
                    session,
                    auth=auth,
                    action="ACCEPT_INVITATION",
                    resource_type="TENANT_USER",
                    resource_id=user_id,
                    resource_label=result_row.user.full_name,
                    result_type=AuditResultType.SUCCESS,
                    details=build_success_details_for_transition(
                        before_status="INVITED",
                        after_status="ACTIVE",
                    ),
                    tenant_id=tenant_id,
                    tenant_name=tenant_name,
                    request_id=request_id,
                    route_to_platform=False,
                )
            elif auth is not None or request_id is not None:
                raise ValueError(
                    "auth and request_id must be provided together for audit "
                    "emission, or both omitted for repo-level test paths"
                )

            return result_row, AcceptInvitationResult.ACTIVATED

        # 0 rows updated: classify by the row's current status (no lock needed; the
        # guarded UPDATE already failed to match). RLS scopes this to the caller's
        # tenant; the explicit tenant_id keeps it self-only.
        current = (
            await session.execute(
                text(
                    f"SELECT status FROM {schema}.tenant_users "
                    "WHERE id = :user_id AND tenant_id = :tenant_id"
                ),
                {"user_id": user_id, "tenant_id": tenant_id},
            )
        ).first()

        if current is None:
            return None, AcceptInvitationResult.NOT_FOUND
        status = str(current.status)
        if status == "ACTIVE":
            return None, AcceptInvitationResult.ALREADY_ACTIVE
        if status == "INVITED":
            # Still INVITED after a 0-row UPDATE: a concurrent accept raced between
            # the UPDATE and this read. Do not misclassify it as already-active or
            # not-invited; surface a transient, retryable conflict.
            return None, AcceptInvitationResult.CONFLICT
        # SUSPENDED (or any other non-acceptable state): cannot self-accept.
        return None, AcceptInvitationResult.NOT_INVITED
