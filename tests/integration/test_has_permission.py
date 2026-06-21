"""Integration tests for has_permission() (Step 6.9.1).

Real Postgres, real schema, real RLS, real ltree. No FastAPI machinery.
Each test builds its own role + assignment graph via the conftest
factories. Permission catalogue rows are reused from the seeded
catalogue via the local ``_lookup_permission_id`` helper rather than
created — ``permissions.code`` has a UNIQUE constraint and the seed
already populates the 30 canonical tuples, so a parallel
``make_permission`` call would collide.

Five tests are LOAD-BEARING (security-critical correctness):

- ``T_C1`` (cascade-correctness): grant at tenant root covers a
  descendant store. If this fails, ltree cascade is broken.
- ``T_C3`` (sibling-region denial): grant at region X must NOT cover
  region Y under the same tenant. Guards against the
  ``str.startswith`` bug class — Postgres ltree ``<@`` respects
  segment boundaries.
- ``T_M1`` (module-disabled denial): TENANT grant with
  ``tenant_module_access.status='DISABLED'`` must deny. Without this,
  disabled-module access leaks. (Prompt's catalogue names the
  scenario ``SUSPENDED``; the live
  ``module_access_status_enum`` has only ``ENABLED`` / ``DISABLED``,
  so the test uses ``DISABLED`` — same denial semantics.)
- ``T_T3`` (inactive-assignment denial): a row in
  ``tenant_user_role_assignments`` with ``status='INACTIVE'`` must
  deny. Guards against accidental status-filter regression.
- ``T_X1`` (cross-tenant injection guard): a TENANT-A user passing a
  TENANT-B path as ``target_anchor`` must be denied. End-to-end check
  on the RLS + composite-FK + ltree-disjoint defense-in-depth chain.

Fixture re-use: every test composes the existing conftest factories.
No new fixture is required.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext
from admin_backend.auth.permissions import has_permission
from admin_backend.auth.reason_code import ReasonCode
from admin_backend.config import get_settings
from admin_backend.db.session import get_tenant_session
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import (
    ModuleAccessStatus,
    ModuleCode,
)


_VALID_AUTH_BASE: dict[str, Any] = {
    "sub": "test-sub",
    "iss": "https://stub-issuer.local/",
    "aud": "https://api.test/",
    "exp": 9999999999,
    "email": "test@ithina.local",
}


def _platform_auth(user_id: uuid.UUID) -> AuthContext:
    return AuthContext(  # type: ignore[call-arg]
        **_VALID_AUTH_BASE,
        user_id=user_id,
        tenant_id=None,
        user_type="PLATFORM",
    )


def _tenant_auth(tenant_id: uuid.UUID, user_id: uuid.UUID) -> AuthContext:
    return AuthContext(  # type: ignore[call-arg]
        **_VALID_AUTH_BASE,
        user_id=user_id,
        tenant_id=tenant_id,
        user_type="TENANT",
    )


async def _lookup_permission_id(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth_ctx: AuthContext,
    *,
    module: str,
    resource: str,
    action: str,
    scope: str,
) -> uuid.UUID:
    """Return the seeded permission id for a (module, resource, action, scope).

    Raises ``LookupError`` if the tuple is not seeded — tests that need
    a novel tuple should create one via the (currently unused-by-this-
    file) ``make_permission`` fixture, but the v0 seed covers every
    tuple used here.
    """
    code = f"{module}.{resource}.{action}.{scope}"
    async for session in get_tenant_session(
        platform_auth_ctx, session_factory
    ):
        result = await session.execute(
            text(
                f"SELECT id FROM {get_settings().db_schema}.permissions WHERE code = :code"
            ),
            {"code": code},
        )
        row = result.first()
    if row is None:
        raise LookupError(
            f"permission code {code!r} not present in seed catalogue"
        )
    return uuid.UUID(str(row[0]))


# ============================================================================
# PLATFORM path
# ============================================================================


async def test_p1_platform_user_with_matching_grant_allowed(
    session_factory,
    platform_auth,
    make_platform_user,
    make_role,
    make_role_permission,
    make_platform_user_role_assignment,
):
    """T_P1: PLATFORM user holding ADMIN.USERS.VIEW.GLOBAL is allowed."""
    pu = await make_platform_user(status="ACTIVE")
    role = await make_role(audience="PLATFORM")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="ADMIN", resource="USERS", action="VIEW", scope="GLOBAL",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_platform_user_role_assignment(
        platform_user_id=pu.id, role_id=role.id, status="ACTIVE"
    )

    auth = _platform_auth(pu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, detail = await has_permission(
            session,
            auth,
            module=ModuleCode.ADMIN,
            resource=PermissionResource.USERS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.GLOBAL,
        )

    assert allowed is True
    assert code is ReasonCode.GRANT_MATCHED
    assert "(ADMIN,USERS,VIEW,GLOBAL)" in detail


async def test_p2_platform_user_without_grant_denied(
    session_factory,
    platform_auth,
    make_platform_user,
    make_role,
    make_role_permission,
    make_platform_user_role_assignment,
):
    """T_P2: PLATFORM user with a role but no row for the queried tuple."""
    pu = await make_platform_user(status="ACTIVE")
    role = await make_role(audience="PLATFORM")
    # Grant USERS.VIEW only; ask for ROLES.CONFIGURE.
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="ADMIN", resource="USERS", action="VIEW", scope="GLOBAL",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_platform_user_role_assignment(
        platform_user_id=pu.id, role_id=role.id, status="ACTIVE"
    )

    auth = _platform_auth(pu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session,
            auth,
            module=ModuleCode.ADMIN,
            resource=PermissionResource.ROLES,
            action=PermissionAction.CONFIGURE,
            scope=PermissionScope.GLOBAL,
        )

    assert allowed is False
    assert code is ReasonCode.NO_MATCHING_GRANT_OR_OUT_OF_SCOPE


async def test_p3_platform_grant_ignores_target_anchor(
    session_factory,
    platform_auth,
    make_platform_user,
    make_role,
    make_role_permission,
    make_platform_user_role_assignment,
):
    """T_P3: PLATFORM grant matches regardless of target_anchor.

    PLATFORM grants apply globally; the function accepts target_anchor
    as a no-op on this path.
    """
    pu = await make_platform_user(status="ACTIVE")
    role = await make_role(audience="PLATFORM")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="ADMIN", resource="TENANTS", action="VIEW", scope="GLOBAL",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_platform_user_role_assignment(
        platform_user_id=pu.id, role_id=role.id, status="ACTIVE"
    )

    auth = _platform_auth(pu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session,
            auth,
            module=ModuleCode.ADMIN,
            resource=PermissionResource.TENANTS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.GLOBAL,
            target_anchor="some.fictional.path",
        )

    assert allowed is True
    assert code is ReasonCode.GRANT_MATCHED


# ============================================================================
# TENANT path — tuple matching
# ============================================================================


async def test_t1_tenant_user_with_matching_grant_allowed(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_T1: matching grant, target_anchor=None → allowed."""
    tenant = await make_tenant(name="T_T1-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    on_id, _on_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="T1HQ", name="T1 HQ"
    )
    role = await make_role(audience="TENANT")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="PRICING_OS", resource="PRICING_RULES",
        action="VIEW", scope="TENANT",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=tu.id,
        org_node_id=on_id,
        role_id=role.id,
        status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.PRICING_OS,
        enabled_by_user_id=pu.id,
        created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant.id, tu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session,
            auth,
            module=ModuleCode.PRICING_OS,
            resource=PermissionResource.PRICING_RULES,
            action=PermissionAction.VIEW,
            scope=PermissionScope.TENANT,
            target_anchor=None,
        )

    assert allowed is True
    assert code is ReasonCode.GRANT_MATCHED


async def test_t2_tenant_user_no_matching_tuple_denied(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_T2: TENANT user with an unrelated grant → denied for the queried tuple."""
    tenant = await make_tenant(name="T_T2-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    on_id, _on_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="T2HQ", name="T2 HQ"
    )
    role = await make_role(audience="TENANT")
    # Grant PRICING_RULES.VIEW; ask for MARKDOWNS.APPROVE.
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="PRICING_OS", resource="PRICING_RULES",
        action="VIEW", scope="TENANT",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=tu.id,
        org_node_id=on_id,
        role_id=role.id,
        status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.PRICING_OS,
        enabled_by_user_id=pu.id,
        created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant.id, tu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session,
            auth,
            module=ModuleCode.PRICING_OS,
            resource=PermissionResource.MARKDOWNS,
            action=PermissionAction.APPROVE,
            scope=PermissionScope.STORE,
        )

    assert allowed is False
    assert code is ReasonCode.NO_MATCHING_GRANT_OR_OUT_OF_SCOPE


async def test_t3_inactive_assignment_denied(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_T3 — LOAD-BEARING: an INACTIVE assignment must not authorise."""
    tenant = await make_tenant(name="T_T3-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    on_id, _on_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="T3HQ", name="T3 HQ"
    )
    role = await make_role(audience="TENANT")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="PRICING_OS", resource="PRICING_RULES",
        action="VIEW", scope="TENANT",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=tu.id,
        org_node_id=on_id,
        role_id=role.id,
        status="INACTIVE",
        revoked_at="2026-01-01 00:00:00+00",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.PRICING_OS,
        enabled_by_user_id=pu.id,
        created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant.id, tu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session,
            auth,
            module=ModuleCode.PRICING_OS,
            resource=PermissionResource.PRICING_RULES,
            action=PermissionAction.VIEW,
            scope=PermissionScope.TENANT,
        )

    assert allowed is False
    assert code is ReasonCode.NO_MATCHING_GRANT_OR_OUT_OF_SCOPE


# ============================================================================
# TENANT path — cascade
# ============================================================================
#
# Cascade tests use ``PRICING_OS.MARKDOWNS.VIEW.STORE`` (seeded) as the
# canonical STORE-scope permission tuple. ``PRICING_RULES`` is only
# seeded at TENANT scope, so STORE-scope cascade tests pivot to
# ``MARKDOWNS``.


async def test_c1_grant_at_root_covers_descendant_store(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_C1 — LOAD-BEARING: ltree cascade from tenant root covers descendants."""
    tenant = await make_tenant(name="T_C1-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="C1HQ", name="C1 HQ"
    )
    region_id, region_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="REGION",
        code="C1WEST",
        name="C1 West",
        parent_id=root_id,
        parent_path=root_path,
    )
    _store_id, store_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="STORE",
        code="C1S1",
        name="C1 Store 1",
        parent_id=region_id,
        parent_path=region_path,
    )

    role = await make_role(audience="TENANT")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="PRICING_OS", resource="MARKDOWNS",
        action="VIEW", scope="STORE",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    # Anchor at the tenant root.
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=tu.id,
        org_node_id=root_id,
        role_id=role.id,
        status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.PRICING_OS,
        enabled_by_user_id=pu.id,
        created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant.id, tu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session,
            auth,
            module=ModuleCode.PRICING_OS,
            resource=PermissionResource.MARKDOWNS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.STORE,
            target_anchor=store_path,
        )

    assert allowed is True
    assert code is ReasonCode.GRANT_MATCHED


async def test_c2_grant_at_region_covers_same_region_store(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_C2: grant anchored at region X covers a store under region X."""
    tenant = await make_tenant(name="T_C2-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="C2HQ", name="C2 HQ"
    )
    region_id, region_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="REGION",
        code="C2WEST",
        name="C2 West",
        parent_id=root_id,
        parent_path=root_path,
    )
    _store_id, store_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="STORE",
        code="C2S1",
        name="C2 Store 1",
        parent_id=region_id,
        parent_path=region_path,
    )

    role = await make_role(audience="TENANT")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="PRICING_OS", resource="MARKDOWNS",
        action="VIEW", scope="STORE",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=tu.id,
        org_node_id=region_id,
        role_id=role.id,
        status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.PRICING_OS,
        enabled_by_user_id=pu.id,
        created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant.id, tu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session,
            auth,
            module=ModuleCode.PRICING_OS,
            resource=PermissionResource.MARKDOWNS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.STORE,
            target_anchor=store_path,
        )

    assert allowed is True
    assert code is ReasonCode.GRANT_MATCHED


async def test_c3_grant_at_region_x_denies_region_y_store(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_C3 — LOAD-BEARING: sibling region denial (segment-boundary respect).

    Grant at region 'c3west'; target = a store under region 'c3east'.
    Postgres ltree ``<@`` is segment-aware, so 'c3east.s1' is NOT a
    descendant of 'c3west'. A naive ``str.startswith`` would still
    confuse e.g. 'c3w' vs 'c3west'; this test catches that class of
    bug.
    """
    tenant = await make_tenant(name="T_C3-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="C3HQ", name="C3 HQ"
    )
    west_id, west_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="REGION",
        code="C3WEST",
        name="C3 West",
        parent_id=root_id,
        parent_path=root_path,
    )
    east_id, east_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="REGION",
        code="C3EAST",
        name="C3 East",
        parent_id=root_id,
        parent_path=root_path,
    )
    _east_store_id, east_store_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="STORE",
        code="C3ES1",
        name="C3 East Store 1",
        parent_id=east_id,
        parent_path=east_path,
    )

    role = await make_role(audience="TENANT")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="PRICING_OS", resource="MARKDOWNS",
        action="VIEW", scope="STORE",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=tu.id,
        org_node_id=west_id,
        role_id=role.id,
        status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.PRICING_OS,
        enabled_by_user_id=pu.id,
        created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    # Defensive sanity: the two region paths are siblings sharing the
    # tenant-root prefix.
    assert west_path.rsplit(".", 1)[0] == east_path.rsplit(".", 1)[0]
    assert west_path != east_path

    auth = _tenant_auth(tenant.id, tu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session,
            auth,
            module=ModuleCode.PRICING_OS,
            resource=PermissionResource.MARKDOWNS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.STORE,
            target_anchor=east_store_path,
        )

    assert allowed is False
    assert code is ReasonCode.NO_MATCHING_GRANT_OR_OUT_OF_SCOPE


async def test_c4_grant_at_region_with_no_target_anchor_allowed(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_C4: target_anchor=None bypasses the cascade clause (tuple match suffices)."""
    tenant = await make_tenant(name="T_C4-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="C4HQ", name="C4 HQ"
    )
    region_id, _region_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="REGION",
        code="C4WEST",
        name="C4 West",
        parent_id=root_id,
        parent_path=root_path,
    )

    role = await make_role(audience="TENANT")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="PRICING_OS", resource="PRICING_RULES",
        action="VIEW", scope="TENANT",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=tu.id,
        org_node_id=region_id,
        role_id=role.id,
        status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.PRICING_OS,
        enabled_by_user_id=pu.id,
        created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant.id, tu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session,
            auth,
            module=ModuleCode.PRICING_OS,
            resource=PermissionResource.PRICING_RULES,
            action=PermissionAction.VIEW,
            scope=PermissionScope.TENANT,
            target_anchor=None,
        )

    assert allowed is True
    assert code is ReasonCode.GRANT_MATCHED


# ============================================================================
# TENANT path — module access
# ============================================================================


async def test_m1_module_disabled_denies(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_M1 — LOAD-BEARING: PRICING_OS grant + module DISABLED → denied.

    Prompt's catalogue names this scenario ``SUSPENDED``; the live
    ``module_access_status_enum`` has only ``ENABLED`` / ``DISABLED``.
    Either non-ENABLED value satisfies the SQL ``tma.status='ENABLED'``
    filter by being non-matching; ``DISABLED`` is the live-enum
    equivalent of the prompt's intent.
    """
    from datetime import datetime, timezone

    tenant = await make_tenant(name="T_M1-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    on_id, _on_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="M1HQ", name="M1 HQ"
    )
    role = await make_role(audience="TENANT")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="PRICING_OS", resource="PRICING_RULES",
        action="VIEW", scope="TENANT",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=tu.id,
        org_node_id=on_id,
        role_id=role.id,
        status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    # DISABLED requires disabled_at and disabled_by_user_id (DDL CHECK).
    now = datetime.now(tz=timezone.utc)
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.PRICING_OS,
        status=ModuleAccessStatus.DISABLED,
        enabled_by_user_id=pu.id,
        created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
        disabled_at=now,
        disabled_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant.id, tu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session,
            auth,
            module=ModuleCode.PRICING_OS,
            resource=PermissionResource.PRICING_RULES,
            action=PermissionAction.VIEW,
            scope=PermissionScope.TENANT,
        )

    assert allowed is False
    assert code is ReasonCode.NO_MATCHING_GRANT_OR_OUT_OF_SCOPE


async def test_m2_module_enabled_allows(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_M2: ADMIN grant + module ENABLED → allowed."""
    tenant = await make_tenant(name="T_M2-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    on_id, _on_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="M2HQ", name="M2 HQ"
    )
    role = await make_role(audience="TENANT")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="ADMIN", resource="USERS",
        action="VIEW", scope="TENANT",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=tu.id,
        org_node_id=on_id,
        role_id=role.id,
        status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.ADMIN,
        enabled_by_user_id=pu.id,
        created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant.id, tu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session,
            auth,
            module=ModuleCode.ADMIN,
            resource=PermissionResource.USERS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.TENANT,
        )

    assert allowed is True
    assert code is ReasonCode.GRANT_MATCHED


# ============================================================================
# Cross-tenant safety
# ============================================================================


async def test_x1_tenant_a_user_denied_at_tenant_b_anchor(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_X1 — LOAD-BEARING: a TENANT-A user cannot pass a TENANT-B path.

    End-to-end check on the defense-in-depth chain: RLS scopes the
    session to TENANT A; the composite-FK JOIN keeps org_nodes joined
    only on tenant_id=A; the ltree ``<@`` compares a TENANT-B path
    against TENANT-A subtree (disjoint), so the predicate is false on
    every candidate row.
    """
    tenant_a = await make_tenant(name="T_X1-TenantA")
    tenant_b = await make_tenant(name="T_X1-TenantB")
    tu_a = await make_tenant_user(tenant_id=tenant_a.id, status="ACTIVE")
    a_root_id, _a_root_path = await make_org_node(
        tenant_id=tenant_a.id, node_type="TENANT", code="X1AHQ", name="X1 A HQ"
    )
    _b_root_id, b_root_path = await make_org_node(
        tenant_id=tenant_b.id, node_type="TENANT", code="X1BHQ", name="X1 B HQ"
    )

    role = await make_role(audience="TENANT")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="PRICING_OS", resource="MARKDOWNS",
        action="VIEW", scope="STORE",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    # TENANT A user has a full ACTIVE grant rooted at A.
    await make_tenant_user_role_assignment(
        tenant_id=tenant_a.id,
        tenant_user_id=tu_a.id,
        org_node_id=a_root_id,
        role_id=role.id,
        status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant_a.id,
        module=ModuleCode.PRICING_OS,
        enabled_by_user_id=pu.id,
        created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant_a.id, tu_a.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session,
            auth,
            module=ModuleCode.PRICING_OS,
            resource=PermissionResource.MARKDOWNS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.STORE,
            target_anchor=b_root_path,  # B's tenant root path
        )

    assert allowed is False
    assert code is ReasonCode.NO_MATCHING_GRANT_OR_OUT_OF_SCOPE


# ============================================================================
# Scope cascade (Step 6.9.3.1) — T_SC1..T_SC8
# ============================================================================
#
# Cascade direction is downward only: a grant at level N satisfies checks at
# every level below N (per the design conversation locked 2026-05-13 and the
# `satisfying_scopes()` helper). Two tests are LOAD-BEARING (T_SC6 and T_SC8).


async def test_sc1_global_grant_passes_global_check(
    session_factory,
    platform_auth,
    make_platform_user,
    make_role,
    make_role_permission,
    make_platform_user_role_assignment,
):
    """T_SC1: PLATFORM user with GLOBAL grant passes a GLOBAL check.

    Sanity: cascade preserves exact-scope behaviour.
    """
    pu = await make_platform_user(status="ACTIVE")
    role = await make_role(audience="PLATFORM")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="ADMIN", resource="TENANTS",
        action="VIEW", scope="GLOBAL",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_platform_user_role_assignment(
        platform_user_id=pu.id, role_id=role.id, status="ACTIVE"
    )

    auth = _platform_auth(pu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session, auth,
            module=ModuleCode.ADMIN,
            resource=PermissionResource.TENANTS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.GLOBAL,
        )

    assert allowed is True
    assert code is ReasonCode.GRANT_MATCHED


async def test_sc2_global_grant_passes_tenant_check(
    session_factory,
    platform_auth,
    make_platform_user,
    make_role,
    make_role_permission,
    make_platform_user_role_assignment,
):
    """T_SC2: PLATFORM user with ADMIN.USERS.VIEW.GLOBAL passes a
    (ADMIN, USERS, VIEW, TENANT) check via downward cascade.
    """
    pu = await make_platform_user(status="ACTIVE")
    role = await make_role(audience="PLATFORM")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="ADMIN", resource="USERS",
        action="VIEW", scope="GLOBAL",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_platform_user_role_assignment(
        platform_user_id=pu.id, role_id=role.id, status="ACTIVE"
    )

    auth = _platform_auth(pu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session, auth,
            module=ModuleCode.ADMIN,
            resource=PermissionResource.USERS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.TENANT,  # narrower than the grant
        )

    assert allowed is True
    assert code is ReasonCode.GRANT_MATCHED


async def test_sc3_global_grant_passes_store_check_on_platform_path(
    session_factory,
    platform_auth,
    make_platform_user,
    make_role,
    make_role_permission,
    make_platform_user_role_assignment,
):
    """T_SC3: PLATFORM user with ADMIN.USERS.VIEW.GLOBAL passes a
    (ADMIN, USERS, VIEW, STORE) check.

    Verifies GLOBAL→STORE cascade on the PLATFORM path. target_anchor
    is not relevant here (PLATFORM path ignores it). The check tuple
    has no catalogue row at STORE scope; cascade allows because the
    user's GLOBAL grant satisfies any narrower scope.
    """
    pu = await make_platform_user(status="ACTIVE")
    role = await make_role(audience="PLATFORM")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="ADMIN", resource="USERS",
        action="VIEW", scope="GLOBAL",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_platform_user_role_assignment(
        platform_user_id=pu.id, role_id=role.id, status="ACTIVE"
    )

    auth = _platform_auth(pu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session, auth,
            module=ModuleCode.ADMIN,
            resource=PermissionResource.USERS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.STORE,  # two levels narrower
        )

    assert allowed is True
    assert code is ReasonCode.GRANT_MATCHED


async def test_sc4_tenant_grant_passes_tenant_check(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_SC4: TENANT user with a TENANT-scope grant passes a TENANT check.

    Sanity: TENANT-path cascade preserves exact-scope behaviour.
    """
    tenant = await make_tenant(name="T_SC4-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    on_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code="SC4HQ", name="SC4 HQ",
    )
    role = await make_role(audience="TENANT")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="ADMIN", resource="USERS",
        action="VIEW", scope="TENANT",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id, tenant_user_id=tu.id,
        org_node_id=on_id, role_id=role.id, status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant.id, module=ModuleCode.ADMIN,
        enabled_by_user_id=pu.id, created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant.id, tu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session, auth,
            module=ModuleCode.ADMIN,
            resource=PermissionResource.USERS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.TENANT,
        )

    assert allowed is True
    assert code is ReasonCode.GRANT_MATCHED


async def test_sc5_tenant_grant_passes_store_check_with_anchor_cascade(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_SC5: TENANT user with ADMIN.USERS.VIEW.TENANT anchored at tenant
    root passes a STORE-scope check with target_anchor under that root.

    Exercises BOTH scope cascade (TENANT→STORE) AND anchor cascade
    (tenant-root path covers the store path via ltree ``<@``).

    Diagnostic value: if this test fails but T_SC4 passes, scope cascade
    is broken on the TENANT path. If T_SC8 (cross-tenant) passes but T_SC5
    fails, anchor cascade is broken.
    """
    tenant = await make_tenant(name="T_SC5-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code="SC5HQ", name="SC5 HQ",
    )
    region_id, region_path = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code="SC5WEST", name="SC5 West",
        parent_id=root_id, parent_path=root_path,
    )
    _store_id, store_path = await make_org_node(
        tenant_id=tenant.id, node_type="STORE",
        code="SC5S1", name="SC5 Store 1",
        parent_id=region_id, parent_path=region_path,
    )

    role = await make_role(audience="TENANT")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="ADMIN", resource="USERS",
        action="VIEW", scope="TENANT",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    # Grant anchored at tenant root; cascade must cover the store under it.
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id, tenant_user_id=tu.id,
        org_node_id=root_id, role_id=role.id, status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant.id, module=ModuleCode.ADMIN,
        enabled_by_user_id=pu.id, created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant.id, tu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session, auth,
            module=ModuleCode.ADMIN,
            resource=PermissionResource.USERS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.STORE,  # narrower than grant's TENANT
            target_anchor=store_path,
        )

    assert allowed is True
    assert code is ReasonCode.GRANT_MATCHED


async def test_sc6_store_grant_fails_tenant_check(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_SC6 — LOAD-BEARING: a user with only STORE grant FAILS a TENANT
    check.

    Cascade direction is downward only. A STORE grant must NOT satisfy a
    TENANT check. Any regression here means upward cascade slipped in.
    """
    tenant = await make_tenant(name="T_SC6-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    on_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code="SC6HQ", name="SC6 HQ",
    )
    role = await make_role(audience="TENANT")
    # Grant is at STORE scope: PRICING_OS.MARKDOWNS.VIEW.STORE.
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="PRICING_OS", resource="MARKDOWNS",
        action="VIEW", scope="STORE",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id, tenant_user_id=tu.id,
        org_node_id=on_id, role_id=role.id, status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant.id, module=ModuleCode.PRICING_OS,
        enabled_by_user_id=pu.id, created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant.id, tu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session, auth,
            module=ModuleCode.PRICING_OS,
            resource=PermissionResource.MARKDOWNS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.TENANT,  # broader than the grant
        )

    assert allowed is False
    assert code is ReasonCode.NO_MATCHING_GRANT_OR_OUT_OF_SCOPE


async def test_sc7_tenant_grant_fails_global_check(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_SC7: a user with only TENANT grant FAILS a GLOBAL check.

    Mirror of T_SC6 one level up. TENANT < GLOBAL; upward access denied.
    """
    tenant = await make_tenant(name="T_SC7-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    on_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code="SC7HQ", name="SC7 HQ",
    )
    role = await make_role(audience="TENANT")
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="ADMIN", resource="USERS",
        action="VIEW", scope="TENANT",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id, tenant_user_id=tu.id,
        org_node_id=on_id, role_id=role.id, status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant.id, module=ModuleCode.ADMIN,
        enabled_by_user_id=pu.id, created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant.id, tu.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session, auth,
            module=ModuleCode.ADMIN,
            resource=PermissionResource.USERS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.GLOBAL,  # broader than the grant
        )

    assert allowed is False
    assert code is ReasonCode.NO_MATCHING_GRANT_OR_OUT_OF_SCOPE


async def test_sc8_cross_tenant_cascade_still_denied(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_role_permission,
    make_tenant_user_role_assignment,
    make_platform_user,
    make_tenant_module_access,
):
    """T_SC8 — LOAD-BEARING: TENANT-A user with TENANT-scope grant cannot
    pass a STORE check whose target_anchor is under TENANT-B, even with
    scope cascade enabled.

    Scope cascade is a SEPARATE concern from anchor cascade. T_X1 already
    proves cross-tenant denial under exact-match scope; this test
    re-asserts it under the broadened scope-cascade SQL.

    Regression target: if scope cascade introduces an OR that bypasses the
    ``target_anchor <@ on_.path`` check, this test catches it.
    """
    tenant_a = await make_tenant(name="T_SC8-TenantA")
    tenant_b = await make_tenant(name="T_SC8-TenantB")
    tu_a = await make_tenant_user(tenant_id=tenant_a.id, status="ACTIVE")
    a_root_id, _a_root_path = await make_org_node(
        tenant_id=tenant_a.id, node_type="TENANT",
        code="SC8AHQ", name="SC8 A HQ",
    )
    _b_root_id, b_root_path = await make_org_node(
        tenant_id=tenant_b.id, node_type="TENANT",
        code="SC8BHQ", name="SC8 B HQ",
    )
    role = await make_role(audience="TENANT")
    # TENANT-scope grant (broader than the STORE check below, so cascade
    # would normally satisfy the tuple — anchor cascade must still deny).
    perm_id = await _lookup_permission_id(
        session_factory, platform_auth,
        module="ADMIN", resource="USERS",
        action="VIEW", scope="TENANT",
    )
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_tenant_user_role_assignment(
        tenant_id=tenant_a.id, tenant_user_id=tu_a.id,
        org_node_id=a_root_id, role_id=role.id, status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    await make_tenant_module_access(
        tenant_id=tenant_a.id, module=ModuleCode.ADMIN,
        enabled_by_user_id=pu.id, created_by_user_id=pu.id,
        updated_by_user_id=pu.id,
    )

    auth = _tenant_auth(tenant_a.id, tu_a.id)
    async for session in get_tenant_session(auth, session_factory):
        allowed, code, _ = await has_permission(
            session, auth,
            module=ModuleCode.ADMIN,
            resource=PermissionResource.USERS,
            action=PermissionAction.VIEW,
            scope=PermissionScope.STORE,  # cascade-satisfied by TENANT grant
            target_anchor=b_root_path,  # but anchor is under TENANT B
        )

    assert allowed is False
    assert code is ReasonCode.NO_MATCHING_GRANT_OR_OUT_OF_SCOPE
