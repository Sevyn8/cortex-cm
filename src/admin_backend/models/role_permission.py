"""SQLAlchemy ORM model for the ``role_permissions`` junction.

Maps every column of ``db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v2.sql``
in DDL order. Composite PK on ``(role_id, permission_id)``.

Pattern (b) audit-actor on ``created_by`` only (the junction has no
update lifecycle — rows are created and deleted, never edited in
place). The ``*_user_id`` is a raw UUID (no DB-level FK to either
user table because the actor could be in either) and the
``*_user_type`` is the ``actor_user_type_enum`` discriminator.

Platform-global, no RLS. Visibility is open to both user types via
the matrix endpoint (which renders the full grid for PLATFORM and a
TENANT-audience-filtered grid for TENANT JWTs); per-role drilldown
goes through E3, which audience-gates the parent role lookup before
loading the junction rows.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, FetchedValue, ForeignKey, Uuid
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import get_settings
from admin_backend.db.base import Base
from admin_backend.models.tenant_user import ActorUserType


_DB_SCHEMA = get_settings().db_schema


class RolePermission(Base):
    """Many-to-many: which permissions does each role grant."""

    __tablename__ = "role_permissions"
    __table_args__ = {"schema": _DB_SCHEMA}

    role_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey(f"{_DB_SCHEMA}.roles.id"),
        primary_key=True,
    )
    permission_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey(f"{_DB_SCHEMA}.permissions.id"),
        primary_key=True,
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
