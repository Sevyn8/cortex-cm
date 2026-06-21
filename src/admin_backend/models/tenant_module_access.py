"""TenantModuleAccess ORM model + module enums.

Tracks which modules each tenant is entitled to use, with full
lifecycle audit columns. Pattern (a) audit-actors per D-13: typed FKs
direct to ``platform_users``, no ``*_by_user_type`` discriminator
(modules are managed by Ithina staff only; no TENANT user_type ever
appears in audit-actor columns).

Resolves FN-AB-16 by replacing the Step 3.3
``_module_entitlements_stub.py`` Python dict.

Per the "Note on PG enum columns" convention, ``module`` and
``status`` use ``postgresql.ENUM(..., create_type=False,
native_enum=True)`` — never ``Text`` (Postgres has no implicit
varchar -> enum cast).

Per Step 3.1's amendment, ``id``, ``created_at``, ``updated_at``
carry ``server_default=FetchedValue()`` so SQLAlchemy omits them
from INSERT and reads them back via RETURNING.
"""
from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, FetchedValue, Uuid
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import get_settings
from admin_backend.db.base import Base


# ROOS retired from Python vocabulary on 2026-05-12. The DB enum
# ``core.module_code_enum`` still contains ROOS as its first value
# (PG enum cleanup deferred to the future rename migration when
# ROOS's replacement module name is decided). The narrowing creates
# a one-way drift: any DB row with ``module='ROOS'`` would crash
# Pydantic validation at the read boundary. The operator-run cloud
# cleanup SQL deletes the lookups and tenant_module_access rows that
# reference ROOS so no live row triggers the crash.
class ModuleCode(str, Enum):
    """Platform-fixed module codes. Mirrors ``module_code_enum`` in DDL,
    minus ROOS (retired from Python vocabulary 2026-05-12; see comment
    above)."""

    PRICING_OS = "PRICING_OS"
    PERISHABLES_ASSISTANT = "PERISHABLES_ASSISTANT"
    PROMOTIONS_ASSISTANT = "PROMOTIONS_ASSISTANT"
    GOAL_CONSOLE = "GOAL_CONSOLE"
    ADMIN = "ADMIN"


class ModuleAccessStatus(str, Enum):
    """Module access lifecycle. Mirrors ``module_access_status_enum`` in DDL."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


class TenantModuleAccess(Base):
    """Per-tenant module entitlement row with lifecycle audit."""

    __tablename__ = "tenant_module_access"
    __table_args__ = {"schema": get_settings().db_schema}

    # ---------- Surrogate primary key ----------
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=FetchedValue()
    )

    # ---------- Identity ----------
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
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
    status: Mapped[ModuleAccessStatus] = mapped_column(
        PG_ENUM(
            ModuleAccessStatus,
            name="module_access_status_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    # ---------- Lifecycle ----------
    enabled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    enabled_by_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disabled_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, nullable=True
    )

    # ---------- Audit (Pattern (a) per D-13) ----------
    # FK declarations live at the DB layer per Step 3.1's pattern; no
    # SA-level relationship() to PlatformUser (the model lands at 5.1).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=FetchedValue(),
    )
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=FetchedValue(),
    )
    updated_by_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
