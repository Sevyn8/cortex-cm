"""Integration tests for the stores set-status endpoint (Step 6.17.4).

Coverage shape:

  RT1-RT13: POST /api/v1/stores/{store_id}/set-status
  MG:       mandatory-gate-discipline anchor

Nine LOAD-BEARING regression tests cited by ID in the final report:
  RT1, RT2, RT3, RT4, RT5, RT6, RT7, RT8, RT9, MG.

Pattern. Each router test uses ``make_store(status=...)`` to set the
starting status directly and then drives POST against the endpoint
via the FastAPI TestClient. Cross-tenant probes route through the
anchor dep (RLS-as-404 per D-17).

Response envelope (Finding B): tests assert ``status_code`` and
``code`` only. Structured context (``store_id``, ``target_status``)
reaches ``exc.context`` for logs per the Q7 lock; response body
``details`` stays ``null``. No body-details assertions on RT7 / RT8.

Class C copy note (Finding C, option A): the response message will
literally read "Tenant cannot transition to the requested state."
for a store transition, because the class's ``public_message`` is
tenant-flavored and reused as-is by stores (matches the existing
tenant_users precedent). Tests do NOT assert the wording; a future
FN-AB tracks generalising the class to be resource-agnostic.
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
from admin_backend.models.store import StoreStatus


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,
    session_factory: Any,
) -> Iterator[TestClient]:
    """TestClient with engine + session_factory wired onto app.state.

    Mirrors test_stores_router.py / test_stores_writes_router.py —
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


# ============================================================================
# POST /stores/{store_id}/set-status (RT1-RT13)
# ============================================================================


async def test_rt1_super_admin_opening_to_active_returns_200(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """LOAD-BEARING — SUPER_ADMIN: OPENING -> ACTIVE: 200 + StoreDetail
    with status=ACTIVE."""
    t = await make_tenant(name="RT1-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="RT1-Store", status=StoreStatus.OPENING
    )
    resp = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "ACTIVE"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["status"] == "ACTIVE"
    assert j["id"] == str(store.id)
    assert j["closed_at"] is None


async def test_rt2_super_admin_active_to_closed_populates_closed_at(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """LOAD-BEARING — Class 1 path through the wire: ACTIVE -> CLOSED:
    200 + closed_at populated in StoreDetail."""
    t = await make_tenant(name="RT2-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="RT2-Store", status=StoreStatus.ACTIVE
    )
    resp = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "CLOSED"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["status"] == "CLOSED"
    assert j["closed_at"] is not None


async def test_rt3_super_admin_closed_to_active_nulls_closed_at(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """LOAD-BEARING — Class 2 path: CLOSED -> ACTIVE: 200; closed_at
    null in response.

    Two-step setup: ACTIVE -> CLOSED via the wire, then CLOSED ->
    ACTIVE. The DDL CHECK rejects bare make_store(status=CLOSED)
    without the closed_* triplet, so we transition into CLOSED via
    the endpoint under test (which populates the triplet correctly).
    """
    t = await make_tenant(name="RT3-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="RT3-Store", status=StoreStatus.ACTIVE
    )
    pre = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "CLOSED"},
        headers=_auth(super_admin_jwt),
    )
    assert pre.status_code == 200, pre.text

    resp = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "ACTIVE"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["status"] == "ACTIVE"
    assert j["closed_at"] is None


async def test_rt4_owner_own_tenant_active_to_inactive_returns_200(
    app_client,
    make_tenant,
    make_store,
    tenant_owner_jwt_factory,
):
    """LOAD-BEARING — OWNER (own tenant store): ACTIVE -> INACTIVE: 200."""
    t = await make_tenant(name="RT4-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="RT4-Store", status=StoreStatus.ACTIVE
    )
    jwt = await tenant_owner_jwt_factory(
        t.id,
        with_grants=[("ADMIN", "STORES", "CONFIGURE", "TENANT")],
    )
    resp = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "INACTIVE"},
        headers=_auth(jwt),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "INACTIVE"


async def test_rt5_owner_cross_tenant_returns_404(
    app_client,
    make_tenant,
    make_store,
    tenant_owner_jwt_factory,
):
    """LOAD-BEARING — OWNER of tenant A targeting tenant B's store:
    404 STORE_NOT_FOUND via the anchor dep (RLS-as-404)."""
    t_a = await make_tenant(name="RT5-A", with_root=True)
    t_b = await make_tenant(name="RT5-B", with_root=True)
    store_b = await make_store(
        tenant_id=t_b.id, name="RT5-B-Store", status=StoreStatus.ACTIVE
    )
    jwt = await tenant_owner_jwt_factory(
        t_a.id,
        with_grants=[("ADMIN", "STORES", "CONFIGURE", "TENANT")],
    )
    resp = app_client.post(
        f"/api/v1/stores/{store_b.id}/set-status",
        json={"target_status": "INACTIVE"},
        headers=_auth(jwt),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "STORE_NOT_FOUND"


async def test_rt6_tenant_no_grants_returns_403_permission_denied(
    app_client,
    settings,
    make_tenant,
    make_store,
):
    """LOAD-BEARING — TENANT JWT with no grants -> 403 PERMISSION_DENIED."""
    t = await make_tenant(name="RT6-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="RT6-Store", status=StoreStatus.ACTIVE
    )
    jwt = _tenant_jwt(settings, t.id)
    resp = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "INACTIVE"},
        headers=_auth(jwt),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "PERMISSION_DENIED"


async def test_rt7_active_to_opening_returns_409_invalid_state(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """LOAD-BEARING — ACTIVE -> OPENING: 409 INVALID_STATE_TRANSITION
    (rejected per LD1, *->OPENING not in matrix).

    Per Finding B (Q7 envelope lock), response body asserts only
    ``status_code`` and ``code``. Structured context (store_id,
    target_status) reaches ``exc.context`` for logs; the wire
    ``details`` stays ``null``.
    """
    t = await make_tenant(name="RT7-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="RT7-Store", status=StoreStatus.ACTIVE
    )
    resp = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "OPENING"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "INVALID_STATE_TRANSITION"


async def test_rt8_same_state_returns_409(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """LOAD-BEARING — ACTIVE -> ACTIVE: 409 INVALID_STATE_TRANSITION
    per LD5 (target NOT in own allowed-sources set)."""
    t = await make_tenant(name="RT8-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="RT8-Store", status=StoreStatus.ACTIVE
    )
    resp = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "ACTIVE"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "INVALID_STATE_TRANSITION"


async def test_rt9_reason_field_accepted_no_observable_side_effect(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """LOAD-BEARING — ``reason`` accepted in body per LD3 forward-
    compatibility; response unchanged (no audit_log yet)."""
    t = await make_tenant(name="RT9-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="RT9-Store", status=StoreStatus.OPENING
    )
    resp = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={
            "target_status": "ACTIVE",
            "reason": "Initial post-buildout review complete; opening for sales.",
        },
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["status"] == "ACTIVE"
    # `reason` is consumed by Pydantic but NOT surfaced anywhere in
    # the response (no field on StoreDetail). Confirm absence.
    assert "reason" not in j


async def test_rt10_reason_field_omitted_accepted(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """``reason`` omitted: accepted, 200 returned."""
    t = await make_tenant(name="RT10-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="RT10-Store", status=StoreStatus.OPENING
    )
    resp = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "ACTIVE"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text


async def test_rt11_extra_field_returns_422_extra_forbid(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """Extra field in body (e.g., store_code, status) -> 422 from
    Pydantic ``extra="forbid"``."""
    t = await make_tenant(name="RT11-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="RT11-Store", status=StoreStatus.OPENING
    )
    resp = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "ACTIVE", "store_code": "should-not-land"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text


async def test_rt12_unknown_store_id_returns_404(
    app_client,
    super_admin_jwt,
):
    """Unknown store_id -> 404 STORE_NOT_FOUND (anchor dep miss path)."""
    ephemeral = uuid.uuid4()
    resp = app_client.post(
        f"/api/v1/stores/{ephemeral}/set-status",
        json={"target_status": "ACTIVE"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "STORE_NOT_FOUND"


async def test_rt13_invalid_target_status_returns_422(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
):
    """``target_status`` not in the StoreStatus enum -> 422 from Pydantic."""
    t = await make_tenant(name="RT13-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="RT13-Store", status=StoreStatus.ACTIVE
    )
    resp = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "BANANA"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text


# ============================================================================
# Mandatory-gate-discipline anchor
# ============================================================================


def test_mg_set_status_endpoint_carries_gate_marker() -> None:
    """LOAD-BEARING — POST /stores/{store_id}/set-status carries the
    ``__permission_gate__`` marker.

    Scoped, named-route assertion that complements the broader
    ``tests/integration/test_gate_discipline.py`` meta-test. A future
    refactor that accidentally drops ``Depends(require(...))`` from
    the set-status handler fails here with a clear message.
    """
    app = create_app()
    target = ("POST", "/api/v1/stores/{store_id}/set-status")
    seen = False
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if (method, route.path) != target:
                continue
            has_gate = any(
                hasattr(dep.call, "__permission_gate__")
                for dep in route.dependant.dependencies
            )
            assert has_gate, (
                f"{method} {route.path}: no __permission_gate__ marker; "
                "gate is required."
            )
            seen = True
    assert seen, f"route not registered: {target}"


# ============================================================================
# SS: Step 6.21.2 set-status cascade tests.
#
# End-to-end via the set-status endpoint. Confirms that the cascade to
# the paired STORE-type org_node's status + archived_* triplet runs
# under the same actor + transaction as the store-side write.
# ============================================================================


async def test_ss1_set_status_closed_cascades_to_org_node_archived(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — store ACTIVE -> CLOSED cascades to paired
    STORE-type org_node ARCHIVED. Verified via direct DB read on the
    paired org_node after the request commits.

    Confirms STORE_STATUS_TO_ORG_NODE_STATUS[CLOSED] = ARCHIVED routes
    through the set-status endpoint end-to-end (not just at the
    StoresRepo.transition layer covered by PW8).
    """
    from sqlalchemy import text
    from admin_backend.config import get_settings
    from admin_backend.db.session import get_tenant_session

    schema = get_settings().db_schema
    t = await make_tenant(name="SS1-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="SS1-Store", status=StoreStatus.ACTIVE
    )

    resp = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "CLOSED"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["status"] == "CLOSED"

    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        row = await session.execute(
            text(
                f"SELECT status, archived_at FROM {schema}.org_nodes "
                "WHERE id = :id"
            ),
            {"id": store.org_node_id},
        )
        node = row.first()
    assert node is not None
    assert node.status == "ARCHIVED"
    assert node.archived_at is not None


async def test_ss2_set_status_active_from_closed_unarchives_org_node(
    app_client,
    make_tenant,
    make_store,
    super_admin_jwt,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — store CLOSED -> ACTIVE cascades to paired
    org_node ARCHIVED -> ACTIVE; archived_* triplet nulled.
    """
    from sqlalchemy import text
    from admin_backend.config import get_settings
    from admin_backend.db.session import get_tenant_session

    schema = get_settings().db_schema
    t = await make_tenant(name="SS2-T", with_root=True)
    store = await make_store(
        tenant_id=t.id, name="SS2-Store", status=StoreStatus.ACTIVE
    )

    # Close it first (sets paired org_node to ARCHIVED).
    resp_close = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "CLOSED"},
        headers=_auth(super_admin_jwt),
    )
    assert resp_close.status_code == 200

    # Revive.
    resp = app_client.post(
        f"/api/v1/stores/{store.id}/set-status",
        json={"target_status": "ACTIVE"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["status"] == "ACTIVE"
    assert j["closed_at"] is None

    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        row = await session.execute(
            text(
                f"SELECT status, archived_at, archived_by_user_id "
                f"FROM {schema}.org_nodes WHERE id = :id"
            ),
            {"id": store.org_node_id},
        )
        node = row.first()
    assert node is not None
    assert node.status == "ACTIVE"
    assert node.archived_at is None
    assert node.archived_by_user_id is None
