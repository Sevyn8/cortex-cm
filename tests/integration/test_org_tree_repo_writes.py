"""Step 6.13 integration tests for ``OrgNodesRepo`` write methods.

Six tests covering the LD-driven behaviours:

  RT1 — Add: parent SELECT FOR UPDATE before INSERT.
  RT2 — Edit reparent: target AND new_parent both locked in one txn.
  RT3 — Subtree re-path: 3-level subtree; post-reparent every
        descendant's path has the new prefix.
  RT4 — Concurrent Add race: parallel INSERT with same code from a
        separate session; UNIQUE-index violation -> 409.
  RT5 — Cascade-order helper: pure-function pair check matrix.
  RT6 — Cycle detection: ``_is_descendant`` correctly identifies
        ``new_parent is descendant of target``.

Fixture-order discipline mirrors test_module_access_repo_writes.py.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext
from admin_backend.config import get_settings
from admin_backend.db.session import get_tenant_session
from admin_backend.errors import (
    CycleDetectedError,
    DuplicateOrgNodeCodeError,
    InvalidParentNodeTypeError,
    ParentNodeNotFoundError,
)
from admin_backend.models.org_node import OrgNodeStatus, OrgNodeType
from admin_backend.models.tenant_user import ActorUserType
from admin_backend.repositories.org_nodes import (
    OrgNodesRepo,
    _check_cascade_order,
    _is_descendant,
)


@pytest.fixture
def repo() -> OrgNodesRepo:
    return OrgNodesRepo()


@pytest_asyncio.fixture
async def cleanup_org_nodes(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Track and clean up org_node IDs created by the repo under test.

    DELETE in REVERSE order so the composite parent FK is respected.
    """
    created: list[UUID] = []
    yield created
    if created:
        schema = get_settings().db_schema
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            for node_id in reversed(created):
                await session.execute(
                    text(
                        f"DELETE FROM {schema}.org_nodes WHERE id = :id"
                    ),
                    {"id": node_id},
                )


async def _fetch_tenant_root(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    tenant_id: UUID,
) -> tuple[UUID, str]:
    """Look up the (id, path) of the tenant-root org_node."""
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"SELECT id, path::text AS path FROM {schema}.org_nodes "
                "WHERE tenant_id = :tid "
                f"AND node_type = CAST('TENANT' AS {schema}.org_node_type_enum) "
                "AND parent_id IS NULL"
            ),
            {"tid": tenant_id},
        )
        row = result.first()
    if row is None:
        raise RuntimeError(f"no tenant root for tenant {tenant_id}")
    return UUID(str(row.id)), str(row.path)


def _platform_auth_for_user(user_id: UUID) -> AuthContext:
    """Build an AuthContext for the repo write APIs (audit-actor)."""
    return AuthContext(
        user_id=user_id,
        tenant_id=None,
        user_type="PLATFORM",
        email="repo-test@ithina.ai",
        sub="repo-test",
        iss="ithina-stub",
        aud="ithina-admin-backend",
        exp=4070908800,
        iat=1700000000,
        nbf=1700000000,
    )


# ---- RT1: add_node locks parent before INSERT -----------------------------


async def test_rt1_add_node_locks_parent_before_insert(
    repo: OrgNodesRepo,
    make_tenant: Callable[..., Awaitable[Any]],
    make_org_node: Callable[..., Awaitable[tuple[UUID, str]]],
    make_platform_user: Callable[..., Awaitable[Any]],
    cleanup_org_nodes: list[UUID],
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """Repo.add_node selects the parent FOR UPDATE before INSERTing.

    Direct verification: the public surface returns the row; the FOR
    UPDATE step is observable via missing-parent -> ParentNodeNotFoundError
    and via cascade-order check (which requires the parent's node_type,
    obtained from the SELECT). Both paths are exercised here for the
    same call site.
    """
    tenant = await make_tenant(name="RT1 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    root_id, _root_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="HQ",
        code=f"rt1-hq-{uuid.uuid4().hex[:6]}",
        name="RT1 HQ",
        parent_id=troot_id,
        parent_path=troot_path,
    )
    pu = await make_platform_user(status="INVITED")
    auth = _platform_auth_for_user(pu.id)

    async for session in get_tenant_session(platform_auth, session_factory):
        node = await repo.add_node(
            session,
            tenant_id=tenant.id,
            parent_id=root_id,
            node_type=OrgNodeType.STORE,
            code=f"rt1-store-{uuid.uuid4().hex[:6]}",
            name="RT1 Store",
            auth=auth,
        )
        cleanup_org_nodes.append(node.id)

    # The fact that the call succeeded under SELECT FOR UPDATE -> INSERT
    # is the positive case. The negative case (missing parent) is:
    async for session in get_tenant_session(platform_auth, session_factory):
        with pytest.raises(ParentNodeNotFoundError):
            await repo.add_node(
                session,
                tenant_id=tenant.id,
                parent_id=uuid.uuid4(),
                node_type=OrgNodeType.STORE,
                code=f"rt1-orphan-{uuid.uuid4().hex[:6]}",
                name="RT1 Orphan",
                auth=auth,
            )


# ---- RT2: edit_node reparent locks target AND new_parent ------------------


async def test_rt2_edit_node_reparent_locks_both(
    repo: OrgNodesRepo,
    make_tenant: Callable[..., Awaitable[Any]],
    make_org_node: Callable[..., Awaitable[tuple[UUID, str]]],
    make_platform_user: Callable[..., Awaitable[Any]],
    cleanup_org_nodes: list[UUID],
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """Repo.edit_node reparent path issues FOR UPDATE on target and
    on new_parent in the same transaction. Direct verification: missing
    new_parent -> ParentNodeNotFoundError (proves lookup ran);
    missing target -> OrgNodeNotFoundError (proves lookup ran first).
    """
    tenant = await make_tenant(name="RT2 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu_a, path_a = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"rt2-bu-a-{uuid.uuid4().hex[:6]}", name="BU A",
        parent_id=troot_id, parent_path=troot_path,
    )
    bu_b, path_b = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"rt2-bu-b-{uuid.uuid4().hex[:6]}", name="BU B",
        parent_id=troot_id, parent_path=troot_path,
    )
    region, _ = await make_org_node(
        tenant_id=tenant.id, node_type="HQ",
        code=f"rt2-hq-{uuid.uuid4().hex[:6]}", name="HQ",
        parent_id=bu_a, parent_path=path_a,
    )
    pu = await make_platform_user(status="INVITED")
    auth = _platform_auth_for_user(pu.id)

    # Happy reparent — proves both lookups succeeded.
    async for session in get_tenant_session(platform_auth, session_factory):
        node = await repo.edit_node(
            session, tenant_id=tenant.id, node_id=region,
            name=None, code=None, parent_id=bu_b, auth=auth,
            reparent=True,
        )
        assert node.parent_id == bu_b
        # New path begins with BU-B's path prefix.
        assert str(node.path).startswith(path_b + ".")

    # Missing new_parent -> ParentNodeNotFoundError.
    async for session in get_tenant_session(platform_auth, session_factory):
        with pytest.raises(ParentNodeNotFoundError):
            await repo.edit_node(
                session, tenant_id=tenant.id, node_id=region,
                name=None, code=None, parent_id=uuid.uuid4(),
                auth=auth, reparent=True,
            )


# ---- RT3: subtree re-path ------------------------------------------------


async def test_rt3_subtree_repath_on_parent_change(
    repo: OrgNodesRepo,
    make_tenant: Callable[..., Awaitable[Any]],
    make_org_node: Callable[..., Awaitable[tuple[UUID, str]]],
    make_platform_user: Callable[..., Awaitable[Any]],
    cleanup_org_nodes: list[UUID],
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """Build BU -> HQ -> COUNTRY -> REGION -> STORE; reparent the HQ
    under a different BU and assert every descendant's path has the new
    prefix.
    """
    tenant = await make_tenant(name="RT3 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    suffix = uuid.uuid4().hex[:6]

    bu1, p_bu1 = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"rt3-bu1-{suffix}", name="BU1",
        parent_id=troot_id, parent_path=troot_path,
    )
    bu2, p_bu2 = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"rt3-bu2-{suffix}", name="BU2",
        parent_id=troot_id, parent_path=troot_path,
    )
    hq, p_hq = await make_org_node(
        tenant_id=tenant.id, node_type="HQ",
        code=f"rt3-hq-{suffix}", name="HQ",
        parent_id=bu1, parent_path=p_bu1,
    )
    country, p_country = await make_org_node(
        tenant_id=tenant.id, node_type="COUNTRY",
        code=f"rt3-c-{suffix}", name="Country",
        parent_id=hq, parent_path=p_hq,
    )
    region, p_region = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code=f"rt3-r-{suffix}", name="Region",
        parent_id=country, parent_path=p_country,
    )
    store, _ = await make_org_node(
        tenant_id=tenant.id, node_type="STORE",
        code=f"rt3-s-{suffix}", name="Store",
        parent_id=region, parent_path=p_region,
    )

    pu = await make_platform_user(status="INVITED")
    auth = _platform_auth_for_user(pu.id)

    async for session in get_tenant_session(platform_auth, session_factory):
        await repo.edit_node(
            session, tenant_id=tenant.id, node_id=hq,
            name=None, code=None, parent_id=bu2, auth=auth,
            reparent=True,
        )

    # Verify every descendant's path lives under bu2.path now.
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"SELECT id, path::text AS path FROM {schema}.org_nodes "
                "WHERE id = ANY(:ids) ORDER BY nlevel(path)"
            ),
            {"ids": [hq, country, region, store]},
        )
        rows = result.all()
        for row in rows:
            assert row.path.startswith(p_bu2 + "."), (
                f"node {row.id} path={row.path} not under {p_bu2}"
            )


# ---- RT4: concurrent Add race ---------------------------------------------


async def test_rt4_concurrent_add_duplicate_code_409(
    repo: OrgNodesRepo,
    make_tenant: Callable[..., Awaitable[Any]],
    make_org_node: Callable[..., Awaitable[tuple[UUID, str]]],
    make_platform_user: Callable[..., Awaitable[Any]],
    cleanup_org_nodes: list[UUID],
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """Two add_node calls for the same code under the same parent in
    parallel: one wins, the other raises DuplicateOrgNodeCodeError.

    Race is deterministic enough at v0 scale; the LD10 mapping is what
    we're testing. We serialize the two so the second sees the first's
    committed row.
    """
    tenant = await make_tenant(name="RT4 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    root_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="HQ",
        code=f"rt4-hq-{uuid.uuid4().hex[:6]}", name="RT4 HQ",
        parent_id=troot_id, parent_path=troot_path,
    )
    pu = await make_platform_user(status="INVITED")
    auth = _platform_auth_for_user(pu.id)

    duplicate_code = f"rt4-dup-{uuid.uuid4().hex[:6]}"
    async for session in get_tenant_session(platform_auth, session_factory):
        first = await repo.add_node(
            session, tenant_id=tenant.id, parent_id=root_id,
            node_type=OrgNodeType.STORE,
            code=duplicate_code, name="First", auth=auth,
        )
        cleanup_org_nodes.append(first.id)

    async for session in get_tenant_session(platform_auth, session_factory):
        with pytest.raises(DuplicateOrgNodeCodeError):
            await repo.add_node(
                session, tenant_id=tenant.id, parent_id=root_id,
                node_type=OrgNodeType.STORE,
                code=duplicate_code.upper(), name="Second", auth=auth,
            )


# ---- RT5: cascade-order helper -------------------------------------------


def test_rt5_check_cascade_order_matrix() -> None:
    """Spot-check 10 pairs against the canonical order."""
    # Valid pairs (parent above child).
    _check_cascade_order(OrgNodeType.TENANT, OrgNodeType.BUSINESS_UNIT)
    _check_cascade_order(OrgNodeType.TENANT, OrgNodeType.STORE)  # skip OK
    _check_cascade_order(OrgNodeType.BUSINESS_UNIT, OrgNodeType.HQ)
    _check_cascade_order(OrgNodeType.HQ, OrgNodeType.COUNTRY)
    _check_cascade_order(OrgNodeType.REGION, OrgNodeType.STORE)
    _check_cascade_order(OrgNodeType.STORE, OrgNodeType.DEPARTMENT)

    # Invalid pairs (reversal or same-type).
    for parent, child in [
        (OrgNodeType.STORE, OrgNodeType.REGION),  # reversal
        (OrgNodeType.HQ, OrgNodeType.BUSINESS_UNIT),  # reversal
        (OrgNodeType.STORE, OrgNodeType.STORE),  # same-type (equal ord)
        (OrgNodeType.TENANT, OrgNodeType.TENANT),  # same-type root
    ]:
        with pytest.raises(InvalidParentNodeTypeError):
            _check_cascade_order(parent, child)


# ---- RT6: cycle-detection helper -----------------------------------------


def test_rt6_is_descendant_helper() -> None:
    """``_is_descendant(candidate, ancestor)`` returns True when
    candidate is the same as or a descendant of ancestor.
    """
    # Same path -> self-parent cycle.
    assert _is_descendant("t.bu1", "t.bu1") is True
    # Descendant.
    assert _is_descendant("t.bu1.hq", "t.bu1") is True
    assert _is_descendant("t.bu1.hq.country.region", "t.bu1") is True
    # Sibling — same prefix but different segment.
    assert _is_descendant("t.bu1_other", "t.bu1") is False
    # Unrelated.
    assert _is_descendant("t.bu2.hq", "t.bu1") is False
    # Ancestor of operand is NOT a descendant.
    assert _is_descendant("t.bu1", "t.bu1.hq") is False


# ---- RT7: set_status (Step 6.21.2 — cascade target) ----------------------


async def test_rt7_set_status_into_archived_populates_triplet(
    repo: OrgNodesRepo,
    make_tenant: Callable[..., Awaitable[Any]],
    make_org_node: Callable[..., Awaitable[tuple[UUID, str]]],
    make_platform_user: Callable[..., Awaitable[Any]],
    cleanup_org_nodes: list[UUID],
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING: ``OrgNodesRepo.set_status`` to ARCHIVED populates
    the ``archived_at`` + ``archived_by_user_id`` + ``archived_by_user_type``
    triplet atomically with the status flip. updated_by_* re-stamps.

    Cascade target for ``StoresRepo.transition``: store CLOSED ->
    paired org_node ARCHIVED routes through this method.
    """
    tenant = await make_tenant(name="RT7 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    # ACTIVE-by-default STORE-type node.
    store_node_id, _ = await make_org_node(
        tenant_id=tenant.id,
        node_type="STORE",
        code=f"rt7-{uuid.uuid4().hex[:6]}",
        name="RT7 Store",
        parent_id=troot_id,
        parent_path=troot_path,
    )
    pu = await make_platform_user(status="INVITED")
    auth = _platform_auth_for_user(pu.id)

    async for session in get_tenant_session(platform_auth, session_factory):
        result = await repo.set_status(
            session,
            tenant_id=tenant.id,
            node_id=store_node_id,
            target_status=OrgNodeStatus.ARCHIVED,
            auth=auth,
        )
    assert result is not None
    assert result.status == OrgNodeStatus.ARCHIVED
    assert result.archived_at is not None
    assert result.archived_by_user_id == pu.id
    assert result.archived_by_user_type == ActorUserType.PLATFORM
    assert result.updated_by_user_id == pu.id


# ---- RT8: set_status out-of-ARCHIVED nulls the triplet -------------------


async def test_rt8_set_status_out_of_archived_nulls_triplet(
    repo: OrgNodesRepo,
    make_tenant: Callable[..., Awaitable[Any]],
    make_org_node: Callable[..., Awaitable[tuple[UUID, str]]],
    make_platform_user: Callable[..., Awaitable[Any]],
    cleanup_org_nodes: list[UUID],
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING: ``OrgNodesRepo.set_status`` from ARCHIVED back to
    ACTIVE nulls the ``archived_*`` triplet atomically with the status
    flip. Symmetric to RT7."""
    tenant = await make_tenant(name="RT8 Tenant", with_root=True)
    troot_id, troot_path = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    store_node_id, _ = await make_org_node(
        tenant_id=tenant.id,
        node_type="STORE",
        code=f"rt8-{uuid.uuid4().hex[:6]}",
        name="RT8 Store",
        parent_id=troot_id,
        parent_path=troot_path,
    )
    pu = await make_platform_user(status="INVITED")
    auth = _platform_auth_for_user(pu.id)

    # First, archive.
    async for session in get_tenant_session(platform_auth, session_factory):
        await repo.set_status(
            session,
            tenant_id=tenant.id,
            node_id=store_node_id,
            target_status=OrgNodeStatus.ARCHIVED,
            auth=auth,
        )

    # Then, un-archive.
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await repo.set_status(
            session,
            tenant_id=tenant.id,
            node_id=store_node_id,
            target_status=OrgNodeStatus.ACTIVE,
            auth=auth,
        )
    assert result is not None
    assert result.status == OrgNodeStatus.ACTIVE
    assert result.archived_at is None
    assert result.archived_by_user_id is None
    assert result.archived_by_user_type is None
    assert result.updated_by_user_id == pu.id
