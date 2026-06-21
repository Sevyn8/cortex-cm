"""Step 6.16.5 LD17: GET /api/v1/audit/activities resource_type filter.

The list endpoint accepts an optional ``resource_type: str | None``
query parameter. AND-composed with existing filters. Applied to both
UNION branches at the repo SQL builder. Unknown values produce 0 rows
naturally (no 422; open string vocabulary).

5 tests. LOAD-BEARING: RTF1, RTF3, RTF4.
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


async def test_rtf1_filter_by_tenant_user_returns_only_matching(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """LOAD-BEARING — ``resource_type=TENANT_USER`` returns only rows
    with that resource_type; rows with other resource_types are
    excluded.
    """
    tenant = await make_tenant(name="RTF1-Tenant")
    tu_row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="RTF1-Tenant",
        resource_type="TENANT_USER", action="CREATE",
        action_label="Created",
    )
    other_row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="RTF1-Tenant",
        resource_type="STORE", action="CREATE",
        action_label="Created",
    )
    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}"
        "&resource_type=TENANT_USER",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    ids = [r["id"] for r in resp.json()["items"]]
    assert str(tu_row.id) in ids
    assert str(other_row.id) not in ids


async def test_rtf2_filter_by_store_returns_only_store_rows(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """``resource_type=STORE`` returns only STORE rows."""
    tenant = await make_tenant(name="RTF2-Tenant")
    s_row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="RTF2-Tenant",
        resource_type="STORE",
    )
    other_row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="RTF2-Tenant",
        resource_type="ORG_NODE",
    )
    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}"
        "&resource_type=STORE",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    ids = [r["id"] for r in resp.json()["items"]]
    assert str(s_row.id) in ids
    assert str(other_row.id) not in ids


async def test_rtf3_unknown_value_returns_zero_rows_no_422(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """LOAD-BEARING — open-vocabulary filter. Unknown values produce
    0 rows naturally (the WHERE clause matches no rows); no 422
    enum-validation error.
    """
    tenant = await make_tenant(name="RTF3-Tenant")
    await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="RTF3-Tenant",
        resource_type="TENANT_USER",
    )
    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}"
        "&resource_type=NONSENSE_RESOURCE",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text  # NOT 422
    items = resp.json()["items"]
    assert items == []


async def test_rtf4_and_composes_with_status_filter(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """LOAD-BEARING — resource_type filter AND-composes with the
    existing status filter; only rows matching BOTH are returned.
    """
    tenant = await make_tenant(name="RTF4-Tenant")
    # Match both filters.
    match_row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="RTF4-Tenant",
        resource_type="ROLE",
        result_type="PERMISSION_DENIED",
    )
    # Same resource_type, different status.
    wrong_status = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="RTF4-Tenant",
        resource_type="ROLE",
        result_type="SUCCESS",
    )
    # Different resource_type, matching status.
    wrong_type = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="RTF4-Tenant",
        resource_type="STORE",
        result_type="PERMISSION_DENIED",
    )

    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}"
        "&resource_type=ROLE&status=PERMISSION_DENIED",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    ids = [r["id"] for r in resp.json()["items"]]
    assert str(match_row.id) in ids
    assert str(wrong_status.id) not in ids
    assert str(wrong_type.id) not in ids


async def test_rtf5_tenant_caller_filter_still_rls_scoped(
    app_client,
    make_tenant,
    make_tenant_activity_audit_log,
    make_platform_activity_audit_log,
    tenant_owner_jwt_factory,
) -> None:
    """TENANT caller filtering by resource_type stays RLS-scoped.

    Filtering by ``TENANT_USER`` returns own-tenant rows. Filtering by
    ``ROLE`` returns 0 rows even if the platform table has matching
    rows (TENANT caller never reaches platform table per 6.16.3 LD13;
    RLS does its job on the tenant table; ROLE rows live on the
    platform table).
    """
    tenant = await make_tenant(name="RTF5-Tenant", with_root=True)
    own_tu = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="RTF5-Tenant",
        resource_type="TENANT_USER",
    )
    # Place a ROLE row on the platform table; tenant caller MUST NOT see it.
    await make_platform_activity_audit_log(
        tenant_id=tenant.id, tenant_name="RTF5-Tenant",
        resource_type="ROLE",
    )

    tjwt = await tenant_owner_jwt_factory(
        tenant.id,
        with_grants=[("ADMIN", "AUDIT_LOG", "VIEW", "TENANT")],
    )

    # First filter: TENANT_USER. Own tenant_user row returned.
    resp = app_client.get(
        "/api/v1/audit/activities?resource_type=TENANT_USER",
        headers=_auth(tjwt),
    )
    assert resp.status_code == 200, resp.text
    ids = [r["id"] for r in resp.json()["items"]]
    assert str(own_tu.id) in ids

    # Second filter: ROLE. Empty (ROLE row lives on platform table;
    # tenant caller never queries that table per LD13).
    resp = app_client.get(
        "/api/v1/audit/activities?resource_type=ROLE",
        headers=_auth(tjwt),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []
