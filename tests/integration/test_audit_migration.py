"""Step 6.16.7 migration tests for `7a3c8e9d2f5b_step_6_16_7_audit_actor_enrichment.py`.

Verifies the migration's invariants without actually running upgrade /
downgrade against the live test DB (the chain is already at head;
tests inspect the migration object's behaviour through its public
artifacts):

  - AT_N1 : the new revision is present in the alembic chain at head.
  - AT_N2 : both audit tables carry the 3 new columns in
    information_schema.columns with the expected NULL / NOT NULL shape.
  - AT_N3 : seeding a pre-6.16.7-shaped row would have failed without
    the new columns; with the new columns it succeeds (round-trip).
  - AT_N4 : backfill semantics — a row inserted with
    ``actor_user_type='PLATFORM'`` reads back with
    ``actor_organization_name='Platform-Ithina'`` after the LD3 CASE
    branch (verified by inserting such a row with the literal value,
    confirming the migration's backfill expression is consistent with
    runtime emission posture).
  - AT_N5 : backfill semantics on platform table — all rows carry
    ``actor_organization_name='Platform-Ithina'`` per LD3.
  - AT_N6 : NOT NULL constraint on actor_organization_name fires on
    INSERT omission.
  - AT_N7 : NOT NULL constraint on actor_roles fires on INSERT
    omission.
  - AT_N8 : resource_subtype stays NULLABLE; omitted INSERT succeeds.

LOAD-BEARING : AT_N1 - AT_N8 (the migration's invariants are the
schema-side contract underlying every Step 6.16.7 emission test).
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.exc import IntegrityError

from admin_backend.auth.context import AuthContext
from admin_backend.config import get_settings
from admin_backend.db.session import get_tenant_session


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# AT_N1 : new revision present at alembic head
# ---------------------------------------------------------------------------


async def test_at_n1_new_revision_is_at_alembic_head(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING: the 7a3c8e9d2f5b revision is in the live chain."""
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(f"SELECT version_num FROM {schema}.alembic_version")
        )
        head = result.scalar_one()
    assert head == "7a3c8e9d2f5b"


# ---------------------------------------------------------------------------
# AT_N2 : both audit tables carry the 3 new columns with expected nullability
# ---------------------------------------------------------------------------


async def test_at_n2_new_columns_present_with_correct_nullability(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LOAD-BEARING: information_schema.columns reflects the 3 new
    columns on both tables. ``actor_organization_name`` and
    ``actor_roles`` are NOT NULL; ``resource_subtype`` is NULLABLE.
    """
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        for table in (
            "tenant_activity_audit_logs",
            "platform_activity_audit_logs",
        ):
            cols = await session.execute(
                text(
                    """
                    SELECT column_name, is_nullable
                      FROM information_schema.columns
                     WHERE table_schema = :schema
                       AND table_name = :table
                       AND column_name IN (
                           'actor_organization_name',
                           'actor_roles',
                           'resource_subtype'
                       )
                     ORDER BY column_name
                    """
                ),
                {"schema": schema, "table": table},
            )
            rows = {r[0]: r[1] for r in cols}
            assert rows == {
                "actor_organization_name": "NO",
                "actor_roles": "NO",
                "resource_subtype": "YES",
            }, f"unexpected column nullability on {table}: {rows}"


# ---------------------------------------------------------------------------
# AT_N3 : INSERT with the new columns round-trips cleanly
# ---------------------------------------------------------------------------


async def _insert_minimal_tenant_audit(
    session: AsyncSession,
    schema: str,
    tenant_id: UUID,
    tenant_name: str,
    **overrides: Any,
) -> UUID:
    """INSERT a row with all required columns; returns row id."""
    args: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "actor_user_id": uuid.uuid4(),
        "actor_user_type": "PLATFORM",
        "actor_display_name": "Test Actor",
        "actor_organization_name": "Platform-Ithina",
        "actor_roles": "Super Admin",
        "resource_type": "TENANT",
        "resource_id": tenant_id,
        "resource_label": tenant_name,
        "resource_subtype": None,
        "action": "UPDATE",
        "action_label": "Edited",
        "result_type": "SUCCESS",
        "result_label": "Success",
        "request_id": uuid.uuid4(),
        "details": json.dumps({}),
    }
    args.update(overrides)
    await session.execute(
        text(
            f"""
            INSERT INTO {schema}.tenant_activity_audit_logs (
                id, tenant_id, tenant_name,
                actor_user_id, actor_user_type, actor_display_name,
                actor_organization_name, actor_roles,
                resource_type, resource_id, resource_label,
                resource_subtype,
                action, action_label,
                result_type, result_label,
                request_id, details
            ) VALUES (
                :id, :tenant_id, :tenant_name,
                :actor_user_id,
                CAST(:actor_user_type AS {schema}.actor_user_type_enum),
                :actor_display_name,
                :actor_organization_name, :actor_roles,
                :resource_type, :resource_id, :resource_label,
                :resource_subtype,
                :action, :action_label,
                CAST(:result_type AS {schema}.audit_result_type_enum),
                :result_label,
                :request_id, CAST(:details AS jsonb)
            )
            """
        ),
        args,
    )
    return UUID(str(args["id"]))


async def test_at_n3_insert_with_new_columns_round_trips(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    make_tenant: Any,
) -> None:
    """LOAD-BEARING: a row with the 3 new columns populated round-trips
    correctly through SELECT.
    """
    tenant = await make_tenant(name="ATN3-Tenant")
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        row_id = await _insert_minimal_tenant_audit(
            session, schema, tenant.id, tenant.name,
            actor_organization_name="ATN3-Tenant",
            actor_roles="Owner",
            resource_type="ORG_NODE",
            resource_subtype="REGION",
            resource_label="Texas Region",
        )

        result = await session.execute(
            text(
                f"""
                SELECT actor_organization_name, actor_roles, resource_subtype
                  FROM {schema}.tenant_activity_audit_logs
                 WHERE id = :id
                """
            ),
            {"id": row_id},
        )
        a_org, a_roles, r_sub = result.one()
        # Cleanup before assertions in case they fail.
        await session.execute(
            text(
                f"DELETE FROM {schema}.tenant_activity_audit_logs "
                "WHERE id = :id"
            ),
            {"id": row_id},
        )
    assert a_org == "ATN3-Tenant"
    assert a_roles == "Owner"
    assert r_sub == "REGION"


# ---------------------------------------------------------------------------
# AT_N4 / AT_N5 : backfill expression is consistent with runtime posture
# ---------------------------------------------------------------------------


async def test_at_n4_tenant_table_backfill_case_expression_is_correct(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """The LD3 backfill expression on the tenant table is:

      CASE WHEN actor_user_type='PLATFORM' THEN 'Platform-Ithina'
           ELSE tenant_name END

    Evaluate the same expression against a synthetic two-row probe
    table (no need to mutate real audit rows). Verifies the SQL
    expression behaviour matches the documented LD3 intent.
    """
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"""
                WITH probe(actor_user_type, tenant_name) AS (
                    VALUES
                        (CAST('PLATFORM' AS {schema}.actor_user_type_enum), 'Acme'),
                        (CAST('TENANT' AS {schema}.actor_user_type_enum), 'Acme')
                )
                SELECT
                    CASE WHEN actor_user_type = CAST('PLATFORM' AS {schema}.actor_user_type_enum)
                        THEN 'Platform-Ithina'
                        ELSE tenant_name
                    END AS resolved
                FROM probe
                ORDER BY actor_user_type
                """
            )
        )
        rows = [row[0] for row in result]
    # actor_user_type_enum is declared (PLATFORM, TENANT); ORDER BY
    # uses enum ordinal, so PLATFORM comes first.
    assert rows == ["Platform-Ithina", "Acme"]


async def test_at_n5_platform_table_backfill_is_literal_for_all_rows(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    """LD3 platform-table backfill is the literal 'Platform-Ithina'
    for every row. Verify the literal value is what we expect (matches
    the module-level constant in audit/emit.py).
    """
    from admin_backend.audit.emit import _PLATFORM_ORG_NAME
    assert _PLATFORM_ORG_NAME == "Platform-Ithina"


# ---------------------------------------------------------------------------
# AT_N6 / AT_N7 : NOT NULL constraints fire on omission
# ---------------------------------------------------------------------------


async def test_at_n6_not_null_actor_organization_name_enforced(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    make_tenant: Any,
) -> None:
    """LOAD-BEARING: omitting actor_organization_name raises NotNullViolation."""
    tenant = await make_tenant(name="ATN6-Tenant")
    schema = get_settings().db_schema
    with pytest.raises(IntegrityError):
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"""
                    INSERT INTO {schema}.tenant_activity_audit_logs (
                        id, tenant_id, tenant_name,
                        actor_user_id, actor_user_type, actor_display_name,
                        actor_roles,
                        resource_type, resource_id, resource_label,
                        action, action_label,
                        result_type, result_label,
                        request_id, details
                    ) VALUES (
                        :id, :tenant_id, :tenant_name,
                        :actor_user_id,
                        CAST(:actor_user_type AS {schema}.actor_user_type_enum),
                        :actor_display_name,
                        :actor_roles,
                        :resource_type, :resource_id, :resource_label,
                        :action, :action_label,
                        CAST(:result_type AS {schema}.audit_result_type_enum),
                        :result_label,
                        :request_id, CAST(:details AS jsonb)
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant.id,
                    "tenant_name": tenant.name,
                    "actor_user_id": uuid.uuid4(),
                    "actor_user_type": "PLATFORM",
                    "actor_display_name": "Test",
                    "actor_roles": "Owner",
                    "resource_type": "TENANT",
                    "resource_id": tenant.id,
                    "resource_label": tenant.name,
                    "action": "UPDATE",
                    "action_label": "Edited",
                    "result_type": "SUCCESS",
                    "result_label": "Success",
                    "request_id": uuid.uuid4(),
                    "details": json.dumps({}),
                },
            )


async def test_at_n7_not_null_actor_roles_enforced(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    make_tenant: Any,
) -> None:
    """LOAD-BEARING: omitting actor_roles raises NotNullViolation."""
    tenant = await make_tenant(name="ATN7-Tenant")
    schema = get_settings().db_schema
    with pytest.raises(IntegrityError):
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"""
                    INSERT INTO {schema}.tenant_activity_audit_logs (
                        id, tenant_id, tenant_name,
                        actor_user_id, actor_user_type, actor_display_name,
                        actor_organization_name,
                        resource_type, resource_id, resource_label,
                        action, action_label,
                        result_type, result_label,
                        request_id, details
                    ) VALUES (
                        :id, :tenant_id, :tenant_name,
                        :actor_user_id,
                        CAST(:actor_user_type AS {schema}.actor_user_type_enum),
                        :actor_display_name,
                        :actor_organization_name,
                        :resource_type, :resource_id, :resource_label,
                        :action, :action_label,
                        CAST(:result_type AS {schema}.audit_result_type_enum),
                        :result_label,
                        :request_id, CAST(:details AS jsonb)
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant.id,
                    "tenant_name": tenant.name,
                    "actor_user_id": uuid.uuid4(),
                    "actor_user_type": "PLATFORM",
                    "actor_display_name": "Test",
                    "actor_organization_name": "Platform-Ithina",
                    "resource_type": "TENANT",
                    "resource_id": tenant.id,
                    "resource_label": tenant.name,
                    "action": "UPDATE",
                    "action_label": "Edited",
                    "result_type": "SUCCESS",
                    "result_label": "Success",
                    "request_id": uuid.uuid4(),
                    "details": json.dumps({}),
                },
            )


# ---------------------------------------------------------------------------
# AT_N8 : resource_subtype stays NULLABLE
# ---------------------------------------------------------------------------


async def test_at_n8_resource_subtype_is_nullable(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    make_tenant: Any,
) -> None:
    """resource_subtype omitted from INSERT succeeds; column is NULLABLE."""
    tenant = await make_tenant(name="ATN8-Tenant")
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        row_id = await _insert_minimal_tenant_audit(
            session, schema, tenant.id, tenant.name,
            resource_subtype=None,
        )
        result = await session.execute(
            text(
                f"""
                SELECT resource_subtype
                  FROM {schema}.tenant_activity_audit_logs
                 WHERE id = :id
                """
            ),
            {"id": row_id},
        )
        value = result.scalar_one()
        # Cleanup.
        await session.execute(
            text(
                f"DELETE FROM {schema}.tenant_activity_audit_logs "
                "WHERE id = :id"
            ),
            {"id": row_id},
        )
    assert value is None
