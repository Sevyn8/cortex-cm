"""Step 6.16.5 : audit emission for module-access enable / disable.

Per-endpoint success + failure coverage. Each test exercises the
real HTTP layer and queries the audit table for the matching row,
asserting result_type + payload shape.

Locked decisions:

  - LD2: ENABLE / DISABLE use a single action code each; before/after
    status distinguishes first-time INSERT (``before=None``) from
    re-enable (``before='DISABLED'``). No-op idempotent paths
    (enable-on-ENABLED, disable-on-DISABLED) emit ZERO audit rows
    (closes FN-AB-42).
  - LD9: resource_label resolves from ``core.lookups`` keyed by
    ``(list_name='module_code', code=:mc)``; e.g. ``GOAL_CONSOLE``
    resolves to ``"Goal Console"``.
  - LD13: 404 ``MODULE_ACCESS_NOT_FOUND`` on disable-of-missing emits
    NO audit row (anchor-404 not audited).

12 tests (MS1-MS7 success-path; MF1-MF5 failure-path). LOAD-BEARING:
MS1, MS2, MS3, MS5, MS6, MF1, MF3.
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
# Test app + helpers (mirror test_audit_emission_tenants.py)
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


def _tenant_jwt(settings: Settings, tenant_id: UUID) -> str:
    return make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="TENANT",
        tenant_id=tenant_id,
    )


# A non-seeded module ensures we can exercise the missing-row branch
# cleanly across runs (mirrors test_module_access_writes_router.py).
_NEW_MODULE = "GOAL_CONSOLE"
_NEW_MODULE_LABEL = "Goal Console"  # from core.lookups, list_name='module_code'


@pytest_asyncio.fixture
async def cleanup_tma_for_audit(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Tracks tenant_module_access row ids created during the test.

    DELETEs them at teardown after clearing referencing audit rows.
    The make_tenant fixture's teardown chain handles tenant-side audit
    rows; this fixture covers the tenant_module_access rows produced
    by the test's POST calls.
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
                    f"DELETE FROM {schema}.tenant_module_access "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": tracked},
            )


async def _fetch_audit_rows(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    table: str,
    tenant_id: UUID,
    resource_type: str | None = None,
) -> list[dict[str, Any]]:
    schema = get_settings().db_schema
    extra = (
        " AND resource_type = :rt" if resource_type is not None else ""
    )
    params: dict[str, Any] = {"tenant_id": tenant_id}
    if resource_type is not None:
        params["rt"] = resource_type
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"""
                SELECT id, action, action_label, resource_type, resource_id,
                       resource_label, result_type::text AS result_type,
                       tenant_id, tenant_name, request_id, details,
                       actor_user_type::text AS actor_user_type
                  FROM {schema}.{table}
                 WHERE tenant_id = :tenant_id
                       {extra}
                 ORDER BY timestamp ASC, id ASC
                """
            ),
            params,
        )
        return [dict(row) for row in result.mappings()]
    raise AssertionError("unreachable")  # pragma: no cover


# ============================================================================
# MS1 - MS7 : success-path emission
# ============================================================================


async def test_ms1_enable_on_missing_emits_first_time_with_before_null(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_tma_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — first-time INSERT path: ``before.status = null``,
    ``after.status = 'ENABLED'``, action=ENABLE, resource_type=MODULE_ACCESS.
    """
    tenant = await make_tenant(name="MS1-Tenant", with_root=True)
    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 200, resp.text
    j = resp.json()
    cleanup_tma_for_audit.append(UUID(j["id"]))

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant.id,
        resource_type="MODULE_ACCESS",
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "ENABLE"
    assert r["action_label"] == "Enabled"
    assert r["resource_type"] == "MODULE_ACCESS"
    assert UUID(str(r["resource_id"])) == UUID(j["id"])
    assert r["result_type"] == "SUCCESS"
    assert r["details"]["before"]["status"] is None
    assert r["details"]["after"]["status"] == "ENABLED"


async def test_ms2_enable_on_disabled_emits_before_disabled_after_enabled(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_platform_user,
    cleanup_tma_for_audit,
    make_tenant_module_access,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — re-enable path: ``before='DISABLED' -> after='ENABLED'``.
    """
    from datetime import datetime, timezone

    from admin_backend.models.tenant_module_access import (
        ModuleAccessStatus,
        ModuleCode,
    )

    actor = await make_platform_user(status="ACTIVE")
    tenant = await make_tenant(name="MS2-Tenant", with_root=True)
    tma = await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode(_NEW_MODULE),
        status=ModuleAccessStatus.DISABLED,
        enabled_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        enabled_by_user_id=actor.id,
        disabled_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
        disabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )

    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 200, resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant.id,
        resource_type="MODULE_ACCESS",
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "ENABLE"
    assert UUID(str(r["resource_id"])) == tma.id
    assert r["details"]["before"]["status"] == "DISABLED"
    assert r["details"]["after"]["status"] == "ENABLED"


async def test_ms3_enable_on_enabled_is_no_op_emits_zero_rows(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_platform_user,
    make_tenant_module_access,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — closes FN-AB-42: idempotent no-op emits zero
    rows. The DB state was already ENABLED; the handler treats it as
    a no-op return, and per LD2 NO audit row fires.
    """
    from datetime import datetime, timezone

    from admin_backend.models.tenant_module_access import (
        ModuleAccessStatus,
        ModuleCode,
    )

    actor = await make_platform_user(status="ACTIVE")
    tenant = await make_tenant(name="MS3-Tenant", with_root=True)
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode(_NEW_MODULE),
        status=ModuleAccessStatus.ENABLED,
        enabled_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )

    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 200, resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant.id,
        resource_type="MODULE_ACCESS",
    )
    assert rows == []


async def test_ms4_disable_on_enabled_emits_before_enabled_after_disabled(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_platform_user,
    make_tenant_module_access,
    session_factory,
    platform_auth,
) -> None:
    """Disable from ENABLED state emits a DISABLE audit row."""
    from datetime import datetime, timezone

    from admin_backend.models.tenant_module_access import (
        ModuleAccessStatus,
        ModuleCode,
    )

    actor = await make_platform_user(status="ACTIVE")
    tenant = await make_tenant(name="MS4-Tenant", with_root=True)
    tma = await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode(_NEW_MODULE),
        status=ModuleAccessStatus.ENABLED,
        enabled_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )

    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/disable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 200, resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant.id,
        resource_type="MODULE_ACCESS",
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "DISABLE"
    assert r["action_label"] == "Disabled"
    assert UUID(str(r["resource_id"])) == tma.id
    assert r["details"]["before"]["status"] == "ENABLED"
    assert r["details"]["after"]["status"] == "DISABLED"


async def test_ms5_disable_on_disabled_is_no_op_emits_zero_rows(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_platform_user,
    make_tenant_module_access,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — closes FN-AB-42 on the disable side: disable
    on already-DISABLED emits ZERO audit rows.
    """
    from datetime import datetime, timezone

    from admin_backend.models.tenant_module_access import (
        ModuleAccessStatus,
        ModuleCode,
    )

    actor = await make_platform_user(status="ACTIVE")
    tenant = await make_tenant(name="MS5-Tenant", with_root=True)
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode(_NEW_MODULE),
        status=ModuleAccessStatus.DISABLED,
        enabled_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        enabled_by_user_id=actor.id,
        disabled_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
        disabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )

    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/disable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 200, resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant.id,
        resource_type="MODULE_ACCESS",
    )
    assert rows == []


async def test_ms6_resource_label_resolves_from_lookups_display_name(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_tma_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — LD9: module's display label from core.lookups.

    e.g. ``GOAL_CONSOLE`` resolves to ``"Goal Console"`` (the
    display_name seeded at Step 3.4.5 / 6.7), not the bare enum
    string.
    """
    tenant = await make_tenant(name="MS6-Tenant", with_root=True)
    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 200, resp.text
    cleanup_tma_for_audit.append(UUID(resp.json()["id"]))

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant.id,
        resource_type="MODULE_ACCESS",
    )
    assert len(rows) == 1
    assert rows[0]["resource_label"] == _NEW_MODULE_LABEL


async def test_ms7_audit_row_request_id_matches_response_header(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_tma_for_audit,
    session_factory,
    platform_auth,
) -> None:
    """The audit row's ``request_id`` matches the response's
    ``X-Request-Id`` header so log lines can correlate.
    """
    tenant = await make_tenant(name="MS7-Tenant", with_root=True)
    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 200, resp.text
    cleanup_tma_for_audit.append(UUID(resp.json()["id"]))
    header_request_id = resp.headers.get("X-Request-Id")
    assert header_request_id is not None

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant.id,
        resource_type="MODULE_ACCESS",
    )
    assert len(rows) == 1
    assert str(rows[0]["request_id"]) == header_request_id


# ============================================================================
# MF1 - MF5 : failure-path emission
# ============================================================================


async def test_mf1_enable_with_tenant_jwt_emits_permission_denied(
    app_client,
    settings,
    make_tenant,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — TENANT JWT on PLATFORM-audience write produces 403
    PLATFORM_AUDIENCE_REQUIRED and a PERMISSION_DENIED audit row.
    """
    tenant = await make_tenant(name="MF1-Tenant", with_root=True)
    jwt = _tenant_jwt(settings, tenant.id)
    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/enable"
    resp = app_client.post(url, headers=_auth(jwt))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "PLATFORM_AUDIENCE_REQUIRED"

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant.id,
        resource_type="MODULE_ACCESS",
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "ENABLE"
    assert r["result_type"] == "PERMISSION_DENIED"
    # caller_audience falls back to auth.user_type via 6.16.4 ext.
    assert r["details"]["caller_audience"] == "TENANT"
    # Per ck_tenant_activity_audit_logs_resource_pair, resource_id and
    # resource_label must be both-NULL or both-NOT-NULL. The tma row
    # doesn't exist yet on this fresh tenant (no seed for GOAL_CONSOLE),
    # so both stay NULL on the failure row.
    assert r["resource_id"] is None
    assert r["resource_label"] is None


async def test_mf2_disable_with_tenant_jwt_emits_permission_denied(
    app_client,
    settings,
    make_tenant,
    session_factory,
    platform_auth,
) -> None:
    """TENANT JWT on disable -> 403 + DISABLE audit row."""
    tenant = await make_tenant(name="MF2-Tenant", with_root=True)
    jwt = _tenant_jwt(settings, tenant.id)
    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/disable"
    resp = app_client.post(url, headers=_auth(jwt))
    assert resp.status_code == 403, resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant.id,
        resource_type="MODULE_ACCESS",
    )
    assert len(rows) == 1
    assert rows[0]["action"] == "DISABLE"
    assert rows[0]["result_type"] == "PERMISSION_DENIED"


async def test_mf3_disable_on_missing_row_emits_zero_rows(
    app_client,
    super_admin_jwt,
    make_tenant,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — LD13 anchor-404: disable on missing row returns
    404 MODULE_ACCESS_NOT_FOUND. The failure handler skips emission
    because the resource does not exist.
    """
    tenant = await make_tenant(name="MF3-Tenant", with_root=True)
    url = f"/api/v1/module-access/{tenant.id}/{_NEW_MODULE}/disable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "MODULE_ACCESS_NOT_FOUND"

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant.id,
        resource_type="MODULE_ACCESS",
    )
    assert rows == []


async def test_mf4_enable_with_invalid_module_code_path_param_no_audit_row(
    app_client,
    super_admin_jwt,
    make_tenant,
    session_factory,
    platform_auth,
) -> None:
    """Invalid module_code in the path -> 422 from FastAPI's Pydantic
    enum validator BEFORE the gate runs. Per FN-AB-63 the
    Pydantic-direct 422 path bypasses the project's
    AdminBackendError handler; audit emission for this path is
    deferred (no audit row asserted here; documented inline).
    """
    tenant = await make_tenant(name="MF4-Tenant", with_root=True)
    url = f"/api/v1/module-access/{tenant.id}/NOT_A_MODULE/enable"
    resp = app_client.post(url, headers=_auth(super_admin_jwt))
    assert resp.status_code == 422

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant.id,
        resource_type="MODULE_ACCESS",
    )
    # Deferred per FN-AB-63: Pydantic-direct 422 doesn't reach the
    # audit-emitting handler today.
    assert rows == []


async def test_mf5_failure_row_resource_label_resolves_when_tma_row_exists(
    app_client,
    settings,
    make_tenant,
    make_platform_user,
    make_tenant_module_access,
    session_factory,
    platform_auth,
) -> None:
    """When a tenant_module_access row exists, failure-path emission
    resolves both ``resource_id`` (via lookup) and ``resource_label``
    (via core.lookups display_name) per LD9. Both populated satisfies
    ``ck_*_resource_pair``.

    Without a pre-existing tma row (MF1 / MF2), the resource pair
    stays NULL per the constraint; only the SUCCESS path can populate
    both (because the success path creates / updates the row in the
    same transaction).
    """
    from datetime import datetime, timezone

    from admin_backend.models.tenant_module_access import (
        ModuleAccessStatus,
        ModuleCode,
    )

    actor = await make_platform_user(status="ACTIVE")
    tenant = await make_tenant(name="MF5-Tenant", with_root=True)
    tma = await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.PRICING_OS,
        status=ModuleAccessStatus.ENABLED,
        enabled_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )

    jwt = _tenant_jwt(settings, tenant.id)
    url = f"/api/v1/module-access/{tenant.id}/PRICING_OS/enable"
    resp = app_client.post(url, headers=_auth(jwt))
    assert resp.status_code == 403, resp.text

    rows = await _fetch_audit_rows(
        session_factory,
        platform_auth,
        table="tenant_activity_audit_logs",
        tenant_id=tenant.id,
        resource_type="MODULE_ACCESS",
    )
    assert len(rows) == 1
    r = rows[0]
    # Both populated since the tma row exists.
    assert UUID(str(r["resource_id"])) == tma.id
    assert r["resource_label"] == "Pricing OS"
