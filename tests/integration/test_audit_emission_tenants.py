"""Step 6.16.2 : success-path audit emission for the 4 tenant endpoints.

Per-endpoint coverage of the SUCCESS audit row that POST / PATCH /
suspend / activate produce. Each test does the data write through
the real HTTP layer, then queries the audit table for the matching
row and asserts shape.

The named exception for routing (LD3): POST /tenants's success row
goes to `platform_activity_audit_logs` even though `tenant_id` is
populated. Every other success row goes to
`tenant_activity_audit_logs`. AS1-AS2 verify both sides of the
exception end-to-end.

10 tests (AS1-AS10). LOAD-BEARING: AS1, AS3, AS5, AS6 (per-endpoint
success contract drives every consumer of the audit subsystem).

Cleanup. The local `cleanup_tenants_for_audit` fixture mirrors
`cleanup_tenants_router` in `test_tenants_writes_router.py`, with the
audit-row DELETE that 6.16.2 added globally to that file: every
audit row referencing the test tenant is cleared before the tenant
DELETE (FK ON DELETE RESTRICT).
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
from admin_backend.config import Settings, get_settings
from admin_backend.db.session import get_tenant_session
from admin_backend.main import create_app


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test app + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,
    session_factory: Any,
) -> Iterator[TestClient]:
    """TestClient with engine + session_factory wired onto app.state."""
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


def _valid_create_body(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "region": "US",
        "tier": "ENTERPRISE",
        "industry": "GROCERY",
        "country": "United States",
        "primary_contact_name": "Alice Operator",
        "contact_email": f"op-{uuid.uuid4().hex[:8]}@test.example.com",
        "number_of_stores": 5,
        "number_of_stores_as_of_date": "2026-01-01",
    }


@pytest_asyncio.fixture
async def cleanup_tenants_for_audit(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Tracks tenant IDs created during the test; DELETEs at teardown.

    Audit rows pin the tenant via FK ON DELETE RESTRICT; clear both
    audit tables before tenant_module_access, org_nodes, and tenants.
    """
    schema = get_settings().db_schema
    created: list[UUID] = []
    yield created

    if created:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            for table in (
                "tenant_activity_audit_logs",
                "platform_activity_audit_logs",
                "tenant_module_access",
                "org_nodes",
            ):
                await session.execute(
                    text(
                        f"DELETE FROM {schema}.{table} "
                        "WHERE tenant_id = ANY(:ids)"
                    ),
                    {"ids": created},
                )
            await session.execute(
                text(f"DELETE FROM {schema}.tenants WHERE id = ANY(:ids)"),
                {"ids": created},
            )


async def _count_audit_rows(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    table: str,
    tenant_id: UUID,
) -> int:
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"SELECT COUNT(*) FROM {schema}.{table} "
                "WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": tenant_id},
        )
        return int(result.scalar_one())
    raise AssertionError("unreachable")  # pragma: no cover


async def _fetch_audit_rows(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    table: str,
    tenant_id: UUID,
) -> list[dict[str, Any]]:
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"SELECT id, action, resource_type, resource_id, result_type, "
                f"actor_user_id, actor_user_type, actor_display_name, "
                f"request_id, details, tenant_id, tenant_name "
                f"FROM {schema}.{table} "
                "WHERE tenant_id = :tenant_id "
                "ORDER BY timestamp ASC, id ASC"
            ),
            {"tenant_id": tenant_id},
        )
        return [dict(row) for row in result.mappings()]
    raise AssertionError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# AS1 / AS2 : POST /tenants success -> platform_activity_audit_logs
# ---------------------------------------------------------------------------


async def test_as1_post_tenants_success_emits_to_platform_table(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: POST /tenants routes to platform_activity_audit_logs.

    The design-doc-named exception (`route_to_platform=True`) makes
    tenant-creation a platform-scope event. The success row carries
    `tenant_id` populated (the just-created tenant) and `action='CREATE'`.
    """
    body = _valid_create_body("AS1-AcmeCo")
    resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 201, resp.text
    tenant_id = UUID(resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="platform_activity_audit_logs",
        tenant_id=tenant_id,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "CREATE"
    assert row["resource_type"] == "TENANT"
    assert UUID(str(row["resource_id"])) == tenant_id
    assert row["result_type"] == "SUCCESS"
    assert row["actor_user_type"] == "PLATFORM"
    assert row["tenant_name"] == "AS1-AcmeCo"


async def test_as2_post_tenants_success_does_not_emit_to_tenant_table(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """End-to-end verification of the named-exception routing.

    Zero rows on `tenant_activity_audit_logs` for the just-created
    tenant; the POST emits only to `platform_activity_audit_logs`.
    """
    body = _valid_create_body("AS2-RoutingProbe")
    resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 201, resp.text
    tenant_id = UUID(resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    count = await _count_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant_id,
    )
    assert count == 0


# ---------------------------------------------------------------------------
# AS3 / AS4 : PATCH /tenants success -> tenant_activity_audit_logs
# ---------------------------------------------------------------------------


async def test_as3_patch_tenants_success_emits_to_tenant_table(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: PATCH success row lands in tenant_activity_audit_logs.

    Normal routing (tenant_id set, route_to_platform=False). Action
    is 'UPDATE'; resource_id matches the path tenant_id.
    """
    body = _valid_create_body("AS3-PatchMe")
    create_resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    assert create_resp.status_code == 201
    tenant_id = UUID(create_resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    patch_resp = app_client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"contact_email": "patched@test.example.com"},
        headers=_auth(super_admin_jwt),
    )
    assert patch_resp.status_code == 200, patch_resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant_id,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "UPDATE"
    assert row["resource_type"] == "TENANT"
    assert UUID(str(row["resource_id"])) == tenant_id
    assert row["result_type"] == "SUCCESS"


async def test_as4_patch_success_does_not_emit_to_platform_table(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """PATCH does not leak a platform-table row.

    The platform-table count post-PATCH equals exactly the count from
    the prior POST (one row, the CREATE event), not two.
    """
    body = _valid_create_body("AS4-NoLeak")
    create_resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    tenant_id = UUID(create_resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    platform_count_after_create = await _count_audit_rows(
        session_factory,
        platform_auth,
        table="platform_activity_audit_logs",
        tenant_id=tenant_id,
    )
    assert platform_count_after_create == 1

    patch_resp = app_client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"contact_email": "x@test.example.com"},
        headers=_auth(super_admin_jwt),
    )
    assert patch_resp.status_code == 200

    platform_count_after_patch = await _count_audit_rows(
        session_factory,
        platform_auth,
        table="platform_activity_audit_logs",
        tenant_id=tenant_id,
    )
    assert platform_count_after_patch == 1  # unchanged


# ---------------------------------------------------------------------------
# AS5 : POST /tenants/{id}/suspend success
# ---------------------------------------------------------------------------


async def test_as5_suspend_success_emits_suspend_action_with_status_diff(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: SUSPEND success row carries before/after status diff."""
    body = _valid_create_body("AS5-Suspendable")
    create_resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    tenant_id = UUID(create_resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    susp_resp = app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert susp_resp.status_code == 200, susp_resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant_id,
    )
    suspend_rows = [r for r in rows if r["action"] == "SUSPEND"]
    assert len(suspend_rows) == 1
    row = suspend_rows[0]
    assert row["result_type"] == "SUCCESS"
    details = row["details"]
    assert details["before"] == {"status": "TRIAL"}
    assert details["after"] == {"status": "SUSPENDED"}


# ---------------------------------------------------------------------------
# AS6 : POST /tenants/{id}/activate success
# ---------------------------------------------------------------------------


async def test_as6_activate_success_emits_activate_action_with_status_diff(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: ACTIVATE success row carries SUSPENDED -> ACTIVE diff."""
    body = _valid_create_body("AS6-Activator")
    create_resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    tenant_id = UUID(create_resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    act_resp = app_client.post(
        f"/api/v1/tenants/{tenant_id}/activate",
        headers=_auth(super_admin_jwt),
    )
    assert act_resp.status_code == 200, act_resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant_id,
    )
    activate_rows = [r for r in rows if r["action"] == "ACTIVATE"]
    assert len(activate_rows) == 1
    row = activate_rows[0]
    assert row["result_type"] == "SUCCESS"
    details = row["details"]
    assert details["before"] == {"status": "SUSPENDED"}
    assert details["after"] == {"status": "ACTIVE"}


# ---------------------------------------------------------------------------
# AS7 : PATCH details payload contains diff of changed fields
# ---------------------------------------------------------------------------


async def test_as7_patch_details_payload_contains_before_after_diff(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """Details JSONB contains before / after sub-objects for the changed field."""
    body = _valid_create_body("AS7-Diff")
    create_resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    tenant_id = UUID(create_resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    new_email = "diff-target@test.example.com"
    patch_resp = app_client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"contact_email": new_email},
        headers=_auth(super_admin_jwt),
    )
    assert patch_resp.status_code == 200

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant_id,
    )
    patch_rows = [r for r in rows if r["action"] == "UPDATE"]
    assert len(patch_rows) == 1
    details = patch_rows[0]["details"]
    assert details["after"] == {"contact_email": new_email}
    assert details["before"] == {"contact_email": body["contact_email"]}


# ---------------------------------------------------------------------------
# AS8 : POST success row's tenant_name equals the created tenant's name
# ---------------------------------------------------------------------------


async def test_as8_post_success_tenant_name_is_denormalised_snapshot(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    body = _valid_create_body("AS8-NameSnapshot")
    resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    tenant_id = UUID(resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="platform_activity_audit_logs",
        tenant_id=tenant_id,
    )
    assert len(rows) == 1
    assert rows[0]["tenant_name"] == "AS8-NameSnapshot"


# ---------------------------------------------------------------------------
# AS9 : Multiple PATCHes produce one audit row per request
# ---------------------------------------------------------------------------


async def test_as9_multiple_patches_produce_one_row_each(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    body = _valid_create_body("AS9-Multi")
    create_resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    tenant_id = UUID(create_resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    for n in range(3):
        patch_resp = app_client.patch(
            f"/api/v1/tenants/{tenant_id}",
            json={"contact_email": f"v{n}@test.example.com"},
            headers=_auth(super_admin_jwt),
        )
        assert patch_resp.status_code == 200

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant_id,
    )
    patch_rows = [r for r in rows if r["action"] == "UPDATE"]
    assert len(patch_rows) == 3


# ---------------------------------------------------------------------------
# AS10 : Audit row request_id equals the X-Request-Id response header
# ---------------------------------------------------------------------------


async def test_as10_audit_row_request_id_matches_response_header(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """Correlation invariant: audit row's request_id == X-Request-Id header."""
    body = _valid_create_body("AS10-Correlation")
    resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 201
    tenant_id = UUID(resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)
    response_request_id = resp.headers.get("X-Request-Id")
    assert response_request_id is not None

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="platform_activity_audit_logs",
        tenant_id=tenant_id,
    )
    assert len(rows) == 1
    assert str(rows[0]["request_id"]) == response_request_id


# ---------------------------------------------------------------------------
# AS_N1 : Step 6.16.7 LD13 — actor enrichment populated on success path
# (LOAD-BEARING: the new NOT NULL columns must be populated on every
# emission; missing one is a silent data-integrity failure post-migration.)
# ---------------------------------------------------------------------------


async def _fetch_audit_rows_full(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    table: str,
    tenant_id: UUID,
) -> list[dict[str, Any]]:
    """Step 6.16.7 helper: SELECT * variant to project the new columns.

    Used by AS_N tests that assert on actor_organization_name,
    actor_roles, resource_subtype.
    """
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"SELECT * FROM {schema}.{table} "
                "WHERE tenant_id = :tenant_id "
                "ORDER BY timestamp ASC, id ASC"
            ),
            {"tenant_id": tenant_id},
        )
        return [dict(row) for row in result.mappings()]
    raise AssertionError("unreachable")  # pragma: no cover


async def test_as_n1_post_tenants_success_carries_actor_enrichment(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING (Step 6.16.7 LD13): tenant-creation success row
    carries ``actor_organization_name``, ``actor_roles``, and
    ``resource_subtype`` correctly. SUPER_ADMIN actor under
    ``super_admin_jwt`` is the seeded Anjali (PLATFORM, single active
    role "Super Admin").
    """
    body = _valid_create_body("ASN1-EnrichmentCo")
    resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    assert resp.status_code == 201, resp.text
    tenant_id = UUID(resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    rows = await _fetch_audit_rows_full(
        session_factory,
        platform_auth,
        table="platform_activity_audit_logs",
        tenant_id=tenant_id,
    )
    assert len(rows) == 1
    row = rows[0]
    # LD6 : PLATFORM actor -> literal organisation.
    assert row["actor_organization_name"] == "Platform-Ithina"
    # LD5 : roles.name display string, not roles.code.
    assert row["actor_roles"] == "Super Admin"
    # LD7 : non-ORG_NODE row -> resource_subtype NULL.
    assert row["resource_subtype"] is None
