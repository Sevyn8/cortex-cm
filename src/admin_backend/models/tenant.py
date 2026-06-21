"""SQLAlchemy ORM model for the ``tenants`` table.

Maps every column of ``db/raw_ddl/Ithina_postgres_SQL_DDL_tenants_v3.sql``
in DDL order. The DDL is the source of truth for schema; this module is
the application's typed view onto it.

Notes on shape:

- Schema qualification (``__table_args__["schema"]``) is resolved from
  the configured ``DB_SCHEMA`` env var per D-15. The schema name is
  pinned at module-import time, which is fine for v0's single-process
  test and runtime model (Settings is process-global anyway via
  lru_cache).

- The ``id`` PK has no Python or ORM-side default value. The DDL
  carries ``DEFAULT uuidv7()`` (project's PL/pgSQL function from
  shared utilities, FN-AB-13 swap-target on Postgres 18). Declaring
  the literal SQL default again at the ORM layer would create a
  maintenance trap when FN-AB-13 lands. Instead, ``id``, ``status``,
  ``created_at``, and ``updated_at`` carry ``server_default=
  FetchedValue()`` — SQLAlchemy's "DB will fill this; omit from
  INSERT and read back via RETURNING" marker. ``FetchedValue()``
  declares the *existence* of a DB-side default without redeclaring
  the SQL, preserving D-21's intent (the DDL stays the single source
  of truth for *what* the default is) while letting SQLAlchemy
  generate correct INSERTs (without it, SA sends explicit NULLs that
  defeat the DB DEFAULTs and trigger NOT NULL violations on
  non-PK columns).

- Audit FKs (``*_by_user_id``) are typed FKs at the DB layer per D-13's
  Pattern (a). The SQLAlchemy ``ForeignKey(...)`` declaration is
  intentionally omitted at this step: ``PlatformUser`` doesn't exist
  until Step 5.1 and a forward reference here would create a chicken-
  and-egg problem at metadata-creation time. The DB still enforces the
  FK; the ORM just doesn't model the relationship for v0.

- The four enum columns reference Postgres enum types already created
  by the DDL; ``create_type=False`` keeps SQLAlchemy from trying to
  ``CREATE TYPE`` again on metadata creation. We use the dialect-
  specific ``postgresql.ENUM`` (not generic ``sqlalchemy.Enum``)
  because the generic class silently drops the ``create_type`` kwarg
  on the postgres dialect impl. ``values_callable`` is set so the DB
  receives ``Enum.value`` (cheap insurance for any future enum where
  ``.name != .value``).
"""
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    FetchedValue,
    Integer,
    Numeric,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import get_settings
from admin_backend.db.base import Base


class TenantStatus(str, Enum):
    """Lifecycle states for a tenant. Mirrors ``tenant_status_enum``."""

    ONBOARDING = "ONBOARDING"
    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"


class TenantTier(str, Enum):
    """Commercial segmentation tier. Mirrors ``tenant_tier_enum``."""

    ENTERPRISE = "ENTERPRISE"
    MID_MARKET = "MID_MARKET"
    SMB = "SMB"
    SINGLE_STORE = "SINGLE_STORE"


class TenantIndustry(str, Enum):
    """Retail vertical. Mirrors ``tenant_industry_enum``."""

    CONVENIENCE_FUEL = "CONVENIENCE_FUEL"
    CONVENIENCE = "CONVENIENCE"
    GROCERY = "GROCERY"
    HYPERMART = "HYPERMART"
    SPECIALITY_GROCERY = "SPECIALITY_GROCERY"
    ORGANIC_GROCERY = "ORGANIC_GROCERY"


class TenantRegion(str, Enum):
    """Deployment region pinned at tenant creation. Mirrors ``tenant_region_enum``."""

    US = "US"
    EU = "EU"


class Tenant(Base):
    """A customer organisation on the Ithina platform.

    Root of the multi-tenancy isolation hierarchy: every tenant-owned
    entity references ``tenants.id`` via ``tenant_id`` and enforces
    row-level isolation against this row.
    """

    __tablename__ = "tenants"
    __table_args__ = {"schema": get_settings().db_schema}

    # ---------- Surrogate primary key ----------
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=FetchedValue()
    )

    # ---------- Identity ----------
    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[TenantRegion] = mapped_column(
        PG_ENUM(
            TenantRegion,
            name="tenant_region_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    # ---------- Classification ----------
    tier: Mapped[TenantTier | None] = mapped_column(
        PG_ENUM(
            TenantTier,
            name="tenant_tier_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
    industry: Mapped[TenantIndustry | None] = mapped_column(
        PG_ENUM(
            TenantIndustry,
            name="tenant_industry_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )

    # ---------- Commercial profile ----------
    monthly_revenue_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 2), nullable=True
    )
    monthly_revenue_as_of_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    number_of_stores: Mapped[int | None] = mapped_column(Integer, nullable=True)
    number_of_stores_as_of_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )

    # ---------- Primary contact ----------
    primary_contact_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------- Lifecycle ----------
    status: Mapped[TenantStatus] = mapped_column(
        PG_ENUM(
            TenantStatus,
            name="tenant_status_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=FetchedValue(),
    )

    # ---------- Audit (Pattern (a) per D-13: typed FKs to platform_users) ----------
    # FK declarations are intentionally absent at this step; PlatformUser
    # lands in Step 5.1. The DB still enforces the FK constraints; the
    # ORM just doesn't model the relationship for v0.
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
    suspended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suspended_by_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    terminated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terminated_by_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
