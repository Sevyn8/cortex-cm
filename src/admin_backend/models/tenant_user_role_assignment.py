"""SQLAlchemy ORM model for ``tenant_user_role_assignments``.

Multi-tenant table: RLS+FORCE with the unconditional OR-branch policy
(matching tenants, tenant_users, org_nodes, stores, tenant_module_access
per D-29). A row is visible to its own tenant under TENANT JWT, and to
any PLATFORM session.

Composite FKs to ``tenant_users(tenant_id, id)`` and
``org_nodes(tenant_id, id)`` make cross-tenant injection structurally
impossible at the schema layer (D-34, AI-RBAC-06 closed). The composite
FKs are NOT declared at the SA layer — composite FKs to non-PK columns
require explicit ``ForeignKeyConstraint`` at ``__table_args__`` which we
omit per existing project convention; no Repo query needs the SA-layer
FK to navigate.

The audience invariant (``role.audience='TENANT'`` only) is enforced at
the DB layer by the BEFORE INSERT/UPDATE OF role_id trigger
``enforce_tenant_role_audience()`` (Step 6.8.1's migration
``3e05299cb533``). Application code does not need to re-enforce.

Notes on shape:

- Schema qualification (``__table_args__["schema"]``) resolves from
  ``DB_SCHEMA`` env var per D-15.

- ``id``, ``status``, ``granted_at``, ``updated_at`` carry
  ``server_default=FetchedValue()`` — same posture as
  ``platform_user_role_assignments`` and the rest of the v0 models.

- Audit-actor columns are Pattern (b) per D-13. ``ActorUserType`` and
  ``UserRoleAssignmentStatus`` are imported from sibling modules (the
  shared enums); no redeclaration.
"""
from datetime import datetime
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
from admin_backend.models.platform_user_role_assignment import (
    UserRoleAssignmentStatus,
)
from admin_backend.models.tenant_user import ActorUserType


_DB_SCHEMA = get_settings().db_schema


class TenantUserRoleAssignment(Base):
    """Active or revoked role grant for a tenant user at an org_node anchor."""

    __tablename__ = "tenant_user_role_assignments"
    __table_args__ = {"schema": _DB_SCHEMA}

    # ---------- Surrogate primary key ----------
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=FetchedValue()
    )

    # ---------- Subject (composite FK at DB layer; no SA FK) ----------
    tenant_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey(f"{_DB_SCHEMA}.tenants.id"),
        nullable=False,
    )
    org_node_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)

    # ---------- Role ----------
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
