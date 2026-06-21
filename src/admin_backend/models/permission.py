"""SQLAlchemy ORM model for the ``permissions`` catalogue.

Maps every column of ``db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v2.sql``
in DDL order. Platform-global; no RLS. Both user types see all rows.

The four enum columns reference Postgres enum types maintained by the
DDL plus a series of cleanup migrations:

  - ``module_code_enum`` (6 values: ADMIN, PRICING_OS,
    PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT, ROOS, GOAL_CONSOLE)
    — Step 6.6 unification (``cec8fae734e0``) re-pointed
    ``permissions.module`` from the (now-dropped) narrow
    ``module_enum`` to ``module_code_enum``, the same enum that
    ``tenant_module_access.module`` uses. The ``ModuleCode`` Python
    enum is imported from ``models/tenant_module_access`` rather than
    redeclared here — single source of truth across both consumer
    columns. Closes the MODULES-EXT forward note from Step 6.1.
    As of 2026-05-12, ROOS is retained in the DB enum but retired
    from the Python ``ModuleCode`` class; PG cleanup deferred to the
    future rename migration when ROOS's replacement is decided.

  - ``permission_scope_enum`` (3 values: GLOBAL, TENANT, STORE) —
    Step 6.1's ``rbac_enum_cleanup`` (``90cd038ae618``) dropped REGION.

  - ``resource_enum`` and ``action_enum`` — locked vocabularies in the
    original DDL; no narrowing migrations.

Audit shape: minimal — ``created_at`` + ``updated_at`` only, no
audit-actor pairs. The catalogue is reference data populated by Ithina
platform admins via migration; per-row attribution lives in the
migration's git history rather than in the row itself.

Schema qualification (``__table_args__["schema"]``) resolves from
``DB_SCHEMA`` env var per D-15.
"""
from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, FetchedValue, Text, Uuid
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import get_settings
from admin_backend.db.base import Base
from admin_backend.models.tenant_module_access import ModuleCode


class PermissionResource(str, Enum):
    """Mirrors ``resource_enum`` (locked vocabulary; no narrowing needed)."""

    PRICING_RULES = "PRICING_RULES"
    MARKDOWNS = "MARKDOWNS"
    EXPIRING_ITEMS = "EXPIRING_ITEMS"
    WASTE_LOG = "WASTE_LOG"
    DONATION_ROUTING = "DONATION_ROUTING"
    CAMPAIGNS = "CAMPAIGNS"
    USERS = "USERS"
    ROLES = "ROLES"
    AUDIT_LOG = "AUDIT_LOG"
    TENANTS = "TENANTS"
    STORES = "STORES"
    ORG_NODES = "ORG_NODES"


class PermissionAction(str, Enum):
    """Mirrors ``action_enum`` (locked vocabulary; no narrowing needed)."""

    VIEW = "VIEW"
    CONFIGURE = "CONFIGURE"
    EXECUTE = "EXECUTE"
    APPROVE = "APPROVE"
    OVERRIDE = "OVERRIDE"
    AUDIT = "AUDIT"


class PermissionScope(str, Enum):
    """Mirrors ``permission_scope_enum`` (post Step 6.1 narrowing)."""

    GLOBAL = "GLOBAL"
    TENANT = "TENANT"
    STORE = "STORE"


class Permission(Base):
    """A canonical (module, resource, action, scope) tuple."""

    __tablename__ = "permissions"
    __table_args__ = {"schema": get_settings().db_schema}

    # ---------- Surrogate primary key ----------
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=FetchedValue()
    )

    # ---------- Composite identity ----------
    # ``ModuleCode`` is the unified Python enum imported from
    # ``models/tenant_module_access``; same enum backs both
    # ``permissions.module`` and ``tenant_module_access.module``
    # post Step 6.6's unification migration (``cec8fae734e0``).
    module: Mapped[ModuleCode] = mapped_column(
        PG_ENUM(
            ModuleCode,
            name="module_code_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    resource: Mapped[PermissionResource] = mapped_column(
        PG_ENUM(
            PermissionResource,
            name="resource_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    action: Mapped[PermissionAction] = mapped_column(
        PG_ENUM(
            PermissionAction,
            name="action_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    scope: Mapped[PermissionScope] = mapped_column(
        PG_ENUM(
            PermissionScope,
            name="permission_scope_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    # ---------- Display ----------
    code: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------- Audit ----------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=FetchedValue(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=FetchedValue(),
    )
