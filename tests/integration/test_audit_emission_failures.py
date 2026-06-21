"""Step 6.16.2 : failure-path audit emission for the 4 tenant endpoints.

Each test produces a failure via the real HTTP layer and asserts the
audit row landed in the right table with the right `result_type` and
details payload.

11 tests (AF1-AF11). LOAD-BEARING: AF1, AF3, AF4, AF6, AF10.

Scope note: AF4-from-Pydantic (request body invalid per Pydantic
schema -> 422 from FastAPI's default RequestValidationError handler)
is NOT covered here. That path bypasses the codebase's
`@app.exception_handler(AdminBackendError)` and produces FastAPI's
default error envelope. Emitting audit for direct-Pydantic 422 is a
wire-contract change spanning all endpoints (not just audit); it is
deferred per FN-AB-63. The 422 path that IS covered here is the
codebase's own ClientError-shaped 422s (EmptyPatchError, etc) which
flow through the standard envelope.

Cleanup mirrors the success-path test file: clears audit rows before
tenant DELETEs (FK ON DELETE RESTRICT).

Each test fixes the test tenant up front (via POST /tenants with
super_admin) and tracks the tenant_id for cleanup. The CREATE itself
emits a SUCCESS audit row in `platform_activity_audit_logs`; that
row is included in audit-row count assertions ONLY where the test
specifies a particular query shape (e.g., result_type filter).
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


# ---------------------------------------------------------------------------
# Test app + helpers
# ---------------------------------------------------------------------------


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


def _tenant_jwt(settings: Settings, tenant_id: UUID) -> str:
    return make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="TENANT",
        tenant_id=tenant_id,
    )


@pytest_asyncio.fixture
async def cleanup_tenants_for_audit(
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
            # Also clear any platform audit rows the failure-path
            # emission may have produced WITHOUT tenant_id set (the
            # POST-failure cases). Track via a wide WHERE on request_id?
            # Cleaner: those rows have tenant_id=NULL and the test
            # function doesn't accumulate orphans across tests because
            # the test DB is shared with the seed-loader test that
            # TRUNCATEs the audit tables at session start. For
            # safety, the per-test cleanup below also targets the
            # platform-table rows linked to the tracked tenant_id.
            await session.execute(
                text(f"DELETE FROM {schema}.tenants WHERE id = ANY(:ids)"),
                {"ids": created},
            )


@pytest_asyncio.fixture
async def cleanup_orphan_platform_audit(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Track UUIDs of platform-table audit rows produced by tests where
    tenant_id is NULL (POST-failure cases). DELETEs them at teardown.

    Failure-path emission for POST /tenants writes to
    ``platform_activity_audit_logs`` with tenant_id IS NULL when the
    request never created a tenant. The standard cleanup (keyed on
    tenant_id) doesn't reach these rows; this fixture tracks them by
    their primary key.
    """
    schema = get_settings().db_schema
    tracked: list[UUID] = []
    yield tracked

    if tracked:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.platform_activity_audit_logs "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": tracked},
            )


async def _fetch_audit_rows(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    table: str,
    tenant_id: UUID | None,
    request_id: UUID | None = None,
) -> list[dict[str, Any]]:
    schema = get_settings().db_schema
    where_parts: list[str] = []
    params: dict[str, Any] = {}
    if tenant_id is not None:
        where_parts.append("tenant_id = :tenant_id")
        params["tenant_id"] = tenant_id
    if request_id is not None:
        where_parts.append("request_id = :request_id")
        params["request_id"] = request_id
    where_clause = (
        f"WHERE {' AND '.join(where_parts)}"
        if where_parts
        else ""
    )
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"SELECT id, action, action_label, resource_type, "
                f"resource_id, resource_label, resource_subtype, "
                f"result_type, result_label, actor_user_id, "
                f"actor_user_type, actor_display_name, "
                f"actor_organization_name, actor_roles, "
                f"request_id, details, tenant_id, tenant_name "
                f"FROM {schema}.{table} "
                f"{where_clause} "
                "ORDER BY timestamp ASC, id ASC"
            ),
            params,
        )
        return [dict(row) for row in result.mappings()]
    raise AssertionError("unreachable")  # pragma: no cover


async def _grant_platform_admin(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    user_id: UUID,
    assignment_tracker: list[tuple[UUID, UUID]],
) -> None:
    """Assign the seeded PLATFORM_ADMIN role to user_id, tracking the
    assignment for teardown."""
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        role_row = await session.execute(
            text(f"SELECT id FROM {schema}.roles WHERE code = 'PLATFORM_ADMIN'")
        )
        role_id = role_row.scalar_one()
        result = await session.execute(
            text(
                f"INSERT INTO {schema}.platform_user_role_assignments ("
                "  platform_user_id, role_id, status,"
                "  granted_by_user_id, granted_by_user_type"
                ") VALUES ("
                "  :user_id, :role_id,"
                f"  CAST('ACTIVE' AS {schema}.user_role_assignment_status_enum),"
                "  NULL, NULL"
                ") RETURNING id"
            ),
            {"user_id": user_id, "role_id": role_id},
        )
        assignment_id = UUID(str(result.scalar_one()))
        assignment_tracker.append((user_id, assignment_id))


@pytest_asyncio.fixture
async def cleanup_platform_admin_assignments(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[tuple[UUID, UUID]]]:
    """Tracks (user_id, assignment_id) tuples for PLATFORM_ADMIN role
    assignments created by tests; DELETEs at teardown."""
    schema = get_settings().db_schema
    tracked: list[tuple[UUID, UUID]] = []
    yield tracked

    if tracked:
        ids = [a for _, a in tracked]
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.platform_user_role_assignments "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": ids},
            )


def _platform_jwt_for_user(settings: Settings, user_id: UUID) -> str:
    return make_test_jwt(settings, user_id=user_id, user_type="PLATFORM")


# ---------------------------------------------------------------------------
# AF1 : POST /tenants with TENANT JWT -> 403 PLATFORM_AUDIENCE_REQUIRED
# ---------------------------------------------------------------------------


async def test_af1_post_tenants_tenant_jwt_emits_permission_denied(
    app_client,
    settings,
    cleanup_orphan_platform_audit,
    session_factory,
    platform_auth,
    make_tenant,
) -> None:
    """LOAD-BEARING: failure-path emission fires at all.

    A TENANT JWT POST /tenants hits Layer 1 audience refusal
    (403 PLATFORM_AUDIENCE_REQUIRED). The audit row lands in
    `platform_activity_audit_logs` (POST /tenants routes to platform
    table per the named exception, both success and failure).
    """
    tenant = await make_tenant(name="AF1-FixtureTenant")
    body = _valid_create_body("AF1-WillNotCreate")
    tjwt = _tenant_jwt(settings, tenant.id)
    resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(tjwt)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "PLATFORM_AUDIENCE_REQUIRED"
    request_id = UUID(resp.headers["X-Request-Id"])

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="platform_activity_audit_logs",
        tenant_id=None,
        request_id=request_id,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["result_type"] == "PERMISSION_DENIED"
    assert row["action"] == "CREATE"
    assert row["resource_type"] == "TENANT"
    assert row["resource_id"] is None  # no tenant was created
    assert row["tenant_id"] is None
    assert row["actor_user_type"] == "TENANT"
    cleanup_orphan_platform_audit.append(UUID(str(row["id"])))


# ---------------------------------------------------------------------------
# AF2 : POST /tenants with PLATFORM user lacking grants -> 403 PERMISSION_DENIED
# ---------------------------------------------------------------------------


async def test_af2_post_tenants_platform_no_grants_emits_permission_denied(
    app_client,
    settings,
    cleanup_orphan_platform_audit,
    session_factory,
    platform_auth,
    make_platform_user,
) -> None:
    """PLATFORM user with no role grants -> 403 PERMISSION_DENIED.

    Audience check passes (user_type=PLATFORM); has_permission denies
    because the user holds no grants. Emission fires.
    """
    pu = await make_platform_user(
        email=f"af2-{uuid.uuid4().hex[:8]}@ithina.ai",
        full_name="AF2 No-Grants",
        status="ACTIVE",
        auth0_sub=f"auth0|af2-{uuid.uuid4().hex[:8]}",
    )
    pjwt = _platform_jwt_for_user(settings, pu.id)
    body = _valid_create_body("AF2-NoGrants")
    resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(pjwt)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "PERMISSION_DENIED"
    request_id = UUID(resp.headers["X-Request-Id"])

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="platform_activity_audit_logs",
        tenant_id=None,
        request_id=request_id,
    )
    assert len(rows) == 1
    assert rows[0]["result_type"] == "PERMISSION_DENIED"
    cleanup_orphan_platform_audit.append(UUID(str(rows[0]["id"])))


# ---------------------------------------------------------------------------
# AF3 : POST /tenants with duplicate name -> 409 CONFLICT
# ---------------------------------------------------------------------------


async def test_af3_post_tenants_duplicate_name_emits_conflict(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    cleanup_orphan_platform_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: CONFLICT result_type emission.

    First POST succeeds; second with the same name returns 409
    DUPLICATE_TENANT_NAME. The failure row carries details with the
    constraint name.
    """
    name = f"AF3-Conflict-{uuid.uuid4().hex[:8]}"
    body = _valid_create_body(name)
    first = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    assert first.status_code == 201
    cleanup_tenants_for_audit.append(UUID(first.json()["id"]))

    body2 = _valid_create_body(name)
    second = app_client.post(
        "/api/v1/tenants", json=body2, headers=_auth(super_admin_jwt)
    )
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "DUPLICATE_TENANT_NAME"
    request_id = UUID(second.headers["X-Request-Id"])

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="platform_activity_audit_logs",
        tenant_id=None,
        request_id=request_id,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["result_type"] == "CONFLICT"
    details = row["details"]
    assert details["constraint"] == "DUPLICATE_TENANT_NAME"
    cleanup_orphan_platform_audit.append(UUID(str(row["id"])))


# ---------------------------------------------------------------------------
# AF4 : PATCH /tenants/{id} with empty body -> 422 VALIDATION_FAILED
# ---------------------------------------------------------------------------


async def test_af4_patch_empty_body_emits_validation_failed(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: VALIDATION_FAILED via the codebase's own 422 path.

    EmptyPatchError is a ClientError subclass with http_status=422;
    it flows through the standard envelope (unlike Pydantic's direct
    RequestValidationError which is deferred per FN-AB-63).
    """
    body = _valid_create_body("AF4-EmptyPatchTarget")
    create_resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    tenant_id = UUID(create_resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    patch_resp = app_client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={},
        headers=_auth(super_admin_jwt),
    )
    assert patch_resp.status_code == 422, patch_resp.text
    assert patch_resp.json()["code"] == "EMPTY_PATCH"
    request_id = UUID(patch_resp.headers["X-Request-Id"])

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant_id,
        request_id=request_id,
    )
    assert len(rows) == 1
    assert rows[0]["result_type"] == "VALIDATION_FAILED"


# ---------------------------------------------------------------------------
# AF5 : PATCH /tenants/{nonexistent_id} -> 404 -> NOT audited
# ---------------------------------------------------------------------------


async def test_af5_patch_nonexistent_tenant_does_not_emit(
    app_client,
    super_admin_jwt,
    session_factory,
    platform_auth,
) -> None:
    """Deliberate scope decision: 404 not audited (no resource to log)."""
    nonexistent = uuid.uuid4()
    patch_resp = app_client.patch(
        f"/api/v1/tenants/{nonexistent}",
        json={"contact_email": "x@test.example.com"},
        headers=_auth(super_admin_jwt),
    )
    assert patch_resp.status_code == 404
    request_id = UUID(patch_resp.headers["X-Request-Id"])

    # Zero rows on either table for this request_id.
    for table in (
        "tenant_activity_audit_logs",
        "platform_activity_audit_logs",
    ):
        rows = await _fetch_audit_rows(
            session_factory,
            platform_auth,
            table=table,
            tenant_id=None,
            request_id=request_id,
        )
        assert rows == [], f"unexpected audit row in {table}"


# ---------------------------------------------------------------------------
# AF6 : SUSPEND on already-SUSPENDED -> 409 INVALID_STATE_TRANSITION
# ---------------------------------------------------------------------------


async def test_af6_suspend_on_suspended_emits_conflict(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: state-transition CONFLICT.

    First /suspend succeeds (TRIAL -> SUSPENDED). Second /suspend
    is rejected (SUSPENDED -> SUSPENDED disallowed). The failure row
    lands with result_type=CONFLICT.
    """
    body = _valid_create_body("AF6-DoubleSuspend")
    create_resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    tenant_id = UUID(create_resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    first_susp = app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert first_susp.status_code == 200

    second_susp = app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert second_susp.status_code == 409, second_susp.text
    assert second_susp.json()["code"] == "INVALID_STATE_TRANSITION"
    request_id = UUID(second_susp.headers["X-Request-Id"])

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant_id,
        request_id=request_id,
    )
    assert len(rows) == 1
    assert rows[0]["result_type"] == "CONFLICT"
    assert rows[0]["details"]["constraint"] == "INVALID_STATE_TRANSITION"


# ---------------------------------------------------------------------------
# AF7 : ACTIVATE on already-ACTIVE -> 409 INVALID_STATE_TRANSITION
# ---------------------------------------------------------------------------


async def test_af7_activate_on_active_emits_conflict(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    body = _valid_create_body("AF7-DoubleActivate")
    create_resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    tenant_id = UUID(create_resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    first_act = app_client.post(
        f"/api/v1/tenants/{tenant_id}/activate",
        headers=_auth(super_admin_jwt),
    )
    assert first_act.status_code == 200  # TRIAL -> ACTIVE
    second_act = app_client.post(
        f"/api/v1/tenants/{tenant_id}/activate",
        headers=_auth(super_admin_jwt),
    )
    assert second_act.status_code == 409
    request_id = UUID(second_act.headers["X-Request-Id"])

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant_id,
        request_id=request_id,
    )
    assert len(rows) == 1
    assert rows[0]["result_type"] == "CONFLICT"


# ---------------------------------------------------------------------------
# AF8 : SUSPEND with PLATFORM_ADMIN -> 403 PERMISSION_DENIED
# ---------------------------------------------------------------------------


async def test_af8_suspend_platform_admin_no_override_emits_permission_denied(
    app_client,
    settings,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
    make_platform_user,
    cleanup_platform_admin_assignments,
) -> None:
    """PLATFORM_ADMIN holds CONFIGURE.GLOBAL but NOT OVERRIDE.GLOBAL.

    Layer 1 (audience PLATFORM) passes; Layer 2 (has_permission for
    OVERRIDE.GLOBAL) denies.
    """
    body = _valid_create_body("AF8-RequiresOverride")
    create_resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    tenant_id = UUID(create_resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    pa = await make_platform_user(
        email=f"af8-{uuid.uuid4().hex[:8]}@ithina.ai",
        full_name="AF8 Platform Admin",
        status="ACTIVE",
        auth0_sub=f"auth0|af8-{uuid.uuid4().hex[:8]}",
    )
    await _grant_platform_admin(
        session_factory,
        platform_auth,
        pa.id,
        cleanup_platform_admin_assignments,
    )
    pajwt = _platform_jwt_for_user(settings, pa.id)

    susp_resp = app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(pajwt),
    )
    assert susp_resp.status_code == 403, susp_resp.text
    assert susp_resp.json()["code"] == "PERMISSION_DENIED"
    request_id = UUID(susp_resp.headers["X-Request-Id"])

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant_id,
        request_id=request_id,
    )
    assert len(rows) == 1
    assert rows[0]["result_type"] == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# AF9 : PATCH with TENANT JWT -> 403 PLATFORM_AUDIENCE_REQUIRED
# ---------------------------------------------------------------------------


async def test_af9_patch_tenant_jwt_emits_permission_denied(
    app_client,
    settings,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    body = _valid_create_body("AF9-PatchByTenant")
    create_resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    tenant_id = UUID(create_resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    tjwt = _tenant_jwt(settings, tenant_id)
    patch_resp = app_client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"contact_email": "tenant@test.example.com"},
        headers=_auth(tjwt),
    )
    assert patch_resp.status_code == 403
    assert patch_resp.json()["code"] == "PLATFORM_AUDIENCE_REQUIRED"
    request_id = UUID(patch_resp.headers["X-Request-Id"])

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant_id,
        request_id=request_id,
    )
    assert len(rows) == 1
    assert rows[0]["result_type"] == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# AF10 : PERMISSION_DENIED details payload contract
# ---------------------------------------------------------------------------


async def test_af10_permission_denied_details_payload_contract(
    app_client,
    settings,
    cleanup_orphan_platform_audit,
    session_factory,
    platform_auth,
    make_tenant,
) -> None:
    """LOAD-BEARING: PERMISSION_DENIED row carries the required keys.

    Design doc payload shape:
      {required_permission, caller_audience, caller_roles}

    Verifies the keys are present and types are correct, not the
    exact values (which depend on which gate decision was taken;
    see _required_permission_from_code).
    """
    tenant = await make_tenant(name="AF10-FixtureTenant")
    body = _valid_create_body("AF10-Probe")
    tjwt = _tenant_jwt(settings, tenant.id)
    resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(tjwt)
    )
    assert resp.status_code == 403
    request_id = UUID(resp.headers["X-Request-Id"])

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="platform_activity_audit_logs",
        tenant_id=None,
        request_id=request_id,
    )
    assert len(rows) == 1
    details = rows[0]["details"]
    assert "required_permission" in details
    assert "caller_audience" in details
    assert "caller_roles" in details
    assert isinstance(details["required_permission"], str)
    assert isinstance(details["caller_roles"], list)
    cleanup_orphan_platform_audit.append(UUID(str(rows[0]["id"])))


# ---------------------------------------------------------------------------
# AF11 : Failure row's request_id correlates with X-Request-Id
# ---------------------------------------------------------------------------


async def test_af11_failure_row_request_id_matches_response_header(
    app_client,
    settings,
    cleanup_orphan_platform_audit,
    session_factory,
    platform_auth,
    make_tenant,
) -> None:
    tenant = await make_tenant(name="AF11-Correlation")
    body = _valid_create_body("AF11-Probe")
    tjwt = _tenant_jwt(settings, tenant.id)
    resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(tjwt)
    )
    assert resp.status_code == 403
    response_request_id = resp.headers["X-Request-Id"]

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="platform_activity_audit_logs",
        tenant_id=None,
        request_id=UUID(response_request_id),
    )
    assert len(rows) == 1
    assert str(rows[0]["request_id"]) == response_request_id
    cleanup_orphan_platform_audit.append(UUID(str(rows[0]["id"])))


# ---------------------------------------------------------------------------
# AF_N1 : Step 6.16.7 LD9 + LD13 — CONFLICT qualifier composition +
# actor enrichment on the failure path (LOAD-BEARING : composed
# result_label is the public wire-shape contract; the dispatch table
# coverage in AE_N6 is unit-level. AF_N1 verifies end-to-end through
# the failure handler.)
# ---------------------------------------------------------------------------


async def test_af_n1_conflict_failure_carries_composed_result_label_and_enrichment(
    app_client,
    super_admin_jwt,
    cleanup_tenants_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING (Step 6.16.7): a 409 INVALID_STATE_TRANSITION emits
    a CONFLICT failure row whose ``result_label`` is the LD9-composed
    "Blocked - status change not allowed" qualifier (not the static
    "Conflict" fallback), and whose actor enrichment columns reflect
    the SUPER_ADMIN actor under ``super_admin_jwt``.
    """
    body = _valid_create_body("AFN1-DoubleSuspend")
    create_resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt)
    )
    tenant_id = UUID(create_resp.json()["id"])
    cleanup_tenants_for_audit.append(tenant_id)

    first_susp = app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert first_susp.status_code == 200, first_susp.text

    second_susp = app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert second_susp.status_code == 409, second_susp.text
    assert second_susp.json()["code"] == "INVALID_STATE_TRANSITION"
    request_id = UUID(second_susp.headers["X-Request-Id"])

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant_id,
        request_id=request_id,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["result_type"] == "CONFLICT"
    # LD9 : result_label composed via _CONFLICT_QUALIFIERS dispatch.
    assert row["result_label"] == "Blocked - status change not allowed"
    # LD13 : actor enrichment populated on the failure path.
    assert row["actor_organization_name"] == "Platform-Ithina"
    assert row["actor_roles"] == "Super Admin"
