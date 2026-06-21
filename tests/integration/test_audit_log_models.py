"""Step 6.16.1: ORM model round-trip tests for audit log tables.

Insert + retrieve smoke tests for `TenantActivityAuditLog` and
`PlatformActivityAuditLog`. The ORM column shape mirrors the DDL
verbatim; these tests exercise the SQLAlchemy mapping end-to-end
(persistence + read-back + enum coercion + JSONB round-trip).

LOAD-BEARING: M4 (all 6 `AuditResultType` values round-trip). The
enum vocabulary is the failure-classification surface that sub-steps
6.16.2-5 rely on; a missing or misnamed value silently breaks
emission downstream.

Inserts go through PLATFORM session (admits both tenant-table and
platform-table writes via the D-29 OR-branch on tenant + no-RLS on
platform).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from admin_backend.auth.context import AuthContext
from admin_backend.db.session import get_tenant_session
from admin_backend.models import (
    ActorUserType,
    AuditResultType,
    PlatformActivityAuditLog,
    Tenant,
    TenantActivityAuditLog,
)


pytestmark = pytest.mark.asyncio


def _tenant_row(tenant: Tenant, **overrides: Any) -> TenantActivityAuditLog:
    """Construct a TenantActivityAuditLog ORM instance with sane defaults.

    Step 6.16.7 LD13 : new audit-row columns
    (``actor_organization_name``, ``actor_roles``, ``resource_subtype``)
    populated with defaults so existing assertions remain valid.
    """
    base: dict[str, Any] = {
        "tenant_id": tenant.id,
        "tenant_name": tenant.name,
        "actor_user_id": uuid.uuid4(),
        "actor_user_type": ActorUserType.PLATFORM,
        "actor_display_name": "Test Actor",
        "actor_organization_name": "Platform-Ithina",
        "actor_roles": "Test Role",
        "resource_type": "TENANT",
        "resource_id": tenant.id,
        "resource_label": tenant.name,
        "resource_subtype": None,
        "action": "SUSPEND",
        "action_label": "Suspend tenant",
        "result_type": AuditResultType.SUCCESS,
        "result_label": "Success",
        "request_id": uuid.uuid4(),
        "details": {"snapshot": {"status": "SUSPENDED"}},
    }
    base.update(overrides)
    return TenantActivityAuditLog(**base)


def _platform_row(
    tenant: Tenant | None = None,
    **overrides: Any,
) -> PlatformActivityAuditLog:
    """Construct a PlatformActivityAuditLog ORM instance with sane defaults."""
    base: dict[str, Any] = {
        "tenant_id": tenant.id if tenant else None,
        "tenant_name": tenant.name if tenant else None,
        "actor_user_id": uuid.uuid4(),
        "actor_user_type": ActorUserType.PLATFORM,
        "actor_display_name": "Anjali",
        "actor_organization_name": "Platform-Ithina",
        "actor_roles": "Test Role",
        "resource_type": "TENANT",
        "resource_id": tenant.id if tenant else None,
        "resource_label": tenant.name if tenant else None,
        "resource_subtype": None,
        "action": "CREATE",
        "action_label": "Create tenant",
        "result_type": AuditResultType.SUCCESS,
        "result_label": "Success",
        "request_id": uuid.uuid4(),
        "details": {"snapshot": {"name": tenant.name if tenant else "n/a"}},
    }
    base.update(overrides)
    return PlatformActivityAuditLog(**base)


async def _delete_tenant_rows(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    row_ids: list[UUID],
) -> None:
    from sqlalchemy import delete
    if not row_ids:
        return
    async for session in get_tenant_session(platform_auth, session_factory):
        await session.execute(
            delete(TenantActivityAuditLog).where(
                TenantActivityAuditLog.id.in_(row_ids)
            )
        )


async def _delete_platform_rows(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    row_ids: list[UUID],
) -> None:
    from sqlalchemy import delete
    if not row_ids:
        return
    async for session in get_tenant_session(platform_auth, session_factory):
        await session.execute(
            delete(PlatformActivityAuditLog).where(
                PlatformActivityAuditLog.id.in_(row_ids)
            )
        )


# ---------------------------------------------------------------------------
# M1 : tenant-table ORM round-trip
# ---------------------------------------------------------------------------


async def test_m1_tenant_row_orm_roundtrip(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    make_tenant: Callable[..., Any],
) -> None:
    tenant = await make_tenant(name="M1-tenant")
    row = _tenant_row(tenant)

    async for session in get_tenant_session(platform_auth, session_factory):
        session.add(row)
        await session.flush()
        await session.refresh(row)
        assert row.id is not None
        assert row.timestamp is not None
        row_id = row.id

    try:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            result = await session.execute(
                select(TenantActivityAuditLog).where(
                    TenantActivityAuditLog.id == row_id
                )
            )
            persisted = result.scalar_one()
        assert persisted.tenant_id == tenant.id
        assert persisted.tenant_name == tenant.name
        assert persisted.actor_user_type == ActorUserType.PLATFORM
        assert persisted.action == "SUSPEND"
        assert persisted.result_type == AuditResultType.SUCCESS
        assert persisted.details == {"snapshot": {"status": "SUSPENDED"}}
    finally:
        await _delete_tenant_rows(session_factory, platform_auth, [row_id])


# ---------------------------------------------------------------------------
# M2 : platform-table ORM round-trip with tenant populated
# ---------------------------------------------------------------------------


async def test_m2_platform_row_with_tenant_populated_roundtrip(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    make_tenant: Callable[..., Any],
) -> None:
    tenant = await make_tenant(name="M2-tenant")
    row = _platform_row(tenant)

    async for session in get_tenant_session(platform_auth, session_factory):
        session.add(row)
        await session.flush()
        await session.refresh(row)
        row_id = row.id

    try:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            result = await session.execute(
                select(PlatformActivityAuditLog).where(
                    PlatformActivityAuditLog.id == row_id
                )
            )
            persisted = result.scalar_one()
        assert persisted.tenant_id == tenant.id
        assert persisted.tenant_name == tenant.name
        assert persisted.action == "CREATE"
        assert persisted.result_type == AuditResultType.SUCCESS
    finally:
        await _delete_platform_rows(session_factory, platform_auth, [row_id])


# ---------------------------------------------------------------------------
# M3 : platform-table ORM round-trip with tenant NULL
# ---------------------------------------------------------------------------


async def test_m3_platform_row_with_tenant_null_roundtrip(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> None:
    row = _platform_row(
        tenant=None,
        action="GRANT",
        action_label="Grant role",
        resource_type="ROLE_ASSIGNMENT",
    )

    async for session in get_tenant_session(platform_auth, session_factory):
        session.add(row)
        await session.flush()
        await session.refresh(row)
        row_id = row.id

    try:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            result = await session.execute(
                select(PlatformActivityAuditLog).where(
                    PlatformActivityAuditLog.id == row_id
                )
            )
            persisted = result.scalar_one()
        assert persisted.tenant_id is None
        assert persisted.tenant_name is None
        assert persisted.action == "GRANT"
        assert persisted.resource_type == "ROLE_ASSIGNMENT"
        assert persisted.resource_id is None
        assert persisted.resource_label is None
    finally:
        await _delete_platform_rows(session_factory, platform_auth, [row_id])


# ---------------------------------------------------------------------------
# M4 : all 6 AuditResultType values round-trip
# ---------------------------------------------------------------------------


async def test_m4_all_result_types_roundtrip(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    make_tenant: Callable[..., Any],
) -> None:
    """LOAD-BEARING: every enum value persists and reads back.

    The failure-classification surface for 6.16.2-5 depends on every
    value being present in the SQL enum AND mapping cleanly through
    SQLAlchemy. A regression that drops or mis-spells a value would
    silently break emission on the affected result path.
    """
    tenant = await make_tenant(name="M4-tenant")
    inserted_ids: list[UUID] = []

    async for session in get_tenant_session(platform_auth, session_factory):
        for rt in AuditResultType:
            # Failed-create rows have resource_id NULL + resource_label NULL
            # for ck_*_resource_pair compliance; SUCCESS rows have both
            # populated. We use the success defaults for every value here
            # and just vary result_type.
            row = _tenant_row(tenant, result_type=rt, result_label=rt.value)
            session.add(row)
        await session.flush()
        for instance in session.new:
            pass  # no-op; new is a set, refresh below catches all
        # Refresh isn't strictly needed since flush populates id; gather ids
        result = await session.execute(
            select(TenantActivityAuditLog.id, TenantActivityAuditLog.result_type)
            .where(TenantActivityAuditLog.tenant_id == tenant.id)
        )
        rows = result.all()
        inserted_ids = [UUID(str(r[0])) for r in rows]
        types_seen = {r[1] for r in rows}

    try:
        assert types_seen == set(AuditResultType)
    finally:
        await _delete_tenant_rows(session_factory, platform_auth, inserted_ids)


# ---------------------------------------------------------------------------
# M5 : JSONB details column round-trip with @> containment query
# ---------------------------------------------------------------------------


async def test_m5_jsonb_details_containment_query(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    make_tenant: Callable[..., Any],
) -> None:
    """JSONB column round-trip via dict-shape value AND @> containment."""
    from sqlalchemy import text
    tenant = await make_tenant(name="M5-tenant")
    payload = {
        "before": {"status": "ACTIVE"},
        "after": {"status": "SUSPENDED"},
        "actor_note": "ops escalation",
    }
    row = _tenant_row(tenant, details=payload)

    async for session in get_tenant_session(platform_auth, session_factory):
        session.add(row)
        await session.flush()
        row_id = row.id

    try:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            schema = (
                await session.execute(text("SELECT current_schema()"))
            ).scalar_one()
            # JSONB containment query: rows whose details JSONB contains
            # {"after": {"status": "SUSPENDED"}}.
            result = await session.execute(
                text(
                    f"""
                    SELECT id FROM {schema}.tenant_activity_audit_logs
                    WHERE id = :id
                      AND details @> CAST(:probe AS jsonb)
                    """
                ),
                {
                    "id": row_id,
                    "probe": '{"after": {"status": "SUSPENDED"}}',
                },
            )
            matched = result.scalar_one_or_none()
        assert matched is not None
        assert UUID(str(matched)) == row_id
    finally:
        await _delete_tenant_rows(session_factory, platform_auth, [row_id])
