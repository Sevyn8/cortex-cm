"""SQLAlchemy ORM model for the ``stores`` table (Step 6.17.2).

Maps every column of
``db/raw_ddl/Ithina_postgres_SQL_DDL_stores_v5.sql`` in DDL order. The
DDL is the source of truth for schema; this module is the application's
typed view onto it.

Replaces the 2-column lightweight stub at
``models/_lightweight_stubs.py::Store`` carried since Step 3.3. The
stub's two consumers in ``repositories/tenants.py`` (the
``num_stores`` correlated subquery and ``count_for_stats``) work
unchanged because the full model retains both ``id`` and ``tenant_id``.

Notes on shape (mirroring ``models/tenant.py``):

- Schema qualification (``__table_args__["schema"]``) resolved from
  ``get_settings().db_schema`` per D-15.

- ``id``, ``status``, ``created_at``, ``updated_at`` carry
  ``server_default=FetchedValue()`` so SQLAlchemy omits them from
  INSERT and reads back via RETURNING. The DDL holds the actual
  defaults (``uuidv7()``, ``'ACTIVE'``, ``NOW()``).

- ``status`` and ``tax_treatment`` columns bind to their respective
  named PG enums via the dialect-specific ``postgresql.ENUM`` with
  ``create_type=False, native_enum=True, values_callable=...`` per the
  CLAUDE.md "Note on PG enum columns" convention.

- Three audit-actor pairs (``created_*``, ``updated_*``, ``closed_*``)
  follow D-13 Pattern (b): bare ``UUID`` columns paired with a typed
  ``actor_user_type_enum`` column. No SA ``ForeignKey`` on the
  ``*_user_id`` half — the actor can be in either user table.

- ``latitude`` / ``longitude`` declared as ``Numeric(9, 6)`` to mirror
  the DDL exactly. They serialise to JSON as strings via a
  ``field_serializer`` on the ``StoreDetail`` schema (NUMERIC-as-string
  per D-28 / Q11).
"""
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    DateTime,
    FetchedValue,
    Numeric,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import get_settings
from admin_backend.db.base import Base
from admin_backend.models.tenant_user import ActorUserType


class StoreStatus(str, Enum):
    """Lifecycle states for a store. Mirrors ``store_status_enum``."""

    OPENING = "OPENING"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    CLOSED = "CLOSED"


class TaxTreatment(str, Enum):
    """Pricing-display tax convention. Mirrors ``tax_treatment_enum``."""

    EXCLUSIVE = "EXCLUSIVE"
    INCLUSIVE = "INCLUSIVE"


class Store(Base):
    """A retail store operated by a tenant.

    Tenant-scoped via the ``tenant_id`` FK. RLS-bound at the DB layer
    (D-29 OR-branch on ``stores_tenant_isolation``) so reads via the
    application role see only the caller's tenant's rows, or all rows
    when the session is PLATFORM-typed.
    """

    __tablename__ = "stores"
    __table_args__ = {"schema": get_settings().db_schema}

    # ---------- Surrogate primary key ----------
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=FetchedValue()
    )

    # ---------- Tenant + org anchor ----------
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    org_node_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    # ---------- Identity ----------
    name: Mapped[str] = mapped_column(Text, nullable=False)
    store_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6), nullable=True
    )
    longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6), nullable=True
    )

    # ---------- Pricing ----------
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    tax_treatment: Mapped[TaxTreatment] = mapped_column(
        PG_ENUM(
            TaxTreatment,
            name="tax_treatment_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    # ---------- Lifecycle ----------
    status: Mapped[StoreStatus] = mapped_column(
        PG_ENUM(
            StoreStatus,
            name="store_status_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=FetchedValue(),
    )

    # ---------- Audit (Pattern (b) per D-13) ----------
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

    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    closed_by_user_type: Mapped[ActorUserType | None] = mapped_column(
        PG_ENUM(
            ActorUserType,
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
