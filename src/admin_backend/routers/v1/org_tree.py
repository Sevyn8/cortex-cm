"""Org-tree router (Step 5.3).

Two GET endpoints backing the Organization Tree page (Frontend spec
7.3):

  - **E2** ``GET /api/v1/tenants/{tenant_id}/org-tree`` — initial tree
    fetch with smart-default behavior. Small tenants (<=500 ACTIVE
    non-TENANT nodes) get the full tree; larger tenants get a
    depth-limited tree (default depth=4) with deeper subtrees
    available via E3. Each node carries ``has_children``,
    ``child_count``, and ``loaded_children`` so the frontend knows
    which subtrees to lazy-fetch.

  - **E3** ``GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children``
    — paginated lazy expansion of a specific node's children. Used
    by the frontend when the user expands a depth-cut subtree.

Auth posture (multi-user-type — see CLAUDE.md "v0 auth model" note).
Both endpoints gate on ``ADMIN.ORG_NODES.VIEW.TENANT`` (Step 6.9.3.2
retrofit) with the appropriate anchor dep (``get_tenant_anchor`` for
E2, ``get_org_node_anchor`` for E3). RLS scopes visibility below the
gate:

  - PLATFORM JWT: sees all tenants' nodes via D-29's unconditional
    OR-branch on ``org_nodes_tenant_isolation``.
  - TENANT JWT: RLS scopes to ``app.tenant_id`` rows.

Cross-tenant requests by TENANT users surface as 404 (RLS-as-404 per
D-17), not 403, to avoid disclosing whether a tenant or node exists
in another tenant. Tests T12 (E2) and T18 (E3) are LOAD-BEARING for
this end-to-end through middleware -> session -> Repo -> router.

Response shapes:
  - E2: ``{tenant_id, tenant_name, stats, tree}`` (deliberate D-30
    exception — singleton resource per tenant; see CLAUDE.md note).
  - E3: ``{node_id, items, pagination}`` (D-30 standard).

Smart-default behavior (E2):
  1. Resolve tenant. 404 if missing or RLS-filtered.
  2. Count non-TENANT ACTIVE nodes for this tenant.
  3. If ``depth`` query-param is set, respect it (capped at MAX_DEPTH).
  4. Else if count <= FULL_TREE_THRESHOLD (500): full-tree mode.
  5. Else: depth-limited mode (default DEFAULT_DEPTH=4).
  6. Fetch nodes-with-child-counts.
  7. If response (non-TENANT count) > PAYLOAD_CAP (1000), reduce
     depth and refetch. Bounded loop, max 2 reductions (DP-4 lean).
     Set ``truncated=true``.
  8. Build tree, return.

Server-side tunables (locked per the 2026-05-04 design conversation):
  - FULL_TREE_THRESHOLD=500: <50 KB compressed; renders <500ms.
  - DEFAULT_DEPTH=4: covers HQ + 3 mid-levels (typical 4-layer hierarchy).
  - MAX_DEPTH=6: realistic max; deeper is pathological.
  - PAYLOAD_CAP=1000: server reduces depth if exceeded.
"""
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.config import get_settings

from admin_backend.auth.anchor_deps import (
    get_org_node_anchor,
    get_tenant_anchor,
)
from admin_backend.auth.context import AuthContext
from admin_backend.auth.permissions import require
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.errors import (
    InternalInvariantViolationError,
    OrgNodeFieldNotAllowedForTypeError,
    OrgNodeNotFoundError,
    TenantNotFoundError,
    TenantRootNotReparentableError,
)
from admin_backend.models.org_node import OrgNode, OrgNodeType
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.repositories.org_nodes import OrgNodesRepo
from admin_backend.repositories.tenants import TenantsRepo
from admin_backend.schemas.org_node import (
    OrgNodeChildrenResponse,
    OrgNodeCreateRequest,
    OrgNodePatchRequest,
    OrgNodeRead,
    OrgNodeTreeItem,
    OrgTreeResponse,
    OrgTreeStats,
)
from admin_backend.schemas.tenant import Pagination


router = APIRouter(tags=["org-tree"])

# Stateless instances reused across requests.
_org_repo = OrgNodesRepo()
_tenants_repo = TenantsRepo()


# ---- Tunables (locked per design conversation 2026-05-04) -------------------

FULL_TREE_THRESHOLD = 500
DEFAULT_DEPTH = 4
MAX_DEPTH = 6
PAYLOAD_CAP = 1000
MAX_REDUCTIONS = 2


# OrgNodeNotFoundError moved to admin_backend.errors at Step 6.9.3.2 so
# anchor deps in auth/anchor_deps.py can raise it without backward layering
# violation (auth/ -> routers/v1/). Per-router import kept above for raise
# sites; behavior identical to pre-move (RLS-as-404 per D-17).


# ---- E2: org-tree ----------------------------------------------------------


@router.get(
    "/tenants/{tenant_id}/org-tree",
    response_model=OrgTreeResponse,
    summary="Get organisation tree for a tenant",
    description=(
        "Returns the tenant's org tree. Smart-default behavior: small "
        "tenants (<=500 ACTIVE non-TENANT nodes) get the full tree; "
        "larger tenants get a depth-limited tree (default depth=4) "
        "with deeper nodes available via the children endpoint. Each "
        "returned node carries `has_children`, `child_count`, and "
        "`loaded_children` so the frontend knows which subtrees to "
        "lazy-fetch. If the depth-limited tree still exceeds 1000 "
        "nodes, the server auto-reduces depth and sets `truncated=true`."
    ),
)
async def get_org_tree(
    tenant_id: UUID,
    depth: int | None = Query(
        None,
        ge=1,
        le=MAX_DEPTH,
        description=(
            "Optional. Max depth of nodes returned (depth from the "
            "implicit TENANT root). 1 = HQ only; 4 = HQ + Country + "
            "Region + Store. If omitted, the server picks based on "
            "tenant size."
        ),
    ),
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.ORG_NODES,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
        anchor_dep=get_tenant_anchor,
    )),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    # 1. Resolve tenant. 404 if missing or RLS-filtered.
    tenant = await _tenants_repo.get_by_id(session, tenant_id)
    if tenant is None:
        raise TenantNotFoundError(
            f"Tenant {tenant_id} not visible to this session",
            tenant_id=str(tenant_id),
        )

    # 2. Count nodes. Drives smart-default mode.
    total = await _org_repo.count_active_by_tenant(session, tenant_id)

    # 3. Decide mode.
    max_depth: int | None
    if depth is not None:
        # Explicit depth requested — respect it (already capped via Query.le).
        max_depth = depth
    elif total <= FULL_TREE_THRESHOLD:
        # Small tenant — return full tree.
        max_depth = None
    else:
        # Large tenant — apply default depth.
        max_depth = DEFAULT_DEPTH

    # 4. Fetch with possible depth filter.
    rows = await _org_repo.list_active_with_child_counts(
        session, tenant_id, max_depth=max_depth
    )

    # 5. Auto-reduce depth if response exceeds PAYLOAD_CAP. Only fires
    #    in depth-limited mode (full-tree mode is gated by the count
    #    threshold so it can't reach the cap in practice).
    truncated = False
    if max_depth is not None:
        for _reduction in range(MAX_REDUCTIONS):
            non_tenant_count = sum(
                1 for n, _ in rows if n.node_type != OrgNodeType.TENANT
            )
            if non_tenant_count <= PAYLOAD_CAP:
                break
            if max_depth <= 1:
                break
            max_depth -= 1
            truncated = True
            rows = await _org_repo.list_active_with_child_counts(
                session, tenant_id, max_depth=max_depth
            )

    # 6. Extract tenant-root row from the existing result set (Step 6.21.1).
    #    list_active_with_child_counts returns ALL ACTIVE rows including
    #    the TENANT-typed root (its WHERE filter is status=ACTIVE only,
    #    and the optional depth filter always admits nlevel(path)=1).
    #    The tenant-root row is guaranteed by Step 6.20.1's atomic
    #    TenantsRepo.create. A None here means a tenant row exists
    #    without its mandatory tenant-root, which is structurally
    #    impossible post-Step-6.20.1; surface as 500 via the
    #    InternalInvariantViolationError tripwire so the failure is
    #    loud rather than serving a partial shape.
    tenant_root_node = next(
        (n for n, _ in rows if n.node_type == OrgNodeType.TENANT),
        None,
    )
    if tenant_root_node is None:
        raise InternalInvariantViolationError(
            f"tenant {tenant.id} has no tenant-root org_node",
            tenant_id=str(tenant.id),
        )

    # 7. Build tree + stats.
    tree, stats = _build_tree(rows, total_full=total, truncated=truncated)

    return OrgTreeResponse(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        tenant_root_id=tenant_root_node.id,
        tenant_root_code=tenant_root_node.code,
        tenant_root_path=str(tenant_root_node.path),
        stats=stats,
        tree=tree,
    )


# ---- E3: lazy-load children ------------------------------------------------


@router.get(
    "/tenants/{tenant_id}/org-nodes/{node_id}/children",
    response_model=OrgNodeChildrenResponse,
    summary="Get immediate children of an org-node (lazy-load)",
    description=(
        "Returns the immediate ACTIVE children of `node_id` within "
        "`tenant_id`. Used by the frontend to lazy-load subtrees that "
        "were not included in the initial /org-tree response (nodes "
        "with `has_children=true` and `loaded_children='none'`). "
        "Paginated by offset/limit. Each child carries its own "
        "`has_children` and `child_count` for further lazy expansion. "
        "Each child's `loaded_children` is 'none' (lazy by default — "
        "caller invokes this endpoint again for grandchildren if "
        "needed)."
    ),
)
async def get_node_children(
    tenant_id: UUID,
    node_id: UUID,
    offset: int = Query(0, ge=0, description="Pagination offset."),
    limit: int = Query(
        100,
        ge=1,
        le=200,
        description="Pagination limit (max 200; default 100).",
    ),
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.ORG_NODES,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
        anchor_dep=get_org_node_anchor,
    )),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    # 1. Resolve tenant. 404 if missing or RLS-filtered.
    tenant = await _tenants_repo.get_by_id(session, tenant_id)
    if tenant is None:
        raise TenantNotFoundError(
            f"Tenant {tenant_id} not visible to this session",
            tenant_id=str(tenant_id),
        )

    # 2. Verify parent node exists ACTIVE within this tenant.
    #    Distinguishes "parent has no children" (200, empty items)
    #    from "parent doesn't exist or is RLS-filtered" (404).
    parent_exists = await _org_repo.node_exists(
        session, tenant_id, node_id
    )
    if not parent_exists:
        raise OrgNodeNotFoundError(
            f"Org node {node_id} not visible to this session",
            tenant_id=str(tenant_id),
            node_id=str(node_id),
        )

    # 3. Fetch paginated children.
    rows, total = await _org_repo.list_children_paginated(
        session, tenant_id, node_id, offset=offset, limit=limit,
    )

    items = [_row_to_e3_item(n, cc) for n, cc in rows]
    return OrgNodeChildrenResponse(
        node_id=node_id,
        items=items,
        pagination=Pagination(total=total, offset=offset, limit=limit),
    )


# ---- Step 6.13 writes -------------------------------------------------------


@router.post(
    "/tenants/{tenant_id}/org-tree",
    response_model=OrgNodeRead,
    status_code=201,
    summary="Add an org_node under an existing parent",
    description=(
        "Multi-audience write (Step 6.13). Gated on "
        "`ADMIN.ORG_NODES.CONFIGURE.TENANT`. SUPER_ADMIN and "
        "PLATFORM_ADMIN pass via the GLOBAL->TENANT cascade; OWNER "
        "passes via direct TENANT grant. Cross-tenant calls by TENANT "
        "users surface as 404 (RLS-as-404 per D-17). The new node's "
        "ltree path is parent.path || lower(code).replace('-', '_'). "
        "Cascade-order rule: parent's node_type must sit strictly "
        "above the child's in the canonical sequence TENANT -> "
        "BUSINESS_UNIT -> HQ -> COUNTRY -> REGION -> STORE -> "
        "DEPARTMENT; level skipping is allowed."
    ),
)
async def add_org_node(
    tenant_id: UUID,
    body: OrgNodeCreateRequest,
    request: Request,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.ORG_NODES,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        anchor_dep=get_tenant_anchor,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    node = await _org_repo.add_node(
        session,
        tenant_id=tenant_id,
        parent_id=body.parent_id,
        node_type=body.node_type,
        code=body.code,
        name=body.name,
        auth=auth,
        request_id=request.state.request_id,
    )
    return OrgNodeRead.model_validate(node)


@router.patch(
    "/tenants/{tenant_id}/org-tree/{node_id}",
    response_model=OrgNodeRead,
    summary="Rename, recode, or reparent an existing org_node",
    description=(
        "Multi-audience write (Step 6.13). Same gate as POST. Body "
        "carries any of `name`, `code`, `parent_id`; at least one is "
        "required (empty body returns 422). `node_type` is immutable. "
        "On reparent the node and every descendant under it have their "
        "ltree paths rewritten in one transaction. Role assignments "
        "anchored at the moved node remain intact (D-11: reference is "
        "by stable id, not path). The tenant-root org_node can be "
        "renamed or recoded but not reparented."
    ),
)
async def edit_org_node(
    tenant_id: UUID,
    node_id: UUID,
    body: OrgNodePatchRequest,
    request: Request,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.ORG_NODES,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        anchor_dep=get_tenant_anchor,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    # Step 6.13: tenant-root reparent guard (existing).
    # Step 6.21.2 (LD8): on STORE-type targets, reject shared fields
    #   ``name`` and ``code`` (owned by /stores per architecture.md
    #   A.4 / A.5); reparent stays allowed. ``status`` is structurally
    #   unreachable on this path because OrgNodePatchRequest's
    #   ``extra="forbid"`` already 422s any ``status`` field.
    #
    # Both checks need the target's node_type, so fetch it once when
    # the body contains any field that would trigger one of the
    # checks. The repo's edit_node also re-fetches under
    # ``SELECT FOR UPDATE`` for its own logic; this pre-fetch is the
    # router's lighter no-lock check for the type-dependent rules
    # before any UPDATE runs.
    if (
        body.parent_id is not None
        or body.name is not None
        or body.code is not None
    ):
        schema = get_settings().db_schema
        target = await session.execute(
            text(
                f"SELECT node_type FROM {schema}.org_nodes "
                "WHERE id = :node_id AND tenant_id = :tenant_id"
            ),
            {"node_id": node_id, "tenant_id": tenant_id},
        )
        row = target.first()
        if row is not None:
            if row.node_type == "TENANT" and body.parent_id is not None:
                raise TenantRootNotReparentableError(
                    (
                        f"node_id={node_id} is a tenant root and "
                        "cannot be reparented"
                    ),
                    node_id=str(node_id),
                    tenant_id=str(tenant_id),
                )
            if row.node_type == "STORE":
                # Compute the set of disallowed shared fields actually
                # present in the patch body. ``parent_id`` (reparent)
                # is intentionally NOT in the disallowed set — STORE
                # reparent flows from either /org-tree or /stores per
                # architecture.md A.5 "Parent ownership: dual-endpoint
                # write".
                disallowed: list[str] = []
                if body.name is not None:
                    disallowed.append("name")
                if body.code is not None:
                    disallowed.append("code")
                if disallowed:
                    raise OrgNodeFieldNotAllowedForTypeError(
                        (
                            f"node_id={node_id} is STORE-type; "
                            f"fields {disallowed} cannot be modified "
                            "via /org-tree"
                        ),
                        node_id=str(node_id),
                        tenant_id=str(tenant_id),
                        fields=disallowed,
                        node_type="STORE",
                    )
        # If row is None we fall through to the repo, which raises
        # the appropriate 404. Same posture as Step 6.11.2's
        # transitions.

    node = await _org_repo.edit_node(
        session,
        tenant_id=tenant_id,
        node_id=node_id,
        name=body.name,
        code=body.code,
        parent_id=body.parent_id,
        auth=auth,
        reparent=body.parent_id is not None,
        request_id=request.state.request_id,
    )
    return OrgNodeRead.model_validate(node)


# ---- Pure-functional helpers ------------------------------------------------


def _build_tree(
    rows: list[tuple[OrgNode, int]],
    *,
    total_full: int,
    truncated: bool,
) -> tuple[list[OrgNodeTreeItem], OrgTreeStats]:
    """Build nested tree + stats from path-ordered (OrgNode, child_count) rows.

    Three passes (DP-5 lean — clarity over terseness):

      1. Build ``OrgNodeTreeItem`` for every non-TENANT node, indexed
         by id with a placeholder ``loaded_children='none'``.
      2. Link children into parents; identify roots as nodes whose
         parent is the TENANT root, or NULL (defensive; CHECK
         ck_org_nodes_root_parent_consistency rejects this for non-
         TENANT), or not present in the loaded set (defensive against
         depth-cut subtrees with absent parent — doesn't happen in
         practice because path-ordering loads parents before children).
      3. Finalize ``loaded_children`` based on whether all
         ``child_count`` immediate children are present in the loaded
         set: ``loaded == child_count`` -> "all"; ``0 == child_count``
         -> "none" (true leaf); ``loaded == 0 < child_count`` -> "none"
         (depth-cut); ``0 < loaded < child_count`` -> "partial" (only
         reachable post-E3 merging; not from E2 alone).

    Input is path-ordered (ltree path-ASC). Sibling-alphabetical-by-
    code falls out for free because path labels are
    ``lower(code).replace('-', '_')``.
    """
    by_id: dict[UUID, OrgNodeTreeItem] = {}
    non_tenant_rows = [
        (n, cc) for (n, cc) in rows if n.node_type != OrgNodeType.TENANT
    ]
    tenant_root_ids = {
        n.id for (n, _) in rows if n.node_type == OrgNodeType.TENANT
    }

    # Pass 1 — build items.
    for node, child_count in non_tenant_rows:
        item = OrgNodeTreeItem(
            id=node.id,
            node_type=node.node_type,
            name=node.name,
            code=node.code,
            status=node.status,
            created_at=node.created_at,
            updated_at=node.updated_at,
            has_children=(child_count > 0),
            child_count=child_count,
            loaded_children="none",  # placeholder; finalized in pass 3
            children=[],
        )
        by_id[node.id] = item

    # Pass 2 — link children, identify roots.
    roots: list[OrgNodeTreeItem] = []
    for node, _ in non_tenant_rows:
        item = by_id[node.id]
        parent_id = node.parent_id
        if (
            parent_id is None
            or parent_id in tenant_root_ids
            or parent_id not in by_id
        ):
            roots.append(item)
        else:
            by_id[parent_id].children.append(item)

    # Pass 3 — finalize loaded_children.
    for node, child_count in non_tenant_rows:
        item = by_id[node.id]
        if child_count == 0:
            # True leaf. "none" by convention; frontend uses
            # has_children=false to recognise leafhood.
            item.loaded_children = "none"
        else:
            loaded = len(item.children)
            label: Literal["all", "partial", "none"]
            if loaded == child_count:
                label = "all"
            elif loaded == 0:
                label = "none"
            else:
                label = "partial"
            item.loaded_children = label

    # Stats.
    stores = sum(
        1 for n, _ in non_tenant_rows if n.node_type == OrgNodeType.STORE
    )
    regions = sum(
        1 for n, _ in non_tenant_rows if n.node_type == OrgNodeType.REGION
    )
    if non_tenant_rows:
        depth_returned = (
            max(_path_depth(n.path) for n, _ in non_tenant_rows) - 1
        )
    else:
        depth_returned = 0
    stats = OrgTreeStats(
        total_nodes=total_full,
        nodes_returned=len(non_tenant_rows),
        stores=stores,
        regions=regions,
        depth_returned=depth_returned,
        truncated=truncated,
    )
    return roots, stats


def _path_depth(path: str) -> int:
    """Compute nlevel of an ltree path string. Equivalent to
    ``nlevel(path::ltree)`` in Postgres for our DDL-permitted label
    set (alphanumeric + underscore).
    """
    return path.count(".") + 1 if path else 0


def _row_to_e3_item(node: OrgNode, child_count: int) -> OrgNodeTreeItem:
    """Convert one (node, child_count) row into an E3 tree item.

    E3 always returns ``loaded_children='none'`` because the endpoint
    fetches only one level — grandchildren require another E3 call.
    """
    return OrgNodeTreeItem(
        id=node.id,
        node_type=node.node_type,
        name=node.name,
        code=node.code,
        status=node.status,
        created_at=node.created_at,
        updated_at=node.updated_at,
        has_children=(child_count > 0),
        child_count=child_count,
        loaded_children="none",
        children=[],
    )
