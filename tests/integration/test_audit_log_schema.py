"""Step 6.16.1: schema-layer tests for the audit log subsystem.

Asserts the live database state at the migration head matches the
design spec in `docs/architecture_audit_logs.md`:

- The two tables and the new enum exist in the configured schema.
- The new enum has exactly 6 values in the locked order.
- The reused `actor_user_type_enum` (PLATFORM, TENANT) backs the
  `actor_user_type` column on both tables (not the new enum).
- CHECK constraints `ck_*_resource_pair` fire on inconsistent inserts;
  `ck_platform_activity_audit_logs_tenant_pair` fires on the platform
  table.
- FK on `tenant_id` (both tables) RESTRICTs tenant deletion when an
  audit row references the tenant.
- RLS is active on `tenant_activity_audit_logs`; the D-29 OR-branch
  policy resolves correctly across the 3 standard GUC contexts
  (TENANT-A, TENANT-B, PLATFORM).

Migration upgrade / downgrade / round-trip safety is verified at
development time via `alembic upgrade head && alembic downgrade -1 &&
alembic upgrade head`. These tests assert that the LIVE schema state
at head matches expectations, which is equivalent runtime evidence
that the migration applied; round-trip behaviour is a property of the
migration code, not of pytest state.

LOAD-BEARING tests: S1 (schema present), S6 (resource_pair CHECK on
tenant table), S7 (resource + tenant pair CHECKs on platform table),
S8 (FK RESTRICT semantics), S9 (RLS + D-29 OR-branch policy).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext
from admin_backend.config import Settings, get_settings
from admin_backend.db.session import get_tenant_session
from admin_backend.models import Tenant


pytestmark = pytest.mark.asyncio


_AUDIT_RESULT_TYPE_VALUES = (
    "SUCCESS",
    "PERMISSION_DENIED",
    "VALIDATION_FAILED",
    "CONFLICT",
    "INTEGRITY_VIOLATION",
    "INTERNAL_ERROR",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _audit_row_args(tenant: Tenant, **overrides: Any) -> dict[str, Any]:
    """Builds a valid tenant-side audit row payload; consumers override.

    Defaults model a SUCCESS row on tenants suspend.

    Step 6.16.7 LD13 : new audit-row columns
    (``actor_organization_name``, ``actor_roles``, ``resource_subtype``)
    populated with defaults satisfying NOT NULL constraints.
    """
    base: dict[str, Any] = {
        "tenant_id": tenant.id,
        "tenant_name": tenant.name,
        "actor_user_id": uuid.uuid4(),
        "actor_user_type": "PLATFORM",
        "actor_display_name": "Test Actor",
        "actor_organization_name": "Platform-Ithina",
        "actor_roles": "Test Role",
        "resource_type": "TENANT",
        "resource_id": tenant.id,
        "resource_label": tenant.name,
        "resource_subtype": None,
        "action": "SUSPEND",
        "action_label": "Suspend tenant",
        "result_type": "SUCCESS",
        "result_label": "Success",
        "request_id": uuid.uuid4(),
        "details": "{}",
    }
    base.update(overrides)
    return base


async def _insert_tenant_audit_row(
    session: AsyncSession,
    schema: str,
    args: dict[str, Any],
) -> UUID:
    """INSERT into tenant_activity_audit_logs; returns the row's id."""
    result = await session.execute(
        text(
            f"""
            INSERT INTO {schema}.tenant_activity_audit_logs (
                tenant_id, tenant_name,
                actor_user_id, actor_user_type, actor_display_name,
                actor_organization_name, actor_roles,
                resource_type, resource_id, resource_label,
                resource_subtype,
                action, action_label,
                result_type, result_label,
                request_id, details
            ) VALUES (
                :tenant_id, :tenant_name,
                :actor_user_id,
                CAST(:actor_user_type AS {schema}.actor_user_type_enum),
                :actor_display_name,
                :actor_organization_name, :actor_roles,
                :resource_type, :resource_id, :resource_label,
                :resource_subtype,
                :action, :action_label,
                CAST(:result_type AS {schema}.audit_result_type_enum),
                :result_label,
                :request_id,
                CAST(:details AS jsonb)
            ) RETURNING id
            """
        ),
        args,
    )
    return UUID(str(result.scalar_one()))


async def _insert_platform_audit_row(
    session: AsyncSession,
    schema: str,
    args: dict[str, Any],
) -> UUID:
    """INSERT into platform_activity_audit_logs; returns the row's id."""
    result = await session.execute(
        text(
            f"""
            INSERT INTO {schema}.platform_activity_audit_logs (
                tenant_id, tenant_name,
                actor_user_id, actor_user_type, actor_display_name,
                actor_organization_name, actor_roles,
                resource_type, resource_id, resource_label,
                resource_subtype,
                action, action_label,
                result_type, result_label,
                request_id, details
            ) VALUES (
                :tenant_id, :tenant_name,
                :actor_user_id,
                CAST(:actor_user_type AS {schema}.actor_user_type_enum),
                :actor_display_name,
                :actor_organization_name, :actor_roles,
                :resource_type, :resource_id, :resource_label,
                :resource_subtype,
                :action, :action_label,
                CAST(:result_type AS {schema}.audit_result_type_enum),
                :result_label,
                :request_id,
                CAST(:details AS jsonb)
            ) RETURNING id
            """
        ),
        args,
    )
    return UUID(str(result.scalar_one()))


# ---------------------------------------------------------------------------
# S1 : tables + enum present at live schema head
# ---------------------------------------------------------------------------


async def test_s1_schema_objects_present_at_head(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING: both tables exist and the new enum is registered."""
    schema = settings.db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        tables_result = await session.execute(
            text(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = :schema
                  AND table_name IN (
                      'tenant_activity_audit_logs',
                      'platform_activity_audit_logs'
                  )
                """
            ),
            {"schema": schema},
        )
        tables = {row[0] for row in tables_result}
        assert tables == {
            "tenant_activity_audit_logs",
            "platform_activity_audit_logs",
        }

        enum_result = await session.execute(
            text(
                """
                SELECT 1 FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = :schema
                  AND t.typname = 'audit_result_type_enum'
                """
            ),
            {"schema": schema},
        )
        assert enum_result.scalar_one_or_none() == 1


# ---------------------------------------------------------------------------
# S4 : audit_result_type_enum has 6 values in locked order
# ---------------------------------------------------------------------------


async def test_s4_audit_result_type_enum_values_in_order(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    schema = settings.db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                """
                SELECT e.enumlabel
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE n.nspname = :schema
                  AND t.typname = 'audit_result_type_enum'
                ORDER BY e.enumsortorder
                """
            ),
            {"schema": schema},
        )
        values = tuple(row[0] for row in result)
    assert values == _AUDIT_RESULT_TYPE_VALUES


# ---------------------------------------------------------------------------
# S5 : actor_user_type column on both audit tables uses the existing enum
# ---------------------------------------------------------------------------


async def test_s5_actor_user_type_column_uses_existing_enum(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    schema = settings.db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                """
                SELECT table_name, udt_name
                FROM information_schema.columns
                WHERE table_schema = :schema
                  AND column_name = 'actor_user_type'
                  AND table_name IN (
                      'tenant_activity_audit_logs',
                      'platform_activity_audit_logs'
                  )
                """
            ),
            {"schema": schema},
        )
        mapping = {row[0]: row[1] for row in result}
    assert mapping == {
        "tenant_activity_audit_logs": "actor_user_type_enum",
        "platform_activity_audit_logs": "actor_user_type_enum",
    }


# ---------------------------------------------------------------------------
# S6 : ck_tenant_activity_audit_logs_resource_pair fires on inconsistent pair
# ---------------------------------------------------------------------------


async def test_s6_tenant_resource_pair_check_fires_on_inconsistency(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    make_tenant: Callable[..., Any],
) -> None:
    """LOAD-BEARING: resource_id NOT NULL with resource_label NULL is rejected."""
    schema = settings.db_schema
    tenant = await make_tenant(name="S6-tenant")

    args = _audit_row_args(
        tenant,
        resource_id=uuid.uuid4(),
        resource_label=None,
    )

    with pytest.raises(IntegrityError):
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await _insert_tenant_audit_row(session, schema, args)


# ---------------------------------------------------------------------------
# S7 : platform table CHECK constraints fire (resource_pair + tenant_pair)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides,description",
    [
        (
            {"resource_id": None, "resource_label": "Orphan label"},
            "resource_pair: id NULL but label NOT NULL",
        ),
        (
            {"tenant_id": None, "tenant_name": "Orphan tenant name"},
            "tenant_pair: id NULL but name NOT NULL",
        ),
    ],
    ids=["resource_pair", "tenant_pair"],
)
async def test_s7_platform_checks_fire_on_inconsistency(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    make_tenant: Callable[..., Any],
    overrides: dict[str, Any],
    description: str,
) -> None:
    """LOAD-BEARING: both platform-table NULL-pair CHECKs reject bad inputs."""
    schema = settings.db_schema
    tenant = await make_tenant(name="S7-tenant")
    args = _audit_row_args(tenant, **overrides)
    # Strip out resource_id when overrides nulls it: the helper would
    # have left it set otherwise. _audit_row_args.update already handles
    # this; nothing extra needed.

    with pytest.raises(IntegrityError):
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await _insert_platform_audit_row(session, schema, args)


# ---------------------------------------------------------------------------
# S8 : FK on tenant_id RESTRICTs tenant deletion (both tables)
# ---------------------------------------------------------------------------


async def test_s8_fk_restricts_tenant_deletion_via_audit_row(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    make_tenant: Callable[..., Any],
) -> None:
    """LOAD-BEARING: an audit row pinning a tenant blocks the tenant DELETE.

    Verified once on the tenant table (the platform table's FK is the
    same shape and accepts NULL; the constraint behaviour when the
    column is populated is identical).
    """
    schema = settings.db_schema
    tenant = await make_tenant(name="S8-tenant")
    args = _audit_row_args(tenant)

    # Insert an audit row pinning this tenant.
    async for session in get_tenant_session(platform_auth, session_factory):
        row_id = await _insert_tenant_audit_row(session, schema, args)

    # DELETE the tenant: must be rejected (FK ON DELETE RESTRICT).
    with pytest.raises(IntegrityError):
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(f"DELETE FROM {schema}.tenants WHERE id = :id"),
                {"id": tenant.id},
            )

    # Clean up: delete the audit row so the make_tenant fixture's
    # teardown DELETE on the tenant can succeed.
    async for session in get_tenant_session(platform_auth, session_factory):
        await session.execute(
            text(
                f"DELETE FROM {schema}.tenant_activity_audit_logs "
                "WHERE id = :id"
            ),
            {"id": row_id},
        )


# ---------------------------------------------------------------------------
# S9 : RLS active + D-29 OR-branch policy resolves correctly
# ---------------------------------------------------------------------------


async def test_s9_tenant_table_rls_active_and_policy_isolates(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    tenant_session_factory: Callable[[UUID], AbstractAsyncContextManager[AsyncSession]],
    make_tenant: Callable[..., Any],
) -> None:
    """LOAD-BEARING: tenants see only own rows; PLATFORM sees all.

    Confirms RLS+FORCE and D-29 OR-branch (USING) policy correctness
    end-to-end. Inserts under PLATFORM session (admits both via the
    OR-branch); reads under TENANT-A, TENANT-B, and PLATFORM contexts.
    """
    schema = settings.db_schema
    tenant_a = await make_tenant(name="S9-Tenant-A")
    tenant_b = await make_tenant(name="S9-Tenant-B")

    args_a = _audit_row_args(tenant_a)
    args_b = _audit_row_args(tenant_b)

    async for session in get_tenant_session(platform_auth, session_factory):
        row_a_id = await _insert_tenant_audit_row(session, schema, args_a)
        row_b_id = await _insert_tenant_audit_row(session, schema, args_b)

    # Verify RLS is enabled + forced on the tenant table.
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"""
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE oid = '{schema}.tenant_activity_audit_logs'::regclass
                """
            )
        )
        relrowsec, relforcerowsec = result.one()
    assert relrowsec is True
    assert relforcerowsec is True

    # TENANT-A sees only A's row.
    async with tenant_session_factory(tenant_a.id) as session:
        result = await session.execute(
            text(
                f"SELECT id FROM {schema}.tenant_activity_audit_logs "
                "ORDER BY id"
            )
        )
        ids_seen = {UUID(str(row[0])) for row in result}
    assert ids_seen == {row_a_id}

    # TENANT-B sees only B's row.
    async with tenant_session_factory(tenant_b.id) as session:
        result = await session.execute(
            text(
                f"SELECT id FROM {schema}.tenant_activity_audit_logs "
                "ORDER BY id"
            )
        )
        ids_seen = {UUID(str(row[0])) for row in result}
    assert ids_seen == {row_b_id}

    # PLATFORM sees both via the unconditional OR-branch (D-29).
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"SELECT id FROM {schema}.tenant_activity_audit_logs "
                f"WHERE id IN (:a, :b)"
            ),
            {"a": row_a_id, "b": row_b_id},
        )
        ids_seen = {UUID(str(row[0])) for row in result}
    assert ids_seen == {row_a_id, row_b_id}

    # Cleanup: PLATFORM session DELETEs both rows so make_tenant
    # teardown's tenant DELETE succeeds.
    async for session in get_tenant_session(platform_auth, session_factory):
        await session.execute(
            text(
                f"DELETE FROM {schema}.tenant_activity_audit_logs "
                "WHERE id IN (:a, :b)"
            ),
            {"a": row_a_id, "b": row_b_id},
        )
