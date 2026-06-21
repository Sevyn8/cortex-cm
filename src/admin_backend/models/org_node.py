"""SQLAlchemy ORM model for the ``org_nodes`` table.

Maps every column of ``db/raw_ddl/Ithina_postgres_SQL_DDL_org_nodes_v2.sql``
in DDL order. Backs the Organization Tree page (Step 5.3, E2 + E3) and
serves as the permission-scope anchor when RBAC enforcement lands at
Step 6.1.

Notes on shape (mirrors ``models/tenant_user.py`` and
``models/tenant_module_access.py``):

- Schema qualification (``__table_args__["schema"]``) resolves from
  ``DB_SCHEMA`` env var per D-15.

- ``id``, ``status``, ``created_at``, ``updated_at`` carry
  ``server_default=FetchedValue()``. ``path`` is also marked with
  ``FetchedValue()`` because while v0 read-only never inserts via the
  ORM, v0.1+ writes will populate ``path`` from a trigger or app-layer
  build step; declaring the default existence here keeps INSERT shape
  forward-compatible. The DDL itself does not carry a path DEFAULT;
  this is a soft declaration that covers the future write path.

- Audit-actor columns use Pattern (b) per D-13. Three pairs:
  ``created_by_user_id`` + ``created_by_user_type``,
  ``updated_by_user_id`` + ``updated_by_user_type``,
  ``archived_by_user_id`` + ``archived_by_user_type``. The
  ``*_user_id`` is a raw UUID (no SQLAlchemy ``ForeignKey`` — the
  actor could be in either ``platform_users`` or ``tenant_users``).
  The ``*_user_type`` is the shared ``actor_user_type_enum``
  (PLATFORM/TENANT) discriminator. ``ActorUserType`` is imported from
  ``models.tenant_user`` (the first model that declared it; per the
  comment there, this is the third model to consume it after
  ``tenant_users`` and ``stores`` is still a stub).

- ``path`` is the ltree materialised path. Treated as opaque ``str``
  in Python; ordering happens via path-ASC at the SQL layer. The PG
  type is ``ltree``; SQLAlchemy doesn't have a native ``ltree``
  dialect type, but ``Text`` marshals the value transparently for
  read paths (``SELECT path::text`` happens implicitly). For write
  paths landing post-v0, callers cast via ``CAST(:path AS ltree)``
  in raw SQL — same pattern the seed loader uses.

- ``node_type`` and ``status`` are PG enums per the "Note on PG enum
  columns" convention; ``create_type=False`` (DDL owns the type).
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
from admin_backend.models.tenant_user import ActorUserType


class OrgNodeType(str, Enum):
    """Node type. Mirrors ``org_node_type_enum`` in the DDL."""

    TENANT = "TENANT"
    BUSINESS_UNIT = "BUSINESS_UNIT"
    HQ = "HQ"
    COUNTRY = "COUNTRY"
    REGION = "REGION"
    STORE = "STORE"
    DEPARTMENT = "DEPARTMENT"


class OrgNodeStatus(str, Enum):
    """Lifecycle status. Mirrors ``org_node_status_enum`` in the DDL."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class OrgNode(Base):
    """A node in a tenant's organisation hierarchy.

    Tenant-scoped: every row has a NOT NULL ``tenant_id`` and is
    visible only to sessions where ``app.tenant_id`` matches (or to
    PLATFORM sessions per the D-29 unconditional OR-branch on
    ``org_nodes_tenant_isolation``).
    """

    __tablename__ = "org_nodes"
    __table_args__ = {"schema": get_settings().db_schema}

    # ---------- Surrogate primary key ----------
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=FetchedValue()
    )

    # ---------- Ownership ----------
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)

    # ---------- Tree position ----------
    parent_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    path: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=FetchedValue()
    )

    # ---------- Identity ----------
    node_type: Mapped[OrgNodeType] = mapped_column(
        PG_ENUM(
            OrgNodeType,
            name="org_node_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------- Lifecycle ----------
    status: Mapped[OrgNodeStatus] = mapped_column(
        PG_ENUM(
            OrgNodeStatus,
            name="org_node_status_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=FetchedValue(),
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
