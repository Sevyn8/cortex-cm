"""SQLAlchemy ORM model for the ``roles`` table.

Maps every column of ``db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v2.sql``
in DDL order. The DDL is the source of truth for schema; this module
is the application's typed view onto it.

Notes on shape (mirrors ``models/tenant_user.py`` exactly):

- Schema qualification (``__table_args__["schema"]``) resolves from
  ``DB_SCHEMA`` env var per D-15.

- Platform-global table — no RLS. Visibility is controlled at the app
  layer via the ``audience`` column: TENANT JWTs see only
  ``audience='TENANT'`` rows. PLATFORM JWTs see both. Captured as the
  audience-filter convention note in CLAUDE.md "Code conventions and
  structure" subsection.

- ``id``, ``status``, ``created_at``, ``updated_at`` carry
  ``server_default=FetchedValue()``. The DDL owns the actual default
  expression (``uuidv7()`` / ``'ACTIVE'`` / ``NOW()``); the ORM
  declares only that a DB-side default exists so SQLAlchemy omits the
  column from INSERT statements. (Without it, SA sends explicit NULLs
  that defeat the DDL DEFAULT and trigger NOT NULL violations.)

- Pattern (b) audit-actor columns per D-13. Three pairs:
  ``created_by_user_id`` + ``created_by_user_type``,
  ``updated_by_user_id`` + ``updated_by_user_type``,
  ``archived_by_user_id`` + ``archived_by_user_type``. The
  ``*_user_id`` is a raw UUID (NO ``ForeignKey`` at this layer —
  Pattern (b) deliberately has no DB FK because the actor could be
  in either ``platform_users`` or ``tenant_users``). The
  ``*_user_type`` is the ``actor_user_type_enum`` discriminator.

- The ``actor_user_type_enum`` is shared platform-wide; it was first
  declared by ``tenant_users`` (Step 5.2). This module imports the
  ``ActorUserType`` Python enum from there rather than redeclaring —
  per the "Note on PG enum columns" convention, redeclaration with
  ``create_type=True`` would error on metadata creation; redeclaration
  with ``create_type=False`` is harmless but duplicates intent. Reuse
  is cleaner.
"""
from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    FetchedValue,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import get_settings
from admin_backend.db.base import Base
from admin_backend.models.tenant_user import ActorUserType


class RoleAudience(str, Enum):
    """Mirrors ``role_audience_enum``. PLATFORM = Ithina staff,
    TENANT = customer staff."""

    PLATFORM = "PLATFORM"
    TENANT = "TENANT"


class RoleStatus(str, Enum):
    """Mirrors ``role_status_enum``."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class Role(Base):
    """A platform-defined named bundle of permissions."""

    __tablename__ = "roles"
    __table_args__ = {"schema": get_settings().db_schema}

    # ---------- Surrogate primary key ----------
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=FetchedValue()
    )

    # ---------- Identity ----------
    name: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------- Classification ----------
    audience: Mapped[RoleAudience] = mapped_column(
        PG_ENUM(
            RoleAudience,
            name="role_audience_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    # ---------- Lifecycle ----------
    status: Mapped[RoleStatus] = mapped_column(
        PG_ENUM(
            RoleStatus,
            name="role_status_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=FetchedValue(),
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=FetchedValue()
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
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_by_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    archived_by_user_type: Mapped[ActorUserType | None] = mapped_column(
        PG_ENUM(
            ActorUserType,
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
