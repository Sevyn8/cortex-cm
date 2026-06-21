"""Audit log ORM models for the two-table audit subsystem.

Both tables share a symmetric 16-column shape per the design at
`docs/architecture_audit_logs.md` (Step 6.16.0). They differ only in:

  * `tenant_id` and `tenant_name` are NOT NULL on
    `TenantActivityAuditLog` and NULLABLE on `PlatformActivityAuditLog`
    (the platform table holds tenant-scope context only for
    tenant-creation success rows; non-tenant-creation rows leave both
    NULL).
  * `TenantActivityAuditLog` has RLS+FORCE with the D-29 unconditional
    OR-branch policy. `PlatformActivityAuditLog` has no RLS; access is
    gated at the API layer (Step 6.16.3 onward).

The `AuditResultType` Python enum mirrors the SQL enum
`audit_result_type_enum` created in migration `c530346032dd` (6 stable
failure categories).

`actor_user_type` reuses the existing `ActorUserType` enum from
`models.tenant_user` per the convention; no redeclaration.

PG enum columns use `postgresql.ENUM(..., create_type=False,
native_enum=True, values_callable=...)` per the "Note on PG enum
columns" convention. `id`, `timestamp`, and `details` carry
`server_default=FetchedValue()` so SQLAlchemy omits them from INSERT
and reads them back via RETURNING when not explicitly set.

Schema qualification (`__table_args__["schema"]`) resolves from the
`DB_SCHEMA` env var per D-15.

No FK constraints declared at the SQLAlchemy level for `tenant_id` on
either table (the DB-level FK in the migration is the structural
guarantee; no Repo query needs the SA-layer FK to navigate). Same
posture as the rest of the v0 models for cross-table audit / FK
columns.
"""
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, FetchedValue, Text, Uuid
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import get_settings
from admin_backend.db.base import Base
from admin_backend.models.tenant_user import ActorUserType


_DB_SCHEMA = get_settings().db_schema


class AuditResultType(str, Enum):
    """Audit result classification. Mirrors `audit_result_type_enum` in DDL.

    Stable vocabulary; adding a value requires a DB enum migration.
    The 6 values cover every outcome the v0 write endpoints can produce.
    """

    SUCCESS = "SUCCESS"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    CONFLICT = "CONFLICT"
    INTEGRITY_VIOLATION = "INTEGRITY_VIOLATION"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class TenantActivityAuditLog(Base):
    """Audit log row for a tenant-scoped action (RLS+FORCE)."""

    __tablename__ = "tenant_activity_audit_logs"
    __table_args__ = {"schema": _DB_SCHEMA}

    # ---------- Surrogate primary key ----------
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=FetchedValue()
    )

    # ---------- When ----------
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=FetchedValue(),
    )

    # ---------- Tenant context (NOT NULL on tenant table) ----------
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    tenant_name: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------- Actor (denormalised snapshot; no FK per Pattern (b) intent) ----------
    actor_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    actor_user_type: Mapped[ActorUserType] = mapped_column(
        PG_ENUM(
            ActorUserType,
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    actor_display_name: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------- Resource ----------
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    resource_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Step 6.16.7 LD1: populated only on ORG_NODE rows with the
    # ``org_nodes.node_type`` enum value frozen at write time; NULL for
    # non-ORG_NODE rows and pre-6.16.7 historical rows.
    resource_subtype: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------- Action ----------
    action: Mapped[str] = mapped_column(Text, nullable=False)
    action_label: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------- Result ----------
    result_type: Mapped[AuditResultType] = mapped_column(
        PG_ENUM(
            AuditResultType,
            name="audit_result_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    result_label: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------- Actor enrichment (Step 6.16.7 LD1, frozen snapshot per LD4) ----------
    # Tenant name for tenant actors, literal ``'Platform-Ithina'`` for
    # platform actors. Resolved at audit emission time per LD6.
    actor_organization_name: Mapped[str] = mapped_column(Text, nullable=False)
    # Comma-separated active role display names from ``roles.name`` at
    # the moment of the audited action. Empty-role and unresolvable
    # cases render as ``'-'``. Resolved at audit emission time per LD5.
    actor_roles: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------- Correlation ----------
    request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)

    # ---------- Payload ----------
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=FetchedValue()
    )


class PlatformActivityAuditLog(Base):
    """Audit log row for a platform-scope action (no RLS).

    `tenant_id` and `tenant_name` are NULLABLE: populated only on
    tenant-creation success rows (per design-doc routing principle);
    NULL on every other platform-scope event.
    """

    __tablename__ = "platform_activity_audit_logs"
    __table_args__ = {"schema": _DB_SCHEMA}

    # ---------- Surrogate primary key ----------
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=FetchedValue()
    )

    # ---------- When ----------
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=FetchedValue(),
    )

    # ---------- Tenant context (NULLABLE on platform table) ----------
    tenant_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    tenant_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------- Actor ----------
    actor_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    actor_user_type: Mapped[ActorUserType] = mapped_column(
        PG_ENUM(
            ActorUserType,
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    actor_display_name: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------- Resource ----------
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    resource_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Step 6.16.7 LD1: populated only on ORG_NODE rows with the
    # ``org_nodes.node_type`` enum value frozen at write time; NULL for
    # non-ORG_NODE rows and pre-6.16.7 historical rows.
    resource_subtype: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------- Action ----------
    action: Mapped[str] = mapped_column(Text, nullable=False)
    action_label: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------- Result ----------
    result_type: Mapped[AuditResultType] = mapped_column(
        PG_ENUM(
            AuditResultType,
            name="audit_result_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    result_label: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------- Actor enrichment (Step 6.16.7 LD1, frozen snapshot per LD4) ----------
    # Literal ``'Platform-Ithina'`` for every platform-table row (the
    # actor is operating with platform authority on this table).
    actor_organization_name: Mapped[str] = mapped_column(Text, nullable=False)
    # Comma-separated active role display names from ``roles.name`` at
    # the moment of the audited action. Empty-role and unresolvable
    # cases render as ``'-'``. Resolved at audit emission time per LD5.
    actor_roles: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------- Correlation ----------
    request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)

    # ---------- Payload ----------
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=FetchedValue()
    )
