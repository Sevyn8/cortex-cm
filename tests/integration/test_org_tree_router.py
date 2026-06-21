"""Integration tests for the org-tree router (Step 5.3).

Two endpoints under test:

  - E2: ``GET /api/v1/tenants/{tenant_id}/org-tree``
  - E3: ``GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children``

Real Postgres, real schema, real RLS, real router via FastAPI's
TestClient. JWTs minted via Step 2.1's ``make_test_jwt``. Mirrors
``test_tenant_users_router.py``'s shape (multi-user-type with
RLS-as-404 cross-tenant test).

Coverage map vs. invariants I1-I13 in the prompt:

  T1   I1, I2, I3, I4, I5, I6, I7, I8, I9, I10, I11
  T2   tree=[] for empty tenant
  T3   I1 (TENANT root excluded)
  T4   I7 (sibling order)
  T5   recursive serialization
  T6   I2 (only ACTIVE)
  T7   smart-default full mode
  T8   smart-default lazy mode (monkeypatched threshold)
  T9   I13 (truncated=true on payload-cap auto-reduce)
  T10  explicit depth respected
  T11  TENANT JWT own tenant
  T12  RLS-as-404 (E2 cross-tenant) -- LOAD-BEARING
  T13  unknown tenant -> 404
  T14  no JWT -> 401
  T15  E3 happy path
  T16  E3 pagination
  T17  E3 unknown node -> 404 ORG_NODE_NOT_FOUND
  T18  E3 cross-tenant -> 404 ORG_NODE_NOT_FOUND -- LOAD-BEARING
  T19  E3 node with no children -> 200 + empty items
  T20  mixed-depth subtrees: loaded_children correct on both branches
  T21  E2 invalid UUID -> 422
  T22  E2 tenant_root_* fields populate under PLATFORM -- Step 6.21.1
  T23  E2 tenant_root_* fields populate under TENANT OWNER -- Step 6.21.1
  T24  E2 tenant_root_* fields populate on empty-descendants tenant -- Step 6.21.1

Fixture-built trees use the canonical Buc-ee's-shape:
  TENANT(BUC) -> HQ(BU-HQ) -> REGION(FL,TX) -> STORE(...) -> DEPT(...).
Path labels are lowercased code with hyphens -> underscores
(ltree label syntax disallows hyphens).
"""
import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings
from admin_backend.main import create_app


# =============================================================================
# Fixtures (mirror test_tenant_users_router.py)
# =============================================================================


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,  # type: ignore[no-any-unimported]
    session_factory: Any,  # type: ignore[no-any-unimported]
) -> Iterator[TestClient]:
    """TestClient against a real app with real engine/session_factory."""
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
        settings,
        user_id=uuid.uuid4(),
        user_type="PLATFORM",
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


# =============================================================================
# Helpers
# =============================================================================


async def _build_bucees(make_tenant: Any, make_org_node: Any) -> dict[str, Any]:
    """Build the canonical Buc-ee's tree (8 non-TENANT nodes).

    Layout (using made-up codes prefixed with the test marker so
    fixtures don't collide):
      TENANT root (BUC)
        HQ (BU-HQ)
          REGION FL
            STORE S101
              DEPT BATH
              DEPT DELI
          REGION TX
            STORE S201
            STORE S202

    Returns a dict of references for assertion convenience.
    """
    tenant = await make_tenant(name="Buc-ee-fixture")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="BUC", name="Buc-ee's",
    )
    hq_id, hq_path = await make_org_node(
        tenant_id=tenant.id, node_type="HQ", code="BU-HQ", name="Buc-ee's HQ",
        parent_id=root_id, parent_path=root_path,
    )
    fl_id, fl_path = await make_org_node(
        tenant_id=tenant.id, node_type="REGION", code="FL", name="Florida Region",
        parent_id=hq_id, parent_path=hq_path,
    )
    tx_id, tx_path = await make_org_node(
        tenant_id=tenant.id, node_type="REGION", code="TX", name="Texas Region",
        parent_id=hq_id, parent_path=hq_path,
    )
    s101_id, s101_path = await make_org_node(
        tenant_id=tenant.id, node_type="STORE", code="S101", name="Store 101",
        parent_id=fl_id, parent_path=fl_path,
    )
    s201_id, s201_path = await make_org_node(
        tenant_id=tenant.id, node_type="STORE", code="S201", name="Store 201",
        parent_id=tx_id, parent_path=tx_path,
    )
    s202_id, s202_path = await make_org_node(
        tenant_id=tenant.id, node_type="STORE", code="S202", name="Store 202",
        parent_id=tx_id, parent_path=tx_path,
    )
    bath_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="DEPARTMENT", code="BATH", name="Bath",
        parent_id=s101_id, parent_path=s101_path,
    )
    deli_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="DEPARTMENT", code="DELI", name="Deli",
        parent_id=s101_id, parent_path=s101_path,
    )
    return {
        "tenant": tenant,
        "root_id": root_id,
        "hq_id": hq_id,
        "fl_id": fl_id,
        "tx_id": tx_id,
        "s101_id": s101_id,
        "s201_id": s201_id,
        "s202_id": s202_id,
        "bath_id": bath_id,
        "deli_id": deli_id,
    }


# =============================================================================
# E2 tests
# =============================================================================


# ---- T1: small full envelope + invariants ---------------------------------
async def test_t1_e2_small_tenant_full_tree_envelope(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """Small Buc-ee's-shape tenant. Full tree, all envelope invariants."""
    refs = await _build_bucees(make_tenant, make_org_node)
    tenant = refs["tenant"]

    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Top-level shape (D-30 exception — singleton resource). The three
    # tenant_root_* fields land at Step 6.21.1.
    assert set(body.keys()) == {
        "tenant_id",
        "tenant_name",
        "tenant_root_id",
        "tenant_root_code",
        "tenant_root_path",
        "stats",
        "tree",
    }
    assert body["tenant_id"] == str(tenant.id)
    assert body["tenant_name"] == tenant.name

    # Stats invariants I4, I5, I6, I13.
    stats = body["stats"]
    assert stats["total_nodes"] == 8
    assert stats["nodes_returned"] == 8
    assert stats["stores"] == 3
    assert stats["regions"] == 2
    assert stats["depth_returned"] == 4  # HQ=1, region=2, store=3, dept=4
    assert stats["truncated"] is False

    # Tree shape — single root: HQ.
    assert len(body["tree"]) == 1
    hq = body["tree"][0]
    assert hq["node_type"] == "HQ"
    assert hq["code"] == "BU-HQ"
    assert hq["has_children"] is True
    assert hq["child_count"] == 2
    assert hq["loaded_children"] == "all"  # I10
    assert len(hq["children"]) == 2

    # Sibling order I7: FL before TX (alphabetical-by-lowered-code).
    assert [c["code"] for c in hq["children"]] == ["FL", "TX"]

    fl = hq["children"][0]
    tx = hq["children"][1]
    assert fl["child_count"] == 1
    assert fl["has_children"] is True  # I11
    assert fl["loaded_children"] == "all"
    assert tx["child_count"] == 2

    # Hidden fields contract.
    for hidden in (
        "created_by_user_id",
        "created_by_user_type",
        "updated_by_user_id",
        "updated_by_user_type",
        "archived_by_user_id",
        "archived_by_user_type",
        "auth0_sub",
    ):
        assert hidden not in hq

    # Bath/Deli leaves: has_children=false, loaded_children="none". I3.
    s101 = fl["children"][0]
    assert s101["code"] == "S101"
    assert s101["child_count"] == 2
    assert [c["code"] for c in s101["children"]] == ["BATH", "DELI"]
    for leaf in s101["children"]:
        assert leaf["has_children"] is False
        assert leaf["child_count"] == 0
        assert leaf["loaded_children"] == "none"
        assert leaf["children"] == []


# ---- T2: empty tenant -----------------------------------------------------
async def test_t2_e2_empty_tenant_with_root_only_returns_empty_tree(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """Tenant with ONLY a TENANT-root org_node (no descendants). Returns
    200 with empty tree.

    Post Step 6.9.3.2: the gate's ``get_tenant_anchor`` dep requires a
    tenant-root org_node to exist; a tenant with ZERO org_nodes raises
    404 from the anchor (no row to resolve the cascade anchor). The
    test's prior premise ("architecturally invalid but DDL-permissive")
    is no longer reachable via the API — the test now provisions a
    tenant root, satisfying the anchor, and asserts the same empty-tree
    response shape for "tenant exists but has no descendants below the
    implicit root."
    """
    tenant = await make_tenant(name="T2-RootOnly")
    await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code=f"t2-{tenant.id.hex[:6]}", name="T2 Root",
    )
    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tree"] == []
    s = body["stats"]
    # The tenant root counts toward total_nodes; nodes_returned remains
    # 0 because the response tree excludes the implicit TENANT root.
    assert s["nodes_returned"] == 0
    assert s["stores"] == 0
    assert s["regions"] == 0
    assert s["depth_returned"] == 0
    assert s["truncated"] is False


# ---- T3: only TENANT root -------------------------------------------------
async def test_t3_e2_only_tenant_root_returns_empty_tree(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """Tenant with only a TENANT-type root, no descendants. tree=[].
    TENANT root excluded per I1."""
    tenant = await make_tenant(name="T3-RootOnly")
    await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="T3", name="T3 Root",
    )
    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tree"] == []
    assert body["stats"]["total_nodes"] == 0


# ---- T4: sibling order -----------------------------------------------------
async def test_t4_e2_sibling_order_alphabetical(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """Insert TX, CA, FL in that order. Response orders CA, FL, TX (I7)."""
    tenant = await make_tenant(name="T4-SiblingOrder")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="T4", name="T4 Root",
    )
    hq_id, hq_path = await make_org_node(
        tenant_id=tenant.id, node_type="HQ", code="HQ4", name="HQ",
        parent_id=root_id, parent_path=root_path,
    )
    # Insert in non-alphabetical order: TX, CA, FL.
    for code in ("TX", "CA", "FL"):
        await make_org_node(
            tenant_id=tenant.id, node_type="REGION", code=code, name=code,
            parent_id=hq_id, parent_path=hq_path,
        )

    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    hq = resp.json()["tree"][0]
    assert [c["code"] for c in hq["children"]] == ["CA", "FL", "TX"]


# ---- T5: recursive depth-3+ ----------------------------------------------
async def test_t5_e2_recursive_serialization(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """4-level chain HQ -> REGION -> STORE -> DEPT. Recursive Pydantic
    serialization produces the right nested shape."""
    refs = await _build_bucees(make_tenant, make_org_node)
    resp = app_client.get(
        f"/api/v1/tenants/{refs['tenant'].id}/org-tree",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    hq = resp.json()["tree"][0]
    fl = hq["children"][0]
    s101 = fl["children"][0]
    bath = s101["children"][0]
    # Walk to the 4th level — leaf.
    assert bath["node_type"] == "DEPARTMENT"
    assert bath["children"] == []


# ---- T6: INACTIVE/ARCHIVED excluded ---------------------------------------
async def test_t6_e2_inactive_nodes_excluded(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """INACTIVE / ARCHIVED nodes excluded. HQ has 1 ACTIVE child only (I2)."""
    tenant = await make_tenant(name="T6-InactiveFilter")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="T6", name="T6 Root",
    )
    hq_id, hq_path = await make_org_node(
        tenant_id=tenant.id, node_type="HQ", code="HQ6", name="HQ",
        parent_id=root_id, parent_path=root_path,
    )
    await make_org_node(
        tenant_id=tenant.id, node_type="STORE", code="ACT",
        name="Active store",
        parent_id=hq_id, parent_path=hq_path,
        status="ACTIVE",
    )
    await make_org_node(
        tenant_id=tenant.id, node_type="STORE", code="INA",
        name="Inactive store",
        parent_id=hq_id, parent_path=hq_path,
        status="INACTIVE",
    )

    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
    )
    body = resp.json()
    assert resp.status_code == 200
    hq = body["tree"][0]
    assert hq["child_count"] == 1
    assert [c["code"] for c in hq["children"]] == ["ACT"]
    assert body["stats"]["total_nodes"] == 2  # HQ + ACT
    assert body["stats"]["stores"] == 1


# ---- T7: smart-default full mode (under threshold) ------------------------
async def test_t7_e2_smart_default_full_mode(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """Tenant under FULL_TREE_THRESHOLD gets full tree without depth param.
    All internal nodes have loaded_children='all'."""
    refs = await _build_bucees(make_tenant, make_org_node)
    resp = app_client.get(
        f"/api/v1/tenants/{refs['tenant'].id}/org-tree",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stats"]["truncated"] is False
    assert body["stats"]["nodes_returned"] == body["stats"]["total_nodes"]

    # Walk: HQ.loaded='all', FL.loaded='all', S101.loaded='all'.
    hq = body["tree"][0]
    assert hq["loaded_children"] == "all"
    assert hq["children"][0]["loaded_children"] == "all"  # FL
    s101 = hq["children"][0]["children"][0]
    assert s101["loaded_children"] == "all"


# ---- T8: smart-default lazy mode (over threshold, monkeypatched) ----------
async def test_t8_e2_smart_default_lazy_mode(
    app_client, settings, make_tenant, make_org_node, monkeypatch,
    super_admin_jwt,
):
    """Tenant over (monkeypatched) threshold gets depth-limited tree by
    default. Nodes at depth boundary have has_children=true and
    loaded_children='none' (depth-cut, not leaf). LOAD-BEARING for the
    lazy-mode branch of the smart-default."""
    from admin_backend.routers.v1 import org_tree as org_tree_module
    monkeypatch.setattr(org_tree_module, "FULL_TREE_THRESHOLD", 2)
    monkeypatch.setattr(org_tree_module, "DEFAULT_DEPTH", 2)

    refs = await _build_bucees(make_tenant, make_org_node)

    resp = app_client.get(
        f"/api/v1/tenants/{refs['tenant'].id}/org-tree",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Tree count > 2, so lazy mode kicks in. depth=2 keeps HQ + regions.
    assert body["stats"]["depth_returned"] == 2
    assert body["stats"]["total_nodes"] == 8  # full tree count
    assert body["stats"]["nodes_returned"] == 3  # HQ + FL + TX

    hq = body["tree"][0]
    fl = hq["children"][0]
    tx = hq["children"][1]
    # FL/TX are at depth boundary; their children (stores) NOT in response.
    # They have ACTIVE children, so has_children=true and
    # loaded_children='none' (depth-cut signal).
    assert fl["has_children"] is True
    assert fl["child_count"] == 1
    assert fl["loaded_children"] == "none"
    assert fl["children"] == []
    assert tx["has_children"] is True
    assert tx["child_count"] == 2
    assert tx["loaded_children"] == "none"


# ---- T9: payload cap auto-reduce ----------------------------------------
async def test_t9_e2_payload_cap_auto_reduce(
    app_client, settings, make_tenant, make_org_node, monkeypatch,
    super_admin_jwt,
):
    """When the depth-limited tree exceeds PAYLOAD_CAP, server reduces
    depth and sets truncated=true. Bounded retry (max 2 reductions).

    Setup: 8-node Buc-ee's-shape tree, monkeypatch PAYLOAD_CAP to 4 and
    threshold/default-depth to force lazy mode at depth 4. The depth=4
    fetch returns 8 nodes (>4 cap), so server reduces to 3 → 7 nodes
    (still >4), reduces to 2 → 3 nodes (under cap). truncated=true."""
    from admin_backend.routers.v1 import org_tree as org_tree_module
    monkeypatch.setattr(org_tree_module, "FULL_TREE_THRESHOLD", 0)
    monkeypatch.setattr(org_tree_module, "DEFAULT_DEPTH", 4)
    monkeypatch.setattr(org_tree_module, "PAYLOAD_CAP", 4)
    monkeypatch.setattr(org_tree_module, "MAX_REDUCTIONS", 3)

    refs = await _build_bucees(make_tenant, make_org_node)
    resp = app_client.get(
        f"/api/v1/tenants/{refs['tenant'].id}/org-tree",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stats"]["truncated"] is True
    # Reduced below requested depth.
    assert body["stats"]["depth_returned"] < 4


# ---- T10: explicit ?depth=N respected -------------------------------------
async def test_t10_e2_explicit_depth_respected(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """?depth=2 returns HQ + first level. depth_returned=2.
    Boundary nodes have has_children=true, loaded_children='none'."""
    refs = await _build_bucees(make_tenant, make_org_node)
    resp = app_client.get(
        f"/api/v1/tenants/{refs['tenant'].id}/org-tree?depth=2",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stats"]["depth_returned"] == 2
    hq = body["tree"][0]
    assert hq["loaded_children"] == "all"
    fl = hq["children"][0]
    assert fl["has_children"] is True
    assert fl["loaded_children"] == "none"  # depth-cut
    assert fl["children"] == []


# ---- T11: TENANT JWT for own tenant ---------------------------------------
async def test_t11_e2_tenant_jwt_own_tenant(
    app_client, make_tenant, make_org_node, tenant_owner_jwt_factory
):
    """TENANT JWT can read its own tenant's org-tree.

    The factory adds 1 TENANT-type org_node (JWT Fixture Root) to the
    tenant; total_nodes counts ACTIVE non-TENANT nodes only, so the
    Buc-ee's-shape 8-node assertion is unaffected.
    """
    refs = await _build_bucees(make_tenant, make_org_node)
    jwt = await tenant_owner_jwt_factory(refs["tenant"].id)
    resp = app_client.get(
        f"/api/v1/tenants/{refs['tenant'].id}/org-tree",
        headers=_auth(jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stats"]["total_nodes"] == 8


# ---- T12: TENANT-A asking for TENANT-B's tree -> 404 (LOAD-BEARING) -------
async def test_t12_e2_cross_tenant_returns_404(
    app_client, settings, make_tenant, make_org_node
):
    """LOAD-BEARING. TENANT-A requesting TENANT-B's org-tree -> 404
    TENANT_NOT_FOUND. Without this regression test, RLS could be silently
    bypassed and a tenant could probe other tenants' existence."""
    tenant_a = await make_tenant(name="T12-TenantA")
    tenant_b = await make_tenant(name="T12-TenantB")
    # Build B's tree so we can prove visibility doesn't leak.
    root_id, root_path = await make_org_node(
        tenant_id=tenant_b.id, node_type="TENANT", code="T12B", name="B Root",
    )
    await make_org_node(
        tenant_id=tenant_b.id, node_type="HQ", code="B-HQ", name="B HQ",
        parent_id=root_id, parent_path=root_path,
    )
    resp = app_client.get(
        f"/api/v1/tenants/{tenant_b.id}/org-tree",
        headers=_auth(_tenant_jwt(settings, tenant_a.id)),
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "TENANT_NOT_FOUND"


# ---- T13: unknown tenant -> 404 ------------------------------------------
async def test_t13_e2_unknown_tenant_returns_404(app_client, settings, super_admin_jwt):
    fake = uuid.uuid4()
    resp = app_client.get(
        f"/api/v1/tenants/{fake}/org-tree",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "TENANT_NOT_FOUND"


# ---- T14: no JWT -> 401 --------------------------------------------------
def test_t14_e2_no_jwt_returns_401(app_client):
    fake = uuid.uuid4()
    resp = app_client.get(f"/api/v1/tenants/{fake}/org-tree")
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MISSING"


# ---- T22: tenant_root_* fields populate (PLATFORM) -- Step 6.21.1 ----------
async def test_t22_e2_tenant_root_fields_platform(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """LOAD-BEARING for the Add-Org-Node fix. PLATFORM caller hitting
    GET /org-tree on a non-empty tenant: the three new top-level fields
    (``tenant_root_id``, ``tenant_root_code``, ``tenant_root_path``)
    surface the tenant-root org_node so the frontend can use the correct
    UUID as ``parent_id`` on POST /org-tree. See Step 6.21.1 and
    ``docs/investigations/2026-05-20-write-surface-coupling.md``.
    """
    refs = await _build_bucees(make_tenant, make_org_node)
    tenant = refs["tenant"]
    root_id = refs["root_id"]

    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The tenant-root metadata matches the fixture's TENANT-typed node.
    assert body["tenant_root_id"] == str(root_id)
    assert body["tenant_root_code"] == "BUC"
    assert body["tenant_root_path"] == "buc"  # lowercased label

    # tenant_id (path-param echo) is distinct from tenant_root_id.
    assert body["tenant_id"] == str(tenant.id)
    assert body["tenant_root_id"] != body["tenant_id"]

    # tree[] still excludes the TENANT-typed row (invariant preserved).
    def _walk(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
        yield node
        for child in node.get("children", []):
            yield from _walk(child)

    for node in body["tree"]:
        for descendant in _walk(node):
            assert descendant["node_type"] != "TENANT"


# ---- T23: tenant_root_* fields under TENANT OWNER -- Step 6.21.1 -----------
async def test_t23_e2_tenant_root_fields_tenant_owner(
    app_client, make_tenant, make_org_node, tenant_owner_jwt_factory,
):
    """TENANT OWNER reading own-tenant org-tree sees the three new
    tenant_root_* fields. RLS-scoped (TENANT JWT path); confirms the
    extraction logic doesn't depend on user_type or PLATFORM-only data
    visibility.

    ``tenant_owner_jwt_factory`` reuses an existing TENANT-typed root if
    one is present, so we get back the BUC fixture root rather than a
    factory-synthesised second root.
    """
    refs = await _build_bucees(make_tenant, make_org_node)
    tenant = refs["tenant"]
    root_id = refs["root_id"]
    jwt = await tenant_owner_jwt_factory(tenant.id)

    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Same BUC root as T22, reached via TENANT-side RLS path.
    assert body["tenant_root_id"] == str(root_id)
    assert body["tenant_root_code"] == "BUC"
    assert body["tenant_root_path"] == "buc"


# ---- T24: tenant_root_* fields on empty-descendants tenant ----------------
async def test_t24_e2_tenant_root_fields_empty_tree(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """LOAD-BEARING. Tenant with ONLY a TENANT-root org_node and zero
    descendants: ``tree=[]`` AND the three tenant_root_* fields STILL
    populate. Regression guard for the empty-tree path of LD3 (the
    extraction runs on the full row list from list_active_with_child_counts,
    not on the post-_build_tree filtered list).
    """
    tenant = await make_tenant(name="T24-RootOnly")
    root_id, _ = await make_org_node(
        tenant_id=tenant.id,
        node_type="TENANT",
        code="T24",
        name="T24 Root",
    )
    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["tree"] == []
    assert body["tenant_root_id"] == str(root_id)
    assert body["tenant_root_code"] == "T24"
    assert body["tenant_root_path"] == "t24"


# =============================================================================
# E3 tests
# =============================================================================


# ---- T15: happy path -----------------------------------------------------
async def test_t15_e3_happy_path(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """E3 returns immediate ACTIVE children of node_id. Each child carries
    its own has_children and child_count."""
    refs = await _build_bucees(make_tenant, make_org_node)
    resp = app_client.get(
        f"/api/v1/tenants/{refs['tenant'].id}/org-nodes/{refs['hq_id']}/children",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"node_id", "items", "pagination"}
    assert body["node_id"] == str(refs["hq_id"])
    # HQ's children = FL, TX in alphabetical-by-code order.
    codes = [i["code"] for i in body["items"]]
    assert codes == ["FL", "TX"]
    # FL has 1 child (S101); TX has 2 (S201, S202).
    by_code = {i["code"]: i for i in body["items"]}
    assert by_code["FL"]["child_count"] == 1
    assert by_code["FL"]["has_children"] is True
    assert by_code["FL"]["loaded_children"] == "none"  # E3 always 'none'
    assert by_code["FL"]["children"] == []
    assert by_code["TX"]["child_count"] == 2
    assert body["pagination"] == {"total": 2, "offset": 0, "limit": 100}


# ---- T16: pagination ----------------------------------------------------
async def test_t16_e3_pagination(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """Build a parent with 5 children; request limit=2 offset=1.
    Verify items has 2 entries; total=5; offset=1; limit=2.
    Order: alphabetical-by-code: A, B, C, D, E -> [B, C] for offset=1, limit=2."""
    tenant = await make_tenant(name="T16-Pagination")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="T16", name="Root",
    )
    parent_id, parent_path = await make_org_node(
        tenant_id=tenant.id, node_type="HQ", code="P", name="Parent",
        parent_id=root_id, parent_path=root_path,
    )
    for code in ("A", "B", "C", "D", "E"):
        await make_org_node(
            tenant_id=tenant.id, node_type="REGION", code=code, name=code,
            parent_id=parent_id, parent_path=parent_path,
        )
    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}/org-nodes/{parent_id}/children?offset=1&limit=2",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [i["code"] for i in body["items"]] == ["B", "C"]
    assert body["pagination"] == {"total": 5, "offset": 1, "limit": 2}


# ---- T17: unknown node_id -> 404 ORG_NODE_NOT_FOUND -----------------------
async def test_t17_e3_unknown_node_returns_404(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    """Tenant exists; node_id doesn't. 404 ORG_NODE_NOT_FOUND."""
    tenant = await make_tenant(name="T17-Tenant")
    fake_node = uuid.uuid4()
    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}/org-nodes/{fake_node}/children",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "ORG_NODE_NOT_FOUND"
    assert body["message"] == "Org node not found"


# ---- T18: cross-tenant -> 404 ORG_NODE_NOT_FOUND (LOAD-BEARING) -----------
async def test_t18_e3_cross_tenant_node_returns_404(
    app_client, settings, make_tenant, make_org_node
):
    """LOAD-BEARING. TENANT-A requesting an org_node that belongs to TENANT-B
    receives 404 ORG_NODE_NOT_FOUND. Same envelope as T17 — an attacker
    can't probe cross-tenant node existence.

    Mechanism end-to-end:
      1. Middleware verifies TENANT-A JWT, populates AuthContext.
      2. get_tenant_session sets app.tenant_id=A.
      3. _tenants_repo.get_by_id(B.id) returns None (RLS filters); 404.

    Note: tenant_a sees TENANT B's tenant_id (path), but the tenant
    doesn't exist in their RLS view, so the FIRST 404 fires — at the
    tenant lookup, not the node-exists check. Both produce the same
    surface. Specifically asserts body['code']=TENANT_NOT_FOUND because
    the tenant resolve runs before node_exists."""
    tenant_a = await make_tenant(name="T18-TenantA")
    tenant_b = await make_tenant(name="T18-TenantB")
    root_b_id, root_b_path = await make_org_node(
        tenant_id=tenant_b.id, node_type="TENANT", code="T18B", name="B Root",
    )
    hq_b_id, _ = await make_org_node(
        tenant_id=tenant_b.id, node_type="HQ", code="B-HQ", name="B HQ",
        parent_id=root_b_id, parent_path=root_b_path,
    )

    # TENANT-A asks E3 with B's tenant_id and B's hq node id.
    # Step 6.9.3.2 retrofit moved the lookup into the anchor dep
    # ``get_org_node_anchor(tenant_b.id, hq_b_id)`` which runs under
    # tenant_a's session GUCs; RLS on org_nodes hides tenant_b's rows
    # → anchor raises ORG_NODE_NOT_FOUND (404). The 404 surface and
    # information-disclosure property are unchanged; only the error
    # code differs from the pre-retrofit path (which would have raised
    # TENANT_NOT_FOUND in the handler body before reaching the
    # node-exists check).
    resp = app_client.get(
        f"/api/v1/tenants/{tenant_b.id}/org-nodes/{hq_b_id}/children",
        headers=_auth(_tenant_jwt(settings, tenant_a.id)),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "ORG_NODE_NOT_FOUND"


# ---- T19: node has no children -> 200 + empty items ---------------------
async def test_t19_e3_no_children_returns_empty(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """Parent node exists ACTIVE but has no children. 200 with items=[]."""
    refs = await _build_bucees(make_tenant, make_org_node)
    # S202 has no children.
    resp = app_client.get(
        f"/api/v1/tenants/{refs['tenant'].id}/org-nodes/{refs['s202_id']}/children",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0


# =============================================================================
# Bonus: T20 mixed-depth subtree, T21 invalid UUID
# =============================================================================


# ---- T20: mixed-depth subtrees -----------------------------------------
async def test_t20_e2_mixed_depth_loaded_children(
    app_client, settings, make_tenant, make_org_node, monkeypatch,
    super_admin_jwt,
):
    """Mixed-depth tree:
      Branch A: HQ -> STORE_A    (true leaf at depth 2)
      Branch B: HQ -> REGION_B -> STORE_B  (REGION_B has children at depth 3)

    Under explicit ?depth=2, both branches' depth-2 nodes are present.
    STORE_A is a true leaf: has_children=false, loaded_children='none'.
    REGION_B is depth-cut: has_children=true, loaded_children='none',
    children=[]. Frontend disambiguates via has_children.

    LOAD-BEARING: if a future regression made 'none' mean only-unloaded
    (or only-leaf), one of the two cases breaks."""
    tenant = await make_tenant(name="T20-MixedDepth")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id, node_type="TENANT", code="T20", name="Root",
    )
    hq_id, hq_path = await make_org_node(
        tenant_id=tenant.id, node_type="HQ", code="HQ20", name="HQ",
        parent_id=root_id, parent_path=root_path,
    )
    # Branch A: STORE only (true leaf at depth 2).
    await make_org_node(
        tenant_id=tenant.id, node_type="STORE", code="SA",
        name="Branch A leaf",
        parent_id=hq_id, parent_path=hq_path,
    )
    # Branch B: REGION at depth 2 with STORE below at depth 3.
    rb_id, rb_path = await make_org_node(
        tenant_id=tenant.id, node_type="REGION", code="RB",
        name="Branch B region",
        parent_id=hq_id, parent_path=hq_path,
    )
    await make_org_node(
        tenant_id=tenant.id, node_type="STORE", code="SB",
        name="Branch B store",
        parent_id=rb_id, parent_path=rb_path,
    )

    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}/org-tree?depth=2",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stats"]["depth_returned"] == 2
    hq = body["tree"][0]
    by_code = {c["code"]: c for c in hq["children"]}
    # Branch A leaf: has_children=false, loaded_children='none'.
    assert by_code["SA"]["has_children"] is False
    assert by_code["SA"]["child_count"] == 0
    assert by_code["SA"]["loaded_children"] == "none"
    # Branch B region: has_children=true (SB exists below cut),
    # loaded_children='none' (depth-cut).
    assert by_code["RB"]["has_children"] is True
    assert by_code["RB"]["child_count"] == 1
    assert by_code["RB"]["loaded_children"] == "none"
    assert by_code["RB"]["children"] == []


# ---- T21: invalid UUID -> 422 -------------------------------------------
def test_t21_e2_invalid_uuid_returns_422(app_client, settings, super_admin_jwt):
    """Malformed UUID in path-param -> FastAPI's 422.

    FastAPI's path-param UUID validation rejects malformed values
    before the handler runs. tenants_router test 13 (Step 3.3) confirms
    422; we mirror that envelope."""
    resp = app_client.get(
        "/api/v1/tenants/not-a-uuid/org-tree",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422
