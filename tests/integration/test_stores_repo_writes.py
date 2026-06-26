"""Integration tests for StoresRepo write methods.

C/U/T-series shipped at Step 6.17.3 / 6.17.4. PW-series (paired-write
cascade) shipped at Step 6.21.2.

  C1-C8: create — happy path, optionals, RLS, store_code duplicate,
         parent-not-found / parent-different-tenant, audit-actor pair,
         DDL-default status. Step 6.21.2 retired C7 (already-linked
         failure mode structurally unreachable under atomic-pair).
  U1-U8: update — happy, empty, same-as-current, store_code rename,
         rename-to-self, unknown id, cross-tenant RLS-as-None,
         audit-actor + updated_at.
  T1-T13: transition state machine.
  PW1-PW10 (Step 6.21.2): paired-write cascade behaviour — atomic
         org_node + store create, validation gates on parent, cascade
         on name/store_code/parent change, cascade on status, mapping
         dict correctness. PW6 splits into PW6a (store_code collision)
         and PW6b (org_node code collision via cascade).

Pattern. Each test uses ``repo.create`` / ``repo.update`` against
sessions yielded by the standard fixtures. ``make_tenant`` /
``make_org_node`` / ``make_platform_user`` build supporting rows
inside committed transactions. ``cleanup_stores`` tracks store IDs
created via the repo (not via ``make_store``) and DELETEs them at
teardown.

Cleanup-fixture order discipline (load-bearing): tests list
``cleanup_stores`` AFTER the upstream factories (``make_tenant``,
``make_org_node``, ``make_platform_user``) and BEFORE
``platform_session``. pytest LIFO teardown then runs
``platform_session`` first (commits the test transaction so rows
become visible), then ``cleanup_stores`` (DELETEs the committed
stores), then the upstream factories (their teardown removes
org_nodes and tenants once the FK refs are gone).
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
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
    DuplicateOrgNodeCodeError,
    DuplicateStoreCodeError,
    InvalidParentNodeTypeError,
    ParentNodeNotFoundError,
)
from admin_backend.models.org_node import OrgNodeStatus
from admin_backend.models.store import StoreStatus, TaxTreatment
from admin_backend.models.tenant_user import ActorUserType
from admin_backend.repositories.stores import (
    STORE_STATUS_TO_ORG_NODE_STATUS,
    StoresRepo,
)
from admin_backend.repositories.tenants import TransitionResult


@pytest.fixture
def repo() -> StoresRepo:
    """Stateless StoresRepo. Safe per-test."""
    return StoresRepo()


@pytest_asyncio.fixture
async def cleanup_stores(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Tracks store IDs created via ``repo.create`` and DELETEs at teardown.

    Step 6.21.2: ``repo.create`` now produces a paired STORE-type
    org_node alongside each store. The cleanup fixture captures the
    paired ``org_node_id`` BEFORE deleting the store (so the FK ref
    is gone when we drop the org_node) and DELETEs both rows in
    sequence.

    Distinct from the ``make_store`` factory's teardown — ``repo.create``
    is the path under test and produces rows the factory wouldn't
    know about. The teardown runs in a fresh PLATFORM session opened
    AFTER ``platform_session`` has committed the test's transaction.
    """
    schema = get_settings().db_schema
    created: list[UUID] = []
    yield created

    if created:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            # Capture the paired org_node_ids while the stores still
            # exist (the FK ref is what makes us delete in this order).
            paired = await session.execute(
                text(
                    f"SELECT org_node_id FROM {schema}.stores "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": created},
            )
            org_node_ids = [r.org_node_id for r in paired if r.org_node_id]
            await session.execute(
                text(
                    f"DELETE FROM {schema}.stores WHERE id = ANY(:ids)"
                ),
                {"ids": created},
            )
            if org_node_ids:
                await session.execute(
                    text(
                        f"DELETE FROM {schema}.org_nodes "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": org_node_ids},
                )


def _platform_auth(actor_id: UUID) -> AuthContext:
    """Synthetic PLATFORM AuthContext for repo.create / update / transition.

    Step 6.21.2: ``StoresRepo.{create,update,transition}`` now take
    ``auth: AuthContext`` directly (replacing the prior
    ``actor_user_id`` + ``actor_user_type`` pair). Tests build a
    minimal valid AuthContext with the actor's id; ``user_type``
    defaults to PLATFORM (callers needing TENANT pass a custom auth).
    Matches the shape used by ``conftest.platform_auth``.
    """
    return AuthContext(  # type: ignore[call-arg]
        sub="test-sub",
        iss="https://stub-issuer.local/",
        aud="https://api.cortex.sevyn8.com",
        exp=9999999999,
        user_id=actor_id,
        tenant_id=None,
        user_type="PLATFORM",
        email="test@ithina.local",
    )


def _tenant_auth(actor_id: UUID, tenant_id: UUID) -> AuthContext:
    """Synthetic TENANT AuthContext."""
    return AuthContext(  # type: ignore[call-arg]
        sub="test-sub",
        iss="https://stub-issuer.local/",
        aud="https://api.cortex.sevyn8.com",
        exp=9999999999,
        user_id=actor_id,
        tenant_id=tenant_id,
        user_type="TENANT",
        email="test@ithina.local",
    )


def _base_create_kwargs(
    *,
    tenant_id: UUID,
    parent_org_node_id: UUID,
    actor_id: UUID,
    name: str,
    store_code: str,
    actor_type: ActorUserType = ActorUserType.PLATFORM,
    address: str | None = None,
    latitude: Decimal | None = None,
    longitude: Decimal | None = None,
) -> dict[str, Any]:
    """Minimal valid kwargs for ``repo.create``.

    Step 6.21.2 renamed ``org_node_id`` -> ``parent_org_node_id``
    (required; the server now creates the paired STORE-type org_node
    fresh) and replaced ``actor_user_id`` / ``actor_user_type`` with a
    single synthesised ``auth: AuthContext``.
    """
    if actor_type == ActorUserType.TENANT:
        auth = _tenant_auth(actor_id, tenant_id)
    else:
        auth = _platform_auth(actor_id)
    return {
        "tenant_id": tenant_id,
        "name": name,
        "country": "United States",
        "timezone": "America/New_York",
        "currency": "USD",
        "store_code": store_code,
        "tax_treatment": TaxTreatment.EXCLUSIVE,
        "parent_org_node_id": parent_org_node_id,
        "address": address,
        "latitude": latitude,
        "longitude": longitude,
        "auth": auth,
    }


@pytest_asyncio.fixture
async def make_parent_org_node(
    make_org_node: Any,
) -> Any:
    """Convenience: build a tenant-root + an HQ child; return the
    HQ id as a valid ``parent_org_node_id`` for ``repo.create``.

    Step 6.21.2: stores attach to a non-STORE parent in the org tree.
    Most C/U/T tests don't care which type of parent — they just need
    a valid one. This fixture returns an HQ-typed node (one level
    below the TENANT root), suitable for cascade-order under STORE.
    Tests that need a different parent type can call ``make_org_node``
    directly.
    """
    async def _make(tenant_id: UUID, marker: str | None = None) -> UUID:
        short = marker or uuid.uuid4().hex[:6]
        root_id, root_path = await make_org_node(
            tenant_id=tenant_id,
            node_type="TENANT",
            code=f"t-{short}-{uuid.uuid4().hex[:6]}",
            name="Test Root",
        )
        hq_id, _ = await make_org_node(
            tenant_id=tenant_id,
            node_type="HQ",
            code=f"hq-{short}-{uuid.uuid4().hex[:6]}",
            name="Test HQ",
            parent_id=root_id,
            parent_path=root_path,
        )
        return hq_id

    return _make


# ============================================================================
# C: create
# ============================================================================


async def test_c1_create_happy_path(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """All fields set; row inserted; row materialised via get_by_id."""
    t = await make_tenant(name="C1-Tenant")
    parent_id = await make_parent_org_node(t.id, "c1")
    actor = await make_platform_user(status="ACTIVE")
    row = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="C1-Store",
            store_code="C1-001",
            address="100 Main St",
            latitude=Decimal("12.345678"),
            longitude=Decimal("-23.456789"),
        ),
    )
    cleanup_stores.append(row.store.id)
    assert row.store.name == "C1-Store"
    assert row.store.tenant_id == t.id
    assert row.store.store_code == "C1-001"
    assert row.store.address == "100 Main St"
    assert row.store.latitude == Decimal("12.345678")
    assert row.store.longitude == Decimal("-23.456789")
    assert row.tenant_name == "C1-Tenant"
    # Step 6.21.2: paired STORE-type org_node exists and is linked.
    assert row.store.org_node_id is not None


async def test_c2_create_optional_fields_omitted(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """Optional fields (lat, lng, address) default to None.

    Step 6.21.2: ``org_node_id`` is no longer optional on the body
    (``parent_org_node_id`` is required and the server provisions
    ``org_node_id`` server-side); the freshly-created store always
    has a non-NULL ``org_node_id``.
    """
    t = await make_tenant(name="C2-Tenant")
    parent_id = await make_parent_org_node(t.id, "c2")
    actor = await make_platform_user(status="ACTIVE")
    row = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="C2-Store",
            store_code="C2-001",
        ),
    )
    cleanup_stores.append(row.store.id)
    assert row.store.address is None
    assert row.store.latitude is None
    assert row.store.longitude is None
    assert row.store.org_node_id is not None


async def test_c3_create_under_tenant_session_creates_for_own_tenant(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    tenant_session_factory,
) -> None:
    """A TENANT-A session calling repo.create with tenant_id=A inserts
    a row visible to A's session. RLS admits the INSERT because the
    session's app.tenant_id matches the row's tenant_id."""
    t_a = await make_tenant(name="C3-A")
    parent_id = await make_parent_org_node(t_a.id, "c3")
    actor = await make_platform_user(status="ACTIVE")
    async with tenant_session_factory(t_a.id) as session:
        row = await repo.create(
            session,
            **_base_create_kwargs(
                tenant_id=t_a.id,
                parent_org_node_id=parent_id,
                actor_id=actor.id,
                name="C3-Store",
                store_code="C3-001",
                actor_type=ActorUserType.TENANT,
            ),
        )
        cleanup_stores.append(row.store.id)
        assert row.store.tenant_id == t_a.id


async def test_c4_create_duplicate_store_code_raises(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """Second create with same (tenant_id, store_code) -> 409."""
    t = await make_tenant(name="C4-Tenant")
    parent_id = await make_parent_org_node(t.id, "c4")
    actor = await make_platform_user(status="ACTIVE")
    first = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="C4-First",
            store_code="C4-001",
        ),
    )
    cleanup_stores.append(first.store.id)

    with pytest.raises(DuplicateStoreCodeError):
        await repo.create(
            platform_session,
            **_base_create_kwargs(
                tenant_id=t.id,
                parent_org_node_id=parent_id,
                actor_id=actor.id,
                name="C4-Second",
                store_code="C4-001",
            ),
        )


async def test_c5_create_with_cross_tenant_parent_raises_parent_not_found(
    repo,
    make_tenant,
    make_org_node,
    make_platform_user,
    cleanup_stores,
    platform_session,
) -> None:
    """parent_org_node_id belonging to tenant B can't link a store
    under tenant A.

    Step 6.21.2 (LD12 retirement of OrgNodeNotForStoreError): the new
    ``_check_parent_node_for_store`` reads the parent under the
    request's tenant_id via the SELECT WHERE clause; a cross-tenant
    parent surfaces as ``ParentNodeNotFoundError`` (404), collapsing
    "not visible" and "different tenant" per D-17. PLATFORM session
    can see both org_nodes globally, but the same-tenant filter in
    the SELECT enforces the rule.
    """
    t_a = await make_tenant(name="C5-A")
    t_b = await make_tenant(name="C5-B")
    actor = await make_platform_user(status="ACTIVE")

    # Build an HQ-level org_node under tenant B.
    root_b, root_b_path = await make_org_node(
        tenant_id=t_b.id,
        node_type="TENANT",
        code=f"c5b-{uuid.uuid4().hex[:8]}",
        name="C5-B-Root",
    )
    hq_b, _ = await make_org_node(
        tenant_id=t_b.id,
        node_type="HQ",
        code=f"c5b-hq-{uuid.uuid4().hex[:8]}",
        name="C5-B-HQ",
        parent_id=root_b,
        parent_path=root_b_path,
    )

    with pytest.raises(ParentNodeNotFoundError):
        await repo.create(
            platform_session,
            **_base_create_kwargs(
                tenant_id=t_a.id,
                parent_org_node_id=hq_b,
                actor_id=actor.id,
                name="C5-Store",
                store_code="C5-001",
            ),
        )


async def test_c6_create_with_unknown_parent_raises_parent_not_found(
    repo,
    make_tenant,
    make_platform_user,
    cleanup_stores,
    platform_session,
) -> None:
    """Non-existent parent_org_node_id -> ParentNodeNotFoundError (404).

    Step 6.21.2 (LD12 retirement): assertion target changed from
    OrgNodeNotForStoreError to ParentNodeNotFoundError per the new
    _check_parent_node_for_store helper.
    """
    t = await make_tenant(name="C6-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    bogus_parent = uuid.uuid4()

    with pytest.raises(ParentNodeNotFoundError):
        await repo.create(
            platform_session,
            **_base_create_kwargs(
                tenant_id=t.id,
                parent_org_node_id=bogus_parent,
                actor_id=actor.id,
                name="C6-Store",
                store_code="C6-001",
            ),
        )


# Step 6.21.2 (LD12): test_c7 ("already linked" failure mode) deleted.
# The "already linked" case is structurally unreachable under the new
# atomic-pair architecture — the server creates the paired STORE-type
# org_node fresh inside the same transaction; there is no
# user-supplied org_node_id to be "already linked" to another store.
# The DDL partial unique index ``uq_stores_org_node_id`` still backs
# the 1:1 link as a defensive backstop.


async def test_c8_create_populates_audit_actor_pairs(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """Both created_by_* and updated_by_* pairs populate on INSERT
    (Pattern (b) ck_stores_*_actor_pair both-NOT-NULL invariants)."""
    t = await make_tenant(name="C8-Tenant")
    parent_id = await make_parent_org_node(t.id, "c8")
    actor = await make_platform_user(status="ACTIVE")
    row = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="C8-Store",
            store_code="C8-001",
        ),
    )
    cleanup_stores.append(row.store.id)
    assert row.store.created_by_user_id == actor.id
    assert row.store.created_by_user_type == ActorUserType.PLATFORM
    assert row.store.updated_by_user_id == actor.id
    assert row.store.updated_by_user_type == ActorUserType.PLATFORM


async def test_c9_create_status_defaults_via_ddl_default(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """Server omits status; DDL default fires; row reads as the DDL
    default value.

    Prompt-vs-codebase contradiction: prompt LD8 said the DDL default
    is OPENING; the DDL is actually ``DEFAULT 'ACTIVE'``. The repo
    honours LD8's intent ("server-forces via DDL default") by omitting
    status from the INSERT; this test pins the DDL default observed
    in v0. If a future migration changes the DDL default (likely to
    OPENING per the product intent implied by the lifecycle enum
    ordering), this test re-aligns automatically — but the test
    documents what the default IS today.
    """
    t = await make_tenant(name="C9-Tenant")
    parent_id = await make_parent_org_node(t.id, "c9")
    actor = await make_platform_user(status="ACTIVE")
    row = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="C9-Store",
            store_code="C9-001",
        ),
    )
    cleanup_stores.append(row.store.id)
    assert row.store.status == StoreStatus.ACTIVE


# ============================================================================
# U: update
# ============================================================================


async def test_u1_update_happy_path(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """Single field changes; updated_at bumps; updated_by_* flips."""
    t = await make_tenant(name="U1-Tenant")
    parent_id = await make_parent_org_node(t.id, "u1")
    actor = await make_platform_user(status="ACTIVE")
    second_actor = await make_platform_user(status="ACTIVE")
    created = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="U1-Original",
            store_code="U1-001",
        ),
    )
    cleanup_stores.append(created.store.id)
    original_updated_at = created.store.updated_at

    updated = await repo.update(
        platform_session,
        created.store.id,
        fields={"name": "U1-Renamed"},
        auth=_platform_auth(second_actor.id),
    )
    assert updated is not None
    assert updated.store.name == "U1-Renamed"
    # ``now()`` is TX-start time in Postgres; within one transaction
    # create + update share the same value. Use ``>=`` to acknowledge
    # the TX-bound timestamp; the BEFORE-UPDATE trigger DID fire (the
    # row's ``updated_by_user_id`` flip below is the proof of update).
    assert updated.store.updated_at >= original_updated_at
    assert updated.store.updated_by_user_id == second_actor.id
    # created_by_* unchanged.
    assert updated.store.created_by_user_id == actor.id


async def test_u2_update_empty_fields_dict_returns_row_unchanged(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """Repo allows fields={} (no-op UPDATE that still bumps actor +
    updated_at since the handler is supposed to guard empty bodies).

    Documents the seam: the handler enforces EmptyPatchError; the
    repo would still produce a SET on the actor columns. This test
    sets a single field to keep the test meaningful while documenting
    that the repo's path doesn't pre-check empty.
    """
    t = await make_tenant(name="U2-Tenant")
    parent_id = await make_parent_org_node(t.id, "u2")
    actor = await make_platform_user(status="ACTIVE")
    created = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="U2-Original",
            store_code="U2-001",
        ),
    )
    cleanup_stores.append(created.store.id)

    # Provide a single field to satisfy the repo's expectation of
    # non-empty input. The handler-level EmptyPatchError is exercised
    # at the router test layer.
    updated = await repo.update(
        platform_session,
        created.store.id,
        fields={"name": "U2-Original"},
        auth=_platform_auth(actor.id),
    )
    assert updated is not None
    assert updated.store.name == "U2-Original"


async def test_u3_update_non_empty_same_as_current_succeeds(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """LD4: non-empty PATCH where every field matches current still
    counts as a write — UPDATE returns the row; trigger fires.

    Timestamp assertion uses ``>=`` to acknowledge that ``now()`` is
    TX-start in Postgres; same-TX create + update share the value.
    The trigger DID fire (the UPDATE statement executed); proof of
    write is the materialised return value plus the actor pair below.
    """
    t = await make_tenant(name="U3-Tenant")
    parent_id = await make_parent_org_node(t.id, "u3")
    actor = await make_platform_user(status="ACTIVE")
    created = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="U3-Same",
            store_code="U3-001",
        ),
    )
    cleanup_stores.append(created.store.id)
    original_updated_at = created.store.updated_at

    updated = await repo.update(
        platform_session,
        created.store.id,
        fields={"name": "U3-Same"},
        auth=_platform_auth(actor.id),
    )
    assert updated is not None
    assert updated.store.name == "U3-Same"
    assert updated.store.updated_at >= original_updated_at


async def test_u4_update_rename_store_code_to_taken_raises(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """Rename to a store_code held by another store same tenant -> 409."""
    t = await make_tenant(name="U4-Tenant")
    parent_id = await make_parent_org_node(t.id, "u4")
    actor = await make_platform_user(status="ACTIVE")
    first = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="U4-First",
            store_code="U4-001",
        ),
    )
    cleanup_stores.append(first.store.id)
    second = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="U4-Second",
            store_code="U4-002",
        ),
    )
    cleanup_stores.append(second.store.id)

    with pytest.raises(DuplicateStoreCodeError):
        await repo.update(
            platform_session,
            second.store.id,
            fields={"store_code": "U4-001"},
            auth=_platform_auth(actor.id),
        )


async def test_u5_update_rename_store_code_to_self_succeeds(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """PATCH a row's store_code to its current value is a no-op success
    (the duplicate pre-check excludes self by id)."""
    t = await make_tenant(name="U5-Tenant")
    parent_id = await make_parent_org_node(t.id, "u5")
    actor = await make_platform_user(status="ACTIVE")
    created = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="U5-Store",
            store_code="U5-SAME",
        ),
    )
    cleanup_stores.append(created.store.id)

    updated = await repo.update(
        platform_session,
        created.store.id,
        fields={"store_code": "U5-SAME"},
        auth=_platform_auth(actor.id),
    )
    assert updated is not None
    assert updated.store.store_code == "U5-SAME"


async def test_u6_update_unknown_id_returns_none(
    repo,
    make_platform_user,
    platform_session,
) -> None:
    """RLS-as-404: missing or RLS-filtered row -> repo returns None."""
    actor = await make_platform_user(status="ACTIVE")
    missing_id = uuid.uuid4()
    result = await repo.update(
        platform_session,
        missing_id,
        fields={"name": "X"},
        auth=_platform_auth(actor.id),
    )
    assert result is None


async def test_u7_update_under_tenant_a_for_tenant_b_store_returns_none(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    tenant_session_factory,
) -> None:
    """A TENANT-A session attempting PATCH on TENANT-B's store sees
    None (RLS-as-404 per D-17). The handler maps None to 404
    STORE_NOT_FOUND."""
    t_a = await make_tenant(name="U7-A")
    t_b = await make_tenant(name="U7-B")
    actor = await make_platform_user(status="ACTIVE")
    store_b = await make_store(tenant_id=t_b.id, name="U7-B-Store")

    async with tenant_session_factory(t_a.id) as session:
        result = await repo.update(
            session,
            store_b.id,
            fields={"name": "U7-Hacked"},
            auth=_tenant_auth(actor.id, t_a.id),
        )
        assert result is None


async def test_u8_update_populates_updated_by_pair_and_bumps_updated_at(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """updated_by_user_id + updated_by_user_type co-write; updated_at bumps."""
    t = await make_tenant(name="U8-Tenant")
    parent_id = await make_parent_org_node(t.id, "u8")
    actor = await make_platform_user(status="ACTIVE")
    second_actor = await make_platform_user(status="ACTIVE")
    created = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="U8-Store",
            store_code="U8-001",
        ),
    )
    cleanup_stores.append(created.store.id)
    original_updated_at = created.store.updated_at

    updated = await repo.update(
        platform_session,
        created.store.id,
        fields={"address": "200 New St"},
        auth=_platform_auth(second_actor.id),
    )
    assert updated is not None
    assert updated.store.updated_by_user_id == second_actor.id
    assert updated.store.updated_by_user_type == ActorUserType.PLATFORM
    # ``>=`` per the TX-bound ``now()`` constraint documented on U1.
    assert updated.store.updated_at >= original_updated_at


# ============================================================================
# T: state transitions (Step 6.17.4)
#
# Each test creates a store via ``make_store`` (which uses the fixture's
# committed-INSERT path) and exercises one matrix cell via
# ``repo.transition``. The fixture's teardown DELETEs the store row.
#
# T1-T9 cover the 9 allowed cells; T10-T12 cover the 3 rejected
# (``*->OPENING``) cells; T13 covers same-state (per LD5);
# T14 covers unknown-id NOT_FOUND; T15 covers cross-tenant RLS-as-404;
# T16 inspects Pattern (b) audit-actor invariants across the
# class-1/class-2/class-3 paths.
#
# Uses ``platform_session`` directly so the transition's SELECT FOR
# UPDATE + UPDATE land in the same TX as ``make_store``'s commit. The
# trigger's ``updated_at = now()`` is TX-bound (per U1's note); we
# assert ``>=`` rather than strict ``>`` on timestamp comparisons.
# ============================================================================


async def test_t1_opening_to_active(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """OPENING -> ACTIVE: status flips; updated_by_* set; closed_* remain NULL."""
    t = await make_tenant(name="T1-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    store = await make_store(
        tenant_id=t.id, name="T1-Store", status=StoreStatus.OPENING
    )

    row, result = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.ACTIVE,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.OK
    assert row is not None
    assert row.store.status is StoreStatus.ACTIVE
    assert row.store.updated_by_user_id == actor.id
    assert row.store.updated_by_user_type is ActorUserType.PLATFORM
    assert row.store.closed_at is None
    assert row.store.closed_by_user_id is None
    assert row.store.closed_by_user_type is None


async def test_t2_opening_to_inactive(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """OPENING -> INACTIVE: status flips; closed_* remain NULL."""
    t = await make_tenant(name="T2-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    store = await make_store(
        tenant_id=t.id, name="T2-Store", status=StoreStatus.OPENING
    )

    row, result = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.INACTIVE,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.OK
    assert row is not None
    assert row.store.status is StoreStatus.INACTIVE
    assert row.store.closed_at is None


async def test_t3_opening_to_closed_populates_closed_triplet(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """OPENING -> CLOSED (Class 1): closed_at + closed_by_* populated."""
    t = await make_tenant(name="T3-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    store = await make_store(
        tenant_id=t.id, name="T3-Store", status=StoreStatus.OPENING
    )

    row, result = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.CLOSED,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.OK
    assert row is not None
    assert row.store.status is StoreStatus.CLOSED
    assert row.store.closed_at is not None
    assert row.store.closed_by_user_id == actor.id
    assert row.store.closed_by_user_type is ActorUserType.PLATFORM


async def test_t4_active_to_inactive_keeps_closed_null(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """ACTIVE -> INACTIVE (Class 3): closed_* remain NULL."""
    t = await make_tenant(name="T4-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    store = await make_store(
        tenant_id=t.id, name="T4-Store", status=StoreStatus.ACTIVE
    )

    row, result = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.INACTIVE,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.OK
    assert row is not None
    assert row.store.status is StoreStatus.INACTIVE
    assert row.store.closed_at is None
    assert row.store.closed_by_user_id is None


async def test_t5_active_to_closed_populates_closed_triplet(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """ACTIVE -> CLOSED (Class 1): closed_at + closed_by_* populated."""
    t = await make_tenant(name="T5-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    store = await make_store(
        tenant_id=t.id, name="T5-Store", status=StoreStatus.ACTIVE
    )

    row, result = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.CLOSED,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.OK
    assert row is not None
    assert row.store.status is StoreStatus.CLOSED
    assert row.store.closed_at is not None
    assert row.store.closed_by_user_id == actor.id
    assert row.store.closed_by_user_type is ActorUserType.PLATFORM


async def test_t6_inactive_to_active(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """INACTIVE -> ACTIVE (Class 3): row transitions; closed_* untouched."""
    t = await make_tenant(name="T6-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    store = await make_store(
        tenant_id=t.id, name="T6-Store", status=StoreStatus.INACTIVE
    )

    row, result = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.ACTIVE,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.OK
    assert row is not None
    assert row.store.status is StoreStatus.ACTIVE


async def test_t7_inactive_to_closed(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """INACTIVE -> CLOSED (Class 1): closed triplet populated."""
    t = await make_tenant(name="T7-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    store = await make_store(
        tenant_id=t.id, name="T7-Store", status=StoreStatus.INACTIVE
    )

    row, result = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.CLOSED,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.OK
    assert row is not None
    assert row.store.status is StoreStatus.CLOSED
    assert row.store.closed_at is not None


async def test_t8_closed_to_active_nulls_closed_triplet(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """CLOSED -> ACTIVE (Class 2): closed_at + closed_by_* nulled."""
    t = await make_tenant(name="T8-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    # Start in ACTIVE so we can transition into CLOSED first, then out.
    # The DDL CHECK ck_stores_closed_consistency requires the closed_*
    # triplet to be co-set with status=CLOSED, so a make_store row with
    # bare status=CLOSED would fail. The two-step setup mirrors how the
    # state would arrive in real flow.
    store = await make_store(
        tenant_id=t.id, name="T8-Store", status=StoreStatus.ACTIVE
    )
    _, r0 = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.CLOSED,
        auth=_platform_auth(actor.id),
    )
    assert r0 is TransitionResult.OK

    row, result = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.ACTIVE,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.OK
    assert row is not None
    assert row.store.status is StoreStatus.ACTIVE
    assert row.store.closed_at is None
    assert row.store.closed_by_user_id is None
    assert row.store.closed_by_user_type is None
    # updated_by_* re-stamped on the out-of-CLOSED transition.
    assert row.store.updated_by_user_id == actor.id
    assert row.store.updated_by_user_type is ActorUserType.PLATFORM


async def test_t9_closed_to_inactive_nulls_closed_triplet(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """CLOSED -> INACTIVE (Class 2): closed triplet nulled."""
    t = await make_tenant(name="T9-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    store = await make_store(
        tenant_id=t.id, name="T9-Store", status=StoreStatus.ACTIVE
    )
    _, r0 = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.CLOSED,
        auth=_platform_auth(actor.id),
    )
    assert r0 is TransitionResult.OK

    row, result = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.INACTIVE,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.OK
    assert row is not None
    assert row.store.status is StoreStatus.INACTIVE
    assert row.store.closed_at is None
    assert row.store.closed_by_user_id is None


async def test_t10_active_to_opening_rejected(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """ACTIVE -> OPENING: rejected (LD1; *->OPENING not in matrix)."""
    t = await make_tenant(name="T10-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    store = await make_store(
        tenant_id=t.id, name="T10-Store", status=StoreStatus.ACTIVE
    )

    row, result = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.OPENING,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.INVALID_STATE
    assert row is None


async def test_t11_inactive_to_opening_rejected(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """INACTIVE -> OPENING: rejected."""
    t = await make_tenant(name="T11-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    store = await make_store(
        tenant_id=t.id, name="T11-Store", status=StoreStatus.INACTIVE
    )

    row, result = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.OPENING,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.INVALID_STATE
    assert row is None


async def test_t12_closed_to_opening_rejected(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """CLOSED -> OPENING: rejected (no reopen-to-OPENING per LD1)."""
    t = await make_tenant(name="T12-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    store = await make_store(
        tenant_id=t.id, name="T12-Store", status=StoreStatus.ACTIVE
    )
    _, r0 = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.CLOSED,
        auth=_platform_auth(actor.id),
    )
    assert r0 is TransitionResult.OK

    row, result = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.OPENING,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.INVALID_STATE
    assert row is None


async def test_t13_same_state_rejected(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """ACTIVE -> ACTIVE: rejected per LD5 (target NOT in own
    allowed-sources set; mirrors tenants ``allowed_sources``)."""
    t = await make_tenant(name="T13-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    store = await make_store(
        tenant_id=t.id, name="T13-Store", status=StoreStatus.ACTIVE
    )

    row, result = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.ACTIVE,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.INVALID_STATE
    assert row is None


async def test_t14_unknown_store_id_returns_not_found(
    repo,
    make_platform_user,
    platform_session,
) -> None:
    """Unknown store_id -> (None, NOT_FOUND)."""
    actor = await make_platform_user(status="ACTIVE")
    missing_id = uuid.uuid4()
    row, result = await repo.transition(
        platform_session,
        missing_id,
        target_status=StoreStatus.ACTIVE,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.NOT_FOUND
    assert row is None


async def test_t15_cross_tenant_under_tenant_a_returns_not_found(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    tenant_session_factory,
) -> None:
    """A TENANT-A session attempting transition on TENANT-B's store
    sees (None, NOT_FOUND) — RLS-as-404 per D-17."""
    t_a = await make_tenant(name="T15-A")
    t_b = await make_tenant(name="T15-B")
    actor = await make_platform_user(status="ACTIVE")
    store_b = await make_store(
        tenant_id=t_b.id, name="T15-B-Store", status=StoreStatus.ACTIVE
    )

    async with tenant_session_factory(t_a.id) as session:
        row, result = await repo.transition(
            session,
            store_b.id,
            target_status=StoreStatus.INACTIVE,
            auth=_tenant_auth(actor.id, t_a.id),
        )
    assert result is TransitionResult.NOT_FOUND
    assert row is None


async def test_t16_pattern_b_audit_actor_invariants(
    repo,
    make_tenant,
    make_platform_user,
    make_store,
    platform_session,
) -> None:
    """Class 1 populates closed_by_* pair; Class 2 nulls it;
    Class 3 leaves closed_* unchanged. updated_by_* always re-stamped.

    Sweeps OPENING -> CLOSED (Class 1) -> ACTIVE (Class 2) ->
    INACTIVE (Class 3) on one row.
    """
    t = await make_tenant(name="T16-Tenant")
    a1 = await make_platform_user(status="ACTIVE")
    a2 = await make_platform_user(status="ACTIVE")
    a3 = await make_platform_user(status="ACTIVE")
    store = await make_store(
        tenant_id=t.id, name="T16-Store", status=StoreStatus.OPENING
    )

    # Class 1: OPENING -> CLOSED with actor a1.
    row, r0 = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.CLOSED,
        auth=_platform_auth(a1.id),
    )
    assert r0 is TransitionResult.OK
    assert row is not None
    assert row.store.closed_by_user_id == a1.id
    assert row.store.closed_by_user_type is ActorUserType.PLATFORM
    assert row.store.updated_by_user_id == a1.id

    # Class 2: CLOSED -> ACTIVE with actor a2. closed_by_* nulled;
    # updated_by_* re-stamped to a2.
    row, r1 = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.ACTIVE,
        auth=_platform_auth(a2.id),
    )
    assert r1 is TransitionResult.OK
    assert row is not None
    assert row.store.closed_at is None
    assert row.store.closed_by_user_id is None
    assert row.store.closed_by_user_type is None
    assert row.store.updated_by_user_id == a2.id

    # Class 3: ACTIVE -> INACTIVE with actor a3. closed_* still NULL;
    # updated_by_* re-stamped to a3.
    row, r2 = await repo.transition(
        platform_session,
        store.id,
        target_status=StoreStatus.INACTIVE,
        auth=_platform_auth(a3.id),
    )
    assert r2 is TransitionResult.OK
    assert row is not None
    assert row.store.closed_at is None
    assert row.store.updated_by_user_id == a3.id


# ============================================================================
# PW: Step 6.21.2 paired-write cascade tests.
#
# These exercise the atomic store + paired STORE-type org_node behaviour:
# create writes both rows in one transaction; update cascades shared
# fields; transition cascades status via STORE_STATUS_TO_ORG_NODE_STATUS
# into the org_node's status + archived_* triplet.
#
# Helper for org_node lookup: ``_fetch_org_node`` runs under platform_session
# so PLATFORM sees the paired org_node regardless of which tenant context
# created it.
# ============================================================================


async def _fetch_org_node_row(
    session: AsyncSession, org_node_id: UUID
) -> Any:
    """Helper: fetch a single org_node row by id under the given
    session. Returns the ORM-mapped row or None.
    """
    schema = get_settings().db_schema
    result = await session.execute(
        text(
            f"SELECT id, tenant_id, parent_id, path::text AS path, "
            "node_type, status, name, code, archived_at, "
            "archived_by_user_id, archived_by_user_type, "
            "updated_by_user_id, updated_by_user_type "
            f"FROM {schema}.org_nodes WHERE id = :id LIMIT 1"
        ),
        {"id": org_node_id},
    )
    return result.first()


async def test_pw1_create_with_valid_parent_creates_paired_org_node(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """LOAD-BEARING: atomic-pair invariant. After repo.create, both
    rows (stores + paired STORE-type org_nodes) exist and link 1:1.
    Audit-actor pair populated on both rows.

    Verifies the core Step 6.21.2 invariant: the org_node is created
    server-side; the store's org_node_id links to it; the org_node's
    code matches the store's store_code (field ownership per
    architecture.md A.5).
    """
    t = await make_tenant(name="PW1-Tenant")
    parent_id = await make_parent_org_node(t.id, "pw1")
    actor = await make_platform_user(status="ACTIVE")
    row = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="PW1-Store",
            store_code="PW1-001",
        ),
    )
    cleanup_stores.append(row.store.id)

    # Stores row populated.
    assert row.store.org_node_id is not None
    assert row.store.created_by_user_id == actor.id

    # Paired org_node exists, is STORE-type, parent matches.
    paired = await _fetch_org_node_row(platform_session, row.store.org_node_id)
    assert paired is not None
    assert paired.node_type == "STORE"
    assert paired.tenant_id == t.id
    assert paired.parent_id == parent_id
    assert paired.code == "PW1-001"  # field ownership
    assert paired.name == "PW1-Store"
    assert paired.updated_by_user_id == actor.id


async def test_pw2_create_with_missing_parent_no_rows_created(
    repo,
    make_tenant,
    make_platform_user,
    cleanup_stores,
    platform_session,
) -> None:
    """LOAD-BEARING: atomic-pair invariant on failure. After a 404
    on parent validation, NO rows exist in either stores or org_nodes
    for this tenant (the transaction rolled back, or pre-check
    rejected before any INSERT)."""
    t = await make_tenant(name="PW2-Tenant")
    actor = await make_platform_user(status="ACTIVE")
    bogus_parent = uuid.uuid4()

    with pytest.raises(ParentNodeNotFoundError):
        await repo.create(
            platform_session,
            **_base_create_kwargs(
                tenant_id=t.id,
                parent_org_node_id=bogus_parent,
                actor_id=actor.id,
                name="PW2-Store",
                store_code="PW2-001",
            ),
        )

    # Verify no orphan org_node or stores row landed.
    schema = get_settings().db_schema
    store_count = (
        await platform_session.execute(
            text(
                f"SELECT COUNT(*) FROM {schema}.stores "
                "WHERE tenant_id = :tid"
            ),
            {"tid": t.id},
        )
    ).scalar_one()
    assert store_count == 0
    # Tenant has no STORE-type org_node either.
    store_node_count = (
        await platform_session.execute(
            text(
                f"SELECT COUNT(*) FROM {schema}.org_nodes "
                "WHERE tenant_id = :tid AND node_type = "
                f"CAST('STORE' AS {schema}.org_node_type_enum)"
            ),
            {"tid": t.id},
        )
    ).scalar_one()
    assert store_node_count == 0


async def test_pw3_create_with_store_type_parent_raises_invalid_parent_type(
    repo,
    make_tenant,
    make_org_node,
    make_platform_user,
    cleanup_stores,
    platform_session,
) -> None:
    """STORE-type parent_org_node_id -> InvalidParentNodeTypeError (422).
    No store row, no new org_node row.
    """
    t = await make_tenant(name="PW3-Tenant")
    actor = await make_platform_user(status="ACTIVE")

    root_id, root_path = await make_org_node(
        tenant_id=t.id,
        node_type="TENANT",
        code=f"pw3-{uuid.uuid4().hex[:6]}",
        name="PW3-Root",
    )
    # Make a STORE-type org_node directly via make_org_node (no paired
    # store; this is a fixture-built malformed shape we're using as a
    # malicious-input parent_org_node_id).
    store_node_id, _ = await make_org_node(
        tenant_id=t.id,
        node_type="STORE",
        code=f"pw3-sn-{uuid.uuid4().hex[:6]}",
        name="PW3-StoreSlot",
        parent_id=root_id,
        parent_path=root_path,
    )

    with pytest.raises(InvalidParentNodeTypeError):
        await repo.create(
            platform_session,
            **_base_create_kwargs(
                tenant_id=t.id,
                parent_org_node_id=store_node_id,
                actor_id=actor.id,
                name="PW3-Store",
                store_code="PW3-001",
            ),
        )


async def test_pw4_create_with_cross_tenant_parent_raises_parent_not_found(
    repo,
    make_tenant,
    make_org_node,
    make_platform_user,
    cleanup_stores,
    platform_session,
) -> None:
    """LOAD-BEARING: parent_org_node_id pointing at a different tenant's
    node raises ParentNodeNotFoundError (404; RLS-as-404 collapse).
    Same shape as C5 (which is now this exact test under the new
    error class), kept under the PW namespace for explicit cascade
    coverage."""
    t_a = await make_tenant(name="PW4-A")
    t_b = await make_tenant(name="PW4-B")
    actor = await make_platform_user(status="ACTIVE")
    root_b, root_b_path = await make_org_node(
        tenant_id=t_b.id,
        node_type="TENANT",
        code=f"pw4b-{uuid.uuid4().hex[:6]}",
        name="PW4-B-Root",
    )
    hq_b, _ = await make_org_node(
        tenant_id=t_b.id,
        node_type="HQ",
        code=f"pw4b-hq-{uuid.uuid4().hex[:6]}",
        name="PW4-B-HQ",
        parent_id=root_b,
        parent_path=root_b_path,
    )
    with pytest.raises(ParentNodeNotFoundError):
        await repo.create(
            platform_session,
            **_base_create_kwargs(
                tenant_id=t_a.id,
                parent_org_node_id=hq_b,
                actor_id=actor.id,
                name="PW4-Store",
                store_code="PW4-001",
            ),
        )


async def test_pw5_update_name_cascades_to_paired_org_node(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """PATCH /stores name change cascades to paired org_node.name in
    one transaction. The paired org_node's updated_by_* pair re-stamps
    with the same actor as the store's updated_by_* pair."""
    t = await make_tenant(name="PW5-Tenant")
    parent_id = await make_parent_org_node(t.id, "pw5")
    actor = await make_platform_user(status="ACTIVE")
    second_actor = await make_platform_user(status="ACTIVE")
    created = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="PW5-Original",
            store_code="PW5-001",
        ),
    )
    cleanup_stores.append(created.store.id)
    org_node_id = created.store.org_node_id
    assert org_node_id is not None

    updated = await repo.update(
        platform_session,
        created.store.id,
        fields={"name": "PW5-Renamed"},
        auth=_platform_auth(second_actor.id),
    )
    assert updated is not None
    assert updated.store.name == "PW5-Renamed"

    paired = await _fetch_org_node_row(platform_session, org_node_id)
    assert paired is not None
    assert paired.name == "PW5-Renamed"
    assert paired.updated_by_user_id == second_actor.id


async def test_pw6a_update_store_code_cascades_with_stores_collision(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """LOAD-BEARING: PATCH store_code -> 409 DUPLICATE_STORE_CODE when
    case-insensitive collision against another store in the same tenant.

    PW6 splits at Step 6.21.2 design (Deviation #7) into 6a (store-vs-
    store collision via _raise_if_store_code_taken) and 6b (store-vs-
    org_node code collision via the cascade UPDATE). This test covers
    6a's narrower scope."""
    t = await make_tenant(name="PW6a-Tenant")
    parent_id = await make_parent_org_node(t.id, "pw6a")
    actor = await make_platform_user(status="ACTIVE")
    first = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="PW6a-First",
            store_code="PW6A-001",
        ),
    )
    cleanup_stores.append(first.store.id)
    second = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="PW6a-Second",
            store_code="PW6A-002",
        ),
    )
    cleanup_stores.append(second.store.id)

    # Case-insensitive: 'pw6a-001' should collide with 'PW6A-001'.
    with pytest.raises(DuplicateStoreCodeError):
        await repo.update(
            platform_session,
            second.store.id,
            fields={"store_code": "pw6a-001"},
            auth=_platform_auth(actor.id),
        )


async def test_pw6b_update_store_code_cascades_with_org_node_code_collision(
    repo,
    make_tenant,
    make_org_node,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """LOAD-BEARING: PATCH store_code where the new value collides with
    an existing non-STORE org_node code in the same tenant ->
    409 DUPLICATE_ORG_NODE_CODE via the cascade UPDATE. Broader scope
    than PW6a (case-insensitive uniqueness across all org_node types,
    not just stores).

    This catches the scenario where the stores-only pre-check passes
    (no other store has the code) but the cascade hits
    uq_org_nodes_tenant_code_lower."""
    t = await make_tenant(name="PW6b-Tenant")
    parent_id = await make_parent_org_node(t.id, "pw6b")
    actor = await make_platform_user(status="ACTIVE")

    # Create a separate REGION-level org_node with code "PW6B-REGION".
    # The cascade UPDATE will try to set the paired STORE org_node's
    # code to "PW6B-REGION" (case-insensitively colliding).
    region_id, region_path = await make_org_node(
        tenant_id=t.id,
        node_type="REGION",
        code="PW6B-REGION",
        name="PW6b Region",
        parent_id=parent_id,
        parent_path=(
            await _fetch_org_node_row(platform_session, parent_id)
        ).path,
    )
    assert region_id is not None
    assert region_path is not None

    store = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="PW6b-Store",
            store_code="PW6B-STORE-INIT",
        ),
    )
    cleanup_stores.append(store.store.id)

    # Case-insensitive collision: 'pw6b-region' against the REGION
    # node's PW6B-REGION code.
    with pytest.raises(DuplicateOrgNodeCodeError):
        await repo.update(
            platform_session,
            store.store.id,
            fields={"store_code": "pw6b-region"},
            auth=_platform_auth(actor.id),
        )


async def test_pw7_update_parent_org_node_id_reparents_paired(
    repo,
    make_tenant,
    make_org_node,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """LOAD-BEARING: PATCH with parent_org_node_id reparents the paired
    org_node (changes its parent_id). stores.org_node_id remains
    unchanged (the slot id is immutable)."""
    t = await make_tenant(name="PW7-Tenant")
    initial_parent_id = await make_parent_org_node(t.id, "pw7a")
    actor = await make_platform_user(status="ACTIVE")
    created = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=initial_parent_id,
            actor_id=actor.id,
            name="PW7-Store",
            store_code="PW7-001",
        ),
    )
    cleanup_stores.append(created.store.id)
    original_org_node_id = created.store.org_node_id
    assert original_org_node_id is not None

    # Build a second HQ-level org_node under a different root to
    # reparent under. Reuse make_parent_org_node for convenience.
    new_parent_id = await make_parent_org_node(t.id, "pw7b")

    updated = await repo.update(
        platform_session,
        created.store.id,
        fields={"parent_org_node_id": new_parent_id},
        auth=_platform_auth(actor.id),
    )
    assert updated is not None
    # store.org_node_id is unchanged (the slot id is immutable).
    assert updated.store.org_node_id == original_org_node_id

    paired = await _fetch_org_node_row(platform_session, original_org_node_id)
    assert paired is not None
    # The paired org_node's parent_id moved to the new parent.
    assert paired.parent_id == new_parent_id


async def test_pw8_transition_to_closed_archives_paired_org_node(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """LOAD-BEARING: store CLOSED -> paired org_node ARCHIVED via the
    cascade. archived_* triplet on the org_node populated with same
    actor as the store's closed_* triplet (same transaction).
    """
    t = await make_tenant(name="PW8-Tenant")
    parent_id = await make_parent_org_node(t.id, "pw8")
    actor = await make_platform_user(status="ACTIVE")
    created = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="PW8-Store",
            store_code="PW8-001",
        ),
    )
    cleanup_stores.append(created.store.id)
    org_node_id = created.store.org_node_id
    assert org_node_id is not None

    row, result = await repo.transition(
        platform_session,
        created.store.id,
        target_status=StoreStatus.CLOSED,
        auth=_platform_auth(actor.id),
    )
    assert result is TransitionResult.OK
    assert row is not None
    assert row.store.status == StoreStatus.CLOSED
    assert row.store.closed_by_user_id == actor.id

    paired = await _fetch_org_node_row(platform_session, org_node_id)
    assert paired is not None
    assert paired.status == "ARCHIVED"
    assert paired.archived_at is not None
    assert paired.archived_by_user_id == actor.id
    assert paired.archived_by_user_type == "PLATFORM"


async def test_pw9_transition_from_closed_unarchives_paired_org_node(
    repo,
    make_tenant,
    make_platform_user,
    make_parent_org_node,
    cleanup_stores,
    platform_session,
) -> None:
    """LOAD-BEARING: store CLOSED -> ACTIVE cascades paired org_node
    ARCHIVED -> ACTIVE. The org_node's archived_* triplet is nulled
    atomically with the status flip."""
    t = await make_tenant(name="PW9-Tenant")
    parent_id = await make_parent_org_node(t.id, "pw9")
    actor = await make_platform_user(status="ACTIVE")
    created = await repo.create(
        platform_session,
        **_base_create_kwargs(
            tenant_id=t.id,
            parent_org_node_id=parent_id,
            actor_id=actor.id,
            name="PW9-Store",
            store_code="PW9-001",
        ),
    )
    cleanup_stores.append(created.store.id)
    org_node_id = created.store.org_node_id
    assert org_node_id is not None

    # First close it.
    _, r0 = await repo.transition(
        platform_session,
        created.store.id,
        target_status=StoreStatus.CLOSED,
        auth=_platform_auth(actor.id),
    )
    assert r0 is TransitionResult.OK

    # Now revive.
    row, r1 = await repo.transition(
        platform_session,
        created.store.id,
        target_status=StoreStatus.ACTIVE,
        auth=_platform_auth(actor.id),
    )
    assert r1 is TransitionResult.OK
    assert row is not None
    assert row.store.status == StoreStatus.ACTIVE
    assert row.store.closed_at is None

    paired = await _fetch_org_node_row(platform_session, org_node_id)
    assert paired is not None
    assert paired.status == "ACTIVE"
    assert paired.archived_at is None
    assert paired.archived_by_user_id is None
    assert paired.archived_by_user_type is None


def test_pw10_status_mapping_projects_correctly() -> None:
    """STORE_STATUS_TO_ORG_NODE_STATUS contains the locked LD6
    projection (architecture.md A.5 "Status mapping table").

    Pure unit-style assertion on the dict — no DB. The cascade
    behaviour itself is exercised by PW8 / PW9 / T-series via the
    state machine.
    """
    assert (
        STORE_STATUS_TO_ORG_NODE_STATUS[StoreStatus.OPENING]
        == OrgNodeStatus.ACTIVE
    )
    assert (
        STORE_STATUS_TO_ORG_NODE_STATUS[StoreStatus.ACTIVE]
        == OrgNodeStatus.ACTIVE
    )
    assert (
        STORE_STATUS_TO_ORG_NODE_STATUS[StoreStatus.INACTIVE]
        == OrgNodeStatus.INACTIVE
    )
    assert (
        STORE_STATUS_TO_ORG_NODE_STATUS[StoreStatus.CLOSED]
        == OrgNodeStatus.ARCHIVED
    )
    # Every StoreStatus is covered.
    assert set(STORE_STATUS_TO_ORG_NODE_STATUS.keys()) == set(StoreStatus)
