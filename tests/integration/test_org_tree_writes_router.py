"""Integration tests for the Step 6.13 org-tree write endpoints.

Coverage shape:

  Add Node (POST /api/v1/tenants/{tenant_id}/org-tree):
    C1-C3   happy paths (SUPER_ADMIN, OWNER, level-skip)
    V1-V7   validation failures
    P1-P4   permission boundary + caller variants
    PA1     PLATFORM_ADMIN happy via GLOBAL->TENANT cascade (FN-AB-47)

  Edit Node (PATCH /api/v1/tenants/{tenant_id}/org-tree/{node_id}):
    E1-E12  rename, recode, reparent, combined, cycle, tenant-root,
            empty-body, role-assignment stability, duplicate, missing
    PA2     PLATFORM_ADMIN write happy via GLOBAL->TENANT cascade
            (FN-AB-47)

LOAD-BEARING tests called out inline.

Cascade-order rule note. The canonical order is:
TENANT(0) -> BUSINESS_UNIT(1) -> HQ(2) -> COUNTRY(3) -> REGION(4) ->
STORE(5) -> DEPARTMENT(6). A parent's ordinal must be STRICTLY less
than the child's; equal ordinals and reversals are rejected. Level
skipping IS allowed (parent ord < child ord with gaps). So:

  - HQ (2) under REGION (4): reversal -> rejected. (V2)
  - STORE (5) under STORE (5): equal ord -> rejected. (V3)
  - STORE (5) under TENANT (0): skip, 0 < 5 -> OK. (C3)
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
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
    from admin_backend.auth.stub import StubAuthClient

    app_obj = create_app()
    app_obj.state.settings = settings
    app_obj.state.engine = engine
    app_obj.state.session_factory = session_factory
    app_obj.state.auth_client = StubAuthClient(settings)
    with TestClient(app_obj) as client:
        yield client


@pytest_asyncio.fixture
async def cleanup_org_nodes_router(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """DELETE in REVERSE insertion order (composite FK)."""
    created: list[UUID] = []
    yield created
    if created:
        schema = get_settings().db_schema
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            for node_id in reversed(created):
                await session.execute(
                    text(
                        f"DELETE FROM {schema}.org_nodes WHERE id = :id"
                    ),
                    {"id": node_id},
                )


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _tenant_jwt(settings: Settings, tenant_id: UUID) -> str:
    """Random-uuid TENANT JWT for negative-permission tests."""
    return make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="TENANT",
        tenant_id=tenant_id,
    )


async def _fetch_tenant_root(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    tenant_id: UUID,
) -> tuple[UUID, str]:
    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        result = await session.execute(
            text(
                f"SELECT id, path::text AS path FROM {schema}.org_nodes "
                "WHERE tenant_id = :tid "
                f"AND node_type = CAST('TENANT' AS {schema}.org_node_type_enum) "
                "AND parent_id IS NULL"
            ),
            {"tid": tenant_id},
        )
        row = result.first()
    if row is None:
        raise RuntimeError(f"no tenant root for tenant {tenant_id}")
    return UUID(str(row.id)), str(row.path)


async def _mint_platform_admin_jwt(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> str:
    """Mint a JWT for seeded Devon (PLATFORM_ADMIN role).

    Devon holds the post-Phase-3b ADMIN.ORG_NODES.CONFIGURE.GLOBAL grant.
    Cascade resolves to .TENANT on the gate. Used by PA1/PA2 to close
    FN-AB-47's coverage gap.
    """
    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        result = await session.execute(
            text(
                f"SELECT id FROM {schema}.platform_users "
                "WHERE email = :email"
            ),
            {"email": "devon@ithina.ai"},
        )
        row = result.first()
    if row is None:
        raise LookupError(
            "Seed user 'devon@ithina.ai' not found in platform_users."
        )
    return make_test_jwt(
        settings,
        user_id=UUID(str(row[0])),
        user_type="PLATFORM",
    )


# ============================================================================
# Add Node — happy paths
# ============================================================================


async def test_c1_super_admin_adds_region_under_business_unit(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    cleanup_org_nodes_router: list[UUID],
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING — SUPER_ADMIN happy path: add REGION under BUSINESS_UNIT.

    Step 6.21.2: pre-6.21.2 C1 added a STORE; STORE-type creates via
    POST /org-tree are now rejected (use POST /stores). REGION is a
    non-STORE non-TENANT type that still validates the happy path
    (parent existence, cascade-order, path build, INSERT).
    """
    tenant = await make_tenant(name="C1 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu, p_bu = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"c1-bu-{uuid.uuid4().hex[:6]}", name="BU",
        parent_id=troot_id, parent_path=troot_path,
    )

    region_code = f"c1-region-{uuid.uuid4().hex[:6]}"
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(bu),
            "node_type": "REGION",
            "code": region_code,
            "name": "C1 Region",
        },
    )
    assert resp.status_code == 201, resp.text
    j = resp.json()
    cleanup_org_nodes_router.append(UUID(j["id"]))
    assert j["node_type"] == "REGION"
    assert j["code"] == region_code
    assert j["parent_id"] == str(bu)
    expected_path = (
        f"{p_bu}.{region_code.lower().replace('-', '_')}"
    )
    assert j["path"] == expected_path


async def test_c2_owner_adds_department_under_store(
    app_client: TestClient,
    make_tenant: Any,
    make_org_node: Any,
    tenant_owner_jwt_factory: Any,
    cleanup_org_nodes_router: list[UUID],
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING — OWNER (TENANT-side) direct .TENANT grant cascade."""
    tenant = await make_tenant(name="C2 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu, p_bu = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"c2-bu-{uuid.uuid4().hex[:6]}", name="BU",
        parent_id=troot_id, parent_path=troot_path,
    )
    store, _ = await make_org_node(
        tenant_id=tenant.id, node_type="STORE",
        code=f"c2-store-{uuid.uuid4().hex[:6]}", name="Store",
        parent_id=bu, parent_path=p_bu,
    )

    owner_jwt = await tenant_owner_jwt_factory(
        tenant.id,
        with_grants=[("ADMIN", "ORG_NODES", "CONFIGURE", "TENANT")],
    )
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(owner_jwt),
        json={
            "parent_id": str(store),
            "node_type": "DEPARTMENT",
            "code": f"c2-dept-{uuid.uuid4().hex[:6]}",
            "name": "Bakery",
        },
    )
    assert resp.status_code == 201, resp.text
    cleanup_org_nodes_router.append(UUID(resp.json()["id"]))


async def test_c3_super_admin_skips_levels_region_under_tenant_root(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    cleanup_org_nodes_router: list[UUID],
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING — level-skipping: REGION under TENANT root OK.

    Step 6.21.2: pre-6.21.2 C3 added a STORE; STORE-type creates via
    POST /org-tree are now rejected (use POST /stores). REGION
    (ordinal=4) under TENANT (ordinal=0) is the equivalent
    level-skipping case.
    """
    tenant = await make_tenant(name="C3 Tenant", with_root=True)
    troot_id, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )

    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "REGION",
            "code": f"c3-region-{uuid.uuid4().hex[:6]}",
            "name": "C3 Region",
        },
    )
    assert resp.status_code == 201, resp.text
    cleanup_org_nodes_router.append(UUID(resp.json()["id"]))


# ============================================================================
# Add Node — validation failures
# ============================================================================


async def test_v1_node_type_tenant_rejected(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """422 on node_type='TENANT' (Pydantic model_validator)."""
    tenant = await make_tenant(name="V1 Tenant", with_root=True)
    troot_id, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "TENANT",
            "code": "would-be-second-root",
            "name": "Forbidden Root",
        },
    )
    assert resp.status_code == 422


async def test_v2_hq_under_region_rejected_as_reversal(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING — cascade-order reversal: HQ (2) under REGION (4)."""
    tenant = await make_tenant(name="V2 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu, p_bu = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"v2-bu-{uuid.uuid4().hex[:6]}", name="BU",
        parent_id=troot_id, parent_path=troot_path,
    )
    region, _ = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code=f"v2-r-{uuid.uuid4().hex[:6]}", name="Region",
        parent_id=bu, parent_path=p_bu,
    )

    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(region),
            "node_type": "HQ",
            "code": f"v2-hq-{uuid.uuid4().hex[:6]}",
            "name": "HQ",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_PARENT_NODE_TYPE"


async def test_v3_equal_ordinal_rejected_as_same_ordinal(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING — equal-ord cascade reject: HQ under HQ.

    Step 6.21.2 rejects ``node_type='STORE'`` on POST entirely, so the
    pre-6.21.2 V3 ("STORE under STORE") is unreachable via the API.
    HQ-under-HQ is the equivalent equal-ordinal case (both ordinal=2).
    """
    tenant = await make_tenant(name="V3 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    hq1, p_hq1 = await make_org_node(
        tenant_id=tenant.id, node_type="HQ",
        code=f"v3-hq1-{uuid.uuid4().hex[:6]}", name="HQ1",
        parent_id=troot_id, parent_path=troot_path,
    )

    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(hq1),
            "node_type": "HQ",
            "code": f"v3-hq2-{uuid.uuid4().hex[:6]}",
            "name": "HQ2",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_PARENT_NODE_TYPE"


async def test_v4_parent_id_random_uuid_404_parent_not_found(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
) -> None:
    """404 PARENT_NODE_NOT_FOUND when parent_id doesn't exist."""
    tenant = await make_tenant(name="V4 Tenant", with_root=True)
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(uuid.uuid4()),
            "node_type": "HQ",
            "code": "v4-orphan",
            "name": "Orphan",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "PARENT_NODE_NOT_FOUND"


async def test_v5_duplicate_code_returns_409(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    cleanup_org_nodes_router: list[UUID],
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING — DDL UNIQUE on (tenant_id, lower(code)) -> 409."""
    tenant = await make_tenant(name="V5 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    existing_code = f"v5-bu-{uuid.uuid4().hex[:6]}"
    await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=existing_code, name="BU",
        parent_id=troot_id, parent_path=troot_path,
    )
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "HQ",
            "code": existing_code,
            "name": "Dup",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "DUPLICATE_ORG_NODE_CODE"


async def test_v6_duplicate_code_case_insensitive_returns_409(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING — uniqueness is lower(code), case-insensitive."""
    tenant = await make_tenant(name="V6 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    existing_code = f"v6-store-{uuid.uuid4().hex[:6]}"
    await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=existing_code, name="BU",
        parent_id=troot_id, parent_path=troot_path,
    )

    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "HQ",
            "code": existing_code.upper(),
            "name": "Dup-CASE",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "DUPLICATE_ORG_NODE_CODE"


async def test_v7_invalid_code_format_pydantic_422(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """Pydantic regex rejects underscore/leading-hyphen."""
    tenant = await make_tenant(name="V7 Tenant", with_root=True)
    troot_id, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    # Underscore violates the code regex.
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "HQ",
            "code": "bad_code",
            "name": "x",
        },
    )
    assert resp.status_code == 422
    # Leading hyphen.
    resp2 = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "HQ",
            "code": "-leading",
            "name": "x",
        },
    )
    assert resp2.status_code == 422


# ============================================================================
# Add Node — permission boundary
# ============================================================================


async def test_p1_owner_tenant_caller_adds_happy(
    app_client: TestClient,
    make_tenant: Any,
    tenant_owner_jwt_factory: Any,
    cleanup_org_nodes_router: list[UUID],
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """OWNER on own tenant: 201 (cascade direct)."""
    tenant = await make_tenant(name="P1 Tenant", with_root=True)
    troot_id, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    owner_jwt = await tenant_owner_jwt_factory(
        tenant.id,
        with_grants=[("ADMIN", "ORG_NODES", "CONFIGURE", "TENANT")],
    )
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(owner_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "BUSINESS_UNIT",
            "code": f"p1-bu-{uuid.uuid4().hex[:6]}",
            "name": "P1 BU",
        },
    )
    assert resp.status_code == 201
    cleanup_org_nodes_router.append(UUID(resp.json()["id"]))


async def test_p2_tenant_caller_without_role_403(
    app_client: TestClient,
    settings: Settings,
    make_tenant: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING — random TENANT JWT (no role) -> 403 PERMISSION_DENIED.

    Anchor dep resolves on the tenant root, gate's Layer 2 denies.
    """
    tenant = await make_tenant(name="P2 Tenant", with_root=True)
    troot_id, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    jwt = _tenant_jwt(settings, tenant.id)
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "HQ",
            "code": "p2-hq",
            "name": "x",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "PERMISSION_DENIED"


async def test_p3_tenant_caller_cross_tenant_404(
    app_client: TestClient,
    settings: Settings,
    make_tenant: Any,
    make_org_node: Any,
    tenant_owner_jwt_factory: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING — OWNER targeting another tenant: 404 from anchor dep
    (RLS-as-404).
    """
    tenant_a = await make_tenant(name="P3 Tenant A", with_root=True)
    tenant_b = await make_tenant(name="P3 Tenant B", with_root=True)
    troot_b_id, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant_b.id
    )
    owner_a_jwt = await tenant_owner_jwt_factory(
        tenant_a.id,
        with_grants=[("ADMIN", "ORG_NODES", "CONFIGURE", "TENANT")],
    )
    resp = app_client.post(
        f"/api/v1/tenants/{tenant_b.id}/org-tree",
        headers=_auth(owner_a_jwt),
        json={
            "parent_id": str(troot_b_id),
            "node_type": "HQ",
            "code": "p3-cross",
            "name": "x",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "TENANT_NOT_FOUND"


async def test_p4_unknown_tenant_id_404(
    app_client: TestClient,
    super_admin_jwt: str,
) -> None:
    """Unknown tenant_id -> 404 TENANT_NOT_FOUND from anchor dep."""
    resp = app_client.post(
        f"/api/v1/tenants/{uuid.uuid4()}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(uuid.uuid4()),
            "node_type": "HQ",
            "code": "p4",
            "name": "x",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "TENANT_NOT_FOUND"


async def test_pa1_platform_admin_adds_happy_via_global_cascade(
    app_client: TestClient,
    settings: Settings,
    make_tenant: Any,
    cleanup_org_nodes_router: list[UUID],
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING (FN-AB-47) — PLATFORM_ADMIN passes GLOBAL->TENANT
    cascade on the new ADMIN.ORG_NODES.CONFIGURE.GLOBAL grant.
    """
    tenant = await make_tenant(name="PA1 Tenant", with_root=True)
    troot_id, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    pa_jwt = await _mint_platform_admin_jwt(
        settings, session_factory, platform_auth
    )
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(pa_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "BUSINESS_UNIT",
            "code": f"pa1-bu-{uuid.uuid4().hex[:6]}",
            "name": "PA1 BU",
        },
    )
    assert resp.status_code == 201, resp.text
    cleanup_org_nodes_router.append(UUID(resp.json()["id"]))


# ============================================================================
# Edit Node
# ============================================================================


async def test_e1_rename_only_path_unchanged(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """PATCH {name} -> 200; path unchanged."""
    tenant = await make_tenant(name="E1 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    code = f"e1-bu-{uuid.uuid4().hex[:6]}"
    node_id, original_path = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=code, name="OldName",
        parent_id=troot_id, parent_path=troot_path,
    )
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{node_id}",
        headers=_auth(super_admin_jwt),
        json={"name": "New Name"},
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["name"] == "New Name"
    assert j["path"] == original_path


async def test_e2_code_change_path_segment_rewritten(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """PATCH {code} -> 200; path's last segment updated to new label."""
    tenant = await make_tenant(name="E2 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    old_code = f"e2-bu-{uuid.uuid4().hex[:6]}"
    node_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=old_code, name="BU",
        parent_id=troot_id, parent_path=troot_path,
    )
    new_code = f"e2-new-{uuid.uuid4().hex[:6]}"
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{node_id}",
        headers=_auth(super_admin_jwt),
        json={"code": new_code},
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["code"] == new_code
    expected_segment = new_code.lower().replace("-", "_")
    assert j["path"].endswith(f".{expected_segment}")


async def test_e3_reparent_leaf_path_updated(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """PATCH {parent_id} on a leaf: path moved under new parent."""
    tenant = await make_tenant(name="E3 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu_a, p_a = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"e3-bu-a-{uuid.uuid4().hex[:6]}", name="BU A",
        parent_id=troot_id, parent_path=troot_path,
    )
    bu_b, p_b = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"e3-bu-b-{uuid.uuid4().hex[:6]}", name="BU B",
        parent_id=troot_id, parent_path=troot_path,
    )
    leaf_code = f"e3-leaf-{uuid.uuid4().hex[:6]}"
    leaf_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="HQ",
        code=leaf_code, name="HQ",
        parent_id=bu_a, parent_path=p_a,
    )
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{leaf_id}",
        headers=_auth(super_admin_jwt),
        json={"parent_id": str(bu_b)},
    )
    assert resp.status_code == 200, resp.text
    expected = f"{p_b}.{leaf_code.lower().replace('-', '_')}"
    assert resp.json()["path"] == expected


async def test_e4_combined_rename_recode_reparent_atomic(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """PATCH all three fields atomically -> 200; all applied."""
    tenant = await make_tenant(name="E4 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu_a, p_a = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"e4-bu-a-{uuid.uuid4().hex[:6]}", name="A",
        parent_id=troot_id, parent_path=troot_path,
    )
    bu_b, p_b = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"e4-bu-b-{uuid.uuid4().hex[:6]}", name="B",
        parent_id=troot_id, parent_path=troot_path,
    )
    target_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="HQ",
        code=f"e4-hq-{uuid.uuid4().hex[:6]}", name="HQ",
        parent_id=bu_a, parent_path=p_a,
    )
    new_code = f"e4-new-{uuid.uuid4().hex[:6]}"
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{target_id}",
        headers=_auth(super_admin_jwt),
        json={
            "name": "Renamed",
            "code": new_code,
            "parent_id": str(bu_b),
        },
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["name"] == "Renamed"
    assert j["code"] == new_code
    assert j["parent_id"] == str(bu_b)
    expected = f"{p_b}.{new_code.lower().replace('-', '_')}"
    assert j["path"] == expected


async def test_e5_reparent_subtree_descendants_repathed(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING (LD7) — reparent target with 3 descendants: all
    paths updated atomically."""
    tenant = await make_tenant(name="E5 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    suffix = uuid.uuid4().hex[:6]
    bu_a, p_a = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"e5-bu-a-{suffix}", name="A",
        parent_id=troot_id, parent_path=troot_path,
    )
    bu_b, p_b = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"e5-bu-b-{suffix}", name="B",
        parent_id=troot_id, parent_path=troot_path,
    )
    hq, p_hq = await make_org_node(
        tenant_id=tenant.id, node_type="HQ",
        code=f"e5-hq-{suffix}", name="HQ",
        parent_id=bu_a, parent_path=p_a,
    )
    country, p_country = await make_org_node(
        tenant_id=tenant.id, node_type="COUNTRY",
        code=f"e5-c-{suffix}", name="C",
        parent_id=hq, parent_path=p_hq,
    )
    region, p_region = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code=f"e5-r-{suffix}", name="R",
        parent_id=country, parent_path=p_country,
    )
    store, _ = await make_org_node(
        tenant_id=tenant.id, node_type="STORE",
        code=f"e5-s-{suffix}", name="S",
        parent_id=region, parent_path=p_region,
    )

    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{hq}",
        headers=_auth(super_admin_jwt),
        json={"parent_id": str(bu_b)},
    )
    assert resp.status_code == 200, resp.text

    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        result = await session.execute(
            text(
                f"SELECT id, path::text AS path FROM {schema}.org_nodes "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": [hq, country, region, store]},
        )
        for row in result.all():
            assert row.path.startswith(p_b + "."), (
                f"descendant {row.id} path {row.path} not under {p_b}"
            )


async def test_e6_cycle_detected_reparent_under_descendant(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING (LD6) — reparent under descendant: 422 CYCLE_DETECTED."""
    tenant = await make_tenant(name="E6 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu, p_bu = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"e6-bu-{uuid.uuid4().hex[:6]}", name="BU",
        parent_id=troot_id, parent_path=troot_path,
    )
    hq, p_hq = await make_org_node(
        tenant_id=tenant.id, node_type="HQ",
        code=f"e6-hq-{uuid.uuid4().hex[:6]}", name="HQ",
        parent_id=bu, parent_path=p_bu,
    )
    # Use BUSINESS_UNIT (ord 1) for the descendant so the cascade-order
    # check on parent_type=BUSINESS_UNIT (1) < target.node_type=BU (1)
    # is NOT the failure: we want the cycle to be the failure mode.
    # Realistic shape: descendant of bu is hq (ord 2). Re-parenting BU
    # under HQ -> reversal (2 >= 1, reject), not cycle. The cycle case:
    # re-parenting BU under itself.
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{bu}",
        headers=_auth(super_admin_jwt),
        json={"parent_id": str(bu)},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "CYCLE_DETECTED"


async def test_e6b_cycle_detected_reparent_bu_under_deep_descendant(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """Cycle case where target is BU and new_parent is its descendant
    HQ. The cycle check fires BEFORE cascade-order, so the failure
    code is CYCLE_DETECTED (not INVALID_PARENT_NODE_TYPE).
    """
    tenant = await make_tenant(name="E6b Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu, p_bu = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"e6b-bu-{uuid.uuid4().hex[:6]}", name="BU",
        parent_id=troot_id, parent_path=troot_path,
    )
    hq, _ = await make_org_node(
        tenant_id=tenant.id, node_type="HQ",
        code=f"e6b-hq-{uuid.uuid4().hex[:6]}", name="HQ",
        parent_id=bu, parent_path=p_bu,
    )
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{bu}",
        headers=_auth(super_admin_jwt),
        json={"parent_id": str(hq)},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "CYCLE_DETECTED"


async def test_e7_tenant_root_reparent_rejected(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING (LD5) — PATCH {parent_id} on TENANT-type rejected."""
    tenant = await make_tenant(name="E7 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu, _ = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"e7-bu-{uuid.uuid4().hex[:6]}", name="BU",
        parent_id=troot_id, parent_path=troot_path,
    )
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{troot_id}",
        headers=_auth(super_admin_jwt),
        json={"parent_id": str(bu)},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "TENANT_ROOT_NOT_REPARENTABLE"


async def test_e8_self_parent_rejected_as_cycle(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """PATCH {parent_id: self} -> 422 CYCLE_DETECTED."""
    tenant = await make_tenant(name="E8 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu, _ = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"e8-bu-{uuid.uuid4().hex[:6]}", name="BU",
        parent_id=troot_id, parent_path=troot_path,
    )
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{bu}",
        headers=_auth(super_admin_jwt),
        json={"parent_id": str(bu)},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "CYCLE_DETECTED"


async def test_e9_empty_patch_body_422(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """PATCH {} rejected by Pydantic model_validator -> 422."""
    tenant = await make_tenant(name="E9 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu, _ = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"e9-bu-{uuid.uuid4().hex[:6]}", name="BU",
        parent_id=troot_id, parent_path=troot_path,
    )
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{bu}",
        headers=_auth(super_admin_jwt),
        json={},
    )
    assert resp.status_code == 422


async def test_e10_role_assignment_unaffected_on_move(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    tenant_owner_jwt_factory: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING (LD8) — assignment anchored at moved node stays
    intact (stable id reference)."""
    tenant = await make_tenant(name="E10 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    # Establish a tenant_owner_jwt (creates a tenant_user + role + role
    # assignment anchored at the tenant root by default).
    await tenant_owner_jwt_factory(
        tenant.id,
        with_grants=[("ADMIN", "ORG_NODES", "VIEW", "TENANT")],
    )

    schema = get_settings().db_schema
    # Pull the assignment row created by the factory.
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        pre_result = await session.execute(
            text(
                f"SELECT id, status::text AS status, org_node_id "
                f"FROM {schema}.tenant_user_role_assignments "
                "WHERE tenant_id = :tid"
            ),
            {"tid": tenant.id},
        )
        pre_rows = pre_result.all()
    assert pre_rows, "factory should have created at least one assignment"

    # Reparent the tenant-root org_node? Not allowed (E7). Instead, create
    # a child BU and an HQ under it, then move HQ under a different BU.
    # Confirm assignments anchored at the (unchanged) tenant root are
    # untouched — id stable, org_node_id stable.
    bu_a, p_a = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"e10-a-{uuid.uuid4().hex[:6]}", name="A",
        parent_id=troot_id, parent_path=troot_path,
    )
    bu_b, _ = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"e10-b-{uuid.uuid4().hex[:6]}", name="B",
        parent_id=troot_id, parent_path=troot_path,
    )
    hq, _ = await make_org_node(
        tenant_id=tenant.id, node_type="HQ",
        code=f"e10-hq-{uuid.uuid4().hex[:6]}", name="HQ",
        parent_id=bu_a, parent_path=p_a,
    )

    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{hq}",
        headers=_auth(super_admin_jwt),
        json={"parent_id": str(bu_b)},
    )
    assert resp.status_code == 200, resp.text

    # Assignment rows unchanged: same ids, same status, same org_node_id.
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        post_result = await session.execute(
            text(
                f"SELECT id, status::text AS status, org_node_id "
                f"FROM {schema}.tenant_user_role_assignments "
                "WHERE tenant_id = :tid"
            ),
            {"tid": tenant.id},
        )
        post_rows = post_result.all()
    pre_set = {(r.id, r.status, r.org_node_id) for r in pre_rows}
    post_set = {(r.id, r.status, r.org_node_id) for r in post_rows}
    assert pre_set == post_set


async def test_e11_patch_code_duplicate_409(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """PATCH {code} colliding with another row in same tenant -> 409."""
    tenant = await make_tenant(name="E11 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    code_a = f"e11-a-{uuid.uuid4().hex[:6]}"
    code_b = f"e11-b-{uuid.uuid4().hex[:6]}"
    await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=code_a, name="A",
        parent_id=troot_id, parent_path=troot_path,
    )
    target_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=code_b, name="B",
        parent_id=troot_id, parent_path=troot_path,
    )
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{target_id}",
        headers=_auth(super_admin_jwt),
        json={"code": code_a},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "DUPLICATE_ORG_NODE_CODE"


async def test_e12_patch_random_node_id_404(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
) -> None:
    """PATCH on random UUID under known tenant -> 404 ORG_NODE_NOT_FOUND."""
    tenant = await make_tenant(name="E12 Tenant", with_root=True)
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{uuid.uuid4()}",
        headers=_auth(super_admin_jwt),
        json={"name": "doesn't matter"},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "ORG_NODE_NOT_FOUND"


async def test_pa2_platform_admin_patches_happy_via_global_cascade(
    app_client: TestClient,
    settings: Settings,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING (FN-AB-47) — PLATFORM_ADMIN PATCH happy via cascade.

    Demonstrates that the new ADMIN.ORG_NODES.CONFIGURE.GLOBAL grant
    resolves to .TENANT for both POST (PA1) and PATCH (here).
    """
    tenant = await make_tenant(name="PA2 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"pa2-bu-{uuid.uuid4().hex[:6]}", name="BU",
        parent_id=troot_id, parent_path=troot_path,
    )
    pa_jwt = await _mint_platform_admin_jwt(
        settings, session_factory, platform_auth
    )
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{bu_id}",
        headers=_auth(pa_jwt),
        json={"name": "Renamed by PLATFORM_ADMIN"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed by PLATFORM_ADMIN"


# ============================================================================
# Step 6.21.2 — POST node_type='STORE' rejection (V8) and PATCH STORE-target
# shared-field rejection (E13, E14, E16). E15 dropped per Deviation #2:
# OrgNodePatchRequest has no ``status`` field, so a body with ``status``
# is already 422'd by Pydantic's extra_forbidden; the new check only
# fires on ``name`` and ``code``.
# ============================================================================


async def test_v8_node_type_store_rejected_on_post(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING — Step 6.21.2: POST /org-tree with
    ``node_type='STORE'`` returns 422. The Pydantic model_validator
    on ``OrgNodeCreateRequest`` rejects the value before the handler
    runs. Mirrors V1's shape (generic Pydantic 422; no dedicated
    wire code per Deviation #2 / LD10 dropped)."""
    tenant = await make_tenant(name="V8 Tenant", with_root=True)
    troot_id, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "STORE",
            "code": "v8-store",
            "name": "Forbidden Store",
        },
    )
    assert resp.status_code == 422


async def test_e13_patch_store_type_with_name_rejected(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING — Step 6.21.2: PATCH /org-tree/{node_id} on a
    STORE-type target with ``name`` in body -> 422
    ORG_NODE_FIELD_NOT_ALLOWED_FOR_TYPE. ``name`` is owned by the
    /stores endpoints per architecture.md A.5 "Field ownership".
    """
    tenant = await make_tenant(name="E13 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    # Create a STORE-type node directly via fixture (the API surface
    # forbids creating STORE via POST per V8).
    store_node, _ = await make_org_node(
        tenant_id=tenant.id,
        node_type="STORE",
        code=f"e13-{uuid.uuid4().hex[:6]}",
        name="E13 Store Node",
        parent_id=troot_id,
        parent_path=troot_path,
    )

    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{store_node}",
        headers=_auth(super_admin_jwt),
        json={"name": "Attempted Rename"},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "ORG_NODE_FIELD_NOT_ALLOWED_FOR_TYPE"


async def test_e14_patch_store_type_with_code_rejected(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING — STORE-type target with ``code`` in body -> 422
    ORG_NODE_FIELD_NOT_ALLOWED_FOR_TYPE."""
    tenant = await make_tenant(name="E14 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    store_node, _ = await make_org_node(
        tenant_id=tenant.id,
        node_type="STORE",
        code=f"e14-{uuid.uuid4().hex[:6]}",
        name="E14 Store Node",
        parent_id=troot_id,
        parent_path=troot_path,
    )

    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{store_node}",
        headers=_auth(super_admin_jwt),
        json={"code": "e14-renamed"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "ORG_NODE_FIELD_NOT_ALLOWED_FOR_TYPE"


async def test_e16_patch_store_type_with_parent_id_only_succeeds(
    app_client: TestClient,
    super_admin_jwt: str,
    make_tenant: Any,
    make_org_node: Any,
    session_factory: Any,
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING positive case — STORE-type reparent via /org-tree
    is ALLOWED (architecture.md A.5 "Parent ownership: dual-endpoint
    write"). If the new field-rejection check is too aggressive and
    blocks parent_id changes too, this test catches the over-reach.
    """
    tenant = await make_tenant(name="E16 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    # Two HQ-typed nodes (cascade-order allows STORE under HQ).
    hq1, p_hq1 = await make_org_node(
        tenant_id=tenant.id,
        node_type="HQ",
        code=f"e16-hq1-{uuid.uuid4().hex[:6]}",
        name="E16 HQ1",
        parent_id=troot_id,
        parent_path=troot_path,
    )
    hq2, _ = await make_org_node(
        tenant_id=tenant.id,
        node_type="HQ",
        code=f"e16-hq2-{uuid.uuid4().hex[:6]}",
        name="E16 HQ2",
        parent_id=troot_id,
        parent_path=troot_path,
    )
    store_node, _ = await make_org_node(
        tenant_id=tenant.id,
        node_type="STORE",
        code=f"e16-st-{uuid.uuid4().hex[:6]}",
        name="E16 Store Node",
        parent_id=hq1,
        parent_path=p_hq1,
    )

    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{store_node}",
        headers=_auth(super_admin_jwt),
        json={"parent_id": str(hq2)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["parent_id"] == str(hq2)
