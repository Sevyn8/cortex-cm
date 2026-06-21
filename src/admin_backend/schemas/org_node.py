"""Pydantic v2 schemas for the org-tree endpoints (E2 + E3).

Three new shapes:

  - ``OrgNodeTreeItem``: one node in the tree. Recursive via
    ``children: list[OrgNodeTreeItem]``. Carries lazy-load metadata
    (``has_children``, ``child_count``, ``loaded_children``) so the
    frontend knows which subtrees to lazy-fetch via E3.
  - ``OrgTreeStats``: counts and depth for the right-pane header and
    for frontend decisions (e.g., display "partial tree" notice when
    ``truncated=true``).
  - ``OrgTreeResponse``: E2 envelope. Carries ``tenant_id``,
    ``tenant_name``, ``stats``, and ``tree``.
  - ``OrgNodeChildrenResponse``: E3 envelope. Carries the parent
    ``node_id`` echo, paginated ``items``, and a standard
    ``Pagination`` block.

Conventions (per D-28 / D-30 / D-31):
  - ``ConfigDict(from_attributes=True)`` on schemas hydrated from
    ORM rows (``OrgNodeTreeItem``).
  - ISO 8601 timestamps with offset (Pydantic v2 default).
  - Nullable fields emitted explicitly as JSON ``null`` (Q7).
  - Field semantics frozen append-only per D-31.
  - **D-30 exception** for E2: the org-tree is a singleton resource
    for the tenant (not a paginatable collection), so the response
    is ``{tenant_id, tenant_name, stats, tree}`` rather than
    ``{items, pagination}``. E3 follows D-30 normally.
  - Hidden fields: all six Pattern (b) audit-actor columns
    (``created_by_*``, ``updated_by_*``, ``archived_by_*``) are
    intentionally absent from ``OrgNodeTreeItem``. Internal lineage,
    not for UI.

Recursive type. Pydantic v2 supports forward references inside the
same module; ``OrgNodeTreeItem.model_rebuild()`` at module bottom
resolves the self-reference. Without ``model_rebuild()``, Pydantic
raises at first validation. Tested at ``test_t1`` and ``test_t5``.

``loaded_children`` semantics:
  - ``"all"``     — every child of this node is in the response.
                    ``len(children) == child_count``.
  - ``"partial"`` — some children present, more available via E3.
                    ``0 < len(children) < child_count``. Reachable
                    only via E3 paginated reads (E2 returns either
                    "all" for nodes within the depth window, or
                    "none" for nodes at the depth cutoff with kids
                    below).
  - ``"none"``    — has_children is true but children is empty,
                    OR has_children is false (true leaf).
                    Frontend disambiguates via has_children.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from admin_backend.models.org_node import OrgNodeStatus, OrgNodeType
from admin_backend.schemas.tenant import Pagination


class OrgNodeTreeItem(BaseModel):
    """One node in the org-tree. Recursive via ``children``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    node_type: OrgNodeType = Field(
        description=(
            "One of BUSINESS_UNIT, HQ, COUNTRY, REGION, STORE, "
            "DEPARTMENT. TENANT-type nodes are excluded from all "
            "responses (the tenant root itself is not part of the "
            "rendered tree)."
        ),
    )
    name: str = Field(description="Display name (e.g., 'Texas Region').")
    code: str = Field(
        description="Short code (e.g., 'TX', 'BU-HQ'). Tenant-unique."
    )
    status: OrgNodeStatus = Field(
        description=(
            "Lifecycle status. Always 'ACTIVE' in v0 — INACTIVE / "
            "ARCHIVED are filtered out at the Repo layer. The field "
            "is exposed so frontend code is forward-compatible with "
            "future status filters."
        ),
    )
    created_at: datetime
    updated_at: datetime

    has_children: bool = Field(
        description=(
            "True if this node has any ACTIVE immediate children, "
            "regardless of whether they are present in this "
            "response. Drives the expand-arrow rendering."
        ),
    )
    child_count: int = Field(
        description=(
            "Count of ACTIVE immediate children. Always reflects the "
            "FULL subtree's children (server-side count), not what is "
            "in the response. Frontend uses this for badge UI and to "
            "decide whether E3 needs to paginate."
        ),
    )
    loaded_children: Literal["all", "partial", "none"] = Field(
        description=(
            "Loading state of `children`. "
            "'all'     = every child is present in `children`. "
            "'partial' = some children present, more available via "
            "E3 with offset > 0. "
            "'none'    = either a true leaf (has_children=false) or "
            "a depth-cut node (has_children=true, children=[]). "
            "Disambiguate via has_children: "
            "  has_children=false + 'none' -> true leaf, no E3 call. "
            "  has_children=true  + 'none' -> call E3 to load. "
            "  has_children=true  + 'partial' -> call E3 with offset>0."
        ),
    )
    children: list[OrgNodeTreeItem] = Field(
        default_factory=list,
        description=(
            "Child nodes. Empty list for true leaves AND for depth-cut "
            "subtrees. Sorted alphabetical by lowercased code (the "
            "ltree path-ASC ordering yields this for free)."
        ),
    )

    # Pattern (b) audit-actor columns are NOT exposed.


class OrgTreeStats(BaseModel):
    """Counts and depth for the right-pane header and frontend decisions."""

    total_nodes: int = Field(
        description=(
            "Full count of non-TENANT ACTIVE nodes for this tenant "
            "(the entire tree, not just what is in this response). "
            "Frontend uses this to decide whether to display a 'large "
            "tenant' indicator."
        ),
    )
    nodes_returned: int = Field(
        description=(
            "Count of nodes actually present in `tree` (recursively). "
            "Equals total_nodes when truncated=false; less when "
            "truncated=true."
        ),
    )
    stores: int = Field(
        description=(
            "Count of nodes IN THE RESPONSE with node_type='STORE'. "
            "May undercount the full tree when depth is limited. "
            "Frontend uses this for the right-pane header badge."
        ),
    )
    regions: int = Field(
        description=(
            "Count of nodes IN THE RESPONSE with node_type='REGION'. "
            "May undercount the full tree when depth is limited."
        ),
    )
    depth_returned: int = Field(
        description=(
            "Maximum nlevel(path) across nodes in the response, "
            "MINUS 1 to exclude the implicit TENANT root (so depth=1 "
            "means HQ-level nodes, depth=4 means HQ + 3 mid-levels). "
            "0 if the tree is empty."
        ),
    )
    truncated: bool = Field(
        description=(
            "True if the server auto-reduced depth below requested "
            "due to the payload cap (1000 nodes). Frontend should "
            "display a 'partial tree' notice. False for organic small "
            "tenants and when the requested depth was honoured."
        ),
    )


class OrgTreeResponse(BaseModel):
    """Response for E2: GET /api/v1/tenants/{tenant_id}/org-tree.

    Deliberate D-30 exception: this is a singleton resource for the
    tenant, not a paginatable collection, so the envelope is
    ``{tenant_id, tenant_name, tenant_root_id, tenant_root_code,
    tenant_root_path, stats, tree}`` rather than ``{items, pagination}``.
    """

    tenant_id: UUID = Field(description="Echo of path-param tenant_id.")
    tenant_name: str = Field(
        description=(
            "Current tenants.name. Saves the frontend a cross-lookup "
            "when rendering the right-pane header."
        ),
    )
    tenant_root_id: UUID = Field(
        description=(
            "UUID of the tenant-root org_node (the implicit TENANT-typed "
            "node that anchors the tenant's tree). Use this as "
            "``parent_id`` when calling POST /org-tree to create a "
            "top-level node directly under the tenant. Distinct from "
            "``tenant_id`` (which is the ``tenants.id`` UUID); the two "
            "are independent."
        ),
    )
    tenant_root_code: str = Field(
        description=(
            "Code of the tenant-root org_node (matches "
            "``org_nodes.code``). Typically derived from the tenant's "
            "display_code or name at tenant-create time."
        ),
    )
    tenant_root_path: str = Field(
        description=(
            "ltree path of the tenant-root org_node (single label; "
            "matches ``org_nodes.path::text``). All descendants' paths "
            "have this as their root segment."
        ),
    )
    stats: OrgTreeStats
    tree: list[OrgNodeTreeItem] = Field(
        description=(
            "Top-level org nodes (children of the tenant root, whose "
            "id/code/path now appear as separate top-level fields on "
            "this response). Empty list for tenants with zero "
            "descendants beyond the tenant root."
        ),
    )


class OrgNodeChildrenResponse(BaseModel):
    """Response for E3: paginated children of a parent node.

    GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children
    """

    node_id: UUID = Field(description="Echo of path-param parent node_id.")
    items: list[OrgNodeTreeItem] = Field(
        description=(
            "Immediate ACTIVE children of the parent node, sorted "
            "alphabetical by lowercased code. Each child's "
            "`loaded_children` is 'none' (lazy by default — caller "
            "invokes E3 again for grandchildren if needed)."
        ),
    )
    pagination: Pagination = Field(
        description="Standard {total, offset, limit} envelope per D-30."
    )


# Pydantic v2 forward-ref resolution for the recursive ``children`` field.
OrgNodeTreeItem.model_rebuild()


# ---- Step 6.13 write schemas ----------------------------------------------


class OrgNodeCreateRequest(BaseModel):
    """Request body for POST /api/v1/tenants/{tenant_id}/org-tree.

    Add a new org_node under an existing parent. ``node_type`` is
    required and must NOT be TENANT (tenant roots are created at tenant
    provisioning; this surface cannot make one). ``parent_id`` is
    required; the parent must exist in the same tenant and its
    ``node_type`` must sit strictly above this child's in the canonical
    cascade order.

    ``extra="forbid"`` rejects unknown fields at Pydantic time (422
    before the handler runs). Code format is enforced server-side via
    the DDL CHECK ``ck_org_nodes_code_format`` plus a Pydantic regex
    (1-64 chars, alphanumerics with hyphens, no underscores, must
    start/end alphanumeric).
    """

    model_config = ConfigDict(extra="forbid")

    parent_id: UUID = Field(
        description=(
            "UUID of the parent org_node. Must exist in the same tenant. "
            "Use the tenant-root's id when adding a top-level node "
            "(typical for first BUSINESS_UNIT or HQ)."
        ),
    )
    node_type: OrgNodeType = Field(
        description=(
            "Type of node to create. TENANT is rejected here "
            "(tenant roots are provisioned with the tenant). STORE is "
            "rejected here too (Step 6.21.2 — stores must be created "
            "via POST /api/v1/stores, which creates the paired "
            "STORE-type org_node atomically)."
        ),
    )
    code: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?$",
        description=(
            "Short tenant-unique code (case-insensitive). 1-64 chars; "
            "alphanumerics plus hyphens; must start and end alphanumeric. "
            "No underscores (org_node code convention; underscores are "
            "reserved for the ltree-label form, which is derived from "
            "this code via _path_label by replacing hyphens with "
            "underscores)."
        ),
    )
    name: str = Field(
        min_length=1,
        max_length=200,
        description="Display name (1-200 chars).",
    )

    @model_validator(mode="after")
    def _reject_forbidden_node_types(self) -> "OrgNodeCreateRequest":
        # Pydantic raises ValueError as ValidationError -> 422. The
        # message text isn't the wire code; the router could convert
        # ValidationError to a domain-shaped error if ever desired,
        # but the default 422 with the offending field path is fine.
        if self.node_type == OrgNodeType.TENANT:
            raise ValueError(
                "node_type 'TENANT' is not allowed on POST; tenant "
                "roots are provisioned at tenant creation."
            )
        if self.node_type == OrgNodeType.STORE:
            # Step 6.21.2: stores own the paired STORE-type org_node;
            # POST /org-tree refuses to create one bare. The caller
            # is directed to POST /api/v1/stores, which creates both
            # rows atomically.
            raise ValueError(
                "node_type 'STORE' is not allowed on POST; STORE-type "
                "nodes are created via POST /api/v1/stores."
            )
        return self


class OrgNodePatchRequest(BaseModel):
    """Request body for PATCH /api/v1/tenants/{tenant_id}/org-tree/{node_id}.

    Three mutable fields, all optional:

    - ``name``: rename only.
    - ``code``: code change (tenant-wide uniqueness re-checked).
    - ``parent_id``: reparent (subtree path rewrite; cascade-order
      checked; cycle prevention via ltree ``@>``).

    ``node_type`` is intentionally absent (immutable per LD3). At least
    one of the three must be set; ``{}`` is rejected as 422 EMPTY_PATCH
    via the model validator below (raising ValueError surfaces as a
    Pydantic ValidationError -> 422, which the router catches and
    re-emits as ``EmptyPatchError`` for the standard envelope).

    ``extra="forbid"`` rejects unknown fields including any attempt to
    sneak ``node_type`` through.
    """

    model_config = ConfigDict(extra="forbid")

    parent_id: UUID | None = Field(
        default=None,
        description=(
            "New parent_id. Triggers reparent (subtree path rewrite). "
            "Cycle, cascade-order, and same-tenant invariants enforced. "
            "Rejected with 422 TENANT_ROOT_NOT_REPARENTABLE when target "
            "is the tenant root."
        ),
    )
    code: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?$",
        description="New code. Tenant-wide case-insensitive uniqueness applies.",
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="New display name.",
    )

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "OrgNodePatchRequest":
        if (
            self.parent_id is None
            and self.code is None
            and self.name is None
        ):
            raise ValueError(
                "PATCH must include at least one of: parent_id, code, name."
            )
        return self


class OrgNodeRead(BaseModel):
    """Response shape for POST and PATCH org_node writes.

    Mirrors the field set tests expect and the frontend renders. Hidden
    fields per H1: all six Pattern (b) audit-actor columns absent.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    tenant_id: UUID
    parent_id: UUID | None
    node_type: OrgNodeType
    code: str
    name: str
    status: OrgNodeStatus
    path: str
    created_at: datetime
    updated_at: datetime
