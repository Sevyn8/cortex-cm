"""Pydantic v2 read schemas for the TenantUser resource.

Mirrors ``schemas/platform_user.py`` (just shipped at Step 5.1) with
two additions:

  - ``tenant_id`` is exposed (it's load-bearing for the frontend —
    every consumer of a tenant_user wants to know which tenant it
    belongs to).
  - Hidden field set is wider: in addition to ``auth0_sub`` and the
    audit-actor IDs (which exist on PlatformUser too), the Pattern (b)
    discriminator columns ``*_by_user_type`` are also hidden. Three
    pairs total: created/updated/suspended × user_id+user_type.

Conventions (per D-28 / D-30 / D-31):
  - ``ConfigDict(from_attributes=True)`` for ORM-row hydration.
  - ISO 8601 timestamps with offset (Pydantic v2 default).
  - Nullable fields emitted explicitly as JSON ``null`` (Q7).
  - List shape is ``{items, pagination}`` per D-30.
  - Field semantics frozen append-only per D-31.

The list shape and the single-resource shape are identical at v0.
``TenantUserListItem = TenantUserRead`` aliases the two so they
cannot drift; if a future trim-down for list responses is needed,
split into a separate class then.

Step 6.8.3 — A1/A2 augmentation. ``UserRoleAssignmentItem`` lands
here as the canonical home (re-exported from
``schemas/platform_user.py``). Both ``TenantUserRead`` and
``PlatformUserRead`` gain a ``roles: list[UserRoleAssignmentItem]``
field. Distinct from the richer nested shapes in
``schemas/role_assignment.py`` (which serve the standalone
``/role-assignments`` endpoint with per-block envelopes); the inline
augmentation here is deliberately flat for per-user rendering.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from admin_backend.models.platform_user_role_assignment import (
    UserRoleAssignmentStatus,
)
from admin_backend.models.tenant_user import TenantUserStatus
from admin_backend.schemas.tenant import Pagination


class UserRoleAssignmentItem(BaseModel):
    """Inline role-assignment item for user response augmentation.

    Used by both tenant_users and platform_users responses. For
    platform users, ``org_node_id`` and ``org_node_name`` are always
    None (the underlying ``platform_user_role_assignments`` table has
    no org-node anchoring). The two keys are present in the JSON
    output regardless of audience so the wire shape is uniform.

    All assignments are returned regardless of status (ACTIVE +
    INACTIVE both ship); frontend filters as needed.

    Schema home: ``schemas/tenant_user.py`` (re-exported from
    ``schemas/platform_user.py``). Distinct from
    ``schemas/role_assignment.py`` shapes which serve the standalone
    ``/role-assignments`` endpoint with a richer nested envelope.
    """

    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    role_id: UUID
    role_name: str
    role_code: str
    status: UserRoleAssignmentStatus
    granted_at: datetime
    org_node_id: UUID | None
    org_node_name: str | None


class TenantUserRead(BaseModel):
    """Tenant (customer-side) user as returned by the API.

    Pattern (b) audit-actor columns and ``auth0_sub`` are intentionally
    absent. The frontend renders lifecycle state from the timestamp
    fields alone.

    ``roles`` (Step 6.8.3): inline list of role assignments. Always
    present; empty array (not null) for users with no assignments.
    Both ACTIVE and INACTIVE assignments included.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    email: str
    full_name: str
    status: TenantUserStatus
    invited_at: datetime | None
    invitation_accepted_at: datetime | None
    suspended_at: datetime | None
    created_at: datetime
    updated_at: datetime
    roles: list[UserRoleAssignmentItem]


# At v0, list shape == detail shape. Alias rather than duplicate so
# the two cannot drift; if a future trim-down for list responses is
# needed, split into a separate class then.
TenantUserListItem = TenantUserRead


class TenantUserListResponse(BaseModel):
    """List endpoint response envelope: {items, pagination} per D-30."""

    items: list[TenantUserListItem]
    pagination: Pagination


# =============================================================================
# Step 6.10.1 write schemas: TenantUserCreateRequest, TenantUserPatchRequest.
#
# Both ``extra="forbid"``. Server-managed and lifecycle-managed fields
# (``id``, ``status``, ``auth0_sub``, ``invited_at``,
# ``invitation_accepted_at``, ``suspended_*``, audit cols) are never
# accepted via the wire. Status transitions go through ``/suspend`` and
# ``/activate``; the Auth0 invite-accept callback (INVITED -> ACTIVE)
# is Stage 3 territory.
#
# Validators below honour the DDL CHECK constraints documented in
# ``db/raw_ddl/Ithina_postgres_SQL_DDL_tenant_users_v1.sql``:
#   - email must be lowercase (ck_tenant_users_email_lowercase)
#   - email shape (ck_tenant_users_email_format)
#   - full_name length 1-200 (ck_tenant_users_full_name_length)
#
# The audience pre-check for ``roles[]`` is handler-side (Option X
# shape (b) per the prompt): Pydantic validators in this codebase are
# pure (no DB access); the handler resolves each role_id against the
# catalogue before calling the Repo. Schema-level validation here is
# limited to non-empty + dedupe; audience and existence checks live in
# the handler.
# =============================================================================


class RoleAssignmentItem(BaseModel):
    """Single role-anchor pair in a ``roles[]`` request body (Step 6.14).

    Each item names the role to grant AND the org_node that anchors
    that grant. Tenant-root anchoring is just one option; the frontend
    resolves the tenant-root ``org_node_id`` via
    ``GET /tenants/{id}/org-tree`` and any non-archived anchor in the
    same tenant is acceptable.

    Pre-Step-6.14 the field was ``list[UUID]`` (role_id only) with the
    repo silently anchoring every assignment at the tenant root. The
    new shape is a breaking change on POST + PATCH ``/tenant-users``;
    handler-side validation rejects the legacy bare-UUID shape as 422
    via Pydantic's ``extra="forbid"`` + missing-field handling.

    Within-request duplicates of ``(role_id, org_node_id)`` are
    rejected by a handler-side pre-check (raises
    ``DuplicateRoleAssignmentInRequestError`` 422 ahead of the repo);
    Pydantic itself only enforces the per-item shape.
    """

    model_config = ConfigDict(extra="forbid")

    role_id: UUID
    org_node_id: UUID


def _dedupe_role_assignments(
    v: list[RoleAssignmentItem],
) -> list[RoleAssignmentItem]:
    """Dedupe (role_id, org_node_id) pairs preserving order.

    Two items with identical tuple are silently collapsed to one;
    the handler-side pre-check uses pre-dedupe length comparison to
    surface intent-revealing duplicates as 422
    ``DUPLICATE_ROLE_ASSIGNMENT_IN_REQUEST``. Dedupe here keeps the
    repo path tuple-set-clean.
    """
    seen: set[tuple[UUID, UUID]] = set()
    result: list[RoleAssignmentItem] = []
    for item in v:
        key = (item.role_id, item.org_node_id)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


class TenantUserCreateRequest(BaseModel):
    """Request shape for ``POST /api/v1/tenant-users``.

    ``status`` is server-forced to ``INVITED`` (the only state a freshly
    created row may enter per ``ck_tenant_users_auth0_sub_consistency``;
    INVITED -> ACTIVE is the Auth0 invite-accept callback flow, out of
    scope for v0). ``id`` is server-generated via DB ``DEFAULT
    uuidv7()``. Audit-actor pair is populated from ``auth.user_id`` /
    ``auth.user_type`` in the repo.

    Bundled role assignments name a (role, anchor) pair each (Step
    6.14). Any TENANT-audience role anchored at any non-archived
    org_node in the same tenant is acceptable; the handler pre-checks
    role audience and org_node existence/status to surface clean 422
    codes ahead of the DB trigger / FK rejects.

    ``min_length=1`` enforces non-empty on create. Within-request
    duplicates of ``(role_id, org_node_id)`` reject as 422
    ``DUPLICATE_ROLE_ASSIGNMENT_IN_REQUEST`` (handler-side pre-check).
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    roles: list[RoleAssignmentItem] = Field(min_length=1)

    @field_validator("email", mode="after")
    @classmethod
    def _lowercase_email(cls, v: str) -> str:
        """Lowercase email to satisfy ``ck_tenant_users_email_lowercase``.

        Same rationale as ``TenantCreateRequest._lowercase_email``.
        """
        return v.lower()


class TenantUserPatchRequest(BaseModel):
    """Request shape for ``PATCH /api/v1/tenant-users/{user_id}``.

    All fields optional; the handler raises ``EmptyPatchError`` (422)
    if ``model_dump(exclude_unset=True)`` produces an empty dict.

    Editable fields (locked decision 2): ``full_name``, ``email``,
    ``roles`` (Step 6.14 diff-replace semantics — see below).

    ``roles=None`` (field omitted): no change to assignments.
    ``roles=[]`` (empty list): revoke ALL current ACTIVE assignments.
    ``roles=[...]``: diff-replace against the current ACTIVE set;
    unchanged (role_id, org_node_id) tuples retain their original
    ``granted_at`` and ``granted_by_*``.

    Excluded fields (rejected at schema layer via ``extra="forbid"``):
      - ``tenant_id``: per-row immutable (tenant migration is out of
        scope for v0; would need composite-FK + RLS migration).
      - ``status``: transitions go through ``/suspend`` and
        ``/activate``.
      - ``auth0_sub``, ``invited_at``, ``invitation_accepted_at``,
        ``suspended_*``: server-managed lifecycle.
      - ``id``, ``created_at``, ``updated_at``, ``*_by_user_id``,
        ``*_by_user_type``: server-managed.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    # Note: no min_length on roles. Empty list is a valid PATCH value
    # (means 'revoke all current ACTIVE assignments'). Distinct from
    # the None default which means 'no change'.
    roles: list[RoleAssignmentItem] | None = None

    @field_validator("email", mode="after")
    @classmethod
    def _lowercase_email(cls, v: str | None) -> str | None:
        return v.lower() if v is not None else None
