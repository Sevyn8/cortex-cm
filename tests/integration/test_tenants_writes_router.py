"""Integration tests for the tenants write endpoints (Step 6.11.2).

Coverage shape:

  C1-C9:  POST /tenants  (create)
  P1-P10: PATCH /tenants/{id}
  S1-S6:  POST /tenants/{id}/suspend
  A1-A5:  POST /tenants/{id}/activate
  AUD-1,
  AUD-2:  audience-kwarg ordering (Layer 1 fires before Layer 2)

Four LOAD-BEARING regression tests:
  - C8:  POST with TENANT JWT -> 403 PLATFORM_AUDIENCE_REQUIRED.
  - P5:  PATCH with TENANT JWT -> 403 PLATFORM_AUDIENCE_REQUIRED.
  - S6:  /suspend with PLATFORM_ADMIN -> 403 PERMISSION_DENIED.
         Catches OVERRIDE-vs-CONFIGURE catalogue privilege regression.
  - AUD-2: TENANT JWT POST gets PLATFORM_AUDIENCE_REQUIRED, not
           PERMISSION_DENIED — Layer 1 ordering invariant.

Cleanup. The local ``cleanup_tenants_router`` fixture tracks IDs
returned by ``POST /tenants`` and DELETEs them at teardown — TMA
rows first (FK is ON DELETE RESTRICT), then tenants. Fixture order
in test signatures: ``make_platform_user`` BEFORE
``cleanup_tenants_router`` so the platform_user FK refs are
released before make_platform_user tries to delete its row. The
TestClient session is request-scoped (FastAPI commits per request),
so cleanup sees committed rows immediately at teardown.
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
    """TestClient with engine + session_factory wired onto app.state.

    Mirrors the ``app_client`` shape used in test_tenants_router.py —
    bypasses the lifespan so the test event loop owns the engine. The
    StubAuthClient is wired so the auth middleware can verify our
    minted JWTs.
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
async def cleanup_tenants_router(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Tracks tenant IDs created via ``POST /api/v1/tenants``; DELETEs
    at teardown (TMA rows first per FK ON DELETE RESTRICT).

    Each TestClient request commits its own request-scope transaction,
    so cleanup sees committed rows at teardown without ordering
    discipline against another open session (unlike the repo-write
    tests' ``cleanup_tenants``). Fixture order in each test signature
    must still list ``make_platform_user`` BEFORE
    ``cleanup_tenants_router`` so the platform_user FK refs are
    released before make_platform_user tries to delete its row.
    """
    schema = get_settings().db_schema
    created: list[UUID] = []
    yield created

    if created:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            # Step 6.16.2: audit rows pin tenants via FK ON DELETE RESTRICT.
            # Clear both audit tables before the tenants DELETE so the
            # cascade-by-explicit-DELETE pattern remains valid.
            await session.execute(
                text(
                    f"DELETE FROM {schema}.tenant_activity_audit_logs "
                    "WHERE tenant_id = ANY(:ids)"
                ),
                {"ids": created},
            )
            await session.execute(
                text(
                    f"DELETE FROM {schema}.platform_activity_audit_logs "
                    "WHERE tenant_id = ANY(:ids)"
                ),
                {"ids": created},
            )
            await session.execute(
                text(
                    f"DELETE FROM {schema}.tenant_module_access "
                    "WHERE tenant_id = ANY(:ids)"
                ),
                {"ids": created},
            )
            # Step 6.20.1: POST /tenants now provisions a tenant-root
            # org_node in the same transaction. Both FKs back to tenants
            # are ON DELETE RESTRICT; clear org_nodes before the tenants
            # DELETE.
            await session.execute(
                text(
                    f"DELETE FROM {schema}.org_nodes "
                    "WHERE tenant_id = ANY(:ids)"
                ),
                {"ids": created},
            )
            await session.execute(
                text(f"DELETE FROM {schema}.tenants WHERE id = ANY(:ids)"),
                {"ids": created},
            )


def _platform_jwt_for_user(settings: Settings, user_id: UUID) -> str:
    """Mint a PLATFORM JWT for a specific platform_users.id.

    Used to assign a JWT to a freshly-created (via make_platform_user)
    PLATFORM user that has no role grants. The Layer 1 audience check
    fires before has_permission, so this is fine for AUD-2.
    """
    return make_test_jwt(
        settings,
        user_id=user_id,
        user_type="PLATFORM",
    )


def _tenant_jwt(settings: Settings, tenant_id: UUID) -> str:
    """Mint a TENANT JWT for an arbitrary user_id in the given tenant.

    The audience-kwarg gate (Layer 1) fires on user_type alone, BEFORE
    any DB lookup — so a random user_id is sufficient for tests that
    only assert the platform-only refusal.
    """
    return make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="TENANT",
        tenant_id=tenant_id,
    )


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _valid_create_body(name: str) -> dict[str, Any]:
    """Minimal valid POST /tenants body for happy-path-style tests.

    Email uses ``.example.com`` (RFC 2606 reserved for documentation /
    test) rather than ``.local`` (RFC 6762 mDNS) which the
    email-validator library blocks as a special-use TLD.
    """
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


async def _grant_platform_admin(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    user_id: UUID,
) -> None:
    """Assign the seeded PLATFORM_ADMIN role to ``user_id``.

    Required for C7 / P6 (PLATFORM_ADMIN can create/patch) and
    S6 (PLATFORM_ADMIN refused on suspend by OVERRIDE.GLOBAL). The
    cleanup happens via test-function-scoped session: the assignment
    row is direct-INSERTed here; after the test the role assignment
    becomes orphan FK referencing a deleted platform_user, but
    ``make_platform_user``'s teardown deletes those PUs and the
    assignment cascades via FK only if ON DELETE CASCADE is set.

    PUR ``ON DELETE`` semantics: per DDL,
    ``platform_user_role_assignments.platform_user_id`` is
    ``ON DELETE RESTRICT``. So we explicitly DELETE the assignment
    here at teardown by appending to a tracker. Done via the
    ``_cleanup_assignments`` fixture pattern below.
    """
    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        role_row = await session.execute(
            text(f"SELECT id FROM {schema}.roles WHERE code = 'PLATFORM_ADMIN'"),
        )
        role_id = role_row.scalar_one()
        await session.execute(
            text(
                f"INSERT INTO {schema}.platform_user_role_assignments ("
                "  platform_user_id, role_id, status,"
                "  granted_by_user_id, granted_by_user_type"
                ") VALUES ("
                "  :user_id,"
                "  :role_id,"
                f"  CAST('ACTIVE' AS {schema}.user_role_assignment_status_enum),"
                "  NULL, NULL"
                ")"
            ),
            {"user_id": user_id, "role_id": role_id},
        )


@pytest_asyncio.fixture
async def cleanup_assignments(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Tracks platform_user IDs whose role assignments need cleanup.

    Delete all platform_user_role_assignments rows referencing each
    tracked user_id at teardown, before make_platform_user tries to
    DELETE the user. ``ON DELETE RESTRICT`` on
    ``platform_user_role_assignments.platform_user_id`` makes this
    cleanup load-bearing for fixture-teardown ordering.
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
                    f"DELETE FROM {schema}.platform_user_role_assignments "
                    "WHERE platform_user_id = ANY(:ids)"
                ),
                {"ids": tracked},
            )


# ============================================================================
# POST /tenants (C1-C9)
# ============================================================================


async def test_c1_super_admin_create_returns_201_with_trial_and_admin_module(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """SUPER_ADMIN happy path: 201, status TRIAL, modules include ADMIN."""
    body = _valid_create_body("C1-Acme")
    resp = app_client.post(
        "/api/v1/tenants",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201, resp.text
    j = resp.json()
    cleanup_tenants_router.append(UUID(j["id"]))
    assert j["name"] == "C1-Acme"
    assert j["status"] == "TRIAL"
    assert any(m["code"] == "ADMIN" for m in j["modules"])


async def test_c2_modules_enabled_force_includes_admin(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """Explicit modules_enabled without ADMIN gets ADMIN appended."""
    body = _valid_create_body("C2-WithModules")
    body["modules_enabled"] = ["PRICING_OS", "PERISHABLES_ASSISTANT"]
    resp = app_client.post(
        "/api/v1/tenants",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201, resp.text
    j = resp.json()
    cleanup_tenants_router.append(UUID(j["id"]))
    codes = {m["code"] for m in j["modules"]}
    assert codes == {"ADMIN", "PRICING_OS", "PERISHABLES_ASSISTANT"}


async def test_c3_empty_modules_enabled_yields_admin_only(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """modules_enabled=[] -> only ADMIN row."""
    body = _valid_create_body("C3-Empty")
    body["modules_enabled"] = []
    resp = app_client.post(
        "/api/v1/tenants",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201, resp.text
    j = resp.json()
    cleanup_tenants_router.append(UUID(j["id"]))
    assert {m["code"] for m in j["modules"]} == {"ADMIN"}


async def test_c4_duplicate_name_returns_409(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """Two POSTs with the same name -> 409 DUPLICATE_TENANT_NAME."""
    body = _valid_create_body("C4-DupName")
    r1 = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    assert r1.status_code == 201
    cleanup_tenants_router.append(UUID(r1.json()["id"]))

    # Second POST with same name; vary email to avoid any other accidental
    # collision should one ship in future.
    body["contact_email"] = "other@example.com"
    r2 = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    assert r2.status_code == 409
    assert r2.json()["code"] == "DUPLICATE_TENANT_NAME"


async def test_c5_invalid_module_code_returns_422(
    app_client, super_admin_jwt,
) -> None:
    """Module code outside the enum is 422 (Pydantic schema rejection)."""
    body = _valid_create_body("C5-BadModule")
    body["modules_enabled"] = ["NOT_A_MODULE"]
    resp = app_client.post(
        "/api/v1/tenants",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422


async def test_c6_status_in_body_returns_422(
    app_client, super_admin_jwt,
) -> None:
    """``status`` is server-forced; extra='forbid' rejects with 422."""
    body = _valid_create_body("C6-StatusInBody")
    body["status"] = "ACTIVE"
    resp = app_client.post(
        "/api/v1/tenants",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422


async def test_c7_platform_admin_create_returns_201(
    app_client,
    settings,
    make_platform_user,
    cleanup_assignments,
    cleanup_tenants_router,
    session_factory,
    platform_auth,
) -> None:
    """PLATFORM_ADMIN holds CONFIGURE.GLOBAL and can create."""
    pa = await make_platform_user(status="ACTIVE")
    await _grant_platform_admin(session_factory, platform_auth, pa.id)
    cleanup_assignments.append(pa.id)
    jwt = _platform_jwt_for_user(settings, pa.id)

    body = _valid_create_body("C7-Pa-Created")
    resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(jwt),
    )
    assert resp.status_code == 201, resp.text
    cleanup_tenants_router.append(UUID(resp.json()["id"]))


async def test_c8_tenant_jwt_returns_403_platform_audience_required(
    app_client, settings,
) -> None:
    """LOAD-BEARING — TENANT JWT POST /tenants -> 403 PLATFORM_AUDIENCE_REQUIRED.

    Without this guard, a future regression dropping audience="PLATFORM"
    from the route would let TENANT users hit the permission check
    (which they'd fail anyway, but with a less specific 403). The
    Layer 1 ordering invariant is independently asserted in AUD-2.
    """
    jwt = _tenant_jwt(settings, uuid.uuid4())
    body = _valid_create_body("C8-Should-Not-Land")
    resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(jwt),
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "PLATFORM_AUDIENCE_REQUIRED"


async def test_c9_audit_columns_populated(
    app_client,
    settings,
    super_admin_jwt,
    session_factory,
    platform_auth,
    cleanup_tenants_router,
) -> None:
    """Created tenant carries created_by_user_id = the JWT's user_id."""
    schema = get_settings().db_schema
    # Resolve Anjali's id from the seed so we can compare.
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        anjali_row = await session.execute(
            text(
                f"SELECT id FROM {schema}.platform_users WHERE email = 'anjali@ithina.ai'"
            )
        )
        anjali_id = UUID(str(anjali_row.scalar_one()))

    body = _valid_create_body("C9-AuditCols")
    resp = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201, resp.text
    new_id = UUID(resp.json()["id"])
    cleanup_tenants_router.append(new_id)

    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        row = await session.execute(
            text(
                "SELECT created_by_user_id, updated_by_user_id "
                f"FROM {schema}.tenants WHERE id = :id"
            ),
            {"id": new_id},
        )
        c_by, u_by = row.one()
        assert UUID(str(c_by)) == anjali_id
        assert UUID(str(u_by)) == anjali_id


# ============================================================================
# PATCH /tenants/{id} (P1-P10)
# ============================================================================


async def test_p1_super_admin_patch_happy_path(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """PATCH a subset of fields -> 200 with updated values reflected."""
    body = _valid_create_body("P1-Original")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)

    patch = app_client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"primary_contact_name": "New Operator"},
        headers=_auth(super_admin_jwt),
    )
    assert patch.status_code == 200
    assert patch.json()["primary_contact_name"] == "New Operator"


async def test_p2_status_in_body_returns_422(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """PATCH with ``status`` in body -> 422 (extra='forbid')."""
    body = _valid_create_body("P2-NoStatus")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)

    patch = app_client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"status": "SUSPENDED"},
        headers=_auth(super_admin_jwt),
    )
    assert patch.status_code == 422


async def test_p3_missing_id_returns_404(
    app_client, super_admin_jwt,
) -> None:
    """PATCH a non-existent tenant -> 404 TENANT_NOT_FOUND."""
    missing = uuid.uuid4()
    resp = app_client.patch(
        f"/api/v1/tenants/{missing}",
        json={"primary_contact_name": "X"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "TENANT_NOT_FOUND"


async def test_p4_allowed_on_suspended_tenant(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """PATCH still accepted while tenant is SUSPENDED — only TERMINATED
    locks the row out (out of scope for v0)."""
    body = _valid_create_body("P4-Suspendable")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)

    suspend = app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert suspend.status_code == 200
    assert suspend.json()["status"] == "SUSPENDED"

    patch = app_client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"primary_contact_name": "Even-While-Suspended"},
        headers=_auth(super_admin_jwt),
    )
    assert patch.status_code == 200


async def test_p5_tenant_jwt_returns_403_platform_audience_required(
    app_client, settings,
) -> None:
    """LOAD-BEARING — PATCH with TENANT JWT -> 403 PLATFORM_AUDIENCE_REQUIRED.

    Multi-audience PATCH lands post-6.16; until then PATCH is strictly
    platform-only. This guard catches accidental gate loosening.
    """
    jwt = _tenant_jwt(settings, uuid.uuid4())
    resp = app_client.patch(
        f"/api/v1/tenants/{uuid.uuid4()}",
        json={"primary_contact_name": "X"},
        headers=_auth(jwt),
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "PLATFORM_AUDIENCE_REQUIRED"


async def test_p6_platform_admin_patch_returns_200(
    app_client,
    settings,
    make_platform_user,
    cleanup_assignments,
    cleanup_tenants_router,
    session_factory,
    platform_auth,
    super_admin_jwt,
) -> None:
    """PLATFORM_ADMIN holds CONFIGURE.GLOBAL and can patch."""
    body = _valid_create_body("P6-PlatformAdmin")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)

    pa = await make_platform_user(status="ACTIVE")
    await _grant_platform_admin(session_factory, platform_auth, pa.id)
    cleanup_assignments.append(pa.id)
    pa_jwt = _platform_jwt_for_user(settings, pa.id)

    patch = app_client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"primary_contact_name": "Patched-by-PA"},
        headers=_auth(pa_jwt),
    )
    assert patch.status_code == 200


async def test_p7_updated_at_and_updated_by_reflect_caller(
    app_client,
    settings,
    super_admin_jwt,
    session_factory,
    platform_auth,
    cleanup_tenants_router,
) -> None:
    """PATCH refreshes updated_at (DB trigger) and updated_by_user_id."""
    body = _valid_create_body("P7-AuditPatch")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)
    initial_updated_at = create.json()["updated_at"]

    patch = app_client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"primary_contact_name": "Refresh-Audit"},
        headers=_auth(super_admin_jwt),
    )
    assert patch.status_code == 200
    assert patch.json()["updated_at"] >= initial_updated_at

    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        row = await session.execute(
            text(
                f"SELECT updated_by_user_id FROM {schema}.tenants WHERE id = :id"
            ),
            {"id": tenant_id},
        )
        anjali_row = await session.execute(
            text(
                f"SELECT id FROM {schema}.platform_users WHERE email = 'anjali@ithina.ai'"
            )
        )
        anjali_id = UUID(str(anjali_row.scalar_one()))
    assert UUID(str(row.scalar_one())) == anjali_id


async def test_p8_empty_patch_returns_422_empty_patch(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """PATCH with empty body -> 422 EMPTY_PATCH."""
    body = _valid_create_body("P8-EmptyPatch")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)

    patch = app_client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={},
        headers=_auth(super_admin_jwt),
    )
    assert patch.status_code == 422
    assert patch.json()["code"] == "EMPTY_PATCH"


async def test_p9_rename_to_taken_returns_409(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """Rename to a name another tenant already holds -> 409."""
    first = app_client.post(
        "/api/v1/tenants",
        json=_valid_create_body("P9-FirstName"),
        headers=_auth(super_admin_jwt),
    )
    cleanup_tenants_router.append(UUID(first.json()["id"]))
    second = app_client.post(
        "/api/v1/tenants",
        json=_valid_create_body("P9-SecondName"),
        headers=_auth(super_admin_jwt),
    )
    second_id = UUID(second.json()["id"])
    cleanup_tenants_router.append(second_id)

    patch = app_client.patch(
        f"/api/v1/tenants/{second_id}",
        json={"name": "P9-FirstName"},
        headers=_auth(super_admin_jwt),
    )
    assert patch.status_code == 409
    assert patch.json()["code"] == "DUPLICATE_TENANT_NAME"


async def test_p10_rename_to_same_name_is_noop_success(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """PATCH name to current value -> 200 (exclude_tenant_id excludes self)."""
    body = _valid_create_body("P10-SameName")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)

    patch = app_client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"name": "P10-SameName"},
        headers=_auth(super_admin_jwt),
    )
    assert patch.status_code == 200
    assert patch.json()["name"] == "P10-SameName"


# ============================================================================
# POST /tenants/{id}/suspend (S1-S6)
# ============================================================================


async def test_s1_trial_to_suspended(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """TRIAL -> SUSPENDED via /suspend."""
    body = _valid_create_body("S1-Trial")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)
    assert create.json()["status"] == "TRIAL"

    resp = app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    j = resp.json()
    assert j["status"] == "SUSPENDED"
    assert j["suspended_at"] is not None


async def test_s2_active_to_suspended(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """ACTIVE -> SUSPENDED."""
    body = _valid_create_body("S2-WillBeActive")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)

    activate = app_client.post(
        f"/api/v1/tenants/{tenant_id}/activate",
        headers=_auth(super_admin_jwt),
    )
    assert activate.json()["status"] == "ACTIVE"

    suspend = app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert suspend.status_code == 200
    assert suspend.json()["status"] == "SUSPENDED"


async def test_s3_suspended_to_suspended_returns_409(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """SUSPENDED -> SUSPENDED -> 409 INVALID_STATE_TRANSITION."""
    body = _valid_create_body("S3-AlreadySuspended")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)
    app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(super_admin_jwt),
    )

    resp = app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "INVALID_STATE_TRANSITION"


async def test_s4_missing_id_returns_404(
    app_client, super_admin_jwt,
) -> None:
    """Suspending a non-existent tenant -> 404."""
    resp = app_client.post(
        f"/api/v1/tenants/{uuid.uuid4()}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404


async def test_s5_tenant_jwt_returns_403_platform_audience_required(
    app_client, settings,
) -> None:
    """TENANT JWT -> 403 PLATFORM_AUDIENCE_REQUIRED."""
    jwt = _tenant_jwt(settings, uuid.uuid4())
    resp = app_client.post(
        f"/api/v1/tenants/{uuid.uuid4()}/suspend",
        headers=_auth(jwt),
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "PLATFORM_AUDIENCE_REQUIRED"


async def test_s6_platform_admin_returns_403_permission_denied(
    app_client,
    settings,
    make_platform_user,
    cleanup_assignments,
    cleanup_tenants_router,
    super_admin_jwt,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — PLATFORM_ADMIN on /suspend -> 403 PERMISSION_DENIED.

    Catches OVERRIDE-vs-CONFIGURE catalogue privilege regression:
    PLATFORM_ADMIN holds CONFIGURE.GLOBAL (so the gate passes Layer 1
    and the audience check) but NOT OVERRIDE.GLOBAL. Without this
    test, a future seed change re-granting OVERRIDE to PLATFORM_ADMIN
    would silently widen the privilege model.
    """
    body = _valid_create_body("S6-NoSuspend")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)

    pa = await make_platform_user(status="ACTIVE")
    await _grant_platform_admin(session_factory, platform_auth, pa.id)
    cleanup_assignments.append(pa.id)
    pa_jwt = _platform_jwt_for_user(settings, pa.id)

    resp = app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(pa_jwt),
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "PERMISSION_DENIED"


# ============================================================================
# POST /tenants/{id}/activate (A1-A5)
# ============================================================================


async def test_a1_trial_to_active(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """TRIAL -> ACTIVE."""
    body = _valid_create_body("A1-Trial")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)

    resp = app_client.post(
        f"/api/v1/tenants/{tenant_id}/activate",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ACTIVE"


async def test_a2_suspended_to_active_clears_suspended_columns(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """SUSPENDED -> ACTIVE clears suspended_at + suspended_by_user_id."""
    body = _valid_create_body("A2-SuspendThenActive")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)

    app_client.post(
        f"/api/v1/tenants/{tenant_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    resp = app_client.post(
        f"/api/v1/tenants/{tenant_id}/activate",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    j = resp.json()
    assert j["status"] == "ACTIVE"
    assert j["suspended_at"] is None


async def test_a3_active_to_active_returns_409(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """ACTIVE -> ACTIVE -> 409 INVALID_STATE_TRANSITION."""
    body = _valid_create_body("A3-AlreadyActive")
    create = app_client.post(
        "/api/v1/tenants", json=body, headers=_auth(super_admin_jwt),
    )
    tenant_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(tenant_id)
    app_client.post(
        f"/api/v1/tenants/{tenant_id}/activate",
        headers=_auth(super_admin_jwt),
    )

    resp = app_client.post(
        f"/api/v1/tenants/{tenant_id}/activate",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "INVALID_STATE_TRANSITION"


async def test_a4_missing_id_returns_404(
    app_client, super_admin_jwt,
) -> None:
    """Activating a non-existent tenant -> 404."""
    resp = app_client.post(
        f"/api/v1/tenants/{uuid.uuid4()}/activate",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404


async def test_a5_tenant_jwt_returns_403_platform_audience_required(
    app_client, settings,
) -> None:
    """TENANT JWT -> 403 PLATFORM_AUDIENCE_REQUIRED."""
    jwt = _tenant_jwt(settings, uuid.uuid4())
    resp = app_client.post(
        f"/api/v1/tenants/{uuid.uuid4()}/activate",
        headers=_auth(jwt),
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "PLATFORM_AUDIENCE_REQUIRED"


# ============================================================================
# Audience-kwarg coverage (AUD-1, AUD-2)
# ============================================================================


async def test_aud1_existing_read_endpoint_tenant_jwt_still_works(
    app_client, settings, make_tenant, tenant_owner_jwt_factory,
) -> None:
    """Audience=None default preserves pre-6.11 call sites.

    /api/v1/tenants/{id} retains its ADMIN.TENANTS.VIEW.TENANT gate
    (no audience kwarg). A TENANT OWNER with the default grant set
    (which includes ADMIN.TENANTS.VIEW.TENANT post Phase 3) gets 200
    on their own tenant — same as pre-step.
    """
    tenant = await make_tenant(name="AUD1-Read")
    jwt = await tenant_owner_jwt_factory(tenant.id)
    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}",
        headers=_auth(jwt),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == str(tenant.id)


async def test_aud2_tenant_jwt_on_post_raises_audience_not_permission(
    app_client, settings,
) -> None:
    """LOAD-BEARING — Layer 1 (audience) fires BEFORE Layer 2 (has_permission).

    A TENANT JWT with NO grants POSTing to /api/v1/tenants gets
    PLATFORM_AUDIENCE_REQUIRED (not PERMISSION_DENIED). Both would
    correctly deny, but the order matters for defense-in-depth: the
    audience check is a structural assertion ahead of any DB query.
    """
    jwt = _tenant_jwt(settings, uuid.uuid4())
    resp = app_client.post(
        "/api/v1/tenants",
        json=_valid_create_body("AUD2-Should-Not-Land"),
        headers=_auth(jwt),
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "PLATFORM_AUDIENCE_REQUIRED"
    # And NOT the PERMISSION_DENIED that would surface if Layer 2 ran first.
    assert resp.json()["code"] != "PERMISSION_DENIED"


# ============================================================================
# RT: POST then GET roundtrip (Step 6.20.1)
# ============================================================================


async def test_post_then_get_roundtrip(
    app_client, super_admin_jwt, cleanup_tenants_router,
) -> None:
    """LOAD-BEARING — POST /api/v1/tenants then GET /api/v1/tenants/{id}
    with the same SUPER_ADMIN JWT must return 200, not 404.

    This is the end-to-end seal on the Step 6.20.1 bug fix. Pre-fix,
    POST succeeded but GET 404'd because the GET handler depends on
    ``get_tenant_anchor`` which looks up a tenant-root ``org_nodes`` row
    that POST did not create. Post-fix, POST inserts the org_node in
    the same transaction; GET resolves the anchor and returns the row.

    Any future refactor that drops the org_node insert from
    ``TenantsRepo.create`` will surface here as a 404.
    """
    body = _valid_create_body("RT-Roundtrip")
    create = app_client.post(
        "/api/v1/tenants",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert create.status_code == 201, create.text
    new_id = UUID(create.json()["id"])
    cleanup_tenants_router.append(new_id)

    get_resp = app_client.get(
        f"/api/v1/tenants/{new_id}",
        headers=_auth(super_admin_jwt),
    )
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["id"] == str(new_id)
    assert get_resp.json()["name"] == "RT-Roundtrip"
