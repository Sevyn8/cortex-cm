"""Integration tests for the platform_users router (Step 5.1).

Real Postgres, real schema, real router via FastAPI's TestClient. JWTs
minted via Step 2.1's ``make_test_jwt``. Mirrors the shape used by
``test_tenants_router.py``.

Coverage:

  L1-L6:  list endpoint (happy, status filter, search, sort, invalid
          sort -> 400, pagination).
  D1-D2:  detail endpoint (happy + hidden-fields contract; 404).
  A1-A2:  auth (no JWT; TENANT JWT -> 403 PERMISSION_DENIED —
          load-bearing for the v0 binary user_type gate).

Tests use the existing ``make_platform_user`` factory (per Step 3.4.5)
to insert known rows. Cleanup is by the factory's teardown DELETE.
``platform_users`` has no RLS, so factory rows are visible to both
PLATFORM and TENANT sessions for reads — the auth gate sits at the
router layer, not the DB.
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


_UUID_RE = __import__("re").compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,  # type: ignore[no-any-unimported]
    session_factory: Any,  # type: ignore[no-any-unimported]
) -> Iterator[TestClient]:
    """TestClient against a real app with real engine/session_factory.

    Bypasses the lifespan (which would re-construct an engine in a
    different event loop than the test). Mirrors the pattern from
    ``test_tenants_router.py``.
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
# List endpoint (L1-L6)
# =============================================================================


# ---- L1: PLATFORM happy path, response shape -------------------------------
async def test_l1_list_platform_returns_envelope(
    app_client, settings, make_platform_user,
    super_admin_jwt,
):
    """List response is ``{items, pagination}`` per D-30; items expose
    only the public field set; audit-actor IDs and ``auth0_sub`` are
    absent.
    """
    await make_platform_user(
        email="l1-active@ithina.test",
        full_name="L1 Active User",
        status="ACTIVE",
    )
    resp = app_client.get(
        "/api/v1/platform-users",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "pagination" in body
    assert set(body["pagination"].keys()) == {"total", "offset", "limit"}
    assert body["pagination"]["total"] >= 1

    # Pick our known item and verify the public field set.
    matches = [i for i in body["items"] if i["email"] == "l1-active@ithina.test"]
    assert len(matches) == 1
    item = matches[0]
    assert set(item.keys()) == {
        "id",
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
    assert "auth0_sub" not in item
    assert "created_by_user_id" not in item
    assert "updated_by_user_id" not in item
    assert "suspended_by_user_id" not in item


# ---- L2: status filter ------------------------------------------------------
async def test_l2_list_filter_by_status(
    app_client, settings, make_platform_user,
    super_admin_jwt,
):
    invited = await make_platform_user(
        email="l2-invited@ithina.test", status="INVITED"
    )
    active = await make_platform_user(
        email="l2-active@ithina.test", status="ACTIVE"
    )
    resp = app_client.get(
        "/api/v1/platform-users",
        params={"status": "ACTIVE", "limit": 200},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(u["status"] == "ACTIVE" for u in items)
    ids = {u["id"] for u in items}
    assert str(active.id) in ids
    assert str(invited.id) not in ids


# ---- L3: search across email and full_name --------------------------------
async def test_l3_list_search_matches_email_and_full_name(
    app_client, settings, make_platform_user,
    super_admin_jwt,
):
    by_email = await make_platform_user(
        email="l3uniqueprefix@ithina.test",
        full_name="Some Other Name",
        status="ACTIVE",
    )
    by_name = await make_platform_user(
        email="someone-else@ithina.test",
        full_name="L3uniqueprefix Person",
        status="ACTIVE",
    )
    not_match = await make_platform_user(
        email="unrelated@ithina.test",
        full_name="Unrelated User",
        status="ACTIVE",
    )
    resp = app_client.get(
        "/api/v1/platform-users",
        params={"search": "l3uniqueprefix"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    ids = {u["id"] for u in resp.json()["items"]}
    assert str(by_email.id) in ids
    assert str(by_name.id) in ids
    assert str(not_match.id) not in ids


# ---- L4: sort=email_asc ----------------------------------------------------
async def test_l4_list_sort_email_asc(
    app_client, settings, make_platform_user,
    super_admin_jwt,
):
    """Restrict the result set with a unique search prefix so the assert
    isn't fragile against unrelated rows in the table.
    """
    await make_platform_user(
        email="l4sort-c@ithina.test", status="ACTIVE"
    )
    await make_platform_user(
        email="l4sort-a@ithina.test", status="ACTIVE"
    )
    await make_platform_user(
        email="l4sort-b@ithina.test", status="ACTIVE"
    )
    resp = app_client.get(
        "/api/v1/platform-users",
        params={"search": "l4sort", "sort": "email_asc"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    emails = [u["email"] for u in resp.json()["items"]]
    assert emails == [
        "l4sort-a@ithina.test",
        "l4sort-b@ithina.test",
        "l4sort-c@ithina.test",
    ]


# ---- L5: unknown sort key returns 400 (not 500) ---------------------------
def test_l5_list_invalid_sort_returns_400(app_client, settings, super_admin_jwt):
    """The Repo raises InvalidSortKeyError (a ValueError); the router
    catches and re-raises as InvalidSortKeyClientError so the response
    is 400 with code INVALID_SORT_KEY, not 500 INTERNAL_ERROR.
    """
    resp = app_client.get(
        "/api/v1/platform-users",
        params={"sort": "not_a_real_sort"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_SORT_KEY"


# ---- L6: pagination + filter interaction -----------------------------------
async def test_l6_list_pagination_with_search(
    app_client, settings, make_platform_user,
    super_admin_jwt,
):
    """Filter applies to BOTH the total count and the page query, so
    ``pagination.total`` matches the filtered set, not the unfiltered
    table.
    """
    for prefix in ("l6page-a", "l6page-b", "l6page-c", "l6page-d"):
        await make_platform_user(
            email=f"{prefix}@ithina.test", status="ACTIVE"
        )

    resp = app_client.get(
        "/api/v1/platform-users",
        params={
            "search": "l6page",
            "sort": "email_asc",
            "limit": 2,
            "offset": 1,
        },
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [u["email"] for u in body["items"]] == [
        "l6page-b@ithina.test",
        "l6page-c@ithina.test",
    ]
    assert body["pagination"] == {
        "total": 4,
        "offset": 1,
        "limit": 2,
    }


# =============================================================================
# Detail endpoint (D1-D2)
# =============================================================================


# ---- D1: happy path + hidden-fields contract ------------------------------
async def test_d1_detail_returns_user_with_hidden_fields_absent(
    app_client, settings, make_platform_user,
    super_admin_jwt,
):
    user = await make_platform_user(
        email="d1-detail@ithina.test",
        full_name="D1 Detail User",
        status="ACTIVE",
    )
    resp = app_client.get(
        f"/api/v1/platform-users/{user.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(user.id)
    assert body["email"] == "d1-detail@ithina.test"
    assert body["full_name"] == "D1 Detail User"
    assert body["status"] == "ACTIVE"
    # Public field set is exhaustive — no extra leakage.
    assert set(body.keys()) == {
        "id",
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


# ---- D2: unknown UUID returns canonical 404 -------------------------------
def test_d2_detail_unknown_id_returns_404(app_client, settings, super_admin_jwt):
    """Canonical error envelope: ``{code, message, details, request_id}``."""
    fake = uuid.uuid4()
    resp = app_client.get(
        f"/api/v1/platform-users/{fake}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "details", "request_id"}
    assert body["code"] == "PLATFORM_USER_NOT_FOUND"
    assert body["message"] == "Platform user not found"
    assert body["details"] is None
    assert _UUID_RE.match(body["request_id"])


# =============================================================================
# Auth (A1-A2). A2 is load-bearing for the v0 binary user_type gate.
# =============================================================================


def test_a1_no_jwt_returns_401(app_client):
    resp = app_client.get("/api/v1/platform-users")
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MISSING"


def test_a2_tenant_jwt_returns_403_permission_denied(
    app_client, settings
):
    """Load-bearing v0 auth-gate assertion.

    A TENANT JWT must NOT be able to read the platform_users directory.
    ``platform_users`` has no RLS — without this assertion, a regression
    that drops the ``Depends(require(ADMIN.USERS.VIEW.GLOBAL))`` gate
    would expose Ithina staff identities to tenant users undetected.

    Post-Step-6.9.3.2: the gate is the per-permission resolver, not the
    binary ``_require_platform_auth`` helper. Error code/message updated
    accordingly. Test behavior unchanged.

    The tenant_id is synthetic (no FK from the JWT to tenants); the
    middleware accepts any well-formed UUID as tenant_id, then the
    router gate fires before any DB call lands.
    """
    synthetic_tenant_id = uuid.uuid4()
    resp = app_client.get(
        "/api/v1/platform-users",
        headers=_auth(_tenant_jwt(settings, synthetic_tenant_id)),
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "PERMISSION_DENIED"
    assert body["message"] == "Permission denied"


# =============================================================================
# Step 6.8.3 — Half 1 (A2) inline roles[] augmentation tests for the
# platform-users endpoints. Naming: U<n>_pu_<short>.
#
# Platform-side assignments have NO org-node anchor. The Repo's
# subquery emits ``org_node_id: null`` and ``org_node_name: null``
# literally so the wire shape stays uniform with tenant-side; the
# tests assert that explicitly.
# =============================================================================


_LOCKED_ROLE_FIELDS_PU: set[str] = {
    "assignment_id",
    "role_id",
    "role_name",
    "role_code",
    "status",
    "granted_at",
    "org_node_id",
    "org_node_name",
}


# ---- U1_pu_list: roles array present and populated (LIST) ---------------
async def test_u1_pu_list_roles_array_present_and_populated(
    app_client,
    settings,
    make_platform_user,
    make_role,
    make_platform_user_role_assignment,
    super_admin_jwt,
):
    """Platform user with one ACTIVE assignment renders the role
    correctly. ``org_node_id`` and ``org_node_name`` MUST be null
    (no org-node anchor on platform-side assignments)."""
    user = await make_platform_user(
        email="u1pu-list@u1pu.test",
        full_name="U1pu List User",
        status="ACTIVE",
    )
    role = await make_role(audience="PLATFORM", name="U1PU-Role")
    assignment = await make_platform_user_role_assignment(
        platform_user_id=user.id, role_id=role.id,
    )

    resp = app_client.get(
        "/api/v1/platform-users",
        params={"limit": 200},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    matches = [i for i in resp.json()["items"] if i["id"] == str(user.id)]
    assert len(matches) == 1
    item = matches[0]
    assert isinstance(item["roles"], list)
    assert len(item["roles"]) == 1
    role_item = item["roles"][0]
    assert set(role_item.keys()) == _LOCKED_ROLE_FIELDS_PU
    assert role_item["assignment_id"] == str(assignment.id)
    assert role_item["role_id"] == str(role.id)
    assert role_item["status"] == "ACTIVE"
    # Platform-side: org_node_* are explicitly null (key present).
    assert role_item["org_node_id"] is None
    assert role_item["org_node_name"] is None


# ---- U1_pu_detail: roles array present and populated (DETAIL) -----------
async def test_u1_pu_detail_roles_array_present_and_populated(
    app_client,
    settings,
    make_platform_user,
    make_role,
    make_platform_user_role_assignment,
    super_admin_jwt,
):
    user = await make_platform_user(
        email="u1pu-detail@u1pu.test",
        full_name="U1pu Detail User",
        status="ACTIVE",
    )
    role = await make_role(audience="PLATFORM", name="U1PUD-Role")
    assignment = await make_platform_user_role_assignment(
        platform_user_id=user.id, role_id=role.id,
    )
    resp = app_client.get(
        f"/api/v1/platform-users/{user.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["roles"], list)
    assert len(body["roles"]) == 1
    role_item = body["roles"][0]
    assert set(role_item.keys()) == _LOCKED_ROLE_FIELDS_PU
    assert role_item["assignment_id"] == str(assignment.id)
    assert role_item["org_node_id"] is None
    assert role_item["org_node_name"] is None


# ---- U2_pu_list: empty roles array (LIST) -------------------------------
async def test_u2_pu_list_roles_empty_array_for_unassigned(
    app_client, settings, make_platform_user,
    super_admin_jwt,
):
    user = await make_platform_user(
        email="u2pu@u2pu.test", status="ACTIVE"
    )
    resp = app_client.get(
        "/api/v1/platform-users",
        params={"limit": 200},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    matches = [i for i in resp.json()["items"] if i["id"] == str(user.id)]
    assert len(matches) == 1
    assert "roles" in matches[0]
    assert matches[0]["roles"] == []


# ---- U2_pu_detail: empty roles array (DETAIL) ---------------------------
async def test_u2_pu_detail_roles_empty_array_for_unassigned(
    app_client, settings, make_platform_user,
    super_admin_jwt,
):
    user = await make_platform_user(
        email="u2pud@u2pud.test", status="ACTIVE"
    )
    resp = app_client.get(
        f"/api/v1/platform-users/{user.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "roles" in body
    assert body["roles"] == []


# ---- U3_pu_list: INACTIVE assignments included (LIST) -------------------
async def test_u3_pu_list_inactive_assignments_included(
    app_client,
    settings,
    make_platform_user,
    make_role,
    make_platform_user_role_assignment,
    super_admin_jwt,
):
    user = await make_platform_user(
        email="u3pu@u3pu.test", status="ACTIVE"
    )
    role_a = await make_role(audience="PLATFORM", name="U3PU-RoleA")
    role_b = await make_role(audience="PLATFORM", name="U3PU-RoleB")

    inactive = await make_platform_user_role_assignment(
        platform_user_id=user.id,
        role_id=role_a.id,
        status="INACTIVE",
        revoked_at="2026-04-01 00:00:00+00",
    )
    active = await make_platform_user_role_assignment(
        platform_user_id=user.id, role_id=role_b.id,
    )

    resp = app_client.get(
        "/api/v1/platform-users",
        params={"limit": 200},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    matches = [i for i in resp.json()["items"] if i["id"] == str(user.id)]
    assert len(matches) == 1
    role_items = matches[0]["roles"]
    assert len(role_items) == 2
    assert {r["status"] for r in role_items} == {"ACTIVE", "INACTIVE"}
    # ACTIVE inserted second; first under granted_at DESC.
    assert role_items[0]["assignment_id"] == str(active.id)
    assert role_items[1]["assignment_id"] == str(inactive.id)


# ---- U3_pu_detail: INACTIVE assignments included (DETAIL) ---------------
async def test_u3_pu_detail_inactive_assignments_included(
    app_client,
    settings,
    make_platform_user,
    make_role,
    make_platform_user_role_assignment,
    super_admin_jwt,
):
    user = await make_platform_user(
        email="u3pud@u3pud.test", status="ACTIVE"
    )
    role_a = await make_role(audience="PLATFORM", name="U3PUD-RoleA")
    role_b = await make_role(audience="PLATFORM", name="U3PUD-RoleB")
    await make_platform_user_role_assignment(
        platform_user_id=user.id,
        role_id=role_a.id,
        status="INACTIVE",
        revoked_at="2026-04-01 00:00:00+00",
    )
    await make_platform_user_role_assignment(
        platform_user_id=user.id, role_id=role_b.id,
    )
    resp = app_client.get(
        f"/api/v1/platform-users/{user.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["roles"]) == 2
    assert {r["status"] for r in body["roles"]} == {"ACTIVE", "INACTIVE"}


# ---- U6_pu_list: pagination not broken by jsonb_agg (regression) --------
async def test_u6_pu_list_pagination_intact_with_roles(
    app_client,
    settings,
    make_platform_user,
    make_role,
    make_platform_user_role_assignment,
    super_admin_jwt,
):
    """Same regression as U6_tu_list, on the platform-users surface.

    7 platform users each with 2-3 assignments; limit=3 returns
    exactly 3 user rows (parent rows not multiplied), pagination.total
    >= 7 (account for any seeded platform_users from the fresh-reseed
    state).
    """
    role_a = await make_role(audience="PLATFORM", name="U6PU-RoleA")
    role_b = await make_role(audience="PLATFORM", name="U6PU-RoleB")
    role_c = await make_role(audience="PLATFORM", name="U6PU-RoleC")

    fixture_users: list[Any] = []
    for i in range(7):
        u = await make_platform_user(
            email=f"u6pu-{i}@u6pu.test", status="ACTIVE"
        )
        fixture_users.append(u)
        await make_platform_user_role_assignment(
            platform_user_id=u.id, role_id=role_a.id
        )
        await make_platform_user_role_assignment(
            platform_user_id=u.id, role_id=role_b.id
        )
        if i % 2 == 0:
            await make_platform_user_role_assignment(
                platform_user_id=u.id, role_id=role_c.id
            )

    resp = app_client.get(
        "/api/v1/platform-users",
        params={"limit": 3, "offset": 0, "sort": "created_at_desc"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Exactly 3 user rows (NOT multiplied by their roles count).
    assert len(body["items"]) == 3
    # Total includes our 7 plus any seeded platform_users.
    assert body["pagination"]["total"] >= 7
    assert body["pagination"]["limit"] == 3
    # Each row carries its full roles[] (not sliced by the limit).
    # The fresh-reseeded platform_users may not have any roles, so
    # only assert against rows we know we created.
    fixture_ids = {str(u.id) for u in fixture_users}
    fixture_returned = [i for i in body["items"] if i["id"] in fixture_ids]
    for item in fixture_returned:
        assert isinstance(item["roles"], list)
        assert len(item["roles"]) >= 2
