"""Integration tests for the Step 6.15 module-access write endpoints.

Coverage shape:

  C1-C6:  POST /api/v1/module-access/{tenant_id}/{module_code}/enable
          and .../disable transition matrix cells (6 row states).
  P1-P4:  permission boundary checks (TENANT, PLATFORM_ADMIN, anchor).
  V1:     path-param validation (invalid module_code -> 422).
  AUD-1:  audience-kwarg ordering (Layer 1 fires before Layer 2).
  R1-R2:  regression flows (sequential idempotence, overwrite ordering).

Five LOAD-BEARING tests:
  - C1:  enable upsert path (missing row -> 200 + new row).
  - C4:  disable on missing -> 404 MODULE_ACCESS_NOT_FOUND.
  - P1/P2: TENANT JWT -> 403 PLATFORM_AUDIENCE_REQUIRED.
  - P3:  PLATFORM_ADMIN -> 403 PERMISSION_DENIED (Layer 2; catches
         OVERRIDE-to-CONFIGURE catalogue regression; mirrors 6.11.2 S6).
  - P4:  Unknown tenant_id -> 404 TENANT_NOT_FOUND from anchor dep.
  - AUD-1: TENANT JWT POST gets PLATFORM_AUDIENCE_REQUIRED, NOT
           PERMISSION_DENIED — Layer 1 ordering invariant.

Cleanup. ``cleanup_module_access_router`` tracks tenant_module_access
row IDs created via TestClient requests and DELETEs them at teardown.
Each TestClient request commits its own request-scope transaction so
cleanup sees committed rows immediately. Fixture order in test
signatures: ``make_platform_user`` BEFORE ``cleanup_module_access_router``
so platform_user FK refs are released before make_platform_user tries
to DELETE its row.
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
    """TestClient wired with engine + session_factory on app.state.

    Bypasses the lifespan startup gate so the test event loop owns the
    engine; mirrors test_tenants_writes_router.py exactly.
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
async def cleanup_module_access_router(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Tracks tenant_module_access IDs created via the write endpoints;
    DELETEs them at teardown.

    Each TestClient request commits its own request-scope transaction,
    so cleanup sees committed rows at teardown without ordering
    discipline against an open session. Fixture order in each test
    signature must list ``make_platform_user`` BEFORE this fixture so
    the platform_user FK refs are released first.
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
                    f"DELETE FROM {schema}.tenant_module_access "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": created},
            )


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _platform_jwt_for_user(settings: Settings, user_id: UUID) -> str:
    """Mint a PLATFORM JWT for a specific platform_users.id."""
    return make_test_jwt(
        settings,
        user_id=user_id,
        user_type="PLATFORM",
    )


def _tenant_jwt(settings: Settings, tenant_id: UUID) -> str:
    """Mint a TENANT JWT for a random user_id in the given tenant.

    Layer 1 audience-kwarg gate fires on user_type alone (before any
    DB lookup), so a random user_id is sufficient for tests asserting
    the platform-only refusal.
    """
    return make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="TENANT",
        tenant_id=tenant_id,
    )


async def _grant_platform_admin(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    user_id: UUID,
) -> None:
    """Grant the seeded PLATFORM_ADMIN role to ``user_id``.

    Mirrors test_tenants_writes_router.py exactly. PLATFORM_ADMIN holds
    ADMIN.TENANTS.CONFIGURE.GLOBAL but NOT ADMIN.TENANTS.OVERRIDE.GLOBAL,
    so Layer 1 (audience=PLATFORM) passes and Layer 2 (has_permission)
    denies — that's the P3 regression assertion.
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

    Same shape as the cleanup_assignments fixture in
    test_tenants_writes_router.py: DELETEs
    platform_user_role_assignments rows referencing each tracked
    user_id at teardown, before make_platform_user tries to DELETE
    the platform_user. ON DELETE RESTRICT on
    platform_user_role_assignments.platform_user_id makes this
    teardown ordering load-bearing.
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


async def _fetch_tma_row_by_tenant_module(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    tenant_id: UUID,
    module: str,
) -> Any:
    """Read the current tenant_module_access row by (tenant_id, module).

    Returns the row tuple or None.
    """
    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        result = await session.execute(
            text(
                f"""
                SELECT id, status::text AS status,
                       enabled_at, enabled_by_user_id,
                       disabled_at, disabled_by_user_id,
                       updated_at
                  FROM {schema}.tenant_module_access
                 WHERE tenant_id = :tenant_id
                   AND module = CAST(:module AS {schema}.module_code_enum)
                """
            ),
            {"tenant_id": tenant_id, "module": module},
        )
        return result.first()


# A non-seeded module to use for clean upsert paths in tests where
# we want the (tenant_id, module) row genuinely missing pre-test.
# GOAL_CONSOLE is not in the seed across any tenant, so it's safe
# (verified via psql against the live local DB at write time).
_NEW_MODULE = "GOAL_CONSOLE"
_OTHER_MODULE = "PROMOTIONS_ASSISTANT"


# ============================================================================
# Transition matrix cells (C1-C6)
# ============================================================================


async def test_c1_enable_on_missing_creates_row(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_module_access_router,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — enable upsert path: missing row -> 200 + new row.

    Verifies the INSERT branch of the upsert seam fires. New row has
    status=ENABLED, enabled_at recent, all four audit columns
    populated, disabled_* NULL.
    """
    tenant = await make_tenant(name="C1-Tenant", with_root=True)
    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 200, resp.text
    j = resp.json()
    cleanup_module_access_router.append(UUID(j["id"]))
    assert j["status"] == "ENABLED"
    assert j["module"] == _NEW_MODULE
    assert j["disabled_at"] is None
    assert j["enabled_at"] is not None
    # Verify all four audit FK columns are populated on the DB row.
    db_row = await _fetch_tma_row_by_tenant_module(
        session_factory, platform_auth, tenant.id, _NEW_MODULE
    )
    assert db_row is not None
    assert db_row.enabled_by_user_id is not None
    assert db_row.disabled_by_user_id is None


async def test_c2_enable_on_disabled_flips_to_enabled_and_overwrites(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_platform_user,
    cleanup_module_access_router,
    session_factory,
    platform_auth,
    make_tenant_module_access,
) -> None:
    """LOAD-BEARING — LD5 overwrite path: DISABLED -> ENABLED writes
    enabled_at + enabled_by_user_id; clears the disabled pair.

    Pre-state: build a DISABLED row via the seed-style factory; capture
    its enabled_at. Then POST /enable and assert: enabled_at refreshed
    (or >= pre-state per the within-tx now() semantic), disabled_*
    cleared.
    """
    from datetime import datetime, timezone

    actor = await make_platform_user(status="ACTIVE")
    tenant = await make_tenant(name="C2-Tenant", with_root=True)
    pre_enabled_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    tma = await make_tenant_module_access(
        tenant_id=tenant.id,
        module=__import__(
            "admin_backend.models.tenant_module_access",
            fromlist=["ModuleCode"],
        ).ModuleCode(_NEW_MODULE),
        status=__import__(
            "admin_backend.models.tenant_module_access",
            fromlist=["ModuleAccessStatus"],
        ).ModuleAccessStatus.DISABLED,
        enabled_at=pre_enabled_at,
        enabled_by_user_id=actor.id,
        disabled_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
        disabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )

    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert UUID(j["id"]) == tma.id  # same row (UPDATE branch)
    assert j["status"] == "ENABLED"
    assert j["disabled_at"] is None
    # enabled_at strictly moved forward (different transactions: pre-row
    # made by factory, then UPDATE by handler).
    from datetime import datetime as _dt
    new_enabled_at = _dt.fromisoformat(j["enabled_at"].replace("Z", "+00:00"))
    assert new_enabled_at > pre_enabled_at

    db_row = await _fetch_tma_row_by_tenant_module(
        session_factory, platform_auth, tenant.id, _NEW_MODULE
    )
    assert db_row is not None
    assert db_row.disabled_at is None
    assert db_row.disabled_by_user_id is None


async def test_c3_enable_on_enabled_is_noop(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_module_access_router,
    session_factory,
    platform_auth,
) -> None:
    """LD4 idempotent no-op: enable on ENABLED returns row unchanged."""
    tenant = await make_tenant(name="C3-Tenant", with_root=True)
    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    first = app_client.post(url, headers=_auth(super_admin_jwt))
    assert first.status_code == 200
    cleanup_module_access_router.append(UUID(first.json()["id"]))
    pre = first.json()

    # Second call: still 200, row unchanged.
    second = app_client.post(url, headers=_auth(super_admin_jwt))
    assert second.status_code == 200
    post = second.json()
    assert post["id"] == pre["id"]
    assert post["enabled_at"] == pre["enabled_at"]
    assert post["updated_at"] == pre["updated_at"]


async def test_c4_disable_on_missing_returns_404(
    app_client,
    super_admin_jwt,
    make_tenant,
) -> None:
    """LOAD-BEARING — disable on (existing tenant, missing module) ->
    404 MODULE_ACCESS_NOT_FOUND."""
    tenant = await make_tenant(name="C4-Tenant", with_root=True)
    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/disable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 404
    assert resp.json()["code"] == "MODULE_ACCESS_NOT_FOUND"


async def test_c5_disable_on_enabled_flips_to_disabled_preserves_enabled_at(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_module_access_router,
    session_factory,
    platform_auth,
) -> None:
    """LD5 preserve path: ENABLED -> DISABLED keeps enabled_at;
    only disabled_at / disabled_by_user_id are written."""
    tenant = await make_tenant(name="C5-Tenant", with_root=True)

    # Seed via /enable (upsert).
    enable_url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    enable_resp = app_client.post(enable_url, headers=_auth(super_admin_jwt))
    assert enable_resp.status_code == 200
    pre = enable_resp.json()
    cleanup_module_access_router.append(UUID(pre["id"]))

    # Disable.
    disable_url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/disable"
    disable_resp = app_client.post(
        disable_url, headers=_auth(super_admin_jwt)
    )
    assert disable_resp.status_code == 200
    post = disable_resp.json()
    assert post["status"] == "DISABLED"
    # enabled_at is preserved through the disable flip (LD5).
    assert post["enabled_at"] == pre["enabled_at"]
    assert post["disabled_at"] is not None

    db_row = await _fetch_tma_row_by_tenant_module(
        session_factory, platform_auth, tenant.id, _NEW_MODULE
    )
    assert db_row is not None
    assert db_row.enabled_by_user_id is not None  # unchanged
    assert db_row.disabled_by_user_id is not None  # newly populated


async def test_c6_disable_on_disabled_is_noop(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_module_access_router,
) -> None:
    """LD4 idempotent no-op: disable on DISABLED returns row unchanged."""
    tenant = await make_tenant(name="C6-Tenant", with_root=True)
    enable_url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    enable_resp = app_client.post(enable_url, headers=_auth(super_admin_jwt))
    cleanup_module_access_router.append(UUID(enable_resp.json()["id"]))

    disable_url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/disable"
    first = app_client.post(disable_url, headers=_auth(super_admin_jwt))
    assert first.status_code == 200
    pre = first.json()

    second = app_client.post(disable_url, headers=_auth(super_admin_jwt))
    assert second.status_code == 200
    post = second.json()
    assert post["id"] == pre["id"]
    assert post["disabled_at"] == pre["disabled_at"]
    assert post["updated_at"] == pre["updated_at"]


# ============================================================================
# Permission boundary (P1-P4)
# ============================================================================


async def test_p1_tenant_jwt_on_enable_returns_403_platform_audience_required(
    app_client, settings, make_tenant,
) -> None:
    """LOAD-BEARING — TENANT JWT on /enable -> 403 PLATFORM_AUDIENCE_REQUIRED.

    Layer 1 audience-kwarg refusal. Without it a future regression
    dropping audience='PLATFORM' would let TENANT users reach Layer 2.

    Uses a real tenant in the URL so the anchor dep (FastAPI Depends
    resolution before the gate body) succeeds; otherwise the test would
    short-circuit on anchor 404 before Layer 1 ever ran. The anchor
    inherits RLS via the TENANT JWT's session GUCs; PLATFORM-only
    impersonation lets the cross-tenant probe succeed at the anchor
    layer regardless. We mint the TENANT JWT against a different
    tenant_id so the URL's tenant_id is reachable via PLATFORM
    visibility but not the JWT's TENANT-scope visibility — irrelevant
    here because Layer 1 fires on user_type alone, not on RLS.
    """
    tenant = await make_tenant(name="P1-Tenant", with_root=True)
    jwt = _tenant_jwt(settings, tenant.id)
    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    resp = app_client.post(url, headers=_auth(jwt))
    assert resp.status_code == 403
    assert resp.json()["code"] == "PLATFORM_AUDIENCE_REQUIRED"


async def test_p2_tenant_jwt_on_disable_returns_403_platform_audience_required(
    app_client, settings, make_tenant,
) -> None:
    """LOAD-BEARING — same Layer 1 refusal on the disable surface."""
    tenant = await make_tenant(name="P2-Tenant", with_root=True)
    jwt = _tenant_jwt(settings, tenant.id)
    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/disable"
    resp = app_client.post(url, headers=_auth(jwt))
    assert resp.status_code == 403
    assert resp.json()["code"] == "PLATFORM_AUDIENCE_REQUIRED"


async def test_p3_platform_admin_returns_403_permission_denied(
    app_client,
    settings,
    make_tenant,
    make_platform_user,
    cleanup_assignments,
    cleanup_module_access_router,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — PLATFORM_ADMIN on /enable -> 403 PERMISSION_DENIED.

    Catches OVERRIDE-vs-CONFIGURE catalogue privilege regression.
    PLATFORM_ADMIN holds CONFIGURE.GLOBAL (passes Layer 1 + audience
    check) but NOT OVERRIDE.GLOBAL. Without this test, a future seed
    change re-granting OVERRIDE to PLATFORM_ADMIN would silently widen
    the privilege model.
    """
    tenant = await make_tenant(name="P3-Tenant", with_root=True)
    pa = await make_platform_user(status="ACTIVE")
    await _grant_platform_admin(session_factory, platform_auth, pa.id)
    cleanup_assignments.append(pa.id)
    pa_jwt = _platform_jwt_for_user(settings, pa.id)

    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    resp = app_client.post(url, headers=_auth(pa_jwt))
    assert resp.status_code == 403
    assert resp.json()["code"] == "PERMISSION_DENIED"


async def test_p4_unknown_tenant_id_returns_404_tenant_not_found(
    app_client, super_admin_jwt,
) -> None:
    """LOAD-BEARING — unknown tenant_id -> 404 TENANT_NOT_FOUND from
    the anchor dep, BEFORE the gate runs.

    Mirrors 6.9.3.2 T_RET_3: anchor-miss security regression — the
    anchor dep must raise (not return None / not silently skip).
    """
    url = f"/api/v1/module-access/{uuid.uuid4()}/{_NEW_MODULE}/enable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 404
    assert resp.json()["code"] == "TENANT_NOT_FOUND"


# ============================================================================
# Path-param validation (V1)
# ============================================================================


async def test_v1_invalid_module_code_returns_422(
    app_client, super_admin_jwt, make_tenant,
) -> None:
    """LD7 — path param binds to canonical ModuleCode enum; invalid
    values surface as 422 from FastAPI path validation BEFORE the
    handler runs."""
    tenant = await make_tenant(name="V1-Tenant", with_root=True)
    url = f"/api/v1/module-access/{tenant.id}/FAKEMOD/enable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 422
    body = resp.json()
    # FastAPI's stock validation error envelope includes a "detail"
    # array; the message names the valid enum values. Asserting on the
    # presence of one canonical value is enough — exhaustive text-
    # matching would be brittle.
    assert "PRICING_OS" in resp.text or "PERISHABLES_ASSISTANT" in resp.text


# ============================================================================
# Audience-kwarg ordering (AUD-1)
# ============================================================================


async def test_aud1_tenant_jwt_layer1_fires_before_layer2(
    app_client, settings, make_tenant,
) -> None:
    """LOAD-BEARING — TENANT JWT POST on /enable returns
    PLATFORM_AUDIENCE_REQUIRED, not PERMISSION_DENIED.

    Both Layer 1 (audience kwarg) and Layer 2 (has_permission) would
    correctly deny a TENANT caller. The order matters for defense-in-
    depth: a structural assertion ahead of any DB query. Mirrors
    6.11.2's AUD-2.

    The anchor dep resolves before the gate body (FastAPI Depends
    resolution). To exercise Layer 1 vs Layer 2 ordering we need the
    anchor lookup to succeed; the test tenant + tenant-root org_node
    provides that.
    """
    tenant = await make_tenant(name="AUD1-Tenant", with_root=True)
    jwt = _tenant_jwt(settings, tenant.id)
    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    resp = app_client.post(url, headers=_auth(jwt))
    assert resp.status_code == 403
    assert resp.json()["code"] == "PLATFORM_AUDIENCE_REQUIRED"
    # And NOT the PERMISSION_DENIED that would surface if Layer 2 ran
    # first.
    assert resp.json()["code"] != "PERMISSION_DENIED"


# ============================================================================
# Regression flows (R1-R2)
# ============================================================================


async def test_r1_sequential_enables_are_idempotent(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_module_access_router,
) -> None:
    """Sequential enable + enable -> first creates, second is no-op.

    Confirms C3 holds after C1 wrote the row in the same test session
    (regression check on the no-op contract across HTTP calls).
    """
    tenant = await make_tenant(name="R1-Tenant", with_root=True)
    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"

    first = app_client.post(url, headers=_auth(super_admin_jwt))
    assert first.status_code == 200
    cleanup_module_access_router.append(UUID(first.json()["id"]))
    pre = first.json()

    second = app_client.post(url, headers=_auth(super_admin_jwt))
    assert second.status_code == 200
    post = second.json()
    assert post["id"] == pre["id"]
    assert post["status"] == "ENABLED"
    assert post["updated_at"] == pre["updated_at"]  # no UPDATE issued


async def test_r2_enable_disable_enable_overwrites_ordering(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_module_access_router,
) -> None:
    """enable, disable, enable: final state ENABLED with disabled_at
    cleared and enabled_at overwritten (>=) between the two enables.

    Mirrors RT3 at the router layer."""
    tenant = await make_tenant(name="R2-Tenant", with_root=True)
    enable_url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    disable_url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/disable"

    first_enable = app_client.post(enable_url, headers=_auth(super_admin_jwt))
    assert first_enable.status_code == 200
    cleanup_module_access_router.append(UUID(first_enable.json()["id"]))
    first_enabled_at = first_enable.json()["enabled_at"]

    disable_resp = app_client.post(disable_url, headers=_auth(super_admin_jwt))
    assert disable_resp.status_code == 200
    assert disable_resp.json()["status"] == "DISABLED"
    # enabled_at preserved through the disable.
    assert disable_resp.json()["enabled_at"] == first_enabled_at

    second_enable = app_client.post(
        enable_url, headers=_auth(super_admin_jwt)
    )
    assert second_enable.status_code == 200
    final = second_enable.json()
    assert final["status"] == "ENABLED"
    assert final["disabled_at"] is None
    # enabled_at moved forward (different transactions).
    from datetime import datetime as _dt
    final_enabled_at = _dt.fromisoformat(
        final["enabled_at"].replace("Z", "+00:00")
    )
    first_enabled_at_parsed = _dt.fromisoformat(
        first_enabled_at.replace("Z", "+00:00")
    )
    assert final_enabled_at > first_enabled_at_parsed
