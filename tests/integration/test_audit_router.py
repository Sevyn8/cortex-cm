"""Integration tests for the Step 6.16.3 audit log read endpoints.

Coverage:

  L1-L15: GET /api/v1/audit/activities (list)
          - L1-L9 audience + filter combinations
          - L10-L12 date range + search composition
          - L13-L15 cursor + limit boundary validation
  D1-D7:  GET /api/v1/audit/activities/{audit_row_id} (detail)
  P1-P3:  permission / gate tests

LOAD-BEARING (8 of ~25):
  L1, L2, L3, L4: list audience + pagination contract.
  L13:           malformed cursor -> 422 INVALID_CURSOR.
  D1:            detail returns the full row.
  D4, D5:        cross-tenant probes return 404 (RLS-as-404 +
                 anti-information-disclosure).
  P2, P3:        permission denial paths.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta, timezone
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
    """TestClient wired with engine + session_factory on app.state."""
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


def _bearer(jwt: str) -> dict[str, str]:
    return _auth(jwt)


@pytest_asyncio.fixture
async def make_no_audit_grant_platform_jwt(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    make_platform_user,
    make_role,
    make_platform_user_role_assignment,
) -> AsyncIterator[str]:
    """Mint a PLATFORM JWT whose user has a custom role with NO audit
    grant.

    P2 LOAD-BEARING test fixture. The seed grants ``.VIEW.GLOBAL`` to all
    3 platform roles (SUPER_ADMIN, PLATFORM_ADMIN, SUPPORT_ADMIN), so we
    can't reach 403 via a real seeded user. The custom-role fixture
    creates a fresh PLATFORM-audience role with NO role_permissions
    grants, then a platform_user assigned to it. has_permission returns
    False -> Layer 2 fires -> 403 PERMISSION_DENIED.
    """
    pu = await make_platform_user(status="ACTIVE")
    role = await make_role(audience="PLATFORM")  # no grants
    await make_platform_user_role_assignment(
        platform_user_id=pu.id,
        role_id=role.id,
        status="ACTIVE",
    )
    jwt = make_test_jwt(settings, user_id=pu.id, user_type="PLATFORM")
    yield jwt


# ============================================================================
# List endpoint tests (L1-L15)
# ============================================================================


async def test_l1_platform_list_merged_stream_with_default_limit(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
    make_platform_activity_audit_log,
) -> None:
    """LOAD-BEARING. PLATFORM caller sees merged stream from both tables."""
    tenant = await make_tenant(name="L1-Merged")
    t_row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="L1-Merged"
    )
    p_row = await make_platform_activity_audit_log(
        tenant_id=tenant.id, tenant_name="L1-Merged"
    )

    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    assert "pagination" in body
    ids = [r["id"] for r in body["items"]]
    assert str(t_row.id) in ids
    assert str(p_row.id) in ids
    scopes = {r["scope"] for r in body["items"]}
    assert "PLATFORM" in scopes
    assert "TENANT" in scopes


async def test_l2_tenant_list_scoped_to_own_rows(
    app_client,
    make_tenant,
    make_tenant_activity_audit_log,
    make_platform_activity_audit_log,
    tenant_owner_jwt_factory,
) -> None:
    """LOAD-BEARING. TENANT caller sees only tenant-table rows (RLS)."""
    tenant = await make_tenant(name="L2-OwnRows", with_root=True)
    t_row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="L2-OwnRows"
    )
    # Platform-table row also tied to this tenant; tenant caller MUST NOT see it.
    p_row = await make_platform_activity_audit_log(
        tenant_id=tenant.id, tenant_name="L2-OwnRows"
    )

    tjwt = await tenant_owner_jwt_factory(
        tenant.id,
        with_grants=[("ADMIN", "AUDIT_LOG", "VIEW", "TENANT")],
    )
    resp = app_client.get(
        "/api/v1/audit/activities",
        headers=_auth(tjwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [r["id"] for r in body["items"]]
    assert str(t_row.id) in ids
    assert str(p_row.id) not in ids
    # Every returned row must have scope='TENANT' (no PLATFORM rows leaked).
    scopes = {r["scope"] for r in body["items"]}
    assert scopes <= {"TENANT"}


async def test_l3_limit_and_pagination_metadata(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """LOAD-BEARING. limit + cursor + has_more populate correctly."""
    tenant = await make_tenant(name="L3-Limit")
    base = datetime.now(tz=timezone.utc)
    for n in range(15):
        await make_tenant_activity_audit_log(
            tenant_id=tenant.id,
            tenant_name="L3-Limit",
            timestamp=base - timedelta(minutes=n),
        )

    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 10
    assert body["pagination"]["has_more"] is True
    assert body["pagination"]["next_cursor"] is not None
    assert body["pagination"]["limit"] == 10


async def test_l4_cursor_round_trip(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """LOAD-BEARING. Page 1 -> next_cursor -> Page 2 yields no overlap."""
    tenant = await make_tenant(name="L4-Cursor")
    base = datetime.now(tz=timezone.utc)
    created = []
    for n in range(8):
        row = await make_tenant_activity_audit_log(
            tenant_id=tenant.id,
            tenant_name="L4-Cursor",
            timestamp=base - timedelta(minutes=n),
        )
        created.append(row)

    page1 = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}&limit=3",
        headers=_auth(super_admin_jwt),
    )
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["items"]) == 3
    cursor = body1["pagination"]["next_cursor"]
    assert cursor is not None

    page2 = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}&limit=3&cursor={cursor}",
        headers=_auth(super_admin_jwt),
    )
    assert page2.status_code == 200
    body2 = page2.json()
    assert len(body2["items"]) == 3
    ids1 = [r["id"] for r in body1["items"]]
    ids2 = [r["id"] for r in body2["items"]]
    assert set(ids1).isdisjoint(set(ids2))

    # Chronological order preserved across pages: every page2
    # timestamp is older than every page1 timestamp.
    last_page1 = body1["items"][-1]["timestamp"]
    first_page2 = body2["items"][0]["timestamp"]
    assert first_page2 < last_page1


async def test_l5_status_filter(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """?status=PERMISSION_DENIED returns only that result_type."""
    tenant = await make_tenant(name="L5-StatusFilter")
    success_row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="L5-StatusFilter",
        result_type="SUCCESS",
        result_label="Success",
    )
    denied_row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="L5-StatusFilter",
        result_type="PERMISSION_DENIED",
        result_label="Permission Denied",
    )

    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}"
        f"&status=PERMISSION_DENIED",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    ids = [r["id"] for r in resp.json()["items"]]
    assert str(denied_row.id) in ids
    assert str(success_row.id) not in ids


async def test_l6_tenant_id_filter(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """?tenant_id=X narrows the tenant branch; platform branch returns
    zero rows scoped to that tenant (only tenant-creation rows would
    populate platform-table tenant_id)."""
    tenant_a = await make_tenant(name="L6-TenantA")
    tenant_b = await make_tenant(name="L6-TenantB")
    row_a = await make_tenant_activity_audit_log(
        tenant_id=tenant_a.id, tenant_name="L6-TenantA"
    )
    row_b = await make_tenant_activity_audit_log(
        tenant_id=tenant_b.id, tenant_name="L6-TenantB"
    )

    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant_a.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["items"]]
    assert str(row_a.id) in ids
    assert str(row_b.id) not in ids


async def test_l7_tenant_jwt_tenant_id_filter_silently_ignored(
    app_client,
    make_tenant,
    make_tenant_activity_audit_log,
    tenant_owner_jwt_factory,
) -> None:
    """TENANT caller's tenant_id filter is silently ignored (RLS enforces)."""
    tenant_a = await make_tenant(name="L7-CallerTenant", with_root=True)
    tenant_b = await make_tenant(name="L7-OtherTenant")
    row_a = await make_tenant_activity_audit_log(
        tenant_id=tenant_a.id, tenant_name="L7-CallerTenant"
    )
    row_b = await make_tenant_activity_audit_log(
        tenant_id=tenant_b.id, tenant_name="L7-OtherTenant"
    )

    tjwt = await tenant_owner_jwt_factory(
        tenant_a.id,
        with_grants=[("ADMIN", "AUDIT_LOG", "VIEW", "TENANT")],
    )
    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant_b.id}",
        headers=_auth(tjwt),
    )
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["items"]]
    # The caller passed tenant_b's id but RLS keeps them in tenant_a's
    # scope. They see neither row_b (other tenant) nor a forbidden
    # leak. row_a may or may not appear depending on what other rows
    # exist for tenant_a; at minimum the cross-tenant row is invisible.
    assert str(row_b.id) not in ids


async def test_l8_scope_filter_platform_only(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
    make_platform_activity_audit_log,
) -> None:
    """?scope=PLATFORM returns only platform-branch rows."""
    tenant = await make_tenant(name="L8-ScopePlatform")
    t_row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="L8-ScopePlatform"
    )
    p_row = await make_platform_activity_audit_log(
        tenant_id=tenant.id, tenant_name="L8-ScopePlatform"
    )

    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}&scope=PLATFORM",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = [r["id"] for r in body["items"]]
    assert str(p_row.id) in ids
    assert str(t_row.id) not in ids
    scopes = {r["scope"] for r in body["items"]}
    assert scopes <= {"PLATFORM"}


async def test_l9_scope_filter_tenant_only(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
    make_platform_activity_audit_log,
) -> None:
    """?scope=TENANT returns only tenant-branch rows."""
    tenant = await make_tenant(name="L9-ScopeTenant")
    t_row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="L9-ScopeTenant"
    )
    p_row = await make_platform_activity_audit_log(
        tenant_id=tenant.id, tenant_name="L9-ScopeTenant"
    )

    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}&scope=TENANT",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = [r["id"] for r in body["items"]]
    assert str(t_row.id) in ids
    assert str(p_row.id) not in ids


async def test_l10_date_range_filter(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """?from=X&to=Y brackets the page chronologically."""
    tenant = await make_tenant(name="L10-DateRange")
    base = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    rows = []
    for n in range(5):
        ts = base + timedelta(hours=n)
        rows.append(
            await make_tenant_activity_audit_log(
                tenant_id=tenant.id,
                tenant_name="L10-DateRange",
                timestamp=ts,
            )
        )

    # URL-encode the ISO-8601 strings; the "+" in "+00:00" decodes to
    # space without encoding and fails datetime parsing at the
    # FastAPI layer.
    from urllib.parse import quote
    from_ts = quote((base + timedelta(hours=1)).isoformat())
    to_ts = quote((base + timedelta(hours=3)).isoformat())
    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}"
        f"&from={from_ts}&to={to_ts}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["items"]}
    our_ids_in_window = {str(rows[1].id), str(rows[2].id), str(rows[3].id)}
    assert our_ids_in_window <= ids
    # rows[0] and rows[4] are outside the window.
    assert str(rows[0].id) not in ids
    assert str(rows[4].id) not in ids


async def test_l11_search_filter(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """?search=<term> matches across 4 columns via ILIKE."""
    tenant = await make_tenant(name="L11-Search")
    matching = await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="L11-Search",
        actor_display_name="Marcus T",
    )
    non_matching = await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="L11-Search",
        actor_display_name="Alice O",
    )

    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}&search=marcus",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["items"]]
    assert str(matching.id) in ids
    assert str(non_matching.id) not in ids


async def test_l12_search_and_tenant_id_compose_via_and(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """search AND tenant_id intersect cleanly."""
    tenant_a = await make_tenant(name="L12-AlphaTenant")
    tenant_b = await make_tenant(name="L12-BetaTenant")
    row_in_a_matching = await make_tenant_activity_audit_log(
        tenant_id=tenant_a.id,
        tenant_name="L12-AlphaTenant",
        actor_display_name="Charlie F",
    )
    row_in_b_matching = await make_tenant_activity_audit_log(
        tenant_id=tenant_b.id,
        tenant_name="L12-BetaTenant",
        actor_display_name="Charlie G",
    )

    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant_a.id}&search=charlie",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["items"]]
    assert str(row_in_a_matching.id) in ids
    assert str(row_in_b_matching.id) not in ids


async def test_l13_malformed_cursor_returns_422(
    app_client,
    super_admin_jwt,
) -> None:
    """LOAD-BEARING. Malformed cursor returns 422 INVALID_CURSOR."""
    # A base64 string that decodes to invalid JSON.
    import base64
    bad_cursor = base64.urlsafe_b64encode(b"definitely not json").decode("ascii")
    resp = app_client.get(
        f"/api/v1/audit/activities?cursor={bad_cursor}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "INVALID_CURSOR"


async def test_l14_limit_above_max_returns_422(
    app_client,
    super_admin_jwt,
) -> None:
    """?limit=500 -> 422 (FastAPI bounds check; max=200)."""
    resp = app_client.get(
        "/api/v1/audit/activities?limit=500",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422


async def test_l15_limit_below_min_returns_422(
    app_client,
    super_admin_jwt,
) -> None:
    """?limit=0 -> 422 (FastAPI bounds check; min=1)."""
    resp = app_client.get(
        "/api/v1/audit/activities?limit=0",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422


# ============================================================================
# actor_user_id filter tests (AUF1-AUF3) — Step 6.16.6
# ============================================================================


async def test_auf1_actor_user_id_filter_happy_path(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """LOAD-BEARING. ?actor_user_id=<id> returns only the matching row.

    Insert 3 audit rows with 3 distinct actor_user_ids on the same tenant;
    GET with one actor's id filters to that row only.
    """
    tenant = await make_tenant(name="AUF1-ActorFilter")
    actor_a = uuid.uuid4()
    actor_b = uuid.uuid4()
    actor_c = uuid.uuid4()
    row_a = await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="AUF1-ActorFilter",
        actor_user_id=actor_a,
    )
    row_b = await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="AUF1-ActorFilter",
        actor_user_id=actor_b,
    )
    row_c = await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="AUF1-ActorFilter",
        actor_user_id=actor_c,
    )

    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}"
        f"&actor_user_id={actor_b}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    ids = [r["id"] for r in resp.json()["items"]]
    assert str(row_b.id) in ids
    assert str(row_a.id) not in ids
    assert str(row_c.id) not in ids


async def test_auf2_actor_user_id_and_status_compose_via_and(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """?actor_user_id=X&status=PERMISSION_DENIED returns only rows matching BOTH.

    Insert 4 rows: actor A SUCCESS, actor A DENIED, actor B SUCCESS,
    actor B DENIED. Filter narrows to one row (actor A x DENIED).
    """
    tenant = await make_tenant(name="AUF2-AndCompose")
    actor_a = uuid.uuid4()
    actor_b = uuid.uuid4()
    a_success = await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="AUF2-AndCompose",
        actor_user_id=actor_a,
        result_type="SUCCESS",
        result_label="Success",
    )
    a_denied = await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="AUF2-AndCompose",
        actor_user_id=actor_a,
        result_type="PERMISSION_DENIED",
        result_label="Permission Denied",
    )
    b_success = await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="AUF2-AndCompose",
        actor_user_id=actor_b,
        result_type="SUCCESS",
        result_label="Success",
    )
    b_denied = await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="AUF2-AndCompose",
        actor_user_id=actor_b,
        result_type="PERMISSION_DENIED",
        result_label="Permission Denied",
    )

    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}"
        f"&actor_user_id={actor_a}&status=PERMISSION_DENIED",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    ids = [r["id"] for r in resp.json()["items"]]
    assert str(a_denied.id) in ids
    assert str(a_success.id) not in ids
    assert str(b_success.id) not in ids
    assert str(b_denied.id) not in ids


async def test_auf3_actor_user_id_unknown_returns_empty_no_422(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """LOAD-BEARING. Unknown UUID returns 0 rows with has_more=false; no 422.

    LD3: open vocabulary posture (mirrors 6.16.5 resource_type filter).
    Caller passes a random UUID that has no matching audit row; the
    endpoint returns 200 with an empty items list, not 422.
    """
    tenant = await make_tenant(name="AUF3-Empty")
    real_actor = uuid.uuid4()
    await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="AUF3-Empty",
        actor_user_id=real_actor,
    )

    unknown_actor = uuid.uuid4()
    resp = app_client.get(
        f"/api/v1/audit/activities?tenant_id={tenant.id}"
        f"&actor_user_id={unknown_actor}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["pagination"]["has_more"] is False
    assert body["pagination"]["next_cursor"] is None


# ============================================================================
# Detail endpoint tests (D1-D7)
# ============================================================================


async def test_d1_platform_detail_returns_full_row(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """LOAD-BEARING. Detail returns full 19-column shape including details JSONB.

    Step 6.16.7 LD10 : detail grew from 16 to 19 columns (added
    actor_organization_name, actor_roles, resource_subtype).
    """
    tenant = await make_tenant(name="D1-Detail")
    row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="D1-Detail",
        details={"before": {"status": "TRIAL"}, "after": {"status": "ACTIVE"}},
    )

    resp = app_client.get(
        f"/api/v1/audit/activities/{row.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(row.id)
    assert body["details"]["before"]["status"] == "TRIAL"
    assert body["details"]["after"]["status"] == "ACTIVE"
    # Verify the 19 expected keys are present (Step 6.16.7).
    expected_keys = {
        "id", "timestamp", "tenant_id", "tenant_name",
        "actor_user_id", "actor_user_type", "actor_display_name",
        "actor_organization_name", "actor_roles",
        "resource_type", "resource_id", "resource_label",
        "resource_subtype",
        "action", "action_label",
        "result_type", "result_label",
        "request_id", "details",
    }
    assert set(body.keys()) == expected_keys


async def test_d2_platform_detail_from_platform_table(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_platform_activity_audit_log,
) -> None:
    """PLATFORM caller can fetch detail for a platform-table row."""
    tenant = await make_tenant(name="D2-PlatformRow")
    row = await make_platform_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="D2-PlatformRow",
        action="CREATE",
    )
    resp = app_client.get(
        f"/api/v1/audit/activities/{row.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(row.id)
    assert body["action"] == "CREATE"


async def test_d3_tenant_detail_own_tenant_succeeds(
    app_client,
    make_tenant,
    make_tenant_activity_audit_log,
    tenant_owner_jwt_factory,
) -> None:
    """TENANT caller can fetch detail for a row in their own tenant."""
    tenant = await make_tenant(name="D3-OwnTenant", with_root=True)
    row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="D3-OwnTenant"
    )

    tjwt = await tenant_owner_jwt_factory(
        tenant.id,
        with_grants=[("ADMIN", "AUDIT_LOG", "VIEW", "TENANT")],
    )
    resp = app_client.get(
        f"/api/v1/audit/activities/{row.id}",
        headers=_auth(tjwt),
    )
    assert resp.status_code == 200, resp.text


async def test_d4_tenant_detail_other_tenant_returns_404(
    app_client,
    make_tenant,
    make_tenant_activity_audit_log,
    tenant_owner_jwt_factory,
) -> None:
    """LOAD-BEARING. Cross-tenant probe surfaces as 404 (RLS-as-404)."""
    tenant_a = await make_tenant(name="D4-CallerTenant", with_root=True)
    tenant_b = await make_tenant(name="D4-OtherTenant")
    other_row = await make_tenant_activity_audit_log(
        tenant_id=tenant_b.id, tenant_name="D4-OtherTenant"
    )

    tjwt = await tenant_owner_jwt_factory(
        tenant_a.id,
        with_grants=[("ADMIN", "AUDIT_LOG", "VIEW", "TENANT")],
    )
    resp = app_client.get(
        f"/api/v1/audit/activities/{other_row.id}",
        headers=_auth(tjwt),
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["code"] == "AUDIT_EVENT_NOT_FOUND"


async def test_d5_tenant_detail_platform_table_row_returns_404(
    app_client,
    make_tenant,
    make_platform_activity_audit_log,
    tenant_owner_jwt_factory,
) -> None:
    """LOAD-BEARING. TENANT caller probing a platform-table row -> 404.

    The platform table has no RLS, so a raw SELECT by id would return
    the row. The router-level check on ``scope == 'PLATFORM' AND
    auth.user_type == 'TENANT'`` converts that to a 404, matching the
    read principle "tenant users never see platform-scope rows."
    """
    tenant = await make_tenant(name="D5-PlatformRow", with_root=True)
    p_row = await make_platform_activity_audit_log(
        tenant_id=tenant.id, tenant_name="D5-PlatformRow"
    )

    tjwt = await tenant_owner_jwt_factory(
        tenant.id,
        with_grants=[("ADMIN", "AUDIT_LOG", "VIEW", "TENANT")],
    )
    resp = app_client.get(
        f"/api/v1/audit/activities/{p_row.id}",
        headers=_auth(tjwt),
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["code"] == "AUDIT_EVENT_NOT_FOUND"


async def test_d6_platform_detail_nonexistent_uuid_returns_404(
    app_client,
    super_admin_jwt,
) -> None:
    """PLATFORM caller probing non-existent UUID -> 404."""
    ephemeral = uuid.uuid4()
    resp = app_client.get(
        f"/api/v1/audit/activities/{ephemeral}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "AUDIT_EVENT_NOT_FOUND"


async def test_d7_malformed_uuid_in_path_returns_422(
    app_client,
    super_admin_jwt,
) -> None:
    """Malformed UUID in path -> 422 (FastAPI path validation)."""
    resp = app_client.get(
        "/api/v1/audit/activities/not-a-uuid",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422


# ============================================================================
# Permission / gate tests (P1-P3)
# ============================================================================


async def test_p1_no_jwt_returns_401(app_client) -> None:
    """No JWT -> 401 (auth middleware)."""
    resp = app_client.get("/api/v1/audit/activities")
    assert resp.status_code == 401


async def test_p2_platform_without_audit_grant_returns_403(
    app_client,
    make_no_audit_grant_platform_jwt,
) -> None:
    """LOAD-BEARING. PLATFORM user lacking the audit grant -> 403
    PERMISSION_DENIED.

    Catches catalogue-vs-gate misalignment: if the seed regressed and
    stopped granting ADMIN.AUDIT_LOG.VIEW.GLOBAL to the 3 platform
    roles, real users would hit 403; this test enforces the negative
    case via a fixture-injected role with no grants.
    """
    resp = app_client.get(
        "/api/v1/audit/activities",
        headers=_auth(make_no_audit_grant_platform_jwt),
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] == "PERMISSION_DENIED"

    # Detail endpoint also denies.
    resp_d = app_client.get(
        f"/api/v1/audit/activities/{uuid.uuid4()}",
        headers=_auth(make_no_audit_grant_platform_jwt),
    )
    assert resp_d.status_code == 403
    assert resp_d.json()["code"] == "PERMISSION_DENIED"


async def test_p3_tenant_without_audit_grant_returns_403(
    app_client,
    make_tenant,
    tenant_owner_jwt_factory,
) -> None:
    """LOAD-BEARING. TENANT user with role lacking .VIEW.TENANT -> 403.

    Uses tenant_owner_jwt_factory's ``with_grants`` override to mint a
    JWT whose user has a non-audit grant (e.g., USERS.VIEW.TENANT) but
    NOT AUDIT_LOG.VIEW.TENANT.
    """
    tenant = await make_tenant(name="P3-NoAuditGrant", with_root=True)
    tjwt = await tenant_owner_jwt_factory(
        tenant.id,
        with_grants=[("ADMIN", "USERS", "VIEW", "TENANT")],
    )
    resp = app_client.get(
        "/api/v1/audit/activities",
        headers=_auth(tjwt),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "PERMISSION_DENIED"

    resp_d = app_client.get(
        f"/api/v1/audit/activities/{uuid.uuid4()}",
        headers=_auth(tjwt),
    )
    assert resp_d.status_code == 403
    assert resp_d.json()["code"] == "PERMISSION_DENIED"


# ============================================================================
# Step 6.16.7 LD10 + LD11 — list response wire-shape extension
# ============================================================================


async def test_l_n1_list_response_carries_14_fields_per_item(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """LOAD-BEARING (Step 6.16.7 LD10): list endpoint item carries the
    14 fields including the 6 new ones (actor_organization_name,
    actor_roles, what, resource_type, resource_subtype, result_type).

    Backend composes ``what`` at read time per LD11. ``resource_type``
    is the raw enum string; ``resource_subtype`` is NULL on this
    non-ORG_NODE row.
    """
    tenant = await make_tenant(name="LN1-Tenant")
    await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="LN1-Tenant",
        actor_organization_name="LN1-Tenant",
        actor_roles="Owner",
        resource_type="TENANT_USER",
        resource_subtype=None,
        resource_label="marcus@bucees.com",
        action="UPDATE",
        action_label="Edited",
        result_type="SUCCESS",
        result_label="Success",
    )

    resp = app_client.get(
        "/api/v1/audit/activities?limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) >= 1
    item = next(
        (i for i in body["items"] if i.get("tenant_name") == "LN1-Tenant"),
        None,
    )
    assert item is not None
    expected = {
        "id",
        "timestamp",
        "actor_display_name",
        "actor_organization_name",
        "actor_roles",
        "action_label",
        "what",
        "resource_label",
        "resource_type",
        "resource_subtype",
        "result_label",
        "result_type",
        "scope",
        "tenant_name",
    }
    assert set(item.keys()) == expected
    assert item["actor_organization_name"] == "LN1-Tenant"
    assert item["actor_roles"] == "Owner"
    assert item["resource_type"] == "TENANT_USER"
    assert item["resource_subtype"] is None
    assert item["what"] == "User: marcus@bucees.com"
    assert item["result_type"] == "SUCCESS"


async def test_l_n2_list_response_org_node_row_carries_subtype_and_composed_what(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_tenant_activity_audit_log,
) -> None:
    """Step 6.16.7: ORG_NODE rows render ``resource_subtype`` and the
    composed ``what`` reflects the LD12 subtype-driven Type label.
    """
    tenant = await make_tenant(name="LN2-Tenant")
    await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="LN2-Tenant",
        resource_type="ORG_NODE",
        resource_subtype="REGION",
        resource_label="Texas Region",
        action="CREATE",
        action_label="Created",
    )

    resp = app_client.get(
        "/api/v1/audit/activities?limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    item = next(
        (i for i in body["items"] if i.get("tenant_name") == "LN2-Tenant"),
        None,
    )
    assert item is not None
    assert item["resource_type"] == "ORG_NODE"
    assert item["resource_subtype"] == "REGION"
    assert item["what"] == "Region: Texas Region"
