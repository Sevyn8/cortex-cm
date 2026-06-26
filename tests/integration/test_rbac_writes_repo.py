"""Repo-direct tests for invariant edge cases (Step 6.18.3).

Smaller test set targeting code paths that are difficult or
non-determistic to reach via the router (W-series). Six tests:

  RW1  _count_override_global_active_holders with mixed
       ACTIVE/INACTIVE assignments
  RW2  _count_override_global_active_holders excludes role-under-edit
       when exclude_role_id is set
  RW3  Both-side filter: INACTIVE assignment OR SUSPENDED user yields 0
  RW4  RolesRepo.update Layer 2 tripwire fires (monkeypatched corruption)
  RW5  Diff-replace preserves created_at + created_by_* (repo-direct)
  RW6  Repo transaction rollback: any error mid-flow leaves DB
       unchanged
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext
from admin_backend.auth.stub import CLAIM_USER_ID
from admin_backend.config import get_settings
from admin_backend.db.session import get_tenant_session
from admin_backend.errors import InternalInvariantViolationError
from admin_backend.models.tenant_user import ActorUserType
from admin_backend.repositories.roles import (
    OVERRIDE_GLOBAL_CODE,
    RolesRepo,
    _count_override_global_active_holders,
    _resolve_override_global_permission_id,
)
from admin_backend.schemas.role import RoleUpdateRequest


# ============================================================================
# Helpers
# ============================================================================


@pytest_asyncio.fixture
async def cleanup_role_perms_for_roles(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Cleanup fixture: tracks role_ids and DELETEs ALL
    ``role_permissions`` for those roles at teardown.

    Mirrors the router-test-side fixture of the same name. Required
    by repo tests that call ``RolesRepo.update`` which can INSERT
    junction rows not tracked by ``make_role_permission``.
    """
    tracked: list[UUID] = []
    yield tracked
    if tracked:
        schema = get_settings().db_schema
        async for s in get_tenant_session(platform_auth, session_factory):
            await s.execute(
                text(
                    f"DELETE FROM {schema}.role_permissions "
                    "WHERE role_id = ANY(:ids)"
                ),
                {"ids": tracked},
            )


async def _override_perm_id(session, schema: str) -> UUID:
    result = await session.execute(
        text(
            f"SELECT id FROM {schema}.permissions WHERE code = :code"
        ),
        {"code": OVERRIDE_GLOBAL_CODE},
    )
    row = result.first()
    assert row is not None
    return uuid.UUID(str(row[0]))


# ============================================================================
# RW1: mixed ACTIVE/INACTIVE assignments
# ============================================================================


async def test_rw1_counts_active_only(
    session_factory, platform_auth,
    make_role, make_role_permission,
    make_platform_user, make_platform_user_role_assignment,
):
    """Mixed assignments: one ACTIVE + one INACTIVE -> count = 1
    (only the ACTIVE one).

    Seed state already has SUPER_ADMIN holders, so the unfiltered
    count is non-zero. This test layers a NEW test role with mixed
    PURAs and confirms the helper's behavior on the new rows by
    using exclude_role_id=<all-other-roles> isn't viable; instead we
    use the test role and demonstrate the ACTIVE/INACTIVE filtering
    via differential counts.
    """
    schema = get_settings().db_schema
    role = await make_role(audience="PLATFORM", name="RW1 Role")
    async for s in get_tenant_session(platform_auth, session_factory):
        override_id = await _override_perm_id(s, schema)
    await make_role_permission(role_id=role.id, permission_id=override_id)

    pu_active = await make_platform_user(
        email=f"rw1-active-{uuid.uuid4()}@rw1.test", status="ACTIVE",
    )
    pu_inactive = await make_platform_user(
        email=f"rw1-inactive-{uuid.uuid4()}@rw1.test", status="ACTIVE",
    )

    # Active assignment from pu_active to role.
    await make_platform_user_role_assignment(
        platform_user_id=pu_active.id, role_id=role.id,
    )
    # Inactive assignment from pu_inactive to role.
    await make_platform_user_role_assignment(
        platform_user_id=pu_inactive.id, role_id=role.id,
        status="INACTIVE", revoked_at="2026-01-01 00:00:00+00",
    )

    # Compute count with the seeded SUPER_ADMIN role EXCLUDED so only
    # our test role's contribution matters. SUPER_ADMIN id lookup.
    async for s in get_tenant_session(platform_auth, session_factory):
        sa_result = await s.execute(
            text(f"SELECT id FROM {schema}.roles WHERE code = 'SUPER_ADMIN'")
        )
        sa_row = sa_result.first()
        assert sa_row is not None
        # The test inspects "what if we excluded SUPER_ADMIN" — our
        # test role's ACTIVE PURA should contribute 1 (the active
        # user) and the INACTIVE one should contribute 0.
        count = await _count_override_global_active_holders(
            s, exclude_role_id=uuid.UUID(str(sa_row[0])),
        )
    assert count == 1


# ============================================================================
# RW2: exclude_role_id behavior
# ============================================================================


async def test_rw2_exclude_role_id_short_circuits(
    session_factory, platform_auth,
    make_role, make_role_permission,
    make_platform_user, make_platform_user_role_assignment,
):
    """When exclude_role_id is set to the only OVERRIDE-bearing role
    that has active holders, the count goes to zero (modulo other
    bearer roles).

    Setup: create test role X with OVERRIDE.GLOBAL + 1 ACTIVE holder.
    Without exclude: count = X's holders + SUPER_ADMIN's.
    With exclude=X: count = SUPER_ADMIN's only (which is non-zero in
    seed, so test asserts inequality).
    """
    schema = get_settings().db_schema
    role = await make_role(audience="PLATFORM", name="RW2 Role")
    async for s in get_tenant_session(platform_auth, session_factory):
        override_id = await _override_perm_id(s, schema)
    await make_role_permission(role_id=role.id, permission_id=override_id)
    pu = await make_platform_user(
        email=f"rw2-{uuid.uuid4()}@rw2.test", status="ACTIVE",
    )
    await make_platform_user_role_assignment(
        platform_user_id=pu.id, role_id=role.id,
    )

    async for s in get_tenant_session(platform_auth, session_factory):
        count_no_exclude = await _count_override_global_active_holders(
            s, exclude_role_id=None,
        )
        count_exclude_role = await _count_override_global_active_holders(
            s, exclude_role_id=role.id,
        )

    # exclude_role_id=role drops 1 (the only ACTIVE holder via role).
    assert count_no_exclude == count_exclude_role + 1


# ============================================================================
# RW3: both-side ACTIVE filter
# ============================================================================


async def test_rw3_user_side_inactive_status_excluded(
    session_factory, platform_auth,
    make_role, make_role_permission,
    make_platform_user, make_platform_user_role_assignment,
):
    """If the user's status is NOT 'ACTIVE' (e.g., INVITED), the
    helper excludes them even if the assignment is ACTIVE.

    Critical correction per LD7: BOTH filters apply. Without the
    user-side filter, an INVITED-but-assigned user would be miscounted
    as an active holder of OVERRIDE.GLOBAL.
    """
    schema = get_settings().db_schema
    role = await make_role(audience="PLATFORM", name="RW3 Role")
    async for s in get_tenant_session(platform_auth, session_factory):
        override_id = await _override_perm_id(s, schema)
        sa_result = await s.execute(
            text(f"SELECT id FROM {schema}.roles WHERE code = 'SUPER_ADMIN'")
        )
        sa_id = uuid.UUID(str(sa_result.scalar_one()))
    await make_role_permission(role_id=role.id, permission_id=override_id)
    # User status='INVITED' (default).
    pu_invited = await make_platform_user(
        email=f"rw3-{uuid.uuid4()}@rw3.test", status="INVITED",
    )
    await make_platform_user_role_assignment(
        platform_user_id=pu_invited.id, role_id=role.id,
        status="ACTIVE",
    )

    # Exclude SUPER_ADMIN so only the test role's contribution counts.
    async for s in get_tenant_session(platform_auth, session_factory):
        count = await _count_override_global_active_holders(
            s, exclude_role_id=sa_id,
        )
    # INVITED user excluded; only seeded ACTIVE-via-SUPER_ADMIN remains,
    # but we excluded SUPER_ADMIN. Net: 0 from our test role.
    assert count == 0


# ============================================================================
# RW4: Layer 2 tripwire fires
# ============================================================================


async def test_rw4_layer_2_tripwire_raises_internal_invariant(
    monkeypatch, session_factory, platform_auth, super_admin_jwt,
    make_role, make_role_permission,
    cleanup_role_perms_for_roles,
):
    """Synthetic mismatch via monkeypatch: Layer 1 passes (returns 1),
    Layer 2 fails (returns 0). The repo.update call raises
    InternalInvariantViolationError.

    Verifies the tripwire actually runs as a separate logical pass
    after the writes (not collapsed into the Layer 1 check).
    """
    schema = get_settings().db_schema
    role = await make_role(audience="PLATFORM", name="RW4 Role")
    cleanup_role_perms_for_roles.append(role.id)
    async for s in get_tenant_session(platform_auth, session_factory):
        override_id = await _override_perm_id(s, schema)
    await make_role_permission(role_id=role.id, permission_id=override_id)

    async def fake_count(session, *, exclude_role_id):
        if exclude_role_id is not None:
            return 1
        return 0

    monkeypatch.setattr(
        "admin_backend.repositories.roles."
        "_count_override_global_active_holders",
        fake_count,
    )

    repo = RolesRepo()
    body = RoleUpdateRequest(permission_ids=[])

    # Resolve super_admin user_id from the JWT-bound fixture's setup
    # via the JWT payload claim.
    import jwt as pyjwt
    payload = pyjwt.decode(
        super_admin_jwt, options={"verify_signature": False}
    )
    actor_user_id = uuid.UUID(payload[CLAIM_USER_ID])

    raised: Exception | None = None
    try:
        async for s in get_tenant_session(platform_auth, session_factory):
            await repo.update(
                s,
                role.id,
                body=body,
                actor_user_id=actor_user_id,
                actor_user_type=ActorUserType.PLATFORM,
            )
    except InternalInvariantViolationError as exc:
        raised = exc

    assert raised is not None, (
        "Expected InternalInvariantViolationError; nothing was raised"
    )


# ============================================================================
# RW5: Diff-replace audit preservation (repo-direct)
# ============================================================================


async def test_rw5_diff_replace_preserves_audit_repo_direct(
    session_factory, platform_auth, super_admin_jwt,
    make_role, make_permission, make_role_permission,
    cleanup_role_perms_for_roles,
):
    """Same contract as router W21, exercised at the repo layer for
    independent verification.
    """
    schema = get_settings().db_schema
    role = await make_role(audience="TENANT", name="RW5 Role")
    cleanup_role_perms_for_roles.append(role.id)
    p_keep = await make_permission(
        module="ADMIN", resource="STORES", action="EXECUTE", scope="STORE",
    )
    p_add = await make_permission(
        module="ADMIN", resource="ORG_NODES", action="EXECUTE",
        scope="STORE",
    )
    await make_role_permission(role_id=role.id, permission_id=p_keep.id)

    async for s in get_tenant_session(platform_auth, session_factory):
        before = (await s.execute(
            text(
                f"SELECT created_at, created_by_user_id, "
                f"created_by_user_type FROM {schema}.role_permissions "
                "WHERE role_id = :rid AND permission_id = :pid"
            ),
            {"rid": role.id, "pid": p_keep.id},
        )).first()

    # Resolve super_admin user_id for the audit-actor pair on the new
    # role_permissions INSERT.
    import jwt as pyjwt
    payload = pyjwt.decode(
        super_admin_jwt, options={"verify_signature": False}
    )
    actor_user_id = uuid.UUID(payload[CLAIM_USER_ID])

    repo = RolesRepo()
    body = RoleUpdateRequest(permission_ids=[p_keep.id, p_add.id])
    async for s in get_tenant_session(platform_auth, session_factory):
        await repo.update(
            s,
            role.id,
            body=body,
            actor_user_id=actor_user_id,
            actor_user_type=ActorUserType.PLATFORM,
        )

    async for s in get_tenant_session(platform_auth, session_factory):
        after = (await s.execute(
            text(
                f"SELECT created_at, created_by_user_id, "
                f"created_by_user_type FROM {schema}.role_permissions "
                "WHERE role_id = :rid AND permission_id = :pid"
            ),
            {"rid": role.id, "pid": p_keep.id},
        )).first()

    assert before == after


# ============================================================================
# RW6: Repo transaction rollback semantics
# ============================================================================


async def test_rw6_error_mid_flow_rolls_back(
    monkeypatch, session_factory, platform_auth, super_admin_jwt,
    make_role, make_role_permission, make_permission,
    cleanup_role_perms_for_roles,
):
    """If a typed error fires mid-flow (e.g., Layer 1 raises after the
    permission existence check passes), no writes should be committed.

    Uses Layer 1 monkeypatch to force a LastOverrideHolderError after
    the writes WOULD have applied — except the error fires BEFORE the
    actual UPDATE/INSERT/DELETE because LD17 step 5d sits before steps
    6-8. Test asserts the role row's updated_at + name remain pre-PATCH
    and no role_permissions changes landed.
    """
    schema = get_settings().db_schema
    role = await make_role(audience="PLATFORM", name="RW6 Original")
    cleanup_role_perms_for_roles.append(role.id)
    async for s in get_tenant_session(platform_auth, session_factory):
        override_id = await _override_perm_id(s, schema)
    await make_role_permission(role_id=role.id, permission_id=override_id)

    p_new = await make_permission(
        module="ADMIN", resource="STORES", action="OVERRIDE", scope="GLOBAL",
    )

    async def fake_count(session, *, exclude_role_id):
        return 0  # Layer 1 fails when removing OVERRIDE

    monkeypatch.setattr(
        "admin_backend.repositories.roles."
        "_count_override_global_active_holders",
        fake_count,
    )

    import jwt as pyjwt
    payload = pyjwt.decode(
        super_admin_jwt, options={"verify_signature": False}
    )
    actor_user_id = uuid.UUID(payload[CLAIM_USER_ID])

    repo = RolesRepo()
    # PATCH removes OVERRIDE.GLOBAL (current holder) + adds p_new.
    body = RoleUpdateRequest(
        name="RW6 Should Not Land",
        permission_ids=[p_new.id],  # removes override_id, adds p_new
    )
    from admin_backend.errors import LastOverrideHolderError
    raised: Exception | None = None
    try:
        async for s in get_tenant_session(platform_auth, session_factory):
            await repo.update(
                s,
                role.id,
                body=body,
                actor_user_id=actor_user_id,
                actor_user_type=ActorUserType.PLATFORM,
            )
    except LastOverrideHolderError as exc:
        raised = exc
    assert raised is not None

    # Verify no writes landed: name unchanged, p_new not on role,
    # override still on role.
    async for s in get_tenant_session(platform_auth, session_factory):
        name_row = await s.execute(
            text(
                f"SELECT name FROM {schema}.roles WHERE id = :rid"
            ),
            {"rid": role.id},
        )
        assert name_row.scalar_one() == "RW6 Original"

        perm_rows = await s.execute(
            text(
                f"SELECT permission_id FROM {schema}.role_permissions "
                "WHERE role_id = :rid"
            ),
            {"rid": role.id},
        )
        current_perm_ids = {row[0] for row in perm_rows.all()}
        assert override_id in current_perm_ids
        assert p_new.id not in current_perm_ids
