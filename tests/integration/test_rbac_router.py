"""Integration tests for RBAC read endpoints (Step 6.1).

Real Postgres, real schema, real router via FastAPI's TestClient.
JWTs minted via Step 2.1's ``make_test_jwt``. Mirrors the shape used
by ``test_tenant_users_router.py`` and ``test_platform_users_router.py``.

Test ID convention:
  R*  E1 ``GET /api/v1/roles``                       (8 tests)
  P*  E2 ``GET /api/v1/permissions``                  (4 tests)
  RP* E3 ``GET /api/v1/roles/{id}/permissions``       (3 tests)
  D*  E7 ``GET /api/v1/roles/{id}`` (Step 6.18.2)     (8 tests)
  M*  E6 ``GET /api/v1/permission-matrix``            (6 tests)
  A*  Auth                                            (1 test)
  H*  Hidden-fields                                   (2 tests)
                                                     ----
                                                       32

Eleven LOAD-BEARING tests:
  R2  TENANT JWT returns empty platform_roles block (audience filter on E1)
  R4  user_count correlated subquery scopes per-row via .correlate(Role)
  RP3 TENANT JWT requesting PLATFORM role's permissions -> 404
  D1  PLATFORM JWT returns full RoleDetail shape (E7 contract)
  D2  TENANT JWT reads same-audience role (audience filter pass)
  D3  TENANT JWT to PLATFORM role -> 404 (audience filter deny on E7)
  D5  E7 permissions[] embeds display labels via 4 LEFT JOINs
  D6  PLATFORM role available_permissions CAN include GLOBAL (no scope filter)
  D7  TENANT role available_permissions excludes GLOBAL (LD2 audience-scope filter)
  M2  E6 cells/roles position alignment invariant (M1, M2)
  M3  E6 TENANT JWT filters role columns (M5)

The DB is in a partially-seeded state when these run (Step 3.5's loader
or partial state from prior runs). Tests that count rows across the
catalogue use ``>=`` on fixture-created entities rather than absolute
totals so they're robust to that.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings
from admin_backend.db.session import get_tenant_session
from admin_backend.main import create_app
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)


# Step 6.6 / 6.7 amendment: module sort basis is ``lookups.display_order``
# (see PermissionsRepo / PermissionMatrixRepo for the SQL change at 6.6).
# The seed data for ``list_name='module_code'`` defines this order;
# mirroring it here keeps the test contract aligned with the live SQL.
# Step 6.7's migration (`2fdc4bc9f4cb`) re-ordered to match the locked
# screenshot sequence: ROOS, GOAL_CONSOLE, PRICING_OS, PERISHABLES,
# PROMOTIONS, ADMIN. ROOS retired from Python vocabulary 2026-05-12;
# the seed loader's --reset deletes the ROOS lookups row so local DB
# carries 5 rows at display_order 2-6 (no renumber — aligned with
# cloud per operator decision). Values below mirror live local DB
# verbatim. If the seed changes again, update this map.
_MODULE_DISPLAY_ORDER: dict[str, int] = {
    "GOAL_CONSOLE": 2,
    "PRICING_OS": 3,
    "PERISHABLES_ASSISTANT": 4,
    "PROMOTIONS_ASSISTANT": 5,
    "ADMIN": 6,
}


def _enum_ordinal(enum_cls: Any, value: str) -> int:
    """Return the position of ``value`` in ``enum_cls``'s declaration
    order. Mirrors how Postgres orders enum columns natively (enum
    ordinal, not string-alphabetic). Used in default-sort assertions
    for resource/action/scope (which still sort by enum ordinal in
    the post-Step-6.6 SQL).
    """
    return list(enum_cls).index(enum_cls(value))


def _permission_sort_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
    """Compute the sort key Postgres uses on permissions rows.

    Module: ``lookups.display_order`` (post-Step-6.6 contract — see
    PermissionsRepo / PermissionMatrixRepo). Resource/action/scope:
    enum ordinal (unchanged from Step 6.1). Tiebreaker: code, then id
    (matches the SQL's stable secondary sort).
    """
    return (
        _MODULE_DISPLAY_ORDER.get(row["module"], 999),
        _enum_ordinal(PermissionResource, row["resource"]),
        _enum_ordinal(PermissionAction, row["action"]),
        _enum_ordinal(PermissionScope, row["scope"]),
        row.get("code", ""),
        row["id"],
    )


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,  # type: ignore[no-any-unimported]
    session_factory: Any,  # type: ignore[no-any-unimported]
) -> Iterator[TestClient]:
    """TestClient against a real app with real engine/session_factory.

    Bypasses the lifespan (would re-construct an engine in a different
    event loop than the test). Mirrors the pattern from the other
    router-test modules.
    """
    from admin_backend.auth.stub import StubAuthClient

    app_obj = create_app()
    app_obj.state.settings = settings
    app_obj.state.engine = engine
    app_obj.state.session_factory = session_factory
    app_obj.state.auth_client = StubAuthClient(settings)
    with TestClient(app_obj) as client:
        yield client


def _platform_jwt(settings: Settings) -> str:
    return make_test_jwt(
        settings, user_id=uuid.uuid4(), user_type="PLATFORM"
    )


def _tenant_jwt(settings: Settings, tenant_id: UUID) -> str:
    return make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="TENANT",
        tenant_id=tenant_id,
    )


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


# Note: the local helpers ``_insert_active_platform_assignment`` and
# ``_delete_assignments_by_id`` were retired at Step 6.8.3 in favour of
# the conftest fixture ``make_platform_user_role_assignment``, which
# tracks IDs and DELETEs at teardown automatically. R4 below is the
# only test that needed them; it now uses the fixture directly.


# =============================================================================
# E1: GET /api/v1/roles  (R1-R8)
# =============================================================================


# ---- R1: pre-grouped envelope + user_count present ------------------------
async def test_r1_envelope_pre_grouped_with_user_count(
    app_client, settings, make_role
):
    """E1's response envelope is ``{platform_roles, tenant_roles}``,
    each block ``{items, total}``. Each item carries user_count.
    Audit-actor / hidden fields absent.
    """
    p = await make_role(audience="PLATFORM", name="R1 Platform")
    t = await make_role(audience="TENANT", name="R1 Tenant")
    resp = app_client.get(
        "/api/v1/roles",
        params={"limit": 200},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"platform_roles", "tenant_roles"}
    for block in (body["platform_roles"], body["tenant_roles"]):
        assert set(block.keys()) == {"items", "total"}

    p_match = [i for i in body["platform_roles"]["items"] if i["id"] == str(p.id)]
    t_match = [i for i in body["tenant_roles"]["items"] if i["id"] == str(t.id)]
    assert len(p_match) == 1
    assert len(t_match) == 1
    item = p_match[0]
    assert set(item.keys()) == {
        "id", "name", "code", "description", "status",
        "is_system", "user_count", "created_at", "updated_at",
    }
    assert item["user_count"] == 0
    assert item["status"] == "ACTIVE"
    assert item["is_system"] is False
    # No audience field on items (implied by container key).
    assert "audience" not in item


# ---- R2: TENANT JWT returns empty platform_roles block (LOAD-BEARING) ----
async def test_r2_tenant_jwt_platform_block_empty(
    app_client, settings, make_tenant, make_role
):
    """LOAD-BEARING: TENANT JWT sees platform_roles always empty.

    The audience filter is the app-layer parallel of RLS for
    platform-global tables. Without it, a TENANT user could see
    PLATFORM-audience role names — a leak of Ithina staff role
    structure.
    """
    tenant = await make_tenant(name="R2-T")
    p_role = await make_role(audience="PLATFORM", name="R2 PlatformRole")
    t_role = await make_role(audience="TENANT", name="R2 TenantRole")

    resp = app_client.get(
        "/api/v1/roles",
        params={"limit": 200},
        headers=_auth(_tenant_jwt(settings, tenant.id)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["platform_roles"]["items"] == []
    assert body["platform_roles"]["total"] == 0
    # The created PLATFORM role must NOT appear anywhere in the response
    p_ids = [i["id"] for i in body["platform_roles"]["items"]]
    t_ids = [i["id"] for i in body["tenant_roles"]["items"]]
    assert str(p_role.id) not in p_ids
    assert str(p_role.id) not in t_ids
    # The created TENANT role does appear under tenant_roles
    assert str(t_role.id) in t_ids


# ---- R3: PLATFORM JWT sees both audiences ---------------------------------
async def test_r3_platform_jwt_sees_both_audiences(
    app_client, settings, make_role
):
    p_role = await make_role(audience="PLATFORM", name="R3 P")
    t_role = await make_role(audience="TENANT", name="R3 T")
    resp = app_client.get(
        "/api/v1/roles",
        params={"limit": 200},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    body = resp.json()
    p_ids = {i["id"] for i in body["platform_roles"]["items"]}
    t_ids = {i["id"] for i in body["tenant_roles"]["items"]}
    assert str(p_role.id) in p_ids
    assert str(t_role.id) in t_ids
    assert body["platform_roles"]["total"] >= 1
    assert body["tenant_roles"]["total"] >= 1


# ---- R4: user_count correlates per-role (LOAD-BEARING) -------------------
async def test_r4_user_count_aggregate_correlates_per_role(
    app_client,
    settings,
    make_role,
    make_platform_user,
    make_platform_user_role_assignment,
):
    """LOAD-BEARING: ``.correlate(Role)`` scopes the count per outer
    row.

    Without ``.correlate(Role)``, the count collapses to a platform-
    wide aggregate and EVERY role would show the same user_count.
    The same trap as Step 3.3 L9 / Step 5.3 L11.

    Setup: 2 PLATFORM-audience roles, 3 platform users. Role-A gets
    2 ACTIVE assignments; Role-B gets 1. Assert distinct user_counts.

    Step 6.8.3 update: switched from the now-retired local helper
    ``_insert_active_platform_assignment`` to the conftest factory
    ``make_platform_user_role_assignment`` (which tracks IDs and
    DELETEs at teardown automatically — no manual cleanup loop needed).
    """
    role_a = await make_role(audience="PLATFORM", name="R4 RoleA")
    role_b = await make_role(audience="PLATFORM", name="R4 RoleB")
    pu1 = await make_platform_user(email="r4-pu1@r4.test")
    pu2 = await make_platform_user(email="r4-pu2@r4.test")
    pu3 = await make_platform_user(email="r4-pu3@r4.test")

    await make_platform_user_role_assignment(
        platform_user_id=pu1.id, role_id=role_a.id,
    )
    await make_platform_user_role_assignment(
        platform_user_id=pu2.id, role_id=role_a.id,
    )
    await make_platform_user_role_assignment(
        platform_user_id=pu3.id, role_id=role_b.id,
    )

    resp = app_client.get(
        "/api/v1/roles",
        params={"limit": 200},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    items = resp.json()["platform_roles"]["items"]
    a_match = [i for i in items if i["id"] == str(role_a.id)]
    b_match = [i for i in items if i["id"] == str(role_b.id)]
    assert len(a_match) == 1
    assert len(b_match) == 1
    assert a_match[0]["user_count"] == 2
    assert b_match[0]["user_count"] == 1


# ---- R5: status filter defaults to ACTIVE --------------------------------
async def test_r5_status_filter_default_active(
    app_client, settings, make_role
):
    """status defaults to ACTIVE when the param is omitted."""
    active_role = await make_role(audience="TENANT", name="R5 Active", status="ACTIVE")
    inactive_role = await make_role(
        audience="TENANT", name="R5 Inactive", status="INACTIVE"
    )

    # Default (no status param) -> ACTIVE only
    resp = app_client.get(
        "/api/v1/roles",
        params={"limit": 200},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    t_ids = {i["id"] for i in resp.json()["tenant_roles"]["items"]}
    assert str(active_role.id) in t_ids
    assert str(inactive_role.id) not in t_ids

    # Explicit status=INACTIVE returns the inactive one
    resp2 = app_client.get(
        "/api/v1/roles",
        params={"status": "INACTIVE", "limit": 200},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp2.status_code == 200
    t_ids2 = {i["id"] for i in resp2.json()["tenant_roles"]["items"]}
    assert str(inactive_role.id) in t_ids2
    assert str(active_role.id) not in t_ids2


# ---- R6: search q ILIKE across name / code / description -----------------
async def test_r6_search_q_ilike(app_client, settings, make_role):
    """The ``q`` param matches case-insensitively across name, code,
    and description.
    """
    by_name = await make_role(
        audience="TENANT",
        name="R6Uniq Display Name",
        code="R6UNIQ_BY_NAME",
    )
    by_code = await make_role(
        audience="TENANT",
        name="Other Name",
        code="R6UNIQ_BY_CODE",
    )
    by_desc = await make_role(
        audience="TENANT",
        name="Yet Another",
        code="R6_UNIQ_DESC",
        description="contains the r6uniq token",
    )
    not_match = await make_role(
        audience="TENANT", name="Unrelated R6", code="R6_OTHER_CODE"
    )

    resp = app_client.get(
        "/api/v1/roles",
        params={"q": "r6uniq", "limit": 200},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["tenant_roles"]["items"]}
    assert str(by_name.id) in ids
    assert str(by_code.id) in ids
    assert str(by_desc.id) in ids
    assert str(not_match.id) not in ids


# ---- R7: invalid sort returns 400 (not 500) ------------------------------
def test_r7_invalid_sort_returns_400(app_client, settings):
    resp = app_client.get(
        "/api/v1/roles",
        params={"sort": "definitely_not_a_real_sort"},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_SORT_KEY"


# ---- R8: is_system filter ------------------------------------------------
async def test_r8_is_system_filter(app_client, settings, make_role):
    sys_role = await make_role(
        audience="TENANT", name="R8 System", is_system=True
    )
    ord_role = await make_role(
        audience="TENANT", name="R8 Ordinary", is_system=False
    )

    resp = app_client.get(
        "/api/v1/roles",
        params={"is_system": "true", "limit": 200},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["tenant_roles"]["items"]}
    assert str(sys_role.id) in ids
    assert str(ord_role.id) not in ids


# =============================================================================
# E2: GET /api/v1/permissions  (P1-P4)
# =============================================================================


# ---- P1: envelope + default sort -----------------------------------------
def test_p1_envelope_and_default_sort(app_client, settings):
    """E2 returns ``{items, pagination}`` with default sort
    module/resource/action/scope ASC.
    """
    resp = app_client.get(
        "/api/v1/permissions",
        params={"limit": 200},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items", "pagination"}
    assert set(body["pagination"].keys()) == {"total", "offset", "limit"}
    # Default sort: module ASC, then resource ASC, then action ASC,
    # then scope ASC. Verify monotonic ordering on the seeded data
    # (only ADMIN, PERISHABLES_ASSISTANT, PRICING_OS, PROMOTIONS_ASSISTANT
    # post-cleanup).
    items = body["items"]
    assert len(items) >= 1
    # Postgres sorts enum columns by enum ordinal (declaration order),
    # not string-alphabetic. Verify against that contract.
    ordinals = [_permission_sort_tuple(i) for i in items]
    assert ordinals == sorted(ordinals)


# ---- P2: module filter ----------------------------------------------------
async def test_p2_module_filter(
    app_client, settings, make_permission
):
    """Filter by module returns only permissions in that module.

    Uses tuples not present in the seeded catalogue so the unique
    (module,resource,action,scope) and unique code constraints don't
    fire. STORES and MARKDOWNS.AUDIT.TENANT are unseeded slots
    post Step 6.1.
    """
    perm_admin = await make_permission(
        module="ADMIN", resource="STORES", action="VIEW", scope="STORE",
    )
    perm_pricing = await make_permission(
        module="PRICING_OS", resource="MARKDOWNS", action="AUDIT",
        scope="TENANT",
    )

    resp = app_client.get(
        "/api/v1/permissions",
        params={"module": "ADMIN", "limit": 200},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = {i["id"] for i in items}
    assert str(perm_admin.id) in ids
    assert str(perm_pricing.id) not in ids
    # All returned items have module=ADMIN
    assert all(i["module"] == "ADMIN" for i in items)


# ---- P3: scope filter ----------------------------------------------------
async def test_p3_scope_filter(app_client, settings, make_permission):
    """Filter by scope returns only permissions at that scope.

    Post Step 6.1 the locked vocabulary is GLOBAL/TENANT/STORE only.
    Uses unseeded (module,resource,action,scope) tuples so the unique
    constraints don't fire — STORES.OVERRIDE.GLOBAL and
    STORES.EXECUTE.STORE are unseeded slots post-cleanup.
    """
    p_global = await make_permission(
        module="ADMIN", resource="STORES", action="OVERRIDE", scope="GLOBAL",
    )
    p_store = await make_permission(
        module="ADMIN", resource="STORES", action="EXECUTE", scope="STORE",
    )
    resp = app_client.get(
        "/api/v1/permissions",
        params={"scope": "GLOBAL", "limit": 200},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = {i["id"] for i in items}
    assert str(p_global.id) in ids
    assert str(p_store.id) not in ids
    assert all(i["scope"] == "GLOBAL" for i in items)


# ---- P4: TENANT JWT sees full catalogue ----------------------------------
async def test_p4_tenant_jwt_sees_full_catalogue(
    app_client, settings, make_tenant, make_permission
):
    """Permission catalogue is reference data — no audience filter on E2.

    TENANT JWTs see the full catalogue (the same set as PLATFORM JWTs).
    """
    tenant = await make_tenant(name="P4-T")
    # Use an unseeded (module,resource,action,scope) tuple so the
    # unique (module,resource,action,scope) and unique code
    # constraints don't fire.
    perm = await make_permission(
        module="ADMIN", resource="STORES", action="EXECUTE", scope="STORE",
    )

    p_resp = app_client.get(
        "/api/v1/permissions",
        params={"limit": 200},
        headers=_auth(_platform_jwt(settings)),
    )
    t_resp = app_client.get(
        "/api/v1/permissions",
        params={"limit": 200},
        headers=_auth(_tenant_jwt(settings, tenant.id)),
    )
    assert p_resp.status_code == 200
    assert t_resp.status_code == 200
    p_ids = {i["id"] for i in p_resp.json()["items"]}
    t_ids = {i["id"] for i in t_resp.json()["items"]}
    assert str(perm.id) in p_ids
    assert str(perm.id) in t_ids
    # Both user types see the same set
    assert p_ids == t_ids


# =============================================================================
# E3: GET /api/v1/roles/{role_id}/permissions  (RP1-RP3)
# =============================================================================


# ---- RP1: returns parent-echo envelope + items -----------------------------
async def test_rp1_returns_role_permissions_with_parent_echo(
    app_client,
    settings,
    make_role,
    make_permission,
    make_role_permission,
):
    """E3 returns ``{role_id, role_name, items}``, items sorted
    module/resource/action/scope ASC.
    """
    role = await make_role(
        audience="TENANT", name="RP1 Owner", code="RP1_OWNER",
    )
    # Unseeded slots — ORG_NODES at STORE scope with EXECUTE/AUDIT
    # actions is not in the seed catalog (and unlikely to be added).
    perm1 = await make_permission(
        module="ADMIN", resource="ORG_NODES", action="EXECUTE", scope="STORE",
    )
    perm2 = await make_permission(
        module="ADMIN", resource="ORG_NODES", action="AUDIT", scope="STORE",
    )
    await make_role_permission(role_id=role.id, permission_id=perm1.id)
    await make_role_permission(role_id=role.id, permission_id=perm2.id)

    resp = app_client.get(
        f"/api/v1/roles/{role.id}/permissions",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"role_id", "role_name", "items"}
    assert body["role_id"] == str(role.id)
    assert body["role_name"] == "RP1 Owner"
    ids = {i["id"] for i in body["items"]}
    assert str(perm1.id) in ids
    assert str(perm2.id) in ids
    # Sort: module/resource/action/scope ASC (Postgres enum-ordinal,
    # not string-alphabetic).
    ordinals = [_permission_sort_tuple(i) for i in body["items"]]
    assert ordinals == sorted(ordinals)


# ---- RP2: unknown role_id -> 404 -----------------------------------------
def test_rp2_unknown_role_returns_404(app_client, settings):
    fake = uuid.uuid4()
    resp = app_client.get(
        f"/api/v1/roles/{fake}/permissions",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "ROLE_NOT_FOUND"
    assert body["message"] == "Role not found"
    assert body["details"] is None


# ---- RP3: TENANT JWT to PLATFORM role -> 404 (LOAD-BEARING) --------------
async def test_rp3_tenant_jwt_platform_role_returns_404(
    app_client, settings, make_tenant, make_role
):
    """LOAD-BEARING: TENANT JWT requesting a PLATFORM-audience role's
    id receives 404 ROLE_NOT_FOUND, not 403 (audience filter applied
    at the app layer; same anti-information-disclosure intent as RLS-
    as-404 per D-17).

    Without this gate, a TENANT JWT could probe whether a PLATFORM
    role id exists by inspecting the response shape.
    """
    tenant = await make_tenant(name="RP3-T")
    platform_role = await make_role(
        audience="PLATFORM", name="RP3 PlatformRole",
    )

    resp = app_client.get(
        f"/api/v1/roles/{platform_role.id}/permissions",
        headers=_auth(_tenant_jwt(settings, tenant.id)),
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "ROLE_NOT_FOUND"

    # Sanity: PLATFORM JWT to the SAME role works (it's the audience
    # filter that blocked, not a bug on our side).
    resp_p = app_client.get(
        f"/api/v1/roles/{platform_role.id}/permissions",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp_p.status_code == 200
    assert resp_p.json()["role_id"] == str(platform_role.id)


# =============================================================================
# E7: GET /api/v1/roles/{role_id}  (D1-D8)  Step 6.18.2
#
# Self-contained role detail for the edit screen. Returns RoleDetail:
# role metadata + held permissions (with display labels) + available
# permissions (catalogue minus held; TENANT-audience roles exclude
# GLOBAL-scope per LD2). Six LOAD-BEARING tests below assert the
# security and contract invariants.
# =============================================================================


# ---- D1: PLATFORM JWT returns full RoleDetail shape (LOAD-BEARING) -------
async def test_d1_platform_jwt_returns_full_role_detail(
    app_client, settings, make_role
):
    """LOAD-BEARING: PLATFORM JWT GET /roles/{platform_role_id} returns
    200 with the full RoleDetail envelope.

    Locked field set per LD1: id, name, code, description, audience,
    status, is_system, user_count, created_at, updated_at, permissions,
    available_permissions.
    """
    role = await make_role(
        audience="PLATFORM",
        name="D1 PlatformRole",
        description="Detail-shape test role",
    )
    resp = app_client.get(
        f"/api/v1/roles/{role.id}",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "id",
        "name",
        "code",
        "description",
        "audience",
        "status",
        "is_system",
        "user_count",
        "created_at",
        "updated_at",
        "permissions",
        "available_permissions",
    }
    assert body["id"] == str(role.id)
    assert body["audience"] == "PLATFORM"
    assert body["status"] == "ACTIVE"
    assert body["is_system"] is False
    assert isinstance(body["permissions"], list)
    assert isinstance(body["available_permissions"], list)


# ---- D2: TENANT JWT can read a TENANT-audience role (LOAD-BEARING) -------
async def test_d2_tenant_jwt_reads_tenant_role(
    app_client, settings, make_tenant, make_role
):
    """LOAD-BEARING: TENANT JWT GET /roles/{tenant_role_id} succeeds
    with audience='TENANT'.

    Same-audience reads pass the audience filter; different from D3
    which asserts cross-audience denial.
    """
    tenant = await make_tenant(name="D2-T")
    role = await make_role(audience="TENANT", name="D2 TenantRole")
    resp = app_client.get(
        f"/api/v1/roles/{role.id}",
        headers=_auth(_tenant_jwt(settings, tenant.id)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(role.id)
    assert body["audience"] == "TENANT"


# ---- D3: TENANT JWT to a PLATFORM role -> 404 (LOAD-BEARING) -------------
async def test_d3_tenant_jwt_platform_role_returns_404(
    app_client, settings, make_tenant, make_role
):
    """LOAD-BEARING: TENANT JWT requesting a PLATFORM-audience role's
    id receives 404 ROLE_NOT_FOUND, not 403 (audience filter applied
    at the app layer; same anti-information-disclosure intent as RLS-
    as-404 per D-17 / LD5).

    Without this gate, a TENANT JWT could probe whether a PLATFORM
    role id exists.
    """
    tenant = await make_tenant(name="D3-T")
    platform_role = await make_role(
        audience="PLATFORM", name="D3 PlatformRole"
    )
    resp = app_client.get(
        f"/api/v1/roles/{platform_role.id}",
        headers=_auth(_tenant_jwt(settings, tenant.id)),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "ROLE_NOT_FOUND"

    # Sanity: PLATFORM JWT to the same role works.
    resp_p = app_client.get(
        f"/api/v1/roles/{platform_role.id}",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp_p.status_code == 200
    assert resp_p.json()["id"] == str(platform_role.id)


# ---- D4: unknown UUID -> 404 ---------------------------------------------
def test_d4_unknown_role_returns_404(app_client, settings):
    fake = uuid.uuid4()
    resp = app_client.get(
        f"/api/v1/roles/{fake}",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "ROLE_NOT_FOUND"
    assert body["details"] is None


# ---- D5: held permissions carry display labels (LOAD-BEARING) ------------
async def test_d5_held_permissions_carry_labels(
    app_client,
    settings,
    make_role,
    make_permission,
    make_role_permission,
):
    """LOAD-BEARING: permissions[] embeds full PermissionDetail tuples
    with module_label / resource_label / action_label / scope_label
    populated from the 4 LEFT JOINs on core.lookups.

    Spot-checks known label mappings (defensive: every label resolves
    through the JOIN, no fallback-to-code paths firing for seeded
    rows).
    """
    role = await make_role(audience="TENANT", name="D5 Role")
    # Pick an unseeded tuple so the role's grant set is exactly {perm}.
    perm = await make_permission(
        module="ADMIN", resource="STORES", action="AUDIT", scope="TENANT",
    )
    await make_role_permission(role_id=role.id, permission_id=perm.id)

    resp = app_client.get(
        f"/api/v1/roles/{role.id}",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    body = resp.json()
    held = body["permissions"]
    assert len(held) == 1
    item = held[0]
    assert set(item.keys()) == {
        "id",
        "module",
        "module_label",
        "resource",
        "resource_label",
        "action",
        "action_label",
        "scope",
        "scope_label",
        "code",
        "description",
    }
    assert item["id"] == str(perm.id)
    assert item["module"] == "ADMIN"
    assert item["module_label"] == "Admin"
    assert item["resource"] == "STORES"
    assert item["resource_label"] == "Stores"
    assert item["action"] == "AUDIT"
    assert item["action_label"] == "Audit"
    assert item["scope"] == "TENANT"
    assert item["scope_label"] == "Tenant"
    assert item["code"] == "ADMIN.STORES.AUDIT.TENANT"


# ---- D6: PLATFORM role's available_permissions CAN include GLOBAL (LB) ---
async def test_d6_platform_role_available_includes_global(
    app_client,
    settings,
    make_role,
    make_permission,
):
    """LOAD-BEARING: PLATFORM-audience role's ``available_permissions``
    contains GLOBAL-scope rows (no scope filter applied per LD2).

    Setup: create a PLATFORM role with zero held + a fresh
    GLOBAL-scope permission. Assert the GLOBAL-scope row appears in
    available_permissions.
    """
    role = await make_role(audience="PLATFORM", name="D6 PlatformRole")
    perm_global = await make_permission(
        module="ADMIN", resource="STORES", action="OVERRIDE", scope="GLOBAL",
    )

    resp = app_client.get(
        f"/api/v1/roles/{role.id}",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    body = resp.json()
    # The fresh role holds nothing.
    assert body["permissions"] == []
    available_ids = {p["id"] for p in body["available_permissions"]}
    assert str(perm_global.id) in available_ids
    # GLOBAL-scope rows present in available_permissions (PLATFORM
    # role: no scope filter).
    global_scopes = [
        p for p in body["available_permissions"] if p["scope"] == "GLOBAL"
    ]
    assert len(global_scopes) >= 1


# ---- D7: TENANT role's available_permissions excludes GLOBAL (LB) -------
async def test_d7_tenant_role_available_excludes_global(
    app_client,
    settings,
    make_role,
    make_permission,
):
    """LOAD-BEARING: TENANT-audience role's ``available_permissions``
    excludes ``scope='GLOBAL'`` rows (LD2 audience-scope coherence).

    Setup: create a TENANT role + a GLOBAL-scope permission. Assert
    the GLOBAL-scope row is NOT in available_permissions, while
    non-GLOBAL rows are.
    """
    role = await make_role(audience="TENANT", name="D7 TenantRole")
    # GLOBAL-scope row: must NOT appear in TENANT role's available set.
    perm_global = await make_permission(
        module="ADMIN", resource="STORES", action="OVERRIDE", scope="GLOBAL",
    )
    # STORE-scope row: must appear (non-GLOBAL, unheld).
    perm_store = await make_permission(
        module="ADMIN", resource="STORES", action="EXECUTE", scope="STORE",
    )

    resp = app_client.get(
        f"/api/v1/roles/{role.id}",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    body = resp.json()
    available_ids = {p["id"] for p in body["available_permissions"]}
    available_scopes = {p["scope"] for p in body["available_permissions"]}

    # GLOBAL excluded.
    assert str(perm_global.id) not in available_ids
    assert "GLOBAL" not in available_scopes

    # Non-GLOBAL included.
    assert str(perm_store.id) in available_ids


# ---- D8: user_count tracks active assignments ----------------------------
async def test_d8_user_count_tracks_active_assignments(
    app_client,
    settings,
    make_role,
    make_platform_user,
    make_platform_user_role_assignment,
):
    """user_count reflects active assignments referencing this role.

    Setup: a fresh PLATFORM role + 2 platform users + 2 ACTIVE
    assignments. Assert user_count == 2. Mirrors R4's correlated-
    subquery sanity check at the single-role surface.
    """
    role = await make_role(audience="PLATFORM", name="D8 Role")
    pu1 = await make_platform_user(email="d8-pu1@d8.test")
    pu2 = await make_platform_user(email="d8-pu2@d8.test")
    await make_platform_user_role_assignment(
        platform_user_id=pu1.id, role_id=role.id,
    )
    await make_platform_user_role_assignment(
        platform_user_id=pu2.id, role_id=role.id,
    )

    resp = app_client.get(
        f"/api/v1/roles/{role.id}",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    assert resp.json()["user_count"] == 2


# =============================================================================
# E6: GET /api/v1/permission-matrix  (M1-M5)
# =============================================================================


# ---- M1: envelope + dimensions -------------------------------------------
def test_m1_matrix_envelope_and_dimensions(app_client, settings):
    """E6 returns ``{roles, rows}`` (no items wrapper, no pagination).

    Each row carries 4 enum codes + 4 display labels + cells.
    """
    resp = app_client.get(
        "/api/v1/permission-matrix",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"roles", "rows"}
    if body["rows"]:
        row = body["rows"][0]
        assert set(row.keys()) == {
            "id",
            "module",
            "module_label",
            "resource",
            "resource_label",
            "action",
            "action_label",
            "scope",
            "scope_label",
            "cells",
        }
        assert isinstance(row["cells"], list)
    if body["roles"]:
        col = body["roles"][0]
        assert set(col.keys()) == {"id", "name", "audience"}


# ---- M2: cells aligned with roles array (LOAD-BEARING) -------------------
async def test_m2_cells_aligned_with_roles_array(
    app_client,
    settings,
    make_role,
    make_permission,
    make_role_permission,
):
    """LOAD-BEARING: M1/M2 invariants — len(cells) == len(roles) for
    every row, and cells[i] is the grant for roles[i].

    Setup: a fresh role + a fresh permission with a grant linking them.
    Find the role in the matrix's roles array; assert the cell at that
    column index is True for the corresponding row, False for any
    other (matrix-pre-existing) permission row that doesn't grant
    this role.
    """
    role = await make_role(audience="TENANT", name="M2 IsolRole")
    # Unseeded slot — STORES.AUDIT.TENANT is not in the seeded catalogue.
    perm = await make_permission(
        module="ADMIN", resource="STORES", action="AUDIT", scope="TENANT",
    )
    await make_role_permission(role_id=role.id, permission_id=perm.id)

    resp = app_client.get(
        "/api/v1/permission-matrix",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    body = resp.json()
    role_ids = [c["id"] for c in body["roles"]]
    assert str(role.id) in role_ids
    role_idx = role_ids.index(str(role.id))

    # M2: every row's cells[] is the same length as the roles array.
    for row in body["rows"]:
        assert len(row["cells"]) == len(body["roles"])

    # The matrix row matching our created permission has cells[role_idx] = True.
    perm_rows = [r for r in body["rows"] if r["id"] == str(perm.id)]
    assert len(perm_rows) == 1
    perm_row = perm_rows[0]
    assert perm_row["cells"][role_idx] is True

    # Any other rows do not grant the new role.
    other_rows = [r for r in body["rows"] if r["id"] != str(perm.id)]
    for r in other_rows:
        assert r["cells"][role_idx] is False


# ---- M3: TENANT JWT filters role columns (LOAD-BEARING) ------------------
async def test_m3_tenant_jwt_filters_role_columns(
    app_client, settings, make_tenant, make_role
):
    """LOAD-BEARING: TENANT JWT response contains audience='TENANT'
    columns only; cells[] arrays correspondingly shorter.

    Mechanism: the audience filter on the matrix repo's role load
    excludes PLATFORM rows. Each row's cells[] is built from the
    filtered roles array, so a TENANT JWT's row.cells aligns with
    only TENANT-audience roles.
    """
    tenant = await make_tenant(name="M3-T")
    p_role = await make_role(audience="PLATFORM", name="M3 PlatformRole")
    t_role = await make_role(audience="TENANT", name="M3 TenantRole")

    resp_p = app_client.get(
        "/api/v1/permission-matrix",
        headers=_auth(_platform_jwt(settings)),
    )
    resp_t = app_client.get(
        "/api/v1/permission-matrix",
        headers=_auth(_tenant_jwt(settings, tenant.id)),
    )
    assert resp_p.status_code == 200
    assert resp_t.status_code == 200
    p_body = resp_p.json()
    t_body = resp_t.json()

    p_role_ids = [r["id"] for r in p_body["roles"]]
    t_role_ids = [r["id"] for r in t_body["roles"]]

    # PLATFORM caller sees both audiences; TENANT does not.
    assert str(p_role.id) in p_role_ids
    assert str(t_role.id) in p_role_ids
    assert str(p_role.id) not in t_role_ids
    assert str(t_role.id) in t_role_ids

    # Every column in the TENANT response has audience='TENANT'.
    assert all(c["audience"] == "TENANT" for c in t_body["roles"])

    # cells[] alignment: PLATFORM response wider than TENANT (or
    # equal-empty in the degenerate case where only TENANT rows exist —
    # this fixture creates one PLATFORM and one TENANT, so PLATFORM
    # is strictly wider).
    assert len(p_body["roles"]) > len(t_body["roles"])
    if p_body["rows"]:
        for row in p_body["rows"]:
            assert len(row["cells"]) == len(p_body["roles"])
    if t_body["rows"]:
        for row in t_body["rows"]:
            assert len(row["cells"]) == len(t_body["roles"])


# ---- M4: display labels resolved from lookups ----------------------------
def test_m4_display_labels_join_from_lookups(app_client, settings):
    """The four ``*_label`` fields per row come from JOIN against
    ``lookups``. The 25 rows seeded by Step 6.1's lookups migration
    cover the locked vocabulary; every row has all four labels
    populated and non-NULL.
    """
    resp = app_client.get(
        "/api/v1/permission-matrix",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) >= 1
    expected_module_labels = {
        "ADMIN": "Admin",
        "PRICING_OS": "Pricing OS",
        "PERISHABLES_ASSISTANT": "Perishables Assistant",
        "PROMOTIONS_ASSISTANT": "Promotions Assistant",
    }
    expected_action_labels = {
        "VIEW": "View",
        "CONFIGURE": "Configure",
        "AUDIT": "Audit",
        "APPROVE": "Approve",
        "OVERRIDE": "Override",
        "EXECUTE": "Execute",
    }
    expected_scope_labels = {
        "GLOBAL": "Global",
        "TENANT": "Tenant",
        "STORE": "Store",
    }
    for r in rows:
        assert r["module_label"]
        assert r["resource_label"]
        assert r["action_label"]
        assert r["scope_label"]
        # Spot-check known mappings (defensive: every label resolves
        # through the JOIN, no fallback-to-code paths firing).
        assert r["module_label"] == expected_module_labels[r["module"]]
        assert r["action_label"] == expected_action_labels[r["action"]]
        assert r["scope_label"] == expected_scope_labels[r["scope"]]


# ---- M5: row order module/resource/action/scope ASC ----------------------
def test_m5_row_order_module_resource_action_scope(app_client, settings):
    """M4 invariant: rows ordered module/resource/action/scope ASC.

    Postgres sorts enum columns by enum ordinal (declaration order),
    not string-alphabetic; the test asserts the same contract.
    """
    resp = app_client.get(
        "/api/v1/permission-matrix",
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    ordinals = [_permission_sort_tuple(r) for r in rows]
    assert ordinals == sorted(ordinals)


# ---- M6: raw SQL schema qualification (LOAD-BEARING regression for Step 6.5.1) -------
async def test_m6_raw_sql_works_with_clobbered_search_path(
    session_factory, platform_auth
):
    """LOAD-BEARING regression guard: PermissionMatrixRepo's ``text()``
    SQL must schema-qualify every table reference. The Repo is
    already correct (Step 6.1 originally wrote it that way); this
    test prevents a future regression from undoing the qualification.

    Same shape as ``test_dashboard_router.py::test_x2_*`` — clobber
    search_path to ``public``, call ``get_matrix``, assert success.
    """
    from sqlalchemy import text

    from admin_backend.db.session import get_tenant_session
    from admin_backend.repositories.permission_matrix import PermissionMatrixRepo

    repo = PermissionMatrixRepo()
    async for session in get_tenant_session(platform_auth, session_factory):
        await session.execute(text("SET search_path TO public"))
        await repo.get_matrix(session, audience_filter=None)


# =============================================================================
# Auth (A1)
# =============================================================================


# ---- A1: no JWT -> 401 across all four endpoints -------------------------
def test_a1_no_jwt_returns_401(app_client):
    """All four RBAC endpoints require authentication.

    Without an Authorization header the auth middleware raises
    AuthMissingError and the response is 401 AUTH_MISSING (not 500).
    """
    for path in (
        "/api/v1/roles",
        "/api/v1/permissions",
        f"/api/v1/roles/{uuid.uuid4()}/permissions",
        "/api/v1/permission-matrix",
    ):
        resp = app_client.get(path)
        assert resp.status_code == 401, f"path={path} expected 401"
        assert resp.json()["code"] == "AUTH_MISSING"


# =============================================================================
# Hidden fields (H1, H2)
# =============================================================================


# ---- H1: role response hides audit-actor columns -------------------------
async def test_h1_role_response_hides_audit_actors(
    app_client, settings, make_role
):
    """E1 items must not include any audit-actor / archived-tower fields.

    Same hide-policy as Steps 3.3 / 5.1 / 5.2 (D-13 Pattern (b)).
    """
    role = await make_role(audience="TENANT", name="H1 Test")
    resp = app_client.get(
        "/api/v1/roles",
        params={"limit": 200},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    items = resp.json()["tenant_roles"]["items"]
    matches = [i for i in items if i["id"] == str(role.id)]
    assert len(matches) == 1
    item = matches[0]
    forbidden = {
        "created_by_user_id",
        "created_by_user_type",
        "updated_by_user_id",
        "updated_by_user_type",
        "archived_at",
        "archived_by_user_id",
        "archived_by_user_type",
    }
    assert forbidden.isdisjoint(item.keys())


# ---- H2: permission response carries no audit-actor fields ---------------
def test_h2_permission_response_no_audit_actor_fields(app_client, settings):
    """The permissions table has no audit-actor pairs in its DDL, but
    the response shape should be exactly the documented field set.
    """
    resp = app_client.get(
        "/api/v1/permissions",
        params={"limit": 5},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    if not items:
        pytest.skip("permissions catalogue empty post-cleanup; skipping shape check")
    item = items[0]
    assert set(item.keys()) == {
        "id",
        "module",
        "resource",
        "action",
        "scope",
        "code",
        "description",
        "created_at",
        "updated_at",
    }
