"""Step 6.16.4 : audit emission for PATCH /api/v1/roles/{role_id}.

Roles are platform-scope catalogue rows; per LD7 every audit row for
PATCH /roles routes to ``platform_activity_audit_logs`` with
``tenant_id`` NULL (``route_to_platform=True``).

Success + failure paths covered:

- RS1-RS3 : success-path emission, routing verification.
- RF1-RF4 : 403 / 409 / 422 failure-path emission.
- RF5     : Layer 2 invariant tripwire (LD12) carries the
            ``invariant`` sub-key in the INTERNAL_ERROR details.

LOAD-BEARING: RS2, RF1, RF2, RF5.
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


def _request_id_from_response(resp: Any) -> UUID:
    rid = resp.headers.get("X-Request-Id")
    assert rid is not None
    return UUID(rid)


@pytest_asyncio.fixture
async def cleanup_audit_by_request_ids(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Track request_ids to clean audit rows at teardown."""
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


@pytest_asyncio.fixture
async def cleanup_role_perms_for_roles(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Track role_ids; clear role_permissions junctions at teardown so
    the make_role teardown's perm DELETE doesn't fail FK.
    """
    tracked: list[UUID] = []
    yield tracked
    if tracked:
        schema = get_settings().db_schema
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.role_permissions "
                    "WHERE role_id = ANY(:ids)"
                ),
                {"ids": tracked},
            )


@pytest_asyncio.fixture
async def override_permission_id(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> UUID:
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"SELECT id FROM {schema}.permissions "
                "WHERE code = :code"
            ),
            {"code": "ADMIN.ROLES.OVERRIDE.GLOBAL"},
        )
        row = result.first()
    if row is None:
        raise LookupError("ADMIN.ROLES.OVERRIDE.GLOBAL not seeded.")
    return uuid.UUID(str(row[0]))


@pytest_asyncio.fixture
async def seeded_super_admin_role_id(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> UUID:
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(f"SELECT id FROM {schema}.roles WHERE code = 'SUPER_ADMIN'")
        )
        row = result.first()
    if row is None:
        raise LookupError("SUPER_ADMIN not in seed.")
    return uuid.UUID(str(row[0]))


async def _fetch_audit_rows_by_request_id(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    request_id: UUID,
) -> list[tuple[str, dict[str, Any]]]:
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


# ---------------------------------------------------------------------------
# RS1 : PATCH role description -> SUCCESS row in platform_activity_audit_logs
# ---------------------------------------------------------------------------


async def test_rs1_patch_description_emits_to_platform_table(
    app_client,
    super_admin_jwt,
    make_role,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    role = await make_role(
        audience="PLATFORM", description="RS1 original"
    )
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"description": "RS1 patched"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    table, row = rows[0]
    assert table == "platform_activity_audit_logs"
    assert row["action"] == "UPDATE"
    assert row["resource_type"] == "ROLE"
    assert UUID(str(row["resource_id"])) == role.id
    assert row["result_type"] == "SUCCESS"
    assert row["tenant_id"] is None
    details = row["details"]
    assert details["before"]["description"] == "RS1 original"
    assert details["after"]["description"] == "RS1 patched"


# ---------------------------------------------------------------------------
# RS2 : PATCH permission_ids diff -> details carries before+after permissions
# lists with frozen labels (LOAD-BEARING)
# ---------------------------------------------------------------------------


async def test_rs2_patch_perms_diff_emits_frozen_label_lists(
    app_client,
    super_admin_jwt,
    make_role,
    make_permission,
    make_role_permission,
    cleanup_role_perms_for_roles,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING per LD9: permissions[] items carry both
    permission_id AND permission_code (frozen at write time).
    Before+after carry FULL lists per Phase 1 Q1.
    """
    role = await make_role(audience="TENANT", name="RS2 Role")
    cleanup_role_perms_for_roles.append(role.id)
    # Use uncommon (module, resource, action, scope) tuples to avoid
    # collision with seed catalogue rows.
    perm_a = await make_permission(
        module="PRICING_OS",
        resource="WASTE_LOG",
        action="AUDIT",
        scope="TENANT",
    )
    perm_b = await make_permission(
        module="PRICING_OS",
        resource="WASTE_LOG",
        action="EXECUTE",
        scope="TENANT",
    )
    # Seed: role holds perm_a only.
    await make_role_permission(role_id=role.id, permission_id=perm_a.id)

    # PATCH: replace [perm_a] with [perm_b].
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": [str(perm_b.id)]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    table, row = rows[0]
    assert table == "platform_activity_audit_logs"
    details = row["details"]
    before_codes = {
        it["permission_code"] for it in details["before"]["permissions"]
    }
    after_codes = {
        it["permission_code"] for it in details["after"]["permissions"]
    }
    assert before_codes == {perm_a.code}
    assert after_codes == {perm_b.code}
    # Frozen-label fields per LD9.
    for item in (
        details["before"]["permissions"] + details["after"]["permissions"]
    ):
        assert set(item.keys()) == {"permission_id", "permission_code"}


# ---------------------------------------------------------------------------
# RS3 : PATCH success leaves tenant table untouched
# ---------------------------------------------------------------------------


async def test_rs3_patch_success_zero_tenant_table_rows(
    app_client,
    super_admin_jwt,
    make_role,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    role = await make_role(audience="PLATFORM", description="rs3")
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"description": "rs3 patched"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    # All rows for this request_id should be on the platform table.
    tables = {t for (t, _r) in rows}
    assert tables == {"platform_activity_audit_logs"}


# ---------------------------------------------------------------------------
# RF1 : TENANT JWT -> 403 PLATFORM_AUDIENCE_REQUIRED emits PERMISSION_DENIED
# (LOAD-BEARING)
# ---------------------------------------------------------------------------


async def test_rf1_tenant_jwt_emits_permission_denied(
    app_client,
    make_tenant,
    make_org_node,
    make_role,
    tenant_owner_jwt_factory,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: Layer 1 audience refusal emits PERMISSION_DENIED
    on platform_activity_audit_logs.
    """
    tenant = await make_tenant(name="RF1 Tenant", with_root=True)
    owner_jwt = await tenant_owner_jwt_factory(
        tenant.id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )
    role = await make_role(audience="PLATFORM", name="RF1 Role")

    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"description": "tenant attempt"},
        headers=_auth(owner_jwt),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "PLATFORM_AUDIENCE_REQUIRED"
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    table, row = rows[0]
    assert table == "platform_activity_audit_logs"
    assert row["result_type"] == "PERMISSION_DENIED"
    assert row["details"]["caller_audience"] == "TENANT"


# ---------------------------------------------------------------------------
# RF2 : PATCH on SUPER_ADMIN -> 409 SUPER_ADMIN_PROTECTED (LOAD-BEARING)
# ---------------------------------------------------------------------------


async def test_rf2_super_admin_patch_emits_conflict(
    app_client,
    super_admin_jwt,
    seeded_super_admin_role_id,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING: SUPER_ADMIN v0 lockout (FN-AB-57) emits CONFLICT
    in details.
    """
    resp = app_client.patch(
        f"/api/v1/roles/{seeded_super_admin_role_id}",
        json={"description": "should be rejected"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "SUPER_ADMIN_PROTECTED"
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    table, row = rows[0]
    assert table == "platform_activity_audit_logs"
    assert row["result_type"] == "CONFLICT"


# ---------------------------------------------------------------------------
# RF3 : PATCH TENANT role with GLOBAL perm -> 422 AUDIENCE_SCOPE_MISMATCH
# ---------------------------------------------------------------------------


async def test_rf3_audience_scope_mismatch_emits_validation_failed(
    app_client,
    super_admin_jwt,
    make_role,
    make_permission,
    cleanup_role_perms_for_roles,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """TENANT-audience role + GLOBAL-scope perm in add-set fires the
    LD10 422; failure-path audit row is VALIDATION_FAILED.
    """
    role = await make_role(audience="TENANT", name="RF3 Role")
    cleanup_role_perms_for_roles.append(role.id)
    # Uncommon tuple (PRICING_OS / WASTE_LOG / AUDIT / GLOBAL) to
    # avoid seed collision.
    global_perm = await make_permission(
        module="PRICING_OS",
        resource="WASTE_LOG",
        action="AUDIT",
        scope="GLOBAL",
    )

    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": [str(global_perm.id)]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "AUDIENCE_SCOPE_MISMATCH"
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    table, row = rows[0]
    assert table == "platform_activity_audit_logs"
    assert row["result_type"] == "VALIDATION_FAILED"


# ---------------------------------------------------------------------------
# RF4 : PATCH with unknown permission_id -> 422 INVALID_PERMISSION_ID
# ---------------------------------------------------------------------------


async def test_rf4_unknown_permission_id_emits_validation_failed(
    app_client,
    super_admin_jwt,
    make_role,
    cleanup_role_perms_for_roles,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    role = await make_role(audience="TENANT", name="RF4 Role")
    cleanup_role_perms_for_roles.append(role.id)
    fake_perm = uuid.uuid4()

    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": [str(fake_perm)]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "INVALID_PERMISSION_ID"
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    table, row = rows[0]
    assert table == "platform_activity_audit_logs"
    assert row["result_type"] == "VALIDATION_FAILED"


# ---------------------------------------------------------------------------
# RF5 : Layer 2 tripwire fires -> INTERNAL_ERROR with invariant sub-key
# (LOAD-BEARING per LD12)
# ---------------------------------------------------------------------------


async def test_rf5_layer_2_tripwire_emits_invariant_sub_key(
    monkeypatch,
    app_client,
    super_admin_jwt,
    make_role,
    make_role_permission,
    override_permission_id,
    cleanup_role_perms_for_roles,
    cleanup_audit_by_request_ids,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING per LD12: Layer 2 tripwire produces an
    INTERNAL_ERROR audit row carrying ``invariant=
    'OVERRIDE_GLOBAL_HOLDER_PRESERVATION'``.

    Setup: create a test role T with OVERRIDE.GLOBAL grant. Force
    Layer 1 to pass (count > 0 when exclude_role_id=T) but Layer 2 to
    fail (count == 0 when exclude_role_id=None). The intermediate
    UPDATE+DELETE applies, then Layer 2 raises
    ``InternalInvariantViolationError`` -> handler emits the
    failure-path audit row with the ``invariant`` sub-key.
    """
    role = await make_role(audience="PLATFORM", name="RF5 Role")
    cleanup_role_perms_for_roles.append(role.id)
    await make_role_permission(
        role_id=role.id, permission_id=override_permission_id,
    )

    real_role_id = role.id

    async def fake_count(session, *, exclude_role_id):
        # Layer 1 (pre-write, exclude_role_id=role.id) sees the seed
        # holders (returns 1: simulate "safe to edit; another holder
        # exists"). Layer 2 (post-write, exclude_role_id=None) returns
        # 0 to simulate the invariant violation.
        if exclude_role_id is None:
            return 0
        return 1

    monkeypatch.setattr(
        "admin_backend.repositories.roles."
        "_count_override_global_active_holders",
        fake_count,
    )

    resp = app_client.patch(
        f"/api/v1/roles/{real_role_id}",
        json={"permission_ids": []},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 500, resp.text
    request_id = _request_id_from_response(resp)
    cleanup_audit_by_request_ids.append(request_id)

    rows = await _fetch_audit_rows_by_request_id(
        session_factory, platform_auth, request_id=request_id
    )
    assert len(rows) == 1
    table, row = rows[0]
    assert table == "platform_activity_audit_logs"
    assert row["result_type"] == "INTERNAL_ERROR"
    details = row["details"]
    # Optional sub-key per LD12.
    assert details["invariant"] == "OVERRIDE_GLOBAL_HOLDER_PRESERVATION"
    # Standard sub-keys preserved.
    assert "error_class" in details
    assert "sanitised_message" in details
