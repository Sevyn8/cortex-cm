"""Integration tests for GET /api/v1/lookups (Step 3.6).

Real Postgres, real router via FastAPI's sync TestClient. Mirrors the
shape used by ``test_tenants_router.py``: the ``app_client`` fixture
+ ``_platform_jwt(settings)`` helper + ``_auth(jwt)`` helper.

Coverage:
  L1 — All requested categories return their seeded rows in
       display_order (tenant_tier, tenant_region, tenant_status,
       tenant_industry, module_code; country deferred per the
       known follow-up).
  L2 — Unknown list_name returns an empty array (predictable shape).
  L3 — No JWT returns 401.
  L4 — Empty/whitespace-only ``lists`` param returns ``{lookups: {}}``
       (200, not 422). Catches the natural front-end shape of "no
       lists chosen yet."
"""
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings
from admin_backend.main import create_app


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,  # type: ignore[no-any-unimported]
    session_factory: Any,  # type: ignore[no-any-unimported]
) -> Iterator[TestClient]:
    """TestClient against a real app with real engine/session_factory.

    Bypasses the lifespan (which would re-construct an engine in a
    different event loop than the test). Mirrors the pattern from
    ``test_tenants_router.py``.
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


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


# ---- L1: all requested categories return their seeded rows ----------------
def test_l1_get_lookups_returns_all_requested_lists(app_client, settings):
    """All 5 categories return their seeded rows in display_order.

    The 4 PG-enum-backed categories were seeded by Step 3.6's
    migration (0644a4186e48); module_code was seeded by Step 3.4.5.
    country is deferred (see Step 3.6 known follow-up); not in the
    request here.
    """
    resp = app_client.get(
        "/api/v1/lookups",
        params={
            "lists": (
                "tenant_tier,tenant_region,tenant_status,"
                "tenant_industry,module_code"
            )
        },
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    body = resp.json()
    lookups = body["lookups"]

    # All 5 categories present.
    assert set(lookups.keys()) == {
        "tenant_tier",
        "tenant_region",
        "tenant_status",
        "tenant_industry",
        "module_code",
    }

    # tenant_tier: 4 rows in display_order.
    tiers = lookups["tenant_tier"]
    assert [t["code"] for t in tiers] == [
        "ENTERPRISE",
        "MID_MARKET",
        "SMB",
        "SINGLE_STORE",
    ]
    assert tiers[0]["display_name"] == "Enterprise"
    assert tiers[0]["display_order"] == 1

    # tenant_region: 2 rows.
    assert len(lookups["tenant_region"]) == 2

    # tenant_status: 5 rows in display_order.
    assert [s["code"] for s in lookups["tenant_status"]] == [
        "ONBOARDING",
        "TRIAL",
        "ACTIVE",
        "SUSPENDED",
        "TERMINATED",
    ]

    # tenant_industry: 6 rows.
    assert len(lookups["tenant_industry"]) == 6

    # module_code from Step 3.4.5: 5 rows post-2026-05-12 ROOS retirement
    # (seed loader's --reset deletes the ROOS lookups row to align local
    # state with the post-cleanup wire vocabulary; ModuleCodeLiteral is
    # narrowed to 5 values).
    assert len(lookups["module_code"]) == 5


# ---- L2: unknown list_name returns an empty array -------------------------
def test_l2_get_lookups_returns_empty_array_for_unknown_list(
    app_client, settings
):
    """Predictable shape: requesting a list_name not seeded in the DB
    returns an empty array for that key. Frontend doesn't need
    null-checks. Covers the country deferred-design case as well —
    ``country`` is a valid list_name shape but currently has zero
    rows; the response includes ``"country": []``.
    """
    resp = app_client.get(
        "/api/v1/lookups",
        params={"lists": "tenant_tier,country,not_a_real_list"},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    lookups = resp.json()["lookups"]
    assert "tenant_tier" in lookups
    assert len(lookups["tenant_tier"]) == 4
    # country: deferred design; zero rows; key still present with [].
    assert lookups["country"] == []
    # Truly unknown list: same predictable empty-array shape.
    assert lookups["not_a_real_list"] == []


# ---- L3: no JWT returns 401 -----------------------------------------------
def test_l3_get_lookups_requires_auth(app_client):
    resp = app_client.get(
        "/api/v1/lookups",
        params={"lists": "tenant_tier"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MISSING"


# ---- L4: empty/whitespace-only lists param ------------------------------
def test_l4_get_lookups_handles_empty_lists_param_gracefully(
    app_client, settings
):
    """Whitespace-only ``lists`` returns ``{lookups: {}}`` with 200,
    not 422. JS frameworks sometimes serialise "no lists chosen yet"
    as an empty / whitespace-only param; better to return a clean
    empty response than an error.
    """
    resp = app_client.get(
        "/api/v1/lookups",
        params={"lists": "  ,  ,  "},
        headers=_auth(_platform_jwt(settings)),
    )
    assert resp.status_code == 200
    assert resp.json()["lookups"] == {}
