"""Pydantic schemas for /role-assignments endpoint (Step 6.8.3).

Two grouped blocks per the API contract: ``platform_assignments`` and
``tenant_assignments``. Each block is its own ``{items, pagination}``
envelope so per-block totals and offset/limit semantics stay
independent (the two physical tables are heterogeneous datasets that
share only their grouped delivery shape, not their pagination state).

Per-row item shapes reflect their physical table — platform has no
tenant/org_node fields, tenant does. No row-level discriminator: the
block name carries the audience.

Hidden fields (per the Step 6.1 H1 hidden-fields convention, D-13
Pattern (b)): ``granted_by_user_id``, ``granted_by_user_type``,
``revoked_by_user_id``, ``revoked_by_user_type``.

Distinct from ``schemas/tenant_user.py``'s ``UserRoleAssignmentItem``
(the inline-augmentation shape on /tenant-users and /platform-users):
this file's shapes have richer nested mini-objects (role, tenant_user,
tenant, org_node, platform_user) that the dedicated /role-assignments
view consumes; the inline augmentation deliberately uses a flat shape
so the per-user `roles[]` chip-list stays compact.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from admin_backend.schemas.tenant import Pagination


class _AssignedRole(BaseModel):
    """Inline role mini-object on each assignment row."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    code: str
    name: str
    audience: str  # 'PLATFORM' or 'TENANT'


class _AssignedPlatformUser(BaseModel):
    """Inline platform_user mini-object on platform-side rows."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    email: str
    full_name: str


class _AssignedTenantUser(BaseModel):
    """Inline tenant_user mini-object on tenant-side rows."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    email: str
    full_name: str


class _AssignedTenant(BaseModel):
    """Inline tenant mini-object on tenant-side rows."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    name: str


class _AssignedOrgNode(BaseModel):
    """Inline org_node mini-object on tenant-side rows."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    name: str
    code: str
    node_type: str


class PlatformAssignmentItem(BaseModel):
    """Row in the ``platform_assignments`` block."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    platform_user: _AssignedPlatformUser
    role: _AssignedRole
    status: str
    granted_at: datetime
    revoked_at: datetime | None
    updated_at: datetime


class TenantAssignmentItem(BaseModel):
    """Row in the ``tenant_assignments`` block."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    tenant_user: _AssignedTenantUser
    tenant: _AssignedTenant
    org_node: _AssignedOrgNode
    role: _AssignedRole
    status: str
    granted_at: datetime
    revoked_at: datetime | None
    updated_at: datetime


class PlatformAssignmentsBlock(BaseModel):
    """``{items, pagination}`` envelope for the platform-side block."""

    model_config = ConfigDict(extra="forbid")

    items: list[PlatformAssignmentItem]
    pagination: Pagination


class TenantAssignmentsBlock(BaseModel):
    """``{items, pagination}`` envelope for the tenant-side block."""

    model_config = ConfigDict(extra="forbid")

    items: list[TenantAssignmentItem]
    pagination: Pagination


class RoleAssignmentsResponse(BaseModel):
    """Response shape for ``/role-assignments``.

    For PLATFORM JWTs: both blocks may be populated.

    For TENANT JWTs: ``platform_assignments`` block has
    ``items=[]`` and ``pagination.total=0`` because the platform-side
    query is short-circuited at the router (locked decision 12 of
    Step 6.8.3, security-load-bearing —
    ``platform_user_role_assignments`` has no RLS, so the app-layer
    routing is the only barrier). ``tenant_assignments`` is RLS-scoped
    to the calling tenant via the D-29 OR-branch on
    ``tenant_user_role_assignments_tenant_isolation``.
    """

    model_config = ConfigDict(extra="forbid")

    platform_assignments: PlatformAssignmentsBlock = Field(
        description=(
            "PLATFORM-audience role assignments to platform_users. "
            "Empty (items=[], pagination.total=0) for TENANT JWTs."
        ),
    )
    tenant_assignments: TenantAssignmentsBlock = Field(
        description=(
            "TENANT-audience role assignments to tenant_users at "
            "org_node anchors. RLS-scoped to the calling tenant for "
            "TENANT JWTs; full list (across all tenants) for "
            "PLATFORM JWTs via D-29's OR-branch."
        ),
    )
