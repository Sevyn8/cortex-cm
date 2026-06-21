"""Step 6.14 integration tests for ``TenantUsersRepo`` write methods.

Six repo-level tests covering the diff-replace invariants and the
race-control catch path that the router-level R-tests can't reliably
simulate inside a single pytest event loop:

  RT1 — Diff-replace preserves ``granted_at`` and ``granted_by_*`` on
        unchanged tuples in (current ∩ desired). The router-level R3
        verifies the same invariant via the wire response; RT1 reads
        the raw row state to confirm no UPDATE fired on the unchanged
        rows (in particular ``updated_at`` stays put because no UPDATE
        ever ran).
  RT2 — Validation runs ahead of any write (LD4 ordering): a malformed
        roles[] entry never reaches the diff helper, so no SELECT FOR
        UPDATE on the assignments table, no INSERT, no UPDATE.
  RT3 — Pattern B: two ACTIVE rows for the same (user, role) at
        distinct ``org_node_id`` values succeed via the diff helper.
  RT4 — LOAD-BEARING: pre-INSERT a duplicate ACTIVE row via a separate
        committed session, then call ``_apply_role_assignments_diff``
        directly with ``current_set=empty``. The INSERT collides with
        ``uq_tenant_user_role_assignments_active``; the repo catches
        ``IntegrityError`` ONLY for that constraint name and raises
        ``RoleAssignmentConflictError``.
  RT5 — Validation order is deterministic: malformed role_id case
        fires INVALID_ROLE before malformed org_node_id fires
        INVALID_ORG_NODE.
  RT6 — Transaction rollback: when org_node validation fails after
        roles passes, no ``tenant_users`` row lands AND no
        ``tenant_user_role_assignments`` rows land.

Fixture-order discipline: ``make_platform_user`` BEFORE ``platform_session``
so the platform_user's FK targets survive until after LIFO teardown
DELETEs assignments + tenant_users. Side-channel writes use SEPARATE
``get_tenant_session`` iterators that auto-commit independently of
``platform_session``.
"""
from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext
from admin_backend.config import get_settings
from admin_backend.db.session import get_tenant_session
from admin_backend.errors import (
    InvalidOrgNodeError,
    InvalidRoleError,
    RoleAssignmentConflictError,
)
from admin_backend.models.tenant_user import ActorUserType
from admin_backend.repositories.tenant_users import TenantUsersRepo


@pytest.fixture
def repo() -> TenantUsersRepo:
    return TenantUsersRepo()


async def _fetch_assignment(
    session: AsyncSession, assignment_id: UUID
) -> Any:
    schema = get_settings().db_schema
    result = await session.execute(
        text(
            f"""
            SELECT id, status::text AS status, role_id, org_node_id,
                   granted_at, granted_by_user_id,
                   granted_by_user_type::text AS granted_by_user_type,
                   updated_at
              FROM {schema}.tenant_user_role_assignments
             WHERE id = :id
            """
        ),
        {"id": assignment_id},
    )
    return result.one()


async def _fetch_active_assignment_ids(
    session: AsyncSession, tenant_user_id: UUID
) -> list[UUID]:
    schema = get_settings().db_schema
    result = await session.execute(
        text(
            f"""
            SELECT id FROM {schema}.tenant_user_role_assignments
             WHERE tenant_user_id = :tu_id
               AND status = CAST('ACTIVE'
                            AS {schema}.user_role_assignment_status_enum)
            """
        ),
        {"tu_id": tenant_user_id},
    )
    return [UUID(str(r.id)) for r in result.all()]


async def _delete_user_and_assignments(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    user_ids: list[UUID],
) -> None:
    """Side-channel cleanup that auto-commits independently of
    platform_session. Used at end-of-test where platform_session has
    already auto-committed."""
    if not user_ids:
        return
    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        await session.execute(
            text(
                f"DELETE FROM {schema}.tenant_user_role_assignments "
                "WHERE tenant_user_id = ANY(:ids)"
            ),
            {"ids": user_ids},
        )
        await session.execute(
            text(
                f"DELETE FROM {schema}.tenant_users WHERE id = ANY(:ids)"
            ),
            {"ids": user_ids},
        )


# ============================================================================
# RT1 — Diff-replace preserves unchanged rows' granted_at + updated_at
# ============================================================================


async def test_rt1_diff_replace_preserves_unchanged_row_audit_fields(
    repo,
    make_tenant,
    make_org_node,
    make_role,
    make_platform_user,
    platform_session,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING (LD3): an unchanged (role, org_node) tuple between
    create and update keeps its original granted_at, granted_by_*,
    and updated_at.

    Bypasses the router so the test reads raw row state pre/post the
    update — confirming no UPDATE fired on unchanged rows.
    """
    tenant = await make_tenant(name="RT1-Tenant")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="TENANT",
        code=f"rt1-{uuid.uuid4().hex[:6]}",
        name="RT1 Root",
    )
    anchor_b_id, _ = await make_org_node(
        tenant_id=tenant.id,
        node_type="REGION",
        code=f"rt1b-{uuid.uuid4().hex[:6]}",
        name="RT1 Region B",
        parent_id=root_id,
        parent_path=root_path,
    )
    role = await make_role(audience="TENANT")
    actor = await make_platform_user()

    # Create user with two assignments via a separate session (auto-commits).
    new_user_id: UUID | None = None
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        detail = await repo.create(
            session,
            tenant_id=tenant.id,
            email=f"rt1-{uuid.uuid4().hex[:8]}@test.example.com",
            full_name="RT1 User",
            role_assignments=[
                (role.id, root_id), (role.id, anchor_b_id)
            ],
            actor_user_id=actor.id,
            actor_user_type=ActorUserType.PLATFORM,
        )
        assert detail is not None
        new_user_id = detail.user.id

    assert new_user_id is not None
    try:
        # Read pre-state via platform_session.
        active_before = await _fetch_active_assignment_ids(
            platform_session, new_user_id
        )
        assert len(active_before) == 2
        pre_rows: dict[tuple[UUID, UUID], Any] = {}
        for aid in active_before:
            r = await _fetch_assignment(platform_session, aid)
            pre_rows[(UUID(str(r.role_id)), UUID(str(r.org_node_id)))] = r
        pre_keep = pre_rows[(role.id, root_id)]

        # Update via a separate session: keep (role, root); drop anchor_b.
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            new_detail = await repo.update(
                session,
                new_user_id,
                fields={"roles": [(role.id, root_id)]},
                actor_user_id=actor.id,
                actor_user_type=ActorUserType.PLATFORM,
            )
            assert new_detail is not None

        # Re-read the kept row in a fresh session (avoid identity map).
        post_row = None
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            post_row = await _fetch_assignment(session, pre_keep.id)

        assert post_row is not None
        assert post_row.status == "ACTIVE"
        assert post_row.granted_at == pre_keep.granted_at
        assert post_row.granted_by_user_id == pre_keep.granted_by_user_id
        assert post_row.granted_by_user_type == pre_keep.granted_by_user_type
        assert post_row.updated_at == pre_keep.updated_at
    finally:
        await _delete_user_and_assignments(
            session_factory, platform_auth, [new_user_id]
        )


# ============================================================================
# RT2 — Validation order: roles validated before any write
# ============================================================================


async def test_rt2_validation_runs_before_writes(
    repo,
    make_tenant,
    make_org_node,
    make_platform_user,
    session_factory,
    platform_auth,
) -> None:
    """LD4: when ``_validate_roles`` raises, neither the tenant_users
    INSERT nor any tenant_user_role_assignments INSERT lands.
    """
    schema = get_settings().db_schema
    tenant = await make_tenant(name="RT2-Tenant")
    root_id, _root_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="TENANT",
        code=f"rt2-{uuid.uuid4().hex[:6]}",
        name="RT2 Root",
    )
    actor = await make_platform_user()
    unknown_role = uuid.uuid4()

    async def _count(session: AsyncSession, sql: str) -> int:
        row = await session.execute(text(sql), {"tid": tenant.id})
        return int(row.scalar_one())

    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        tu_before = await _count(
            session,
            f"SELECT count(*) FROM {schema}.tenant_users "
            "WHERE tenant_id = :tid",
        )
        ura_before = await _count(
            session,
            f"SELECT count(*) FROM "
            f"{schema}.tenant_user_role_assignments "
            "WHERE tenant_id = :tid",
        )

    # The repo.create raises inside its own session; rollback is
    # automatic when the session generator's transaction context
    # exits via exception.
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        with pytest.raises(InvalidRoleError):
            await repo.create(
                session,
                tenant_id=tenant.id,
                email=f"rt2-{uuid.uuid4().hex[:8]}@test.example.com",
                full_name="RT2 User",
                role_assignments=[(unknown_role, root_id)],
                actor_user_id=actor.id,
                actor_user_type=ActorUserType.PLATFORM,
            )
        # Roll the failed session back so the auto-commit at exit
        # doesn't try to write phantom state.
        await session.rollback()

    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        tu_after = await _count(
            session,
            f"SELECT count(*) FROM {schema}.tenant_users "
            "WHERE tenant_id = :tid",
        )
        ura_after = await _count(
            session,
            f"SELECT count(*) FROM "
            f"{schema}.tenant_user_role_assignments "
            "WHERE tenant_id = :tid",
        )

    assert tu_after == tu_before
    assert ura_after == ura_before


# ============================================================================
# RT3 — Pattern B: same role at two anchors via diff helper
# ============================================================================


async def test_rt3_pattern_b_same_role_at_distinct_anchors(
    repo,
    make_tenant,
    make_org_node,
    make_role,
    make_platform_user,
    session_factory,
    platform_auth,
) -> None:
    """The partial-UNIQUE index permits (user, role) at distinct
    org_node values; the diff helper's per-row INSERT succeeds for
    both."""
    tenant = await make_tenant(name="RT3-Tenant")
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="TENANT",
        code=f"rt3-{uuid.uuid4().hex[:6]}",
        name="RT3 Root",
    )
    anchor_a_id, _ = await make_org_node(
        tenant_id=tenant.id,
        node_type="REGION",
        code=f"rt3a-{uuid.uuid4().hex[:6]}",
        name="RT3 A",
        parent_id=root_id,
        parent_path=root_path,
    )
    anchor_b_id, _ = await make_org_node(
        tenant_id=tenant.id,
        node_type="REGION",
        code=f"rt3b-{uuid.uuid4().hex[:6]}",
        name="RT3 B",
        parent_id=root_id,
        parent_path=root_path,
    )
    role = await make_role(audience="TENANT")
    actor = await make_platform_user()

    new_user_id: UUID | None = None
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        detail = await repo.create(
            session,
            tenant_id=tenant.id,
            email=f"rt3-{uuid.uuid4().hex[:8]}@test.example.com",
            full_name="RT3 User",
            role_assignments=[
                (role.id, anchor_a_id), (role.id, anchor_b_id)
            ],
            actor_user_id=actor.id,
            actor_user_type=ActorUserType.PLATFORM,
        )
        assert detail is not None
        new_user_id = detail.user.id

    assert new_user_id is not None
    try:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            ids = await _fetch_active_assignment_ids(session, new_user_id)
        assert len(ids) == 2
    finally:
        await _delete_user_and_assignments(
            session_factory, platform_auth, [new_user_id]
        )


# ============================================================================
# RT4 — Concurrent UNIQUE conflict caught and raised as 409 type
# ============================================================================


async def test_rt4_concurrent_unique_conflict_raises_role_assignment_conflict(
    repo,
    make_tenant,
    make_org_node,
    make_role,
    make_platform_user,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING (LD7): pre-INSERT a conflicting ACTIVE row, then
    call ``_apply_role_assignments_diff`` directly with
    ``current_set=empty``. The INSERT collides with
    ``uq_tenant_user_role_assignments_active``; the repo catches the
    IntegrityError ONLY for that constraint name and raises
    ``RoleAssignmentConflictError``.

    Bypassing SELECT FOR UPDATE inside the test is the deterministic
    way to exercise the catch path; in real production the race
    happens between two concurrent requests, which pytest can't
    interleave inside a single event loop.
    """
    tenant = await make_tenant(name="RT4-Tenant")
    root_id, _root_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="TENANT",
        code=f"rt4-{uuid.uuid4().hex[:6]}",
        name="RT4 Root",
    )
    role = await make_role(audience="TENANT")
    actor = await make_platform_user()

    # Create the user with the conflicting (role, root) ACTIVE row.
    new_user_id: UUID | None = None
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        detail = await repo.create(
            session,
            tenant_id=tenant.id,
            email=f"rt4-{uuid.uuid4().hex[:8]}@test.example.com",
            full_name="RT4 User",
            role_assignments=[(role.id, root_id)],
            actor_user_id=actor.id,
            actor_user_type=ActorUserType.PLATFORM,
        )
        assert detail is not None
        new_user_id = detail.user.id

    assert new_user_id is not None
    try:
        # Call the diff helper directly with current_set=empty, faking
        # a SELECT FOR UPDATE that saw nothing. The INSERT collides
        # with the partial-UNIQUE index.
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            with pytest.raises(RoleAssignmentConflictError) as exc_info:
                await repo._apply_role_assignments_diff(
                    session,
                    tenant_id=tenant.id,
                    tenant_user_id=new_user_id,
                    current_set=set(),
                    desired_set={(role.id, root_id)},
                    actor_user_id=actor.id,
                    actor_user_type=ActorUserType.PLATFORM,
                )
            await session.rollback()

        triple = exc_info.value.context["conflicting_triple"]
        assert triple["tenant_user_id"] == str(new_user_id)
        assert triple["role_id"] == str(role.id)
        assert triple["org_node_id"] == str(root_id)
    finally:
        await _delete_user_and_assignments(
            session_factory, platform_auth, [new_user_id]
        )


# ============================================================================
# RT5 — Validation order: roles validated before org_nodes
# ============================================================================


async def test_rt5_role_validation_fires_before_org_node_validation(
    repo,
    make_tenant,
    make_platform_user,
    session_factory,
    platform_auth,
) -> None:
    """LD4 ordering: when BOTH role_id AND org_node_id are malformed,
    the raised error is InvalidRoleError, not InvalidOrgNodeError.
    """
    tenant = await make_tenant(name="RT5-Tenant")
    actor = await make_platform_user()
    unknown_role = uuid.uuid4()
    unknown_anchor = uuid.uuid4()

    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        with pytest.raises(InvalidRoleError):
            await repo.create(
                session,
                tenant_id=tenant.id,
                email=f"rt5-{uuid.uuid4().hex[:8]}@test.example.com",
                full_name="RT5 User",
                role_assignments=[(unknown_role, unknown_anchor)],
                actor_user_id=actor.id,
                actor_user_type=ActorUserType.PLATFORM,
            )
        await session.rollback()


# ============================================================================
# RT6 — Transaction rollback on org_node validation failure
# ============================================================================


async def test_rt6_create_rolls_back_on_org_node_validation_failure(
    repo,
    make_tenant,
    make_org_node,
    make_role,
    make_platform_user,
    session_factory,
    platform_auth,
) -> None:
    """Validation failure mid-create (org_node fails after roles pass)
    leaves no rows in either ``tenant_users`` or
    ``tenant_user_role_assignments``.
    """
    schema = get_settings().db_schema
    tenant = await make_tenant(name="RT6-Tenant")
    role = await make_role(audience="TENANT")
    actor = await make_platform_user()
    unknown_anchor = uuid.uuid4()

    async def _count(session: AsyncSession, sql: str) -> int:
        row = await session.execute(text(sql), {"tid": tenant.id})
        return int(row.scalar_one())

    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        tu_before = await _count(
            session,
            f"SELECT count(*) FROM {schema}.tenant_users "
            "WHERE tenant_id = :tid",
        )
        ura_before = await _count(
            session,
            f"SELECT count(*) FROM "
            f"{schema}.tenant_user_role_assignments "
            "WHERE tenant_id = :tid",
        )

    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        with pytest.raises(InvalidOrgNodeError):
            await repo.create(
                session,
                tenant_id=tenant.id,
                email=f"rt6-{uuid.uuid4().hex[:8]}@test.example.com",
                full_name="RT6 User",
                role_assignments=[(role.id, unknown_anchor)],
                actor_user_id=actor.id,
                actor_user_type=ActorUserType.PLATFORM,
            )
        await session.rollback()

    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        tu_after = await _count(
            session,
            f"SELECT count(*) FROM {schema}.tenant_users "
            "WHERE tenant_id = :tid",
        )
        ura_after = await _count(
            session,
            f"SELECT count(*) FROM "
            f"{schema}.tenant_user_role_assignments "
            "WHERE tenant_id = :tid",
        )

    assert tu_after == tu_before
    assert ura_after == ura_before
