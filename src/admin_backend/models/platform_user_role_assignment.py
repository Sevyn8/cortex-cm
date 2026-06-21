"""SQLAlchemy ORM model for ``platform_user_role_assignments``.

Platform-global table: no RLS, no ``tenant_id``. PLATFORM-audience role
assignments to ``platform_users``. Mirrors the platform_users /
tenant_users Pattern 2 split (D-12, D-34).

The audience invariant (``role.audience='PLATFORM'`` only) is enforced
at the DB layer by the BEFORE INSERT/UPDATE OF role_id trigger
``enforce_platform_role_audience()`` (Step 6.8.1's migration
``3e05299cb533``). Application code does not need to re-enforce.

Notes on shape (mirrors ``models/tenant_user.py`` / ``models/role.py``):

- Schema qualification (``__table_args__["schema"]``) resolves from
  ``DB_SCHEMA`` env var per D-15.

- ``id``, ``status``, ``granted_at``, ``updated_at`` carry
  ``server_default=FetchedValue()``. The DDL owns the actual default
  expression (``uuidv7()`` / ``'ACTIVE'`` / ``NOW()``); the ORM
  declares only that a DB-side default exists so SQLAlchemy omits the
  column from INSERT statements (without it, SA sends explicit NULLs
  that defeat the DDL DEFAULT and trigger NOT NULL violations).

- Audit-actor columns (Pattern (b) per D-13). Two pairs:
  ``granted_by_user_id`` + ``granted_by_user_type`` and
  ``revoked_by_user_id`` + ``revoked_by_user_type``. The
  ``*_user_id`` is a raw UUID (NO ``ForeignKey`` at this layer —
  Pattern (b) deliberately has no DB FK because the actor could be
  in either ``platform_users`` or ``tenant_users``). The
  ``*_user_type`` is the shared ``actor_user_type_enum``
  discriminator imported from ``models.tenant_user``.

- ``UserRoleAssignmentStatus`` is a new Python enum declared here
  (the file naming makes it the natural home); ``tenant_user_role_assignment``
  imports from this module rather than redeclaring.
"""
from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    DateTime,
    FetchedValue,
    ForeignKey,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import get_settings
from admin_backend.db.base import Base
from admin_backend.models.tenant_user import ActorUserType


_DB_SCHEMA = get_settings().db_schema


class UserRoleAssignmentStatus(str, Enum):
    """Mirrors ``user_role_assignment_status_enum``.

    Shared by both physical assignment tables. ACTIVE rows count
    toward role usage (E1's ``user_count`` aggregate); INACTIVE rows
    represent revoked assignments retained for audit history.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class PlatformUserRoleAssignment(Base):
    """Active or revoked role grant for a platform user.

    No RLS — ``platform_users`` are globally visible. Audience-check
    trigger ensures only PLATFORM-audience roles can be assigned here.
    """

    __tablename__ = "platform_user_role_assignments"
    __table_args__ = {"schema": _DB_SCHEMA}

    # ---------- Surrogate primary key ----------
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=FetchedValue()
    )

    # ---------- Subject + role ----------
    platform_user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey(f"{_DB_SCHEMA}.platform_users.id"),
        nullable=False,
    )
    role_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey(f"{_DB_SCHEMA}.roles.id"),
        nullable=False,
    )

    # ---------- Lifecycle ----------
    status: Mapped[UserRoleAssignmentStatus] = mapped_column(
        PG_ENUM(
            UserRoleAssignmentStatus,
            name="user_role_assignment_status_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=FetchedValue(),
    )

    # ---------- Grant (Pattern b audit) ----------
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=FetchedValue(),
    )
    granted_by_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    granted_by_user_type: Mapped[ActorUserType | None] = mapped_column(
        PG_ENUM(
            ActorUserType,
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )

    # ---------- Revoke (Pattern b audit) ----------
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    revoked_by_user_type: Mapped[ActorUserType | None] = mapped_column(
        PG_ENUM(
            ActorUserType,
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )

    # ---------- Update audit ----------
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=FetchedValue(),
    )
