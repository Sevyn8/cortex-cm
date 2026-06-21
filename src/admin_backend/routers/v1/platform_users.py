"""Platform users router (Step 5.1; augmented at Step 6.8.3; retrofitted at Step 6.9.3.2).

Two GET endpoints under ``/platform-users``:

  - ``GET /api/v1/platform-users``         — list (filter / search /
                                              sort / pagination)
  - ``GET /api/v1/platform-users/{user_id}`` — detail

Auth posture (post Step 6.9.3.2): both endpoints gate on
``ADMIN.USERS.VIEW.GLOBAL`` via ``Depends(require(...))``. The
prior ``_require_platform_auth(auth)`` user-type-only check (Step
5.1) was retired at 6.9.3.2 — replaced by the RBAC gate factory
introduced at Step 6.9.2. Behavioral envelope is equivalent: TENANT
JWTs and PLATFORM users without ``ADMIN.USERS.VIEW.GLOBAL`` are denied
with 403 ``PERMISSION_DENIED``. ``platform_users`` has no RLS (per
the DDL's "No Row-Level Security" section), so the gate is the sole
access boundary.

Per D-30: list returns ``{items, pagination}``; detail returns the
resource directly. Per D-31: response field semantics frozen
append-only.

Per D-17: missing-or-not-visible rows surface as 404 from
``PlatformUserNotFoundError``. ``platform_users`` has no RLS so
"not visible" reduces to "doesn't exist," but the same shape applies.

Sort key validation: an unknown ``sort`` raises ``InvalidSortKeyError``
in the Repo (a ValueError subclass). The handler catches it and
re-raises as ``InvalidSortKeyClientError`` (a ClientError) so it
surfaces as 400 instead of 500.

Step 6.8.3 — A2 augmentation: each response item now carries an
inline ``roles: list[UserRoleAssignmentItem]`` field. For platform
users every item's ``org_node_id`` and ``org_node_name`` are null
(the underlying ``platform_user_role_assignments`` table has no
org-node anchoring); the keys are still present so the wire shape
stays uniform with tenant-side.
"""
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.permissions import require
from admin_backend.dependencies import get_tenant_session_dep
from admin_backend.errors import ClientError, InvalidSortKeyClientError
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.platform_user import PlatformUserStatus
from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.repositories._errors import InvalidSortKeyError
from admin_backend.repositories.platform_users import (
    PlatformUserDetailRow,
    PlatformUserListRow,
    PlatformUsersRepo,
)
from admin_backend.schemas.platform_user import (
    PlatformUserListItem,
    PlatformUserListResponse,
    PlatformUserRead,
)
from admin_backend.schemas.tenant import Pagination
from admin_backend.schemas.tenant_user import UserRoleAssignmentItem


router = APIRouter(prefix="/platform-users", tags=["platform-users"])

# Stateless instance reused across requests (mirrors TenantsRepo).
_repo = PlatformUsersRepo()


# ---- Errors specific to this router ----------------------------------------


# DEAD CODE candidate (post-Step-6.9.3.2 retrofit).
#
# ``_require_platform_auth`` was retired at Step 6.9.3.2; this class was
# its sole raise site. Kept here as a forward-defensive artefact in case
# a future PLATFORM-only check needs a distinct error code from
# ``PERMISSION_DENIED``. Safe to remove once Stage 3 confirms no
# consumer emerges; tracked as a CLAUDE.md forward note.
class PlatformAccessRequiredError(ClientError):
    """Raised when a non-PLATFORM JWT calls a PLATFORM-only endpoint.

    Retired at Step 6.9.3.2 — `_require_platform_auth` was replaced
    with ``Depends(require(ADMIN, USERS, VIEW, GLOBAL))`` which raises
    ``PermissionDeniedError`` (code ``PERMISSION_DENIED``). This class
    has no current raise site.
    """

    public_message = "This endpoint requires platform access"
    http_status = 403
    code = "PLATFORM_ACCESS_REQUIRED"


class PlatformUserNotFoundError(ClientError):
    """Raised when a platform_user lookup by id finds nothing."""

    public_message = "Platform user not found"
    http_status = 404
    code = "PLATFORM_USER_NOT_FOUND"


# InvalidSortKeyClientError lives in admin_backend.errors at Step 5.2;
# imported above. The router still owns the catch-and-rewrap site.
#
# _require_platform_auth retired at Step 6.9.3.2. Both call sites
# replaced with Depends(require(ADMIN, USERS, VIEW, GLOBAL)). The
# gate's PermissionDeniedError carries code='PERMISSION_DENIED'
# instead of the prior 'PLATFORM_ACCESS_REQUIRED'.


# ---- Mappers ---------------------------------------------------------------


def _role_item_from_dict(d: dict[str, Any]) -> UserRoleAssignmentItem:
    """Map a single jsonb-decoded role dict to ``UserRoleAssignmentItem``.

    For platform users, the Repo's subquery emits ``org_node_id`` and
    ``org_node_name`` as JSON null (cast NULL::uuid / NULL::text); the
    Pydantic schema's ``UUID | None`` / ``str | None`` types accept
    that as None directly. Same explicit-construction pattern as
    ``routers/v1/tenant_users.py``.
    """
    return UserRoleAssignmentItem(
        assignment_id=d["assignment_id"],
        role_id=d["role_id"],
        role_name=d["role_name"],
        role_code=d["role_code"],
        status=d["status"],
        granted_at=d["granted_at"],
        org_node_id=d["org_node_id"],
        org_node_name=d["org_node_name"],
    )


def _list_item_from_row(row: PlatformUserListRow) -> PlatformUserListItem:
    """Map a PlatformUserListRow (PlatformUser + roles) ->
    PlatformUserListItem."""
    u = row.user
    roles = [_role_item_from_dict(r) for r in row.roles]
    return PlatformUserListItem(
        id=u.id,
        email=u.email,
        full_name=u.full_name,
        status=u.status,
        invited_at=u.invited_at,
        invitation_accepted_at=u.invitation_accepted_at,
        suspended_at=u.suspended_at,
        created_at=u.created_at,
        updated_at=u.updated_at,
        roles=roles,
    )


def _detail_from_row(row: PlatformUserDetailRow) -> PlatformUserRead:
    """Map a PlatformUserDetailRow -> PlatformUserRead."""
    u = row.user
    roles = [_role_item_from_dict(r) for r in row.roles]
    return PlatformUserRead(
        id=u.id,
        email=u.email,
        full_name=u.full_name,
        status=u.status,
        invited_at=u.invited_at,
        invitation_accepted_at=u.invitation_accepted_at,
        suspended_at=u.suspended_at,
        created_at=u.created_at,
        updated_at=u.updated_at,
        roles=roles,
    )


# ---- Endpoints --------------------------------------------------------------


@router.get(
    "",
    response_model=PlatformUserListResponse,
    summary="List platform users",
    description=(
        "List Ithina staff users. Gated on ADMIN.USERS.VIEW.GLOBAL — "
        "TENANT JWTs and PLATFORM users lacking the grant receive 403 "
        "PERMISSION_DENIED. Supports filter by "
        "status, case-insensitive search across email/full_name, sort, "
        "and offset/limit pagination. Each item carries an inline "
        "`roles[]` array (Step 6.8.3 augmentation); platform users' "
        "assignments have no org-node anchor so `org_node_id` / "
        "`org_node_name` are always null."
    ),
)
async def list_platform_users(
    status_filter: PlatformUserStatus | None = Query(
        None,
        alias="status",
        description="Filter by status: INVITED, ACTIVE, or SUSPENDED.",
    ),
    search: str | None = Query(
        None,
        description=(
            "Case-insensitive substring match across email and full_name."
        ),
    ),
    sort: str = Query(
        "created_at_desc",
        description=(
            "Sort key: created_at_asc, created_at_desc, full_name_asc, "
            "full_name_desc, email_asc, email_desc."
        ),
    ),
    offset: int = Query(0, ge=0, description="Pagination offset."),
    limit: int = Query(
        50, ge=1, le=200, description="Pagination limit (max 200)."
    ),
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.USERS,
        PermissionAction.VIEW,
        PermissionScope.GLOBAL,
    )),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    if search is not None:
        trimmed = search.strip()
        search = trimmed if trimmed else None

    try:
        rows, total = await _repo.list(
            session,
            status=status_filter,
            search=search,
            sort=sort,
            offset=offset,
            limit=limit,
        )
    except InvalidSortKeyError as exc:
        raise InvalidSortKeyClientError(
            str(exc), sort=sort
        ) from exc

    return PlatformUserListResponse(
        items=[_list_item_from_row(r) for r in rows],
        pagination=Pagination(total=total, offset=offset, limit=limit),
    )


@router.get(
    "/{user_id}",
    response_model=PlatformUserRead,
    summary="Get platform user by ID",
    description=(
        "Get a single platform user by UUID. Gated on "
        "ADMIN.USERS.VIEW.GLOBAL — TENANT JWTs and PLATFORM users "
        "lacking the grant receive 403 PERMISSION_DENIED. Unknown "
        "user_id returns 404 PLATFORM_USER_NOT_FOUND. Includes inline "
        "`roles[]` array (Step 6.8.3 augmentation)."
    ),
)
async def get_platform_user(
    user_id: UUID,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.USERS,
        PermissionAction.VIEW,
        PermissionScope.GLOBAL,
    )),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    row = await _repo.get_by_id(session, user_id)
    if row is None:
        raise PlatformUserNotFoundError(
            f"Platform user {user_id} not found",
            user_id=str(user_id),
        )
    return _detail_from_row(row)
