"""Step 6.16.5 : audit emission for stores POST / PATCH / set-status.

Per-endpoint success + failure coverage of the stores write surface.

Locked decisions:

  - LD3: set-status dispatches on ``target_status`` to pick one of 4
    per-target action codes on the SUCCESS path (OPEN_SOFT / ACTIVATE
    / CLOSE / DEACTIVATE). The failure-path action code stays
    ``SET_STATUS`` (single fallback because the failure handler can
    not re-parse the request body to determine target_status).
  - LD6: POST /stores success row carries
    ``org_node_created_atomically: True`` in ``details.snapshot``;
    under 6.21.2 atomic-pair this is always True in v0 (FN-AB-68
    reserves the False branch for forward variants).
  - LD13: anchor-404 paths (PATCH / set-status on missing store_id)
    emit ZERO audit rows.

Test catalogue adjustment per operator authorisation:

  - SS5 (target=OPENING) is DROPPED: no TRANSITION_MATRIX cell allows
    ``*->OPENING`` (entry-only via POST per 6.17.4 LD1). AE11 unit
    test covers the OPEN_SOFT -> "Soft-opened" label dispatch.
  - SS1/SS2 (pre-existing org_node vs atomic-pair) are CONSOLIDATED:
    every POST /stores creates the org_node atomically under 6.21.2;
    the False branch of LD6's flag is unreachable today.

15 tests total. LOAD-BEARING: SS_atomic, SS_activate, SS_close,
SF_dupcode, SF_invalid_transition, SF_anchor_404.
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


pytestmark = pytest.mark.asyncio


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


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _tenant_jwt(settings: Settings, tenant_id: UUID) -> str:
    return make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="TENANT",
        tenant_id=tenant_id,
    )


def _valid_create_body(
    *,
    tenant_id: UUID,
    parent_org_node_id: UUID,
    name: str,
    store_code: str,
) -> dict[str, Any]:
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
        raise RuntimeError(f"no tenant root for tenant {tenant_id}")
    return UUID(str(row.id))


@pytest_asyncio.fixture
async def cleanup_stores_audit(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
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


@pytest_asyncio.fixture
async def cleanup_orphan_platform_audit_stores(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[str]]:
    """Track ``X-Request-Id`` of POST /stores failure rows that route
    to ``platform_activity_audit_logs`` with ``tenant_id=NULL`` per
    LD10. The make_tenant teardown chain doesn't reach these (no
    tenant_id linkage); test deletes them by request_id at teardown.

    Mirrors test_audit_emission_failures.py's
    ``cleanup_orphan_platform_audit`` pattern (tenants POST failure).
    """
    schema = get_settings().db_schema
    tracked: list[str] = []
    yield tracked
    if tracked:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.platform_activity_audit_logs "
                    "WHERE request_id = ANY(CAST(:rids AS uuid[]))"
                ),
                {"rids": tracked},
            )


async def _fetch_audit_rows(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    tenant_id: UUID,
) -> list[dict[str, Any]]:
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"""
                SELECT id, action, action_label, resource_type, resource_id,
                       resource_label, result_type::text AS result_type,
                       tenant_id, tenant_name, request_id, details
                  FROM {schema}.tenant_activity_audit_logs
                 WHERE tenant_id = :tenant_id
                   AND resource_type = 'STORE'
                 ORDER BY timestamp ASC, id ASC
                """
            ),
            {"tenant_id": tenant_id},
        )
        return [dict(row) for row in result.mappings()]
    raise AssertionError("unreachable")  # pragma: no cover


# ============================================================================
# Success-path
# ============================================================================


async def test_ss_atomic_post_emits_create_with_org_node_atomic_true(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_stores_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING (consolidates SS1+SS2) — every POST /stores creates
    the paired org_node atomically under 6.21.2; the audit snapshot
    carries ``org_node_created_atomically: True`` per LD6 (always
    True in v0 per FN-AB-68).
    """
    tenant = await make_tenant(name="SS-Atomic-Tenant", with_root=True)
    parent_id = await _ensure_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    code = f"SSA-{uuid.uuid4().hex[:6]}"
    resp = app_client.post(
        "/api/v1/stores",
        json=_valid_create_body(
            tenant_id=tenant.id, parent_org_node_id=parent_id,
            name="Atomic Store", store_code=code,
        ),
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201, resp.text
    store_id = UUID(resp.json()["id"])
    cleanup_stores_audit.append(store_id)

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "CREATE"
    assert r["resource_type"] == "STORE"
    assert UUID(str(r["resource_id"])) == store_id
    snap = r["details"]["snapshot"]
    assert snap["name"] == "Atomic Store"
    assert snap["store_code"] == code
    assert snap["org_node_created_atomically"] is True
    assert snap["org_node_id"] is not None
    assert snap["org_node_name"] == "Atomic Store"


async def test_ss_patch_rename_emits_update_with_name_diff(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_stores_audit,
    session_factory,
    platform_auth,
) -> None:
    """PATCH rename produces an UPDATE audit row with the name diff."""
    tenant = await make_tenant(name="SSR-Tenant", with_root=True)
    parent_id = await _ensure_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    resp = app_client.post(
        "/api/v1/stores",
        json=_valid_create_body(
            tenant_id=tenant.id, parent_org_node_id=parent_id,
            name="Old Name", store_code=f"SSR-{uuid.uuid4().hex[:6]}",
        ),
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201
    store_id = UUID(resp.json()["id"])
    cleanup_stores_audit.append(store_id)

    resp = app_client.patch(
        f"/api/v1/stores/{store_id}",
        json={"name": "New Name"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    # 1 CREATE + 1 UPDATE
    assert len(rows) == 2
    update_row = rows[1]
    assert update_row["action"] == "UPDATE"
    assert update_row["details"]["before"]["name"] == "Old Name"
    assert update_row["details"]["after"]["name"] == "New Name"


async def test_ss_patch_with_same_value_emits_zero_extra_rows(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_stores_audit,
    session_factory,
    platform_auth,
) -> None:
    """PATCH where every field equals current value: zero UPDATE rows.

    Diff builder filters to actual changes; an all-no-change PATCH
    falls through without emitting (per LD14 / LD2 invariant pattern).
    """
    tenant = await make_tenant(name="SSnop-Tenant", with_root=True)
    parent_id = await _ensure_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    resp = app_client.post(
        "/api/v1/stores",
        json=_valid_create_body(
            tenant_id=tenant.id, parent_org_node_id=parent_id,
            name="Same Name", store_code=f"SSnop-{uuid.uuid4().hex[:6]}",
        ),
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201
    store_id = UUID(resp.json()["id"])
    cleanup_stores_audit.append(store_id)

    resp = app_client.patch(
        f"/api/v1/stores/{store_id}",
        json={"name": "Same Name"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    # Only the CREATE row; no UPDATE row.
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"


async def _create_active_store(
    app_client: TestClient,
    super_admin_jwt: str,
    tenant_id: UUID,
    parent_id: UUID,
) -> UUID:
    """Create a store and return its id. Initial status is ACTIVE
    (the DDL default in v0 per Step 6.17.3 LD8 / FN-AB-51).
    """
    code = f"SS-{uuid.uuid4().hex[:6]}"
    resp = app_client.post(
        "/api/v1/stores",
        json=_valid_create_body(
            tenant_id=tenant_id, parent_org_node_id=parent_id,
            name=f"SS Store {code}", store_code=code,
        ),
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201, resp.text
    return UUID(resp.json()["id"])


async def test_ss_set_status_active_to_closed_emits_close_action(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_stores_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — set-status target=CLOSED dispatches action='CLOSE'
    with label='Closed' (LD3).
    """
    tenant = await make_tenant(name="SSc-Tenant", with_root=True)
    parent_id = await _ensure_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    store_id = await _create_active_store(
        app_client, super_admin_jwt, tenant.id, parent_id
    )
    cleanup_stores_audit.append(store_id)

    resp = app_client.post(
        f"/api/v1/stores/{store_id}/set-status",
        json={"target_status": "CLOSED", "reason": "End of life"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    # CREATE then set-status row.
    set_row = rows[-1]
    assert set_row["action"] == "CLOSE"
    assert set_row["action_label"] == "Closed"
    assert set_row["details"]["before"]["status"] == "ACTIVE"
    assert set_row["details"]["after"]["status"] == "CLOSED"


async def test_ss_set_status_active_to_inactive_emits_deactivate(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_stores_audit,
    session_factory,
    platform_auth,
) -> None:
    """set-status target=INACTIVE dispatches action='DEACTIVATE'."""
    tenant = await make_tenant(name="SSi-Tenant", with_root=True)
    parent_id = await _ensure_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    store_id = await _create_active_store(
        app_client, super_admin_jwt, tenant.id, parent_id
    )
    cleanup_stores_audit.append(store_id)

    resp = app_client.post(
        f"/api/v1/stores/{store_id}/set-status",
        json={"target_status": "INACTIVE"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    set_row = rows[-1]
    assert set_row["action"] == "DEACTIVATE"
    assert set_row["action_label"] == "Deactivated"


async def test_ss_set_status_closed_to_active_emits_activate(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_stores_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — out-of-CLOSED dispatches action='ACTIVATE'."""
    tenant = await make_tenant(name="SSa-Tenant", with_root=True)
    parent_id = await _ensure_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    store_id = await _create_active_store(
        app_client, super_admin_jwt, tenant.id, parent_id
    )
    cleanup_stores_audit.append(store_id)

    # First transition to CLOSED.
    resp = app_client.post(
        f"/api/v1/stores/{store_id}/set-status",
        json={"target_status": "CLOSED"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    # Then re-open.
    resp = app_client.post(
        f"/api/v1/stores/{store_id}/set-status",
        json={"target_status": "ACTIVE"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    # CREATE, CLOSE, ACTIVATE.
    assert len(rows) >= 3
    assert rows[-1]["action"] == "ACTIVATE"
    assert rows[-1]["action_label"] == "Activated"
    assert rows[-1]["details"]["before"]["status"] == "CLOSED"
    assert rows[-1]["details"]["after"]["status"] == "ACTIVE"


async def test_ss_atomic_post_emits_only_one_audit_row(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_stores_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — LD14 invariant: atomic-pair POST emits EXACTLY 1
    audit row (the stores CREATE row), not 2 (no separate ORG_NODE
    CREATE row).
    """
    tenant = await make_tenant(name="SS-Single-Tenant", with_root=True)
    parent_id = await _ensure_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    resp = app_client.post(
        "/api/v1/stores",
        json=_valid_create_body(
            tenant_id=tenant.id, parent_org_node_id=parent_id,
            name="Single Row Store",
            store_code=f"SR-{uuid.uuid4().hex[:6]}",
        ),
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201
    cleanup_stores_audit.append(UUID(resp.json()["id"]))

    # Count rows across BOTH resource types for this tenant.
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"""
                SELECT resource_type, COUNT(*) AS n
                  FROM {schema}.tenant_activity_audit_logs
                 WHERE tenant_id = :tid
                 GROUP BY resource_type
                """
            ),
            {"tid": tenant.id},
        )
        counts = {r.resource_type: int(r.n) for r in result.all()}
    # 1 STORE row, 0 ORG_NODE rows (the paired org_node was created
    # via the repo's cascade but not separately audited per LD14).
    assert counts.get("STORE", 0) == 1
    assert counts.get("ORG_NODE", 0) == 0


async def test_ss_audit_row_request_id_matches_response_header(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_stores_audit,
    session_factory,
    platform_auth,
) -> None:
    tenant = await make_tenant(name="SSrid-Tenant", with_root=True)
    parent_id = await _ensure_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    resp = app_client.post(
        "/api/v1/stores",
        json=_valid_create_body(
            tenant_id=tenant.id, parent_org_node_id=parent_id,
            name="RID Store", store_code=f"RID-{uuid.uuid4().hex[:6]}",
        ),
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201
    cleanup_stores_audit.append(UUID(resp.json()["id"]))
    header_request_id = resp.headers.get("X-Request-Id")
    assert header_request_id is not None
    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert str(rows[0]["request_id"]) == header_request_id


# ============================================================================
# Failure-path
# ============================================================================


async def test_sf_post_with_duplicate_store_code_emits_conflict(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_stores_audit,
    cleanup_orphan_platform_audit_stores,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — duplicate store_code 409 produces CONFLICT row.

    POST /stores's failure path routes to the platform audit table
    with tenant_id=NULL per LD10 (the body containing tenant_id is
    consumed by FastAPI before the failure handler runs; the failure
    handler has no path-bound tenant_id to populate the tenant table).
    Mirrors the POST /tenant-users + POST /tenants failure-row
    posture from 6.16.2 / 6.16.4.
    """
    tenant = await make_tenant(name="SF-Dup-Tenant", with_root=True)
    parent_id = await _ensure_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    dup_code = f"SFD-{uuid.uuid4().hex[:6]}"
    resp = app_client.post(
        "/api/v1/stores",
        json=_valid_create_body(
            tenant_id=tenant.id, parent_org_node_id=parent_id,
            name="First", store_code=dup_code,
        ),
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201
    cleanup_stores_audit.append(UUID(resp.json()["id"]))
    first_request_id = resp.headers["X-Request-Id"]

    resp = app_client.post(
        "/api/v1/stores",
        json=_valid_create_body(
            tenant_id=tenant.id, parent_org_node_id=parent_id,
            name="Collider", store_code=dup_code,
        ),
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "DUPLICATE_STORE_CODE"
    fail_request_id = resp.headers["X-Request-Id"]
    cleanup_orphan_platform_audit_stores.append(fail_request_id)

    # SUCCESS row lives on the tenant table (tenant_id populated by
    # the success path); CONFLICT row lives on the platform table
    # (POST /stores failure path; tenant_id is NULL because the body
    # was consumed).
    rows_tenant = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    success = [
        r for r in rows_tenant if r["result_type"] == "SUCCESS"
    ]
    assert len(success) == 1
    assert str(success[0]["request_id"]) == first_request_id

    # Find the CONFLICT row on the platform table by request_id.
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"""
                SELECT result_type::text AS result_type, action,
                       resource_type
                  FROM {schema}.platform_activity_audit_logs
                 WHERE request_id = :rid
                """
            ),
            {"rid": fail_request_id},
        )
        platform_rows = [dict(r) for r in result.mappings()]
    assert len(platform_rows) == 1
    assert platform_rows[0]["result_type"] == "CONFLICT"
    assert platform_rows[0]["action"] == "CREATE"
    assert platform_rows[0]["resource_type"] == "STORE"


async def test_sf_patch_empty_body_emits_validation_failed(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_stores_audit,
    session_factory,
    platform_auth,
) -> None:
    """PATCH with no fields -> 422 EMPTY_PATCH -> VALIDATION_FAILED row."""
    tenant = await make_tenant(name="SF-Empty-Tenant", with_root=True)
    parent_id = await _ensure_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    store_id = await _create_active_store(
        app_client, super_admin_jwt, tenant.id, parent_id
    )
    cleanup_stores_audit.append(store_id)

    resp = app_client.patch(
        f"/api/v1/stores/{store_id}",
        json={},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    failure = [r for r in rows if r["result_type"] == "VALIDATION_FAILED"]
    # EmptyPatchError flows through AdminBackendError -> audit emits.
    assert len(failure) == 1
    assert failure[0]["action"] == "UPDATE"


async def test_sf_patch_on_nonexistent_id_emits_zero_audit_rows(
    app_client,
    super_admin_jwt,
    make_tenant,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — LD13 anchor-404: PATCH on missing store_id ->
    404 -> no audit row.
    """
    tenant = await make_tenant(name="SF-Anchor-Tenant", with_root=True)
    nonexistent = uuid.uuid4()
    resp = app_client.patch(
        f"/api/v1/stores/{nonexistent}",
        json={"name": "Anything"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert rows == []


async def test_sf_set_status_invalid_transition_emits_conflict(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_stores_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — invalid transition (e.g. ACTIVE -> OPENING; not in
    TRANSITION_MATRIX) -> 409 INVALID_STATE_TRANSITION -> CONFLICT row.
    Failure-path uses the AUDITED_ROUTES fallback action ``SET_STATUS``.
    """
    tenant = await make_tenant(name="SF-Trans-Tenant", with_root=True)
    parent_id = await _ensure_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    store_id = await _create_active_store(
        app_client, super_admin_jwt, tenant.id, parent_id
    )
    cleanup_stores_audit.append(store_id)

    # ACTIVE -> OPENING is rejected (no cell in TRANSITION_MATRIX).
    resp = app_client.post(
        f"/api/v1/stores/{store_id}/set-status",
        json={"target_status": "OPENING"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "INVALID_STATE_TRANSITION"

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    conflict = [r for r in rows if r["result_type"] == "CONFLICT"]
    assert len(conflict) == 1
    assert conflict[0]["action"] == "SET_STATUS"
    # Step 6.16.7 LD8 : SET_STATUS label changed "Status change" -> "Set status".
    assert conflict[0]["action_label"] == "Set status"


async def test_sf_set_status_on_nonexistent_id_emits_zero_audit_rows(
    app_client,
    super_admin_jwt,
    make_tenant,
    session_factory,
    platform_auth,
) -> None:
    """LD13 anchor-404: set-status on missing store_id -> 404 -> no row.
    """
    tenant = await make_tenant(name="SF-Set-Anchor-Tenant", with_root=True)
    nonexistent = uuid.uuid4()
    resp = app_client.post(
        f"/api/v1/stores/{nonexistent}/set-status",
        json={"target_status": "ACTIVE"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404, resp.text
    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert rows == []


async def test_sf_patch_with_tenant_jwt_no_grant_emits_permission_denied(
    app_client,
    settings,
    make_tenant,
    cleanup_stores_audit,
    super_admin_jwt,
    session_factory,
    platform_auth,
) -> None:
    """TENANT JWT without grant on PATCH -> 403 PERMISSION_DENIED row."""
    tenant = await make_tenant(name="SF-Perm-Tenant", with_root=True)
    parent_id = await _ensure_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    store_id = await _create_active_store(
        app_client, super_admin_jwt, tenant.id, parent_id
    )
    cleanup_stores_audit.append(store_id)

    jwt = _tenant_jwt(settings, tenant.id)
    resp = app_client.patch(
        f"/api/v1/stores/{store_id}",
        json={"name": "Renamed"},
        headers=_auth(jwt),
    )
    assert resp.status_code == 403, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    permission_denied = [
        r for r in rows if r["result_type"] == "PERMISSION_DENIED"
    ]
    assert len(permission_denied) == 1
    assert permission_denied[0]["action"] == "UPDATE"
    assert permission_denied[0]["details"]["caller_audience"] == "TENANT"
