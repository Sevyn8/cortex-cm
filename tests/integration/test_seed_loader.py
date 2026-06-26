"""Integration tests for the dev seed loader.

Each test runs the loader (or one of its components) against the
real test DB and asserts on observable database state.

Layer shape:
  L1 — end-to-end run completes without exceptions.
  L2 — per-table row counts match the Excel sheets.
  L3 — sentinel rows: spot-check specific values that exercise the
       trickier load paths (NULL translation, audit-actor synthesis,
       per-row routing, ltree paths).
  L4 — production-refusal guard: settings.environment == "production"
       returns exit 2 with no DB writes.

Note on RLS visibility (D-29). All six multi-tenant tables use the
unconditional PLATFORM OR-branch, so a PLATFORM session sees every
row. Post Step 6.8.1 split (D-34), the previously-mixed
``user_role_assignments`` is gone; it lives in two physical tables:
``platform_user_role_assignments`` (no RLS) and
``tenant_user_role_assignments`` (RLS+FORCE, unconditional OR-branch).
PLATFORM-without-impersonation now sees every assignment row directly
in either table.
"""
from sqlalchemy import text

from admin_backend.config import get_settings


# Excel-sheet row counts after the corrected Excel + the
# excel_reader's phantom-row filter (which drops a stray #VALUE!
# row at row 1,048,558 in role_permissions).
#
# Step 6.1 narrowed the permission catalogue: dropped the
# PRICING_OS.MARKDOWNS.APPROVE.REGION row (scope='REGION' no longer in
# permission_scope_enum) AND the 4 role_permissions referencing it.
# 24 -> 23 permissions, 117 -> 113 role_permissions.
#
# Step 6.8.1 split user_role_assignments. The seed Excel is unchanged
# (still 22 logical rows: 3 PLATFORM-audience + 19 TENANT-side); the
# loader routes each row to one of the two physical tables.
#
# Phase 3 seed update (2026-05-13, post-Step-6.9.3.2): +1 permission
# (ADMIN.TENANTS.VIEW.TENANT tuple) and +2 role_permissions (OWNER →
# ADMIN.TENANTS.VIEW.TENANT, OWNER → ADMIN.ORG_NODES.VIEW.TENANT).
# 30 -> 31 permissions, 120 -> 122 role_permissions.
#
# Phase 3b seed update (2026-05-16, pre-Step-6.13 catalogue gap closure
# per FN-AB-47): +2 permissions (ADMIN.ORG_NODES.CONFIGURE.GLOBAL,
# ADMIN.ORG_NODES.VIEW.GLOBAL) and +5 role_permissions (SUPER_ADMIN ->
# both new GLOBAL tuples, PLATFORM_ADMIN -> both new GLOBAL tuples,
# OWNER -> ADMIN.ORG_NODES.CONFIGURE.TENANT).
# 31 -> 33 permissions, 122 -> 127 role_permissions.
EXPECTED_VISIBLE_COUNTS_PLATFORM = {
    "platform_users": 3,
    "tenants": 7,
    "org_nodes": 49,
    "stores": 25,
    "tenant_users": 17,
    "roles": 15,
    # 2026-05-20 (Step 6.16.3 operator catalogue update):
    # +1 permission `ADMIN.AUDIT_LOG.VIEW.GLOBAL` (36 -> 37).
    # Net role_permissions movement (132 -> 131): platform roles
    # SUPER_ADMIN / PLATFORM_ADMIN / SUPPORT_ADMIN previously held
    # `.VIEW.TENANT`; operator REVOKED those and GRANTED
    # `.VIEW.GLOBAL` to the same 3 platform roles. Tenant-side
    # `.VIEW.TENANT` grants on the 8 tenant roles unchanged.
    "permissions": 37,
    "role_permissions": 131,
    # 2026-05-12: 4 ROOS rows removed from XLSX (Buc-ee's, Żabka,
    # Infomil, GreenLeaf); count moves 27 → 23.
    "tenant_module_access": 23,
    # platform_user_role_assignments has no RLS — all 3 PLATFORM-audience
    # rows visible to any session.
    "platform_user_role_assignments": 3,
    # tenant_user_role_assignments uses the unconditional D-29 OR-branch;
    # PLATFORM session sees all 19 TENANT-side rows.
    "tenant_user_role_assignments": 19,
}

# Total assignment rows across both physical tables (the
# PLATFORM-audience 3 plus the 19 TENANT-side rows).
EXPECTED_ASSIGNMENTS_TOTAL = 22


# ---- L1: end-to-end ----------------------------------------------------
async def test_l1_seed_runs_clean_end_to_end(
    settings, engine, session_factory
):
    """Loader runs without exceptions against an empty DB.

    Reuses the integration-conftest engine + session_factory rather
    than letting run_seed open its own — keeps everything in one
    event loop.
    """
    from scripts.seed_dev_data.runner import (
        SHEETS_IN_ORDER,
        _platform_auth,
        run_seed,
    )

    # We can't easily inject the engine into run_seed (it owns its
    # own engine creation for CLI use), so for this test we just
    # call run_seed directly. It uses get_settings() which is the
    # same Settings the conftest engine uses.
    rc = await run_seed(reset=True)
    assert rc == 0


# ---- L2: row counts ----------------------------------------------------
async def test_l2_seed_row_counts(platform_session):
    """Per-table PLATFORM-visible row counts match Excel.

    Uses ``platform_session`` (PLATFORM AuthContext, app.tenant_id
    NULL). user_role_assignments visibility is IS-NULL-gated; see
    test_l2b for the TENANT-side rows.
    """
    schema = get_settings().db_schema
    for table, expected in EXPECTED_VISIBLE_COUNTS_PLATFORM.items():
        result = await platform_session.execute(
            text(f"SELECT count(*) FROM {schema}.{table}")
        )
        actual = result.scalar_one()
        assert actual == expected, (
            f"{table}: expected {expected} rows visible to PLATFORM, "
            f"got {actual}"
        )


async def test_l2b_role_assignments_total_split_correctly(platform_session):
    """Post-split: PLATFORM session reads both physical tables directly.

    Before Step 6.8.1, ``user_role_assignments`` used the IS-NULL-gated
    D-29 form, so PLATFORM-without-impersonation only saw the 3
    PLATFORM-audience rows; this test iterated per-tenant impersonation
    to verify that gate.

    Post Step 6.8.1 / 6.8.2 (D-34): ``tenant_user_role_assignments``
    uses the unconditional OR-branch, so PLATFORM-without-impersonation
    sees all rows; ``platform_user_role_assignments`` has no RLS at
    all. No iteration needed; sum the two counts directly and compare
    against the expected total.
    """
    schema = get_settings().db_schema
    result = await platform_session.execute(
        text(f"SELECT count(*) FROM {schema}.platform_user_role_assignments")
    )
    platform_count = result.scalar_one()

    result = await platform_session.execute(
        text(f"SELECT count(*) FROM {schema}.tenant_user_role_assignments")
    )
    tenant_count = result.scalar_one()

    total = platform_count + tenant_count
    assert total == EXPECTED_ASSIGNMENTS_TOTAL, (
        f"role assignments split totals: platform={platform_count}, "
        f"tenant={tenant_count}, sum={total}, expected "
        f"{EXPECTED_ASSIGNMENTS_TOTAL}"
    )


# ---- L3: sentinel rows ------------------------------------------------
async def test_l3_seed_sentinel_rows(platform_session):
    """Spot-checks for known-tricky values across the seed."""

    # Buc-ee's: ENTERPRISE tier; monthly_revenue_usd is the
    # snapshot value as a Decimal-shaped string.
    result = await platform_session.execute(
        text(
            "SELECT name, tier, monthly_revenue_usd::text "
            f"FROM {get_settings().db_schema}.tenants WHERE name = 'Buc-ee''s'"
        )
    )
    row = result.one()
    assert row.name == "Buc-ee's"
    assert row.tier == "ENTERPRISE"
    assert row.monthly_revenue_usd is not None

    # tenant_module_access for Buc-ee's: the loader synthesised
    # the audit-actor columns via the seed's universal "system actor"
    # (Anjali). All three audit-actor FKs MUST be populated.
    result = await platform_session.execute(
        text(
            f"SELECT count(*) FROM {get_settings().db_schema}.tenant_module_access tma "
            f"JOIN {get_settings().db_schema}.tenants t ON t.id = tma.tenant_id "
            "WHERE t.name = 'Buc-ee''s'"
        )
    )
    assert result.scalar_one() >= 5

    result = await platform_session.execute(
        text(
            f"SELECT count(*) FROM {get_settings().db_schema}.tenant_module_access "
            "WHERE enabled_by_user_id IS NULL "
            "OR created_by_user_id IS NULL "
            "OR updated_by_user_id IS NULL"
        )
    )
    assert result.scalar_one() == 0, (
        "Audit-actor synthesis didn't populate every row"
    )

    # PLATFORM-audience role assignments now live on
    # ``platform_user_role_assignments`` (no RLS; every session sees
    # them). Post Step 6.8.1 split: no ``tenant_id`` /
    # ``tenant_user_id`` / ``org_node_id`` columns to check — the
    # physical table separation IS the audience guarantee.
    result = await platform_session.execute(
        text(f"SELECT count(*) FROM {get_settings().db_schema}.platform_user_role_assignments")
    )
    assert result.scalar_one() >= 3, (
        "Expected at least 3 PLATFORM-audience role assignments"
    )

    # org_nodes ltree paths: every non-root path begins with its
    # parent's path + '.'. Validated via a self-join.
    result = await platform_session.execute(
        text(
            f"SELECT count(*) FROM {get_settings().db_schema}.org_nodes child "
            f"JOIN {get_settings().db_schema}.org_nodes parent ON parent.id = child.parent_id "
            "WHERE NOT (child.path::text LIKE parent.path::text || '.%')"
        )
    )
    assert result.scalar_one() == 0, (
        "Some org_nodes have paths that don't start with parent.path"
    )


# ---- L4: production refusal -------------------------------------------
def test_l4_seed_refuses_production(monkeypatch):
    """ENVIRONMENT=production refuses to run, exits non-zero, no DB writes.

    Synchronous test: the guard runs before any async work, so we
    don't need the platform_session fixture. We reset sys.argv so
    main()'s argparse doesn't pick up pytest's arguments, and we
    set AUTH_CLIENT_MODE + JWT_ISSUER to prod-valid values so the
    Settings model itself validates (otherwise its
    production_must_use_auth0 / production_issuer_must_not_be_stub
    validators reject construction before the loader's guard fires).
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_CLIENT_MODE", "AUTH0")
    monkeypatch.setenv("JWT_ISSUER", "https://sevyn8.us.auth0.com/")
    monkeypatch.setattr("sys.argv", ["seed_dev_data"])
    # Force settings re-read since get_settings uses lru_cache.
    from admin_backend.config import get_settings
    get_settings.cache_clear()
    try:
        from scripts.seed_dev_data.__main__ import main
        rc = main()
        assert rc == 2
    finally:
        # Restore the cache so subsequent tests don't see prod.
        get_settings.cache_clear()
