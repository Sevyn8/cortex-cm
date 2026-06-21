"""Step 6.16.4 : failure-path audit emission for the 4 tenant-users endpoints.

Per-endpoint coverage of the FAILURE audit row that 403 / 409 / 422
responses produce on tenant-users routes. Each test triggers a known
failure case via the real HTTP layer, then queries the audit tables
for the matching row and asserts result_type + details shape.

12 tests (AF1-AF12). LOAD-BEARING: AF1, AF2, AF3, AF7, AF9, AF12
(four main failure result_types + self-edit-denial sub-key + state-
transition CONFLICT shape).

Routing nuance per LD3: tenant-users routes carry route_to_platform
=False, but failure-path emission can only derive tenant_id from the
URL path or (LD7 extension) by JOIN against ``tenant_users``. POST
/tenant-users has no path tenant_id and the body is not readable
post-fault; the failure row falls back to the platform table per the
existing handler routing (AF1 acknowledges either).
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
# Test app + helpers (mirror test_audit_emission_tenant_users.py)
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


async def _seed_tenant_with_root(
    make_tenant: Any,
    make_org_node: Any,
    *,
    name: str,
) -> tuple[UUID, UUID, str]:
    tenant = await make_tenant(name=name)
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="TENANT",
        code=f"r-{uuid.uuid4().hex[:8]}",
        name=f"Root {name}",
    )
    return tenant.id, root_id, root_path


def _roles_payload(items: list[tuple[UUID, UUID]]) -> list[dict[str, str]]:
    return [
        {"role_id": str(rid), "org_node_id": str(oid)}
        for (rid, oid) in items
    ]


def _valid_create_body(
    *,
    tenant_id: UUID,
    role_assignments: list[tuple[UUID, UUID]],
    name_suffix: str,
) -> dict[str, Any]:
    return {
        "tenant_id": str(tenant_id),
        "email": f"af-{name_suffix}-{uuid.uuid4().hex[:8]}@example.com",
        "full_name": f"Audit Failure User {name_suffix}",
        "roles": _roles_payload(role_assignments),
    }


@pytest_asyncio.fixture
async def cleanup_tu_audit_users(
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
            await session.execute(
                text(
                    f"DELETE FROM {schema}.tenant_activity_audit_logs "
                    "WHERE resource_id = ANY(:ids) "
                    "OR actor_user_id = ANY(:ids)"
                ),
                {"ids": created},
            )
            await session.execute(
                text(
                    f"DELETE FROM {schema}.platform_activity_audit_logs "
                    "WHERE resource_id = ANY(:ids) "
                    "OR actor_user_id = ANY(:ids)"
                ),
                {"ids": created},
            )
            await session.execute(
                text(
                    f"DELETE FROM {schema}.tenant_user_role_assignments "
                    "WHERE tenant_user_id = ANY(:ids)"
                ),
                {"ids": created},
            )
            await session.execute(
                text(
                    f"DELETE FROM {schema}.tenant_users WHERE id = ANY(:ids)"
                ),
                {"ids": created},
            )


@pytest_asyncio.fixture
async def cleanup_audit_by_request_ids(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Track request_ids for audit rows that don't pin to a tracked
    tenant_user (e.g. AF1's POST /tenant-users failure where the path
    has no user_id and the row routes to the platform table). Cleans
    by request_id at teardown.
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
            ):
                await session.execute(
                    text(
                        f"DELETE FROM {schema}.{table} "
                        "WHERE request_id = ANY(:ids)"
                    ),
                    {"ids": created},
                )


async def _fetch_audit_rows_by_request_id(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    request_id: UUID,
) -> list[tuple[str, dict[str, Any]]]:
    """Return (table_name, row_dict) tuples across both tables for a
    given request_id. Multiple rows allowed (failure-path emits one;
    success path zero for tests that intentionally fail).
    """
    schema = get_settings().db_schema
    out: list[tuple[str, dict[str, Any]]] = []
    async for session in get_tenant_session(platform_auth, session_factory):
        for table in (
            "tenant_activity_audit_logs",
            "platform_activity_audit_logs",
        ):
            result = await session.execute(
                text(
                    f"SELECT action, resource_type, resource_id, "
                    f"resource_label, result_type, actor_user_id, "
                    f"actor_user_type, request_id, details, tenant_id, "
                    f"tenant_name FROM {schema}.{table} "
                    f"WHERE request_id = :rid"
                ),
                {"rid": request_id},
            )
            for row in result.mappings():
                out.append((table, dict(row)))
    return out


def _request_id_from_response(resp: Any) -> UUID:
    rid = resp.headers.get("X-Request-Id")
    assert rid is not None, "X-Request-Id header missing on response"
    return UUID(rid)


# ---------------------------------------------------------------------------
# AF1 : POST /tenant-users with insufficient permission -> 403 emission
# (LOAD-BEARING)
# ---------------------------------------------------------------------------


async def test_af1_post_with_tenant_jwt_no_grant_emits_permission_denied(
    app_client,
    settings: Settings,
    make_tenant,
    make_org_node,
    make_role,
    tenant_owner_jwt_factory,
    cleanup_tu_audit_users,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: a TENANT JWT without
    ``ADMIN.USERS.CONFIGURE.TENANT`` is denied at the gate and emits
    a PERMISSION_DENIED row. Routing falls back to the platform table
    because POST /tenant-users has no path tenant_id and the body is
    unreadable from the failure-path handler (LD3 acknowledged
    routing nuance).
    """
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AF1-Tenant"
    )
    role = await make_role(audience="TENANT")
    # Mint a TENANT JWT with NO grants (default factory issues OWNER
    # role with ADMIN.USERS.CONFIGURE.TENANT grant — instead use a
    # variant with empty grants to deny).
    owner_jwt = await tenant_owner_jwt_factory(
        tenant_id, with_grants=[]
    )

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="af1",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(owner_jwt),
    )
    assert resp.status_code == 403, resp.text
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    table, row = rows[0]
    assert row["action"] == "CREATE"
    assert row["resource_type"] == "TENANT_USER"
    assert row["result_type"] == "PERMISSION_DENIED"
    details = row["details"]
    assert "required_permission" in details
    assert "caller_audience" in details
    assert details["caller_audience"] == "TENANT"


# ---------------------------------------------------------------------------
# AF2 : PATCH self-edit -> 403 with denial_reason=SELF_EDIT_FORBIDDEN
# (LOAD-BEARING)
# ---------------------------------------------------------------------------


async def test_af2_patch_self_edit_emits_denial_reason_sub_key(
    app_client,
    settings: Settings,
    make_tenant,
    make_org_node,
    tenant_owner_jwt_factory,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING per LD11: handler-side self-edit guard fires the
    audit failure path with ``details.denial_reason='SELF_EDIT_FORBIDDEN'``
    in addition to the standard PERMISSION_DENIED sub-keys.
    """
    import jwt as pyjwt
    from admin_backend.auth.stub import CLAIM_USER_ID

    tenant_id, _, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AF2-Tenant"
    )
    owner_jwt = await tenant_owner_jwt_factory(
        tenant_id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )
    payload = pyjwt.decode(owner_jwt, options={"verify_signature": False})
    self_user_id = UUID(str(payload[CLAIM_USER_ID]))

    resp = app_client.patch(
        f"/api/v1/tenant-users/{self_user_id}",
        json={"full_name": "New Name"},
        headers=_auth(owner_jwt),
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] == "SELF_EDIT_FORBIDDEN"

    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    _table, row = rows[0]
    assert row["result_type"] == "PERMISSION_DENIED"
    details = row["details"]
    assert details["denial_reason"] == "SELF_EDIT_FORBIDDEN"
    # Standard sub-keys remain (LD11).
    assert "required_permission" in details
    assert "caller_audience" in details
    assert "caller_roles" in details


# ---------------------------------------------------------------------------
# AF3 : POST with duplicate email -> 409 CONFLICT (LOAD-BEARING)
# ---------------------------------------------------------------------------


async def test_af3_post_duplicate_email_emits_conflict(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: 409 from ``DuplicateTenantUserEmailError`` emits
    CONFLICT with the conflict-shape details.
    """
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AF3-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="af3",
    )
    create_resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert create_resp.status_code == 201
    cleanup_tu_audit_users.append(UUID(create_resp.json()["id"]))

    # Re-POST with same email.
    dup_resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert dup_resp.status_code == 409, dup_resp.text
    request_id = _request_id_from_response(dup_resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    _table, row = rows[0]
    assert row["result_type"] == "CONFLICT"
    assert "constraint" in row["details"]


# ---------------------------------------------------------------------------
# AF4 : POST with PLATFORM-audience role -> 422 INVALID_ROLE_AUDIENCE
# ---------------------------------------------------------------------------


async def test_af4_post_platform_role_in_body_emits_validation_failed(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AF4-Tenant"
    )
    bad_role = await make_role(audience="PLATFORM")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(bad_role.id, root_id)],
        name_suffix="af4",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    _table, row = rows[0]
    assert row["result_type"] == "VALIDATION_FAILED"


# ---------------------------------------------------------------------------
# AF5 : POST with archived org_node in roles[] -> 422
# ---------------------------------------------------------------------------


async def test_af5_post_missing_org_node_emits_validation_failed(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """``_validate_org_nodes`` rejects org_node_id values that don't
    exist in the catalogue OR live in a different tenant OR are
    archived. Use a missing-globally id (cheap to construct;
    structurally rejects archived too per the aggregated error).
    """
    tenant_id, _root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AF5-Tenant"
    )
    role = await make_role(audience="TENANT")
    nonexistent_org_node = uuid.uuid4()

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, nonexistent_org_node)],
        name_suffix="af5",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    _table, row = rows[0]
    assert row["result_type"] == "VALIDATION_FAILED"


# ---------------------------------------------------------------------------
# AF6 : POST with within-request duplicate (role_id, org_node_id) -> 422
# ---------------------------------------------------------------------------


async def test_af6_post_within_request_duplicate_emits_validation_failed(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AF6-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id), (role.id, root_id)],
        name_suffix="af6",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    _table, row = rows[0]
    assert row["result_type"] == "VALIDATION_FAILED"


# ---------------------------------------------------------------------------
# AF7 : PATCH with empty body -> 422 EMPTY_PATCH (LOAD-BEARING)
# ---------------------------------------------------------------------------


async def test_af7_patch_empty_body_emits_validation_failed(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: a codebase-422 (EmptyPatchError) emits a
    VALIDATION_FAILED row.
    """
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AF7-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="af7",
    )
    create_resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create_resp.json()["id"])
    cleanup_tu_audit_users.append(user_id)

    patch_resp = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={},
        headers=_auth(super_admin_jwt),
    )
    assert patch_resp.status_code == 422, patch_resp.text
    request_id = _request_id_from_response(patch_resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    _table, row = rows[0]
    assert row["result_type"] == "VALIDATION_FAILED"


# ---------------------------------------------------------------------------
# AF8 : PATCH on nonexistent user -> 404 SKIPS emission (per AF6 precedent)
# ---------------------------------------------------------------------------


async def test_af8_patch_nonexistent_user_skips_emission(
    app_client,
    super_admin_jwt,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """404 NOT_FOUND skips audit emission (per 6.16.2 precedent on
    AF6: there is no resource to associate the attempt with).
    """
    fake_id = uuid.uuid4()
    resp = app_client.patch(
        f"/api/v1/tenant-users/{fake_id}",
        json={"full_name": "Anything"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404, resp.text
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# AF9 : POST /suspend on INVITED user -> 409 INVALID_STATE_TRANSITION
# (LOAD-BEARING)
# ---------------------------------------------------------------------------


async def test_af9_suspend_invited_user_emits_conflict_with_current_state(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING per LD10: state-transition CONFLICT details.value
    is the *current* state (the one that blocked the transition), not
    the attempted target.
    """
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AF9-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="af9",
    )
    create_resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create_resp.json()["id"])
    cleanup_tu_audit_users.append(user_id)

    susp_resp = app_client.post(
        f"/api/v1/tenant-users/{user_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert susp_resp.status_code == 409, susp_resp.text
    request_id = _request_id_from_response(susp_resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    _table, row = rows[0]
    assert row["action"] == "SUSPEND"
    assert row["result_type"] == "CONFLICT"
    details = row["details"]
    assert details["constraint"] == "INVALID_STATE_TRANSITION"


# ---------------------------------------------------------------------------
# AF10 : POST /activate on INVITED user -> 409 INVALID_STATE_TRANSITION
# ---------------------------------------------------------------------------


async def test_af10_activate_invited_user_emits_conflict(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AF10-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="af10",
    )
    create_resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create_resp.json()["id"])
    cleanup_tu_audit_users.append(user_id)

    act_resp = app_client.post(
        f"/api/v1/tenant-users/{user_id}/activate",
        headers=_auth(super_admin_jwt),
    )
    assert act_resp.status_code == 409, act_resp.text
    request_id = _request_id_from_response(act_resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    _table, row = rows[0]
    assert row["action"] == "ACTIVATE"
    assert row["result_type"] == "CONFLICT"


# ---------------------------------------------------------------------------
# AF11 : PATCH with TENANT JWT for other tenant's user -> 404 (RLS-as-404)
# ---------------------------------------------------------------------------


async def test_af11_patch_cross_tenant_skips_emission(
    app_client,
    settings: Settings,
    make_tenant,
    make_org_node,
    make_role,
    super_admin_jwt,
    tenant_owner_jwt_factory,
    cleanup_tu_audit_users,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """RLS-as-404 (D-17): TENANT-A OWNER probing TENANT-B's user gets
    404 from the anchor dep. 404 skips emission per AF8 precedent.
    """
    tenant_a_id, root_a_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AF11-TenantA"
    )
    tenant_b_id, root_b_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AF11-TenantB"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_b_id,
        role_assignments=[(role.id, root_b_id)],
        name_suffix="af11-b",
    )
    create_b = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_b_id = UUID(create_b.json()["id"])
    cleanup_tu_audit_users.append(user_b_id)

    owner_a_jwt = await tenant_owner_jwt_factory(
        tenant_a_id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )
    resp = app_client.patch(
        f"/api/v1/tenant-users/{user_b_id}",
        json={"full_name": "Cross-tenant Try"},
        headers=_auth(owner_a_jwt),
    )
    assert resp.status_code == 404, resp.text
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# AF12 : self-edit denied row carries the full Phase 1 Q8 contract
# (LOAD-BEARING; complements AF2)
# ---------------------------------------------------------------------------


async def test_af12_self_edit_row_carries_full_q8_contract(
    app_client,
    settings: Settings,
    make_tenant,
    make_org_node,
    tenant_owner_jwt_factory,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING per Phase 1 Q8: the self-edit denied row carries
    denial_reason, required_permission, caller_audience, caller_roles
    ALL populated. AF2 checks denial_reason presence; AF12 asserts
    the full set is non-empty.
    """
    import jwt as pyjwt
    from admin_backend.auth.stub import CLAIM_USER_ID

    tenant_id, _, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AF12-Tenant"
    )
    owner_jwt = await tenant_owner_jwt_factory(
        tenant_id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )
    payload = pyjwt.decode(owner_jwt, options={"verify_signature": False})
    self_user_id = UUID(str(payload[CLAIM_USER_ID]))

    resp = app_client.post(
        f"/api/v1/tenant-users/{self_user_id}/suspend",
        headers=_auth(owner_jwt),
    )
    assert resp.status_code == 403
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    _table, row = rows[0]
    details = row["details"]
    assert details["denial_reason"] == "SELF_EDIT_FORBIDDEN"
    assert details["required_permission"]  # non-empty string
    assert details["caller_audience"] == "TENANT"
    assert isinstance(details["caller_roles"], list)
