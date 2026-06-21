"""Gate retrofit behavioral tests (Step 6.9.3.2).

8 tests covering the retrofit's behavioral surface end-to-end:

  - T_RET_1: SUPER_ADMIN passes /tenants/{id} via GLOBAL→TENANT cascade
  - T_RET_2: OWNER on /tenants/{id} — DEFERRED (xfail-seed-update)
  - T_RET_3: Cross-tenant request returns 404 via anchor dep (LOAD-BEARING)
  - T_RET_4: No-auth → 401 (gate runs after auth middleware)
  - T_RET_5: _require_platform_auth retirement behavioral equivalence (LOAD-BEARING)
  - T_RET_6: Gate marker introspection — positive verification (LOAD-BEARING)
  - T_RET_7: Anchor dep injection — target_anchor flows through
  - T_RET_8: Multi-user-type endpoint via cascade

3 LOAD-BEARING: T_RET_3 (anchor-miss raises 404, never returns None —
F-THREADING-4 security invariant), T_RET_5 (retirement doesn't regress
the access surface; same 403 outcome for non-PLATFORM JWTs, new error
code), T_RET_6 (positive verification of the gate marker's tuple —
pairs with the discipline test's structural-only assertion).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from admin_backend.auth.gate_info import PermissionGateInfo
from admin_backend.auth.stub import StubAuthClient
from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings
from admin_backend.main import create_app
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import ModuleCode


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _tenant_jwt_for_random_user(
    settings: Settings, tenant_id: UUID
) -> str:
    """Mint a TENANT JWT with a random user_id (used by cross-tenant T_RET_3).

    The point of T_RET_3 is the anchor dep raising 404 from the
    cross-tenant probe — the user_id doesn't need to map to a real
    seeded user because the anchor lookup runs BEFORE the gate body
    that would otherwise care.
    """
    return make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="TENANT",
        tenant_id=tenant_id,
    )


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,
    session_factory: Any,
) -> Iterator[TestClient]:
    """Standard retrofit-test TestClient. Mirrors sibling router-test files."""
    app_obj = create_app()
    app_obj.state.settings = settings
    app_obj.state.engine = engine
    app_obj.state.session_factory = session_factory
    app_obj.state.auth_client = StubAuthClient(settings)
    with TestClient(app_obj) as c:
        yield c


# =============================================================================
# T_RET_1 — SUPER_ADMIN passes /tenants/{id} via GLOBAL→TENANT cascade
# =============================================================================


async def test_ret_1_super_admin_passes_tenant_detail_via_cascade(
    app_client, super_admin_jwt, make_tenant, make_org_node
):
    """SUPER_ADMIN holds ADMIN.TENANTS.VIEW.GLOBAL; the /tenants/{id}
    gate requires ADMIN.TENANTS.VIEW.TENANT. Cascade satisfies. The
    handler returns 200 with the tenant row.

    The synthetic tenant has a TENANT-root org_node so the anchor
    dep ``get_tenant_anchor`` resolves cleanly. Without the root,
    anchor would raise 404 BEFORE the gate gets to evaluate.
    """
    tenant = await make_tenant(name="RET1-Tenant")
    await make_org_node(
        tenant_id=tenant.id,
        node_type="TENANT",
        code=f"ret1-{tenant.id.hex[:6]}",
        name="RET1 Root",
    )

    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == str(tenant.id)


# =============================================================================
# T_RET_2 — OWNER on /tenants/{their_id}
# =============================================================================


async def test_ret_2_owner_passes_own_tenant_detail(
    app_client, make_tenant, make_org_node, tenant_owner_jwt_factory
):
    """OWNER-equivalent TENANT user requests their own tenant detail.

    Post Phase 3 seed update (2026-05-13): the
    ADMIN.TENANTS.VIEW.TENANT tuple is in the catalogue; the factory
    grants it to the synthetic OWNER via explicit ``with_grants``.
    Tenant-row request → 200 via direct grant.
    """
    tenant = await make_tenant(name="RET2-Tenant")
    await make_org_node(
        tenant_id=tenant.id,
        node_type="TENANT",
        code=f"ret2-{tenant.id.hex[:6]}",
        name="RET2 Root",
    )
    jwt = await tenant_owner_jwt_factory(
        tenant.id,
        with_grants=[("ADMIN", "TENANTS", "VIEW", "TENANT")],
    )
    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}",
        headers=_auth(jwt),
    )
    assert resp.status_code == 200


# =============================================================================
# T_RET_3 — Cross-tenant 404 via anchor dep (LOAD-BEARING)
# =============================================================================


async def test_ret_3_cross_tenant_returns_404_via_anchor_dep(
    app_client, settings, make_tenant, make_org_node
):
    """LOAD-BEARING — F-THREADING-4 security invariant.

    A TENANT-A JWT probes TENANT-B's tenant_id. The anchor dep
    ``get_tenant_anchor(tenant_b.id)`` runs under TENANT-A's session
    GUCs; RLS hides TENANT-B's row; anchor raises
    ``TenantNotFoundError`` (404) BEFORE the gate body evaluates.

    Returning None instead of raising 404 would short-circuit the
    cascade clause in has_permission to TRUE (no target_anchor →
    cascade inactive → grant matches), creating a security regression.
    This test asserts the 404 path holds end-to-end.
    """
    tenant_a = await make_tenant(name="RET3-A")
    tenant_b = await make_tenant(name="RET3-B")
    # Both tenants get TENANT-root org_nodes so the anchor lookup has
    # something to find when called for their own tenant (i.e., the
    # "cross-tenant probe fails specifically because of RLS, not because
    # the root doesn't exist" property).
    await make_org_node(
        tenant_id=tenant_a.id,
        node_type="TENANT",
        code=f"ret3a-{tenant_a.id.hex[:6]}",
        name="RET3 A Root",
    )
    await make_org_node(
        tenant_id=tenant_b.id,
        node_type="TENANT",
        code=f"ret3b-{tenant_b.id.hex[:6]}",
        name="RET3 B Root",
    )

    resp = app_client.get(
        f"/api/v1/tenants/{tenant_b.id}",
        headers=_auth(_tenant_jwt_for_random_user(settings, tenant_a.id)),
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "TENANT_NOT_FOUND"


# =============================================================================
# T_RET_4 — No-auth → 401 (gate runs after auth middleware)
# =============================================================================


def test_ret_4_no_auth_returns_401(app_client):
    """Regression baseline. Gate evaluation is gated behind auth
    middleware. No JWT → 401 from middleware; gate never fires."""
    resp = app_client.get("/api/v1/tenants")
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MISSING"


# =============================================================================
# T_RET_5 — _require_platform_auth retirement equivalence (LOAD-BEARING)
# =============================================================================


def test_ret_5a_platform_user_with_grant_passes_platform_users(
    app_client, super_admin_jwt
):
    """SUPER_ADMIN holds ADMIN.USERS.VIEW.GLOBAL; passes /platform-users."""
    resp = app_client.get(
        "/api/v1/platform-users",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200


def test_ret_5b_tenant_jwt_denied_at_platform_users_with_new_code(
    app_client, settings
):
    """LOAD-BEARING — retirement behavioral equivalence.

    TENANT JWT denied at /platform-users; error code changed from
    PLATFORM_ACCESS_REQUIRED (pre-retirement) to PERMISSION_DENIED
    (post-retirement). Same 403 outcome; new code reflects the new
    gate path. Replaces the prior test that asserted the old code.
    """
    synthetic_tenant_id = uuid.uuid4()
    resp = app_client.get(
        "/api/v1/platform-users",
        headers=_auth(
            make_test_jwt(
                settings,
                user_id=uuid.uuid4(),
                user_type="TENANT",
                tenant_id=synthetic_tenant_id,
            )
        ),
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "PERMISSION_DENIED"
    assert body["message"] == "Permission denied"


# =============================================================================
# T_RET_6 — Gate marker positive verification (LOAD-BEARING)
# =============================================================================


def test_ret_6_gate_markers_capture_correct_tuples() -> None:
    """LOAD-BEARING — paired with the discipline meta-test.

    The discipline test verifies marker EXISTENCE only — it would
    accept a stub marker that doesn't match the gate's actual
    semantics. This test verifies CORRECTNESS — for 3 sample
    retrofitted routes, the marker captures the expected
    (module, resource, action, scope) tuple.

    Sample selection: one PLATFORM-scope (/platform-users), one
    TENANT-scope with anchor (GET /tenants/{id}), one TENANT-scope
    without anchor (/tenant-users). Together they cover the factory's
    two inner-function shapes and the marker's anchor_dep population.

    Step 6.11.2: keyed by ``(method, path)`` rather than path alone
    because the tenants write endpoints share parameterised paths with
    the GET ``/tenants/{tenant_id}`` route (PATCH on the same path,
    /suspend + /activate as nested POSTs). Method-aware indexing keeps
    the GET sample valid without altering its semantics.
    """
    app = create_app()
    expected: dict[
        tuple[str, str],
        tuple[ModuleCode, PermissionResource, PermissionAction, PermissionScope, bool],
    ] = {
        ("GET", "/api/v1/platform-users"): (
            ModuleCode.ADMIN,
            PermissionResource.USERS,
            PermissionAction.VIEW,
            PermissionScope.GLOBAL,
            False,  # no anchor_dep
        ),
        ("GET", "/api/v1/tenants/{tenant_id}"): (
            ModuleCode.ADMIN,
            PermissionResource.TENANTS,
            PermissionAction.VIEW,
            PermissionScope.TENANT,
            True,  # anchor_dep present (get_tenant_anchor)
        ),
        ("GET", "/api/v1/tenant-users"): (
            ModuleCode.ADMIN,
            PermissionResource.USERS,
            PermissionAction.VIEW,
            PermissionScope.TENANT,
            False,  # list endpoint — no anchor_dep
        ),
    }

    found: dict[tuple[str, str], PermissionGateInfo] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            key = (method, route.path)
            if key not in expected:
                continue
            for dep in route.dependant.dependencies:
                info = getattr(dep.call, "__permission_gate__", None)
                if info is not None:
                    found[key] = info
                    break

    assert set(found.keys()) == set(expected.keys()), (
        f"Markers missing for: {set(expected.keys()) - set(found.keys())}"
    )

    for key, (mod, res, act, scp, has_anchor) in expected.items():
        info = found[key]
        assert info.module == mod, f"{key} module mismatch"
        assert info.resource == res, f"{key} resource mismatch"
        assert info.action == act, f"{key} action mismatch"
        assert info.scope == scp, f"{key} scope mismatch"
        assert (info.anchor_dep is not None) == has_anchor, (
            f"{key} anchor_dep presence mismatch: "
            f"expected {has_anchor}, got {info.anchor_dep}"
        )


# =============================================================================
# T_RET_7 — Anchor dep injection: target_anchor flows through
# =============================================================================


async def test_ret_7_anchor_dep_resolves_for_org_node_children(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
):
    """Anchor dep ``get_org_node_anchor(tenant_id, node_id)`` resolves
    successfully when both ids correspond to a real row. The 200
    response confirms the gate passed (anchor returned a path; cascade
    satisfied via SUPER_ADMIN).

    Verification mechanism: end-to-end response. If the anchor dep
    failed silently or returned None, has_permission would either
    deny (random anchor doesn't match) or short-circuit incorrectly.
    A clean 200 with the expected response shape is the contract.
    """
    tenant = await make_tenant(name="RET7-Tenant")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="TENANT",
        code=f"ret7-{tenant.id.hex[:6]}",
        name="RET7 Root",
    )
    hq_id, _hq_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="HQ",
        code=f"ret7hq-{tenant.id.hex[:6]}",
        name="RET7 HQ",
        parent_id=root_id,
        parent_path=root_path,
    )

    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}/org-nodes/{hq_id}/children",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Anchor resolved → gate passed → handler ran → response well-formed.
    assert "items" in body
    assert "pagination" in body


# =============================================================================
# T_RET_8 — Multi-user-type via cascade
# =============================================================================


async def test_ret_8a_platform_jwt_passes_tenant_users_via_cascade(
    app_client, super_admin_jwt
):
    """PLATFORM SUPER_ADMIN passes /tenant-users gated on
    ADMIN.USERS.VIEW.TENANT via GLOBAL→TENANT cascade (SUPER_ADMIN
    holds .VIEW.GLOBAL)."""
    resp = app_client.get(
        "/api/v1/tenant-users",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200


async def test_ret_8b_tenant_owner_passes_tenant_users_via_direct_grant(
    app_client,
    make_tenant,
    tenant_owner_jwt_factory,
):
    """TENANT OWNER-equivalent user passes /tenant-users via direct
    ADMIN.USERS.VIEW.TENANT grant (no cascade needed; the gate's tuple
    matches the held grant exactly).

    Fixture order matters: ``make_tenant`` listed FIRST so it tears
    down LAST (after every fixture the factory depends on, which all
    insert rows referencing tenant_id). Listing ``make_tenant`` after
    the factory triggers FK violations on teardown because the
    factory's ``make_tenant_user`` rows still reference the tenant
    being deleted.
    """
    tenant = await make_tenant(name="RET8-Tenant")
    jwt = await tenant_owner_jwt_factory(tenant.id)
    resp = app_client.get(
        "/api/v1/tenant-users",
        headers=_auth(jwt),
    )
    assert resp.status_code == 200
