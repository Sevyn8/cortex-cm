"""OrgNodesRepo — read-only data access for ``org_nodes``.

Backs the Organization Tree page (Step 5.3, E2 + E3). Owns SELECT
queries against ``org_nodes``; does NOT set session GUCs, NOT begin
transactions, NOT handle commits/rollbacks. Visibility flows from the
session's ``app.tenant_id`` / ``app.user_type`` GUCs (RLS-bound):

  - PLATFORM JWT: sees all rows via D-29's unconditional OR-branch on
    ``org_nodes_tenant_isolation``.
  - TENANT JWT: RLS scopes to the matching ``app.tenant_id`` row set.

Per D-24, no ``tenant_id`` argument acts as a *visibility* filter —
the GUCs handle visibility. The ``tenant_id`` parameters on these
methods are *application-layer scoping* (the Repo answers "tell me
about tenant X's tree"); RLS layers an extra invariant that the
caller can actually see tenant X's rows. For TENANT JWTs requesting
their own tenant_id this is redundant; for cross-tenant requests
RLS yields zero rows and the router converts to 404 at the boundary.

Mirrors ``TenantsRepo`` and ``TenantUsersRepo``: stateless singleton,
each method takes ``session`` as the first positional argument, no
instance state.

Four methods (DP-1, DP-2, DP-3 lean choices applied):

  - ``count_active_by_tenant``      : non-TENANT ACTIVE node count
                                      (drives E2 smart-default mode).
  - ``list_active_with_child_counts``: full or depth-limited tree
                                      fetch, with per-row child count.
                                      One outer query + one CTE.
  - ``list_children_paginated``    : E3 paginated immediate children.
  - ``node_exists``                : E3 disambiguator — distinguishes
                                      "parent has no children" (200,
                                      empty items) from "parent does
                                      not exist" (404). Separate
                                      method for clarity (DP-3 lean).

SQL strategy notes:

- Per-row child count is attached via CTE + LEFT JOIN. The CTE
  groups all the tenant's ACTIVE rows by parent_id once; the outer
  query LEFT JOINs to attach per-node counts. ``func.coalesce(..., 0)``
  turns the no-children case (no row in the CTE) into 0 instead of
  NULL. **Approach A** per the prompt's DP-1: split count + LEFT
  JOIN. Two queries per E2 call, both inheriting RLS independently.

- Sibling order = path-ASC. ltree's natural ordering is lexical-by-
  label and our path labels are lowercased + hyphen→underscore
  versions of ``code``; the result is alphabetical-by-code at each
  level (I7 in the contract).

- ``max_depth`` filter is ``nlevel(path) <= max_depth + 1``. The +1
  accounts for the implicit TENANT root level: ``depth=1`` should
  return HQ-level nodes (path nlevel 2 = "tenant.hq"); ``depth=4``
  should return HQ + 3 mid-levels (max nlevel 5). The TENANT root
  itself (nlevel 1) is included in the fetch so the helper can
  identify it as the parent of tree-root candidates; the helper
  filters TENANT-type nodes out of the response shape.

- ``func.nlevel(path)`` works because the underlying PG column type
  is ``ltree`` (declared in the DDL); SQLAlchemy passes the column
  reference through unchanged and Postgres resolves the function
  against the native type.
"""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.audit.emit import (
    build_success_details_for_create,
    build_success_details_for_update,
    emit_audit_event,
)
from admin_backend.auth.context import AuthContext
from admin_backend.config import get_settings
from admin_backend.errors import (
    CycleDetectedError,
    DuplicateOrgNodeCodeError,
    InvalidParentNodeTypeError,
    OrgNodeNotFoundError,
    ParentNodeNotFoundError,
)
from admin_backend.models.audit_log import AuditResultType
from admin_backend.models.org_node import OrgNode, OrgNodeStatus, OrgNodeType
from admin_backend.models.tenant_user import ActorUserType


# ---- Step 6.13 cascade-order rule ------------------------------------------
#
# Canonical org-tree ordinal. A parent's node_type must have an ordinal
# STRICTLY LOWER than the child's; equal ordinals (e.g., STORE under
# STORE) are rejected. Skipping levels is allowed.
#
# Decoupled from the gate's ``_SCOPE_CASCADE_ORDER`` (which carries a
# leading ``GLOBAL`` for the platform cascade root): this map is only
# for the 7 ``org_node_type_enum`` values.

_ORDINAL_MAP: dict[OrgNodeType, int] = {
    OrgNodeType.TENANT: 0,
    OrgNodeType.BUSINESS_UNIT: 1,
    OrgNodeType.HQ: 2,
    OrgNodeType.COUNTRY: 3,
    OrgNodeType.REGION: 4,
    OrgNodeType.STORE: 5,
    OrgNodeType.DEPARTMENT: 6,
}


def _check_cascade_order(
    parent_type: OrgNodeType,
    child_type: OrgNodeType,
) -> None:
    """Raise ``InvalidParentNodeTypeError`` if parent_type does not
    sit strictly above child_type in the canonical order.

    Pure function; no DB. Used by both ``add_node`` and the
    parent-change path of ``edit_node``.
    """
    parent_ord = _ORDINAL_MAP[parent_type]
    child_ord = _ORDINAL_MAP[child_type]
    if parent_ord >= child_ord:
        raise InvalidParentNodeTypeError(
            (
                f"parent_type={parent_type.value} (ordinal {parent_ord}) "
                f"must sit strictly above child_type={child_type.value} "
                f"(ordinal {child_ord}) in the canonical org-tree order"
            ),
            child_type=child_type.value,
            parent_type=parent_type.value,
            attempted_ordinal_child=child_ord,
            attempted_ordinal_parent=parent_ord,
        )


def _path_label(code: str) -> str:
    """Derive an ltree label from a node's code.

    Matches the ``make_org_node`` fixture convention so test paths and
    production paths align. ltree label syntax disallows hyphens; we
    lowercase and substitute hyphens with underscores. Single-char
    codes (allowed by the DDL CHECK as a special-case branch) keep their
    label-validity since alphanumerics are unchanged by the substitution.
    """
    return code.lower().replace("-", "_")


@dataclass(frozen=True)
class _NodeRow:
    """Minimal row shape used by repo write helpers.

    Step 6.16.5 extension: ``name`` and ``code`` carry the pre-write
    values so ``edit_node`` can build a before/after diff for the audit
    row without a second SELECT against the target.
    """

    id: UUID
    tenant_id: UUID
    parent_id: UUID | None
    path: str
    node_type: OrgNodeType
    name: str
    code: str


class OrgNodesRepo:
    """Repository for ``org_nodes`` — reads (Step 5.3) and writes (Step 6.13)."""

    async def count_active_by_tenant(
        self,
        session: AsyncSession,
        tenant_id: UUID,
    ) -> int:
        """Count non-TENANT ACTIVE nodes in the tenant's tree.

        Drives E2's smart-default decision: ``<= FULL_TREE_THRESHOLD``
        gets full-tree mode; else depth-limited.
        """
        stmt = (
            select(func.count(OrgNode.id))
            .where(
                OrgNode.tenant_id == tenant_id,
                OrgNode.status == OrgNodeStatus.ACTIVE,
                OrgNode.node_type != OrgNodeType.TENANT,
            )
        )
        result = await session.execute(stmt)
        total: int = result.scalar_one()
        return total

    async def list_active_with_child_counts(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        *,
        max_depth: int | None = None,
    ) -> list[tuple[OrgNode, int]]:
        """Return ACTIVE nodes (path-ASC) with per-row immediate-child count.

        Includes the TENANT root if present (helper filters it out of
        the response). Returns ``[(OrgNode, child_count), ...]``.

        ``max_depth=None`` returns the full tree. Else filters
        ``nlevel(path) <= max_depth + 1`` (the +1 absorbs the TENANT
        root level so callers think in "depth from root").

        ``child_count`` reflects the FULL subtree's immediate ACTIVE
        children — independent of the depth filter — so the frontend
        can decide whether to lazy-fetch via E3.
        """
        child_counts = (
            select(
                OrgNode.parent_id.label("parent_id"),
                func.count().label("n"),
            )
            .where(
                OrgNode.tenant_id == tenant_id,
                OrgNode.status == OrgNodeStatus.ACTIVE,
                OrgNode.parent_id.is_not(None),
            )
            .group_by(OrgNode.parent_id)
            .cte("child_counts")
        )

        stmt = (
            select(
                OrgNode,
                func.coalesce(child_counts.c.n, 0).label("child_count"),
            )
            .outerjoin(
                child_counts, child_counts.c.parent_id == OrgNode.id
            )
            .where(
                OrgNode.tenant_id == tenant_id,
                OrgNode.status == OrgNodeStatus.ACTIVE,
            )
            .order_by(OrgNode.path.asc())
        )

        if max_depth is not None:
            stmt = stmt.where(
                func.nlevel(OrgNode.path) <= max_depth + 1
            )

        result = await session.execute(stmt)
        return [(node, int(count)) for node, count in result.all()]

    async def list_children_paginated(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        parent_id: UUID,
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[tuple[OrgNode, int]], int]:
        """Return ``(rows, total)``: paginated ACTIVE children of ``parent_id``.

        Each child carries its own ``child_count`` (grandchild count).
        ``total`` is the unpaginated count of children matching the
        filter, so pagination metadata is correct.

        Caller MUST verify ``parent_id`` exists via ``node_exists``
        before treating zero-rows as "no children" — both
        "parent does not exist" and "parent has no children" return
        ``rows=[], total=0`` from this method.
        """
        total_stmt = (
            select(func.count())
            .select_from(OrgNode)
            .where(
                OrgNode.tenant_id == tenant_id,
                OrgNode.parent_id == parent_id,
                OrgNode.status == OrgNodeStatus.ACTIVE,
            )
        )
        total_result = await session.execute(total_stmt)
        total: int = total_result.scalar_one()

        child_counts = (
            select(
                OrgNode.parent_id.label("parent_id"),
                func.count().label("n"),
            )
            .where(
                OrgNode.tenant_id == tenant_id,
                OrgNode.status == OrgNodeStatus.ACTIVE,
                OrgNode.parent_id.is_not(None),
            )
            .group_by(OrgNode.parent_id)
            .cte("child_counts_e3")
        )

        items_stmt = (
            select(
                OrgNode,
                func.coalesce(child_counts.c.n, 0).label("child_count"),
            )
            .outerjoin(
                child_counts, child_counts.c.parent_id == OrgNode.id
            )
            .where(
                OrgNode.tenant_id == tenant_id,
                OrgNode.parent_id == parent_id,
                OrgNode.status == OrgNodeStatus.ACTIVE,
            )
            .order_by(OrgNode.path.asc())
            .offset(offset)
            .limit(limit)
        )
        items_result = await session.execute(items_stmt)
        rows = [(n, int(c)) for n, c in items_result.all()]
        return rows, total

    async def node_exists(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        node_id: UUID,
    ) -> bool:
        """Verify a node exists ACTIVE within the tenant.

        Used by E3 to distinguish "parent has no children" (200,
        empty items) from "parent does not exist" (404). Runs against
        the RLS-bound session, so cross-tenant requests return False
        regardless of whether the row physically exists — matches the
        D-17 RLS-as-404 posture.
        """
        stmt = (
            select(OrgNode.id)
            .where(
                OrgNode.tenant_id == tenant_id,
                OrgNode.id == node_id,
                OrgNode.status == OrgNodeStatus.ACTIVE,
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ---- Step 6.13 writes --------------------------------------------------

    async def add_node(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        parent_id: UUID,
        node_type: OrgNodeType,
        code: str,
        name: str,
        auth: AuthContext,
        request_id: UUID | None = None,
    ) -> OrgNode:
        """Insert a new org_node under ``parent_id``.

        Order of operations:
          1. SELECT FOR UPDATE on the parent row (within RLS).
             - Missing -> ParentNodeNotFoundError (404).
          2. Cascade-order check: parent.node_type < child.node_type
             - Violation -> InvalidParentNodeTypeError (422).
          3. Build path = parent.path || lower(code).replace('-','_').
          4. INSERT into org_nodes (single statement; FetchedValue
             populates id/created_at/updated_at; raw SQL casts the
             ltree path).
             - IntegrityError on uq_org_nodes_tenant_code_lower
               -> DuplicateOrgNodeCodeError (409).
             - Other IntegrityErrors propagate (500).

        Returns the freshly-inserted row via a SELECT-by-id refetch so
        the OrgNode ORM object has all server defaults populated.

        Step 6.16.5 audit emission: ``request_id`` is the optional
        emission trigger. ``auth`` stays mandatory (load-bearing for
        the INSERT's audit-actor pair). When ``request_id is not None``
        a SUCCESS row is emitted with the new node's snapshot frozen
        (LD5: includes ``parent_org_node_name``).
        """
        schema = get_settings().db_schema

        # 1. Lock the parent.
        parent = await self._select_for_update_node(
            session, tenant_id=tenant_id, node_id=parent_id
        )
        if parent is None:
            raise ParentNodeNotFoundError(
                f"parent_id={parent_id} not visible in tenant_id={tenant_id}",
                parent_id=str(parent_id),
                tenant_id=str(tenant_id),
            )

        # 2. Cascade-order check.
        _check_cascade_order(parent.node_type, node_type)

        # 3. Build path.
        new_path = f"{parent.path}.{_path_label(code)}"

        # 4. INSERT.
        actor_type = _actor_user_type_from_auth(auth)
        insert_sql = text(
            f"""
            INSERT INTO {schema}.org_nodes (
                tenant_id, parent_id, path, node_type,
                name, code,
                created_by_user_id, created_by_user_type,
                updated_by_user_id, updated_by_user_type
            ) VALUES (
                :tenant_id, :parent_id,
                CAST(:path AS ltree),
                CAST(:node_type AS {schema}.org_node_type_enum),
                :name, :code,
                :actor_id, CAST(:actor_type AS {schema}.actor_user_type_enum),
                :actor_id, CAST(:actor_type AS {schema}.actor_user_type_enum)
            )
            RETURNING id
            """
        )
        try:
            result = await session.execute(
                insert_sql,
                {
                    "tenant_id": tenant_id,
                    "parent_id": parent_id,
                    "path": new_path,
                    "node_type": node_type.value,
                    "name": name,
                    "code": code,
                    "actor_id": auth.user_id,
                    "actor_type": actor_type.value,
                },
            )
        except IntegrityError as exc:
            self._map_code_uniqueness_violation(
                exc, code=code, tenant_id=tenant_id
            )
            raise
        new_id = result.scalar_one()
        session.expire_all()

        # Refetch the freshly-inserted row.
        row = await self._refetch_by_id(session, tenant_id, new_id)
        if row is None:  # pragma: no cover - defensive
            raise OrgNodeNotFoundError(
                f"org_node id={new_id} not visible after INSERT",
                tenant_id=str(tenant_id),
                node_id=str(new_id),
            )

        # Step 6.16.5 success audit emission.
        if request_id is not None:
            tenant_name = await self._lookup_tenant_name(
                session, tenant_id
            )
            snapshot = {
                "id": row.id,
                "name": row.name,
                "code": row.code,
                "node_type": row.node_type.value,
                "path": row.path,
                "parent_id": row.parent_id,
                "parent_org_node_name": parent.name,
                "status": row.status.value,
            }
            await emit_audit_event(
                session,
                auth=auth,
                action="CREATE",
                resource_type="ORG_NODE",
                resource_id=row.id,
                resource_label=row.name,
                # Step 6.16.7 LD7 : populate resource_subtype with the
                # row's ``node_type`` enum value frozen at write time.
                resource_subtype=row.node_type.value,
                result_type=AuditResultType.SUCCESS,
                details=build_success_details_for_create(snapshot),
                tenant_id=tenant_id,
                tenant_name=tenant_name,
                request_id=request_id,
                route_to_platform=False,
            )

        return row

    async def edit_node(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        node_id: UUID,
        name: str | None,
        code: str | None,
        parent_id: UUID | None,
        auth: AuthContext,
        reparent: bool,
        request_id: UUID | None = None,
    ) -> OrgNode:
        """Apply rename / code change / reparent to ``node_id`` atomically.

        ``reparent`` is True when the request body explicitly set
        ``parent_id`` (the router distinguishes "no change" from
        "set to NULL"; for org_node the only valid post-condition is
        non-NULL parent_id on non-TENANT rows, so reparent=True implies
        ``parent_id`` is non-NULL here). The handler validates the
        tenant-root special case upstream and never delegates a
        TENANT-type row's parent change to this method.

        Order:
          1. SELECT FOR UPDATE on target. Missing -> OrgNodeNotFoundError.
          2. If reparent: SELECT FOR UPDATE on new_parent.
             - Missing -> ParentNodeNotFoundError.
             - Self / descendant -> CycleDetectedError.
             - Cascade-order violation -> InvalidParentNodeTypeError.
          3. UPDATE org_nodes SET name, code, parent_id, path
             where applicable; subtree re-path in same UPDATE for
             non-target rows via ltree subpath operators.
          4. IntegrityError on uq_org_nodes_tenant_code_lower
             -> DuplicateOrgNodeCodeError.
        """
        schema = get_settings().db_schema

        target = await self._select_for_update_node(
            session, tenant_id=tenant_id, node_id=node_id
        )
        if target is None:
            raise OrgNodeNotFoundError(
                f"node_id={node_id} not visible in tenant_id={tenant_id}",
                tenant_id=str(tenant_id),
                node_id=str(node_id),
            )

        new_path: str | None = None
        new_parent: _NodeRow | None = None
        if reparent:
            assert parent_id is not None, (
                "edit_node reparent=True requires non-NULL parent_id"
            )
            if parent_id == target.id:
                raise CycleDetectedError(
                    f"cannot reparent node {node_id} to itself",
                    target_id=str(node_id),
                    attempted_parent_id=str(parent_id),
                )
            new_parent = await self._select_for_update_node(
                session, tenant_id=tenant_id, node_id=parent_id
            )
            if new_parent is None:
                raise ParentNodeNotFoundError(
                    f"parent_id={parent_id} not visible in tenant_id={tenant_id}",
                    parent_id=str(parent_id),
                    tenant_id=str(tenant_id),
                )

            # Cycle: new_parent must NOT be a descendant of target.
            # ltree contains operator: target.path @> new_parent.path
            # means target is an ancestor of new_parent (target.path is
            # prefix of new_parent.path). That's the cycle case.
            if _is_descendant(new_parent.path, target.path):
                raise CycleDetectedError(
                    (
                        f"node {node_id} (path={target.path}) cannot be "
                        f"reparented under descendant {parent_id} "
                        f"(path={new_parent.path})"
                    ),
                    target_id=str(node_id),
                    attempted_parent_id=str(parent_id),
                )

            _check_cascade_order(new_parent.node_type, target.node_type)

            # Build new path: new_parent.path || target's own label.
            target_label = target.path.split(".")[-1]
            new_path = f"{new_parent.path}.{target_label}"

        # If code changed, the path label changes too (last segment).
        # Recompute the path's last segment using the NEW code; if
        # we're also reparenting, layer that on top.
        if code is not None:
            new_label = _path_label(code)
            if new_path is not None:
                # New parent path + new label.
                parent_prefix = new_path.rsplit(".", 1)[0]
                new_path = f"{parent_prefix}.{new_label}"
            else:
                # Same parent; replace just the last segment.
                if "." in target.path:
                    parent_prefix = target.path.rsplit(".", 1)[0]
                    new_path = f"{parent_prefix}.{new_label}"
                else:
                    new_path = new_label

        actor_type = _actor_user_type_from_auth(auth)

        # Build UPDATE for the target row. Subtree re-path handled
        # separately so the target's UPDATE remains a single row write
        # (cleaner audit-actor and updated_at semantics).
        update_target_sql = text(
            f"""
            UPDATE {schema}.org_nodes
               SET name       = COALESCE(:name, name),
                   code       = COALESCE(:code, code),
                   parent_id  = CASE WHEN :reparent THEN :parent_id
                                     ELSE parent_id END,
                   path       = CASE WHEN :has_new_path
                                     THEN CAST(:new_path AS ltree)
                                     ELSE path END,
                   updated_by_user_id   = :actor_id,
                   updated_by_user_type = CAST(:actor_type AS {schema}.actor_user_type_enum)
             WHERE id = :node_id
               AND tenant_id = :tenant_id
            """
        )
        try:
            await session.execute(
                update_target_sql,
                {
                    "name": name,
                    "code": code,
                    "reparent": reparent,
                    "parent_id": parent_id,
                    "has_new_path": new_path is not None,
                    "new_path": new_path,
                    "actor_id": auth.user_id,
                    "actor_type": actor_type.value,
                    "node_id": node_id,
                    "tenant_id": tenant_id,
                },
            )

            # Subtree re-path. Two cases trigger it:
            #   (a) reparent: target.path moved; every descendant must
            #       have its path's target.path-prefix swapped to new_path.
            #   (b) code change without reparent: target.path's last
            #       segment changed; descendants encoded that segment as
            #       their own ancestor segment, so they must be rewritten.
            if new_path is not None and new_path != target.path:
                subtree_sql = text(
                    f"""
                    UPDATE {schema}.org_nodes
                       SET path = CAST(:new_prefix AS ltree)
                                  || subpath(path, nlevel(CAST(:old_prefix AS ltree))),
                           updated_by_user_id   = :actor_id,
                           updated_by_user_type = CAST(:actor_type AS {schema}.actor_user_type_enum)
                     WHERE tenant_id = :tenant_id
                       AND path <@ CAST(:old_prefix AS ltree)
                       AND id <> :node_id
                    """
                )
                await session.execute(
                    subtree_sql,
                    {
                        "old_prefix": target.path,
                        "new_prefix": new_path,
                        "actor_id": auth.user_id,
                        "actor_type": actor_type.value,
                        "tenant_id": tenant_id,
                        "node_id": node_id,
                    },
                )
        except IntegrityError as exc:
            self._map_code_uniqueness_violation(
                exc, code=code or "", tenant_id=tenant_id
            )
            raise

        session.expire_all()
        row = await self._refetch_by_id(session, tenant_id, node_id)
        if row is None:  # pragma: no cover - defensive
            raise OrgNodeNotFoundError(
                f"org_node id={node_id} not visible after UPDATE",
                tenant_id=str(tenant_id),
                node_id=str(node_id),
            )

        # Step 6.16.5 success audit emission. LD4: action is always
        # "UPDATE" regardless of which fields changed. LD5: diff
        # carries only fields that actually changed; if parent_id
        # changed, both before/after halves carry
        # ``parent_org_node_name`` (the OLD parent name on the before
        # side; the NEW parent name on the after side).
        if request_id is not None:
            before_diff: dict[str, object] = {}
            after_diff: dict[str, object] = {}

            if name is not None and name != target.name:
                before_diff["name"] = target.name
                after_diff["name"] = name
            if code is not None and code != target.code:
                before_diff["code"] = target.code
                after_diff["code"] = code
            if reparent and new_parent is not None and parent_id != target.parent_id:
                before_diff["parent_id"] = target.parent_id
                after_diff["parent_id"] = parent_id
                old_parent_name = await self._lookup_parent_name(
                    session, tenant_id, target.parent_id
                )
                before_diff["parent_org_node_name"] = old_parent_name
                after_diff["parent_org_node_name"] = new_parent.name

            # Only emit when at least one field changed. (A no-op
            # PATCH body upstream is caught by EmptyPatchError; a
            # body with only no-change fields would still fall here
            # — emit nothing in that case.)
            if before_diff:
                tenant_name = await self._lookup_tenant_name(
                    session, tenant_id
                )
                await emit_audit_event(
                    session,
                    auth=auth,
                    action="UPDATE",
                    resource_type="ORG_NODE",
                    resource_id=row.id,
                    resource_label=row.name,
                    # Step 6.16.7 LD7 : populate resource_subtype with
                    # the row's ``node_type`` enum value (post-update;
                    # node_type is immutable on PATCH so before==after).
                    resource_subtype=row.node_type.value,
                    result_type=AuditResultType.SUCCESS,
                    details=build_success_details_for_update(
                        before=before_diff,
                        after=after_diff,
                    ),
                    tenant_id=tenant_id,
                    tenant_name=tenant_name,
                    request_id=request_id,
                    route_to_platform=False,
                )

        return row

    async def set_status(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        node_id: UUID,
        target_status: OrgNodeStatus,
        auth: AuthContext,
    ) -> OrgNode | None:
        """Set the status of one ``org_nodes`` row + archived_* triplet.

        Step 6.21.2 cascade target. The two-table-one-entity coupling
        between ``stores`` and the paired STORE-type ``org_nodes`` row
        (architecture.md A.5) makes the store status the owner; this
        method receives the projected target_status from
        ``StoresRepo.transition`` via ``STORE_STATUS_TO_ORG_NODE_STATUS``
        and applies it inside the same request transaction.

        Pattern mirrors ``StoresRepo.transition``'s closed_* triplet
        handling, but on the ``archived_*`` triplet of org_nodes:

          - **Into-ARCHIVED**: populate ``archived_at``,
            ``archived_by_user_id``, ``archived_by_user_type`` atomically
            with the status flip.
          - **Out-of-ARCHIVED**: null the triplet atomically with the
            status flip. Historical archive metadata is lost on the row;
            Step 6.2 audit_log preserves the history when shipped.
          - **Between non-ARCHIVED** (ACTIVE <-> INACTIVE):
            ``archived_*`` columns untouched (already NULL by invariant).

        Same-state (e.g., ACTIVE -> ACTIVE) is a no-op apart from the
        ``updated_by_*`` re-stamp; returns the refetched row. The caller
        (``StoresRepo.transition``) has already validated the store-side
        transition matrix; this method does NOT enforce a separate
        org_node-side state machine.

        ``updated_by_*`` populates on every call (Pattern (b) per D-13).

        Returns ``None`` when the row is RLS-invisible OR genuinely
        absent (D-17). ``StoresRepo.transition`` propagates a None here
        as a ``NOT_FOUND`` outcome.

        Schema-qualified raw SQL per CSD-03.
        """
        schema = get_settings().db_schema

        target = await self._select_for_update_node(
            session, tenant_id=tenant_id, node_id=node_id
        )
        if target is None:
            return None

        actor_type = _actor_user_type_from_auth(auth)
        actor_cast = (
            f"CAST(:actor_type AS {schema}.actor_user_type_enum)"
        )
        status_cast = (
            f"CAST(:target_status AS {schema}.org_node_status_enum)"
        )

        # Need current status to pick the archived_* triplet class.
        current_row = await session.execute(
            text(
                f"SELECT status FROM {schema}.org_nodes "
                "WHERE id = :node_id AND tenant_id = :tenant_id "
                "LIMIT 1"
            ),
            {"node_id": node_id, "tenant_id": tenant_id},
        )
        current = current_row.first()
        if current is None:  # pragma: no cover - defensive
            return None
        current_status = OrgNodeStatus(current.status)

        if target_status is OrgNodeStatus.ARCHIVED:
            # Class 1: into-ARCHIVED. Populate the archived_* triplet.
            update_sql = text(
                f"""
                UPDATE {schema}.org_nodes
                   SET status = {status_cast},
                       archived_at = now(),
                       archived_by_user_id = :actor_id,
                       archived_by_user_type = {actor_cast},
                       updated_by_user_id = :actor_id,
                       updated_by_user_type = {actor_cast}
                 WHERE id = :node_id
                   AND tenant_id = :tenant_id
                """
            )
        elif current_status is OrgNodeStatus.ARCHIVED:
            # Class 2: out-of-ARCHIVED. Null the archived_* triplet.
            update_sql = text(
                f"""
                UPDATE {schema}.org_nodes
                   SET status = {status_cast},
                       archived_at = NULL,
                       archived_by_user_id = NULL,
                       archived_by_user_type = NULL,
                       updated_by_user_id = :actor_id,
                       updated_by_user_type = {actor_cast}
                 WHERE id = :node_id
                   AND tenant_id = :tenant_id
                """
            )
        else:
            # Class 3: between non-ARCHIVED. archived_* untouched
            # (already NULL by invariant).
            update_sql = text(
                f"""
                UPDATE {schema}.org_nodes
                   SET status = {status_cast},
                       updated_by_user_id = :actor_id,
                       updated_by_user_type = {actor_cast}
                 WHERE id = :node_id
                   AND tenant_id = :tenant_id
                """
            )

        await session.execute(
            update_sql,
            {
                "target_status": target_status.value,
                "actor_id": auth.user_id,
                "actor_type": actor_type.value,
                "node_id": node_id,
                "tenant_id": tenant_id,
            },
        )

        # Raw UPDATE bypasses SA ORM; expire so the materialising read
        # returns fresh status / archived_* / updated_*.
        session.expire_all()
        return await self._refetch_by_id(session, tenant_id, node_id)

    # ---- private helpers --------------------------------------------------

    async def _select_for_update_node(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        node_id: UUID,
    ) -> _NodeRow | None:
        """SELECT FOR UPDATE within the RLS-bound session.

        Returns ``None`` when the row is RLS-invisible OR genuinely
        absent — the two cases collapse intentionally (D-17). Callers
        translate ``None`` into the appropriate domain-shaped 404 based
        on whether the id was the operand or the operand's parent.
        """
        schema = get_settings().db_schema
        sql = text(
            f"""
            SELECT id, tenant_id, parent_id, path::text AS path,
                   node_type, name, code
              FROM {schema}.org_nodes
             WHERE id = :node_id
               AND tenant_id = :tenant_id
             FOR UPDATE
            """
        )
        result = await session.execute(
            sql, {"node_id": node_id, "tenant_id": tenant_id}
        )
        row = result.first()
        if row is None:
            return None
        return _NodeRow(
            id=row.id,
            tenant_id=row.tenant_id,
            parent_id=row.parent_id,
            path=str(row.path),
            node_type=OrgNodeType(row.node_type),
            name=str(row.name),
            code=str(row.code),
        )

    async def _refetch_by_id(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        node_id: UUID,
    ) -> OrgNode | None:
        """Return the freshly-saved ORM row for response materialization."""
        stmt = (
            select(OrgNode)
            .where(
                OrgNode.tenant_id == tenant_id,
                OrgNode.id == node_id,
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _lookup_tenant_name(
        self,
        session: AsyncSession,
        tenant_id: UUID,
    ) -> str:
        """Resolve tenant_name for the audit row (NOT NULL on the
        tenant audit table). Defensive ``<unknown>`` fallback if the
        tenant row is concurrently deleted.
        """
        schema = get_settings().db_schema
        result = await session.execute(
            text(
                f"SELECT name FROM {schema}.tenants WHERE id = :tenant_id"
            ),
            {"tenant_id": tenant_id},
        )
        name = result.scalar_one_or_none()
        return str(name) if name is not None else "<unknown>"

    async def _lookup_parent_name(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        parent_id: UUID | None,
    ) -> str | None:
        """Resolve the ``name`` of the supplied parent for audit
        ``parent_org_node_name``. Returns None when ``parent_id`` is
        None (the TENANT-root case has no parent).
        """
        if parent_id is None:
            return None
        schema = get_settings().db_schema
        result = await session.execute(
            text(
                f"SELECT name FROM {schema}.org_nodes "
                "WHERE id = :parent_id AND tenant_id = :tenant_id"
            ),
            {"parent_id": parent_id, "tenant_id": tenant_id},
        )
        name = result.scalar_one_or_none()
        return str(name) if name is not None else "<unknown>"

    @staticmethod
    def _map_code_uniqueness_violation(
        exc: IntegrityError,
        *,
        code: str,
        tenant_id: UUID,
    ) -> None:
        """Raise DuplicateOrgNodeCodeError IF the violation is the
        ``uq_org_nodes_tenant_code_lower`` index. Otherwise return
        silently so the caller can ``raise`` the original.
        """
        msg = str(exc.orig) if exc.orig is not None else str(exc)
        if "uq_org_nodes_tenant_code_lower" in msg:
            raise DuplicateOrgNodeCodeError(
                (
                    f"code={code!r} already exists in tenant_id={tenant_id} "
                    "(case-insensitive)"
                ),
                code=code,
                tenant_id=str(tenant_id),
            ) from exc


def _is_descendant(candidate_path: str, ancestor_path: str) -> bool:
    """Return True when ``candidate_path`` is a descendant of
    ``ancestor_path`` in ltree semantics (or equal — which is the
    "self-parent" cycle case).

    Pure-string evaluation since ltree paths are dot-separated. Equivalent
    to ``ancestor_path @> candidate_path``: the ancestor is a prefix of
    the candidate, on a segment boundary.
    """
    if candidate_path == ancestor_path:
        return True
    prefix = ancestor_path + "."
    return candidate_path.startswith(prefix)


def _actor_user_type_from_auth(auth: AuthContext) -> ActorUserType:
    """Map the AuthContext's user_type literal to ActorUserType enum.

    Pattern (b) audit-actor pairing per D-13: every INSERT / UPDATE writes
    both the actor_id and the actor_type discriminator. The DB-side
    audience-check trigger (Step 6.8.1) only fires on role-assignment
    tables, not org_nodes, so we trust the JWT-derived value.
    """
    return ActorUserType(auth.user_type)


# Module-level exports for tests that want to exercise the helpers
# without going through the Repo.
__all__ = [
    "OrgNodesRepo",
    "_check_cascade_order",
    "_is_descendant",
    "_ORDINAL_MAP",
    "_path_label",
]
