"""Integration tests for the stores router (Step 6.17.2).

Real Postgres, real schema, real RLS, real router via FastAPI's
TestClient. JWTs minted via Step 2.1's ``make_test_jwt``. Mirrors the
shape used by ``test_tenant_users_router.py`` and
``test_tenants_router.py``.

Coverage shape:

  L1-L10: list endpoint
  D1-D4:  detail endpoint
  MG1:    mandatory-gate-discipline anchor

Eight LOAD-BEARING tests (cited by ID in the final report):
  L1 — PLATFORM list happy path returns 25-row envelope.
  L2 — TENANT-A list scoped to TENANT-A only (RLS at router).
  L9 — TENANT JWT without the .TENANT grant -> 403 PERMISSION_DENIED.
  L10 — TENANT OWNER with the .TENANT grant -> own-tenant rows only.
  D1 — Detail under PLATFORM returns 17 fields + tenant_name shape.
  D3 — Cross-tenant detail under TENANT-A returns 404 STORE_NOT_FOUND
        (RLS-as-404 via the anchor dep).
  D4 — Detail under TENANT OWNER: same tenant 200, different tenant 404.
  MG1 — Both endpoints carry the ``__permission_gate__`` marker.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings
from admin_backend.main import create_app


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,
    session_factory: Any,
) -> Iterator[TestClient]:
    """TestClient with engine + session_factory wired onto app.state.

    Bypasses the lifespan so the test event loop owns the engine.
    Mirrors ``test_tenants_router.py``'s pattern.
    """
    from admin_backend.auth.stub import StubAuthClient

    app_obj = create_app()
    app_obj.state.settings = settings
    app_obj.state.engine = engine
    app_obj.state.session_factory = session_factory
    app_obj.state.auth_client = StubAuthClient(settings)
    with TestClient(app_obj) as client:
        yield client


def _tenant_jwt(settings: Settings, tenant_id: UUID) -> str:
    """Random-user TENANT JWT for a given tenant_id. Caller has no
    seeded role assignment row → gate denies the .TENANT check."""
    return make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="TENANT",
        tenant_id=tenant_id,
    )


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


# =============================================================================
# List endpoint (L1-L10)
# =============================================================================


# ---- L1: PLATFORM list happy path + envelope (LOAD-BEARING) -----------------
async def test_l1_list_platform_returns_envelope(
    app_client, make_tenant, make_store, super_admin_jwt,
):
    """PLATFORM session sees all stores; envelope is {items, pagination}
    per D-30; each item has the slim 8-field shape."""
    t = await make_tenant(name="L1-T")
    s = await make_store(tenant_id=t.id, name="L1-Store")

    resp = app_client.get(
        "/api/v1/stores?limit=100",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    assert "pagination" in body
    assert set(body["pagination"].keys()) == {"total", "offset", "limit"}

    matches = [i for i in body["items"] if i["id"] == str(s.id)]
    assert len(matches) == 1
    item = matches[0]
    assert set(item.keys()) == {
        "id",
        "tenant_id",
        "tenant_name",
        "name",
        "store_code",
        "country",
        "status",
        "created_at",
    }
    assert item["tenant_name"] == "L1-T"
    assert item["tenant_id"] == str(t.id)


# ---- L2: TENANT JWT list scoped by RLS (LOAD-BEARING) -----------------------
async def test_l2_list_tenant_scoped_by_rls(
    app_client, make_tenant, make_store, tenant_owner_jwt_factory,
):
    """OWNER-side TENANT JWT lists only own-tenant stores."""
    t_a = await make_tenant(name="L2-A", with_root=True)
    t_b = await make_tenant(name="L2-B", with_root=True)
    s_a = await make_store(tenant_id=t_a.id, name="L2-A-Store")
    await make_store(tenant_id=t_b.id, name="L2-B-Store")

    jwt = await tenant_owner_jwt_factory(
        t_a.id,
        with_grants=[("ADMIN", "STORES", "VIEW", "TENANT")],
    )
    resp = app_client.get(
        "/api/v1/stores?limit=100",
        headers=_auth(jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {i["id"] for i in body["items"]}
    assert str(s_a.id) in ids
    for item in body["items"]:
        assert item["tenant_id"] == str(t_a.id)


# ---- L3: PLATFORM list with tenant_id filter --------------------------------
async def test_l3_list_with_tenant_id_filter_under_platform(
    app_client, make_tenant, make_store, super_admin_jwt,
):
    t_a = await make_tenant(name="L3-A")
    t_b = await make_tenant(name="L3-B")
    await make_store(tenant_id=t_a.id, name="L3-A-S")
    s_b = await make_store(tenant_id=t_b.id, name="L3-B-S")

    resp = app_client.get(
        f"/api/v1/stores?tenant_id={t_b.id}&limit=100",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {i["id"] for i in body["items"]}
    assert str(s_b.id) in ids
    for item in body["items"]:
        assert item["tenant_id"] == str(t_b.id)


# ---- L4: status filter ------------------------------------------------------
async def test_l4_list_with_status_filter(
    app_client, make_tenant, make_store, super_admin_jwt,
):
    from admin_backend.models.store import StoreStatus

    t = await make_tenant(name="L4-T")
    s_act = await make_store(tenant_id=t.id, name="L4-Active")
    s_open = await make_store(
        tenant_id=t.id, name="L4-Opening", status=StoreStatus.OPENING
    )

    resp = app_client.get(
        f"/api/v1/stores?tenant_id={t.id}&status=ACTIVE&limit=100",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = {i["id"] for i in body["items"]}
    assert str(s_act.id) in ids
    assert str(s_open.id) not in ids


# ---- L5: search matches name -----------------------------------------------
async def test_l5_list_with_search(
    app_client, make_tenant, make_store, super_admin_jwt,
):
    t = await make_tenant(name="L5-T")
    s_buc = await make_store(tenant_id=t.id, name="L5-Buc-eeNo7")
    s_other = await make_store(tenant_id=t.id, name="L5-WholeFoods")

    resp = app_client.get(
        f"/api/v1/stores?tenant_id={t.id}&search=Buc",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = {i["id"] for i in body["items"]}
    assert str(s_buc.id) in ids
    assert str(s_other.id) not in ids


# ---- L6: sort name_desc -----------------------------------------------------
async def test_l6_list_with_sort_name_desc(
    app_client, make_tenant, make_store, super_admin_jwt,
):
    t = await make_tenant(name="L6-T")
    await make_store(tenant_id=t.id, name="L6-Alpha")
    await make_store(tenant_id=t.id, name="L6-Zeta")

    resp = app_client.get(
        f"/api/v1/stores?tenant_id={t.id}&sort=name_desc&limit=100",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    names = [i["name"] for i in body["items"]]
    # Within the tenant, names are in descending order.
    assert names == sorted(names, reverse=True)


# ---- L7: invalid sort -> 400 INVALID_SORT_KEY -------------------------------
async def test_l7_invalid_sort_returns_400(
    app_client, super_admin_jwt,
):
    resp = app_client.get(
        "/api/v1/stores?sort=bogus_key",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_SORT_KEY"


# ---- L8: pagination offset+limit slice --------------------------------------
async def test_l8_pagination_slices(
    app_client, make_tenant, make_store, super_admin_jwt,
):
    t = await make_tenant(name="L8-T")
    for i in range(5):
        await make_store(tenant_id=t.id, name=f"L8-Store-{i:02d}")

    resp_full = app_client.get(
        f"/api/v1/stores?tenant_id={t.id}&sort=name_asc&limit=100",
        headers=_auth(super_admin_jwt),
    )
    assert resp_full.status_code == 200
    full_items = resp_full.json()["items"]
    assert len(full_items) == 5

    resp_page = app_client.get(
        f"/api/v1/stores?tenant_id={t.id}&sort=name_asc&offset=2&limit=2",
        headers=_auth(super_admin_jwt),
    )
    assert resp_page.status_code == 200
    page_items = resp_page.json()["items"]
    assert len(page_items) == 2
    assert [i["id"] for i in page_items] == [i["id"] for i in full_items[2:4]]


# ---- L9: TENANT JWT without .TENANT grant -> 403 (LOAD-BEARING) -------------
async def test_l9_tenant_jwt_without_stores_grant_denied(
    app_client, settings, make_tenant,
):
    """A TENANT JWT minted with a random user_id has no seeded role
    assignment row, so the gate's ``has_permission`` query returns
    zero matches and denies with 403 ``PERMISSION_DENIED``. This is
    the structural denial path for any caller without the
    ``ADMIN.STORES.VIEW.TENANT`` grant (or a satisfying GLOBAL
    cascade)."""
    t = await make_tenant(name="L9-T", with_root=True)
    jwt = _tenant_jwt(settings, t.id)
    resp = app_client.get(
        "/api/v1/stores",
        headers=_auth(jwt),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "PERMISSION_DENIED"


# ---- L10: TENANT OWNER with .TENANT grant sees own rows (LOAD-BEARING) -----
async def test_l10_tenant_owner_with_stores_grant_sees_own_rows(
    app_client, make_tenant, make_store, tenant_owner_jwt_factory,
):
    """Synthetic OWNER user granted ``ADMIN.STORES.VIEW.TENANT`` for
    their own tenant. List returns own-tenant stores; cross-tenant
    rows hidden by RLS."""
    t_a = await make_tenant(name="L10-A", with_root=True)
    t_b = await make_tenant(name="L10-B")
    s_a = await make_store(tenant_id=t_a.id, name="L10-A-Store")
    await make_store(tenant_id=t_b.id, name="L10-B-Store")

    jwt = await tenant_owner_jwt_factory(
        t_a.id,
        with_grants=[("ADMIN", "STORES", "VIEW", "TENANT")],
    )
    resp = app_client.get(
        "/api/v1/stores?limit=100",
        headers=_auth(jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {i["id"] for i in body["items"]}
    assert str(s_a.id) in ids
    for item in body["items"]:
        assert item["tenant_id"] == str(t_a.id)


# =============================================================================
# Detail endpoint (D1-D4)
# =============================================================================


# ---- D1: detail under PLATFORM (LOAD-BEARING) -------------------------------
async def test_d1_detail_under_platform_returns_full_shape(
    app_client, make_tenant, make_store, super_admin_jwt,
):
    """StoreDetail shape: 17 fields per locked decision 6
    (all 22 DDL columns minus 6 audit-actor IDs, plus tenant_name)."""
    t = await make_tenant(name="D1-T", with_root=True)
    s = await make_store(
        tenant_id=t.id,
        name="D1-Store",
        store_code="D1-0001",
    )

    resp = app_client.get(
        f"/api/v1/stores/{s.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {
        "id",
        "tenant_id",
        "tenant_name",
        "org_node_id",
        "name",
        "store_code",
        "country",
        "timezone",
        "address",
        "latitude",
        "longitude",
        "currency",
        "tax_treatment",
        "status",
        "created_at",
        "updated_at",
        "closed_at",
    }
    assert body["id"] == str(s.id)
    assert body["tenant_name"] == "D1-T"
    assert body["store_code"] == "D1-0001"
    assert body["status"] == "ACTIVE"


# ---- D2: detail unknown id -> 404 STORE_NOT_FOUND ---------------------------
async def test_d2_detail_unknown_id_returns_404(
    app_client, super_admin_jwt,
):
    ephemeral = uuid.uuid4()
    resp = app_client.get(
        f"/api/v1/stores/{ephemeral}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "STORE_NOT_FOUND"


# ---- D3: cross-tenant detail returns 404 (LOAD-BEARING) ---------------------
async def test_d3_cross_tenant_detail_returns_404(
    app_client, make_tenant, make_store, tenant_owner_jwt_factory,
):
    """Cross-tenant probe surfaces as 404 STORE_NOT_FOUND, not 403.

    Per D-17 / F-THREADING-4: the anchor dep ``get_store_anchor`` runs
    BEFORE the gate body. TENANT-A's session can't see TENANT-B's
    store row (RLS), so the anchor dep raises StoreNotFoundError
    rather than returning a path that would have let the gate proceed.
    The existence of TENANT-B's store_id is therefore not disclosed.
    """
    t_a = await make_tenant(name="D3-A", with_root=True)
    t_b = await make_tenant(name="D3-B", with_root=True)
    s_b = await make_store(tenant_id=t_b.id, name="D3-B-Store")

    jwt = await tenant_owner_jwt_factory(
        t_a.id,
        with_grants=[("ADMIN", "STORES", "VIEW", "TENANT")],
    )
    resp = app_client.get(
        f"/api/v1/stores/{s_b.id}",
        headers=_auth(jwt),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "STORE_NOT_FOUND"


# ---- D4: TENANT OWNER same-tenant 200, different-tenant 404 (LOAD-BEARING) -
async def test_d4_tenant_owner_same_tenant_200_other_404(
    app_client, make_tenant, make_store, tenant_owner_jwt_factory,
):
    """OWNER granted ADMIN.STORES.VIEW.TENANT for their own tenant.
    Same tenant store -> 200; cross-tenant store -> 404 (anchor dep
    miss before gate body)."""
    t_a = await make_tenant(name="D4-A", with_root=True)
    t_b = await make_tenant(name="D4-B", with_root=True)
    s_a = await make_store(tenant_id=t_a.id, name="D4-A-Store")
    s_b = await make_store(tenant_id=t_b.id, name="D4-B-Store")

    jwt = await tenant_owner_jwt_factory(
        t_a.id,
        with_grants=[("ADMIN", "STORES", "VIEW", "TENANT")],
    )

    resp_same = app_client.get(
        f"/api/v1/stores/{s_a.id}",
        headers=_auth(jwt),
    )
    assert resp_same.status_code == 200, resp_same.text
    assert resp_same.json()["id"] == str(s_a.id)

    resp_other = app_client.get(
        f"/api/v1/stores/{s_b.id}",
        headers=_auth(jwt),
    )
    assert resp_other.status_code == 404
    assert resp_other.json()["code"] == "STORE_NOT_FOUND"


# =============================================================================
# Mandatory-gate-discipline anchor
# =============================================================================


# ---- MG1: both /stores endpoints carry the gate marker (LOAD-BEARING) ------
def test_mg1_stores_endpoints_carry_gate_marker() -> None:
    """Positive-control assertion: both /stores routes have a gate.

    The broader meta-test at ``test_gate_discipline.py`` enumerates
    every APIRoute; this test pins the stores routes specifically so
    a future refactor that accidentally dropped ``Depends(require(...))``
    from either handler fails here with a clear, scoped message.
    """
    app = create_app()
    target_paths = {"/api/v1/stores", "/api/v1/stores/{store_id}"}
    seen: set[str] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path not in target_paths:
            continue
        has_gate = any(
            hasattr(dep.call, "__permission_gate__")
            for dep in route.dependant.dependencies
        )
        assert has_gate, (
            f"{route.path}: no __permission_gate__ marker on any "
            "dependency; gate is required."
        )
        seen.add(route.path)
    assert seen == target_paths, (
        f"Stores routes missing from app registration: "
        f"{target_paths - seen}"
    )
