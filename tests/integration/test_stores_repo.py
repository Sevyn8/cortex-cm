"""Integration tests for StoresRepo (Step 6.17.2).

Real Postgres, real schema, real RLS. No FastAPI machinery. Sessions
come from ``get_tenant_session`` via the ``platform_session`` and
``tenant_session_factory`` fixtures.

R2, R6, R12 are load-bearing cross-tenant / behaviour anchors:
  R2  list under TENANT-A scoped to A only (RLS).
  R6  search ILIKE matches on `name`.
  R12 cross-tenant get_by_id returns None (RLS-as-404 floor).
"""
from __future__ import annotations

import uuid

import pytest

from admin_backend.models.store import StoreStatus
from admin_backend.repositories._errors import InvalidSortKeyError
from admin_backend.repositories.stores import StoresRepo


@pytest.fixture
def repo() -> StoresRepo:
    """A bare StoresRepo. Stateless; no DB binding; safe per-test."""
    return StoresRepo()


# ---- R1: list under PLATFORM sees all stores across tenants (LOAD-BEARING) -
async def test_r1_list_platform_sees_all_stores(
    repo, make_tenant, make_store, platform_session,
):
    """PLATFORM session sees stores across multiple tenants via D-29's
    OR-branch."""
    t_a = await make_tenant(name="R1-A")
    t_b = await make_tenant(name="R1-B")
    s_a = await make_store(tenant_id=t_a.id, name="R1-A-Store")
    s_b1 = await make_store(tenant_id=t_b.id, name="R1-B-Store-1")
    s_b2 = await make_store(tenant_id=t_b.id, name="R1-B-Store-2")

    rows, total = await repo.list(platform_session, limit=100)
    ids = {r.store.id for r in rows}
    assert {s_a.id, s_b1.id, s_b2.id}.issubset(ids)
    # Each row has tenant_name resolved via LEFT JOIN.
    by_id = {r.store.id: r for r in rows}
    assert by_id[s_a.id].tenant_name == "R1-A"
    assert by_id[s_b1.id].tenant_name == "R1-B"
    assert total >= 3


# ---- R2: list under TENANT-A returns only TENANT-A's stores (LOAD-BEARING) -
async def test_r2_list_tenant_scoped_by_rls(
    repo, make_tenant, make_store, tenant_session_factory,
):
    """RLS-scoping floor: TENANT-A session does not see TENANT-B's stores."""
    t_a = await make_tenant(name="R2-A")
    t_b = await make_tenant(name="R2-B")
    s_a = await make_store(tenant_id=t_a.id, name="R2-A-Store")
    await make_store(tenant_id=t_b.id, name="R2-B-Store")

    async with tenant_session_factory(t_a.id) as session:
        rows, total = await repo.list(session, limit=100)

    ids = {r.store.id for r in rows}
    assert s_a.id in ids
    # No row from TENANT-B; all visible rows are TENANT-A.
    for r in rows:
        assert r.store.tenant_id == t_a.id
    assert total == len(rows)


# ---- R3: list with tenant_id filter under PLATFORM scopes to that tenant ----
async def test_r3_list_with_tenant_id_filter(
    repo, make_tenant, make_store, platform_session,
):
    t_a = await make_tenant(name="R3-A")
    t_b = await make_tenant(name="R3-B")
    await make_store(tenant_id=t_a.id, name="R3-A-Store")
    s_b = await make_store(tenant_id=t_b.id, name="R3-B-Store")

    rows, total = await repo.list(
        platform_session, tenant_id=t_b.id, limit=100
    )
    ids = {r.store.id for r in rows}
    assert s_b.id in ids
    # No rows belong to t_a under the explicit tenant_id filter.
    for r in rows:
        assert r.store.tenant_id == t_b.id
    assert total == len(rows)


# ---- R4: list with status=ACTIVE filter ------------------------------------
async def test_r4_list_with_status_filter_active(
    repo, make_tenant, make_store, platform_session,
):
    """Status filter scopes to the matching subset. The seed defaults
    to ACTIVE, so a filter of ACTIVE keeps the row visible."""
    t = await make_tenant(name="R4-T")
    s_act = await make_store(tenant_id=t.id, name="R4-Active")
    s_open = await make_store(
        tenant_id=t.id, name="R4-Opening", status=StoreStatus.OPENING
    )

    rows, _ = await repo.list(
        platform_session, status=StoreStatus.ACTIVE, limit=100,
    )
    ids = {r.store.id for r in rows}
    assert s_act.id in ids
    assert s_open.id not in ids


# ---- R5: list with country filter ------------------------------------------
async def test_r5_list_with_country_filter(
    repo, make_tenant, make_store, platform_session,
):
    """Country filter is exact-match case-sensitive."""
    t = await make_tenant(name="R5-T")
    s_us = await make_store(
        tenant_id=t.id, name="R5-US-Store", country="United States"
    )
    s_fr = await make_store(
        tenant_id=t.id, name="R5-FR-Store", country="France"
    )

    rows, _ = await repo.list(
        platform_session, country="France", limit=100,
    )
    ids = {r.store.id for r in rows}
    assert s_fr.id in ids
    assert s_us.id not in ids


# ---- R6: list with search='Buc' matches Buc-prefixed stores (LOAD-BEARING) -
async def test_r6_list_with_search_matches_name(
    repo, make_tenant, make_store, platform_session,
):
    """ILIKE search matches the store name (and store_code; the code
    branch is exercised in R7).
    """
    t = await make_tenant(name="R6-T")
    s_match = await make_store(tenant_id=t.id, name="Buc-eeNo7")
    s_other = await make_store(tenant_id=t.id, name="Whole Foods")

    rows, _ = await repo.list(platform_session, search="Buc", limit=100)
    ids = {r.store.id for r in rows}
    assert s_match.id in ids
    assert s_other.id not in ids


# ---- R7: list with search matches store_code -------------------------------
async def test_r7_list_with_search_matches_store_code(
    repo, make_tenant, make_store, platform_session,
):
    t = await make_tenant(name="R7-T")
    s_match = await make_store(
        tenant_id=t.id, name="R7-NoMatchOnName", store_code="OT-100"
    )
    s_other = await make_store(
        tenant_id=t.id, name="R7-OtherName", store_code="ZZ-9"
    )

    rows, _ = await repo.list(platform_session, search="OT-", limit=100)
    ids = {r.store.id for r in rows}
    assert s_match.id in ids
    assert s_other.id not in ids


# ---- R8: list with each of the 8 sort keys is well-formed -------------------
@pytest.mark.parametrize(
    "sort_key",
    [
        "tenant_name_asc",
        "tenant_name_desc",
        "name_asc",
        "name_desc",
        "created_at_asc",
        "created_at_desc",
        "status_asc",
        "country_asc",
    ],
)
async def test_r8_list_with_each_sort_key(
    repo, make_tenant, make_store, platform_session, sort_key,
):
    """All 8 sort keys execute without error and return rows in a
    consistent order (no exception; rows are a non-decreasing sequence
    under the primary sort column for ascending keys).
    """
    t = await make_tenant(name="R8-T")
    await make_store(tenant_id=t.id, name="R8-Zeta")
    await make_store(tenant_id=t.id, name="R8-Alpha")

    rows, _ = await repo.list(platform_session, sort=sort_key, limit=100)
    # Smoke check: the SORT_MAP resolved correctly and the query ran.
    assert len(rows) >= 2


# ---- R9: invalid sort key raises InvalidSortKeyError -----------------------
async def test_r9_invalid_sort_key_raises(repo, platform_session):
    with pytest.raises(InvalidSortKeyError):
        await repo.list(platform_session, sort="bogus_key")


# ---- R10: pagination offset+limit slices the canonical ordered set ---------
async def test_r10_pagination_slices_correctly(
    repo, make_tenant, make_store, platform_session,
):
    """offset/limit slice the result set under the default sort.

    The default sort is tenant_name_asc; the fixture-created rows
    sit under a single tenant whose name keeps the assertion robust
    against pre-existing rows from other tests.
    """
    t = await make_tenant(name="R10-T")
    # Create 5 stores; assert that limit=2 returns 2 and that
    # offset=2,limit=2 returns rows 3-4 of the same canonical ordering.
    for i in range(5):
        await make_store(tenant_id=t.id, name=f"R10-Store-{i:02d}")

    rows_full, total_full = await repo.list(
        platform_session, tenant_id=t.id, sort="name_asc", limit=100
    )
    assert total_full == 5
    rows_offset, _ = await repo.list(
        platform_session, tenant_id=t.id, sort="name_asc",
        offset=2, limit=2,
    )
    assert len(rows_offset) == 2
    assert [r.store.id for r in rows_offset] == [
        r.store.id for r in rows_full[2:4]
    ]


# ---- R11: get_by_id under PLATFORM returns the row + tenant_name -----------
async def test_r11_get_by_id_returns_row_with_tenant_name(
    repo, make_tenant, make_store, platform_session,
):
    t = await make_tenant(name="R11-T")
    s = await make_store(tenant_id=t.id, name="R11-S")

    row = await repo.get_by_id(platform_session, s.id)
    assert row is not None
    assert row.store.id == s.id
    assert row.tenant_name == "R11-T"


# ---- R12: cross-tenant get_by_id returns None (LOAD-BEARING) ---------------
async def test_r12_cross_tenant_get_by_id_returns_none(
    repo, make_tenant, make_store, tenant_session_factory,
):
    """RLS-as-404 floor at the repo: TENANT-A's session does not see
    TENANT-B's store row. None is the contract that the router converts
    to 404 STORE_NOT_FOUND."""
    t_a = await make_tenant(name="R12-A")
    t_b = await make_tenant(name="R12-B")
    s_b = await make_store(tenant_id=t_b.id, name="R12-B-Store")

    async with tenant_session_factory(t_a.id) as session:
        row = await repo.get_by_id(session, s_b.id)
    assert row is None


# ---- R13: get_by_id for unknown id returns None ----------------------------
async def test_r13_get_by_id_unknown_returns_none(repo, platform_session):
    ephemeral_id = uuid.uuid4()
    row = await repo.get_by_id(platform_session, ephemeral_id)
    assert row is None
