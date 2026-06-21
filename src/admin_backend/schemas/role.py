"""Pydantic v2 read schemas for the Role resource.

Three endpoints touch these schemas:

  - E1 ``GET /api/v1/roles`` returns a pre-grouped envelope
    ``{platform_roles: AudienceBlock, tenant_roles: AudienceBlock}``
    (deliberate D-30 exception — the pre-grouped shape doesn't compose
    with cross-group pagination).
  - E3 ``GET /api/v1/roles/{role_id}/permissions`` returns the role's
    granted permissions plus a parent-echo (role_id + role_name).
  - E6 ``GET /api/v1/permission-matrix`` references the role's
    audience for the column-header column block.

Conventions:
  - ``ConfigDict(from_attributes=True)`` for ORM hydration.
  - ISO 8601 timestamps with offset (Pydantic v2 default).
  - Nullable fields emitted explicitly as JSON ``null`` per D-28 / Q7.
  - Field semantics frozen append-only per D-31.
  - ``audience`` field is intentionally NOT included on
    ``RoleListItem`` — it's implied by the container key
    (``platform_roles`` vs ``tenant_roles``). Adding it would be
    redundant data that the frontend ignores.
  - Audit-actor columns hidden (Pattern (b) hide-policy per D-13;
    same as Steps 3.3 / 5.1 / 5.2).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from admin_backend.models.role import RoleAudience, RoleStatus
from admin_backend.schemas.permission import PermissionDetail


class RoleListItem(BaseModel):
    """Item shape for E1's pre-grouped response.

    The ``audience`` enum is NOT included — it's implied by the
    container key (``platform_roles`` vs ``tenant_roles``).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    code: str
    description: str | None
    status: RoleStatus
    is_system: bool
    user_count: int = Field(
        description=(
            "Active assignments referencing this role. Counted via a "
            "correlated subquery against user_role_assignments where "
            "status='ACTIVE'. RLS-scoped for TENANT JWTs (the count "
            "reflects only the caller's tenant's assignments). For "
            "PLATFORM JWTs the count spans all tenants for TENANT-"
            "audience roles, and platform-wide assignments for "
            "PLATFORM-audience roles."
        ),
    )
    created_at: datetime
    updated_at: datetime


class AudienceBlock(BaseModel):
    """One block of E1's pre-grouped response."""

    items: list[RoleListItem]
    total: int = Field(
        description=(
            "Number of items in this block, after audience and status "
            "filters. Pagination is not applied at this level (see "
            "endpoint notes); ``total == len(items)``."
        ),
    )


class RoleListResponse(BaseModel):
    """E1 response: roles pre-grouped by audience (deliberate D-30 exception).

    For TENANT JWTs the ``platform_roles`` block always returns
    ``{items: [], total: 0}``. The frontend suppresses the empty
    section header.
    """

    platform_roles: AudienceBlock = Field(
        description=(
            "PLATFORM-audience roles (Ithina staff). Empty for TENANT "
            "JWTs."
        ),
    )
    tenant_roles: AudienceBlock = Field(
        description="TENANT-audience roles (customer staff)."
    )


class RoleUpdateRequest(BaseModel):
    """PATCH body for role-edit (Step 6.18.3).

    All fields optional. The handler enforces at-least-one via
    ``body.model_dump(exclude_unset=True)`` and ``EmptyPatchError``.

    ``extra='forbid'`` rejects ``audience``, ``code``, ``is_system``,
    ``status``, and any audit-column write attempt at the schema layer
    (Pydantic 422 with offending field name). Audience is immutable
    per LD2 (would break the audience-check trigger invariant on
    existing assignments); ``code`` and ``is_system`` are intentional
    stability fields.

    ``permission_ids`` semantics: replace-set. Repo applies diff vs
    current grants and DELETEs/INSERTs accordingly. Unchanged rows
    preserve their ``created_at`` / ``created_by_*`` audit trail per
    LD5.

    ``name`` length matches ``ck_roles_name_length`` (1..100).
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    permission_ids: list[UUID] | None = None

    model_config = ConfigDict(extra="forbid")


class RoleDetail(BaseModel):
    """Self-contained role detail for the edit screen (E7).

    Returned by ``GET /api/v1/roles/{role_id}``. Frontend renders the
    edit form from this response alone — held permissions and
    grantable permissions are both embedded with display labels.

    Audience-scope coherence (LD2): when ``audience='TENANT'``,
    ``available_permissions`` excludes ``scope='GLOBAL'`` rows
    (GLOBAL scope is structurally invalid for TENANT-audience roles).
    PLATFORM-audience roles see the full catalogue minus held.

    Audit-actor columns hidden (Pattern (b) per D-13, mirrors E1's
    ``RoleListItem`` policy).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    code: str
    description: str | None
    audience: RoleAudience = Field(
        description=(
            "Role audience: PLATFORM (Ithina staff) or TENANT "
            "(customer staff). Drives the GLOBAL-scope filter on "
            "``available_permissions`` per LD2."
        ),
    )
    status: RoleStatus
    is_system: bool
    user_count: int = Field(
        description=(
            "Active assignments referencing this role. RLS-scoped on "
            "the tenant-side branch for TENANT JWTs (count reflects "
            "only the caller's tenant's assignments). For PLATFORM "
            "JWTs the count spans all tenants for TENANT-audience "
            "roles, and platform-wide assignments for PLATFORM-"
            "audience roles."
        ),
    )
    created_at: datetime
    updated_at: datetime
    permissions: list[PermissionDetail] = Field(
        description=(
            "Permissions currently granted to this role, sorted "
            "module/resource/action/scope ascending. Each carries 4 "
            "enum codes plus 4 display labels resolved server-side."
        ),
    )
    available_permissions: list[PermissionDetail] = Field(
        description=(
            "Permissions grantable to this role but not currently "
            "held. For TENANT-audience roles, ``scope='GLOBAL'`` rows "
            "are excluded (audience-scope coherence per LD2). Sort "
            "matches ``permissions``."
        ),
    )
