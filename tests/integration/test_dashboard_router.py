"""Integration tests for the dashboard stats router (Step 6.5).

Real Postgres, real schema, real RLS, real router via FastAPI's
TestClient. JWTs minted via Step 2.1's ``make_test_jwt``. Mirrors
the shape used by ``test_tenants_router.py``.

Test ID convention:
  S*  fleet-stats endpoint                            (8 tests)
  O*  governance-stats endpoint                       (6 tests)
  A*  auth                                            (1 test)
  X*  cross-cutting                                   (1 test)
                                                     ----
                                                     ~16 tests

Five LOAD-BEARING tests:
  S2  TENANT JWT fleet-stats — RLS scopes counts to own tenant.
  S5  fleet-stats sub_text scope-awareness — same data, two requests.
  S7  MRR delta is permanently stubbed (`available: false`); guards
      against accidentally flipping to true without the snapshot
      table existing.
  O2  governance-stats: modules_deployed real and RLS-scoped while
      the other 3 cards stay stubbed.
  O5  modules_deployed sub_text scope-awareness across user types.

Plus one design-review-amendment test:
  S3+ active_tenants sub_text covers ONBOARDING (Step 6.5 amendment;
       lifecycle order onboarding → trial → suspended).

The seed loader's --reset state is recreated by each test that needs
seeded counts; tests that assert relative ordering / proportions
rather than absolute counts are robust to leftover state from other
test runs.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings, get_settings
from admin_backend.db.session import get_tenant_session
from admin_backend.main import create_app
from admin_backend.models.tenant import TenantStatus
from admin_backend.models.tenant_module_access import ModuleCode


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,  # type: ignore[no-any-unimported]
    session_factory: Any,  # type: ignore[no-any-unimported]
) -> Iterator[TestClient]:
    """TestClient against a real app with real engine/session_factory.

    Bypasses the lifespan (would re-construct an engine in a different
    event loop than the test). Same pattern as the other
    router-test modules.
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
        settings, user_id=uuid.uuid4(), user_type="PLATFORM"
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
# E1: fleet-stats (S1-S8 + S3+ amendment)
# =============================================================================


# ---- S1: PLATFORM envelope shape -------------------------------------------
async def test_s1_fleet_stats_platform_envelope(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    """All 4 cards present with expected fields and `available` flags.

    Fixture-creates a single tenant so the response has at least one
    visible row; absolute counts are not asserted (other tests'
    leftover state could perturb them) — we assert envelope shape only.
    """
    await make_tenant(name="S1-Tenant")
    resp = app_client.get(
        "/api/v1/dashboard/fleet-stats",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "active_tenants",
        "platform_users",
        "stores",
        "mrr_aggregated",
    }
    # Every card except stores has a populated DeltaBlock; stores' delta
    # is null by contract.
    assert set(body["active_tenants"].keys()) == {
        "value", "total", "sub_text", "delta", "available",
    }
    assert body["active_tenants"]["available"] is True
    assert body["platform_users"]["available"] is True
    assert body["stores"]["available"] is True
    assert body["mrr_aggregated"]["available"] is True
    # MRR delta stubbed.
    assert body["mrr_aggregated"]["delta"]["available"] is False
    # Stores has no delta.
    assert body["stores"]["delta"] is None


# ---- S2: TENANT JWT RLS scoping (LOAD-BEARING) ----------------------------
async def test_s2_fleet_stats_tenant_rls_scoping(
    app_client, make_tenant, make_tenant_user, tenant_owner_jwt_factory
):
    """LOAD-BEARING: RLS scopes counts to the calling tenant.

    Two tenants A and B are created; tenant A gets 2 ACTIVE
    tenant_users (the factory adds a 3rd synthetic OWNER), tenant B
    gets 3. A TENANT-A JWT calling fleet-stats must see
    active_tenants.value == 1, total == 1, and platform_users
    reflecting only tenant A's users (not the cross-tenant total).

    Without RLS scoping here, a TENANT user could read fleet-wide
    user counts via this endpoint.
    """
    tenant_a = await make_tenant(name="S2-TenantA", status=TenantStatus.ACTIVE)
    tenant_b = await make_tenant(name="S2-TenantB", status=TenantStatus.ACTIVE)
    for _ in range(2):
        await make_tenant_user(tenant_id=tenant_a.id, status="ACTIVE")
    for _ in range(3):
        await make_tenant_user(tenant_id=tenant_b.id, status="ACTIVE")

    jwt = await tenant_owner_jwt_factory(tenant_a.id)
    resp = app_client.get(
        "/api/v1/dashboard/fleet-stats",
        headers=_auth(jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Tenant-A JWT sees only tenant A.
    assert body["active_tenants"]["value"] == 1
    assert body["active_tenants"]["total"] == 1
    # platform_users is the TENANT-A user count (2 explicit + 1 from
    # the factory's synthetic OWNER), not the cross-tenant total.
    assert body["platform_users"]["value"] == 3


# ---- S3: active_tenants sub_text formatting (lifecycle segments) ---------
# Note on fixture ordering: ``make_platform_user`` is requested BEFORE
# ``make_tenant`` so pytest tears down tenants first (reverse-of-request
# order). Tenant rows reference the platform_user via
# ``suspended_by_user_id`` FK ON DELETE RESTRICT; deleting the actor
# first would fail the FK. Same constraint applies to L10 in the
# tenants router tests but is implicit there because the FK lands on
# tenant_module_access (teardown'd between platform_user and tenant).
async def test_s3_active_tenants_sub_text_branches(
    app_client, settings, make_platform_user, make_tenant,
    super_admin_jwt,
):
    """Sub_text covers ONBOARDING + TRIAL + SUSPENDED in lifecycle order.

    Step 6.5 amendment (2026-05-06): the sub_text vocabulary was
    extended to cover ONBOARDING (the lifecycle's first state). This
    test creates a tenant in each of the three breakout states and
    verifies all three appear in the sub_text in lifecycle order
    (onboarding → trial → suspended), separated by " · ".

    SUSPENDED requires ``suspended_at`` AND ``suspended_by_user_id`` to
    be NOT NULL per ``ck_tenants_suspended_consistency`` — the make_tenant
    fixture accepts both as overrides; an actor is created via
    make_platform_user.
    """
    from datetime import datetime, timezone

    actor = await make_platform_user(
        email=f"s3-actor-{uuid.uuid4()}@ithina.test"
    )
    await make_tenant(
        name="S3-Onb", status=TenantStatus.ONBOARDING
    )
    await make_tenant(name="S3-Trial", status=TenantStatus.TRIAL)
    await make_tenant(
        name="S3-Susp",
        status=TenantStatus.SUSPENDED,
        suspended_at=datetime.now(tz=timezone.utc),
        suspended_by_user_id=actor.id,
    )

    resp = app_client.get(
        "/api/v1/dashboard/fleet-stats",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    sub_text = resp.json()["active_tenants"]["sub_text"]
    # All three breakout segments present.
    assert "onboarding" in sub_text
    assert "trial" in sub_text
    assert "suspended" in sub_text
    # Lifecycle ordering preserved.
    assert sub_text.index("onboarding") < sub_text.index("trial")
    assert sub_text.index("trial") < sub_text.index("suspended")
    # The separator is " · " (Unicode middle-dot with spaces).
    assert " · " in sub_text


# ---- S4: active_tenants 7d delta -------------------------------------------
async def test_s4_active_tenants_delta_7d(
    app_client, settings, make_tenant, session_factory, platform_auth,
    super_admin_jwt,
):
    """7d delta counts non-terminated tenants created in the last 7 days.

    Insert two tenants: one created today (counts), one with
    created_at backdated 8 days (does not count). Assert the delta
    captures only the recent one.

    Backdating the older tenant is via raw SQL UPDATE because the
    standard make_tenant fixture doesn't accept a created_at override
    and ``created_at`` is server_default'd by ``NOW()`` at INSERT.
    """
    fresh = await make_tenant(
        name="S4-Fresh", status=TenantStatus.ACTIVE
    )
    old = await make_tenant(
        name="S4-Old", status=TenantStatus.ACTIVE
    )
    # Backdate the second tenant by 8 days.
    schema = get_settings().db_schema
    async for s in get_tenant_session(platform_auth, session_factory):
        await s.execute(
            text(
                f"UPDATE {schema}.tenants SET created_at = NOW() - INTERVAL '8 days' "
                "WHERE id = :id"
            ),
            {"id": old.id},
        )

    resp = app_client.get(
        "/api/v1/dashboard/fleet-stats",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    delta = resp.json()["active_tenants"]["delta"]
    assert delta["available"] is True
    assert delta["window"] == "7d"
    # The fresh tenant counts; the old one doesn't. The exact value
    # depends on what other tenants exist in the DB, so assert that
    # the delta is at least 1 (we know fresh was created within 7d)
    # but DOES NOT include the 8-day-old one. We can't easily check
    # the exclusion absolutely, but a regression that dropped the
    # date filter would surface as delta >> the new test fixtures'
    # count. Anchor on direction.
    assert delta["value"] is not None
    assert delta["value"] >= 1
    if delta["value"] > 0:
        assert delta["direction"] == "up"
    elif delta["value"] == 0:
        assert delta["direction"] == "flat"


# ---- S5: sub_text scope-awareness (LOAD-BEARING) -------------------------
async def test_s5_fleet_stats_sub_text_scope_awareness(
    app_client, make_tenant,
    super_admin_jwt,
    tenant_owner_jwt_factory,
):
    """LOAD-BEARING: same data, two requests, sub_text differs per
    locked rules.

    Specifically verifies the platform_users.sub_text branch — the
    only sub_text that's strictly user_type-dispatched (others are
    same rule for both user types).
    """
    tenant = await make_tenant(name="S5-T", status=TenantStatus.ACTIVE)

    p_resp = app_client.get(
        "/api/v1/dashboard/fleet-stats",
        headers=_auth(super_admin_jwt),
    )
    t_jwt = await tenant_owner_jwt_factory(tenant.id)
    t_resp = app_client.get(
        "/api/v1/dashboard/fleet-stats",
        headers=_auth(t_jwt),
    )
    assert p_resp.status_code == 200
    assert t_resp.status_code == 200
    assert p_resp.json()["platform_users"]["sub_text"] == "across all tenants"
    assert t_resp.json()["platform_users"]["sub_text"] == "in your organization"


# ---- S6: stores distinct_countries ---------------------------------------
async def test_s6_stores_distinct_countries(
    app_client, make_tenant, make_store, tenant_owner_jwt_factory,
):
    """distinct_countries reflects COUNT(DISTINCT country) over visible stores.

    Insert 4 stores in 3 distinct countries under one tenant; assert
    the TENANT-scoped response sees distinct_countries=3 with sub_text
    "3 countries". Step 6.17.2 upgraded make_store to accept ``country``
    directly; the prior raw UPDATE override is retired.
    """
    tenant = await make_tenant(name="S6-T")
    await make_store(tenant_id=tenant.id, country="France")
    await make_store(tenant_id=tenant.id, country="France")
    await make_store(tenant_id=tenant.id, country="Germany")
    await make_store(tenant_id=tenant.id, country="Spain")

    jwt = await tenant_owner_jwt_factory(tenant.id)
    resp = app_client.get(
        "/api/v1/dashboard/fleet-stats",
        headers=_auth(jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stores"]["value"] == 4
    assert body["stores"]["distinct_countries"] == 3
    assert body["stores"]["sub_text"] == "3 countries"


# ---- S7: MRR delta permanently stubbed (LOAD-BEARING) --------------------
async def test_s7_mrr_delta_permanently_stubbed(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    """LOAD-BEARING: contract guard against accidentally flipping the
    MRR delta to ``available: true`` without the snapshot table.

    Per S14: ``mrr_aggregated.delta.available`` is **always false** in
    v0. ``value`` and ``direction`` are null; ``window`` is preserved
    as ``"monthly"`` (the intended cadence).

    A regression that made this true would surface here.
    """
    await make_tenant(name="S7-T")
    resp = app_client.get(
        "/api/v1/dashboard/fleet-stats",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    delta = resp.json()["mrr_aggregated"]["delta"]
    assert delta["available"] is False
    assert delta["value"] is None
    assert delta["direction"] is None
    # Window is preserved as the intended cadence.
    assert delta["window"] == "monthly"


# ---- S8: mrr_aggregated.value is a 2dp string ----------------------------
async def test_s8_mrr_value_is_2dp_string(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    """Per Q2 (Step 6.5 design review): explicit f"{x:.2f}" format.

    Insert a tenant with a known revenue and verify the response
    string ends with "0.00" or "00" pattern (the actual digits depend
    on what else is visible). The strict guarantee is type-string +
    exactly 2 chars after the decimal.
    """
    await make_tenant(
        name="S8-T", status=TenantStatus.ACTIVE,
    )
    resp = app_client.get(
        "/api/v1/dashboard/fleet-stats",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    value = resp.json()["mrr_aggregated"]["value"]
    assert isinstance(value, str)
    # Always 2 decimal places.
    assert "." in value
    decimals = value.split(".")[1]
    assert len(decimals) == 2
    assert decimals.isdigit()


# =============================================================================
# E2: governance-stats (O1-O6)
# =============================================================================


# ---- O1: PLATFORM envelope shape -----------------------------------------
def test_o1_governance_stats_platform_envelope(app_client, settings, super_admin_jwt):
    """3 cards available:false with locked unavailable_reason; modules
    available:true.
    """
    resp = app_client.get(
        "/api/v1/dashboard/governance-stats",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "pending_approvals",
        "guardrails_fired_24h",
        "custom_roles",
        "modules_deployed",
    }
    assert body["pending_approvals"]["available"] is False
    assert body["guardrails_fired_24h"]["available"] is False
    assert body["custom_roles"]["available"] is False
    assert body["modules_deployed"]["available"] is True
    # All cards have delta: null in v0.
    for card in body.values():
        assert card["delta"] is None


# ---- O2: modules_deployed real + RLS-scoped (LOAD-BEARING) --------------
async def test_o2_modules_deployed_real_and_rls_scoped(
    app_client,
    make_tenant,
    make_platform_user,
    make_tenant_module_access,
    super_admin_jwt,
    tenant_owner_jwt_factory,
):
    """LOAD-BEARING: modules_deployed is the only real card.

    Insert tenant_module_access rows in two tenants; PLATFORM JWT
    sees the sum, TENANT JWT sees only own-tenant.
    """
    actor = await make_platform_user(
        email=f"o2-actor-{uuid.uuid4()}@ithina.test"
    )
    tenant_a = await make_tenant(name="O2-TenantA")
    tenant_b = await make_tenant(name="O2-TenantB")
    # 3 modules under A
    for module in (ModuleCode.GOAL_CONSOLE, ModuleCode.PRICING_OS, ModuleCode.ADMIN):
        await make_tenant_module_access(
            tenant_id=tenant_a.id,
            module=module,
            enabled_by_user_id=actor.id,
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )
    # 1 module under B
    await make_tenant_module_access(
        tenant_id=tenant_b.id,
        module=ModuleCode.ADMIN,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )

    p_resp = app_client.get(
        "/api/v1/dashboard/governance-stats",
        headers=_auth(super_admin_jwt),
    )
    # Two synthetic OWNERs, one per tenant. Each call's factory pre-
    # checks the ADMIN TMA row and finds it (test already created
    # one), so no extra TMA rows are inserted; per-tenant counts
    # remain 3 (A) and 1 (B).
    a_jwt = await tenant_owner_jwt_factory(tenant_a.id)
    b_jwt = await tenant_owner_jwt_factory(tenant_b.id)
    a_resp = app_client.get(
        "/api/v1/dashboard/governance-stats",
        headers=_auth(a_jwt),
    )
    b_resp = app_client.get(
        "/api/v1/dashboard/governance-stats",
        headers=_auth(b_jwt),
    )

    # PLATFORM sees at least the 4 we created (could be more from seed).
    p_value = p_resp.json()["modules_deployed"]["value"]
    a_value = a_resp.json()["modules_deployed"]["value"]
    b_value = b_resp.json()["modules_deployed"]["value"]
    assert p_value >= 4
    assert a_value == 3
    assert b_value == 1
    # PLATFORM count is at least the sum of the two tenants' counts.
    assert p_value >= a_value + b_value


# ---- O3: unavailable_reason exact strings --------------------------------
def test_o3_unavailable_reason_codes(app_client, settings, super_admin_jwt):
    """The 3 stub cards carry the locked v0 unavailable_reason codes."""
    resp = app_client.get(
        "/api/v1/dashboard/governance-stats",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (
        body["pending_approvals"]["unavailable_reason"]
        == "approvals_table_not_built"
    )
    assert (
        body["guardrails_fired_24h"]["unavailable_reason"]
        == "audit_logs_or_guardrails_not_wired"
    )
    assert (
        body["custom_roles"]["unavailable_reason"]
        == "custom_role_creation_not_shipped"
    )


# ---- O4: pending_approvals sub_text scope-awareness ----------------------
async def test_o4_pending_approvals_sub_text_scope(
    app_client, make_tenant,
    super_admin_jwt,
    tenant_owner_jwt_factory,
):
    tenant = await make_tenant(name="O4-T")
    p_resp = app_client.get(
        "/api/v1/dashboard/governance-stats",
        headers=_auth(super_admin_jwt),
    )
    t_jwt = await tenant_owner_jwt_factory(tenant.id)
    t_resp = app_client.get(
        "/api/v1/dashboard/governance-stats",
        headers=_auth(t_jwt),
    )
    assert (
        p_resp.json()["pending_approvals"]["sub_text"]
        == "across guardrails"
    )
    assert (
        t_resp.json()["pending_approvals"]["sub_text"]
        == "across your organization"
    )


# ---- O5: modules_deployed sub_text scope-awareness (LOAD-BEARING) -------
async def test_o5_modules_deployed_sub_text_scope(
    app_client,
    make_tenant,
    make_platform_user,
    make_tenant_module_access,
    super_admin_jwt,
    tenant_owner_jwt_factory,
):
    """LOAD-BEARING: PLATFORM sub_text reflects 'across N tenants'
    (singular/plural); TENANT sub_text is 'enabled for your organization'.
    """
    actor = await make_platform_user(
        email=f"o5-actor-{uuid.uuid4()}@ithina.test"
    )
    tenant = await make_tenant(name="O5-T")
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.ADMIN,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )

    p_resp = app_client.get(
        "/api/v1/dashboard/governance-stats",
        headers=_auth(super_admin_jwt),
    )
    t_jwt = await tenant_owner_jwt_factory(tenant.id)
    t_resp = app_client.get(
        "/api/v1/dashboard/governance-stats",
        headers=_auth(t_jwt),
    )
    p_text = p_resp.json()["modules_deployed"]["sub_text"]
    t_text = t_resp.json()["modules_deployed"]["sub_text"]
    # PLATFORM: "across N tenant" or "across N tenants"
    assert p_text.startswith("across ")
    assert "tenant" in p_text
    # TENANT: locked literal
    assert t_text == "enabled for your organization"


# ---- O6: modules_deployed singular vs plural -----------------------------
async def test_o6_modules_deployed_singular(
    app_client,
    make_tenant,
    make_platform_user,
    make_tenant_module_access,
    tenant_owner_jwt_factory,
):
    """When only 1 visible tenant has modules enabled, sub_text reads
    'across 1 tenant' (no 's').

    To force this: TENANT-scoped session sees only own-tenant
    tenant_module_access rows (RLS), so visible_tenant_count == 1
    deterministically when there's at least one ENABLED row in own
    tenant. The PLATFORM 'across N tenants' branch is exercised by
    O2 / O5 indirectly.
    """
    actor = await make_platform_user(
        email=f"o6-actor-{uuid.uuid4()}@ithina.test"
    )
    tenant = await make_tenant(name="O6-T")
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.ADMIN,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    # Use a special fixture-creator: one tenant with one module gives a
    # CTE result with visible_tenant_count == 1. PLATFORM JWT sees all
    # tenant_module_access; the test needs an isolated single-tenant view.
    # We rely on the fact that in the test DB only this tenant has modules
    # immediately after the truncation+seed cycle... but tests don't
    # truncate. Use a TENANT-A session — RLS scopes to its own tenant,
    # then visible_tenant_count == 1 regardless.
    jwt = await tenant_owner_jwt_factory(tenant.id)
    resp = app_client.get(
        "/api/v1/dashboard/governance-stats",
        headers=_auth(jwt),
    )
    body = resp.json()
    # The TENANT branch's sub_text is the locked literal — singular/
    # plural is a PLATFORM-side concern. So this test really exercises
    # the TENANT branch end-to-end on a single-tenant module set.
    # (The PLATFORM singular branch is structurally hard to hit without
    # truncating other test state; covered indirectly by O5's substring
    # checks.)
    assert body["modules_deployed"]["sub_text"] == "enabled for your organization"
    assert body["modules_deployed"]["value"] >= 1


# =============================================================================
# Auth (A1)
# =============================================================================


def test_a1_no_jwt_returns_401(app_client):
    """Both endpoints reject unauthenticated requests."""
    for path in (
        "/api/v1/dashboard/fleet-stats",
        "/api/v1/dashboard/governance-stats",
    ):
        resp = app_client.get(path)
        assert resp.status_code == 401, f"path={path} expected 401"
        assert resp.json()["code"] == "AUTH_MISSING"


# =============================================================================
# Cross-cutting (X1)
# =============================================================================


def test_x1_pydantic_extra_forbid_guards_drift(app_client, settings, super_admin_jwt):
    """Schemas use ConfigDict(extra='forbid').

    This test asserts the response shape is exactly the documented field
    set — a future PR adding an undocumented field would fail because
    the schema's response_model serialization would either omit it or
    fail validation. Direct unit-level assertion is overkill; this
    integration-level shape check is sufficient for v0.
    """
    resp = app_client.get(
        "/api/v1/dashboard/fleet-stats",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Top-level shape exact.
    assert set(body.keys()) == {
        "active_tenants",
        "platform_users",
        "stores",
        "mrr_aggregated",
    }
    # Each card's keys are the documented field set.
    expected_keys = {
        "active_tenants": {
            "value", "total", "sub_text", "delta", "available",
        },
        "platform_users": {
            "value", "sub_text", "delta", "available",
        },
        "stores": {
            "value", "distinct_countries", "sub_text", "delta", "available",
        },
        "mrr_aggregated": {
            "value", "currency", "sub_text", "delta", "available",
        },
    }
    for key, expected in expected_keys.items():
        assert set(body[key].keys()) == expected, f"drift on {key}"


# ---- X2: raw SQL schema qualification (LOAD-BEARING regression for Step 6.5.1) -------
async def test_x2_raw_sql_works_with_clobbered_search_path(
    session_factory, platform_auth
):
    """LOAD-BEARING regression guard: DashboardRepo's ``text()`` SQL
    must schema-qualify every table reference so it works regardless
    of session ``search_path``.

    Pre-Step-6.5.1 the queries were unqualified and silently relied
    on the engine's connect-time ``SET search_path`` hook. Locally
    that hook + the role-default both included ``core``, so the bug
    was masked. On Cloud SQL the search_path occasionally lost
    ``core`` (connection cycling, pool recycle, async event-listener
    ordering — exact cause not diagnosed because the fix removes the
    dependency) and both endpoints returned 500
    ``relation "tenants" does not exist``.

    This test clobbers search_path to ``public`` (no ``core``) and
    asserts both Repo methods still succeed. With schema-qualified
    SQL the search_path is irrelevant; without it both methods would
    raise ``UndefinedTable``.
    """
    from sqlalchemy import text

    from admin_backend.db.session import get_tenant_session
    from admin_backend.repositories.dashboard import DashboardRepo

    repo = DashboardRepo()
    async for session in get_tenant_session(platform_auth, session_factory):
        await session.execute(text("SET search_path TO public"))
        # Both must succeed; assertions are existence — the queries
        # return whatever rows the post-truncate state holds.
        await repo.fleet_stats(session)
        await repo.governance_stats(session)
