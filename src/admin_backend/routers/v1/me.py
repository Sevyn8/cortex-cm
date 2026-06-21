"""``/api/v1/me/*`` — caller's own permission context.

Two endpoints, both multi-user-type (PLATFORM and TENANT accepted):

- ``GET /me/permissions`` — full grant set. Frontend consumes once per
  session to gate UI. Always returns an array; empty when the caller
  has no active grants.
- ``GET /me/can-do`` — single-permission server-authoritative check.
  Pre-flight before high-stakes UI actions. Cascade-aware via
  ``target_anchor``.

These describe the caller's own state, so no ``require(...)`` gate
applies. ``PUBLIC_PATHS`` in the auth middleware still excludes
``/me/*`` — JWT is mandatory.

Step 6.9.2.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.context import AuthContext
from admin_backend.auth.permissions import (
    get_permissions_for_user,
    has_permission,
)
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.schemas.me import (
    MeCanDoResponse,
    MePermissionsResponse,
    PermissionGrantRead,
)


router = APIRouter(prefix="/me", tags=["me"])


@router.get(
    "/permissions",
    response_model=MePermissionsResponse,
    summary="Get caller's permission set",
    description=(
        "Returns the caller's full set of active permission grants. "
        "Used by the frontend at login/session-refresh to decide which "
        "UI elements to render. Always returns an array; empty if the "
        "caller has no grants. PLATFORM callers see every grant on "
        "their platform_user_role_assignments rows. TENANT callers see "
        "grants whose module is currently ENABLED on their "
        "tenant_module_access; suspended-module grants are filtered "
        "out. Server-side enforcement (the require() gate or in-handler "
        "has_permission call) is the security boundary; this endpoint "
        "is a UX hint."
    ),
)
async def get_me_permissions(
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> MePermissionsResponse:
    grants = await get_permissions_for_user(session, auth)
    return MePermissionsResponse(
        permissions=[
            PermissionGrantRead.model_validate(g) for g in grants
        ]
    )


@router.get(
    "/can-do",
    response_model=MeCanDoResponse,
    summary="Check a single permission",
    description=(
        "Server-authoritative single-permission check. Pass the "
        "(module, resource, action, scope) tuple via query parameters; "
        "optionally pass ``target_anchor`` (ltree path) for "
        "cascade-aware verification. Returns "
        "``{allowed: bool, reason_code: str}`` where ``reason_code`` "
        "is one of ``GRANT_MATCHED`` or "
        "``NO_MATCHING_GRANT_OR_OUT_OF_SCOPE`` (v0 binary; granular "
        "codes deferred to Step 6.16). Frontend uses this for "
        "pre-flight checks before high-stakes actions when "
        "cascade-aware verification matters."
    ),
)
async def get_me_can_do(
    module: Annotated[
        ModuleCode, Query(description="Permission module slot")
    ],
    resource: Annotated[
        PermissionResource, Query(description="Permission resource slot")
    ],
    action: Annotated[
        PermissionAction, Query(description="Permission action slot")
    ],
    scope: Annotated[
        PermissionScope, Query(description="Permission scope slot")
    ],
    target_anchor: Annotated[
        str | None,
        Query(
            pattern=r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$",
            max_length=1024,
            description=(
                "Optional ltree path of the target the action would "
                "apply to. Required for cascade-aware checks on TENANT "
                "grants; ignored on the PLATFORM path. Format: "
                "dot-separated labels; each label is one or more "
                "alphanumerics or underscores (Postgres ltree grammar). "
                "Example: 'tenant_root.region_us.store_dallas'. Caller "
                "sending non-ltree input (e.g., a hyphen) gets 422 "
                "before the gate runs (Step 6.20.2)."
            ),
        ),
    ] = None,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> MeCanDoResponse:
    allowed, reason_code, _ = await has_permission(
        session, auth, module, resource, action, scope, target_anchor
    )
    return MeCanDoResponse(allowed=allowed, reason_code=reason_code.value)
