"""Lookup ORM model.

Maps the ``lookups`` table created at Step 1.4. Each row is a
``(list_name, code, display_name)`` reference entry — the platform's
source of truth for enum-style display data the API exposes to
frontends.

list_name is snake_case (matching ``ck_lookups_list_name_format``);
code is UPPER_SNAKE_CASE. ``description`` is optional. ``display_order``
controls UI ordering within a list (low-to-high). ``is_active``
soft-deactivates entries without deleting them.

The table is platform-global: no ``tenant_id``, no RLS — same lists
for all tenants in v0.

Step 3.4.5 introduces this model so ``TenantsRepo`` can JOIN it for
the per-tenant modules subquery. Future endpoints (``/api/v1/lookups``)
will read from it directly.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, FetchedValue, Integer, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import get_settings
from admin_backend.db.base import Base


class Lookup(Base):
    """Platform-global reference list row."""

    __tablename__ = "lookups"
    __table_args__ = {"schema": get_settings().db_schema}

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=FetchedValue()
    )
    list_name: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=FetchedValue()
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=FetchedValue()
    )
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
