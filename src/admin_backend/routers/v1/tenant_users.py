"""Tenant users router (Step 5.2; augmented at Step 6.8.3).

Two GET endpoints under ``/tenant-users``:

  - ``GET /api/v1/tenant-users``           — list (filter / search /
                                               sort / pagination)
  - ``GET /api/v1/tenant-users/{user_id}`` — detail

Auth posture (multi-user-type — see CLAUDE.md "v0 auth model" note).
Both endpoints gate on ``ADMIN.USERS.VIEW.TENANT`` (Step 6.9.3.2
retrofit). PLATFORM JWTs with SUPER_ADMIN-or-similar grants pass via
GLOBAL→TENANT cascade; TENANT JWTs with OWNER pass directly. Visibility
scoping below the gate is the DB layer's job via RLS:

  - PLATFORM JWT: sees all ``tenant_users`` across all tenants per
    D-29's unconditional OR-branch on
    ``tenant_users_tenant_isolation``.
  - TENANT JWT: RLS scopes to the matching ``app.tenant_id`` rows.

Cross-tenant access by TENANT users surfaces as 404 (RLS-as-404 per
D-17), not 403, to avoid information disclosure about whether a
user_id exists in another tenant. Test T9 in
``test_tenant_users_router.py`` is the load-bearing assertion that
this works end-to-end through middleware -> session -> Repo -> router.

Optional ``?tenant_id=X`` query param: an application-layer narrowing
for PLATFORM callers who want to scope a list view to a single
tenant (e.g., the admin console showing tenant detail with its
users). For TENANT JWTs the filter is functionally redundant
(RLS already scopes); a non-matching value just intersects to empty
rather than disclosing other-tenant rows.

Per D-30: list returns ``{items, pagination}``; detail returns the
resource directly. Per D-31: response field semantics frozen
append-only.

Sort key validation: an unknown ``sort`` raises ``InvalidSortKeyError``
in the Repo (a ValueError subclass shared with PlatformUsersRepo via
``repositories._errors``). The handler catches it and re-raises as
``InvalidSortKeyClientError`` from ``admin_backend.errors`` so the
response surfaces as 400 ``INVALID_SORT_KEY`` instead of 500.

Step 6.8.3 — A1 augmentation: each response item now carries an
inline ``roles: list[UserRoleAssignmentItem]`` field. The Repo's
correlated jsonb_agg subquery returns a list[dict] per row; the
hand-written mapper ``_list_item_from_row`` constructs the typed
``UserRoleAssignmentItem`` instances explicitly, mirroring
``routers/v1/tenants.py:_list_item_from_row``'s ``Module`` mapper
exactly.
"""
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.anchor_deps import get_tenant_user_anchor
from admin_backend.auth.context import AuthContext
from admin_backend.auth.permissions import require
from admin_backend.auth.provisioning import provision_auth0_user
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.errors import (
    DuplicateRoleAssignmentInRequestError,
    EmptyPatchError,
    InvalidSortKeyClientError,
    InvalidStateTransitionError,
    SelfEditForbiddenError,
    TenantNotFoundError,
    TenantUserNotFoundError,
)
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.models.tenant_user import ActorUserType, TenantUserStatus
from admin_backend.repositories._errors import InvalidSortKeyError
from admin_backend.repositories.tenant_users import (
    TenantUserDetailRow,
    TenantUserListRow,
    TenantUsersRepo,
)
from admin_backend.repositories.tenants import TransitionResult
from admin_backend.schemas.tenant import Pagination
from admin_backend.schemas.tenant_user import (
    RoleAssignmentItem,
    TenantUserCreateRequest,
    TenantUserListItem,
    TenantUserListResponse,
    TenantUserPatchRequest,
    TenantUserRead,
    UserRoleAssignmentItem,
)


router = APIRouter(prefix="/tenant-users", tags=["tenant-users"])

# Stateless instance reused across requests (mirrors PlatformUsersRepo).
_repo = TenantUsersRepo()


# TenantUserNotFoundError moved to admin_backend.errors at Step 6.9.3.2 so
# anchor deps in auth/anchor_deps.py can raise it without backward layering
# violation (auth/ -> routers/v1/). Per-router import kept above for raise
# sites; behavior identical to pre-move (RLS-as-404 per D-17).


# ---- Mappers ---------------------------------------------------------------


def _role_item_from_dict(d: dict[str, Any]) -> UserRoleAssignmentItem:
    """Map a single jsonb-decoded role dict to ``UserRoleAssignmentItem``.

    Pydantic v2 auto-coerces UUID-format strings into ``UUID`` and
    ISO-8601 strings into ``datetime``; the str-Enum coerces
    ``"ACTIVE"`` / ``"INACTIVE"`` into ``UserRoleAssignmentStatus``.
    Explicit construction (rather than ``model_validate``) keeps the
    boundary between the Repo's raw JSONB output and the typed
    response schema obvious — same posture as
    ``routers/v1/tenants.py:_list_item_from_row``'s ``Module`` mapper.
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


def _list_item_from_row(row: TenantUserListRow) -> TenantUserListItem:
    """Map a TenantUserListRow (TenantUser + roles) -> TenantUserListItem.

    ``roles`` arrives on the row as ``list[dict]`` from the Repo's
    correlated jsonb_agg subquery; the COALESCE in the subquery
    guarantees the list is empty rather than None when no
    assignments exist.
    """
    u = row.user
    roles = [_role_item_from_dict(r) for r in row.roles]
    return TenantUserListItem(
        id=u.id,
        tenant_id=u.tenant_id,
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


def _detail_from_row(row: TenantUserDetailRow) -> TenantUserRead:
    """Map a TenantUserDetailRow -> TenantUserRead. Same shape as list
    item at v0; mirrored helper kept for symmetry with tenants router."""
    u = row.user
    roles = [_role_item_from_dict(r) for r in row.roles]
    return TenantUserRead(
        id=u.id,
        tenant_id=u.tenant_id,
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
    response_model=TenantUserListResponse,
    summary="List tenant users",
    description=(
        "List customer-side users. PLATFORM JWTs see all tenant_users "
        "across all tenants; TENANT JWTs see only their own tenant's "
        "users (RLS-scoped). Optional `tenant_id` filter narrows a "
        "PLATFORM view to a single tenant. Supports filter by status, "
        "case-insensitive search across email/full_name, sort, and "
        "offset/limit pagination. Each item carries an inline `roles[]` "
        "array of role assignments (Step 6.8.3 augmentation)."
    ),
)
async def list_tenant_users(
    tenant_id: UUID | None = Query(
        None,
        description=(
            "Filter to a single tenant. Useful for PLATFORM callers "
            "scoping a list view; TENANT callers see only their own "
            "tenant regardless (RLS handles it)."
        ),
    ),
    status_filter: TenantUserStatus | None = Query(
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
        PermissionScope.TENANT,
    )),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    if search is not None:
        trimmed = search.strip()
        search = trimmed if trimmed else None

    try:
        rows, total = await _repo.list(
            session,
            tenant_id=tenant_id,
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

    return TenantUserListResponse(
        items=[_list_item_from_row(r) for r in rows],
        pagination=Pagination(total=total, offset=offset, limit=limit),
    )


@router.get(
    "/{user_id}",
    response_model=TenantUserRead,
    summary="Get tenant user by ID",
    description=(
        "Get a single tenant user by UUID. PLATFORM JWTs can read any "
        "tenant_user. TENANT JWTs can only read their own tenant's "
        "users — requesting another tenant's user_id returns 404 "
        "(RLS-as-404 per D-17, not 403, to avoid disclosing that the "
        "user_id exists in another tenant). Includes inline `roles[]` "
        "array (Step 6.8.3 augmentation)."
    ),
)
async def get_tenant_user(
    user_id: UUID,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.USERS,
        PermissionAction.VIEW,
        PermissionScope.TENANT,
        anchor_dep=get_tenant_user_anchor,
    )),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    row = await _repo.get_by_id(session, user_id)
    if row is None:
        raise TenantUserNotFoundError(
            f"Tenant user {user_id} not visible to this session",
            user_id=str(user_id),
        )
    return _detail_from_row(row)


# ============================================================================
# Step 6.10.1 write endpoints: POST / PATCH / suspend / activate.
#
# Multi-audience (audience=None on every require() call); both PLATFORM
# and TENANT JWTs pass Layer 1. Gate tuple is ADMIN.USERS.CONFIGURE.TENANT
# (held by SUPER_ADMIN + PLATFORM_ADMIN + OWNER per the seed catalogue).
#
# Self-edit guard for the 3 path-bound endpoints (PATCH / suspend /
# activate): TENANT-audience callers cannot operate on themselves. The
# guard runs handler-side AFTER the Layer 2 has_permission check
# (Depends resolution graph) but BEFORE the repo call; PLATFORM
# callers are skipped (no self-edit case — PLATFORM users live in a
# different table).
#
# POST has no path user_id (the body carries tenant_id), so the
# self-create scenario isn't expressible there.
# ============================================================================


def _actor_type_from_auth(auth: AuthContext) -> ActorUserType:
    """Map ``AuthContext.user_type`` (Literal) to the typed enum used
    by the Repo and the DDL ``actor_user_type_enum``.

    The Literal vs enum split is intentional: AuthContext keeps the
    minimal Literal shape (D-24), and Pattern (b) audit-actor columns
    use the typed enum.
    """
    return (
        ActorUserType.PLATFORM
        if auth.user_type == "PLATFORM"
        else ActorUserType.TENANT
    )


def _flatten_role_assignments(
    items: list[RoleAssignmentItem],
) -> list[tuple[UUID, UUID]]:
    """Convert Pydantic ``RoleAssignmentItem`` list to repo tuples
    AND raise ``DuplicateRoleAssignmentInRequestError`` (422) on any
    within-request ``(role_id, org_node_id)`` duplicate.

    Step 6.14 LD5: handler-side pre-check ahead of the repo so the
    duplicate-detection response envelope is uniform with the rest
    of the AdminBackendError family. Pydantic's ``extra="forbid"``
    on ``RoleAssignmentItem`` and per-item shape are already
    enforced when this function runs; this layer catches semantic
    duplicates that Pydantic doesn't.

    Q7 posture: structured ``duplicate_pairs`` (list of
    ``{role_id, org_node_id}`` dicts) lives in ``exc.context``;
    response envelope ``details`` stays ``null``.
    """
    tuples: list[tuple[UUID, UUID]] = []
    seen: set[tuple[UUID, UUID]] = set()
    duplicates: list[tuple[UUID, UUID]] = []
    for it in items:
        key = (it.role_id, it.org_node_id)
        if key in seen:
            duplicates.append(key)
        else:
            seen.add(key)
            tuples.append(key)
    if duplicates:
        raise DuplicateRoleAssignmentInRequestError(
            f"duplicate (role_id, org_node_id) in roles[]: {duplicates!r}",
            duplicate_pairs=[
                {"role_id": str(rid), "org_node_id": str(oid)}
                for (rid, oid) in duplicates
            ],
        )
    return tuples


def _raise_if_self_edit(auth: AuthContext, user_id: UUID) -> None:
    """Handler-side self-edit guard for TENANT-audience callers.

    PLATFORM users can never self-edit a ``tenant_users`` row by
    construction (they live in ``platform_users``); the guard fires
    for TENANT callers only. Runs after Layer 2 has_permission (which
    has already authorised the caller for CONFIGURE.TENANT) and
    before the repo call.
    """
    if auth.user_type == "TENANT" and auth.user_id == user_id:
        raise SelfEditForbiddenError(
            f"TENANT user {auth.user_id} attempted self-edit on "
            f"tenant_user {user_id}",
            user_id=str(user_id),
        )


@router.post(
    "",
    response_model=TenantUserRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant_user(
    body: TenantUserCreateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.USERS,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        # No audience kwarg — multi-audience.
        # No anchor_dep — tenant_id is in the body, not the path.
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Create a tenant user in INVITED state with bundled role
    assignments. Multi-audience.

    Server-forces ``status=INVITED``; ``auth0_sub`` and
    ``invitation_accepted_at`` stay NULL until the Stage 3 Auth0
    invite-accept callback. Bundled TENANT-audience role assignments
    anchor at the tenant root org_node (locked decision 4); the
    handler-side validator pre-checks audience + existence for a clean
    422 ahead of the DB trigger reject.

    Gate: ``ADMIN.USERS.CONFIGURE.TENANT`` (SUPER_ADMIN +
    PLATFORM_ADMIN + OWNER per the seed). Multi-audience: PLATFORM
    callers pass via GLOBAL->TENANT cascade; TENANT OWNER passes
    directly. TENANT-side tenant-isolation enforced by RLS — a TENANT
    JWT with a body referencing another tenant_id finds the tenant
    root invisible and surfaces as 404 ``TENANT_NOT_FOUND``.

    Errors:
      - 422 ``INVALID_ROLE`` when a role_id is missing from the
        catalogue.
      - 422 ``INVALID_ROLE_AUDIENCE`` when a role exists but is not
        TENANT audience.
      - 409 ``DUPLICATE_TENANT_USER_EMAIL`` when the email already
        exists within ``tenant_id``.
      - 404 ``TENANT_NOT_FOUND`` when the target tenant is missing or
        RLS-filtered.
    """
    role_assignments = _flatten_role_assignments(body.roles)
    row = await _repo.create(
        session,
        tenant_id=body.tenant_id,
        email=body.email,
        full_name=body.full_name,
        role_assignments=role_assignments,
        actor_user_id=auth.user_id,
        actor_user_type=_actor_type_from_auth(auth),
        auth=auth,
        request_id=request.state.request_id,
    )
    if row is None:
        raise TenantNotFoundError(
            f"Tenant {body.tenant_id} not visible to this session",
            tenant_id=str(body.tenant_id),
        )

    # Post-commit Auth0 provisioning (Step CI-4b). Extract DETACHED PRIMITIVES from the
    # ORM row HERE, while the request session is still open, and pass those locals to the
    # background task. The session commits in dependency teardown and is gone before the
    # task runs, so the task must never hold a session-bound ORM object or trigger a lazy
    # load. (id/tenant_id/email are already-loaded column attributes; this is belt-and-
    # suspenders independent of expire_on_commit.) The task is fail-safe: an Auth0 failure
    # leaves the committed user INVITED / re-provisionable and never affects this response.
    new_user_id: UUID = row.user.id
    new_tenant_id: UUID = row.user.tenant_id
    new_email: str = row.user.email
    background_tasks.add_task(
        provision_auth0_user,
        request.app.state.auth0_management_client,
        user_id=new_user_id,
        tenant_id=new_tenant_id,
        email=new_email,
        user_type="TENANT",
        request_id=request.state.request_id,
    )

    return _detail_from_row(row)


@router.patch(
    "/{user_id}",
    response_model=TenantUserRead,
)
async def patch_tenant_user(
    user_id: UUID,
    body: TenantUserPatchRequest,
    request: Request,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.USERS,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        anchor_dep=get_tenant_user_anchor,
        # No audience kwarg — multi-audience.
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Partial update of a tenant user. Multi-audience.

    Editable fields: ``full_name``, ``email``, ``roles`` (replace-set).
    Other fields rejected at the schema layer via ``extra="forbid"``.

    Behaviour:
      - 422 ``EMPTY_PATCH`` when no fields set.
      - 422 ``INVALID_ROLE`` / ``INVALID_ROLE_AUDIENCE`` on bad
        ``roles`` content.
      - 403 ``SELF_EDIT_FORBIDDEN`` when a TENANT caller targets their
        own user_id.
      - 409 ``DUPLICATE_TENANT_USER_EMAIL`` on rename collision in
        the same tenant.
      - 404 ``TENANT_USER_NOT_FOUND`` when the row is missing or
        RLS-filtered.
      - Allowed in any state (INVITED, ACTIVE, SUSPENDED).
    """
    _raise_if_self_edit(auth, user_id)

    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise EmptyPatchError(
            f"PATCH on tenant_user {user_id} had no set fields",
            user_id=str(user_id),
        )

    # Convert Pydantic RoleAssignmentItem list to (role_id, org_node_id)
    # tuples AND raise 422 on within-request duplicates (Step 6.14 LD5).
    # An empty list is a valid PATCH value (revoke-all); ``roles`` set
    # to None means the field was omitted and the converter is skipped.
    if "roles" in fields and body.roles is not None:
        fields["roles"] = _flatten_role_assignments(body.roles)

    row = await _repo.update(
        session,
        user_id,
        fields=fields,
        actor_user_id=auth.user_id,
        actor_user_type=_actor_type_from_auth(auth),
        auth=auth,
        request_id=request.state.request_id,
    )
    if row is None:
        raise TenantUserNotFoundError(
            f"Tenant user {user_id} not visible to this session",
            user_id=str(user_id),
        )
    return _detail_from_row(row)


@router.post(
    "/{user_id}/suspend",
    response_model=TenantUserRead,
)
async def suspend_tenant_user(
    user_id: UUID,
    request: Request,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.USERS,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        anchor_dep=get_tenant_user_anchor,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Suspend a tenant user. Multi-audience.

    Allowed source: ACTIVE only.
      - INVITED -> SUSPENDED is structurally rejected by
        ``ck_tenant_users_auth0_sub_consistency`` (SUSPENDED requires
        ``auth0_sub`` non-NULL; INVITED requires NULL). The app
        layer maps the rejection to 409 ``INVALID_STATE_TRANSITION``
        so the caller never sees a 500.
      - SUSPENDED -> SUSPENDED returns 409 ``INVALID_STATE_TRANSITION``.

    Self-suspend forbidden for TENANT callers (uniform self-edit
    guard).
    """
    _raise_if_self_edit(auth, user_id)

    row, result = await _repo.transition(
        session,
        user_id,
        target_status="SUSPENDED",
        actor_user_id=auth.user_id,
        actor_user_type=_actor_type_from_auth(auth),
        auth=auth,
        request_id=request.state.request_id,
    )
    if result is TransitionResult.NOT_FOUND:
        raise TenantUserNotFoundError(
            f"Tenant user {user_id} not visible to this session",
            user_id=str(user_id),
        )
    if result is TransitionResult.INVALID_STATE:
        raise InvalidStateTransitionError(
            (
                f"tenant_user {user_id} cannot transition to SUSPENDED "
                "from its current status"
            ),
            user_id=str(user_id),
            target_status="SUSPENDED",
        )
    assert row is not None  # TransitionResult.OK guarantees a row
    return _detail_from_row(row)


@router.post(
    "/{user_id}/activate",
    response_model=TenantUserRead,
)
async def activate_tenant_user(
    user_id: UUID,
    request: Request,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.USERS,
        PermissionAction.CONFIGURE,
        PermissionScope.TENANT,
        anchor_dep=get_tenant_user_anchor,
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """Activate a tenant user. Multi-audience.

    Allowed source: SUSPENDED only.
      - INVITED -> ACTIVE is the Auth0 invite-accept callback flow
        (out of scope for v0; Stage 3). Surfaces here as 409
        ``INVALID_STATE_TRANSITION``.
      - ACTIVE -> ACTIVE returns 409 ``INVALID_STATE_TRANSITION``.

    SUSPENDED -> ACTIVE clears ``suspended_at``,
    ``suspended_by_user_id``, and ``suspended_by_user_type`` atomically
    with the ``status`` flip (required by
    ``ck_tenant_users_suspended_consistency``).

    Self-activate forbidden for TENANT callers (functional
    impossibility — a suspended user has no session — but the guard
    fires uniformly across the 3 path-bound endpoints).
    """
    _raise_if_self_edit(auth, user_id)

    row, result = await _repo.transition(
        session,
        user_id,
        target_status="ACTIVE",
        actor_user_id=auth.user_id,
        actor_user_type=_actor_type_from_auth(auth),
        auth=auth,
        request_id=request.state.request_id,
    )
    if result is TransitionResult.NOT_FOUND:
        raise TenantUserNotFoundError(
            f"Tenant user {user_id} not visible to this session",
            user_id=str(user_id),
        )
    if result is TransitionResult.INVALID_STATE:
        raise InvalidStateTransitionError(
            (
                f"tenant_user {user_id} cannot transition to ACTIVE "
                "from its current status"
            ),
            user_id=str(user_id),
            target_status="ACTIVE",
        )
    assert row is not None
    return _detail_from_row(row)
