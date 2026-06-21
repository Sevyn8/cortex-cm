"""Project-wide SQLAlchemy DeclarativeBase.

Every ORM model in admin-backend inherits this Base. Schema qualification
is per-table via ``__table_args__ = {"schema": ...}`` (per D-15), not
set globally on ``Base.metadata`` — the schema name is parameterised by
the ``DB_SCHEMA`` env var and resolved through ``get_settings()`` at the
model module's import time.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Project-wide SQLAlchemy DeclarativeBase. Every ORM model inherits this."""
