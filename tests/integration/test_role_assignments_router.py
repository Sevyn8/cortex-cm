"""Integration tests for /role-assignments router (Step 6.8.3 — Half 2).

15 tests (R1-R15). Five LOAD-BEARING:

  R2  TENANT JWT does NOT see platform_user_role_assignments rows
      (security-load-bearing: platform-side has no RLS; app-layer
      routing is the only barrier).
  R8  Cross-tenant injection rejection at DB layer (composite FK
      from Step 6.8.1 D-34 / AI-RBAC-06).
  R12 PLATFORM no-impersonation regression (FN-AB-14 anti-pattern
      retired in 6.8.1; PLATFORM JWT sees both tables in one query
      without per-row impersonation).

Plus:
  R3  TENANT JWT sees own-tenant tenant_assignments only (RLS).
  R7  PLATFORM JWT can filter by tenant_id (the new filter from
      deliverable #5).
"""
import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings, get_settings
from admin_backend.main import create_app


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,  # type: ignore[no-any-unimported]
    session_factory: Any,  # type: ignore[no-any-unimported]
) -> Iterator[TestClient]:
    """TestClient bypassing the lifespan; mirrors other router-test
    fixtures."""
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


# =============================================================================
# R1: PLATFORM JWT — both blocks populated against seed data.
# =============================================================================


def test_r1_platform_jwt_both_blocks_populated(app_client, settings, super_admin_jwt):
    """Seed counts post-fresh-reseed: 3 PLATFORM + 19 TENANT
    assignments. Both blocks return populated items and counts under
    a PLATFORM JWT.
    """
    resp = app_client.get(
        "/api/v1/role-assignments?limit=200",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "platform_assignments" in body
    assert "tenant_assignments" in body
    assert body["platform_assignments"]["pagination"]["total"] >= 3
    assert body["tenant_assignments"]["pagination"]["total"] >= 19
    assert len(body["platform_assignments"]["items"]) >= 3
    assert len(body["tenant_assignments"]["items"]) >= 19


# =============================================================================
# R2 (LOAD-BEARING SECURITY): TENANT JWT short-circuits platform-side query.
# =============================================================================


async def test_r2_tenant_jwt_does_not_see_platform_assignments(
    app_client, make_tenant, tenant_owner_jwt_factory,
):
    """LOAD-BEARING: TENANT JWT response has empty platform_assignments
    block AND the platform-side Repo method was NOT invoked.

    platform_user_role_assignments has NO RLS (per Step 6.8.1 D-34).
    The router's app-layer routing is the only barrier preventing
    a TENANT JWT from seeing every platform-side assignment in the
    DB. We assert BOTH the response shape AND the no-call invariant
    (via patch on the Repo method).

    Post Step 6.9.3.2: JWT switched from random-UUID `_tenant_jwt` to
    `tenant_owner_jwt_factory` which builds a synthetic OWNER-like
    user with ADMIN.USERS.VIEW.TENANT grant in the tenant; gate
    passes via direct grant; the no-call invariant remains the
    load-bearing assertion.
    """
    tenant = await make_tenant(name="R2-Tenant")
    jwt = await tenant_owner_jwt_factory(tenant.id)
    # Patch ``list_platform_assignments`` on the singleton Repo so
    # we can assert it was NEVER called for a TENANT JWT.
    from admin_backend.routers.v1 import role_assignments as router_module

    with patch.object(
        router_module._repo,
        "list_platform_assignments",
        new=AsyncMock(),
    ) as mock_platform_list:
        resp = app_client.get(
            "/api/v1/role-assignments?limit=200",
            headers=_auth(jwt),
        )

    assert resp.status_code == 200
    body = resp.json()
    # Response shape guarantee.
    assert body["platform_assignments"]["items"] == []
    assert body["platform_assignments"]["pagination"]["total"] == 0
    # Stronger no-call invariant — the platform query never fired.
    assert mock_platform_list.call_count == 0


# =============================================================================
# R3: TENANT JWT sees own-tenant tenant_assignments only.
# =============================================================================


async def test_r3_tenant_jwt_own_tenant_only(
    app_client,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    tenant_owner_jwt_factory,
):
    """RLS scoping verified: tenant A and tenant B each have an
    assignment; tenant A's JWT sees only its own.

    Post Step 6.9.3.2: JWT switched from random-UUID `_tenant_jwt` to
    `tenant_owner_jwt_factory(tenant_a.id)` which builds a synthetic
    OWNER user with ADMIN.USERS.VIEW.TENANT grant in tenant_a; gate
    passes; RLS scopes tenant_assignments to A.
    """
    tenant_a = await make_tenant(name="R3-A")
    tenant_b = await make_tenant(name="R3-B")
    user_a = await make_tenant_user(
        tenant_id=tenant_a.id, email="r3a@r3.test", status="ACTIVE"
    )
    user_b = await make_tenant_user(
        tenant_id=tenant_b.id, email="r3b@r3.test", status="ACTIVE"
    )
    node_a, _ = await make_org_node(
        tenant_id=tenant_a.id,
        code="R3-A-ROOT",
        name="R3 A Root",
        node_type="TENANT",
    )
    node_b, _ = await make_org_node(
        tenant_id=tenant_b.id,
        code="R3-B-ROOT",
        name="R3 B Root",
        node_type="TENANT",
    )
    role = await make_role(audience="TENANT", name="R3-Role")
    a_assn = await make_tenant_user_role_assignment(
        tenant_id=tenant_a.id,
        tenant_user_id=user_a.id,
        org_node_id=node_a,
        role_id=role.id,
    )
    b_assn = await make_tenant_user_role_assignment(
        tenant_id=tenant_b.id,
        tenant_user_id=user_b.id,
        org_node_id=node_b,
        role_id=role.id,
    )

    jwt = await tenant_owner_jwt_factory(tenant_a.id)
    resp = app_client.get(
        "/api/v1/role-assignments?limit=200",
        headers=_auth(jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["tenant_assignments"]["items"]
    visible_ids = {i["id"] for i in items}
    assert str(a_assn.id) in visible_ids
    assert str(b_assn.id) not in visible_ids


# =============================================================================
# R4: filter by role_id.
# =============================================================================


async def test_r4_filter_by_role_id(
    app_client,
    settings,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    super_admin_jwt,


):
    """?role_id=X scopes both blocks (or whichever block has rows
    matching) to that role only."""
    tenant = await make_tenant(name="R4-T")
    user = await make_tenant_user(
        tenant_id=tenant.id, email="r4@r4.test", status="ACTIVE"
    )
    node, _ = await make_org_node(
        tenant_id=tenant.id,
        code="R4-ROOT",
        name="R4 Root",
        node_type="TENANT",
    )
    role_a = await make_role(audience="TENANT", name="R4-Role-A")
    role_b = await make_role(audience="TENANT", name="R4-Role-B")
    a_assn = await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=user.id,
        org_node_id=node,
        role_id=role_a.id,
    )
    await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=user.id,
        org_node_id=node,
        role_id=role_b.id,
    )

    resp = app_client.get(
        f"/api/v1/role-assignments?role_id={role_a.id}&limit=200",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    tenant_items = body["tenant_assignments"]["items"]
    role_ids = {i["role"]["id"] for i in tenant_items}
    assert role_ids == {str(role_a.id)}
    assert any(i["id"] == str(a_assn.id) for i in tenant_items)


# =============================================================================
# R5: filter by platform_user_id.
# =============================================================================


async def test_r5_filter_by_platform_user_id(
    app_client,
    settings,
    make_platform_user,
    make_role,
    make_platform_user_role_assignment,
    super_admin_jwt,


):
    user = await make_platform_user(email="r5@r5.test", status="ACTIVE")
    role = await make_role(audience="PLATFORM", name="R5-Role")
    assignment = await make_platform_user_role_assignment(
        platform_user_id=user.id, role_id=role.id
    )

    resp = app_client.get(
        f"/api/v1/role-assignments?platform_user_id={user.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    p_items = body["platform_assignments"]["items"]
    assert all(i["platform_user"]["id"] == str(user.id) for i in p_items)
    assert any(i["id"] == str(assignment.id) for i in p_items)
    # tenant_assignments empty (filter is platform-user-specific).
    assert body["tenant_assignments"]["items"] == []


# =============================================================================
# R6: filter by tenant_user_id.
# =============================================================================


async def test_r6_filter_by_tenant_user_id(
    app_client,
    settings,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    super_admin_jwt,


):
    tenant = await make_tenant(name="R6-T")
    user = await make_tenant_user(
        tenant_id=tenant.id, email="r6@r6.test", status="ACTIVE"
    )
    node, _ = await make_org_node(
        tenant_id=tenant.id,
        code="R6-ROOT",
        name="R6 Root",
        node_type="TENANT",
    )
    role = await make_role(audience="TENANT", name="R6-Role")
    assignment = await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=user.id,
        org_node_id=node,
        role_id=role.id,
    )

    resp = app_client.get(
        f"/api/v1/role-assignments?tenant_user_id={user.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    t_items = body["tenant_assignments"]["items"]
    assert all(i["tenant_user"]["id"] == str(user.id) for i in t_items)
    assert any(i["id"] == str(assignment.id) for i in t_items)
    # platform_assignments empty (filter is tenant-user-specific).
    assert body["platform_assignments"]["items"] == []


# =============================================================================
# R7: filter by tenant_id (the NEW deliverable #5 filter).
# =============================================================================


async def test_r7_filter_by_tenant_id_platform_jwt(
    app_client,
    settings,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    super_admin_jwt,


):
    """PLATFORM JWT requesting ?tenant_id=A sees only tenant A's
    tenant_assignments. Verifies the filter added in deliverable #5.
    """
    tenant_a = await make_tenant(name="R7-A")
    tenant_b = await make_tenant(name="R7-B")
    user_a = await make_tenant_user(
        tenant_id=tenant_a.id, email="r7a@r7.test", status="ACTIVE"
    )
    user_b = await make_tenant_user(
        tenant_id=tenant_b.id, email="r7b@r7.test", status="ACTIVE"
    )
    node_a, _ = await make_org_node(
        tenant_id=tenant_a.id,
        code="R7-A-ROOT",
        name="R7 A Root",
        node_type="TENANT",
    )
    node_b, _ = await make_org_node(
        tenant_id=tenant_b.id,
        code="R7-B-ROOT",
        name="R7 B Root",
        node_type="TENANT",
    )
    role = await make_role(audience="TENANT", name="R7-Role")
    a_assn = await make_tenant_user_role_assignment(
        tenant_id=tenant_a.id,
        tenant_user_id=user_a.id,
        org_node_id=node_a,
        role_id=role.id,
    )
    b_assn = await make_tenant_user_role_assignment(
        tenant_id=tenant_b.id,
        tenant_user_id=user_b.id,
        org_node_id=node_b,
        role_id=role.id,
    )

    resp = app_client.get(
        f"/api/v1/role-assignments?tenant_id={tenant_a.id}&limit=200",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    t_items = body["tenant_assignments"]["items"]
    visible_assn_ids = {i["id"] for i in t_items}
    assert str(a_assn.id) in visible_assn_ids
    assert str(b_assn.id) not in visible_assn_ids


# =============================================================================
# R8 (LOAD-BEARING): cross-tenant injection rejection at DB layer.
# =============================================================================


async def test_r8_cross_tenant_injection_rejected_at_db_layer(
    session_factory,
    platform_auth,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
):
    """LOAD-BEARING: composite FK
    fk_tenant_user_role_assignments_tenant_user_same_tenant rejects
    a row whose ``tenant_id`` mismatches the parent tenant_user's
    tenant_id at INSERT time.

    Step 6.8.1 D-34 / AI-RBAC-06 closure: cross-tenant injection is
    structurally impossible at the schema layer (replaces the v2
    app-layer pre-check).
    """
    from sqlalchemy import text
    from admin_backend.db.session import get_tenant_session

    tenant_a = await make_tenant(name="R8-A")
    tenant_b = await make_tenant(name="R8-B")
    user_a = await make_tenant_user(
        tenant_id=tenant_a.id, email="r8a@r8.test", status="ACTIVE"
    )
    node_b, _ = await make_org_node(
        tenant_id=tenant_b.id,
        code="R8-B-ROOT",
        name="R8 B Root",
        node_type="TENANT",
    )
    # Also need a tenant-A org_node so the TURA insert wouldn't fail
    # on the org_node side (we want it to fail on the tenant_user
    # side specifically — clearer assertion target).
    node_a, _ = await make_org_node(
        tenant_id=tenant_a.id,
        code="R8-A-ROOT",
        name="R8 A Root",
        node_type="TENANT",
    )
    role = await make_role(audience="TENANT", name="R8-Role")

    # Attempt: assignment row claims tenant_id=B but references
    # user_a (whose tenant is A) and node_a (whose tenant is A).
    # The composite FK to tenant_users(tenant_id, id) requires
    # (B, user_a.id) to exist — it doesn't.
    schema = get_settings().db_schema
    with pytest.raises(IntegrityError):
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.tenant_user_role_assignments ("
                    "  id, tenant_id, tenant_user_id, org_node_id,"
                    "  role_id, status,"
                    "  granted_by_user_id, granted_by_user_type"
                    ") VALUES ("
                    "  :id, :t_id, :tu_id, :on_id,"
                    "  :role_id,"
                    f"  CAST('ACTIVE' AS {schema}.user_role_assignment_status_enum),"
                    "  NULL, NULL"
                    ")"
                ),
                {
                    "id": uuid.uuid4(),
                    "t_id": tenant_b.id,  # claim tenant B
                    "tu_id": user_a.id,   # but reference tenant-A user
                    "on_id": node_b,
                    "role_id": role.id,
                },
            )


# =============================================================================
# R9: filter by org_node_id.
# =============================================================================


async def test_r9_filter_by_org_node_id(
    app_client,
    settings,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    super_admin_jwt,


):
    tenant = await make_tenant(name="R9-T")
    user = await make_tenant_user(
        tenant_id=tenant.id, email="r9@r9.test", status="ACTIVE"
    )
    node, _ = await make_org_node(
        tenant_id=tenant.id,
        code="R9-ROOT",
        name="R9 Root",
        node_type="TENANT",
    )
    role = await make_role(audience="TENANT", name="R9-Role")
    assignment = await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=user.id,
        org_node_id=node,
        role_id=role.id,
    )

    resp = app_client.get(
        f"/api/v1/role-assignments?org_node_id={node}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    t_items = body["tenant_assignments"]["items"]
    assert all(i["org_node"]["id"] == str(node) for i in t_items)
    assert any(i["id"] == str(assignment.id) for i in t_items)


# =============================================================================
# R10: filter by status.
# =============================================================================


async def test_r10_filter_by_status(
    app_client,
    settings,
    make_tenant,
    make_tenant_user,
    make_org_node,
    make_role,
    make_tenant_user_role_assignment,
    super_admin_jwt,


):
    """Both ACTIVE-only and INACTIVE-only filters return correct
    subsets."""
    tenant = await make_tenant(name="R10-T")
    user = await make_tenant_user(
        tenant_id=tenant.id, email="r10@r10.test", status="ACTIVE"
    )
    node, _ = await make_org_node(
        tenant_id=tenant.id,
        code="R10-ROOT",
        name="R10 Root",
        node_type="TENANT",
    )
    role_a = await make_role(audience="TENANT", name="R10-Role-A")
    role_b = await make_role(audience="TENANT", name="R10-Role-B")
    active_assn = await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=user.id,
        org_node_id=node,
        role_id=role_a.id,
    )
    inactive_assn = await make_tenant_user_role_assignment(
        tenant_id=tenant.id,
        tenant_user_id=user.id,
        org_node_id=node,
        role_id=role_b.id,
        status="INACTIVE",
        revoked_at="2026-04-01 00:00:00+00",
    )

    # ACTIVE filter
    resp = app_client.get(
        f"/api/v1/role-assignments?tenant_id={tenant.id}&status=ACTIVE",
        headers=_auth(super_admin_jwt),
    )
    body = resp.json()
    a_ids = {i["id"] for i in body["tenant_assignments"]["items"]}
    assert str(active_assn.id) in a_ids
    assert str(inactive_assn.id) not in a_ids

    # INACTIVE filter
    resp = app_client.get(
        f"/api/v1/role-assignments?tenant_id={tenant.id}&status=INACTIVE",
        headers=_auth(super_admin_jwt),
    )
    body = resp.json()
    i_ids = {i["id"] for i in body["tenant_assignments"]["items"]}
    assert str(inactive_assn.id) in i_ids
    assert str(active_assn.id) not in i_ids


# =============================================================================
# R11: pagination per block.
# =============================================================================


def test_r11_pagination_per_block(app_client, settings, super_admin_jwt):
    """Total counts are independent per block; offset/limit applies
    per block. Smaller-limit request produces the same totals but
    smaller items lists.
    """
    full_resp = app_client.get(
        "/api/v1/role-assignments?limit=200",
        headers=_auth(super_admin_jwt),
    )
    full = full_resp.json()
    full_p_total = full["platform_assignments"]["pagination"]["total"]
    full_t_total = full["tenant_assignments"]["pagination"]["total"]

    paged_resp = app_client.get(
        "/api/v1/role-assignments?limit=2",
        headers=_auth(super_admin_jwt),
    )
    paged = paged_resp.json()
    # Totals unchanged — pagination doesn't shrink the count.
    assert paged["platform_assignments"]["pagination"]["total"] == full_p_total
    assert paged["tenant_assignments"]["pagination"]["total"] == full_t_total
    # items lists capped at limit.
    assert len(paged["platform_assignments"]["items"]) <= 2
    assert len(paged["tenant_assignments"]["items"]) <= 2


# =============================================================================
# R12 (LOAD-BEARING): PLATFORM no-impersonation regression.
# =============================================================================


def test_r12_platform_no_impersonation_sees_both_tables(
    app_client, settings,
    super_admin_jwt,


):
    """LOAD-BEARING: PLATFORM JWT calling /role-assignments without
    setting app.tenant_id sees:

      - platform_user_role_assignments rows (no RLS — direct read).
      - tenant_user_role_assignments rows from ALL tenants via
        D-29's unconditional OR-branch (current_setting('app.user_type')
        = 'PLATFORM' admits everything).

    No per-row impersonation — the FN-AB-14 anti-pattern was retired
    by the 6.8.1 split. Regression guard: any future change that
    introduces per-tenant impersonation would break this assertion
    by causing tenant_assignments to come back empty (PLATFORM
    sessions don't set app.tenant_id).
    """
    resp = app_client.get(
        "/api/v1/role-assignments?limit=200",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Both blocks have rows — no impersonation needed.
    assert body["platform_assignments"]["pagination"]["total"] >= 3
    assert body["tenant_assignments"]["pagination"]["total"] >= 19


# =============================================================================
# R13: audience-check trigger regression.
# =============================================================================


async def test_r13_audience_check_triggers(
    session_factory,
    platform_auth,
    make_role,
    make_platform_user,
    make_tenant,
    make_tenant_user,
    make_org_node,
):
    """Both audience-check triggers from Step 6.8.1 reject the
    audience-mismatched INSERT:

      enforce_platform_role_audience: rejects TENANT-audience role
        on platform_user_role_assignments.
      enforce_tenant_role_audience: rejects PLATFORM-audience role
        on tenant_user_role_assignments.

    Triggers raise plpgsql exceptions which surface as
    ``DBAPIError``-derived classes through psycopg+SQLAlchemy.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from admin_backend.db.session import get_tenant_session

    tenant_role = await make_role(audience="TENANT", name="R13-Tenant-Role")
    platform_role = await make_role(audience="PLATFORM", name="R13-Plat-Role")
    pu = await make_platform_user(email="r13pu@r13.test", status="ACTIVE")
    tenant = await make_tenant(name="R13-T")
    tu = await make_tenant_user(
        tenant_id=tenant.id, email="r13tu@r13.test", status="ACTIVE"
    )
    node, _ = await make_org_node(
        tenant_id=tenant.id,
        code="R13-ROOT",
        name="R13 Root",
        node_type="TENANT",
    )

    schema = get_settings().db_schema

    # Attempt 1: TENANT role on platform_user_role_assignments
    with pytest.raises(DBAPIError):
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.platform_user_role_assignments ("
                    "  id, platform_user_id, role_id, status"
                    ") VALUES ("
                    "  :id, :pu_id, :role_id,"
                    f"  CAST('ACTIVE' AS {schema}.user_role_assignment_status_enum)"
                    ")"
                ),
                {
                    "id": uuid.uuid4(),
                    "pu_id": pu.id,
                    "role_id": tenant_role.id,  # WRONG audience
                },
            )

    # Attempt 2: PLATFORM role on tenant_user_role_assignments
    with pytest.raises(DBAPIError):
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.tenant_user_role_assignments ("
                    "  id, tenant_id, tenant_user_id, org_node_id,"
                    "  role_id, status"
                    ") VALUES ("
                    "  :id, :t_id, :tu_id, :on_id,"
                    "  :role_id,"
                    f"  CAST('ACTIVE' AS {schema}.user_role_assignment_status_enum)"
                    ")"
                ),
                {
                    "id": uuid.uuid4(),
                    "t_id": tenant.id,
                    "tu_id": tu.id,
                    "on_id": node,
                    "role_id": platform_role.id,  # WRONG audience
                },
            )


# =============================================================================
# R14: invalid sort key returns 400.
# =============================================================================


def test_r14_invalid_sort_returns_400(app_client, settings, super_admin_jwt):
    resp = app_client.get(
        "/api/v1/role-assignments?sort=garbage_desc",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_SORT_KEY"


# =============================================================================
# R15: 401 without JWT.
# =============================================================================


def test_r15_no_jwt_returns_401(app_client):
    resp = app_client.get("/api/v1/role-assignments")
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MISSING"
