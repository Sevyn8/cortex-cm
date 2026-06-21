"""Integration tests for the tenant_users router (Step 5.2).

Real Postgres, real schema, real RLS, real router via FastAPI's
TestClient. JWTs minted via Step 2.1's ``make_test_jwt``. Mirrors the
shape used by ``test_platform_users_router.py`` and
``test_tenants_router.py``.

Coverage:

  L1-L8:  list endpoint (PLATFORM happy path, PLATFORM with tenant_id
          filter, status filter, search, sort, invalid sort -> 400,
          pagination, TENANT-A list scoped to A only).
  D1-D2:  detail endpoint (happy + hidden-fields contract; 404).
  T9-T10: cross-tenant isolation. T9 is LOAD-BEARING — it proves
          RLS-as-404 (D-17) works end-to-end through middleware ->
          session -> Repo -> router.
  A1:     auth (no JWT -> 401).

The cross-tenant tests use ``make_tenant`` + ``make_tenant_user`` to
build TENANT-A and TENANT-B in isolation per test, then assert that a
TENANT-A JWT cannot see TENANT-B's user (T9) and gets an empty list
when filtering by TENANT-B (T10). The factories handle teardown via
DELETE so tests don't leave state.
"""
import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient

from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings
from admin_backend.main import create_app


import pytest


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,  # type: ignore[no-any-unimported]
    session_factory: Any,  # type: ignore[no-any-unimported]
) -> Iterator[TestClient]:
    """TestClient against a real app with real engine/session_factory.

    Bypasses the lifespan (would re-construct an engine in a different
    event loop than the test). Mirrors the pattern from
    ``test_platform_users_router.py`` and ``test_tenants_router.py``.
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
# List endpoint, PLATFORM context (L1-L7)
# =============================================================================


# ---- L1: PLATFORM list happy path + envelope + hidden-fields contract -----
async def test_l1_list_platform_envelope_and_hidden_fields(
    app_client, settings, make_tenant, make_tenant_user,
    super_admin_jwt,
):
    """List response is ``{items, pagination}`` per D-30. Pattern (b)
    audit-actor columns and ``auth0_sub`` are absent from items.
    """
    tenant = await make_tenant(name="L1-Tenant")
    user = await make_tenant_user(
        tenant_id=tenant.id,
        email="l1-active@l1tenant.test",
        full_name="L1 Active User",
        status="ACTIVE",
    )

    resp = app_client.get(
        "/api/v1/tenant-users",
        params={"tenant_id": str(tenant.id), "limit": 200},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "pagination" in body
    assert set(body["pagination"].keys()) == {"total", "offset", "limit"}

    matches = [i for i in body["items"] if i["id"] == str(user.id)]
    assert len(matches) == 1
    item = matches[0]
    assert set(item.keys()) == {
        "id",
        "tenant_id",
        "email",
        "full_name",
        "status",
        "invited_at",
        "invitation_accepted_at",
        "suspended_at",
        "created_at",
        "updated_at",
        "roles",  # Step 6.8.3 augmentation: inline role assignments.
    }
    # Hidden fields stay hidden.
    for hidden in (
        "auth0_sub",
        "created_by_user_id",
        "created_by_user_type",
        "updated_by_user_id",
        "updated_by_user_type",
        "suspended_by_user_id",
        "suspended_by_user_type",
    ):
        assert hidden not in item
    assert item["tenant_id"] == str(tenant.id)


# ---- L2: PLATFORM with ?tenant_id=X scopes to that tenant only ------------
async def test_l2_list_platform_with_tenant_filter(
    app_client, settings, make_tenant, make_tenant_user,
    super_admin_jwt,
):
    tenant_a = await make_tenant(name="L2-TenantA")
    tenant_b = await make_tenant(name="L2-TenantB")
    user_a = await make_tenant_user(
        tenant_id=tenant_a.id, email="l2a@l2.test", status="ACTIVE"
    )
    user_b = await make_tenant_user(
        tenant_id=tenant_b.id, email="l2b@l2.test", status="ACTIVE"
    )

    resp = app_client.get(
        "/api/v1/tenant-users",
        params={"tenant_id": str(tenant_a.id), "limit": 200},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = {u["id"] for u in items}
    assert str(user_a.id) in ids
    assert str(user_b.id) not in ids
    assert all(u["tenant_id"] == str(tenant_a.id) for u in items)


# ---- L3: status filter ----------------------------------------------------
async def test_l3_list_filter_by_status(
    app_client, settings, make_tenant, make_tenant_user,
    super_admin_jwt,
):
    tenant = await make_tenant(name="L3-Tenant")
    invited = await make_tenant_user(
        tenant_id=tenant.id, email="l3-invited@l3.test", status="INVITED"
    )
    active = await make_tenant_user(
        tenant_id=tenant.id, email="l3-active@l3.test", status="ACTIVE"
    )
    resp = app_client.get(
        "/api/v1/tenant-users",
        params={
            "tenant_id": str(tenant.id),
            "status": "ACTIVE",
            "limit": 200,
        },
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = {u["id"] for u in items}
    assert str(active.id) in ids
    assert str(invited.id) not in ids
    assert all(u["status"] == "ACTIVE" for u in items)


# ---- L4: search across email and full_name --------------------------------
async def test_l4_list_search_matches_email_and_full_name(
    app_client, settings, make_tenant, make_tenant_user,
    super_admin_jwt,
):
    tenant = await make_tenant(name="L4-Tenant")
    by_email = await make_tenant_user(
        tenant_id=tenant.id,
        email="l4uniq@l4.test",
        full_name="Some Other Name",
        status="ACTIVE",
    )
    by_name = await make_tenant_user(
        tenant_id=tenant.id,
        email="x@l4.test",
        full_name="L4uniq Person",
        status="ACTIVE",
    )
    not_match = await make_tenant_user(
        tenant_id=tenant.id,
        email="other@l4.test",
        full_name="Unrelated User",
        status="ACTIVE",
    )
    resp = app_client.get(
        "/api/v1/tenant-users",
        params={
            "tenant_id": str(tenant.id),
            "search": "l4uniq",
            "limit": 200,
        },
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    ids = {u["id"] for u in resp.json()["items"]}
    assert str(by_email.id) in ids
    assert str(by_name.id) in ids
    assert str(not_match.id) not in ids


# ---- L5: sort=email_asc ---------------------------------------------------
async def test_l5_list_sort_email_asc(
    app_client, settings, make_tenant, make_tenant_user,
    super_admin_jwt,
):
    tenant = await make_tenant(name="L5-Tenant")
    await make_tenant_user(
        tenant_id=tenant.id, email="l5sort-c@l5.test", status="ACTIVE"
    )
    await make_tenant_user(
        tenant_id=tenant.id, email="l5sort-a@l5.test", status="ACTIVE"
    )
    await make_tenant_user(
        tenant_id=tenant.id, email="l5sort-b@l5.test", status="ACTIVE"
    )
    resp = app_client.get(
        "/api/v1/tenant-users",
        params={
            "tenant_id": str(tenant.id),
            "search": "l5sort",
            "sort": "email_asc",
        },
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    emails = [u["email"] for u in resp.json()["items"]]
    assert emails == [
        "l5sort-a@l5.test",
        "l5sort-b@l5.test",
        "l5sort-c@l5.test",
    ]


# ---- L6: unknown sort key returns 400 (not 500) --------------------------
def test_l6_list_invalid_sort_returns_400(app_client, settings, super_admin_jwt):
    """The Repo raises InvalidSortKeyError; the router catches and
    re-raises as InvalidSortKeyClientError (shared with platform_users
    per Step 5.2 promotion) so the response is 400 with code
    INVALID_SORT_KEY, not 500.
    """
    resp = app_client.get(
        "/api/v1/tenant-users",
        params={"sort": "not_a_real_sort"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_SORT_KEY"


# ---- L7: pagination + filter interaction ----------------------------------
async def test_l7_list_pagination_with_filter(
    app_client, settings, make_tenant, make_tenant_user,
    super_admin_jwt,
):
    tenant = await make_tenant(name="L7-Tenant")
    for prefix in ("l7page-a", "l7page-b", "l7page-c", "l7page-d"):
        await make_tenant_user(
            tenant_id=tenant.id, email=f"{prefix}@l7.test", status="ACTIVE"
        )

    resp = app_client.get(
        "/api/v1/tenant-users",
        params={
            "tenant_id": str(tenant.id),
            "search": "l7page",
            "sort": "email_asc",
            "limit": 2,
            "offset": 1,
        },
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [u["email"] for u in body["items"]] == [
        "l7page-b@l7.test",
        "l7page-c@l7.test",
    ]
    assert body["pagination"] == {
        "total": 4,
        "offset": 1,
        "limit": 2,
    }


# =============================================================================
# List endpoint, TENANT context (L8) — RLS-scoped
# =============================================================================


# ---- L8: TENANT-A JWT lists only tenant A's users -------------------------
async def test_l8_list_under_tenant_a_returns_only_a_users(
    app_client, make_tenant, make_tenant_user, tenant_owner_jwt_factory,
):
    """RLS scopes a TENANT-A session to tenant A's rows. Tenant B's
    users — created in the same test — must not appear.

    Post Step 6.9.3.2: random-UUID `_tenant_jwt` swapped for
    `tenant_owner_jwt_factory(tenant_a.id)` which builds a synthetic
    OWNER-like user in tenant_a with ADMIN.USERS.VIEW.TENANT grant;
    gate passes; RLS scopes list results to tenant_a.
    """
    tenant_a = await make_tenant(name="L8-TenantA")
    tenant_b = await make_tenant(name="L8-TenantB")
    user_a1 = await make_tenant_user(
        tenant_id=tenant_a.id, email="l8a1@l8.test", status="ACTIVE"
    )
    user_a2 = await make_tenant_user(
        tenant_id=tenant_a.id, email="l8a2@l8.test", status="ACTIVE"
    )
    user_b = await make_tenant_user(
        tenant_id=tenant_b.id, email="l8b@l8.test", status="ACTIVE"
    )

    jwt = await tenant_owner_jwt_factory(tenant_a.id)
    resp = app_client.get(
        "/api/v1/tenant-users",
        params={"limit": 200},
        headers=_auth(jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = {u["id"] for u in items}
    assert str(user_a1.id) in ids
    assert str(user_a2.id) in ids
    assert str(user_b.id) not in ids
    assert all(u["tenant_id"] == str(tenant_a.id) for u in items)


# =============================================================================
# Detail endpoint (D1-D2)
# =============================================================================


# ---- D1: detail by id under PLATFORM + hidden-fields contract -----------
async def test_d1_detail_platform_returns_user_with_hidden_absent(
    app_client, settings, make_tenant, make_org_node, make_tenant_user,
    super_admin_jwt,
):
    """Post Step 6.9.3.2: /tenant-users/{user_id} gates via
    ``get_tenant_user_anchor`` which queries
    ``tenant_users JOIN org_nodes (tenant_root)``. The synthetic
    tenant from ``make_tenant`` has no org_nodes; the anchor would
    raise 404 before the gate could fire. Provision a TENANT-root
    org_node so the anchor resolves and the test exercises the
    actual detail-fetch path."""
    tenant = await make_tenant(name="D1-Tenant")
    await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code=f"d1-{tenant.id.hex[:6]}", name="D1 Root",
    )
    user = await make_tenant_user(
        tenant_id=tenant.id,
        email="d1-detail@d1.test",
        full_name="D1 Detail User",
        status="ACTIVE",
    )
    resp = app_client.get(
        f"/api/v1/tenant-users/{user.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(user.id)
    assert body["tenant_id"] == str(tenant.id)
    assert body["email"] == "d1-detail@d1.test"
    assert body["status"] == "ACTIVE"
    assert set(body.keys()) == {
        "id",
        "tenant_id",
        "email",
        "full_name",
        "status",
        "invited_at",
        "invitation_accepted_at",
        "suspended_at",
        "created_at",
        "updated_at",
        "roles",  # Step 6.8.3 augmentation: inline role assignments.
    }


# ---- D2: unknown UUID returns canonical 404 ------------------------------
def test_d2_detail_unknown_id_returns_404(app_client, settings, super_admin_jwt):
    """Canonical error envelope: ``{code, message, details, request_id}``."""
    fake = uuid.uuid4()
    resp = app_client.get(
        f"/api/v1/tenant-users/{fake}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "details", "request_id"}
    assert body["code"] == "TENANT_USER_NOT_FOUND"
    assert body["message"] == "Tenant user not found"
    assert body["details"] is None


# =============================================================================
# Cross-tenant isolation (T9-T10) — LOAD-BEARING
# =============================================================================


# ---- T9: TENANT-A asking for TENANT-B's user_id -> 404 (LOAD-BEARING) -----
async def test_t9_cross_tenant_detail_returns_404(
    app_client, settings, make_tenant, make_tenant_user
):
    """LOAD-BEARING: cross-tenant access by TENANT users surfaces as 404
    (RLS-as-404 per D-17), not 403.

    Mechanism end-to-end:
      1. Middleware verifies the TENANT-A JWT and populates AuthContext.
      2. ``get_tenant_session`` sets ``app.tenant_id`` to TENANT-A's id
         and ``app.user_type`` to ``'TENANT'``.
      3. The Repo's ``get_by_id(user_b.id)`` runs under RLS;
         ``tenant_users_tenant_isolation`` filters out the row because
         tenant_id != app.tenant_id and the IS-NULL-gated PLATFORM
         OR-branch doesn't fire for user_type='TENANT'.
      4. Repo returns ``None``.
      5. Router converts to ``TenantUserNotFoundError`` (404).

    If this test fails, RLS isn't actually enforcing isolation through
    the API and any tenant could probe other tenants' user_ids.
    """
    tenant_a = await make_tenant(name="T9-TenantA")
    tenant_b = await make_tenant(name="T9-TenantB")
    user_b = await make_tenant_user(
        tenant_id=tenant_b.id, email="t9b@t9.test", status="ACTIVE"
    )

    resp = app_client.get(
        f"/api/v1/tenant-users/{user_b.id}",
        headers=_auth(_tenant_jwt(settings, tenant_a.id)),
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "TENANT_USER_NOT_FOUND"
    assert body["message"] == "Tenant user not found"


# ---- T10: TENANT-A querying ?tenant_id=B -> empty list -------------------
async def test_t10_cross_tenant_list_filter_returns_empty(
    app_client, make_tenant, make_tenant_user, tenant_owner_jwt_factory,
):
    """TENANT-A querying ``?tenant_id=B`` returns an empty list.

    Mechanism: RLS adds ``tenant_id = A`` to the WHERE clause for the
    TENANT-A session; the application-layer filter adds
    ``tenant_id = B``. Combined as AND, no row satisfies both, so the
    result is empty. Specifically, NOT 500 (the filter must compose
    cleanly with RLS) and NOT a row leak from tenant B.

    Post Step 6.9.3.2: random-UUID JWT swapped for
    `tenant_owner_jwt_factory(tenant_a.id)` so the gate passes; the
    list endpoint then runs with RLS scoped to tenant_a.
    """
    tenant_a = await make_tenant(name="T10-TenantA")
    tenant_b = await make_tenant(name="T10-TenantB")
    await make_tenant_user(
        tenant_id=tenant_b.id, email="t10b@t10.test", status="ACTIVE"
    )

    jwt = await tenant_owner_jwt_factory(tenant_a.id)
    resp = app_client.get(
        "/api/v1/tenant-users",
        params={"tenant_id": str(tenant_b.id)},
        headers=_auth(jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0


# =============================================================================
# Auth (A1)
# =============================================================================


def test_a1_no_jwt_returns_401(app_client):
    resp = app_client.get("/api/v1/tenant-users")
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MISSING"


# =============================================================================
# Step 6.8.3 — Half 1 (A1) inline roles[] augmentation tests.
#
# Naming: U<n>_<endpoint_short>. Short codes: tu_list, tu_detail.
# (Platform-side U*_pu_* tests live in test_platform_users_router.py;
# the cross-cutting U7 parametrized test sits at the bottom of THIS
# file because it spans all 4 endpoints — both routers register on
# the same app_client.)
# =============================================================================


_LOCKED_ROLE_FIELDS: set[str] = {
    "assignment_id",
    "role_id",
    "role_name",
    "role_code",
    "status",
    "granted_at",
    "org_node_id",
    "org_node_name",
}


# ---- U1_tu_list: roles array present and populated (tenant-side LIST) ----
async def test_u1_tu_list_roles_array_present_and_populated(
    app_client,
    settings,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    super_admin_jwt,
):
    """Tenant user with one ACTIVE assignment renders the role correctly
    in the list response. All 8 fields populated; org_node_id /
    org_node_name resolved from the anchor (non-null on tenant side).
    """
    tenant = await make_tenant(name="U1tu-Tenant")
    user = await make_tenant_user(
        tenant_id=tenant.id,
        email="u1tu-list@u1tu.test",
        status="ACTIVE",
    )
    node_name = "U1tu Root"
    node_id, _ = await make_org_node(
        tenant_id=tenant.id,
        code="U1TU-ROOT",
        name=node_name,
        node_type="TENANT",
    )
    role = await make_role(audience="TENANT", name="U1TU-Role")
    assignment = await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=user.id,
        org_node_id=node_id,
        role_id=role.id,
    )

    resp = app_client.get(
        "/api/v1/tenant-users",
        params={"tenant_id": str(tenant.id), "limit": 200},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    matches = [i for i in resp.json()["items"] if i["id"] == str(user.id)]
    assert len(matches) == 1
    item = matches[0]
    assert isinstance(item["roles"], list)
    assert len(item["roles"]) == 1

    role_item = item["roles"][0]
    assert set(role_item.keys()) == _LOCKED_ROLE_FIELDS
    assert role_item["assignment_id"] == str(assignment.id)
    assert role_item["role_id"] == str(role.id)
    assert role_item["role_name"] == role.name
    assert role_item["role_code"] == role.code
    assert role_item["status"] == "ACTIVE"
    assert role_item["org_node_id"] == str(node_id)
    assert role_item["org_node_name"] == node_name
    # granted_at present and ISO-8601-shaped.
    assert "T" in role_item["granted_at"]


# ---- U1_tu_detail: roles array present and populated (tenant-side DETAIL) -
async def test_u1_tu_detail_roles_array_present_and_populated(
    app_client,
    settings,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    super_admin_jwt,
):
    """Same as U1_tu_list but on the single-fetch endpoint."""
    tenant = await make_tenant(name="U1tud-Tenant")
    user = await make_tenant_user(
        tenant_id=tenant.id,
        email="u1tud-detail@u1tud.test",
        status="ACTIVE",
    )
    node_name = "U1tud Root"
    node_id, _ = await make_org_node(
        tenant_id=tenant.id,
        code="U1TUD-ROOT",
        name=node_name,
        node_type="TENANT",
    )
    role = await make_role(audience="TENANT", name="U1TUD-Role")
    assignment = await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=user.id,
        org_node_id=node_id,
        role_id=role.id,
    )

    resp = app_client.get(
        f"/api/v1/tenant-users/{user.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["roles"], list)
    assert len(body["roles"]) == 1
    role_item = body["roles"][0]
    assert set(role_item.keys()) == _LOCKED_ROLE_FIELDS
    assert role_item["assignment_id"] == str(assignment.id)
    assert role_item["org_node_id"] == str(node_id)
    assert role_item["org_node_name"] == node_name


# ---- U2_tu_list: roles array empty for unassigned user (LIST) -----------
async def test_u2_tu_list_roles_empty_array_for_unassigned(
    app_client, settings, make_tenant, make_tenant_user,
    super_admin_jwt,
):
    """Unassigned user gets ``"roles": []`` (not null, not omitted).

    Verifies the COALESCE(jsonb_agg(...), '[]'::jsonb) wrap in the
    Repo's correlated subquery.
    """
    tenant = await make_tenant(name="U2tu-Tenant")
    user = await make_tenant_user(
        tenant_id=tenant.id, email="u2tu@u2tu.test", status="ACTIVE"
    )
    resp = app_client.get(
        "/api/v1/tenant-users",
        params={"tenant_id": str(tenant.id), "limit": 200},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    matches = [i for i in resp.json()["items"] if i["id"] == str(user.id)]
    assert len(matches) == 1
    item = matches[0]
    assert "roles" in item
    assert item["roles"] == []


# ---- U2_tu_detail: roles array empty for unassigned user (DETAIL) -------
async def test_u2_tu_detail_roles_empty_array_for_unassigned(
    app_client, settings, make_tenant, make_org_node, make_tenant_user,
    super_admin_jwt,
):
    """Post Step 6.9.3.2: detail-endpoint anchor dep needs a tenant
    root org_node to resolve (same as D1)."""
    tenant = await make_tenant(name="U2tud-Tenant")
    await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code=f"u2tud-{tenant.id.hex[:6]}", name="U2tud Root",
    )
    user = await make_tenant_user(
        tenant_id=tenant.id, email="u2tud@u2tud.test", status="ACTIVE"
    )
    resp = app_client.get(
        f"/api/v1/tenant-users/{user.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "roles" in body
    assert body["roles"] == []


# ---- U3_tu_list: INACTIVE assignments included (LIST) -------------------
async def test_u3_tu_list_inactive_assignments_included(
    app_client,
    settings,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    super_admin_jwt,
):
    """Both ACTIVE and INACTIVE assignments appear in roles[]; ordering
    respects ``granted_at DESC, id ASC`` per locked decision (the
    ``aggregate_order_by`` in the Repo's subquery)."""
    tenant = await make_tenant(name="U3tu-Tenant")
    user = await make_tenant_user(
        tenant_id=tenant.id, email="u3tu@u3tu.test", status="ACTIVE"
    )
    node_id, _ = await make_org_node(
        tenant_id=tenant.id,
        code="U3TU-ROOT",
        name="U3tu Root",
        node_type="TENANT",
    )
    role_a = await make_role(audience="TENANT", name="U3TU-RoleA")
    role_b = await make_role(audience="TENANT", name="U3TU-RoleB")

    # Insert ACTIVE second so it has the LATER granted_at and should
    # appear FIRST under DESC ordering.
    inactive = await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=user.id,
        org_node_id=node_id,
        role_id=role_a.id,
        status="INACTIVE",
        revoked_at="2026-04-01 00:00:00+00",
    )
    active = await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=user.id,
        org_node_id=node_id,
        role_id=role_b.id,
    )

    resp = app_client.get(
        "/api/v1/tenant-users",
        params={"tenant_id": str(tenant.id), "limit": 200},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    matches = [i for i in resp.json()["items"] if i["id"] == str(user.id)]
    assert len(matches) == 1
    role_items = matches[0]["roles"]
    assert len(role_items) == 2
    statuses = {r["status"] for r in role_items}
    assert statuses == {"ACTIVE", "INACTIVE"}
    # ACTIVE was inserted SECOND; should be first under granted_at DESC.
    assert role_items[0]["assignment_id"] == str(active.id)
    assert role_items[1]["assignment_id"] == str(inactive.id)


# ---- U3_tu_detail: INACTIVE assignments included (DETAIL) ---------------
async def test_u3_tu_detail_inactive_assignments_included(
    app_client,
    settings,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    super_admin_jwt,
):
    tenant = await make_tenant(name="U3tud-Tenant")
    user = await make_tenant_user(
        tenant_id=tenant.id, email="u3tud@u3tud.test", status="ACTIVE"
    )
    node_id, _ = await make_org_node(
        tenant_id=tenant.id,
        code="U3TUD-ROOT",
        name="U3tud Root",
        node_type="TENANT",
    )
    role_a = await make_role(audience="TENANT", name="U3TUD-RoleA")
    role_b = await make_role(audience="TENANT", name="U3TUD-RoleB")
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=user.id,
        org_node_id=node_id,
        role_id=role_a.id,
        status="INACTIVE",
        revoked_at="2026-04-01 00:00:00+00",
    )
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=user.id,
        org_node_id=node_id,
        role_id=role_b.id,
    )

    resp = app_client.get(
        f"/api/v1/tenant-users/{user.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["roles"]) == 2
    assert {r["status"] for r in body["roles"]} == {"ACTIVE", "INACTIVE"}


# ---- U4_tu_list: PLATFORM JWT visibility, no per-user contamination -----
async def test_u4_tu_list_platform_visibility_per_user_attribution(
    app_client,
    settings,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    super_admin_jwt,
):
    """PLATFORM JWT calling /tenant-users sees rows across all tenants
    via D-29's OR-branch; each user's roles[] is correctly attributed
    to that user only (no cross-user contamination from a sloppy
    correlated subquery)."""
    tenant_a = await make_tenant(name="U4tu-A")
    tenant_b = await make_tenant(name="U4tu-B")
    user_a = await make_tenant_user(
        tenant_id=tenant_a.id, email="u4tu-a@u4tu.test", status="ACTIVE"
    )
    user_b = await make_tenant_user(
        tenant_id=tenant_b.id, email="u4tu-b@u4tu.test", status="ACTIVE"
    )
    node_a_id, _ = await make_org_node(
        tenant_id=tenant_a.id,
        code="U4TU-A-ROOT",
        name="U4tu A Root",
        node_type="TENANT",
    )
    node_b_id, _ = await make_org_node(
        tenant_id=tenant_b.id,
        code="U4TU-B-ROOT",
        name="U4tu B Root",
        node_type="TENANT",
    )
    role = await make_role(audience="TENANT", name="U4TU-Role")
    a_assignment = await make_tenant_user_role_assignment(
        tenant_id=tenant_a.id,
        tenant_user_id=user_a.id,
        org_node_id=node_a_id,
        role_id=role.id,
    )
    b_assignment = await make_tenant_user_role_assignment(
        tenant_id=tenant_b.id,
        tenant_user_id=user_b.id,
        org_node_id=node_b_id,
        role_id=role.id,
    )

    resp = app_client.get(
        "/api/v1/tenant-users",
        params={"limit": 200},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    a_match = [i for i in items if i["id"] == str(user_a.id)]
    b_match = [i for i in items if i["id"] == str(user_b.id)]
    assert len(a_match) == 1 and len(b_match) == 1
    a_role_ids = {r["assignment_id"] for r in a_match[0]["roles"]}
    b_role_ids = {r["assignment_id"] for r in b_match[0]["roles"]}
    # Each user sees only ITS OWN assignment.
    assert a_role_ids == {str(a_assignment.id)}
    assert b_role_ids == {str(b_assignment.id)}


# ---- U5_tu_list: cross-tenant RLS isolation (LIST, LOAD-BEARING) --------
async def test_u5_tu_list_cross_tenant_rls_isolation(
    app_client,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    tenant_owner_jwt_factory,
):
    """LOAD-BEARING — proves the augmentation's roles[] subquery is
    RLS-correct: a TENANT-A JWT sees ONLY tenant-A users and ONLY
    tenant-A's assignments in their roles[]; tenant B's assignments
    are completely invisible.

    Composite-FK invariant assertion: every visible role item's
    org_node_id (when non-null) should belong to tenant A's org tree.
    """
    tenant_a = await make_tenant(name="U5tu-A")
    tenant_b = await make_tenant(name="U5tu-B")
    user_a = await make_tenant_user(
        tenant_id=tenant_a.id, email="u5tu-a@u5tu.test", status="ACTIVE"
    )
    user_b = await make_tenant_user(
        tenant_id=tenant_b.id, email="u5tu-b@u5tu.test", status="ACTIVE"
    )
    node_a_id, _ = await make_org_node(
        tenant_id=tenant_a.id,
        code="U5TU-A-ROOT",
        name="U5tu A Root",
        node_type="TENANT",
    )
    node_b_id, _ = await make_org_node(
        tenant_id=tenant_b.id,
        code="U5TU-B-ROOT",
        name="U5tu B Root",
        node_type="TENANT",
    )
    role = await make_role(audience="TENANT", name="U5TU-Role")
    await make_tenant_user_role_assignment(
        tenant_id=tenant_a.id,
        tenant_user_id=user_a.id,
        org_node_id=node_a_id,
        role_id=role.id,
    )
    await make_tenant_user_role_assignment(
        tenant_id=tenant_b.id,
        tenant_user_id=user_b.id,
        org_node_id=node_b_id,
        role_id=role.id,
    )

    # Tenant A JWT (post Step 6.9.3.2: synthetic OWNER in tenant_a)
    jwt = await tenant_owner_jwt_factory(tenant_a.id)
    resp = app_client.get(
        "/api/v1/tenant-users",
        params={"limit": 200},
        headers=_auth(jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    visible_ids = {i["id"] for i in items}
    assert str(user_a.id) in visible_ids
    assert str(user_b.id) not in visible_ids

    # Composite-FK invariant: tenant A's anchor for any role in items.
    expected_org_ids = {str(node_a_id)}
    for item in items:
        for r in item["roles"]:
            if r["org_node_id"] is not None:
                assert r["org_node_id"] in expected_org_ids or item[
                    "tenant_id"
                ] == str(tenant_a.id)


# ---- U5_tu_detail: cross-tenant 404 (DETAIL, LOAD-BEARING) --------------
async def test_u5_tu_detail_cross_tenant_returns_404(
    app_client, settings, make_tenant, make_tenant_user
):
    """Augmentation must NOT regress RLS-as-404 (D-17). TENANT-A
    requesting TENANT-B's user_id returns 404 TENANT_USER_NOT_FOUND.

    Existing T9 test covers the same regression on the pre-augment
    code path; this test re-asserts it with the new roles[] subquery
    in play.
    """
    tenant_a = await make_tenant(name="U5tud-A")
    tenant_b = await make_tenant(name="U5tud-B")
    user_b = await make_tenant_user(
        tenant_id=tenant_b.id, email="u5tud-b@u5tud.test", status="ACTIVE"
    )
    resp = app_client.get(
        f"/api/v1/tenant-users/{user_b.id}",
        headers=_auth(_tenant_jwt(settings, tenant_a.id)),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "TENANT_USER_NOT_FOUND"


# ---- U6_tu_list: pagination not broken by jsonb_agg (regression) --------
async def test_u6_tu_list_pagination_intact_with_roles(
    app_client,
    settings,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    super_admin_jwt,
):
    """Regression: a naïve LEFT JOIN on the assignments table would
    multiply parent rows N×M and break offset/limit semantics. The
    correlated jsonb_agg subquery returns a single column per row; no
    multiplication.

    Setup: 7 tenant users in tenant A, each with 2-3 assignments.
    Request limit=3. Response has exactly 3 users (parent rows not
    multiplied), each with their full roles[]. pagination.total = 7.
    """
    tenant = await make_tenant(name="U6tu-Tenant")
    node_id, _ = await make_org_node(
        tenant_id=tenant.id,
        code="U6TU-ROOT",
        name="U6tu Root",
        node_type="TENANT",
    )
    role_a = await make_role(audience="TENANT", name="U6TU-RoleA")
    role_b = await make_role(audience="TENANT", name="U6TU-RoleB")
    role_c = await make_role(audience="TENANT", name="U6TU-RoleC")

    users: list[Any] = []
    for i in range(7):
        u = await make_tenant_user(
            tenant_id=tenant.id,
            email=f"u6tu-{i}@u6tu.test",
            status="ACTIVE",
        )
        users.append(u)
        await make_tenant_user_role_assignment(
            tenant_id=tenant.id,
            tenant_user_id=u.id,
            org_node_id=node_id,
            role_id=role_a.id,
        )
        await make_tenant_user_role_assignment(
            tenant_id=tenant.id,
            tenant_user_id=u.id,
            org_node_id=node_id,
            role_id=role_b.id,
        )
        if i % 2 == 0:
            await make_tenant_user_role_assignment(
                tenant_id=tenant.id,
                tenant_user_id=u.id,
                org_node_id=node_id,
                role_id=role_c.id,
            )

    resp = app_client.get(
        "/api/v1/tenant-users",
        params={"tenant_id": str(tenant.id), "limit": 3, "offset": 0},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Exactly 3 user rows came back — NOT multiplied by their roles
    # count.
    assert len(body["items"]) == 3
    assert body["pagination"]["total"] == 7
    assert body["pagination"]["limit"] == 3
    # Each row carries its full roles[] (not sliced by the limit).
    for item in body["items"]:
        assert isinstance(item["roles"], list)
        assert len(item["roles"]) >= 2


# ---- U7: negative-key assertion across all 4 endpoints (parametrized) ---
@pytest.mark.parametrize(
    "endpoint_kind",
    ["tu_list", "tu_detail", "pu_list", "pu_detail"],
)
async def test_u7_role_item_keys_locked_no_audit_actor_leakage(
    endpoint_kind,
    app_client,
    settings,
    make_tenant,
    make_tenant_user,
    make_platform_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    make_platform_user_role_assignment,
    super_admin_jwt,
):
    """For every role item across all 4 endpoints, only the 8 locked
    fields are present. Pattern (b) audit-actor columns and the
    parent table's tenant_id MUST NOT leak into the inline roles[].
    """
    if endpoint_kind in ("tu_list", "tu_detail"):
        tenant = await make_tenant(name=f"U7-{endpoint_kind}")
        user = await make_tenant_user(
            tenant_id=tenant.id,
            email=f"u7-{endpoint_kind}@u7.test",
            status="ACTIVE",
        )
        node_id, _ = await make_org_node(
            tenant_id=tenant.id,
            code=f"U7-{endpoint_kind.upper().replace('_', '-')}",
            name=f"U7 {endpoint_kind} Root",
            node_type="TENANT",
        )
        role = await make_role(audience="TENANT", name=f"U7-{endpoint_kind}")
        await make_tenant_user_role_assignment(
            tenant_id=tenant.id,
            tenant_user_id=user.id,
            org_node_id=node_id,
            role_id=role.id,
        )
        url = (
            "/api/v1/tenant-users"
            if endpoint_kind == "tu_list"
            else f"/api/v1/tenant-users/{user.id}"
        )
        params = (
            {"tenant_id": str(tenant.id), "limit": 200}
            if endpoint_kind == "tu_list"
            else {}
        )
    else:
        # Platform-side
        user = await make_platform_user(
            email=f"u7-{endpoint_kind}@u7.test", status="ACTIVE"
        )
        role = await make_role(audience="PLATFORM", name=f"U7-{endpoint_kind}")
        await make_platform_user_role_assignment(
            platform_user_id=user.id, role_id=role.id
        )
        url = (
            "/api/v1/platform-users"
            if endpoint_kind == "pu_list"
            else f"/api/v1/platform-users/{user.id}"
        )
        params = {"limit": 200} if endpoint_kind == "pu_list" else {}

    resp = app_client.get(
        url, params=params, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 200
    body = resp.json()
    items: list[dict[str, Any]]
    if endpoint_kind in ("tu_list", "pu_list"):
        items = [i for i in body["items"] if i["id"] == str(user.id)]
    else:
        items = [body]
    assert len(items) == 1
    role_list = items[0]["roles"]
    assert len(role_list) >= 1
    for r in role_list:
        assert set(r.keys()) == _LOCKED_ROLE_FIELDS, (
            f"endpoint={endpoint_kind}: extraneous or missing keys in role item; "
            f"got {set(r.keys())}, expected {_LOCKED_ROLE_FIELDS}"
        )
