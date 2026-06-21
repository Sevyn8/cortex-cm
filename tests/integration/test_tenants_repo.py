"""Integration tests for TenantsRepo.

Real Postgres, real schema, real RLS. No FastAPI machinery (per the
Test pyramid in CLAUDE.md). Sessions come from `get_tenant_session`
via the `platform_session` and `tenant_session_factory` fixtures.

R4 and R5 are the load-bearing cross-tenant isolation tests; if they
ever fail, that is a security regression — the RLS policy on
`tenants` (or its OR-branch from D-29) has drifted.

Stale-data robustness: tests that don't already key on a specific
`make_tenant`-returned id use **subset assertions** rather than
exact-equality on row counts. The `make_tenant` fixture's teardown
DELETEs every id it created, so under normal runs the DB is clean
between tests; but a failed teardown (or a future parallel-run mode)
could leave rows behind. Subset assertions stay correct in both
cases.
"""
import uuid

import pytest

from admin_backend.models.tenant import (
    Tenant,
    TenantRegion,
    TenantStatus,
)
from admin_backend.repositories.tenants import TenantsRepo


@pytest.fixture
def repo() -> TenantsRepo:
    """A bare TenantsRepo. Stateless; no DB binding; safe per-test."""
    return TenantsRepo()


# ---- R1: get_by_id happy path (PLATFORM) ----------------------------------
async def test_get_by_id_returns_tenant_under_platform(
    repo, make_tenant, platform_session
):
    tenant = await make_tenant(name="R1-Acme")
    result = await repo.get_by_id(platform_session, tenant.id)
    assert result is not None
    assert result.id == tenant.id
    assert result.name == "R1-Acme"
    assert result.region == TenantRegion.US
    assert result.status == TenantStatus.ACTIVE


# ---- R2: get_by_id returns None for non-existent id (PLATFORM) ------------
async def test_get_by_id_returns_none_for_missing_id(
    repo, platform_session
):
    # Ephemeral test-only id; never persisted. Per D-21's carve-out,
    # uuid4 is fine here because there's nothing to be persisted —
    # the assertion is "no row matches this random id."
    ephemeral_id = uuid.uuid4()
    result = await repo.get_by_id(platform_session, ephemeral_id)
    assert result is None


# ---- R3: list_all returns visible tenants (PLATFORM) ----------------------
async def test_list_all_under_platform_sees_all(
    repo, make_tenant, platform_session
):
    """PLATFORM session sees all rows via D-29's OR-branch."""
    a = await make_tenant(name="R3-Alpha")
    b = await make_tenant(name="R3-Bravo")
    c = await make_tenant(name="R3-Charlie")
    results = await repo.list_all(platform_session)
    ids = {t.id for t in results}
    assert {a.id, b.id, c.id}.issubset(ids)
    # Ordering is name ASC. The three R3-* names are in alphabetical
    # order; assert that within the slice we care about, order holds.
    r3_only = [t.name for t in results if t.name.startswith("R3-")]
    assert r3_only == ["R3-Alpha", "R3-Bravo", "R3-Charlie"]


# ---- R4: list_all under TENANT excludes other tenants ---------------------
# Load-bearing cross-tenant isolation test #1.
async def test_list_all_under_tenant_excludes_other_tenants(
    repo, make_tenant, tenant_session_factory
):
    tenant_a = await make_tenant(name="R4-TenantA")
    tenant_b = await make_tenant(name="R4-TenantB")
    async with tenant_session_factory(tenant_a.id) as session:
        results = await repo.list_all(session)
    assert len(results) == 1
    assert results[0].id == tenant_a.id
    # Defensive: the row we shouldn't see is in fact different.
    assert tenant_b.id != tenant_a.id


# ---- R5: get_by_id under TENANT returns None for other-tenant id ----------
# Load-bearing cross-tenant isolation test #2.
async def test_get_by_id_cross_tenant_returns_none(
    repo, make_tenant, tenant_session_factory
):
    tenant_a = await make_tenant(name="R5-TenantA")
    tenant_b = await make_tenant(name="R5-TenantB")
    async with tenant_session_factory(tenant_a.id) as session:
        result = await repo.get_by_id(session, tenant_b.id)
    assert result is None


# ---- R6: list_by_status filters by status (PLATFORM) ----------------------
async def test_list_by_status_filters_correctly_under_platform(
    repo, make_tenant, platform_session
):
    await make_tenant(name="R6-Onb-1", status=TenantStatus.ONBOARDING)
    await make_tenant(name="R6-Act-1", status=TenantStatus.ACTIVE)
    await make_tenant(name="R6-Act-2", status=TenantStatus.ACTIVE)
    results = await repo.list_by_status(
        platform_session, TenantStatus.ACTIVE
    )
    names = {t.name for t in results}
    assert {"R6-Act-1", "R6-Act-2"}.issubset(names)
    assert "R6-Onb-1" not in names


# ---- R7: list_by_status under TENANT respects RLS -------------------------
async def test_list_by_status_under_tenant_respects_rls(
    repo, make_tenant, tenant_session_factory
):
    tenant_a = await make_tenant(
        name="R7-TenantA", status=TenantStatus.ACTIVE
    )
    tenant_b = await make_tenant(
        name="R7-TenantB", status=TenantStatus.ACTIVE
    )
    async with tenant_session_factory(tenant_a.id) as session:
        results = await repo.list_by_status(
            session, TenantStatus.ACTIVE
        )
    # TENANT A's session must see exactly its own row, not B's.
    matching = [t for t in results if t.id in {tenant_a.id, tenant_b.id}]
    assert len(matching) == 1
    assert matching[0].id == tenant_a.id


# ---- R8: PLATFORM list_all is unfiltered across statuses ------------------
# Validates D-29's PLATFORM OR-branch on `tenants`. Drop-out here would
# be a security regression — surface immediately.
async def test_platform_list_all_is_unfiltered_across_statuses(
    repo, make_tenant, platform_session
):
    # Pick three statuses whose CHECK constraints don't require
    # companion fields. SUSPENDED needs suspended_at + suspended_by;
    # TERMINATED needs terminated_at + terminated_by; ONBOARDING /
    # TRIAL / ACTIVE have no companion-field requirement, so the
    # fixture's defaults satisfy them as-is. The R8 property is
    # "PLATFORM sees rows of different statuses," not "SUSPENDED
    # specifically" — three free-form statuses prove the same thing.
    onb = await make_tenant(
        name="R8-Mix-Onb", status=TenantStatus.ONBOARDING
    )
    act = await make_tenant(
        name="R8-Mix-Act", status=TenantStatus.ACTIVE
    )
    tri = await make_tenant(
        name="R8-Mix-Tri", status=TenantStatus.TRIAL
    )
    results = await repo.list_all(platform_session)
    ids = {t.id for t in results}
    assert {onb.id, act.id, tri.id}.issubset(ids)


# ---- R9: list_all empty for orphan TENANT context -------------------------
# Defensive: confirms RLS doesn't accidentally allow non-matching tenants.
async def test_list_all_empty_for_orphan_tenant_context(
    repo, make_tenant, tenant_session_factory
):
    # Create a row so there's something for RLS to filter; otherwise
    # the assertion would also pass on an empty table.
    await make_tenant(name="R9-RealTenant")
    orphan_id = uuid.uuid4()  # carve-out per R2's reasoning
    async with tenant_session_factory(orphan_id) as session:
        results = await repo.list_all(session)
    assert results == []
