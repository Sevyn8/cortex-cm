"""Integration tests for the stores write endpoints (Step 6.17.3).

Coverage shape:

  RC1-RC12: POST  /api/v1/stores  (create)
  RP1-RP11: PATCH /api/v1/stores/{store_id}
  MG:       mandatory-gate-discipline anchor

Eleven LOAD-BEARING regression tests cited by ID in the final report:
  RC1, RC3, RC4, RC6, RC7, RC8, RC9, RC10,
  RP1, RP2, RP3, RP4, RP5, RP6, RP7, RP8, RP9, RP11,
  MG.

Cleanup. ``cleanup_stores_router`` tracks store IDs returned by POST
or referenced by PATCH; DELETEs at teardown. TestClient requests are
request-scope-transactional (FastAPI commits per request) so cleanup
just runs after all in-test requests have committed. Fixture order
in test signatures: upstream factories (``make_tenant``,
``make_platform_user``, ``make_org_node``) BEFORE
``cleanup_stores_router`` so the upstream factories' DELETEs happen
AFTER cleanup_stores_router has removed referencing rows.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext
from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings, get_settings
from admin_backend.db.session import get_tenant_session
from admin_backend.main import create_app


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,
    session_factory: Any,
) -> Iterator[TestClient]:
    """TestClient with engine + session_factory wired onto app.state.

    Mirrors test_stores_router.py / test_tenants_writes_router.py —
    bypasses the lifespan so the test event loop owns the engine.
    """
    from admin_backend.auth.stub import StubAuthClient

    app_obj = create_app()
    app_obj.state.settings = settings
    app_obj.state.engine = engine
    app_obj.state.session_factory = session_factory
    app_obj.state.auth_client = StubAuthClient(settings)
    with TestClient(app_obj) as client:
        yield client


@pytest_asyncio.fixture
async def cleanup_stores_router(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Tracks store IDs created via the router or pre-existing rows
    PATCHed during a test; DELETEs at teardown.

    Step 6.21.2: each repo-created store has a paired STORE-type
    org_node. Cleanup captures the paired ``org_node_id`` from the
    stores row BEFORE deleting and DELETEs both in sequence.

    TestClient requests are request-scope-transactional (FastAPI
    commits per request), so the teardown runs in a fresh PLATFORM
    session and sees committed rows immediately.
    """
    schema = get_settings().db_schema
    created: list[UUID] = []
    yield created

    if created:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            paired = await session.execute(
                text(
                    f"SELECT org_node_id FROM {schema}.stores "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": created},
            )
            org_node_ids = [r.org_node_id for r in paired if r.org_node_id]
            await session.execute(
                text(
                    f"DELETE FROM {schema}.stores WHERE id = ANY(:ids)"
                ),
                {"ids": created},
            )
            if org_node_ids:
                await session.execute(
                    text(
                        f"DELETE FROM {schema}.org_nodes "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": org_node_ids},
                )


def _tenant_jwt(settings: Settings, tenant_id: UUID) -> str:
    """Random-user TENANT JWT — no seeded grants. The gate denies via
    PERMISSION_DENIED (no matching has_permission row)."""
    return make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="TENANT",
        tenant_id=tenant_id,
    )


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _valid_create_body(
    *,
    tenant_id: UUID,
    parent_org_node_id: UUID,
    name: str,
    store_code: str,
) -> dict[str, Any]:
    """Minimal valid POST /stores body.

    Step 6.21.2: ``parent_org_node_id`` is REQUIRED (replaces the
    pre-6.21.2 optional ``org_node_id``). The server provisions the
    paired STORE-type org_node under ``parent_org_node_id``.
    """
    return {
        "tenant_id": str(tenant_id),
        "name": name,
        "country": "United States",
        "timezone": "America/New_York",
        "currency": "USD",
        "store_code": store_code,
        "tax_treatment": "EXCLUSIVE",
        "parent_org_node_id": str(parent_org_node_id),
    }


async def _ensure_tenant_root(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    tenant_id: UUID,
) -> UUID:
    """Look up the (or create a) TENANT-root org_node for ``tenant_id``.

    Tests typically use ``make_tenant(with_root=True)`` to provision
    the root; this helper returns its id so the test body can pass
    it as ``parent_org_node_id``.
    """
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"SELECT id FROM {schema}.org_nodes "
                "WHERE tenant_id = :tid "
                f"AND node_type = CAST('TENANT' AS {schema}.org_node_type_enum) "
                "AND parent_id IS NULL LIMIT 1"
            ),
            {"tid": tenant_id},
        )
        row = result.first()
    if row is None:
        raise RuntimeError(
            f"no tenant-root org_node for tenant {tenant_id}; "
            "use make_tenant(with_root=True)"
        )
    return UUID(str(row.id))


# ============================================================================
# POST /stores (RC1-RC12)
# ============================================================================


async def test_rc1_super_admin_create_returns_201_full_detail(
    app_client,
    make_tenant,
    cleanup_stores_router,
    super_admin_jwt,
    session_factory,
    platform_auth,
):
    """SUPER_ADMIN happy path: 201, full StoreDetail; audit cols populated."""
    t = await make_tenant(name="RC1-T", with_root=True)
    parent_id = await _ensure_tenant_root(session_factory, platform_auth, t.id)
    body = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=parent_id,
        name="RC1-Store", store_code="RC1-001",
    )
    resp = app_client.post(
        "/api/v1/stores", json=body, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 201, resp.text
    j = resp.json()
    cleanup_stores_router.append(UUID(j["id"]))
    assert j["name"] == "RC1-Store"
    assert j["tenant_id"] == str(t.id)
    # Detail shape: 17 keys; audit-actor IDs hidden.
    assert "created_by_user_id" not in j
    assert "updated_by_user_id" not in j

    # Verify audit columns landed against the DB.
    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        row = await session.execute(
            text(
                "SELECT created_by_user_id, created_by_user_type, "
                "updated_by_user_id, updated_by_user_type "
                f"FROM {schema}.stores WHERE id = :id"
            ),
            {"id": j["id"]},
        )
        c_by, c_type, u_by, u_type = row.one()
        assert c_by is not None
        assert c_type == "PLATFORM"
        assert u_by is not None
        assert u_type == "PLATFORM"


async def test_rc2_platform_admin_create_returns_201(
    app_client,
    settings,
    make_tenant,
    make_platform_user,
    cleanup_stores_router,
    session_factory,
    platform_auth,
):
    """PLATFORM_ADMIN holds CONFIGURE.GLOBAL; cascade carries to TENANT."""
    schema = get_settings().db_schema
    t = await make_tenant(name="RC2-T", with_root=True)
    pa = await make_platform_user(status="ACTIVE")
    # Assign PLATFORM_ADMIN role.
    role_id: UUID | None = None
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        result = await session.execute(
            text(
                f"SELECT id FROM {schema}.roles WHERE code = 'PLATFORM_ADMIN'"
            )
        )
        role_id = UUID(str(result.scalar_one()))
        await session.execute(
            text(
                f"INSERT INTO {schema}.platform_user_role_assignments ("
                "  platform_user_id, role_id, status,"
                "  granted_by_user_id, granted_by_user_type"
                ") VALUES ("
                "  :user_id, :role_id,"
                f"  CAST('ACTIVE' AS {schema}.user_role_assignment_status_enum),"
                "  NULL, NULL"
                ")"
            ),
            {"user_id": pa.id, "role_id": role_id},
        )

    jwt = make_test_jwt(settings, user_id=pa.id, user_type="PLATFORM")
    parent_id = await _ensure_tenant_root(session_factory, platform_auth, t.id)
    body = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=parent_id,
        name="RC2-Store", store_code="RC2-001",
    )
    resp = app_client.post(
        "/api/v1/stores", json=body, headers=_auth(jwt)
    )
    assert resp.status_code == 201, resp.text
    cleanup_stores_router.append(UUID(resp.json()["id"]))

    # Cleanup the assignment (ON DELETE RESTRICT on platform_user_id).
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        await session.execute(
            text(
                f"DELETE FROM {schema}.platform_user_role_assignments "
                "WHERE platform_user_id = :id"
            ),
            {"id": pa.id},
        )


async def test_rc3_owner_creates_for_own_tenant_returns_201(
    app_client,
    make_tenant,
    cleanup_stores_router,
    tenant_owner_jwt_factory,
    session_factory,
    platform_auth,
):
    """OWNER granted ADMIN.STORES.CONFIGURE.TENANT creates for own tenant."""
    t = await make_tenant(name="RC3-T", with_root=True)
    parent_id = await _ensure_tenant_root(session_factory, platform_auth, t.id)
    jwt = await tenant_owner_jwt_factory(
        t.id,
        with_grants=[("ADMIN", "STORES", "CONFIGURE", "TENANT")],
    )
    body = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=parent_id,
        name="RC3-Store", store_code="RC3-001",
    )
    resp = app_client.post(
        "/api/v1/stores", json=body, headers=_auth(jwt)
    )
    assert resp.status_code == 201, resp.text
    cleanup_stores_router.append(UUID(resp.json()["id"]))


async def test_rc4_owner_with_other_tenant_in_body_returns_404(
    app_client,
    make_tenant,
    tenant_owner_jwt_factory,
    session_factory,
    platform_auth,
):
    """LOAD-BEARING — TENANT-A OWNER submits tenant_id=B in body: the
    cross-tenant id is RLS-invisible to the OWNER's session; the
    repo's tenant-visibility pre-check converts to 404
    TENANT_NOT_FOUND (RLS-as-404 per D-17). Without this guard the
    INSERT would surface as a 500 InsufficientPrivilege from the
    stores RLS WITH CHECK predicate."""
    t_a = await make_tenant(name="RC4-A", with_root=True)
    t_b = await make_tenant(name="RC4-B", with_root=True)
    parent_b = await _ensure_tenant_root(session_factory, platform_auth, t_b.id)
    jwt = await tenant_owner_jwt_factory(
        t_a.id,
        with_grants=[("ADMIN", "STORES", "CONFIGURE", "TENANT")],
    )
    body = _valid_create_body(
        tenant_id=t_b.id, parent_org_node_id=parent_b,
        name="RC4-Store", store_code="RC4-001",
    )
    resp = app_client.post(
        "/api/v1/stores", json=body, headers=_auth(jwt)
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "TENANT_NOT_FOUND"


async def test_rc5_missing_required_field_returns_422(
    app_client,
    make_tenant,
    super_admin_jwt,
    session_factory,
    platform_auth,
):
    """POST without ``name`` -> 422 from Pydantic."""
    t = await make_tenant(name="RC5-T", with_root=True)
    parent_id = await _ensure_tenant_root(session_factory, platform_auth, t.id)
    body = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=parent_id,
        name="RC5-Discard", store_code="RC5-001",
    )
    del body["name"]
    resp = app_client.post(
        "/api/v1/stores", json=body, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 422


async def test_rc6_status_in_body_returns_422_extra_forbid(
    app_client,
    make_tenant,
    super_admin_jwt,
    session_factory,
    platform_auth,
):
    """status is rejected by Pydantic extra='forbid' (LD8)."""
    t = await make_tenant(name="RC6-T", with_root=True)
    parent_id = await _ensure_tenant_root(session_factory, platform_auth, t.id)
    body = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=parent_id,
        name="RC6-Store", store_code="RC6-001",
    )
    body["status"] = "ACTIVE"
    resp = app_client.post(
        "/api/v1/stores", json=body, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 422, resp.text


async def test_rc7_tenant_no_grants_returns_403_permission_denied(
    app_client,
    settings,
    make_tenant,
    session_factory,
    platform_auth,
):
    """TENANT JWT with no grants -> 403 PERMISSION_DENIED."""
    t = await make_tenant(name="RC7-T", with_root=True)
    parent_id = await _ensure_tenant_root(session_factory, platform_auth, t.id)
    jwt = _tenant_jwt(settings, t.id)
    body = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=parent_id,
        name="RC7-Store", store_code="RC7-001",
    )
    resp = app_client.post(
        "/api/v1/stores", json=body, headers=_auth(jwt)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "PERMISSION_DENIED"


async def test_rc8_duplicate_store_code_returns_409(
    app_client,
    make_tenant,
    cleanup_stores_router,
    super_admin_jwt,
    session_factory,
    platform_auth,
):
    """Second POST with same (tenant_id, store_code) -> 409 DUPLICATE_STORE_CODE."""
    t = await make_tenant(name="RC8-T", with_root=True)
    parent_id = await _ensure_tenant_root(session_factory, platform_auth, t.id)
    body_a = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=parent_id,
        name="RC8-First", store_code="RC8-DUP",
    )
    resp_a = app_client.post(
        "/api/v1/stores", json=body_a, headers=_auth(super_admin_jwt)
    )
    assert resp_a.status_code == 201
    cleanup_stores_router.append(UUID(resp_a.json()["id"]))

    body_b = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=parent_id,
        name="RC8-Second", store_code="RC8-DUP",
    )
    resp_b = app_client.post(
        "/api/v1/stores", json=body_b, headers=_auth(super_admin_jwt)
    )
    assert resp_b.status_code == 409, resp_b.text
    assert resp_b.json()["code"] == "DUPLICATE_STORE_CODE"


async def test_rc9_cross_tenant_parent_returns_404(
    app_client,
    make_tenant,
    make_org_node,
    super_admin_jwt,
):
    """parent_org_node_id from another tenant -> 404 PARENT_NODE_NOT_FOUND.

    Step 6.21.2 supersedes the pre-6.21.2 RC9 (which expected 409
    ORG_NODE_NOT_FOR_STORE). The retired ``OrgNodeNotForStoreError``
    collapsed three causes; the new ``_check_parent_node_for_store``
    surfaces cross-tenant or missing parents as
    ``ParentNodeNotFoundError`` (404 per RLS-as-404 / D-17 framing).
    """
    t_a = await make_tenant(name="RC9-A", with_root=True)
    t_b = await make_tenant(name="RC9-B", with_root=True)
    root_b, root_b_path = await make_org_node(
        tenant_id=t_b.id,
        node_type="TENANT",
        code=f"rc9b-{uuid.uuid4().hex[:8]}",
        name="RC9-B-Root",
    )
    hq_b, _ = await make_org_node(
        tenant_id=t_b.id,
        node_type="HQ",
        code=f"rc9b-hq-{uuid.uuid4().hex[:8]}",
        name="RC9-B-HQ",
        parent_id=root_b,
        parent_path=root_b_path,
    )
    body = _valid_create_body(
        tenant_id=t_a.id,
        parent_org_node_id=hq_b,
        name="RC9-Store",
        store_code="RC9-001",
    )
    resp = app_client.post(
        "/api/v1/stores", json=body, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "PARENT_NODE_NOT_FOUND"


# Step 6.21.2: test_rc10_already_linked_org_node_returns_409 deleted.
# The "already linked" failure mode is structurally unreachable under
# the new atomic-pair architecture (the server creates the paired
# STORE-type org_node fresh inside the same transaction). The DDL
# partial unique index ``uq_stores_org_node_id`` remains as a defensive
# backstop.


async def test_rc11_happy_path_with_all_optionals(
    app_client,
    make_tenant,
    cleanup_stores_router,
    super_admin_jwt,
    session_factory,
    platform_auth,
):
    """All optional fields populated; row inserted; coords as strings.

    Step 6.21.2: ``org_node_id`` is no longer accepted in the body;
    the test now passes ``parent_org_node_id`` and asserts on the
    server-allocated ``org_node_id`` being present (UUID-shaped) in
    the response.
    """
    t = await make_tenant(name="RC11-T", with_root=True)
    parent_id = await _ensure_tenant_root(session_factory, platform_auth, t.id)
    body = _valid_create_body(
        tenant_id=t.id,
        parent_org_node_id=parent_id,
        name="RC11-Store",
        store_code="RC11-001",
    )
    body["address"] = "100 Main St"
    body["latitude"] = "12.345678"
    body["longitude"] = "-23.456789"

    resp = app_client.post(
        "/api/v1/stores", json=body, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 201, resp.text
    j = resp.json()
    cleanup_stores_router.append(UUID(j["id"]))
    assert j["address"] == "100 Main St"
    # Coords serialise as JSON strings per the field_serializer.
    assert j["latitude"] == "12.345678"
    assert j["longitude"] == "-23.456789"
    # Step 6.21.2: server allocates org_node_id; not server-side null.
    assert j["org_node_id"] is not None
    UUID(j["org_node_id"])  # parseable


async def test_rc12_happy_path_with_optionals_omitted(
    app_client,
    make_tenant,
    cleanup_stores_router,
    super_admin_jwt,
    session_factory,
    platform_auth,
):
    """Optional fields omitted -> nulls in response shape.

    Step 6.21.2: ``org_node_id`` is server-allocated (no longer
    optional / nullable on the wire). The assertion shifted from
    "org_node_id is None" to "org_node_id is present, UUID-shaped".
    """
    t = await make_tenant(name="RC12-T", with_root=True)
    parent_id = await _ensure_tenant_root(session_factory, platform_auth, t.id)
    body = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=parent_id,
        name="RC12-Store", store_code="RC12-001",
    )
    resp = app_client.post(
        "/api/v1/stores", json=body, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 201
    j = resp.json()
    cleanup_stores_router.append(UUID(j["id"]))
    assert j["address"] is None
    assert j["latitude"] is None
    assert j["longitude"] is None
    # Step 6.21.2: server-allocated; never null.
    assert j["org_node_id"] is not None
    UUID(j["org_node_id"])  # parseable


# ============================================================================
# PATCH /stores/{store_id} (RP1-RP11)
# ============================================================================


async def test_rp1_super_admin_patch_returns_200_updated_fields(
    app_client,
    make_tenant,
    make_store,
    cleanup_stores_router,
    super_admin_jwt,
    session_factory,
    platform_auth,
):
    """PATCH under SUPER_ADMIN: 200, name reflected; updated_by_user_id flipped."""
    schema = get_settings().db_schema
    t = await make_tenant(name="RP1-T", with_root=True)
    store = await make_store(tenant_id=t.id, name="RP1-Original")

    resp = app_client.patch(
        f"/api/v1/stores/{store.id}",
        json={"name": "RP1-Renamed"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["name"] == "RP1-Renamed"

    # Verify updated_by_user_id was set (was NULL on the fixture row).
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        row = await session.execute(
            text(
                "SELECT updated_by_user_id, updated_by_user_type "
                f"FROM {schema}.stores WHERE id = :id"
            ),
            {"id": store.id},
        )
        u_by, u_type = row.one()
        assert u_by is not None
        assert u_type == "PLATFORM"


async def test_rp2_owner_patch_own_tenant_store_returns_200(
    app_client,
    make_tenant,
    make_store,
    tenant_owner_jwt_factory,
):
    """OWNER granted CONFIGURE.TENANT patches own-tenant store -> 200."""
    t = await make_tenant(name="RP2-T", with_root=True)
    store = await make_store(tenant_id=t.id, name="RP2-Original")
    jwt = await tenant_owner_jwt_factory(
        t.id,
        with_grants=[("ADMIN", "STORES", "CONFIGURE", "TENANT")],
    )
    resp = app_client.patch(
        f"/api/v1/stores/{store.id}",
        json={"name": "RP2-Renamed"},
        headers=_auth(jwt),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "RP2-Renamed"


async def test_rp3_cross_tenant_patch_returns_404(
    app_client,
    make_tenant,
    make_store,
    tenant_owner_jwt_factory,
):
    """LOAD-BEARING — TENANT-A patches TENANT-B's store -> 404 STORE_NOT_FOUND.

    Anchor dep get_store_anchor returns the path from a JOIN that
    RLS-filters TENANT-B's row out of TENANT-A's session — the
    cross-tenant probe surfaces as 404 (RLS-as-404 per D-17), not 403.
    """
    t_a = await make_tenant(name="RP3-A", with_root=True)
    t_b = await make_tenant(name="RP3-B", with_root=True)
    store_b = await make_store(tenant_id=t_b.id, name="RP3-B-Store")
    jwt = await tenant_owner_jwt_factory(
        t_a.id,
        with_grants=[("ADMIN", "STORES", "CONFIGURE", "TENANT")],
    )
    resp = app_client.patch(
        f"/api/v1/stores/{store_b.id}",
        json={"name": "RP3-Hacked"},
        headers=_auth(jwt),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "STORE_NOT_FOUND"


async def test_rp4_empty_body_returns_422_empty_patch(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """LOAD-BEARING — empty body -> 422 EMPTY_PATCH."""
    t = await make_tenant(name="RP4-T", with_root=True)
    store = await make_store(tenant_id=t.id, name="RP4-Store")
    resp = app_client.patch(
        f"/api/v1/stores/{store.id}",
        json={},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "EMPTY_PATCH"


async def test_rp5_status_in_body_returns_422_extra_forbid(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """status rejected by extra='forbid' (lifecycle is Step 6.17.4)."""
    t = await make_tenant(name="RP5-T", with_root=True)
    store = await make_store(tenant_id=t.id, name="RP5-Store")
    resp = app_client.patch(
        f"/api/v1/stores/{store.id}",
        json={"status": "ACTIVE"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text


async def test_rp6_org_node_id_in_body_returns_422_extra_forbid(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """LOAD-BEARING — LD3: org_node_id immutable; rejected by extra='forbid'."""
    t = await make_tenant(name="RP6-T", with_root=True)
    store = await make_store(tenant_id=t.id, name="RP6-Store")
    resp = app_client.patch(
        f"/api/v1/stores/{store.id}",
        json={"org_node_id": str(uuid.uuid4())},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text


async def test_rp7_tenant_id_in_body_returns_422_extra_forbid(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """LOAD-BEARING — tenant_id rejected by extra='forbid' (can't change tenancy)."""
    t = await make_tenant(name="RP7-T", with_root=True)
    store = await make_store(tenant_id=t.id, name="RP7-Store")
    resp = app_client.patch(
        f"/api/v1/stores/{store.id}",
        json={"tenant_id": str(uuid.uuid4())},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text


async def test_rp8_rename_store_code_to_taken_returns_409(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """LOAD-BEARING — rename store_code to a value held by another
    same-tenant store -> 409 DUPLICATE_STORE_CODE."""
    t = await make_tenant(name="RP8-T", with_root=True)
    store_a = await make_store(
        tenant_id=t.id, name="RP8-A", store_code="RP8-AAA"
    )
    store_b = await make_store(
        tenant_id=t.id, name="RP8-B", store_code="RP8-BBB"
    )
    resp = app_client.patch(
        f"/api/v1/stores/{store_b.id}",
        json={"store_code": "RP8-AAA"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "DUPLICATE_STORE_CODE"
    # store_a kept its store_code; sanity.
    _ = store_a


async def test_rp9_rename_store_code_to_same_returns_200_noop(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """LOAD-BEARING — rename to same value is a 200 no-op (exclude-self
    in the duplicate pre-check)."""
    t = await make_tenant(name="RP9-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="RP9-Store", store_code="RP9-SAME"
    )
    resp = app_client.patch(
        f"/api/v1/stores/{store.id}",
        json={"store_code": "RP9-SAME"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["store_code"] == "RP9-SAME"


async def test_rp10_unknown_id_returns_404(
    app_client,
    super_admin_jwt,
):
    """PATCH on unknown store_id -> 404 STORE_NOT_FOUND.

    The anchor dep get_store_anchor fires first and raises before
    the gate body, so the 404 comes from the anchor dep path.
    """
    ephemeral = uuid.uuid4()
    resp = app_client.patch(
        f"/api/v1/stores/{ephemeral}",
        json={"name": "Should not land"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "STORE_NOT_FOUND"


async def test_rp11_tenant_no_grants_returns_403_permission_denied(
    app_client,
    settings,
    make_tenant,
    make_store,
):
    """LOAD-BEARING — TENANT JWT with no grants -> 403 PERMISSION_DENIED."""
    t = await make_tenant(name="RP11-T", with_root=True)
    store = await make_store(tenant_id=t.id, name="RP11-Store")
    jwt = _tenant_jwt(settings, t.id)
    resp = app_client.patch(
        f"/api/v1/stores/{store.id}",
        json={"name": "RP11-Denied"},
        headers=_auth(jwt),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "PERMISSION_DENIED"


# ============================================================================
# Mandatory-gate-discipline anchor
# ============================================================================


def test_mg_stores_write_endpoints_carry_gate_marker() -> None:
    """LOAD-BEARING — POST /stores and PATCH /stores/{id} carry the
    ``__permission_gate__`` marker.

    Scoped, named-route assertion that complements the broader
    ``tests/integration/test_gate_discipline.py`` meta-test. A future
    refactor that accidentally drops ``Depends(require(...))`` from
    either write endpoint fails here with a clear message.
    """
    app = create_app()
    target = {
        ("POST", "/api/v1/stores"),
        ("PATCH", "/api/v1/stores/{store_id}"),
    }
    seen: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            key = (method, route.path)
            if key not in target:
                continue
            has_gate = any(
                hasattr(dep.call, "__permission_gate__")
                for dep in route.dependant.dependencies
            )
            assert has_gate, (
                f"{method} {route.path}: no __permission_gate__ marker; "
                "gate is required."
            )
            seen.add(key)
    assert seen == target, f"missing routes: {target - seen}"


# ============================================================================
# W: Step 6.21.2 paired-write router tests.
#
# End-to-end via TestClient. Confirms the wire shape of the new
# parent_org_node_id field and the response's org_node_id field that
# matches the freshly-allocated org_node row.
# ============================================================================


async def test_w1_post_stores_with_valid_parent_returns_org_node_id(
    app_client,
    make_tenant,
    cleanup_stores_router,
    super_admin_jwt,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: POST /stores end-to-end produces a response with
    a non-null UUID-shaped ``org_node_id`` that matches an actual
    STORE-type org_node row in the same tenant.
    """
    schema = get_settings().db_schema
    t = await make_tenant(name="W1-T", with_root=True)
    parent_id = await _ensure_tenant_root(session_factory, platform_auth, t.id)
    body = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=parent_id,
        name="W1-Store", store_code="W1-001",
    )
    resp = app_client.post(
        "/api/v1/stores", json=body, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 201, resp.text
    j = resp.json()
    cleanup_stores_router.append(UUID(j["id"]))
    assert j["org_node_id"] is not None
    UUID(j["org_node_id"])  # parseable

    # Verify the paired org_node row actually exists with STORE node_type.
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        row = await session.execute(
            text(
                f"SELECT node_type, parent_id FROM {schema}.org_nodes "
                "WHERE id = :id"
            ),
            {"id": j["org_node_id"]},
        )
        node = row.first()
    assert node is not None
    assert node.node_type == "STORE"
    assert node.parent_id == parent_id


async def test_w2_post_stores_without_parent_org_node_id_returns_422(
    app_client,
    make_tenant,
    super_admin_jwt,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: POST without ``parent_org_node_id`` -> 422 from
    Pydantic field-required validation."""
    t = await make_tenant(name="W2-T", with_root=True)
    parent_id = await _ensure_tenant_root(session_factory, platform_auth, t.id)
    body = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=parent_id,
        name="W2-Store", store_code="W2-001",
    )
    del body["parent_org_node_id"]
    resp = app_client.post(
        "/api/v1/stores", json=body, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 422, resp.text


async def test_w3_post_stores_with_legacy_org_node_id_returns_422(
    app_client,
    make_tenant,
    super_admin_jwt,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: POST with the deprecated ``org_node_id`` field ->
    422 via Pydantic's ``extra="forbid"`` (LD1 / Q7 lock).

    Catches the case where a frontend on the old wire shape sends
    ``org_node_id`` after the contract change; the schema rejects
    rather than silently dropping the field.
    """
    t = await make_tenant(name="W3-T", with_root=True)
    parent_id = await _ensure_tenant_root(session_factory, platform_auth, t.id)
    body = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=parent_id,
        name="W3-Store", store_code="W3-001",
    )
    body["org_node_id"] = str(uuid.uuid4())
    resp = app_client.post(
        "/api/v1/stores", json=body, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 422, resp.text


async def test_w4_patch_stores_name_cascades_end_to_end(
    app_client,
    make_tenant,
    cleanup_stores_router,
    super_admin_jwt,
    session_factory,
    platform_auth,
) -> None:
    """PATCH /stores with ``name`` change cascades to the paired
    org_node.name. Verified via direct DB read after the request
    commits.
    """
    schema = get_settings().db_schema
    t = await make_tenant(name="W4-T", with_root=True)
    parent_id = await _ensure_tenant_root(session_factory, platform_auth, t.id)

    create_body = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=parent_id,
        name="W4-Original", store_code="W4-001",
    )
    resp_a = app_client.post(
        "/api/v1/stores", json=create_body, headers=_auth(super_admin_jwt)
    )
    assert resp_a.status_code == 201
    j_a = resp_a.json()
    cleanup_stores_router.append(UUID(j_a["id"]))
    org_node_id = j_a["org_node_id"]

    resp_b = app_client.patch(
        f"/api/v1/stores/{j_a['id']}",
        json={"name": "W4-Renamed"},
        headers=_auth(super_admin_jwt),
    )
    assert resp_b.status_code == 200, resp_b.text
    assert resp_b.json()["name"] == "W4-Renamed"

    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        row = await session.execute(
            text(
                f"SELECT name FROM {schema}.org_nodes WHERE id = :id"
            ),
            {"id": org_node_id},
        )
        node = row.first()
    assert node is not None
    assert node.name == "W4-Renamed"


async def test_w5_patch_stores_parent_org_node_id_reparents_end_to_end(
    app_client,
    make_tenant,
    make_org_node,
    cleanup_stores_router,
    super_admin_jwt,
    session_factory,
    platform_auth,
) -> None:
    """PATCH /stores with ``parent_org_node_id`` reparents the paired
    org_node. Verified via direct DB read of the paired org_node's
    parent_id after the request commits.

    Uses make_org_node for the second parent so its teardown runs in
    the correct order after cleanup_stores_router has removed the
    paired org_node's FK ref.
    """
    schema = get_settings().db_schema
    t = await make_tenant(name="W5-T", with_root=True)
    initial_parent_id = await _ensure_tenant_root(
        session_factory, platform_auth, t.id
    )
    initial_parent_path = f"t_{t.id.hex[:8]}"

    # Build a second HQ-level org_node to reparent under, via the
    # tracked fixture so teardown handles its DELETE in order.
    new_parent_id, _ = await make_org_node(
        tenant_id=t.id,
        node_type="HQ",
        code=f"w5-newhq-{uuid.uuid4().hex[:6]}",
        name="W5-NewHQ",
        parent_id=initial_parent_id,
        parent_path=initial_parent_path,
    )

    create_body = _valid_create_body(
        tenant_id=t.id, parent_org_node_id=initial_parent_id,
        name="W5-Store", store_code="W5-001",
    )
    resp_a = app_client.post(
        "/api/v1/stores", json=create_body, headers=_auth(super_admin_jwt)
    )
    assert resp_a.status_code == 201
    j_a = resp_a.json()
    cleanup_stores_router.append(UUID(j_a["id"]))
    org_node_id = j_a["org_node_id"]

    resp_b = app_client.patch(
        f"/api/v1/stores/{j_a['id']}",
        json={"parent_org_node_id": str(new_parent_id)},
        headers=_auth(super_admin_jwt),
    )
    assert resp_b.status_code == 200, resp_b.text

    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        row = await session.execute(
            text(
                f"SELECT parent_id FROM {schema}.org_nodes WHERE id = :id"
            ),
            {"id": org_node_id},
        )
        node = row.first()
    assert node is not None
    assert node.parent_id == new_parent_id
