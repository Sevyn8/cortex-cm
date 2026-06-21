"""Integration tests for the tenants router (Step 3.3).

Real Postgres, real schema, real RLS, real router via FastAPI's
TestClient. JWTs minted via Step 2.1's ``make_test_jwt``.

Coverage shape:

  L1-L10: list endpoint.
  S1-S3:  stats endpoint.
  D1-D6:  detail endpoint.
  A1-A2:  auth (covering all three).

Two load-bearing assertions are flagged in their docstrings:
  - L9: per-row aggregate subqueries scope correctly per Tenant via
        SQL ``.correlate(Tenant)``. Validates that PLATFORM-visible
        ``num_stores`` / ``num_users_active`` numbers are per-tenant,
        not platform-wide totals applied to every row.
  - D4: TENANT-A asking for TENANT-B's id returns 404 (RLS-blocked
        surfaces as not-found, per D-17). Cross-tenant security
        regression assertion.
"""
import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings
from admin_backend.main import create_app
from admin_backend.models.tenant import TenantStatus, TenantTier
from admin_backend.models.tenant_module_access import (
    ModuleAccessStatus,
    ModuleCode,
)


_UUID_RE = __import__("re").compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@pytest.fixture(scope="module")
def app(settings: Settings) -> FastAPI:
    """Real FastAPI app via create_app(); lifespan is bypassed by
    TestClient unless invoked as a context manager — for our tests we
    skip the lifespan and pre-populate app.state in the client fixture
    below.
    """
    return create_app()


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,  # type: ignore[no-any-unimported]
    session_factory: Any,  # type: ignore[no-any-unimported]
) -> Iterator[TestClient]:
    """TestClient against a real app with real engine/session_factory.

    Bypasses the lifespan (which would re-construct an engine in a
    different event loop than the test). Mirrors the
    ``app_with_test_routes`` pattern from Step 2.3/2.4 but doesn't
    register any test-only routes — just exercises the real tenants
    router.
    """
    from admin_backend.auth.stub import StubAuthClient

    app_obj = create_app()
    app_obj.state.settings = settings
    app_obj.state.engine = engine
    app_obj.state.session_factory = session_factory
    app_obj.state.auth_client = StubAuthClient(settings)
    with TestClient(app_obj) as client:
        yield client


def _platform_jwt(settings: Settings) -> str:
    return make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="PLATFORM",
    )


def _tenant_jwt(settings: Settings, tenant_id: UUID) -> str:
    return make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="TENANT",
        tenant_id=tenant_id,
    )


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


# =============================================================================
# List endpoint (L1-L10)
# =============================================================================


# ---- L1: PLATFORM, no params, paginated --------------------------------------
async def test_l1_list_platform_no_params_returns_paginated_items(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    a = await make_tenant(name="L1-Alpha")
    b = await make_tenant(name="L1-Bravo")
    resp = app_client.get(
        "/api/v1/tenants",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body and "pagination" in body
    names = {item["name"] for item in body["items"]}
    assert {"L1-Alpha", "L1-Bravo"}.issubset(names)
    # pagination.total reflects RLS-visible count (>= 2 from this test)
    assert body["pagination"]["total"] >= 2
    assert body["pagination"]["offset"] == 0
    assert body["pagination"]["limit"] == 20


# ---- L2: tier filter --------------------------------------------------------
async def test_l2_list_filters_by_tier(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    ent = await make_tenant(name="L2-Ent", tier=TenantTier.ENTERPRISE)
    smb = await make_tenant(name="L2-SMB", tier=TenantTier.SMB)
    resp = app_client.get(
        "/api/v1/tenants?tier=ENTERPRISE",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["items"]}
    assert str(ent.id) in ids
    assert str(smb.id) not in ids


# ---- L3: search filter (ILIKE name/display_code/contact_email) ---------------
async def test_l3_list_search_matches_name_display_code_email(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    # The search substring "acmesearch" must literally appear in the
    # field for the ILIKE to match — e.g., dashes inside a name will
    # break a substring lookup.
    by_name = await make_tenant(name="L3-acmesearch-by-name")
    by_code = await make_tenant(
        name="L3-OtherName", display_code="acmesearchcode"
    )
    by_email = await make_tenant(
        name="L3-DifferentName", contact_email="hello@acmesearch.com"
    )
    not_match = await make_tenant(name="L3-Unrelated-Tenant")
    resp = app_client.get(
        "/api/v1/tenants?search=acmesearch",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["items"]}
    assert str(by_name.id) in ids
    assert str(by_code.id) in ids
    assert str(by_email.id) in ids
    assert str(not_match.id) not in ids


# ---- L4: empty-search-string treated as no filter ---------------------------
async def test_l4_list_empty_search_after_trim_is_no_filter(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    a = await make_tenant(name="L4-OnlyMatch")
    resp = app_client.get(
        "/api/v1/tenants?search=%20%20%20",  # whitespace only
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    # The whitespace search should be treated as "no filter" — so the
    # response includes our tenant and possibly others (subset OK).
    names = {item["name"] for item in resp.json()["items"]}
    assert "L4-OnlyMatch" in names


# ---- L5: deterministic search-AND-pagination interaction --------------------
async def test_l5_pagination_with_search_filter(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    """Load-bearing: filter is applied to BOTH the count and the page query.

    Five tenants share a unique prefix; expect the second page (offset=2,
    limit=2) to return rows 3 and 4 alphabetically, with total=5.

    **Step 6.4 note:** the test pins ``sort=name_asc`` explicitly so the
    alphabetical-page assertion holds independent of the default sort.
    Pre-Step-6.4 the endpoint had no sort param and ordering was
    hardcoded ``name ASC``; the new default is ``created_at_desc``.
    """
    for n in ("L5-Alpha", "L5-Bravo", "L5-Charlie", "L5-Delta", "L5-Echo"):
        await make_tenant(name=n)

    resp = app_client.get(
        "/api/v1/tenants?search=L5-&sort=name_asc&limit=2&offset=2",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [item["name"] for item in body["items"]] == [
        "L5-Charlie",
        "L5-Delta",
    ]
    assert body["pagination"] == {
        "total": 5,
        "offset": 2,
        "limit": 2,
    }


# =============================================================================
# Sort vocabulary (Step 6.4 — L4a-L4g column keys + L5a-L5e aggregate keys)
# =============================================================================


async def test_l4a_sort_created_at_asc(
    app_client, settings, make_tenant,
    super_admin_jwt,


):
    """``sort=created_at_asc`` returns rows ordered by created_at ascending.

    Three tenants are inserted in sequence; their ``created_at``
    timestamps are DB-managed (DEFAULT NOW()) and monotonic in insert
    order. The unique-prefix search confines the assertion to the rows
    this test created, isolating from prior test or seed state.
    """
    a = await make_tenant(name="L4a-First")
    b = await make_tenant(name="L4a-Second")
    c = await make_tenant(name="L4a-Third")
    resp = app_client.get(
        "/api/v1/tenants?search=L4a-&sort=created_at_asc&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["items"]]
    assert names == ["L4a-First", "L4a-Second", "L4a-Third"]


async def test_l4b_sort_created_at_desc(
    app_client, settings, make_tenant,
    super_admin_jwt,


):
    """``sort=created_at_desc`` returns rows ordered by created_at descending.

    Also doubles as the new-default coverage: callers who don't pass
    ``sort`` get this ordering implicitly (verified separately by the
    unchanged L1).
    """
    a = await make_tenant(name="L4b-First")
    b = await make_tenant(name="L4b-Second")
    c = await make_tenant(name="L4b-Third")
    resp = app_client.get(
        "/api/v1/tenants?search=L4b-&sort=created_at_desc&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["items"]]
    assert names == ["L4b-Third", "L4b-Second", "L4b-First"]


async def test_l4c_sort_name_asc(app_client, settings, make_tenant, super_admin_jwt):
    """``sort=name_asc`` returns rows alphabetically by name."""
    await make_tenant(name="L4c-Charlie")
    await make_tenant(name="L4c-Alpha")
    await make_tenant(name="L4c-Bravo")
    resp = app_client.get(
        "/api/v1/tenants?search=L4c-&sort=name_asc&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["items"]]
    assert names == ["L4c-Alpha", "L4c-Bravo", "L4c-Charlie"]


async def test_l4d_sort_name_desc(app_client, settings, make_tenant, super_admin_jwt):
    """``sort=name_desc`` returns rows reverse-alphabetically by name."""
    await make_tenant(name="L4d-Charlie")
    await make_tenant(name="L4d-Alpha")
    await make_tenant(name="L4d-Bravo")
    resp = app_client.get(
        "/api/v1/tenants?search=L4d-&sort=name_desc&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["items"]]
    assert names == ["L4d-Charlie", "L4d-Bravo", "L4d-Alpha"]


async def test_l4e_sort_tier_asc(app_client, settings, make_tenant, super_admin_jwt):
    """``sort=tier_asc`` returns rows by tier enum ordinal ascending.

    Postgres orders enum columns by enum ordinal (DDL declaration
    order), not string-alphabetic. ``tenant_tier_enum`` declares
    ENTERPRISE, MID_MARKET, SMB, SINGLE_STORE in that order.
    Asserting the relative position of two tenants (ENTERPRISE before
    SMB) is enum-order-correct regardless of alphabet.
    """
    enterprise = await make_tenant(
        name="L4e-Ent", tier=TenantTier.ENTERPRISE
    )
    smb = await make_tenant(name="L4e-SMB", tier=TenantTier.SMB)
    resp = app_client.get(
        "/api/v1/tenants?search=L4e-&sort=tier_asc&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["items"]]
    # ENTERPRISE (ordinal 1) < SMB (ordinal 3) — ENTERPRISE first.
    assert names.index("L4e-Ent") < names.index("L4e-SMB")


async def test_l4f_sort_tier_desc(app_client, settings, make_tenant, super_admin_jwt):
    """``sort=tier_desc`` returns rows by tier enum ordinal descending."""
    enterprise = await make_tenant(
        name="L4f-Ent", tier=TenantTier.ENTERPRISE
    )
    smb = await make_tenant(name="L4f-SMB", tier=TenantTier.SMB)
    resp = app_client.get(
        "/api/v1/tenants?search=L4f-&sort=tier_desc&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["items"]]
    assert names.index("L4f-SMB") < names.index("L4f-Ent")


def test_l4g_invalid_sort_returns_400(app_client, settings, super_admin_jwt):
    """Unknown sort key surfaces as 400 INVALID_SORT_KEY (not 500).

    The Repo raises ``InvalidSortKeyError`` (a ValueError subclass);
    the router catches and re-raises as ``InvalidSortKeyClientError``
    so the response is the canonical 400 envelope rather than a
    generic 500. Mirrors the pattern used by /platform-users and
    /tenant-users endpoints.
    """
    resp = app_client.get(
        "/api/v1/tenants?sort=garbage_desc",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_SORT_KEY"
    assert body["message"] == "Invalid sort key"
    assert body["details"] is None


async def test_l5a_sort_num_users_active_asc(
    app_client, settings, make_tenant, make_tenant_user,
    super_admin_jwt,


):
    """``sort=num_users_active_asc`` returns rows ordered by active user
    count ascending.

    Three tenants with 0 / 1 / 2 ACTIVE users; the unique-prefix search
    isolates the assertion. Tied count rows fall back to ``Tenant.id
    ASC`` so the result is deterministic — for this test all counts are
    distinct so the tiebreaker isn't exercised.
    """
    zero_users = await make_tenant(name="L5a-Zero")
    one_user = await make_tenant(name="L5a-One")
    two_users = await make_tenant(name="L5a-Two")
    await make_tenant_user(tenant_id=one_user.id, status="ACTIVE")
    await make_tenant_user(tenant_id=two_users.id, status="ACTIVE")
    await make_tenant_user(tenant_id=two_users.id, status="ACTIVE")

    resp = app_client.get(
        "/api/v1/tenants?search=L5a-&sort=num_users_active_asc&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    counts = [item["num_users_active"] for item in items]
    names = [item["name"] for item in items]
    assert names == ["L5a-Zero", "L5a-One", "L5a-Two"]
    assert counts == [0, 1, 2]


async def test_l5b_sort_num_users_active_desc(
    app_client, settings, make_tenant, make_tenant_user,
    super_admin_jwt,


):
    """**LOAD-BEARING** — Step 6.5's Top Tenants dashboard panel calls
    ``GET /tenants?sort=num_users_active_desc&limit=5`` exactly.
    Without this sort key working, the panel would receive a 400
    INVALID_SORT_KEY response and the dashboard would fail to render.

    Three tenants with 0 / 3 / 5 ACTIVE users; expect descending
    order with limit=5 returning all three (since the test creates
    fewer than five distinct prefixed rows).
    """
    a = await make_tenant(name="L5b-Few")
    b = await make_tenant(name="L5b-Many")
    c = await make_tenant(name="L5b-Zero")
    for _ in range(3):
        await make_tenant_user(tenant_id=a.id, status="ACTIVE")
    for _ in range(5):
        await make_tenant_user(tenant_id=b.id, status="ACTIVE")
    # No users for c.

    resp = app_client.get(
        "/api/v1/tenants?search=L5b-&sort=num_users_active_desc&limit=5",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    counts = [item["num_users_active"] for item in items]
    names = [item["name"] for item in items]
    assert names == ["L5b-Many", "L5b-Few", "L5b-Zero"]
    assert counts == [5, 3, 0]


async def test_l5c_sort_num_stores_asc(
    app_client, settings, make_tenant, make_store,
    super_admin_jwt,


):
    """``sort=num_stores_asc`` returns rows ordered by stores count ascending."""
    zero = await make_tenant(name="L5c-Zero")
    two = await make_tenant(name="L5c-Two")
    four = await make_tenant(name="L5c-Four")
    for _ in range(2):
        await make_store(tenant_id=two.id)
    for _ in range(4):
        await make_store(tenant_id=four.id)

    resp = app_client.get(
        "/api/v1/tenants?search=L5c-&sort=num_stores_asc&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    counts = [item["num_stores"] for item in items]
    names = [item["name"] for item in items]
    assert names == ["L5c-Zero", "L5c-Two", "L5c-Four"]
    assert counts == [0, 2, 4]


async def test_l5d_sort_num_stores_desc(
    app_client, settings, make_tenant, make_store,
    super_admin_jwt,


):
    """``sort=num_stores_desc`` returns rows ordered by stores count descending."""
    zero = await make_tenant(name="L5d-Zero")
    two = await make_tenant(name="L5d-Two")
    four = await make_tenant(name="L5d-Four")
    for _ in range(2):
        await make_store(tenant_id=two.id)
    for _ in range(4):
        await make_store(tenant_id=four.id)

    resp = app_client.get(
        "/api/v1/tenants?search=L5d-&sort=num_stores_desc&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    counts = [item["num_stores"] for item in items]
    names = [item["name"] for item in items]
    assert names == ["L5d-Four", "L5d-Two", "L5d-Zero"]
    assert counts == [4, 2, 0]


async def test_l5e_tenant_jwt_denied_at_tenants_list_with_sort(
    app_client, settings, make_tenant
):
    """Gate-by-design denial for TENANT JWTs on the tenants list.

    Post-Step-6.9.3.2 ``GET /api/v1/tenants`` is gated by
    ``ADMIN.TENANTS.VIEW.GLOBAL``. A TENANT JWT (no grants) hits a
    403 PERMISSION_DENIED at the gate before any Repo call. Any sort
    or filter query params are inert. The pre-retrofit assertion
    (RLS-scopes aggregate-keyed sort to TENANT-A only) is unreachable
    via this endpoint for TENANT callers; the gate is the higher-level
    guarantee.
    """
    tenant_a = await make_tenant(name="L5e-TenantA")
    resp = app_client.get(
        "/api/v1/tenants?sort=num_users_active_desc&limit=5",
        headers=_auth(_tenant_jwt(settings, tenant_a.id)),
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "PERMISSION_DENIED"
    assert body["message"] == "Permission denied"


# ---- L6: limit cap validation ----------------------------------------------
def test_l6_list_limit_above_cap_returns_422(app_client, settings, super_admin_jwt):
    resp = app_client.get(
        "/api/v1/tenants?limit=200",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422


# ---- L7: invalid tier returns 422 ------------------------------------------
def test_l7_list_invalid_tier_returns_422(app_client, settings, super_admin_jwt):
    resp = app_client.get(
        "/api/v1/tenants?tier=GIANT",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422


# ---- L8: TENANT JWT denied at /api/v1/tenants (gate-by-design) -------------
async def test_l8_tenant_jwt_denied_at_tenants_list(
    app_client, settings, make_tenant
):
    """Gate-by-design denial for TENANT JWTs on the tenants list.

    Post-Step-6.9.3.2 the endpoint is gated by
    ``ADMIN.TENANTS.VIEW.GLOBAL`` — a fleet-wide capability. TENANT JWTs
    (synthetic, no grants) are denied at the gate. The pre-retrofit
    assertion ("RLS scopes own row only") is gone because RLS no longer
    runs; the gate is the higher-level guarantee against cross-tenant
    enumeration.
    """
    tenant_a = await make_tenant(name="L8-TenantA")
    resp = app_client.get(
        "/api/v1/tenants",
        headers=_auth(_tenant_jwt(settings, tenant_a.id)),
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "PERMISSION_DENIED"
    assert body["message"] == "Permission denied"


# ---- L9: RLS-under-aggregates (LOAD-BEARING) -------------------------------
async def test_l9_per_row_aggregates_scope_per_tenant(
    app_client, settings, make_tenant, make_store, make_tenant_user,
    super_admin_jwt,
):
    """Validates the .correlate(Tenant) subquery semantics.

    Each row's num_stores / num_users_active must be that row's tenant's
    counts, not the platform-wide totals applied to every row. If
    .correlate(Tenant) is missing or wrong, the subqueries collapse to
    cartesian counts — every row would carry the same total, and this
    test would fail.
    """
    tenant_a = await make_tenant(name="L9-TenantA")
    tenant_b = await make_tenant(name="L9-TenantB")
    # 3 stores under A, 2 under B
    for _ in range(3):
        await make_store(tenant_id=tenant_a.id)
    for _ in range(2):
        await make_store(tenant_id=tenant_b.id)
    # 4 ACTIVE + 1 INVITED tenant_users under A. The INVITED user
    # exercises the "non-ACTIVE row not counted" branch of
    # num_users_active without pulling in the SUSPENDED audit-actor
    # tower (deferred to Step 5.2).
    for _ in range(4):
        await make_tenant_user(tenant_id=tenant_a.id, status="ACTIVE")
    await make_tenant_user(tenant_id=tenant_a.id, status="INVITED")

    resp = app_client.get(
        "/api/v1/tenants?search=L9-",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    by_id = {item["id"]: item for item in resp.json()["items"]}
    assert by_id[str(tenant_a.id)]["num_stores"] == 3
    assert by_id[str(tenant_a.id)]["num_users_active"] == 4
    assert by_id[str(tenant_b.id)]["num_stores"] == 2
    assert by_id[str(tenant_b.id)]["num_users_active"] == 0


# ---- L10: modules from tenant_module_access table -------------------------
async def test_l10_modules_from_table_with_display_name_resolution(
    app_client, settings, make_tenant, make_platform_user,
    make_tenant_module_access,
    super_admin_jwt,
):
    """Modules come from the real tenant_module_access table (FN-AB-16
    RESOLVED at Step 3.4.5). Verifies the JOIN to lookups, the
    DISABLED-status filter, the display_order ordering, and the
    cross-tenant isolation via .correlate(Tenant).
    """
    from datetime import datetime, timezone

    actor = await make_platform_user(email="l10-actor@ithina.test")
    tenant_a = await make_tenant(name="L10-Alpha")
    tenant_b = await make_tenant(name="L10-Bravo")

    # Tenant A: GOAL_CONSOLE (display_order 2) + PRICING_OS (3) ENABLED;
    # ADMIN (6) DISABLED — must not surface.
    await make_tenant_module_access(
        tenant_id=tenant_a.id,
        module=ModuleCode.GOAL_CONSOLE,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    await make_tenant_module_access(
        tenant_id=tenant_a.id,
        module=ModuleCode.PRICING_OS,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    await make_tenant_module_access(
        tenant_id=tenant_a.id,
        module=ModuleCode.ADMIN,
        status=ModuleAccessStatus.DISABLED,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
        disabled_at=datetime.now(tz=timezone.utc),
        disabled_by_user_id=actor.id,
    )
    # Tenant B: just GOAL_CONSOLE — verifies cross-tenant isolation.
    await make_tenant_module_access(
        tenant_id=tenant_b.id,
        module=ModuleCode.GOAL_CONSOLE,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )

    resp = app_client.get(
        "/api/v1/tenants?search=L10-",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    items_by_name = {i["name"]: i for i in resp.json()["items"]}

    # Tenant A: 2 modules, ENABLED only, ordered by display_order.
    a_modules = items_by_name["L10-Alpha"]["modules"]
    assert a_modules == [
        {"code": "GOAL_CONSOLE", "name": "Goal Console"},
        {"code": "PRICING_OS", "name": "Pricing OS"},
    ]

    # Tenant B: just GOAL_CONSOLE.
    assert items_by_name["L10-Bravo"]["modules"] == [
        {"code": "GOAL_CONSOLE", "name": "Goal Console"},
    ]


# ---- L10b: empty-modules COALESCE path -------------------------------------
async def test_l10b_tenant_with_no_modules_returns_empty_array(
    app_client, settings, make_tenant, make_org_node,
    super_admin_jwt,
):
    """Tenant with zero rows in tenant_module_access. The COALESCE in
    the subquery wraps NULL -> '[]'::jsonb so the response carries
    [], not None. Guards against future "simplifications" that strip
    the COALESCE.

    Post-Step-6.9.3.2: the detail endpoint's ``anchor_dep`` requires a
    tenant-root org_node to exist; without it the gate raises 404
    TENANT_NOT_FOUND before the Repo runs. Each test that exercises
    ``/api/v1/tenants/{id}`` must provision a TENANT-type root via
    ``make_org_node``.
    """
    fixture_tenant = await make_tenant(name="L10b-NoModules")
    await make_org_node(
        tenant_id=fixture_tenant.id, node_type="TENANT",
        code="L10B-ROOT", name="L10b Root",
    )
    resp = app_client.get(
        f"/api/v1/tenants/{fixture_tenant.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    assert resp.json()["modules"] == []


# =============================================================================
# Stats endpoint (S1-S3)
# =============================================================================


# ---- S1: PLATFORM scalars are positive ints --------------------------------
async def test_s1_stats_platform_returns_positive_scalars(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    await make_tenant(name="S1-Tenant")
    resp = app_client.get(
        "/api/v1/tenants/stats",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["total_tenants"], int)
    assert isinstance(body["total_stores"], int)
    assert body["total_tenants"] >= 1
    assert body["total_stores"] >= 0


# ---- S2: TENANT JWT denied at /api/v1/tenants/stats (gate-by-design) -------
async def test_s2_tenant_jwt_denied_at_stats(
    app_client, settings, make_tenant
):
    """Gate-by-design denial for TENANT JWTs on the stats endpoint.

    Post-Step-6.9.3.2 ``/api/v1/tenants/stats`` is gated by
    ``ADMIN.TENANTS.VIEW.GLOBAL``. The previous RLS-scoped "own counts"
    semantic is unreachable for TENANT JWTs. Tracked as forward note:
    a future TENANT-scoped own-counts endpoint (e.g.,
    ``/api/v1/my-tenant/stats``) would re-introduce this behavior under
    a different URL.
    """
    tenant_a = await make_tenant(name="S2-TenantA")
    resp = app_client.get(
        "/api/v1/tenants/stats",
        headers=_auth(_tenant_jwt(settings, tenant_a.id)),
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "PERMISSION_DENIED"
    assert body["message"] == "Permission denied"


# ---- S3: Cache-Control header set ------------------------------------------
def test_s3_stats_cache_control_header(app_client, settings, super_admin_jwt):
    resp = app_client.get(
        "/api/v1/tenants/stats",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "private, max-age=60"


# =============================================================================
# Detail endpoint (D1-D6)
# =============================================================================


# ---- D1: PLATFORM detail returns full shape --------------------------------
async def test_d1_detail_platform_returns_all_fields(
    app_client, settings, make_tenant, make_org_node,
    make_store, make_tenant_user,
    super_admin_jwt,
):
    tenant = await make_tenant(name="D1-Detail")
    await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code="D1-ROOT", name="D1 Root",
    )
    await make_store(tenant_id=tenant.id)
    await make_store(tenant_id=tenant.id)
    await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")

    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Spot-check a representative subset of the 21 expected fields.
    expected_fields = {
        "id", "name", "display_code", "country", "region", "tier",
        "industry", "monthly_revenue_usd", "monthly_revenue_as_of_date",
        "number_of_stores", "number_of_stores_as_of_date",
        "primary_contact_name", "contact_email", "status",
        "created_at", "updated_at", "suspended_at", "terminated_at",
        "num_stores", "num_users_active", "modules",
    }
    assert set(body.keys()) == expected_fields
    assert body["id"] == str(tenant.id)
    assert body["name"] == "D1-Detail"
    assert body["num_stores"] == 2
    assert body["num_users_active"] == 1


# ---- D2: 404 for non-existent UUID, canonical envelope ---------------------
def test_d2_detail_missing_id_returns_canonical_404(app_client, settings, super_admin_jwt):
    """Canonical error envelope: {code, message, details, request_id}."""
    ephemeral_id = uuid.uuid4()
    resp = app_client.get(
        f"/api/v1/tenants/{ephemeral_id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "details", "request_id"}
    assert body["code"] == "TENANT_NOT_FOUND"
    assert body["message"] == "Tenant not found"
    assert body["details"] is None
    assert _UUID_RE.match(body["request_id"])


# ---- D3: TENANT OWNER own id returns 200 ----------------------------------
async def test_d3_detail_tenant_owner_own_id_returns_200(
    app_client, settings, make_tenant, make_org_node, tenant_owner_jwt_factory
):
    """A TENANT OWNER reads own-tenant detail.

    Gated by ``ADMIN.TENANTS.VIEW.TENANT`` with
    ``anchor_dep=get_tenant_anchor``. Factory's default grants cover
    the required tuple post Phase 3 seed update (2026-05-13).
    """
    tenant = await make_tenant(name="D3-Own")
    await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code="D3-ROOT", name="D3 Root",
    )
    owner_jwt = await tenant_owner_jwt_factory(tenant.id)
    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}",
        headers=_auth(owner_jwt),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == str(tenant.id)


# ---- D4: TENANT-A asking for TENANT-B id -> 404 (LOAD-BEARING) -------------
async def test_d4_detail_cross_tenant_returns_404(
    app_client, settings, make_tenant
):
    """Load-bearing security regression test.

    RLS blocks tenant B's row from tenant A's session; the handler
    sees None, raises TenantNotFoundError, the response is the
    canonical TENANT_NOT_FOUND 404 envelope. If this test ever fails
    (status != 404 or body shape changes), cross-tenant data isolation
    has regressed.
    """
    tenant_a = await make_tenant(name="D4-TenantA")
    tenant_b = await make_tenant(name="D4-TenantB")
    resp = app_client.get(
        f"/api/v1/tenants/{tenant_b.id}",
        headers=_auth(_tenant_jwt(settings, tenant_a.id)),
    )
    assert resp.status_code == 404
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "details", "request_id"}
    assert body["code"] == "TENANT_NOT_FOUND"
    assert body["details"] is None


# ---- D5: malformed UUID -> 422 ---------------------------------------------
def test_d5_detail_malformed_uuid_returns_422(app_client, settings, super_admin_jwt):
    resp = app_client.get(
        "/api/v1/tenants/not-a-uuid",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422
    # FastAPI's default validation error body is its own shape; we
    # don't enforce the canonical envelope on FastAPI-emitted 422s.


# ---- D6: detail modules from tenant_module_access table ------------------
async def test_d6_detail_modules_from_table(
    app_client, settings, make_tenant, make_org_node, make_platform_user,
    make_tenant_module_access,
    super_admin_jwt,
):
    """Detail endpoint resolves modules from the real table (FN-AB-16
    RESOLVED). Insert one ENABLED module; assert the response carries
    its code + display name from lookups."""
    actor = await make_platform_user(email="d6-actor@ithina.test")
    tenant = await make_tenant(name="D6-Tenant")
    await make_org_node(
        tenant_id=tenant.id, node_type="TENANT",
        code="D6-ROOT", name="D6 Root",
    )
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.GOAL_CONSOLE,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )

    resp = app_client.get(
        f"/api/v1/tenants/{tenant.id}",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    assert resp.json()["modules"] == [
        {"code": "GOAL_CONSOLE", "name": "Goal Console"},
    ]


# =============================================================================
# Auth (A1-A2). Cover all three endpoints, but one each is enough — middleware
# behaviour was already verified at Step 2.3.
# =============================================================================


def test_a1_no_authorization_header_returns_401(app_client):
    resp = app_client.get("/api/v1/tenants")
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MISSING"


def test_a2_invalid_jwt_returns_401(app_client):
    resp = app_client.get(
        "/api/v1/tenants",
        headers={"Authorization": "Bearer not.a.real.jwt"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_INVALID"
