"""Integration tests for the Module Access read endpoints (Step 6.7).

Real Postgres, real schema, real RLS, real router via FastAPI's
TestClient. JWTs minted via Step 2.1's ``make_test_jwt``. Mirrors the
shape used by ``test_dashboard_router.py`` and ``test_rbac_router.py``.

Test ID convention:
  M*  /modules                                          (5 tests)
  X*  /matrix                                           (8 tests)
  A*  Auth                                              (1 test)
                                                       ----
                                                        14

Five LOAD-BEARING tests:
  M2  TENANT JWT /modules aggregate counts collapse — RLS-on-aggregate.
      Without scoping a TENANT user could read fleet-wide module counts.
  M3  Module ordering position-alignment across /modules.items[i] and
      /matrix.items[*].cells[i]. Frontend reconciles by index; if the
      orderings drift the UI mis-paints.
  M4  Server-side label resolution end-to-end against the seeded
      tenant_tier / tenant_status / module_code lookup rows.
  X1  TENANT JWT /matrix returns exactly 1 row (own tenant only).
      Same anti-information-disclosure intent as RLS-as-404.
  X2  Synthesized DISABLED cells respect RLS — for a tenant with N
      ENABLED rows in tenant_module_access, ``cells[]`` always has 6
      entries, with the absent modules rendering as DISABLED.

The DB may be in a partially-seeded state when these run (Step 3.5's
loader or partial state from prior runs). Tests that count rows across
the catalogue use fixture-created entities and ``>=``-style assertions
where absolute counts would be brittle.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings
from admin_backend.main import create_app
from admin_backend.models.tenant import TenantStatus, TenantTier
from admin_backend.models.tenant_module_access import ModuleCode


# Locked module ordering post-Step-6.7 (seed migration ``2fdc4bc9f4cb``),
# minus ROOS retired on 2026-05-12. Mirrors the live ``lookups`` row
# set after the seed loader's ROOS cleanup; assertions on
# position-alignment anchor on this sequence. display_order values
# remain at 2-6 (no renumber); the sequence is contiguous on order
# even though the underlying integers skip 1.
_EXPECTED_MODULE_ORDER: list[str] = [
    "GOAL_CONSOLE",
    "PRICING_OS",
    "PERISHABLES_ASSISTANT",
    "PROMOTIONS_ASSISTANT",
    "ADMIN",
]


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,  # type: ignore[no-any-unimported]
    session_factory: Any,  # type: ignore[no-any-unimported]
) -> Iterator[TestClient]:
    """TestClient against a real app with real engine/session_factory.

    Bypasses the lifespan (would re-construct an engine in a different
    event loop than the test). Same pattern as the other router-test
    modules.
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
# E1: /modules (M1-M5)
# =============================================================================


# ---- M1: PLATFORM envelope shape -------------------------------------------
def test_m1_modules_platform_envelope(app_client, settings, super_admin_jwt):
    """6 cards in locked ordering with the expected field set per card."""
    resp = app_client.get(
        "/api/v1/module-access/modules",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items"}
    assert len(body["items"]) == 5

    # Locked module ordering anchored on lookups.display_order.
    actual_codes = [item["module_code"] for item in body["items"]]
    assert actual_codes == _EXPECTED_MODULE_ORDER

    expected_keys = {
        "module_code",
        "module_label",
        "enabled_count",
        "total_active_trial_tenants",
    }
    for item in body["items"]:
        assert set(item.keys()) == expected_keys
        # All counts are non-negative ints; total is shared on every card.
        assert isinstance(item["enabled_count"], int)
        assert item["enabled_count"] >= 0
        assert isinstance(item["total_active_trial_tenants"], int)
        assert item["total_active_trial_tenants"] >= 0

    # total_active_trial_tenants is a row-set property — same on every card.
    totals = {item["total_active_trial_tenants"] for item in body["items"]}
    assert len(totals) == 1, (
        f"total_active_trial_tenants should be uniform across cards; got "
        f"{totals}"
    )


# ---- M2: TENANT JWT aggregate collapse (LOAD-BEARING) ----------------------
async def test_m2_modules_tenant_aggregate_collapse(
    app_client,
    make_platform_user,
    make_tenant,
    make_tenant_module_access,
    tenant_owner_jwt_factory,
):
    """LOAD-BEARING: TENANT JWT counts collapse to own tenant only.

    Construct two ACTIVE tenants A and B, enable GOAL_CONSOLE on both,
    then query /modules under TENANT-A's JWT. Every card's
    ``total_active_trial_tenants`` must be 1 (A is the only visible
    tenant), and GOAL_CONSOLE's ``enabled_count`` must be 1 (A's row,
    not the cross-tenant 2).
    """
    actor = await make_platform_user(
        email=f"m2-actor-{uuid.uuid4()}@ithina.test"
    )
    tenant_a = await make_tenant(
        name="M2-TenantA", status=TenantStatus.ACTIVE
    )
    tenant_b = await make_tenant(
        name="M2-TenantB", status=TenantStatus.ACTIVE
    )
    for tid in (tenant_a.id, tenant_b.id):
        await make_tenant_module_access(
            tenant_id=tid,
            module=ModuleCode.GOAL_CONSOLE,
            enabled_by_user_id=actor.id,
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )

    jwt = await tenant_owner_jwt_factory(tenant_a.id)
    resp = app_client.get(
        "/api/v1/module-access/modules",
        headers=_auth(jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    # Every card sees only tenant A as the visible denominator.
    for item in items:
        assert item["total_active_trial_tenants"] == 1, (
            f"card {item['module_code']} leaked cross-tenant denominator: "
            f"{item}"
        )
        # enabled_count is 0 or 1 — TENANT scope can't exceed 1. The
        # factory inserts an ENABLED ADMIN TMA in tenant_a, so the
        # ADMIN card's enabled_count is 1 in this assertion (still in
        # the (0, 1) range).
        assert item["enabled_count"] in (0, 1), (
            f"card {item['module_code']} enabled_count out of TENANT bounds: "
            f"{item}"
        )

    # GOAL_CONSOLE specifically must be 1 (we enabled it on tenant A).
    goal_console = next(
        i for i in items if i["module_code"] == "GOAL_CONSOLE"
    )
    assert goal_console["enabled_count"] == 1


# ---- M3: position-alignment across /modules and /matrix (LOAD-BEARING) -----
async def test_m3_modules_matrix_position_alignment(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    """LOAD-BEARING: ``/modules.items[i].module_code ==
    /matrix.items[*].cells[i].module_code`` for every i.

    Frontend reconciles cells to modules by index; if the orderings
    drift the UI mis-paints (PRICING_OS column header rendering above
    GOAL_CONSOLE cells, etc.). Two requests, one PLATFORM JWT.
    """
    # Need at least one matrix row to read cells from.
    await make_tenant(name="M3-T", status=TenantStatus.ACTIVE)

    jwt = super_admin_jwt
    modules_resp = app_client.get(
        "/api/v1/module-access/modules", headers=_auth(jwt)
    )
    matrix_resp = app_client.get(
        "/api/v1/module-access/matrix?limit=5", headers=_auth(jwt)
    )
    assert modules_resp.status_code == 200
    assert matrix_resp.status_code == 200

    modules_order = [
        item["module_code"] for item in modules_resp.json()["items"]
    ]
    matrix_items = matrix_resp.json()["items"]
    assert matrix_items, "matrix returned empty; M3 needs at least 1 row"
    for row in matrix_items:
        cells_order = [c["module_code"] for c in row["cells"]]
        assert cells_order == modules_order, (
            f"cells/modules ordering drift on tenant {row['name']}: "
            f"cells={cells_order} modules={modules_order}"
        )


# ---- M4: server-side label resolution (LOAD-BEARING) ----------------------
async def test_m4_label_resolution_against_seeded_lookups(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    """LOAD-BEARING: ``module_label``, ``tier_label``, ``status_label``
    populate from the seeded lookup rows (verifies the JOIN works
    end-to-end against this step's migration's seed).

    Locks the contract that labels degrade to raw enum codes only when
    a lookup row is missing — under normal seed they should be the
    human display strings.
    """
    tenant = await make_tenant(
        name="M4-T",
        status=TenantStatus.ACTIVE,
        tier=TenantTier.ENTERPRISE,
    )

    # /modules: spot-check known-good labels.
    modules_resp = app_client.get(
        "/api/v1/module-access/modules",
        headers=_auth(super_admin_jwt),
    )
    items = modules_resp.json()["items"]
    label_by_code = {it["module_code"]: it["module_label"] for it in items}
    # Three high-signal labels. Raw enum codes (e.g., "PRICING_OS") would
    # mean the JOIN failed and COALESCE fell back.
    assert label_by_code["PRICING_OS"] == "Pricing OS"
    assert label_by_code["PERISHABLES_ASSISTANT"] == "Perishables Assistant"
    assert label_by_code["GOAL_CONSOLE"] == "Goal Console"

    # /matrix: tier_label and status_label for our seeded ENTERPRISE/ACTIVE
    # tenant. q-filter narrows to the fixture row to avoid ordering noise.
    matrix_resp = app_client.get(
        "/api/v1/module-access/matrix?q=M4-T",
        headers=_auth(super_admin_jwt),
    )
    matrix_items = matrix_resp.json()["items"]
    row = next(r for r in matrix_items if r["tenant_id"] == str(tenant.id))
    assert row["tier"] == "ENTERPRISE"
    assert row["tier_label"] == "Enterprise"
    assert row["status"] == "ACTIVE"
    assert row["status_label"] == "Active"


# ---- M5: ENABLED-only counting on /modules.enabled_count -------------------
async def test_m5_enabled_count_excludes_disabled_rows(
    app_client,
    make_platform_user,
    make_tenant,
    make_tenant_module_access,
    tenant_owner_jwt_factory,
):
    """``enabled_count`` reflects ENABLED rows only.

    DISABLED ``tenant_module_access`` rows must NOT pad the count.
    This guards against a regression where the SQL drops the
    ``WHERE tma.status = 'ENABLED'`` predicate.

    The DISABLED-status fixture is keyed on GOAL_CONSOLE (not ADMIN);
    the factory needs ENABLED ADMIN to satisfy the gate, and a
    pre-existing DISABLED ADMIN would defeat that per the factory's
    caller contract (see conftest docstring).
    """
    from datetime import datetime, timezone

    actor = await make_platform_user(
        email=f"m5-actor-{uuid.uuid4()}@ithina.test"
    )
    tenant = await make_tenant(name="M5-T", status=TenantStatus.ACTIVE)
    # One ENABLED PRICING_OS, one DISABLED GOAL_CONSOLE.
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.PRICING_OS,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    await make_tenant_module_access(
        tenant_id=tenant.id,
        module=ModuleCode.GOAL_CONSOLE,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
        status=__import__(
            "admin_backend.models.tenant_module_access",
            fromlist=["ModuleAccessStatus"],
        ).ModuleAccessStatus.DISABLED,
        disabled_at=datetime.now(tz=timezone.utc),
        disabled_by_user_id=actor.id,
    )

    jwt = await tenant_owner_jwt_factory(tenant.id)
    resp = app_client.get(
        "/api/v1/module-access/modules",
        headers=_auth(jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    cards = {it["module_code"]: it for it in items}
    # PRICING_OS: ENABLED — counts.
    assert cards["PRICING_OS"]["enabled_count"] == 1
    # GOAL_CONSOLE: DISABLED — does NOT count.
    assert cards["GOAL_CONSOLE"]["enabled_count"] == 0


# =============================================================================
# E2: /matrix (X1-X9)
# =============================================================================


# ---- X1: TENANT JWT 1-row (LOAD-BEARING) ----------------------------------
async def test_x1_matrix_tenant_one_row(
    app_client, make_tenant, tenant_owner_jwt_factory
):
    """LOAD-BEARING: TENANT JWT sees exactly 1 row (own tenant only).

    Construct 3 tenants; under TENANT-A's JWT only A appears.
    Without RLS scoping a TENANT user could enumerate the fleet.
    """
    a = await make_tenant(name="X1-A", status=TenantStatus.ACTIVE)
    await make_tenant(name="X1-B", status=TenantStatus.ACTIVE)
    await make_tenant(name="X1-C", status=TenantStatus.ACTIVE)

    jwt = await tenant_owner_jwt_factory(a.id)
    resp = app_client.get(
        "/api/v1/module-access/matrix",
        headers=_auth(jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["tenant_id"] == str(a.id)
    assert body["items"][0]["name"] == "X1-A"


# ---- X2: cell synthesis under RLS (LOAD-BEARING) --------------------------
async def test_x2_matrix_cell_synthesis_under_rls(
    app_client,
    settings,
    make_platform_user,
    make_tenant,
    make_tenant_module_access,
    super_admin_jwt,
):
    """LOAD-BEARING: ``cells[]`` is always 5 (post-ROOS-retirement
    2026-05-12), with absent + DISABLED rows both rendering as DISABLED.

    Tenant T has 3 ENABLED rows (PRICING_OS, GOAL_CONSOLE, ADMIN).
    Expected: cells[] has 5 entries; those 3 codes are ENABLED, the
    other 2 (PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT) are
    DISABLED — synthesised by the CROSS JOIN, not present in
    tenant_module_access at all.
    """
    actor = await make_platform_user(
        email=f"x2-actor-{uuid.uuid4()}@ithina.test"
    )
    tenant = await make_tenant(name="X2-T", status=TenantStatus.ACTIVE)
    enabled_modules = (
        ModuleCode.PRICING_OS,
        ModuleCode.GOAL_CONSOLE,
        ModuleCode.ADMIN,
    )
    for module in enabled_modules:
        await make_tenant_module_access(
            tenant_id=tenant.id,
            module=module,
            enabled_by_user_id=actor.id,
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )

    resp = app_client.get(
        f"/api/v1/module-access/matrix?q=X2-T",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    row = next(r for r in items if r["tenant_id"] == str(tenant.id))
    assert len(row["cells"]) == 5

    cell_status_by_code = {c["module_code"]: c["status"] for c in row["cells"]}
    # Three ENABLED.
    for m in enabled_modules:
        assert cell_status_by_code[m.value] == "ENABLED", (
            f"expected ENABLED for {m.value}, got "
            f"{cell_status_by_code[m.value]}"
        )
    # Two synthesised DISABLED (no tenant_module_access row at all).
    for m in (
        ModuleCode.PERISHABLES_ASSISTANT,
        ModuleCode.PROMOTIONS_ASSISTANT,
    ):
        assert cell_status_by_code[m.value] == "DISABLED", (
            f"expected synthesised DISABLED for {m.value}, got "
            f"{cell_status_by_code[m.value]}"
        )


# ---- X3: PLATFORM envelope shape ------------------------------------------
async def test_x3_matrix_platform_envelope(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    """N rows, each with 5 cells (ROOS retired 2026-05-12), plus
    pagination block."""
    await make_tenant(name="X3-T", status=TenantStatus.ACTIVE)
    resp = app_client.get(
        "/api/v1/module-access/matrix?limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items", "pagination"}
    assert set(body["pagination"].keys()) == {"total", "offset", "limit"}
    assert body["pagination"]["limit"] == 10
    assert body["pagination"]["offset"] == 0
    assert isinstance(body["pagination"]["total"], int)
    for row in body["items"]:
        assert set(row.keys()) == {
            "tenant_id",
            "name",
            "tier",
            "tier_label",
            "status",
            "status_label",
            "cells",
        }
        assert len(row["cells"]) == 5


# ---- X4: sort=name_asc orders alphabetically ------------------------------
async def test_x4_matrix_sort_name_asc(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    """``sort=name_asc`` orders matching rows by name ascending."""
    # Distinct, sortable prefix to insulate from other test data.
    await make_tenant(name="X4-Charlie", status=TenantStatus.ACTIVE)
    await make_tenant(name="X4-Alpha",   status=TenantStatus.ACTIVE)
    await make_tenant(name="X4-Bravo",   status=TenantStatus.ACTIVE)

    resp = app_client.get(
        "/api/v1/module-access/matrix?q=X4-&sort=name_asc&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    names = [r["name"] for r in resp.json()["items"]]
    # Filter to our X4-* prefixes (q-search may catch nothing else but
    # a defensive filter keeps the assertion stable).
    x4_names = [n for n in names if n.startswith("X4-")]
    assert x4_names == ["X4-Alpha", "X4-Bravo", "X4-Charlie"]


# ---- X5: invalid sort -> 400 INVALID_SORT_KEY -----------------------------
def test_x5_matrix_invalid_sort_400(app_client, settings, super_admin_jwt):
    resp = app_client.get(
        "/api/v1/module-access/matrix?sort=garbage_desc",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_SORT_KEY"


# ---- X6: filter tier=ENTERPRISE -------------------------------------------
async def test_x6_matrix_filter_tier(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    """``tier=ENTERPRISE`` returns only enterprise tenants."""
    await make_tenant(
        name="X6-Ent",
        status=TenantStatus.ACTIVE,
        tier=TenantTier.ENTERPRISE,
    )
    await make_tenant(
        name="X6-SMB",
        status=TenantStatus.ACTIVE,
        tier=TenantTier.SMB,
    )

    resp = app_client.get(
        "/api/v1/module-access/matrix?q=X6-&tier=ENTERPRISE&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    rows = resp.json()["items"]
    x6_ent = [r for r in rows if r["name"].startswith("X6-")]
    assert len(x6_ent) == 1
    assert x6_ent[0]["name"] == "X6-Ent"
    assert x6_ent[0]["tier"] == "ENTERPRISE"


# ---- X7: filter status=ACTIVE excludes ONBOARDING -------------------------
async def test_x7_matrix_filter_status(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    """``status=ACTIVE`` returns only ACTIVE tenants among the matched set."""
    await make_tenant(name="X7-Act", status=TenantStatus.ACTIVE)
    await make_tenant(name="X7-Onb", status=TenantStatus.ONBOARDING)

    resp = app_client.get(
        "/api/v1/module-access/matrix?q=X7-&status=ACTIVE&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    rows = [r for r in resp.json()["items"] if r["name"].startswith("X7-")]
    assert len(rows) == 1
    assert rows[0]["name"] == "X7-Act"
    assert rows[0]["status"] == "ACTIVE"


# ---- X8: q-search ILIKE substring ----------------------------------------
async def test_x8_matrix_q_search(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    """``q=<substring>`` uses case-insensitive ILIKE on tenants.name."""
    await make_tenant(name="X8-Searchable-Buc", status=TenantStatus.ACTIVE)
    await make_tenant(name="X8-OtherName",      status=TenantStatus.ACTIVE)

    resp = app_client.get(
        "/api/v1/module-access/matrix?q=searchable&limit=10",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    rows = resp.json()["items"]
    # ILIKE is case-insensitive: 'searchable' matches 'Searchable-Buc'.
    assert any(r["name"] == "X8-Searchable-Buc" for r in rows)
    assert not any(r["name"] == "X8-OtherName" for r in rows)


# ---- X9: pagination total + slice ----------------------------------------
async def test_x9_matrix_pagination(
    app_client, settings, make_tenant,
    super_admin_jwt,
):
    """``limit`` clips items but ``total`` reflects full filter set.

    Insert 3 X9-* tenants; with limit=2 + offset=1 we should see
    exactly 2 rows in items but ``total >= 3`` (other test rows may
    leak in via the q-search; we anchor on the X9-prefix ones).
    """
    await make_tenant(name="X9-T1", status=TenantStatus.ACTIVE)
    await make_tenant(name="X9-T2", status=TenantStatus.ACTIVE)
    await make_tenant(name="X9-T3", status=TenantStatus.ACTIVE)

    resp = app_client.get(
        "/api/v1/module-access/matrix?q=X9-&sort=name_asc&limit=2&offset=1",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200
    body = resp.json()
    # All three X9 rows match the filter.
    assert body["pagination"]["total"] == 3
    assert body["pagination"]["limit"] == 2
    assert body["pagination"]["offset"] == 1
    assert len(body["items"]) == 2
    # Sort stable; offset=1 skips the first (T1), so we see T2 and T3.
    names = [r["name"] for r in body["items"]]
    assert names == ["X9-T2", "X9-T3"]


# =============================================================================
# Auth (A1)
# =============================================================================


def test_a1_no_jwt_returns_401(app_client):
    """Both endpoints reject unauthenticated requests."""
    for path in (
        "/api/v1/module-access/modules",
        "/api/v1/module-access/matrix",
    ):
        resp = app_client.get(path)
        assert resp.status_code == 401, f"path={path} expected 401"
        assert resp.json()["code"] == "AUTH_MISSING"
