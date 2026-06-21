"""Integration tests for the /me/* router and the require() gate factory.

Step 6.9.2.

Real Postgres, real schema, real RLS, real ltree. Uses the existing
``app_client`` pattern from sibling test files (mirrors
``test_role_assignments_router.py``). Each test mints JWTs via
``make_test_jwt``; ``app_client_with_gate`` extends the standard
``app_client`` with a test-only ``/api/v1/_test_gated_global`` endpoint
that exercises the ``require(...)`` factory and a Repo call inside the
handler body (the latter for the T_GF4 "Repo never invoked when gate
denies" invariant).

Four tests are LOAD-BEARING:

- ``T_GF1`` — ``require(...)`` factory produces a callable that
  FastAPI accepts as a Depends.
- ``T_GF2`` — Gate denies → 403 ``PERMISSION_DENIED`` with
  ``details: null`` (envelope contract per F-ERR-3).
- ``T_GF3`` — Gate allows → handler body runs to completion.
- ``T_GF4`` — Gate denies → handler body's Repo call is NEVER fired
  (mirrors Step 6.8.3 R2's no-call invariant via patch + AsyncMock).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext
from admin_backend.auth.permissions import require
from admin_backend.auth.stub import StubAuthClient
from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings, get_settings
from admin_backend.db.session import get_tenant_session
from admin_backend.dependencies import get_tenant_session_dep
from admin_backend.main import create_app
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.repositories.tenants import TenantsRepo


_GATED_TEST_PATH = "/api/v1/_test_gated_global"


# Module-level Repo singleton. The ``app_client_with_gate`` fixture
# registers a test-only endpoint whose handler body calls
# ``_test_repo.list_with_aggregates(session)``; T_GF4 patches this
# instance to assert the call_count == 0 invariant when the gate denies.
_test_repo = TenantsRepo()


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _platform_jwt(settings: Settings, user_id: UUID | None = None) -> str:
    return make_test_jwt(
        settings,
        user_id=user_id or uuid.uuid4(),
        user_type="PLATFORM",
    )


def _tenant_jwt(
    settings: Settings, tenant_id: UUID, user_id: UUID | None = None
) -> str:
    return make_test_jwt(
        settings,
        user_id=user_id or uuid.uuid4(),
        user_type="TENANT",
        tenant_id=tenant_id,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,
    session_factory: Any,
) -> Iterator[TestClient]:
    """Standard /me/* test client. Mirrors sibling router-test files."""
    app_obj = create_app()
    app_obj.state.settings = settings
    app_obj.state.engine = engine
    app_obj.state.session_factory = session_factory
    app_obj.state.auth_client = StubAuthClient(settings)
    with TestClient(app_obj) as c:
        yield c


@pytest.fixture
def app_client_with_gate(
    settings: Settings,
    engine: Any,
    session_factory: Any,
) -> Iterator[TestClient]:
    """TestClient with an extra test-only gated endpoint mounted.

    The endpoint is gated on ``(ADMIN, USERS, VIEW, GLOBAL)`` — a
    PLATFORM-scope permission so ``target_anchor=None`` (the 6.9.2
    factory's hardcoded value) is semantically correct. The handler
    body calls ``_test_repo.list_with_aggregates(session)`` so T_GF4
    can patch the method and assert it was NEVER reached when the gate
    denies.
    """
    app_obj = create_app()
    app_obj.state.settings = settings
    app_obj.state.engine = engine
    app_obj.state.session_factory = session_factory
    app_obj.state.auth_client = StubAuthClient(settings)

    @app_obj.get(
        _GATED_TEST_PATH,
        include_in_schema=False,
        dependencies=[
            Depends(
                require(
                    ModuleCode.ADMIN,
                    PermissionResource.USERS,
                    PermissionAction.VIEW,
                    PermissionScope.GLOBAL,
                )
            )
        ],
    )
    async def _test_gated_global(
        session: AsyncSession = Depends(get_tenant_session_dep),
    ) -> dict[str, Any]:
        # The call_count==0 invariant in T_GF4 needs the handler body
        # to perform a Repo call when reached. ``list_with_aggregates``
        # is cheap, RLS-correct under both audiences, and module-bound
        # so the test patch is straightforward.
        await _test_repo.list_with_aggregates(session)
        return {"reached_handler": True}

    with TestClient(app_obj) as c:
        yield c


# ---------------------------------------------------------------------------
# Helper: build an empty-permissions PLATFORM user (T_MP3)
# ---------------------------------------------------------------------------


async def _make_platform_user_with_grant(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    make_platform_user: Any,
    make_role: Any,
    make_role_permission: Any,
    make_platform_user_role_assignment: Any,
    *,
    module: str,
    resource: str,
    action: str,
    scope: str,
) -> Any:
    """Create a PLATFORM user wired to a seeded permission tuple.

    Looks up the existing seed permission row by code; the seed
    catalogue covers every tuple the v0 test suite needs. Returns the
    SimpleNamespace from ``make_platform_user`` so the caller has the
    ``id`` field to mint a JWT against.
    """
    pu = await make_platform_user(status="ACTIVE")
    role = await make_role(audience="PLATFORM")
    code = f"{module}.{resource}.{action}.{scope}"
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(f"SELECT id FROM {get_settings().db_schema}.permissions WHERE code = :code"),
            {"code": code},
        )
        row = result.first()
    if row is None:
        raise LookupError(f"seed permission {code!r} not present")
    perm_id = uuid.UUID(str(row[0]))
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_platform_user_role_assignment(
        platform_user_id=pu.id, role_id=role.id, status="ACTIVE"
    )
    return pu


# ============================================================================
# /me/permissions — T_MP1..T_MP6
# ============================================================================


async def test_mp1_platform_user_with_grants(
    app_client,
    settings,
    session_factory,
    platform_auth,
    make_platform_user,
    make_role,
    make_role_permission,
    make_platform_user_role_assignment,
):
    """T_MP1: PLATFORM user with one grant gets a non-empty array.

    The grant tuple appears in the response with anchor_path=null.
    """
    pu = await _make_platform_user_with_grant(
        session_factory, platform_auth,
        make_platform_user, make_role, make_role_permission,
        make_platform_user_role_assignment,
        module="ADMIN", resource="USERS",
        action="VIEW", scope="GLOBAL",
    )

    resp = app_client.get(
        "/api/v1/me/permissions",
        headers=_auth(_platform_jwt(settings, pu.id)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "permissions" in body
    assert isinstance(body["permissions"], list)
    grants = body["permissions"]
    assert len(grants) >= 1
    match = next(
        (
            g for g in grants
            if g["module"] == "ADMIN"
            and g["resource"] == "USERS"
            and g["action"] == "VIEW"
            and g["scope"] == "GLOBAL"
        ),
        None,
    )
    assert match is not None
    assert match["anchor_path"] is None


async def test_mp2_tenant_user_scoped_to_enabled_modules(
    app_client,
    settings,
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
    """T_MP2: TENANT user gets grants scoped to their tenant; anchor_path set.

    Reuses the canonical seed permission ``PRICING_OS.PRICING_RULES.VIEW.TENANT``
    plus a tenant_module_access row with status=ENABLED.
    """
    tenant = await make_tenant(name="MP2-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    on_id, on_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code="MP2HQ", name="MP2 HQ",
    )
    role = await make_role(audience="TENANT")

    code = "PRICING_OS.PRICING_RULES.VIEW.TENANT"
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(f"SELECT id FROM {get_settings().db_schema}.permissions WHERE code = :code"),
            {"code": code},
        )
        perm_row = result.first()
    assert perm_row is not None
    perm_id = uuid.UUID(str(perm_row[0]))
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

    resp = app_client.get(
        "/api/v1/me/permissions",
        headers=_auth(_tenant_jwt(settings, tenant.id, tu.id)),
    )
    assert resp.status_code == 200
    grants = resp.json()["permissions"]
    assert len(grants) >= 1
    match = next(
        (
            g for g in grants
            if g["module"] == "PRICING_OS"
            and g["resource"] == "PRICING_RULES"
            and g["action"] == "VIEW"
            and g["scope"] == "TENANT"
        ),
        None,
    )
    assert match is not None
    assert match["anchor_path"] == on_path


async def test_mp3_user_with_no_assignments_empty_array(
    app_client, settings, make_tenant, make_tenant_user
):
    """T_MP3: TENANT user with no role assignments → empty permissions array.

    The TENANT user is created without ANY role assignment; the response
    must be the empty-array shape, not 404 or 500.
    """
    tenant = await make_tenant(name="MP3-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")

    resp = app_client.get(
        "/api/v1/me/permissions",
        headers=_auth(_tenant_jwt(settings, tenant.id, tu.id)),
    )
    assert resp.status_code == 200
    assert resp.json() == {"permissions": []}


async def test_mp4_tenant_grant_in_disabled_module_filtered_out(
    app_client,
    settings,
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
    """T_MP4: TENANT user with a PRICING_OS grant but DISABLED module.

    Grant exists in tenant_user_role_assignments + role_permissions, but
    tenant_module_access for PRICING_OS is DISABLED. The /me/permissions
    response must NOT include the grant.
    """
    from datetime import datetime, timezone

    tenant = await make_tenant(name="MP4-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    on_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code="MP4HQ", name="MP4 HQ",
    )
    role = await make_role(audience="TENANT")
    code = "PRICING_OS.PRICING_RULES.VIEW.TENANT"
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(f"SELECT id FROM {get_settings().db_schema}.permissions WHERE code = :code"),
            {"code": code},
        )
        perm_row = result.first()
    assert perm_row is not None
    perm_id = uuid.UUID(str(perm_row[0]))
    await make_role_permission(role_id=role.id, permission_id=perm_id)
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=tu.id,
        org_node_id=on_id,
        role_id=role.id,
        status="ACTIVE",
    )
    pu = await make_platform_user(status="ACTIVE")
    now = datetime.now(tz=timezone.utc)
    from admin_backend.models.tenant_module_access import ModuleAccessStatus
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

    resp = app_client.get(
        "/api/v1/me/permissions",
        headers=_auth(_tenant_jwt(settings, tenant.id, tu.id)),
    )
    assert resp.status_code == 200
    grants = resp.json()["permissions"]
    matching = [
        g for g in grants
        if g["module"] == "PRICING_OS"
        and g["resource"] == "PRICING_RULES"
    ]
    assert matching == []


async def test_mp5_response_shape_matches_grant_structure(
    app_client,
    settings,
    session_factory,
    platform_auth,
    make_platform_user,
    make_role,
    make_role_permission,
    make_platform_user_role_assignment,
):
    """T_MP5: each grant item has exactly the 5 expected fields."""
    pu = await _make_platform_user_with_grant(
        session_factory, platform_auth,
        make_platform_user, make_role, make_role_permission,
        make_platform_user_role_assignment,
        module="ADMIN", resource="USERS",
        action="VIEW", scope="GLOBAL",
    )
    resp = app_client.get(
        "/api/v1/me/permissions",
        headers=_auth(_platform_jwt(settings, pu.id)),
    )
    assert resp.status_code == 200
    grants = resp.json()["permissions"]
    assert grants
    for g in grants:
        assert set(g.keys()) == {
            "module", "resource", "action", "scope", "anchor_path"
        }
        assert isinstance(g["module"], str)
        assert isinstance(g["resource"], str)
        assert isinstance(g["action"], str)
        assert isinstance(g["scope"], str)
        assert g["anchor_path"] is None or isinstance(g["anchor_path"], str)


def test_mp6_no_auth_returns_401(app_client):
    """T_MP6: missing JWT → 401 ``AUTH_MISSING``."""
    resp = app_client.get("/api/v1/me/permissions")
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MISSING"


# ============================================================================
# /me/can-do — T_MC1..T_MC7
# ============================================================================


async def test_mc1_user_with_permission_allowed(
    app_client,
    settings,
    session_factory,
    platform_auth,
    make_platform_user,
    make_role,
    make_role_permission,
    make_platform_user_role_assignment,
):
    """T_MC1: PLATFORM user with the queried permission → allowed=true."""
    pu = await _make_platform_user_with_grant(
        session_factory, platform_auth,
        make_platform_user, make_role, make_role_permission,
        make_platform_user_role_assignment,
        module="ADMIN", resource="USERS",
        action="VIEW", scope="GLOBAL",
    )
    resp = app_client.get(
        "/api/v1/me/can-do",
        params={
            "module": "ADMIN",
            "resource": "USERS",
            "action": "VIEW",
            "scope": "GLOBAL",
        },
        headers=_auth(_platform_jwt(settings, pu.id)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"allowed": True, "reason_code": "GRANT_MATCHED"}


async def test_mc2_user_without_permission_denied(
    app_client,
    settings,
    make_tenant,
    make_tenant_user,
):
    """T_MC2: TENANT user with no grants → allowed=false."""
    tenant = await make_tenant(name="MC2-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    resp = app_client.get(
        "/api/v1/me/can-do",
        params={
            "module": "ADMIN",
            "resource": "USERS",
            "action": "VIEW",
            "scope": "TENANT",
        },
        headers=_auth(_tenant_jwt(settings, tenant.id, tu.id)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "allowed": False,
        "reason_code": "NO_MATCHING_GRANT_OR_OUT_OF_SCOPE",
    }


async def test_mc3_cascade_target_under_region_allowed(
    app_client,
    settings,
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
    """T_MC3: TENANT user with region grant; target_anchor under that region.

    Grant anchored at region X; query target_anchor = a store under
    region X. ltree cascade matches → allowed.
    """
    tenant = await make_tenant(name="MC3-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code="MC3HQ", name="MC3 HQ",
    )
    region_id, region_path = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code="MC3WEST", name="MC3 West",
        parent_id=root_id, parent_path=root_path,
    )
    _store_id, store_path = await make_org_node(
        tenant_id=tenant.id, node_type="STORE",
        code="MC3S1", name="MC3 Store 1",
        parent_id=region_id, parent_path=region_path,
    )
    role = await make_role(audience="TENANT")
    code = "PRICING_OS.MARKDOWNS.VIEW.STORE"
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(f"SELECT id FROM {get_settings().db_schema}.permissions WHERE code = :code"),
            {"code": code},
        )
        perm_row = result.first()
    assert perm_row is not None
    perm_id = uuid.UUID(str(perm_row[0]))
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

    resp = app_client.get(
        "/api/v1/me/can-do",
        params={
            "module": "PRICING_OS",
            "resource": "MARKDOWNS",
            "action": "VIEW",
            "scope": "STORE",
            "target_anchor": store_path,
        },
        headers=_auth(_tenant_jwt(settings, tenant.id, tu.id)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed"] is True
    assert body["reason_code"] == "GRANT_MATCHED"


async def test_mc4_cascade_target_outside_region_denied(
    app_client,
    settings,
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
    """T_MC4: TENANT user with region X grant; target_anchor under region Y.

    Sibling region; ltree <@ returns false; reason_code = no-match.
    """
    tenant = await make_tenant(name="MC4-Tenant")
    tu = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code="MC4HQ", name="MC4 HQ",
    )
    west_id, _west_path = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code="MC4WEST", name="MC4 West",
        parent_id=root_id, parent_path=root_path,
    )
    east_id, east_path = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code="MC4EAST", name="MC4 East",
        parent_id=root_id, parent_path=root_path,
    )
    _east_store_id, east_store_path = await make_org_node(
        tenant_id=tenant.id, node_type="STORE",
        code="MC4ES1", name="MC4 East Store 1",
        parent_id=east_id, parent_path=east_path,
    )
    role = await make_role(audience="TENANT")
    code = "PRICING_OS.MARKDOWNS.VIEW.STORE"
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(f"SELECT id FROM {get_settings().db_schema}.permissions WHERE code = :code"),
            {"code": code},
        )
        perm_row = result.first()
    assert perm_row is not None
    perm_id = uuid.UUID(str(perm_row[0]))
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

    resp = app_client.get(
        "/api/v1/me/can-do",
        params={
            "module": "PRICING_OS",
            "resource": "MARKDOWNS",
            "action": "VIEW",
            "scope": "STORE",
            "target_anchor": east_store_path,
        },
        headers=_auth(_tenant_jwt(settings, tenant.id, tu.id)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed"] is False
    assert body["reason_code"] == "NO_MATCHING_GRANT_OR_OUT_OF_SCOPE"


def test_mc5_missing_query_param_returns_422(app_client, settings):
    """T_MC5: missing the ``scope`` query param → 422 (FastAPI validation)."""
    resp = app_client.get(
        "/api/v1/me/can-do",
        params={"module": "ADMIN", "resource": "USERS", "action": "VIEW"},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 422


def test_mc6_invalid_module_enum_returns_422(app_client, settings):
    """T_MC6: bogus module value → 422.

    Uses a clearly-invented value ('NOT_A_MODULE'); avoiding 'ROOS'
    which sits in a different category (DB enum has it but the Python
    ModuleCode enum doesn't post-9462e11).
    """
    resp = app_client.get(
        "/api/v1/me/can-do",
        params={
            "module": "NOT_A_MODULE",
            "resource": "USERS",
            "action": "VIEW",
            "scope": "GLOBAL",
        },
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 422


def test_mc7_no_auth_returns_401(app_client):
    """T_MC7: missing JWT → 401."""
    resp = app_client.get(
        "/api/v1/me/can-do",
        params={
            "module": "ADMIN",
            "resource": "USERS",
            "action": "VIEW",
            "scope": "GLOBAL",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MISSING"


# ============================================================================
# Gate factory tests — T_GF1..T_GF4 (LOAD-BEARING)
# ============================================================================


def test_gf1_factory_returns_fastapi_compatible_dependency(app_client_with_gate):
    """T_GF1 (LOAD-BEARING): the ``require()`` factory produces a callable
    that FastAPI mounts as a Depends without error.

    The fixture's app construction proves this: if ``require(...)``
    returned a non-callable or a callable with an unacceptable signature,
    FastAPI would raise at app construction (before the TestClient is
    yielded). Reaching this test body is the assertion.

    A subsequent no-auth probe of the gated endpoint must surface 401
    AUTH_MISSING (not 500 or 422), proving the dependency chain is
    well-formed end-to-end.
    """
    resp = app_client_with_gate.get(_GATED_TEST_PATH)
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MISSING"


async def test_gf2_gate_denies_returns_403_permission_denied(
    app_client_with_gate,
    settings,
    make_tenant,
):
    """T_GF2 (LOAD-BEARING): gate denial → 403 with the expected envelope.

    Asserts response envelope keys ({code, message, details, request_id}),
    code value, and details=null per the F-ERR-3 contract.
    """
    tenant = await make_tenant(name="GF2-Tenant")
    resp = app_client_with_gate.get(
        _GATED_TEST_PATH,
        headers=_auth(_tenant_jwt(settings, tenant.id)),
    )
    assert resp.status_code == 403
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "details", "request_id"}
    assert body["code"] == "PERMISSION_DENIED"
    assert body["message"] == "Permission denied"
    assert body["details"] is None
    assert body["request_id"]  # non-empty


async def test_gf3_gate_allows_handler_body_runs(
    app_client_with_gate,
    settings,
    session_factory,
    platform_auth,
    make_platform_user,
    make_role,
    make_role_permission,
    make_platform_user_role_assignment,
):
    """T_GF3 (LOAD-BEARING): gate allow → handler body executes to completion.

    The handler returns ``{"reached_handler": True}``; the assertion
    confirms the body ran (not just that the gate didn't 403).
    """
    pu = await _make_platform_user_with_grant(
        session_factory, platform_auth,
        make_platform_user, make_role, make_role_permission,
        make_platform_user_role_assignment,
        module="ADMIN", resource="USERS",
        action="VIEW", scope="GLOBAL",
    )
    resp = app_client_with_gate.get(
        _GATED_TEST_PATH,
        headers=_auth(_platform_jwt(settings, pu.id)),
    )
    assert resp.status_code == 200
    assert resp.json() == {"reached_handler": True}


async def test_gf4_gate_denies_before_repo_call(
    app_client_with_gate,
    settings,
    make_tenant,
):
    """T_GF4 (LOAD-BEARING): denied request never reaches the handler body.

    Mirrors Step 6.8.3 R2. Patches the module-level ``_test_repo``'s
    ``list_with_aggregates`` to AsyncMock and asserts call_count == 0
    after a denied request. Proves the gate raises before the handler
    body's Repo call fires.

    Regression target: if a future refactor moved the ``has_permission``
    check from a Depends-time raise to a handler-body call, the denial
    response would still be 403 but ``call_count`` would be 1, and this
    test would catch the regression.
    """
    tenant = await make_tenant(name="GF4-Tenant")
    with patch.object(
        _test_repo, "list_with_aggregates", new=AsyncMock(),
    ) as mock_list:
        resp = app_client_with_gate.get(
            _GATED_TEST_PATH,
            headers=_auth(_tenant_jwt(settings, tenant.id)),
        )

    assert resp.status_code == 403
    assert resp.json()["code"] == "PERMISSION_DENIED"
    assert mock_list.call_count == 0


# ============================================================================
# Cross-tenant — T_XT1
# ============================================================================


async def test_xt1_tenant_a_user_denied_at_tenant_b_anchor(
    app_client,
    settings,
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
    """T_XT1: TENANT-A user calling /me/can-do with TENANT-B's anchor → denied.

    End-to-end check: HTTP → middleware → AuthContext → require/has_permission.
    Mirrors Step 6.9.1's T_X1 but through the HTTP path.
    """
    tenant_a = await make_tenant(name="XT1-TenantA")
    tenant_b = await make_tenant(name="XT1-TenantB")
    tu_a = await make_tenant_user(tenant_id=tenant_a.id, status="ACTIVE")
    a_root_id, _ = await make_org_node(
        tenant_id=tenant_a.id, node_type="TENANT",
        code="XT1AHQ", name="XT1 A HQ",
    )
    _b_root_id, b_root_path = await make_org_node(
        tenant_id=tenant_b.id, node_type="TENANT",
        code="XT1BHQ", name="XT1 B HQ",
    )
    role = await make_role(audience="TENANT")
    code = "PRICING_OS.MARKDOWNS.VIEW.STORE"
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(f"SELECT id FROM {get_settings().db_schema}.permissions WHERE code = :code"),
            {"code": code},
        )
        perm_row = result.first()
    assert perm_row is not None
    perm_id = uuid.UUID(str(perm_row[0]))
    await make_role_permission(role_id=role.id, permission_id=perm_id)
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

    resp = app_client.get(
        "/api/v1/me/can-do",
        params={
            "module": "PRICING_OS",
            "resource": "MARKDOWNS",
            "action": "VIEW",
            "scope": "STORE",
            "target_anchor": b_root_path,
        },
        headers=_auth(_tenant_jwt(settings, tenant_a.id, tu_a.id)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed"] is False
    assert body["reason_code"] == "NO_MATCHING_GRANT_OR_OUT_OF_SCOPE"


# ---------------------------------------------------------------------------
# Step 6.20.2 — /me/can-do target_anchor pattern validation (MC8)
# ---------------------------------------------------------------------------


def test_mc8_malformed_target_anchor_returns_422(app_client, settings):
    """T_MC8: malformed target_anchor rejected at Pydantic layer with 422.

    Step 6.20.2 closes FN-AB-61. Pre-fix, target_anchor was a bare
    ``str | None`` Query param passed verbatim to ``has_permission``,
    where ``CAST(:target_anchor AS ltree)`` raised
    ``psycopg.errors.SyntaxError`` that bubbled to the generic 500
    envelope. Post-fix, the Query carries
    ``pattern=r"^[A-Za-z0-9_]+(\\.[A-Za-z0-9_]+)*$"`` and ``max_length=1024``
    so FastAPI returns 422 BEFORE the gate dependency or handler body
    runs.

    Test shape mirrors ``test_v7_invalid_code_format_pydantic_422`` at
    ``tests/integration/test_org_tree_writes_router.py:485-521`` —
    single function, one block per failure shape, all asserting 422.

    Per LD4 the cloud-reported bug fires only under TENANT JWT
    (PLATFORM branch in ``has_permission`` ignores target_anchor and
    never reaches the ltree CAST). Pydantic 422 is JWT-type-agnostic
    (the pattern check runs as part of FastAPI Query validation,
    independent of auth dispatch), but using TENANT JWT here mirrors
    the cloud failure shape end-to-end.

    Load-bearing IDs (commit-report sign-off):
      - MC8a (hyphen / UUID shape — the cloud-reported failure)
      - MC8b (leading dot)
      - MC8c (trailing dot)
      - MC8d (consecutive dots)
      - MC8e (whitespace)

    MC8f (empty string) is correctness-only — empty string fails the
    pattern (which requires at least one character per label).
    """
    tenant_id = uuid.uuid4()
    jwt = _tenant_jwt(settings, tenant_id)
    base_params = {
        "module": "ADMIN",
        "resource": "USERS",
        "action": "VIEW",
        "scope": "TENANT",
    }

    def _assert_pattern_rejected(value: str, *, label: str) -> None:
        resp = app_client.get(
            "/api/v1/me/can-do",
            params={**base_params, "target_anchor": value},
            headers=_auth(jwt),
        )
        assert resp.status_code == 422, (
            f"{label}: expected 422 for target_anchor={value!r}, "
            f"got {resp.status_code} body={resp.text}"
        )
        # FastAPI's default 422 envelope contains a `detail` list; each
        # entry identifies the failing input via `loc`. We only assert
        # the field appears in loc rather than pinning exact wording
        # (Pydantic error messages drift across versions).
        payload = resp.json()
        loc_strs = [
            ".".join(str(p) for p in (entry.get("loc") or []))
            for entry in payload.get("detail", [])
        ]
        assert any("target_anchor" in s for s in loc_strs), (
            f"{label}: target_anchor not identified in error detail; "
            f"got loc list = {loc_strs!r}"
        )

    # MC8a — hyphen-bearing (UUID shape; cloud-reported failure at
    # v0.1.17 / admin-backend-00018-46f). LOAD-BEARING.
    _assert_pattern_rejected(
        "019df261-b87c-7d3e-ab9e-dcf26259cec6", label="MC8a (UUID)"
    )

    # MC8b — leading dot. LOAD-BEARING.
    _assert_pattern_rejected(
        ".tenant_root.region_us", label="MC8b (leading dot)"
    )

    # MC8c — trailing dot. LOAD-BEARING.
    _assert_pattern_rejected(
        "tenant_root.region_us.", label="MC8c (trailing dot)"
    )

    # MC8d — consecutive dots (empty inner label). LOAD-BEARING.
    _assert_pattern_rejected(
        "tenant_root..region_us", label="MC8d (consecutive dots)"
    )

    # MC8e — whitespace mid-path. LOAD-BEARING.
    _assert_pattern_rejected(
        "tenant_root region_us", label="MC8e (whitespace)"
    )

    # MC8f — empty string. Correctness-only: pattern requires at least
    # one alphanumeric or underscore, so "" fails. (FastAPI binds
    # absent param to None and skips pattern; an explicit empty string
    # binds as "" and is pattern-checked.)
    _assert_pattern_rejected("", label="MC8f (empty)")
