"""Pydantic v2 read schemas for permissions and the permission matrix.

Three endpoints touch these schemas:

  - E2 ``GET /api/v1/permissions`` returns the flat catalogue
    ``{items, pagination}`` per D-30. No display labels — labels live
    in E6's render-ready response only (Q-side decision: E2 consumers
    are admin-side dropdowns and reference views that have the labels
    rendered separately).
  - E3 ``GET /api/v1/roles/{role_id}/permissions`` returns the same
    item shape under a parent-echo envelope (``role_id`` and
    ``role_name`` at the top level, plus an ``items`` array). No
    pagination — a role has bounded permissions.
  - E6 ``GET /api/v1/permission-matrix`` returns the render-ready grid
    with ``cells: list[bool]`` position-aligned to the ``roles`` column
    array.

E6 invariants (M1-M8 in BUILD_PLAN's Step 6.1):
  - ``len(row.cells) == len(roles)`` for every row.
  - ``cells[i]`` is the grant state for ``roles[i]``.
  - ``roles`` ordered audience_asc, name_asc.
  - ``rows`` ordered module/resource/action/scope ascending.
  - TENANT JWT: ``roles`` filtered to ``audience='TENANT'`` only;
    ``cells[]`` shrinks correspondingly.
  - 4 enum codes + 4 display labels per row, joined from ``lookups``.

Conventions: ``ConfigDict(from_attributes=True)``, no aliasing,
nullable fields explicit per D-28 / Q7, append-only per D-31.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.role import RoleAudience
from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.schemas.tenant import Pagination


class PermissionRead(BaseModel):
    """Permission catalogue item. Used by E2 and E3 (no display labels)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    module: ModuleCode
    resource: PermissionResource
    action: PermissionAction
    scope: PermissionScope
    code: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class PermissionDetail(BaseModel):
    """Permission with display labels for the role-edit screen.

    Used by E7 (``GET /api/v1/roles/{role_id}``) only. Carries the same
    enum slots as ``PermissionRead`` plus four ``*_label`` strings
    resolved server-side via LEFT JOIN against ``core.lookups`` (mirror
    of ``PermissionMatrixRow`` per LD3 / LD4). Distinct from
    ``PermissionRead`` so the existing E2/E3 wire shape stays
    untouched.

    Schema-quality fields omitted (``created_at``, ``updated_at``):
    the role-edit screen does not need them; the catalogue lifecycle
    is admin-managed via migrations.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    module: ModuleCode
    module_label: str
    resource: PermissionResource
    resource_label: str
    action: PermissionAction
    action_label: str
    scope: PermissionScope
    scope_label: str
    code: str
    description: str | None


class PermissionListResponse(BaseModel):
    """E2 response envelope: ``{items, pagination}`` per D-30."""

    items: list[PermissionRead]
    pagination: Pagination


class RolePermissionsResponse(BaseModel):
    """E3 response: parent role identity echoed + permissions list.

    No pagination — a role has bounded permissions (typically 5-30);
    the parent echo (``role_id`` + ``role_name``) lets the frontend
    avoid an additional E1 lookup when rendering the right-pane header.
    """

    role_id: UUID = Field(
        description=(
            "Echo of the path parameter. Frontend race-condition guard: "
            "if the user clicks two roles in quick succession, the "
            "older response can be discarded by id-equality without a "
            "round-trip."
        ),
    )
    role_name: str = Field(
        description=(
            "Display name of the role. Saves the frontend a cross-"
            "lookup against E1's cached list when rendering the right-"
            "pane header."
        ),
    )
    items: list[PermissionRead]


class PermissionMatrixRoleColumn(BaseModel):
    """One column header in E6's grid."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    audience: RoleAudience


class PermissionMatrixRow(BaseModel):
    """One permission row in E6's grid.

    Each row carries 4 enum codes + 4 display labels + a boolean array
    of grant states position-aligned with the roles[] column array.
    """

    id: UUID
    module: ModuleCode
    module_label: str
    resource: PermissionResource
    resource_label: str
    action: PermissionAction
    action_label: str
    scope: PermissionScope
    scope_label: str
    cells: list[bool] = Field(
        description=(
            "Position-based grant array. ``cells[i]`` is the grant "
            "state of this permission for ``roles[i]`` (where "
            "``roles`` is the top-level column array of the same "
            "response). ``len(cells) == len(roles)`` is a hard "
            "invariant — the backend computes the alignment."
        ),
    )


class PermissionMatrixResponse(BaseModel):
    """E6 response: render-ready grid (deliberate D-30 exception).

    No ``items`` wrapper, no pagination. PLATFORM JWTs see the full
    grid (15 columns × N rows); TENANT JWTs see TENANT-audience role
    columns only (12 columns × N rows; ``cells[]`` arrays shrink
    correspondingly).
    """

    roles: list[PermissionMatrixRoleColumn] = Field(
        description=(
            "Column headers, ordered audience_asc, name_asc. PLATFORM "
            "columns first, alphabetical within. TENANT JWTs see only "
            "audience='TENANT' columns."
        ),
    )
    rows: list[PermissionMatrixRow] = Field(
        description=(
            "Permission rows, ordered module/resource/action/scope "
            "ascending. Same dataset for both user types — the catalogue "
            "is reference data."
        ),
    )
