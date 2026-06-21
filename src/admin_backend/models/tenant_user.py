"""SQLAlchemy ORM model for the ``tenant_users`` table.

Maps every column of ``db/raw_ddl/Ithina_postgres_SQL_DDL_tenant_users_v1.sql``
in DDL order. The DDL is the source of truth for schema; this module
is the application's typed view onto it.

This step (5.2) replaces the lightweight ``TenantUser`` stub at
``models/_lightweight_stubs.py`` (used since Step 3.3 by
``TenantsRepo``'s correlated subqueries). The stub declared only ``id``,
``tenant_id``, and ``status``; the full model declares all 17 columns.
The Repo's existing subqueries reference exactly those three columns,
so the swap is SQL-equivalent at the call sites.

Notes on shape (mirrors ``models/tenant.py`` and ``models/platform_user.py``):

- Schema qualification (``__table_args__["schema"]``) resolves from
  ``DB_SCHEMA`` env var per D-15.

- ``id``, ``status``, ``created_at``, ``updated_at`` carry
  ``server_default=FetchedValue()``. The DDL owns the actual default
  expression (``uuidv7()`` / ``'INVITED'`` / ``NOW()``); the ORM
  declares only that a DB-side default exists so SQLAlchemy omits
  the column from INSERT statements (without it, SA sends explicit
  NULLs that defeat the DDL DEFAULT and trigger NOT NULL violations).

- Audit-actor columns (Pattern (b) per D-13). Three pairs:
  ``created_by_user_id`` + ``created_by_user_type``,
  ``updated_by_user_id`` + ``updated_by_user_type``,
  ``suspended_by_user_id`` + ``suspended_by_user_type``. The
  ``*_user_id`` is a raw UUID (NO SQLAlchemy ``ForeignKey`` at this
  layer — Pattern (b) deliberately has no DB FK because the actor
  could be in either ``platform_users`` or ``tenant_users``). The
  ``*_user_type`` is the ``actor_user_type_enum`` (PLATFORM/TENANT)
  discriminator. App-layer validation (AI-TU-03 / FN-AB-09) ensures
  the UUID exists in the correct table given the type.

- ``tenant_id`` is NOT NULL with FK to ``tenants.id`` at the DB layer.
  No SQLAlchemy ``relationship("Tenant", ...)`` declared — the
  relationship isn't used by this Repo's queries and adding it would
  introduce import-ordering ceremony for no read-side benefit.

- The four enum columns reference Postgres enum types created by the
  DDL; ``create_type=False`` keeps SQLAlchemy from re-issuing
  ``CREATE TYPE``. Dialect-specific ``postgresql.ENUM`` (not generic
  ``sqlalchemy.Enum``) per the "Note on PG enum columns" convention
  in CLAUDE.md.
"""
from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    DateTime,
    FetchedValue,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import get_settings
from admin_backend.db.base import Base


class TenantUserStatus(str, Enum):
    """Lifecycle states for a tenant user. Mirrors ``tenant_user_status_enum``."""

    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class ActorUserType(str, Enum):
    """Discriminator for Pattern (b) audit-actor columns (per D-13).

    Mirrors the shared ``actor_user_type_enum``. Indicates which user
    table the audit-actor UUID points at: PLATFORM -> ``platform_users``,
    TENANT -> ``tenant_users``. This enum is shared platform-wide; it's
    declared here because ``tenant_users`` is the first model to
    consume it. Step 4.5 (Stores) and Step 5.3 (OrgNodes) will import
    it from this module.
    """

    PLATFORM = "PLATFORM"
    TENANT = "TENANT"


class TenantUser(Base):
    """A customer-side user (Pattern 2 user split per D-02).

    Tenant-scoped: every row has a NOT NULL ``tenant_id`` and is
    visible only to sessions where ``app.tenant_id`` matches (or to
    PLATFORM sessions per the D-29 unconditional OR-branch on
    ``tenant_users_tenant_isolation``).
    """

    __tablename__ = "tenant_users"
    __table_args__ = {"schema": get_settings().db_schema}

    # ---------- Surrogate primary key ----------
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=FetchedValue()
    )

    # ---------- Ownership ----------
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)

    # ---------- External identity ----------
    auth0_sub: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------- Identity ----------
    email: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------- Lifecycle ----------
    status: Mapped[TenantUserStatus] = mapped_column(
        PG_ENUM(
            TenantUserStatus,
            name="tenant_user_status_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=FetchedValue(),
    )

    # ---------- Invitation ----------
    invited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    invitation_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ---------- Suspension (Pattern b) ----------
    suspended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suspended_by_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    suspended_by_user_type: Mapped[ActorUserType | None] = mapped_column(
        PG_ENUM(
            ActorUserType,
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )

    # ---------- Audit (Pattern b) ----------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=FetchedValue(),
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_by_user_type: Mapped[ActorUserType | None] = mapped_column(
        PG_ENUM(
            ActorUserType,
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=FetchedValue(),
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    updated_by_user_type: Mapped[ActorUserType | None] = mapped_column(
        PG_ENUM(
            ActorUserType,
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
