"""Pydantic v2 read schemas for the PlatformUser resource.

Mirrors `schemas/tenant.py`'s conventions:
  - `ConfigDict(from_attributes=True)` for ORM-row hydration.
  - ISO 8601 timestamps with offset (Pydantic v2 default for tz-aware
    datetimes).
  - Nullable fields emitted explicitly as JSON ``null`` (Q7).

Hidden by deliberate design:
  - ``auth0_sub`` — internal mapping to Auth0 identity provider; no UI use.
  - ``created_by_user_id``, ``updated_by_user_id``, ``suspended_by_user_id``
    — audit-actor IDs; same hide-policy as Tenant. Internal lineage only.

The list shape and the single-resource shape are identical at v0 (the
dataset is small; no need for a slimmer list projection). One class
serves both via the ``PlatformUserListItem = PlatformUserRead`` alias —
keeping the type names distinct in router signatures and OpenAPI while
avoiding maintenance drift between two identical shapes.

Step 6.8.3 — A1/A2 augmentation. ``PlatformUserRead`` gains a
``roles: list[UserRoleAssignmentItem]`` field; the
``UserRoleAssignmentItem`` type is canonically defined in
``schemas/tenant_user.py`` and re-exported here.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from admin_backend.models.platform_user import PlatformUserStatus
from admin_backend.schemas.tenant import Pagination
from admin_backend.schemas.tenant_user import UserRoleAssignmentItem

__all__ = [
    "PlatformUserListItem",
    "PlatformUserListResponse",
    "PlatformUserRead",
    "UserRoleAssignmentItem",
]


class PlatformUserRead(BaseModel):
    """Platform (Ithina staff) user as returned by the API.

    Audit-actor IDs and ``auth0_sub`` are intentionally absent: those
    are internal-only. The frontend renders lifecycle state from the
    timestamp fields alone.

    ``roles`` (Step 6.8.3): inline list of role assignments from
    ``platform_user_role_assignments``. Each item's ``org_node_id``
    and ``org_node_name`` are always null (platform-side assignments
    have no org-node anchor); the keys are still present so the wire
    shape stays uniform with tenant-side. Always present; empty array
    (not null) for platform users with no assignments. Both ACTIVE
    and INACTIVE assignments included.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    status: PlatformUserStatus
    invited_at: datetime | None
    invitation_accepted_at: datetime | None
    suspended_at: datetime | None
    created_at: datetime
    updated_at: datetime
    roles: list[UserRoleAssignmentItem]


# At v0, list shape == detail shape. Alias rather than duplicate so the
# two cannot drift; if a future trim-down for list responses is needed,
# split into a separate class then.
PlatformUserListItem = PlatformUserRead


class PlatformUserListResponse(BaseModel):
    """List endpoint response envelope: {items, pagination} per D-30."""

    items: list[PlatformUserListItem]
    pagination: Pagination
