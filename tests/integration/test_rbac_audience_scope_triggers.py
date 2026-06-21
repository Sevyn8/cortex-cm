"""Step 6.20.3 : DB-direct tests for the three RBAC structural triggers.

17 tests covering three triggers added by migration
``5e22b2ca13cc_step_6_20_3_rbac_structural_triggers.py``:

1. ``tg_role_permissions_audience_scope_coherence`` (BEFORE INSERT
   OR UPDATE OF role_id, permission_id ON role_permissions).
   Rejects (TENANT-audience role x GLOBAL-scope permission).

2. ``tg_role_permissions_protect_super_admin_override`` (BEFORE DELETE
   ON role_permissions). Pins the (SUPER_ADMIN x OVERRIDE.GLOBAL) row.

3. ``tg_roles_protect_super_admin`` (BEFORE UPDATE OR DELETE ON roles).
   Pins SUPER_ADMIN row: status, code, audience immutable; DELETE
   blocked. Name and description remain editable.

12 LOAD-BEARING: T1, T2, T4, T5, T6, T8, T9, T11, T12, T13, T14, T16.

Implementation notes
--------------------

* Trigger error class. plpgsql ``RAISE EXCEPTION`` with default SQLSTATE
  P0001 wraps as ``sqlalchemy.exc.ProgrammingError`` (verified
  empirically against local Postgres 15 via psycopg3). The original
  prompt called this ``IntegrityError`` (the conventional SQLAlchemy
  class for SQLSTATE 23xxx integrity violations); precedent at
  ``test_role_assignments_router.py:468`` uses ``IntegrityError`` for a
  real FK violation. Mirroring the actual SQLAlchemy wrap, the tests
  here use ``ProgrammingError``. Surfaced in step doc retro.

* Failure-mode tests use ``async for session in get_tenant_session``
  inside ``with pytest.raises(ProgrammingError)``; the exception
  bubbles out of the generator, which rolls back automatically.

* Success-mode tests that mutate seed rows (T9 delete, T14/T15 name
  update, T17 status update on a factory role) use a raw
  ``session_factory()`` with explicit ``await session.rollback()`` in a
  ``try/finally`` so the mutation never persists.

* Success-mode INSERTs use the ``make_role_permission`` factory: the
  factory tracks the (role_id, permission_id) tuple and DELETEs at
  teardown; if the trigger were to reject, the factory call would
  raise.
"""
from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext
from admin_backend.config import Settings, get_settings
from admin_backend.db.session import get_tenant_session


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Local helpers.
# ---------------------------------------------------------------------------


async def _lookup_role_id(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    code: str,
) -> UUID:
    schema = settings.db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(f"SELECT id FROM {schema}.roles WHERE code = :c"),
            {"c": code},
        )
        return UUID(str(result.scalar_one()))
    raise AssertionError("unreachable")  # pragma: no cover


async def _lookup_permission_id(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    code: str,
) -> UUID:
    schema = settings.db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(f"SELECT id FROM {schema}.permissions WHERE code = :c"),
            {"c": code},
        )
        return UUID(str(result.scalar_one()))
    raise AssertionError("unreachable")  # pragma: no cover


# ===========================================================================
# Trigger 1: tg_role_permissions_audience_scope_coherence
# (TENANT-audience role cannot hold GLOBAL-scope permission).
# ===========================================================================


async def test_t1_insert_tenant_role_global_perm_rejected(
    make_role: Any,
    make_permission: Any,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING : INSERT of (TENANT role x GLOBAL perm) raises.

    Covers the seed-loader bypass path: if Excel ever shipped a
    TENANT-audience role with a GLOBAL-scope permission grant, the
    loader's bulk INSERT would have silently succeeded pre-trigger.
    """
    schema = settings.db_schema
    role = await make_role(audience="TENANT", name="T1-Role")
    # AUDIT.GLOBAL combinations are not present in the seed catalogue;
    # avoids uq_permissions_code collisions with seed rows.
    perm = await make_permission(
        module="ADMIN", resource="TENANTS", action="AUDIT", scope="GLOBAL"
    )
    with pytest.raises(ProgrammingError) as exc_info:
        async for session in get_tenant_session(platform_auth, session_factory):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.role_permissions "
                    "(role_id, permission_id) VALUES (:r, :p)"
                ),
                {"r": role.id, "p": perm.id},
            )
    assert "audience-scope-check" in str(exc_info.value)


async def test_t2_insert_tenant_role_tenant_perm_succeeds(
    make_role: Any,
    make_permission: Any,
    make_role_permission: Any,
) -> None:
    """LOAD-BEARING : Trigger 1 does NOT fire on valid TENANT x TENANT."""
    role = await make_role(audience="TENANT", name="T2-Role")
    perm = await make_permission(
        module="ADMIN", resource="USERS", action="AUDIT", scope="TENANT"
    )
    # Would raise via factory if trigger rejected.
    await make_role_permission(role_id=role.id, permission_id=perm.id)


async def test_t3_insert_tenant_role_store_perm_succeeds(
    make_role: Any,
    make_permission: Any,
    make_role_permission: Any,
) -> None:
    """Trigger 1 does NOT fire on TENANT x STORE."""
    role = await make_role(audience="TENANT", name="T3-Role")
    perm = await make_permission(
        module="ADMIN", resource="STORES", action="AUDIT", scope="STORE"
    )
    await make_role_permission(role_id=role.id, permission_id=perm.id)


async def test_t4_insert_platform_role_global_perm_succeeds(
    make_role: Any,
    make_permission: Any,
    make_role_permission: Any,
) -> None:
    """LOAD-BEARING : Trigger 1 does NOT fire on PLATFORM x GLOBAL.

    PLATFORM-audience roles are the entire reason GLOBAL-scope
    permissions exist; the trigger's audience-scope ban must NOT touch
    this combination.
    """
    role = await make_role(audience="PLATFORM", name="T4-Role")
    perm = await make_permission(
        module="ADMIN", resource="USERS", action="AUDIT", scope="GLOBAL"
    )
    await make_role_permission(role_id=role.id, permission_id=perm.id)


async def test_t5_update_role_id_to_tenant_with_global_perm_rejected(
    make_role: Any,
    make_permission: Any,
    make_role_permission: Any,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING : UPDATE OF role_id fires Trigger 1.

    Start from valid (PLATFORM x GLOBAL); attempt UPDATE to point at a
    TENANT role. Trigger fires because role_id changed.
    """
    schema = settings.db_schema
    platform_role = await make_role(audience="PLATFORM", name="T5-PR")
    tenant_role = await make_role(audience="TENANT", name="T5-TR")
    global_perm = await make_permission(
        module="ADMIN", resource="ROLES", action="AUDIT", scope="GLOBAL"
    )
    await make_role_permission(
        role_id=platform_role.id, permission_id=global_perm.id
    )
    with pytest.raises(ProgrammingError) as exc_info:
        async for session in get_tenant_session(platform_auth, session_factory):
            await session.execute(
                text(
                    f"UPDATE {schema}.role_permissions "
                    "SET role_id = :new_r "
                    "WHERE role_id = :old_r AND permission_id = :p"
                ),
                {
                    "new_r": tenant_role.id,
                    "old_r": platform_role.id,
                    "p": global_perm.id,
                },
            )
    assert "audience-scope-check" in str(exc_info.value)


async def test_t6_update_permission_id_to_global_under_tenant_role_rejected(
    make_role: Any,
    make_permission: Any,
    make_role_permission: Any,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING : UPDATE OF permission_id fires Trigger 1.

    Start from valid (TENANT x TENANT); attempt UPDATE to point at a
    GLOBAL-scope permission. Trigger fires because permission_id
    changed and the new shape is forbidden.
    """
    schema = settings.db_schema
    tenant_role = await make_role(audience="TENANT", name="T6-Role")
    tenant_perm = await make_permission(
        module="ADMIN", resource="USERS", action="AUDIT", scope="TENANT"
    )
    global_perm = await make_permission(
        module="ADMIN", resource="ROLES", action="AUDIT", scope="GLOBAL"
    )
    await make_role_permission(
        role_id=tenant_role.id, permission_id=tenant_perm.id
    )
    with pytest.raises(ProgrammingError) as exc_info:
        async for session in get_tenant_session(platform_auth, session_factory):
            await session.execute(
                text(
                    f"UPDATE {schema}.role_permissions "
                    "SET permission_id = :new_p "
                    "WHERE role_id = :r AND permission_id = :old_p"
                ),
                {
                    "new_p": global_perm.id,
                    "r": tenant_role.id,
                    "old_p": tenant_perm.id,
                },
            )
    assert "audience-scope-check" in str(exc_info.value)


async def test_t7_update_audit_column_only_no_trigger_fire(
    make_role: Any,
    make_permission: Any,
    make_role_permission: Any,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """UPDATE of created_by_user_id / created_by_user_type does NOT fire
    Trigger 1.

    Trigger declaration is ``UPDATE OF role_id, permission_id`` so an
    UPDATE that only touches audit columns must not fire. Use a row
    that, hypothetically, would fail the trigger's predicate if it ran
    against the post-image (TENANT role x GLOBAL perm doesn't exist
    here; instead we use a clean PLATFORM x TENANT shape and verify
    the audit-column UPDATE succeeds).
    """
    schema = settings.db_schema
    role = await make_role(audience="PLATFORM", name="T7-Role")
    perm = await make_permission(
        module="ADMIN", resource="USERS", action="AUDIT", scope="TENANT"
    )
    await make_role_permission(role_id=role.id, permission_id=perm.id)
    actor_id = uuid.uuid4()
    async for session in get_tenant_session(platform_auth, session_factory):
        await session.execute(
            text(
                f"UPDATE {schema}.role_permissions SET "
                "  created_by_user_id = :u, "
                f"  created_by_user_type = CAST('PLATFORM' AS {schema}.actor_user_type_enum) "
                "WHERE role_id = :r AND permission_id = :p"
            ),
            {"u": actor_id, "r": role.id, "p": perm.id},
        )


# ===========================================================================
# Trigger 2: tg_role_permissions_protect_super_admin_override
# (SUPER_ADMIN x ADMIN.ROLES.OVERRIDE.GLOBAL grant cannot be deleted).
# ===========================================================================


async def test_t8_delete_super_admin_override_global_rejected(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING : DELETE of (SUPER_ADMIN x OVERRIDE.GLOBAL) raises.

    Platform-bootstrap protection: this grant is the OVERRIDE.GLOBAL
    last-holder when no other role holds the permission. Step 6.18.3
    LD6/LD8 enforces this app-side; the trigger backstops direct-SQL.
    """
    schema = settings.db_schema
    with pytest.raises(ProgrammingError) as exc_info:
        async for session in get_tenant_session(platform_auth, session_factory):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.role_permissions "
                    "WHERE role_id = "
                    f"  (SELECT id FROM {schema}.roles WHERE code = 'SUPER_ADMIN') "
                    "AND permission_id = "
                    f"  (SELECT id FROM {schema}.permissions WHERE code = 'ADMIN.ROLES.OVERRIDE.GLOBAL')"
                )
            )
    assert "bootstrap-protection" in str(exc_info.value)


async def test_t9_delete_other_super_admin_grant_succeeds(
    make_permission: Any,
    make_role_permission: Any,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING : Trigger 2 does NOT fire on other SUPER_ADMIN
    grants.

    Insert a sentinel (SUPER_ADMIN x other non-OVERRIDE permission)
    via the factory; DELETE the grant in the test body. Trigger should
    not fire because permission_id is not OVERRIDE.GLOBAL.
    """
    schema = settings.db_schema
    super_admin_id = await _lookup_role_id(
        settings, session_factory, platform_auth, "SUPER_ADMIN"
    )
    sentinel = await make_permission(
        module="ADMIN", resource="USERS", action="EXECUTE", scope="STORE"
    )
    await make_role_permission(
        role_id=super_admin_id, permission_id=sentinel.id
    )
    # DELETE in a new session; trigger should be silent.
    async for session in get_tenant_session(platform_auth, session_factory):
        await session.execute(
            text(
                f"DELETE FROM {schema}.role_permissions "
                "WHERE role_id = :r AND permission_id = :p"
            ),
            {"r": super_admin_id, "p": sentinel.id},
        )


async def test_t10_delete_non_super_admin_override_global_succeeds(
    make_role: Any,
    make_role_permission: Any,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """Trigger 2 does NOT fire on non-SUPER_ADMIN OVERRIDE.GLOBAL grants.

    Create a fresh PLATFORM role; grant it OVERRIDE.GLOBAL; DELETE the
    grant. Trigger should not fire because role_id is not SUPER_ADMIN.
    """
    schema = settings.db_schema
    override_global_id = await _lookup_permission_id(
        settings, session_factory, platform_auth, "ADMIN.ROLES.OVERRIDE.GLOBAL"
    )
    role = await make_role(audience="PLATFORM", name="T10-Role")
    await make_role_permission(
        role_id=role.id, permission_id=override_global_id
    )
    async for session in get_tenant_session(platform_auth, session_factory):
        await session.execute(
            text(
                f"DELETE FROM {schema}.role_permissions "
                "WHERE role_id = :r AND permission_id = :p"
            ),
            {"r": role.id, "p": override_global_id},
        )


# ===========================================================================
# Trigger 3: tg_roles_protect_super_admin
# (SUPER_ADMIN role status/code/audience immutable; DELETE blocked).
# ===========================================================================


async def _update_super_admin_field_expecting_error(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    set_clause: str,
    params: dict[str, Any],
) -> ProgrammingError:
    """Attempt an UPDATE on SUPER_ADMIN; assert ProgrammingError raised."""
    schema = settings.db_schema
    with pytest.raises(ProgrammingError) as exc_info:
        async for session in get_tenant_session(platform_auth, session_factory):
            await session.execute(
                text(
                    f"UPDATE {schema}.roles SET {set_clause} "
                    "WHERE code = 'SUPER_ADMIN'"
                ),
                params,
            )
    return exc_info.value


async def test_t11_update_super_admin_status_rejected(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING : UPDATE SUPER_ADMIN SET status raises."""
    schema = settings.db_schema
    err = await _update_super_admin_field_expecting_error(
        settings,
        session_factory,
        platform_auth,
        f"status = CAST(:s AS {schema}.role_status_enum)",
        {"s": "INACTIVE"},
    )
    assert "bootstrap-protection" in str(err)


async def test_t12_update_super_admin_code_rejected(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING : UPDATE SUPER_ADMIN SET code raises."""
    err = await _update_super_admin_field_expecting_error(
        settings,
        session_factory,
        platform_auth,
        "code = :c",
        {"c": "SUPER_ADMIN_RENAMED"},
    )
    assert "bootstrap-protection" in str(err)


async def test_t13_update_super_admin_audience_rejected(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING : UPDATE SUPER_ADMIN SET audience raises."""
    schema = settings.db_schema
    err = await _update_super_admin_field_expecting_error(
        settings,
        session_factory,
        platform_auth,
        f"audience = CAST(:a AS {schema}.role_audience_enum)",
        {"a": "TENANT"},
    )
    assert "bootstrap-protection" in str(err)


async def test_t14_update_super_admin_name_succeeds(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """LOAD-BEARING : UPDATE SUPER_ADMIN SET name does NOT raise.

    LD3 explicitly preserves name and description editability for
    branding flexibility. UPDATE succeeds; explicit rollback prevents
    persistence so the seed row keeps its original name.
    """
    schema = settings.db_schema
    session = session_factory()
    try:
        await session.execute(
            text(
                f"UPDATE {schema}.roles SET name = :new_name "
                "WHERE code = 'SUPER_ADMIN'"
            ),
            {"new_name": "T14 Renamed"},
        )
        readback = (
            await session.execute(
                text(f"SELECT name FROM {schema}.roles WHERE code = 'SUPER_ADMIN'")
            )
        ).scalar_one()
        assert readback == "T14 Renamed"
    finally:
        await session.rollback()
        await session.close()


async def test_t15_update_super_admin_description_succeeds(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """UPDATE SUPER_ADMIN SET description does NOT raise."""
    schema = settings.db_schema
    session = session_factory()
    try:
        await session.execute(
            text(
                f"UPDATE {schema}.roles SET description = :d "
                "WHERE code = 'SUPER_ADMIN'"
            ),
            {"d": "T15 description"},
        )
    finally:
        await session.rollback()
        await session.close()


async def test_t16_delete_super_admin_rejected(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING : DELETE SUPER_ADMIN role raises."""
    schema = settings.db_schema
    with pytest.raises(ProgrammingError) as exc_info:
        async for session in get_tenant_session(platform_auth, session_factory):
            await session.execute(
                text(f"DELETE FROM {schema}.roles WHERE code = 'SUPER_ADMIN'")
            )
    assert "bootstrap-protection" in str(exc_info.value)


async def test_t17_update_non_super_admin_role_status_succeeds(
    make_role: Any,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Trigger 3 does NOT fire on non-SUPER_ADMIN UPDATEs.

    Status update on a factory-created role must succeed. The factory
    cleans up the row at fixture teardown regardless of what the test
    did to it.
    """
    schema = settings.db_schema
    role = await make_role(audience="TENANT", name="T17-Role")
    session = session_factory()
    try:
        await session.execute(
            text(
                f"UPDATE {schema}.roles SET "
                f"status = CAST(:s AS {schema}.role_status_enum) "
                "WHERE id = :id"
            ),
            {"s": "INACTIVE", "id": role.id},
        )
    finally:
        await session.rollback()
        await session.close()
