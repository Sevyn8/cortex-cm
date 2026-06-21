"""Step 6.16.4 : success-path audit emission for the 4 tenant-users endpoints.

Per-endpoint coverage of the SUCCESS audit row produced by
``POST / PATCH / suspend / activate`` on ``/api/v1/tenant-users``.
Each test does the data write through the real HTTP layer, then queries
the audit table for the matching row and asserts shape.

Routing per LD1: tenant-users routes carry ``route_to_platform=False``;
the SUCCESS rows land in ``tenant_activity_audit_logs`` (tenant_id set
from the user's row).

10 tests (AS1-AS10). LOAD-BEARING: AS1, AS4, AS6, AS8 (per-endpoint
success path + role-diff payload + frozen labels).

Cleanup. Local ``cleanup_tu_audit_users`` fixture tracks tenant_user
IDs and DELETEs audit rows + assignments + users at teardown. The
``make_tenant`` fixture extension (Step 6.16.4 conftest update) cleans
audit rows referencing the tenant_id; this local fixture cleans rows
referencing the user_id (Pattern (b) actor / resource columns have no
FK back to user tables but the rows are still test-noise).
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
        "email": f"audit-{name_suffix}-{uuid.uuid4().hex[:8]}@example.com",
        "full_name": f"Audit User {name_suffix}",
        "roles": _roles_payload(role_assignments),
    }


@pytest_asyncio.fixture
async def cleanup_tu_audit_users(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Track user IDs created in the test; clear audit + assignments
    + tenant_users rows at teardown. Ordering: audit rows first (the
    actor_user_id / resource_id columns have no FK to user tables but
    keeping them out of the DB avoids cross-test query interference),
    assignments next (composite FK ON DELETE RESTRICT blocks tenant_user
    DELETE), then tenant_users.
    """
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


async def _fetch_audit_rows(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    table: str,
    user_id: UUID,
) -> list[dict[str, Any]]:
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"SELECT action, resource_type, resource_id, resource_label, "
                f"result_type, "
                f"actor_user_id, actor_user_type, actor_display_name, "
                f"request_id, details, tenant_id, tenant_name "
                f"FROM {schema}.{table} "
                f"WHERE resource_id = :rid "
                "ORDER BY timestamp ASC"
            ),
            {"rid": user_id},
        )
        return [dict(row) for row in result.mappings()]
    raise AssertionError("unreachable")  # pragma: no cover


async def _count_audit_rows_in_table(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    table: str,
    user_id: UUID,
) -> int:
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"SELECT COUNT(*) FROM {schema}.{table} "
                f"WHERE resource_id = :rid"
            ),
            {"rid": user_id},
        )
        return int(result.scalar_one())
    raise AssertionError("unreachable")  # pragma: no cover


async def _promote_to_active(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    user_id: UUID,
) -> None:
    """Direct DB UPDATE: INVITED -> ACTIVE (the Auth0 invite-accept
    callback is Stage 3; tests bypass to reach states beyond INVITED).
    Sets auth0_sub + invitation_accepted_at to satisfy
    ``ck_tenant_users_auth0_sub_consistency``.
    """
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        await session.execute(
            text(
                f"UPDATE {schema}.tenant_users "
                f"SET status = CAST('ACTIVE' "
                f"  AS {schema}.tenant_user_status_enum), "
                f"  auth0_sub = :sub, "
                f"  invitation_accepted_at = now() "
                f"WHERE id = :uid"
            ),
            {"sub": f"auth0|test-{user_id}", "uid": user_id},
        )


# ---------------------------------------------------------------------------
# AS1 : POST /tenant-users success -> tenant_activity_audit_logs (LOAD-BEARING)
# ---------------------------------------------------------------------------


async def test_as1_post_tenant_users_success_emits_to_tenant_table(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: per-endpoint success contract for the most-used
    write endpoint on tenant-users. CREATE row goes to the tenant
    table (LD1 route_to_platform=False).
    """
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AS1-Tenant"
    )
    role = await make_role(audience="TENANT")

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="as1",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201, resp.text
    user_id = UUID(resp.json()["id"])
    cleanup_tu_audit_users.append(user_id)

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        user_id=user_id,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "CREATE"
    assert row["resource_type"] == "TENANT_USER"
    assert UUID(str(row["resource_id"])) == user_id
    assert row["result_type"] == "SUCCESS"
    # SUPER_ADMIN is a PLATFORM user.
    assert row["actor_user_type"] == "PLATFORM"
    assert UUID(str(row["tenant_id"])) == tenant_id
    assert row["tenant_name"] == "AS1-Tenant"
    assert row["resource_label"] == body["full_name"]


# ---------------------------------------------------------------------------
# AS2 : POST success leaves platform table untouched
# ---------------------------------------------------------------------------


async def test_as2_post_tenant_users_success_zero_platform_rows(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    session_factory,
    platform_auth,
) -> None:
    """Routing verification: POST /tenant-users does not leak a
    platform-table row.
    """
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AS2-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="as2",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201
    user_id = UUID(resp.json()["id"])
    cleanup_tu_audit_users.append(user_id)

    count = await _count_audit_rows_in_table(
        session_factory,
        platform_auth,
        table="platform_activity_audit_logs",
        user_id=user_id,
    )
    assert count == 0


# ---------------------------------------------------------------------------
# AS3 : PATCH full_name change -> UPDATE row with field-level diff
# ---------------------------------------------------------------------------


async def test_as3_patch_full_name_emits_update_with_field_diff(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    session_factory,
    platform_auth,
) -> None:
    """PATCH that only changes ``full_name`` emits one UPDATE row with
    details.before.full_name + details.after.full_name. No ``roles``
    key on either side.
    """
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AS3-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="as3",
    )
    create_resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create_resp.json()["id"])
    cleanup_tu_audit_users.append(user_id)
    original_name = body["full_name"]

    patch_resp = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={"full_name": "Ada Lovelace"},
        headers=_auth(super_admin_jwt),
    )
    assert patch_resp.status_code == 200, patch_resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        user_id=user_id,
    )
    update_rows = [r for r in rows if r["action"] == "UPDATE"]
    assert len(update_rows) == 1
    details = update_rows[0]["details"]
    assert details["before"] == {"full_name": original_name}
    assert details["after"] == {"full_name": "Ada Lovelace"}


# ---------------------------------------------------------------------------
# AS4 : PATCH roles diff -> before+after FULL role lists (LOAD-BEARING)
# ---------------------------------------------------------------------------


async def test_as4_patch_roles_diff_emits_full_before_after_lists(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING per LD8 + Phase 1 Q1: details.before.roles and
    details.after.roles BOTH carry the full role list (not the diff)
    when roles change. Each item carries the 4 frozen-label fields.
    """
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AS4-Tenant"
    )
    role_a = await make_role(audience="TENANT", code="AS4_A", name="Role A")
    role_b = await make_role(audience="TENANT", code="AS4_B", name="Role B")
    role_c = await make_role(audience="TENANT", code="AS4_C", name="Role C")

    # Create with [A, B]
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role_a.id, root_id), (role_b.id, root_id)],
        name_suffix="as4",
    )
    create_resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create_resp.json()["id"])
    cleanup_tu_audit_users.append(user_id)

    # PATCH to [B, C]: removes A, keeps B, adds C.
    patch_resp = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={
            "roles": _roles_payload(
                [(role_b.id, root_id), (role_c.id, root_id)]
            )
        },
        headers=_auth(super_admin_jwt),
    )
    assert patch_resp.status_code == 200, patch_resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        user_id=user_id,
    )
    update_rows = [r for r in rows if r["action"] == "UPDATE"]
    assert len(update_rows) == 1
    details = update_rows[0]["details"]
    # Full lists per Phase 1 Q1 (not diffs).
    before_ids = {item["role_id"] for item in details["before"]["roles"]}
    after_ids = {item["role_id"] for item in details["after"]["roles"]}
    assert before_ids == {str(role_a.id), str(role_b.id)}
    assert after_ids == {str(role_b.id), str(role_c.id)}
    # Frozen-label fields present per LD9.
    for item in details["before"]["roles"] + details["after"]["roles"]:
        assert set(item.keys()) >= {
            "role_id", "role_name", "org_node_id", "org_node_name",
        }
        assert item["org_node_id"] == str(root_id)


# ---------------------------------------------------------------------------
# AS5 : PATCH full_name AND roles -> combined diff
# ---------------------------------------------------------------------------


async def test_as5_patch_combined_emits_field_and_role_diff(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    session_factory,
    platform_auth,
) -> None:
    """A single PATCH that changes both full_name AND roles produces
    ONE audit row carrying both field-level AND role-list diffs.
    """
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AS5-Tenant"
    )
    role_a = await make_role(audience="TENANT", code="AS5_A", name="Role A")
    role_b = await make_role(audience="TENANT", code="AS5_B", name="Role B")

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role_a.id, root_id)],
        name_suffix="as5",
    )
    create_resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create_resp.json()["id"])
    cleanup_tu_audit_users.append(user_id)
    orig_name = body["full_name"]

    patch_resp = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={
            "full_name": "New Name",
            "roles": _roles_payload([(role_b.id, root_id)]),
        },
        headers=_auth(super_admin_jwt),
    )
    assert patch_resp.status_code == 200, patch_resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        user_id=user_id,
    )
    update_rows = [r for r in rows if r["action"] == "UPDATE"]
    assert len(update_rows) == 1
    details = update_rows[0]["details"]
    # Field-level diff.
    assert details["before"]["full_name"] == orig_name
    assert details["after"]["full_name"] == "New Name"
    # Role-list diff.
    before_ids = {it["role_id"] for it in details["before"]["roles"]}
    after_ids = {it["role_id"] for it in details["after"]["roles"]}
    assert before_ids == {str(role_a.id)}
    assert after_ids == {str(role_b.id)}


# ---------------------------------------------------------------------------
# AS6 : POST /suspend success -> SUSPEND action with status diff (LOAD-BEARING)
# ---------------------------------------------------------------------------


async def test_as6_suspend_success_emits_suspend_with_status_diff(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: SUSPEND success row carries ACTIVE -> SUSPENDED
    status diff in the standard transition shape.
    """
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AS6-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="as6",
    )
    create_resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create_resp.json()["id"])
    cleanup_tu_audit_users.append(user_id)
    await _promote_to_active(
        session_factory, platform_auth, user_id=user_id
    )

    susp_resp = app_client.post(
        f"/api/v1/tenant-users/{user_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert susp_resp.status_code == 200, susp_resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        user_id=user_id,
    )
    suspend_rows = [r for r in rows if r["action"] == "SUSPEND"]
    assert len(suspend_rows) == 1
    details = suspend_rows[0]["details"]
    assert details["before"] == {"status": "ACTIVE"}
    assert details["after"] == {"status": "SUSPENDED"}


# ---------------------------------------------------------------------------
# AS7 : POST /activate success -> ACTIVATE with status diff
# ---------------------------------------------------------------------------


async def test_as7_activate_success_emits_activate_with_status_diff(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    session_factory,
    platform_auth,
) -> None:
    """ACTIVATE success row carries SUSPENDED -> ACTIVE status diff."""
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AS7-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="as7",
    )
    create_resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create_resp.json()["id"])
    cleanup_tu_audit_users.append(user_id)
    await _promote_to_active(
        session_factory, platform_auth, user_id=user_id
    )
    app_client.post(
        f"/api/v1/tenant-users/{user_id}/suspend",
        headers=_auth(super_admin_jwt),
    )

    act_resp = app_client.post(
        f"/api/v1/tenant-users/{user_id}/activate",
        headers=_auth(super_admin_jwt),
    )
    assert act_resp.status_code == 200, act_resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        user_id=user_id,
    )
    activate_rows = [r for r in rows if r["action"] == "ACTIVATE"]
    assert len(activate_rows) == 1
    details = activate_rows[0]["details"]
    assert details["before"] == {"status": "SUSPENDED"}
    assert details["after"] == {"status": "ACTIVE"}


# ---------------------------------------------------------------------------
# AS8 : CREATE row's details.snapshot.roles[] has frozen labels (LOAD-BEARING)
# ---------------------------------------------------------------------------


async def test_as8_create_snapshot_roles_carry_frozen_labels(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING per LD9: CREATE audit row's
    details.snapshot.roles[] contains each role with the 4
    frozen-label fields. Names snapshotted at write time.
    """
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AS8-Tenant"
    )
    role = await make_role(
        audience="TENANT", code="AS8_ROLE", name="AS8 Role Display"
    )
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="as8",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(resp.json()["id"])
    cleanup_tu_audit_users.append(user_id)

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        user_id=user_id,
    )
    assert len(rows) == 1
    details = rows[0]["details"]
    snapshot = details["snapshot"]
    assert "roles" in snapshot
    assert len(snapshot["roles"]) == 1
    item = snapshot["roles"][0]
    assert item["role_id"] == str(role.id)
    assert item["role_name"] == "AS8 Role Display"
    assert item["org_node_id"] == str(root_id)
    assert item["org_node_name"] is not None


# ---------------------------------------------------------------------------
# AS9 : PATCH diff preserves unchanged (current ∩ desired) tuples
# ---------------------------------------------------------------------------


async def test_as9_patch_diff_preserves_unchanged_roles_in_both_lists(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    session_factory,
    platform_auth,
) -> None:
    """When a PATCH preserves a (role_id, org_node_id) tuple across
    the diff, the same tuple appears in both before.roles and
    after.roles with consistent frozen labels (the snapshot is taken
    once per side; the helper resolves names from the live tables).
    """
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AS9-Tenant"
    )
    role_keeper = await make_role(
        audience="TENANT", code="AS9_KEEP", name="Keeper Role"
    )
    role_drop = await make_role(
        audience="TENANT", code="AS9_DROP", name="Dropped Role"
    )
    role_add = await make_role(
        audience="TENANT", code="AS9_ADD", name="Added Role"
    )

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role_keeper.id, root_id), (role_drop.id, root_id)],
        name_suffix="as9",
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
        json={
            "roles": _roles_payload(
                [(role_keeper.id, root_id), (role_add.id, root_id)]
            )
        },
        headers=_auth(super_admin_jwt),
    )
    assert patch_resp.status_code == 200

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        user_id=user_id,
    )
    update_rows = [r for r in rows if r["action"] == "UPDATE"]
    details = update_rows[0]["details"]
    # Keeper appears in both halves with the same id + name.
    before_by_id = {it["role_id"]: it for it in details["before"]["roles"]}
    after_by_id = {it["role_id"]: it for it in details["after"]["roles"]}
    keeper_str = str(role_keeper.id)
    assert keeper_str in before_by_id and keeper_str in after_by_id
    assert before_by_id[keeper_str]["role_name"] == "Keeper Role"
    assert after_by_id[keeper_str]["role_name"] == "Keeper Role"


# ---------------------------------------------------------------------------
# AS10 : audit row request_id correlates with X-Request-Id header
# ---------------------------------------------------------------------------


async def test_as10_audit_row_request_id_matches_response_header(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tu_audit_users,
    session_factory,
    platform_auth,
) -> None:
    """Correlation invariant: the audit row's request_id equals the
    HTTP response's X-Request-Id header value.
    """
    tenant_id, root_id, _ = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="AS10-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="as10",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201
    user_id = UUID(resp.json()["id"])
    cleanup_tu_audit_users.append(user_id)
    response_request_id = resp.headers.get("X-Request-Id")
    assert response_request_id is not None

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        user_id=user_id,
    )
    assert len(rows) == 1
    assert str(rows[0]["request_id"]) == response_request_id
