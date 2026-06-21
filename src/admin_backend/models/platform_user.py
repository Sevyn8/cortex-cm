"""SQLAlchemy ORM model for the ``platform_users`` table.

Maps every column of ``db/raw_ddl/Ithina_postgres_SQL_DDL_platform_users_v1.sql``
in DDL order. The DDL is the source of truth for schema; this module is
the application's typed view onto it.

Notes on shape (mirror of ``models/tenant.py`` — same conventions):

- Schema qualification (``__table_args__["schema"]``) is resolved from
  ``DB_SCHEMA`` env var per D-15.

- ``id``, ``status``, ``created_at``, ``updated_at`` carry
  ``server_default=FetchedValue()``. The DDL defines the actual default
  expression; declaring it again at the ORM layer would create an
  FN-AB-13 maintenance trap. ``FetchedValue()`` declares only the
  *existence* of a DB-side default so SQLAlchemy generates correct
  INSERTs (without it, SA sends explicit NULLs that defeat the DB
  DEFAULTs and trigger NOT NULL violations).

- Audit FKs (``*_by_user_id``) self-reference platform_users at the DB
  layer. The SQLAlchemy ``ForeignKey(...)`` declaration is
  intentionally omitted: a forward self-reference would only add
  declaration ceremony with no behavioural value for v0 read-only.
  The DB still enforces the FK (RESTRICT on delete/update).

- ``status`` references the Postgres enum type ``platform_user_status_enum``
  already created by the DDL; ``create_type=False`` keeps SQLAlchemy
  from issuing ``CREATE TYPE`` again on metadata creation. The
  dialect-specific ``postgresql.ENUM`` is used (not generic
  ``sqlalchemy.Enum``) because the generic class silently drops the
  ``create_type`` kwarg on the postgres dialect impl. ``values_callable``
  ensures the DB receives ``Enum.value``.
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


class PlatformUserStatus(str, Enum):
    """Lifecycle states for a platform user. Mirrors ``platform_user_status_enum``."""

    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class PlatformUser(Base):
    """An Ithina staff user. Platform-global; no tenant boundary.

    Physically separated from ``tenant_users`` (Pattern 2 per D-02) so
    cross-audience leakage is structurally impossible rather than
    policy-only. No RLS on this table; access is controlled by DB role
    (only the staff DB role connects here) plus application-layer
    PLATFORM-only gating on the resource's HTTP endpoints.
    """

    __tablename__ = "platform_users"
    __table_args__ = {"schema": get_settings().db_schema}

    # ---------- Surrogate primary key ----------
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=FetchedValue()
    )

    # ---------- External identity ----------
    auth0_sub: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------- Identity ----------
    email: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------- Lifecycle ----------
    status: Mapped[PlatformUserStatus] = mapped_column(
        PG_ENUM(
            PlatformUserStatus,
            name="platform_user_status_enum",
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

    # ---------- Suspension ----------
    suspended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suspended_by_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    # ---------- Audit ----------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=FetchedValue(),
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=FetchedValue(),
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
