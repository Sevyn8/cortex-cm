"""RolesRepo — read-only data access for ``roles`` and the role -> permissions JOIN.

Backs E1 (``GET /api/v1/roles``) and E3
(``GET /api/v1/roles/{role_id}/permissions``).

``roles`` is platform-global; no RLS. Visibility is controlled at the
app layer via the ``audience`` column. The router computes
``audience_filter`` from ``AuthContext.user_type`` (TENANT JWTs ->
``'TENANT'``; PLATFORM JWTs -> ``None``); the Repo applies it as a
WHERE clause. Distinct from RLS scoping but the intent is the same:
TENANT users see only TENANT-audience rows.

Per D-24, no ``tenant_id`` parameter on visibility-bearing methods —
visibility is the session/auth's job, not the Repo's. The
``audience_filter`` argument is the app-layer parallel for non-RLS
tables.

The user_count correlated subquery is the load-bearing piece of E1.
Post Step 6.8.2 it sums two independent correlated scalar subqueries
(one per physical assignment table) at the column-expression layer.
``.correlate(Role)`` is applied to EACH inner subquery (Step 3.3 L9
/ Step 5.3 L11 / Step 6.1 R4 lesson — third occurrence; this time
on TWO subqueries instead of one). For TENANT JWTs, the
``tenant_user_role_assignments`` branch inherits the request's
session GUCs and RLS scopes the count to the calling tenant
(D-29 unconditional OR-branch). The
``platform_user_role_assignments`` branch has no RLS — every session
sees all platform-side assignments. Audience-check triggers ensure
PLATFORM-audience roles only carry rows on the platform table and
TENANT-audience roles only on the tenant table; the other branch
contributes 0 by construction.

Stateless singleton at module level (mirrors ``TenantsRepo`` /
``PlatformUsersRepo`` / ``TenantUsersRepo`` shape).
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.audit.emit import (
    build_success_details_for_update,
    emit_audit_event,
)
from admin_backend.auth.context import AuthContext
from admin_backend.config import get_settings
from admin_backend.errors import (
    AudienceScopeMismatchError,
    EmptyPatchError,
    InternalInvariantViolationError,
    InvalidPermissionError,
    LastOverrideHolderError,
    RoleArchivedError,
    SuperAdminProtectedError,
)
from admin_backend.models import (
    PlatformUserRoleAssignment,
    Role,
    TenantUserRoleAssignment,
)
from admin_backend.models.audit_log import AuditResultType
from admin_backend.models.permission import Permission
from admin_backend.models.role_permission import RolePermission
from admin_backend.models.tenant_user import ActorUserType
from admin_backend.repositories._errors import InvalidSortKeyError
from admin_backend.schemas.role import RoleUpdateRequest


# Permission code for the platform-admin bootstrap invariant. Captured
# as a module-level constant so the two-layer invariant queries (Step
# 6.18.3 LD6) bind the same string in both Layer 1 and Layer 2.
OVERRIDE_GLOBAL_CODE = "ADMIN.ROLES.OVERRIDE.GLOBAL"


# Sort keys for E1's role list. A stable tiebreaker by ``id ASC`` is
# appended at query time so identical primary-sort values page
# deterministically. ``dict[str, Any]`` rather than the inferred
# ``dict[str, object]`` per the same mypy nuance documented in
# platform_users.SORT_MAP.
SORT_MAP: dict[str, Any] = {
    "name_asc": Role.name.asc(),
    "name_desc": Role.name.desc(),
    "created_at_asc": Role.created_at.asc(),
    "created_at_desc": Role.created_at.desc(),
}


def _user_count_subquery() -> Any:
    """Correlated count of ACTIVE assignments for the outer ``Role``
    row, summed across both physical assignment tables.

    Implementation: TWO independent correlated scalar subqueries
    (one per physical table), summed at the column-expression layer.
    Cleaner than UNION-then-SUM because each ``.correlate(Role)``
    is on a single subquery (more obvious; easier to read; harder to
    get wrong) and there's no extra subquery wrapper around a UNION.
    SQLAlchemy emits two scalar subselects added at SQL level —
    Postgres optimises this efficiently.

    Both subqueries MUST ``.correlate(Role)``. The R4 test verifies
    per-row correlation; without ``correlate(Role)``, the subqueries
    execute once per query (returning a global total) instead of
    once per row.

    For TENANT JWTs: the tenant-side branch inherits the request's
    session GUCs and RLS scopes the count to the calling tenant
    automatically (D-29 unconditional OR-branch). The platform-side
    branch has no RLS — every session sees all platform-side
    assignments. Audience-check triggers ensure PLATFORM-audience
    roles only have entries on the platform table; TENANT-audience
    roles only on the tenant table. The other branch contributes 0
    by construction.

    Returns a SQLAlchemy column expression that callers wrap in
    ``.label("user_count")`` — same contract as the previous helper.
    """
    platform_count_subq = (
        select(func.count(PlatformUserRoleAssignment.id))
        .where(PlatformUserRoleAssignment.role_id == Role.id)
        .where(PlatformUserRoleAssignment.status == "ACTIVE")
        .correlate(Role)
        .scalar_subquery()
    )
    tenant_count_subq = (
        select(func.count(TenantUserRoleAssignment.id))
        .where(TenantUserRoleAssignment.role_id == Role.id)
        .where(TenantUserRoleAssignment.status == "ACTIVE")
        .correlate(Role)
        .scalar_subquery()
    )
    # Sum the two scalars; either may return 0 in normal operation.
    # The audience-check triggers guarantee a role's assignments live
    # in exactly one of the two tables, so one branch is always zero
    # for any given role row.
    return platform_count_subq + tenant_count_subq


class RolesRepo:
    """Read-only repository for ``roles`` (and the role -> permissions JOIN)."""

    async def list_grouped(
        self,
        session: AsyncSession,
        *,
        audience_filter: str | None,
        status: str | None = None,
        is_system: bool | None = None,
        q: str | None = None,
        sort: str = "name_asc",
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, tuple[list[tuple[Role, int]], int]]:
        """Return ``{'PLATFORM': (rows, total), 'TENANT': (rows, total)}``.

        For TENANT-JWT callers (``audience_filter='TENANT'``), the
        PLATFORM bucket is short-circuited to ``([], 0)`` so we don't
        run the same query twice. The handler renders the pre-grouped
        response shape from this output.

        Each ``rows`` entry is ``(Role, user_count)`` so the handler
        can hydrate ``RoleListItem`` directly without a second pass.

        Filters layered onto the audience filter:
          - ``status``: defaults to ACTIVE if None (most-common case).
          - ``is_system``: filter by Ithina-system flag.
          - ``q``: case-insensitive ILIKE across name/code/description.
          - ``sort``: one of SORT_MAP keys.
          - ``offset``/``limit``: present for consistency, never
            paginates in v0 (the catalogue fits in one page).
        """
        if sort not in SORT_MAP:
            raise InvalidSortKeyError(f"unknown sort key: {sort}")

        # Status defaults to ACTIVE so the catalogue tab renders the
        # commonly-relevant rows by default. Callers pass a specific
        # value (INACTIVE, ARCHIVED) to widen.
        effective_status = status if status is not None else "ACTIVE"

        out: dict[str, tuple[list[tuple[Role, int]], int]] = {
            "PLATFORM": ([], 0),
            "TENANT": ([], 0),
        }

        audiences_to_query = (
            ["PLATFORM", "TENANT"]
            if audience_filter is None
            else [audience_filter]
        )

        for audience in audiences_to_query:
            conditions = [
                Role.audience == audience,
                Role.status == effective_status,
            ]
            if is_system is not None:
                conditions.append(Role.is_system == is_system)
            if q:
                pat = f"%{q}%"
                conditions.append(
                    or_(
                        Role.name.ilike(pat),
                        Role.code.ilike(pat),
                        Role.description.ilike(pat),
                    )
                )

            count_stmt = select(func.count()).select_from(Role).where(*conditions)
            total_result = await session.execute(count_stmt)
            total: int = total_result.scalar_one()

            user_count_col = _user_count_subquery().label("user_count")
            stmt = (
                select(Role, user_count_col)
                .where(*conditions)
                .order_by(SORT_MAP[sort], Role.id.asc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows: list[tuple[Role, int]] = [
                (row[0], row[1]) for row in result.all()
            ]
            out[audience] = (rows, total)

        return out

    async def get_by_id(
        self,
        session: AsyncSession,
        role_id: UUID,
        *,
        audience_filter: str | None,
    ) -> tuple[Role, int] | None:
        """Single-role lookup with optional audience gate.

        Returns ``None`` if the role doesn't exist OR if
        ``audience_filter`` excludes it. The router converts ``None``
        to 404 ``ROLE_NOT_FOUND`` (RP3 load-bearing — TENANT JWTs
        cannot probe whether a PLATFORM-audience role id exists).
        """
        user_count_col = _user_count_subquery().label("user_count")
        stmt = select(Role, user_count_col).where(Role.id == role_id)
        if audience_filter is not None:
            stmt = stmt.where(Role.audience == audience_filter)
        result = await session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        return (row[0], row[1])

    async def list_permissions_for_role(
        self,
        session: AsyncSession,
        role_id: UUID,
    ) -> list[Permission]:
        """JOIN role_permissions with permissions. Sorted module/
        resource/action/scope ascending. Used by E3.

        Caller MUST confirm via ``get_by_id`` that the role exists and
        is audience-visible before calling this — otherwise a TENANT
        JWT could enumerate a PLATFORM-audience role's permissions.
        """
        stmt = (
            select(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
            .order_by(
                Permission.module.asc(),
                Permission.resource.asc(),
                Permission.action.asc(),
                Permission.scope.asc(),
                Permission.id.asc(),
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_detail_by_id(
        self,
        session: AsyncSession,
        role_id: UUID,
        *,
        audience_filter: str | None,
    ) -> dict[str, Any] | None:
        """Return role metadata + user_count + held permissions (with
        labels) + available permissions (with labels; audience-scope
        filtered) for the role-edit screen (E7).

        Returns ``None`` if the role doesn't exist OR if
        ``audience_filter`` excludes it (RLS-as-404 anti-information-
        disclosure per D-17). The router converts ``None`` to 404
        ``ROLE_NOT_FOUND``.

        Audience-scope coherence (LD2): TENANT-audience roles cannot
        hold ``scope='GLOBAL'`` grants; the available set excludes
        GLOBAL rows for those roles.

        Three sequential queries:
          1. Role lookup with user_count (mirrors ``get_by_id``).
          2. Held permissions with labels (4 LEFT JOINs on ``lookups``).
          3. Available permissions with labels (same JOIN block; WHERE
             NOT IN held set; optional scope != 'GLOBAL').
        """
        role_or_none = await self.get_by_id(
            session, role_id, audience_filter=audience_filter
        )
        if role_or_none is None:
            return None
        role, user_count = role_or_none

        held_permissions = await _select_permissions_with_labels(
            session,
            role_id=role.id,
            only_held=True,
            exclude_global=False,
        )
        # LD2: TENANT-audience roles cannot hold GLOBAL-scope
        # permissions. ``role.audience`` hydrates to a ``RoleAudience``
        # enum which subclasses ``str``; the equality compare against
        # the literal string works for both the enum instance and the
        # raw string form.
        exclude_global = role.audience == "TENANT"
        available_permissions = await _select_permissions_with_labels(
            session,
            role_id=role.id,
            only_held=False,
            exclude_global=exclude_global,
        )

        return {
            "id": role.id,
            "name": role.name,
            "code": role.code,
            "description": role.description,
            "audience": role.audience,
            "status": role.status,
            "is_system": role.is_system,
            "user_count": user_count,
            "created_at": role.created_at,
            "updated_at": role.updated_at,
            "permissions": held_permissions,
            "available_permissions": available_permissions,
        }


    async def update(
        self,
        session: AsyncSession,
        role_id: UUID,
        *,
        body: RoleUpdateRequest,
        actor_user_id: UUID,
        actor_user_type: ActorUserType,
        auth: AuthContext | None = None,
        request_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        """Apply PATCH to a role; return updated RoleDetail-shaped dict.

        Order of operations per LD17. Each step short-circuits with a
        named typed error; the session-dep rolls back on any exception
        escape. Returns ``None`` only if the role doesn't exist (RLS-
        as-404 anti-information-disclosure per D-17); the router raises
        ``RoleNotFoundError`` on ``None``.

        Layer 2 OVERRIDE.GLOBAL tripwire runs after the writes complete
        but before this method returns. If the post-write state
        violates the invariant despite Layer 1 saying it was safe,
        ``InternalInvariantViolationError`` propagates (no explicit
        ROLLBACK — the session-dep does it).

        PATCH is PLATFORM-only by gate construction; the
        ``audience_filter`` argument on the lookup is hardcoded to
        ``None`` (no app-layer audience gate).
        """
        schema = get_settings().db_schema

        # Step 1: fetch role. PATCH gate is PLATFORM-only so no
        # audience filter is applied at the lookup; cross-audience
        # cases are impossible at the gate level.
        role_or_none = await self.get_by_id(
            session, role_id, audience_filter=None
        )
        if role_or_none is None:
            return None
        role, _user_count = role_or_none

        # Step 6.16.4 LD8 / LD9: snapshot the role's pre-write
        # ``name`` and ``description`` for the audit row's ``before``
        # field-level diff. These attributes will not survive the
        # later ``session.expire_all()``; capture them now while the
        # ORM identity-map entry is still hot.
        before_name = str(role.name)
        before_description = (
            str(role.description) if role.description is not None else None
        )

        # Step 2: SUPER_ADMIN protection (LD12 / LD18 — fires BEFORE
        # status check; SUPER_ADMIN is uneditable even if ARCHIVED).
        if role.code == "SUPER_ADMIN":
            raise SuperAdminProtectedError(
                "PATCH refused on SUPER_ADMIN role",
                role_id=str(role.id),
                role_code=role.code,
            )

        # Step 3: ARCHIVED state rejection (LD3).
        if role.status == "ARCHIVED":
            raise RoleArchivedError(
                f"PATCH refused on ARCHIVED role {role.id}",
                role_id=str(role.id),
            )

        # Step 4: empty-body guard.
        sent_fields = body.model_dump(exclude_unset=True)
        if not sent_fields:
            raise EmptyPatchError(
                f"PATCH on role {role_id} had no set fields",
                role_id=str(role.id),
            )

        # Step 5: permission-set processing.
        new_perm_ids: set[UUID] | None = None
        current_perm_ids: set[UUID] = set()
        added_ids: set[UUID] = set()
        removed_ids: set[UUID] = set()

        if "permission_ids" in sent_fields:
            raw_new_ids = sent_fields["permission_ids"]
            new_perm_ids = set(raw_new_ids) if raw_new_ids else set()

            # 5a: permission existence pre-check (LD11). Empty set
            # short-circuits (legitimate "remove all perms" case;
            # no IDs to verify).
            if new_perm_ids:
                existing_perm_rows = await session.execute(
                    text(
                        f"SELECT id FROM {schema}.permissions "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": list(new_perm_ids)},
                )
                found_ids = {row[0] for row in existing_perm_rows.all()}
                missing = new_perm_ids - found_ids
                if missing:
                    raise InvalidPermissionError(
                        f"permission_ids contain unknown UUIDs for role {role.id}",
                        role_id=str(role.id),
                        missing_ids=[str(m) for m in missing],
                    )

            # 5b: current grants -> compute diff.
            current_rows = await session.execute(
                text(
                    f"SELECT permission_id FROM {schema}.role_permissions "
                    "WHERE role_id = :role_id"
                ),
                {"role_id": role.id},
            )
            current_perm_ids = {row[0] for row in current_rows.all()}
            added_ids = new_perm_ids - current_perm_ids
            removed_ids = current_perm_ids - new_perm_ids

            # 5c: audience-scope coherence (LD10). Lenient — only the
            # added set is inspected.
            if role.audience == "TENANT" and added_ids:
                offending_rows = await session.execute(
                    text(
                        f"SELECT id FROM {schema}.permissions "
                        "WHERE id = ANY(:ids) AND scope = 'GLOBAL'"
                    ),
                    {"ids": list(added_ids)},
                )
                offending = [row[0] for row in offending_rows.all()]
                if offending:
                    raise AudienceScopeMismatchError(
                        f"TENANT-audience role {role.id} cannot add "
                        "GLOBAL-scope permissions",
                        role_id=str(role.id),
                        role_audience="TENANT",
                        offending_permission_ids=[str(p) for p in offending],
                    )

            # 5d: Layer 1 OVERRIDE invariant pre-check (LD6, LD8, LD9).
            # Optimisation: only run if edit removes OVERRIDE.GLOBAL
            # from the current holder. If the role doesn't currently
            # hold OVERRIDE or will continue to hold it, the invariant
            # is unchanged or strengthened.
            override_perm_id = await _resolve_override_global_permission_id(
                session
            )
            currently_holds = override_perm_id in current_perm_ids
            will_hold = override_perm_id in new_perm_ids
            if currently_holds and not will_hold:
                # Edit removes OVERRIDE from this role. Count active
                # holders through OTHER roles (Layer 1: exclude
                # role-under-edit).
                layer_1_count = await _count_override_global_active_holders(
                    session, exclude_role_id=role.id
                )
                if layer_1_count == 0:
                    raise LastOverrideHolderError(
                        "edit would leave zero active holders of "
                        f"{OVERRIDE_GLOBAL_CODE}",
                        role_id=str(role.id),
                    )

        # Step 6: UPDATE roles row. Always bumps updated_by_*.
        # ``updated_at`` is refreshed by ``tg_roles_set_updated_at``
        # trigger; SET clause omits it.
        set_parts = [
            "updated_by_user_id = :actor",
            f"updated_by_user_type = CAST(:actor_type AS {schema}.actor_user_type_enum)",
        ]
        params: dict[str, Any] = {
            "actor": actor_user_id,
            "actor_type": actor_user_type.value,
            "role_id": role.id,
        }
        if "name" in sent_fields and sent_fields["name"] is not None:
            set_parts.append("name = :name")
            params["name"] = sent_fields["name"]
        if "description" in sent_fields:
            set_parts.append("description = :description")
            params["description"] = sent_fields["description"]

        await session.execute(
            text(
                f"UPDATE {schema}.roles SET {', '.join(set_parts)} "
                "WHERE id = :role_id"
            ),
            params,
        )

        # Step 7: DELETE removed role_permissions rows.
        if removed_ids:
            await session.execute(
                text(
                    f"DELETE FROM {schema}.role_permissions "
                    "WHERE role_id = :role_id "
                    "AND permission_id = ANY(:perm_ids)"
                ),
                {
                    "role_id": role.id,
                    "perm_ids": list(removed_ids),
                },
            )

        # Step 8: INSERT added role_permissions rows with audit-actor
        # pair populated (LD14, Pattern (b) per D-13). The DDL CHECK
        # ``ck_role_permissions_created_by_actor_pair`` enforces
        # both-or-neither on the audit-actor pair.
        if added_ids:
            await session.execute(
                text(
                    f"INSERT INTO {schema}.role_permissions ("
                    "  role_id, permission_id,"
                    "  created_by_user_id, created_by_user_type"
                    ") SELECT :role_id, perm_id, :actor, "
                    f"CAST(:actor_type AS {schema}.actor_user_type_enum) "
                    "FROM UNNEST(CAST(:perm_ids AS UUID[])) AS perm_id"
                ),
                {
                    "role_id": role.id,
                    "perm_ids": list(added_ids),
                    "actor": actor_user_id,
                    "actor_type": actor_user_type.value,
                },
            )

        # Capture the role's id BEFORE expiring the identity map so
        # the post-write reads can reuse the function parameter cleanly
        # (accessing ``role.id`` after ``expire_all()`` in an async
        # session can fire a lazy-load and raise MissingGreenlet).
        captured_role_id = role.id

        # Raw UPDATE/INSERT/DELETE bypass SA ORM; expire identity-map
        # caches so the materialising read returns fresh data.
        session.expire_all()

        # Step 9: Layer 2 OVERRIDE invariant tripwire (LD6 LD8). Reads
        # actual committed-to-be state (no exclude_role_id). Should
        # always pass if Layer 1 was logically correct; failure
        # indicates a Layer 1 bug or a write defect.
        if new_perm_ids is not None:
            # The override permission id can have rotated only via a
            # post-Layer-1 catalogue mutation (race). We re-resolve
            # defensively.
            override_perm_id_check = await _resolve_override_global_permission_id(
                session
            )
            layer_2_count = await _count_override_global_active_holders(
                session, exclude_role_id=None
            )
            if layer_2_count == 0:
                # Step 6.16.4 LD12: name the specific invariant so the
                # failure-path audit row carries the ``invariant``
                # sub-key inside its INTERNAL_ERROR ``details`` payload.
                raise InternalInvariantViolationError(
                    "Layer 2 OVERRIDE.GLOBAL tripwire fired",
                    role_id=str(captured_role_id),
                    override_perm_id=str(override_perm_id_check),
                    layer_2_count=layer_2_count,
                    invariant="OVERRIDE_GLOBAL_HOLDER_PRESERVATION",
                )

        # Step 10: re-fetch RoleDetail for the response.
        detail = await self.get_detail_by_id(
            session, captured_role_id, audience_filter=None
        )

        # Step 6.16.4 audit emission. Roles are platform-scope
        # catalogue rows; ``tenant_id`` is NULL and the row routes to
        # ``platform_activity_audit_logs`` per LD7 (route_to_platform
        # =True). Same-transaction success row.
        if (
            auth is not None
            and request_id is not None
            and detail is not None
        ):
            after_name = (
                str(sent_fields["name"])
                if "name" in sent_fields and sent_fields["name"] is not None
                else before_name
            )
            before_payload: dict[str, Any] = {}
            after_payload: dict[str, Any] = {}
            if "name" in sent_fields and sent_fields["name"] is not None:
                before_payload["name"] = before_name
                after_payload["name"] = str(sent_fields["name"])
            if "description" in sent_fields:
                before_payload["description"] = before_description
                after_payload["description"] = (
                    str(sent_fields["description"])
                    if sent_fields["description"] is not None
                    else None
                )
            before_perms: list[dict[str, Any]] | None = None
            after_perms: list[dict[str, Any]] | None = None
            if new_perm_ids is not None:
                # Resolve permission codes for the UNION; partition
                # into before+after preserving each id (frozen labels
                # at write time per LD9). Stable sort for reader
                # predictability.
                union_ids = current_perm_ids | new_perm_ids
                code_by_id: dict[UUID, str] = {}
                if union_ids:
                    perm_rows = await session.execute(
                        text(
                            f"SELECT id, code FROM {schema}.permissions "
                            "WHERE id = ANY(:ids)"
                        ),
                        {"ids": list(union_ids)},
                    )
                    code_by_id = {
                        UUID(str(r.id)): str(r.code) for r in perm_rows
                    }
                before_perms = [
                    {
                        "permission_id": str(pid),
                        "permission_code": code_by_id.get(pid),
                    }
                    for pid in sorted(current_perm_ids, key=lambda u: str(u))
                ]
                after_perms = [
                    {
                        "permission_id": str(pid),
                        "permission_code": code_by_id.get(pid),
                    }
                    for pid in sorted(new_perm_ids, key=lambda u: str(u))
                ]
            await emit_audit_event(
                session,
                auth=auth,
                action="UPDATE",
                resource_type="ROLE",
                resource_id=captured_role_id,
                resource_label=after_name,
                result_type=AuditResultType.SUCCESS,
                details=build_success_details_for_update(
                    before_payload,
                    after_payload,
                    before_permissions=before_perms,
                    after_permissions=after_perms,
                ),
                tenant_id=None,
                tenant_name=None,
                request_id=request_id,
                route_to_platform=True,
            )
        elif auth is not None or request_id is not None:
            raise ValueError(
                "auth and request_id must be provided together for audit "
                "emission, or both omitted for repo-level test paths"
            )

        return detail


async def _select_permissions_with_labels(
    session: AsyncSession,
    *,
    role_id: UUID,
    only_held: bool,
    exclude_global: bool,
) -> list[dict[str, Any]]:
    """Load permission rows with display labels via 4 LEFT JOINs on
    ``core.lookups`` (mirror of ``permission_matrix.py``'s pattern at
    LD4).

    Two modes:
      - ``only_held=True``: INNER JOIN ``role_permissions`` so only
        rows granted to ``role_id`` are returned.
      - ``only_held=False``: filter to rows NOT in the held set; this
        is the "available to grant" set. ``exclude_global=True``
        additionally drops ``scope='GLOBAL'`` rows (LD2).

    Sort: module/resource/action/scope/code/id ascending (matches
    ``list_permissions_for_role``'s ORDER BY per LD8). All sort fields
    are enum-ordinal where applicable.

    Schema-qualified per CSD-03 (raw ``text()`` bypasses the
    ``__table_args__["schema"]`` pathway; ``get_settings().db_schema``
    is the same source the ORM uses).
    """
    schema = get_settings().db_schema

    if only_held:
        where_clause = "WHERE rp.role_id = :role_id"
        from_join = (
            f"FROM {schema}.permissions AS p "
            f"INNER JOIN {schema}.role_permissions AS rp "
            "  ON rp.permission_id = p.id"
        )
    else:
        # Exclude permissions already held by this role; additional
        # GLOBAL-scope filter for TENANT-audience roles per LD2.
        clauses = [
            "p.id NOT IN ("
            f"SELECT permission_id FROM {schema}.role_permissions "
            "WHERE role_id = :role_id"
            ")"
        ]
        if exclude_global:
            clauses.append("p.scope != 'GLOBAL'")
        where_clause = "WHERE " + " AND ".join(clauses)
        from_join = f"FROM {schema}.permissions AS p"

    sql = text(
        f"""
        SELECT
            p.id AS id,
            p.module::text   AS module,
            p.resource::text AS resource,
            p.action::text   AS action,
            p.scope::text    AS scope,
            p.code AS code,
            p.description AS description,
            COALESCE(lk_module.display_name,   p.module::text)   AS module_label,
            COALESCE(lk_resource.display_name, p.resource::text) AS resource_label,
            COALESCE(lk_action.display_name,   p.action::text)   AS action_label,
            COALESCE(lk_scope.display_name,    p.scope::text)    AS scope_label
        {from_join}
        LEFT JOIN {schema}.lookups AS lk_module
            ON lk_module.list_name = 'module_code'
            AND lk_module.code = p.module::text
        LEFT JOIN {schema}.lookups AS lk_resource
            ON lk_resource.list_name = 'resource'
            AND lk_resource.code = p.resource::text
        LEFT JOIN {schema}.lookups AS lk_action
            ON lk_action.list_name = 'permission_action'
            AND lk_action.code = p.action::text
        LEFT JOIN {schema}.lookups AS lk_scope
            ON lk_scope.list_name = 'permission_scope'
            AND lk_scope.code = p.scope::text
        {where_clause}
        ORDER BY
            p.module ASC, p.resource ASC, p.action ASC,
            p.scope ASC, p.code ASC, p.id ASC
        """
    )
    result = await session.execute(sql, {"role_id": role_id})
    return [dict(row) for row in result.mappings().all()]


async def _resolve_override_global_permission_id(
    session: AsyncSession,
) -> UUID:
    """Look up the permission id for ``ADMIN.ROLES.OVERRIDE.GLOBAL``.

    Used by both Layer 1 pre-check and Layer 2 tripwire. The catalogue
    row landed in Step 6.18.1 (seed delta); pre-flight Check #3
    asserts it exists. If it ever doesn't, the invariant cannot be
    evaluated and the edit cannot proceed safely.

    Schema-qualified per CSD-03.
    """
    schema = get_settings().db_schema
    result = await session.execute(
        text(
            f"SELECT id FROM {schema}.permissions "
            "WHERE code = :code LIMIT 1"
        ),
        {"code": OVERRIDE_GLOBAL_CODE},
    )
    row = result.first()
    if row is None:
        # Catalogue is missing the OVERRIDE.GLOBAL row entirely. This
        # is a deployment-state defect (Step 6.18.1 seed delta did not
        # land); raise as ServerError so the wire returns 500
        # INTERNAL_ERROR. Class reuse: InternalInvariantViolationError
        # is the closest match (the invariant cannot be evaluated).
        raise InternalInvariantViolationError(
            f"permission row {OVERRIDE_GLOBAL_CODE} missing from catalogue",
            code=OVERRIDE_GLOBAL_CODE,
            invariant="OVERRIDE_GLOBAL_CATALOGUE_PRESENCE",
        )
    perm_id: UUID = row[0]
    return perm_id


async def _count_override_global_active_holders(
    session: AsyncSession,
    *,
    exclude_role_id: UUID | None,
) -> int:
    """Layer 1 + Layer 2 OVERRIDE.GLOBAL invariant query (Step 6.18.3
    LD6, LD7, LD8).

    Counts distinct ACTIVE users who hold
    ``ADMIN.ROLES.OVERRIDE.GLOBAL`` through any role. Filters BOTH
    assignment-side ``status='ACTIVE'`` AND user-side ``status='ACTIVE'``
    per LD7 (critical correction from investigation Bucket 6c —
    without the assignment-side filter, INACTIVE revoked assignments
    would count and a true last-holder edit could falsely pass).

    ``exclude_role_id``:
      - Layer 1 (pre-check) passes the role-under-edit's id so the
        post-edit state is computed (LD8). Asks "would any OTHER role
        still have an active holder of OVERRIDE.GLOBAL?"
      - Layer 2 (tripwire) passes ``None`` so the actual committed-to-
        be state is read directly.

    Schema-qualified raw SQL per CSD-03.
    """
    schema = get_settings().db_schema
    sql = text(
        f"""
        WITH override_role_ids AS (
            SELECT rp.role_id
            FROM {schema}.role_permissions rp
            JOIN {schema}.permissions p ON p.id = rp.permission_id
            WHERE p.code = :override_code
              AND (CAST(:exclude_role_id AS UUID) IS NULL
                   OR rp.role_id != CAST(:exclude_role_id AS UUID))
        )
        SELECT COUNT(DISTINCT user_id) AS active_holders
        FROM (
            SELECT pura.platform_user_id AS user_id
            FROM {schema}.platform_user_role_assignments pura
            JOIN override_role_ids ori ON ori.role_id = pura.role_id
            JOIN {schema}.platform_users pu
                ON pu.id = pura.platform_user_id
            WHERE pura.status = CAST(
                    'ACTIVE' AS {schema}.user_role_assignment_status_enum
                  )
              AND pu.status = CAST(
                    'ACTIVE' AS {schema}.platform_user_status_enum
                  )
            UNION
            SELECT tura.tenant_user_id AS user_id
            FROM {schema}.tenant_user_role_assignments tura
            JOIN override_role_ids ori ON ori.role_id = tura.role_id
            JOIN {schema}.tenant_users tu
                ON tu.id = tura.tenant_user_id
            WHERE tura.status = CAST(
                    'ACTIVE' AS {schema}.user_role_assignment_status_enum
                  )
              AND tu.status = CAST(
                    'ACTIVE' AS {schema}.tenant_user_status_enum
                  )
        ) holders
        """
    )
    result = await session.execute(
        sql,
        {
            "override_code": OVERRIDE_GLOBAL_CODE,
            "exclude_role_id": exclude_role_id,
        },
    )
    value: int = result.scalar_one()
    return value
