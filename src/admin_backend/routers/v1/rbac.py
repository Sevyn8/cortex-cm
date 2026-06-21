"""RBAC read endpoints (Step 6.1).

Four GET endpoints across three URL prefixes, all accepting both
PLATFORM and TENANT JWTs (multi-user-type per the v0 auth model):

  - ``GET /api/v1/roles``                   E1 — pre-grouped catalog
  - ``GET /api/v1/roles/{role_id}/permissions``
                                            E3 — sub-resource w/ parent echo
  - ``GET /api/v1/permissions``             E2 — flat catalogue
  - ``GET /api/v1/permission-matrix``       E6 — render-ready grid

Audience filtering. ``roles``, ``permissions``, ``role_permissions``
are platform-global tables with NO RLS (per the DDL's "platform-global,
no RLS" notes). Visibility is enforced AT THE APPLICATION LAYER via
the ``audience`` column. TENANT JWTs see only ``audience='TENANT'``
rows on E1 (the platform_roles block returns ``{items: [], total: 0}``),
E3 (cross-audience id surfaces as 404 ROLE_NOT_FOUND, like RLS-as-404
but app-layer-driven), and E6 (only TENANT-audience role columns
appear, ``cells[]`` arrays correspondingly shorter). PLATFORM JWTs see
all rows. Distinct from RLS (DB-layer); same intent.

E1, E3, E6 are deliberate D-30 exceptions:
  - E1 is pre-grouped (``platform_roles`` + ``tenant_roles`` blocks);
    the pre-grouped shape doesn't compose with cross-group pagination.
  - E3 echoes the parent role identity at top level (``role_id``,
    ``role_name``); a single-resource sub-resource has nowhere
    pagination would belong.
  - E6 is render-ready (``roles`` column array + ``rows`` with
    position-aligned ``cells[]``); the matrix is one shape, returned
    in full.

E2 follows D-30 normally (``{items, pagination}``).

Permission catalogue (E2) is reference data: both user types see the
full catalogue regardless of audience.

Per D-17, audience-gated misses surface as 404, not 403, to avoid
disclosing whether a role id exists in another audience. The router's
``RoleNotFoundError`` handles this.

Sort key validation: an unknown ``sort`` raises ``InvalidSortKeyError``
in the Repo (a ValueError subclass shared with platform_users /
tenant_users via ``repositories._errors``). The handler catches it and
re-raises as ``InvalidSortKeyClientError`` from
``admin_backend.errors`` so the response surfaces as 400
``INVALID_SORT_KEY`` instead of 500.
"""
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.context import AuthContext
from admin_backend.auth.permissions import require
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.errors import ClientError, InvalidSortKeyClientError
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.models.tenant_user import ActorUserType
from admin_backend.repositories._errors import InvalidSortKeyError
from admin_backend.repositories.permission_matrix import PermissionMatrixRepo
from admin_backend.repositories.permissions import PermissionsRepo
from admin_backend.repositories.roles import RolesRepo
from admin_backend.schemas.permission import (
    PermissionListResponse,
    PermissionMatrixResponse,
    PermissionMatrixRoleColumn,
    PermissionMatrixRow,
    PermissionRead,
    RolePermissionsResponse,
)
from admin_backend.schemas.role import (
    AudienceBlock,
    RoleDetail,
    RoleListItem,
    RoleListResponse,
    RoleUpdateRequest,
)
from admin_backend.schemas.tenant import Pagination


# Three sibling routers under three URL prefixes. Wired separately by
# main.py via ``app.include_router(rbac.X_router, prefix=api_prefix)``.
roles_router = APIRouter(prefix="/roles", tags=["rbac"])
permissions_router = APIRouter(prefix="/permissions", tags=["rbac"])
matrix_router = APIRouter(prefix="/permission-matrix", tags=["rbac"])


# Stateless instances reused across requests (mirrors TenantsRepo / etc.).
_roles_repo = RolesRepo()
_permissions_repo = PermissionsRepo()
_matrix_repo = PermissionMatrixRepo()


# ---- Errors specific to this router ----------------------------------------


class RoleNotFoundError(ClientError):
    """Raised when a role lookup by id finds nothing.

    Per D-17 / Step 6.1's audience-filter convention this fires for
    both genuinely missing rows AND rows filtered out by the
    audience-gate (e.g., a TENANT JWT requesting a PLATFORM-audience
    role's id). Distinguishing the two would leak that the role
    exists in the other audience.
    """

    public_message = "Role not found"
    http_status = 404
    code = "ROLE_NOT_FOUND"


def _audience_filter_for(auth: AuthContext) -> str | None:
    """TENANT JWTs see only audience='TENANT'; PLATFORM sees both.

    The filter value flows from the JWT's ``user_type`` claim only;
    no other source. Mirrors AI-MT-03's source-binding discipline,
    one layer up.
    """
    return "TENANT" if auth.user_type == "TENANT" else None


def _actor_type_from_auth(auth: AuthContext) -> ActorUserType:
    """Map ``AuthContext.user_type`` (Literal) to ``ActorUserType``
    (typed enum) for Pattern (b) audit-actor writes (Step 6.18.3).

    Mirrors ``routers/v1/stores.py::_actor_type_from_auth`` and
    ``routers/v1/tenant_users.py::_actor_type_from_auth`` exactly.
    Third local copy in the codebase; promotion to a shared module is
    tracked as an FN-AB for future cleanup. Repo-local instead of a
    shared import keeps ``routers/v1/*`` decoupled from each other in
    the interim.
    """
    return (
        ActorUserType.PLATFORM
        if auth.user_type == "PLATFORM"
        else ActorUserType.TENANT
    )


# ---- E1: GET /api/v1/roles --------------------------------------------------


@roles_router.get(
    "",
    response_model=RoleListResponse,
    summary="List roles, pre-grouped by audience",
    description=(
        "Returns the role catalogue pre-grouped into platform_roles and "
        "tenant_roles blocks (deliberate D-30 exception). PLATFORM JWTs "
        "see both blocks populated; TENANT JWTs see platform_roles "
        "always {items: [], total: 0} (audience filter applied at the "
        "app layer — these tables have no RLS). Each item carries a "
        "user_count field counting active assignments referencing the "
        "role (RLS-scoped for TENANT JWTs to the calling tenant)."
    ),
)
async def list_roles(
    status_filter: str | None = Query(
        None,
        alias="status",
        description=(
            "Filter by status: ACTIVE (default), INACTIVE, or ARCHIVED."
        ),
    ),
    is_system: bool | None = Query(
        None,
        description=(
            "Filter by Ithina-system flag (true=system roles like "
            "SUPER_ADMIN that should not be deleted)."
        ),
    ),
    q: str | None = Query(
        None,
        description=(
            "Case-insensitive substring match across name, code, and "
            "description."
        ),
    ),
    sort: str = Query(
        "name_asc",
        description=(
            "Sort key: name_asc (default), name_desc, created_at_asc, "
            "created_at_desc. Sort applies within each block."
        ),
    ),
    offset: int = Query(0, ge=0, description="Pagination offset."),
    limit: int = Query(
        50,
        ge=1,
        le=200,
        description=(
            "Pagination limit. Present for consistency; v0 catalogue "
            "fits in one page."
        ),
    ),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    if q is not None:
        trimmed = q.strip()
        q = trimmed if trimmed else None

    try:
        grouped = await _roles_repo.list_grouped(
            session,
            audience_filter=_audience_filter_for(auth),
            status=status_filter,
            is_system=is_system,
            q=q,
            sort=sort,
            offset=offset,
            limit=limit,
        )
    except InvalidSortKeyError as exc:
        raise InvalidSortKeyClientError(
            str(exc), sort=sort
        ) from exc

    def _to_block(
        bucket: tuple[list[tuple[Any, int]], int]
    ) -> AudienceBlock:
        rows, total = bucket
        items = [
            RoleListItem.model_validate(
                {
                    "id": role.id,
                    "name": role.name,
                    "code": role.code,
                    "description": role.description,
                    "status": role.status,
                    "is_system": role.is_system,
                    "user_count": user_count,
                    "created_at": role.created_at,
                    "updated_at": role.updated_at,
                }
            )
            for role, user_count in rows
        ]
        return AudienceBlock(items=items, total=total)

    return RoleListResponse(
        platform_roles=_to_block(grouped["PLATFORM"]),
        tenant_roles=_to_block(grouped["TENANT"]),
    )


# ---- E3: GET /api/v1/roles/{role_id}/permissions ---------------------------


@roles_router.get(
    "/{role_id}/permissions",
    response_model=RolePermissionsResponse,
    summary="List permissions granted by a role",
    description=(
        "Returns the role's granted permissions plus a parent-echo "
        "envelope (role_id + role_name at top level, items array "
        "below). No pagination — a role has bounded permissions "
        "(typically 5-30). TENANT JWTs requesting a PLATFORM-audience "
        "role's id receive 404 ROLE_NOT_FOUND (audience filter applied "
        "at the app layer; same anti-information-disclosure intent as "
        "RLS-as-404 per D-17)."
    ),
)
async def list_role_permissions(
    role_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    role_or_none = await _roles_repo.get_by_id(
        session, role_id, audience_filter=_audience_filter_for(auth)
    )
    if role_or_none is None:
        raise RoleNotFoundError(
            f"Role {role_id} not found or not visible to this audience",
            role_id=str(role_id),
            user_type=auth.user_type,
        )
    role, _user_count = role_or_none

    permissions = await _roles_repo.list_permissions_for_role(
        session, role_id
    )
    return RolePermissionsResponse(
        role_id=role.id,
        role_name=role.name,
        items=[PermissionRead.model_validate(p) for p in permissions],
    )


# ---- E7: GET /api/v1/roles/{role_id} ---------------------------------------
#
# Declared AFTER E3 (``/{role_id}/permissions``) per FastAPI convention
# (more-specific routes declared before more-general). Path-parameters
# don't match ``/`` by default, so routing works either way, but the
# convention is defensive.


@roles_router.get(
    "/{role_id}",
    response_model=RoleDetail,
    summary="Get role detail with held + available permissions",
    description=(
        "Returns role metadata plus held permissions and grantable "
        "permissions (catalogue minus held) for the role-edit screen. "
        "Both permission lists carry display labels resolved server-"
        "side via JOIN against the lookups table. TENANT-audience "
        "roles see ``available_permissions`` with ``scope='GLOBAL'`` "
        "rows excluded (audience-scope coherence per LD2). TENANT "
        "JWTs requesting a PLATFORM-audience role's id receive 404 "
        "ROLE_NOT_FOUND (audience filter applied at the app layer; "
        "same anti-information-disclosure intent as RLS-as-404 per "
        "D-17). No pagination — the v0 catalogue (~36 permissions) "
        "fits in one response."
    ),
)
async def get_role(
    role_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    detail = await _roles_repo.get_detail_by_id(
        session, role_id, audience_filter=_audience_filter_for(auth)
    )
    if detail is None:
        raise RoleNotFoundError(
            f"Role {role_id} not found or not visible to this audience",
            role_id=str(role_id),
            user_type=auth.user_type,
        )
    return RoleDetail.model_validate(detail)


# ---- E8: PATCH /api/v1/roles/{role_id} (Step 6.18.3) -----------------------
#
# Role-edit write endpoint. Gated by ADMIN.ROLES.OVERRIDE.GLOBAL plus
# audience="PLATFORM" (defense-in-depth against catalogue drift; the
# gate-discipline meta-test enumerates PLATFORM-only writes in
# ``_PLATFORM_ONLY_WRITE_ROUTES`` and asserts the marker carries
# ``audience='PLATFORM'``).
#
# Two-layer OVERRIDE.GLOBAL invariant lives in the repo (LD6):
#   - Layer 1: pre-write check. 409 LAST_OVERRIDE_HOLDER if the edit
#     would zero out the platform-wide active holder count.
#   - Layer 2: post-write tripwire. 500 INTERNAL_ERROR + ROLLBACK if
#     Layer 1's logic is buggy.


@roles_router.patch(
    "/{role_id}",
    response_model=RoleDetail,
    summary="Edit a role's name, description, and/or permission set",
    description=(
        "Partial update of a role. Editable fields: name, description, "
        "permission_ids (replace-set; diff-replace preserves "
        "created_at on unchanged role_permissions rows per LD5). "
        "audience, code, is_system, status, and audit columns are "
        "rejected at the schema layer via extra='forbid'. Gated by "
        "ADMIN.ROLES.OVERRIDE.GLOBAL (held by SUPER_ADMIN only post "
        "Step 6.18.1 seed; PLATFORM-only by gate-tuple construction). "
        "SUPER_ADMIN role itself is uneditable via API in v0 (409 "
        "SUPER_ADMIN_PROTECTED); operator workflow is direct SQL on "
        "core.roles / core.role_permissions. Two-layer OVERRIDE.GLOBAL "
        "invariant guards against zeroing out the platform-admin "
        "bootstrap."
    ),
)
async def patch_role(
    role_id: UUID,
    body: RoleUpdateRequest,
    request: Request,
    _: None = Depends(require(
        ModuleCode.ADMIN,
        PermissionResource.ROLES,
        PermissionAction.OVERRIDE,
        PermissionScope.GLOBAL,
        audience="PLATFORM",
    )),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    """E8: edit a role. PLATFORM-only by gate construction.

    Errors:
      - 401 (no JWT)
      - 403 PLATFORM_AUDIENCE_REQUIRED (TENANT JWT)
      - 403 PERMISSION_DENIED (PLATFORM JWT without OVERRIDE.GLOBAL grant)
      - 404 ROLE_NOT_FOUND (role missing)
      - 409 SUPER_ADMIN_PROTECTED (target is SUPER_ADMIN; LD12)
      - 409 ROLE_ARCHIVED (target.status='ARCHIVED'; LD3)
      - 409 LAST_OVERRIDE_HOLDER (edit would zero out OVERRIDE.GLOBAL
        active holders; LD6 Layer 1)
      - 422 EMPTY_PATCH (body has no fields set)
      - 422 INVALID_PERMISSION_ID (unknown permission UUIDs)
      - 422 AUDIENCE_SCOPE_MISMATCH (TENANT role + new GLOBAL perm)
      - 422 Pydantic (extra='forbid', type, length)
      - 500 INTERNAL_ERROR (Layer 2 tripwire fired; bug indicator)
    """
    detail = await _roles_repo.update(
        session,
        role_id,
        body=body,
        actor_user_id=auth.user_id,
        actor_user_type=_actor_type_from_auth(auth),
        auth=auth,
        request_id=request.state.request_id,
    )
    if detail is None:
        raise RoleNotFoundError(
            f"Role {role_id} not found",
            role_id=str(role_id),
        )
    return RoleDetail.model_validate(detail)


# ---- E2: GET /api/v1/permissions -------------------------------------------


@permissions_router.get(
    "",
    response_model=PermissionListResponse,
    summary="List permission catalogue",
    description=(
        "Returns the canonical permission catalogue ((module, "
        "resource, action, scope) tuples). Reference data — both user "
        "types see all rows; no audience filter. Default sort clusters "
        "related permissions together (module_asc compound: "
        "module/resource/action/scope ASC), matching matrix render "
        "order."
    ),
)
async def list_permissions(
    module: str | None = Query(
        None,
        description=(
            "Filter by module: ADMIN, PRICING_OS, "
            "PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT."
        ),
    ),
    scope: str | None = Query(
        None,
        description="Filter by scope: GLOBAL, TENANT, STORE.",
    ),
    sort: str = Query(
        "module_asc",
        description=(
            "Sort key: module_asc (default; compound module/resource/"
            "action/scope ASC), code_asc, code_desc."
        ),
    ),
    offset: int = Query(0, ge=0, description="Pagination offset."),
    limit: int = Query(
        100,
        ge=1,
        le=200,
        description=(
            "Pagination limit. Present for consistency; v0 catalogue "
            "fits in one page."
        ),
    ),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    try:
        items, total = await _permissions_repo.list(
            session,
            module=module,
            scope=scope,
            sort=sort,
            offset=offset,
            limit=limit,
        )
    except InvalidSortKeyError as exc:
        raise InvalidSortKeyClientError(
            str(exc), sort=sort
        ) from exc

    return PermissionListResponse(
        items=[PermissionRead.model_validate(p) for p in items],
        pagination=Pagination(total=total, offset=offset, limit=limit),
    )


# ---- E6: GET /api/v1/permission-matrix -------------------------------------


@matrix_router.get(
    "",
    response_model=PermissionMatrixResponse,
    summary="Render-ready permission × role matrix",
    description=(
        "Returns the full role × permission grid for the Roles & "
        "Permissions matrix tab (Frontend spec 7.5.4). Cells are "
        "boolean grant flags, position-aligned with the roles[] "
        "column array (cells[i] is the grant state for roles[i]; "
        "len(cells) == len(roles) for every row). TENANT JWT response "
        "filters roles to audience='TENANT' only; cells[] arrays are "
        "correspondingly 12 elements wide instead of 15. Each row "
        "carries 4 enum codes plus 4 display labels resolved from "
        "the lookups table (module, resource, permission_action, "
        "permission_scope list_names)."
    ),
)
async def get_permission_matrix(
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    roles, permission_rows, grants = await _matrix_repo.get_matrix(
        session, audience_filter=_audience_filter_for(auth)
    )

    granted_set: set[tuple[UUID, UUID]] = set(grants)
    role_id_order = [r.id for r in roles]

    return PermissionMatrixResponse(
        roles=[
            PermissionMatrixRoleColumn.model_validate(r) for r in roles
        ],
        rows=[
            PermissionMatrixRow(
                id=row["id"],
                module=row["module"],
                module_label=row["module_label"],
                resource=row["resource"],
                resource_label=row["resource_label"],
                action=row["action"],
                action_label=row["action_label"],
                scope=row["scope"],
                scope_label=row["scope_label"],
                cells=[
                    (rid, row["id"]) in granted_set
                    for rid in role_id_order
                ],
            )
            for row in permission_rows
        ],
    )
